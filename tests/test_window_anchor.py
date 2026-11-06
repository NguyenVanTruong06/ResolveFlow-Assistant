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


def test_anchor_loss_emits_none(qapp):
    from src.ui.lux.window_anchor import ResolveWindowAnchor
    tracker = ResolveWindowAnchor()
    values = []
    tracker.anchor_updated.connect(values.append)
    tracker.get_rect = MagicMock(return_value=(100, 50, 1600, 900))
    tracker._check_anchor()
    tracker.get_rect.return_value = None
    tracker._check_anchor()
    assert values == [(100, 50, 1600, 900), None]


def test_identity_parser_ignores_browser_and_prefers_main_window():
    from src.ui.lux.window_anchor import _parse_wmctrl_output
    output = '''0x01 0 100 400 300 900 600 google-chrome.Google-chrome host DaVinci Resolve - Google Chrome
0x02 0 200 600 400 800 500 resolve.Resolve host DaVinci Resolve Dialog
0x03 0 200 0 0 1920 1080 resolve.Resolve host DaVinci Resolve Studio
'''
    assert _parse_wmctrl_output(output, with_identity=True) == (0, 0, 1920, 1080)


@pytest.mark.parametrize('anchor,want', [
    ((1800, 1000, 400, 500), (1520, 1000, 400, 80)),
    ((2500, 100, 1000, 800), (1520, 0, 400, 1080)),
    ((-200, -100, 1600, 900), (1000, 0, 400, 800)),
])
def test_geometry_stays_inside_screen(anchor, want):
    from src.ui.lux.window_anchor import calculate_dock_geometry
    assert calculate_dock_geometry(anchor, (0, 0, 1920, 1080), 400) == want
