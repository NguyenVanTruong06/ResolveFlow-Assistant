import pytest
from pathlib import Path
from src.core.collections_manager import CollectionsManager


@pytest.fixture(autouse=True)
def temp_collections_file(tmp_path, monkeypatch):
    test_file = tmp_path / "collections.json"
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", test_file)
    yield test_file


def test_collections_crud():
    # 1. Initial state
    assert CollectionsManager.get_collection_names() == []
    assert CollectionsManager.get_collection_assets("Vlog") == []

    # 2. Create collection
    assert CollectionsManager.create_collection("Vlog Du Lịch") is True
    assert CollectionsManager.create_collection("Vlog Du Lịch") is False  # duplicate
    assert CollectionsManager.create_collection("") is False
    assert CollectionsManager.get_collection_names() == ["Vlog Du Lịch"]

    # 3. Add & remove assets
    assert CollectionsManager.add_asset_to_collection("Vlog Du Lịch", "asset_1") is True
    assert CollectionsManager.add_asset_to_collection("Vlog Du Lịch", "asset_1") is False  # already there
    assert CollectionsManager.add_asset_to_collection("Vlog Du Lịch", "asset_2") is True
    assert CollectionsManager.get_collection_assets("Vlog Du Lịch") == ["asset_1", "asset_2"]
    assert CollectionsManager.get_asset_collections("asset_1") == ["Vlog Du Lịch"]

    # 4. Toggle asset
    # Removing asset_1
    assert CollectionsManager.toggle_asset_collection("Vlog Du Lịch", "asset_1") is False
    assert CollectionsManager.get_collection_assets("Vlog Du Lịch") == ["asset_2"]
    # Re-adding asset_1
    assert CollectionsManager.toggle_asset_collection("Vlog Du Lịch", "asset_1") is True
    assert "asset_1" in CollectionsManager.get_collection_assets("Vlog Du Lịch")

    # 5. Rename collection
    assert CollectionsManager.rename_collection("Vlog Du Lịch", "TikTok Viral") is True
    assert CollectionsManager.get_collection_names() == ["TikTok Viral"]
    assert "asset_1" in CollectionsManager.get_collection_assets("TikTok Viral")

    # 6. Delete collection
    assert CollectionsManager.delete_collection("TikTok Viral") is True
    assert CollectionsManager.get_collection_names() == []
    assert CollectionsManager.delete_collection("Non-existent") is False
