import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_dock_rail_widget_init(qapp):
    from src.ui.lux.shell import DockRailWidget

    rail = DockRailWidget()
    assert rail.width() == 44 or rail.sizeHint().width() == 44
    assert hasattr(rail, "btn_logo")
    assert hasattr(rail, "btn_director")
    assert hasattr(rail, "btn_palette")


def test_edge_dock_window_geometry_and_flags(qapp):
    from src.ui.lux.shell import EdgeDockWindow, DockState

    dock = EdgeDockWindow()
    try:
        flags = dock.windowFlags()
        assert (flags & Qt.WindowType.FramelessWindowHint) == Qt.WindowType.FramelessWindowHint
        assert (flags & Qt.WindowType.WindowStaysOnTopHint) == Qt.WindowType.WindowStaysOnTopHint

        assert dock.fsm.state in (DockState.RAIL, DockState.OPEN, DockState.HIDDEN)
    finally:
        dock.close()
