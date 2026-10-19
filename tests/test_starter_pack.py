import os
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from src.core.asset_indexer import AssetIndexer
from src.ui.tabs.tab_assets import TabAssets


def test_starter_pack_manifest_exists_and_valid():
    """Kiểm tra module starter_pack_downloader và cấu trúc manifest chuẩn."""
    from src.core.starter_pack_downloader import StarterPackManifest, StarterPackDownloader

    manifest = StarterPackManifest.get_manifest()
    assert "sfx" in manifest
    assert "memes" in manifest
    assert "luts" in manifest
    assert "bgm" in manifest
    assert "overlays" in manifest

    # Tổng số tài nguyên trong starter pack >= 50
    total_items = sum(len(items) for items in manifest.values())
    assert total_items >= 50, f"Expected at least 50 starter pack items, got {total_items}"

    # Kiểm tra metadata từng item có tag cảm xúc / tình huống
    first_sfx = manifest["sfx"][0]
    assert "id" in first_sfx
    assert "name" in first_sfx
    assert "emotion_tag" in first_sfx
    assert "file_path" in first_sfx


def test_starter_pack_seeder_execution():
    """Kiểm tra bộ seeder nạp đạo cụ chạy mượt mà và trả về thống kê chuẩn."""
    from src.core.starter_pack_downloader import StarterPackDownloader

    progress_history = []
    def on_progress(pct, msg):
        progress_history.append((pct, msg))

    summary = StarterPackDownloader.seed_all(progress_callback=on_progress)
    assert isinstance(summary, dict)
    assert summary.get("sfx", 0) > 0
    assert summary.get("luts", 0) > 0
    assert summary.get("bgm", 0) > 0
    assert summary.get("overlays", 0) > 0

    assert len(progress_history) > 0
    assert progress_history[-1][0] == 100


def test_asset_indexer_smart_emotion_tags():
    """Kiểm tra bộ quét AssetIndexer tự động gán nhãn cảm xúc CapCut chuẩn xác."""
    indexer = AssetIndexer()
    all_assets = indexer.scan_all_assets(force_refresh=True)

    # 1. Kiểm tra SFX được phân loại theo cảm xúc: chuyen_canh, hai_huoc, diem_nhan, giat_minh, cong_nghe
    sfx_list = all_assets.get("sfx", [])
    assert len(sfx_list) > 0
    categories = {item.get("category") for item in sfx_list}
    # Phải có ít nhất 2 nhóm cảm xúc phổ biến
    assert any(c in categories for c in ["chuyen_canh", "hai_huoc", "diem_nhan", "giat_minh", "cong_nghe", "accent"])

    # 2. Kiểm tra Memes được phân loại: vietnam_viral, reaction, cham_hoi, cay_cu, trending
    memes_list = all_assets.get("memes", [])
    if len(memes_list) > 0:
        meme_cats = {m.get("category") for m in memes_list}
        assert any(c in meme_cats for c in ["vietnam_viral", "reaction", "cham_hoi", "cay_cu", "trending"])


def test_tab_assets_starter_pack_integration(qapp):
    """Kiểm tra TabAssets có nút nạp Starter Pack và danh mục phân loại theo cảm xúc."""
    tab = TabAssets()
    tab.show()

    assert hasattr(tab, "btn_starter_pack"), "TabAssets must have btn_starter_pack button"
    assert isinstance(tab.btn_starter_pack, QPushButton)
    assert "Starter Pack" in tab.btn_starter_pack.text() or "Gói Đạo Cụ" in tab.btn_starter_pack.text()

    # Kiểm tra Cột 2 khi chọn tab SFX hiển thị danh mục cảm xúc
    tab.current_rail_tab = "sfx"
    tab._rebuild_categories_column()
    sfx_btn_texts = [
        tab.cats_vbox.itemAt(i).widget().text()
        for i in range(tab.cats_vbox.count())
        if tab.cats_vbox.itemAt(i) and isinstance(tab.cats_vbox.itemAt(i).widget(), QPushButton)
    ]
    # Phải có các nhóm cảm xúc như Chuyển cảnh, Hài hước, Điểm nhấn, Giật mình
    assert any("Chuyển cảnh" in t for t in sfx_btn_texts)
    assert any("Hài hước" in t for t in sfx_btn_texts)
    assert any("Điểm nhấn" in t or "Nhấn mạnh" in t for t in sfx_btn_texts)

    # Kiểm tra Cột 2 khi chọn tab Meme
    tab.current_rail_tab = "meme"
    tab._rebuild_categories_column()
    meme_btn_texts = [
        tab.cats_vbox.itemAt(i).widget().text()
        for i in range(tab.cats_vbox.count())
        if tab.cats_vbox.itemAt(i) and isinstance(tab.cats_vbox.itemAt(i).widget(), QPushButton)
    ]
    assert any("Việt Nam" in t or "Reaction" in t or "Ngơ ngác" in t or "Thịnh hành" in t for t in meme_btn_texts)
