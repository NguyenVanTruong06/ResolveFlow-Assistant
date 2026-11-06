"""
tests/core/test_batch_planner.py
Unit tests for src.core.batch_planner (G06-06A1-I).
Pure in-memory finite test suite with strict assertions, zero I/O, zero Qt, zero models.
"""

from dataclasses import dataclass
import json
import math
from typing import Any, Dict, List, Optional
import pytest

from src.core.batch_planner import (
    BatchBudgetConfig,
    EvidenceWindowSummary,
    ClipBatchItem,
    BoundedBatch,
    ProjectBatchPlan,
    UnaccountedClipIssue,
    partition_manifest_into_batches,
    render_batch_prompt,
    estimate_batch_prompt_bytes,
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
# NHÓM 1: GLOBAL IDS / ORDER / 30 CLIPS 60S -> 3 BATCHES VỚI TARGET=HARD=600
# ==============================================================================

def test_30_clips_60s_partitioning_preserves_global_ids():
    """30 clips mỗi clip 60s, target=hard=600s -> 3 batches (10 clips/batch), 0 renumber."""
    clips = [
        MockClipEntry(clip_id=f"C{i+1:02d}", content_hash=f"hash_{i+1:02d}", file_name=f"clip_{i+1:02d}.mp4", duration_sec=60.0)
        for i in range(30)
    ]
    manifest = MockClipManifest(project_name="Test30", manifest_id="m_test30", clips=clips)
    config = BatchBudgetConfig(target_batch_duration_sec=600.0, max_batch_duration_sec=600.0, max_clips_per_batch=20)

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)

    assert plan.manifest_id == "m_test30"
    assert plan.project_name == "Test30"
    assert plan.total_clips == 30
    assert plan.planned_clip_count == 30
    assert len(plan.unaccounted_clips) == 0
    assert len(plan.batches) == 3

    # Batch 1: C01..C10
    assert plan.batches[0].batch_id == "B01"
    assert plan.batches[0].batch_index == 0
    assert len(plan.batches[0].items) == 10
    assert plan.batches[0].items[0].clip_id == "C01"
    assert plan.batches[0].items[-1].clip_id == "C10"
    assert plan.batches[0].total_duration_sec == pytest.approx(600.0)

    # Batch 2: C11..C20 (Giữ nguyên ID toàn cục, không renumber về C01)
    assert plan.batches[1].batch_id == "B02"
    assert plan.batches[1].batch_index == 1
    assert len(plan.batches[1].items) == 10
    assert plan.batches[1].items[0].clip_id == "C11"
    assert plan.batches[1].items[-1].clip_id == "C20"
    assert plan.batches[1].total_duration_sec == pytest.approx(600.0)

    # Batch 3: C21..C30
    assert plan.batches[2].batch_id == "B03"
    assert plan.batches[2].batch_index == 2
    assert len(plan.batches[2].items) == 10
    assert plan.batches[2].items[0].clip_id == "C21"
    assert plan.batches[2].items[-1].clip_id == "C30"
    assert plan.batches[2].total_duration_sec == pytest.approx(600.0)

    assert plan.total_duration_sec == pytest.approx(1800.0)


# ==============================================================================
# NHÓM 2: LONG CLIP 1200S VỚI WINDOW=600S -> 2 RANGES KHÔNG GAP/OVERLAP
# ==============================================================================

def test_long_clip_1200s_partitions_exactly_into_2_ranges():
    """1 clip 1200s với default window_partition_sec=600s chia đúng 2 ranges [0, 600] và [600, 1200]."""
    clip = MockClipEntry(clip_id="C01", content_hash="hash_c01", file_name="long_clip.mp4", duration_sec=1200.0)
    manifest = MockClipManifest(project_name="LongProj", manifest_id="m_long", clips=[clip])
    config = BatchBudgetConfig(
        target_batch_duration_sec=600.0,
        max_batch_duration_sec=900.0,
        window_partition_sec=600.0
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)

    assert len(plan.batches) == 2
    b1_item = plan.batches[0].items[0]
    b2_item = plan.batches[1].items[0]

    # Kiểm tra bounds và partitions
    assert b1_item.clip_id == "C01"
    assert b1_item.source_in_sec == pytest.approx(0.0)
    assert b1_item.source_out_sec == pytest.approx(600.0)
    assert b1_item.item_duration_sec == pytest.approx(600.0)
    assert b1_item.partition_index == 0
    assert b1_item.total_partitions == 2

    assert b2_item.clip_id == "C01"
    assert b2_item.source_in_sec == pytest.approx(600.0)
    assert b2_item.source_out_sec == pytest.approx(1200.0)
    assert b2_item.item_duration_sec == pytest.approx(600.0)
    assert b2_item.partition_index == 1
    assert b2_item.total_partitions == 2


def test_fractional_duration_has_no_drift():
    """Độ dài phân số (fractional durations) không bị trôi số dấu phẩy động."""
    # Clip 100.125s với window 40.0s -> 3 partitions: [0, 40], [40, 80], [80, 100.125]
    clip = MockClipEntry(clip_id="C01", content_hash="hash_frac", file_name="frac.mp4", duration_sec=100.125)
    manifest = MockClipManifest(project_name="Frac", manifest_id="m_frac", clips=[clip])
    config = BatchBudgetConfig(
        target_batch_duration_sec=200.0,
        max_batch_duration_sec=200.0,
        window_partition_sec=40.0
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)

    assert len(plan.batches) == 1
    items = plan.batches[0].items
    assert len(items) == 3

    assert items[0].source_in_sec == pytest.approx(0.0)
    assert items[0].source_out_sec == pytest.approx(40.0)
    assert items[1].source_in_sec == pytest.approx(40.0)
    assert items[1].source_out_sec == pytest.approx(80.0)
    assert items[2].source_in_sec == pytest.approx(80.0)
    assert items[2].source_out_sec == pytest.approx(100.125)
    assert sum(it.item_duration_sec for it in items) == pytest.approx(100.125)


# ==============================================================================
# NHÓM 3: SOFT VS HARD DURATION & DISTINCT CLIP COUNT CONSTRAINTS
# ==============================================================================

def test_single_item_exceeding_soft_target_is_allowed():
    """Item đơn 700s khi target=600s nhưng <= hard=900s được phép trong batch riêng."""
    clip = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=700.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[clip])
    config = BatchBudgetConfig(
        target_batch_duration_sec=600.0,
        max_batch_duration_sec=900.0,
        window_partition_sec=900.0
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)
    assert len(plan.batches) == 1
    assert plan.batches[0].total_duration_sec == pytest.approx(700.0)


def test_soft_target_triggers_new_batch_when_current_not_empty():
    """Nếu thêm item làm vượt soft target và batch hiện tại không rỗng thì tách batch mới."""
    # Clip 1: 400s, Clip 2: 300s -> Tổng 700s > target 600s -> Clip 2 sang Batch 2
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=400.0)
    c2 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=300.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2])
    config = BatchBudgetConfig(target_batch_duration_sec=600.0, max_batch_duration_sec=900.0, window_partition_sec=900.0)

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)
    assert len(plan.batches) == 2
    assert plan.batches[0].items[0].clip_id == "C01"
    assert plan.batches[1].items[0].clip_id == "C02"


def test_distinct_clip_count_counts_same_clip_partitions_as_one():
    """Nhiều partitions của cùng một clip trong 1 batch chỉ tính là 1 distinct clip."""
    # 1 clip dài 300s chia 3 partitions 100s trong 1 batch
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=300.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    config = BatchBudgetConfig(
        target_batch_duration_sec=600.0,
        max_batch_duration_sec=600.0,
        window_partition_sec=100.0,
        max_clips_per_batch=1
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)
    assert len(plan.batches) == 1
    assert len(plan.batches[0].items) == 3
    assert plan.batches[0].distinct_clip_count == 1


def test_max_clips_per_batch_constraint():
    """Vượt quá max_clips_per_batch thì chuyển sang batch mới."""
    clips = [
        MockClipEntry(clip_id=f"C{i+1:02d}", content_hash=f"h{i}", file_name=f"c{i}.mp4", duration_sec=10.0)
        for i in range(5)
    ]
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=clips)
    config = BatchBudgetConfig(target_batch_duration_sec=600.0, max_batch_duration_sec=600.0, max_clips_per_batch=2)

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=config)
    # 5 clips, max 2 clips/batch -> 3 batches (2, 2, 1)
    assert len(plan.batches) == 3
    assert plan.batches[0].distinct_clip_count == 2
    assert plan.batches[1].distinct_clip_count == 2
    assert plan.batches[2].distinct_clip_count == 1


# ==============================================================================
# NHÓM 4: STATUS PRESERVATION (MIXED SPEECH, NO-AUDIO, FAILED, MISSING)
# ==============================================================================

def test_mixed_speech_and_no_audio_status_preservation():
    """Bảo toàn trạng thái speech, no_audio_track, và unscanned mà không fake words."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="speech.mp4", duration_sec=10.0)
    c2 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="noaudio.mp4", duration_sec=10.0)
    c3 = MockClipEntry(clip_id="C03", content_hash="h3", file_name="unscanned.mp4", duration_sec=10.0)
    c4 = MockClipEntry(clip_id="C04", content_hash="h4", file_name="failed.mp4", duration_sec=10.0)

    analysis_cache = {
        "C01": {"has_audio": True, "speech_status": "transcribed", "windows": []},
        "C02": {"has_audio": False, "speech_status": "no_audio_track", "windows": []},
        # C03 không có trong cache -> default unscanned
        "C04": {"has_audio": True, "speech_status": "failed", "windows": []},
    }

    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2, c3, c4])
    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)

    items = plan.batches[0].items
    assert items[0].speech_status == "transcribed"
    assert items[0].has_audio is True

    assert items[1].speech_status == "no_audio_track"
    assert items[1].has_audio is False

    assert items[2].speech_status == "unscanned"
    assert items[2].has_audio is None

    assert items[3].speech_status == "failed"
    assert items[3].has_audio is True


def test_unknown_duration_and_source_missing_accounted_in_issues():
    """Clip duration 0/None hoặc source_missing được đưa vào unaccounted_clips kèm issue."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="normal.mp4", duration_sec=10.0)
    c2 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="zero_dur.mp4", duration_sec=0.0)
    c3 = MockClipEntry(clip_id="C03", content_hash="h3", file_name="missing.mp4", duration_sec=15.0)

    analysis_cache = {
        "C03": {"speech_status": "source_missing"}
    }

    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2, c3])
    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)

    assert plan.total_clips == 3
    assert plan.planned_clip_count == 1
    assert "C02" in plan.unaccounted_clips
    assert "C03" in plan.unaccounted_clips
    assert len(plan.issues) == 2

    issues_by_clip = {iss.clip_id: iss for iss in plan.issues}
    assert issues_by_clip["C02"].reason == "DURATION_UNKNOWN"
    assert issues_by_clip["C03"].reason == "SOURCE_MISSING"


# ==============================================================================
# NHÓM 5: EXACT UTF-8 BYTES BUDGET, ESCAPING, VIETNAMESE, EMOJI
# ==============================================================================

def test_exact_utf8_budget_measurement_and_match():
    """estimated_prompt_bytes phải bằng đúng len(render_batch_prompt(batch).encode('utf-8'))."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="clip_tieng_viet.mp4", duration_sec=10.0)
    analysis_cache = {
        "C01": {
            "has_audio": True,
            "speech_status": "transcribed",
            "windows": [
                {
                    "window_id": "W01",
                    "start_sec": 0.0,
                    "end_sec": 10.0,
                    "speech_summary": "Chào mừng quý khách đến Hà Giang! 🌄 Điểm cực Bắc 🇻🇳",
                    "speech_word_count": 12,
                    "visual_action": "Toàn cảnh đèo Mã Pí Lèng nhìn từ trên cao",
                    "origin": "flite",
                }
            ],
        }
    }
    manifest = MockClipManifest(project_name="Hà Giang Đẹp", manifest_id="m_hg", clips=[c1])
    config = BatchBudgetConfig(
        prompt_prefix="### KỊCH BẢN ĐẦU VÀO:\n",
        prompt_suffix="\n### HẾT KỊCH BẢN."
    )

    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache, config=config)
    batch = plan.batches[0]

    rendered_text = render_batch_prompt(batch)
    actual_bytes = len(rendered_text.encode("utf-8"))

    assert batch.estimated_prompt_bytes == actual_bytes
    assert estimate_batch_prompt_bytes(batch) == actual_bytes
    assert "Chào mừng quý khách đến Hà Giang! 🌄" in rendered_text
    assert "### KỊCH BẢN ĐẦU VÀO:\n" in rendered_text
    assert "\n### HẾT KỊCH BẢN." in rendered_text


def test_oversized_indivisible_item_raises_error():
    """Một item đơn mà dung lượng kết xuất vượt trần max_prompt_bytes phải ném ValueError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="huge.mp4", duration_sec=10.0)
    # Tạo text summary cực dài vượt 1000 bytes
    huge_text = "Lời thoại cực kỳ dài " * 100
    analysis_cache = {
        "C01": {
            "windows": [
                {"window_id": "W1", "start_sec": 0.0, "end_sec": 10.0, "speech_summary": huge_text}
            ]
        }
    }
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    config = BatchBudgetConfig(max_prompt_bytes=500)  # Trần chỉ 500 bytes

    with pytest.raises(ValueError, match="ERR_ITEM_EXCEEDS_PROMPT_BUDGET"):
        partition_manifest_into_batches(manifest, analysis_cache=analysis_cache, config=config)


# ==============================================================================
# NHÓM 6: MALFORMED TYPES, INVALID CONFIG, RESOURCE GUARDS
# ==============================================================================

def test_invalid_config_types_raise_value_error():
    """Config không hợp lệ (bool cho max_prompt_bytes, target > hard, NaN/Inf) ném ValueError."""
    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        BatchBudgetConfig(max_prompt_bytes=True)  # type: ignore

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        BatchBudgetConfig(max_prompt_bytes=0)

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        BatchBudgetConfig(max_prompt_bytes=50000)  # > 32768

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        BatchBudgetConfig(target_batch_duration_sec=1000.0, max_batch_duration_sec=500.0)  # target > hard

    with pytest.raises(ValueError, match="ERR_INVALID_CONFIG"):
        BatchBudgetConfig(target_batch_duration_sec=float("nan"))


def test_duplicate_clip_ids_raise_value_error():
    """Manifest có clip_id trùng lặp ném ValueError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    c2 = MockClipEntry(clip_id="C01", content_hash="h2", file_name="c2.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2])

    with pytest.raises(ValueError, match="ERR_DUPLICATE_CLIP_ID"):
        partition_manifest_into_batches(manifest, analysis_cache={})


def test_unknown_clip_in_analysis_cache_raises_value_error():
    """analysis_cache chứa clip_id không tồn tại trong manifest ném ValueError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    analysis_cache = {"C99": {"speech_status": "transcribed"}}

    with pytest.raises(ValueError, match="ERR_UNKNOWN_ANALYSIS_CLIP_ID"):
        partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)


def test_max_planned_items_preallocation_guard():
    """Vượt quá max_planned_items ném ERR_PLAN_LIMIT_EXCEEDED."""
    # Clip 1000s với window 0.01s sẽ cần 100,000 items
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=1000.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    config = BatchBudgetConfig(window_partition_sec=0.01, max_planned_items=100)

    with pytest.raises(ValueError, match="ERR_PLAN_LIMIT_EXCEEDED"):
        partition_manifest_into_batches(manifest, analysis_cache={}, config=config)


# ==============================================================================
# NHÓM 7: WINDOW CROSSING & POINT BOUNDARIES
# ==============================================================================

def test_window_crossing_partition_boundaries():
    """Window trải dài qua ranh giới partition được gán vào cả hai với cờ clipped/overlap."""
    # Clip 20s, partition 10s -> P0: [0, 10], P1: [10, 20]
    # Window W1: [5.0, 15.0] (nằm vắt ngang qua mốc 10s)
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=20.0)
    analysis_cache = {
        "C01": {
            "windows": [
                {
                    "window_id": "W1",
                    "start_sec": 5.0,
                    "end_sec": 15.0,
                    "speech_summary": "Câu thoại dài bắt đầu từ giây 5 đến giây 15",
                    "speech_word_count": 10,
                }
            ]
        }
    }
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    config = BatchBudgetConfig(window_partition_sec=10.0, target_batch_duration_sec=30.0, max_batch_duration_sec=30.0)

    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache, config=config)
    items = plan.batches[0].items
    assert len(items) == 2

    # Item 1: [0, 10]
    assert len(items[0].windows) == 1
    assert items[0].windows[0].window_id == "W1"
    assert items[0].windows[0].start_sec == 5.0
    assert items[0].windows[0].end_sec == 15.0
    # Original text preserved, not chopped
    assert items[0].windows[0].speech_summary == "Câu thoại dài bắt đầu từ giây 5 đến giây 15"

    # Item 2: [10, 20]
    assert len(items[1].windows) == 1
    assert items[1].windows[0].window_id == "W1"
    assert items[1].windows[0].speech_summary == "Câu thoại dài bắt đầu từ giây 5 đến giây 15"


def test_point_boundary_belongs_to_right_partition():
    """Mốc quan sát điểm (point observation start==end==10.0) thuộc về partition bên phải."""
    # Clip 20s, partitions: [0, 10], [10, 20]
    # Point observation tại 10.0s
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=20.0)
    analysis_cache = {
        "C01": {
            "windows": [
                {"window_id": "P_POINT", "start_sec": 10.0, "end_sec": 10.0, "visual_action": "Bật đèn"}
            ]
        }
    }
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    config = BatchBudgetConfig(window_partition_sec=10.0, target_batch_duration_sec=30.0, max_batch_duration_sec=30.0)

    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache, config=config)
    items = plan.batches[0].items

    # Item 0 [0, 10) không chứa point tại 10.0
    assert len(items[0].windows) == 0
    # Item 1 [10, 20] chứa point tại 10.0
    assert len(items[1].windows) == 1
    assert items[1].windows[0].window_id == "P_POINT"


# ==============================================================================
# NHÓM 8: DETERMINISTIC REPEATED PLAN, SIGNATURE, EMPTY MANIFEST, IMMUTABILITY
# ==============================================================================

def test_empty_manifest_returns_empty_batches():
    """Manifest rỗng trả về ProjectBatchPlan hợp lệ với 0 batches."""
    manifest = MockClipManifest(project_name="Empty", manifest_id="m_empty", clips=[])
    plan = partition_manifest_into_batches(manifest, analysis_cache={})

    assert plan.manifest_id == "m_empty"
    assert plan.total_clips == 0
    assert plan.planned_clip_count == 0
    assert len(plan.batches) == 0
    assert plan.total_duration_sec == 0.0


def test_deterministic_signatures_and_immutability():
    """Hai lần chạy giống hệt nhau sinh ra cùng output, cùng content_signature, immutability bảo toàn."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=25.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    analysis_cache = {"C01": {"has_audio": True, "speech_status": "transcribed"}}

    plan1 = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)
    plan2 = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)

    assert plan1.batches[0].content_signature == plan2.batches[0].content_signature
    assert render_batch_prompt(plan1.batches[0]) == render_batch_prompt(plan2.batches[0])

    # Thử thay đổi thuộc tính frozen -> TypeError
    with pytest.raises(Exception):
        plan1.batches[0].total_duration_sec = 999.0  # type: ignore


def test_exact_budget_equality_and_plus_one_split():
    """Kiểm tra ranh giới chính xác: bằng đúng max_prompt_bytes thì chấp nhận, thêm đúng 1 byte thì tách batch."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    c2 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2])

    # Chạy thử 1 lần để đo chính xác dung lượng của 2 clip trong cùng 1 batch
    test_config = BatchBudgetConfig(max_prompt_bytes=32768, target_batch_duration_sec=600.0, max_batch_duration_sec=600.0)
    base_plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=test_config)
    assert len(base_plan.batches) == 1
    exact_both_bytes = base_plan.batches[0].estimated_prompt_bytes

    # Cấu hình max_prompt_bytes bằng đúng exact_both_bytes -> Vẫn vừa trong 1 batch!
    tight_config = BatchBudgetConfig(max_prompt_bytes=exact_both_bytes, target_batch_duration_sec=600.0, max_batch_duration_sec=600.0)
    tight_plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=tight_config)
    assert len(tight_plan.batches) == 1
    assert tight_plan.batches[0].estimated_prompt_bytes == exact_both_bytes

    # Cấu hình max_prompt_bytes = exact_both_bytes - 1 -> Bị tràn 1 byte -> Phải tách thành 2 batches!
    split_config = BatchBudgetConfig(max_prompt_bytes=exact_both_bytes - 1, target_batch_duration_sec=600.0, max_batch_duration_sec=600.0)
    split_plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=split_config)
    assert len(split_plan.batches) == 2
    assert split_plan.batches[0].items[0].clip_id == "C01"
    assert split_plan.batches[1].items[0].clip_id == "C02"


def test_zero_io_clock_and_random_spies(monkeypatch):
    """Đảm bảo planner hoàn toàn là pure function: zero I/O file, zero clock, zero random."""
    import builtins
    import time
    import random

    def forbidden_open(*args, **kwargs):
        raise AssertionError("Planner không được thực hiện I/O tệp (open)!")

    def forbidden_time(*args, **kwargs):
        raise AssertionError("Planner không được gọi time.time()!")

    def forbidden_random(*args, **kwargs):
        raise AssertionError("Planner không được gọi random()!")

    monkeypatch.setattr(builtins, "open", forbidden_open)
    monkeypatch.setattr(time, "time", forbidden_time)
    monkeypatch.setattr(random, "random", forbidden_random)

    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=30.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    analysis_cache = {
        "C01": {
            "has_audio": True,
            "speech_status": "transcribed",
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 10.0, "speech_summary": "Test pure"}],
        }
    }

    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)
    assert len(plan.batches) == 1
    assert plan.batches[0].items[0].clip_id == "C01"
    rendered = render_batch_prompt(plan.batches[0])
    assert "Test pure" in rendered


# ==============================================================================
# NHÓM 9: G06-06A1-R1 REGRESSION & CONTRACT HARDENING
# ==============================================================================

def test_rollover_budget_byte_guard_rejects_oversize_on_new_batch():
    """
    Tái hiện probe Codex: 11 clips 1s, max_clips_per_batch=1.
    Đo dung lượng của batch index 9 (batch B10), sau đó đặt max_prompt_bytes bằng đúng dung lượng đó.
    Batch B11 (tại index 10) có thêm 1 ký tự ở batch_index/batch_id -> vượt quá max_prompt_bytes.
    Planner phải raise ERR_ITEM_EXCEEDS_PROMPT_BUDGET thay vì sinh ra batch vượt trần!
    """
    clips = [
        MockClipEntry(clip_id=f"C{i+1:02d}", content_hash=f"h{i+1:02d}", file_name=f"c{i+1:02d}.mp4", duration_sec=1.0)
        for i in range(11)
    ]
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=clips)

    # Đo dung lượng baseline với config rộng
    base_cfg = BatchBudgetConfig(max_prompt_bytes=32768, target_batch_duration_sec=600.0, max_batch_duration_sec=600.0, max_clips_per_batch=1)
    base_plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=base_cfg)
    assert len(base_plan.batches) == 11
    b10_bytes = base_plan.batches[9].estimated_prompt_bytes
    b11_bytes = base_plan.batches[10].estimated_prompt_bytes
    assert b11_bytes > b10_bytes

    # Bây giờ đặt max_prompt_bytes = b10_bytes -> Batch 11 tại index 10 sẽ bị vượt ngân sách
    tight_cfg = BatchBudgetConfig(max_prompt_bytes=b10_bytes, target_batch_duration_sec=600.0, max_batch_duration_sec=600.0, max_clips_per_batch=1)
    with pytest.raises(ValueError) as exc:
        partition_manifest_into_batches(manifest, analysis_cache={}, config=tight_cfg)
    assert "ERR_ITEM_EXCEEDS_PROMPT_BUDGET" in str(exc.value)


def test_hard_duration_exact_fraction_no_tolerance():
    """
    Tái hiện probe Codex: Hai clip 0.5000004s, target=hard=window=1.0s.
    Tổng = 1.0000008s > 1.0s.
    Không được dùng tolerance +1e-6 để gộp chung vào 1 batch. Phải tách thành 2 batches!
    """
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=0.5000004)
    c2 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=0.5000004)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1, c2])
    cfg = BatchBudgetConfig(target_batch_duration_sec=1.0, max_batch_duration_sec=1.0, window_partition_sec=1.0)

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg)
    assert len(plan.batches) == 2
    assert plan.batches[0].items[0].clip_id == "C01"
    assert plan.batches[1].items[0].clip_id == "C02"
    assert plan.batches[0].total_duration_sec == pytest.approx(0.5000004)
    assert plan.batches[1].total_duration_sec == pytest.approx(0.5000004)


def test_lossless_source_bounds_in_json():
    """
    Tái hiện probe Codex: Source bound 1.1374999 không bị round(..., 6) thành 1.1375 trong JSON kết xuất.
    """
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=1.1374999)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    cfg = BatchBudgetConfig(window_partition_sec=10.0)

    plan = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg)
    rendered_json = render_batch_prompt(plan.batches[0])
    parsed = json.loads(rendered_json)

    item = parsed["items"][0]
    assert item["source_out_sec"] == 1.1374999
    assert item["clip_duration_sec"] == 1.1374999
    assert item["source_out_sec"] != 1.1375


def test_sampled_content_hash_and_project_name_preserved():
    """
    Bảo toàn sampled content_hash từ ClipEntry và project_name từ manifest vào envelope và items.
    """
    c1 = MockClipEntry(clip_id="C01", content_hash="sampled_hash_999", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="ChunDVC_Project", manifest_id="m_chun", clips=[c1])
    analysis_cache = {
        "C01": {
            "full_content_sha256": "a" * 64,
            "speech_status": "transcribed",
        }
    }
    plan = partition_manifest_into_batches(manifest, analysis_cache=analysis_cache)
    batch = plan.batches[0]
    assert batch.project_name == "ChunDVC_Project"
    assert batch.items[0].content_hash == "sampled_hash_999"
    assert batch.items[0].full_content_sha256 == "a" * 64

    rendered = render_batch_prompt(batch)
    parsed = json.loads(rendered)
    assert parsed["project_name"] == "ChunDVC_Project"
    assert parsed["items"][0]["content_hash"] == "sampled_hash_999"
    assert parsed["items"][0]["full_content_sha256"] == "a" * 64


def test_evidence_text_change_alters_signature():
    """
    Tái hiện probe Codex: Thay đổi speech_summary giữa hai văn bản A và B
    làm rendered prompt khác nhau -> content_signature BẮT BUỘC phải khác nhau.
    """
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    cache_a = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 5.0, "speech_summary": "Text A"}],
        }
    }
    cache_b = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 5.0, "speech_summary": "Text B"}],
        }
    }

    plan_a = partition_manifest_into_batches(manifest, analysis_cache=cache_a)
    plan_b = partition_manifest_into_batches(manifest, analysis_cache=cache_b)

    assert plan_a.batches[0].content_signature != plan_b.batches[0].content_signature


def test_visual_action_and_provenance_alters_signature():
    """Thay đổi visual_action, origin, is_sampled hoặc analysis_signature làm thay đổi signature."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    cache_1 = {
        "C01": {
            "analysis_signature": "sig_v1",
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 5.0, "visual_action": "Action 1", "origin": "flite", "is_sampled": True}],
        }
    }
    cache_2 = {
        "C01": {
            "analysis_signature": "sig_v2",
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 5.0, "visual_action": "Action 2", "origin": "whisper", "is_sampled": False}],
        }
    }

    p1 = partition_manifest_into_batches(manifest, analysis_cache=cache_1)
    p2 = partition_manifest_into_batches(manifest, analysis_cache=cache_2)
    assert p1.batches[0].content_signature != p2.batches[0].content_signature


def test_config_change_alters_signature():
    """Thay đổi cấu hình ngân sách làm thay đổi signature dù layout không đổi."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    cfg_1 = BatchBudgetConfig(max_prompt_bytes=32768)
    cfg_2 = BatchBudgetConfig(max_prompt_bytes=16384)

    p1 = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg_1)
    p2 = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg_2)
    assert p1.batches[0].content_signature != p2.batches[0].content_signature


def test_prefix_suffix_framing_no_boundary_collision():
    """
    Tránh va chạm ranh giới prefix='a', suffix='bc' vs prefix='ab', suffix='c'.
    """
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    cfg_1 = BatchBudgetConfig(prompt_prefix="a", prompt_suffix="bc")
    cfg_2 = BatchBudgetConfig(prompt_prefix="ab", prompt_suffix="c")

    p1 = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg_1)
    p2 = partition_manifest_into_batches(manifest, analysis_cache={}, config=cfg_2)
    assert p1.batches[0].content_signature != p2.batches[0].content_signature


def test_manifest_id_none_or_bool_or_blank_rejected():
    """manifest_id là None, bool hoặc blank phải raise ValueError (không được ép kiểu thành 'None')."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)

    for bad_id in [None, True, False, "", "   "]:
        m = MockClipManifest(project_name="P", manifest_id=bad_id, clips=[c1])
        with pytest.raises(ValueError) as exc:
            partition_manifest_into_batches(m, analysis_cache={})
        assert "ERR_INVALID_MANIFEST" in str(exc.value)


def test_project_name_bool_or_invalid_rejected():
    """project_name là bool hoặc non-str phải raise ValueError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    m = MockClipManifest(project_name=True, manifest_id="m", clips=[c1])
    with pytest.raises(ValueError) as exc:
        partition_manifest_into_batches(m, analysis_cache={})
    assert "ERR_INVALID_MANIFEST" in str(exc.value)


def test_non_mapping_analysis_entry_rejected():
    """Entry trong analysis_cache không phải Mapping (ví dụ list) phải raise ValueError có lý do, không AttributeError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    with pytest.raises(ValueError) as exc:
        partition_manifest_into_batches(manifest, analysis_cache={"C01": []})
    assert "ERR_INVALID_ANALYSIS" in str(exc.value)


def test_bigint_config_overflow_rejected():
    """Config có số quá lớn 10**400 gây OverflowError phải raise ValueError có kiểm soát."""
    with pytest.raises(ValueError) as exc:
        BatchBudgetConfig(target_batch_duration_sec=10**400)
    assert "ERR_INVALID_CONFIG" in str(exc.value)


def test_bool_window_time_rejected():
    """start_sec=False hoặc end_sec=True phải bị từ chối bằng ValueError, không được coi là số thực 0.0/1.0."""
    with pytest.raises(ValueError) as exc:
        EvidenceWindowSummary(window_id="W1", start_sec=False, end_sec=1.0)
    assert "ERR_INVALID_WINDOW" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        EvidenceWindowSummary(window_id="W1", start_sec=0.0, end_sec=True)
    assert "ERR_INVALID_WINDOW" in str(exc.value)

    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    cache = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": False, "end_sec": 1.0}],
        }
    }
    with pytest.raises(ValueError) as exc:
        partition_manifest_into_batches(manifest, analysis_cache=cache)
    assert "ERR_INVALID_WINDOW" in str(exc.value)


def test_mutable_output_alias_rejected():
    """
    EvidenceWindowSummary từ chối list/object cho speech_summary.
    Đầu ra plan là immutable tuple, caller không thể làm đột biến rendered output bằng cách mutate input list.
    """
    with pytest.raises(ValueError) as exc:
        EvidenceWindowSummary(window_id="W1", start_sec=0.0, end_sec=1.0, speech_summary=["A"])
    assert "ERR_INVALID_WINDOW" in str(exc.value)

    # Caller truyền list windows ngoài
    w_list = [EvidenceWindowSummary(window_id="W1", start_sec=0.0, end_sec=5.0, speech_summary="Original")]
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])
    cache = {"C01": {"windows": w_list}}

    plan = partition_manifest_into_batches(manifest, analysis_cache=cache)
    initial_bytes = plan.batches[0].estimated_prompt_bytes
    initial_render = render_batch_prompt(plan.batches[0])

    # Caller mutate list ban đầu
    w_list.append(EvidenceWindowSummary(window_id="W2", start_sec=5.0, end_sec=10.0, speech_summary="Mutated"))

    # Plan không bị ảnh hưởng
    assert len(plan.batches[0].items[0].windows) == 1
    assert plan.batches[0].estimated_prompt_bytes == initial_bytes
    assert render_batch_prompt(plan.batches[0]) == initial_render


# ==============================================================================
# NHÓM 10: G06-06A1-R2 RESIDUAL VALIDATION & BOUNDS
# ==============================================================================

def test_mapping_window_bigint_overflow_controlled_value_error():
    """Mapping window chứa start_sec hoặc end_sec 10**400 phải raise ValueError có lý do, không OverflowError."""
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    manifest = MockClipManifest(project_name="P", manifest_id="m", clips=[c1])

    cache_overflow = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": 10**400, "end_sec": 10**400}],
        }
    }
    with pytest.raises(ValueError) as exc:
        partition_manifest_into_batches(manifest, analysis_cache=cache_overflow)
    assert "ERR_INVALID_WINDOW" in str(exc.value)


def test_skipped_clip_does_not_mask_malformed_fields():
    """
    Các clip bị loại khỏi kế hoạch (duration unknown, source missing, source changed)
    KHÔNG được che giấu dữ liệu sai kiểu trong snapshot analysis hoặc metadata clip.
    """
    # 1. duration 0 + speech_status=False
    c_zero_dur = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=0.0)
    m1 = MockClipManifest(project_name="P", manifest_id="m", clips=[c_zero_dur])
    with pytest.raises(ValueError) as exc1:
        partition_manifest_into_batches(m1, analysis_cache={"C01": {"speech_status": False}})
    assert "ERR_INVALID_ANALYSIS" in str(exc1.value)

    # 2. duration None + windows chứa phần tử không hợp lệ
    c_none_dur = MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=None)
    m2 = MockClipManifest(project_name="P", manifest_id="m", clips=[c_none_dur])
    with pytest.raises(ValueError) as exc2:
        partition_manifest_into_batches(m2, analysis_cache={"C02": {"windows": ["bad_window_string"]}})
    assert "ERR_INVALID_WINDOW" in str(exc2.value)

    # 3. source_missing + has_audio='bad'
    c_missing = MockClipEntry(clip_id="C03", content_hash="h3", file_name="c3.mp4", duration_sec=10.0)
    m3 = MockClipManifest(project_name="P", manifest_id="m", clips=[c_missing])
    with pytest.raises(ValueError) as exc3:
        partition_manifest_into_batches(m3, analysis_cache={"C03": {"speech_status": "source_missing", "has_audio": "bad"}})
    assert "ERR_INVALID_ANALYSIS" in str(exc3.value)

    # Ngược lại: duration unknown hoặc source_missing với dữ liệu chuẩn thì vẫn được chấp nhận hợp lệ
    c_valid_unaccounted = MockClipEntry(clip_id="C04", content_hash="h4", file_name="c4.mp4", duration_sec=0.0)
    m4 = MockClipManifest(project_name="P", manifest_id="m", clips=[c_valid_unaccounted])
    plan4 = partition_manifest_into_batches(m4, analysis_cache={"C04": {"speech_status": "unscanned", "has_audio": None}})
    assert "C04" in plan4.unaccounted_clips
    assert plan4.issues[0].reason == "DURATION_UNKNOWN"


def test_identity_and_metadata_preserved_without_alteration():
    """
    Bảo toàn nguyên bản raw strings của manifest_id/clip_id/window_id (kể cả có whitespace hợp lệ);
    Không strip() giá trị được giữ. project_name=None, file_name=None, content_hash=None bị reject.
    """
    # Whitespace hợp lệ trong manifest_id được giữ nguyên
    c1 = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=10.0)
    m_space = MockClipManifest(project_name="P", manifest_id=" M ", clips=[c1])
    plan_space = partition_manifest_into_batches(m_space, analysis_cache={})
    assert plan_space.manifest_id == " M "
    assert plan_space.batches[0].manifest_id == " M "
    rendered = render_batch_prompt(plan_space.batches[0])
    assert '"manifest_id":" M "' in rendered

    # Blank thuần túy bị reject
    m_blank = MockClipManifest(project_name="P", manifest_id="   ", clips=[c1])
    with pytest.raises(ValueError) as exc_blank:
        partition_manifest_into_batches(m_blank, analysis_cache={})
    assert "ERR_INVALID_MANIFEST" in str(exc_blank.value)

    # project_name = None bị reject
    m_none_proj = MockClipManifest(project_name=None, manifest_id="m", clips=[c1])
    with pytest.raises(ValueError) as exc_p:
        partition_manifest_into_batches(m_none_proj, analysis_cache={})
    assert "ERR_INVALID_MANIFEST" in str(exc_p.value)

    # file_name = None bị reject
    c_none_file = MockClipEntry(clip_id="C01", content_hash="h1", file_name=None, duration_sec=10.0)
    m_none_file = MockClipManifest(project_name="P", manifest_id="m", clips=[c_none_file])
    with pytest.raises(ValueError) as exc_f:
        partition_manifest_into_batches(m_none_file, analysis_cache={})
    assert "ERR_INVALID_CLIP" in str(exc_f.value)

    # content_hash = None bị reject
    c_none_hash = MockClipEntry(clip_id="C01", content_hash=None, file_name="c1.mp4", duration_sec=10.0)
    m_none_hash = MockClipManifest(project_name="P", manifest_id="m", clips=[c_none_hash])
    with pytest.raises(ValueError) as exc_h:
        partition_manifest_into_batches(m_none_hash, analysis_cache={})
    assert "ERR_INVALID_CLIP" in str(exc_h.value)


def test_window_bounds_zero_tolerance_and_one_past_end():
    """
    Ranh giới window chính xác tuyệt đối không epsilon:
    - Interval: 0 <= start < end <= duration. end=1.000001 với duration=1.0 phải raise ValueError.
    - end=1.0 với duration=1.0 hợp lệ.
    - Point: 0 <= t < duration. Point t=1.0 với duration=1.0 (t == duration) phải raise ValueError.
    - Point t=600.0 tại ranh giới partition của clip 1200s thuộc partition bên phải [600, 1200).
    """
    c_1s = MockClipEntry(clip_id="C01", content_hash="h1", file_name="c1.mp4", duration_sec=1.0)
    m_1s = MockClipManifest(project_name="P", manifest_id="m", clips=[c_1s])

    # 1. end = 1.000001 vượt nhẹ 1.0s -> BẮT BUỘC PHẢI TỪ CHỐI
    cache_overrun = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 1.000001}],
        }
    }
    with pytest.raises(ValueError) as exc_over:
        partition_manifest_into_batches(m_1s, analysis_cache=cache_overrun)
    assert "ERR_WINDOW_OUT_OF_BOUNDS" in str(exc_over.value)

    # 2. end = 1.0 đúng thời lượng -> HỢP LỆ
    cache_exact_end = {
        "C01": {
            "windows": [{"window_id": "W1", "start_sec": 0.0, "end_sec": 1.0}],
        }
    }
    plan_exact = partition_manifest_into_batches(m_1s, analysis_cache=cache_exact_end)
    assert len(plan_exact.batches[0].items[0].windows) == 1

    # 3. Point observation tại t = 1.0 (t == duration) -> BẮT BUỘC PHẢI TỪ CHỐI
    cache_point_end = {
        "C01": {
            "windows": [{"window_id": "W_point", "start_sec": 1.0, "end_sec": 1.0}],
        }
    }
    with pytest.raises(ValueError) as exc_point:
        partition_manifest_into_batches(m_1s, analysis_cache=cache_point_end)
    assert "ERR_WINDOW_OUT_OF_BOUNDS" in str(exc_point.value)

    # 4. Point observation tại boundary nội bộ t = 600.0 trong clip 1200s (window_partition = 600s)
    c_1200 = MockClipEntry(clip_id="C02", content_hash="h2", file_name="c2.mp4", duration_sec=1200.0)
    m_1200 = MockClipManifest(project_name="P", manifest_id="m", clips=[c_1200])
    cache_boundary_point = {
        "C02": {
            "windows": [
                {"window_id": "W_mid", "start_sec": 600.0, "end_sec": 600.0},
            ]
        }
    }
    plan_1200 = partition_manifest_into_batches(m_1200, analysis_cache=cache_boundary_point)
    assert len(plan_1200.batches) == 2
    # Partition 0 [0, 600) KHÔNG chứa W_mid
    assert len(plan_1200.batches[0].items[0].windows) == 0
    # Partition 1 [600, 1200) CHỨA W_mid
    assert len(plan_1200.batches[1].items[0].windows) == 1
    assert plan_1200.batches[1].items[0].windows[0].window_id == "W_mid"
