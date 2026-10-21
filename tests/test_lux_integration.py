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


def test_chundvc_edge_dock_integration(qapp):
    from src.ui.lux.shell import EdgeDockWindow, DockState
    from src.ui.director import DirectorWindow

    dock = EdgeDockWindow()
    try:
        assert hasattr(dock, "rail")
        assert hasattr(dock, "sheet")
        assert hasattr(dock, "fsm")
        assert dock.fsm.state in (DockState.RAIL, DockState.OPEN, DockState.HIDDEN)

        # Test expand / collapse
        dock.fsm.open_dock()
        assert dock.fsm.state == DockState.OPEN

        dock.fsm.collapse_dock()
        assert dock.fsm.state == DockState.RAIL

        # Test Director window trigger
        assert hasattr(dock, "open_director")
        dock.open_director()
        assert dock._director_window is not None
        assert isinstance(dock._director_window, DirectorWindow)
        dock._director_window.close()
    finally:
        dock.close()
