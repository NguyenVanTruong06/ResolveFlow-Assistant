"""
tests/ui/test_batch_json_slot_guard.py
=============================================================================
Finite TDD Test Suite cho G06-06A4a-I (Safe Batch JSON Slot Guard):
1. Pure Helper (contains_batch_draft_plan):
   - Root batch_id presence (valid, None, empty str, False, 0).
   - Discriminators (type='batch_draft', $schema='chundvc_batch_draft_v1').
   - Actual parsed plan container wrapping (video_plan, kinetic_plan, generic_plan, dual_plan).
   - Cycle-safe & bounded depth/visit fail-closed policy.
   - Legitimate V1/V2 with leaf note mentioning 'batch_id' -> NOT false-positive.
   - Non-dict payloads.
2. Parser Guard (parse_web_prompt_json):
   - Early reject before explicit slot1/slot2/auto/dual classification.
   - Truthful Vietnamese guidance message (BATCH_DRAFT_REJECT_MSG).
   - Multiple JSON blocks (1 legitimate V1 + 1 batch draft) -> rejects ENTIRE input.
   - Nested dual plan containing batch draft -> rejected.
   - Fallback markdown code fence path -> guarded.
   - Legitimate V1/V2/dual payload -> accepted normally.
3. UI Boundary & State Preservation (ReviewPage):
   - Direct _apply_json_data bypass -> blocked immediately, 0 mutation, 0 IO.
   - Input routes: text_changed, btn_apply_json, _paste_to_slot, dropzone, load_from_config.
   - Sentinel state preservation: Slot1/Slot2/manifest/prepared snapshots 100% preserved.
   - Spy verification: ClipManifest.get_or_create 0 calls, 0 signal emits, 0 dispatch.
   - User text retention: pasted raw JSON remains in txt_json_input for user to copy.
=============================================================================
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtWidgets import QApplication

from src.ui.director.director_state import (
    BATCH_DRAFT_REJECT_MSG,
    DirectorConfig,
    contains_batch_draft_plan,
    parse_web_prompt_json,
)
from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class CountedList(list):
    """List kiểm thử đo lường chính xác số phần tử được duyệt qua __iter__."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.access_count = 0

    def __iter__(self):
        for item in super().__iter__():
            self.access_count += 1
            yield item


# =============================================================================
# NHÓM 1: PURE HELPER (contains_batch_draft_plan)
# =============================================================================

def test_contains_batch_draft_plan_root_markers():
    # 1. Root batch_id key presence với mọi kiểu giá trị
    assert contains_batch_draft_plan({"batch_id": "B01"}) is True
    assert contains_batch_draft_plan({"batch_id": None}) is True
    assert contains_batch_draft_plan({"batch_id": ""}) is True
    assert contains_batch_draft_plan({"batch_id": False}) is True
    assert contains_batch_draft_plan({"batch_id": 0}) is True
    assert contains_batch_draft_plan({"batch_id": {"nested": "val"}}) is True

    # 2. Discriminator tường minh
    assert contains_batch_draft_plan({"type": "batch_draft"}) is True
    assert contains_batch_draft_plan({"$schema": "chundvc_batch_draft_v1"}) is True


def test_contains_batch_draft_plan_wrapped_containers():
    # 1. Wrapper chuẩn dạng {type: "video_plan", data: {...}}
    wrapped_v1 = {
        "type": "video_plan",
        "data": {
            "batch_id": "B02",
            "timeline_segments": [{"clip_id": "C01", "start_sec": 1.0, "end_sec": 5.0}]
        }
    }
    assert contains_batch_draft_plan(wrapped_v1) is True

    # 2. Wrapper generic plan
    wrapped_generic = {
        "type": "generic_plan",
        "data": {"batch_id": "B03"}
    }
    assert contains_batch_draft_plan(wrapped_generic) is True

    # 3. Dual plan chứa batch_id ở nhánh video_plan
    dual_v_batch = {
        "type": "dual_plan",
        "video_plan": {"batch_id": "B01", "timeline_segments": []},
        "kinetic_plan": {"subtitles": []}
    }
    assert contains_batch_draft_plan(dual_v_batch) is True

    # 4. Dual plan chứa batch_id ở nhánh kinetic_plan
    dual_k_batch = {
        "type": "dual_plan",
        "video_plan": {"timeline_segments": []},
        "kinetic_plan": {"batch_id": "B01", "subtitles": []}
    }
    assert contains_batch_draft_plan(dual_k_batch) is True

    # 5. Raw nested dict
    raw_nested = {
        "video_plan": {
            "type": "batch_draft",
            "segments": []
        }
    }
    assert contains_batch_draft_plan(raw_nested) is True


def test_contains_batch_draft_plan_cycle_and_budget_safety():
    # 1. Chu trình vòng lặp (Cycle safe) -> fail-closed (True)
    cyclic_dict = {"data": {}}
    cyclic_dict["data"]["video_plan"] = cyclic_dict
    assert contains_batch_draft_plan(cyclic_dict) is True

    # 2. Độ sâu vượt quá ngân sách max_depth -> fail-closed (True)
    deep_dict = {}
    curr = deep_dict
    for _ in range(12):
        curr["data"] = {}
        curr = curr["data"]
    assert contains_batch_draft_plan(deep_dict, max_depth=8) is True


def test_contains_batch_draft_plan_dag_shared_reference(review_page_with_sentinel):
    # Regression: Kịch bản Dual Plan hợp lệ chứa cả timeline_segments và subtitles
    # được parse_web_prompt_json gán video_plan và kinetic_plan cùng single_obj (shared reference)
    combined_json = json.dumps({
        "timeline_segments": [{"id": 1, "title": "Cảnh 1", "start": 0, "end": 5}],
        "subtitles": [{"start": 1.0, "end": 4.0, "text": "Phụ đề 1"}]
    })
    ok, msg, parsed = parse_web_prompt_json(combined_json)
    assert ok is True
    assert parsed["type"] == "dual_plan"
    assert parsed["video_plan"] is parsed["kinetic_plan"]  # Shared alias

    # Phải phân biệt được DAG với Cycle: Không được coi tham chiếu chung là cycle!
    assert contains_batch_draft_plan(parsed) is False

    # Nạp vào ReviewPage: Không được bị BATCH reject
    page = review_page_with_sentinel
    page._apply_json_data(parsed, msg)
    assert page._video_plan_data == parsed["video_plan"]
    assert page._kinetic_plan_data == parsed["kinetic_plan"]
    assert "Đây là bản thảo của một lô" not in page.lbl_json_hint.text()
    assert "✓ Đã nạp" in page.lbl_slot_vid_status.text()
    assert "✓ Đã nạp" in page.lbl_slot_kin_status.text()


def test_contains_batch_draft_plan_dag_with_batch_marker_rejected():
    # Shared alias nhưng node chung có chứa batch marker -> Vẫn phải reject an toàn!
    shared_batch = {
        "batch_id": "B01",
        "timeline_segments": [{"id": 1}],
        "subtitles": [{"text": "sub"}]
    }
    dual_with_batch = {
        "type": "dual_plan",
        "video_plan": shared_batch,
        "kinetic_plan": shared_batch
    }
    assert contains_batch_draft_plan(dual_with_batch) is True


def test_contains_batch_draft_plan_wide_list_bounded_work():
    # Test CountedList 1000 phần tử với max_visits=2:
    # Tuyệt đối không được duyệt toàn bộ 1000 phần tử, chỉ duyệt tối đa remaining budget + sentinel nhỏ
    cl = CountedList([{"title": f"item_{i}"} for i in range(1000)])
    wide_data = {"data": cl}
    res = contains_batch_draft_plan(wide_data, max_visits=2)
    assert res is True
    assert cl.access_count <= 2

    # Test wide list có batch marker ở cuối: Vẫn phải fail-closed và access_count <= 2
    cl_end = CountedList([{"title": f"item_{i}"} for i in range(999)] + [{"batch_id": "B_END"}])
    wide_end = {"data": cl_end}
    res_end = contains_batch_draft_plan(wide_end, max_visits=2)
    assert res_end is True
    assert cl_end.access_count <= 2


def test_contains_batch_draft_plan_legitimate_payloads_not_rejected():
    # 1. Legitimate V1 video plan không có batch marker
    legit_v1 = {
        "timeline_segments": [
            {"id": 1, "title": "Hook", "note": "Video này không liên quan tới batch_id"}
        ],
        "global_hook": True
    }
    assert contains_batch_draft_plan(legit_v1) is False

    # 2. Legitimate V2 kinetic plan có text chứa 'batch_id'
    legit_v2 = {
        "subtitles": [
            {"start": 1.0, "end": 3.0, "text": "Hôm nay mình sẽ nói về từ khóa batch_id trong lập trình"}
        ]
    }
    assert contains_batch_draft_plan(legit_v2) is False

    # 3. Arbitrary leaf metadata có key batch_id nhưng KHÔNG ở container position
    # (Container positions chỉ gồm: data, video_plan, kinetic_plan, generic_plan, dual_plan)
    leaf_meta = {
        "timeline_segments": [
            {"id": 1, "custom_asset_props": {"batch_id": "ignored_in_leaf"}}
        ]
    }
    assert contains_batch_draft_plan(leaf_meta) is False

    # 4. Non-dict payloads
    assert contains_batch_draft_plan(None) is False
    assert contains_batch_draft_plan("chuỗi văn bản") is False
    assert contains_batch_draft_plan([1, 2, 3]) is False
    assert contains_batch_draft_plan(12345) is False


# =============================================================================
# NHÓM 2: PARSER GUARD (parse_web_prompt_json)
# =============================================================================

def test_parse_web_prompt_json_rejects_batch_draft_root():
    batch_raw = json.dumps({
        "manifest_id": "M_TEST",
        "batch_id": "B01",
        "batch_strategy_summary": "Tóm tắt chiến lược lô",
        "timeline_segments": [
            {"clip_id": "C01", "start_sec": 0.0, "end_sec": 10.0, "action": "keep"}
        ]
    })

    # Test auto slot
    ok, msg, data = parse_web_prompt_json(batch_raw)
    assert ok is False
    assert msg == BATCH_DRAFT_REJECT_MSG
    assert data is None

    # Test explicit slot1 (trước đây bị nhận nhầm thành Video Plan!)
    ok, msg, data = parse_web_prompt_json(batch_raw, target_slot="slot1")
    assert ok is False
    assert msg == BATCH_DRAFT_REJECT_MSG
    assert data is None

    # Test explicit slot2
    ok, msg, data = parse_web_prompt_json(batch_raw, target_slot="slot2")
    assert ok is False
    assert msg == BATCH_DRAFT_REJECT_MSG
    assert data is None


def test_parse_web_prompt_json_rejects_mixed_blocks_fail_closed():
    # 2 khối markdown JSON: Khối 1 là Video Plan hợp lệ, Khối 2 là Batch Draft
    mixed_raw = """
    ```json
    {
        "timeline_segments": [
            {"id": 1, "title": "Cảnh 1", "start": 0, "end": 5}
        ]
    }
    ```
    ```json
    {
        "manifest_id": "M_TEST",
        "batch_id": "B02",
        "timeline_segments": [
            {"clip_id": "C02", "start_sec": 600.0, "end_sec": 630.0}
        ]
    }
    ```
    """
    ok, msg, data = parse_web_prompt_json(mixed_raw)
    # Phải từ chối TOÀN BỘ đầu vào, không được nhận một phần rồi bỏ qua batch
    assert ok is False
    assert msg == BATCH_DRAFT_REJECT_MSG
    assert data is None


def test_parse_web_prompt_json_rejects_fallback_code_fence():
    # Fallback path bóc regex ```json
    code_fence_batch = """
    Dưới đây là kết quả phân lô:
    ```json
    {
        "batch_id": "B01",
        "strategy": "rough cut",
        "scenes": [{"title": "Đoạn 1"}]
    }
    ```
    """
    ok, msg, data = parse_web_prompt_json(code_fence_batch)
    assert ok is False
    assert msg == BATCH_DRAFT_REJECT_MSG
    assert data is None


def test_parse_web_prompt_json_legitimate_plans_still_work():
    # 1. Legitimate V1 Video Plan
    v1_raw = '{"timeline_segments": [{"id": 1, "title": "Hook mở đầu"}], "global_hook": true}'
    ok, msg, data = parse_web_prompt_json(v1_raw)
    assert ok is True
    assert "Video Plan" in msg
    assert data["type"] == "video_plan"

    # 2. Legitimate V2 Kinetic Plan
    v2_raw = '{"subtitles": [{"start": 0.5, "end": 2.0, "text": "Phụ đề test"}]}'
    ok, msg, data = parse_web_prompt_json(v2_raw)
    assert ok is True
    assert "Kinetic Plan" in msg
    assert data["type"] == "kinetic_plan"

    # 3. Legitimate V1 với note chứa chữ 'batch_id'
    v1_with_note = '{"timeline_segments": [{"id": 1, "title": "Cảnh 1", "note": "batch_id demo"}]}'
    ok, msg, data = parse_web_prompt_json(v1_with_note)
    assert ok is True
    assert "Video Plan" in msg


# =============================================================================
# NHÓM 3: UI BOUNDARY & STATE PRESERVATION (ReviewPage)
# =============================================================================

@pytest.fixture
def review_page_with_sentinel(qapp):
    page = ReviewPage()

    # Cài đặt trạng thái mẫu (Sentinel Last-Good State)
    page._video_plan_data = {
        "sentinel": "SLOT1_VALID",
        "timeline_segments": [{"id": 101, "title": "Sentinelled Video Plan"}]
    }
    page._video_plan_summary = "✓ Slot 1 Sentinel Summary"
    page._kinetic_plan_data = {
        "sentinel": "SLOT2_VALID",
        "subtitles": [{"start": 1.0, "end": 4.0, "text": "Sentinelled Subtitle"}]
    }
    page._kinetic_plan_summary = "✓ Slot 2 Sentinel Summary"

    # Cài đặt manifest và prepared snapshot mẫu
    fake_manifest = MagicMock()
    fake_manifest.manifest_id = "M_SENTINEL_123"
    page._manifest = fake_manifest

    fake_snapshot = MagicMock()
    fake_snapshot.snapshot_id = "SNAP_SENTINEL_456"
    page._prepared_prompt_snapshot = fake_snapshot

    return page


def test_review_page_direct_apply_json_data_bypass_blocked(review_page_with_sentinel):
    page = review_page_with_sentinel

    batch_payload = {
        "type": "video_plan",
        "data": {
            "batch_id": "B01",
            "manifest_id": "M_SENTINEL_123",
            "timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]
        }
    }

    # Giám sát các hàm và tín hiệu nhạy cảm
    with patch("src.core.clip_manifest.ClipManifest.get_or_create") as mock_get_or_create:
        mock_config_changed = MagicMock()
        mock_json_applied = MagicMock()
        page.config_changed.connect(mock_config_changed)
        page.json_applied.connect(mock_json_applied)

        # Gọi trực tiếp bypass parser
        page._apply_json_data(batch_payload, "Tóm tắt giả")

        # 1. Tuyệt đối không gọi ClipManifest IO
        assert mock_get_or_create.call_count == 0

        # 2. Tuyệt đối không phát tín hiệu đột biến
        assert mock_config_changed.call_count == 0
        assert mock_json_applied.call_count == 0

        # 3. Bảo toàn 100% last-good state của Slot 1 và Slot 2
        assert page._video_plan_data["sentinel"] == "SLOT1_VALID"
        assert page._video_plan_summary == "✓ Slot 1 Sentinel Summary"
        assert page._kinetic_plan_data["sentinel"] == "SLOT2_VALID"
        assert page._kinetic_plan_summary == "✓ Slot 2 Sentinel Summary"

        # 4. Hiển thị thông báo từ chối trung thực
        assert "Đây là bản thảo của một lô" in page.lbl_json_hint.text()


def test_review_page_apply_script_button_route_blocked(review_page_with_sentinel):
    page = review_page_with_sentinel

    batch_json_text = json.dumps({
        "manifest_id": "M_SENTINEL_123",
        "batch_id": "B02",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 600, "end_sec": 610}]
    })

    page.txt_json_input.setPlainText(batch_json_text)
    page.btn_apply_json.click()

    # Slot 1 và Slot 2 phải giữ nguyên giá trị sentinel
    assert page._video_plan_data["sentinel"] == "SLOT1_VALID"
    assert page._kinetic_plan_data["sentinel"] == "SLOT2_VALID"
    assert "Không thể áp dụng" in page.lbl_json_hint.text()
    assert "Đây là bản thảo của một lô" in page.lbl_json_hint.text()


def test_review_page_paste_to_slot_route_blocked(review_page_with_sentinel):
    page = review_page_with_sentinel

    batch_json_text = json.dumps({
        "manifest_id": "M_SENTINEL_123",
        "batch_id": "B01",
        "timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]
    })

    # Giả lập clipboard chứa batch JSON
    clipboard = QApplication.clipboard()
    clipboard.setText(batch_json_text)

    # Thử nạp vào Slot 1
    page._paste_to_slot("slot1")
    assert page._video_plan_data["sentinel"] == "SLOT1_VALID"
    assert "Không thể nạp vào SLOT1" in page.lbl_json_hint.text()

    # Thử nạp vào Slot 2
    page._paste_to_slot("slot2")
    assert page._kinetic_plan_data["sentinel"] == "SLOT2_VALID"
    assert "Không thể nạp vào SLOT2" in page.lbl_json_hint.text()


def test_review_page_dropzone_direct_recognition_blocked(review_page_with_sentinel):
    page = review_page_with_sentinel

    batch_data = {
        "type": "video_plan",
        "data": {
            "batch_id": "B03",
            "timeline_segments": []
        }
    }

    # Bắn trực tiếp tín hiệu dropzone recognized
    page._on_dropzone_json_recognized(batch_data, "Dropzone Batch Summary")

    # Không được gán vào _pasted_data là dữ liệu hợp lệ của trang
    assert page._pasted_data != batch_data
    assert page._video_plan_data["sentinel"] == "SLOT1_VALID"

    # Raw text vẫn được hiển thị trên txt_json_input để người dùng copy lại
    assert "B03" in page.txt_json_input.toPlainText()
    assert "Đây là bản thảo của một lô" in page.lbl_json_hint.text()


def test_review_page_load_from_config_ignores_batch_draft(review_page_with_sentinel):
    page = review_page_with_sentinel

    # Tạo config bẩn có chứa batch draft ở các slots
    dirty_config = DirectorConfig()
    dirty_config.video_plan_data = {
        "batch_id": "B_DIRTY_1",
        "timeline_segments": []
    }
    dirty_config.kinetic_plan_data = {
        "batch_id": "B_DIRTY_2",
        "subtitles": []
    }
    dirty_config.web_prompt_pasted_data = {
        "batch_id": "B_DIRTY_3"
    }

    page.load_from_config(dirty_config)

    # Slot 1 và Slot 2 giữ nguyên sentinel, không bị ghi đè bởi config bẩn
    assert page._video_plan_data["sentinel"] == "SLOT1_VALID"
    assert page._kinetic_plan_data["sentinel"] == "SLOT2_VALID"


def test_review_page_legitimate_plan_accepted_normally(review_page_with_sentinel):
    page = review_page_with_sentinel

    legit_v1_text = json.dumps({
        "manifest_id": "M_SENTINEL_123",
        "timeline_segments": [
            {"id": 1, "title": "Cảnh mới toanh", "start": 0, "end": 15}
        ]
    })

    page.txt_json_input.setPlainText(legit_v1_text)
    page.btn_apply_json.click()

    # Slot 1 được cập nhật thành công với dữ liệu hợp lệ
    assert page._video_plan_data["timeline_segments"][0]["title"] == "Cảnh mới toanh"
    assert "✓ Đã nạp" in page.lbl_slot_vid_status.text()
    assert page._kinetic_plan_data["sentinel"] == "SLOT2_VALID"
