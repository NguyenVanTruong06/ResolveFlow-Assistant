import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from PySide6.QtCore import Qt, QRect

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_edge_drawer_window_init(qapp):
    from src.ui.lux.panel_edge_rail import EdgeDrawerWindow

    drawer = EdgeDrawerWindow()
    # Verify window flags
    flags = drawer.windowFlags()
    assert (flags & Qt.WindowType.FramelessWindowHint) == Qt.WindowType.FramelessWindowHint
    assert (flags & Qt.WindowType.WindowStaysOnTopHint) == Qt.WindowType.WindowStaysOnTopHint

    assert drawer.is_expanded is False
    assert drawer.width() == EdgeDrawerWindow.COLLAPSED_WIDTH


def test_edge_drawer_expand_and_collapse(qapp):
    from src.ui.lux.panel_edge_rail import EdgeDrawerWindow

    drawer = EdgeDrawerWindow()
    drawer.set_anchor_rect((0, 0, 1920, 1080))

    # Test immediate expand
    drawer.expand(animated=False)
    assert drawer.is_expanded is True
    assert drawer.width() == EdgeDrawerWindow.EXPANDED_WIDTH
    assert drawer.geometry().x() == 1920 - EdgeDrawerWindow.EXPANDED_WIDTH

    # Test immediate collapse
    drawer.collapse(animated=False)
    assert drawer.is_expanded is False
    assert drawer.width() == EdgeDrawerWindow.COLLAPSED_WIDTH
    assert drawer.geometry().x() == 1920 - EdgeDrawerWindow.COLLAPSED_WIDTH


def test_edge_drawer_central_widget_hosting(qapp):
    from src.ui.lux.panel_edge_rail import EdgeDrawerWindow

    drawer = EdgeDrawerWindow()
    content = QLabel("Central Content Test")
    drawer.set_central_widget(content)

    assert drawer.central_widget == content


def test_edge_drawer_peek_reveal(qapp):
    from src.ui.lux.panel_edge_rail import EdgeDrawerWindow

    drawer = EdgeDrawerWindow()
    drawer.setGeometry(1920 - EdgeDrawerWindow.COLLAPSED_WIDTH, 0, EdgeDrawerWindow.COLLAPSED_WIDTH, 1080)

    orig_x = drawer.geometry().x()
    # Trigger peek reveal
    drawer._peek_reveal(True)
    assert drawer.geometry().x() == orig_x - EdgeDrawerWindow.PEEK_OFFSET

    # Unpeek
    drawer._peek_reveal(False)
    assert drawer.geometry().x() == orig_x
