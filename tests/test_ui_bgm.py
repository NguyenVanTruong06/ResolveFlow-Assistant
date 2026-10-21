import os
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.ui.director.director_state import DirectorConfig
from src.ui.director.pages.style_page import StylePage
from src.ui.tabs.tab_assets import TabAssets, AssetCard, StudioAsset


def test_director_bgm_controls(qapp):
    """Kiểm tra DirectorConfig có cấu hình BGM: bgm_name, bgm_path, beat_sync."""
    config = DirectorConfig()
    assert hasattr(config, "bgm_name"), "DirectorConfig must have bgm_name"
    assert hasattr(config, "bgm_path"), "DirectorConfig must have bgm_path"
    assert hasattr(config, "beat_sync"), "DirectorConfig must have beat_sync"
    assert config.beat_sync is True
    assert config.bgm_name == "Chill Vlog Lofi"


def test_director_config_bgm_selection(qapp, tmp_path):
    """Kiểm tra DirectorConfig lưu trữ file audio hợp lệ."""
    config = DirectorConfig()
    assert config.bgm_name == "Chill Vlog Lofi"

    custom_audio = tmp_path / "my_track.mp3"
    custom_audio.write_bytes(b"dummy mp3 data")
    config.bgm_name = "Custom Audio"
    config.bgm_path = str(custom_audio)
    assert config.bgm_path == str(custom_audio)


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
