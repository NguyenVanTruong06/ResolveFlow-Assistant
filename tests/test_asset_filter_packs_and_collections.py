import pytest
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
