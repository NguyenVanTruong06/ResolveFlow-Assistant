import pytest
from PySide6.QtWidgets import QApplication
from src.ui.studio.studio_window import StudioWindow
from src.ui.tabs.tab_assets import TabAssets, StudioAsset


@pytest.fixture
def studio_window(qapp):
    win = StudioWindow()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_studio_window_initialization(studio_window):
    win = studio_window
    assert win is not None
    assert "Kho Đạo Cụ" in win.windowTitle()
    assert win.tab_assets is not None
    assert win.btn_pin is not None


def test_studio_window_rail_tabs(studio_window):
    win = studio_window
    tab_assets = win.tab_assets

    # 1. Kiểm tra đủ 9 nút điều hướng trên Rail (đã thêm tab Nhạc BGM)
    assert len(tab_assets.rail_btns) == 9
    assert "text" in tab_assets.rail_btns
    assert "lut" in tab_assets.rail_btns
    assert "trans" in tab_assets.rail_btns
    assert "icon" in tab_assets.rail_btns
    assert "sfx" in tab_assets.rail_btns
    assert "bgm" in tab_assets.rail_btns
    assert "fav" in tab_assets.rail_btns

    # 2. Chuyển sang tab Màu (LUT)
    tab_assets._filter_by_rail("lut")
    assert tab_assets.current_rail_tab == "lut"
    assert "LUT" in tab_assets.lbl_cats_head.text()

    # 3. Chuyển sang tab Âm thanh (SFX)
    tab_assets._filter_by_rail("sfx")
    assert tab_assets.current_rail_tab == "sfx"
    assert "Âm thanh" in tab_assets.lbl_cats_head.text()


def test_studio_window_card_selection_and_inspector(studio_window):
    win = studio_window
    tab_assets = win.tab_assets

    # Mặc định đang ở tab Text
    tab_assets._filter_by_rail("text")
    tab_assets._on_card_selected("kinetic_hormozi")

    assert tab_assets.selected_asset is not None
    assert tab_assets.selected_asset.id == "kinetic_hormozi"
    assert "Alex Hormozi" in tab_assets.lbl_insp_title.text()

    # Nhập text mới và kiểm tra preview cập nhật
    tab_assets.txt_single_title.setText("ResolveFlow Test")
    preview_html = tab_assets.lbl_insp_preview.text()
    assert "ResolveFlow" in preview_html and "Test" in preview_html


def test_studio_window_insert_signal(studio_window):
    win = studio_window
    tab_assets = win.tab_assets

    events = []
    tab_assets.insert_title_requested.connect(lambda txt, pid, dur: events.append((txt, pid, dur)))

    tab_assets.txt_single_title.setText("Tiêu đề thử nghiệm")
    tab_assets.slide_title_dur.setValue(50)  # 5.0s
    tab_assets.btn_insert_title_playhead.click()

    assert len(events) == 1
    assert events[0][0] == "Tiêu đề thử nghiệm"
    assert events[0][2] == 5.0


def test_studio_window_pin_toggle(studio_window):
    win = studio_window
    win.btn_pin.setChecked(True)
    win._toggle_always_on_top()
    win.btn_pin.setChecked(False)
    win._toggle_always_on_top()


def test_cross_window_navigation_signals(qapp):
    from src.ui.auto.auto_window import AutoWindow
    auto_win = AutoWindow()
    studio_win = StudioWindow()

    assert hasattr(auto_win, "btn_switch_studio")
    assert hasattr(studio_win, "btn_switch_auto")
    assert auto_win.btn_switch_studio.property("role") == "nav-switch"
    assert studio_win.btn_switch_auto.property("role") == "nav-switch"

    auto_signals = []
    studio_signals = []
    auto_win.open_studio_requested.connect(lambda: auto_signals.append(True))
    studio_win.open_auto_requested.connect(lambda: studio_signals.append(True))

    auto_win.btn_switch_studio.click()
    studio_win.btn_switch_auto.click()

    assert len(auto_signals) == 1
    assert len(studio_signals) == 1

    auto_win.close()
    auto_win.deleteLater()
    studio_win.close()
    studio_win.deleteLater()
    qapp.processEvents()
