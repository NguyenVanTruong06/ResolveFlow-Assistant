"""
tests/core/test_batch_evidence_adapter.py
Unit & contract tests for src.core.batch_evidence_adapter (G06-06A2-I).
Pure in-memory finite test suite with strict assertions, zero I/O, zero Qt, zero models.
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional
import pytest

from src.core.batch_planner import (
    BatchBudgetConfig,
    EvidenceWindowSummary,
    partition_manifest_into_batches,
    render_batch_prompt,
)
from src.core.batch_evidence_adapter import (
    AdapterConfig,
    AdapterIssue,
    AdaptedBatchSnapshot,
    adapt_clips_data_to_batch_analysis_cache,
)


@dataclass(frozen=True)
class MockClipEntry:
    clip_id: str
    content_hash: str
    file_name: str
    duration_sec: float
    file_size: int = 1048576
    fps: float = 30.0
    start_tc_frames: int = 0
    shot_time: str = "2026-10-28T10:00:00"
    shot_time_source: str = "filename"
    current_path: str = "/media/test/clip.mp4"


@dataclass(frozen=True)
class MockClipManifest:
    project_name: str
    manifest_id: str
    clips: List[MockClipEntry]


# ==============================================================================
# NHÓM 1: VERIFIED SPEECH + MACHINE VISUAL + USER POINT & A1 INTEGRATION
# ==============================================================================

def test_verified_speech_and_machine_visual_and_user_point():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[
            MockClipEntry(clip_id="C01", content_hash="hash01", file_name="clip01.mp4", duration_sec=10.0),
        ],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [
                {"start": 1.0, "end": 3.0, "text": "Hello world"},
                {"start_sec": 4.0, "end_sec": 6.0, "text": "Second sentence"},
            ],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "start_sec": 1.5,
                    "end_sec": 3.5,
                    "description": "Person speaking to camera",
                    "origin": "local_model:gemini",
                    "analysis_status": "described",
                    "observation_kind": "interval",
                    "window_id": "w0",
                },
                {
                    "time_sec": 7.5,
                    "end_sec": None,
                    "description": "Important cut marker",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                    "window_id": "w1",
                },
            ],
            "quarantined_visual_segments": [],
            "visual_stale_excluded_count": 0,
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)

    assert isinstance(snapshot, AdaptedBatchSnapshot)
    assert snapshot.manifest_id == "M01"
    assert snapshot.word_count_method == "whitespace_estimate"
    assert len(snapshot.issues) == 0

    c01 = snapshot.analysis_cache["C01"]
    assert c01["speech_status"] == "transcribed"
    assert c01["has_audio"] is True
    assert len(c01["windows"]) == 4

    # Speech window 0
    w_sp0 = c01["windows"][0]
    assert w_sp0.window_id == "C01:speech:0"
    assert w_sp0.start_sec == 1.0
    assert w_sp0.end_sec == 3.0
    assert w_sp0.speech_summary == "Hello world"
    assert w_sp0.speech_word_count == 2
    assert w_sp0.has_audio is True
    assert w_sp0.is_sampled is False
    assert w_sp0.status == "transcribed"
    assert w_sp0.origin == "whisper"
    assert w_sp0.visual_action == ""

    # Speech window 1
    w_sp1 = c01["windows"][1]
    assert w_sp1.window_id == "C01:speech:1"
    assert w_sp1.start_sec == 4.0
    assert w_sp1.end_sec == 6.0
    assert w_sp1.speech_summary == "Second sentence"
    assert w_sp1.speech_word_count == 2

    # Visual window
    w_vis = c01["windows"][2]
    assert w_vis.window_id == "C01:visual:0"
    assert w_vis.start_sec == 1.5
    assert w_vis.end_sec == 3.5
    assert w_vis.visual_action == "Person speaking to camera"
    assert w_vis.origin == "local_model:gemini"
    assert w_vis.status == "described"
    assert w_vis.has_audio is None
    assert w_vis.is_sampled is True

    # User point window
    w_usr = c01["windows"][3]
    assert w_usr.window_id == "C01:user:1"
    assert w_usr.start_sec == 7.5
    assert w_usr.end_sec == 7.5
    assert w_usr.visual_action == "Important cut marker"
    assert w_usr.origin == "user"
    assert w_usr.status == "needs_review"
    assert w_usr.has_audio is None
    assert w_usr.is_sampled is True

    # Integration thông suốt với A1 batch planner & renderer
    plan = partition_manifest_into_batches(manifest, snapshot.analysis_cache)
    assert plan.planned_clip_count == 1
    assert len(plan.batches) == 1
    assert len(plan.batches[0].items[0].windows) == 4

    rendered = render_batch_prompt(plan.batches[0])
    assert "Hello world" in rendered
    assert "Important cut marker" in rendered


# ==============================================================================
# NHÓM 2: AUDIO AUTHORITY & ABSENT / SHORT CLIPS RESTORATION
# ==============================================================================

def test_verified_no_audio_vs_completed_empty_vs_candidate_vs_missing():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[
            MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=5.0),
            MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=5.0),
            MockClipEntry(clip_id="C03", content_hash="h3", file_name="c3.mp4", duration_sec=5.0),
            MockClipEntry(clip_id="C04", content_hash="h4", file_name="c4.mp4", duration_sec=5.0),
        ],
    )
    clips_data = [
        # C01: verified no_audio_track -> has_audio=False
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "no_audio_track",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
        },
        # C02: verified completed_empty -> has_audio=None (Codex SSOT)
        {
            "clip_id": "C02",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
        },
        # C03: candidate -> has_audio=None, 0 speech windows, issue recorded
        {
            "clip_id": "C03",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "candidate",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Unverified speech"}],
        },
        # C04: unavailable -> has_audio=None, 0 speech windows
        {
            "clip_id": "C04",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "unavailable",
            "audio_analysis_status": "unscanned",
            "speech_status": "unscanned",
            "dialogue_intervals": [],
        },
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)

    assert snapshot.analysis_cache["C01"]["has_audio"] is False
    assert snapshot.analysis_cache["C01"]["speech_status"] == "no_audio_track"

    assert snapshot.analysis_cache["C02"]["has_audio"] is None
    assert snapshot.analysis_cache["C02"]["speech_status"] == "completed_empty"

    assert snapshot.analysis_cache["C03"]["has_audio"] is None
    assert len(snapshot.analysis_cache["C03"]["windows"]) == 0
    assert any(iss.clip_id == "C03" and iss.issue_type == "AUDIO_UNVERIFIED" for iss in snapshot.issues)

    assert snapshot.analysis_cache["C04"]["has_audio"] is None
    assert snapshot.analysis_cache["C04"]["speech_status"] == "unscanned"


def test_short_clip_absent_and_missing_producer_rows_restored():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[
            MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=0.75),
            MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=5.0),
            MockClipEntry(clip_id="C03", content_hash="h3", file_name="c3.mp4", duration_sec=10.0),
        ],
    )
    # clips_data chỉ chứa C02; C01 bị build_clips_data bỏ do dur < 1s; C03 vắng mặt vì lý do khác
    clips_data = [
        {
            "clip_id": "C02",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)

    # Đảm bảo giữ đúng thứ tự và không drop clip nào
    assert list(snapshot.analysis_cache.keys()) == ["C01", "C02", "C03"]

    # C01 (short clip)
    c01 = snapshot.analysis_cache["C01"]
    assert c01["has_audio"] is None
    assert c01["speech_status"] == "unscanned"
    assert c01["windows"] == ()
    assert any(iss.clip_id == "C01" and iss.issue_type == "SHORT_CLIP_UNSCANNED" for iss in snapshot.issues)

    # C03 (absent producer row)
    c03 = snapshot.analysis_cache["C03"]
    assert c03["has_audio"] is None
    assert c03["speech_status"] == "unscanned"
    assert c03["windows"] == ()
    assert any(iss.clip_id == "C03" and iss.issue_type == "MISSING_PRODUCER_ENTRY" for iss in snapshot.issues)


# ==============================================================================
# NHÓM 3: AUDIO ERROR / CONTRADICTION & SOURCE AVAILABILITY PRECEDENCE
# ==============================================================================

def test_audio_contradiction_rejects_audio_keeps_valid_visual():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=5.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "no_audio_track",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Impossible speech"}],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "start_sec": 0.5,
                    "end_sec": 1.5,
                    "description": "Visual scene",
                    "origin": "local_model:x",
                    "analysis_status": "described",
                    "observation_kind": "interval",
                }
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    # Audio bị reject do mâu thuẫn dữ liệu
    assert c01["has_audio"] is None
    assert c01["speech_status"] == "scan_error"
    assert any(iss.clip_id == "C01" and iss.issue_type == "AUDIO_CONTRADICTION" for iss in snapshot.issues)

    # Visual hợp lệ vẫn được giữ nguyên
    assert len(c01["windows"]) == 1
    assert c01["windows"][0].visual_action == "Visual scene"


def test_audio_error_rejects_speech_keeps_valid_visual():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=5.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "unavailable",
            "audio_analysis_status": "failed",
            "speech_status": "scan_error",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Stale transcript"}],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "start_sec": 0.5,
                    "end_sec": 1.5,
                    "description": "Valid visual",
                    "origin": "local_model:x",
                    "analysis_status": "described",
                    "observation_kind": "interval",
                }
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    assert c01["has_audio"] is None
    assert c01["speech_status"] == "scan_error"
    assert len(c01["windows"]) == 1
    assert c01["windows"][0].visual_action == "Valid visual"


@pytest.mark.parametrize("source_status", ["source_missing", "source_changed", "source_error", "unavailable"])
def test_source_unavailability_rejects_all_evidence(source_status):
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=5.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 5.0,
            "duration_known": True,
            "source_availability": source_status,
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Old text"}],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "time_sec": 1.0,
                    "end_sec": None,
                    "description": "User note",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                }
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    assert c01["has_audio"] is None
    assert c01["speech_status"] == source_status
    assert len(c01["windows"]) == 0
    assert any(iss.clip_id == "C01" and source_status.upper() in iss.issue_type for iss in snapshot.issues)


# ==============================================================================
# NHÓM 4: VISUAL STATUS, USER NOTES & UNIQUE NAMESPACING
# ==============================================================================

def test_top_visual_status_stale_blocks_machine_retains_user_notes():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
            "visual_status": "analyzed",
            "visual_analysis_status": "stale",
            "visual_segments": [
                {
                    "time_sec": 3.0,
                    "end_sec": None,
                    "description": "User confirmed marker",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                }
            ],
            "quarantined_visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 4.0,
                    "description": "Stale machine observation",
                    "origin": "local_model:old",
                    "analysis_status": "stale",
                }
            ],
            "visual_stale_excluded_count": 1,
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    # Machine stale bị chặn hoàn toàn, văn bản quarantined không lọt vào usable windows
    assert len(c01["windows"]) == 1
    assert c01["windows"][0].origin == "user"
    assert c01["windows"][0].visual_action == "User confirmed marker"
    assert "Stale machine observation" not in [w.visual_action for w in c01["windows"]]


def test_machine_individual_status_filtering():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 2.0,
                    "description": "Seg 1 needs review",
                    "origin": "local_model:x",
                    "analysis_status": "needs_review",
                    "observation_kind": "interval",
                },
                {
                    "start_sec": 3.0,
                    "end_sec": 4.0,
                    "description": "Seg 2 error",
                    "origin": "local_model:x",
                    "analysis_status": "error",
                    "observation_kind": "interval",
                },
                {
                    "start_sec": 5.0,
                    "end_sec": 6.0,
                    "description": "Seg 3 unknown",
                    "origin": "local_model:x",
                    "analysis_status": "unknown",
                    "observation_kind": "interval",
                },
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    # Chỉ seg 1 được nhận, seg 2 & seg 3 bị loại có issue
    assert len(c01["windows"]) == 1
    assert c01["windows"][0].status == "needs_review"
    assert c01["windows"][0].visual_action == "Seg 1 needs review"


def test_same_producer_window_id_produces_unique_output_window_ids():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 2.0,
                    "description": "Seg A",
                    "origin": "local_model:x",
                    "analysis_status": "described",
                    "window_id": "w0",
                },
                {
                    "start_sec": 3.0,
                    "end_sec": 4.0,
                    "description": "Seg B",
                    "origin": "local_model:x",
                    "analysis_status": "described",
                    "window_id": "w0",  # Trùng producer window_id
                },
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    assert len(c01["windows"]) == 2
    assert c01["windows"][0].window_id == "C01:visual:0"
    assert c01["windows"][1].window_id == "C01:visual:1"
    assert c01["windows"][0].window_id != c01["windows"][1].window_id


# ==============================================================================
# NHÓM 5: ZERO CLAMPING, BOUNDS & STRICT SCHEMA VALIDATION
# ==============================================================================

def test_zero_clamping_on_out_of_bounds_intervals_and_points():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [
                # Valid: [1.0, 3.0]
                {"start": 1.0, "end": 3.0, "text": "Valid interval"},
                # OOB: end 10.01 > duration 10.0 (drift 0.01s) -> BỊ LOẠI, KHÔNG CLAMP
                {"start": 8.0, "end": 10.01, "text": "Drift interval"},
                # OOB: start >= end
                {"start": 5.0, "end": 4.0, "text": "Reversed interval"},
                # OOB: negative start
                {"start": -0.5, "end": 2.0, "text": "Negative interval"},
                # Valid boundary: end == duration 10.0 is VALID for interval
                {"start": 7.0, "end": 10.0, "text": "Touching boundary interval"},
            ],
            "visual_status": "analyzed",
            "visual_analysis_status": "described",
            "visual_segments": [
                # Point at end: t == 10.0 is STRICTLY INVALID for point (0 <= t < duration) -> BỊ LOẠI
                {
                    "time_sec": 10.0,
                    "end_sec": None,
                    "description": "Point at exact end",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                },
                # Valid point: t == 9.999
                {
                    "time_sec": 9.999,
                    "end_sec": None,
                    "description": "Point near end",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                },
            ],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    # Chỉ có 2 intervals hợp lệ ([1.0, 3.0] và [7.0, 10.0]) + 1 point hợp lệ (9.999)
    assert len(c01["windows"]) == 3
    valid_summaries = [w.speech_summary for w in c01["windows"] if w.speech_summary]
    assert valid_summaries == ["Valid interval", "Touching boundary interval"]

    # Kiểm tra structured issues được ghi nhận đầy đủ với thông tin gốc
    oob_issues = [iss for iss in snapshot.issues if iss.issue_type == "WINDOW_OUT_OF_BOUNDS"]
    assert len(oob_issues) == 4  # drift (10.01), reversed (5.0, 4.0), negative (-0.5), point at end (10.0)

    drift_iss = next(i for i in oob_issues if i.original_end_sec == 10.01)
    assert drift_iss.original_start_sec == 8.0
    assert drift_iss.record_kind == "speech"


def test_unknown_manifest_duration_rejects_timed_windows():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=0.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 0.0,
            "duration_known": False,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Speech"}],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    c01 = snapshot.analysis_cache["C01"]

    assert len(c01["windows"]) == 0
    assert any(iss.clip_id == "C01" and iss.issue_type == "ERR_UNKNOWN_DURATION" for iss in snapshot.issues)


def test_strict_type_and_schema_validation_failures():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )

    # 1. Foreign clip_id
    with pytest.raises(ValueError, match="ERR_FOREIGN_CLIP_ID"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [{"clip_id": "C99", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True}],
        )

    # 2. Duplicate clip_id
    with pytest.raises(ValueError, match="ERR_DUPLICATE_CLIP_ID"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True},
                {"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True},
            ],
        )

    # 3. Mismatched manifest_id
    with pytest.raises(ValueError, match="ERR_MANIFEST_ID_MISMATCH"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [{"clip_id": "C01", "manifest_id": "WRONG_M", "duration_sec": 10.0, "duration_known": True}],
        )

    # 4. Non-finite float (NaN)
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "dialogue_intervals": [{"start": float("nan"), "end": 2.0, "text": "T"}],
                }
            ],
        )

    # 5. Boolean passed as numeric timestamp
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "dialogue_intervals": [{"start": True, "end": 2.0, "text": "T"}],
                }
            ],
        )

    # 6. Conflicting start_sec and time_sec
    with pytest.raises(ValueError, match="ERR_CONFLICTING_TIMESTAMPS"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "visual_status": "analyzed",
                    "visual_analysis_status": "described",
                    "visual_segments": [
                        {
                            "start_sec": 1.0,
                            "time_sec": 2.0,
                            "end_sec": 3.0,
                            "description": "Conflict",
                            "origin": "user",
                        }
                    ],
                }
            ],
        )


# ==============================================================================
# NHÓM 6: CONTEXT METADATA, CANONICAL SIGNATURES & IMMUTABILITY
# ==============================================================================

def test_context_metadata_and_canonical_signatures():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
        }
    ]

    # Không có context_metadata
    snap1 = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert snap1.analysis_cache["C01"]["full_content_sha256"] is None
    assert isinstance(snap1.context_signature, str) and len(snap1.context_signature) == 64

    # Cung cấp context_metadata hợp lệ
    full_sha = "a" * 64
    context = {"C01": {"full_content_sha256": full_sha, "analysis_signature": "custom_sig_123"}}
    snap2 = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data, context_metadata=context)

    assert snap2.analysis_cache["C01"]["full_content_sha256"] == full_sha
    # analysis_signature trong analysis_cache luôn là generated semantic signature
    assert snap2.analysis_cache["C01"]["analysis_signature"] != "custom_sig_123"
    assert len(snap2.analysis_cache["C01"]["analysis_signature"]) == 64
    assert snap2.audit_by_clip["C01"]["supplied_signature"] == "custom_sig_123"
    # Thay đổi context làm thay đổi analysis_signature và context_signature
    assert snap2.analysis_cache["C01"]["analysis_signature"] != snap1.analysis_cache["C01"]["analysis_signature"]
    assert snap2.context_signature != snap1.context_signature

    # Foreign key trong context_metadata
    with pytest.raises(ValueError, match="ERR_UNKNOWN_CONTEXT_CLIP_ID"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest, clips_data, context_metadata={"C99": {"full_content_sha256": full_sha}}
        )

    # Invalid sha format
    with pytest.raises(ValueError, match="ERR_INVALID_CONTEXT_METADATA"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest, clips_data, context_metadata={"C01": {"full_content_sha256": "not_64_hex"}}
        )


def test_immutability_and_zero_input_mutation():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    original_dict = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "completed_empty",
        "speech_status": "no_dialogue_detected",
        "dialogue_intervals": [],
    }
    clips_data = [original_dict]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)

    # Mutate input dict
    original_dict["speech_status"] = "mutated_status"
    clips_data.append({"clip_id": "C02"})

    # Snapshot không bị ảnh hưởng
    assert snapshot.analysis_cache["C01"]["speech_status"] == "completed_empty"
    assert "C02" not in snapshot.analysis_cache

    # Snapshot là immutable
    with pytest.raises(Exception):
        snapshot.analysis_cache["C01"] = {}  # Read-only mapping

    with pytest.raises(Exception):
        snapshot.issues = ()  # Frozen dataclass


# ==============================================================================
# NHÓM 7: INDIVISIBLE EVIDENCE LIMIT & ADAPTER CONFIG CAPS
# ==============================================================================

def test_large_evidence_preserves_text_and_propagates_a1_budget_error():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    huge_text = "A" * 35000  # Vượt quá prompt_bytes_limit 32768
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 5.0, "text": huge_text}],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)

    # Adapter không tự ý truncate hay drop window
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 1
    assert snapshot.analysis_cache["C01"]["windows"][0].speech_summary == huge_text

    # Khi đưa sang A1 batch planner, A1 sẽ ném ERR_ITEM_EXCEEDS_PROMPT_BUDGET
    with pytest.raises(ValueError, match="ERR_ITEM_EXCEEDS_PROMPT_BUDGET"):
        partition_manifest_into_batches(manifest, snapshot.analysis_cache)


def test_adapter_config_caps_enforced():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[
            MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0),
            MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=10.0),
        ],
    )
    clips_data = [
        {"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True},
        {"clip_id": "C02", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True},
    ]

    # max_input_records = 1 mà có 2 clips_data
    cfg = AdapterConfig(max_input_records=1)
    with pytest.raises(ValueError, match="ERR_ADAPTER_LIMIT_EXCEEDED"):
        adapt_clips_data_to_batch_analysis_cache(manifest, clips_data, config=cfg)

    # max_windows_per_clip = 1 mà có 2 intervals
    cfg2 = AdapterConfig(max_windows_per_clip=1)
    clips_data_multi_win = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [
                {"start": 1.0, "end": 2.0, "text": "One"},
                {"start": 3.0, "end": 4.0, "text": "Two"},
            ],
        }
    ]
    with pytest.raises(ValueError, match="ERR_ADAPTER_LIMIT_EXCEEDED"):
        adapt_clips_data_to_batch_analysis_cache(manifest, clips_data_multi_win, config=cfg2)


def test_empty_clips_data_all_manifest_unscanned():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[
            MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=5.0),
            MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=8.0),
        ],
    )
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, [])
    assert len(snapshot.analysis_cache) == 2
    assert snapshot.analysis_cache["C01"]["speech_status"] == "unscanned"
    assert snapshot.analysis_cache["C02"]["speech_status"] == "unscanned"
    assert len(snapshot.issues) == 2


def test_overflow_integer_timestamp_raises_error():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    huge_int = 10**400
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "dialogue_intervals": [{"start": huge_int, "end": 2.0, "text": "T"}],
                }
            ],
        )


def test_quarantined_text_never_leaks_into_snapshot_or_audit():
    secret_text = "SECRET_QUARANTINED_SENSITIVE_TEXT_12345"
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
            "visual_status": "analyzed",
            "visual_analysis_status": "stale",
            "visual_segments": [],
            "quarantined_visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 3.0,
                    "description": secret_text,
                    "origin": "local_model:old",
                    "analysis_status": "stale",
                }
            ],
            "visual_stale_excluded_count": 1,
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    # Kiểm tra secret_text không xuất hiện trong windows
    assert secret_text not in [w.visual_action for w in snapshot.analysis_cache["C01"]["windows"]]
    # Kiểm tra secret_text không xuất hiện trong issues
    assert not any(secret_text in iss.message for iss in snapshot.issues)
    # Kiểm tra secret_text không xuất hiện trong audit_by_clip
    assert secret_text not in str(snapshot.audit_by_clip["C01"])


def test_adapter_config_boundary_validation():
    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        AdapterConfig(max_input_records=0)

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        AdapterConfig(max_windows_per_clip=100001)

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        AdapterConfig(max_input_records=True)  # type: ignore


# ==============================================================================
# NHÓM R1: CODEX COUNTEREXAMPLES (GROUPS A, B, C)
# ==============================================================================

# A.1: Absent source_availability -> không default available
def test_r1_group_a_missing_source_availability_not_promoted():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            # source_availability vắng mặt
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Speech"}],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert snapshot.analysis_cache["C01"]["speech_status"] == "unavailable"
    assert snapshot.analysis_cache["C01"]["has_audio"] is None
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 0
    assert any(iss.clip_id == "C01" and "UNAVAILABLE" in iss.issue_type for iss in snapshot.issues)


# A.2: Machine segment missing origin hoặc status -> không default described/unknown
def test_r1_group_a_machine_segment_missing_status_or_origin_excluded():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                # Thiếu analysis_status
                {"start_sec": 1.0, "end_sec": 2.0, "description": "Seg 1", "origin": "local_model:gemini"},
                # Thiếu origin
                {"start_sec": 3.0, "end_sec": 4.0, "description": "Seg 2", "analysis_status": "described"},
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 0
    assert any(iss.clip_id == "C01" and "MISSING_STATUS" in iss.issue_type for iss in snapshot.issues)
    assert any(iss.clip_id == "C01" and "MISSING_ORIGIN" in iss.issue_type for iss in snapshot.issues)


# A.2: Parent status pending/unknown/unscanned -> chặn machine
def test_r1_group_a_pending_parent_status_blocks_machine():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "pending",  # Chưa hoàn tất
            "visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 2.0,
                    "description": "Machine seg",
                    "origin": "local_model:x",
                    "analysis_status": "described",
                }
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 0


# A.3: Machine missing range với observation_kind=point -> bị loại với ERR_MACHINE_MISSING_RANGE
def test_r1_group_a_machine_missing_range_with_point_kind_excluded():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                {
                    "time_sec": 2.0,
                    "end_sec": None,
                    "description": "Machine point attempt",
                    "origin": "local_model:gemini",
                    "analysis_status": "described",
                    "observation_kind": "point",
                }
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 0
    assert any(iss.clip_id == "C01" and "ERR_MACHINE_MISSING_RANGE" in iss.issue_type for iss in snapshot.issues)


# A.3: Conflicting observation_kind vs geometry -> ValueError
def test_r1_group_a_conflicting_observation_kind_vs_geometry_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 2.0,
                    "description": "Conflict point with end_sec",
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",  # geometry là interval nhưng khai point
                }
            ],
        }
    ]
    with pytest.raises(ValueError, match="ERR_CONFLICTING_OBSERVATION_KIND"):
        adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)


# B.1: Missing / null manifest_id in row -> ValueError
def test_r1_group_b_missing_or_null_manifest_id_in_row_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    # Thiếu manifest_id
    with pytest.raises(ValueError, match="ERR_MANIFEST_ID_MISMATCH"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest, [{"clip_id": "C01", "duration_sec": 10.0, "duration_known": True}]
        )
    # manifest_id là None
    with pytest.raises(ValueError, match="ERR_MANIFEST_ID_MISMATCH"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest, [{"clip_id": "C01", "manifest_id": None, "duration_sec": 10.0, "duration_known": True}]
        )


# B.2: null source_availability -> ValueError (không AttributeError)
def test_r1_group_b_null_source_availability_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [{"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True, "source_availability": None}],
        )


# B.3: Dialogue interval thiếu start hoặc end -> ValueError (không TypeError)
def test_r1_group_b_dialogue_missing_start_or_end_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "dialogue_intervals": [{"end": 2.0, "text": "Missing start"}],
                }
            ],
        )


# B.4: Path mismatch với manifest.current_path -> ValueError
def test_r1_group_b_path_mismatch_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0, current_path="/real/c1.mp4")],
    )
    with pytest.raises(ValueError, match="ERR_PATH_MISMATCH"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "path": "/wrong/c1.mp4",
                }
            ],
        )


# B.4: Malformed known fields -> ValueError
def test_r1_group_b_malformed_known_fields_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    # duration_known = "yes"
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [{"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": "yes"}],
        )
    # visual_samples = [] (phải là int)
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [{"clip_id": "C01", "manifest_id": "M01", "duration_sec": 10.0, "duration_known": True, "visual_samples": []}],
        )


# B.5: description = None trong visual segment -> ValueError
def test_r1_group_b_null_description_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "visual_status": "analyzed",
                    "visual_analysis_status": "complete",
                    "visual_segments": [
                        {"start_sec": 1.0, "end_sec": 2.0, "origin": "user", "analysis_status": "needs_review", "description": None}
                    ],
                }
            ],
        )


# B.5: Verbatim whitespace in text & visual preserved
def test_r1_group_b_text_verbatim_whitespace_preserved():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    raw_sp_text = "  Leading and trailing whitespace  "
    raw_vis_text = "   User note with spaces   "
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": raw_sp_text}],
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                {
                    "time_sec": 3.0,
                    "end_sec": None,
                    "description": raw_vis_text,
                    "origin": "user",
                    "analysis_status": "needs_review",
                    "observation_kind": "point",
                }
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert snapshot.analysis_cache["C01"]["windows"][0].speech_summary == raw_sp_text
    assert snapshot.analysis_cache["C01"]["windows"][1].visual_action == raw_vis_text


# B.1: Negative manifest duration -> ValueError
def test_r1_group_b_negative_manifest_duration_raises():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=-1.0)],
    )
    with pytest.raises(ValueError, match="ERR_INVALID_MANIFEST"):
        adapt_clips_data_to_batch_analysis_cache(manifest, [])


# B.2: AdapterIssue big-int overflow protection
def test_r1_group_b_adapter_issue_bigint_overflow_safe():
    huge_int = 10**400
    with pytest.raises(ValueError, match="ERR_INVALID_ISSUE"):
        AdapterIssue(clip_id="C01", issue_type="TEST", message="Test", original_start_sec=huge_int)


# C.2: Signature sensitive to coverage and stale count
def test_r1_group_c_signature_sensitive_to_coverage_and_stale_count():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    base_clip = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "completed_empty",
        "speech_status": "no_dialogue_detected",
        "dialogue_intervals": [],
        "visual_status": "analyzed",
        "visual_analysis_status": "complete",
        "visual_segments": [],
        "visual_coverage": {"sampled_ratio": 0.5},
        "visual_analyzed_coverage": {"analyzed_ratio": 0.4},
        "visual_stale_excluded_count": 0,
    }
    snap1 = adapt_clips_data_to_batch_analysis_cache(manifest, [base_clip])

    # Đổi visual_coverage
    alt_clip_cov = dict(base_clip, visual_coverage={"sampled_ratio": 0.8})
    snap2 = adapt_clips_data_to_batch_analysis_cache(manifest, [alt_clip_cov])
    assert snap2.context_signature != snap1.context_signature

    # Đổi visual_analyzed_coverage
    alt_clip_analyzed = dict(base_clip, visual_analyzed_coverage={"analyzed_ratio": 0.9})
    snap3 = adapt_clips_data_to_batch_analysis_cache(manifest, [alt_clip_analyzed])
    assert snap3.context_signature != snap1.context_signature

    # Đổi visual_stale_excluded_count
    alt_clip_stale = dict(base_clip, visual_stale_excluded_count=2)
    snap4 = adapt_clips_data_to_batch_analysis_cache(manifest, [alt_clip_stale])
    assert snap4.context_signature != snap1.context_signature


# C.2: Signature sensitive to objects and producer window_id ref
def test_r1_group_c_signature_sensitive_to_objects_and_producer_ref():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    seg1 = {
        "start_sec": 1.0,
        "end_sec": 2.0,
        "description": "Scene A",
        "origin": "local_model:gemini",
        "analysis_status": "described",
        "window_id": "w0",
        "objects": ["car"],
    }
    base_clip = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "completed_empty",
        "speech_status": "no_dialogue_detected",
        "dialogue_intervals": [],
        "visual_status": "analyzed",
        "visual_analysis_status": "complete",
        "visual_segments": [seg1],
    }
    snap1 = adapt_clips_data_to_batch_analysis_cache(manifest, [base_clip])

    # Đổi objects
    alt_seg_obj = dict(seg1, objects=["person"])
    alt_clip_obj = dict(base_clip, visual_segments=[alt_seg_obj])
    snap2 = adapt_clips_data_to_batch_analysis_cache(manifest, [alt_clip_obj])
    assert snap2.context_signature != snap1.context_signature

    # Đổi producer window_id
    alt_seg_wid = dict(seg1, window_id="w99")
    alt_clip_wid = dict(base_clip, visual_segments=[alt_seg_wid])
    snap3 = adapt_clips_data_to_batch_analysis_cache(manifest, [alt_clip_wid])
    assert snap3.context_signature != snap1.context_signature


# C.3: Missing-row and unavailable supplied signature alters context signature
def test_r1_group_c_missing_row_and_unavailable_supplied_signature_alters_hash():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    # C01 vắng mặt hoàn toàn
    snap_m1 = adapt_clips_data_to_batch_analysis_cache(
        manifest, [], context_metadata={"C01": {"analysis_signature": "SIG_A"}}
    )
    snap_m2 = adapt_clips_data_to_batch_analysis_cache(
        manifest, [], context_metadata={"C01": {"analysis_signature": "SIG_B"}}
    )
    assert snap_m1.context_signature != snap_m2.context_signature

    # C01 nguồn unavailable
    unavail_clip = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "source_missing",
    }
    snap_u1 = adapt_clips_data_to_batch_analysis_cache(
        manifest, [unavail_clip], context_metadata={"C01": {"analysis_signature": "SIG_A"}}
    )
    snap_u2 = adapt_clips_data_to_batch_analysis_cache(
        manifest, [unavail_clip], context_metadata={"C01": {"analysis_signature": "SIG_B"}}
    )
    assert snap_u1.context_signature != snap_u2.context_signature


# C.1: Opaque supplied signature với changed text vẫn đổi generated analysis_signature
def test_r1_group_c_opaque_supplied_signature_with_changed_text_changes_generated_signature():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clip_text_a = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "transcribed",
        "speech_status": "transcribed",
        "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Text A"}],
    }
    clip_text_b = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "transcribed",
        "speech_status": "transcribed",
        "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Text B"}],
    }
    context = {"C01": {"analysis_signature": "opaque_same_signature"}}
    snap_a = adapt_clips_data_to_batch_analysis_cache(manifest, [clip_text_a], context_metadata=context)
    snap_b = adapt_clips_data_to_batch_analysis_cache(manifest, [clip_text_b], context_metadata=context)

    # Generated analysis_signature trong analysis_cache BẮT BUỘC phải khác nhau dù supplied signature giống nhau!
    assert snap_a.analysis_cache["C01"]["analysis_signature"] != snap_b.analysis_cache["C01"]["analysis_signature"]


# C.4: Audit record refs đầy đủ và immutable
def test_r1_group_c_audit_record_refs_and_immutability():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [{"start": 1.0, "end": 2.0, "text": "Hello"}],
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                {
                    "start_sec": 3.0,
                    "end_sec": 4.0,
                    "description": "Visual scene",
                    "origin": "local_model:gemini",
                    "analysis_status": "described",
                    "window_id": "w_prod_01",
                    "objects": ["tree", "road"],
                    "observed_actions": ["walking"],
                    "evidence_times": [3.5],
                }
            ],
            "visual_coverage": {"sampled_ratio": 0.5},
            "visual_analyzed_coverage": {"analyzed_ratio": 0.4},
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    audit = snapshot.audit_by_clip["C01"]
    assert "windows_audit" in audit
    win_audits = audit["windows_audit"]
    assert len(win_audits) == 2

    # Speech audit ref
    sp_ref = win_audits[0]
    assert sp_ref["window_id"] == "C01:speech:0"
    assert sp_ref["modality"] == "speech"

    # Visual audit ref
    vis_ref = win_audits[1]
    assert vis_ref["window_id"] == "C01:visual:0"
    assert vis_ref["producer_window_id"] == "w_prod_01"
    assert vis_ref["objects"] == ("tree", "road")
    assert vis_ref["observed_actions"] == ("walking",)
    assert vis_ref["evidence_times"] == (3.5,)

    # Immutability
    with pytest.raises(Exception):
        audit["windows_audit"] = ()  # Read-only mapping


# ==============================================================================
# NHÓM R2: PRODUCER SCHEMA THẬT & RESIDUAL VALIDATION / SIGNATURES
# ==============================================================================

# R2.A.1: observation_kind='segment' được chấp nhận bình thường
def test_r2_actual_producer_observation_kind_segment_accepted():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [
                {
                    "start_sec": 1.0,
                    "end_sec": 3.0,
                    "description": "Producer segment range",
                    "origin": "local_model:gemini",
                    "analysis_status": "described",
                    "observation_kind": "segment",  # Schema thật từ VisualIndex.validate_segment
                }
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 1
    assert snapshot.analysis_cache["C01"]["windows"][0].visual_action == "Producer segment range"


# R2.A.2: Integration trực tiếp với validate_segment từ visual_index
def test_r2_actual_producer_validate_segment_integration():
    from src.core.visual_index import validate_segment
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )

    # Sử dụng trực tiếp pure function validate_segment để tạo records
    seg_range = validate_segment(
        {"start_sec": 3.0, "end_sec": 4.0, "description": "Actual producer range", "origin": "local_model:gemini"},
        duration_sec=10.0,
    )
    seg_point = validate_segment(
        {"time_sec": 1.0, "description": "Actual producer user point", "origin": "user"},
        duration_sec=10.0,
    )

    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_status": "analyzed",
            "visual_analysis_status": "complete",
            "visual_segments": [seg_range, seg_point],
        }
    ]

    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 2

    # Tích hợp trực tiếp sang A1 planner và renderer
    plan = partition_manifest_into_batches(manifest, snapshot.analysis_cache)
    assert plan.planned_clip_count == 1
    rendered = render_batch_prompt(plan.batches[0])
    assert "Actual producer range" in rendered
    assert "Actual producer user point" in rendered


# R2.A.3: Audio authority/status lạ bị từ chối, không fallback transcribed
def test_r2_unknown_audio_authority_or_status_rejected():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    # audio_authority lạ
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "audio_authority": "bogus_authority",
                    "speech_status": "transcribed",
                }
            ],
        )

    # audio_analysis_status lạ
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "audio_authority": "verified",
                    "audio_analysis_status": "bogus_status",
                    "speech_status": "transcribed",
                }
            ],
        )


# R2.B.1: Nullable alias start_sec=None nhưng start=1.0 hợp lệ
def test_r2_nullable_timing_alias_start_sec_none_with_start_valid():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "transcribed",
            "speech_status": "transcribed",
            "dialogue_intervals": [
                {"start_sec": None, "start": 1.0, "end_sec": 3.0, "end": None, "text": "Nullable alias test"}
            ],
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert len(snapshot.analysis_cache["C01"]["windows"]) == 1
    w = snapshot.analysis_cache["C01"]["windows"][0]
    assert w.start_sec == 1.0
    assert w.end_sec == 3.0


# R2.B.2: Invalid nested coverage bị từ chối
@pytest.mark.parametrize(
    "bad_coverage",
    [
        {"complete": "yes"},
        {"total_windows": True},
        {"sampled_windows": float("nan")},
        {"custom_obj": object()},
    ],
)
def test_r2_invalid_nested_coverage_rejected(bad_coverage):
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "available",
                    "visual_coverage": bad_coverage,
                }
            ],
        )


# R2.B.2: Nullable visual_stale_excluded_count=None được chấp nhận
def test_r2_nullable_stale_count_accepted():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_stale_excluded_count": None,
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert snapshot.audit_by_clip["C01"]["visual_stale_excluded_count"] == 0


# R2.B.2: Malformed fields dưới source_missing vẫn bị từ chối trước gating
def test_r2_malformed_fields_under_source_missing_rejected():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    # quarantined_visual_segments = [123]
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "source_missing",
                    "quarantined_visual_segments": [123],
                }
            ],
        )

    # visual_segments = [{"origin": {}}]
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "source_missing",
                    "visual_segments": [{"origin": {}}],
                }
            ],
        )

    # visual_segments = [{"analysis_status": []}]
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "source_missing",
                    "visual_segments": [{"analysis_status": []}],
                }
            ],
        )

    # visual_segments = [{"window_id": 123}]
    with pytest.raises(ValueError, match="ERR_INVALID_DATA_TYPE"):
        adapt_clips_data_to_batch_analysis_cache(
            manifest,
            [
                {
                    "clip_id": "C01",
                    "manifest_id": "M01",
                    "duration_sec": 10.0,
                    "duration_known": True,
                    "source_availability": "source_missing",
                    "visual_segments": [{"window_id": 123}],
                }
            ],
        )


# R2.B.3: MappingProxyType nested coverage được chấp nhận và chuẩn hóa
def test_r2_mapping_proxy_type_coverage_accepted():
    from types import MappingProxyType
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    raw_cov = MappingProxyType({"complete": True, "total_windows": 5, "sampled_windows": 3})
    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "visual_coverage": raw_cov,
        }
    ]
    snapshot = adapt_clips_data_to_batch_analysis_cache(manifest, clips_data)
    assert snapshot.audit_by_clip["C01"]["visual_coverage"]["complete"] is True


# R2.C.1: Parent status delta đổi context_signature
def test_r2_parent_status_signature_delta():
    manifest = MockClipManifest(
        project_name="TestProject",
        manifest_id="M01",
        clips=[MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)],
    )
    clip_complete = {
        "clip_id": "C01",
        "manifest_id": "M01",
        "duration_sec": 10.0,
        "duration_known": True,
        "source_availability": "available",
        "audio_authority": "verified",
        "audio_analysis_status": "completed_empty",
        "speech_status": "no_dialogue_detected",
        "dialogue_intervals": [],
        "visual_status": "analyzed",
        "visual_analysis_status": "complete",
        "visual_segments": [],
    }
    clip_partial = dict(clip_complete, visual_analysis_status="partial")

    snap1 = adapt_clips_data_to_batch_analysis_cache(manifest, [clip_complete])
    snap2 = adapt_clips_data_to_batch_analysis_cache(manifest, [clip_partial])
    assert snap1.context_signature != snap2.context_signature


# R2.C.1: Manifest identity delta (content_hash, file_name, current_path) đổi context_signature
def test_r2_manifest_identity_signature_delta():
    clip_base = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0, current_path="/path1.mp4")
    m1 = MockClipManifest(project_name="TestProject", manifest_id="M01", clips=[clip_base])

    clips_data = [
        {
            "clip_id": "C01",
            "manifest_id": "M01",
            "duration_sec": 10.0,
            "duration_known": True,
            "source_availability": "available",
            "audio_authority": "verified",
            "audio_analysis_status": "completed_empty",
            "speech_status": "no_dialogue_detected",
            "dialogue_intervals": [],
        }
    ]
    snap_base = adapt_clips_data_to_batch_analysis_cache(m1, clips_data)

    # Đổi content_hash
    clip_alt_hash = MockClipEntry(clip_id="C01", content_hash="h_DIFF", file_name="c1.mp4", duration_sec=10.0, current_path="/path1.mp4")
    m_alt_hash = MockClipManifest(project_name="TestProject", manifest_id="M01", clips=[clip_alt_hash])
    snap_alt_hash = adapt_clips_data_to_batch_analysis_cache(m_alt_hash, clips_data)
    assert snap_alt_hash.context_signature != snap_base.context_signature

    # Đổi file_name
    clip_alt_name = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c_DIFF.mp4", duration_sec=10.0, current_path="/path1.mp4")
    m_alt_name = MockClipManifest(project_name="TestProject", manifest_id="M01", clips=[clip_alt_name])
    snap_alt_name = adapt_clips_data_to_batch_analysis_cache(m_alt_name, clips_data)
    assert snap_alt_name.context_signature != snap_base.context_signature

    # Đổi current_path
    clip_alt_path = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0, current_path="/path_DIFF.mp4")
    m_alt_path = MockClipManifest(project_name="TestProject", manifest_id="M01", clips=[clip_alt_path])
    snap_alt_path = adapt_clips_data_to_batch_analysis_cache(m_alt_path, clips_data)
    assert snap_alt_path.context_signature != snap_base.context_signature



