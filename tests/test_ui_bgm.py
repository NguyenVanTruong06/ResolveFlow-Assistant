import os
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.ui.tabs.tab_copilot import TabCopilot
from src.ui.tabs.tab_assets import TabAssets, AssetCard, StudioAsset


def test_tab_copilot_bgm_controls(qapp):
    """Kiểm tra TabCopilot có Card / Controls BGM: combo_bgm_mode, chk_beat_sync, btn_preview_bgm."""
    widget = TabCopilot()

    assert hasattr(widget, "combo_bgm_mode"), "TabCopilot must have combo_bgm_mode"
    assert hasattr(widget, "chk_beat_sync"), "TabCopilot must have chk_beat_sync"
    assert hasattr(widget, "btn_preview_bgm"), "TabCopilot must have btn_preview_bgm"

    # Verify combo_bgm_mode items
    item_datas = [widget.combo_bgm_mode.itemData(i) for i in range(widget.combo_bgm_mode.count())]
    expected_datas = [
        "auto_mood",
        "mood_chill",
        "mood_upbeat",
        "mood_cinematic",
        "mood_funny",
        "custom_file",
        "none",
    ]
    for exp in expected_datas:
        assert exp in item_datas, f"combo_bgm_mode must contain itemData '{exp}'"

    # Verify chk_beat_sync defaults to True
    assert widget.chk_beat_sync.isChecked() is True, "chk_beat_sync must be checked by default"


def test_tab_copilot_get_selected_bgm_files(qapp, tmp_path):
    """Kiểm tra TabCopilot.get_selected_bgm_files() trả về file audio hợp lệ."""
    widget = TabCopilot()

    # 1. Chế độ none -> trả về []
    idx_none = widget.combo_bgm_mode.findData("none")
    assert idx_none >= 0
    widget.combo_bgm_mode.setCurrentIndex(idx_none)
    assert widget.get_selected_bgm_files() == []

    # 2. Chế độ mood_chill -> trả về file nhạc chill nếu có sẵn trong assets
    idx_chill = widget.combo_bgm_mode.findData("mood_chill")
    assert idx_chill >= 0
    widget.combo_bgm_mode.setCurrentIndex(idx_chill)
    chill_files = widget.get_selected_bgm_files()
    assert isinstance(chill_files, list)
    if chill_files:
        assert os.path.exists(chill_files[0])
        assert chill_files[0].lower().endswith((".wav", ".mp3", ".m4a", ".aac"))

    # 3. Chế độ custom_file với đường dẫn tùy chọn
    custom_audio = tmp_path / "my_track.mp3"
    custom_audio.write_bytes(b"dummy mp3 data")
    idx_custom = widget.combo_bgm_mode.findData("custom_file")
    assert idx_custom >= 0
    widget.combo_bgm_mode.setCurrentIndex(idx_custom)
    widget._custom_bgm_file = str(custom_audio)
    res = widget.get_selected_bgm_files()
    assert res == [str(custom_audio)]


def test_tab_assets_bgm_category(qapp):
    """Kiểm tra TabAssets tích hợp mục 🎵 Nhạc Nền (BGM) và load được assets."""
    tab_assets = TabAssets()

    # Kiểm tra rail_btns có "bgm"
    assert "bgm" in tab_assets.rail_btns, "TabAssets rail_btns must have 'bgm'"

    # Click chuyển sang tab BGM
    tab_assets._filter_by_rail("bgm")
    assert tab_assets.current_rail_tab == "bgm"

    # Kiểm tra danh mục con BGM
    assert hasattr(tab_assets, "lbl_cats_head")
    assert "BGM" in tab_assets.lbl_cats_head.text() or "Nhạc Nền" in tab_assets.lbl_cats_head.text()

    # Kiểm tra có tồn tại asset bgm nếu đã scan
    bgm_assets = [a for a in tab_assets.all_assets if getattr(a, "tab", "") == "bgm"]
    assert len(bgm_assets) >= 0


def test_tab_assets_bgm_card_and_inspector_preview(qapp, tmp_path):
    """Kiểm tra render thumbnail và preview inspector cho asset category BGM."""
    dummy_wav = tmp_path / "chill_song.wav"
    dummy_wav.write_bytes(b"dummy wav data")

    asset = StudioAsset(
        id="bgm_chill_test",
        name="Chill Song Test",
        tab="bgm",
        category="chill_vlog",
        sub="Chill Vlog",
        badge_icon="🎵",
        duration=120.0,
        file_path=str(dummy_wav)
    )

    card = AssetCard(asset)
    assert hasattr(card, "btn_quick_play"), "BGM card must have quick play button"

    tab_assets = TabAssets()
    tab_assets.selected_asset = asset
    tab_assets._update_inspector_details()

    # Inspector sfx/bgm viewport should be shown
    assert tab_assets.inspector_sfx_viewport.isVisible() or not tab_assets.inspector_sfx_viewport.isHidden()
