import pytest
from PySide6.QtWidgets import QApplication
from src.ui.bubble.floating_bubble import FloatingBubbleWidget


def test_floating_bubble_initialization(qapp):
    bubble = FloatingBubbleWidget()
    assert bubble is not None
    assert bubble.progress_pct == 0
    assert bubble.status_text == "Sẵn sàng"
    assert "AI Director" in bubble.lbl_title.text()


def test_floating_bubble_progress_update(qapp):
    bubble = FloatingBubbleWidget()
    bubble.update_progress(50, "Đang quét clip 1...")
    assert bubble.progress_pct == 50
    assert bubble.is_processing is True
    assert "50%" in bubble.lbl_progress.text()

    bubble.update_progress(100, "Hoàn tất!")
    assert bubble.progress_pct == 100
    assert bubble.is_processing is False
