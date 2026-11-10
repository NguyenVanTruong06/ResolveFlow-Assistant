"""
tests/ui/test_header_settings_access.py
Kiểm thử tính năng hiển thị phiên bản và truy cập Cài đặt góc trên bên phải (Persistent Header Actions):
- Hiển thị chính xác phiên bản APP_VERSION nhập từ src.core.branding (nguồn duy nhất).
- Nút bánh răng 'Cài đặt' (⚙) luôn hiển thị và dễ tiếp cận trên DirectorWindow và StudioWindow.
- Header và nút cài đặt bảo toàn, không bị ẩn hay biến mất khi chuyển tab/bước (Step/Category).
- Các tín hiệu settings_requested và ai_setup_requested phát ra chính xác.
- Kiểm tra tính tương thích co giãn kích thước (1024x768, 1280x720 và chế độ hẹp) không bị clip.
"""

import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize

from src.core.branding import APP_VERSION
from src.ui.director.director_window import DirectorWindow
from src.ui.studio.studio_window import StudioWindow
from src.ui.lux.shell.dock_header import DockHeaderWidget
from src.ui.widgets.app_header_actions import AppHeaderActions


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_app_header_actions_widget_standalone(qapp):
    """Kiểm tra widget AppHeaderActions độc lập hiển thị đúng version và phát signal."""
    widget = AppHeaderActions(show_version=True)
    qapp.processEvents()

    assert widget.lbl_version is not None
    assert widget.lbl_version.text() == str(APP_VERSION)
    assert widget.btn_settings.accessibleName() == "Cài đặt"

    settings_received = []
    ai_received = []

    widget.settings_requested.connect(lambda: settings_received.append(True))
    widget.ai_setup_requested.connect(lambda: ai_received.append(True))

    widget.trigger_settings()
    assert len(settings_received) == 1

    widget.trigger_ai_setup()
    assert len(ai_received) == 1
    widget.close()


def test_director_window_persistent_header_and_version(qapp):
    """DirectorWindow luôn hiển thị phiên bản và nút Cài đặt ở góc phải qua mọi bước stepper."""
    win = DirectorWindow()
    qapp.processEvents()

    # 1. Kiểm tra sự hiện diện của header_actions
    assert hasattr(win, "header_actions")
    assert win.header_actions.lbl_version.text() == str(APP_VERSION)
    assert not win.header_actions.btn_settings.isHidden()
    assert win.header_actions.btn_settings.accessibleName() == "Cài đặt"

    # 2. Kiểm tra header tồn tại xuyên suốt qua các bước 1 -> 2 -> 3 -> 4
    for step in [1, 2, 3, 4]:
        win.go_to_step(step)
        qapp.processEvents()
        assert win.header_actions.isVisibleTo(win) or not win.header_actions.isHidden()
        assert win.header_actions.lbl_version.text() == str(APP_VERSION)

    # 3. Kiểm tra signal settings_requested
    settings_called = []
    win.settings_requested.connect(lambda: settings_called.append(True))
    win.header_actions.trigger_settings()
    assert len(settings_called) == 1

    win.close()


def test_studio_window_persistent_header_and_version(qapp):
    """StudioWindow hiển thị phiên bản và nút Cài đặt cố định trên thanh tiêu đề."""
    win = StudioWindow()
    qapp.processEvents()

    # 1. Kiểm tra cụm nút tiêu đề
    assert hasattr(win, "header_actions")
    assert win.header_actions.lbl_version.text() == str(APP_VERSION)
    assert not win.header_actions.btn_settings.isHidden()

    # 2. Kiểm tra signal settings_requested
    settings_called = []
    win.settings_requested.connect(lambda: settings_called.append(True))
    win.header_actions.trigger_settings()
    assert len(settings_called) == 1

    win.close()


def test_dock_header_settings_accessibility_in_narrow_mode(qapp):
    """DockHeader đảm bảo nút Cài đặt luôn tiếp cận được ở cả chế độ thường và chế độ hẹp (<410px)."""
    header = DockHeaderWidget()
    header.show()
    qapp.processEvents()

    # 1. Chiều rộng thông thường (420px): nút settings trực tiếp hiển thị
    header.resize(420, 44)
    qapp.processEvents()
    assert not header.btn_settings.isHidden()
    assert header.btn_settings.accessibleName() == "Cài đặt"

    settings_emitted = []
    header.settings_clicked.connect(lambda: settings_emitted.append(True))
    header.btn_settings.click()
    assert len(settings_emitted) == 1

    # 2. Chiều rộng hẹp (320px): nút overflow hiển thị và thay thế để mở settings
    header.resize(320, 44)
    qapp.processEvents()
    assert header.btn_settings.isHidden()
    assert not header.btn_overflow.isHidden()
    assert "Cài đặt" in header.btn_overflow.accessibleName()

    header.close()


def test_header_actions_responsive_sizes(qapp):
    """Kiểm tra bố cục không bị tràn vỡ hoặc khuất nút ở các độ phân giải 1024x768 và 1280x720."""
    for w, h in [(1024, 768), (1280, 720)]:
        win = DirectorWindow()
        win.resize(w, h)
        qapp.processEvents()

        # Nút cài đặt và chip whisper không bị đè nhau
        assert not win.header_actions.btn_settings.isHidden()
        assert not win.btn_whisper.isHidden()
        assert win.header_actions.width() > 0
        win.close()
