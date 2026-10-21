import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import patch
from PySide6.QtCore import QPoint

def test_is_point_inside_rect():
    from src.ui.lux.drag_drop_tracker import is_point_inside_rect

    rect = (100, 100, 800, 600)  # x: 100..900, y: 100..700

    assert is_point_inside_rect(QPoint(500, 400), rect) is True
    assert is_point_inside_rect(QPoint(100, 100), rect) is True
    assert is_point_inside_rect(QPoint(900, 700), rect) is True
    assert is_point_inside_rect(QPoint(50, 400), rect) is False
    assert is_point_inside_rect(QPoint(500, 800), rect) is False


def test_is_mouse_over_resolve_mock():
    from src.ui.lux.drag_drop_tracker import is_mouse_over_resolve

    mock_rect = (0, 0, 1920, 1080)
    with patch("src.ui.lux.drag_drop_tracker.get_resolve_window_rect", return_value=mock_rect):
        assert is_mouse_over_resolve(QPoint(500, 500)) is True
        assert is_mouse_over_resolve(QPoint(2000, 500)) is False

    with patch("src.ui.lux.drag_drop_tracker.get_resolve_window_rect", return_value=None):
        assert is_mouse_over_resolve(QPoint(500, 500)) is False
