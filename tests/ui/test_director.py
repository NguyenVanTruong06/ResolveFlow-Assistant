"""
tests/ui/test_director.py
Kiểm thử toàn diện hệ thống ChunDVC Director (Bước 6):
- DirectorState & Recipe Mapping
- Web Prompt JSON Parser
- Stepper 4 bước
- DirectorController & PipelineWorker Kwargs
- DirectorWindow giao diện & chuyển bước
- Tích hợp Palette command & EdgeDock
"""

import pytest
from PySide6.QtWidgets import QApplication
from src.ui.director.director_state import (
    DirectorConfig, STYLE_PODCAST, STYLE_SHORTS, STYLE_VLOG, STYLE_CUSTOM,
    FEAT_HOOK, FEAT_PUNCH_IN, FEAT_REFRAME, parse_web_prompt_json
)
from src.ui.director.director_controller import DirectorController
from src.ui.director.widgets.stepper import DirectorStepperWidget
from src.ui.director.widgets.mini_timeline_view import MiniTimelineWidget
from src.ui.director.director_window import DirectorWindow
from src.ui.lux.shell.edge_dock import EdgeDockWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_director_state_defaults_and_preset_mapping(qapp):
    cfg = DirectorConfig()
    assert cfg.whisper_model == "small"
    assert cfg.whisper_language == "vi"
    assert cfg.style == STYLE_VLOG

    # 1. Preset Podcast
    cfg.apply_style_preset(STYLE_PODCAST)
    assert cfg.style == STYLE_PODCAST
    assert cfg.footage_type == "Nói liên tục"
    assert cfg.pacing == "Thong thả"
    assert cfg.silence_db == -38
    assert cfg.min_silence_dur == 0.6
    assert cfg.speedup_silence is False

    # 2. Preset Shorts
    cfg.apply_style_preset(STYLE_SHORTS)
    assert cfg.style == STYLE_SHORTS
    assert cfg.pacing == "Nhanh"
    assert cfg.intensity == 3
    assert cfg.silence_db == -32
    assert cfg.min_silence_dur == 0.35
    assert FEAT_REFRAME in cfg.enabled_features
    assert FEAT_HOOK in cfg.enabled_features

    # 3. Preset Vlog
    cfg.apply_style_preset(STYLE_VLOG)
    assert cfg.style == STYLE_VLOG
    assert cfg.footage_type == "Hỗn hợp"
    assert cfg.pacing == "Cân bằng"
    assert cfg.speedup_silence is True
    assert cfg.scene_guard_enabled is True
    assert cfg.vlog_hook_dur == 2.0


def test_parse_web_prompt_json(qapp):
    # 1. JSON Video Plan bọc code fence
    raw_video = """
    Dưới đây là kịch bản dựng:
    ```json
    {
        "timeline": [
            {"time": "00:00", "title": "Hook giật gân"},
            {"time": "00:05", "title": "Giới thiệu"},
            {"time": "00:15", "title": "Nội dung chính"}
        ]
    }
    ```
    Chúc bạn dựng video vui vẻ!
    """
    ok, msg, data = parse_web_prompt_json(raw_video)
    assert ok is True
    assert "Video Plan" in msg
    assert data["type"] == "video_plan"
    assert data["count"] == 3

    # 2. JSON Kinetic Plan
    raw_sub = """
    {
        "subtitles": [
            {"start": 1.0, "end": 2.5, "text": "Hế lô anh em"},
            {"start": 2.6, "end": 4.0, "text": "Hôm nay mình sẽ hướng dẫn"}
        ]
    }
    """
    ok, msg, data = parse_web_prompt_json(raw_sub)
    assert ok is True
    assert "Kinetic Plan" in msg
    assert data["type"] == "kinetic_plan"
    assert data["count"] == 2

    # 3. JSON lỗi cú pháp
    bad_json = "```json { this is broken json "
    ok, msg, data = parse_web_prompt_json(bad_json)
    assert ok is False
    assert "JSON không hợp lệ" in msg
    assert data is None

    # 4. Rỗng
    ok, msg, data = parse_web_prompt_json("")
    assert ok is False


def test_director_stepper_widget(qapp):
    stepper = DirectorStepperWidget()
    assert stepper.current_step == 1
    assert len(stepper._buttons) == 4

    # Bước 1 active, chưa completed
    assert stepper._buttons[0].is_active is True
    assert stepper._buttons[0].is_completed is False

    # Chuyển sang bước 3
    events = []
    stepper.step_selected.connect(events.append)
    stepper.set_current_step(3)
    assert stepper.current_step == 3
    assert stepper._buttons[0].is_completed is True
    assert stepper._buttons[1].is_completed is True
    assert stepper._buttons[2].is_active is True
    assert stepper._buttons[3].is_active is False

    # Click bước 2
    stepper._buttons[1].click()
    assert events == [2]
    assert stepper.current_step == 2


def test_director_controller_pipeline_kwargs(qapp):
    cfg = DirectorConfig()
    cfg.video_paths = ["/tmp/test_clip.mp4"]
    cfg.apply_style_preset(STYLE_VLOG)

    controller = DirectorController(config=cfg)
    controller.proposed_segments = [{"row": 0, "approved": True}]

    # Phase 0
    kwargs0 = controller.build_pipeline_kwargs(phase=0)
    assert kwargs0["video_paths"] == ["/tmp/test_clip.mp4"]
    assert kwargs0["model_size"] == "small"
    assert kwargs0["language"] == "vi"
    assert kwargs0["phase"] == 0
    assert kwargs0["proposed_segments_override"] is None
    assert kwargs0["silence_db"] == -35
    assert kwargs0["min_duration"] == 0.5
    assert kwargs0["speed_up_silence"] is True
    assert kwargs0["scene_guard"] is True

    # Phase 2: truyền proposed_segments_override
    kwargs2 = controller.build_pipeline_kwargs(phase=2)
    assert kwargs2["phase"] == 2
    assert kwargs2["proposed_segments_override"] == [{"row": 0, "approved": True}]

    # Start pipeline khi rỗng
    cfg.video_paths = []
    fin_events = []
    controller.pipeline_finished.connect(lambda ok, msg: fin_events.append((ok, msg)))
    res = controller.start_pipeline(phase=0)
    assert res is False
    assert fin_events[0][0] is False


def test_director_window_navigation_and_panels(qapp):
    controller = DirectorController()
    win = DirectorWindow(controller=controller)
    win.show()

    # Khởi đầu ở bước 1
    assert win.current_step == 1
    assert win.btn_back.isEnabled() is False
    assert win.btn_primary.text() == "Tiếp: Kiểu dựng ›"
    assert win.adv_panel.isHidden() is True
    assert win.prog_panel.isHidden() is True

    # Chuyển bước 2
    win.btn_primary.click()
    assert win.current_step == 2
    assert win.btn_back.isEnabled() is True
    assert win.btn_primary.text() == "Tiếp: Duyệt & kịch bản ›"

    # Bật bảng nâng cao
    win.toggle_advanced_panel()
    assert win.adv_panel.isVisible() is True
    win.adv_panel.btn_close = win.adv_panel.findChild(type(win.adv_panel.chk_cut))  # check child
    win.adv_panel.close_requested.emit()
    assert win.adv_panel.isVisible() is False

    # Chuyển bước 3
    win.btn_primary.click()
    assert win.current_step == 3
    assert win.btn_primary.text() == "Tiếp: Dựng timeline ›"

    # Chuyển bước 4
    win.btn_primary.click()
    assert win.current_step == 4
    assert win.btn_primary.text() == "✂ 🚀 Dựng timeline"

    # Bấm quay lại
    win.btn_back.click()
    assert win.current_step == 3

    win.close()


def test_edge_dock_open_director_and_palette_command(qapp):
    dock = EdgeDockWindow()

    # Kiểm tra gọi open_director
    assert dock._director_window is None
    dock.open_director()
    assert dock._director_window is not None
    assert isinstance(dock._director_window, DirectorWindow)

    # Thử qua palette command
    dock._director_window.close()
    dock._on_palette_command_executed("cmd_director")
    assert dock._director_window.isVisible() is True

    dock._on_palette_command_executed("cmd_autocut")
    assert dock._director_window.isVisible() is True

    # Thử qua header button và rail button
    dock._director_window.close()
    dock.header.btn_director.click()
    assert dock._director_window.isVisible() is True

    dock._director_window.close()
    dock.rail.btn_director.click()
    assert dock._director_window.isVisible() is True

    dock.close()
    dock._director_window.close()
    dock.palette.close()
