import re
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent

from src.ui.tabs.tab_assets import TabAssets, SuggestionChip
from src.core.contextual_suggester import SuggestedAsset


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_tab_assets_has_suggestion_bar_and_suggester(qapp):
    tab = TabAssets()
    
    assert hasattr(tab, "suggestion_bar")
    assert tab.suggestion_bar is not None
    assert hasattr(tab, "combo_context_sentence")
    assert hasattr(tab, "btn_analyze_sentence")
    assert hasattr(tab, "scroll_sug")
    assert hasattr(tab, "suggester")
    assert tab.suggester is not None


def test_tab_assets_set_context_sentence_renders_chips(qapp):
    tab = TabAssets()
    
    tab.set_context_sentence("Thật không thể tin nổi cú sốc này kinh hoàng quá")
    
    # Should render chips in sug_chips_layout
    chips = [tab.sug_chips_layout.itemAt(i).widget() 
             for i in range(tab.sug_chips_layout.count()) 
             if isinstance(tab.sug_chips_layout.itemAt(i).widget(), SuggestionChip)]
    
    assert len(chips) > 0
    # Check that chip has suggested asset
    chip = chips[0]
    assert isinstance(chip.suggested, SuggestedAsset)
    assert chip.asset is not None


def test_suggestion_chip_selection_and_quick_add(qapp, monkeypatch):
    tab = TabAssets()
    tab.set_context_sentence("Hài hước buồn cười quá đi mất haha")
    
    chips = [tab.sug_chips_layout.itemAt(i).widget() 
             for i in range(tab.sug_chips_layout.count()) 
             if isinstance(tab.sug_chips_layout.itemAt(i).widget(), SuggestionChip)]
    assert len(chips) > 0
    
    chip = chips[0]
    
    # Test selecting chip
    chip.mousePressEvent(None)
    assert tab.selected_asset.id == chip.asset.id
    
    # Test quick add
    added = []
    monkeypatch.setattr(tab, "_insert_at_playhead", lambda: added.append(True))
    
    chip.btn_add.click()
    assert len(added) == 1
    
    # Test double click
    dbl_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )
    chip.mouseDoubleClickEvent(dbl_ev)
    assert len(added) == 2


def test_load_transcript_segments(qapp):
    tab = TabAssets()
    segments = [
        {"start": 0.5, "end": 2.5, "text": "Bạn có biết bí mật này chưa?", "role": "hook"},
        {"start": 3.0, "end": 6.0, "text": "Toang thật rồi ông giáo ơi haha", "role": "punchline"}
    ]
    
    tab.load_transcript_segments(segments)
    
    assert tab.combo_context_sentence.count() == 2
    assert "00:00" in tab.combo_context_sentence.itemText(0)
    assert "Bạn có biết" in tab.combo_context_sentence.itemText(0)
    
    # Switching item should update context
    tab.combo_context_sentence.setCurrentIndex(1)
    chips = [tab.sug_chips_layout.itemAt(i).widget() 
             for i in range(tab.sug_chips_layout.count()) 
             if isinstance(tab.sug_chips_layout.itemAt(i).widget(), SuggestionChip)]
    assert len(chips) > 0


def test_toggle_suggestion_tray(qapp):
    tab = TabAssets()
    assert not tab.scroll_sug.isHidden()
    
    tab.btn_toggle_sug.click()
    assert tab.scroll_sug.isHidden()
    assert tab.btn_toggle_sug.text() == "▼"
    
    tab.btn_toggle_sug.click()
    assert not tab.scroll_sug.isHidden()
    assert tab.btn_toggle_sug.text() == "▲"


def test_stylesheets_no_fractional_px(qapp):
    tab = TabAssets()
    tab.set_context_sentence("Kiểm tra stylesheet")
    
    chips = [tab.sug_chips_layout.itemAt(i).widget() 
             for i in range(tab.sug_chips_layout.count()) 
             if isinstance(tab.sug_chips_layout.itemAt(i).widget(), SuggestionChip)]
    
    fractional_pattern = re.compile(r'\d+\.\d+px')
    assert not fractional_pattern.search(tab.suggestion_bar.styleSheet())
    if chips:
        assert not fractional_pattern.search(chips[0].styleSheet())


def test_suggestion_ribbon_compact_height_and_close_reopen(qapp):
    tab = TabAssets()
    assert tab.suggestion_bar.maximumHeight() <= 40
    assert not tab.suggestion_bar.isHidden()

    # Close with × button
    tab.btn_close_sug.click()
    assert tab.suggestion_bar.isHidden()
    assert not tab.btn_toggle_sug_bar.isChecked()

    # Reopen from toolbar button
    tab.btn_toggle_sug_bar.click()
    assert not tab.suggestion_bar.isHidden()


def test_suggestion_chip_switches_category_and_clears_filter(qapp):
    tab = TabAssets()
    tab._filter_by_rail("lut")
    tab.txt_search.setText("xyz_random_search")

    # Pick an sfx asset that exists in tab.all_assets
    sfx_item = next(a for a in tab.all_assets if getattr(a, "tab", "") == "sfx")
    tab._on_card_selected(sfx_item.id)

    assert tab.current_rail_tab == "sfx"
    assert tab.txt_search.text() == ""
    assert tab.selected_asset is not None
    assert tab.selected_asset.id == sfx_item.id


def test_suggestion_missing_asset_warning(qapp, monkeypatch):
    tab = TabAssets()
    toasts = []
    monkeypatch.setattr(tab, "_show_toast", lambda msg: toasts.append(msg))

    orig_selected = tab.selected_asset
    tab._on_card_selected("non_existent_asset_id_99999")

    assert len(toasts) == 1
    assert "không còn tồn tại trong kho" in toasts[0]
    assert tab.selected_asset == orig_selected


def test_dock_edit_suggest_block_compact_and_no_insert(qapp):
    from unittest.mock import MagicMock
    from src.ui.lux.context.special_blocks import EditSuggestBlock
    from src.ui.lux.shell.edge_dock import EdgeDockWindow

    block = EditSuggestBlock()
    assert block.maximumHeight() <= 40

    long_sentence = "Một câu thoại cực kỳ dài vượt quá chiều rộng của ribbon để kiểm tra tính năng elide text QFontMetrics"
    block.set_suggestion("00:01:23", long_sentence)
    assert len(block.lbl_text.text()) < len(long_sentence)
    assert long_sentence in block.lbl_text.toolTip()

    # Test that clicking suggestion in EdgeDock selects and previews, does NOT call insert_service.insert
    mock_insert_service = MagicMock()
    dock = EdgeDockWindow(insert_service=mock_insert_service)
    dock.set_assets([
        {"id": "sfx_whoosh", "name": "Whoosh", "category": "sfx", "tab": "sfx", "type": "sfx", "preview_path": "test.mp3"}
    ])
    dock.category_nav.select_category("text")

    dock._on_suggested_asset("sfx_whoosh")

    # Switched category to sfx
    assert dock.category_nav.current_category == "sfx"
    # Selected asset updated
    assert dock._selected_asset is not None
    assert dock._selected_asset["id"] == "sfx_whoosh"
    # Did NOT auto-insert to timeline!
    assert mock_insert_service.insert.call_count == 0
