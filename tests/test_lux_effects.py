import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, QPoint, QEasingCurve, QRect
from PySide6.QtGui import QColor

# Ensure QApplication exists
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_ripple_overlay_creation_and_trigger(qapp):
    from src.ui.lux.lux_effects import RippleOverlay

    parent = QWidget()
    parent.resize(200, 100)
    btn = QPushButton("Test Button", parent)
    btn.setGeometry(10, 10, 180, 40)

    ripple = RippleOverlay(parent=parent)
    # Check transparent for mouse events
    assert ripple.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True

    # Trigger ripple at center of button
    ripple.trigger_ripple(QPoint(100, 30), color=QColor("#3dcee1"))
    assert ripple.is_animating() is True
    assert ripple.current_radius > 0
    assert ripple.current_opacity <= 0.40

    # Advance or finish animation
    ripple.stop()
    assert ripple.is_animating() is False


def test_segment_indicator_slide(qapp):
    from src.ui.lux.lux_effects import SegmentIndicator

    parent = QWidget()
    parent.resize(400, 40)

    # 4 segments, each ~100px wide
    indicator = SegmentIndicator(segment_count=4, parent=parent)
    indicator.setGeometry(0, 36, 400, 4)
    indicator.set_index(0, animated=False)

    assert indicator.current_index == 0
    rect0 = indicator.get_indicator_rect()
    assert rect0.width() == 100
    assert rect0.x() == 0

    # Animate to index 2
    indicator.set_index(2, animated=True)
    assert indicator.target_index == 2
    assert indicator.animation.easingCurve().type() == QEasingCurve.Type.OutCubic

    # Stop animation and check destination
    indicator.animation.setCurrentTime(indicator.animation.duration())
    assert indicator.current_index == 2
    rect2 = indicator.get_indicator_rect()
    assert rect2.x() == 200


def test_luxury_dark_stylesheet():
    from src.ui.lux.lux_effects import get_luxury_dark_stylesheet

    qss = get_luxury_dark_stylesheet()
    assert isinstance(qss, str)
    assert len(qss) > 100
    assert "rgba(14, 17, 22" in qss or "rgba(18, 22, 28" in qss
    assert "rgba(255, 255, 255, 0.08)" in qss or "0.08" in qss
    assert "#3C9A8C" in qss or "#3c9a8c" in qss or "#3dcee1" in qss
