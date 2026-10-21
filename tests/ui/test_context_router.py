"""
Tests for Step 4: Context Router & Profiles (UX_REDESIGN.md Section 4).
Covers:
- 7 Context profiles configuration and accents
- Deferral rules (pending chip, busy states)
- Profile locking ('Khóa hồ sơ')
- Memory of category/subcategory/scroll position per page
- EdgeDock integration (accent line, header page chip, action bar label, special blocks)
- Fusion direct paste and Deliver render queue
- Sound pad shortcuts on Cut / Fairlight
"""
import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QKeyEvent

from src.ui.lux.context.context_profiles import PROFILES, PAGES, ContextProfile
from src.ui.lux.context.context_router import ContextRouter, ProfileMemory
from src.ui.lux.context.special_blocks import (
    EditSuggestBlock, CutPadsBlock, FusionCaptureBlock,
    ColorSpecialBlock, FairlightBlock, DeliverBlock, MediaBlock
)
from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.services.insert_service import InsertService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_context_profiles_definitions():
    """Verify all 7 DaVinci Resolve page profiles exist with correct colors and actions."""
    assert len(PAGES) == 7
    for p in ["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"]:
        assert p in PROFILES
        profile = PROFILES[p]
        assert profile.page == p
        assert profile.label.lower() == p.lower()
        assert profile.color.startswith("#")

    # Specific color assertions
    assert PROFILES["edit"].color == "#7c3aed"
    assert PROFILES["fusion"].color == "#06b6d4"
    assert PROFILES["color"].color == "#f59e0b"
    assert PROFILES["fairlight"].color == "#ec4899"
    assert PROFILES["cut"].color == "#a8a8b3"
    assert PROFILES["deliver"].color == "#a8a8b3"
    assert PROFILES["media"].color == "#a8a8b3"

    # Categories
    assert PROFILES["media"].categories == []
    assert PROFILES["deliver"].categories == []
    assert "text" in PROFILES["edit"].categories
    assert "sfx" in PROFILES["cut"].categories
    assert "lut" in PROFILES["color"].categories
    assert "sfx" in PROFILES["fairlight"].categories

    # Primary actions
    assert "Chèn" in PROFILES["edit"].primary_action
    assert "Fusion" in PROFILES["fusion"].primary_action
    assert "kết xuất" in PROFILES["deliver"].primary_action
    assert "footage" in PROFILES["media"].primary_action


def test_context_router_page_transition_and_accent(qapp):
    """Verify router immediately switches profile when user is not busy."""
    router = ContextRouter(initial_page="edit")
    assert router.active_profile.page == "edit"

    changed_profiles = []
    boost_cats = []
    router.profile_changed.connect(changed_profiles.append)
    router.search_boost_requested.connect(boost_cats.append)

    router.on_resolve_page_changed("fusion")
    assert len(changed_profiles) == 1
    assert changed_profiles[0].page == "fusion"
    assert router.active_profile.page == "fusion"
    assert len(boost_cats) == 1
    assert "mine" in boost_cats[0]


def test_context_router_deferral_rules(qapp):
    """Verify router defers page change when busy and applies when idle."""
    router = ContextRouter(initial_page="edit")

    pending_list = []
    changed_list = []
    router.pending_changed.connect(pending_list.append)
    router.profile_changed.connect(changed_list.append)

    # 1. User is inside dock
    router.is_in_dock = True
    assert router.is_busy is True

    # DaVinci switches to Fusion
    router.on_resolve_page_changed("fusion")

    # Should NOT switch immediately
    assert router.active_profile.page == "edit"
    assert len(changed_list) == 0
    assert router.pending_profile is not None
    assert router.pending_profile.page == "fusion"
    assert len(pending_list) == 1
    assert pending_list[-1].page == "fusion"

    # User leaves dock
    router.is_in_dock = False

    # Should automatically apply pending
    assert router.active_profile.page == "fusion"
    assert router.pending_profile is None
    assert len(changed_list) == 1
    assert changed_list[-1].page == "fusion"
    assert pending_list[-1] is None

    # 2. User has search text
    router.has_search_text = True
    assert router.is_busy is True

    router.on_resolve_page_changed("color")
    assert router.active_profile.page == "fusion"
    assert router.pending_profile.page == "color"

    # User clicks pending chip to apply immediately
    router.apply_pending()
    assert router.active_profile.page == "color"
    assert router.pending_profile is None


def test_context_router_profile_lock(qapp):
    """Verify profile lock prevents auto page changes until unlocked."""
    router = ContextRouter(initial_page="edit")
    locked_signals = []
    router.profile_locked_changed.connect(lambda l, p: locked_signals.append((l, p)))

    # Lock to Edit
    router.lock_profile("edit")
    assert router.is_locked is True
    assert len(locked_signals) == 1
    assert locked_signals[-1] == (True, "edit")

    # Try switching to Fairlight
    router.on_resolve_page_changed("fairlight")
    assert router.active_profile.page == "edit"
    assert router.pending_profile is None

    # Unlock to auto
    router.lock_profile("auto")
    assert router.is_locked is False
    assert locked_signals[-1] == (False, "edit")

    # Now switching to Fairlight works
    router.on_resolve_page_changed("fairlight")
    assert router.active_profile.page == "fairlight"


def test_profile_memory_restore(qapp):
    """Verify category, subcategory, and scroll position are preserved across page switches."""
    mem = ProfileMemory()
    mem.save("edit", category="sfx", subcategory="Impact", scroll_y=350)
    mem.save("color", category="lut", subcategory="Điện ảnh", scroll_y=0)

    edit_state = mem.get("edit")
    assert edit_state is not None
    assert edit_state["category"] == "sfx"
    assert edit_state["subcategory"] == "Impact"
    assert edit_state["scroll_y"] == 350

    color_state = mem.get("color")
    assert color_state is not None
    assert color_state["category"] == "lut"
    assert color_state["subcategory"] == "Điện ảnh"
    assert color_state["scroll_y"] == 0

    assert mem.get("unknown") is None


def test_edge_dock_context_integration(qapp):
    """Test full integration in EdgeDockWindow: accent line, page chip, action bar, special blocks."""
    mock_insert = MagicMock(spec=InsertService)
    dock = EdgeDockWindow(insert_service=mock_insert)
    dock.show()
    dock.open_dock(animated=False)

    # 1. Initial State (Edit page)
    assert dock.router.active_profile.page == "edit"
    assert dock.accent_line.width() == 2
    assert "7c3aed" in dock.accent_line.styleSheet().lower()
    assert "Edit" in dock.header.btn_page_chip.text()
    assert "Chèn" in dock.action_bar.btn_primary.text()
    assert not dock.category_nav.isHidden()
    assert not dock.grid.isHidden()

    # 2. Simulate Resolve page change to Fusion
    dock._on_resolve_state_changed(True, "fusion", "Timeline_01", "00:00:10:00")
    assert dock.router.active_profile.page == "fusion"
    assert "06b6d4" in dock.accent_line.styleSheet().lower()
    assert "Fusion" in dock.header.btn_page_chip.text()
    assert "Dán vào Fusion" in dock.action_bar.btn_primary.text()
    assert dock.special_container.currentWidget() == dock.block_fusion

    # Double click on Fusion page should trigger fusion_paste mode
    test_macro = {"id": "macro_title", "name": "Title Macro", "category": "text", "type": "macro"}
    dock._on_asset_double_clicked(test_macro)
    mock_insert.insert.assert_called_with(test_macro, mode="fusion_paste")

    # 3. Simulate Resolve page change to Deliver
    dock._on_resolve_state_changed(True, "deliver", "Timeline_01", "00:00:10:00")
    assert dock.router.active_profile.page == "deliver"
    # Categories and Grid should be hidden, special container expanded
    assert dock.category_nav.isHidden()
    assert dock.grid.isHidden()
    assert dock.special_container.currentWidget() == dock.block_deliver
    assert "kết xuất" in dock.action_bar.btn_primary.text().lower()

    # Triggering render should create a job with progress bar
    dock.block_deliver.btn_render.click()
    assert dock.block_deliver.queue_container.count() > 0

    # 4. Sound pad shortcuts on Cut page
    dock._on_resolve_state_changed(True, "cut", "Timeline_01", "00:00:10:00")
    assert dock.router.active_profile.page == "cut"
    assert dock.special_container.currentWidget() == dock.block_cut

    # Trigger key '1' (Whoosh) when search has no focus
    dock.header.txt_search.clearFocus()
    key_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1")
    dock.keyPressEvent(key_event)
    # mock_insert should be called with pad asset
    assert mock_insert.insert.called
    dock.close()


def test_edge_dock_deferral_with_pending_chip(qapp):
    """Test deferral flow in EdgeDockWindow showing and clicking pending chip."""
    dock = EdgeDockWindow()
    dock.show()
    dock.open_dock(animated=False)

    # Hover inside dock
    dock.router.is_in_dock = True
    assert dock.router.is_busy is True

    # DaVinci changes page to Color
    dock._on_resolve_state_changed(True, "color", "Timeline_01", "00:00:00:00")

    # Still on Edit page, pending chip visible
    assert dock.router.active_profile.page == "edit"
    assert not dock.header.btn_pending_chip.isHidden()
    assert "Color" in dock.header.btn_pending_chip.text()

    # Click pending chip
    dock.header.btn_pending_chip.click()
    assert dock.router.active_profile.page == "color"
    assert dock.header.btn_pending_chip.isHidden()
    assert "Color" in dock.header.btn_page_chip.text()
    dock.close()
