"""
Tests for Step 5: Quick Palette + Global Hotkey (UX_REDESIGN.md Section 5).
Covers:
- CommandRegistry and system commands
- PaletteSearchProvider (empty query groups, text search, command search, frecency)
- CommandPaletteWindow UI, geometry (640px, 22% top), and dynamic height
- Keyboard navigation (↑↓, Enter, Shift+Enter, Alt+1..9, Tab, Ctrl+Enter, Esc)
- ChunDVCToast notification with 1-click Undo and error states
- GlobalHotkeyService clean architecture and simulation
- EdgeDock integration
"""
import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QKeyEvent

from src.services.insert_service import InsertService, InsertResult
from src.services.search_index import SearchIndex
from src.services.global_hotkey import GlobalHotkeyService
from src.ui.lux.palette.command_registry import CommandRegistry, CommandItem
from src.ui.lux.palette.palette_model import PaletteSearchProvider, PaletteItem
from src.ui.lux.palette.command_palette import CommandPaletteWindow
from src.ui.lux.feedback.toast import ChunDVCToast
from src.ui.lux.shell.edge_dock import EdgeDockWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


SAMPLE_ASSETS = [
    {"id": "tr_shake", "name": "Camera Shake", "category": "trans", "duration": 1.2},
    {"id": "tr_glitch", "name": "RGB Glitch", "category": "trans", "duration": 0.8},
    {"id": "t_minimal", "name": "Clean Minimal Title", "category": "text", "duration": 4.0},
    {"id": "s_swoosh", "name": "Deep Swoosh", "category": "sfx", "duration": 0.6},
    {"id": "s_whoosh", "name": "Fast Whoosh", "category": "sfx", "duration": 0.4},
    {"id": "l_cinema", "name": "Cinematic Gold LUT", "category": "lut", "duration": 0.0},
    {"id": "m_macro", "name": "Motion Blur Macro", "category": "mine", "duration": 2.5},
]


def test_command_registry():
    """Verify built-in system commands and query searching."""
    reg = CommandRegistry()
    cmds = reg.all_commands()
    assert len(cmds) >= 7

    # Check required commands
    cmd_names = [c.name.lower() for c in cmds]
    assert any(">cắt khoảng lặng" in n for n in cmd_names)
    assert any(">render tiktok" in n for n in cmd_names)
    assert any(">mở director" in n for n in cmd_names)
    assert any(">chuẩn hóa loudness" in n for n in cmd_names)
    assert any(">đi tới trang fusion" in n for n in cmd_names)

    # Search with and without '>'
    res1 = reg.search(">render")
    assert len(res1) >= 1
    assert "render" in res1[0].name.lower()

    res2 = reg.search("cắt")
    assert len(res2) >= 1
    assert "cắt" in res2[0].name.lower()


def test_palette_search_provider_empty_and_text(qapp):
    """Verify search provider empty query groups, text search, and command search."""
    provider = PaletteSearchProvider()
    provider.set_assets(SAMPLE_ASSETS)
    provider.set_recent_ids(["tr_shake", "s_swoosh"])
    provider.set_fav_ids({"t_minimal"})

    # 1. Empty query should return Recent, Favorites, and Page suggestions
    items = provider.search("", current_page="edit", limit=7)
    assert len(items) >= 3
    assert items[0].data["id"] == "tr_shake"
    assert "Gần đây" in items[0].meta
    assert items[0].shortcut_hint == "⏎"
    assert items[1].shortcut_hint == "⌥2"

    # 2. Command query
    cmd_items = provider.search(">cắt", limit=7)
    assert len(cmd_items) >= 1
    assert cmd_items[0].kind == "command"
    assert "cắt" in cmd_items[0].title

    # 3. Text query (without SearchIndex fallback)
    text_items = provider.search("swoosh", limit=7)
    assert len(text_items) >= 1
    assert text_items[0].data["id"] == "s_swoosh"

    # 4. Toggle favorite
    is_fav = provider.toggle_fav("tr_shake")
    assert is_fav is True
    is_fav2 = provider.toggle_fav("tr_shake")
    assert is_fav2 is False


def test_command_palette_ui_geometry_and_dynamic_height(qapp):
    """Verify CommandPaletteWindow 640px width, 22% top position, and dynamic height."""
    palette = CommandPaletteWindow()
    palette.set_assets(SAMPLE_ASSETS)
    palette.set_anchor_rect((100, 50, 1600, 900))

    # Calculate target geometry
    target = palette._calc_target_rect()
    assert target.width() == 640
    # Center X: 100 + (1600 - 640) // 2 = 100 + 480 = 580
    assert target.x() == 580
    # Top Y at 22% of height: 50 + int(900 * 0.22) = 50 + 198 = 248
    assert target.y() == 248

    # Open palette
    palette.open_palette()
    assert palette.isVisible()
    assert palette.width() == 640
    assert palette.height() >= 100
    assert palette.height() <= 420
    palette.close_palette()


def test_command_palette_keyboard_interactions(qapp):
    """Test arrow navigation, Enter insert, Alt+1..9, Tab customize, Esc close."""
    mock_insert = MagicMock(spec=InsertService)
    mock_insert.insert.return_value = InsertResult(
        ok=True,
        track="V3",
        start_tc="00:01:00:00",
        item_name="Camera Shake",
    )

    palette = CommandPaletteWindow(insert_service=mock_insert)
    palette.set_assets(SAMPLE_ASSETS)
    palette.set_resolve_context("edit", "00:01:00:00")
    palette.open_palette()

    inserted_records = []
    palette.asset_inserted.connect(lambda a, p: inserted_records.append((a, p)))

    # Navigate down
    initial_sel = palette._selected_index
    palette._on_key_nav("down")
    assert palette._selected_index == (initial_sel + 1) % len(palette._items)

    # Press Enter to insert
    palette._on_enter_pressed(is_shift=False, is_ctrl=False)
    assert len(inserted_records) == 1
    assert mock_insert.insert.called
    assert not palette.isVisible()

    # Re-open and test Alt+2 instant insert
    palette.open_palette()
    palette._on_alt_digit(2)
    assert len(inserted_records) == 2

    # Re-open and test Tab customize
    customized = []
    palette.customize_requested.connect(customized.append)
    palette.open_palette()
    palette._on_key_nav("tab")
    assert len(customized) == 1
    assert not palette.isVisible()

    # Re-open and test Esc close
    palette.open_palette()
    palette._on_key_nav("esc")
    assert not palette.isVisible()

    palette.toast.close()


def test_command_palette_shift_enter_modes(qapp):
    """Verify Shift+Enter inserts as new_track on Edit and fusion_paste on Fusion."""
    mock_insert = MagicMock(spec=InsertService)
    mock_insert.insert.return_value = InsertResult(ok=True)

    palette = CommandPaletteWindow(insert_service=mock_insert)
    palette.set_assets(SAMPLE_ASSETS)

    # 1. On Edit page, Shift+Enter should use mode 'new_track'
    palette.set_resolve_context("edit", "00:00:00:00")
    palette.open_palette()
    palette._on_enter_pressed(is_shift=True, is_ctrl=False)
    assert mock_insert.insert.call_args[1]["mode"] == "new_track"

    # 2. On Fusion page, Shift+Enter or Enter should use mode 'fusion_paste'
    palette.set_resolve_context("fusion", "00:00:00:00")
    palette.open_palette()
    palette._on_enter_pressed(is_shift=False, is_ctrl=False)
    assert mock_insert.insert.call_args[1]["mode"] == "fusion_paste"

    palette.toast.close()


def test_toast_feedback_success_and_undo(qapp):
    """Verify ChunDVCToast floating feedback, undo callback, and error display."""
    toast = ChunDVCToast()
    undo_mock = MagicMock()
    mock_result = InsertResult(ok=True, track="V2", start_tc="00:01:12:00")

    # Show success toast
    toast.show_success(
        asset_name="Camera Shake",
        track_name="V2",
        timecode="00:01:12:00",
        result=mock_result,
        undo_callback=undo_mock,
    )
    assert toast.isVisible()
    assert "Camera Shake" in toast.lbl_msg.text()
    assert toast.btn_action.isVisible()
    assert toast.btn_action.text() == "Hoàn tác"

    # Click Undo
    toast._on_action_clicked()
    assert undo_mock.called
    assert "Đã hoàn tác" in toast.lbl_msg.text()
    assert not toast.btn_action.isVisible()

    # Show error toast
    toast.show_error(reason="Không có track trống", action_label="Tạo track mới")
    assert "Không có track trống" in toast.lbl_msg.text()
    assert toast.btn_action.isVisible()
    assert toast.btn_action.text() == "Tạo track mới"

    toast.close()


def test_global_hotkey_service_clean_architecture_and_simulation(qapp):
    """Verify GlobalHotkeyService adheres to Clean Architecture and responds to triggers."""
    import src.services.global_hotkey as gh_module

    # Clean Architecture Verification: ensure QtWidgets and QtGui are never imported
    assert "QtWidgets" not in gh_module.__dict__
    assert "QtGui" not in gh_module.__dict__

    service = GlobalHotkeyService()
    palette_events = []
    cmd_events = []
    service.palette_requested.connect(lambda: palette_events.append(True))
    service.command_mode_requested.connect(lambda: cmd_events.append(True))

    service.simulate_hotkey("palette")
    assert len(palette_events) == 1
    assert len(cmd_events) == 0

    service.simulate_hotkey("command")
    assert len(palette_events) == 1
    assert len(cmd_events) == 1

    service.set_enabled(False)
    service.simulate_hotkey("palette")
    assert len(palette_events) == 1

    service.stop()


def test_edge_dock_palette_and_hotkey_integration(qapp):
    """Verify EdgeDockWindow has palette and hotkey_service wired up."""
    dock = EdgeDockWindow()
    dock.show()
    dock.open_dock(animated=False)

    assert hasattr(dock, "palette")
    assert hasattr(dock, "hotkey_service")

    dock.open_palette(command_mode=False)
    assert dock.palette.isVisible()

    # Simulate Tab customize from palette
    test_asset = {"id": "test_asset_1", "name": "Custom Title", "category": "text"}
    dock.palette.customize_requested.emit(test_asset)
    assert dock.is_expanded
    assert dock.sheet.is_open

    dock.palette.close_palette()
    dock.close()
