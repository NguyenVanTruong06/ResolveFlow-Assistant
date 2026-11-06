"""
tests/ui/test_dock_profile_taxonomy.py
Focused regression test suite for EdgeDockWindow taxonomy and profile synchronization.
Task: G04-04A1-I — Giữ category tùy biến qua Context Profile.
"""
from typing import Dict, Any, List
import pytest
from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.context.context_profiles import PROFILES
from src.core.storage_manager import StorageManager


@pytest.fixture
def dock(qapp, monkeypatch):
    """Fixture khởi tạo EdgeDockWindow an toàn và dọn dẹp widget sau test."""
    dock_instance = EdgeDockWindow()
    yield dock_instance
    dock_instance.close()


def test_custom_category_persists_across_profiles(dock, monkeypatch):
    """1. custom tồn tại sau set_assets -> Edit -> Color -> Fairlight -> profile trước."""
    custom_tax = {"custom_pack_1": "Gói Tùy Biến 1"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    sample_assets = [
        {"id": "a1", "name": "Asset Custom", "tab": "custom_pack_1", "category": "custom_pack_1", "storage_managed": True}
    ]
    dock.set_assets(sample_assets)

    # Đang ở Edit (mặc định)
    cat_ids_edit = [c["id"] for c in dock.category_nav._categories]
    assert "custom_pack_1" in cat_ids_edit
    assert cat_ids_edit[:6] == PROFILES["edit"].categories

    # Chuyển sang Color (chỉ có lut) -> custom_pack_1 vẫn phải ở cuối
    dock._on_profile_changed(PROFILES["color"])
    cat_ids_color = [c["id"] for c in dock.category_nav._categories]
    assert "custom_pack_1" in cat_ids_color
    assert cat_ids_color[0] == "lut"

    # Chuyển sang Fairlight (sfx, bgm) -> custom_pack_1 vẫn tồn tại
    dock._on_profile_changed(PROFILES["fairlight"])
    cat_ids_fairlight = [c["id"] for c in dock.category_nav._categories]
    assert "custom_pack_1" in cat_ids_fairlight
    assert cat_ids_fairlight[:2] == ["sfx", "bgm"]

    # Chuyển quay lại Edit -> custom_pack_1 vẫn nguyên vẹn
    dock._on_profile_changed(PROFILES["edit"])
    cat_ids_edit_back = [c["id"] for c in dock.category_nav._categories]
    assert "custom_pack_1" in cat_ids_edit_back
    assert cat_ids_edit_back[:6] == PROFILES["edit"].categories


def test_taxonomy_empty_custom_visible_without_assets(dock, monkeypatch):
    """2. taxonomy rỗng asset vẫn thấy custom chip trên nav."""
    custom_tax = {"empty_custom": "Danh Mục Trống Mới"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    dock.set_assets([])  # Không có asset nào trong kho

    cat_defs = {c["id"]: c for c in dock.category_nav._categories}
    assert "empty_custom" in cat_defs
    assert cat_defs["empty_custom"]["label"] == "Danh Mục Trống Mới"
    assert cat_defs["empty_custom"]["subs"] == ["Tất cả"]


def test_two_ids_with_same_label_not_merged(dock, monkeypatch):
    """3. hai ID khác nhau cùng label tiếng Việt không bị gộp (loại trùng theo ID)."""
    custom_tax = {
        "winter_deal": "Khuyến Mãi Đặc Biệt",
        "summer_deal": "Khuyến Mãi Đặc Biệt",
    }
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    dock.set_assets([])

    cat_defs = dock.category_nav._categories
    matching_chips = [c for c in cat_defs if c["label"] == "Khuyến Mãi Đặc Biệt"]
    assert len(matching_chips) == 2
    ids = {c["id"] for c in matching_chips}
    assert ids == {"winter_deal", "summer_deal"}


def test_canonical_aliases_do_not_create_duplicate_chips(dock, monkeypatch):
    """4. canonical storage aliases (overlays, memes, luts, templates) không tạo chip custom trùng."""
    canonical_tax = {
        "overlays": "Lớp phủ",
        "memes": "Meme",
        "luts": "Màu LUT",
        "templates": "Mẫu Fusion",
    }
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(canonical_tax))

    # Kèm theo cả asset mang tab canonical
    dock.set_assets([
        {"id": "o1", "name": "O1", "tab": "overlays", "category": "overlays"},
        {"id": "t1", "name": "T1", "tab": "templates", "category": "templates"},
    ])

    cat_ids = [c["id"] for c in dock.category_nav._categories]
    assert "overlays" not in cat_ids
    assert "memes" not in cat_ids
    assert "luts" not in cat_ids
    assert "templates" not in cat_ids
    # Các chip chuẩn UI chỉ xuất hiện đúng 1 lần
    assert cat_ids.count("overlay") == 1
    assert cat_ids.count("meme") == 1
    assert cat_ids.count("lut") == 1
    assert cat_ids.count("text") == 1
    assert cat_ids.count("trans") == 1


def test_vietnamese_labels_subs_and_tags_preserved(dock, monkeypatch):
    """5. nhãn tiếng Việt nguyên bản, sub và tags tổng hợp từ assets không bị mất."""
    custom_tax = {"social_media": "Mạng Xã Hội Đẹp"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    dock.set_assets([
        {
            "id": "s1",
            "name": "TikTok Transition",
            "tab": "social_media",
            "category": "social_media",
            "sub": "TikTok",
            "tags": ["Xu hướng", "Viral"],
            "storage_managed": True,
        },
        {
            "id": "s2",
            "name": "Reels Intro",
            "tab": "social_media",
            "category": "social_media",
            "sub": "Reels",
            "tags": ["Viral", "Shorts"],
            "storage_managed": True,
        },
    ])

    cat_defs = {c["id"]: c for c in dock.category_nav._categories}
    assert "social_media" in cat_defs
    def_social = cat_defs["social_media"]
    assert def_social["label"] == "Mạng Xã Hội Đẹp"
    expected_subs = ["Tất cả", "TikTok", "Xu hướng", "Viral", "Reels", "Shorts"]
    assert def_social["subs"] == expected_subs


def test_saved_custom_category_restored_and_missing_fallback_synced(dock, monkeypatch):
    """6. saved custom được restore, missing custom fallback đồng bộ nav và proxy_model."""
    custom_tax = {"project_alpha": "Dự Án Alpha"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    dock.set_assets([
        {
            "id": "pa1",
            "name": "Alpha 1",
            "tab": "project_alpha",
            "category": "project_alpha",
            "sub": "Ep01",
            "tags": ["Cut1"],
            "storage_managed": True,
        }
    ])

    # Chọn category custom và subcategory
    dock.category_nav.select_category("project_alpha")
    dock.category_nav.select_subcategory("Ep01")
    assert dock.category_nav.current_category == "project_alpha"
    assert dock.proxy_model.filter_category() == "project_alpha"
    assert dock.proxy_model.filter_subcategory() == "Ep01"

    # Chuyển sang Color rồi quay về Edit: trạng thái phải được restore
    dock._on_profile_changed(PROFILES["color"])
    dock._on_profile_changed(PROFILES["edit"])
    assert dock.category_nav.current_category == "project_alpha"
    assert dock.proxy_model.filter_category() == "project_alpha"
    assert dock.category_nav.current_subcategory == "Ep01"
    assert dock.proxy_model.filter_subcategory() == "Ep01"

    # Test Missing Custom Category Fallback:
    # 1. Chuyển sang Color trước
    dock._on_profile_changed(PROFILES["color"])
    # 2. Giả lập trang Edit đã lưu 1 category không còn tồn tại trong taxonomy (bị xóa hoặc mất)
    if hasattr(dock.router, "_memory"):
        dock.router._memory.save("edit", "ghost_category", "ghost_sub", 0)
    # 3. Chuyển về Edit để kích hoạt restore saved state của Edit
    dock._on_profile_changed(PROFILES["edit"])

    # Phải fallback có chủ ý về category đầu tiên của effective list ("text")
    assert dock.category_nav.current_category == "text"
    assert dock.proxy_model.filter_category() == "text"
    assert dock.category_nav.current_subcategory in ("Tất cả", "all")
    assert dock.proxy_model.filter_subcategory() in ("Tất cả", "all")


def test_empty_library_profile_hides_nav_and_grid(dock):
    """7. profile không có library (media, deliver) vẫn tiếp tục ẩn nav và grid."""
    # Chuyển sang Media: categories == []
    dock._on_profile_changed(PROFILES["media"])
    assert dock.category_nav.isHidden() is True
    assert dock.grid.isHidden() is True
    assert dock.special_container.isHidden() is False

    # Chuyển sang Deliver: categories == []
    dock._on_profile_changed(PROFILES["deliver"])
    assert dock.category_nav.isHidden() is True
    assert dock.grid.isHidden() is True
    assert dock.special_container.isHidden() is False

    # Chuyển sang Edit: categories có thư viện -> hiện lại
    dock._on_profile_changed(PROFILES["edit"])
    assert dock.category_nav.isHidden() is False
    assert dock.grid.isHidden() is False


def test_offline_taxonomy_uses_snapshot(dock, monkeypatch):
    """8. offline taxonomy đọc từ snapshot dict khi StorageManager.online == False."""
    snapshot_tax = {"offline_vault": "Kho Ngoại Tuyến Snapshot"}

    # Mock instance của StorageManager
    def mock_taxonomy(self):
        # Khi offline, trả về snapshot
        if not self.online:
            return dict(snapshot_tax)
        return {}

    monkeypatch.setattr(StorageManager, "online", False)
    monkeypatch.setattr(StorageManager, "taxonomy", mock_taxonomy)

    dock.set_assets([])
    cat_defs = {c["id"]: c for c in dock.category_nav._categories}
    assert "offline_vault" in cat_defs
    assert cat_defs["offline_vault"]["label"] == "Kho Ngoại Tuyến Snapshot"


def test_broll_chip_dynamic_based_on_assets(dock, monkeypatch):
    """Kiểm tra B-roll: chỉ hiển thị khi có asset B-roll, và giữ qua đổi profile."""
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: {})

    # Khi không có B-roll
    dock.set_assets([{"id": "t1", "name": "Title", "tab": "text", "category": "text"}])
    assert "broll" not in [c["id"] for c in dock.category_nav._categories]

    # Khi có B-roll
    dock.set_assets([
        {"id": "t1", "name": "Title", "tab": "text", "category": "text"},
        {"id": "b1", "name": "B-roll 1", "tab": "broll", "category": "broll"},
    ])
    cat_ids = [c["id"] for c in dock.category_nav._categories]
    assert "broll" in cat_ids
    broll_def = next(c for c in dock.category_nav._categories if c["id"] == "broll")
    assert broll_def["label"] == "Cảnh (B-Roll)"

    # Đổi profile sang Color rồi quay lại Edit: B-roll vẫn tồn tại
    dock._on_profile_changed(PROFILES["color"])
    assert "broll" in [c["id"] for c in dock.category_nav._categories]
    dock._on_profile_changed(PROFILES["edit"])
    assert "broll" in [c["id"] for c in dock.category_nav._categories]


def test_taxonomy_called_once_per_rebuild(dock, monkeypatch):
    """9. Đọc taxonomy đúng 1 lần cho mỗi lần rebuild nav, không đọc mỗi asset hay mỗi hover."""
    call_count = 0

    def spy_taxonomy(self):
        nonlocal call_count
        call_count += 1
        return {"custom_spy": "Tùy Biến Spy"}

    monkeypatch.setattr(StorageManager, "taxonomy", spy_taxonomy)

    call_count = 0
    # Truyền vào 50 assets
    many_assets = [
        {"id": f"a_{i}", "name": f"Asset {i}", "tab": "custom_spy", "category": "custom_spy", "storage_managed": True}
        for i in range(50)
    ]
    dock.set_assets(many_assets)
    # taxonomy chỉ được gọi đúng 1 lần, tuyệt đối không gọi 50 lần
    assert call_count == 1

    # Khi đổi profile cũng chỉ đọc taxonomy đúng 1 lần
    call_count = 0
    dock._on_profile_changed(PROFILES["color"])
    assert call_count == 1


def test_refresh_assets_resets_stale_subcategory_and_avoids_empty_grid(dock, monkeypatch):
    """G04-04A1-R1: Khi refresh assets mà subcategory đang chọn bị mất:
    - Nav và proxy phải reset về 'Tất cả' / 'all'.
    - Lưới không bị rỗng giả (rowCount > 0).
    - Profile roundtrip sau refresh không phục hồi lại sub cũ 'Old'.
    """
    custom_tax = {"probe_custom": "Probe Custom"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    # 1. Ban đầu có asset với sub "Old"
    assets_v1 = [
        {"id": "p1", "name": "Asset Probe", "tab": "probe_custom", "category": "probe_custom", "sub": "Old", "storage_managed": True}
    ]
    dock.set_assets(assets_v1)
    dock.category_nav.select_category("probe_custom")
    dock.category_nav.select_subcategory("Old")

    assert dock.category_nav.current_category == "probe_custom"
    assert dock.category_nav.current_subcategory == "Old"
    assert dock.proxy_model.filter_subcategory() == "Old"
    assert dock.proxy_model.rowCount() == 1

    # 2. Refresh assets: cùng ID/category nhưng sub đổi thành "New" (sub "Old" không còn)
    assets_v2 = [
        {"id": "p1", "name": "Asset Probe", "tab": "probe_custom", "category": "probe_custom", "sub": "New", "storage_managed": True}
    ]
    dock.set_assets(assets_v2)

    # Sub "Old" đã mất -> phải reset về "Tất cả" trên nav và "all" trên proxy
    assert dock.category_nav.current_subcategory == "Tất cả"
    assert dock.proxy_model.filter_subcategory() in ("all", "Tất cả")
    # Lưới phải hiển thị asset mới, không bị rỗng giả 0 rows
    assert dock.proxy_model.rowCount() == 1

    # 3. Profile roundtrip: đổi sang Color rồi quay lại Edit -> không phục hồi lại sub "Old" đã mất
    dock._on_profile_changed(PROFILES["color"])
    dock._on_profile_changed(PROFILES["edit"])
    assert dock.category_nav.current_category == "probe_custom"
    assert dock.category_nav.current_subcategory == "Tất cả"
    assert dock.proxy_model.filter_subcategory() in ("all", "Tất cả")
    assert dock.proxy_model.rowCount() == 1


def test_refresh_assets_preserves_valid_subcategory(dock, monkeypatch):
    """G04-04A1-R1: Khi refresh assets mà subcategory đang chọn vẫn hợp lệ thì giữ nguyên."""
    custom_tax = {"probe_custom": "Probe Custom"}
    monkeypatch.setattr(StorageManager, "taxonomy", lambda self: dict(custom_tax))

    assets_v1 = [
        {"id": "p1", "name": "Asset 1", "tab": "probe_custom", "category": "probe_custom", "sub": "KeepMe", "storage_managed": True},
        {"id": "p2", "name": "Asset 2", "tab": "probe_custom", "category": "probe_custom", "sub": "Other", "storage_managed": True},
    ]
    dock.set_assets(assets_v1)
    dock.category_nav.select_category("probe_custom")
    dock.category_nav.select_subcategory("KeepMe")

    assert dock.category_nav.current_subcategory == "KeepMe"
    assert dock.proxy_model.rowCount() == 1

    # Refresh với thêm asset mới nhưng sub "KeepMe" vẫn tồn tại
    assets_v2 = [
        {"id": "p1", "name": "Asset 1 Updated", "tab": "probe_custom", "category": "probe_custom", "sub": "KeepMe", "storage_managed": True},
        {"id": "p3", "name": "Asset 3", "tab": "probe_custom", "category": "probe_custom", "sub": "Extra", "storage_managed": True},
    ]
    dock.set_assets(assets_v2)

    # "KeepMe" vẫn phải được giữ nguyên
    assert dock.category_nav.current_subcategory == "KeepMe"
    assert dock.proxy_model.filter_subcategory() == "KeepMe"
    assert dock.proxy_model.rowCount() == 1


def test_dock_set_assets_preserves_all_category_mode_and_search_query(dock):
    """G04-04A2a-R1: set_assets metadata refresh bảo toàn proxy filter_category='all' và search query."""
    assets = [
        {"id": "t1", "name": "Text One", "tab": "text", "category": "text"},
        {"id": "b1", "name": "Special Needle Footage", "tab": "broll", "category": "broll"},
        {"id": "a1", "name": "Audio Track", "tab": "audio", "category": "audio"},
    ]
    dock.set_assets(assets)

    # Đặt dock ở chế độ tìm kiếm toàn cục ("all") với query "needle"
    dock.proxy_model.set_category("all")
    dock.proxy_model.set_search_query("needle")

    assert dock.proxy_model.filter_category() == "all"
    assert dock.proxy_model.rowCount() == 1
    assert dock.proxy_model.index(0, 0).data(Qt.ItemDataRole.UserRole + 1) == "b1"  # IdRole

    # Nạp lại metadata refresh với tập assets mới (thêm asset broll khác)
    updated_assets = list(assets) + [
        {"id": "b2", "name": "Another Broll", "tab": "broll", "category": "broll"}
    ]
    dock.set_assets(updated_assets)

    # Proxy model phải tiếp tục ở chế độ category="all" và query="needle", không bị nav "text" ghi đè
    assert dock.proxy_model.filter_category() == "all"
    assert dock.proxy_model._search_query == "needle"
    assert dock.proxy_model.rowCount() == 1
    assert dock.proxy_model.index(0, 0).data(Qt.ItemDataRole.UserRole + 1) == "b1"

    # Profile switch vẫn hoạt động chính xác theo chủ ý
    dock._on_profile_changed(PROFILES["color"])
    assert dock.category_nav.current_category == "lut"
    assert dock.proxy_model.filter_category() == "lut"

    dock._on_profile_changed(PROFILES["edit"])
    assert dock.category_nav.current_category == "text"
    assert dock.proxy_model.filter_category() == "text"



