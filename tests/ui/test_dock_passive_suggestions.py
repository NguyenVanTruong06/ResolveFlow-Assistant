"""
tests/ui/test_dock_passive_suggestions.py
Integration tests for passive suggestion click behavior in EdgeDockWindow (G04-04C1-I).
"""
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.library.asset_model import AssetListModel
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.services.search_index import SearchIndex


@pytest.fixture
def app():
    instance = QApplication.instance()
    if not instance:
        instance = QApplication([])
    return instance


@pytest.fixture
def dock_window(app, tmp_path):
    """Creates an EdgeDockWindow instance with mock services in offscreen mode."""
    with patch("src.ui.lux.shell.edge_dock.ResolveStateService"):
        dock = EdgeDockWindow()
        dock.hide()
        if not dock._insert_service:
            dock._insert_service = MagicMock()
        yield dock
        dock.close()


def test_suggestion_click_is_completely_passive(dock_window):
    """
    Clicking a suggestion chip selects the asset, scrolls grid, opens sheet and action bar,
    but NEVER calls preview_asset, never triggers audio/video playback, and never auto-inserts.
    """
    assets = [
        {
            "id": "sfx_riser_01",
            "name": "Cinematic Riser 01",
            "category": "sfx",
            "tab": "sfx",
            "preview_path": "/tmp/riser.wav",
            "file_path": "/tmp/riser.wav",
            "availability": "online",
            "duration": 3.5,
        },
        {
            "id": "tpl_glitch_01",
            "name": "Glitch Transition",
            "category": "trans",
            "tab": "trans",
            "availability": "online",
        }
    ]
    dock_window.set_assets(assets)

    # Spy on preview_asset and insert
    spy_preview = MagicMock()
    dock_window.grid.hover_controller.preview_asset = spy_preview

    spy_insert = MagicMock()
    dock_window._insert_service.insert = spy_insert

    # Trigger suggestion for audio asset
    dock_window._on_suggested_asset("sfx_riser_01")

    # 1. Verification of passive UI updates
    assert dock_window._selected_asset is not None
    assert dock_window._selected_asset.get("id") == "sfx_riser_01"
    assert dock_window.category_nav.current_category == "sfx"
    assert dock_window.sheet.is_open or dock_window.sheet.isVisible()
    assert dock_window.action_bar._current_asset is not None
    assert dock_window.action_bar._current_asset.get("id") == "sfx_riser_01"

    # 2. Strict verification: ZERO autoplay and ZERO auto-insert calls
    spy_preview.assert_not_called()
    spy_insert.assert_not_called()


def test_suggestion_click_missing_id_resets_selection_cleanly(dock_window):
    """
    If suggested asset ID does not exist in model, show toast warning and cleanly
    clear stale selection from dock, action bar, and sheet.
    """
    assets = [
        {"id": "existing_01", "name": "Existing Asset", "category": "text", "tab": "text", "availability": "online"}
    ]
    dock_window.set_assets(assets)

    # First, select an existing asset
    dock_window._on_suggested_asset("existing_01")
    assert dock_window._selected_asset is not None
    assert dock_window.action_bar._current_asset is not None

    # Now suggest an ID that does not exist
    spy_toast = MagicMock()
    dock_window._get_toast = MagicMock(return_value=spy_toast)

    dock_window._on_suggested_asset("non_existent_asset_id_999")

    # 1. Toast must be shown with warning
    spy_toast.show_message.assert_called_once()
    args, kwargs = spy_toast.show_message.call_args
    assert "non_existent_asset_id_999" in args[0]
    assert kwargs.get("level") == "warning"

    # 2. Stale selection MUST be cleared from dock and action bar
    assert dock_window._selected_asset is None, "Selected asset must be reset to None"
    assert dock_window.action_bar._current_asset is None, "Action bar must be reset to None"
    assert not dock_window.sheet.is_open, "Sheet inspector must be closed"
