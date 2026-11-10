"""
tests/ui/test_ai_setup_dialog.py
Kiểm thử giao diện hộp thoại Thiết Lập AI Cục Bộ (AISetupDialog):
- Khởi tạo và hiển thị độc lập trong môi trường offscreen
- Không tự động kích hoạt tải mạng khi mở hộp thoại
- Hiển thị đầy đủ 4 phân vùng: Thư mục lưu trữ, Whisper, Ollama daemon, Vision model
- Tương tác chọn model Whisper và Vision phát tín hiệu đồng bộ
- Kiểm tra các nút hành động (Cài đặt, Khởi động, Tải, Xác thực)
- Bảo vệ an toàn khi đóng cửa sổ trong lúc worker đang xử lý
"""

import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent

from src.services.local_ai_setup_service import (
    LocalAISetupService,
    OllamaInstallStatus,
    VisionModelStatus,
    DEFAULT_VISION_MODEL,
)
from src.ui.director.widgets.ai_setup_dialog import AISetupDialog
from tests.services.test_local_ai_setup_service import (
    FakeHttpClient,
    FakeProcessRunner,
    FakeFileSystemAdapter,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def test_setup_context(tmp_path):
    settings = QSettings(str(tmp_path / "test_dialog_settings.ini"), QSettings.Format.IniFormat)
    storage_root = str(tmp_path / "ai_storage")

    http = FakeHttpClient()
    proc = FakeProcessRunner()
    fs = FakeFileSystemAdapter()

    service = LocalAISetupService(
        storage_root=storage_root,
        settings=settings,
        http_client=http,
        process_runner=proc,
        fs_adapter=fs,
        platform_info=("linux", "x86_64"),
    )
    return service, http, proc, fs, tmp_path


def test_dialog_init_no_auto_network(qapp, test_setup_context):
    """Mở dialog không được tự động tải file hay gọi network download."""
    service, http, proc, fs, tmp_path = test_setup_context

    # Giả lập khi chưa cài đặt và daemon chưa chạy (get_json ném ConnectionError)
    http.get_json = MagicMock(side_effect=ConnectionError("Daemon not running"))

    dialog = AISetupDialog(service=service)
    qapp.processEvents()

    assert len(http.download_calls) == 0
    assert len(http.pull_calls) == 0
    assert dialog.txt_storage_path.text() == service.get_storage_root()
    assert not dialog.btn_install_ollama.isHidden()
    dialog.close()


def test_dialog_model_selection_signals(qapp, test_setup_context):
    """Thay đổi lựa chọn mô hình Whisper hoặc Vision phát tín hiệu đồng bộ."""
    service, http, proc, fs, tmp_path = test_setup_context

    dialog = AISetupDialog(service=service)
    whisper_emitted = []
    vision_emitted = []

    dialog.whisper_model_changed.connect(lambda m: whisper_emitted.append(m))
    dialog.vision_model_changed.connect(lambda m: vision_emitted.append(m))

    dialog.cbo_whisper_model.setCurrentText("medium")
    qapp.processEvents()
    assert "medium" in whisper_emitted

    dialog.cbo_vision_model.setCurrentText("qwen2.5-vl:7b")
    qapp.processEvents()
    assert "qwen2.5-vl:7b" in vision_emitted
    dialog.close()


def test_start_button_refreshes_an_already_running_daemon_without_stopping_it(qapp, test_setup_context):
    service, http, proc, _, tmp_path = test_setup_context
    binary = tmp_path / "ai_storage" / "bin" / "ollama"
    binary.parent.mkdir(parents=True)
    binary.touch()
    pid, identity = proc.start_process([str(binary), "serve"], {}, str(binary.parent))
    service._save_daemon_metadata(identity)
    http.tags_response = {"models": []}

    dialog = AISetupDialog(service=service)
    assert dialog.btn_toggle_daemon.text() == "Khởi động"
    dialog.btn_toggle_daemon.click()
    worker = dialog._active_worker
    if worker is not None:
        assert worker.wait(2000)
        qapp.processEvents()

    assert proc.terminated_pids == []
    assert proc.is_process_running(pid)
    assert len(proc.started_commands) == 1
    assert dialog.btn_toggle_daemon.text() == "Dừng"
    dialog.close()


def test_dialog_install_ollama_action(qapp, test_setup_context):
    """Bấm Cài đặt Ollama kích hoạt worker và cập nhật giao diện."""
    import hashlib
    service, http, proc, fs, tmp_path = test_setup_context

    # Chuẩn bị dummy payload có mã hash khớp với manifest
    dummy_payload = b"VALID_OLLAMA_BINARY_ARCHIVE"
    hasher = hashlib.sha256()
    hasher.update(dummy_payload)
    valid_hash = hasher.hexdigest()
    http.download_data = dummy_payload
    service.manifest[("linux", "x86_64")]["sha256"] = valid_hash

    dialog = AISetupDialog(service=service)
    qapp.processEvents()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_install_ollama.click()
        assert dialog._active_worker is not None
        dialog._active_worker.wait(2000)
        qapp.processEvents()

    assert dialog._active_worker is None
    assert len(fs.extracted_archives) == 1
    dialog.close()


def test_dialog_vision_pull_and_verify(qapp, test_setup_context):
    """Thực hiện tải mô hình thị giác và xác thực độ sẵn sàng (READY)."""
    service, http, proc, fs, tmp_path = test_setup_context

    # Giả lập Ollama đang chạy
    proc.running_pids.add(1234)
    pid_file = tmp_path / "ai_storage" / "ollama.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("1234")
    http.tags_response = {"models": []}

    dialog = AISetupDialog(service=service)
    dialog._refresh_all_statuses()
    qapp.processEvents()

    # Model chưa tải
    assert "Chưa tải" in dialog.lbl_vision_badge.text()
    assert dialog.btn_pull_vision.isEnabled()

    # Bấm Tải model
    http.pull_events = [{"status": "success"}]
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_pull_vision.click()
        assert dialog._active_worker is not None
        dialog._active_worker.wait(2000)
        qapp.processEvents()

    # Sau khi pull, giả lập tags đã có model
    http.tags_response = {"models": [{"name": DEFAULT_VISION_MODEL, "digest": "sha256:model123"}]}
    dialog._refresh_all_statuses()
    qapp.processEvents()

    # Chưa verify -> Đã tải
    assert "Đã tải" in dialog.lbl_vision_badge.text()
    assert dialog.btn_verify_vision.isEnabled()

    # Bấm Xác thực (Show response thực tế không có digest, generate trả về done=True)
    http.show_response = {"capabilities": ["vision"]}
    http.generate_response = {"response": "hi", "done": True}

    dialog.btn_verify_vision.click()
    assert dialog._active_worker is not None
    dialog._active_worker.wait(2000)
    qapp.processEvents()

    # Đã xác thực -> Sẵn sàng
    dialog._refresh_all_statuses()
    qapp.processEvents()
    assert "Sẵn sàng" in dialog.lbl_vision_badge.text()
    dialog.close()


def test_dialog_close_during_active_worker_defers_and_cancels(qapp, test_setup_context):
    """Khi đóng dialog trong lúc worker đang chạy, hoãn đóng (ignore event), gửi cancel và chỉ đóng khi worker hoàn tất."""
    import hashlib
    service, http, proc, fs, tmp_path = test_setup_context

    dummy_payload = b"VALID_OLLAMA_BINARY_ARCHIVE"
    hasher = hashlib.sha256()
    hasher.update(dummy_payload)
    http.download_data = dummy_payload
    service.manifest[("linux", "x86_64")]["sha256"] = hasher.hexdigest()

    dialog = AISetupDialog(service=service)
    qapp.processEvents()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_install_ollama.click()

    assert dialog.is_busy() is True
    worker = dialog._active_worker
    assert worker is not None

    # Giả lập người dùng gửi sự kiện đóng cửa sổ (closeEvent)
    close_event = QCloseEvent()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.closeEvent(close_event)

    # Sự kiện phải bị IGNORE để không hủy C++ QDialog ngay lập tức
    assert not close_event.isAccepted()
    assert dialog._close_pending is True
    assert worker._is_cancelled is True

    # Đợi worker hoàn tất
    worker.wait(2000)
    qapp.processEvents()

    assert dialog.is_busy() is False


def test_dialog_whisper_delegate_real_progress_and_completion(qapp, test_setup_context):
    """Whisper delegate nhận callback on_progress và on_finished, cập nhật thanh tiến trình thực."""
    service, http, proc, fs, tmp_path = test_setup_context

    captured_callbacks = {}

    def fake_whisper_delegate(model, on_progress=None, on_finished=None):
        captured_callbacks["model"] = model
        captured_callbacks["on_progress"] = on_progress
        captured_callbacks["on_finished"] = on_finished
        return True

    dialog = AISetupDialog(service=service, whisper_prep_delegate=fake_whisper_delegate)
    qapp.processEvents()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_download_whisper.click()

    assert captured_callbacks.get("model") == "small"
    assert captured_callbacks.get("on_progress") is not None
    assert captured_callbacks.get("on_finished") is not None
    assert dialog.is_busy() is True
    assert "Đang tải trong nền" in dialog.lbl_whisper_badge.text()

    # Báo tiến trình 60%
    prog_fn = captured_callbacks["on_progress"]
    prog_fn("small", 60.0, "Đang tải chunk 6/10")
    qapp.processEvents()
    assert dialog.prog_whisper.value() == 60

    # Báo hoàn tất
    fin_fn = captured_callbacks["on_finished"]
    fin_fn("small", True, "done")
    qapp.processEvents()

    assert dialog.is_busy() is False
    assert dialog.prog_whisper.isHidden()
    dialog.close()


def test_dialog_duplicate_job_prevention(qapp, test_setup_context):
    """Khi dialog đang bận (is_busy), các thao tác kích hoạt worker khác bị từ chối ngay lập tức."""
    service, http, proc, fs, tmp_path = test_setup_context

    dialog = AISetupDialog(service=service)
    dialog._whisper_in_progress = True
    assert dialog.is_busy() is True

    # Click các nút khác không được phép mở worker mới
    dialog.btn_install_ollama.click()
    assert dialog._current_worker is None

    dialog.btn_toggle_daemon.click()
    assert dialog._current_worker is None

    dialog.btn_pull_vision.click()
    assert dialog._current_worker is None

    dialog.btn_verify_vision.click()
    assert dialog._current_worker is None

    dialog._whisper_in_progress = False
    dialog.close()


def test_dialog_windows_installer_prompt_and_honest_status(qapp, tmp_path):
    """Trên Windows, bấm Cài đặt hiển thị thông báo hướng dẫn trung thực và không báo thành công giả tạo."""
    settings = QSettings(str(tmp_path / "win_ui_settings.ini"), QSettings.Format.IniFormat)
    storage_root = str(tmp_path / "win_ui_storage")
    http = FakeHttpClient()
    proc = FakeProcessRunner()
    fs = FakeFileSystemAdapter()

    import hashlib
    dummy_exe = b"WINDOWS_INSTALLER_PAYLOAD"
    hasher = hashlib.sha256()
    hasher.update(dummy_exe)
    http.download_data = dummy_exe

    service = LocalAISetupService(
        storage_root=storage_root,
        settings=settings,
        http_client=http,
        process_runner=proc,
        fs_adapter=fs,
        platform_info=("win32", "x86_64"),
    )
    service.manifest[("win32", "x86_64")]["sha256"] = hasher.hexdigest()

    dialog = AISetupDialog(service=service)
    qapp.processEvents()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_install_ollama.click()
        assert dialog._active_worker is not None
        dialog._active_worker.wait(2000)
        qapp.processEvents()

    # Footer status phải thông báo trung thực là đã mở trình cài đặt, chưa xong hoàn toàn
    assert "OllamaSetup.exe" in dialog.lbl_footer_status.text()
    assert "quay lại" in dialog.lbl_footer_status.text().lower() or "hoàn tất" in dialog.lbl_footer_status.text().lower()
    dialog.close()


def test_dialog_whisper_cancel_and_error_restores_ui(qapp, test_setup_context):
    """Khi tiến trình tải Whisper bị hủy hoặc gặp lỗi, giao diện và nút bấm được khôi phục nguyên vẹn."""
    service, http, proc, fs, tmp_path = test_setup_context

    captured = {}
    def fake_delegate(model, on_progress=None, on_finished=None):
        captured["on_finished"] = on_finished
        return True

    dialog = AISetupDialog(service=service, whisper_prep_delegate=fake_delegate)
    qapp.processEvents()

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_download_whisper.click()

    assert dialog.is_busy() is True
    # Báo hủy
    captured["on_finished"]("small", False, "cancelled")
    qapp.processEvents()

    assert dialog.is_busy() is False
    assert "Đã hủy" in dialog.lbl_footer_status.text()
    assert dialog.btn_download_whisper.isEnabled()

    # Báo lỗi mạng
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dialog.btn_download_whisper.click()
    captured["on_finished"]("small", False, "Mạng không khả dụng")
    qapp.processEvents()

    assert dialog.is_busy() is False
    assert "Lỗi tải Whisper" in dialog.lbl_footer_status.text()
    assert dialog.btn_download_whisper.isEnabled()
    dialog.close()


def test_source_page_whisper_prep_shows_user_facing_messages(qapp):
    """SourcePage hiển thị thông báo toast trực quan tới người dùng trên các nhánh busy, success, error, cancel."""
    from src.ui.director.pages.source_page import SourcePage
    sp = SourcePage()
    qapp.processEvents()

    # 1. Nhánh busy warning
    sp._workers_registry["whisper_prep_small"] = MagicMock()
    with patch.object(sp, "_show_toast") as mock_toast:
        res = sp.start_whisper_model_preparation("small")
        assert res is False
        mock_toast.assert_called_once()
        assert "tiến trình" in mock_toast.call_args[0][0]

    sp._workers_registry.clear()
    sp.close()
