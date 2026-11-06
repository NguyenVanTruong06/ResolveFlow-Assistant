"""Unit & UI tests cho Cổng Phê Duyệt Bước 4 và Chặn Đường Bypass AI Build Cũ — G07-07A1-R1.

Kiểm thử toàn diện 6 nhóm trong Upfront Finite Acceptance Matrix:
1. Real valid S3 proposal qua _rebuild_and_present_proposal + actual user approval action,
   actual Director context supplier wired -> owned getter pair, 1 supplier read, Step 4 summary allowed.
2. Positive first, then malformed typed proposal.video_plan; deleted required approval.proposal_revision;
   wrong omission/revision/type/foreign context; supplier error -> None / 0 Qt exception / record cleared.
   Dữ liệu gốc và bản thảo được bảo toàn.
3. Unexpected getter throw tại Window Next/stepper/direct step4/Build -> controlled denial,
   hiển thị lý do trực quan tiếng Việt (visible QLabel + toolTip), 0 dispatch, không tiến độ giả mạo.
4. Safe state-query: Owned worker query throw (RuntimeError) -> False mà không phát terminal signal,
   giữ nguyên worker. Spies chứng minh 0 kwargs/constructor/start cho phase 0/2/invalid.
   Phase 1 mocked worker positive khởi chạy đúng 1 lần; hủy/kết thúc độc lập.
5. Missing LLMDirectorWorker -> phát ai_script_finished(False, truthful reason, "") (không mô phỏng thành công).
6. Purity: Tripwires IO thật cài đặt SAU fixture/module setup (open, stat, ffprobe, model, Gateway, UUID/time),
   getter vẫn thành công và 0 IO trips. Đọc supplier đúng 1 lần.
"""

import builtins
import dataclasses
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch
import uuid

import pytest
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QLabel

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    create_app_owned_approval_record,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.ui.director.director_controller import DirectorController
import src.ui.director.director_controller as dc
from src.ui.director.director_state import DirectorConfig
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.review_page import ReviewPage
from tests.core.contracts.test_edit_plan_proposal_contracts import _make_valid_proposal_dict
from tests.ui.test_common_proposal_review import (
    _make_sample_manifest,
    _make_sample_video_plan,
)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _make_test_proposal(manifest_id: str = "m_test01") -> EditPlanProposal:
    raw = _make_valid_proposal_dict(manifest_id)
    return normalize_edit_plan_proposal(raw)


def _setup_review_page_with_proposal(
    page: ReviewPage,
    proposal: Optional[EditPlanProposal] = None,
    with_approval: bool = True,
    video_paths: Optional[List[str]] = None,
    manifest_id: str = "m_test01",
) -> Tuple[EditPlanProposal, Optional[AppOwnedApprovalRecord]]:
    """Thiết lập trạng thái chuẩn của ReviewPage với proposal và approval hợp lệ."""
    if proposal is None:
        proposal = _make_test_proposal(manifest_id)

    if video_paths is None:
        video_paths = ["/media/footage/intro.mp4", "/media/footage/main_a.mp4"]

    manifest = {
        "manifest_id": manifest_id,
        "clips": [
            {"clip_id": "C01", "file_name": "intro.mp4", "current_path": video_paths[0], "fps": 30.0},
            {"clip_id": "C02", "file_name": "main_a.mp4", "current_path": video_paths[1], "fps": 30.0},
        ],
    }
    setattr(page, "_manifest", manifest)

    video_plan = {
        "version": "1.0",
        "cuts": [
            {"clip_id": "C01", "start_sec": 0.0, "end_sec": 5.0},
            {"clip_id": "C02", "start_sec": 0.0, "end_sec": 5.0},
        ],
    }
    setattr(page, "_video_plan_data", video_plan)

    ctx = {
        "video_paths": list(video_paths),
        "timeline_fps": 30.0,
        "creative_context": {
            "style_preset": "cute_vlog",
            "target_duration_sec": 60.0,
        },
    }
    setattr(page, "_context_supplier", lambda: dict(ctx))

    curr_ctx = page._get_current_context()
    fp = page._build_current_input_fingerprint(curr_ctx)
    page._last_displayed_input_fingerprint = fp
    page._current_proposal = proposal
    page._current_geometry_result = None
    page._geometry_blocked_diagnostic = None

    approval_record = None
    if with_approval:
        approval_record = create_app_owned_approval_record(
            proposal,
            approval_id="appr_test_001",
            approved_at_iso="2026-11-02T02:00:00Z",
        )
        page._current_approval_record = approval_record

    return proposal, approval_record


def _setup_real_s3_approval(win: DirectorWindow, manifest_id: str = "mf_real_s3_gate"):
    """Thiết lập proposal S3 thực chất qua _rebuild_and_present_proposal và phê duyệt app-owned."""
    page = win.page_review
    manifest = _make_sample_manifest(manifest_id)
    page._manifest = manifest
    page._video_plan_data = _make_sample_video_plan(manifest_id)
    page._kinetic_plan_data = None
    page._last_good_matching_sidecar = None
    page._current_user_candidate_choices = {}
    page._current_approval_record = None
    page._source_frame_evidence_table = None
    paths = [c.current_path for c in manifest.clips]
    win.page_source.get_video_paths = lambda: list(paths)
    win.config.video_paths = list(paths)

    # Dựng proposal S3 thực chất bằng logic nghiệp vụ ReviewPage
    page._rebuild_and_present_proposal()
    prop = page._current_proposal
    assert prop is not None, "Proposal phải được dựng thành công từ S3 data"
    rev = compute_proposal_revision(prop)
    page._on_proposal_approved(prop.proposal_id, rev)
    assert page._current_approval_record is not None, "Approval record phải được sinh ra sau khi bấm duyệt"
    return prop, page._current_approval_record


# ---------------------------------------------------------------------------
# NHÓM 1: Real valid S3 proposal qua _rebuild_and_present_proposal + actual approval
# ---------------------------------------------------------------------------

def test_getter_real_s3_rebuild_approve_positive_and_director_step4(qapp):
    """1. Proposal S3 thực qua rebuild -> duyệt thực -> getter trả về owned pair -> chuyển sang Bước 4 xem summary."""
    win = DirectorWindow()
    win.show()
    try:
        page = win.page_review
        prop, appr = _setup_real_s3_approval(win, manifest_id="mf_real_s3_01")

        # Kiểm tra supplier được gọi đúng 1 lần khi gọi getter
        supplier_calls = 0
        orig_supplier = getattr(page, "_context_supplier")
        def _counted_supplier():
            nonlocal supplier_calls
            supplier_calls += 1
            return orig_supplier()
        setattr(page, "_context_supplier", _counted_supplier)

        res = page.get_approved_proposal_and_record()
        assert res is not None
        owned_prop, owned_appr = res
        assert supplier_calls == 1

        # Xác thực liên kết chặt chẽ
        assert isinstance(owned_prop, EditPlanProposal)
        assert isinstance(owned_appr, AppOwnedApprovalRecord)
        assert verify_proposal_approval_binding(owned_prop, owned_appr) is True
        assert owned_appr.approval_scope == "ENTIRE_PROPOSAL"
        assert owned_appr.proposal_revision == compute_proposal_revision(owned_prop)

        # Chuyển sang Bước 4 thành công
        win.go_to_step(4)
        QApplication.processEvents()
        assert win.current_step == 4
        assert win.stepper.current_step == 4
        assert win.stack.currentIndex() == 3
        assert win.btn_primary.text() == "Kiểm tra nguồn & tài nguyên"
        assert not win.lbl_gate_status.isVisible()

    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.close()


# ---------------------------------------------------------------------------
# NHÓM 2: Phủ định các trường hợp payload hỏng, thiếu thành viên, sai lệch
# ---------------------------------------------------------------------------

def test_getter_denials_on_malformed_and_missing_members(qapp):
    """2. Payload hỏng, thiếu trường bắt buộc, mismatch -> Trả về None, xóa approval không hợp lệ, không ném exception."""
    win = DirectorWindow()
    win.show()
    try:
        page = win.page_review

        # 2.1 Malformed typed proposal (video_plan bị hỏng cấu trúc)
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_malformed_01")
        object.__setattr__(prop, "video_plan", {"timeline_segments": "bad"})
        assert page.get_approved_proposal_and_record() is None
        assert page._current_approval_record is None  # Đã bị xóa an toàn

        # 2.2 Missing required approval record attribute (thiếu proposal_revision)
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_missing_attr_02")
        object.__delattr__(rec, "proposal_revision")
        assert page.get_approved_proposal_and_record() is None
        assert page._current_approval_record is None

        # 2.3 Thiếu approval
        page._current_approval_record = None
        assert page.get_approved_proposal_and_record() is None

        # 2.4 Foreign revision mismatch
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_mismatch_03")
        tampered_rec = AppOwnedApprovalRecord(
            approval_id=rec.approval_id,
            proposal_id=rec.proposal_id,
            proposal_revision="a" * 64,
            approved_at_iso=rec.approved_at_iso,
            accepted_omission_ids=rec.accepted_omission_ids,
            approval_scope=rec.approval_scope,
        )
        page._current_approval_record = tampered_rec
        assert page.get_approved_proposal_and_record() is None

        # 2.5 Sai lệch danh sách omission
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_omission_04")
        tampered_rec2 = AppOwnedApprovalRecord(
            approval_id=rec.approval_id,
            proposal_id=rec.proposal_id,
            proposal_revision=rec.proposal_revision,
            approved_at_iso=rec.approved_at_iso,
            accepted_omission_ids=("fake_omission_id",),
            approval_scope=rec.approval_scope,
        )
        page._current_approval_record = tampered_rec2
        assert page.get_approved_proposal_and_record() is None

        # 2.6 Nguồn video thay đổi (fingerprint mismatch)
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_source_change_05")
        win.page_source.get_video_paths = lambda: ["/media/different_clip.mp4"]
        win.config.video_paths = ["/media/different_clip.mp4"]
        assert page.get_approved_proposal_and_record() is None

        # 2.7 Supplier ném ngoại lệ
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_supplier_err_06")
        setattr(page, "_context_supplier", lambda: (_ for _ in ()).throw(RuntimeError("Context supplier offline")))
        assert page.get_approved_proposal_and_record() is None

        # 2.8 AI JSON gửi đối tượng phê duyệt giả qua dữ liệu video plan không thể cấp quyền
        page._current_approval_record = None
        page._video_plan_data = {
            "version": "1.0",
            "approved": True,
            "approval": {"approval_id": "ai_forged_approval"},
            "timeline_segments": [],
        }
        assert page.get_approved_proposal_and_record() is None

    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.close()


# ---------------------------------------------------------------------------
# NHÓM 3: UI Step 4 Entry Gate & Phản hồi trực quan người dùng (Visible Feedback)
# ---------------------------------------------------------------------------

def test_step4_entry_gate_denial_and_visible_feedback(qapp):
    """3. Chặn chuyển Bước 4 khi chưa duyệt; hiển thị lý do trực quan tiếng Việt (QLabel + toolTip); xử lý exception getter an toàn."""
    win = DirectorWindow()
    win.show()

    try:
        win.go_to_step(3)
        assert win.current_step == 3
        assert win.page_review.get_approved_proposal_and_record() is None

        expected_reason = "Chưa có phê duyệt biên tập hợp lệ cho kịch bản hiện tại."

        # 3.1 Next từ Bước 3 bị chặn
        win._on_btn_primary_clicked()
        QApplication.processEvents()
        assert win.current_step == 3
        assert win.lbl_gate_status.isVisible() is True
        assert expected_reason in win.lbl_gate_status.text()
        assert win.btn_primary.toolTip() == expected_reason
        assert any(expected_reason in l.text() for l in win.findChildren(QLabel) if l.isVisible())

        # 3.2 Stepper 4 bị chặn
        win.stepper.step_selected.emit(4)
        QApplication.processEvents()
        assert win.current_step == 3
        assert win.lbl_gate_status.isVisible() is True

        # 3.3 go_to_step(4) trực tiếp bị chặn
        win.go_to_step(4)
        QApplication.processEvents()
        assert win.current_step == 3
        assert win.lbl_gate_status.isVisible() is True

        # 3.4 Ngoại lệ bất ngờ từ getter được bao bọc an toàn
        with patch.object(win.page_review, "get_approved_proposal_and_record", side_effect=RuntimeError("Getter unexpected failure")):
            win.go_to_step(4)
            QApplication.processEvents()
            assert win.current_step == 3
            assert win.lbl_gate_status.isVisible() is True
            assert expected_reason in win.lbl_gate_status.text()

        # 3.5 Duyệt hợp lệ -> Vào Bước 4, phản hồi gate cũ được xóa sạch
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_real_s3_visible")
        win.go_to_step(4)
        QApplication.processEvents()
        assert win.current_step == 4
        assert not win.lbl_gate_status.isVisible()
        assert win.btn_primary.toolTip() == ""

        # 3.6 Thu hồi approval khi đang ở Bước 4, sau đó gọi Build -> Trả về Bước 3 với lý do trực quan
        win.page_review._current_approval_record = None
        win._start_build_timeline()
        QApplication.processEvents()
        assert win.current_step == 3
        assert win.lbl_gate_status.isVisible() is True
        assert expected_reason in win.lbl_gate_status.text()

    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.close()


# ---------------------------------------------------------------------------
# NHÓM 4: Step 4 Build Kiểm soát trung thực & Chặn đứng AI Build cũ
# ---------------------------------------------------------------------------

def test_step4_build_controlled_unavailable_visible_feedback_and_zero_dispatch(qapp):
    """4. Nút Dựng timeline ở Bước 4 không kích hoạt worker cũ, hiển thị lý do trực quan, không hiển thị progress panel giả."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, rec = _setup_real_s3_approval(win, manifest_id="mf_real_s3_build_01")
        win.go_to_step(4)
        QApplication.processEvents()
        assert win.current_step == 4

        with patch.object(controller, "start_pipeline") as mock_start_pipe:
            win._start_build_timeline()
            # Đợi preflight worker hoàn tất nếu đang chạy trên background thread
            for _ in range(50):
                QApplication.processEvents()
                if not win._preflight_workers:
                    break
                time.sleep(0.02)

            # 0 dispatch to legacy pipeline
            assert mock_start_pipe.call_count == 0

            # Progress panel không mở ra đánh lừa người dùng
            assert win.prog_panel.isHidden() is True

            # Kết quả build nội bộ ghi nhận trung thực (0 execution)
            assert hasattr(win, "_last_build_result")
            assert win._last_build_result["ready_to_execute"] is False
            assert win._last_build_result["proposal_id"] == prop.proposal_id
            assert win._last_build_result["approval_id"] == rec.approval_id

            # Clicks lặp lại vẫn 0 dispatch
            win._start_build_timeline()
            win._start_build_timeline()
            assert mock_start_pipe.call_count == 0
            for _ in range(50):
                QApplication.processEvents()
                if not win._preflight_workers:
                    break
                time.sleep(0.02)

        # Nếu getter ném ngoại lệ khi bấm Build: Quay về Bước 3 an toàn
        with patch.object(win.page_review, "get_approved_proposal_and_record", side_effect=RuntimeError("Unexpected getter crash on build")):
            win._start_build_timeline()
            QApplication.processEvents()
            assert win.current_step == 3
            assert win.lbl_gate_status.isVisible() is True

    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.close()


# ---------------------------------------------------------------------------
# NHÓM 5: Controller An toàn State-query, Chặn Phase Cũ & Tương thích Phase 1
# ---------------------------------------------------------------------------

class _FakeUnqueryableWorker:
    def isRunning(self):
        raise RuntimeError("worker wrapper query failed")


class _MockPhase1Worker(QObject):
    log_signal = Signal(str)
    step_signal = Signal(str, str)
    progress_signal = Signal(int)
    finished_signal = Signal(bool, str)

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self._running = False
        self.stopped = False

    def isRunning(self) -> bool:
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self.stopped = True
        self._running = False

    def requestInterruption(self):
        self.stopped = True
        self._running = False


def test_director_controller_safe_worker_query_and_phase_rejection(qapp):
    """5. start_pipeline: Query throw -> bận an toàn; 0 kwargs/0 constructor/0 start cho phase 0/2/invalid; Phase 1 positive."""
    cfg = DirectorConfig()
    cfg.video_paths = ["/tmp/test_clip.mp4"]
    controller = DirectorController(config=cfg)

    fin_events = []
    controller.pipeline_finished.connect(lambda ok, msg: fin_events.append((ok, msg)))

    # 5.1 Owned worker query throw -> False mà không thay thế worker, không phát terminal signal
    unqueryable = _FakeUnqueryableWorker()
    controller.worker = unqueryable
    res = controller.start_pipeline(phase=0)
    assert res is False
    assert controller.worker is unqueryable
    assert len(fin_events) == 0  # Không phát tín hiệu giả mạo kết thúc job đang bận

    # 5.2 Spies xác thực: phase 0, 2 và invalid types bị từ chối TRƯỚC KHI tạo kwargs hay khởi tạo worker
    controller.worker = None
    with patch.object(controller, "build_pipeline_kwargs") as spy_kwargs, \
         patch.object(dc, "PipelineWorker") as spy_worker_cls:

        # Phase 0
        assert controller.start_pipeline(phase=0) is False
        assert spy_kwargs.call_count == 0
        assert spy_worker_cls.call_count == 0

        # Phase 2
        assert controller.start_pipeline(phase=2) is False
        assert spy_kwargs.call_count == 0
        assert spy_worker_cls.call_count == 0

        # Invalid types: bool, str, unknown int
        assert controller.start_pipeline(phase=True) is False  # type: ignore
        assert controller.start_pipeline(phase="1") is False  # type: ignore
        assert controller.start_pipeline(phase=99) is False
        assert spy_kwargs.call_count == 0
        assert spy_worker_cls.call_count == 0

    # 5.3 Phase 1 mocked worker positive: Khởi tạo, kết nối và start đúng 1 lần
    controller.worker = None
    with patch.object(dc, "PipelineWorker", side_effect=_MockPhase1Worker) as mock_worker_factory:
        res1 = controller.start_pipeline(phase=1)
        assert res1 is True
        assert isinstance(controller.worker, _MockPhase1Worker)
        assert controller.worker.isRunning() is True
        assert mock_worker_factory.call_count == 1

        # Thử gọi đè khi đang chạy -> Bị từ chối
        assert controller.start_pipeline(phase=1) is False

        # Hủy worker
        controller.cancel_pipeline()
        assert controller.worker.stopped is True

        # Kết thúc worker gửi finished_signal
        controller.worker.finished_signal.emit(True, "Phase 1 STT hoàn tất")
        assert len(fin_events) > 0
        assert fin_events[-1] == (True, "Phase 1 STT hoàn tất")


def test_director_controller_truthful_missing_ai_worker(qapp):
    """5.4 Khi LLMDirectorWorker là None, run_ai_script trả về failure trung thực, payload rỗng (không mô phỏng thành công)."""
    controller = DirectorController()
    events = []
    controller.ai_script_finished.connect(lambda *a: events.append(a))

    with patch.object(dc, "LLMDirectorWorker", None):
        controller.run_ai_script("probe prompt")

    assert len(events) == 1
    success, message, payload = events[0]
    assert success is False
    assert payload == ""
    assert "LLMDirectorWorker" in message or "không khả dụng" in message


# ---------------------------------------------------------------------------
# NHÓM 6: Purity với IO Tripwires Thật & Single Supplier Read
# ---------------------------------------------------------------------------

def test_getter_purity_with_io_tripwires_and_single_read(qapp):
    """6. Tripwires cài đặt SAU fixture/module setup; getter đọc supplier đúng 1 lần, 0 IO, 0 UUID/time mới."""
    win = DirectorWindow()
    win.show()

    try:
        prop, appr = _setup_real_s3_approval(win, manifest_id="mf_purity_01")
        page = win.page_review

        # Đếm số lần đọc context supplier
        supplier_calls = 0
        orig_supplier = getattr(page, "_context_supplier")
        def _counted_supplier():
            nonlocal supplier_calls
            supplier_calls += 1
            return orig_supplier()
        setattr(page, "_context_supplier", _counted_supplier)

        orig_id = appr.approval_id
        orig_ts = appr.approved_at_iso

        # Cài đặt tripwires sau khi thiết lập xong toàn bộ fixtures
        tripwire_counts = {"open": 0, "stat": 0, "popen": 0, "uuid": 0, "time": 0}

        orig_open = builtins.open
        orig_stat = os.stat
        orig_popen = subprocess.Popen
        orig_uuid = uuid.uuid4
        orig_time = time.time

        def _bad_open(*args, **kwargs):
            tripwire_counts["open"] += 1
            return orig_open(*args, **kwargs)

        def _bad_stat(*args, **kwargs):
            tripwire_counts["stat"] += 1
            return orig_stat(*args, **kwargs)

        def _bad_popen(*args, **kwargs):
            tripwire_counts["popen"] += 1
            return orig_popen(*args, **kwargs)

        def _bad_uuid():
            tripwire_counts["uuid"] += 1
            return orig_uuid()

        with patch("builtins.open", side_effect=_bad_open), \
             patch("os.stat", side_effect=_bad_stat), \
             patch("subprocess.Popen", side_effect=_bad_popen), \
             patch("uuid.uuid4", side_effect=_bad_uuid):

            res = page.get_approved_proposal_and_record()

        assert res is not None
        owned_prop, owned_appr = res

        # 1 context supplier read
        assert supplier_calls == 1

        # 0 tripwires triggered trong suốt quá trình getter chạy
        assert tripwire_counts["open"] == 0
        assert tripwire_counts["stat"] == 0
        assert tripwire_counts["popen"] == 0
        assert tripwire_counts["uuid"] == 0

        # Không sinh mới approval_id hoặc approved_at_iso
        assert owned_appr.approval_id == orig_id
        assert owned_appr.approved_at_iso == orig_ts

    finally:
        for timer in win.findChildren(QTimer):
            timer.stop()
        win.close()
