import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_chundvc_drawer_mode_toggle(qapp):
    from src.ui.app import ChunDVCApp
    from src.ui.lux.panel_edge_rail import EdgeDrawerWindow

    app_window = ChunDVCApp()
    assert hasattr(app_window, "btn_toggle_drawer")
    assert app_window.drawer is None

    # Switch to Drawer Mode
    app_window._toggle_drawer_mode()
    assert isinstance(app_window.drawer, EdgeDrawerWindow)
    assert app_window.drawer.isVisible() is True
    assert app_window.isHidden() is True

    # Switch back to Standard Window
    app_window._restore_from_drawer()
    assert app_window.drawer.isHidden() is True
    assert app_window.isVisible() is True

    # Clean up
    app_window.drawer.close()
    app_window.close()
