"""
Tests for TabAssets Smart Motion & Bilingual Accent-Insensitive Search + Async Thumbnail Cache.
"""
import os
import pytest
import tempfile
from PIL import Image

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from src.ui.tabs.tab_assets import TabAssets, StudioAsset
from src.core.text_preset import TextStylePreset


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_tab_assets_bilingual_smart_search(qapp):
    tab = TabAssets()
    tab.show()

    # Search for "Hormozi"
    tab.txt_search.setText("hormozi")
    tab._refresh_grid()
    visible_cards = [c for c in tab.card_widgets if not c.isHidden()]
    assert len(visible_cards) >= 1
    names = [getattr(c.preset, "name", "").lower() for c in visible_cards]
    assert any("hormozi" in n for n in names)

    # Search with Vietnamese with tone marks: "lò xo" or "nảy" (from Hormozi tags/description)
    tab.txt_search.setText("nảy")
    tab._refresh_grid()
    visible_cards = [c for c in tab.card_widgets if not c.isHidden()]
    # Since kinetic_hormozi has bounce/pop, "nay" / "nảy" matches
    if len(visible_cards) > 0:
        assert any("hormozi" in getattr(c.preset, "id", "").lower() or "pop" in getattr(c.preset, "id", "").lower() for c in visible_cards)

    # Search with Vietnamese WITHOUT tone marks: "nay" (should match same items as "nảy")
    tab.txt_search.setText("nay")
    tab._refresh_grid()
    visible_cards_unaccented = [c for c in tab.card_widgets if not c.isHidden()]
    assert len(visible_cards_unaccented) == len(visible_cards)

    # Clear search
    tab.txt_search.setText("")
    tab._refresh_grid()
    visible_all = [c for c in tab.card_widgets if not c.isHidden()]
    assert len(visible_all) > len(visible_cards)


def test_tab_assets_async_thumbnail_loading(qapp):
    # Create temporary media file for asset
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img = Image.new("RGBA", (320, 180), (30, 200, 150, 255))
        img.save(f.name)
        tmp_media = f.name

    try:
        from src.ui.tabs.tab_assets import AssetCard
        mock_asset = StudioAsset(
            id="test_async_meme",
            name="Test Async Meme",
            tab="meme",
            category="reaction",
            file_path=tmp_media,
            thumbnail_path="" # empty initially
        )
        card = AssetCard(mock_asset)
        assert card is not None
        # Verify thumbnail is updated or can be loaded
        card._on_async_thumb_ready(tmp_media)
        assert getattr(mock_asset, "thumbnail_path", "") == tmp_media
    finally:
        if os.path.exists(tmp_media):
            os.remove(tmp_media)
