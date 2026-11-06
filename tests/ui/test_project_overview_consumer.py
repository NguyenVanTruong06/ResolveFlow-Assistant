"""
tests/ui/test_project_overview_consumer.py
=============================================================================
Finite TDD Test Suite cho G06-06A4c2b-I / Section B:
Explicit Async Copy Toàn Project (Project Overview Consumer UI).

1. UI Elements & State Refresh:
   - btn_copy_project_overview và lbl_overview_status hiện diện, đúng nhãn, mặc định disabled.
   - Disabled khi không current snapshot, 0 draft, close pending hoặc worker busy.
   - Enabled khi có snapshot, >= 1 draft, và không có worker vật lý bận.
2. Explicit Async Action & Dispatch:
   - Chỉ explicit click mới dispatch, không auto-copy khi prepare/intake/select.
   - Validation SSOT: context signature, snapshot, ledger entries, >= 1 draft.
   - Zero GUI IO: không model/transcription/manifest stat trên UI thread.
3. Terminal Authority & Ownership:
   - Terminal success: active request/generation/pending khớp.
   - Recompute ledger signature đủ cả received set -> add/delete/replace đều stale (0 copy).
   - Combo box select-only KHÔNG làm stale overview.
   - Lấy artifact từ owned worker của pending, đối soát validate_project_overview_result_against_artifact.
   - Đúng 1 clipboard.setText(prompt_text), 0 clear/setText khi lỗi/stale/hủy/tamper.
   - Label hiển thị M/N và source gaps trung thực.
=============================================================================
"""

import copy
import dataclasses
import hashlib
import json
import os
import time
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThread

from tests.ui.test_project_overview_worker import (
    _build_snapshot_fixture,
    _make_dummy_draft_entry,
)
from src.ui.director.pages.review_page import ReviewPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _wait_for_workers(page, timeout_s: float = 6.0):
    """Đợi worker hoàn thành an toàn qua monotonic deadline và processEvents."""
    start = time.monotonic()
    while bool(getattr(page, "_prompt_workers_registry", None)) and (time.monotonic() - start < timeout_s):
        QApplication.processEvents()
        time.sleep(0.02)
    # Cooperative cleanup
    if hasattr(page, "_prompt_workers_registry"):
        for w in list(page._prompt_workers_registry.values()):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception:
                    pass
            if hasattr(w, "wait"):
                try:
                    w.wait(500)
                except Exception:
                    pass
    QApplication.processEvents()


def _seed_review_page_with_batches_and_draft(page, tmp_path):
    """Thiết lập ReviewPage với prepared_batch_snapshot chuẩn và 1 draft trong ledger."""
    ctx = {
        "video_paths": [str(tmp_path / "clip1.mp4"), str(tmp_path / "clip2.mp4")],
        "whisper_model": "small",
        "whisper_language": "vi",
        "enable_visual_describe": False,
        "vision_model": "",
        "vision_endpoint": "http://127.0.0.1:11434",
        "window_sec": 8.0,
        "frames_per_window": 2,
        "max_windows": 10,
        "max_refinements": 1,
        "style_intent": "travel_vlog",
        "hook_duration": "30_60s",
        "target_duration": "full",
        "project_name": "ChunDVC_Overview_Project",
        "max_prompt_bytes": 32768,
        "manifest_id": "man_overview_test",
    }
    ui_sig = page._extract_context_signature(ctx)
    snap, v1, v2, s_id = _build_snapshot_fixture(tmp_path, max_b=32768, ui_sig=ui_sig)
    ctx["video_paths"] = [v1, v2]

    page._stored_validated_context = copy.deepcopy(ctx)
    page._get_current_context = lambda: copy.deepcopy(ctx)
    page._prepared_batch_snapshot = snap

    # Cập nhật combo box
    page.cbo_batch_selector.blockSignals(True)
    page.cbo_batch_selector.clear()
    for b in snap.batches:
        page.cbo_batch_selector.addItem(f"Lô {b.batch_index + 1} ({b.batch_id})", b.batch_id)
    page.cbo_batch_selector.blockSignals(False)

    # Nạp 1 draft vào ledger cho B01
    entry = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B01", source_ranges=snap.batches[0].source_ranges)
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): entry}

    return snap, entry, ctx


# =============================================================================
# 1. UI PRESENCE & STATE REFRESH TESTS
# =============================================================================

def test_overview_ui_components_presence_and_defaults(qapp):
    """btn_copy_project_overview và lbl_overview_status có mặt, đúng nhãn, mặc định disabled."""
    page = ReviewPage()
    assert hasattr(page, "btn_copy_project_overview")
    assert hasattr(page, "lbl_overview_status")
    assert page.btn_copy_project_overview.text() == "📋 Sao Chép Tổng Quan Dự Án"
    assert page.btn_copy_project_overview.isEnabled() is False


def test_overview_button_state_refresh_conditions(qapp, tmp_path):
    """btn_copy_project_overview được bật/tắt đúng theo các điều kiện logic."""
    page = ReviewPage()
    assert page.btn_copy_project_overview.isEnabled() is False

    # Snapshot rỗng -> False
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False

    # Seed snapshot nhưng chưa có draft -> False
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._batch_drafts_ledger = {}
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False

    # Có 1 draft -> True!
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): entry}
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is True

    # Close pending -> False
    page._review_close_pending = True
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False
    page._review_close_pending = False

    # Worker registry có worker đang chạy -> False
    dummy_worker = MagicMock()
    page._prompt_workers_registry["req_busy"] = dummy_worker
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False
    page._prompt_workers_registry.clear()

    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is True


# =============================================================================
# 2. EXPLICIT ASYNC COPY ACTION & TERMINAL SUCCESS TESTS
# =============================================================================

def test_overview_copy_success_real_qthread_and_single_clipboard_call(qapp, tmp_path):
    """Click nút Sao Chép Tổng Quan Dự Án khởi chạy QThread thật và ghi Clipboard đúng 1 lần."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is True

    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        # Explicit click
        page._on_copy_project_overview_clicked()

        # Nút bị khóa trong lúc đang chạy
        assert page.btn_copy_project_overview.isEnabled() is False

        # Đợi worker hoàn tất
        active_id = page._active_prompt_request_id
        worker = page._prompt_workers_registry.get(active_id)
        assert worker is not None
        worker.wait(5000)

        # Chờ Qt xử lý tín hiệu
        QApplication.processEvents()

        # Xác nhận clipboard được gọi đúng 1 lần với full text
        assert mock_set_text.call_count == 1
        copied_text = mock_set_text.call_args[0][0]
        assert "TỔNG QUAN DỰ ÁN" in copied_text or "PROJECT OVERVIEW" in copied_text or "LÔ KỊCH BẢN" in copied_text
        assert "B01" in copied_text

        # Label trạng thái hiển thị trung thực (1/3 lô)
        assert "1/3" in page.lbl_overview_status.text()
        assert page.btn_copy_project_overview.isEnabled() is True


def test_overview_stale_detection_on_draft_mutation_prevents_copy(qapp, tmp_path):
    """Nếu ledger bị thay đổi (add/delete/replace draft) sau khi dispatch overview worker,
    terminal phát hiện stale ledger signature và từ chối ghi Clipboard (0 copy)."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._update_overview_button_state()

    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        # Dispatch
        page._on_copy_project_overview_clicked()
        active_id = page._active_prompt_request_id
        worker = page._prompt_workers_registry.get(active_id)
        assert worker is not None

        # Simulate user mutated ledger: thêm 1 draft mới cho B02 trước khi worker trả kết quả
        entry2 = _make_dummy_draft_entry(snap.manifest_id, snap.snapshot_id, "B02", source_ranges=snap.batches[1].source_ranges)
        page._batch_drafts_ledger[(snap.snapshot_id, "B02")] = entry2

        worker.wait(5000)
        QApplication.processEvents()

        # Clipboard tuyệt đối KHÔNG được ghi (0 copy) vì ledger đã đổi
        assert mock_set_text.call_count == 0
        assert "thay đổi" in page.lbl_overview_status.text().lower()


def test_overview_combo_selection_change_does_not_stale(qapp, tmp_path):
    """Chuyển đổi lựa chọn lô trên combo box đơn thuần KHÔNG làm stale overview prompt."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._update_overview_button_state()

    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        page._on_copy_project_overview_clicked()
        active_id = page._active_prompt_request_id
        worker = page._prompt_workers_registry.get(active_id)

        # User chuyển sang xem Lô 2 trên UI
        page.cbo_batch_selector.setCurrentIndex(1)
        QApplication.processEvents()

        worker.wait(5000)
        QApplication.processEvents()

        # Vẫn copy thành công vì ledger và snapshot không đổi!
        assert mock_set_text.call_count == 1


def test_overview_tamper_artifact_detected_and_rejected(qapp, tmp_path):
    """Nếu artifact bị giả mạo hoặc lệch hash/counters với result -> từ chối ghi clipboard (0 copy)."""
    import dataclasses
    from src.ui.director.workers.prompt_preparation_worker import PromptPreparationWorker

    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._update_overview_button_state()

    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        orig_get_artifact = PromptPreparationWorker.get_project_overview_artifact

        def tampered_get_artifact(self):
            real_art = orig_get_artifact(self)
            if real_art is not None:
                tampered_overview = dataclasses.replace(real_art.overview, rendered_prompt="TAMPERED PROMPT WITHOUT MATCHING SHA")
                return dataclasses.replace(real_art, overview=tampered_overview)
            return None

        with patch.object(PromptPreparationWorker, "get_project_overview_artifact", tampered_get_artifact):
            page._on_copy_project_overview_clicked()
            active_id = page._active_prompt_request_id
            worker = page._prompt_workers_registry.get(active_id)
            assert worker is not None

            worker.wait(5000)
            QApplication.processEvents()

            # Clipboard không bị ghi vì validate against artifact phát hiện tamper
            assert mock_set_text.call_count == 0
            assert "thất bại" in page.lbl_overview_status.text().lower()


# =============================================================================
# 3. CONTROLLED PREFLIGHT & RESIDUAL VALIDATION TESTS (G06-06A4c2b-R1)
# =============================================================================

def test_overview_preflight_corrupted_counter_rejected_cleanly(qapp, tmp_path):
    """Corrupted counter (total_selected_duration_sec=True) trả về None có kiểm soát,
    không ném exception ra ngoài, không đổi generation, không dispatch worker."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)

    # Đặt counter sai kiểu (bool thay vì float)
    bad_entry = dataclasses.replace(entry, total_selected_duration_sec=True)
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): bad_entry}

    init_gen = page._prompt_generation
    req_id = page.request_prompt_preparation(intent="copy_project_overview")

    # Bắt buộc trả về None
    assert req_id is None
    # Generation không bị tăng
    assert page._prompt_generation == init_gen
    # Không có worker nào trong registry
    assert len(page._prompt_workers_registry) == 0
    # Nhãn trạng thái hiển thị thông báo lỗi thân thiện tiếng Việt
    assert "không hợp lệ" in page.lbl_overview_status.text().lower()


def test_overview_preflight_corrupted_object_entry_rejected_cleanly(qapp, tmp_path):
    """Entry là object() thô không có thuộc tính DTO trả về None có kiểm soát,
    không ném AttributeError ra ngoài."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)

    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): object()}

    init_gen = page._prompt_generation
    req_id = page.request_prompt_preparation(intent="copy_project_overview")

    assert req_id is None
    assert page._prompt_generation == init_gen
    assert len(page._prompt_workers_registry) == 0
    assert "không hợp lệ" in page.lbl_overview_status.text().lower()


def test_overview_preflight_malformed_keys_and_mismatched_inner_id_rejected_cleanly(qapp, tmp_path):
    """Khóa ledger không phải 2-tuple hoặc inner ID không khớp khóa đều bị từ chối sạch sẽ."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)

    # 1. Khóa ledger sai định dạng (string thay vì tuple)
    page._batch_drafts_ledger = {"invalid_key_string": entry}
    assert page.request_prompt_preparation(intent="copy_project_overview") is None

    # 2. Inner snapshot_id không khớp khóa
    mismatched_snap_entry = dataclasses.replace(entry, snapshot_id="FOREIGN_SNAP_ID")
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): mismatched_snap_entry}
    assert page.request_prompt_preparation(intent="copy_project_overview") is None

    # 3. Inner batch_id không khớp khóa
    mismatched_batch_entry = dataclasses.replace(entry, batch_id="B02")
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): mismatched_batch_entry}
    assert page.request_prompt_preparation(intent="copy_project_overview") is None


def test_overview_button_state_disabled_on_corrupted_ledger(qapp, tmp_path):
    """_update_overview_button_state tự động khóa nút khi ledger bị hỏng dữ liệu."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)

    # Hợp lệ -> Enabled
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is True

    # Corrupted counter -> Disabled
    bad_entry = dataclasses.replace(entry, total_selected_duration_sec=True)
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): bad_entry}
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False

    # Corrupted object -> Disabled
    page._batch_drafts_ledger = {(snap.snapshot_id, "B01"): object()}
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False

    # Corrupted key -> Disabled
    page._batch_drafts_ledger = {"bad_key": entry}
    page._update_overview_button_state()
    assert page.btn_copy_project_overview.isEnabled() is False


def test_overview_terminal_corrupted_ledger_denies_copy_and_cleans_pending(qapp, tmp_path):
    """Nếu ledger bị hỏng dữ liệu trong lúc worker đang xử lý, terminal từ chối 0 copy,
    xóa pending record và không ném exception."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)
    page._update_overview_button_state()

    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        page._on_copy_project_overview_clicked()
        active_id = page._active_prompt_request_id
        worker = page._prompt_workers_registry.get(active_id)
        assert worker is not None

        # Làm hỏng ledger trong lúc worker đang chạy
        page._batch_drafts_ledger[(snap.snapshot_id, "B01")] = object()

        _wait_for_workers(page)

        # 0 copy
        assert mock_set_text.call_count == 0
        assert page._pending_prompt_expected is None
        assert page.btn_copy_project_overview.isEnabled() is False


def test_overview_terminal_pending_gate_checks_policy_and_context(qapp, tmp_path):
    """Terminal đối soát đầy đủ các trường của pending record: policy, context_signature, worker_ref."""
    page = ReviewPage()
    snap, entry, ctx = _seed_review_page_with_batches_and_draft(page, tmp_path)

    # 1. Missing pending record -> 0 copy
    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        page._on_copy_project_overview_clicked()
        page._pending_prompt_expected = None
        _wait_for_workers(page)
        assert mock_set_text.call_count == 0

    # 2. Wrong worker_ref in pending record -> 0 copy
    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        page._on_copy_project_overview_clicked()
        page._pending_prompt_expected["worker_ref"] = MagicMock(get_project_overview_artifact=lambda: None)
        _wait_for_workers(page)
        assert mock_set_text.call_count == 0

    # 3. Policy mismatch in pending record -> 0 copy
    with patch.object(QApplication.clipboard(), "setText") as mock_set_text:
        page._on_copy_project_overview_clicked()
        page._pending_prompt_expected["formatter_policy"] = "unsupported_policy_v99"
        _wait_for_workers(page)
        assert mock_set_text.call_count == 0

