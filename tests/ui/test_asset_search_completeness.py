"""
tests/ui/test_asset_search_completeness.py
Integration tests for search completeness across virtual proxy model and facet filtering (G04-04B1-I).
Verifies that SearchIndex returns 100% of matches (limit=0) so that:
- Assets ranked outside the top 50 are not truncated.
- Facets (category, subcategory, pack, tags) intersecting with assets outside top 50 display properly.
- Dynamic index updates (add, rename, delete) reflect immediately under active query without stale IDs.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.ui.lux.library.asset_model import AssetListModel, IdRole
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.services.search_index import SearchIndex


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def search_env(tmp_path):
    frecency_file = str(tmp_path / "frecency_test.json")

    # Tạo 125 asset có tên khớp với từ khóa "Action"
    # 0..69: Pack "Alpha Pack", sub="Day", tag="daylight"
    # 70..124: Pack "Beta Pack", sub="Night", tag="dark"
    items = []
    for i in range(125):
        if i < 70:
            pack = "Alpha Pack"
            sub = "Day"
            tags = ["daylight", "action"]
        else:
            pack = "Beta Pack"
            sub = "Night"
            tags = ["dark", "action"]

        items.append({
            "id": f"action_shot_{i:03d}",
            "name": f"Action Footage Shot {i:03d}",
            "tab": "broll",
            "category": "broll",
            "sub": sub,
            "subcategory": sub,
            "pack_name": pack,
            "tags": tags,
        })

    # Thêm 1 asset category khác khớp "Action"
    items.append({
        "id": "sfx_action_punch",
        "name": "Action Punch Impact",
        "tab": "sfx",
        "category": "sfx",
        "sub": "Impact",
        "subcategory": "Impact",
        "pack_name": "Combat SFX",
        "tags": ["fight", "punch"],
    })

    search_index = SearchIndex(items, frecency_path=frecency_file)
    model = AssetListModel(items)
    proxy = AssetFilterProxyModel(search_index=search_index)
    proxy.setSourceModel(model)

    return {
        "items": items,
        "search_index": search_index,
        "model": model,
        "proxy": proxy,
        "tmp_path": tmp_path,
    }


def test_search_completeness_beyond_top_50(search_env):
    """1. Kiểm tra proxy model nhận toàn bộ >50 kết quả khớp từ khóa thay vì bị cắt cụt ở 50."""
    proxy = search_env["proxy"]

    # Áp dụng query "Action"
    proxy.set_search_query("Action")

    # Tổng cộng có 125 broll + 1 sfx = 126 asset khớp từ khóa "Action"
    assert proxy.rowCount() == 126, f"Kỳ vọng 126 kết quả, nhưng proxy chỉ nhận: {proxy.rowCount()}"

    visible_ids = {proxy.index(r, 0).data(IdRole) for r in range(proxy.rowCount())}
    expected_ids = {f"action_shot_{i:03d}" for i in range(125)} | {"sfx_action_punch"}
    assert visible_ids == expected_ids


def test_facet_intersection_with_assets_outside_top_50(search_env):
    """2. Facet (pack, subcategory, tag) giao với asset nằm ngoài top 50 phải hiển thị đầy đủ, không rỗng giả."""
    proxy = search_env["proxy"]
    proxy.set_search_query("Action")

    # Lọc theo Pack "Beta Pack" (chứa các asset 70..124 vốn nằm ngoài top 50 ban đầu)
    proxy.set_sub_category("pack:Beta Pack")
    assert proxy.rowCount() == 55, f"Kỳ vọng 55 asset thuộc Beta Pack, nhận được: {proxy.rowCount()}"

    beta_ids = {proxy.index(r, 0).data(IdRole) for r in range(proxy.rowCount())}
    expected_beta_ids = {f"action_shot_{i:03d}" for i in range(70, 125)}
    assert beta_ids == expected_beta_ids

    # Lọc theo Subcategory "Night" (chứa các asset 70..124)
    proxy.set_sub_category("Night")
    assert proxy.rowCount() == 55
    night_ids = {proxy.index(r, 0).data(IdRole) for r in range(proxy.rowCount())}
    assert night_ids == expected_beta_ids

    # Lọc theo Tag explicit "dark"
    proxy.set_sub_category("all")
    proxy.set_tag("dark")
    assert proxy.rowCount() == 55
    tag_dark_ids = {proxy.index(r, 0).data(IdRole) for r in range(proxy.rowCount())}
    assert tag_dark_ids == expected_beta_ids

    # Lọc kết hợp Category "sfx" khi query "Action"
    proxy.set_tag(None)
    proxy.set_category("sfx")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data(IdRole) == "sfx_action_punch"


def test_search_query_cleared_restores_natural_order_and_zero_matches(search_env):
    """3. Xóa query khôi phục natural order; query không khớp trả về 0 kết quả."""
    proxy = search_env["proxy"]
    model = search_env["model"]

    # Ban đầu query "Action"
    proxy.set_search_query("Action")
    assert proxy.rowCount() == 126

    # Xóa query -> trả về toàn bộ danh sách theo natural order
    proxy.set_search_query("")
    assert proxy.rowCount() == len(search_env["items"])
    # Hàng đầu tiên trong proxy trùng với hàng đầu tiên trong model
    assert proxy.index(0, 0).data(IdRole) == model.index(0, 0).data(IdRole)

    # Query không tồn tại -> 0 kết quả
    proxy.set_search_query("nonexistent_super_query_xyz_9999")
    assert proxy.rowCount() == 0


def test_active_query_updates_dynamically_on_index_refresh(search_env):
    """4. Khi query đang hoạt động, cập nhật tài nguyên (thêm, sửa tên, xóa) phản ánh lập tức vào proxy."""
    proxy = search_env["proxy"]
    model = search_env["model"]
    search_index = search_env["search_index"]

    proxy.set_search_query("Action")
    assert proxy.rowCount() == 126

    # Thêm 1 asset mới khớp query "Action"
    new_items = list(search_env["items"])
    new_items.append({
        "id": "action_shot_new",
        "name": "Action Shot New 999",
        "tab": "broll",
        "category": "broll",
        "sub": "Day",
        "subcategory": "Day",
        "pack_name": "Alpha Pack",
        "tags": ["action"],
    })
    # Đổi tên 1 asset cũ để không còn khớp "Action"
    renamed_item = dict(new_items[0])
    renamed_item["name"] = "Romantic Sunset Timelapse"
    renamed_item["tags"] = ["sunset", "nature"]
    new_items[0] = renamed_item

    # Xóa 1 asset cũ
    deleted_id = new_items[1]["id"]
    new_items.pop(1)

    # Cập nhật cả model và search_index qua API chính thức
    search_index.set_assets(new_items)
    model.set_assets(new_items)
    # proxy nhận indexer mới hoặc kích hoạt search refresh
    proxy.set_search_index(search_index)

    # Kiểm tra:
    # 1. action_shot_new phải xuất hiện
    # 2. renamed_item (action_shot_000) phải biến mất
    # 3. deleted_id (action_shot_001) phải biến mất
    visible_ids = {proxy.index(r, 0).data(IdRole) for r in range(proxy.rowCount())}
    assert "action_shot_new" in visible_ids
    assert renamed_item["id"] not in visible_ids
    assert deleted_id not in visible_ids
