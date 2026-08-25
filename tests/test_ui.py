import sys
import pytest
from PySide6.QtWidgets import QApplication
from src.ui.app import PipelineWorker, ResolveFlowApp

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app

def test_ui_imports():
    assert PipelineWorker is not None
    assert ResolveFlowApp is not None

def test_worker_stop():
    worker = PipelineWorker(
        video_paths=["test.mp4"],
        model_size="small",
        language="Auto",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        split_mode="words",
        split_limit=6,
        font_name="Arial",
        font_size=48,
        enable_vlog_hook=True,
        vlog_hook_duration=2.0
    )
    assert worker.is_interrupted is False
    assert worker.split_mode == "words"
    assert worker.split_limit == 6
    assert worker.enable_vlog_hook is True
    
    worker.stop()
    assert worker.is_interrupted is True

def test_user_customized_limit_protection(qapp):
    window = ResolveFlowApp()
    
    # Mặc định chưa tùy chỉnh
    assert window.user_customized_limit is False
    
    # Giả lập phát hiện video ngang (không bị tùy chỉnh)
    from unittest.mock import patch
    with patch("src.core.resolve_api.is_vertical_video", return_value=False):
        window._update_default_chars_limit("landscape.mp4")
        assert window.txt_split_limit.text() == "42"

    with patch("src.core.resolve_api.is_vertical_video", return_value=True):
        window._update_default_chars_limit("portrait.mp4")
        assert window.txt_split_limit.text() == "22"

    # Người dùng chủ động gõ số 30
    window.txt_split_limit.setText("30")
    window._on_user_customized_limit()
    assert window.user_customized_limit is True

    # Khi người dùng đã tùy chỉnh, việc phát hiện video khác KHÔNG ĐƯỢC ghi đè
    with patch("src.core.resolve_api.is_vertical_video", return_value=False):
        window._update_default_chars_limit("landscape.mp4")
        assert window.txt_split_limit.text() == "30"  # Vẫn giữ nguyên 30!

    with patch("src.core.resolve_api.is_vertical_video", return_value=True):
        window._update_default_chars_limit("portrait.mp4")
        assert window.txt_split_limit.text() == "30"  # Vẫn giữ nguyên 30!
