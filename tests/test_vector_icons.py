import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QIcon
from src.ui.icons import create_vector_icon, get_vector_icon
from src.ui.widgets.step_progress_widget import StepProgressWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_create_vector_icons_all_types(qapp):
    icon_types = [
        "ai", "studio", "grid", "resolve", "download", "folder",
        "video", "cloud", "check", "done", "cross", "error",
        "clock", "pending", "refresh", "running", "play", "stop",
        "arrow_right", "sparkles", "unknown_type"
    ]
    for it in icon_types:
        pix = create_vector_icon(it, "#8b5cf6", 24)
        assert isinstance(pix, QPixmap)
        assert not pix.isNull()
        assert pix.width() == 24
        assert pix.height() == 24

        ico = get_vector_icon(it, "#8b5cf6", 20)
        assert isinstance(ico, QIcon)
        assert not ico.isNull()


def test_step_progress_widget_vector_icons(qapp):
    steps = [
        ("step1", "Kiểm tra"),
        ("step2", "Nhận diện"),
        ("step3", "Xuất bản"),
    ]
    widget = StepProgressWidget(steps)
    assert len(widget.steps) == 3
    assert widget.steps["step1"]["status"] == "pending"

    # Status changes
    widget.set_step_status("step1", "running")
    assert widget.steps["step1"]["status"] == "running"

    widget.set_step_status("step1", "done")
    assert widget.steps["step1"]["status"] == "done"

    widget.set_step_status("step2", "error")
    assert widget.steps["step2"]["status"] == "error"

    widget.reset()
    assert widget.steps["step1"]["status"] == "pending"
    assert widget.steps["step2"]["status"] == "pending"
