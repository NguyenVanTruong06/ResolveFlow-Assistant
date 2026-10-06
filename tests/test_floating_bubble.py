import pytest
from PySide6.QtWidgets import QApplication
from src.ui.bubble.floating_bubble import FloatingBubbleWidget, create_vector_icon


def test_floating_bubble_initialization(qapp):
    bubble = FloatingBubbleWidget()
    assert bubble is not None
    assert bubble.progress_pct == 0
    assert bubble.status_text == "Sẵn sàng"
    assert "AI Director" in bubble.lbl_title.text()
    assert bubble.tray is not None
    assert bubble.tray.lbl_tray_status.text() == "<b>Sẵn sàng</b>"
    assert bubble.tray.mini_bar.isHidden() is True
    assert bubble.tray.btn_insert_timeline.isHidden() is True


def test_floating_bubble_progress_update(qapp):
    bubble = FloatingBubbleWidget()
    bubble.update_progress(50, "Đang quét clip 1...")
    assert bubble.progress_pct == 50
    assert bubble.is_processing is True
    assert "50%" in bubble.lbl_progress.text()
    assert bubble.tray.mini_bar.isHidden() is False
    assert bubble.tray.mini_bar.value() == 50

    bubble.update_progress(100, "Hoàn tất!")
    assert bubble.progress_pct == 100
    assert bubble.is_processing is False
    assert bubble.tray.mini_bar.isHidden() is True
    assert bubble.tray.btn_insert_timeline.isHidden() is False


def test_floating_bubble_tray_states_and_signals(qapp):
    bubble = FloatingBubbleWidget()
    
    # 1. Trạng thái Run
    bubble.update_progress(46, "Đang nhận diện lời thoại")
    assert bubble.is_processing is True
    assert "46%" in bubble.tray.lbl_tray_right.text()
    assert "Đang nhận diện lời thoại" in bubble.tray.lbl_tray_status.text()
    assert bubble.tray.mini_bar.isHidden() is False
    assert bubble.tray.btn_insert_timeline.isHidden() is True

    # 2. Trạng thái Done với duration
    bubble.update_progress(100, "Đã xuất xong", duration="21:01")
    assert bubble.is_done is True
    assert "Đã xuất timeline" in bubble.tray.lbl_tray_status.text()
    assert bubble.tray.lbl_tray_right.text() == "21:01"
    assert bubble.tray.btn_insert_timeline.isHidden() is False

    # 3. Tín hiệu bấm Nạp vào DaVinci
    fired = []
    bubble.insert_timeline_requested.connect(lambda: fired.append(True))
    bubble.tray.btn_insert_timeline.click()
    assert len(fired) == 1


def test_create_vector_icons():
    ai_pix = create_vector_icon("ai", "#c4b5fd", 20)
    assert not ai_pix.isNull()
    assert ai_pix.width() == 20

    grid_pix = create_vector_icon("grid", "#67e8f9", 20)
    assert not grid_pix.isNull()
    assert grid_pix.width() == 20

    resolve_pix = create_vector_icon("resolve", "#ffffff", 20)
    assert not resolve_pix.isNull()
    assert resolve_pix.width() == 20
