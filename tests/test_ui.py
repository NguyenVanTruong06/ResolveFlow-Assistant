import pytest

def test_ui_imports() -> None:
    """
    Xác nhận toàn bộ các lớp giao diện PyQt5/PySide6 được nạp đúng cách, 
    không bị lỗi cú pháp hay thiếu thư viện liên kết.
    """
    from src.ui.app import PipelineWorker, ResolveFlowApp
    
    assert PipelineWorker is not None
    assert ResolveFlowApp is not None
