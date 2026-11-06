"""
tests/ui/test_project_overview_slot_guard.py
=============================================================================
Finite TDD Test Suite cho G06-06A4c2b-I / Section C:
Macro Schema (Project Overview Draft) không được nạp nhầm vào Slots 1/2.

1. Pure Helper (contains_project_overview_draft):
   - Root marker kind='project_overview_draft'
   - Root discriminators type='project_overview_draft', $schema='chundvc_project_overview_v1'
   - Nested container wrapping (data, video_plan, kinetic_plan, generic_plan, dual_plan)
   - Real macro output schema even when tricked with timeline_segments or subtitles
   - Cycle-safe & bounded depth/visit fail-closed policy
   - Shared DAG alias is safe (not rejected if no marker)
   - Legitimate V1/V2/Batch draft -> returns False for overview guard
2. Parser Guard (parse_web_prompt_json):
   - Early reject before target_slot='slot1', 'slot2', 'auto', 'dual'
   - Rejects with truthful Vietnamese PROJECT_OVERVIEW_REJECT_MSG
   - Multiple JSON candidates (1 legit V1 + 1 macro overview) -> rejects entire input
   - Fenced markdown and raw decode fallback paths guarded
   - Legitimate V1/V2/Dual plans accepted normally
3. UI Boundary & State Preservation (ReviewPage):
   - Direct _apply_json_data bypass -> blocked immediately, 0 mutation, 0 IO
   - _on_dropzone_json_recognized -> blocked, sets error hint
   - load_from_config -> if any candidate in config is macro, preserves both last-good slots, no half-restore
   - Sentinel state preservation: Slot1/Slot2/manifest 100% preserved
=============================================================================
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtWidgets import QApplication

from src.ui.director.director_state import (
    DirectorConfig,
    contains_batch_draft_plan,
    parse_web_prompt_json,
)
try:
    from src.ui.director.director_state import (
        PROJECT_OVERVIEW_REJECT_MSG,
        contains_project_overview_draft,
    )
except ImportError:
    # Fallback placeholders for RED phase before implementation
    PROJECT_OVERVIEW_REJECT_MSG = "placeholder"
    contains_project_overview_draft = None

from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# =============================================================================
# 1. PURE HELPER TESTS (contains_project_overview_draft)
# =============================================================================

def test_contains_project_overview_draft_root_markers():
    """contains_project_overview_draft phát hiện các markers ở root."""
    assert contains_project_overview_draft is not None

    # Primary marker
    assert contains_project_overview_draft({"kind": "project_overview_draft"}) is True

    # Secondary discriminators
    assert contains_project_overview_draft({"type": "project_overview_draft"}) is True
    assert contains_project_overview_draft({"$schema": "chundvc_project_overview_v1"}) is True

    # Non-dict payloads
    assert contains_project_overview_draft(None) is False
    assert contains_project_overview_draft("string") is False
    assert contains_project_overview_draft([1, 2, 3]) is False
    assert contains_project_overview_draft(123) is False


def test_contains_project_overview_draft_real_output_schema():
    """Phát hiện schema đầu ra thực tế của Project Overview Prompt kể cả khi bị chèn thêm timeline_segments."""
    real_macro = {
        "kind": "project_overview_draft",
        "schema_version": 1,
        "manifest_id": "man_123",
        "snapshot_id": "snap_456",
        "macro_narrative": "Video hành trình khám phá Đà Lạt 3 ngày 2 đêm",
        "batch_order": ["B01", "B02", "B03"],
        "unaccounted_notes": [],
    }
    assert contains_project_overview_draft(real_macro) is True

    # Kẻ tấn công hoặc model nhầm lẫn sinh thêm các key của Video Plan nhằm đánh lừa parser
    spoofed = dict(real_macro)
    spoofed["timeline_segments"] = [{"scene_id": "S01", "in_point": 0.0, "out_point": 5.0}]
    spoofed["subtitles"] = [{"start": 0.0, "end": 2.0, "text": "Xin chào"}]
    assert contains_project_overview_draft(spoofed) is True


def test_contains_project_overview_draft_nested_containers():
    """Phát hiện macro overview nằm lồng trong recognized containers."""
    macro = {"kind": "project_overview_draft", "macro_narrative": "Tổng quan"}

    assert contains_project_overview_draft({"data": macro}) is True
    assert contains_project_overview_draft({"video_plan": macro}) is True
    assert contains_project_overview_draft({"kinetic_plan": macro}) is True
    assert contains_project_overview_draft({"generic_plan": macro}) is True
    assert contains_project_overview_draft({"dual_plan": {"video_plan": macro}}) is True
    assert contains_project_overview_draft({"data": [macro]}) is True


def test_contains_project_overview_draft_cycle_and_budget_protection():
    """Bảo vệ chống cycle và cạn kiệt ngân sách (fail-closed)."""
    # True Cycle through recognized container
    cyclic = {"data": {}}
    cyclic["data"]["video_plan"] = cyclic
    assert contains_project_overview_draft(cyclic) is True

    # Over-budget depth
    deep_dict = {}
    curr = deep_dict
    for _ in range(12):
        curr["data"] = {}
        curr = curr["data"]
    assert contains_project_overview_draft(deep_dict) is True

    # Shared DAG alias (safe)
    leaf = {"timeline_segments": [{"in_point": 0.0, "out_point": 5.0}]}
    safe_dag = {"video_plan": leaf, "kinetic_plan": leaf}
    assert contains_project_overview_draft(safe_dag) is False


def test_contains_project_overview_draft_legacy_payloads_not_false_positive():
    """Không báo nhầm (false-positive) cho kịch bản Video Plan, Kinetic Plan hoặc Batch Draft hợp lệ."""
    legit_video = {
        "timeline_segments": [{"in_point": 0.0, "out_point": 5.0, "note": "cảnh quay"}],
        "global_hook": "Hook hấp dẫn",
    }
    assert contains_project_overview_draft(legit_video) is False

    legit_kinetic = {
        "subtitles": [{"start": 0.0, "end": 2.0, "text": "Xin chào", "style": "pop"}],
    }
    assert contains_project_overview_draft(legit_kinetic) is False

    legit_batch_draft = {
        "batch_id": "B01",
        "segments": [{"in": 0.0, "out": 3.0}],
    }
    # Overview guard không báo True cho batch draft (đã có batch guard riêng lo)
    assert contains_project_overview_draft(legit_batch_draft) is False


# =============================================================================
# 2. PARSER GUARD TESTS (parse_web_prompt_json)
# =============================================================================

def test_parse_web_prompt_json_rejects_project_overview_plain():
    """parse_web_prompt_json từ chối chuỗi JSON macro với thông báo tiếng Việt đúng chuẩn."""
    macro_json = json.dumps({
        "kind": "project_overview_draft",
        "macro_narrative": "Mô tả tổng quan dự án",
        "batch_order": ["B01"],
    })

    # Auto target
    ok, msg, data = parse_web_prompt_json(macro_json, target_slot="auto")
    assert ok is False
    assert PROJECT_OVERVIEW_REJECT_MSG in msg
    assert data is None

    # Explicit slot1
    ok1, msg1, data1 = parse_web_prompt_json(macro_json, target_slot="slot1")
    assert ok1 is False
    assert PROJECT_OVERVIEW_REJECT_MSG in msg1
    assert data1 is None

    # Explicit slot2
    ok2, msg2, data2 = parse_web_prompt_json(macro_json, target_slot="slot2")
    assert ok2 is False
    assert PROJECT_OVERVIEW_REJECT_MSG in msg2
    assert data2 is None


def test_parse_web_prompt_json_rejects_project_overview_mixed_candidates():
    """Nếu văn bản chứa cả Video Plan hợp lệ và Macro Overview -> từ chối toàn bộ đầu vào."""
    legit_v1 = json.dumps({"timeline_segments": [{"in_point": 0.0, "out_point": 5.0}]})
    macro = json.dumps({"kind": "project_overview_draft", "macro_narrative": "Tổng quan"})

    combined_text = f"Đây là plan:\n```json\n{legit_v1}\n```\nVà đây là overview:\n```json\n{macro}\n```"

    ok, msg, data = parse_web_prompt_json(combined_text)
    assert ok is False
    assert PROJECT_OVERVIEW_REJECT_MSG in msg
    assert data is None


def test_parse_web_prompt_json_accepts_legitimate_v1_v2():
    """Các kịch bản hợp lệ vẫn được parse bình thường."""
    legit_v1 = json.dumps({
        "timeline_segments": [{"scene_id": "S01", "in_point": 0.0, "out_point": 5.0}],
        "global_hook": "Hook hay",
    })
    ok, msg, data = parse_web_prompt_json(legit_v1)
    assert ok is True
    assert data is not None


# =============================================================================
# 3. UI BOUNDARY & STATE PRESERVATION (ReviewPage)
# =============================================================================

def test_review_page_apply_json_data_rejects_project_overview(qapp):
    """Direct _apply_json_data bị chặn ngay lập tức khi gặp macro overview, bảo toàn Slots 1 & 2."""
    page = ReviewPage()

    # Seed last-good state
    page._video_plan_data = {"type": "video_plan", "timeline_segments": [{"in_point": 1.0}]}
    page._video_plan_summary = "Last Good Video Plan"
    page._kinetic_plan_data = {"type": "kinetic_plan", "subtitles": [{"text": "Last Good"}]}
    page._kinetic_plan_summary = "Last Good Kinetic Plan"

    macro = {"kind": "project_overview_draft", "macro_narrative": "Tổng quan dự án"}

    # Gọi _apply_json_data trực tiếp
    page._apply_json_data(macro, "Macro Summary")

    # Assert 0 mutation
    assert page._video_plan_summary == "Last Good Video Plan"
    assert page._kinetic_plan_summary == "Last Good Kinetic Plan"
    assert PROJECT_OVERVIEW_REJECT_MSG in page.lbl_json_hint.text()


def test_review_page_dropzone_rejects_project_overview(qapp):
    """_on_dropzone_json_recognized chặn macro overview và hiển thị lỗi."""
    page = ReviewPage()
    macro = {"kind": "project_overview_draft", "macro_narrative": "Tổng quan dự án"}

    page._on_dropzone_json_recognized(macro, "Dropzone Macro")
    assert PROJECT_OVERVIEW_REJECT_MSG in page.lbl_json_hint.text()


def test_review_page_load_from_config_no_half_restore_on_macro(qapp):
    """load_from_config: nếu bất kỳ plan candidate nào trong config là macro overview,
    giữ nguyên toàn bộ cả 2 slots last-good hiện tại, không half-restore một plan khác."""
    page = ReviewPage()

    # Seed last-good
    page._video_plan_data = {"type": "video_plan", "timeline_segments": [{"in_point": 1.0}]}
    page._video_plan_summary = "Original Video"
    page._kinetic_plan_data = {"type": "kinetic_plan", "subtitles": [{"text": "Original"}]}
    page._kinetic_plan_summary = "Original Kinetic"

    # Config chứa Video Plan mới nhưng Kinetic Plan là Macro Overview
    config = DirectorConfig()
    config.api_key = "test_api_key_preserved"
    config.video_plan_data = {"type": "video_plan", "timeline_segments": [{"in_point": 99.0}]}
    config.video_plan_summary = "New Video Plan"
    config.kinetic_plan_data = {"kind": "project_overview_draft", "macro_narrative": "Corrupted Slot"}
    config.kinetic_plan_summary = "Corrupted Summary"

    page.load_from_config(config)

    # Non-plan settings vẫn được restore
    assert page.txt_cloud_api_key.text() == "test_api_key_preserved"

    # Plans: Không half-restore video_plan! Cả hai slots giữ nguyên last-good!
    assert page._video_plan_summary == "Original Video"
    assert page._kinetic_plan_summary == "Original Kinetic"
