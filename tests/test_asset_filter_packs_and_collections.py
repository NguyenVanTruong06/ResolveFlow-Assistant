import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.ui.lux.library.asset_model import AssetListModel
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.core.collections_manager import CollectionsManager


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def temp_collections(tmp_path, monkeypatch):
    test_file = tmp_path / "collections.json"
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", test_file)
    yield test_file


def test_asset_filter_pack_and_collection(qapp):
    items = [
        {"id": "lut_1", "name": "Vlog Warm", "tab": "lut", "category": "natural", "sub_category": "natural"},
        {"id": "lut_2", "name": "Secret Ferrari", "tab": "lut", "category": "cinema", "pack_name": "Secret Sauce LUTs"},
        {"id": "sfx_1", "name": "Whoosh", "tab": "sfx", "category": "trans", "pack_name": "Pro SFX Pack"},
        {"id": "text_1", "name": "Title Kinetic", "tab": "text", "category": "kinetic"},
    ]
    favs = {"lut_1", "sfx_1"}

    model = AssetListModel(items, favs=favs)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # 1. Tab LUT, sub = all -> 2 items (lut_1 and lut_2)
    proxy.set_category("lut")
    proxy.set_sub_category("all")
    assert proxy.rowCount() == 2

    # 2. Filter by pack
    proxy.set_sub_category("pack:Secret Sauce LUTs")
    assert proxy.rowCount() == 1
    idx = proxy.index(0, 0)
    assert proxy.data(idx) == "Secret Ferrari"

    # 3. Favorites tab: sub = all
    proxy.set_category("fav")
    proxy.set_sub_category("all")
    # lut_1 and sfx_1 are favs
    assert proxy.rowCount() == 2

    # 4. Custom collection in Favorites tab
    CollectionsManager.create_collection("My Vlog Project")
    CollectionsManager.add_asset_to_collection("My Vlog Project", "lut_2")
    CollectionsManager.add_asset_to_collection("My Vlog Project", "text_1")

    proxy.set_sub_category("coll:My Vlog Project")
    assert proxy.rowCount() == 2
    names = [proxy.data(proxy.index(i, 0)) for i in range(proxy.rowCount())]
    assert "Secret Ferrari" in names
    assert "Title Kinetic" in names


def test_asset_filter_canonical_categories_and_unicode_normalization(qapp):
    items = [
        {"id": "t1", "name": "Tiêu đề Kinetic", "tab": "text", "category": "Kinetic", "tags": ["hot", "trend"]},
        {"id": "c1", "name": "Zoom In Chuyển cảnh", "tab": "trans", "category": "Chuyển động", "tags": ["seamless"]},
        {"id": "s1", "name": "Whoosh Bay", "tab": "sfx", "category": "Whoosh", "tags": ["fast"]},
        {"id": "l1", "name": "Teal Orange", "tab": "lut", "category": "Cinema", "tags": ["film"]},
    ]
    model = AssetListModel(items)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # Alias "âm thanh" -> "sfx"
    proxy.set_category("Âm thanh")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Whoosh Bay"

    # Alias "chuyển cảnh" -> "trans"
    proxy.set_category("chuyển cảnh")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Zoom In Chuyển cảnh"

    # Alias "màu" -> "lut"
    proxy.set_category("MÀU")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Teal Orange"


def test_asset_filter_tag_and_category_logical_and(qapp):
    items = [
        {"id": "t1", "name": "Title Kinetic Hot", "tab": "text", "sub": "Kinetic", "tags": ["hot", "viral"]},
        {"id": "t2", "name": "Title Kinetic Cold", "tab": "text", "sub": "Kinetic", "tags": ["minimal"]},
        {"id": "t3", "name": "Title Neon Hot", "tab": "text", "sub": "Neon", "tags": ["hot", "retro"]},
        {"id": "s1", "name": "Whoosh Hot", "tab": "sfx", "sub": "Whoosh", "tags": ["hot"]},
    ]
    model = AssetListModel(items)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    # 1. Category "text" AND sub "Kinetic" -> 2 items (t1, t2)
    proxy.set_category("text")
    proxy.set_sub_category("Kinetic")
    assert proxy.rowCount() == 2

    # 2. Add tag "hot": Category "text" AND sub "Kinetic" AND tag "hot" -> 1 item (t1)
    proxy.set_tag("hot")
    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Title Kinetic Hot"

    # 3. Tag "nonexistent" -> 0 items
    proxy.set_tag("nonexistent")
    assert proxy.rowCount() == 0

    # 4. "Tất cả" resets tag
    proxy.set_sub_category("Tất cả")
    assert proxy.active_tag() is None

    # 5. Switching category resets tag
    proxy.set_tag("viral")
    assert proxy.active_tag() == "viral"
    proxy.set_category("sfx")
    assert proxy.active_tag() is None
    assert proxy.filter_subcategory() == "all"


def test_edge_dock_empty_state_and_deselection(qapp):
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    dock = EdgeDockWindow()
    dock.show()
    dock.set_assets([
        {"id": "a1", "name": "Whoosh One", "tab": "sfx", "category": "sfx"},
        {"id": "a2", "name": "Whoosh Two", "tab": "sfx", "category": "sfx"}
    ])

    dock.open_dock(animated=False)
    qapp.processEvents()

    # Select asset a1
    dock._on_asset_clicked({"id": "a1", "name": "Whoosh One", "category": "sfx"})
    assert dock._selected_asset is not None
    assert dock._selected_asset["id"] == "a1"

    # Filter with impossible query -> rowCount == 0
    dock.proxy_model.set_search_query("impossible_query_99999")
    qapp.processEvents()
    assert dock.proxy_model.rowCount() == 0
    assert dock.empty_state_widget.isVisible()
    # Selected asset deselected
    assert dock._selected_asset is None

    # Clear filters -> restores items, hides empty state
    dock.clear_all_filters()
    qapp.processEvents()
    assert dock.proxy_model.rowCount() > 0
    assert not dock.empty_state_widget.isVisible()


def test_category_nav_broll_verification(qapp):
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    # Without B-roll
    dock1 = EdgeDockWindow()
    dock1.set_assets([
        {"id": "t1", "name": "Title", "tab": "text", "category": "text"},
        {"id": "tr1", "name": "Zoom", "tab": "trans", "category": "trans"}
    ])
    cat_ids1 = [c["id"] for c in dock1.category_nav._categories]
    assert "broll" not in cat_ids1
    # Transitions remain "trans" / "Chuyển cảnh", NOT renamed to "Cảnh"
    trans_cat = next(c for c in dock1.category_nav._categories if c["id"] == "trans")
    assert trans_cat["label"] == "Chuyển cảnh"

    # With real B-roll footage asset
    dock2 = EdgeDockWindow()
    dock2.set_assets([
        {"id": "t1", "name": "Title", "tab": "text", "category": "text"},
        {"id": "br1", "name": "City Timelapse", "tab": "broll", "category": "broll"}
    ])
    cat_ids2 = [c["id"] for c in dock2.category_nav._categories]
    assert "broll" in cat_ids2


def test_exact_category_filtering_no_substring_bleed(qapp):
    """P1 defect: 'sfx' must NOT match 'not_sfx' due to substring check."""
    items = [
        {"id": "real_sfx", "name": "Real SFX", "tab": "sfx", "category": "sfx"},
        {"id": "not_sfx", "name": "Not An SFX", "tab": "not_sfx", "category": "not_sfx"},
        {"id": "trans_1", "name": "Transition", "tab": "trans", "category": "trans"},
    ]
    model = AssetListModel(items)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.set_category("sfx")
    assert proxy.rowCount() == 1
    matched_ids = [proxy.index(i, 0).data(Qt.ItemDataRole.UserRole)["id"] for i in range(proxy.rowCount())]
    assert matched_ids == ["real_sfx"]


def test_tag_reset_when_subcategory_already_all(qapp):
    """P1 defect: resetting tag when sub_category is already 'all' must invalidate cache."""
    items = [
        {"id": "item_1", "name": "Item One", "tab": "all", "category": "text", "tags": ["tag_a"]},
        {"id": "item_2", "name": "Item Two", "tab": "all", "category": "sfx", "tags": ["tag_b"]},
    ]
    model = AssetListModel(items)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.set_category("all")
    proxy.set_sub_category("all")
    assert proxy.rowCount() == 2

    # Filter with non-existent tag
    proxy.set_tag("absent")
    assert proxy.rowCount() == 0

    # Reset tag using set_sub_category('all')
    proxy.set_sub_category("all")
    assert proxy.active_tag() is None
    assert proxy.rowCount() == 2
    matched_ids = [proxy.index(i, 0).data(Qt.ItemDataRole.UserRole)["id"] for i in range(proxy.rowCount())]
    assert matched_ids == ["item_1", "item_2"]


def test_pack_sub_tags_independent_metadata(qapp):
    """P1 defect: pack_name must NOT overwrite subcategory and must not match regular tags."""
    item = {
        "id": "asset_complex",
        "name": "Cinematic Whoosh",
        "tab": "sfx",
        "category": "sfx",
        "sub": "Whoosh",
        "subcategory": "Whoosh",
        "pack_name": "Secret Sauce SFX",
        "tags": ["cinema", "trailer"]
    }
    model = AssetListModel([item])
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(model)

    proxy.set_category("sfx")

    # 1. Match subcategory Whoosh
    proxy.set_sub_category("Whoosh")
    assert proxy.rowCount() == 1
    assert proxy.index(0, 0).data(Qt.ItemDataRole.UserRole)["id"] == "asset_complex"

    # 2. Pack name must NOT match as a regular subcategory
    proxy.set_sub_category("Secret Sauce SFX")
    assert proxy.rowCount() == 0

    # 3. Explicit pack filter matches
    proxy.set_sub_category("pack:Secret Sauce SFX")
    assert proxy.rowCount() == 1

    # 4. Tag matches
    proxy.set_sub_category("all")
    proxy.set_tag("cinema")
    assert proxy.rowCount() == 1
    proxy.set_tag("trailer")
    assert proxy.rowCount() == 1
    proxy.set_tag("Secret Sauce SFX")
    assert proxy.rowCount() == 0
