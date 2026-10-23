import pytest
from PySide6.QtWidgets import QApplication
from src.ui.tabs.tab_assets import TabAssets, StudioAsset
from src.core.collections_manager import CollectionsManager
from src.core.pack_manager import PackManager, PackManifest


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    colls_file = tmp_path / "collections.json"
    packs_file = tmp_path / "installed_packs.json"
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", colls_file)
    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", packs_file)
    yield


def test_column2_packs_and_collections(qapp):
    tab = TabAssets(headless=True)

    # 1. Inject a custom pack asset into all_luts and all_assets
    test_lut = StudioAsset(
        id="test_pack_lut_1",
        name="Cinematic Teal Special",
        tab="lut",
        category="cinema",
        sub="Secret Sauce Cinema LUTs",
        pack_name="Secret Sauce Cinema LUTs"
    )
    tab.all_assets.append(test_lut)
    tab._populate_cards()

    # Switch to LUT tab
    tab._filter_by_rail("lut")

    # Column 2 should have button for the pack
    pack_btn_found = False
    for i in range(tab.cats_vbox.count()):
        item = tab.cats_vbox.itemAt(i)
        if item and item.widget():
            txt = getattr(item.widget(), "text", lambda: "")()
            if "Secret Sauce Cinema LUTs" in txt:
                pack_btn_found = True
                break
    assert pack_btn_found, "Pack button for 'Secret Sauce Cinema LUTs' should be created in Column 2"

    # Click the pack button
    tab._on_sub_category_clicked("pack:Secret Sauce Cinema LUTs")
    assert tab.current_sub_cat == "pack:Secret Sauce Cinema LUTs"
    assert tab.asset_filter.rowCount() == 1
    idx = tab.asset_filter.index(0, 0)
    assert tab.asset_filter.data(idx) == "Cinematic Teal Special"

    # 2. Test Favorites Tab & Custom Collections
    tab.favorites.add("test_pack_lut_1")
    tab._filter_by_rail("fav")
    assert tab.lbl_cats_head.text() == "Bộ sưu tập & Đã lưu"

    # Create a custom collection
    CollectionsManager.create_collection("Dự án Vlog Tết")
    CollectionsManager.add_asset_to_collection("Dự án Vlog Tết", "test_pack_lut_1")

    # Rebuild Column 2 in Favorites
    tab._rebuild_categories_column()

    coll_btn_found = False
    for i in range(tab.cats_vbox.count()):
        item = tab.cats_vbox.itemAt(i)
        if item and item.widget():
            txt = getattr(item.widget(), "text", lambda: "")()
            if "Dự án Vlog Tết" in txt:
                coll_btn_found = True
                break
    assert coll_btn_found, "Collection button 'Dự án Vlog Tết' should be created in Column 2"

    # Filter by collection
    tab._on_sub_category_clicked("coll:Dự án Vlog Tết")
    assert tab.asset_filter.rowCount() == 1

    # Inspector details verification
    tab.selected_asset = test_lut
    tab._update_inspector_details()
    assert "Secret Sauce Cinema LUTs" in tab.lbl_insp_sub.text()
    assert "[Dự án Vlog Tết]" in tab.lbl_insp_collections.text()
