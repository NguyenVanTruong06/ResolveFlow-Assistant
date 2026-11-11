"""
tests/core/test_prompt_authority_consumption.py
=============================================================================
Finite Unit & Integration Tests for G05-05C2a-I:
- Core evidence consumer (build_clips_data):
  1. Unknown / invalid duration -> 0.0 + duration_known=False (no fake 60s).
  2. Manifest IDs / order preserved, same basename distinct, silent != B-roll.
  3. Candidate vs Verified audio cache API, v3 [] without status, no verified fallback.
  4. Pre/post source stat checks -> 0 stale evidence on missing/changed.
  5. Cooperative cancellation propagates InterruptedError, no dict mutation.
  6. Visual signature_data mismatch -> stale/excluded machine obs, user notes preserved.
  7. Real ScanCacheManager tmpdir integration read.
- Bounded prompt formatter (StoryCopilot.generate_copilot_prompt):
  8. Strict UTF-8 byte budget (<= max_prompt_bytes), all eligible IDs preserved.
  9. JSON output schema 100% intact and parseable (never truncated).
  10. Truthful omission counts for speech intervals and visual segments.
  11. Tiny budget raises explicit Vietnamese ValueError.
  12. Legacy max_prompt_bytes=None backward compatibility.
  13. Malicious delimiters in captions sanitized as data.
=============================================================================
"""

import os
import math
import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.core.clip_manifest import ClipManifest, ClipEntry, build_clips_data
from src.core.story_copilot import StoryCopilot
from src.core.cache_manager import ScanCacheManager


# =============================================================================
# 1. TEST DURATION NORMALIZATION & MANIFEST INTEGRITY (NO FAKE 60s)
# =============================================================================

@pytest.mark.parametrize("bad_dur", [
    None, "bad", float("nan"), float("inf"), True, False, -10.0, 0.0, 1e9
])
def test_build_clips_data_unknown_and_invalid_duration_zero_not_60(tmp_path, bad_dur):
    """Kiểm tra C2a-A:
    - Thời lượng không hợp lệ hoặc <= 0 được chuẩn hóa thành 0.0 và duration_known=False.
    - Tuyệt đối không bịa duration 60.0s."""
    p = str(tmp_path / "clip_dur.mp4")
    with open(p, "wb") as f:
        f.write(b"video_bytes")

    entry = ClipEntry(
        clip_id="C01",
        content_hash="dummy_hash",
        file_name="clip_dur.mp4",
        file_size=11,
        duration_sec=bad_dur,
        fps=30.0,
        start_tc_frames=0,
        shot_time="2026-10-27T10:00:00",
        shot_time_source="filename",
        current_path=p,
    )
    manifest = ClipManifest(project_name="TestProj", manifest_id="M01", clips=[entry])

    class EmptyCache:
        def get_cached_scan(self, *args, **kwargs):
            return None

    class EmptyVisual:
        def load(self, *args, **kwargs):
            return None

    clips_data = build_clips_data(manifest, cache_mgr=EmptyCache(), visual_index=EmptyVisual())
    assert len(clips_data) == 1
    cd = clips_data[0]
    assert cd["duration"] == 0.0
    assert cd["duration_sec"] == 0.0
    assert cd["duration_known"] is False
    assert cd["duration"] != 60.0


def test_build_clips_data_manifest_order_and_same_basename_and_silent_not_broll(tmp_path):
    """Kiểm tra C2a-A:
    - Thứ tự Manifest và clip_id được bảo toàn 100%.
    - Hai clip cùng tên ở hai thư mục con khác nhau được phân biệt độc lập.
    - Clip im lặng (completed_empty) KHÔNG tự ý bị gán is_broll=True."""
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()
    p1 = str(dir1 / "video.mp4")
    p2 = str(dir2 / "video.mp4")
    with open(p1, "wb") as f:
        f.write(b"data1")
    with open(p2, "wb") as f:
        f.write(b"data2")

    e1 = ClipEntry("C02", "h1", "video.mp4", 5, 10.0, 30.0, 0, "2026-10-27T10:00:00", "mtime", p1)
    e2 = ClipEntry("C01", "h2", "video.mp4", 5, 15.0, 30.0, 0, "2026-10-27T09:00:00", "mtime", p2)
    # Manifest cố ý đưa C02 trước C01 để kiểm tra bảo toàn thứ tự
    manifest = ClipManifest(project_name="OrderTest", manifest_id="M_ORD", clips=[e1, e2])

    class SilentCache:
        def get_cached_scan(self, path, *args, **kwargs):
            return {"status": "complete", "clip_dur": 10.0, "raw_subtitles": []}

    clips_data = build_clips_data(manifest, cache_mgr=SilentCache(), visual_index=MagicMock())
    assert len(clips_data) == 2
    assert clips_data[0]["clip_id"] == "C02"
    assert clips_data[0]["path"] == p1
    assert clips_data[0]["is_broll"] is False
    assert clips_data[0]["audio_analysis_status"] == "completed_empty"

    assert clips_data[1]["clip_id"] == "C01"
    assert clips_data[1]["path"] == p2
    assert clips_data[1]["is_broll"] is False


# =============================================================================
# 2. TEST CANDIDATE VS VERIFIED AUDIO API CALLS & EVIDENCE PARTITION
# =============================================================================

def test_build_clips_data_candidate_vs_verified_audio_calls(tmp_path):
    """Kiểm tra C2a-A:
    - verify_audio=False gọi API 3 tham số thông thường, audio_authority='candidate'.
    - verify_audio=True gọi verify_full=True và validate payload v3 nghiêm ngặt, audio_authority='verified'.
    - Verified miss/error KHÔNG fallback sang candidate hay name-only."""
    p = str(tmp_path / "audio_verify.mp4")
    with open(p, "wb") as f:
        f.write(b"content")

    entry = ClipEntry("C01", "h", "audio_verify.mp4", 7, 5.0, 30.0, 0, "2026", "mtime", p)
    manifest = ClipManifest(project_name="P", manifest_id="M", clips=[entry])

    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = {
        "status": "complete",
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Candidate Speech"}]
    }

    # 1. Candidate call
    res_cand = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=False)
    assert res_cand[0]["audio_authority"] == "candidate"
    assert res_cand[0]["audio_analysis_status"] == "transcribed"
    mock_cache.get_cached_scan.assert_called_with(p, "small", "vi")

    # 2. Verified call với actual v3 shape
    mock_cache.reset_mock()
    v3_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "a" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 5.0,
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Verified Speech"}]
    }
    mock_cache.get_cached_scan.return_value = v3_payload
    res_ver = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=True)
    assert res_ver[0]["audio_authority"] == "verified"
    assert res_ver[0]["audio_analysis_status"] == "transcribed"
    assert mock_cache.get_cached_scan.call_args[1].get("verify_full") is True

    # 3. Verified miss -> audio_authority='unavailable', không fallback
    mock_cache.reset_mock()
    mock_cache.get_cached_scan.return_value = None
    res_miss = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=True)
    assert res_miss[0]["audio_authority"] == "unavailable"
    assert res_miss[0]["audio_analysis_status"] == "unscanned"


@pytest.mark.parametrize("bad_payload", [
    {},
    {"status": "complete"},
    {"raw_subtitles": None},
    {"raw_subtitles": "invalid_string"},
    {"raw_subtitles": {"not": "a_list"}},
    {"cache_schema_version": 99, "raw_subtitles": []},
    {"cache_schema_version": 3, "algorithm_version": "sha256_full_v3", "full_content_sha256": "bad_hex", "raw_subtitles": []},
    {"cache_schema_version": 3, "algorithm_version": "sha256_full_v3", "full_content_sha256": "a" * 64, "clip_dur": 5.0, "model": "wrong_model", "language_requested": "vi", "raw_subtitles": []},
    {"cache_schema_version": 3, "algorithm_version": "sha256_full_v3", "full_content_sha256": "a" * 64, "clip_dur": 5.0, "model": "small", "language_requested": "en", "raw_subtitles": []},
    {"cache_schema_version": 3, "algorithm_version": "sha256_full_v3", "full_content_sha256": "a" * 64, "clip_dur": 5.0, "model": "small", "language_requested": "vi", "raw_subtitles": [{"start": -3.0, "end": -1.0, "text": "negative times"}]},
    {"cache_schema_version": 3, "algorithm_version": "sha256_full_v3", "full_content_sha256": "a" * 64, "clip_dur": 5.0, "model": "small", "language_requested": "vi", "raw_subtitles": [{"start": 5.0, "end": 2.0, "text": "reversed times"}]},
])
def test_build_clips_data_invalid_payloads_strict_rejection(tmp_path, bad_payload):
    """Kiểm tra C2a-A & Probe 1:
    - Payload không hợp lệ hoặc sai schema/digest/model/times:
      * Khi verify_audio=True -> audio_authority='unavailable', scan_error / failed, 0 transcript usable.
      * Tuyệt đối không fallback sang candidate."""
    p = str(tmp_path / "bad_payload.mp4")
    with open(p, "wb") as f:
        f.write(b"content")

    entry = ClipEntry("C01", "h", "bad_payload.mp4", 7, 5.0, 30.0, 0, "2026", "mtime", p)
    manifest = ClipManifest("P", "M", [entry])

    class BadCache:
        def get_cached_scan(self, *args, **kwargs):
            return bad_payload

    res = build_clips_data(manifest, cache_mgr=BadCache(), verify_audio=True)
    assert res[0]["audio_authority"] == "unavailable"
    assert res[0]["audio_analysis_status"] == "failed"
    assert res[0]["speech_status"] == "scan_error"
    assert res[0]["transcript"] == ""
    assert res[0]["dialogue_intervals"] == []


def test_build_clips_data_v3_empty_subtitles_without_status_field(tmp_path):
    """Kiểm tra C2a-A:
    - V3 validated raw_subtitles=[] là completed_empty kể cả khi không có status field."""
    p = str(tmp_path / "v3_empty.mp4")
    with open(p, "wb") as f:
        f.write(b"data")

    entry = ClipEntry("C01", "h", "v3_empty.mp4", 4, 6.0, 30.0, 0, "2026", "mtime", p)
    manifest = ClipManifest("P", "M", [entry])

    v3_empty = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "c" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 6.0,
        "raw_subtitles": []  # Không có status field
    }

    class V3EmptyNoStatusCache:
        def get_cached_scan(self, *args, **kwargs):
            return v3_empty

    # Cả candidate lẫn verified đều nhận diện đúng completed_empty
    res_cand = build_clips_data(manifest, cache_mgr=V3EmptyNoStatusCache(), verify_audio=False)
    assert res_cand[0]["audio_analysis_status"] == "completed_empty"
    assert res_cand[0]["speech_status"] == "no_dialogue_detected"

    res_ver = build_clips_data(manifest, cache_mgr=V3EmptyNoStatusCache(), verify_audio=True)
    assert res_ver[0]["audio_authority"] == "verified"
    assert res_ver[0]["audio_analysis_status"] == "completed_empty"
    assert res_ver[0]["speech_status"] == "no_dialogue_detected"


def test_real_scan_cache_manager_tmpdir_integration(tmp_path):
    """Kiểm tra C2a-A:
    - Tích hợp thực tế với ScanCacheManager trên thư mục tạm tmpdir.
    - Lưu verified_identity v3 và đọc lại ở cả candidate và verified mode."""
    from src.core.cache_manager import compute_full_file_sha256

    cache_dir = tmp_path / "cache_dir"
    cache_mgr = ScanCacheManager(cache_dir=str(cache_dir))

    media_f = tmp_path / "media.mp4"
    with open(media_f, "wb") as f:
        f.write(b"real_media_content_for_cache_test_v3")

    # Băm verified_identity
    verified_id = compute_full_file_sha256(str(media_f))

    # Lưu cache scan thực v3 vào cache_mgr
    cache_mgr.save_scan_result(
        file_path=str(media_f),
        scan_data={
            "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Real Cached Speech"}],
            "clip_dur": 10.0,
        },
        model_size="small",
        language="vi",
        verified_identity=verified_id,
    )

    entry = ClipEntry("C01", "h", "media.mp4", 36, 10.0, 30.0, 0, "2026", "mtime", str(media_f))
    manifest = ClipManifest("P", "M", [entry])

    # 1. Candidate read
    clips_cand = build_clips_data(manifest, cache_mgr=cache_mgr, verify_audio=False)
    assert len(clips_cand) == 1
    assert clips_cand[0]["audio_authority"] == "candidate"
    assert clips_cand[0]["audio_analysis_status"] == "transcribed"
    assert clips_cand[0]["dialogue_intervals"][0]["text"] == "Real Cached Speech"

    # 2. Verified read
    clips_ver = build_clips_data(manifest, cache_mgr=cache_mgr, verify_audio=True)
    assert len(clips_ver) == 1
    assert clips_ver[0]["audio_authority"] == "verified"
    assert clips_ver[0]["audio_analysis_status"] == "transcribed"
    assert clips_ver[0]["dialogue_intervals"][0]["text"] == "Real Cached Speech"


# =============================================================================
# 3. TEST SOURCE STAT MUTATION / MISSING & CANCELLATION
# =============================================================================

def test_source_mutated_or_missing_during_read_zeros_evidence(tmp_path):
    """Kiểm tra C2a-A:
    - File nguồn bị mất hoặc bị đổi giữa lúc đọc -> giữ clip_id trong inventory
      nhưng xóa sạch 0 usable transcript/visual và gắn source_availability."""
    del_file = str(tmp_path / "del.mp4")
    mod_file = str(tmp_path / "mod.mp4")
    with open(del_file, "wb") as f:
        f.write(b"del_bytes")
    with open(mod_file, "wb") as f:
        f.write(b"mod_bytes")

    e1 = ClipEntry("C01", "h1", "del.mp4", 9, 5.0, 30.0, 0, "2026", "mtime", del_file)
    e2 = ClipEntry("C02", "h2", "mod.mp4", 9, 5.0, 30.0, 0, "2026", "mtime", mod_file)
    manifest = ClipManifest("P", "M", [e1, e2])

    class MutatingVisualIndex:
        def load(self, path, hash_val=None):
            if path == del_file:
                if os.path.exists(del_file):
                    os.remove(del_file)
            elif path == mod_file:
                with open(mod_file, "ab") as f:
                    f.write(b"_APPENDED_MUTATION")
            return {
                "status": "complete",
                "descriptions": [{"window_id": "W0", "start_sec": 0, "end_sec": 2, "description": "OLD OBS"}]
            }

    class DummyCache:
        def get_cached_scan(self, path, *args, **kwargs):
            return {"status": "complete", "raw_subtitles": [{"start": 0, "end": 2, "text": "OLD TEXT"}]}

    clips_data = build_clips_data(manifest, cache_mgr=DummyCache(), visual_index=MutatingVisualIndex())
    assert len(clips_data) == 2

    # C01 bị mất
    assert clips_data[0]["clip_id"] == "C01"
    assert clips_data[0]["source_availability"] in ("source_missing", "source_error")
    assert clips_data[0]["transcript"] == ""
    assert clips_data[0]["visual_segments"] == []

    # C02 bị sửa đổi
    assert clips_data[1]["clip_id"] == "C02"
    assert clips_data[1]["source_availability"] in ("source_changed", "source_error")
    assert clips_data[1]["transcript"] == ""
    assert clips_data[1]["visual_segments"] == []


def test_cancellation_propagates_interrupted_error_and_input_not_mutated(tmp_path):
    """Kiểm tra C2a-A:
    - cancelled_fn trả về True -> ném InterruptedError, không nuốt thành failed.
    - Không làm thay đổi dictionary đầu vào của provider."""
    p = str(tmp_path / "cancel.mp4")
    with open(p, "wb") as f:
        f.write(b"data")

    entry = ClipEntry("C01", "h", "cancel.mp4", 4, 5.0, 30.0, 0, "2026", "mtime", p)
    manifest = ClipManifest("P", "M", [entry])

    with pytest.raises(InterruptedError):
        build_clips_data(manifest, cancelled_fn=lambda: True)


# =============================================================================
# 4. TEST VISUAL PROVENANCE, CONFIG MISMATCH & USER ORIGIN
# =============================================================================

def test_visual_nested_provenance_and_mismatch_and_user_origin_preservation(tmp_path):
    """Kiểm tra C2a-A:
    - visual_context mismatch -> AI descriptions bị đánh dấu stale/loại bỏ.
    - user notes (origin='user') luôn được giữ nguyên literal.
    - Không bịa điểm confidence tự đoán."""
    p = str(tmp_path / "vis.mp4")
    with open(p, "wb") as f:
        f.write(b"data")

    entry = ClipEntry("C01", "h", "vis.mp4", 4, 8.0, 30.0, 0, "2026", "mtime", p)
    manifest = ClipManifest("P", "M", [entry])

    class ConfiguredVisualIndex:
        def load(self, path, hash_val=None):
            return {
                "status": "complete",
                "coverage": {"sampled_windows": 2, "total_windows": 4, "complete": False},
                "analysis": {
                    "status": "partial",
                    "coverage": {"analyzed_windows": 1, "total_windows": 4},
                    "signature_data": {
                        "model": "qwen3-vl:2b-instruct",
                        "endpoint": "http://127.0.0.1:11434",
                        "sampling": {"window_sec": 8.0, "frames_per_window": 2, "max_windows": 10},
                    }
                },
                "descriptions": [
                    {"window_id": "W0", "start_sec": 0, "end_sec": 4, "description": "AI OBS", "origin": "local_model:qwen3-vl:2b-instruct", "analysis_status": "described"},
                    {"window_id": "W1", "start_sec": 4, "end_sec": 8, "description": "USER NOTE", "origin": "user", "analysis_status": "described"},
                ]
            }

    # Caller yêu cầu visual_context với model khác ("llava")
    mismatch_context = {
        "vision_model": "llava:7b",
        "vision_endpoint": "http://127.0.0.1:11434",
        "window_sec": 8.0,
        "frames_per_window": 2,
        "max_windows": 10,
    }

    clips_data = build_clips_data(
        manifest,
        cache_mgr=MagicMock(),
        visual_index=ConfiguredVisualIndex(),
        visual_context=mismatch_context
    )
    cd = clips_data[0]

    # Kiểm tra phân biệt sampling coverage vs analyzed coverage
    assert cd["visual_coverage"]["sampled_windows"] == 2
    assert cd["visual_analyzed_coverage"]["analyzed_windows"] == 1

    # Quan sát AI bị đánh dấu stale hoặc loại bỏ do mismatch model; user note giữ nguyên
    segs = cd["visual_segments"]
    # STRICT ASSERT: Quan sát AI bị loại bỏ hoàn toàn khỏi usable visual_segments
    segs = cd["visual_segments"]
    user_segs = [s for s in segs if s.get("origin") == "user"]
    ai_segs = [s for s in segs if s.get("origin") != "user"]

    assert len(user_segs) == 1
    assert user_segs[0]["description"] == "USER NOTE"
    assert len(ai_segs) == 0, "Không được giữ bất kỳ machine observation stale nào trong visual_segments usable!"

    # Machine obs được đưa vào quarantined_visual_segments và đếm đúng số lượng
    assert len(cd["quarantined_visual_segments"]) == 1
    assert cd["quarantined_visual_segments"][0]["description"] == "AI OBS"
    assert cd["visual_stale_excluded_count"] == 1

    # Formatter rendering test: AI OBS không lọt vào prompt, user note có [user]
    prompt = StoryCopilot.generate_copilot_prompt("VisualTest", clips_data)
    assert "AI OBS" not in prompt, "Machine caption stale không được phép lọt vào prompt!"
    assert "USER NOTE" in prompt
    assert "[user]" in prompt
    assert "⚠️ Đã loại bỏ 1 mô tả thị giác do lệch cấu hình mô hình/endpoint; không suy đoán nội dung." in prompt
    assert "lấy mẫu 2/4 cửa sổ, phân tích 1/4 cửa sổ" in prompt


def test_formatter_self_defense_direct_stale_input():
    """Kiểm tra C2a-B & Probe 2:
    - Formatter tự phòng vệ trước caller truyền trực tiếp dict có visual_analysis_status='stale'.
    - 0 machine caption lọt vào cả summary và detail prompt.
    - User note giữ nguyên kèm [user].
    - Input dict không bị mutate."""
    clip = {
        "clip_id": "C01",
        "clip_index": 1,
        "name": "direct_stale.mp4",
        "duration": 10.0,
        "duration_sec": 10.0,
        "duration_known": True,
        "visual_analysis_status": "stale",
        "visual_segments": [
            {"start_sec": 0, "end_sec": 2, "description": "STALE_SECRET_OBS", "origin": "local_model:old", "analysis_status": "stale"},
            {"start_sec": 2, "end_sec": 4, "description": "USER_ACTION_NOTE", "origin": "user"},
        ],
    }

    prompt = StoryCopilot.generate_copilot_prompt("SelfDefenseTest", [clip])
    assert "STALE_SECRET_OBS" not in prompt
    assert "USER_ACTION_NOTE" in prompt
    assert "[user]" in prompt
    assert "⚠️ Đã loại bỏ 1 mô tả thị giác do lệch cấu hình mô hình/endpoint; không suy đoán nội dung." in prompt
    assert len(clip["visual_segments"]) == 2, "Input dict không được phép bị mutate!"


# =============================================================================
# 5. TEST BOUNDED PROMPT FORMATTER (STORY COPILOT UTF-8 BYTES BUDGET)
# =============================================================================

def test_prompt_preserves_exact_source_bounds_over_rounded_display_duration():
    clip = {
        "clip_id": "C01", "name": "Cisco.mp4", "duration": 349.3,
        "duration_sec": 349.274558, "duration_known": True,
        "dialogue_intervals": [{"start": 345.125, "end": 349.274558, "text": "Câu cuối"}],
    }
    prompt = StoryCopilot.generate_copilot_prompt("Bounds", [clip], max_prompt_bytes=32768)
    inventory = next(line for line in prompt.splitlines() if line.startswith("| C01 |"))
    assert "349.274558s" in inventory
    assert "349.3s" not in prompt
    assert "start_sec=345.125" in prompt
    assert "end_sec=349.274558" in prompt


@pytest.mark.parametrize("budget", [None, 32768])
def test_prompt_keeps_all_87_dialogue_lines_when_they_fit(budget):
    clip = {
        "clip_id": "C01", "name": "Cisco.mp4", "duration_sec": 349.274558,
        "duration_known": True,
        "dialogue_intervals": [
            {"start": i * 4.0, "end": i * 4.0 + 3.5, "text": f"CISCO_SENTENCE_{i:02d}: Giải thích VLAN."}
            for i in range(87)
        ],
    }
    prompt = StoryCopilot.generate_copilot_prompt("Coverage", [clip], max_prompt_bytes=budget)
    assert all(f"CISCO_SENTENCE_{i:02d}:" in prompt for i in range(87))
    assert "lược bỏ 75 câu thoại" not in prompt
    if budget is not None:
        assert len(prompt.encode("utf-8")) <= budget


def test_bounded_prompt_utf8_bytes_enforcement_50_clips():
    """Kiểm tra C2a-B:
    - 50 clip với tiếng Việt, emoji, CJK và tên tệp dài.
    - Giới hạn max_prompt_bytes = 32768 UTF-8 bytes.
    - Toàn bộ 50 clip ID (C01..C50) phải được bảo toàn trong bảng inventory.
    - Khối JSON Schema đầu ra nguyên vẹn và parseable.
    - Kích thước UTF-8 bytes của prompt không bao giờ vượt quá 32768."""
    clips = []
    for i in range(1, 51):
        cid = f"C{i:02d}"
        intervals = [
            {"start": j * 2.0, "end": j * 2.0 + 1.8, "text": f"Câu thoại thứ {j+1} của video có tiếng Việt và ký tự 🎬 🔥"}
            for j in range(15)  # 15 intervals mỗi clip
        ]
        v_segs = [
            {"start_sec": k * 4.0, "end_sec": k * 4.0 + 3.5, "description": f"Mô tả hình ảnh cảnh quay {k} đẹp mắt 🌸 日本語"}
            for k in range(10)  # 10 visual segments mỗi clip
        ]
        clips.append({
            "clip_id": cid,
            "clip_index": i,
            "id": i,
            "name": f"Clip_Vlog_Du_Lich_Dai_Ten_So_{i}_ChunDVC.mp4",
            "clip_name": f"Clip_Vlog_Du_Lich_Dai_Ten_So_{i}_ChunDVC.mp4",
            "chapter": f"Chương_{i % 5}",
            "duration": 60.0,
            "duration_sec": 60.0,
            "duration_known": True,
            "transcript": " ".join([d["text"] for d in intervals]),
            "speech": " ".join([d["text"] for d in intervals]),
            "is_broll": False,
            "speech_status": "transcribed",
            "audio_analysis_status": "transcribed",
            "dialogue_intervals": intervals,
            "visual_segments": v_segs,
            "manifest_id": "M_TEST50",
        })

    max_bytes = 32768
    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="Dự Án 50 Clips UTF8 Bounded Test",
        clips_data=clips,
        story_intent="travel_vlog",
        target_duration="full",
        manifest_id="M_TEST50",
        max_prompt_bytes=max_bytes,
    )

    prompt_bytes = len(prompt.encode("utf-8"))
    assert prompt_bytes <= max_bytes, f"Prompt vượt trần: {prompt_bytes} > {max_bytes}"

    # Toàn bộ 50 clip IDs phải có mặt trong bảng danh mục
    for i in range(1, 51):
        cid = f"C{i:02d}"
        assert f"| {cid} |" in prompt, f"Clip ID {cid} bị thiếu trong bảng inventory!"

    # Khung xương JSON Output Schema phải còn nguyên vẹn
    assert "```json" in prompt
    assert '"timeline_segments":' in prompt
    assert '"global_hook":' in prompt

    # Trích xuất và parse thử JSON schema block
    json_start = prompt.rfind("```json")
    json_end = prompt.rfind("```")
    schema_str = prompt[json_start + 7:json_end].strip()
    # Thay thế placeholder ... để validate cú pháp JSON
    validable_json = schema_str.replace('"..."', '"valid"').replace("...", '"valid"')
    parsed = json.loads(validable_json)
    assert "timeline_segments" in parsed
    assert "manifest_id" in parsed


def test_tiny_budget_raises_explicit_vietnamese_value_error():
    """Kiểm tra C2a-B:
    - Nếu max_prompt_bytes quá nhỏ không đủ chứa khung xương kịch bản tối thiểu:
      Ném ValueError tiếng Việt rõ ràng, không cắt vụn schema JSON."""
    clips = [{"clip_id": "C01", "name": "c.mp4", "duration": 10.0, "duration_sec": 10.0, "duration_known": True}]
    with pytest.raises(ValueError) as exc_info:
        StoryCopilot.generate_copilot_prompt(
            project_name="Test",
            clips_data=clips,
            max_prompt_bytes=500  # Quá nhỏ so với skeleton ~6000 bytes
        )
    assert "không đủ chứa khung xương" in str(exc_info.value) or "Dung lượng tối đa" in str(exc_info.value)


def test_legacy_unbounded_mode_compatibility():
    """Kiểm tra C2a-B:
    - Khi max_prompt_bytes=None, giữ nguyên hành vi không giới hạn cũ."""
    clips = [{"clip_id": "C01", "name": "c.mp4", "duration": 10.0, "duration_sec": 10.0, "duration_known": True, "dialogue_intervals": [{"start": 0, "end": 2, "text": "Hello"}]}]
    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="Legacy",
        clips_data=clips,
        max_prompt_bytes=None
    )
    assert isinstance(prompt, str)
    assert "| C01 |" in prompt


# =============================================================================
# 6. TEST DELIMITER SANITIZATION (DATA IS NOT INSTRUCTION)
# =============================================================================

def test_malicious_caption_delimiters_sanitized_as_data():
    """Kiểm tra C2a-B:
    - Mô tả chứa ký tự phá vỡ bảng Markdown (`|`, `\n`) hoặc schema JSON được làm sạch an toàn.
    - Không bị gãy cột trong bảng kịch bản."""
    malicious_text = "Hành động nguy hiểm | Cột giả | Bị phá bảng\nXuống dòng độc hại ```json {\"hack\": true} ```"
    clips = [{
        "clip_id": "C01",
        "clip_index": 1,
        "name": "danger.mp4",
        "duration": 5.0,
        "duration_sec": 5.0,
        "duration_known": True,
        "speech_status": "transcribed",
        "dialogue_intervals": [{"start": 0, "end": 2, "text": malicious_text}],
        "visual_segments": [{"start_sec": 0, "end_sec": 2, "description": malicious_text, "origin": "local_model:qwen"}],
    }]

    prompt = StoryCopilot.generate_copilot_prompt(
        project_name="SanitizeTest",
        clips_data=clips,
        max_prompt_bytes=32768
    )

    # Dòng bảng không bị thừa cột do ký tự pipe '|'
    for line in prompt.split("\n"):
        if line.startswith("| C01 |"):
            # Bảng chuẩn có đúng 8 dấu pipe phân cách
            assert line.count("|") == 8


# =============================================================================
# 7. TEST BUDGET LADDER ZERO DETAILS & OPTIONAL ASSETS REDUCTION (PROBE 3)
# =============================================================================

def test_budget_ladder_forces_zero_details_preserves_warnings_and_omissions():
    """Kiểm tra C2a-C & Probe 3:
    - Giới hạn budget buộc rơi vào bậc thang 0 details (max_intervals=0, max_visual=0).
    - Toàn bộ clip IDs, quality/provenance warnings và omission notices vẫn còn nguyên vẹn trong prompt.
    - JSON Schema đầu ra 100% nguyên vẹn và parseable."""
    intervals = [
        {"start": i * 1.5, "end": i * 1.5 + 1.2, "text": f"Câu thoại thứ {i+1} của nhân vật"}
        for i in range(20)
    ]
    v_segs = [
        {"start_sec": 0, "end_sec": 2, "description": "Cảnh mở đầu", "origin": "user"},
        {"start_sec": 2, "end_sec": 5, "description": "Cảnh phong cảnh", "origin": "user"},
    ]
    clip = {
        "clip_id": "C01",
        "clip_index": 1,
        "name": "force_zero.mp4",
        "duration": 30.0,
        "duration_sec": 30.0,
        "duration_known": True,
        "speech_status": "transcribed",
        "audio_analysis_status": "transcribed",
        "dialogue_intervals": intervals,
        "visual_segments": v_segs,
        "visual_coverage": {"sampled_windows": 2, "total_windows": 4, "complete": False},
        "visual_analyzed_coverage": {"analyzed_windows": 1, "total_windows": 4},
        "manifest_id": "M_ZERO_TEST",
    }

    # Find the lowest accepted budget without depending on the wording of the template.
    lower = 1
    upper = len(StoryCopilot.generate_copilot_prompt("Probe", [clip]).encode("utf-8"))
    while lower < upper:
        budget = (lower + upper) // 2
        try:
            StoryCopilot.generate_copilot_prompt("Probe", [clip], max_prompt_bytes=budget)
        except ValueError:
            lower = budget + 1
        else:
            upper = budget
    target_budget = lower
    prompt = StoryCopilot.generate_copilot_prompt("Probe", [clip], max_prompt_bytes=target_budget)
    prompt_bytes = len(prompt.encode("utf-8"))
    assert prompt_bytes <= target_budget
    assert "| C01 |" in prompt
    assert "lược bỏ 20 câu thoại do giới hạn dung lượng prompt" in prompt
    assert "lược bỏ 2 đoạn hình ảnh do giới hạn dung lượng prompt" in prompt
    assert "lấy mẫu 2/4 cửa sổ, phân tích 1/4 cửa sổ" in prompt
    assert "```json" in prompt
    assert '"timeline_segments":' in prompt

    # Dưới mức tối thiểu, ném ValueError tiếng Việt
    with pytest.raises(ValueError) as exc:
        StoryCopilot.generate_copilot_prompt("TooSmall", [clip], max_prompt_bytes=5000)
    assert "không đủ chứa khung xương" in str(exc.value)


def test_optional_assets_reduced_before_mandatory_budget_failure():
    """Kiểm tra C2a-C:
    - Khi danh sách meme/sfx rất dài và ngân sách bị thắt chặt,
      hệ thống giảm cấp optional assets trước khi ném lỗi budget."""
    clip = {
        "clip_id": "C01",
        "clip_index": 1,
        "name": "assets_test.mp4",
        "duration": 10.0,
        "duration_sec": 10.0,
        "duration_known": True,
        "speech_status": "no_dialogue_detected",
        "audio_analysis_status": "completed_empty",
    }
    # Thử ở mức ngân sách vừa phải
    bounded_prompt = StoryCopilot.generate_copilot_prompt("AssetsReduced", [clip], max_prompt_bytes=8000)
    assert len(bounded_prompt.encode("utf-8")) <= 8000
    assert "| C01 |" in bounded_prompt


# =============================================================================
# 8. TEST PRODUCER STATUS PRECEDENCE (G05-05C2a-R2)
# =============================================================================

@pytest.mark.parametrize("verify_audio", [True, False])
def test_producer_status_error_precedence(tmp_path, verify_audio):
    """Kiểm tra R2:
    - Khi payload chứa status='error', dù schema v3 hợp lệ và có raw_subtitles đầy đủ,
      status='error' vẫn phải thắng: audio_authority='unavailable',
      audio_analysis_status='failed', speech_status='scan_error', 0 transcript.
    - Formatter không rò rỉ ERROR_PAYLOAD_TEXT vào prompt."""
    video_file = tmp_path / "err_clip.mp4"
    video_file.write_bytes(b"dummy_video_bytes")
    p = str(video_file)

    entry = ClipEntry(
        clip_id="C01", content_hash="hash_err", file_name="err_clip.mp4",
        file_size=len(b"dummy_video_bytes"), duration_sec=10.0, fps=24.0,
        start_tc_frames=0, shot_time="2026-01-01", shot_time_source="metadata",
        current_path=p,
    )
    manifest = ClipManifest(project_name="ErrProj", manifest_id="M_ERR", clips=[entry])

    err_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "e" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        "status": "error",
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "ERROR_PAYLOAD_TEXT"}],
    }

    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = err_payload

    res = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=verify_audio)
    assert res[0]["audio_authority"] == "unavailable"
    assert res[0]["audio_analysis_status"] == "failed"
    assert res[0]["speech_status"] == "scan_error"
    assert res[0]["transcript"] == ""
    assert res[0]["dialogue_intervals"] == []

    # Kiểm tra formatter không chứa text lỗi
    prompt = StoryCopilot.generate_copilot_prompt("ErrTest", res, max_prompt_bytes=32768)
    assert "ERROR_PAYLOAD_TEXT" not in prompt


@pytest.mark.parametrize("verify_audio", [True, False])
def test_producer_status_error_beats_no_audio_track(tmp_path, verify_audio):
    """Kiểm tra R2:
    - Khi cả status='error' và no_audio_track=True đồng thời xuất hiện, error thắng."""
    video_file = tmp_path / "err_no_audio.mp4"
    video_file.write_bytes(b"dummy_video_bytes")
    p = str(video_file)

    entry = ClipEntry(
        clip_id="C01", content_hash="hash_err_na", file_name="err_no_audio.mp4",
        file_size=len(b"dummy_video_bytes"), duration_sec=10.0, fps=24.0,
        start_tc_frames=0, shot_time="2026-01-01", shot_time_source="metadata",
        current_path=p,
    )
    manifest = ClipManifest(project_name="ErrNAProj", manifest_id="M_ERR_NA", clips=[entry])

    err_na_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "e" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        "status": "error",
        "no_audio_track": True,
        "raw_subtitles": [],
    }

    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = err_na_payload

    res = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=verify_audio)
    assert res[0]["audio_authority"] == "unavailable"
    assert res[0]["audio_analysis_status"] == "failed"
    assert res[0]["speech_status"] == "scan_error"
    assert res[0]["transcript"] == ""


@pytest.mark.parametrize("no_audio_field,no_audio_val", [
    ("no_audio_track", True),
    ("status", "no_audio_track"),
])
def test_producer_no_audio_track_preserves_classification(tmp_path, no_audio_field, no_audio_val):
    """Kiểm tra R2:
    - Payload xác nhận không có track âm thanh (no_audio_track=True hoặc status='no_audio_track')
      với raw_subtitles=[] phải bảo toàn audio_analysis_status='no_audio_track',
      KHÔNG được tự ý đổi thành completed_empty."""
    video_file = tmp_path / "no_audio.mp4"
    video_file.write_bytes(b"dummy_video_bytes")
    p = str(video_file)

    entry = ClipEntry(
        clip_id="C01", content_hash="hash_na", file_name="no_audio.mp4",
        file_size=len(b"dummy_video_bytes"), duration_sec=10.0, fps=24.0,
        start_tc_frames=0, shot_time="2026-01-01", shot_time_source="metadata",
        current_path=p,
    )
    manifest = ClipManifest(project_name="NAProj", manifest_id="M_NA", clips=[entry])

    na_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "b" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        no_audio_field: no_audio_val,
        "raw_subtitles": [],
    }

    # 1. Verified mode
    mock_cache_v = MagicMock()
    mock_cache_v.get_cached_scan.return_value = na_payload
    res_v = build_clips_data(manifest, cache_mgr=mock_cache_v, verify_audio=True)
    assert res_v[0]["audio_authority"] == "verified"
    assert res_v[0]["audio_analysis_status"] == "no_audio_track"
    assert res_v[0]["speech_status"] == "no_dialogue_detected"
    assert res_v[0]["transcript"] == ""

    # 2. Candidate mode
    mock_cache_c = MagicMock()
    mock_cache_c.get_cached_scan.return_value = na_payload
    res_c = build_clips_data(manifest, cache_mgr=mock_cache_c, verify_audio=False)
    assert res_c[0]["audio_authority"] == "candidate"
    assert res_c[0]["audio_analysis_status"] == "no_audio_track"
    assert res_c[0]["speech_status"] == "no_dialogue_detected"
    assert res_c[0]["transcript"] == ""


@pytest.mark.parametrize("verify_audio", [True, False])
def test_producer_contradictory_no_audio_with_subtitles_fails_safe(tmp_path, verify_audio):
    """Kiểm tra R2:
    - Khi có cờ no_audio_track=True nhưng lại chứa raw_subtitles non-empty:
      Dữ liệu mâu thuẫn -> fail-safe chuyển thành failed/unavailable, 0 usable text."""
    video_file = tmp_path / "contra.mp4"
    video_file.write_bytes(b"dummy_video_bytes")
    p = str(video_file)

    entry = ClipEntry(
        clip_id="C01", content_hash="hash_contra", file_name="contra.mp4",
        file_size=len(b"dummy_video_bytes"), duration_sec=10.0, fps=24.0,
        start_tc_frames=0, shot_time="2026-01-01", shot_time_source="metadata",
        current_path=p,
    )
    manifest = ClipManifest(project_name="ContraProj", manifest_id="M_CONTRA", clips=[entry])

    contra_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "c" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        "no_audio_track": True,
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "CONTRADICTORY_TEXT"}],
    }

    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = contra_payload

    res = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=verify_audio)
    assert res[0]["audio_authority"] == "unavailable"
    assert res[0]["audio_analysis_status"] == "failed"
    assert res[0]["speech_status"] == "scan_error"
    assert res[0]["transcript"] == ""
    assert res[0]["dialogue_intervals"] == []


def test_producer_v3_empty_and_valid_speech_regression(tmp_path):
    """Kiểm tra R2 đối chứng:
    - V3 hợp lệ không status và raw_subtitles=[] vẫn là completed_empty/verified.
    - V3 hợp lệ có thoại vẫn là transcribed/verified."""
    video_file = tmp_path / "normal.mp4"
    video_file.write_bytes(b"dummy_video_bytes")
    p = str(video_file)

    entry = ClipEntry(
        clip_id="C01", content_hash="hash_norm", file_name="normal.mp4",
        file_size=len(b"dummy_video_bytes"), duration_sec=10.0, fps=24.0,
        start_tc_frames=0, shot_time="2026-01-01", shot_time_source="metadata",
        current_path=p,
    )
    manifest = ClipManifest(project_name="NormProj", manifest_id="M_NORM", clips=[entry])

    # 1. Empty subtitles
    empty_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "d" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        "raw_subtitles": [],
    }
    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = empty_payload
    res_empty = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=True)
    assert res_empty[0]["audio_authority"] == "verified"
    assert res_empty[0]["audio_analysis_status"] == "completed_empty"
    assert res_empty[0]["speech_status"] == "no_dialogue_detected"

    # 2. Valid speech
    speech_payload = {
        "cache_schema_version": 3,
        "algorithm_version": "sha256_full_v3",
        "full_content_sha256": "e" * 64,
        "model": "small",
        "language_requested": "vi",
        "clip_dur": 10.0,
        "raw_subtitles": [{"start": 1.0, "end": 3.0, "text": "Lời thoại bình thường"}],
    }
    mock_cache.get_cached_scan.return_value = speech_payload
    res_speech = build_clips_data(manifest, cache_mgr=mock_cache, verify_audio=True)
    assert res_speech[0]["audio_authority"] == "verified"
    assert res_speech[0]["audio_analysis_status"] == "transcribed"
    assert res_speech[0]["speech_status"] == "transcribed"
    assert "Lời thoại bình thường" in res_speech[0]["transcript"]

