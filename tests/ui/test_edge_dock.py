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
from PySide6.QtCore import Qt, QRect, QVariantAnimation

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

    dock.close()
    dock.palette.close()


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
    from src.core.branding import TRAY_TOOLTIP
    tray = ChunDVCTrayIcon()
    assert tray.toolTip() == TRAY_TOOLTIP
    assert tray.contextMenu() is not None

    menu_actions = [a.text() for a in tray.contextMenu().actions() if a.text()]
    assert any("Edge Dock" in a for a in menu_actions)
    assert any("AI Director" in a for a in menu_actions)
    assert any("Cài đặt" in a for a in menu_actions)
    assert any("Thoát" in a for a in menu_actions)


def test_edge_dock_left_right_position_and_settings_dialog(qapp):
    from src.ui.lux.shell.dock_settings_dialog import DockSettingsDialog

    dock = EdgeDockWindow()

    # 1. Mặc định hoặc chuyển sang bên phải
    dock.set_dock_edge("right")
    assert dock.dock_edge == "right"
    assert dock.header.btn_collapse.text() == "»"
    sx, sy, sw, sh = dock._get_screen_rect()
    geom_r = dock._calc_target_geometry(44)
    assert geom_r.x() == sx + sw - 44

    # 2. Chuyển sang bên trái
    dock.set_dock_edge("left")
    assert dock.dock_edge == "left"
    assert dock.header.btn_collapse.text() == "«"
    geom_l = dock._calc_target_geometry(44)
    assert geom_l.x() == sx

    # 3. Thử nghiệm hộp thoại DockSettingsDialog
    dlg = DockSettingsDialog(current_edge="left")
    assert dlg.rad_left.isChecked() is True

    received_edge = []
    dlg.dock_edge_changed.connect(lambda e: received_edge.append(e))

    dlg.rad_right.setChecked(True)
    assert received_edge[-1] == "right"

    dlg.rad_left.setChecked(True)
    assert received_edge[-1] == "left"

    # Reset defaults
    dlg.btn_reset.click()
    assert dlg.rad_right.isChecked() is True
    assert dlg.chk_auto_director.isChecked() is True

    dlg.close()

    # Đưa dock về lại bên phải chuẩn
    dock.set_dock_edge("right")
    dock.close()
    dock.palette.close()


def test_dock_header_overflow_at_360px_and_label_preservation(qapp):
    dock = EdgeDockWindow()
    dock._auto_collapse_on_leave = False
    dock.show()
    dock.open_dock(animated=False)
    qapp.processEvents()

    dock._update_dock_position(420, animated=False)
    qapp.processEvents()
    assert dock.header.btn_director.isVisible() is True
    assert dock.header.btn_studio.isVisible() is True
    assert dock.header.btn_settings.isVisible() is True
    assert dock.header.btn_overflow.isVisible() is False

    dock.header.set_page_label("Fusion", locked=True)
    assert "Fusion" in dock.header.btn_page_chip.text()
    assert "Khóa" in dock.header.btn_page_chip.text()

    dock._update_dock_position(360, animated=False)
    qapp.processEvents()
    assert dock.header.btn_director.isVisible() is False
    assert dock.header.btn_studio.isVisible() is False
    assert dock.header.btn_settings.isVisible() is False
    assert dock.header.btn_overflow.isVisible() is True

    assert "Fusion" in dock.header.btn_page_chip.text()
    assert "Khóa" in dock.header.btn_page_chip.text()

    dock.close()
    dock.palette.close()


def test_dock_suggest_block_reopen_and_horizontal_scroll(qapp):
    from src.ui.lux.context.special_blocks import HorizontalChipScrollArea
    dock = EdgeDockWindow()
    dock.show()
    dock.open_dock(animated=False)
    qapp.processEvents()

    block = dock.block_edit
    assert isinstance(block.scroll_chips, HorizontalChipScrollArea)
    assert block.container.isVisible() is True
    assert block.btn_reopen.isVisible() is False

    block.btn_close.click()
    qapp.processEvents()
    assert block.container.isVisible() is False
    assert block.btn_reopen.isVisible() is True

    block.btn_reopen.click()
    qapp.processEvents()
    assert block.container.isVisible() is True
    assert block.btn_reopen.isVisible() is False

    dock.close()
    dock.palette.close()


def test_dock_toggle_multi_cycle_snapshot_restore_and_guards(qapp):
    """Regression test: Ít nhất 2 chu kỳ toggle/open qua hotkey signal và strip button,
    bảo toàn category, search, scroll, selected asset và guards.
    """
    from src.ui.lux.shell.dock_state import DockState
    from PySide6.QtCore import QVariantAnimation

    dock = EdgeDockWindow(edge_side="right")
    dock._auto_collapse_on_leave = False
    dock.show()

    assets = [
        {"id": "a", "name": "A Whoosh", "category": "sfx"},
        {"id": "b", "name": "B Title", "category": "text"},
    ]
    dock.asset_model.set_assets(assets)
    dock.open_dock(animated=False)
    qapp.processEvents()

    # Chu kỳ 1: SFX + search 'Whoosh' + Asset A
    dock.category_nav.select_category("sfx")
    dock.header.txt_search.setText("Whoosh")
    dock.proxy_model.set_search_query("Whoosh")
    qapp.processEvents()

    src_a = dock.asset_model.index_of_asset_id("a")
    proxy_a = dock.proxy_model.mapFromSource(src_a)
    assert proxy_a.isValid()
    dock.grid.setCurrentIndex(proxy_a)
    dock._selected_asset = assets[0]
    dock.action_bar.set_asset(assets[0])

    # Thu gọn bằng hotkey signal -> Dock ẩn hoàn toàn (0px trên màn hình, không hiện bong bóng)
    dock.hotkey_service.dock_toggle_requested.emit()
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.bubble.isVisible() is False
    assert dock.peek_handle.isVisible() is False

    # Rê chuột lại gần cạnh phải -> mũi tên nhỏ nhô ra -> click mở lại toàn bộ Dock
    dock._get_screen_rect = lambda: (0, 0, 1920, 1080)
    dock.set_dock_edge("right")
    from PySide6.QtCore import QPoint
    assert dock._check_edge_proximity(QPoint(1914, 520)) is True
    assert dock.peek_handle.isVisible() is True

    dock.peek_handle.open_dock_requested.emit()
    if dock._anim.state() == QVariantAnimation.State.Running:
        dock._anim.setCurrentTime(dock._anim.totalDuration())
    qapp.processEvents()
    assert dock.peek_handle.isVisible() is False
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert dock.category_nav.current_category == "sfx"
    assert dock.header.txt_search.text() == "Whoosh"
    assert dock._selected_asset is not None and dock._selected_asset["id"] == "a"
    assert dock.grid.currentIndex().isValid()

    # --- CHU KỲ 2: TEXT + search rỗng '' + Asset B ---
    dock.category_nav.select_category("text")
    dock.header.clear_search()
    dock.proxy_model.set_search_query("")
    qapp.processEvents()

    src_b = dock.asset_model.index_of_asset_id("b")
    proxy_b = dock.proxy_model.mapFromSource(src_b)
    assert proxy_b.isValid()
    dock.grid.setCurrentIndex(proxy_b)
    dock._selected_asset = assets[1]
    dock.action_bar.set_asset(assets[1])

    # Thu gọn bằng hotkey signal lần 2 -> Dock ẩn hoàn toàn vào cạnh màn hình
    dock.hotkey_service.dock_toggle_requested.emit()
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.bubble.isVisible() is False
    assert dock.peek_handle.isVisible() is False

    # Rê chuột gần cạnh phải & click mũi tên mở lại lần 2
    assert dock._check_edge_proximity(QPoint(1916, 480)) is True
    assert dock.peek_handle.isVisible() is True
    dock.peek_handle.open_dock_requested.emit()
    if dock._anim.state() == QVariantAnimation.State.Running:
        dock._anim.setCurrentTime(dock._anim.totalDuration())
    qapp.processEvents()
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert dock.category_nav.current_category == "text"
    assert dock.header.txt_search.text() == ""
    assert dock._selected_asset is not None and dock._selected_asset["id"] == "b"

    # --- CHU KỲ 3: Cập nhật model & Xóa asset khi đang thu gọn ---
    dock.collapse_dock(to_strip=True, animated=False)
    qapp.processEvents()
    # Sửa metadata asset b
    dock.asset_model.set_assets([
        {"id": "a", "name": "A Whoosh", "category": "sfx"},
        {"id": "b", "name": "B Title Updated", "category": "text"},
    ])
    qapp.processEvents()
    dock.open_dock(animated=False)
    qapp.processEvents()
    assert dock._selected_asset is not None
    assert dock._selected_asset["name"] == "B Title Updated"

    # Xóa asset b khỏi model khi đang thu gọn
    dock.collapse_dock(to_strip=True, animated=False)
    qapp.processEvents()
    dock.asset_model.set_assets([
        {"id": "a", "name": "A Whoosh", "category": "sfx"},
    ])
    qapp.processEvents()
    dock.open_dock(animated=False)
    qapp.processEvents()
    assert dock._selected_asset is None
    assert dock.action_bar._current_asset is None
    assert dock.action_bar.btn_primary.isEnabled() is False

    # Guards test
    dock.fsm._is_in_dock = False
    assert dock.fsm.can_auto_collapse() is True
    dock.fsm.set_dragging(True)
    assert dock.fsm.can_auto_collapse() is False
    dock.fsm.set_dragging(False)

    dock.fsm.set_menu_open(True)
    assert dock.fsm.can_auto_collapse() is False
    dock.fsm.set_menu_open(False)

    dock.header.btn_pin.click()
    assert dock.fsm.is_pinned is True
    assert dock.fsm.can_auto_collapse() is False

    dock._anim.stop()
    dock.close()
    dock.palette.close()


def test_dock_builtin_presets_multi_cycle_restore_and_filter_change(qapp):
    """Regression test: Dùng BUILTIN_PRESETS thật (TextStylePreset), chọn -> hotkey thu ->
    strip mở lặp lại ít nhất 2 chu kỳ, assert _selected_asset là dict và đúng ID,
    sau khi reopen đổi bộ lọc chứng minh _on_model_filter_changed không AttributeError và deselect đúng.
    """
    from src.core.text_preset import BUILTIN_PRESETS, TextStylePreset
    from src.ui.lux.shell.dock_state import DockState
    from PySide6.QtCore import QVariantAnimation

    if len(BUILTIN_PRESETS) >= 2:
        preset1 = BUILTIN_PRESETS[0]
        preset2 = BUILTIN_PRESETS[1]
    else:
        preset1 = TextStylePreset(id="txt_preset_1", name="Karaoke Pop", category="kinetic", description="Karaoke")
        preset2 = TextStylePreset(id="txt_preset_2", name="Clean Sub", category="clean_minimal", description="Clean")

    dock = EdgeDockWindow(edge_side="right")
    dock._auto_collapse_on_leave = False
    dock.show()
    dock.set_assets([preset1, preset2])
    dock.open_dock(animated=False)
    qapp.processEvents()

    # Chọn preset 1 thật
    dock._on_asset_clicked(preset1)
    qapp.processEvents()
    assert isinstance(dock._selected_asset, dict)
    assert dock._selected_asset["id"] == preset1.id

    # --- CHU KỲ 1: Thu gọn qua hotkey -> ẩn hoàn toàn -> mở lại qua click mũi tên cạnh ---
    dock.hotkey_service.dock_toggle_requested.emit()
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.bubble.isVisible() is False
    assert dock.peek_handle.isVisible() is False

    dock.peek_handle.open_dock_requested.emit()
    if dock._anim.state() == QVariantAnimation.State.Running:
        dock._anim.setCurrentTime(dock._anim.totalDuration())
    qapp.processEvents()
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert isinstance(dock._selected_asset, dict), f"Phải là dict, không được là {type(dock._selected_asset)}"
    assert dock._selected_asset["id"] == preset1.id

    # --- CHU KỲ 2: Thu gọn qua hotkey -> mở lại qua click mũi tên cạnh lần 2 ---
    dock.hotkey_service.dock_toggle_requested.emit()
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.bubble.isVisible() is False

    dock.peek_handle.open_dock_requested.emit()
    if dock._anim.state() == QVariantAnimation.State.Running:
        dock._anim.setCurrentTime(dock._anim.totalDuration())
    qapp.processEvents()
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert isinstance(dock._selected_asset, dict), f"Chu kỳ 2 phải là dict, không được là {type(dock._selected_asset)}"
    assert dock._selected_asset["id"] == preset1.id

    # Chọn preset 2 và kiểm tra tiếp 1 chu kỳ
    dock._on_asset_clicked(preset2)
    qapp.processEvents()
    assert isinstance(dock._selected_asset, dict)
    assert dock._selected_asset["id"] == preset2.id

    dock.collapse_dock(to_strip=True, animated=False)
    qapp.processEvents()
    dock.open_dock(animated=False)
    qapp.processEvents()
    assert isinstance(dock._selected_asset, dict)
    assert dock._selected_asset["id"] == preset2.id

    # Đổi filter che preset sau reopen: không gây AttributeError và deselect chuẩn
    dock.proxy_model.set_category("sfx")
    dock._on_model_filter_changed()
    qapp.processEvents()
    assert dock._selected_asset is None
    assert dock.action_bar._current_asset is None

    dock._anim.stop()
    dock.close()
    dock.palette.close()


def test_dock_edge_auto_hide_and_proximity_peek_arrow(qapp):
    """Kiểm tra tính năng thu ẩn hoàn toàn EdgeDock vào cạnh màn hình:
    1. Khi thu nhỏ: Dock, Strip và Bong bóng đều ẩn hoàn toàn (0px chiếm dụng màn hình).
    2. Khi chuột tiến lại gần cạnh màn hình (<= 24px): Mũi tên nhỏ (EdgePeekHandle) nhô ra sát mép.
    3. Khi chuột ở góc trên cùng nút [X] cửa sổ: Không nhô mũi tên gây vướng.
    4. Khi chuột rời xa vùng cạnh (> 68px): Mũi tên tự động thụt ẩn hoàn toàn.
    5. Khi bấm chuột trái vào mũi tên: Mũi tên ẩn đi và mở toàn bộ thanh Dock 420px.
    6. Tương thích cả cạnh Phải ('right'), cạnh Trái ('left') và cờ tùy chọn '--bubble'.
    """
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    from src.ui.lux.shell.edge_peek_handle import EdgePeekHandle

    dock = EdgeDockWindow(edge_side="right")
    dock._auto_collapse_on_leave = False
    dock._get_screen_rect = lambda: (0, 0, 1920, 1080)
    dock.set_dock_edge("right")
    assert hasattr(dock, "peek_handle")
    assert isinstance(dock.peek_handle, EdgePeekHandle)

    # Kiểm tra Focus safety của mũi tên (không bao giờ cướp phím tắt của DaVinci Resolve)
    pflags = dock.peek_handle.windowFlags()
    assert bool(pflags & Qt.WindowType.FramelessWindowHint) is True
    assert bool(pflags & Qt.WindowType.WindowStaysOnTopHint) is True
    assert bool(pflags & Qt.WindowType.Tool) is True
    assert dock.peek_handle.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
    assert dock.peek_handle.focusPolicy() == Qt.FocusPolicy.NoFocus

    # 1. Ban đầu mở Dock 420px: Dock hiện, mũi tên & bong bóng đều ẩn
    dock.show()
    dock.open_dock(animated=False)
    qapp.processEvents()
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert dock.width() == 420
    assert dock.peek_handle.isVisible() is False
    assert dock.bubble.isVisible() is False

    # 2. Thu ẩn Dock vào cạnh phải: 0px trên màn hình khi chuột ở giữa màn hình
    dock.collapse_dock(to_strip=True, animated=False)
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.bubble.isVisible() is False
    assert dock.peek_handle.isVisible() is False
    assert dock._edge_poll_timer.isActive() is True

    # Chuột ở giữa màn hình (960, 540) -> vẫn ẩn hoàn toàn
    assert dock._check_edge_proximity(QPoint(960, 540)) is False
    assert dock.peek_handle.isVisible() is False

    # 3. Chuột ở sát góc trên cùng nút đóng [X] cửa sổ (1915, 12) -> KHÔNG nhô mũi tên gây vướng
    assert dock._check_edge_proximity(QPoint(1915, 12)) is False
    assert dock.peek_handle.isVisible() is False

    # 4. Chuột tiến sát cạnh phải (1912, 500) -> Mũi tên nhỏ nhô ra sát mép phải
    assert dock._check_edge_proximity(QPoint(1912, 500)) is True
    assert dock.peek_handle.isVisible() is True
    assert dock.peek_handle.x() == 1920 - dock.peek_handle.width()
    assert abs(dock.peek_handle.geometry().center().y() - 500) <= 4

    # 5. Chuột rời xa vùng cạnh phải về giữa timeline (1500, 500) -> Mũi tên tự động ẩn hoàn toàn
    assert dock._check_edge_proximity(QPoint(1500, 500)) is False
    assert dock.peek_handle.isVisible() is False

    # 6. Đưa chuột lại gần cạnh phải (1915, 560) rồi bấm chuột trái thật vào mũi tên -> Mở hẳn Dock 420px
    assert dock._check_edge_proximity(QPoint(1915, 560)) is True
    assert dock.peek_handle.isVisible() is True
    local_center = QPointF(dock.peek_handle.width() / 2, dock.peek_handle.height() / 2)
    global_center = QPointF(dock.peek_handle.mapToGlobal(local_center.toPoint()))
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        local_center,
        global_center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        local_center,
        global_center,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    dock.peek_handle.mousePressEvent(press_ev)
    dock.peek_handle.mouseReleaseEvent(release_ev)
    if dock._anim.state() == QVariantAnimation.State.Running:
        dock._anim.setCurrentTime(dock._anim.totalDuration())
    qapp.processEvents()

    assert dock.peek_handle.isVisible() is False
    assert dock.isVisible() is True
    assert dock.fsm.is_open is True
    assert dock.width() == 420

    # 7. Kiểm tra chế độ neo cạnh Trái ('left')
    dock.set_dock_edge("left")
    dock.collapse_dock(animated=False)
    qapp.processEvents()
    assert dock.isVisible() is False
    assert dock.peek_handle.isVisible() is False

    assert dock._check_edge_proximity(QPoint(6, 420)) is True
    assert dock.peek_handle.isVisible() is True
    assert dock.peek_handle.x() == 0
    assert dock.peek_handle.dock_edge == "left"

    # 8. Tương thích ngược với cờ --bubble khi được bật tường minh
    dock.set_dock_edge("right")
    dock.set_bubble_mode(True)
    dock.collapse_dock(animated=False)
    qapp.processEvents()
    assert dock.bubble.isVisible() is True
    assert dock.peek_handle.isVisible() is False

    # Dọn dẹp
    dock._anim.stop()
    dock.close()
    dock.palette.close()


def test_dock_special_container_adaptive_size_and_no_empty_gap(qapp):
    """Regression test: Đảm bảo special_container tự động thích ứng kích thước theo widget con hiện tại,
    không bị phình to bởi các trang lớn (như Deliver 233px) gây ra khoảng trống đen gần 200px.
    Grid tài nguyên phải nằm ngay sát dưới thanh Gợi ý thoại (khoảng cách = 0px).
    """
    from src.ui.lux.shell.edge_dock import AdaptiveStackedWidget

    dock = EdgeDockWindow(edge_side="right")
    dock._auto_collapse_on_leave = False
    assert isinstance(dock.special_container, AdaptiveStackedWidget)

    dock.show()
    dock.open_dock(animated=False)
    qapp.processEvents()

    # 1. Trang Edit: special_container chỉ cao bằng EditSuggestBlock (36-38px)
    assert dock.special_container.height() <= 38
    assert dock.special_container.currentWidget() == dock.block_edit

    # 2. Không có khoảng trống (Gap = 0) giữa thanh gợi ý và Grid tài nguyên
    gap = dock.grid.y() - (dock.special_container.y() + dock.special_container.height())
    assert gap == 0, f"Phát hiện khoảng trống rỗng {gap}px giữa thanh gợi ý và grid!"

    # 3. Khi đóng thanh gợi ý (✕), container tự co lại 24px và grid tự động kéo lên
    dock.block_edit.btn_close.click()
    qapp.processEvents()
    assert dock.special_container.height() <= 26
    gap_closed = dock.grid.y() - (dock.special_container.y() + dock.special_container.height())
    assert gap_closed == 0

    # 4. Khi mở lại thanh gợi ý (✨ Gợi ý), container dãn ra 36-38px và grid duy trì gap = 0
    dock.block_edit.btn_reopen.click()
    qapp.processEvents()
    assert dock.special_container.height() <= 38
    gap_reopened = dock.grid.y() - (dock.special_container.y() + dock.special_container.height())
    assert gap_reopened == 0

    dock.close()
    dock.palette.close()

