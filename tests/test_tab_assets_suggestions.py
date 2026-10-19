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
