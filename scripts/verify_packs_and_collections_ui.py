import os
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSize, Qt

from src.ui.tabs.tab_assets import TabAssets, StudioAsset
from src.core.collections_manager import CollectionsManager
from src.core.pack_manager import PackManager, PackManifest

def main():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    artifact_dir = Path("/home/vanntruongg/.gemini/antigravity/brain/17b4c02d-da1e-4059-8e60-495e0079d670")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Setup demo collections and installed pack for visual preview
    CollectionsManager.create_collection("Dự Án Vlog Du Lịch")
    CollectionsManager.create_collection("TikTok Viral SFX")
    CollectionsManager.create_collection("Intro Bùng Nổ")

    tab = TabAssets(headless=False)
    tab.resize(1280, 800)
    tab.show()
    app.processEvents()

    sample_lut = StudioAsset(
        id="secret_sauce_ferrari",
        name="Secret Ferrari Red 3D",
        tab="lut",
        category="cinema",
        sub="Secret Sauce Cinema LUTs",
        pack_name="Secret Sauce Cinema LUTs"
    )
    if not any(a.id == "secret_sauce_ferrari" for a in tab.all_assets):
        tab.all_assets.append(sample_lut)
        tab._populate_cards()

    CollectionsManager.add_asset_to_collection("Dự Án Vlog Du Lịch", "secret_sauce_ferrari")
    CollectionsManager.add_asset_to_collection("Dự Án Vlog Du Lịch", "clean_rec709")
    tab.favorites.add("secret_sauce_ferrari")
    tab.favorites.add("clean_rec709")

    # 1. Capture LUTs tab with installed packs (All)
    tab._filter_by_rail("lut")
    tab.selected_asset = sample_lut
    tab._update_inspector_details()
    app.processEvents()

    pix_lut = tab.grab()
    pix_lut.save(str(artifact_dir / "test_visual_packs_column2.png"))

    # 1b. Capture LUTs tab with Pack FILTERED
    tab._on_sub_category_clicked("pack:Secret Sauce Cinema LUTs")
    app.processEvents()
    pix_pack_filtered = tab.grab()
    pix_pack_filtered.save(str(artifact_dir / "test_visual_pack_filtered.png"))
    print("Saved test_visual_pack_filtered.png")

    # 2. Switch to Favorites tab (Của tôi)
    tab._filter_by_rail("fav")
    app.processEvents()

    pix_fav = tab.grab()
    pix_fav.save(str(artifact_dir / "test_visual_custom_collections.png"))

    # 3. Filter by "Dự Án Vlog Du Lịch" collection
    tab._on_sub_category_clicked("coll:Dự Án Vlog Du Lịch")
    app.processEvents()

    pix_coll = tab.grab()
    pix_coll.save(str(artifact_dir / "test_visual_collection_filtered.png"))

    tab.close()
    print("All screenshots generated successfully.")

if __name__ == "__main__":
    main()
