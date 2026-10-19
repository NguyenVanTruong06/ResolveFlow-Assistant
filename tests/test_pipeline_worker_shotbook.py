import os
import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication

from src.ui.auto.auto_window import PipelineWorker as AutoPipelineWorker, AutoWindow
from src.ui.app import PipelineWorker as AppPipelineWorker
from src.core.shotbook_orchestrator import ShotRole, LayoutMode


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_pipeline_worker_default_parameters():
    """Kiểm tra PipelineWorker khởi tạo với đầy đủ các cờ cấu hình Phase 3."""
    worker = AutoPipelineWorker(
        video_paths=["test.mp4"],
        model_size="tiny",
        language="vi",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5
    )
    assert worker.enable_evidence_hunter is True
    assert worker.enable_shotbook is True
    assert worker.enable_geometry_checker is True
    assert worker.safe_zone_platform == "tiktok"

    # Đồng bộ trên AppPipelineWorker
    app_worker = AppPipelineWorker(
        video_paths=["test.mp4"],
        model_size="tiny",
        language="vi",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5
    )
    assert app_worker.enable_evidence_hunter is True
    assert app_worker.enable_shotbook is True
    assert app_worker.enable_geometry_checker is True
    assert app_worker.safe_zone_platform == "tiktok"


def test_autowindow_checkboxes_and_delegates(qapp):
    """Kiểm tra AutoWindow có các checkbox săn B-roll Web và Khử đè mặt/Safe Zone."""
    win = AutoWindow()
    assert hasattr(win, "check_evidence")
    assert hasattr(win, "check_geometry")
    assert win.check_evidence.isChecked() is True
    assert win.check_geometry.isChecked() is True
    assert "Evidence" in win.check_evidence.text()
    assert "Safe Zone" in win.check_geometry.text()


def test_pipeline_worker_shotbook_and_evidence_integration(tmp_path):
    """
    Kiểm tra chu trình tích hợp hoàn chỉnh của PipelineWorker:
    Transcript có chứa link web và thực thể DaVinci Resolve ->
    Tự động săn Evidence screenshot -> Tạo ShotbookPlan -> Khử xung đột bố cục ->
    Xuất FCPXML v1.9 có cấu trúc multi-track lane chuẩn cho DaVinci Resolve.
    """
    video = str(tmp_path / "talking_head.mp4")
    with open(video, "w") as f:
        f.write("mock video data")

    worker = AutoPipelineWorker(
        video_paths=[video],
        model_size="tiny",
        language="vi",
        run_cut=False,
        silence_db=-35.0,
        min_duration=0.5,
        split_mode="characters",
        split_limit=42,
        speed_up_silence=False,
        enable_broll=True,
        enable_sfx=True,
        enable_vlog_hook=False,
        enable_subtitles=True,
        ai_mode="silence_only",
        use_cache=False,
        enable_evidence_hunter=True,
        enable_shotbook=True,
        enable_geometry_checker=True,
        safe_zone_platform="tiktok"
    )

    # Transcript đề cập đến link web và câu hỏi hook
    subs = [
        {
            "start": 1.0,
            "end": 5.0,
            "text": "Truy cập https://github.com tải tool.",
            "words": []
        },
        {
            "start": 6.0,
            "end": 12.0,
            "text": "Làm thế nào để tạo ra hiệu ứng chuyển cảnh triệu đô cực đỉnh?",
            "words": []
        }
    ]

    captured_shotbook_plan = None
    original_generate_fcpxml = None

    from src.core.fcpxml_generator import FCPXMLGenerator
    original_generate_fcpxml = FCPXMLGenerator.generate_timeline_fcpxml

    def spy_generate_timeline_fcpxml(*args, **kwargs):
        nonlocal captured_shotbook_plan
        captured_shotbook_plan = kwargs.get("shotbook_plan")
        return original_generate_fcpxml(*args, **kwargs)

    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=20.0), \
         patch("src.core.autocut.get_media_metadata", return_value={"duration": 20.0, "fps": 30.0, "start_seconds": 0.0, "has_audio": True}), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=subs), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False), \
         patch.object(FCPXMLGenerator, "generate_timeline_fcpxml", side_effect=spy_generate_timeline_fcpxml):
        worker.run()

    # 1. Xác minh shotbook_plan được tạo và chuyển giao vào generate_timeline_fcpxml
    assert captured_shotbook_plan is not None, "Shotbook plan phải được tạo và truyền cho FCPXMLGenerator"
    assert len(captured_shotbook_plan.items) >= 2

    # 2. Phân cảnh chứa bằng chứng web phải có vai trò EVIDENCE với PiP
    ev_items = [it for it in captured_shotbook_plan.items if it.role == ShotRole.EVIDENCE]
    assert len(ev_items) >= 1
    ev_item = ev_items[0]
    assert ev_item.layout_mode == LayoutMode.PIP_CORNER
    assert ev_item.evidence_path is not None
    assert os.path.exists(ev_item.evidence_path), "Ảnh screenshot bằng chứng phải được lưu trên đĩa"

    # 3. Phân cảnh chứa câu hỏi hook (chứa 'làm thế nào', 'triệu đô') phải có vai trò HOOK với Punch-in
    hook_items = [it for it in captured_shotbook_plan.items if it.role == ShotRole.HOOK]
    assert len(hook_items) >= 1
    hook_item = hook_items[0]
    assert hook_item.layout_mode == LayoutMode.PUNCH_IN
    assert hook_item.zoom_scale > 1.0

    # 4. Kiểm tra file FCPXML Timeline chính được tạo ra thành công và hợp lệ
    timeline_fcpxml_files = [f for f in os.listdir(tmp_path) if f.endswith(".fcpxml") and "Timeline" in f]
    assert len(timeline_fcpxml_files) > 0, "Phải xuất ra file Timeline .fcpxml cho DaVinci Resolve"

    fcpxml_content = open(os.path.join(tmp_path, timeline_fcpxml_files[0]), encoding="utf-8").read()
    # Kiểm tra FCPXML có cấu trúc đa track lane="1" (Evidence B-roll) và lane="2" (Subtitles)
    assert 'lane="1"' in fcpxml_content, "Phải có track lane 1 cho Evidence B-Roll"
    assert 'lane="2"' in fcpxml_content, "Phải có track lane 2 cho Phụ đề Text+ Karaoke"
