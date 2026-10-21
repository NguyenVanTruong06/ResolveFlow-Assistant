"""
Unit tests for Edge Dock (Bước 3):
- DockStateMachine (6 states, debounce timers, auto-collapse guards)
- EdgeDockWindow (Frameless, StaysOnTop, WA_ShowWithoutActivating, NoFocus policies)
- Geometry calculations and boundary clamping
- Keyboard shortcuts (Enter, Tab, Esc)
- Sheet Inspector responsive sliding (bottom 62% vs right 320px)
- System Tray Icon (ChunDVCTrayIcon)
"""
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QRect

from src.ui.lux.shell.dock_state import DockState, DockStateMachine
from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.shell.tray import ChunDVCTrayIcon
from src.ui.lux.inspector.inspector_host import SheetInspector
from src.ui.lux.inspector.editors import (
    TextPresetEditor, AudioEditor, LutEditor, MacroEditor, SafeTrackSelector
)


def test_dock_state_machine_transitions(qapp):
    fsm = DockStateMachine()
    fsm.set_resolve_width(1920)

    # Initial state
    assert fsm.state == DockState.RAIL
    assert fsm.get_width_for_state(DockState.RAIL) == 44
    assert fsm.get_width_for_state(DockState.PEEK) == 56
    assert fsm.get_width_for_state(DockState.WIDE) == 760
    assert fsm.get_width_for_state(DockState.HIDDEN) == 2

    # Open width clamp: clamp(320, 30% of 1920 = 576, 420) -> 420
    assert fsm.calculate_open_width() == 420

    # Narrow resolve clamp: 30% of 900 = 270 -> clamped to min 320
    fsm.set_resolve_width(900)
    assert fsm.calculate_open_width() == 320

    # Transitions
    fsm.open_dock(animated=False)
    assert fsm.state == DockState.OPEN
    assert fsm.is_open is True

    fsm.toggle_wide()
    assert fsm.state == DockState.WIDE
    assert fsm.is_wide is True

    fsm.toggle_wide()
    assert fsm.state == DockState.OPEN
    assert fsm.is_wide is False

    fsm.toggle_pin()
    assert fsm.state == DockState.PINNED
    assert fsm.is_pinned is True

    # Collapse with pin shouldn't auto-collapse
    assert fsm.can_auto_collapse() is False
    fsm.toggle_pin()
    assert fsm.can_auto_collapse() is True

    # Search text guard
    fsm.on_search_text_changed("camera shake")
    assert fsm.can_auto_collapse() is False
    fsm.on_search_text_changed("")
    assert fsm.can_auto_collapse() is True

    # Sheet open guard
    fsm.set_sheet_open(True)
    assert fsm.can_auto_collapse() is False
    fsm.set_sheet_open(False)
    assert fsm.can_auto_collapse() is True

    # Explicit collapse
    fsm.collapse_dock(animated=False)
    assert fsm.state == DockState.RAIL
    assert fsm.is_open is False


def test_edge_dock_window_flags_and_focus_policy(qapp):
    dock = EdgeDockWindow()

    # Window flags
    flags = dock.windowFlags()
    assert bool(flags & Qt.WindowType.FramelessWindowHint) is True
    assert bool(flags & Qt.WindowType.WindowStaysOnTopHint) is True
    assert bool(flags & Qt.WindowType.Tool) is True

    # Window Attributes
    assert dock.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert dock.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True

    # Focus policy of container and passive elements MUST be NoFocus
    assert dock.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert dock.rail.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert dock.grid.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert dock.action_bar.focusPolicy() == Qt.FocusPolicy.NoFocus

    # Only search input and input fields have StrongFocus
    assert dock.header.txt_search.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_edge_dock_geometry_calculation_and_boundary_clamping(qapp):
    dock = EdgeDockWindow()
    dock._get_screen_rect = lambda: (0, 0, 1920, 1080)

    # Case 1: Normal 1920x1080 Resolve window
    anchor = (0, 0, 1920, 1080)
    dock.set_anchor_rect(anchor)
    geom_rail = dock._calc_target_geometry(44)
    assert geom_rail.x() == 1920 - 44
    assert geom_rail.width() == 44
    assert geom_rail.height() == 1080

    geom_open = dock._calc_target_geometry(420)
    assert geom_open.x() == 1920 - 420
    assert geom_open.width() == 420

    # Case 2: Narrow Resolve window (< 900px) e.g. 800px wide at x=0
    anchor_narrow = (0, 0, 800, 900)
    dock.set_anchor_rect(anchor_narrow)
    geom_narrow = dock._calc_target_geometry(320)
    # Since ax=0, aw=800 -> ideal_x = 800 <= screen width (1920), dock floats outside right edge!
    assert geom_narrow.x() == 800
    assert geom_narrow.width() == 320

    # Case 3: Narrow Resolve window at right edge of screen (ax=1200, aw=720) on 1920 screen
    # ideal_x = 1200 + 720 = 1920 -> exceeds screen! Clamped or shifted left
    anchor_right = (1200, 0, 720, 900)
    dock.set_anchor_rect(anchor_right)
    geom_clamped = dock._calc_target_geometry(320)
    assert geom_clamped.x() + geom_clamped.width() <= 1920


def test_edge_dock_keyboard_actions(qapp):
    mock_insert_service = MagicMock()
    mock_insert_service.insert.return_value = MagicMock(success=True)

    dock = EdgeDockWindow(insert_service=mock_insert_service)
    dock.set_anchor_rect((0, 0, 1920, 1080))
    dock.open_dock(animated=False)

    sample_asset = {
        "id": "t_hormozi",
        "name": "Alex Hormozi Pop",
        "category": "text",
        "duration": 4.0,
    }
    dock.set_assets([sample_asset])
    dock._on_asset_clicked(sample_asset)

    # Press Enter -> primary insert triggered
    with patch("src.ui.lux.shell.edge_dock.focus_davinci_resolve") as mock_focus:
        from PySide6.QtGui import QKeyEvent
        enter_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        dock.keyPressEvent(enter_event)

        assert mock_insert_service.insert.called
        assert mock_focus.called

    # Press Tab -> Sheet opened
    with patch("src.ui.lux.shell.edge_dock.focus_davinci_resolve"):
        assert dock.sheet.is_open is False
        tab_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
        dock.keyPressEvent(tab_event)
        assert dock.sheet.is_open is True

        # Press Esc -> Sheet closed
        esc_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dock.keyPressEvent(esc_event)
        assert dock.sheet.is_open is False


def test_sheet_inspector_responsive_modes_and_editors(qapp):
    parent = EdgeDockWindow()
    parent.resize(420, 720)
    sheet = SheetInspector(parent)

    sample_text = {"id": "t1", "name": "Text Sample", "category": "text", "duration": 3.0}
    sample_audio = {"id": "s1", "name": "SFX Sample", "category": "sfx", "duration": 1.5}
    sample_lut = {"id": "l1", "name": "LUT Sample", "category": "lut", "duration": 0}

    # Under 560px: bottom sheet covering 62% height
    sheet.open_sheet(sample_text)
    geom_bottom = sheet._calc_open_geometry()
    assert geom_bottom.width() == 420
    assert geom_bottom.height() == int(720 * 0.62)
    assert geom_bottom.y() == 720 - int(720 * 0.62)
    sheet.close_sheet(animated=False)

    # At 760px (Wide mode): right sheet 320px
    parent.resize(760, 720)
    sheet.open_sheet(sample_audio)
    geom_right = sheet._calc_open_geometry()
    assert geom_right.width() == 320
    assert geom_right.x() == 760 - 320
    assert geom_right.height() == 720
    sheet.close_sheet(animated=False)

    # Test editors get_params()
    text_ed = TextPresetEditor(sample_text)
    text_params = text_ed.get_params()
    assert "text" in text_params
    assert "duration" in text_params
    assert "motion" in text_params

    audio_ed = AudioEditor(sample_audio)
    audio_params = audio_ed.get_params()
    assert "volume_db" in audio_params
    assert "fade_seconds" in audio_params

    lut_ed = LutEditor(sample_lut)
    lut_params = lut_ed.get_params()
    assert "strength" in lut_params
    assert "scope" in lut_params


def test_system_tray_icon(qapp):
    tray = ChunDVCTrayIcon()
    assert tray.toolTip() == "ChunDVC — ResolveFlow Assistant"
    assert tray.contextMenu() is not None

    menu_actions = [a.text() for a in tray.contextMenu().actions() if a.text()]
    assert any("Edge Dock" in a for a in menu_actions)
    assert any("AI Director" in a for a in menu_actions)
    assert any("Thoát" in a for a in menu_actions)
