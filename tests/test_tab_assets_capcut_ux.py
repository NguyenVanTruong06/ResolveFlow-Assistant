import os
import re
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtGui import QMouseEvent, QEnterEvent

from src.ui.tabs.tab_assets import (
    AssetCard, TabAssets, MOCKUP_SFX, StudioAsset
)
from src.core.text_preset import BUILTIN_PRESETS


def test_card_has_quick_add_button_and_double_click(qapp):
    """Kiểm tra thẻ đạo cụ có nút ➕ Thêm nhanh và hỗ trợ nhấp đúp (Double Click)."""
    preset = BUILTIN_PRESETS[0]
    card = AssetCard(preset)

    assert hasattr(card, "btn_quick_add"), "AssetCard must have btn_quick_add button"
    assert card.btn_quick_add.text() == "➕"

    # Test click nút quick add phát signal add_requested
    received_assets = []
    card.add_requested.connect(lambda item: received_assets.append(item))
    card.btn_quick_add.click()
    assert len(received_assets) == 1
    assert received_assets[0] == preset

    # Test double click vào thẻ phát signal add_requested
    received_assets.clear()
    selected_ids = []
    card.selected_signal.connect(lambda pid: selected_ids.append(pid))

    event = QMouseEvent(
        QEvent.MouseButtonDblClick,
        QPoint(10, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    card.mouseDoubleClickEvent(event)
    assert len(received_assets) == 1
    assert received_assets[0] == preset
    assert len(selected_ids) == 1
    assert selected_ids[0] == preset.id


def test_audio_hover_preview_sfx(qapp):
    """Kiểm tra tính năng Rê chuột nghe thử (Hover Preview) chuẩn CapCut cho SFX/Nhạc."""
    sfx = MOCKUP_SFX[0]
    card = AssetCard(sfx)

    AssetCard.enable_hover_preview = True
    assert card._is_playing_audio is False

    # Simulate enterEvent (rê chuột vào card)
    enter_ev = QEnterEvent(QPoint(10, 10), QPoint(10, 10), QPoint(10, 10))
    card.enterEvent(enter_ev)
    assert card._is_playing_audio is True

    # Simulate leaveEvent (rê chuột ra ngoài)
    leave_ev = QEvent(QEvent.Leave)
    card.leaveEvent(leave_ev)
    assert card._is_playing_audio is False

    # Khi tắt hover preview thì không được tự phát khi rê chuột
    AssetCard.enable_hover_preview = False
    card.enterEvent(enter_ev)
    assert card._is_playing_audio is False

    # Reset lại setting
    AssetCard.enable_hover_preview = True


def test_single_active_audio_card_no_overlap(qapp):
    """Đảm bảo chỉ có tối đa 1 âm thanh phát cùng lúc, không bị chồng tiếng khi lướt chuột."""
    sfx1 = MOCKUP_SFX[0]
    sfx2 = MOCKUP_SFX[1]
    card1 = AssetCard(sfx1)
    card2 = AssetCard(sfx2)

    # Card 1 bắt đầu phát
    card1._start_quick_play()
    assert card1._is_playing_audio is True
    assert card2._is_playing_audio is False

    # Card 2 bắt đầu phát -> Card 1 phải tự động ngắt ngay lập tức
    card2._start_quick_play()
    assert card1._is_playing_audio is False
    assert card2._is_playing_audio is True

    # Dừng tất cả
    AssetCard.stop_all_audio()
    assert card1._is_playing_audio is False
    assert card2._is_playing_audio is False


def test_tab_assets_quick_add_and_double_click_integration(qapp, monkeypatch):
    """Kiểm tra TabAssets kết nối nút Thêm nhanh và Double-click để chèn vào DaVinci Resolve."""
    tab = TabAssets()
    tab.show()

    assert hasattr(tab, "chk_hover_preview"), "TabAssets must have chk_hover_preview checkbox"
    assert tab.chk_hover_preview.isChecked() is True

    # Toggle checkbox hover preview
    tab.chk_hover_preview.setChecked(False)
    assert AssetCard.enable_hover_preview is False
    tab.chk_hover_preview.setChecked(True)
    assert AssetCard.enable_hover_preview is True

    # Monkeypatch ResolveAutomation
    inserted_calls = []
    class MockResolve:
        def insert_title_at_playhead(self, **kwargs):
            inserted_calls.append(("title", kwargs))
            return True
        def insert_sfx_to_track(self, **kwargs):
            inserted_calls.append(("sfx", kwargs))
            return True

    monkeypatch.setattr("src.ui.tabs.tab_assets.ResolveAutomation", lambda: MockResolve())

    # Tìm thẻ text đầu tiên và kích hoạt add_requested
    text_card = next(c for c in tab.card_widgets if getattr(c.preset, "tab", "") == "text" or isinstance(c.preset, BUILTIN_PRESETS[0].__class__))
    text_card.btn_quick_add.click()
    assert len(inserted_calls) == 1
    assert inserted_calls[0][0] == "title"

    # Tìm thẻ sfx đầu tiên và double click
    sfx_card = next(c for c in tab.card_widgets if getattr(c.preset, "tab", "") == "sfx")
    dbl_ev = QMouseEvent(
        QEvent.MouseButtonDblClick,
        QPoint(10, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    sfx_card.mouseDoubleClickEvent(dbl_ev)
    assert len(inserted_calls) == 2
    assert inserted_calls[1][0] == "sfx"


def test_card_stylesheets_no_fractional_px(qapp):
    """Kiểm tra không có cỡ chữ thập phân (ví dụ 10.5px) trong stylesheet mới."""
    fractional_px_pattern = re.compile(r'\b\d+\.\d+px\b')
    sfx = MOCKUP_SFX[0]
    card = AssetCard(sfx)

    if hasattr(card, "btn_quick_add"):
        sheet = card.btn_quick_add.styleSheet()
        matches = fractional_px_pattern.findall(sheet)
        assert len(matches) == 0, f"Found fractional px in btn_quick_add: {matches}"
