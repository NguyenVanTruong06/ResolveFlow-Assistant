"""
tests/ui/test_tab_assets_category_merge.py
Regression tests for TabAssets category merge behavior:
- Dialog presents translated labels for default categories instead of raw slugs.
- Dialog selection maps to canonical destination identity (slug for default, normalized name for custom).
- Bounded to existing categories (editable=False); no silent category creation.
- Source category is hidden from sidebar after merge (0 items).
- Target category contains union of items with no duplicates.
- CategoryOverridesManager persistence across simulated reloads.
- User cancellation and CategoryOverridesManager save failure leave items unchanged.
- Isolation across rail tabs (other tabs unaffected).
- Unicode normalization (NFC/NFD) matching.
- Pack groups distinction and explicit rejection.
"""

import unicodedata
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog

from src.ui.tabs.tab_assets import (
    TabAssets,
    StudioAsset,
    CategoryOverridesManager,
    FavoritesManager,
    CollectionsManager,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    overrides_file = data_dir / "category_overrides.json"
    fav_file = data_dir / "favorites.json"
    coll_file = data_dir / "collections.json"

    monkeypatch.setattr(CategoryOverridesManager, "FILE_PATH", overrides_file)
    monkeypatch.setattr(FavoritesManager, "FILE_PATH", fav_file)
    monkeypatch.setattr(CollectionsManager, "FILE_PATH", coll_file)
    CategoryOverridesManager._cache = None
    CategoryOverridesManager._cache_mtime = -1.0
    CategoryOverridesManager._cache_path = ""


def _create_sample_tab():
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    # Default kinetic items (slug 'kinetic', display 'Kinetic / Nhảy chữ')
    k1 = StudioAsset(id="txt_k1", name="Kinetic Hormozi", tab="text", category="kinetic", sub="kinetic")
    k2 = StudioAsset(id="txt_k2", name="Bounce Word", tab="text", category="kinetic", sub="kinetic")

    # Source custom category items
    s1 = StudioAsset(id="src_1", name="Custom Source 1", tab="text", category="My Old Group", sub="My Old Group")
    s2 = StudioAsset(id="src_2", name="Custom Source 2", tab="text", category="My Old Group", sub="My Old Group")

    # Another custom category items
    c1 = StudioAsset(id="cust_1", name="Custom Target 1", tab="text", category="My Custom Target", sub="My Custom Target")

    # Unrelated item in another tab ('trans') with same sub name 'My Old Group'
    tr1 = StudioAsset(id="tr_1", name="Transition 1", tab="trans", category="My Old Group", sub="My Old Group")

    tab.all_assets = [k1, k2, s1, s2, c1, tr1]
    tab._populate_cards()
    tab._rebuild_categories_column()
    return tab, [k1, k2], [s1, s2], [c1], tr1


def test_prompt_merge_category_dialog_translated_default_target(qapp):
    """A1: Driving dialog selection with translated label 'Kinetic / Nhảy chữ' maps to slug 'kinetic'."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    # Capture arguments passed to QInputDialog.getItem
    captured_items = []
    captured_editable = None

    def fake_get_item(parent, title, label, items, current, editable):
        nonlocal captured_items, captured_editable
        captured_items = list(items)
        captured_editable = editable
        # User selects translated default label
        return "Kinetic / Nhảy chữ", True

    with patch.object(QInputDialog, "getItem", side_effect=fake_get_item):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    # Assert dialog properties
    assert captured_editable is False, "Merge dialog must have editable=False to prevent accidental creation"
    assert "Kinetic / Nhảy chữ" in captured_items, "Translated label must be presented, not raw slug"
    assert "kinetic" not in captured_items, "Raw slug must not be displayed to user"
    assert "My Old Group" not in captured_items, "Source category must be excluded"
    assert "all" not in captured_items and "Tất cả" not in captured_items

    # Assets membership after merge:
    for item in src_items:
        assert item.sub == "kinetic", "Source items must have their identity mapped to canonical slug 'kinetic'"
    for item in kin_items:
        assert item.sub == "kinetic"

    # CategoryOverridesManager persisted overrides with canonical slug
    overrides = CategoryOverridesManager.load()
    assert overrides["src_1"] == "kinetic"
    assert overrides["src_2"] == "kinetic"

    # Verify no spurious 'Kinetic / Nhảy chữ' category was created in sub_cats
    tab._rebuild_categories_column()
    rendered_cids = []
    for i in range(tab.cats_vbox.count()):
        w = tab.cats_vbox.itemAt(i).widget()
        if w and w.property("sub_cat_id"):
            rendered_cids.append(w.property("sub_cat_id"))
    assert "Kinetic / Nhảy chữ" not in rendered_cids
    assert "kinetic" in rendered_cids


def test_prompt_merge_category_dialog_existing_custom_target(qapp):
    """A2: Merging into an existing custom target category."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    def fake_get_item(parent, title, label, items, current, editable):
        assert "My Custom Target" in items
        return "My Custom Target", True

    with patch.object(QInputDialog, "getItem", side_effect=fake_get_item):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    for item in src_items:
        assert item.sub == "My Custom Target"

    overrides = CategoryOverridesManager.load()
    assert overrides["src_1"] == "My Custom Target"
    assert overrides["src_2"] == "My Custom Target"


def test_merge_category_source_hidden_after_merge(qapp):
    """A3: Source category has 0 items after merge and is hidden from sidebar."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", True)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    tab._rebuild_categories_column()
    rendered_cids = []
    for i in range(tab.cats_vbox.count()):
        w = tab.cats_vbox.itemAt(i).widget()
        if w and w.property("sub_cat_id"):
            rendered_cids.append(w.property("sub_cat_id"))

    assert "My Old Group" not in rendered_cids, "Source category with 0 items must be hidden from sidebar"


def test_merge_category_target_union_no_duplicates(qapp):
    """A4: Target category contains the union of existing and merged items without duplicates."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", True)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    target_items = [a for a in tab.all_assets if tab._get_asset_tab(a) == "text" and tab._get_asset_sub(a) == "kinetic"]
    assert len(target_items) == 4  # 2 original + 2 merged
    unique_ids = {tab._get_asset_id(a) for a in target_items}
    assert len(unique_ids) == 4
    assert unique_ids == {"txt_k1", "txt_k2", "src_1", "src_2"}


def test_merge_category_persistence_and_reload(qapp):
    """A5: Category overrides persist and reload cleanly in a fresh TabAssets instance."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", True)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    # Simulate reload
    tab2 = TabAssets(headless=True)
    tab2.current_rail_tab = "text"
    tab2.all_assets = [
        StudioAsset(id="txt_k1", name="Kinetic Hormozi", tab="text", category="kinetic", sub="kinetic"),
        StudioAsset(id="txt_k2", name="Bounce Word", tab="text", category="kinetic", sub="kinetic"),
        StudioAsset(id="src_1", name="Custom Source 1", tab="text", category="My Old Group", sub="My Old Group"),
        StudioAsset(id="src_2", name="Custom Source 2", tab="text", category="My Old Group", sub="My Old Group"),
        StudioAsset(id="cust_1", name="Custom Target 1", tab="text", category="My Custom Target", sub="My Custom Target"),
    ]
    tab2._apply_category_overrides()
    tab2._rebuild_categories_column()

    k_items_reloaded = [a for a in tab2.all_assets if tab2._get_asset_sub(a) == "kinetic"]
    assert len(k_items_reloaded) == 4
    assert {tab2._get_asset_id(a) for a in k_items_reloaded} == {"txt_k1", "txt_k2", "src_1", "src_2"}


def test_merge_category_cancel_and_save_failure(qapp, monkeypatch):
    """A6: Cancel in dialog leaves items unchanged; CategoryOverridesManager failure rolls back."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    # Case A: User cancels dialog
    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", False)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    for item in src_items:
        assert item.sub == "My Old Group"
    assert CategoryOverridesManager.load() == {}

    # Case B: Storage/persistence failure
    monkeypatch.setattr(CategoryOverridesManager, "set_overrides_batch", lambda mapping: False)
    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", True)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    for item in src_items:
        assert item.sub == "My Old Group", "On save failure, in-memory items must not be mutated"


def test_merge_category_other_tab_unaffected(qapp):
    """A7: Assets in other tabs (e.g. 'trans') with the same sub name are not changed."""
    tab, kin_items, src_items, cust_items, tr1 = _create_sample_tab()

    with patch.object(QInputDialog, "getItem", return_value=("Kinetic / Nhảy chữ", True)):
        tab._prompt_merge_category("My Old Group", "My Old Group")

    # Item tr1 in 'trans' tab must remain 'My Old Group'
    assert tr1.tab == "trans"
    assert tr1.sub == "My Old Group"
    assert "tr_1" not in CategoryOverridesManager.load()


def test_merge_category_unicode_normalization(qapp):
    """A8: Handles Unicode NFD vs NFC for category matching."""
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    # NFD decomposed Vietnamese string: "Đồ họa"
    nfd_name = unicodedata.normalize("NFD", "Đồ họa")
    nfc_name = unicodedata.normalize("NFC", "Đồ họa")

    a1 = StudioAsset(id="uni_1", name="Item 1", tab="text", category=nfd_name, sub=nfd_name)
    a2 = StudioAsset(id="uni_2", name="Item 2", tab="text", category="Kinetic", sub="kinetic")
    tab.all_assets = [a1, a2]
    tab._populate_cards()

    # User merges nfc_name into kinetic
    ok = tab.merge_category(nfc_name, "kinetic", tab="text")
    assert ok is True
    assert a1.sub == "kinetic"


def test_merge_category_pack_rejected(qapp):
    """A9: Merging a pack or into a pack is rejected without pretending success."""
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    p1 = StudioAsset(id="p_1", name="Pack Item", tab="text", category="text", sub="pack:SummerPack")
    tab.all_assets = [p1]

    with patch.object(QMessageBox, "warning") as mock_warn:
        tab._prompt_merge_category("pack:SummerPack", "SummerPack")
        assert mock_warn.called, "Pack merge prompt must show an explicit warning dialog"

    assert tab.merge_category("pack:SummerPack", "kinetic") is False
    assert tab.merge_category("kinetic", "pack:SummerPack") is False


# ===========================================================================
# R2-02: Destination Alias Canonicalization & Filtered Visible Union Tests
# ===========================================================================

def test_merge_category_destination_alias_canonicalization_and_union(qapp):
    """R2-02: Existing destination item with sub='Kinetic / Nhảy chữ' + source sub='Old' yields unified visible items."""
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    dest_item = StudioAsset(id="dest_1", name="Dest Kinetic", tab="text", category="kinetic", sub="Kinetic / Nhảy chữ")
    src_item = StudioAsset(id="src_1", name="Source Old", tab="text", category="Old", sub="Old")

    tab.all_assets = [dest_item, src_item]
    tab._populate_cards()
    tab._rebuild_categories_column()

    # 1. BEFORE merge: clicking canonical 'kinetic' must show existing translated target item!
    tab._on_sub_category_clicked("kinetic")
    assert tab.asset_filter is not None
    assert tab.asset_filter.rowCount() == 1, "Existing translated target item must be visible when clicking canonical 'kinetic' BEFORE merge"
    from src.ui.lux.library.asset_model import IdRole
    assert tab.asset_filter.index(0, 0).data(IdRole) == "dest_1"

    # 2. Perform merge
    ok = tab.merge_category("Old", "kinetic", tab="text")
    assert ok is True

    # Both items must have canonical sub == 'kinetic'
    assert dest_item.sub == "kinetic"
    assert src_item.sub == "kinetic"

    # Both overrides must be recorded as 'kinetic'
    overrides = CategoryOverridesManager.load()
    assert overrides.get("dest_1") == "kinetic"
    assert overrides.get("src_1") == "kinetic"

    # 3. AFTER merge: both visible in target union!
    tab._on_sub_category_clicked("kinetic")
    filter_count = tab.asset_filter.rowCount()
    assert filter_count == 2, f"Target union must contain 2 items, got {filter_count}"

    visible_ids = {tab.asset_filter.index(r, 0).data(IdRole) for r in range(filter_count)}
    assert "dest_1" in visible_ids
    assert "src_1" in visible_ids


def test_sidebar_counts_canonicalize_translated_labels(qapp):
    """R2-02: Sidebar counts map translated alias 'Kinetic / Nhảy chữ' to canonical slug 'kinetic'."""
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    item = StudioAsset(id="k_trans", name="Item Translated", tab="text", category="kinetic", sub="Kinetic / Nhảy chữ")
    tab.all_assets = [item]
    tab._populate_cards()
    tab._rebuild_categories_column()

    # Find the button for kinetic
    kinetic_btn = None
    all_cat_btns = []
    for i in range(tab.cats_vbox.count()):
        w = tab.cats_vbox.itemAt(i).widget()
        if w and hasattr(w, "property"):
            cid = w.property("sub_cat_id")
            all_cat_btns.append((cid, w.text()))
            if cid == "kinetic":
                kinetic_btn = w

    assert kinetic_btn is not None, "Button for canonical 'kinetic' must exist"
    assert "(1)" in kinetic_btn.text(), f"Kinetic button must show count (1), got: {kinetic_btn.text()}"
    # Ensure no duplicate button with cid == 'Kinetic / Nhảy chữ' was added
    custom_cids = [cid for cid, _ in all_cat_btns if cid == "Kinetic / Nhảy chữ"]
    assert len(custom_cids) == 0, "Translated label must not create a duplicate category button"


def test_merge_category_nfd_custom_destination(qapp):
    """R2-02: Destination in NFD form is canonicalized to NFC for persistence, UI union, and reload."""
    tab = TabAssets(headless=True)
    tab.current_rail_tab = "text"

    nfd_dest = unicodedata.normalize("NFD", "Thiết Kế Đồ Họa")
    nfc_dest = unicodedata.normalize("NFC", "Thiết Kế Đồ Họa")

    dest_item = StudioAsset(id="nfd_dest_1", name="Design 1", tab="text", category=nfd_dest, sub=nfd_dest)
    src_item = StudioAsset(id="src_custom_1", name="Source 1", tab="text", category="OldGroup", sub="OldGroup")

    tab.all_assets = [dest_item, src_item]
    tab._populate_cards()
    tab._rebuild_categories_column()

    ok = tab.merge_category("OldGroup", nfd_dest, tab="text")
    assert ok is True

    # Both items canonicalized to NFC
    assert dest_item.sub == nfc_dest
    assert src_item.sub == nfc_dest

    # Overrides stored in NFC
    overrides = CategoryOverridesManager.load()
    assert overrides.get("nfd_dest_1") == nfc_dest
    assert overrides.get("src_custom_1") == nfc_dest

    # Filter proxy model contains both items under NFC identity
    tab._on_sub_category_clicked(nfc_dest)
    assert tab.asset_filter.rowCount() == 2
