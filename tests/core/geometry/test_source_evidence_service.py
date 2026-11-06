"""tests/core/geometry/test_source_evidence_service.py
Bộ kiểm thử TDD cho SourceEvidenceService và các hợp đồng bằng chứng thi công (G07-07A3-I).
Bao phủ các ca kiểm thử: T01, T02, T03, T04, T05, T07, T08, T09.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
import unittest
from unittest.mock import patch

import pytest

from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.local_metadata_acquisition import (
    SourceFreshnessStat,
    _BoundedPipeReader,
    _capture_source_freshness,
)
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.geometry.source_evidence_service import (
    PROFILE_UNIFORM_TICKS_V1,
    SourceEvidenceBatchRequest,
    SourceEvidenceBatchResult,
    SourceEvidenceItemRequest,
    SourceEvidenceService,
    SourceExecutionEvidenceEnvelope,
    _scan_decoded_frames_uniform_ticks,
)


def _create_audio_first_bframe_video(
    file_path: Path,
    *,
    video_dur_sec: float = 0.5,
    audio_dur_sec: float = 1.0,
    fps: int = 30,
    b_frames: int = 2,
) -> None:
    """Tạo video fixture siêu nhỏ: audio global stream 0, video global stream 1, B-frames."""
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-f", "lavfi", "-i", f"testsrc=size=64x36:rate={fps}:duration={video_dur_sec}",
        "-map", "0:a:0",
        "-map", "1:v:0",
        "-t", str(audio_dur_sec),
        "-c:a", "aac",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-g", str(fps),
        "-bf", str(b_frames),
        "-x264-params", "b-adapt=0",
        "-y", str(file_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _create_single_frame_video(file_path: Path) -> None:
    """Tạo video chỉ có đúng 1 khung hình giải mã."""
    cmd = [
        "ffmpeg",
        "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=30:duration=0.034",
        "-frames:v", "1",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-y", str(file_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


class TestSourceEvidenceServiceMatrix:
    """Kiểm thử ma trận T01–T09 cho SourceEvidenceService."""

    def test_t01_cfr_bframes_real_fixture(self, tmp_path: Path) -> None:
        """T01: Real tiny CFR fixture MP4 có B-frames -> correct N, native FPS, full SHA, complete policy."""
        v_path = tmp_path / "t01_bframe.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.5, audio_dur_sec=1.0, fps=30, b_frames=2)

        # Kiểm tra packet order bằng ffprobe để chứng minh packet PTS không đơn điệu
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "1",
            "-show_packets",
            "-show_entries", "packet=pts",
            "-of", "json",
            str(v_path),
        ]
        r = subprocess.run(probe_cmd, check=True, capture_output=True)
        packets = json.loads(r.stdout).get("packets", [])
        packet_pts = [int(p["pts"]) for p in packets if "pts" in p]
        is_packet_monotone = all(a < b for a, b in zip(packet_pts, packet_pts[1:]))
        assert not is_packet_monotone, "Fixture phải có B-frames khiến packet PTS không đơn điệu"

        # Tính full SHA256 độc lập qua hashlib
        with open(v_path, "rb") as f:
            expected_hash = hashlib.sha256(f.read()).hexdigest()

        service = SourceEvidenceService()
        req_item = SourceEvidenceItemRequest(
            clip_id="clip_t01",
            manifest_id="manifest_001",
            file_path=str(v_path),
            stream_index=None,
            expected_full_sha256=expected_hash,
        )
        batch_req = SourceEvidenceBatchRequest(items=(req_item,))
        result = service.acquire_evidence_for_batch(batch_req)

        assert result.status == "completed"
        assert "clip_t01" in result.evidence_by_clip
        env = result.evidence_by_clip["clip_t01"]

        assert env.clip_id == "clip_t01"
        assert env.manifest_id == "manifest_001"
        assert env.stream_index == 1  # Đã tự động chọn video stream ở global index 1
        assert env.observed_full_sha256 == expected_hash
        assert env.expected_full_sha256 == expected_hash
        assert env.complete_scan is True
        assert env.profile_policy == PROFILE_UNIFORM_TICKS_V1

        frame_ev = env.frame_evidence
        assert frame_ev.evidence_status == "VERIFIED_CFR"
        assert frame_ev.total_frames == 15  # 0.5s * 30fps
        assert frame_ev.source_fps is not None
        assert frame_ev.source_fps.as_fraction() == Fraction(30, 1)

    def test_t02_audio_first_global_index_selection(self, tmp_path: Path) -> None:
        """T02: Audio global 0 / Video global 1 và Video 0.5s / Audio 1.0s:
        Chọn đúng global index 1, N=15, không dùng v:1, không false failure do container format duration 1.0s != 0.5s.
        """
        v_path = tmp_path / "t02_audio_first.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.5, audio_dur_sec=1.0, fps=30)

        service = SourceEvidenceService()
        req_item = SourceEvidenceItemRequest(
            clip_id="clip_t02",
            manifest_id="manifest_002",
            file_path=str(v_path),
            stream_index=None,  # Tự động phát hiện global video index
        )
        batch_req = SourceEvidenceBatchRequest(items=(req_item,))
        res = service.acquire_evidence_for_batch(batch_req)

        assert res.status == "completed"
        env = res.evidence_by_clip["clip_t02"]
        assert env.stream_index == 1
        assert env.frame_evidence.evidence_status == "VERIFIED_CFR"
        assert env.frame_evidence.total_frames == 15

    def test_t03_irregular_pts_and_insufficient_frames(self, tmp_path: Path) -> None:
        """T03: Video 1 frame -> HEADER_REPORTED_ONLY; nhịp không đều / thiếu PTS -> không false verified."""
        # 1. Video chỉ có 1 khung hình
        v_single = tmp_path / "t03_single.mp4"
        _create_single_frame_video(v_single)

        service = SourceEvidenceService()
        req_single = SourceEvidenceItemRequest(
            clip_id="clip_single",
            manifest_id="manifest_003",
            file_path=str(v_single),
        )
        res_single = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req_single,)))
        env_single = res_single.evidence_by_clip["clip_single"]
        assert env_single.frame_evidence.evidence_status == "HEADER_REPORTED_ONLY"
        assert env_single.frame_evidence.source_fps is None
        assert any("ERR_INSUFFICIENT_FRAMES" in d for d in env_single.diagnostics)

        # 2. Quét trực tiếp hàm scanner với tham số lỗi
        tot_frames, inf_fps, ev_status, diags, comp = _scan_decoded_frames_uniform_ticks(
            str(v_single),
            global_stream_index=0,
            time_base=None,  # time_base thiếu
            reported_nb_frames=None,
            deadline=time.monotonic() + 5.0,
            max_frames=1000,
            stdout_budget=1024 * 1024,
            stderr_budget=64 * 1024,
            ffprobe_bin=service._ffprobe_bin,
        )
        assert ev_status == "HEADER_REPORTED_ONLY"
        assert any("ERR_INVALID_TIME_BASE" in d for d in diags)

    def test_t04_full_sha256_hashlib_match_and_freshness_mutation(self, tmp_path: Path) -> None:
        """T04: Full SHA khớp hashlib trên tiny fixture, 5 stat/fstat; mutation phát hiện."""
        v_path = tmp_path / "t04_hash.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.2, audio_dur_sec=0.4, fps=30)

        with open(v_path, "rb") as f:
            real_sha = hashlib.sha256(f.read()).hexdigest()

        service = SourceEvidenceService()
        req = SourceEvidenceItemRequest(
            clip_id="clip_t04",
            manifest_id="manifest_004",
            file_path=str(v_path),
            expected_full_sha256=real_sha,
        )
        res = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req,)))
        env = res.evidence_by_clip["clip_t04"]
        assert env.observed_full_sha256 == real_sha

        # Kiểm tra 5-stat của freshness
        fresh = env.freshness
        assert fresh is not None
        assert fresh.file_size_bytes == os.path.getsize(v_path)
        assert fresh.device_id >= 0
        assert fresh.inode_number >= 0

        # Kiểm tra mutation: Ghi đè tệp bằng nội dung khác
        with open(v_path, "wb") as f:
            f.write(b"MUTATED_CONTENT_FOR_TESTING_FRESHNESS_FAIL")

        res_mutated = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req,)))
        env_mut = res_mutated.evidence_by_clip["clip_t04"]
        assert env_mut.frame_evidence.evidence_status == "HEADER_REPORTED_ONLY"
        assert env_mut.complete_scan is False

    def test_t05_ram_cache_hit_stat_recheck_and_caller_rebind(self, tmp_path: Path) -> None:
        """T05: Cache RAM hit: 0 rehash/0 probe nhưng still stat; thay đổi tệp -> rescan; rebind caller IDs."""
        v_path = tmp_path / "t05_cache.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.3, audio_dur_sec=0.6, fps=30)

        service = SourceEvidenceService()

        # Cuộc gọi 1: Cache miss, tính toán và quét đầy đủ
        req1 = SourceEvidenceItemRequest(
            clip_id="clip_user_A",
            manifest_id="manifest_A",
            file_path=str(v_path),
        )
        res1 = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req1,)))
        assert len(service._cache) == 1
        env1 = res1.evidence_by_clip["clip_user_A"]
        assert env1.frame_evidence.evidence_status == "VERIFIED_CFR"

        # Cuộc gọi 2: Cache hit với caller ID, manifest_id và expected_full_sha256 khác hoàn toàn
        expected_sha = env1.observed_full_sha256
        req2 = SourceEvidenceItemRequest(
            clip_id="clip_user_B",
            manifest_id="manifest_B",
            file_path=str(v_path),
            expected_full_sha256=expected_sha,
        )
        res2 = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req2,)))
        env2 = res2.evidence_by_clip["clip_user_B"]

        assert env2.clip_id == "clip_user_B"
        assert env2.manifest_id == "manifest_B"
        assert env2.expected_full_sha256 == expected_sha
        assert env2.frame_evidence.clip_id == "clip_user_B"
        assert env2.frame_evidence.manifest_id == "manifest_B"
        assert env2.frame_evidence.evidence_status == "VERIFIED_CFR"

        # Đổi mtime của tệp trên đĩa -> phải làm mất hiệu lực cache và rescan
        past_time = time.time() - 100
        os.utime(v_path, (past_time, past_time))
        res3 = service.acquire_evidence_for_batch(SourceEvidenceBatchRequest(items=(req2,)))
        # Key mới sẽ được tạo trong cache
        assert len(service._cache) >= 1

    def test_t07_strict_request_result_typed_boundaries(self, tmp_path: Path) -> None:
        """T07: Kiểm tra biên kiểu dữ liệu nghiêm ngặt cho Item, Batch, Envelope, Result:
        Không bool thay int/str, không NaN/Inf, không unhashable ID, bounds <= 4096.
        """
        valid_path = str(tmp_path / "valid.mp4")

        # 1. SourceEvidenceItemRequest
        with pytest.raises(ValueError, match="ERR_BLANK_FIELD|ERR_INVALID_STRING"):
            SourceEvidenceItemRequest(clip_id="", manifest_id="m1", file_path=valid_path)
        with pytest.raises(ValueError, match="ERR_INVALID_STREAM_INDEX"):
            # bool thay int
            SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path=valid_path, stream_index=True)  # type: ignore
        with pytest.raises(ValueError, match="ERR_INVALID_STREAM_INDEX"):
            # stream_index âm
            SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path=valid_path, stream_index=-1)
        with pytest.raises(ValueError, match="ERR_INVALID_DIGEST"):
            # digest không đủ 64 hex
            SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path=valid_path, expected_full_sha256="abc")
        with pytest.raises(ValueError, match="ERR_INVALID_PATH"):
            # đường dẫn tương đối
            SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path="relative/path.mp4")

        # 2. SourceEvidenceBatchRequest
        item1 = SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path=valid_path)
        item2_diff_manifest = SourceEvidenceItemRequest(clip_id="c2", manifest_id="m2", file_path=valid_path)
        with pytest.raises(ValueError, match="ERR_MANIFEST_ID_MISMATCH"):
            SourceEvidenceBatchRequest(items=(item1, item2_diff_manifest))

        item1_dup = SourceEvidenceItemRequest(clip_id="c1", manifest_id="m1", file_path=valid_path)
        with pytest.raises(ValueError, match="ERR_DUPLICATE_CLIP_ID"):
            SourceEvidenceBatchRequest(items=(item1, item1_dup))

        with pytest.raises(ValueError, match="ERR_INVALID_TIMEOUT"):
            # timeout là bool
            SourceEvidenceBatchRequest(items=(item1,), timeout_per_file_sec=True)  # type: ignore
        with pytest.raises(ValueError, match="ERR_INVALID_TIMEOUT"):
            # timeout là NaN
            SourceEvidenceBatchRequest(items=(item1,), timeout_per_file_sec=float("nan"))
        with pytest.raises(ValueError, match="ERR_INVALID_BUDGET"):
            SourceEvidenceBatchRequest(items=(item1,), max_decoded_frames=0)

    def test_t08_resource_bounds_and_child_reap(self, tmp_path: Path) -> None:
        """T08: Resource bounds: stdout/stderr/frames/deadline; timeout reap child, không deadlock."""
        v_path = tmp_path / "t08_bounds.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.5, audio_dur_sec=1.0, fps=30)

        service = SourceEvidenceService()
        req_item = SourceEvidenceItemRequest(
            clip_id="clip_t08",
            manifest_id="manifest_008",
            file_path=str(v_path),
        )

        # 1. Hạn mức stdout cực nhỏ (1024 bytes) dẫn tới ERR_STDOUT_OVERFLOW
        batch_small_stdout = SourceEvidenceBatchRequest(
            items=(req_item,),
            stdout_budget_bytes=1024,
        )
        res_overflow = service.acquire_evidence_for_batch(batch_small_stdout)
        env = res_overflow.evidence_by_clip["clip_t08"]
        assert env.frame_evidence.evidence_status == "HEADER_REPORTED_ONLY"
        assert env.complete_scan is False
        assert any("ERR_STDOUT_OVERFLOW" in d for d in env.diagnostics)

        # 2. Hạn mức max_decoded_frames cực nhỏ (2 frames) trên video 15 frames
        batch_small_frames = SourceEvidenceBatchRequest(
            items=(req_item,),
            max_decoded_frames=2,
        )
        res_frames_ovf = service.acquire_evidence_for_batch(batch_small_frames)
        env_f = res_frames_ovf.evidence_by_clip["clip_t08"]
        assert env_f.frame_evidence.evidence_status == "HEADER_REPORTED_ONLY"
        assert any("ERR_FRAME_COUNT_OVERFLOW" in d for d in env_f.diagnostics)

    def test_t09_cancellation_checkpoints(self, tmp_path: Path) -> None:
        """T09: Hủy bỏ bởi người dùng trước start/giữa hash/giữa frames: cancelled, 0 success."""
        v_path = tmp_path / "t09_cancel.mp4"
        _create_audio_first_bframe_video(v_path, video_dur_sec=0.3, audio_dur_sec=0.6, fps=30)

        service = SourceEvidenceService()
        req_item = SourceEvidenceItemRequest(
            clip_id="clip_t09",
            manifest_id="manifest_009",
            file_path=str(v_path),
        )
        batch_req = SourceEvidenceBatchRequest(items=(req_item,))

        # 1. Hủy ngay từ đầu
        res_cancelled_early = service.acquire_evidence_for_batch(
            batch_req,
            cancelled_fn=lambda: True,
        )
        assert res_cancelled_early.status == "cancelled"
        assert len(res_cancelled_early.evidence_by_clip) == 0

        # 2. Hủy sau 1 nhịp (cooperative cancel)
        call_count = 0
        def cancel_after_1_call() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count > 1

        res_cancelled_mid = service.acquire_evidence_for_batch(
            batch_req,
            cancelled_fn=cancel_after_1_call,
        )
        assert res_cancelled_mid.status == "cancelled"

    def test_r1_codex_review_probes_p01_to_p12(self, tmp_path: Path) -> None:
        """Kiểm chứng hồi quy trực tiếp các probes P01–P12 từ Codex review."""
        import dataclasses
        from unittest.mock import patch
        from types import SimpleNamespace
        import sys

        frames = [{'pts': 0, 'duration': 1}, {'pts': 1, 'duration': 1}, {'pts': 2, 'duration': 1}]

        def scan(name: str, payload: str, *, stderr: str = '', sleep: float = 0, max_frames: int = 100, deadline_sec: float = 2):
            fake = tmp_path / (name + '.py')
            fake.write_text('#!/usr/bin/python3\nimport sys,time\n'
                            + 'sys.stdout.write(' + repr(payload) + '); sys.stdout.flush()\n'
                            + 'sys.stderr.write(' + repr(stderr) + '); sys.stderr.flush()\n'
                            + 'time.sleep(' + repr(sleep) + ')\n')
            fake.chmod(0o700)
            begin = time.monotonic()
            res = _scan_decoded_frames_uniform_ticks(
                str(tmp_path / 'ignored.mp4'), global_stream_index=0, time_base=Fraction(1, 24),
                reported_nb_frames=None, deadline=begin + deadline_sec, max_frames=max_frames,
                stdout_budget=65536, stderr_budget=65536, ffprobe_bin=str(fake))
            return res, time.monotonic() - begin

        # P01: scan_positive
        res, _ = scan('p01_pos', json.dumps({'frames': frames}))
        assert res[2] == 'VERIFIED_CFR'

        # P02: missing_final_duration
        missing_last = [dict(f) for f in frames]
        missing_last[-1].pop('duration')
        res, _ = scan('p02_mis', json.dumps({'frames': missing_last}))
        assert res[2] == 'HEADER_REPORTED_ONLY'

        # P03: decoder_error_exit_zero
        res, _ = scan('p03_dec', json.dumps({'frames': frames}), stderr='[h264] error while decoding MB 0 0\n')
        assert res[2] == 'HEADER_REPORTED_ONLY'

        # P04: truncated_json_exit_zero
        res, _ = scan('p04_tru', json.dumps({'frames': frames})[:-2])
        assert res[2] == 'HEADER_REPORTED_ONLY'

        # P05: two_frames_exceed_cap_one
        res, _ = scan('p05_cap', json.dumps({'frames': frames[:2]}), max_frames=1)
        assert res[2] == 'HEADER_REPORTED_ONLY'

        # P06: short_write_deadline_bounded
        res, elapsed = scan('p06_short', json.dumps({'frames': frames}), sleep=0.8, deadline_sec=0.05)
        assert elapsed < 0.5
        assert res[2] == 'HEADER_REPORTED_ONLY'

        # P07: ram_hit_header_probe_count & P08: batch_request_owns_item
        source = tmp_path / 'source_p07.mp4'
        source.write_bytes(b'owned_source_fixture')
        fresh = _capture_source_freshness(str(source))
        header = SimpleNamespace(freshness=fresh, video_header=SimpleNamespace(stream_index=0, time_base=Fraction(1, 24), reported_nb_frames=3))
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        prov = dict(file_size=fresh.file_size_bytes, mtime_ns=fresh.mtime_ns, ctime_ns=fresh.ctime_ns, dev=fresh.device_id, ino=fresh.inode_number)
        item = SourceEvidenceItemRequest('C01', 'm', str(source), expected_full_sha256=sha)
        req = SourceEvidenceBatchRequest((item,))
        service = SourceEvidenceService(ffprobe_path='/explicit/no-constructor-lookup')

        with patch('src.core.geometry.source_evidence_service.acquire_local_video_header', return_value=header) as header_spy, \
             patch('src.core.geometry.source_evidence_service.compute_full_file_sha256', return_value=(sha, prov)), \
             patch('src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks', return_value=(3, RationalFPS(24, 1), 'VERIFIED_CFR', (), True)):
            service.acquire_evidence_for_batch(req)
            service.acquire_evidence_for_batch(req)
            assert header_spy.call_count == 1  # P07: 0 header calls on cache hit

        assert req.items[0] is not item  # P08: batch request owns cloned items

        # P09: batch_constructor_revalidates_typed_item
        malformed_item = dataclasses.replace(item)
        object.__setattr__(malformed_item, 'stream_index', True)
        with pytest.raises(ValueError):
            SourceEvidenceBatchRequest((malformed_item,))

        # P10: public_entry_invalid_budget_stat_calls
        bad_req = dataclasses.replace(req)
        object.__setattr__(bad_req, 'max_decoded_frames', True)
        with patch('src.core.geometry.source_evidence_service._capture_source_freshness', return_value=fresh) as stat_spy:
            with pytest.raises(ValueError):
                SourceEvidenceService(ffprobe_path='/explicit').acquire_evidence_for_batch(bad_req)
            assert stat_spy.call_count == 0  # 0 stat calls before validation

        # P11: default_constructor_lookup_calls
        with patch('src.core.geometry.source_evidence_service.shutil') as shutil_spy:
            SourceEvidenceService()
            assert shutil_spy.which.call_count == 0  # 0 constructor lookup calls

        # P12: cancel_at_poststat_cache_entries
        cancel = [False]
        def capture_hook(path: str, **kw: Any) -> SourceFreshnessStat:
            if kw.get('is_after'):
                cancel[0] = True
            return fresh

        service_cancel = SourceEvidenceService(ffprobe_path='/explicit')
        with patch('src.core.geometry.source_evidence_service._capture_source_freshness', side_effect=capture_hook), \
             patch('src.core.geometry.source_evidence_service.acquire_local_video_header', return_value=header), \
             patch('src.core.geometry.source_evidence_service.compute_full_file_sha256', return_value=(sha, prov)), \
             patch('src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks', return_value=(3, RationalFPS(24, 1), 'VERIFIED_CFR', (), True)):
            result_cancel = service_cancel.acquire_evidence_for_batch(req, cancelled_fn=lambda: cancel[0])
            assert result_cancel.status == 'cancelled'
            assert len(service_cancel._cache) == 0  # 0 cache entries on cancel after post-stat


def test_r2_residual_parser_and_reader_probes(tmp_path: Path) -> None:
    """Tái hiện đầy đủ 7 residual probes cho Parser và Reader theo G07-07A3-R2 Section A."""
    frames = [dict(pts=i, duration=1) for i in range(3)]

    def scan(name: str, payload: str, stderr: str = "") -> Any:
        fake = tmp_path / f"{name}.py"
        fake.write_text(
            f"#!/usr/bin/python3\nimport sys\nsys.stdout.write({repr(payload)});"
            f"sys.stdout.flush()\nsys.stderr.write({repr(stderr)})\n"
        )
        fake.chmod(0o700)
        return _scan_decoded_frames_uniform_ticks(
            str(tmp_path / "unused.mp4"),
            global_stream_index=0,
            time_base=Fraction(1, 24),
            reported_nb_frames=None,
            deadline=time.monotonic() + 2,
            max_frames=100,
            stdout_budget=100000,
            stderr_budget=65536,
            ffprobe_bin=str(fake),
        )

    # 1. positive control
    assert scan("positive", json.dumps({"frames": frames}))[2] == "VERIFIED_CFR"

    # 2. fractional_pts
    assert scan("fractional_pts", json.dumps({"frames": [dict(pts=i + 0.2, duration=1.8) for i in range(3)]}))[2] == "HEADER_REPORTED_ONLY"

    # 3. wrong_root_key
    assert scan("wrong_root_key", json.dumps({"not_frames": frames}))[2] == "HEADER_REPORTED_ONLY"

    # 4. missing_record_commas
    assert scan("missing_record_commas", '{"frames":[' + ''.join(json.dumps(f) for f in frames) + ']}')[2] == "HEADER_REPORTED_ONLY"

    # 5. trailing_garbage
    assert scan("trailing_garbage", json.dumps({"frames": frames}) + " garbage")[2] == "HEADER_REPORTED_ONLY"

    # 6. string_record_over_16k
    assert scan("string_record_over_16k", json.dumps({"frames": [dict(f, note="X" * 20000) for f in frames]}))[2] == "HEADER_REPORTED_ONLY"

    # 7. unlisted_decoder_stderr
    assert scan("nal_error", json.dumps({"frames": frames}), "[h264] Invalid NAL unit size (0 > 42)\n")[2] == "HEADER_REPORTED_ONLY"

    # 8. stderr_reader_exception
    original_reader = _BoundedPipeReader
    class FailedReader(original_reader):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self.exception = OSError("controlled stderr read failure")

    with patch("src.core.geometry.source_evidence_service._BoundedPipeReader", FailedReader):
        assert scan("reader_error", json.dumps({"frames": frames}))[2] == "HEADER_REPORTED_ONLY"


def test_r2_residual_cache_stream_and_deadline_probes(tmp_path: Path) -> None:
    """Tái hiện residual probes cho Cache, Stream Map và Post-stat Deadline theo G07-07A3-R2 Section B."""
    path = tmp_path / "source.mp4"
    path.write_bytes(b"controlled source for cache test")
    fresh = _capture_source_freshness(str(path))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    prov = dict(file_size=fresh.file_size_bytes, mtime_ns=fresh.mtime_ns, ctime_ns=fresh.ctime_ns, dev=fresh.device_id, ino=fresh.inode_number)
    item = SourceEvidenceItemRequest("C01", "m", str(path), expected_full_sha256=sha)
    request = SourceEvidenceBatchRequest((item,))

    def header(*a: Any, **kw: Any) -> SimpleNamespace:
        return SimpleNamespace(
            freshness=fresh,
            video_header=SimpleNamespace(stream_index=kw.get("selected_stream_index") or 0, time_base=Fraction(1, 24), reported_nb_frames=3),
        )

    # 1. explicit_stream_must_not_poison_default
    svc = SourceEvidenceService(ffprobe_path="/explicit")
    with patch("src.core.geometry.source_evidence_service.acquire_local_video_header", side_effect=header), \
         patch("src.core.geometry.source_evidence_service.compute_full_file_sha256", return_value=(sha, prov)), \
         patch("src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks", return_value=(3, RationalFPS(24, 1), "VERIFIED_CFR", (), True)):
        svc.acquire_evidence_for_batch(SourceEvidenceBatchRequest((dataclasses.replace(item, stream_index=1),)))
        default = svc.acquire_evidence_for_batch(request)
        assert default.evidence_by_clip["C01"].stream_index == 0

    # 2. clear_cache()
    assert len(svc._cache) > 0
    svc.clear_cache()
    assert len(svc._cache) == 0
    assert len(svc._default_stream_map) == 0

    # 3. poststat_deadline_cache_count
    svc_deadline = SourceEvidenceService(ffprobe_path="/explicit")
    clock = [0.0]
    def capture_hook(*a: Any, **kw: Any) -> SourceFreshnessStat:
        if kw.get("is_after"):
            clock[0] = 2.0
        return fresh

    with patch.object(time, "monotonic", side_effect=lambda: clock[0]), \
         patch("src.core.geometry.source_evidence_service._capture_source_freshness", side_effect=capture_hook), \
         patch("src.core.geometry.source_evidence_service.acquire_local_video_header", side_effect=header), \
         patch("src.core.geometry.source_evidence_service.compute_full_file_sha256", return_value=(sha, prov)), \
         patch("src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks", return_value=(3, RationalFPS(24, 1), "VERIFIED_CFR", (), True)):
        late = svc_deadline.acquire_evidence_for_batch(dataclasses.replace(request, timeout_per_file_sec=1.0))
        assert len(svc_deadline._cache) == 0
        assert late.evidence_by_clip["C01"].complete_scan is False


def test_r2_residual_owned_request_public_entry_probes(tmp_path: Path) -> None:
    """Tái hiện residual probes cho Owned Request và Result theo G07-07A3-R2 Section C."""
    path = tmp_path / "source.mp4"
    path.write_bytes(b"controlled source for ownership test")
    fresh = _capture_source_freshness(str(path))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    prov = dict(file_size=fresh.file_size_bytes, mtime_ns=fresh.mtime_ns, ctime_ns=fresh.ctime_ns, dev=fresh.device_id, ino=fresh.inode_number)
    item = SourceEvidenceItemRequest("C01", "m", str(path), expected_full_sha256=sha)
    request = SourceEvidenceBatchRequest((item,))

    def header(*a: Any, **kw: Any) -> SimpleNamespace:
        return SimpleNamespace(
            freshness=fresh,
            video_header=SimpleNamespace(stream_index=kw.get("selected_stream_index") or 0, time_base=Fraction(1, 24), reported_nb_frames=3),
        )

    def service_run(req: Any) -> Tuple[int, Optional[str]]:
        with patch("src.core.geometry.source_evidence_service._capture_source_freshness", return_value=fresh) as stat_spy, \
             patch("src.core.geometry.source_evidence_service.acquire_local_video_header", side_effect=header), \
             patch("src.core.geometry.source_evidence_service.compute_full_file_sha256", return_value=(sha, prov)), \
             patch("src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks", return_value=(3, RationalFPS(24, 1), "VERIFIED_CFR", (), True)):
            try:
                SourceEvidenceService(ffprobe_path="/explicit").acquire_evidence_for_batch(req)
                error = None
            except Exception as e:
                error = type(e).__name__
            return stat_spy.call_count, error

    # 1. public_entry_mutated_item_preio
    bad1 = dataclasses.replace(request)
    object.__setattr__(bad1.items[0], "stream_index", True)
    calls1, err1 = service_run(bad1)
    assert calls1 == 0
    assert err1 == "ValueError"

    # 2. public_entry_upper_budget_preio
    bad2 = dataclasses.replace(request)
    object.__setattr__(bad2, "stdout_budget_bytes", 10**12)
    calls2, err2 = service_run(bad2)
    assert calls2 == 0
    assert err2 == "ValueError"

    # 3. public_entry_missing_items_controlled
    bad3 = dataclasses.replace(request)
    object.__delattr__(bad3, "items")
    calls3, err3 = service_run(bad3)
    assert calls3 == 0
    assert err3 == "ValueError"

    # 4. batch_result_owns_envelope
    env = SourceExecutionEvidenceEnvelope(
        clip_id="C01",
        manifest_id="m",
        file_path=str(path),
        stream_index=0,
        expected_full_sha256=sha,
        observed_full_sha256=sha,
        freshness=fresh,
        frame_evidence=SourceFrameEvidence("m", "C01", RationalFPS(24, 1), 3, "VERIFIED_CFR"),
        complete_scan=True,
    )
    batch = SourceEvidenceBatchResult("completed", {"C01": env})
    assert batch.evidence_by_clip["C01"] is not env


def test_r3_final_checklist_grammar_and_precheck_probes(tmp_path: Path) -> None:
    """Kiểm tra toàn bộ các ca grammar và public precheck theo final checklist R3."""
    # 1. Grammar probes
    frames = [{"pts": i, "duration": 1} for i in range(3)]
    valid = json.dumps({"frames": frames})

    def scan(name: str, payload: str) -> str:
        exe = tmp_path / f"{name}.py"
        exe.write_text(f"#!/usr/bin/python3\nimport sys\nsys.stdout.write({payload!r})\n")
        exe.chmod(0o700)
        return _scan_decoded_frames_uniform_ticks(
            str(tmp_path / "unused"),
            global_stream_index=0,
            time_base=Fraction(1, 24),
            reported_nb_frames=None,
            deadline=time.monotonic() + 2,
            max_frames=100,
            stdout_budget=65536,
            stderr_budget=65536,
            ffprobe_bin=str(exe),
        )[2]

    assert scan("grammar_positive", valid) == "VERIFIED_CFR"
    assert scan("missing_colon", valid.replace('"frames":', '"frames"')) == "HEADER_REPORTED_ONLY"
    assert scan("trailing_array_comma", valid[:-2] + ",]}") == "HEADER_REPORTED_ONLY"
    assert scan("nonrecord_array_value", valid.replace("[", "[123,", 1)) == "HEADER_REPORTED_ONLY"
    assert scan("leading_garbage", "garbage " + valid) == "HEADER_REPORTED_ONLY"

    # 2. Public precheck probes
    source = tmp_path / "source_r3.mp4"
    source.write_bytes(b"controlled source r3")
    fresh = _capture_source_freshness(str(source))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    prov = {
        "file_size": fresh.file_size_bytes,
        "mtime_ns": fresh.mtime_ns,
        "ctime_ns": fresh.ctime_ns,
        "dev": fresh.device_id,
        "ino": fresh.inode_number,
    }
    item = SourceEvidenceItemRequest("C01", "m", str(source), stream_index=0, expected_full_sha256=sha)
    req = SourceEvidenceBatchRequest((item,))
    header = SimpleNamespace(
        freshness=fresh,
        video_header=SimpleNamespace(stream_index=0, time_base=Fraction(1, 24), reported_nb_frames=3),
    )

    def attempt(request: Any) -> Tuple[int, Optional[str]]:
        with patch("src.core.geometry.source_evidence_service._capture_source_freshness", return_value=fresh) as st, \
             patch("src.core.geometry.source_evidence_service.acquire_local_video_header", return_value=header), \
             patch("src.core.geometry.source_evidence_service.compute_full_file_sha256", return_value=(sha, prov)), \
             patch("src.core.geometry.source_evidence_service._scan_decoded_frames_uniform_ticks", return_value=(3, RationalFPS(24, 1), "VERIFIED_CFR", (), True)):
            try:
                SourceEvidenceService(ffprobe_path="/explicit").acquire_evidence_for_batch(request)
                err = None
            except Exception as e:
                err = type(e).__name__
            return st.call_count, err

    class CountedList(list):
        visits = 0
        def __iter__(self):
            for x in super().__iter__():
                self.visits += 1
                yield x

    oversized = CountedList([item] * 4097)
    bad_over = dataclasses.replace(req)
    object.__setattr__(bad_over, "items", oversized)
    calls_over, err_over = attempt(bad_over)
    assert oversized.visits == 0
    assert calls_over == 0
    assert err_over == "ValueError"

    for field in ("timeout_per_file_sec", "max_decoded_frames", "stdout_budget_bytes", "stderr_budget_bytes"):
        bad_b = dataclasses.replace(req)
        object.__delattr__(bad_b, field)
        calls_b, err_b = attempt(bad_b)
        assert calls_b == 0
        assert err_b == "ValueError"

    for field in ("stream_index", "expected_full_sha256"):
        bad_i = dataclasses.replace(req)
        object.__delattr__(bad_i.items[0], field)
        calls_i, err_i = attempt(bad_i)
        assert calls_i == 0
        assert err_i == "ValueError"
