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

import json
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

    # 5. Dual Plan (cả 2 block markdown trong cùng 1 văn bản trả về từ AI)
    combined_response = f"""
    Xin chào, đây là kịch bản hoàn chỉnh cho video của bạn:
    
    1. Kịch bản Video Plan:
    ```json
    {{"timeline_segments": [{{"title": "Cảnh 1", "start": 0, "end": 10}}]}}
    ```
    
    2. Kịch bản Kinetic Typography:
    ```json
    {{"subtitles": [{{"start": 1.0, "end": 3.0, "text": "Đà Lạt phố hoa"}}]}}
    ```
    """
    ok, msg, data = parse_web_prompt_json(combined_response)
    assert ok is True
    assert "CẢ 2" in msg
    assert data["type"] == "dual_plan"
    assert "timeline_segments" in data["video_plan"]
    assert "subtitles" in data["kinetic_plan"]

    # 6. Target Slot chỉ định rõ
    generic_text = '{"info": "test data"}'
    ok1, msg1, data1 = parse_web_prompt_json(generic_text, target_slot="slot1")
    assert ok1 is True
    assert data1["type"] == "video_plan"

    ok2, msg2, data2 = parse_web_prompt_json(generic_text, target_slot="slot2")
    assert ok2 is True
    assert data2["type"] == "kinetic_plan"



def test_generated_kinetic_json_routes_to_slot2_with_page_count():
    from src.core.kinetic.kinetic_engine import generate_kinetic_prompt
    words = [{"word": f"word{i}", "start": i * 0.5, "end": (i + 1) * 0.5} for i in range(5)]
    prompt = generate_kinetic_prompt(words)
    raw = prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
    ok, _, parsed = parse_web_prompt_json(raw)
    assert ok
    assert parsed["type"] == "kinetic_plan"
    assert parsed["count"] == 2
    assert parsed["data"]["presets"]["hormozi"]["font"] == "Montserrat"


@pytest.mark.parametrize("data,slot", [
    ({"timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]}, "slot2"),
    ({"presets": {"basic": {"font": "Montserrat"}}, "pages": [{"word_range": [0, 1], "preset": "basic"}]}, "slot1"),
])
def test_known_json_plan_cannot_be_forced_into_the_opposite_slot(data, slot):
    ok, msg, parsed = parse_web_prompt_json(json.dumps(data), target_slot=slot)
    assert not ok
    assert parsed is None
    assert "V1" in msg and "V2" in msg


def test_explicit_slot_selects_matching_plan_from_combined_json():
    video = {"timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]}
    kinetic = {"presets": {"basic": {"font": "Montserrat"}}, "pages": [{"word_range": [0, 1], "preset": "basic"}]}
    for raw in (json.dumps(video) + "\n" + json.dumps(kinetic), json.dumps({"video_plan": video, "kinetic_plan": kinetic})):
        ok, _, parsed = parse_web_prompt_json(raw, target_slot="slot2")
        assert ok
        assert parsed["type"] == "kinetic_plan"
        assert parsed["data"] == kinetic
        assert parsed["count"] == 1


def test_kinetic_pages_paste_keeps_video_plan_and_rejects_wrong_slot(qapp):
    from src.ui.director.pages.review_page import ReviewPage
    page = ReviewPage()
    video = {"timeline_segments": [{"clip_id": "C01", "start_sec": 0, "end_sec": 10}]}
    kinetic = {"presets": {"basic": {"font": "Montserrat"}}, "pages": [{"word_range": [0, 1], "preset": "basic"}]}
    page._video_plan_data = video
    clipboard = QApplication.clipboard()
    old_text = clipboard.text()
    try:
        clipboard.setText(json.dumps(kinetic))
        page._paste_to_slot("slot2")
        assert page._kinetic_plan_data == kinetic
        assert page._video_plan_data is video
        clipboard.setText(json.dumps(video))
        page._paste_to_slot("slot2")
        assert page._kinetic_plan_data == kinetic
        assert page._video_plan_data is video
        assert "Không thể nạp" in page.lbl_json_hint.text()
    finally:
        clipboard.setText(old_text)
        page.close()


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

    # Chuyển bước 4 khi chưa duyệt -> bị chặn ở Bước 3 (G07 Approval Gate)
    win.btn_primary.click()
    assert win.current_step == 3
    assert win.btn_primary.text() == "Tiếp: Dựng timeline ›"

    # Khi có kịch bản đã duyệt hợp lệ -> cho phép vào Bước 4 xem tóm tắt
    from unittest.mock import MagicMock
    from src.core.contracts.edit_plan_proposal_contracts import EditPlanProposal
    from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
    mock_prop = MagicMock(spec=EditPlanProposal)
    mock_prop.proposal_id = "p_test"
    mock_appr = MagicMock(spec=AppOwnedApprovalRecord)
    mock_appr.approval_id = "a_test"
    win.page_review.get_approved_proposal_and_record = lambda: (mock_prop, mock_appr)

    win.go_to_step(4)
    assert win.current_step == 4
    assert win.btn_primary.text() == "Kiểm tra nguồn & tài nguyên"

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


def test_review_page_three_modes_and_two_prompts(qapp):
    from src.ui.director.pages.review_page import ReviewPage

    page = ReviewPage()

    # 1. Mặc định ở chế độ Web Prompt (index 0)
    assert page.ai_stack.currentIndex() == 0
    assert page.btn_mode_web.isChecked() is True
    assert page.btn_mode_cloud.isChecked() is False
    assert page.btn_mode_ollama.isChecked() is False

    # 2. Chuyển sang Cloud API (index 1)
    page.btn_mode_cloud.click()
    assert page.ai_stack.currentIndex() == 1
    assert page.btn_mode_cloud.isChecked() is True

    # 3. Chuyển sang Local Ollama (index 2)
    page.btn_mode_ollama.click()
    assert page.ai_stack.currentIndex() == 2
    assert page.btn_mode_ollama.isChecked() is True

    # Quay lại Web Prompt
    page.btn_mode_web.click()
    assert page.ai_stack.currentIndex() == 0

    # 4. Kiểm tra sinh 2 luồng prompt theo hợp đồng C2b:
    # Luồng 1: Prompt Dựng Video (V1) - sync getter trả về '' khi chưa chuẩn bị, không bịa clip giả
    assert page.get_video_prompt() == ""

    # Khi có snapshot chuẩn bị sẵn khớp ngữ cảnh in-memory:
    curr_ctx = page._get_current_context()
    curr_sig = page._extract_context_signature(curr_ctx)
    page._prepared_prompt_snapshot = {
        "prompt_text": "THÔNG TIN DỰ ÁN\n\nTranscript lời thoại...",
        "context_signature": curr_sig,
        "request_id": "test_prep_1",
    }
    video_prompt = page.get_video_prompt()
    assert "THÔNG TIN DỰ ÁN" in video_prompt or "transcript" in video_prompt.lower()
    assert len(video_prompt) > 20

    # Khi không có video_paths, bấm copy không tự ý phát signal copied rác
    copied_signals = []
    page.prompt_copied.connect(copied_signals.append)
    page.btn_copy_video_prompt.click()
    assert "video" not in copied_signals  # Chưa có tệp video, bị reject an toàn

    # Luồng 2: Không có dữ liệu nguồn thì V2 không được tạo prompt giả.
    kinetic_prompt = page.get_kinetic_prompt(aspect="9:16")
    assert kinetic_prompt == ""  # Gated, không chạy DEMO_WORDS hay legacy I/O

    page.btn_copy_kinetic_prompt.click()
    assert "kinetic" not in copied_signals  # Không ghi đè clipboard, 0 signal


def test_review_page_json_apply_and_persistence(qapp):
    from src.ui.director.pages.review_page import ReviewPage

    page = ReviewPage()

    # 1. Nhập JSON kịch bản Video Plan
    video_json = '{"scenes": [{"title": "Mở đầu hấp dẫn", "start": 0, "end": 5}]}'
    page.txt_json_input.setPlainText(video_json)
    assert page.paste_zone.is_recognized is True

    applied_events = []
    page.json_applied.connect(lambda data, msg: applied_events.append((data, msg)))
    page.btn_apply_json.click()

    assert len(applied_events) == 1
    assert applied_events[0][0]["type"] == "video_plan"

    # 2. Tự nhận diện prefix API Key
    # Gemini
    page.txt_cloud_api_key.setText("AQ.test_gemini_key")
    assert page.combo_cloud_provider.currentData() == "gemini"

    # Claude
    page.txt_cloud_api_key.setText("sk-ant-test_claude_key")
    assert page.combo_cloud_provider.currentData() == "claude"

    # OpenAI
    page.txt_cloud_api_key.setText("sk-proj-test_openai_key")
    assert page.combo_cloud_provider.currentData() == "openai"

    # DeepSeek
    page.txt_cloud_api_key.setText("sk-deepseek-key-123")
    assert page.combo_cloud_provider.currentData() == "deepseek"

    # 3. Đồng bộ với DirectorConfig
    cfg = DirectorConfig()
    page._switch_mode(1)  # Cloud
    page.save_to_config(cfg)
    assert cfg.ai_engine_mode == "cloud"
    assert cfg.cloud_provider == "deepseek"
    assert cfg.api_key == "sk-deepseek-key-123"

    # Load ngược lại
    cfg2 = DirectorConfig()
    cfg2.ai_engine_mode = "ollama"
    cfg2.ollama_model = "qwen2.5:7b"
    cfg2.ollama_endpoint = "http://127.0.0.1:11434"
    page.load_from_config(cfg2)

    assert page.ai_stack.currentIndex() == 2
    assert page.combo_ollama_model.currentText() == "qwen2.5:7b"
    assert page.txt_ollama_endpoint.text() == "http://127.0.0.1:11434"


def test_review_page_dual_plan_and_slot_paste(qapp):
    from src.ui.director.pages.review_page import ReviewPage

    page = ReviewPage()

    # 1. Test nạp tuần tự: Dán Video Plan vào Slot 1
    v_json = '{"timeline_segments": [{"id": 1, "title": "Cảnh 1", "start": 0, "end": 5}]}'
    page.txt_json_input.setPlainText(v_json)
    page.btn_apply_json.click()

    assert page._video_plan_data is not None
    assert "✓ Đã nạp" in page.lbl_slot_vid_status.text()
    assert page._kinetic_plan_data is None  # Slot 2 chưa bị ảnh hưởng

    # 2. Dán tiếp Kinetic Plan vào Slot 2
    k_json = '{"subtitles": [{"start": 0.5, "end": 2.0, "text": "Phụ đề test"}]}'
    page.txt_json_input.setPlainText(k_json)
    page.btn_apply_json.click()

    assert page._video_plan_data is not None  # Slot 1 VẪN NGUYÊN VẸN, không bị đè!
    assert page._kinetic_plan_data is not None  # Slot 2 nạp thành công
    assert "✓ Đã nạp" in page.lbl_slot_kin_status.text()

    # 3. Test nạp CẢ 2 CÙNG LÚC (Dual Plan)
    combined = """
    ```json
    {"timeline_segments": [{"id": 1, "title": "Cảnh mới", "start": 0, "end": 10}]}
    ```
    ```json
    {"subtitles": [{"start": 1.0, "end": 4.0, "text": "Phụ đề mới"}]}
    ```
    """
    page.txt_json_input.setPlainText(combined)
    page.btn_apply_json.click()

    assert page._video_plan_data["timeline_segments"][0]["title"] == "Cảnh mới"
    assert page._kinetic_plan_data["subtitles"][0]["text"] == "Phụ đề mới"
    assert "✓ Đã nạp" in page.lbl_slot_vid_status.text()
    assert "✓ Đã nạp" in page.lbl_slot_kin_status.text()

    # 4. Test Target Slot toggle selector
    page.btn_target_slot1.setChecked(True)
    assert page._get_current_target_slot() == "slot1"
    page.btn_target_slot2.setChecked(True)
    assert page._get_current_target_slot() == "slot2"
    page.btn_target_auto.setChecked(True)
    assert page._get_current_target_slot() is None


