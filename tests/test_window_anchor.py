import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import patch, MagicMock
from PySide6.QtCore import QRect

def test_calculate_dock_geometry():
    from src.ui.lux.window_anchor import calculate_dock_geometry

    # When Resolve window is at (100, 50, 1600, 900)
    anchor_rect = (100, 50, 1600, 900)
    screen_rect = (0, 0, 1920, 1080)

    # Expanded drawer of 440px on right edge
    x, y, w, h = calculate_dock_geometry(anchor_rect, screen_rect, drawer_width=440, edge="right")
    assert w == 440
    assert h == 900
    assert y == 50
    assert x == 100 + 1600 - 440

    # Collapsed drawer of 38px on right edge
    x, y, w, h = calculate_dock_geometry(anchor_rect, screen_rect, drawer_width=38, edge="right")
    assert w == 38
    assert x == 100 + 1600 - 38

    # Fallback to screen when anchor_rect is None
    x, y, w, h = calculate_dock_geometry(None, screen_rect, drawer_width=400, edge="right")
    assert w == 400
    assert h == 1080
    assert y == 0
    assert x == 1920 - 400


def test_linux_wmctrl_parsing():
    from src.ui.lux.window_anchor import _parse_wmctrl_output

    sample_output = """0x04000003  0 0    0    1920 1080 myhost Desktop
0x0480000a  0 100  50   1600 900  myhost DaVinci Resolve Studio - Project
0x05000005  0 200  200  800  600  myhost Some Other App
"""
    rect = _parse_wmctrl_output(sample_output)
    assert rect == (100, 50, 1600, 900)


def test_anchor_tracker_timer(qapp):
    from src.ui.lux.window_anchor import ResolveWindowAnchor

    tracker = ResolveWindowAnchor(poll_interval_ms=100)
    tracker.get_rect = MagicMock(return_value=(0, 0, 1280, 720))

    emitted = []
    tracker.anchor_updated.connect(lambda r: emitted.append(r))

    tracker._check_anchor()
    assert emitted == [(0, 0, 1280, 720)]

    # If rect is same, no redundant emit
    tracker._check_anchor()
    assert len(emitted) == 1

    # If rect changes
    tracker.get_rect = MagicMock(return_value=(100, 100, 1280, 720))
    tracker._check_anchor()
    assert len(emitted) == 2
    assert emitted[1] == (100, 100, 1280, 720)
