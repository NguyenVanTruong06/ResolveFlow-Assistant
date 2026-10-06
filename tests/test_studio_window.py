import pytest
from PySide6.QtWidgets import QApplication
from src.ui.studio.studio_window import StudioWindow
from src.ui.tabs.tab_assets import TabAssets, StudioAsset


def test_studio_window_initialization(qapp):
    win = StudioWindow()
    assert win is not None
    assert "Kho Đạo Cụ" in win.windowTitle()
    assert win.tab_assets is not None
    assert win.btn_pin is not None


def test_studio_window_rail_tabs(qapp):
    win = StudioWindow()
    tab_assets = win.tab_assets

    # 1. Kiểm tra đủ 8 nút điều hướng trên Rail
    assert len(tab_assets.rail_btns) == 8
    assert "text" in tab_assets.rail_btns
    assert "lut" in tab_assets.rail_btns
    assert "trans" in tab_assets.rail_btns
    assert "icon" in tab_assets.rail_btns
    assert "sfx" in tab_assets.rail_btns
    assert "fav" in tab_assets.rail_btns

    # 2. Chuyển sang tab Màu (LUT)
    tab_assets._filter_by_rail("lut")
    assert tab_assets.current_rail_tab == "lut"
    assert "LUT" in tab_assets.lbl_cats_head.text()

    # 3. Chuyển sang tab Âm thanh (SFX)
    tab_assets._filter_by_rail("sfx")
    assert tab_assets.current_rail_tab == "sfx"
    assert "Âm thanh" in tab_assets.lbl_cats_head.text()


def test_studio_window_card_selection_and_inspector(qapp):
    win = StudioWindow()
    tab_assets = win.tab_assets

    # Mặc định đang ở tab Text
    tab_assets._filter_by_rail("text")
    tab_assets._on_card_selected("kinetic_hormozi")

    assert tab_assets.selected_asset is not None
    assert tab_assets.selected_asset.id == "kinetic_hormozi"
    assert "Alex Hormozi" in tab_assets.lbl_insp_title.text()

    # Nhập text mới và kiểm tra preview cập nhật
    tab_assets.txt_single_title.setText("ResolveFlow Test")
    assert tab_assets.lbl_insp_preview.text() == "ResolveFlow Test"


def test_studio_window_insert_signal(qapp):
    win = StudioWindow()
    tab_assets = win.tab_assets

    events = []
    tab_assets.insert_title_requested.connect(lambda txt, pid, dur: events.append((txt, pid, dur)))

    tab_assets.txt_single_title.setText("Tiêu đề thử nghiệm")
    tab_assets.slide_title_dur.setValue(50)  # 5.0s
    tab_assets.btn_insert_title_playhead.click()

    assert len(events) == 1
    assert events[0][0] == "Tiêu đề thử nghiệm"
    assert events[0][2] == 5.0


def test_studio_window_pin_toggle(qapp):
    win = StudioWindow()
    win.btn_pin.setChecked(True)
    win._toggle_always_on_top()
    win.btn_pin.setChecked(False)
    win._toggle_always_on_top()
