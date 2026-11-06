"""Unit & UI tests cho Safe Window Close & App Quit Retention — G07-07A2b-I.

Kiểm thử toàn diện 4 nhóm (U09–U12) trong Acceptance Matrix:
- U09: Window close lúc active và sau logical terminal -> ignore, cancel, closed chưa emit;
       native finished + all families idle -> close/closed 1 lần, 0 destruction while busy.
- U10: App Quit qua SourceShutdownGuard với Director hidden/preflight only -> 0 app.quit trước native finish;
       sau finish -> 1 quit. Chỉ logical terminal chưa cho quit.
- U11: Review prompt/matching worker hoặc Source worker còn sống khi preflight idle -> Quit vẫn đợi;
       existing standalone/nonDirector SourcePage+LibraryJob vẫn đợi. Repeated Quit 0 duplicate connections / 0 double quit.
- U12: Synchronous idle trong cancel/collection và simultaneous source/review/preflight jobs -> không premature quit;
       complete ordered release -> 1 quit.
"""

import threading
import time
import uuid
from typing import Any, Tuple
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer, QThread, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.core.clip_manifest import ClipEntry, ClipManifest
from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
)
from src.core.source_catalog_readiness import (
    ReadinessRequest,
    SourceCatalogReadinessResult,
)
from src.services.source_shutdown import SourceShutdownGuard
from src.ui.director.director_controller import DirectorController
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.source_page import SourcePage
from src.ui.director.workers.preflight_worker import PreflightWorker


def _setup_positive_real_approval(
    win: DirectorWindow, tmp_path, clip_count: int = 1
) -> Tuple[EditPlanProposal, AppOwnedApprovalRecord]:
    """Tạo proposal S3 thực chất và approval app-owned trên các tệp tạm có thực."""
    clips = []
    video_paths = []
    for i in range(clip_count):
        p = tmp_path / f"clip_shut_{i:02d}_{uuid.uuid4().hex[:6]}.mp4"
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
    manifest = ClipManifest(manifest_id=f"mf_shut_{uuid.uuid4().hex[:8]}", project_name="ChunDVC_Project", clips=clips)
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


def _drain_window_workers(win: DirectorWindow, max_wait_sec: float = 3.0) -> None:
    start = time.time()
    while not win.is_analysis_and_preflight_idle() and (time.time() - start < max_wait_sec):
        QApplication.processEvents()
        time.sleep(0.01)


# ============================================================================
# U09: Window close lúc active & sau logical terminal -> Ignore, deferred close 1 lần
# ============================================================================

def test_u09_window_close_during_preflight_and_after_terminal_defers_and_emits_once(qapp, tmp_path):
    """U09: win.close() khi preflight đang chạy hoặc sau logical terminal -> ignore, giữ window; chỉ close khi native finished."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    closed_emits = 0
    def _on_closed():
        nonlocal closed_emits
        closed_emits += 1
    win.closed.connect(_on_closed)

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Tạo request và worker giả định kiểm soát được timing
        req = ReadinessRequest(request_id="req_u09", generation=1, proposal=prop, approval=appr)
        worker = PreflightWorker(request=req, parent=win)
        win._preflight_workers[req.request_id] = worker
        win._preflight_generation = 1

        # 1. Gọi win.close() khi worker đang active trong registry
        assert win.is_analysis_and_preflight_idle() is False
        win.close()
        QApplication.processEvents()

        assert win.isVisible() is True, "Window không được đóng khi worker chưa dừng vật lý"
        assert win._close_pending is True
        assert win._is_closed is False
        assert closed_emits == 0

        # 2. Phát logical terminal: vẫn chưa được đóng vì chưa physical finished
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

        assert win._is_closed is False
        assert closed_emits == 0

        # 3. Phát native finished: bây giờ mới được đóng hoàn tất
        win._on_readiness_worker_finished(worker, req.request_id)
        # Chờ timer đóng thực tế
        for _ in range(20):
            QApplication.processEvents()
            time.sleep(0.01)

        assert win._is_closed is True
        assert closed_emits == 1, "Tín hiệu closed chỉ được phát đúng 1 lần"
    finally:
        _drain_window_workers(win)


# ============================================================================
# U10: App Quit với Director ẩn / preflight-only -> 0 quit trước finish, 1 quit sau finish
# ============================================================================

def test_u10_source_shutdown_guard_with_hidden_director_and_preflight_only(qapp, tmp_path):
    """U10: SourceShutdownGuard phát hiện DirectorWindow ẩn; 0 app.quit trước native finish; 1 quit sau khi finish."""
    win = DirectorWindow()
    win.hide()  # Cửa sổ ẩn

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        req = ReadinessRequest(request_id="req_u10", generation=1, proposal=prop, approval=appr)
        worker = PreflightWorker(request=req, parent=win)
        win._preflight_workers[req.request_id] = worker
        win._preflight_generation = 1

        guard = SourceShutdownGuard(qapp)

        with patch.object(qapp, "quit") as mock_quit:
            # Yêu cầu Quit ứng dụng
            guard.request_quit()
            QApplication.processEvents()

            assert guard._pending is True
            assert win in guard._director_windows, "Phải phát hiện và giữ strong ref DirectorWindow ẩn"
            assert mock_quit.call_count == 0, "Không được gọi app.quit khi preflight worker còn sống"

            # Phát logical terminal: vẫn chưa quit
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
            assert mock_quit.call_count == 0

            # Phát native finished: kích hoạt idle -> gọi app.quit đúng 1 lần
            win._on_readiness_worker_finished(worker, req.request_id)
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert mock_quit.call_count == 1, "app.quit phải được gọi đúng 1 lần sau physical finish"
    finally:
        _drain_window_workers(win)
        win.close()


# ============================================================================
# U11: Review worker hoặc Source worker còn sống -> Quit vẫn đợi; Idempotent Quit
# ============================================================================

def test_u11_review_or_source_busy_keeps_quit_waiting_and_idempotent_quit(qapp, tmp_path):
    """U11: Preflight rảnh nhưng Review hoặc Source worker còn sống -> Quit vẫn đợi; Repeated quit idempotent."""
    win = DirectorWindow()
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(3)
        QApplication.processEvents()

        guard = SourceShutdownGuard(qapp)

        # Giả lập Review worker đang chạy
        mock_rev_worker = MagicMock()
        win.page_review._prompt_workers_registry["p_test"] = mock_rev_worker

        with patch.object(qapp, "quit") as mock_quit:
            # Quit lần 1
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0, "Quit phải đợi Review worker"

            # Quit lần 2 (idempotent, không tạo thêm connection hay crash)
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0

            # Thu hồi Review worker
            win.page_review._prompt_workers_registry.pop("p_test", None)
            win.page_review.analysis_workers_idle.emit()
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert mock_quit.call_count == 1, "Chỉ gọi app.quit 1 lần khi tất cả đã rảnh"
    finally:
        _drain_window_workers(win)
        win.close()


# ============================================================================
# U12: Synchronous idle trong collection không làm premature quit
# ============================================================================

def test_u12_synchronous_idle_during_collection_does_not_premature_quit(qapp, tmp_path):
    """U12: Tín hiệu idle đồng bộ phát ra trong quá trình thu thập không làm thoát sớm khi còn worker khác."""
    win1 = DirectorWindow()
    win1.show()
    win2 = DirectorWindow()
    win2.show()

    try:
        prop1, appr1 = _setup_positive_real_approval(win1, tmp_path, clip_count=1)
        req1 = ReadinessRequest(request_id="req_u12_1", generation=1, proposal=prop1, approval=appr1)
        worker1 = PreflightWorker(request=req1, parent=win1)
        win1._preflight_workers[req1.request_id] = worker1

        prop2, appr2 = _setup_positive_real_approval(win2, tmp_path, clip_count=1)
        req2 = ReadinessRequest(request_id="req_u12_2", generation=1, proposal=prop2, approval=appr2)
        worker2 = PreflightWorker(request=req2, parent=win2)
        win2._preflight_workers[req2.request_id] = worker2

        guard = SourceShutdownGuard(qapp)

        with patch.object(qapp, "quit") as mock_quit:
            # Khi win1 bị shutdown, giả lập win1 dừng ngay lập tức và phát idle đồng bộ
            orig_shutdown = win1.shutdown_analysis_and_preflight_workers
            def _sync_idle_shutdown():
                win1._preflight_workers.clear()
                win1.analysis_and_preflight_idle.emit()
                return True
            win1.shutdown_analysis_and_preflight_workers = _sync_idle_shutdown

            guard.request_quit()
            QApplication.processEvents()

            # win2 vẫn còn bận -> mock_quit tuyệt đối không được gọi
            assert mock_quit.call_count == 0, "Không được thoát sớm khi win2 vẫn đang có worker"

            # Bây giờ win2 hoàn tất
            win2._preflight_workers.clear()
            win2.analysis_and_preflight_idle.emit()
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert mock_quit.call_count == 1
    finally:
        _drain_window_workers(win1)
        _drain_window_workers(win2)
        win1.close()
        win2.close()


# ============================================================================
# R06: Sau shutdown request -> 0 constructor / 0 start; queued close không _is_closed trước hạn
# ============================================================================

def test_r06_shutdown_requested_blocks_preflight_and_queued_close_dedup(qapp, tmp_path):
    """R06: Sau shutdown request dù chưa có worker/registry vừa empty vẫn 0 constructor/0 start;
    normal close queued chưa _is_closed/closed. Nhiều idle signals không 2 close/2 quit."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Gọi shutdown_analysis_and_preflight_workers()
        win.shutdown_analysis_and_preflight_workers()
        assert win._shutdown_requested is True

        # Khi shutdown đã requested, start_readiness_preflight phải bị chặn: 0 worker, 0 start
        with patch("src.ui.director.director_window.PreflightWorker") as mock_pw:
            win._start_readiness_preflight()
            assert mock_pw.call_count == 0, "Không được khởi tạo PreflightWorker sau khi đã shutdown requested"

        # Kiểm tra trạng thái queued close
        win._close_pending = True
        win._close_queued = False
        win._is_closed = False

        # Phát idle signal đầu tiên
        win._on_aggregate_workers_idle()
        assert win._is_closed is False, "_is_closed không được set trước khi closeEvent thực sự hoàn tất"
        assert win._close_queued is True

        # Phát idle signal thứ 2 (duplicate) -> không được trigger close thêm lần nữa
        with patch.object(win, "close") as mock_close:
            win._on_aggregate_workers_idle()
            assert mock_close.call_count == 0, "Không được gọi close lặp lại khi đã queued"
    finally:
        _drain_window_workers(win)
        win.close()


# ============================================================================
# R07: LibraryJob.isRunning ném ngoại lệ -> retain=True, 0 quit; native finish -> 1 quit
# ============================================================================

def test_r07_library_job_is_running_exception_retains_and_quits_on_finish(qapp):
    """R07: LibraryJob.isRunning throws tại discovery/check -> retain=True, 0 quit; khi query hồi phục và native finish -> 1 quit."""
    class FlakyLibraryJob(QObject):
        finished = Signal()

        def __init__(self):
            super().__init__()
            self._throw = True

        def isRunning(self):
            if self._throw:
                raise RuntimeError("Temporary status query failure")
            return False

        def requestInterruption(self):
            pass

    job = FlakyLibraryJob()
    guard = SourceShutdownGuard(qapp)

    try:
        with patch("src.ui.widgets.pack_ingest_wizard.LibraryJob.active_jobs", {job}), \
             patch.object(qapp, "quit") as mock_quit:

            guard.request_quit()
            QApplication.processEvents()

            assert job in guard._library_jobs, "Phải retain job trong _library_jobs khi isRunning() ném lỗi"
            assert mock_quit.call_count == 0, "Không được quit khi job trạng thái không xác định (fail-safe busy)"

            # Native finished signal phát ra
            job._throw = False
            job.finished.emit()
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert mock_quit.call_count == 1, "Phải gọi app.quit chính xác 1 lần sau khi job kết thúc vật lý"
    finally:
        if guard is not None:
            qapp.removeEventFilter(guard)
            guard.deleteLater()


# ============================================================================
# R08: Real QThread barrier hai pha: logical terminal đã phát nhưng run chưa return
# ============================================================================

def test_r08_real_qthread_barrier_two_phase_shutdown(qapp, tmp_path):
    """R08: Real QThread barrier hai pha: logical terminal đã phát nhưng run chưa return.
    Test window.close và hidden Director app Quit: 0 closed/quit tới physical finish, release -> exact 1 close/quit, 0 leak."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    guard = SourceShutdownGuard(qapp)

    barrier = threading.Event()

    class BarrierPreflightWorker(PreflightWorker):
        def __init__(self, request, barrier_ev, *, parent=None):
            super().__init__(request, parent=parent)
            self._barrier = barrier_ev

        def run(self):
            super().run()
            # Đợi barrier trước khi thoát hoàn toàn khỏi run()
            self._barrier.wait(timeout=5.0)

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        req = ReadinessRequest("req_r08_barrier", 1, prop, appr)
        with patch("src.ui.director.director_window.PreflightWorker", lambda request=None, parent=None, **kw: BarrierPreflightWorker(request, barrier, parent=parent)):
            win._start_readiness_preflight()

        assert len(win._preflight_workers) == 1
        worker = list(win._preflight_workers.values())[0]

        # Chờ terminal logical được tiêu thụ trong khi thread vật lý vẫn đang chạy ở barrier
        start_wait = time.time()
        while not getattr(win, "_preflight_terminal_consumed", False) and time.time() - start_wait < 3.0:
            QApplication.processEvents()
            time.sleep(0.01)

        assert win._preflight_terminal_consumed is True
        assert worker.isRunning() is True, "Thread vật lý phải còn đang chạy do bị barrier giữ"

        # Pha 1: Thử window.close() và app.quit() khi thread chưa return run()
        with patch.object(qapp, "quit") as mock_quit:
            # Thử close window
            win.close()
            QApplication.processEvents()

            assert win._close_pending is True
            assert win._is_closed is False, "Cửa sổ không được đóng khi thread vật lý đang chạy"

            # Thử app quit
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0, "Không được app.quit khi thread vật lý đang chạy"

            # Pha 2: Thả barrier cho thread kết thúc vật lý
            barrier.set()
            worker.wait(5000)

            for _ in range(30):
                QApplication.processEvents()
                time.sleep(0.01)

            # Cửa sổ phải đóng thành công và app.quit được gọi chính xác 1 lần
            assert win._is_closed is True, "Cửa sổ phải được đóng sau khi thread vật lý hoàn tất"
            assert mock_quit.call_count == 1, "app.quit phải được gọi đúng 1 lần sau khi tất cả đã idle"
    finally:
        barrier.set()
        if guard is not None:
            qapp.removeEventFilter(guard)
            guard.deleteLater()
        _drain_window_workers(win)
        win.close()


# ============================================================================
# R09: Native finished không terminal và constructor/start failure handling
# ============================================================================

def test_r09_native_finished_without_terminal_and_start_exceptions(qapp, tmp_path):
    """R09: Native finished không terminal và constructor/start throws -> lỗi visible đúng, 0 fake success;
    never-start rollback, running-on-start-failure retain+cancel tới native finish."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        # Case 1: Thread vật lý kết thúc mà không phát ra terminal
        req = ReadinessRequest("req_r09_noterm", 1, prop, appr)
        worker = PreflightWorker(req, parent=win)
        win._register_preflight_candidate(worker, req)
        win.page_build.set_readiness_busy(True)
        win._reconcile_controls_state()

        # Giả lập worker_finished mà _preflight_terminal_consumed vẫn là False
        win._on_readiness_worker_finished(worker, req.request_id)
        assert win._last_readiness_result is None
        assert "không có kết quả" in win.page_build.lbl_readiness_status.text()
        assert win.page_build.btn_cancel_readiness.isVisible() is False
        assert win.btn_primary.isEnabled() is True

        # Case 2: Constructor failure hiển thị thông báo lỗi tiếng Việt
        with patch("src.ui.director.director_window.PreflightWorker", side_effect=RuntimeError("Constructor crash")):
            win._start_readiness_preflight()
            assert len(win._preflight_workers) == 0
            assert "Không thể khởi tạo" in win.page_build.lbl_readiness_status.text()
            assert win.btn_primary.isEnabled() is True

        # Case 3: Start failure khi worker đã running -> retain & cancel
        with patch.object(PreflightWorker, "start", side_effect=RuntimeError("Start crash")), \
             patch.object(PreflightWorker, "isRunning", return_value=True), \
             patch.object(PreflightWorker, "cancel") as mock_cancel:
            win._start_readiness_preflight()
            assert len(win._preflight_workers) == 1
            assert mock_cancel.call_count == 1
            worker3 = list(win._preflight_workers.values())[0]
            win._on_readiness_worker_finished(worker3, list(win._preflight_workers.keys())[0])
    finally:
        _drain_window_workers(win)
        win.close()


# ============================================================================
# R10: Source shutdown raises -> Review/Preflight vẫn được cancel; query fail-safe busy
# ============================================================================

def test_r10_source_shutdown_exception_does_not_block_review_and_preflight_cancel(qapp, tmp_path):
    """R10: Source shutdown raises nhưng Review/Preflight vẫn được cancel, all owners còn strong.
    Query error fail-safe busy, independent families drain mới close/quit."""
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    try:
        prop, appr = _setup_positive_real_approval(win, tmp_path, clip_count=1)
        win.go_to_step(4)
        QApplication.processEvents()

        req = ReadinessRequest("req_r10", 1, prop, appr)
        worker = PreflightWorker(req, parent=win)
        win._preflight_workers[req.request_id] = worker

        mock_rev_cancel = MagicMock(return_value=True)
        win.page_review.shutdown_analysis_workers = mock_rev_cancel

        with patch.object(win.page_source, "shutdown_workers", side_effect=RuntimeError("Source shutdown crash")), \
             patch.object(worker, "cancel") as mock_pre_cancel:

            # Ngoại lệ ở Source không được ngăn Review và Preflight cancel
            win.shutdown_analysis_and_preflight_workers()
            assert mock_rev_cancel.call_count == 1, "Review cancel phải được gọi dù Source ném ngoại lệ"
            assert mock_pre_cancel.call_count == 1, "Preflight cancel phải được gọi dù Source ném ngoại lệ"

        # Query error fail-safe busy
        with patch.object(worker, "isRunning", side_effect=RuntimeError("Cannot query thread")):
            assert win.is_analysis_and_preflight_idle() is False, "Query error phải coi là busy (fail-safe)"

        win._preflight_workers.clear()
        worker.deleteLater()
    finally:
        _drain_window_workers(win)
        win.close()


# ============================================================================
# R11: Standalone SourcePage và LibraryJob held còn sống -> Quit vẫn đợi; repeated Quit idempotent
# ============================================================================

def test_r11_standalone_source_page_and_library_job_quit_waiting_and_idempotence(qapp):
    """R11 (Dummy Object query simulation): Standalone/nonDirector SourcePage và dummy LibraryJob
    held còn sống thì Quit vẫn đợi; collecting synchronous idle/repeatedQuit giữ 1 connection/1 quit."""
    page_source = SourcePage()
    page_source.show()

    class DummyLibraryJob(QObject):
        finished = Signal()
        def __init__(self):
            super().__init__()
            self._running = True
            self.interrupted = False
        def isRunning(self):
            return self._running
        def requestInterruption(self):
            self.interrupted = True

    job = DummyLibraryJob()
    guard = SourceShutdownGuard(qapp)

    try:
        with patch("src.ui.widgets.pack_ingest_wizard.LibraryJob.active_jobs", {job}), \
             patch.object(qapp, "quit") as mock_quit:

            # Quit lần 1: Job đang chạy -> chưa quit
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0

            # Repeated Quit lần 2: Idempotent, không ném lỗi, không gọi double quit
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0

            # Job kết thúc vật lý
            job._running = False
            job.finished.emit()
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert mock_quit.call_count == 1, "Chỉ được gọi app.quit đúng 1 lần khi tất cả hoàn tất"
    finally:
        if guard is not None:
            qapp.removeEventFilter(guard)
            guard.deleteLater()
        page_source.close()


# ============================================================================
# L4–L5: Acceptance Tests cho Targeted Signal Cleanup & Real LibraryJob QThread
# ============================================================================

def test_l4_guard_own_connection_cleanup_and_non_guard_observers(qapp):
    """L4: SourceShutdownGuard chỉ dọn dẹp kết nối của chính mình: các listener khác nối trước
    hoặc sau guard đều được gọi khi finished; đổi [] thành ['observer']."""
    class Job(QObject):
        finished = Signal()
        def isRunning(self):
            return False

    job = Job()
    called = []
    job.finished.connect(lambda: called.append("before_guard"))

    guard = SourceShutdownGuard(qapp)
    try:
        # Giả lập job active trong guard
        guard._pending = True
        guard._library_jobs.add(job)
        slot = lambda j=job: guard._on_library_job_finished(j)
        guard._library_job_slots[job] = slot
        job.finished.connect(slot)

        job.finished.connect(lambda: called.append("observer"))

        with patch.object(qapp, "quit"):
            job.finished.emit()
            QApplication.processEvents()

        assert "before_guard" in called, "Listener nối trước guard phải được gọi"
        assert "observer" in called, "Listener nối sau guard phải được gọi (đổi [] thành ['observer'])"
        assert called == ["before_guard", "observer"]
        assert len(guard._library_jobs) == 0, "Guard phải dọn sạch registry sau khi idle"
    finally:
        qapp.removeEventFilter(guard)
        guard.deleteLater()


def test_l5_real_library_job_qthread_barrier_quit_waiting_and_cleanup(qapp):
    """L5: Real LibraryJob QThread operation held ở Event barrier: guard 0 quit khi running;
    release barrier -> native finished -> other observers chạy, active_jobs sạch, app.quit spy 1 lần."""
    from src.ui.widgets.pack_ingest_wizard import LibraryJob

    barrier = threading.Event()
    def _held_op(is_interrupted_fn, progress_fn):
        barrier.wait(timeout=5.0)
        return "success"

    job = LibraryJob(operation=_held_op)
    other_called = []
    job.finished.connect(lambda: other_called.append("real_observer"))

    guard = SourceShutdownGuard(qapp)
    try:
        job.start()
        assert job in LibraryJob.active_jobs, "Real LibraryJob phải tự đăng ký vào active_jobs khi start"
        assert job.isRunning() is True, "Phải là real QThread đang chạy"

        with patch.object(qapp, "quit") as mock_quit:
            # Quit khi job đang bị held ở barrier -> 0 quit
            guard.request_quit()
            QApplication.processEvents()
            assert mock_quit.call_count == 0, "Guard không được quit khi real QThread đang running"
            assert guard._pending is True

            # Thử lặp lại Quit trong drain: guard vẫn đợi
            guard.request_quit()  # Idempotent
            QApplication.processEvents()
            assert mock_quit.call_count == 0

            # Giải phóng barrier: thread chạy xong -> native finished
            barrier.set()
            job.wait(5000)
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)

            assert "real_observer" in other_called, "Other finished observers phải được gọi sau khi thread xong"
            assert job not in LibraryJob.active_jobs, "active_jobs phải được dọn dẹp sạch sẽ"
            assert mock_quit.call_count == 1, "Chỉ được gọi app.quit đúng 1 lần duy nhất"
    finally:
        barrier.set()
        job.wait(3000)
        if guard is not None:
            qapp.removeEventFilter(guard)
            guard.deleteLater()

