"""Unit & UI tests cho Director Step 4 Readiness Wiring và Readonly Feedback — G07-07A2b-I.

Kiểm thử toàn diện 8 nhóm (U01–U08) trong Acceptance Matrix:
- U01: Enter Step 4 with real approved pair -> 0 workers auto-start; explicit click -> 1 backend worker metadata check,
       result counts đúng/ready False/0 pipeline 0/2/0 Resolve. No approval/getter throw -> 0 worker/no GUI IO.
- U02: Repeated click/direct calls không tạo worker 2 khi active; invalid request/constructor failure trước generation mutation
       không hủy/mất current state.
- U03: Source-only no root config -> pass preliminary checks; offline/missing assignment -> bảng lỗi, không auto-omit, no execution.
       Rendering/plain text 0 IO, <=200 rows minh bạch khi >200.
- U04: Logical terminal chưa physical finish -> primary/back/stepper/start vẫn khóa, Cancel còn dùng;
       unrelated source idle/pipeline finish không unlock/đè nhãn.
- U05: Late/duplicate terminal của A, hoặc A.finished tới lúc B active không clear B/enable controls/delete B.
       Registry pop theo exact worker.
- U06: Current proposal/FPS/style/source hoặc reapproval thay đổi trong job -> terminal không publish success/stale feedback,
       bảo toàn user drafts; current getter error handled.
- U07: Cancel cooperative -> cancelled/0 success, registry giữ tới native finished, restore controls exactly 1 lần đúng step hiện tại.
- U08: Constructor/start failure: never-started rollback sạch; .start raises sau khi worker đã chạy -> retain+cancel,
       close chờ finish, 0 thread leak. State query error coi busy.
"""

import threading
import time
import uuid
from dataclasses import replace
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
)
from src.core.source_catalog_readiness import (
    AssetAssignmentObservation,
    ReadinessRequest,
    SourceCatalogReadinessResult,
    SourceClipObservation,
    inspect_source_and_catalog_readiness,
)
from src.ui.director.director_controller import DirectorController
from src.ui.director.director_window import DirectorWindow
from src.ui.director.workers.preflight_worker import PreflightWorker


def _setup_positive_real_approval(
    win: DirectorWindow, tmp_path, clip_count: int = 2
) -> Tuple[EditPlanProposal, AppOwnedApprovalRecord]:
    """Tạo proposal S3 thực chất và approval app-owned trên các tệp tạm có thực."""
    clips = []
    video_paths = []
    for i in range(clip_count):
        p = tmp_path / f"clip_{i:02d}_{uuid.uuid4().hex[:6]}.mp4"
        p.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"x" * 100)
        video_paths.append(str(p))
        clips.append(
            ClipEntry.from_dict({
                "clip_id": f"C{i+1:02d}",
                "file_name": p.name,
                "current_path": str(p),
                "duration_sec": 10.0,
                "fps": 30.0,
            })
        )
    manifest = ClipManifest(manifest_id=f"mf_{uuid.uuid4().hex[:8]}", project_name="ChunDVC_Project", clips=clips)
    win.page_source.get_video_paths = lambda: list(video_paths)
    win.config.video_paths = list(video_paths)
    win.page_review._manifest = manifest
    win.page_review._video_plan_data = {
        "manifest_id": manifest.manifest_id,
        "timeline_segments": [
            {
                "clip_id": c.clip_id,
                "start_sec": 0.0,
                "end_sec": 5.0,
                "action": "keep",
            }
            for c in clips
        ],
        "broll_inserts": [],
    }
    win.page_review._kinetic_plan_data = None
    win.page_review._last_good_matching_sidecar = None
    win.page_review._current_user_candidate_choices = {}
    win.page_review._current_approval_record = None
    win.page_review._source_frame_evidence_table = None

    win.page_review._rebuild_and_present_proposal()
    prop = win.page_review._current_proposal
    assert prop is not None
    rev = compute_proposal_revision(prop)
    win.page_review._on_proposal_approved(prop.proposal_id, rev)
    appr = win.page_review._current_approval_record
    assert appr is not None
    return prop, appr


def _drain_preflight_workers(win: DirectorWindow, max_wait_sec: float = 3.0) -> None:
    """Chờ tất cả preflight worker trên nền kết thúc vật lý an toàn."""
    start = time.time()
    while bool(win._preflight_workers) and (time.time() - start < max_wait_sec):
        QApplication.processEvents()
        time.sleep(0.01)


# ============================================================================
# U01: Chuyển bước không auto-start; Explicit click chạy 1 worker thật
# ============================================================================

def test_u01_enter_step4_zero_auto_scan_and_explicit_click_starts_real_worker(qapp, tmp_path):
    """U01: Vào Bước 4 không tự scan; click nút chính chạy đúng 1 PreflightWorker thật; không có duyệt -> 0 worker."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=2)

        # 1. Chuyển sang Bước 4: TUYỆT ĐỐI không tự scan
        win.go_to_step(4)
        QApplication.processEvents()

        assert win.current_step == 4
        assert len(win._preflight_workers) == 0, "Không được tự khởi động worker khi vào Bước 4"
        assert win.btn_primary.text() == "Kiểm tra nguồn & tài nguyên"
        assert win.page_build.table_readiness.rowCount() == 0

        # 2. Click nút chính: Khởi động 1 PreflightWorker thật
        with patch.object(controller, "start_pipeline") as mock_pipe:
            win.btn_primary.click()
            QApplication.processEvents()

            # Chờ worker hoàn tất trên background thread
            _drain_preflight_workers(win)

            # Kiểm tra kết quả
            assert win._last_readiness_result is not None
            res = win._last_readiness_result
            assert res.status == "completed"
            assert res.checks_passed is True
            assert res.ready_to_execute is False
            assert len(res.source_observations) == 2
            assert win.page_build.table_readiness.rowCount() == 2
            assert mock_pipe.call_count == 0, "Tuyệt đối không gọi pipeline thi công Resolve"

        # 3. Phủ định: Thu hồi approval -> gọi kiểm tra -> 0 worker, chuyển về Bước 3
        win.page_review._current_approval_record = None
        win._start_readiness_preflight()
        QApplication.processEvents()

        assert len(win._preflight_workers) == 0, "Không được khởi tạo worker khi không có approval"
        assert win.current_step == 3
        assert win.lbl_gate_status.isVisible() is True
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u02_repeated_clicks_no_second_worker_and_constructor_failure_rollback(qapp, tmp_path):
    """U02: Clicks lặp lại không tạo worker thứ 2; Lỗi khởi tạo request/worker không làm tăng generation hay mất state."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=2)
        win.go_to_step(4)
        QApplication.processEvents()

        hold_gate = threading.Event()
        orig_inspect = inspect_source_and_catalog_readiness

        def _gated_inspect(*args, **kwargs):
            hold_gate.wait(timeout=3.0)
            return orig_inspect(*args, **kwargs)

        with patch("src.ui.director.workers.preflight_worker.inspect_source_and_catalog_readiness", side_effect=_gated_inspect):
            # Bấm lần 1: Worker 1 khởi chạy và đang bị giữ trong background thread
            win.btn_primary.click()
            QApplication.processEvents()
            assert len(win._preflight_workers) == 1, "Phải có đúng 1 worker đang active"
            gen_before = win._preflight_generation

            # Bấm lần 2 và direct call khi worker đang chạy
            win.btn_primary.click()
            win._start_readiness_preflight()
            QApplication.processEvents()

            assert len(win._preflight_workers) == 1, "Clicks lặp lại không được tạo worker thứ 2"
            assert win._preflight_generation == gen_before

            # Mở cổng cho worker 1 hoàn tất
            hold_gate.set()
            _drain_preflight_workers(win)

        assert len(win._preflight_workers) == 0

        # Kiểm tra constructor failure trước khi mutate generation
        curr_gen = win._preflight_generation
        with patch("src.ui.director.director_window.ReadinessRequest", side_effect=ValueError("Mock invalid request")):
            win._start_readiness_preflight()
            QApplication.processEvents()

            assert win._preflight_generation == curr_gen, "Generation không được tăng khi constructor ném lỗi"
            assert len(win._preflight_workers) == 0
            assert win.btn_primary.isEnabled() is True
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u03_source_only_passes_and_catalog_offline_shows_table_and_max_200_limit(qapp, tmp_path):
    """U03: Source-only hợp lệ đạt kiểm tra sơ bộ; Tài nguyên lỗi hiển thị bảng; Giới hạn tối đa 200 dòng."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # 1. Chạy source-only thành công
        win._start_readiness_preflight()
        _drain_preflight_workers(win)
        assert win._last_readiness_result.checks_passed is True

        # 2. Kiểm tra hiển thị giới hạn 200 dòng văn bản thuần
        obs_many = []
        for i in range(250):
            obs_many.append(
                SourceClipObservation(
                    clip_id=f"C{i:03d}",
                    path=f"/path/clip_{i}.mp4",
                    duration_sec=1.0,
                    exists=True,
                    is_regular_file=True,
                    is_non_empty=True,
                    is_readable=True,
                    recheck_passed=True,
                )
            )

        fake_result = SourceCatalogReadinessResult(
            request_id="req_test_max200",
            generation=1,
            proposal_id=prop.proposal_id,
            proposal_revision=compute_proposal_revision(prop),
            approval_id=appr.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=tuple(obs_many),
            catalog_observations=(),
            diagnostics=(),
        )

        win.page_build.show_readiness_result(fake_result)
        QApplication.processEvents()

        assert win.page_build.table_readiness.rowCount() == 200, "Bảng chỉ hiển thị tối đa 200 dòng"
        assert win.page_build.lbl_readiness_count.isVisible() is True
        assert "Hiển thị 200/tổng số 250 dòng" in win.page_build.lbl_readiness_count.text()

        # 3. Tài nguyên bị từ chối / missing: hiển thị đúng hàng lỗi, không auto-omit
        cat_obs = (
            AssetAssignmentObservation(
                demand_id="d1",
                slot="bgm",
                asset_id="ast_offline_01",
                source_kind="music",
                found_in_catalog=False,
                duplicate_in_catalog=False,
                provenance_matched=False,
                guard_allowed=False,
                guard_status="missing",
                error_code="ERR_CATALOG_OFFLINE",
                error_message="Kho lưu trữ đang ngoại tuyến",
            ),
        )
        fake_cat_result = SourceCatalogReadinessResult(
            request_id="req_test_offline",
            generation=1,
            proposal_id=prop.proposal_id,
            proposal_revision=compute_proposal_revision(prop),
            approval_id=appr.approval_id,
            status="failed",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=cat_obs,
            diagnostics=("Tài nguyên ngoại tuyến",),
        )

        win.page_build.show_readiness_result(fake_cat_result)
        QApplication.processEvents()

        assert win.page_build.table_readiness.rowCount() == 1
        item_status = win.page_build.table_readiness.item(0, 2)
        assert item_status is not None
        assert "Từ chối" in item_status.text()
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u04_logical_terminal_keeps_controls_locked_until_physical_finished(qapp, tmp_path):
    """U04: Logical terminal chưa physical finish -> controls vẫn khóa; Source idle không unlock/đè nhãn preflight."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        req = ReadinessRequest(
            request_id="req_u04_mock",
            generation=1,
            proposal=prop,
            approval=appr,
        )

        # Worker mock không tự chạy native thread để kiểm soát chính xác 2 pha
        worker = PreflightWorker(request=req, parent=win)
        win._preflight_workers[req.request_id] = worker
        win._preflight_generation = 1
        win.btn_primary.setEnabled(False)
        win.btn_primary.setText("⏳ Đang kiểm tra nguồn...")
        win.btn_back.setEnabled(False)
        win.stepper.setEnabled(False)
        win.page_build.set_readiness_busy(True)

        # 1. Phát tín hiệu logical terminal
        fake_result = SourceCatalogReadinessResult(
            request_id=req.request_id,
            generation=1,
            proposal_id=prop.proposal_id,
            proposal_revision=compute_proposal_revision(prop),
            approval_id=appr.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )

        win._on_readiness_terminal(worker, req, fake_result)
        QApplication.processEvents()

        # Đã cập nhật view readonly nhưng controls VẪN KHÓA
        assert win.btn_primary.isEnabled() is False
        assert win.btn_back.isEnabled() is False
        assert win.stepper.isEnabled() is False
        assert win.page_build.btn_cancel_readiness.isVisible() is True

        # 2. Source-analysis hoặc pipeline kết thúc không liên quan không được mở controls hoặc đè nhãn
        win._on_source_analysis_busy_changed(False)
        assert win.btn_primary.isEnabled() is False
        assert "Đang kiểm tra" in win.btn_primary.text()

        with patch("PySide6.QtWidgets.QMessageBox.information"):
            win._on_pipeline_finished(True, "Success")
        assert win.btn_primary.isEnabled() is False

        # 3. Chỉ khi native finished được gọi, controls mới được phục hồi
        win._on_readiness_worker_finished(worker, req.request_id)
        QApplication.processEvents()

        assert win.btn_primary.isEnabled() is True
        assert win.btn_primary.text() == "Kiểm tra nguồn & tài nguyên"
        assert win.btn_back.isEnabled() is True
        assert win.stepper.isEnabled() is True
        assert win.page_build.btn_cancel_readiness.isVisible() is False
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u05_late_terminal_and_worker_finished_isolation(qapp, tmp_path):
    """U05: Terminal trễ của worker A hoặc A.finished không làm thay đổi hay xóa worker B đang active."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        req_a = ReadinessRequest(request_id="req_A", generation=1, proposal=prop, approval=appr)
        worker_a = PreflightWorker(request=req_a, parent=win)

        req_b = ReadinessRequest(request_id="req_B", generation=2, proposal=prop, approval=appr)
        worker_b = PreflightWorker(request=req_b, parent=win)

        # B đang active trong registry
        win._preflight_workers[req_b.request_id] = worker_b
        win._preflight_generation = 2
        win.btn_primary.setEnabled(False)

        # Terminal của A (generation 1) tới trễ
        fake_result_a = SourceCatalogReadinessResult(
            request_id="req_A",
            generation=1,
            proposal_id=prop.proposal_id,
            proposal_revision=compute_proposal_revision(prop),
            approval_id=appr.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=(),
        )
        win._on_readiness_terminal(worker_a, req_a, fake_result_a)
        QApplication.processEvents()

        # B vẫn an toàn trong registry, controls vẫn khóa
        assert win._preflight_workers.get("req_B") is worker_b
        assert win.btn_primary.isEnabled() is False

        # Finished của A tới trễ
        win._on_readiness_worker_finished(worker_a, "req_A")
        QApplication.processEvents()

        # B vẫn an toàn, controls vẫn khóa vì B còn sống
        assert win._preflight_workers.get("req_B") is worker_b
        assert win.btn_primary.isEnabled() is False

        # Finished của B tới
        win._on_readiness_worker_finished(worker_b, "req_B")
        QApplication.processEvents()

        assert len(win._preflight_workers) == 0
        assert win.btn_primary.isEnabled() is True
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u06_approval_change_during_job_detects_stale_cleanly(qapp, tmp_path):
    """U06: Proposal / Approval bị sửa hoặc thu hồi trong khi worker đang chạy -> Báo Stale, bảo toàn dữ liệu."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=2)
        win.go_to_step(4)
        QApplication.processEvents()

        hold_gate = threading.Event()
        orig_inspect = inspect_source_and_catalog_readiness

        def _gated_inspect(*args, **kwargs):
            hold_gate.wait(timeout=3.0)
            return orig_inspect(*args, **kwargs)

        with patch("src.ui.director.workers.preflight_worker.inspect_source_and_catalog_readiness", side_effect=_gated_inspect):
            # Khởi động worker thật
            win._start_readiness_preflight()
            QApplication.processEvents()
            assert len(win._preflight_workers) == 1

            # Thay đổi approval giữa chừng (thu hồi approval) khi worker đang chạy
            win.page_review._current_approval_record = None

            # Cho worker hoàn tất
            hold_gate.set()
            _drain_preflight_workers(win)

        # Terminal kiểm tra getter hiện hành và phát hiện mismatch -> STALE
        assert win._last_build_result is not None
        assert win._last_build_result.get("status") == "STALE"
        assert "hết hiệu lực" in win.page_build.lbl_readiness_status.text()
        assert win.page_build.table_readiness.rowCount() == 0, "Không được publish bảng kết quả thành công khi stale"
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u07_cancel_cooperative_restores_controls_on_native_finished(qapp, tmp_path):
    """U07: Cancel cooperative từ giao diện -> kết quả cancelled, registry giữ worker tới native finished."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=2)
        win.go_to_step(4)
        QApplication.processEvents()

        hold_gate = threading.Event()
        orig_inspect = inspect_source_and_catalog_readiness

        def _gated_inspect(*args, **kwargs):
            hold_gate.wait(timeout=3.0)
            return orig_inspect(*args, **kwargs)

        with patch("src.ui.director.workers.preflight_worker.inspect_source_and_catalog_readiness", side_effect=_gated_inspect):
            win._start_readiness_preflight()
            QApplication.processEvents()
            assert len(win._preflight_workers) == 1

            # Nhấn nút Hủy trên BuildPage khi worker đang chạy
            win.page_build.btn_cancel_readiness.click()
            QApplication.processEvents()

            # Thả cổng để worker thoát qua cancel checkpoint
            hold_gate.set()
            _drain_preflight_workers(win)

        assert win._last_readiness_result is not None
        assert win._last_readiness_result.status == "cancelled"
        assert win._last_readiness_result.checks_passed is False
        assert win.btn_primary.isEnabled() is True
        assert win.btn_primary.text() == "Kiểm tra nguồn & tài nguyên"
        assert win.stepper.isEnabled() is True
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_u08_start_failure_rollback_and_running_failure_retains_worker(qapp, tmp_path):
    """U08: worker.start() ném ngoại lệ khi chưa chạy -> rollback sạch; ném ngoại lệ khi đã chạy -> retain & cancel."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Case A: start() ném ngoại lệ khi worker chưa kịp chạy
        with patch.object(PreflightWorker, "start", side_effect=RuntimeError("Cannot start thread")):
            win._start_readiness_preflight()
            QApplication.processEvents()

            assert len(win._preflight_workers) == 0, "Phải rollback sạch worker khỏi registry"
            assert win.btn_primary.isEnabled() is True
            assert win.page_build.btn_cancel_readiness.isVisible() is False

        # Case B: start() ném ngoại lệ nhưng worker đã chạy (hoặc isRunning query lỗi) -> retain & cancel
        with patch.object(PreflightWorker, "start", side_effect=RuntimeError("Raised after starting")), \
             patch.object(PreflightWorker, "isRunning", return_value=True), \
             patch.object(PreflightWorker, "cancel") as mock_cancel:
            win._start_readiness_preflight()
            QApplication.processEvents()

            assert len(win._preflight_workers) == 1, "Phải giữ lại worker trong registry để chờ kết thúc vật lý"
            assert mock_cancel.call_count == 1, "Phải gọi cancel cooperative"

            # Thu hồi thủ công để dọn dẹp fixture
            worker = list(win._preflight_workers.values())[0]
            win._on_readiness_worker_finished(worker, list(win._preflight_workers.keys())[0])
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r01_outcome_5_fields_mismatch_denies_publish_and_valid_control_publishes(qapp, tmp_path):
    """R01: Wrong từng field request_id/generation/proposal_id/revision/approval_id trong outcome -> 0 publish;
    valid control actual coordinator start vẫn publish."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Giữ worker không tự chạy run()
        with patch.object(PreflightWorker, "start"):
            win._start_readiness_preflight()
            QApplication.processEvents()

            assert len(win._preflight_workers) == 1
            worker = list(win._preflight_workers.values())[0]
            req = worker._request

            good = inspect_source_and_catalog_readiness(req)
            assert good.checks_passed is True

            # Case A: outcome_req_id sai lệch -> 0 publish
            bad_req = replace(good, request_id="OTHER_REQUEST_ID")
            win._on_readiness_terminal(worker, req, bad_req)
            assert win._last_readiness_result is None
            assert win.page_build.table_readiness.rowCount() == 0

            # Case B: outcome_gen sai lệch -> 0 publish
            bad_gen = replace(good, generation=999)
            win._on_readiness_terminal(worker, req, bad_gen)
            assert win._last_readiness_result is None
            assert win.page_build.table_readiness.rowCount() == 0

            # Case C: outcome_prop_id sai lệch -> 0 publish
            bad_prop = replace(good, proposal_id="OTHER_PROPOSAL_ID")
            win._on_readiness_terminal(worker, req, bad_prop)
            assert win._last_readiness_result is None
            assert win.page_build.table_readiness.rowCount() == 0

            # Case D: outcome_rev sai lệch -> 0 publish
            bad_rev = replace(good, proposal_revision="f" * 64)
            win._on_readiness_terminal(worker, req, bad_rev)
            assert win._last_readiness_result is None
            assert win.page_build.table_readiness.rowCount() == 0

            # Case E: outcome_appr_id sai lệch -> 0 publish
            bad_appr = replace(good, approval_id="OTHER_APPROVAL_ID")
            win._on_readiness_terminal(worker, req, bad_appr)
            assert win._last_readiness_result is None
            assert win.page_build.table_readiness.rowCount() == 0

            # Valid control: Khớp đủ cả 5 fields -> Publish đúng 1 lần
            win._on_readiness_terminal(worker, req, good)
            assert win._last_readiness_result is not None
            assert win._last_readiness_result.checks_passed is True
            assert win.page_build.table_readiness.rowCount() > 0

            # Pending authority check: gọi từ worker lạ hoặc không có pending record -> 0 publish
            stranger = PreflightWorker(req, parent=win)
            win._last_readiness_result = None
            win._on_readiness_terminal(stranger, req, good)
            assert win._last_readiness_result is None
            stranger.deleteLater()

            # Dọn dẹp worker
            win._on_readiness_worker_finished(worker, req.request_id)
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r02_duplicate_terminal_keeps_first_result_and_late_callback_isolation(qapp, tmp_path):
    """R02: Duplicate terminal completed->failed trong cùng worker giữ kết quả đầu; late callbacks A không đè B hoặc unlock physical registry."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        with patch.object(PreflightWorker, "start"):
            win._start_readiness_preflight()
            worker_a = list(win._preflight_workers.values())[0]
            req_a = worker_a._request
            good_a = inspect_source_and_catalog_readiness(req_a)

            # Terminal 1: completed
            win._on_readiness_terminal(worker_a, req_a, good_a)
            first_res = win._last_readiness_result
            assert first_res is not None
            assert first_res.status == "completed"

            # Terminal 2 (duplicate): failed -> Phải giữ kết quả đầu, không đè
            win._on_readiness_terminal(worker_a, req_a, replace(good_a, status="failed", checks_passed=False))
            assert win._last_readiness_result.status == "completed"
            assert win._last_readiness_result.checks_passed is True

            # Thu hồi worker A
            win._on_readiness_worker_finished(worker_a, req_a.request_id)
            assert len(win._preflight_workers) == 0

            # Khởi động worker B
            win._start_readiness_preflight()
            worker_b = list(win._preflight_workers.values())[0]
            req_b = worker_b._request

            # Giả lập late progress từ worker A -> Không được đè UI của B
            win.btn_primary.setText("Worker B Đang chạy...")
            win._on_readiness_progress(worker_a, 50, "Late progress từ A")
            assert win.btn_primary.text() == "Worker B Đang chạy..."

            # Giả lập late worker_finished từ worker A -> Không được gỡ worker B khỏi registry hay unlock
            win._on_readiness_worker_finished(worker_a, req_a.request_id)
            assert len(win._preflight_workers) == 1
            assert win._pending_preflight_worker is worker_b

            # Dọn dẹp worker B
            win._on_readiness_worker_finished(worker_b, req_b.request_id)
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r03_preflight_worker_progress_to_ui_and_late_progress_ignored(qapp, tmp_path):
    """R03: Actual PreflightWorker progress 45% tới UI; terminal decision xong thì late progress bị ignore; không worker.request phantom access."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        with patch.object(PreflightWorker, "start"):
            win._start_readiness_preflight()
            worker = list(win._preflight_workers.values())[0]
            req = worker._request

            # Tripwire kiểm tra PreflightWorker KHÔNG có thuộc tính public 'request' (chỉ có _request)
            assert hasattr(worker, "_request")
            assert not hasattr(worker, "request")

            # Progress 45% từ actual worker -> Phải hiển thị lên UI
            win._on_readiness_progress(worker, 45, "Đang kiểm tra tài nguyên... (45%)")
            assert "45%" in win.btn_primary.text()

            # Terminal decision được đưa ra
            good = inspect_source_and_catalog_readiness(req)
            win._on_readiness_terminal(worker, req, good)
            assert win._preflight_terminal_consumed is True

            # Late progress sau khi terminal đã consumed -> Phải bị ignore hoàn toàn
            btn_text_after_terminal = win.btn_primary.text()
            status_text_after_terminal = win.page_build.lbl_readiness_status.text()

            win._on_readiness_progress(worker, 90, "Late progress 90%")
            assert win.btn_primary.text() == btn_text_after_terminal
            assert win.page_build.lbl_readiness_status.text() == status_text_after_terminal

            win._on_readiness_worker_finished(worker, req.request_id)
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r04_stale_approval_or_normalize_error_maintains_cancel_until_native_finish(qapp, tmp_path):
    """R04: Stale approval/normalize error khi native thread còn giữ -> Cancel visible/enabled,
    controls start vẫn khóa, last_result không stale-success. Native finish mới hideCancel/unlock."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        with patch.object(PreflightWorker, "start"):
            win._start_readiness_preflight()
            worker = list(win._preflight_workers.values())[0]
            req = worker._request
            good = inspect_source_and_catalog_readiness(req)

            # Mô phỏng approval bị invalidate giữa chừng
            win.page_review._current_approval_record = None

            # Terminal nhận kết quả -> Phát hiện stale
            win._on_readiness_terminal(worker, req, good)

            # Kết quả không được lưu là success
            assert win._last_readiness_result is None
            assert win._last_build_result.get("status") == "STALE"

            # Cancel button vẫn phải visible và enabled vì worker vẫn còn trong registry
            assert win.page_build.btn_cancel_readiness.isVisible() is True
            assert win.page_build.btn_cancel_readiness.isEnabled() is True
            assert win.btn_primary.isEnabled() is False, "Start controls phải tiếp tục bị khóa"

            # Native thread finish -> Giờ mới ẩn Cancel và khôi phục controls
            win._on_readiness_worker_finished(worker, req.request_id)
            assert win.page_build.btn_cancel_readiness.isVisible() is False
            assert win.btn_primary.isEnabled() is True

            # Thử nghiệm với normalize error:
            win.page_review._current_approval_record = appr
            win._start_readiness_preflight()
            worker2 = list(win._preflight_workers.values())[0]
            req2 = worker2._request

            with patch("src.ui.director.director_window.normalize_readiness_result", side_effect=ValueError("Bad schema")):
                win._on_readiness_terminal(worker2, req2, "invalid payload")

            assert win._last_readiness_result is None
            assert win.page_build.btn_cancel_readiness.isVisible() is True
            assert win.btn_primary.isEnabled() is False

            win._on_readiness_worker_finished(worker2, req2.request_id)
            assert win.page_build.btn_cancel_readiness.isVisible() is False
            assert win.btn_primary.isEnabled() is True
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r05_drop_event_denied_during_preflight_and_input_changes_trigger_cancel(qapp, tmp_path):
    """R05: Drop lúc preflight active -> 0 add_paths, không đổi source.
    Direct/programmatic input notification trong job -> stale/cancel; currentgetter kiểm tra UI state mới, bảo toàn draft."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        with patch.object(PreflightWorker, "start"):
            win._start_readiness_preflight()
            worker = list(win._preflight_workers.values())[0]
            req = worker._request

            # 1. Thử drop file khi preflight đang active
            class MockMime:
                def hasUrls(self):
                    return True
                def urls(self):
                    from PySide6.QtCore import QUrl
                    return [QUrl.fromLocalFile(str(tmp_path / "drop_denied.mp4"))]

            class MockDrop:
                def __init__(self):
                    self.ignored = False
                    self.accepted = False
                def mimeData(self):
                    return MockMime()
                def ignore(self):
                    self.ignored = True
                def acceptProposedAction(self):
                    self.accepted = True

            drop = MockDrop()
            with patch.object(win.page_source, "add_paths") as mock_add:
                win.dropEvent(drop)
                assert mock_add.call_count == 0, "Không được gọi add_paths khi preflight đang active"
                assert drop.ignored is True, "Phải gọi event.ignore()"

            # 2. Thay đổi cấu hình phong cách khi worker đang chạy -> Phải cooperative cancel và báo hủy
            with patch.object(worker, "cancel") as mock_cancel:
                win._on_style_changed("new_style_profile")
                assert mock_cancel.call_count == 1, "Phải gọi cancel cooperative khi cấu hình đổi"
                assert win._last_readiness_result is None
                assert "Cấu hình phong cách" in win.page_build.lbl_readiness_status.text()

            win._on_readiness_worker_finished(worker, req.request_id)
    finally:
        _drain_preflight_workers(win)
        win.close()



def test_r12_ui_plaintext_tripwires_row_cap_and_geometry_fit(qapp, tmp_path):
    """R12: UI render/plaintext 0 IO tripwires; diagnostic '<b>...</b>' hiển thị literal;
    valid/cancel/stale paths; bảng 200 row cap/count và 1200x750 status/Cancel không bị cắt."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.resize(1200, 750)
    win.show()
    QApplication.processEvents()

    try:
        # 1. Plain text format tripwire
        assert win.page_build.lbl_readiness_status.textFormat() == Qt.TextFormat.PlainText
        assert win.page_build.lbl_readiness_count.textFormat() == Qt.TextFormat.PlainText

        # Diagnostic '<b>...</b>' hiển thị literal plain text, không render HTML
        diagnostic_text = "Tệp cảnh báo: <b>/path/with/<special>&chars.mp4</b> không tìm thấy"
        win.page_build.lbl_readiness_status.setText(diagnostic_text)
        assert win.page_build.lbl_readiness_status.text() == diagnostic_text

        # 2. Bảng 200 row cap
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        req = ReadinessRequest("req_r12", 1, prop, appr)
        clips_250 = [
            SourceClipObservation(
                clip_id=f"C{i:03d}",
                path=f"/fake/path/{i}.mp4",
                duration_sec=10.0,
                exists=True,
                is_regular_file=True,
                is_non_empty=True,
                is_readable=True,
                stat_identity=(1, 2, 1000, 100, 100),
                recheck_passed=True,
                error_code="",
                error_message="",
            )
            for i in range(250)
        ]
        result_250 = SourceCatalogReadinessResult(
            request_id=req.request_id,
            generation=req.generation,
            proposal_id=prop.proposal_id,
            proposal_revision=appr.proposal_revision,
            approval_id=appr.approval_id,
            status="completed",
            checks_passed=True,
            ready_to_execute=False,
            source_observations=tuple(clips_250),
            catalog_observations=(),
            diagnostics=(),
        )

        # Tripwire 0 disk IO trong quá trình hiển thị bảng
        orig_open = open
        def _guarded_open(file, *args, **kwargs):
            if any(str(file).endswith(ext) for ext in [".mp4", ".mov", ".mkv", ".wav"]):
                raise RuntimeError(f"Zero IO tripwire: Attempted to open media file {file}")
            return orig_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=_guarded_open):
            win.page_build.show_readiness_result(result_250)

        assert win.page_build.table_readiness.rowCount() == 200, "Phải giới hạn hiển thị tối đa 200 dòng"
        assert "200" in win.page_build.lbl_readiness_count.text() and "250" in win.page_build.lbl_readiness_count.text()

        # 3. Geometry fit kiểm tra tại 1200x750
        win.go_to_step(4)
        QApplication.processEvents()
        assert win.width() == 1200
        assert win.height() == 750
        assert win.page_build.lbl_readiness_status.geometry().isValid()
        assert win.page_build.lbl_readiness_status.width() >= 100
        assert win.page_build.table_readiness.geometry().isValid()
    finally:
        _drain_preflight_workers(win)
        win.close()


def test_l2_reopen_during_active_worker_or_quit_drain_blocked(qapp, tmp_path):
    """L2: Close khi worker active giữ owner tới finished; show trong Quit drain
    không reset admission kể cả khi registry rỗng."""
    ctrl = DirectorController()
    win = DirectorWindow(controller=ctrl)
    win.show()
    QApplication.processEvents()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Trường hợp 1: Worker đang active khi close -> không reset khi show lại trước khi worker dừng
        req = ReadinessRequest(request_id="req_l2", generation=1, proposal=prop, approval=appr)
        worker = PreflightWorker(request=req, parent=win)
        win._preflight_workers[req.request_id] = worker
        win._preflight_generation = 1
        assert len(win._preflight_workers) == 1
        assert win.is_analysis_and_preflight_idle() is False

        # Đóng cửa sổ: close bị hoãn vì worker đang bận
        win.close()
        QApplication.processEvents()
        assert win._close_pending is True

        # Thử gọi show khi worker vẫn bận -> không cho phép resume admission
        win.show()
        QApplication.processEvents()
        assert win._close_pending is True
        assert win._shutdown_requested is True

        # Giải phóng worker và drain
        win._preflight_workers.clear()
        win._on_aggregate_workers_idle()
        QApplication.processEvents()
        assert win._is_closed is True

        # Trường hợp 2: Global application quit drain -> sticky cờ _app_quit_requested
        win.shutdown_analysis_and_preflight_workers(application_quit=True)
        assert win._app_quit_requested is True

        # Thử show lại sau khi đã yêu cầu app quit -> không được phép reset
        win.show()
        QApplication.processEvents()
        assert win._shutdown_requested is True
        assert win._is_closed is True

        # Navigation bị chặn hoàn toàn
        win.go_to_step(1)
        assert win.current_step == 4
        assert len(win._preflight_workers) == 0
    finally:
        _drain_preflight_workers(win)
        win.close()


def test_l3_child_lifecycle_resumption_after_normal_reopen(qapp, tmp_path):
    """L3: Child lifecycle sau normal reopen: Source inventory/analysis controls và
    Review request admission hoạt động trở lại ở scope offscreen/DI; stale drafts không bị đè."""
    ctrl = DirectorController()
    win = DirectorWindow(controller=ctrl)
    win.show()
    QApplication.processEvents()

    try:
        # Giả lập đóng window để kích hoạt shutdown các child page
        win.close()
        QApplication.processEvents()
        assert win._is_closed is True
        assert win.page_review._review_close_pending is True

        # Mở lại cửa sổ bình thường
        win.show()
        QApplication.processEvents()
        assert win._is_closed is False
        assert win.page_review._review_close_pending is False, "ReviewPage close pending phải được reset"
        assert win.page_source._shutdown_pending is False, "SourcePage shutdown pending phải được reset"

        # Các nút điều khiển trên ReviewPage phải được phục hồi
        assert win.page_review.btn_copy_video_prompt.isEnabled() is True
        assert win.page_review.btn_copy_kinetic_prompt.isEnabled() is True
        assert win.page_review.btn_prepare_batches.isEnabled() is True
        assert win.page_review.is_analysis_idle() is True

        # Các nút nạp footage trên SourcePage phải được phục hồi
        assert win.page_source.btn_folder.isEnabled() is True
        assert win.page_source.btn_files.isEnabled() is True
    finally:
        _drain_preflight_workers(win)
        win.close()

# ============================================================================
# L1–L3: Acceptance Tests cho Normal Reopen & Child Lifecycle Resumption
# ============================================================================

def test_l1_cached_director_normal_reopen_navigation_and_explicit_preflight(qapp, tmp_path):
    """L1: Close/reopen cùng instance qua cached Dock -> navigate 1/2/3/4 hoạt động,
    config/draft giữ nguyên, 0 auto-worker; explicit preflight chạy 1 job rồi drain."""
    from src.ui.lux.shell.edge_dock import EdgeDockWindow
    dock = EdgeDockWindow()
    dock.open_director()
    win = dock._director_window
    assert win is not None

    try:
        # Cấu hình phong cách và chuyển sang bước 2
        win.config.apply_style_preset("cinematic_epic")
        win.go_to_step(2)
        assert win.current_step == 2

        # Đóng DirectorWindow theo luồng bình thường
        win.close()
        QApplication.processEvents()
        assert win._is_closed is True
        assert win._shutdown_requested is True

        # Mở lại qua cached receiver của EdgeDockWindow
        dock.open_director()
        QApplication.processEvents()
        assert dock._director_window is win, "Phải sử dụng đúng cached instance"
        assert win.isVisible() is True
        assert win._is_closed is False, "_is_closed phải được phục hồi False"
        assert win._shutdown_requested is False, "_shutdown_requested phải được phục hồi False"
        assert len(win._preflight_workers) == 0, "Không được tự động kích hoạt worker khi reopen"

        # Bảo toàn cấu hình đã thiết lập trước đó
        assert win.config.style == "cinematic_epic"

        # Khả năng chuyển bước 1 và 2 hoạt động bình thường
        win.go_to_step(1)
        assert win.current_step == 1
        win.go_to_step(2)
        assert win.current_step == 2

        # Cấp phê duyệt thực chất và chuyển sang bước 4
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(3)
        assert win.current_step == 3
        win.go_to_step(4)
        assert win.current_step == 4

        start = time.time()
        while not win.is_analysis_and_preflight_idle() and time.time() - start < 3.0:
            QApplication.processEvents()
            time.sleep(0.01)

        # Kích hoạt preflight tường minh bằng nút bấm
        assert len(win._preflight_workers) == 0
        win.btn_primary.click()
        assert len(win._preflight_workers) == 1

        # Chờ worker hoàn tất và drain sạch
        _drain_preflight_workers(win)
        assert len(win._preflight_workers) == 0
        assert win.page_build.lbl_readiness_status.text() != ""
    finally:
        _drain_preflight_workers(win)
        win.close()
        dock.close()
