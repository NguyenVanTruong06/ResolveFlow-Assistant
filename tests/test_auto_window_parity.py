import sys
import os
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

def test_auto_window_init(qapp):
    from src.ui.auto.auto_window import AutoWindow
    window = AutoWindow()
    assert window is not None
    assert hasattr(window, "btn_open_viral_pack")
    assert hasattr(window, "btn_1click_resolve")
    assert window.last_viral_pack_md is None
    window.close()
    window.deleteLater()

def test_auto_window_checkbox_sync(qapp):
    from src.ui.auto.auto_window import AutoWindow
    window = AutoWindow()
    
    # Test checking visible check_punch_in updates tab_autocut.check_punch_in
    window.check_punch_in.setChecked(True)
    assert window.tab_autocut.check_punch_in.isChecked() is True
    
    window.check_punch_in.setChecked(False)
    assert window.tab_autocut.check_punch_in.isChecked() is False
    
    # Test vice versa: setting tab_autocut updates visible checkbox
    window.tab_autocut.check_vlog_hook.setChecked(True)
    assert window.check_hook.isChecked() is True
    
    window.tab_autocut.check_vlog_hook.setChecked(False)
    assert window.check_hook.isChecked() is False
    
    window.close()
    window.deleteLater()

def test_start_gui_symbol(qapp):
    import inspect
    from src.ui.auto import auto_window
    src = inspect.getsource(auto_window.start_gui)
    assert "ChunDVCApp" not in src
    assert "AutoWindow" in src
