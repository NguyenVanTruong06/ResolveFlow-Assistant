"""tests/ui/director/test_prompt_preparation_metadata_slice.py
Kiểm thử TDD tập trung cho lát cắt tích hợp consumer siêu dữ liệu bất đồng bộ (TASK_ID: G06-06C2c2-R1).

Bao phủ toàn diện các ranh giới:
1. Core provider known NTSC/header duration maps finite float correctly, one call per unique source,
   all IDs/order/hash/10 ClipEntry keys unchanged vs controlled chronological baseline;
   None optional fields -> 0.0; huge finite Fraction/FPS overflow -> 0.0 without crash, no default 30.
2. Core providerNone legacy compatibility, wrong provider/result/typed member/path mismatch rejected;
   persistFalse 0 save/mkdir vs persistTrue default save; invalid persist/cancel callback;
   cancellation during provider, after return and at pre-save boundary 0 save/0 next-source;
   caller object mutation after return does not mutate ClipEntry.
3. Six malformed provider probes rejected with controlled ValueError (string duration, NaN, bool header index,
   as_float fake duck object, relative file_path, bool freshness size).
4. Worker fresh branch receives 3.0 timeout and CALLABLE strictbool cancel;
   real QThread.start asserts provider ran off QApplication/main thread;
   bounded test wait/owner harness with try/finally cancel/wait (never destroy a live QThread).
5. Worker metadata and explicit-cache replay make 0 acquisition calls and verify terminal success;
   fresh paths 0 shared/default manifest writes (including supplied manifest_cache_dir).
6. Terminal diagnostics & cleanup failure priority:
   - timeout carries actual clip_path (not None);
   - cleanup failure (ERR_PROBE_CLEANUP_FAILED) takes precedence over cancellation -> source_error with path;
   - ordinary cancellation -> cancelled;
   - missing tool / no video -> source_error with path.
7. Positive handoff for BOTH V1 (copy_video) and prepare_batches:
   - header-derived fields (fps, duration) reflected in manifest;
   - no CFR flag / invented fields added;
   - exact 10 ClipEntry schema fields.
8. Final source mutation & cancellation guard before terminal publication for BOTH copy_video and prepare_batches.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256
from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.local_metadata_acquisition import (
    LocalHeaderAcquisitionResult,
    SourceFreshnessStat,
)
from src.core.geometry.source_frame_evidence_contracts import VideoStreamHeader
from src.ui.director.workers.prompt_preparation_worker import (
    PromptPreparationRequest,
    PromptPreparationWorker,
)
import src.core.chronological_sorter as _cs_mod
_ORIG_GET_CLIP_SHOT_TIME = _cs_mod.get_clip_shot_time

# Registry lưu trữ tham chiếu mạnh của worker nếu xảy ra rò rỉ thread ngoài ý muốn
_RETAINED_WORKERS: list[PromptPreparationWorker] = []


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """
    Cô lập triệt để môi trường kiểm thử:
    - RESOLVEFLOW_VISUAL_CACHE_DIR và HOME hướng vào thư mục tạm tmp_path, không đụng chạm cache thật của user.
    - get_clip_shot_time và sort_clips_chronologically được kiểm soát tất định ở cả 2 module alias, loại bỏ hoàn toàn legacy ffprobe locator / subprocess.
    - Cài đặt fail-fast tripwire spy để chứng minh 0 subprocess/locator nào bị chạm tới.
    - Teardown kiểm tra và thu hồi các worker còn sót lại trong _RETAINED_WORKERS.
    """
    test_cache_dir = str(tmp_path / "visual_cache")
    os.makedirs(test_cache_dir, exist_ok=True)
    fake_home = str(tmp_path / "fake_home")
    os.makedirs(fake_home, exist_ok=True)
    monkeypatch.setenv("RESOLVEFLOW_VISUAL_CACHE_DIR", test_cache_dir)
    monkeypatch.setenv("HOME", fake_home)

    def _controlled_shot_time(p: str) -> tuple[str, str]:
        return ("2026-10-30T08:00:00", "controlled_fixture")

    # Cô lập cả 2 alias của get_clip_shot_time và sort_clips_chronologically
    monkeypatch.setattr("src.core.clip_manifest.get_clip_shot_time", _controlled_shot_time)
    monkeypatch.setattr("src.core.chronological_sorter.get_clip_shot_time", _controlled_shot_time)
    monkeypatch.setattr(
        "src.core.clip_manifest.sort_clips_chronologically",
        lambda paths: sorted(paths, key=lambda p: os.path.basename(p)),
    )
    monkeypatch.setattr(
        "src.core.chronological_sorter.sort_clips_chronologically",
        lambda paths: sorted(paths, key=lambda p: os.path.basename(p)),
    )

    # Tripwire & Spy: Bắt lỗi nếu bất kỳ phần nào của kiểm thử gọi get_ffprobe_path cho chronology
    from unittest.mock import MagicMock
    ffprobe_locator_spy = MagicMock(name="ffprobe_locator_spy")
    monkeypatch.setattr("src.core.ffmpeg_utils.get_ffprobe_path", ffprobe_locator_spy)

    yield

    # Kiểm chứng tripwire: 0 lời gọi get_ffprobe_path cho chronology trong toàn bộ feature suite
    assert ffprobe_locator_spy.call_count == 0, (
        f"TRIPWIRE_TRIGGERED: get_ffprobe_path bị gọi {ffprobe_locator_spy.call_count} lần trong feature tests!"
    )

    # Teardown: Thu hồi an toàn các worker được giữ tham chiếu
    if _RETAINED_WORKERS:
        for w in list(_RETAINED_WORKERS):
            if w.isRunning():
                w.cancel()
                w.wait(500)
            if not w.isRunning():
                _RETAINED_WORKERS.remove(w)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _create_mock_video_file(path: str, size: int = 4096, content: bytes = None) -> str:
    """Tạo file giả lập có stat hợp lệ và trả về đường dẫn chuẩn tuyệt đối."""
    abs_p = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    payload = content if content is not None else (b"MOCK_VIDEO_DATA_FOR_METADATA_SLICE_" * (size // 30 + 1))[:size]
    with open(abs_p, "wb") as f:
        f.write(payload)
    return abs_p


def _make_dummy_header_result(
    file_path: str,
    *,
    duration_fraction: Optional[Fraction] = Fraction(1001, 500),
    avg_fps: Optional[RationalFPS] = RationalFPS(30000, 1001),
    r_fps: Optional[RationalFPS] = RationalFPS(30000, 1001),
    stream_index: int = 0,
    codec_name: str = "mpeg4",
    nb_frames: Optional[int] = 60,
    time_base: Optional[Fraction] = Fraction(1, 1000),
) -> LocalHeaderAcquisitionResult:
    """Helper tạo LocalHeaderAcquisitionResult hợp lệ ràng buộc với stat tệp."""
    abs_p = os.path.abspath(file_path)
    st = os.stat(abs_p)
    freshness = SourceFreshnessStat(
        file_size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        device_id=st.st_dev,
        inode_number=st.st_ino,
    )
    header = VideoStreamHeader(
        stream_index=stream_index,
        codec_name=codec_name,
        r_frame_rate=r_fps,
        avg_frame_rate=avg_fps,
        reported_nb_frames=nb_frames,
        time_base=time_base,
    )
    return LocalHeaderAcquisitionResult(
        file_path=abs_p,
        video_header=header,
        format_duration_sec=duration_fraction,
        freshness=freshness,
    )


def _run_worker_with_bounded_harness(
    worker: PromptPreparationWorker,
    timeout_sec: float = 6.0,
) -> list[tuple[str, bool, str, dict]]:
    """
    Thực thi và chờ worker kết thúc với deadline hữu hạn (monotonic deadline)
    và khối try/finally đảm bảo thu hồi (cancel + wait bounded) tuyệt đối không rò rỉ QThread.
    Nếu join thất bại, giữ lại tham chiếu mạnh trong _RETAINED_WORKERS trước khi raise.
    """
    terminals = []
    worker.finished_signal.connect(lambda rid, ok, msg, res: terminals.append((rid, ok, msg, res)))

    start_mono = time.monotonic()
    deadline = start_mono + timeout_sec
    try:
        worker.start()
        while worker.isRunning():
            if time.monotonic() > deadline:
                worker.cancel()
                worker.wait(1000)
                raise TimeoutError(f"Worker did not terminate within {timeout_sec}s deadline")
            QTest.qWait(10)
        # Bounded flush cho signal delivery
        flush_deadline = time.monotonic() + 0.5
        while not terminals and time.monotonic() < flush_deadline:
            QTest.qWait(10)
    finally:
        if worker.isRunning():
            worker.cancel()
            worker.wait(2000)
            if worker.isRunning():
                _RETAINED_WORKERS.append(worker)
                pytest.fail("ERR_THREAD_LEAK: Worker không thể join an toàn trong teardown; đã giữ lại tham chiếu trong registry")
    return terminals


# ==============================================================================
# Case 1: Core provider known NTSC/header duration mapping & baseline integrity
# ==============================================================================
def test_case1_core_provider_known_ntsc_and_duration_mapping_and_huge_overflow(tmp_path):
    """
    1. Core provider known NTSC/header duration maps finite float correctly.
    - 1 call per unique source
    - all IDs/order/hash/10 ClipEntry keys unchanged vs controlled chronological baseline
    - None optional fields -> 0.0
    - huge finite Fraction/FPS overflow -> 0.0 without crash, no default 30.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1.mp4"), size=1024)
    v2 = _create_mock_video_file(str(tmp_path / "v2.mp4"), size=2048)
    v3_overflow = _create_mock_video_file(str(tmp_path / "v3_overflow.mp4"), size=3072)

    calls_count = {}

    def _test_provider(p: str) -> LocalHeaderAcquisitionResult:
        abs_p = os.path.abspath(p)
        calls_count[abs_p] = calls_count.get(abs_p, 0) + 1

        if "v1.mp4" in abs_p:
            # Known NTSC
            return _make_dummy_header_result(
                abs_p,
                duration_fraction=Fraction(1001, 500),  # 2.002s
                avg_fps=RationalFPS(30000, 1001),
                r_fps=RationalFPS(30000, 1001),
                nb_frames=60,
            )
        elif "v2.mp4" in abs_p:
            # None optional fields
            return _make_dummy_header_result(
                abs_p,
                duration_fraction=None,
                avg_fps=None,
                r_fps=None,
                nb_frames=None,
            )
        else:
            # Huge overflow Fraction/FPS
            return _make_dummy_header_result(
                abs_p,
                duration_fraction=Fraction(10**5000, 1),
                avg_fps=RationalFPS(10**5000, 1),
                r_fps=RationalFPS(10**5000, 1),
            )

    # Chạy qua provider
    manifest = ClipManifest.create(
        [v1, v2, v3_overflow],
        project_name="TestNTSC",
        cache_dir=str(tmp_path / "manifests"),
        metadata_provider=_test_provider,
        persist=False,
    )

    # Đúng 1 lần gọi cho mỗi nguồn duy nhất
    assert len(calls_count) == 3
    for p, cnt in calls_count.items():
        assert cnt == 1, f"Nguồn {p} bị gọi {cnt} lần (yêu cầu đúng 1)"

    assert len(manifest.clips) == 3
    c1, c2, c3 = manifest.clips

    # Kiểm tra v1: Ánh xạ chuẩn xác NTSC
    assert c1.current_path == v1
    assert c1.clip_id == "C01"
    assert c1.duration_sec == 2.002
    assert c1.fps == pytest.approx(29.97002997, rel=1e-5)
    assert c1.start_tc_frames == 0

    # Kiểm tra v2: None optional fields -> 0.0 (không bị gán 30.0)
    assert c2.current_path == v2
    assert c2.clip_id == "C02"
    assert c2.duration_sec == 0.0
    assert c2.fps == 0.0
    assert c2.start_tc_frames == 0

    # Kiểm tra v3: Huge overflow Fraction/FPS -> 0.0 không crash, không fallback 30
    assert c3.current_path == v3_overflow
    assert c3.clip_id == "C03"
    assert c3.duration_sec == 0.0
    assert c3.fps == 0.0

    # Đối chiếu đúng 10 khóa của ClipEntry (sửa lỗi đánh máy từ 11 thành đúng 10)
    expected_keys = {
        "clip_id", "content_hash", "file_name", "file_size",
        "duration_sec", "fps", "start_tc_frames", "shot_time",
        "shot_time_source", "current_path"
    }
    assert len(ClipEntry.__dataclass_fields__) == 10
    for c in manifest.clips:
        assert set(c.__dataclass_fields__.keys()) == expected_keys


# ==============================================================================
# Case 2: Core providerNone compatibility, rejection of invalid inputs & cancellation
# ==============================================================================
def test_case2_core_provider_none_compatibility_and_rejections_and_cancellation(tmp_path):
    """
    2. Core providerNone legacy compatibility, wrong provider/result/typed member/path mismatch rejected;
    persistFalse 0 save/mkdir vs persistTrue default save; invalid persist/cancel callback;
    cancellation during provider, after return and at pre-save boundary 0 save/0 next-source;
    caller object mutation after return does not mutate ClipEntry.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1.mp4"), size=1024)
    v2 = _create_mock_video_file(str(tmp_path / "v2.mp4"), size=2048)

    # 2.1 Legacy compatibility khi metadata_provider is None: fallback 30.0 được bảo toàn
    legacy_manifest = ClipManifest.create([v1], project_name="LegacyProj", metadata_provider=None, persist=False)
    assert legacy_manifest.clips[0].fps == 30.0
    assert legacy_manifest.clips[0].duration_sec == 0.0

    # 2.2 Rejection of invalid provider / results
    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_PROVIDER"):
        ClipManifest.create([v1], metadata_provider="not_a_callable")

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=lambda p: {"dict": "result"})

    # Path mismatch
    def _mismatched_provider(p: str):
        res = _make_dummy_header_result(p)
        other_path = _create_mock_video_file(str(tmp_path / "different.mp4"), size=512)
        object.__setattr__(res, "file_path", other_path)
        return res

    with pytest.raises(ValueError, match="ERR_METADATA_SOURCE_MISMATCH"):
        ClipManifest.create([v1], metadata_provider=_mismatched_provider)

    # 2.3 Invalid persist / cancel callback
    with pytest.raises(ValueError, match="ERR_INVALID_PERSIST_FLAG"):
        ClipManifest.create([v1], persist=1)  # int, không phải strict bool

    with pytest.raises(ValueError, match="ERR_INVALID_CANCELLED_FN"):
        ClipManifest.create([v1], cancelled_fn="not_callable")

    with pytest.raises(RuntimeError, match="ERR_PROBE_CANCELLED_CALLBACK"):
        ClipManifest.create([v1], cancelled_fn=lambda: 1)  # int, không phải strict bool

    # 2.4 persistFalse 0 save/mkdir vs persistTrue default save
    manifest_dir = str(tmp_path / "no_write_manifests")
    assert not os.path.exists(manifest_dir)
    m_no_write = ClipManifest.create([v1], cache_dir=manifest_dir, persist=False)
    assert not os.path.exists(manifest_dir), "persist=False không được tạo thư mục hoặc ghi đĩa!"
    assert len(m_no_write.clips) == 1

    manifest_write_dir = str(tmp_path / "write_manifests")
    m_write = ClipManifest.create([v1], cache_dir=manifest_write_dir, persist=True)
    assert os.path.exists(manifest_write_dir), "persist=True phải lưu manifest ra đĩa"
    assert len(os.listdir(manifest_write_dir)) >= 1

    # 2.5 Cancellation during provider, after return, at pre-save boundary
    # a. Cancel during provider call
    calls_before_cancel = 0

    def _cancel_mid_provider(p: str):
        nonlocal calls_before_cancel
        calls_before_cancel += 1
        raise InterruptedError("ERR_PROBE_CANCELLED: tác vụ bị hủy giữa chừng")

    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        ClipManifest.create([v1, v2], metadata_provider=_cancel_mid_provider, persist=False)

    assert calls_before_cancel == 1, "Sau khi hủy không được tiếp tục gọi nguồn tiếp theo (0 next-source)"

    # b. Cancel at pre-save boundary
    cancel_flag = False

    def _cancel_after_clips(p: str):
        nonlocal cancel_flag
        if "v2.mp4" in p:
            cancel_flag = True
        return _make_dummy_header_result(p)

    target_save_dir = str(tmp_path / "cancel_boundary_manifests")
    with pytest.raises(InterruptedError, match="ERR_PROBE_CANCELLED"):
        ClipManifest.create(
            [v1, v2],
            cache_dir=target_save_dir,
            metadata_provider=_cancel_after_clips,
            cancelled_fn=lambda: cancel_flag,
            persist=True,
        )

    assert not os.path.exists(target_save_dir)

    # 2.6 Caller object mutation after return does not mutate ClipEntry
    saved_res = _make_dummy_header_result(v1)
    m_isolated = ClipManifest.create([v1], metadata_provider=lambda p: saved_res, persist=False)
    # Mutate the caller object
    object.__setattr__(saved_res, "format_duration_sec", Fraction(99999, 1))
    assert m_isolated.clips[0].duration_sec == pytest.approx(2.002, rel=1e-3)


# ==============================================================================
# Case 2b: Six malformed provider probes rejected
# ==============================================================================
def test_case2_b_six_malformed_provider_probes(tmp_path):
    """
    Tái hiện chính xác 6 probes bất hợp lệ từ review của Codex:
    1. duration string '9.0'
    2. duration NaN
    3. header.stream_index = True (bool)
    4. avg_frame_rate with as_float (fake duck object)
    5. relative file_path
    6. freshness.file_size_bytes = False (bool)
    Tất cả bắt buộc phải bị từ chối bằng controlled ValueError (ERR_INVALID_METADATA_RESULT).
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1.mp4"), size=1024)

    # Probe 1: duration string
    def _p1(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res, "format_duration_sec", "9.0")
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p1, persist=False)

    # Probe 2: duration NaN
    def _p2(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res, "format_duration_sec", float("nan"))
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p2, persist=False)

    # Probe 3: header bool index
    def _p3(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res.video_header, "stream_index", True)
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p3, persist=False)

    # Probe 4: fps duck object
    class FakeDuckFPS:
        def as_float(self):
            return 123.0

    def _p4(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res.video_header, "avg_frame_rate", FakeDuckFPS())
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p4, persist=False)

    # Probe 5: relative result path
    def _p5(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res, "file_path", "v1.mp4")
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p5, persist=False)

    # Probe 6: freshness bool size
    def _p6(p):
        res = _make_dummy_header_result(p)
        object.__setattr__(res.freshness, "file_size_bytes", False)
        return res

    with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
        ClipManifest.create([v1], metadata_provider=_p6, persist=False)


# ==============================================================================
# Case 2c: Fifteen required member deletion probes (A)
# ==============================================================================
def test_case2_c_fifteen_required_member_probes(tmp_path):
    """
    Tất cả 15 trường bắt buộc của 3 DTO (4 Result + 6 Header + 5 Freshness) khi bị xóa
    bắt buộc phải bị từ chối bằng controlled ValueError (ERR_INVALID_METADATA_RESULT),
    tuyệt đối không để rò rỉ AttributeError/TypeError hay âm thầm chấp nhận làm giá trị rỗng/mặc định.
    Đồng thời đảm bảo persist=True không ghi bất kỳ tệp nào xuống đĩa (0 save).
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1.mp4"), size=1024)
    save_dir = str(tmp_path / "no_save_on_invalid_dto")

    # 4 trường Result
    res_fields = ["file_path", "video_header", "format_duration_sec", "freshness"]
    for f in res_fields:
        def _prov(p, field=f):
            r = _make_dummy_header_result(p)
            object.__delattr__(r, field)
            return r

        with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
            ClipManifest.create([v1], metadata_provider=_prov, cache_dir=save_dir, persist=True)
        assert not os.path.exists(save_dir), f"Result thiếu trường {f} không được phép ghi đĩa!"

    # 6 trường Header
    vh_fields = ["stream_index", "codec_name", "r_frame_rate", "avg_frame_rate", "reported_nb_frames", "time_base"]
    for f in vh_fields:
        def _prov(p, field=f):
            r = _make_dummy_header_result(p)
            object.__delattr__(r.video_header, field)
            return r

        with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
            ClipManifest.create([v1], metadata_provider=_prov, cache_dir=save_dir, persist=True)
        assert not os.path.exists(save_dir), f"Header thiếu trường {f} không được phép ghi đĩa!"

    # 5 trường Freshness
    fr_fields = ["file_size_bytes", "mtime_ns", "ctime_ns", "device_id", "inode_number"]
    for f in fr_fields:
        def _prov(p, field=f):
            r = _make_dummy_header_result(p)
            object.__delattr__(r.freshness, field)
            return r

        with pytest.raises(ValueError, match="ERR_INVALID_METADATA_RESULT"):
            ClipManifest.create([v1], metadata_provider=_prov, cache_dir=save_dir, persist=True)
        assert not os.path.exists(save_dir), f"Freshness thiếu trường {f} không được phép ghi đĩa!"


# ==============================================================================
# Case 2d: Worker rejects missing DTO attribute with source_error & known clip_path
# ==============================================================================
def test_case2_d_worker_rejects_missing_dto_attribute_with_source_error(tmp_path, qapp):
    """
    Worker xử lý khi provider trả về DTO bị thiếu trường bắt buộc:
    phải phát ra chính xác 1 source_error kèm theo clip_path đã biết,
    không gọi formatter và không bàn giao thành công.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_struct_err.mp4"), size=1024)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _broken_provider(path, **kw):
        res = _make_dummy_header_result(path)
        object.__delattr__(res, "format_duration_sec")
        return res

    req = PromptPreparationRequest(
        request_id="req_broken_dto",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_broken_provider):
        results = _run_worker_with_bounded_harness(worker)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_error"
    assert res["clip_path"] == v1
    assert "ERR_INVALID_METADATA_RESULT" in res["error"]


# ==============================================================================
# Case 3: Worker fresh branch receives 3.0 timeout, callable cancel, runs off GUI
# ==============================================================================
def test_case3_worker_fresh_branch_runs_off_gui_thread_and_timeout(tmp_path, qapp):
    """
    3. Worker fresh branch receives 3.0 timeout and CALLABLE strictbool cancel;
    real QThread.start asserts provider ran off QApplication/main thread;
    sử dụng bounded harness có try/finally cancel/wait.
    """
    v1 = _create_mock_video_file(str(tmp_path / "worker_v1.mp4"), size=1024)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại worker"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    req = PromptPreparationRequest(
        request_id="req_thread_check",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )

    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    provider_thread_id = None
    captured_timeout = None
    captured_cancelled_fn = None
    provider_called = threading.Event()

    def _spy_acquire(path, *, ffprobe_path=None, selected_stream_index=None, timeout_sec=5.0, cancelled_fn=None):
        nonlocal provider_thread_id, captured_timeout, captured_cancelled_fn
        provider_thread_id = threading.get_ident()
        captured_timeout = timeout_sec
        captured_cancelled_fn = cancelled_fn
        provider_called.set()
        return _make_dummy_header_result(path)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_spy_acquire):
        results = _run_worker_with_bounded_harness(worker, timeout_sec=5.0)

    # 1. Khẳng định provider chạy NGOÀI luồng chính QApplication
    assert provider_thread_id is not None
    assert provider_thread_id != threading.main_thread().ident, "Provider phải chạy trên QThread chạy nền!"

    # 2. Khẳng định timeout là 3.0s
    assert captured_timeout == 3.0, f"Worker phải truyền timeout_sec=3.0, nhận: {captured_timeout}"

    # 3. Khẳng định cancelled_fn là callable trả về strict bool
    assert callable(captured_cancelled_fn)
    assert type(captured_cancelled_fn()) is bool

    # 4. Worker hoàn thành thành công
    assert len(results) == 1
    assert results[0][1] is True


# ==============================================================================
# Case 4: Worker metadata and explicit-cache replay make 0 acquisition calls
# ==============================================================================
def test_case4_worker_cache_replay_zero_probes_and_fresh_zero_disk_writes(tmp_path, qapp):
    """
    4. Worker metadata and explicit-cache replay make 0 acquisition calls and verify terminal success;
    fresh paths 0 shared/default manifest writes (including supplied manifest_cache_dir).
    """
    v1 = _create_mock_video_file(str(tmp_path / "replay_v1.mp4"), size=1024)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại replay"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    probe_spy = MagicMock()

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", probe_spy):
        # 4.1 Branch 2a: Request có manifest_metadata hợp lệ -> 0 new acquisition calls và thành công
        manifest_meta_dict = {
            "project_name": "ChunDVC_Project",
            "manifest_id": "M_REUSE_123",
            "clips": [{
                "clip_id": "C01",
                "content_hash": "dummy_hash",
                "file_name": "replay_v1.mp4",
                "file_size": 1024,
                "duration_sec": 12.5,
                "fps": 24.0,
                "start_tc_frames": 0,
                "shot_time": "",
                "shot_time_source": "",
                "current_path": v1,
            }]
        }
        req_2a = PromptPreparationRequest(
            request_id="req_2a",
            generation=1,
            intent="copy_video",
            video_paths=(v1,),
            manifest_metadata=manifest_meta_dict,
        )
        worker_2a = PromptPreparationWorker(req_2a, cache_mgr=cache_mgr)
        results_2a = _run_worker_with_bounded_harness(worker_2a, timeout_sec=5.0)

        assert len(results_2a) == 1
        assert results_2a[0][1] is True, "Branch 2a phải hoàn thành thành công!"
        assert results_2a[0][3]["manifest_metadata"]["manifest_id"] == "M_REUSE_123"
        assert probe_spy.call_count == 0, "Branch 2a không được gọi probe tiêu đề!"

        # 4.2 Branch 2b: Request có manifest_cache_dir chứa manifest đĩa khớp -> 0 new acquisition calls và thành công
        disk_cache_dir = str(tmp_path / "explicit_disk_cache")
        ClipManifest.create([v1], project_name="DiskProj", cache_dir=disk_cache_dir, persist=True)

        req_2b = PromptPreparationRequest(
            request_id="req_2b",
            generation=1,
            intent="copy_video",
            project_name="DiskProj",
            video_paths=(v1,),
            manifest_cache_dir=disk_cache_dir,
        )
        worker_2b = PromptPreparationWorker(req_2b, cache_mgr=cache_mgr)
        results_2b = _run_worker_with_bounded_harness(worker_2b, timeout_sec=5.0)

        assert len(results_2b) == 1
        assert results_2b[0][1] is True, "Branch 2b phải hoàn thành thành công!"
        assert probe_spy.call_count == 0, "Branch 2b không được gọi probe tiêu đề!"

    # 4.3 Branch 2c: Fresh creation ghi 0 files vào manifest_cache_dir
    supplied_manifest_dir = str(tmp_path / "supplied_manifests_empty")
    os.makedirs(supplied_manifest_dir, exist_ok=True)

    def _dummy_acquire(path, **kw):
        return _make_dummy_header_result(path)

    req_2c = PromptPreparationRequest(
        request_id="req_2c_fresh",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        manifest_cache_dir=supplied_manifest_dir,
    )
    worker_2c = PromptPreparationWorker(req_2c, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_dummy_acquire):
        results_2c = _run_worker_with_bounded_harness(worker_2c, timeout_sec=5.0)

    assert len(results_2c) == 1
    assert results_2c[0][1] is True
    assert os.listdir(supplied_manifest_dir) == [], "Nhánh 2c fresh không được ghi file manifest vào manifest_cache_dir!"


# ==============================================================================
# Case 5: Terminal diagnostics, cleanup failure priority & cancellation
# ==============================================================================
def test_case5_terminal_diagnostics_cleanup_failure_and_cancellation(tmp_path, qapp):
    """
    5. Terminal diagnostics & cleanup failure priority:
    - Freshness mismatch TOCTOU -> source_changed;
    - Timeout probe carries actual clip_path (not None);
    - Cleanup failure (ERR_PROBE_CLEANUP_FAILED) takes precedence over cancellation -> source_error with path;
    - Ordinary cancellation -> cancelled;
    - Missing tool / no video -> source_error with path.
    """
    v1 = _create_mock_video_file(str(tmp_path / "fail_v1.mp4"), size=1024)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 10.0, "raw_subtitles": [{"start": 0, "end": 1, "text": "Thoại fail"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    # 5.1 Freshness mismatch
    def _mutated_acquire(path, **kw):
        res = _make_dummy_header_result(path)
        mutated_freshness = SourceFreshnessStat(
            file_size_bytes=999999,
            mtime_ns=res.freshness.mtime_ns,
            ctime_ns=res.freshness.ctime_ns,
            device_id=res.freshness.device_id,
            inode_number=res.freshness.inode_number,
        )
        object.__setattr__(res, "freshness", mutated_freshness)
        return res

    req = PromptPreparationRequest(
        request_id="req_mutated",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)
    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_mutated_acquire):
        results = _run_worker_with_bounded_harness(worker)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_changed"
    assert "ERR_SOURCE_CHANGED" in res["error"]

    # 5.2 Timeout probe mang actual clip_path (tái hiện terminal-probes.json probe 1)
    def _timeout_acquire(path, **kw):
        raise TimeoutError("ERR_PROBE_TIMEOUT: controlled")

    req_to = PromptPreparationRequest(
        request_id="req_timeout",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker_to = PromptPreparationWorker(req_to, cache_mgr=cache_mgr)
    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_timeout_acquire):
        results_to = _run_worker_with_bounded_harness(worker_to)

    assert len(results_to) == 1
    rid, ok, msg, res = results_to[0]
    assert ok is False
    assert msg == "source_error"
    assert res["clip_path"] == v1, f"clip_path không được là None! Nhận: {res['clip_path']}"
    assert "ERR_PROBE_TIMEOUT" in res["error"]

    # 5.3 Cleanup failure sau cancel (tái hiện terminal-probes.json probe 2)
    def _cleanup_fail_acquire(path, **kw):
        worker_cf.cancel()
        raise RuntimeError("ERR_PROBE_CLEANUP_FAILED: reader thread leak")

    req_cf = PromptPreparationRequest(
        request_id="req_cleanup_fail",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker_cf = PromptPreparationWorker(req_cf, cache_mgr=cache_mgr)
    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_cleanup_fail_acquire):
        results_cf = _run_worker_with_bounded_harness(worker_cf)

    assert len(results_cf) == 1
    rid, ok, msg, res = results_cf[0]
    assert ok is False
    assert msg == "source_error", "Cleanup failure phải ưu tiên phát source_error thay vì cancelled!"
    assert res["clip_path"] == v1
    assert "ERR_PROBE_CLEANUP_FAILED" in res["error"]

    # 5.4 Ordinary cancellation during acquisition -> cancelled
    def _cancelled_acquire(path, **kw):
        raise InterruptedError("ERR_PROBE_CANCELLED: caller đã hủy")

    req_cancel = PromptPreparationRequest(
        request_id="req_cancelled",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker_cancel = PromptPreparationWorker(req_cancel, cache_mgr=cache_mgr)
    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_cancelled_acquire):
        results_cancel = _run_worker_with_bounded_harness(worker_cancel)

    assert len(results_cancel) == 1
    rid, ok, msg, res = results_cancel[0]
    assert ok is False
    assert msg == "cancelled"

    # 5.5 Missing tool / no video stream -> source_error with clip_path
    def _missing_tool_acquire(path, **kw):
        raise FileNotFoundError("ERR_PROBE_TOOL_NOT_FOUND: ffprobe missing")

    req_tool = PromptPreparationRequest(
        request_id="req_tool",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
    )
    worker_tool = PromptPreparationWorker(req_tool, cache_mgr=cache_mgr)
    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_missing_tool_acquire):
        results_tool = _run_worker_with_bounded_harness(worker_tool)

    assert len(results_tool) == 1
    rid, ok, msg, res = results_tool[0]
    assert ok is False
    assert msg == "source_error"
    assert res["clip_path"] == v1
    assert "ERR_PROBE_TOOL_NOT_FOUND" in res["error"]


# ==============================================================================
# Case 6.1: Positive V1 copy_video handoff
# ==============================================================================
def test_case6_1_positive_handoff_v1_copy_video(tmp_path, qapp):
    """
    6.1 Positive V1 copy_video handoff: new manifest metadata reflects header-derived fields
    and source bindings while schemas unchanged (no CFR flag / frame authority added).
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_happy.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={
            "clip_dur": 15.0,
            "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thử nghiệm V1 thành công"}]
        },
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(
            path,
            duration_fraction=Fraction(15, 1),
            avg_fps=RationalFPS(25, 1),
            r_fps=RationalFPS(25, 1),
            nb_frames=375,
        )

    req = PromptPreparationRequest(
        request_id="req_happy_v1",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
        project_name="Vlog_Project",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        results = _run_worker_with_bounded_harness(worker)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is True
    assert msg == "success"

    manifest_meta = res.get("manifest_metadata")
    assert manifest_meta is not None
    assert manifest_meta["project_name"] == "Vlog_Project"
    clips = manifest_meta["clips"]
    assert len(clips) == 1
    clip_entry = clips[0]
    assert clip_entry["duration_sec"] == 15.0
    assert clip_entry["fps"] == 25.0
    assert clip_entry["current_path"] == v1
    assert "is_cfr" not in clip_entry
    assert "probe_error" not in clip_entry


# ==============================================================================
# Case 6.2: Positive prepare_batches handoff
# ==============================================================================
def test_case6_2_positive_handoff_prepare_batches(tmp_path, qapp):
    """
    6.2 Positive prepare_batches handoff: executes full batch planner & adapter pipeline,
    verifying exactly 1 success, valid batch_snapshot, source ranges, and header-derived floats.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_batch.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={
            "clip_dur": 20.0,
            "raw_subtitles": [
                {"start": 1.0, "end": 4.0, "text": "Phân đoạn 1 batch testing"},
                {"start": 6.0, "end": 9.0, "text": "Phân đoạn 2 batch testing"},
            ]
        },
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(
            path,
            duration_fraction=Fraction(20, 1),
            avg_fps=RationalFPS(25, 1),
            r_fps=RationalFPS(25, 1),
            nb_frames=500,
        )

    req = PromptPreparationRequest(
        request_id="req_happy_batch",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
        project_name="Batch_Project",
        target_batch_duration_sec=30.0,
        max_batch_duration_sec=60.0,
        max_prompt_bytes=8000,
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        results = _run_worker_with_bounded_harness(worker)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is True
    assert msg == "success"
    assert res["intent"] == "prepare_batches"

    # Thẩm định batch_snapshot
    snapshot = res.get("batch_snapshot")
    assert snapshot is not None
    assert snapshot.total_batches >= 1
    assert len(snapshot.batches) >= 1
    first_b = snapshot.batches[0]
    assert len(first_b.source_ranges) >= 1
    sr = first_b.source_ranges[0]
    assert sr.clip_id == "C01"

    # Thẩm định manifest_metadata
    manifest_meta = res.get("manifest_metadata")
    assert manifest_meta is not None
    clips = manifest_meta["clips"]
    assert len(clips) == 1
    assert clips[0]["duration_sec"] == 20.0
    assert clips[0]["fps"] == 25.0
    assert "is_cfr" not in clips[0]


# ==============================================================================
# Case 6.3: Post-format source mutation guard for copy_video (B)
# ==============================================================================
def test_case6_3_copy_video_post_format_source_mutation(tmp_path, qapp):
    """
    6.3 copy_video post-format source mutation:
    Hook StoryCopilot.generate_copilot_prompt, mutate file stat ngay sau khi render xong prompt,
    xác thực terminal nhận đúng 1 source_changed kèm clip_path, 0 success, không bàn giao prompt.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_mut_post_v1.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 15.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(path)

    from src.core.story_copilot import StoryCopilot
    orig_generate = StoryCopilot.generate_copilot_prompt
    formatter_reached = False

    def _wrap_generate(*args, **kwargs):
        nonlocal formatter_reached
        formatter_reached = True
        res = orig_generate(*args, **kwargs)
        # Đột biến mtime của tệp nguồn sau khi formatter đã chạy xong
        now = time.time() + 100
        os.utime(v1, (now, now))
        return res

    req = PromptPreparationRequest(
        request_id="req_mutate_post_v1",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        with patch.object(StoryCopilot, "generate_copilot_prompt", side_effect=_wrap_generate):
            results = _run_worker_with_bounded_harness(worker)

    assert formatter_reached is True, "Formatter phải được gọi thành công trước khi phát hiện đột biến tệp!"
    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_changed"
    assert res.get("clip_path") == v1
    assert "prompt_text" not in res


# ==============================================================================
# Case 6.4: Post-format cancellation guard for copy_video (B)
# ==============================================================================
def test_case6_4_copy_video_post_format_cancellation(tmp_path, qapp):
    """
    6.4 copy_video post-format cancellation:
    Hook StoryCopilot.generate_copilot_prompt, gọi worker.cancel() ngay sau khi render xong prompt,
    xác thực terminal nhận đúng 1 cancelled, 0 success.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_cancel_post_v1.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 15.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(path)

    from src.core.story_copilot import StoryCopilot
    orig_generate = StoryCopilot.generate_copilot_prompt
    formatter_reached = False

    def _wrap_generate_cancel(*args, **kwargs):
        nonlocal formatter_reached
        formatter_reached = True
        res = orig_generate(*args, **kwargs)
        # Hủy worker ngay tại biên sau khi formatter đã sinh prompt xong
        worker.cancel()
        return res

    req = PromptPreparationRequest(
        request_id="req_cancel_post_v1",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        with patch.object(StoryCopilot, "generate_copilot_prompt", side_effect=_wrap_generate_cancel):
            results = _run_worker_with_bounded_harness(worker)

    assert formatter_reached is True, "Formatter phải được gọi thành công trước khi hủy!"
    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "cancelled"
    assert res == {}


# ==============================================================================
# Case 6.5: Post-render source mutation guard for prepare_batches (B)
# ==============================================================================
def test_case6_5_prepare_batches_post_render_source_mutation(tmp_path, qapp):
    """
    6.5 prepare_batches post-render source mutation:
    Hook render_batch_prompt, mutate file stat ngay sau khi render xong batch prompt,
    xác thực terminal nhận đúng 1 source_changed kèm clip_path, 0 success, không bàn giao batch_snapshot authority.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_mut_post_batches.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 20.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(
            path,
            duration_fraction=Fraction(20, 1),
            avg_fps=RationalFPS(25, 1),
            r_fps=RationalFPS(25, 1),
            nb_frames=500,
        )

    import src.core.batch_planner as bp_mod
    orig_render = bp_mod.render_batch_prompt
    render_reached = False

    def _wrap_render(*args, **kwargs):
        nonlocal render_reached
        render_reached = True
        res = orig_render(*args, **kwargs)
        # Đột biến mtime của tệp nguồn sau khi render_batch_prompt chạy xong
        now = time.time() + 100
        os.utime(v1, (now, now))
        return res

    req = PromptPreparationRequest(
        request_id="req_mutate_post_batches",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        with patch("src.core.batch_planner.render_batch_prompt", side_effect=_wrap_render):
            results = _run_worker_with_bounded_harness(worker)

    assert render_reached is True, "render_batch_prompt phải được gọi thành công trước khi phát hiện đột biến tệp!"
    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_changed"
    assert res.get("clip_path") == v1
    assert "batch_snapshot" not in res


# ==============================================================================
# Case 6.6: Post-render cancellation guard for prepare_batches (B)
# ==============================================================================
def test_case6_6_prepare_batches_post_render_cancellation(tmp_path, qapp):
    """
    6.6 prepare_batches post-render cancellation:
    Hook render_batch_prompt, gọi worker.cancel() ngay sau khi render xong batch prompt,
    xác thực terminal nhận đúng 1 cancelled, 0 success.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_cancel_post_batches.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 20.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _happy_acquire(path, **kw):
        return _make_dummy_header_result(
            path,
            duration_fraction=Fraction(20, 1),
            avg_fps=RationalFPS(25, 1),
            r_fps=RationalFPS(25, 1),
            nb_frames=500,
        )

    import src.core.batch_planner as bp_mod
    orig_render = bp_mod.render_batch_prompt
    render_reached = False

    def _wrap_render_cancel(*args, **kwargs):
        nonlocal render_reached
        render_reached = True
        res = orig_render(*args, **kwargs)
        # Hủy worker ngay tại biên sau khi render_batch_prompt đã sinh prompt xong
        worker.cancel()
        return res

    req = PromptPreparationRequest(
        request_id="req_cancel_post_batches",
        generation=1,
        intent="prepare_batches",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_happy_acquire):
        with patch("src.core.batch_planner.render_batch_prompt", side_effect=_wrap_render_cancel):
            results = _run_worker_with_bounded_harness(worker)

    assert render_reached is True, "render_batch_prompt phải được gọi thành công trước khi hủy!"
    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "cancelled"
    assert res == {}


# ==============================================================================
# Case 6.7: Early-stage acquisition mutation guard (labeling honesty)
# ==============================================================================
def test_case6_7_early_stage_acquisition_source_mutation(tmp_path, qapp):
    """
    6.7 Đột biến nguồn ở giai đoạn sớm (trong lúc probe tiêu đề):
    Đột biến mtime ngay trong acquire_local_video_header callback
    được bắt bởi tiền kiểm/hậu kiểm độ tươi mới và phát ra source_changed.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_mut_early.mp4"), size=2048)
    digest, prov = compute_full_file_sha256(v1)
    cache_mgr = ScanCacheManager(cache_dir=str(tmp_path / "cache"))
    cache_mgr.save_scan_result(
        file_path=v1,
        scan_data={"clip_dur": 15.0, "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Thoại"}]},
        model_size="small",
        language="vi",
        verified_identity=(digest, prov),
    )

    def _acquire_and_mutate(path, **kw):
        res = _make_dummy_header_result(path)
        now = time.time() + 100
        os.utime(path, (now, now))
        return res

    req = PromptPreparationRequest(
        request_id="req_mutate_early",
        generation=1,
        intent="copy_video",
        video_paths=(v1,),
        whisper_model="small",
        whisper_language="vi",
    )
    worker = PromptPreparationWorker(req, cache_mgr=cache_mgr)

    with patch("src.core.geometry.local_metadata_acquisition.acquire_local_video_header", side_effect=_acquire_and_mutate):
        results = _run_worker_with_bounded_harness(worker)

    assert len(results) == 1
    rid, ok, msg, res = results[0]
    assert ok is False
    assert msg == "source_changed"


# ==============================================================================
# Case 7: Chronology isolation & fail-fast tripwire verification
# ==============================================================================
def test_case7_chronology_isolation_tripwire(tmp_path, monkeypatch):
    """
    7. Chronology isolation tripwire:
    Xác minh việc khởi tạo manifest và phân tích clip không bao giờ chạm tới
    legacy ffprobe locator hoặc spawn subprocess cho mốc thời gian (0 calls).
    Đồng thời kiểm chứng cơ chế tripwire phát hiện được nếu get_ffprobe_path bị gọi.
    """
    v1 = _create_mock_video_file(str(tmp_path / "v1_tripwire.mp4"), size=1024)

    # 1. Với fixture cô lập hiện tại: ClipManifest.create không được gọi locator
    from unittest.mock import MagicMock

    def _dummy_provider(p):
        return _make_dummy_header_result(p)

    m = ClipManifest.create([v1], metadata_provider=_dummy_provider, persist=False)
    assert len(m.clips) == 1
    assert m.clips[0].shot_time == "2026-10-30T08:00:00"
    assert m.clips[0].shot_time_source == "controlled_fixture"

    # 2. Tripwire proof (red-to-green counterexample):
    # Nếu gọi get_clip_shot_time gốc (chưa cô lập) trên file không có regex timestamp,
    # hàm gốc bắt buộc sẽ gọi get_ffprobe_path
    locator_tripwire_spy = MagicMock(return_value=None)
    monkeypatch.setattr("src.core.ffmpeg_utils.get_ffprobe_path", locator_tripwire_spy)

    # Gọi hàm gốc trực tiếp -> locator_tripwire_spy BẮT BUỘC bị gọi (bằng chứng tripwire hoạt động)
    _ORIG_GET_CLIP_SHOT_TIME(v1)
    assert locator_tripwire_spy.call_count == 1, "Tripwire chứng minh hàm gốc get_clip_shot_time có gọi locator!"
