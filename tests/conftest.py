import sys
import pytest

# Windows compatibility shim for POSIX-only fcntl module
if sys.platform == "win32" and "fcntl" not in sys.modules:
    import types
    dummy_fcntl = types.ModuleType("fcntl")
    dummy_fcntl.LOCK_EX = 2
    dummy_fcntl.LOCK_SH = 1
    dummy_fcntl.LOCK_NB = 4
    dummy_fcntl.LOCK_UN = 8
    dummy_fcntl.flock = lambda *args, **kwargs: None
    dummy_fcntl.fcntl = lambda *args, **kwargs: 0
    sys.modules["fcntl"] = dummy_fcntl

from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    # Dừng timer giao diện và đóng cửa sổ trước khi kết thúc phiên Qt.
    for widget in app.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    # Lưu trạng thái phiên thật, kể cả lỗi collection và KeyboardInterrupt.
    session.config._qt_exitstatus = int(session.exitstatus)


def pytest_unconfigure(config):
    import os
    # On Linux with PySide6, Python finalization collides with Qt C++ static destructors.
    # Exit cleanly using the exit code set by pytest without triggering widget destructor segfaults.
    # Chỉ thoát cưỡng bức sau khi pytest đã teardown fixture và ghi báo cáo.
    status = getattr(config, "_qt_exitstatus", None)
    if status is not None:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(status)


@pytest.fixture(autouse=True)
def offline_resolve(monkeypatch):
    """Unit test chỉ dùng API giả; không nạp thư viện native hay chạm project thật."""
    import ctypes
    import types
    for name in ('DaVinciResolveScript', 'fusionscript', 'bmd'):
        module = types.ModuleType(name)
        module.scriptapp = lambda *args, **kwargs: None
        monkeypatch.setitem(sys.modules, name, module)
    original_cdll = ctypes.CDLL

    def isolated_cdll(name, *args, **kwargs):
        if name and 'fusionscript' in str(name).lower():
            raise OSError('Native Resolve bị vô hiệu hóa trong unit test')
        return original_cdll(name, *args, **kwargs)

    monkeypatch.setattr(ctypes, 'CDLL', isolated_cdll)
