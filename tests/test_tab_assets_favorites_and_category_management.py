import os
import json
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QEvent, QModelIndex
from PySide6.QtGui import QMouseEvent

from src.ui.tabs.tab_assets import (
    TabAssets, StudioAsset, FavoritesManager, CategoryOverridesManager,
    FavoriteGroupSelectDialog, MoveCategoryDialog
)
from src.core.collections_manager import CollectionsManager
from src.core.pack_manager import PackManager
from src.core.storage_manager import StorageManager
from src.ui.lux.library.asset_model import AssetListModel, extract_asset_info
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.ui.lux.library.asset_delegate import AssetCardDelegate
from src.ui.lux.library.asset_grid import AssetGridView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fav_file = data_dir / "favorites.json"
    coll_file = data_dir / "collections.json"
    packs_file = data_dir / "installed_packs.json"
    overrides_file = data_dir / "category_overrides.json"

    monkeypatch.setattr(FavoritesManager, "FILE_PATH", fav_file)
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", coll_file)
    monkeypatch.setattr(PackManager, "INSTALLED_PACKS_FILE", packs_file)
    monkeypatch.setattr(CategoryOverridesManager, "FILE_PATH", overrides_file)

    # Isolate StorageManager paths
    storage_dir = tmp_path / "xdg"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(storage_dir))

    # Reset in-memory caches
    FavoritesManager._cache = None
    FavoritesManager._cache_mtime = -1.0
    FavoritesManager._cache_path = ""
    CollectionsManager._cache = None
    CategoryOverridesManager._cache = None
    CategoryOverridesManager._cache_mtime = -1.0
    CategoryOverridesManager._cache_path = ""

    yield


# =========================================================================
# 1. Tests for FavoritesManager & CategoryOverridesManager
# =========================================================================

def test_favorites_manager_load_save_error_handling(tmp_path, monkeypatch):
    """Test FavoritesManager loading, saving, cache tracking, and failure handling."""
    fav_file = tmp_path / "data" / "favorites.json"

    # Initial load on missing file returns empty set
    assert FavoritesManager.load() == set()

    # Save returns True and writes sorted JSON
    ok = FavoritesManager.save({"asset_c", "asset_a", "asset_b"})
    assert ok is True
    assert fav_file.exists()

    with open(fav_file, "r", encoding="utf-8") as f:
        loaded_raw = json.load(f)
    assert loaded_raw == ["asset_a", "asset_b", "asset_c"]

    # Cache hit on subsequent load
    assert FavoritesManager.load() == {"asset_a", "asset_b", "asset_c"}

    # Failure simulation: target directory is read-only or invalid file path
    invalid_file = tmp_path / "non_existent_dir_read_only" / "forbidden" / "favorites.json"
    monkeypatch.setattr(FavoritesManager, "FILE_PATH", invalid_file)
    # Monkeypatch mkdir to fail
    def mock_mkdir(*args, **kwargs):
        raise OSError("Permission denied")
    monkeypatch.setattr(Path, "mkdir", mock_mkdir)

    fail_ok = FavoritesManager.save({"item_1"})
    assert fail_ok is False, "FavoritesManager.save must return False on write failure without raising"


def test_category_overrides_manager(tmp_path):
    """Test CategoryOverridesManager set, batch, and rename."""
    assert CategoryOverridesManager.load() == {}

    ok = CategoryOverridesManager.set_override("asset_1", "Kinetic High")
    assert ok is True
    assert CategoryOverridesManager.load() == {"asset_1": "Kinetic High"}

    # Batch set
    CategoryOverridesManager.set_overrides_batch({"asset_2": "Kinetic High", "asset_3": "Vlog Vlog"})
    data = CategoryOverridesManager.load()
    assert data["asset_1"] == "Kinetic High"
    assert data["asset_2"] == "Kinetic High"
    assert data["asset_3"] == "Vlog Vlog"

    # Category rename updates all references
    CategoryOverridesManager.update_category_rename("Kinetic High", "Super Kinetic")
    data_after = CategoryOverridesManager.load()
    assert data_after["asset_1"] == "Super Kinetic"
    assert data_after["asset_2"] == "Super Kinetic"
    assert data_after["asset_3"] == "Vlog Vlog"


# =========================================================================
# 2. End-to-End Heart Click and Favorites Workflow
# =========================================================================

def test_favorite_click_delegate_editor_event(qapp):
    """Test direct clicking on heart icon in AssetCardDelegate emits favorite_toggled."""
    view = AssetGridView()
    delegate = AssetCardDelegate(view)

    asset = {
        "id": "asset_heart_test",
        "name": "Heart Click Test",
        "category": "text",
        "is_fav": False,
    }
    model = AssetListModel([asset], favs=set())
    view.setModel(model)

    toggled_signals = []
    view.favorite_toggled.connect(lambda aid, is_fav: toggled_signals.append((aid, is_fav)))

    idx = model.index(0, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(10, 10, 160, 190)

    # Calculate heart hit target: preview_rect is (10, 10, 160, 120)
    # fav_hit_rect is (right - 28, y + 2, 26, 26) -> (170 - 28, 12, 26, 26) = (142, 12, 26, 26)
    click_pos = QPointF(150, 20)
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        click_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )

    consumed = delegate.editorEvent(event, model, option, idx)
    assert consumed is True, "Heart click event should be consumed by delegate"
    assert len(toggled_signals) == 1
    assert toggled_signals[0] == ("asset_heart_test", True)


def test_favorites_workflow_model_filter_persistence(qapp):
    """Test full favorite workflow: toggling updates model, proxy model in fav tab, and disk persistence."""
    tab = TabAssets(headless=True)
    test_asset = StudioAsset(
        id="fav_full_test_1",
        name="Title Star One",
        tab="text",
        category="kinetic",
        sub="kinetic"
    )
    tab.all_assets.append(test_asset)
    tab._populate_cards()

    # Initially not favorite
    assert "fav_full_test_1" not in tab.favorites
    assert tab.asset_model.is_favorite("fav_full_test_1") is False

    # Switch to Favorites tab
    tab._filter_by_rail("fav")
    tab._on_sub_category_clicked("all")
    assert tab.asset_filter.rowCount() == 0

    # Toggle favorite on (headless defaults group to "")
    ok = tab._on_favorite_toggled("fav_full_test_1", True)
    assert ok is True
    assert "fav_full_test_1" in tab.favorites
    assert tab.asset_model.is_favorite("fav_full_test_1") is True

    # Proxy model must now show the item in Favorites -> All
    assert tab.asset_filter.rowCount() == 1
    idx = tab.asset_filter.index(0, 0)
    assert tab.asset_filter.data(idx) == "Title Star One"

    # Disk file must contain the favorite
    persisted = FavoritesManager.load()
    assert "fav_full_test_1" in persisted

    # Toggle favorite off
    ok_off = tab._on_favorite_toggled("fav_full_test_1", False)
    assert ok_off is True
    assert "fav_full_test_1" not in tab.favorites
    assert tab.asset_model.is_favorite("fav_full_test_1") is False
    assert tab.asset_filter.rowCount() == 0
    assert "fav_full_test_1" not in FavoritesManager.load()


# =========================================================================
# 3. Custom Favorite Grouping, Collections, and Cancellation
# =========================================================================

def test_favorite_grouping_create_select_cancel(qapp, monkeypatch):
    """Test custom grouping on adding favorite: create group, select existing, ungrouped, and cancel."""
    tab = TabAssets(headless=True)
    asset_a = StudioAsset(id="item_grp_a", name="Shorts Intro", tab="text", category="clean")
    asset_b = StudioAsset(id="item_grp_b", name="Vlog Transition", tab="trans", category="motion")
    tab.all_assets.extend([asset_a, asset_b])
    tab._populate_cards()

    # 1. Add asset_a to new custom group "Shorts Series"
    tab._on_favorite_toggled("item_grp_a", True, group="Shorts Series")
    assert "item_grp_a" in tab.favorites
    assert "Shorts Series" in CollectionsManager.get_collection_names()
    assert "item_grp_a" in CollectionsManager.get_collection_assets("Shorts Series")
    assert CollectionsManager.get_asset_collections("item_grp_a") == ["Shorts Series"]

    # 2. Add asset_b to existing group "Shorts Series"
    tab._on_favorite_toggled("item_grp_b", True, group="Shorts Series")
    assert "item_grp_b" in tab.favorites
    assert "item_grp_b" in CollectionsManager.get_collection_assets("Shorts Series")

    # 3. Add asset_c as ungrouped default ("")
    asset_c = StudioAsset(id="item_grp_c", name="Ungrouped SFX", tab="sfx", category="whoosh")
    tab.all_assets.append(asset_c)
    tab._populate_cards()
    tab._on_favorite_toggled("item_grp_c", True, group="")
    assert "item_grp_c" in tab.favorites
    assert CollectionsManager.get_asset_collections("item_grp_c") == []
    assert "item_grp_c" in CollectionsManager.get_ungrouped_favorites(tab.favorites)

    # 4. Cancellation simulation: user cancels dialog (_prompt_favorite_group returns None)
    asset_d = StudioAsset(id="item_grp_d", name="Cancelled Asset", tab="text", category="clean")
    tab.all_assets.append(asset_d)
    tab._populate_cards()

    monkeypatch.setattr(tab, "_prompt_favorite_group", lambda pid: None)
    res = tab._on_favorite_toggled("item_grp_d", True, group=None)
    assert res is False, "Cancelled favorite toggle should return False"
    assert "item_grp_d" not in tab.favorites
    assert tab.asset_model.is_favorite("item_grp_d") is False

    # 5. Filter tests in Favorites tab
    tab._filter_by_rail("fav")

    # All Favorites view contains a, b, c
    tab._on_sub_category_clicked("all")
    assert tab.asset_filter.rowCount() == 3

    # Ungrouped Favorites view contains only c
    tab._on_sub_category_clicked("coll:__ungrouped__")
    assert tab.asset_filter.rowCount() == 1
    idx = tab.asset_filter.index(0, 0)
    assert tab.asset_filter.data(idx) == "Ungrouped SFX"

    # Specific collection view contains a and b
    tab._on_sub_category_clicked("coll:Shorts Series")
    assert tab.asset_filter.rowCount() == 2


def test_legacy_favorites_reload_and_no_duplicates(qapp):
    """Test loading pre-existing legacy favorites and re-importing without duplicates."""
    # Pre-populate favorites file with legacy IDs
    FavoritesManager.save({"legacy_title_1", "legacy_trans_2"})

    # Launch tab
    tab = TabAssets(headless=True)
    assert "legacy_title_1" in tab.favorites
    assert "legacy_trans_2" in tab.favorites

    # Re-saving or adding existing does not duplicate
    tab._on_favorite_toggled("legacy_title_1", True, group="")
    assert len(tab.favorites) == 2
    assert FavoritesManager.load() == {"legacy_title_1", "legacy_trans_2"}


# =========================================================================
# 4. Move Category for Single and Multiple Selected Items
# =========================================================================

def test_move_single_and_multiple_items_category(qapp):
    """Test moving logical category for single and multiple items without touching physical files."""
    tab = TabAssets(headless=True)

    item1 = StudioAsset(id="mv_item_1", name="Move Item 1", tab="text", category="clean", sub="clean", file_path="/fake/path1.setting")
    item2 = StudioAsset(id="mv_item_2", name="Move Item 2", tab="text", category="clean", sub="clean", file_path="/fake/path2.setting")
    item3 = StudioAsset(id="mv_item_3", name="Move Item 3", tab="text", category="kinetic", sub="kinetic", file_path="/fake/path3.setting")
    tab.all_assets.extend([item1, item2, item3])
    tab._populate_cards()

    # Move multiple items (item1 and item2) to new category "Ultra Modern"
    moved = tab.move_assets_category([item1, item2], "Ultra Modern")
    assert moved == 2

    assert item1.sub == "Ultra Modern"
    assert item2.sub == "Ultra Modern"
    assert item3.sub == "kinetic"

    # Verify category_overrides.json was persisted
    overrides = CategoryOverridesManager.load()
    assert overrides["mv_item_1"] == "Ultra Modern"
    assert overrides["mv_item_2"] == "Ultra Modern"

    # Verify physical file paths are completely unchanged
    assert item1.file_path == "/fake/path1.setting"
    assert item2.file_path == "/fake/path2.setting"

    # Verify persistence across simulated reload: create new TabAssets and verify overrides apply
    tab2 = TabAssets(headless=True)
    tab2.all_assets.extend([
        StudioAsset(id="mv_item_1", name="Move Item 1", tab="text", category="clean", sub="clean"),
        StudioAsset(id="mv_item_2", name="Move Item 2", tab="text", category="clean", sub="clean")
    ])
    tab2._apply_category_overrides()
    assert tab2._get_asset_sub(tab2.all_assets[-2]) == "Ultra Modern"
    assert tab2._get_asset_sub(tab2.all_assets[-1]) == "Ultra Modern"


# =========================================================================
# 5. Merge Categories and StorageManager Integration
# =========================================================================

def test_merge_categories_no_data_loss(qapp):
    """Test merging source category into destination category without losing any items."""
    tab = TabAssets(headless=True)

    a1 = StudioAsset(id="merge_1", name="M1", tab="text", category="legacy_a", sub="legacy_a")
    a2 = StudioAsset(id="merge_2", name="M2", tab="text", category="legacy_a", sub="legacy_a")
    b1 = StudioAsset(id="merge_3", name="B1", tab="text", category="dest_b", sub="dest_b")
    tab.all_assets.extend([a1, a2, b1])
    tab._populate_cards()

    # Pre-set an override on merge_1 to legacy_a
    CategoryOverridesManager.set_override("merge_1", "legacy_a")

    # Merge legacy_a into dest_b
    ok = tab.merge_category("legacy_a", "dest_b")
    assert ok is True

    # All items from legacy_a now belong to dest_b
    assert a1.sub == "dest_b"
    assert a2.sub == "dest_b"
    assert b1.sub == "dest_b"

    # Overrides updated
    assert CategoryOverridesManager.load()["merge_1"] == "dest_b"

    # Verify UI reflects destination category
    tab._filter_by_rail("text")
    tab._on_sub_category_clicked("dest_b")
    # All 3 items should match dest_b
    matching = [a for a in tab.all_assets if tab._get_asset_sub(a) == "dest_b"]
    assert len(matching) == 3


def test_merge_category_rejects_self_merge_and_invalid(qapp):
    """Test merge_category rejects self-merge and empty target with zero data loss."""
    tab = TabAssets(headless=True)
    a1 = StudioAsset(id="m_same_1", name="Same", tab="text", category="same_cat", sub="same_cat")
    tab.all_assets.append(a1)
    tab._populate_cards()

    # Self-merge
    assert tab.merge_category("same_cat", "same_cat") is False
    assert a1.sub == "same_cat"

    # Empty inputs
    assert tab.merge_category("", "same_cat") is False
    assert tab.merge_category("same_cat", "") is False
    assert tab.merge_category("   ", "   ") is False
    assert a1.sub == "same_cat"


def test_storage_manager_merge_category_direct(tmp_path):
    """Directly test StorageManager.merge_category handles taxonomy, registry, and links."""
    sm = StorageManager(config_dir=tmp_path / "cfg", reserve_bytes=0)
    sm.configure(tmp_path / "library")

    # Register mock taxonomy and items
    tax = sm.taxonomy()
    tax["src_cat"] = "Nguồn"
    tax["dst_cat"] = "Đích"
    from src.core.storage_manager import atomic_json
    atomic_json(sm.meta / "taxonomy.json", tax)

    reg = sm.registry()
    reg["assets"] = [
        {"id": "item_reg_1", "category_id": "src_cat", "name": "Item 1"},
        {"id": "item_reg_2", "category_id": "dst_cat", "name": "Item 2"}
    ]
    atomic_json(sm.meta / "registry.json", reg)

    links = [
        {"id": "folder_link_1", "category": "src_cat"}
    ]
    atomic_json(sm.root / "03_local_linked/links.json", links)

    # Self merge rejected with ValueError
    with pytest.raises(ValueError):
        sm.merge_category("src_cat", "src_cat")

    # Destination not in taxonomy rejected with ValueError
    with pytest.raises(ValueError):
        sm.merge_category("src_cat", "non_existent_dst")

    # System category rejected with ValueError
    with pytest.raises(ValueError):
        sm.merge_category("fav", "dst_cat")
    with pytest.raises(ValueError):
        sm.merge_category("src_cat", "all")

    # Valid merge
    ok = sm.merge_category("src_cat", "dst_cat")
    assert ok is True

    # src_cat removed from taxonomy, dst_cat preserved
    tax_after = sm.taxonomy()
    assert "src_cat" not in tax_after
    assert "dst_cat" in tax_after

    # item_reg_1 moved to dst_cat
    reg_after = {a["id"]: a["category_id"] for a in sm.registry().get("assets", [])}
    assert reg_after["item_reg_1"] == "dst_cat"
    assert reg_after["item_reg_2"] == "dst_cat"

    # folder link moved to dst_cat
    links_after = sm.links()
    assert len(links_after) == 1
    assert links_after[0]["category"] == "dst_cat"


# =========================================================================
# 6. Codex R1 Review Remediation Tests (F1 - F4)
# =========================================================================

def test_f1_cross_tab_same_named_category_merge_isolation(qapp):
    """F1: Merging/renaming categories in one tab must not affect same-named categories in other tabs."""
    tab = TabAssets(headless=True)
    sfx_asset = StudioAsset(id="sfx_iso_1", name="Boom SFX", tab="sfx", category="sfx", sub="Action")
    text_asset = StudioAsset(id="text_iso_1", name="Action Title", tab="text", category="text", sub="Action")
    tab.all_assets.extend([sfx_asset, text_asset])
    tab._populate_cards()

    # Switch to SFX rail tab
    tab._filter_by_rail("sfx")
    assert tab.current_rail_tab == "sfx"

    # Merge "Action" -> "Combat" in SFX
    ok = tab.merge_category("Action", "Combat")
    assert ok is True

    # SFX asset moved to "Combat"
    assert tab._get_asset_sub(sfx_asset) == "Combat"
    # Text asset remains isolated in "Action"
    assert tab._get_asset_sub(text_asset) == "Action"

    # Overrides file must only contain sfx_iso_1 -> Combat, NOT text_iso_1
    overrides = CategoryOverridesManager.load()
    assert overrides.get("sfx_iso_1") == "Combat"
    assert "text_iso_1" not in overrides

    # Rename "Combat" -> "Battle" in SFX explicitly
    tab._rename_sub_category("Combat", "Battle", tab="sfx")
    assert tab._get_asset_sub(sfx_asset) == "Battle"
    assert tab._get_asset_sub(text_asset) == "Action"
    overrides_after = CategoryOverridesManager.load()
    assert overrides_after.get("sfx_iso_1") == "Battle"
    assert "text_iso_1" not in overrides_after


def test_f2_merge_persistence_freshly_imported_items_and_reload(qapp):
    """F2: Freshly imported items with no prior overrides must persist merged category across reload/snapshots."""
    tab = TabAssets(headless=True)
    # Fresh items not in category_overrides.json
    f1 = StudioAsset(id="fresh_1", name="Fresh Item 1", tab="sfx", category="sfx", sub="FreshOld")
    f2 = StudioAsset(id="fresh_2", name="Fresh Item 2", tab="sfx", category="sfx", sub="FreshOld")
    tab.all_assets.extend([f1, f2])
    tab._populate_cards()

    # Pre-condition: no overrides
    assert CategoryOverridesManager.load() == {}

    # Set rail to sfx
    tab._filter_by_rail("sfx")

    # Merge FreshOld -> FreshMerged
    ok = tab.merge_category("FreshOld", "FreshMerged")
    assert ok is True
    assert tab._get_asset_sub(f1) == "FreshMerged"
    assert tab._get_asset_sub(f2) == "FreshMerged"

    # Persistence verification: overrides must contain BOTH fresh_1 and fresh_2
    overrides = CategoryOverridesManager.load()
    assert overrides.get("fresh_1") == "FreshMerged"
    assert overrides.get("fresh_2") == "FreshMerged"

    # Simulate fresh reload or snapshot: create fresh asset objects resetting to their base sub "FreshOld"
    f1_reloaded = StudioAsset(id="fresh_1", name="Fresh Item 1", tab="sfx", category="sfx", sub="FreshOld")
    f2_reloaded = StudioAsset(id="fresh_2", name="Fresh Item 2", tab="sfx", category="sfx", sub="FreshOld")
    tab.all_assets = [f1_reloaded, f2_reloaded]
    tab._apply_category_overrides()

    # Both must retain "FreshMerged" without reverting to "FreshOld"
    assert tab._get_asset_sub(f1_reloaded) == "FreshMerged"
    assert tab._get_asset_sub(f2_reloaded) == "FreshMerged"


def test_f3_move_category_persistence_path_and_storage_clean(qapp, tmp_path):
    """F3: move_assets_category persists cleanly via CategoryOverridesManager without storage corruption."""
    tab = TabAssets(headless=True)
    a1 = StudioAsset(id="clean_move_1", name="Move Target", tab="trans", category="trans", sub="Slide")
    tab.all_assets.append(a1)
    tab._populate_cards()

    moved = tab.move_assets_category([a1], "Zoom")
    assert moved == 1
    assert tab._get_asset_sub(a1) == "Zoom"
    assert CategoryOverridesManager.load().get("clean_move_1") == "Zoom"

    # StorageManager merge_category does not leave duplicate meta/links.json
    sm = StorageManager(config_dir=tmp_path / "f3_cfg", reserve_bytes=0)
    sm.configure(tmp_path / "f3_lib")
    tax = sm.taxonomy()
    tax["cat_a"] = "Cat A"
    tax["cat_b"] = "Cat B"
    from src.core.storage_manager import atomic_json
    atomic_json(sm.meta / "taxonomy.json", tax)
    atomic_json(sm.root / "03_local_linked/links.json", [{"id": "link1", "category": "cat_a"}])

    ok = sm.merge_category("cat_a", "cat_b")
    assert ok is True
    # Ensure meta/links.json was not created
    assert not (sm.meta / "links.json").exists()
    # Ensure 03_local_linked/links.json was updated
    links = sm.links()
    assert len(links) == 1
    assert links[0]["category"] == "cat_b"


def test_f4_atomic_saves_and_favorite_group_write_failure_rollback(qapp, monkeypatch, tmp_path):
    """F4: Atomic saves prevent corruption on write failure, and favorite grouping rolls back on error."""
    # 1. Test atomic save does not corrupt existing files on disk
    fav_file = tmp_path / "data" / "favorites.json"
    FavoritesManager.save({"fav_saved_1", "fav_saved_2"})
    assert fav_file.exists()
    with open(fav_file, "r", encoding="utf-8") as f:
        original_fav_data = f.read()

    orig_replace = os.replace
    def mock_replace_fail(src, dst):
        raise OSError("Disk write failed / out of space")
    monkeypatch.setattr(os, "replace", mock_replace_fail)

    save_res = FavoritesManager.save({"new_fav"})
    assert save_res is False
    # Original file must remain intact and not truncated
    with open(fav_file, "r", encoding="utf-8") as f:
        assert f.read() == original_fav_data

    monkeypatch.setattr(os, "replace", orig_replace)

    # 2. Test _on_favorite_toggled rollback when FavoritesManager.save fails
    tab = TabAssets(headless=True)
    asset_rb = StudioAsset(id="rb_asset_1", name="Rollback Asset", tab="text", category="clean", sub="clean")
    tab.all_assets.append(asset_rb)
    tab._populate_cards()

    orig_fav_save = FavoritesManager.save
    monkeypatch.setattr(FavoritesManager, "save", lambda data: False)
    res = tab._on_favorite_toggled("rb_asset_1", True, group="")
    assert res is False
    assert "rb_asset_1" not in tab.favorites
    assert tab.asset_model.is_favorite("rb_asset_1") is False
    monkeypatch.setattr(FavoritesManager, "save", orig_fav_save)

    # 3. Test _on_favorite_toggled rollback when CollectionsManager fails
    orig_add = CollectionsManager.add_asset_to_collection
    monkeypatch.setattr(CollectionsManager, "add_asset_to_collection", lambda c, a: False)

    res_grp = tab._on_favorite_toggled("rb_asset_1", True, group="FailGroup")
    assert res_grp is False
    assert "rb_asset_1" not in tab.favorites
    assert tab.asset_model.is_favorite("rb_asset_1") is False
    # Disk favorites must also not contain rb_asset_1
    assert "rb_asset_1" not in FavoritesManager.load()
    monkeypatch.setattr(CollectionsManager, "add_asset_to_collection", orig_add)

    # 4. Test CategoryOverridesManager write failure in merge_category and move_assets_category
    orig_batch = CategoryOverridesManager.set_overrides_batch
    monkeypatch.setattr(CategoryOverridesManager, "set_overrides_batch", lambda mapping: False)
    fail_merge = tab.merge_category("clean", "dirty")
    assert fail_merge is False
    assert tab._get_asset_sub(asset_rb) == "clean"

    fail_move = tab.move_assets_category([asset_rb], "dirty")
    assert fail_move == 0
    assert tab._get_asset_sub(asset_rb) == "clean"
    monkeypatch.setattr(CategoryOverridesManager, "set_overrides_batch", orig_batch)


def test_rename_sub_category_immediate_proxy_row_count_and_reload(qapp):
    """Test _rename_sub_category immediately updates proxy row count and persists across reload without affecting same-name categories in other tabs."""
    tab = TabAssets(headless=True)
    sfx_item_1 = StudioAsset(id="sfx_rn_1", name="SFX Rename 1", tab="sfx", category="sfx", sub="OldSub")
    sfx_item_2 = StudioAsset(id="sfx_rn_2", name="SFX Rename 2", tab="sfx", category="sfx", sub="OldSub")
    text_item_1 = StudioAsset(id="text_rn_1", name="Text Same Name", tab="text", category="text", sub="OldSub")
    tab.all_assets.extend([sfx_item_1, sfx_item_2, text_item_1])
    tab._populate_cards()

    # In SFX rail tab
    tab._filter_by_rail("sfx")
    tab._on_sub_category_clicked("OldSub")
    assert tab.asset_filter.rowCount() == 2

    # Rename OldSub -> NewSub in SFX
    tab._rename_sub_category("OldSub", "NewSub", tab="sfx")

    # Immediately destination proxy row count must reflect 2 items without reload
    assert tab.current_sub_cat == "NewSub"
    assert tab.asset_filter.rowCount() == 2
    assert tab._get_asset_sub(sfx_item_1) == "NewSub"
    assert tab._get_asset_sub(sfx_item_2) == "NewSub"

    # Same-name category in other tab (text) is completely unaffected
    tab._filter_by_rail("text")
    tab._on_sub_category_clicked("OldSub")
    assert tab.asset_filter.rowCount() == 1
    assert tab._get_asset_sub(text_item_1) == "OldSub"

    tab._on_sub_category_clicked("NewSub")
    assert tab.asset_filter.rowCount() == 0

    # Test persistence on simulated application reload
    tab2 = TabAssets(headless=True)
    tab2.all_assets = [
        StudioAsset(id="sfx_rn_1", name="SFX Rename 1", tab="sfx", category="sfx", sub="OldSub"),
        StudioAsset(id="sfx_rn_2", name="SFX Rename 2", tab="sfx", category="sfx", sub="OldSub"),
        StudioAsset(id="text_rn_1", name="Text Same Name", tab="text", category="text", sub="OldSub"),
    ]
    tab2._apply_category_overrides()
    tab2._populate_cards()

    tab2._filter_by_rail("sfx")
    tab2._on_sub_category_clicked("NewSub")
    assert tab2.asset_filter.rowCount() == 2

    tab2._filter_by_rail("text")
    tab2._on_sub_category_clicked("OldSub")
    assert tab2.asset_filter.rowCount() == 1


def test_non_favorite_item_in_collection_inspector_and_ungrouped_selector(qapp):
    """Test non-favorite items in custom collections (inspector path), real collection named 'ungrouped', and favorite removal."""
    tab = TabAssets(headless=True)
    non_fav_item = StudioAsset(id="non_fav_sfx", name="Impact SFX", tab="sfx", category="sfx", sub="Hit")
    tab.all_assets.append(non_fav_item)
    tab._populate_cards()

    # Pre-condition: item is not in favorites
    assert "non_fav_sfx" not in tab.favorites
    assert tab.asset_model.is_favorite("non_fav_sfx") is False

    # Simulate Inspector adding non-favorite item to collection
    CollectionsManager.create_collection("Inspector Hits")
    CollectionsManager.add_asset_to_collection("Inspector Hits", "non_fav_sfx")

    # Switch to Favorites tab
    tab._filter_by_rail("fav")

    # 1. 'All Favorites' requires favorite: non-favorite item must NOT appear
    tab._on_sub_category_clicked("all")
    assert tab.asset_filter.rowCount() == 0

    # 2. 'Chưa phân nhóm' (synthetic ungrouped) requires favorite: must NOT appear
    tab._on_sub_category_clicked("coll:__ungrouped__")
    assert tab.asset_filter.rowCount() == 0

    # 3. Specific collection 'Inspector Hits': membership is independent, must appear!
    tab._on_sub_category_clicked("coll:Inspector Hits")
    assert tab.asset_filter.rowCount() == 1
    idx = tab.asset_filter.index(0, 0)
    assert tab.asset_filter.data(idx) == "Impact SFX"

    # 4. Test real collection named 'ungrouped' (must not be hijacked by synthetic selector)
    CollectionsManager.create_collection("ungrouped")
    real_ungrouped_item = StudioAsset(id="item_named_ungrouped", name="In Real Ungrouped Collection", tab="sfx", category="sfx")
    tab.all_assets.append(real_ungrouped_item)
    tab._populate_cards()
    CollectionsManager.add_asset_to_collection("ungrouped", "item_named_ungrouped")

    # Viewing real collection 'coll:ungrouped' shows real_ungrouped_item
    tab._on_sub_category_clicked("coll:ungrouped")
    assert tab.asset_filter.rowCount() == 1
    idx2 = tab.asset_filter.index(0, 0)
    assert tab.asset_filter.data(idx2) == "In Real Ungrouped Collection"

    # Viewing synthetic ungrouped selector 'coll:__ungrouped__' does not hijack or show real_ungrouped_item
    tab._on_sub_category_clicked("coll:__ungrouped__")
    assert tab.asset_filter.rowCount() == 0

    # 5. Favorite removal semantics consistent with UI
    fav_item = StudioAsset(id="fav_sync_1", name="Synced Fav", tab="text", category="clean")
    tab.all_assets.append(fav_item)
    tab._populate_cards()
    tab._on_favorite_toggled("fav_sync_1", True, group="Inspector Hits")
    assert "fav_sync_1" in tab.favorites
    assert "fav_sync_1" in CollectionsManager.get_collection_assets("Inspector Hits")

    # Un-favorite removes from favorites and removes from all collections
    tab._on_favorite_toggled("fav_sync_1", False)
    assert "fav_sync_1" not in tab.favorites
    assert "fav_sync_1" not in CollectionsManager.get_collection_assets("Inspector Hits")
    assert tab.asset_model.is_favorite("fav_sync_1") is False


