"""
tests/ui/test_empty_library_startup.py
Kiểm thử khởi động và làm mới khi kho tài nguyên rỗng (LIB-PUBLISH-02B):
- load_all_library_assets và TabAssets không tự sinh 24 text styles, 4 transitions hay MOCKUP data
- Không tạo cards giả hoặc reseed demo files vào đĩa
- Selected asset = None, các thao tác copy/paste/insert an toàn với empty state tiếng Việt
- Áp dụng snapshot và dọn rỗng hoạt động chuẩn xác, không có hiệu ứng lặp hay rò rỉ state
"""

import os
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from src.main import load_all_library_assets
from src.core.text_preset import BUILTIN_PRESETS
from src.core.transition_preset import BUILTIN_TRANSITIONS
from src.core.storage_manager import StorageManager
from src.ui.tabs.tab_assets import TabAssets, StudioAsset, MOCKUP_LUTS, MOCKUP_MEMES, MOCKUP_SFX, MOCKUP_OVERLAYS, MOCKUP_ICONS


def test_builtin_catalogs_are_empty():
    """Xác nhận toàn bộ catalogs demo mặc định đã được dọn sạch khỏi production runtime."""
    assert BUILTIN_PRESETS == [], "BUILTIN_PRESETS phải là list rỗng trong production"
    assert BUILTIN_TRANSITIONS == [], "BUILTIN_TRANSITIONS phải là list rỗng trong production"
    assert MOCKUP_LUTS == [], "MOCKUP_LUTS phải là list rỗng"
    assert MOCKUP_MEMES == [], "MOCKUP_MEMES phải là list rỗng"
    assert MOCKUP_SFX == [], "MOCKUP_SFX phải là list rỗng"
    assert MOCKUP_OVERLAYS == [], "MOCKUP_OVERLAYS phải là list rỗng"
    assert MOCKUP_ICONS == [], "MOCKUP_ICONS phải là list rỗng"


def test_load_all_library_assets_no_builtin_leakage(monkeypatch, tmp_path):
    """load_all_library_assets không chứa bất kỳ preset demo hay mockup cũ nào."""
    # Giả lập môi trường indexer rỗng
    class DummyIndexer:
        def scan_memes(self): return []
        def scan_sfx(self): return []
        def scan_luts(self): return []
        def scan_overlays(self): return []
        def scan_titles(self): return []
        def scan_transitions(self): return []
        def scan_music_assets(self): return {}

    monkeypatch.setattr("src.main.AssetIndexer", DummyIndexer)
    
    dummy_storage = StorageManager(tmp_path / "cfg", reserve_bytes=0)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: dummy_storage)

    assets = load_all_library_assets()
    assert isinstance(assets, list)
    # Không có ID demo nào xuất hiện
    demo_ids = {"kinetic_hormozi", "whip_pan_left", "clean_rec709", "highlighter_swipe"}
    loaded_ids = {a.get("id") if isinstance(a, dict) else getattr(a, "id", "") for a in assets}
    assert demo_ids.isdisjoint(loaded_ids), f"Phát hiện rò rỉ ID demo trong load_all_library_assets: {demo_ids & loaded_ids}"


def test_tab_assets_empty_startup_and_rescan(qapp, tmp_path, monkeypatch):
    """TabAssets khởi động với kho rỗng không tự động tạo cards giả hay reseed."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))

    class CleanIndexer:
        @classmethod
        def clear_cache(cls): pass
        def scan_titles(self): return []
        def scan_transitions(self): return []
        def scan_luts(self): return []
        def scan_memes(self): return []
        def scan_sfx(self): return []
        def scan_overlays(self): return []
        def scan_music_assets(self): return {}
        def get_linked_folders(self): return []
        def _find_thumbnail(self, p): return None

    monkeypatch.setattr("src.ui.tabs.tab_assets.AssetIndexer", CleanIndexer)

    tab = TabAssets(headless=False)
    try:
        assert len(tab.all_assets) == 0, f"Kho tài nguyên phải rỗng, nhưng có {len(tab.all_assets)} assets"
        assert tab.selected_asset is None

        # Rescan không sinh ra tài nguyên demo
        tab._rescan_assets()
        assert len(tab.all_assets) == 0
        assert tab.selected_asset is None
    finally:
        if hasattr(tab, "toast_timer") and tab.toast_timer.isActive():
            tab.toast_timer.stop()
        if hasattr(tab, "sfx_playback_timer") and tab.sfx_playback_timer.isActive():
            tab.sfx_playback_timer.stop()
        tab.close()


def test_tab_assets_empty_state_action_guards(qapp, tmp_path, monkeypatch):
    """Khi kho rỗng, các hành động xuất Fusion macro hoặc copy an toàn, có thông báo tiếng Việt."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))

    class CleanIndexer:
        @classmethod
        def clear_cache(cls): pass
        def scan_titles(self): return []
        def scan_transitions(self): return []
        def scan_luts(self): return []
        def scan_memes(self): return []
        def scan_sfx(self): return []
        def scan_overlays(self): return []
        def scan_music_assets(self): return {}
        def get_linked_folders(self): return []
        def _find_thumbnail(self, p): return None

    monkeypatch.setattr("src.ui.tabs.tab_assets.AssetIndexer", CleanIndexer)

    tab = TabAssets(headless=False)
    try:
        toasts = []
        tab._show_toast = lambda msg: toasts.append(msg)

        # 1. Thử copy Fusion khi chưa chọn asset
        tab._on_copy_fusion_clicked()
        assert any("Chưa chọn tài nguyên" in t for t in toasts), f"Thiếu thông báo chưa chọn tài nguyên: {toasts}"

        # 2. Thử fast paste Fusion khi chưa chọn asset
        toasts.clear()
        tab._on_fast_paste_fusion_clicked()
        assert any("Chưa chọn tài nguyên" in t for t in toasts)
    finally:
        if hasattr(tab, "toast_timer") and tab.toast_timer.isActive():
            tab.toast_timer.stop()
        if hasattr(tab, "sfx_playback_timer") and tab.sfx_playback_timer.isActive():
            tab.sfx_playback_timer.stop()
        tab.close()


def test_tab_assets_custom_snapshot_on_empty_library(qapp, tmp_path, monkeypatch):
    """Kho rỗng nạp được snapshot người dùng hợp lệ, sau đó dọn sạch về rỗng."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))

    class CleanIndexer:
        @classmethod
        def clear_cache(cls): pass
        def scan_titles(self): return []
        def scan_transitions(self): return []
        def scan_luts(self): return []
        def scan_memes(self): return []
        def scan_sfx(self): return []
        def scan_overlays(self): return []
        def scan_music_assets(self): return {}
        def get_linked_folders(self): return []
        def _find_thumbnail(self, p): return None

    monkeypatch.setattr("src.ui.tabs.tab_assets.AssetIndexer", CleanIndexer)

    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    tab = TabAssets(headless=False)
    tab.storage = storage
    try:
        assert len(tab.all_assets) == 0

        # Áp dụng snapshot với 2 assets hợp lệ
        sample_snapshot = [
            {"id": "usr_sfx_01", "name": "Whoosh Clean", "tab": "sfx", "category": "custom_pack", "sub": "Whoosh"},
            {"id": "usr_lut_01", "name": "Vibrant Sun", "tab": "lut", "category": "custom_pack", "sub": "Sunny"}
        ]
        tab.apply_storage_snapshot(managed_assets=sample_snapshot, taxonomy={})
        assert len(tab.all_assets) == 2
        assert {a.id for a in tab.all_assets} == {"usr_sfx_01", "usr_lut_01"}

        # Dọn sạch snapshot
        tab.apply_storage_snapshot(managed_assets=[], taxonomy={})
        assert len(tab.all_assets) == 0
    finally:
        if hasattr(tab, "toast_timer") and tab.toast_timer.isActive():
            tab.toast_timer.stop()
        if hasattr(tab, "sfx_playback_timer") and tab.sfx_playback_timer.isActive():
            tab.sfx_playback_timer.stop()
        tab.close()
