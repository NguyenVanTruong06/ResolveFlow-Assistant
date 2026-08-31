import sys
import pytest
from PySide6.QtWidgets import QApplication
from src.ui.app import PipelineWorker, ResolveFlowApp, PreviewDialog
from src.core.recipe_manager import Recipe
from src.core.text_preset import TextStylePreset

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

@pytest.fixture
def app_window(qapp):
    window = ResolveFlowApp()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()

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

def test_user_customized_limit_protection(app_window):
    window = app_window
    
    assert window.user_customized_limit is False
    
    from unittest.mock import patch
    with patch("src.core.resolve_api.is_vertical_video", return_value=False):
        window._update_default_chars_limit("landscape.mp4")
        assert window.txt_split_limit.text() == "42"

    with patch("src.core.resolve_api.is_vertical_video", return_value=True):
        window._update_default_chars_limit("portrait.mp4")
        assert window.txt_split_limit.text() == "22"

    window.txt_split_limit.setText("30")
    window._on_user_customized_limit()
    assert window.user_customized_limit is True

    with patch("src.core.resolve_api.is_vertical_video", return_value=False):
        window._update_default_chars_limit("landscape.mp4")
        assert window.txt_split_limit.text() == "30"

    with patch("src.core.resolve_api.is_vertical_video", return_value=True):
        window._update_default_chars_limit("portrait.mp4")
        assert window.txt_split_limit.text() == "30"

def test_workflow_modes_switch(app_window):
    window = app_window
    
    # 1. Chuyển sang Podcast
    idx_podcast = window.combo_workflow.findData("podcast")
    assert idx_podcast >= 0
    window.combo_workflow.setCurrentIndex(idx_podcast)
    assert window.check_cut.isChecked() is True
    assert window.check_subtitle.isChecked() is True
    assert window.combo_ai_mode.currentData() == "clean_talk"

    # 2. Chuyển sang Shorts / TikTok
    idx_shorts = window.combo_workflow.findData("shorts")
    assert idx_shorts >= 0
    window.combo_workflow.setCurrentIndex(idx_shorts)
    assert window.check_reframe.isChecked() is True
    assert window.combo_ai_mode.currentData() == "viral_shorts"
    assert window.combo_split_mode.currentData() == "words"

    # 3. Chuyển sang Vlog Hook
    idx_vlog = window.combo_workflow.findData("vlog")
    assert idx_vlog >= 0
    window.combo_workflow.setCurrentIndex(idx_vlog)
    assert window.check_vlog_hook.isChecked() is True
    assert window.check_speedup.isChecked() is True

def test_master_intensity_slider(app_window):
    window = app_window
    
    # Cường độ Nhẹ (1)
    window.slide_master_intensity.setValue(1)
    assert window.slide_db.value() == -42
    assert window.slide_dur.value() == 8
    assert window.txt_confidence_threshold.text() == "0.55"

    # Cường độ Mạnh (3)
    window.slide_master_intensity.setValue(3)
    assert window.slide_db.value() == -28
    assert window.slide_dur.value() == 3
    assert window.txt_confidence_threshold.text() == "0.85"

def test_recipe_and_presets_ui(app_window):
    window = app_window
    
    # Presets combobox populated
    assert window.combo_text_preset.count() >= 7
    
    # Recipes combobox populated
    assert window.combo_recipes.count() >= 3

    # Toggle advanced panel
    is_hidden_init = window.advanced_container.isHidden()
    window._toggle_advanced_panel()
    assert window.advanced_container.isHidden() != is_hidden_init

def test_pipeline_worker_execution_mocked(tmp_path):
    import os
    from unittest.mock import patch
    dummy_video = os.path.join(tmp_path, "mock_clip.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    worker = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Auto",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        split_mode="characters",
        split_limit=42,
        speed_up_silence=True,
        silence_speed=8.0,
        enable_broll=True,
        enable_sfx=True,
        enable_vlog_hook=True,
        text_preset_id="karaoke_pop",
        use_cache=False
    )

    mock_speed_segs = [
        {"type": "speedup", "start": 0.0, "end": 2.0, "duration": 2.0, "speed": 8.0, "rec_duration": 0.25},
        {"type": "voice", "start": 2.0, "end": 8.0, "duration": 6.0, "speed": 1.0, "rec_duration": 6.0}
    ]

    with patch("src.core.audio.AudioExtractor.extract_audio"):
        with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
            with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=[{"start": 1.0, "end": 3.0, "text": "công nghệ AI tuyệt vời", "words": [{"word": "công nghệ", "start": 1.0, "end": 2.0}]}]):
                    with patch("src.core.autocut.SilenceDetector.detect_intervals_with_speedup", return_value=mock_speed_segs):
                        with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
                            with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
                                worker.run()

    assert os.path.exists(os.path.join(tmp_path, "mock_clip_Timeline_CatLoc.fcpxml"))
    assert os.path.exists(os.path.join(tmp_path, "mock_clip_Timeline_CatLoc.edl"))
    assert os.path.exists(os.path.join(tmp_path, "mock_clip_Timeline_Intro_Teaser.fcpxml"))

def test_pipeline_worker_no_transcription_optimization(tmp_path):
    import os
    from unittest.mock import patch
    dummy_video = os.path.join(tmp_path, "mock_clip_no_sub.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    worker = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Auto",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="silence_only",
        enable_subtitles=False,
        enable_broll=False,
        enable_sfx=False,
        enable_vlog_hook=False,
        use_cache=False
    )

    mock_silence_segs = [
        (0.0, 2.0),
        (3.0, 5.0)
    ]

    with patch("src.core.audio.AudioExtractor.extract_audio") as mock_extract:
        with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=5.0):
            with patch("src.core.transcriber.ResolveTranscriber") as mock_transcriber_class:
                with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=mock_silence_segs) as mock_detect:
                    with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
                        with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
                            worker.run()
                            
                            mock_detect.assert_called_once()
                            mock_extract.assert_called()
                            mock_transcriber_class.assert_not_called()

    assert os.path.exists(os.path.join(tmp_path, "mock_clip_no_sub_Timeline_CatLoc.fcpxml"))
    assert os.path.exists(os.path.join(tmp_path, "mock_clip_no_sub_Timeline_CatLoc.edl"))
    assert not os.path.exists(os.path.join(tmp_path, "mock_clip_no_sub_PhuDe_VideoDaCat.srt"))

def test_pipeline_worker_phases_mocked(tmp_path):
    import os
    from unittest.mock import patch
    dummy_video = os.path.join(tmp_path, "phase_clip.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    # ---- PHASE 1 RUN ----
    worker1 = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Auto",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=1,
        use_cache=False
    )

    with patch("src.core.validator.DryRunValidator.validate_media_files") as mock_val:
        mock_val.return_value.errors = []
        mock_val.return_value.warnings = []
        with patch("src.core.audio.AudioExtractor.extract_audio"):
            with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
                with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                    with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=[{"start": 1.0, "end": 3.0, "text": "câu nói vấp", "words": [{"word": "câu", "start": 1.0, "end": 1.5}, {"word": "vấp", "start": 2.0, "end": 2.5}]}]):
                        with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=[(1.0, 3.0)]):
                            worker1.run()

    assert worker1.proposed_segments is not None
    assert len(worker1.proposed_segments) > 0
    assert len(worker1.clip_data_out_cache) == 1

    # ---- PHASE 2 RUN ----
    worker1.proposed_segments[0].approved = False

    worker2 = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Auto",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=2,
        proposed_segments_override=worker1.proposed_segments,
        clip_data_cache=worker1.clip_data_out_cache,
        use_cache=False
    )

    with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
        with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
            worker2.run()

    assert os.path.exists(os.path.join(tmp_path, "phase_clip_Timeline_CatLoc.fcpxml"))

def test_export_timeline_phase2_dryrun_validator_no_unbound_local_error(tmp_path):
    """
    Kiểm tra luồng Phase 2 (Xuất Timeline sang DaVinci Resolve) có kích hoạt
    DryRunValidator.validate_fcpxml_integrity mà KHÔNG gặp UnboundLocalError.
    """
    import os
    from unittest.mock import patch, MagicMock
    from src.core.ai_director import ProposedSegment
    from src.core.validator import DryRunValidator

    dummy_video = os.path.join(tmp_path, "phase2_clip.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock_video_content")

    # Giả lập dữ liệu clip_data_cache từ Phase 1
    mock_clip_data = [{
        "video_path": dummy_video,
        "clip_dur": 10.0,
        "raw_subtitles": [{"start": 0.0, "end": 4.0, "text": "Đoạn test xuất timeline", "words": [{"word": "Đoạn", "start": 0.0, "end": 1.0}]}],
        "clip_subs": [{"start": 0.0, "end": 4.0, "text": "Đoạn test xuất timeline", "words": [{"word": "Đoạn", "start": 0.0, "end": 1.0}]}],
        "silence_keep_intervals": [(0.0, 4.0), (5.0, 10.0)],
        "speedup_segments": []
    }]

    mock_props = [
        ProposedSegment(
            id=0,
            start=0.0,
            end=4.0,
            text="Đoạn test xuất timeline",
            decision="keep",
            reason="good take",
            confidence=0.9,
            approved=True
        )
    ]

    worker_phase2 = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=2,
        proposed_segments_override=mock_props,
        clip_data_cache=mock_clip_data,
        use_cache=False
    )

    validator_called = []
    original_validate = DryRunValidator.validate_fcpxml_integrity

    def tracked_validate(xml_path, expected_media_paths=None, resolve_automation=None):
        validator_called.append(xml_path)
        return original_validate(xml_path, expected_media_paths=expected_media_paths, resolve_automation=resolve_automation)

    finished_results = []
    worker_phase2.finished_signal.connect(lambda ok, msg: finished_results.append((ok, msg)))

    with patch("src.ui.app.DryRunValidator.validate_fcpxml_integrity", side_effect=tracked_validate):
        with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
            with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=True):
                worker_phase2.run()

    # Xác nhận DryRunValidator thực sự được gọi
    assert len(validator_called) == 1
    assert os.path.exists(validator_called[0])
    # Xác nhận Phase 2 hoàn tất thành công mà không có UnboundLocalError
    assert len(finished_results) == 1
    assert finished_results[0][0] is True
    assert finished_results[0][1] == "phase2_done"

def test_user_friendly_error_system_exceptions(app_window):
    """Kiểm tra việc định dạng lỗi hệ thống (UnboundLocalError, NameError...) trung thực và không gợi ý sai lệch."""
    window = app_window
    
    # 1. Lỗi hệ thống: UnboundLocalError
    system_err = "UnboundLocalError: cannot access local variable 'DryRunValidator' where it is not associated with a value"
    friendly_msg = window._format_user_friendly_error(system_err)
    assert "lỗi nội bộ trong hệ thống xử lý" in friendly_msg
    assert "DryRunValidator" in friendly_msg
    assert "không phải do dữ liệu video" in friendly_msg
    assert "model 'tiny'" not in friendly_msg

    # 2. Lỗi hệ thống: ImportError
    import_err = "ImportError: No module named 'fake_module'"
    friendly_msg_import = window._format_user_friendly_error(import_err)
    assert "lỗi nội bộ trong hệ thống xử lý" in friendly_msg_import
    assert "model 'tiny'" not in friendly_msg_import

    # 3. Lỗi thông thường: CUDA
    cuda_err = "torch.cuda.OutOfMemoryError: CUDA out of memory"
    friendly_cuda = window._format_user_friendly_error(cuda_err)
    assert "Tràn bộ nhớ GPU" in friendly_cuda

def test_pipeline_worker_cut_beginning_greeting_no_phantom_sub(tmp_path):
    """
    Kiểm tra luồng PipelineWorker: khi đoạn đầu (chứa câu chào) bị cắt bỏ (qua keep_intervals hoặc AI Director),
    tệp phụ đề đã cắt (_PhuDe_VideoDaCat.srt) và tệp FCPXML KHÔNG CÒN CHỨA câu chào đó.
    """
    import os
    from unittest.mock import patch

    dummy_video = os.path.join(tmp_path, "clip_with_greeting.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    worker = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        ai_mode="clean_talk",
        enable_subtitles=True,
        phase=0,
        use_cache=False
    )

    # Đoạn đầu 0.0s đến 3.0s là câu chào "Xin chào các bạn"
    # Đoạn sau 3.5s đến 7.0s là "Hôm nay tôi sẽ hướng dẫn các bạn"
    mock_subs = [
        {"start": 0.5, "end": 2.5, "text": "Xin chào các bạn đã đến kênh", "words": [{"word": "Xin", "start": 0.5, "end": 1.0}, {"word": "chào", "start": 1.1, "end": 1.5}, {"word": "các", "start": 1.6, "end": 2.0}, {"word": "bạn", "start": 2.1, "end": 2.5}]},
        {"start": 3.5, "end": 6.0, "text": "Hôm nay tôi hướng dẫn", "words": [{"word": "Hôm", "start": 3.5, "end": 4.0}, {"word": "nay", "start": 4.1, "end": 4.5}, {"word": "tôi", "start": 4.6, "end": 5.0}, {"word": "hướng", "start": 5.1, "end": 5.5}, {"word": "dẫn", "start": 5.6, "end": 6.0}]}
    ]
    # SilenceDetector chỉ giữ từ 3.0s đến 7.0s (đoạn 0.0s - 3.0s bị cắt)
    mock_keep = [(3.0, 7.0)]

    with patch("src.core.audio.AudioExtractor.extract_audio"):
        with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=7.0):
            with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=mock_subs):
                    with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=mock_keep):
                        with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
                            with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
                                worker.run()

    srt_path = os.path.join(tmp_path, "clip_with_greeting_PhuDe_VideoDaCat.srt")
    fcpxml_path = os.path.join(tmp_path, "clip_with_greeting_Timeline_CatLoc.fcpxml")

    assert os.path.exists(srt_path)
    assert os.path.exists(fcpxml_path)

    with open(srt_path, "r", encoding="utf-8") as sf:
        srt_content = sf.read()
    with open(fcpxml_path, "r", encoding="utf-8") as xf:
        fcpxml_content = xf.read()

    # Xác nhận câu chào 0.0s-3.0s đã bị xóa sạch khỏi phụ đề đã cắt
    assert "Xin chào các bạn" not in srt_content
    assert "Xin" not in fcpxml_content and "chào" not in fcpxml_content
    # Nhưng câu nội dung chính (3.5s - 6.0s) thì vẫn còn nguyên
    assert "Hôm nay tôi hướng dẫn" in srt_content
    assert "Hôm" in fcpxml_content and "hướng" in fcpxml_content

def test_app_reset_workflow_phase_on_file_change(qapp):
    from src.ui.app import ResolveFlowApp
    app_window = ResolveFlowApp()
    
    # Giả lập app đang ở Phase 2 với dữ liệu cache của video2.mp4
    app_window.current_phase = 2
    app_window.clip_data_cache = [{"video_path": "d:/video2.mp4"}]
    app_window.proposed_segments = [{"id": 1, "text": "test"}]
    
    # Khi reset phase (hoặc người dùng chọn video1.mp4)
    app_window._reset_workflow_phase()
    
    assert app_window.current_phase == 1
    assert app_window.clip_data_cache == []
    assert app_window.proposed_segments == []
    assert app_window.table_review.isHidden() is True



