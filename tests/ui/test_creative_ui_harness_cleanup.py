"""
tests/ui/test_creative_ui_harness_cleanup.py
=============================================================================
Unit Tests cho Teardown Safety & Cleanup Logic của Desktop Smoke Harness
(TASK_ID: G06-06C3-S2d-HARNESS-FINAL-THEN-S3-D)

Kiểm thử toàn diện quy trình dọn dẹp tài nguyên thông qua safe DI và fakes:
1. Dọn dẹp thành công khi không còn worker chạy (session_root bị xóa, env phục hồi, clipboard phục hồi, window đóng).
2. Bảo tồn session_root VÀ GIỮ NGUYÊN window khi còn worker đang chạy (WINDOW_RETAINED, RETAINED_DUE_TO_RUNNING_WORKER).
3. Báo cáo lỗi khi session_root không xóa được hoặc thư mục vẫn còn tồn tại (REMOVAL_FAILED).
4. Phân định trung thực mã thoát và verdict dựa trên kết quả dọn dẹp thực tế (evaluate_harness_verdict_and_exit).
5. Bảo tồn clipboard khi người dùng / ứng dụng ngoại vi đã ghi đè dữ liệu (PRESERVED_USER_OR_FOREIGN).
6. Bảo tồn clipboard khi người dùng đã xóa rỗng (không dùng fallback 'empty means ours').
7. Giải phóng barrier đang bị giữ khi xảy ra ngoại lệ.
8. Enumerate chính xác tất cả live workers từ mọi registry.
=============================================================================
"""

import os
import shutil
import tempfile
import threading
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData, QByteArray

from task.desktop_verify_06c3_creative_ui import (
    SafeMimeSnapshot,
    SessionOwnershipRecord,
    capture_session_ownership_record,
    _LIVE_SUPERVISOR_RETAINED_OBJECTS,
    compute_mime_digest,
    enumerate_live_workers,
    cancel_and_wait_workers,
    resolve_clipboard_teardown,
    cleanup_harness_resources,
    evaluate_harness_verdict_and_exit,
    hold_live_supervisor_until_safe,
    supervisor_main,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_enumerate_live_workers_across_all_registries():
    """Kiểm tra enumerate_live_workers phát hiện đúng các worker đang chạy từ mọi registry."""
    director = MagicMock()

    # 1. Prompt workers: 1 running, 1 stopped
    w1_running = MagicMock()
    w1_running.isRunning.return_value = True
    w1_stopped = MagicMock()
    w1_stopped.isRunning.return_value = False
    director.page_review._prompt_workers_registry = {
        "req_1": w1_running,
        "req_old": w1_stopped,
    }

    # 2. Analysis workers: 1 running
    w2_running = MagicMock()
    w2_running.isRunning.return_value = True
    director.page_review._active_analysis_workers = [w2_running]

    # 3. Kinetic workers: 1 running
    w3_running = MagicMock()
    w3_running.isRunning.return_value = True
    director.page_review._kinetic_workers_registry = {"kin_1": w3_running}

    # 4. SourcePage worker: 1 running
    w4_running = MagicMock()
    w4_running.isRunning.return_value = True
    director.page_source._worker = w4_running

    # 5. Controller worker: 1 running
    w5_running = MagicMock()
    w5_running.isRunning.return_value = True
    director.controller.worker = w5_running

    live = enumerate_live_workers(director)
    assert len(live) == 5
    assert w1_running in live
    assert w2_running in live
    assert w3_running in live
    assert w4_running in live
    assert w5_running in live
    assert w1_stopped not in live


def test_cleanup_success_with_stopped_workers(tmp_path, qapp):
    """Kiểm tra cleanup thành công: đóng window, xóa session_root, phục hồi env và khôi phục clipboard sở hữu."""
    session_dir = tmp_path / "mock_session"
    session_dir.mkdir(parents=True, exist_ok=True)
    canary = session_dir / "canary.txt"
    canary.write_text("SESSION_DATA")

    orig_env = {"MOCK_CLEANUP_ENV_VAR": "ORIGINAL_VAL"}
    os.environ["MOCK_CLEANUP_ENV_VAR"] = "MODIFIED_IN_HARNESS"

    cb = qapp.clipboard()
    init_mime = QMimeData()
    init_mime.setText("INITIAL_CLIPBOARD_TEXT")
    cb.setMimeData(init_mime)
    snap_initial = SafeMimeSnapshot(cb.mimeData())
    init_dig = snap_initial.digest

    harness_mime = QMimeData()
    harness_mime.setText("HARNESS_COPIED_PROMPT")
    cb.setMimeData(harness_mime)
    harness_dig = compute_mime_digest(cb.mimeData())
    owned_digests = {init_dig, harness_dig}

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    ownership_record = capture_session_ownership_record(session_dir)
    assert ownership_record is not None

    metrics = {}
    res = cleanup_harness_resources(
        director=director,
        app=qapp,
        session_root=session_dir,
        session_ownership_record=ownership_record,
        initial_clipboard=snap_initial,
        owned_clipboard_digests=owned_digests,
        metrics=metrics,
        orig_env=orig_env,
        worker_timeout_ms=100,
    )

    assert res["cancellation_outcome"] == "JOINED_CLEANLY"
    assert res["window_cleanup"] == "CLOSED"
    assert director.close.called is True, "Window phải được đóng khi mọi worker đã kết thúc"
    assert res["session_ownership_status"] == "REMOVED"
    assert not session_dir.exists(), "session_dir phải bị xóa an toàn khi mọi worker đã kết thúc"
    assert res["clipboard_verdict"] == "RESTORED_INITIAL"
    assert cb.text() == "INITIAL_CLIPBOARD_TEXT"
    assert os.environ.get("MOCK_CLEANUP_ENV_VAR") == "ORIGINAL_VAL"
    assert metrics["cleanup"]["session_ownership_status"] == "REMOVED"


def test_cleanup_still_running_worker_retains_session_and_window(tmp_path, qapp):
    """Kiểm tra an toàn tuyệt đối: Nếu còn worker đang chạy, KHÔNG đóng window và KHÔNG XÓA session_root."""
    session_dir = tmp_path / "mock_session_retained"
    session_dir.mkdir(parents=True, exist_ok=True)
    canary = session_dir / "canary.txt"
    canary.write_text("CRITICAL_CRASH_OR_RUNNING_DATA")

    stubborn_worker = MagicMock()
    stubborn_worker.isRunning.return_value = True
    stubborn_worker.__repr__ = lambda self: "<StubbornWorkerRunning>"

    director = MagicMock()
    director.page_review._prompt_workers_registry = {"stubborn": stubborn_worker}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    metrics = {}
    res = cleanup_harness_resources(
        director=director,
        app=qapp,
        session_root=session_dir,
        metrics=metrics,
        worker_timeout_ms=50,
    )

    # Khẳng định an toàn:
    assert res["cancellation_outcome"] == "TIMEOUT_RETAINED"
    assert res["window_cleanup"] == "WINDOW_RETAINED"
    assert director.close.called is False, "KHÔNG ĐƯỢC PHÉP đóng window khi worker còn đang chạy!"
    assert res["session_ownership_status"] == "RETAINED_DUE_TO_RUNNING_WORKER"
    assert session_dir.exists(), "session_dir BẮT BUỘC giữ nguyên trên đĩa khi worker còn chạy!"
    assert canary.read_text() == "CRITICAL_CRASH_OR_RUNNING_DATA"
    assert len(res["running_workers_after"]) == 1
    assert "<StubbornWorkerRunning>" in res["running_workers_after"][0]


def test_cleanup_removal_failure_when_dir_persists(tmp_path, qapp):
    """Kiểm tra báo cáo trung thực: Nếu rmtree không xóa được (hoặc giả lập no-op), phải báo REMOVAL_FAILED."""
    session_dir = tmp_path / "mock_session_stubborn_dir"
    session_dir.mkdir(parents=True, exist_ok=True)
    ownership_record = capture_session_ownership_record(session_dir)

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    # Patch shutil.rmtree thành no-op để thư mục vẫn tồn tại sau thao tác xóa
    with patch("shutil.rmtree") as mock_rm:
        mock_rm.return_value = None  # Không xóa gì cả
        res = cleanup_harness_resources(
            director=director,
            app=qapp,
            session_root=session_dir,
            session_ownership_record=ownership_record,
            worker_timeout_ms=50,
        )

    assert "REMOVAL_FAILED" in res["session_ownership_status"]
    assert session_dir.exists()


def test_cleanup_removal_failure_on_exception(tmp_path, qapp):
    """Kiểm tra báo cáo trung thực: Khi rmtree ném ngoại lệ PermissionError, phải báo REMOVAL_FAILED."""
    session_dir = tmp_path / "mock_session_perm_err"
    session_dir.mkdir(parents=True, exist_ok=True)
    ownership_record = capture_session_ownership_record(session_dir)

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    with patch("shutil.rmtree", side_effect=PermissionError("Permission Denied Fake")):
        res = cleanup_harness_resources(
            director=director,
            app=qapp,
            session_root=session_dir,
            session_ownership_record=ownership_record,
            worker_timeout_ms=50,
        )

    assert "REMOVAL_FAILED" in res["session_ownership_status"]
    assert "Permission Denied Fake" in res["session_ownership_status"]


def test_cleanup_unauthorized_path_rejected_without_rmtree(tmp_path, qapp):
    """Kiểm tra an toàn sở hữu độc quyền (Part 0):
    Đường dẫn không có ownership_record khớp (kể cả có chứa substring tmp/mock/pytest)
    phải bị từ chối NGAY LẬP TỨC với 0 lần gọi rmtree.
    """
    unowned_dir = tmp_path / "arbitrary_customer_data_in_tmp"
    unowned_dir.mkdir(parents=True, exist_ok=True)
    canary = unowned_dir / "user_secret.txt"
    canary.write_text("SENSITIVE_DATA")

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    with patch("shutil.rmtree") as mock_rm:
        res = cleanup_harness_resources(
            director=director,
            app=qapp,
            session_root=unowned_dir,
            session_ownership_record=None,  # Không có record sở hữu
            worker_timeout_ms=50,
        )

    assert mock_rm.called is False, "rmtree TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI khi thiếu ownership record!"
    assert res["session_ownership_status"] == "REMOVAL_REJECTED_UNAUTHORIZED"
    assert canary.exists()
    assert canary.read_text() == "SENSITIVE_DATA"


def test_cleanup_wrong_identity_ownership_record_rejected(tmp_path, qapp):
    """Kiểm tra an toàn sở hữu: Record có path/inode/device không khớp phải bị từ chối 0 lần gọi rmtree."""
    real_dir = tmp_path / "actual_session_dir"
    real_dir.mkdir(parents=True, exist_ok=True)

    other_dir = tmp_path / "other_dir"
    other_dir.mkdir(parents=True, exist_ok=True)
    wrong_record = capture_session_ownership_record(other_dir)

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    with patch("shutil.rmtree") as mock_rm:
        res = cleanup_harness_resources(
            director=director,
            app=qapp,
            session_root=real_dir,
            session_ownership_record=wrong_record,  # Record thuộc thư mục khác
            worker_timeout_ms=50,
        )

    assert mock_rm.called is False, "rmtree TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI khi identity mismatch!"
    assert res["session_ownership_status"] == "REMOVAL_REJECTED_UNAUTHORIZED"


def test_cleanup_symlink_session_root_rejected(tmp_path, qapp):
    """Kiểm tra an toàn: session_root là symlink phải bị từ chối, không rmtree."""
    target_dir = tmp_path / "target_dir"
    target_dir.mkdir(parents=True, exist_ok=True)
    symlink_dir = tmp_path / "symlink_dir"
    symlink_dir.symlink_to(target_dir, target_is_directory=True)

    fake_rec = SessionOwnershipRecord(
        canonical_path=str(symlink_dir.resolve()),
        device=target_dir.stat().st_dev,
        inode=target_dir.stat().st_ino,
    )

    director = MagicMock()
    director.page_review._prompt_workers_registry = {}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    with patch("shutil.rmtree") as mock_rm:
        res = cleanup_harness_resources(
            director=director,
            app=qapp,
            session_root=symlink_dir,
            session_ownership_record=fake_rec,
            worker_timeout_ms=50,
        )

    assert mock_rm.called is False, "rmtree TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI trên symlink!"
    assert res["session_ownership_status"] == "REMOVAL_REJECTED_UNAUTHORIZED"


def test_supervisor_registry_retains_live_worker_objects(tmp_path, qapp):
    """Kiểm tra: Khi worker còn chạy, supervisor registry lưu trữ live references đầy đủ."""
    session_dir = tmp_path / "mock_session_live_worker"
    session_dir.mkdir(parents=True, exist_ok=True)

    stubborn_worker = MagicMock()
    stubborn_worker.isRunning.return_value = True

    director = MagicMock()
    director.page_review._prompt_workers_registry = {"stubborn": stubborn_worker}
    director.page_review._active_analysis_workers = {}
    director.page_review._kinetic_workers_registry = {}
    director.page_source._worker = None
    director.controller.worker = None

    _LIVE_SUPERVISOR_RETAINED_OBJECTS.clear()

    res = cleanup_harness_resources(
        director=director,
        app=qapp,
        session_root=session_dir,
        worker_timeout_ms=50,
    )

    assert res["window_cleanup"] == "WINDOW_RETAINED"
    assert "still_running_workers" in _LIVE_SUPERVISOR_RETAINED_OBJECTS
    assert stubborn_worker in _LIVE_SUPERVISOR_RETAINED_OBJECTS["still_running_workers"]
    assert _LIVE_SUPERVISOR_RETAINED_OBJECTS["director"] is director


def test_cleanup_verdict_and_exit_selection_policy():
    """Kiểm tra evaluate_harness_verdict_and_exit tuân thủ chính xác điều kiện quan sát dọn dẹp."""
    clean_ok = {
        "cancellation_outcome": "JOINED_CLEANLY",
        "window_cleanup": "CLOSED",
        "session_ownership_status": "REMOVED",
        "env_cleanup": "RESTORED",
        "running_workers_after": [],
    }

    clean_fail_worker = {
        "cancellation_outcome": "TIMEOUT_RETAINED",
        "window_cleanup": "WINDOW_RETAINED",
        "session_ownership_status": "RETAINED_DUE_TO_RUNNING_WORKER",
        "env_cleanup": "RESTORED",
        "running_workers_after": ["w1"],
    }

    clean_fail_session = {
        "cancellation_outcome": "JOINED_CLEANLY",
        "window_cleanup": "CLOSED",
        "session_ownership_status": "REMOVAL_FAILED: dir exists",
        "env_cleanup": "RESTORED",
        "running_workers_after": [],
    }

    # 1. Normal mode: Cả test và cleanup đều thành công -> COMPONENT_DESKTOP_VERIFIED, exit 0
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=False,
        test_cases_passed=True,
        fault_boundary_reached=False,
        caught_expected_fault=False,
        cleanup_res=clean_ok,
    )
    assert v == "COMPONENT_DESKTOP_VERIFIED"
    assert code == 0

    # 2. Normal mode: Test pass nhưng cleanup fail (worker retained) -> FAILED, exit 1
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=False,
        test_cases_passed=True,
        fault_boundary_reached=False,
        caught_expected_fault=False,
        cleanup_res=clean_fail_worker,
    )
    assert v == "FAILED"
    assert code == 1

    # 3. Normal mode: Test pass nhưng session removal fail -> FAILED, exit 1
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=False,
        test_cases_passed=True,
        fault_boundary_reached=False,
        caught_expected_fault=False,
        cleanup_res=clean_fail_session,
    )
    assert v == "FAILED"
    assert code == 1

    # 4. Fault injection mode: Boundary reached + fault caught + cleanup ok -> FAULT_INJECTION_RECOVERED_EXIT_2, exit 2
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=True,
        test_cases_passed=False,
        fault_boundary_reached=True,
        caught_expected_fault=True,
        cleanup_res=clean_ok,
    )
    assert v == "FAULT_INJECTION_RECOVERED_EXIT_2"
    assert code == 2

    # 5. Fault injection mode: Boundary reached nhưng cleanup fail -> FAULT_INJECTION_FAILED, exit 1
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=True,
        test_cases_passed=False,
        fault_boundary_reached=True,
        caught_expected_fault=True,
        cleanup_res=clean_fail_worker,
    )
    assert v == "FAULT_INJECTION_FAILED"
    assert code == 1

    # 6. Fault injection mode: Boundary không đạt -> FAULT_INJECTION_FAILED, exit 1
    v, code = evaluate_harness_verdict_and_exit(
        fault_injection=True,
        test_cases_passed=False,
        fault_boundary_reached=False,
        caught_expected_fault=False,
        cleanup_res=clean_ok,
    )
    assert v == "FAULT_INJECTION_FAILED"
    assert code == 1


def test_cleanup_foreign_clipboard_preserved(qapp):
    """Kiểm tra: Nếu clipboard hiện tại mang dữ liệu ngoại vi/người dùng, bảo tồn và không ghi đè."""
    cb = qapp.clipboard()

    init_mime = QMimeData()
    init_mime.setText("OLD_INITIAL")
    cb.setMimeData(init_mime)
    snap_initial = SafeMimeSnapshot(cb.mimeData())
    init_dig = snap_initial.digest

    owned_digests = {init_dig, "owned_digest_123"}

    foreign_mime = QMimeData()
    foreign_mime.setText("EXTERNAL_USER_SECRET_CODE")
    cb.setMimeData(foreign_mime)

    verdict = resolve_clipboard_teardown(qapp, snap_initial, owned_digests)
    assert verdict == "PRESERVED_USER_OR_FOREIGN"
    assert cb.text() == "EXTERNAL_USER_SECRET_CODE", "Clipboard người dùng phải được giữ nguyên vẹn!"


def test_cleanup_cleared_clipboard_preserved(qapp):
    """Kiểm tra: Người dùng xóa clipboard -> Không dùng fallback 'empty means ours', giữ nguyên trạng thái rỗng."""
    cb = qapp.clipboard()

    init_mime = QMimeData()
    init_mime.setText("NON_EMPTY_INITIAL")
    cb.setMimeData(init_mime)
    snap_initial = SafeMimeSnapshot(cb.mimeData())
    init_dig = snap_initial.digest

    owned_digests = {init_dig, "harness_write_456"}

    cb.clear()

    verdict = resolve_clipboard_teardown(qapp, snap_initial, owned_digests)
    assert verdict == "PRESERVED_USER_OR_FOREIGN"
    assert cb.text() == "", "Không được phép khôi phục ban đầu khi người dùng đã chủ động xóa clipboard!"


def test_cleanup_releases_held_barrier_on_exception(tmp_path, qapp):
    """Kiểm tra giải phóng barrier: Nếu có thread đang chờ tại barrier, cleanup_harness_resources phải unblock."""
    barrier = threading.Event()
    assert not barrier.is_set()

    metrics = {}
    cleanup_harness_resources(
        director=None,
        app=qapp,
        session_root=tmp_path / "barrier_test",
        held_barrier_release=barrier,
        metrics=metrics,
    )

    assert barrier.is_set() is True, "held_barrier_release.set() phải được gọi để không gây deadlock!"


def test_hold_live_supervisor_waits_and_exits_when_workers_finish(tmp_path, qapp):
    """Probe D: hold_live_supervisor_until_safe giữ event loop và dọn dẹp an toàn khi workers kết thúc."""
    fake_worker = MagicMock()
    # Chạy 2 nhịp rồi kết thúc
    fake_worker.isRunning.side_effect = [True, True, False]

    session_dir = tmp_path / "supervisor_safe_session"
    session_dir.mkdir(parents=True, exist_ok=True)
    canary = session_dir / "canary.txt"
    canary.write_text("DATA")

    rec = capture_session_ownership_record(session_dir)

    fake_director = MagicMock()

    _LIVE_SUPERVISOR_RETAINED_OBJECTS.clear()
    _LIVE_SUPERVISOR_RETAINED_OBJECTS["director"] = fake_director
    _LIVE_SUPERVISOR_RETAINED_OBJECTS["still_running_workers"] = [fake_worker]
    _LIVE_SUPERVISOR_RETAINED_OBJECTS["session_root"] = session_dir
    _LIVE_SUPERVISOR_RETAINED_OBJECTS["session_ownership_record"] = rec

    metrics = {}
    res = hold_live_supervisor_until_safe(
        app=qapp,
        max_wait_sec=2.0,
        poll_ms=20,
        metrics=metrics,
    )

    assert res["supervisor_status"] == "SAFE_ALL_WORKERS_EXITED"
    assert res["pending_workers_count"] == 0
    assert fake_director.close.called is True
    assert not session_dir.exists(), "session_root phải được dọn dẹp khi workers kết thúc an toàn!"
    assert metrics["live_supervisor"]["supervisor_status"] == "SAFE_ALL_WORKERS_EXITED"


def test_hold_live_supervisor_stops_on_stop_event_or_timeout(qapp):
    """Probe D: Live supervisor dừng có kiểm soát qua stop_event, không rơi vào treo vô hạn."""
    hung_worker = MagicMock()
    hung_worker.isRunning.return_value = True

    _LIVE_SUPERVISOR_RETAINED_OBJECTS.clear()
    _LIVE_SUPERVISOR_RETAINED_OBJECTS["still_running_workers"] = [hung_worker]

    stop_evt = threading.Event()
    stop_evt.set()  # Đã dừng trước

    res = hold_live_supervisor_until_safe(
        app=qapp,
        max_wait_sec=1.0,
        poll_ms=10,
        stop_event=stop_evt,
    )

    assert res["supervisor_status"] == "SUPERVISOR_HOLDING"
    assert res["pending_workers_count"] == 1


def test_supervisor_main_never_exits_while_workers_pending_on_timeout_or_stop_event():
    """Probe D / R2-7: Khi worker còn đang chạy tại boundary timeout/stop_event, supervisor_main
    tuyệt đối KHÔNG gọi sys_exit_fn (0 lần gọi exit), bảo toàn live supervisor và tiến trình.
    """
    hung_worker = MagicMock()
    hung_worker.isRunning.return_value = True

    retained = {"still_running_workers": [hung_worker]}
    exit_spy = MagicMock()

    # Case A: Stop event được kích hoạt trong khi worker còn chạy
    stop_evt = threading.Event()
    stop_evt.set()

    res = supervisor_main(
        fault_injection=False,
        harness_runner_fn=lambda **kw: 0,
        supervisor_wait_fn=lambda **kw: {
            "supervisor_status": "SUPERVISOR_HOLDING",
            "pending_workers_count": 1,
            "held_duration_sec": 0.05,
        },
        sys_exit_fn=exit_spy,
        get_retained_objects_fn=lambda: retained,
        max_wait_sec=0.1,
        stop_event=stop_evt,
    )

    assert res is None, "Khi worker còn pending, supervisor_main phải trả về None và từ chối exit"
    assert exit_spy.call_count == 0, "KHÔNG ĐƯỢC PHÉP gọi sys_exit_fn khi worker còn pending (stop_event)!"

    # Case B: Timeout xảy ra trong khi worker còn chạy
    exit_spy.reset_mock()
    res_timeout = supervisor_main(
        fault_injection=False,
        harness_runner_fn=lambda **kw: 1,
        supervisor_wait_fn=lambda **kw: {
            "supervisor_status": "SUPERVISOR_TIMEOUT_WORKERS_RETAINED",
            "pending_workers_count": 1,
            "held_duration_sec": 1.0,
        },
        sys_exit_fn=exit_spy,
        get_retained_objects_fn=lambda: retained,
        max_wait_sec=0.1,
    )

    assert res_timeout is None
    assert exit_spy.call_count == 0, "KHÔNG ĐƯỢC PHÉP gọi sys_exit_fn khi worker còn pending (timeout)!"


def test_supervisor_main_permits_final_exit_when_all_workers_idle():
    """Probe D / R2-7: Khi tất cả workers đã idle (hoặc không có worker nào chạy),
    supervisor_main cho phép gọi sys_exit_fn đúng mã thoát dự kiến và hoàn tất dọn dẹp.
    """
    exit_spy = MagicMock()

    # Case A: Không có worker nào chạy từ đầu
    retained_empty = {"still_running_workers": []}
    code_a = supervisor_main(
        fault_injection=False,
        harness_runner_fn=lambda **kw: 0,
        sys_exit_fn=exit_spy,
        get_retained_objects_fn=lambda: retained_empty,
    )
    assert code_a == 0
    assert exit_spy.call_count == 1
    assert exit_spy.call_args[0][0] == 0

    # Case B: Có worker nhưng sau đó supervisor_wait_fn báo SAFE_ALL_WORKERS_EXITED (pending = 0)
    exit_spy.reset_mock()
    m_worker = MagicMock()
    m_worker.isRunning.return_value = True

    def fake_wait(**kw):
        m_worker.isRunning.return_value = False
        return {
            "supervisor_status": "SAFE_ALL_WORKERS_EXITED",
            "pending_workers_count": 0,
            "held_duration_sec": 0.2,
        }

    retained_with_worker = {"still_running_workers": [m_worker]}
    code_b = supervisor_main(
        fault_injection=True,
        harness_runner_fn=lambda **kw: 2,
        supervisor_wait_fn=fake_wait,
        sys_exit_fn=exit_spy,
        get_retained_objects_fn=lambda: retained_with_worker,
    )
    assert code_b == 2
    assert exit_spy.call_count == 1
    assert exit_spy.call_args[0][0] == 2


# =============================================================================
# R3: Fake Main Keep-Loop Physical Completion Tests (Matrix 6)
# =============================================================================

def test_probe_r3_supervisor_main_keep_loop_physical_reaping():
    """R3 Matrix 6: Fake main keep-loop: worker True -> False qua fake event tick.
    supervisor_main CHỈ cleanup/exit sau khi worker physically False, 0 exit trước đó."""
    class FakeWorker:
        def __init__(self):
            self.running = True

        def isRunning(self):
            return self.running

    class FakeApp:
        def __init__(self, target_worker):
            self.worker = target_worker
            self.ticks = 0

        def processEvents(self):
            self.ticks += 1
            # Trên tick 1, worker physically hoàn tất
            self.worker.running = False

    w = FakeWorker()
    app = FakeApp(w)
    exits = []
    retained = {"still_running_workers": [w]}

    with patch("task.desktop_verify_06c3_creative_ui.log"), patch("task.desktop_verify_06c3_creative_ui.time.sleep"):
        res = supervisor_main(
            harness_runner_fn=lambda **kw: 0,
            supervisor_wait_fn=lambda **kw: {"pending_workers_count": 1},
            get_retained_objects_fn=lambda: retained,
            app_instance_fn=lambda: app,
            sys_exit_fn=exits.append,
            keep_event_loop_on_pending=True,
        )

    assert res == 0, "supervisor_main phải trả về exit code khi all workers idle!"
    assert exits == [0], "sys_exit_fn phải được gọi đúng 1 lần với mã thoát 0 sau khi worker kết thúc!"
    assert app.ticks >= 1, "Event loop phải được pump ít nhất 1 lần để gặt hái worker!"
    assert w.running is False, "Worker phải ở trạng thái physically stopped!"


@pytest.mark.parametrize("metrics_variant", [
    {},
    {"pending_workers_count": 0},
    {"pending_workers_count": -5},
    {"pending_workers_count": False},
    {"pending_workers_count": "wrong_type"},
])
def test_supervisor_main_ignores_absent_zero_or_malformed_metrics_when_worker_alive(metrics_variant):
    """A3 / Matrix 5: Diagnostics {} / counter 0 sai / negative / bool / wrong type
    tuyệt đối không cho phép cleanup/exit khi worker physically alive."""
    class FakeWorker:
        def __init__(self):
            self.running = True

        def isRunning(self):
            return self.running

    class FakeApp:
        def __init__(self, target_worker):
            self.worker = target_worker
            self.ticks = 0

        def processEvents(self):
            self.ticks += 1
            # Tick 1: worker kết thúc
            self.worker.running = False

    w = FakeWorker()
    app = FakeApp(w)
    exits = []
    retained = {"still_running_workers": [w]}

    with patch("task.desktop_verify_06c3_creative_ui.log"), patch("task.desktop_verify_06c3_creative_ui.time.sleep"):
        res = supervisor_main(
            harness_runner_fn=lambda **kw: 0,
            supervisor_wait_fn=lambda **kw: metrics_variant,
            get_retained_objects_fn=lambda: retained,
            app_instance_fn=lambda: app,
            sys_exit_fn=exits.append,
            keep_event_loop_on_pending=True,
        )

    assert res == 0
    assert exits == [0]
    assert app.ticks >= 1
    assert w.running is False


def test_is_worker_alive_exception_treated_as_unknown_pending():
    """A3 / Matrix 5: Query exception phải trả về True (unknown/pending), không False."""
    from task.desktop_verify_06c3_creative_ui import _is_worker_alive

    class ThrowingWorker:
        def isRunning(self):
            raise RuntimeError("Fake query failure")

    w = ThrowingWorker()
    assert _is_worker_alive(w) is True




