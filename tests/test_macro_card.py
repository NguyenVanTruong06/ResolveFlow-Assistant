import pytest
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QEnterEvent

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_macro_card_init_and_hover_preview(qapp):
    from src.ui.lux.macro_card import MacroCard

    card = MacroCard(
        preset_id="alex_hormozi_pop",
        name="Alex Hormozi Pop",
        category="Text+",
        duration_text="1.5s",
        style_dna="box",
    )
    assert card.preset_id == "alex_hormozi_pop"
    assert card.name == "Alex Hormozi Pop"
    assert card.is_previewing is False

    # Simulate mouse hover event
    enter_ev = QEnterEvent(QPoint(10, 10), QPoint(10, 10), QPoint(10, 10))
    card.enterEvent(enter_ev)
    assert card.hover_timer.isActive() is True

    # Fast forward hover timer
    card._start_live_preview()
    assert card.is_previewing is True
    assert card.preview_timer.isActive() is True

    # Advance a few animation frames
    card._on_preview_tick()
    assert card.preview_frame > 0

    # Simulate mouse leave event
    leave_ev = QEvent(QEvent.Type.Leave)
    card.leaveEvent(leave_ev)
    assert card.hover_timer.isActive() is False
    assert card.preview_timer.isActive() is False
    assert card.is_previewing is False
    assert card.preview_frame == 0


def test_macro_card_double_click_signal(qapp):
    from src.ui.lux.macro_card import MacroCard

    card = MacroCard(
        preset_id="comic_manga_burst",
        name="Comic Manga Burst",
        category="Text+",
    )

    received = []
    card.insert_requested.connect(lambda pid: received.append(pid))

    # Simulate double click
    card.mouseDoubleClickEvent(None)
    assert received == ["comic_manga_burst"]


def test_macro_grid_widget(qapp):
    from src.ui.lux.macro_card import MacroGridWidget, MacroCard

    grid = MacroGridWidget()
    grid.resize(400, 500)

    items = [
        {"id": "t1", "name": "Title One", "category": "Text+", "style_dna": "glow"},
        {"id": "t2", "name": "Title Two", "category": "Text+", "style_dna": "glitch"},
        {"id": "s1", "name": "Whoosh SFX", "category": "SFX", "style_dna": "sfx"},
    ]
    grid.load_items(items)
    grid.show()

    assert len(grid.cards) == 3

    # Test search filter
    grid.search_input.setText("Whoosh")
    visible_cards = [c for c in grid.cards if not c.isHidden()]
    assert len(visible_cards) == 1
    assert visible_cards[0].preset_id == "s1"

    # Clear filter
    grid.search_input.setText("")
    visible_cards = [c for c in grid.cards if not c.isHidden()]
    assert len(visible_cards) == 3


def test_macro_card_fast_paste_and_drag_resolve(qapp):
    from unittest.mock import patch
    from src.ui.lux.macro_card import MacroCard

    card = MacroCard(
        preset_id="RF_Impact_Zoom",
        name="Impact Zoom",
        category="Transition",
    )

    fast_paste_events = []
    card.fast_paste_requested.connect(lambda pid: fast_paste_events.append(pid))
    card.request_fast_paste()
    assert fast_paste_events == ["RF_Impact_Zoom"]

    drop_events = []
    card.dropped_on_resolve.connect(lambda pid: drop_events.append(pid))
    with patch("src.ui.lux.drag_drop_tracker.is_mouse_over_resolve", return_value=True):
        card._handle_drop_on_release()
        assert drop_events == ["RF_Impact_Zoom"]


def test_macro_card_bilingual_search_and_thumbnail(qapp):
    import tempfile
    from PIL import Image
    from src.ui.lux.macro_card import MacroGridWidget, MacroCard

    # Create dummy thumbnail image
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img = Image.new("RGB", (320, 180), color=(100, 200, 50))
        img.save(f.name)
        thumb_path = f.name

    try:
        grid = MacroGridWidget()
        items = [
            {
                "id": "shake_title",
                "name": "Earthquake Shake",
                "category": "Text+",
                "thumbnail_path": thumb_path,
                "motion_tags": ["shake", "rung giật", "glitch"]
            },
            {
                "id": "zoom_title",
                "name": "Cinematic Zoom In",
                "category": "Text+",
                "motion_tags": ["zoom_in", "phóng to", "scale"]
            }
        ]
        grid.load_items(items)
        assert grid.cards[0].thumbnail_pixmap is not None
        assert not grid.cards[0].thumbnail_pixmap.isNull()

        # Search with Vietnamese with accents: "rung giật"
        grid.search_input.setText("rung giật")
        visible = [c for c in grid.cards if not c.isHidden()]
        assert len(visible) == 1
        assert visible[0].preset_id == "shake_title"

        # Search with Vietnamese WITHOUT accents: "rung giat" -> should still match!
        grid.search_input.setText("rung giat")
        visible = [c for c in grid.cards if not c.isHidden()]
        assert len(visible) == 1
        assert visible[0].preset_id == "shake_title"

        # Search for "phong to" (accent-insensitive) -> should match zoom_title!
        grid.search_input.setText("phong to")
        visible = [c for c in grid.cards if not c.isHidden()]
        assert len(visible) == 1
        assert visible[0].preset_id == "zoom_title"

    finally:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


