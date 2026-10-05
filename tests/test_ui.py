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

def test_app_reset_workflow_phase_on_file_change(app_window):
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

def test_pipeline_worker_auto_skip_phase1_when_all_keep(tmp_path):
    """
    Kiểm tra cơ chế tự động skip Phase 1 Review khi toàn bộ đề xuất đều là 'keep' (không có gì để cắt):
    - Worker khởi chạy với phase=1.
    - AI Director phân tích kịch bản và nhận diện 0 câu nói vấp (has_cut_proposals == False).
    - Worker tự động chuyển self.phase = 2 và tiếp tục xuất bản timeline thay vì dừng ở 'phase1_done'.
    - Kết quả: tệp FCPXML được tạo và worker phát tín hiệu 'phase2_done'.
    """
    import os
    from unittest.mock import patch

    dummy_video = os.path.join(tmp_path, "all_keep_clip.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    worker = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-50.0,
        min_duration=5.0,
        ai_mode="clean_talk",
        remove_bad_takes=False,
        enable_punch_in=True,
        enable_subtitles=True,
        phase=1,
        use_cache=False
    )

    mock_subs = [
        {"start": 1.0, "end": 4.0, "text": "Hôm nay chúng ta cùng tìm hiểu vlog mới", "words": [{"word": "Hôm", "start": 1.0, "end": 1.5}, {"word": "nay", "start": 1.5, "end": 2.0}]},
        {"start": 5.0, "end": 8.0, "text": "Nội dung rất hấp dẫn và thú vị", "words": [{"word": "Nội", "start": 5.0, "end": 5.5}, {"word": "dung", "start": 5.5, "end": 6.0}]}
    ]

    finished_signals = []
    worker.finished_signal.connect(lambda ok, msg: finished_signals.append((ok, msg)))

    with patch("src.core.validator.DryRunValidator.validate_media_files") as mock_val:
        mock_val.return_value.errors = []
        mock_val.return_value.warnings = []
        with patch("src.core.audio.AudioExtractor.extract_audio"):
            with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
                with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                    with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=mock_subs):
                        with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=[(1.0, 4.0), (5.0, 8.0)]):
                            with patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False):
                                with patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
                                    worker.run()

    # 1. Xác nhận các phân đoạn đều là keep
    assert worker.proposed_segments is not None
    assert len(worker.proposed_segments) == 2
    assert all(p.decision == "keep" for p in worker.proposed_segments)
    
    # 2. Xác nhận worker đã tự động đổi phase sang 2 và xuất timeline thành công
    assert worker.phase == 2
    assert len(finished_signals) == 1
    assert finished_signals[0][0] is True
    assert finished_signals[0][1] == "phase2_done"
    
    # 3. File FCPXML đã được tạo đầy đủ
    fcpxml_path = os.path.join(tmp_path, "all_keep_clip_Timeline_CatLoc.fcpxml")
    assert os.path.exists(fcpxml_path)

def test_pipeline_worker_stops_at_phase1_when_has_cut_proposals(tmp_path):
    """
    Kiểm tra luồng có đề xuất cắt (Bad Take):
    - Worker chạy với phase=1.
    - AI Director phát hiện bad take (has_cut_proposals == True).
    - Worker dừng lại ở Phase 1, phát tín hiệu 'phase1_done' và CHƯA xuất timeline.
    """
    import os
    from unittest.mock import patch

    dummy_video = os.path.join(tmp_path, "has_cut_clip.mp4")
    with open(dummy_video, "w", encoding="utf-8") as f:
        f.write("mock")

    worker = PipelineWorker(
        video_paths=[dummy_video],
        model_size="tiny",
        language="Tiếng Việt",
        run_cut=True,
        silence_db=-35.0,
        min_duration=0.5,
        split_mode="words",
        split_limit=4,
        ai_mode="clean_talk",
        remove_bad_takes=True,
        enable_punch_in=True,
        enable_subtitles=True,
        phase=1,
        use_cache=False
    )

    # 2 câu nói lặp lại -> Bad Take
    mock_subs = [
        {"start": 1.0, "end": 2.5, "text": "Xin chao cac ban", "words": [{"word": "Xin", "start": 1.0, "end": 1.3}, {"word": "chao", "start": 1.3, "end": 1.6}, {"word": "cac", "start": 1.6, "end": 2.0}, {"word": "ban", "start": 2.0, "end": 2.5}]},
        {"start": 3.0, "end": 6.0, "text": "Xin chao cac ban hom nay toi se chia se", "words": [{"word": "Xin", "start": 3.0, "end": 3.3}, {"word": "chao", "start": 3.3, "end": 3.6}, {"word": "cac", "start": 3.6, "end": 4.0}, {"word": "ban", "start": 4.0, "end": 4.4}, {"word": "hom", "start": 4.4, "end": 4.8}, {"word": "nay", "start": 4.8, "end": 5.2}, {"word": "toi", "start": 5.2, "end": 5.5}, {"word": "se", "start": 5.5, "end": 5.7}, {"word": "chia", "start": 5.7, "end": 5.9}, {"word": "se", "start": 5.9, "end": 6.0}]}
    ]

    finished_signals = []
    worker.finished_signal.connect(lambda ok, msg: finished_signals.append((ok, msg)))

    with patch("src.core.validator.DryRunValidator.validate_media_files") as mock_val:
        mock_val.return_value.errors = []
        mock_val.return_value.warnings = []
        with patch("src.core.audio.AudioExtractor.extract_audio"):
            with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0):
                with patch("src.core.transcriber.ResolveTranscriber.load_model"):
                    with patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=mock_subs):
                        with patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=[(1.0, 6.0)]):
                            worker.run()

    assert worker.proposed_segments is not None
    assert len(worker.proposed_segments) > 0
    # Câu đầu tiên là Bad Take bị đề xuất cắt
    assert worker.proposed_segments[0].decision == "cut"
    # Worker dừng lại ở phase 1 vì có đề xuất cắt cần người dùng duyệt
    assert len(finished_signals) == 1
    assert finished_signals[0][0] is True
    assert finished_signals[0][1] == "phase1_done"

def test_tab_widget_structure(app_window):
    """Kiểm tra khởi tạo QTabWidget chứa đủ 5 Workflow Tabs (AI Copilot, Dựng Thô, Chữ & Phụ Đề, SFX, Polish & Export)."""
    window = app_window
    assert hasattr(window, "tab_widget")
    assert window.tab_widget.count() == 5
    
    tab_names = [window.tab_widget.tabText(i) for i in range(5)]
    assert any("AI Copilot" in name or "Kịch Bản" in name for name in tab_names)
    assert any("Dựng Thô" in name or "Auto Cut" in name for name in tab_names)
    assert any("Chữ & Phụ Đề" in name for name in tab_names)
    assert any("SFX" in name for name in tab_names)
    assert any("Export" in name or "Polish" in name for name in tab_names)

def test_tab_copilot_and_cache_check(app_window, monkeypatch):
    """Kiểm tra TabCopilot và cơ chế tự động kiểm tra Cache."""
    window = app_window
    assert hasattr(window, "tab_copilot")
    assert hasattr(window, "lbl_cache_badge")
    
    # Kiểm tra trạng thái ban đầu
    window.selected_files = []
    window._check_project_cache_status()
    assert "Chưa chọn video" in window.lbl_cache_badge.text()
    
    # Giả lập file có cache
    from unittest.mock import patch, MagicMock
    with patch("src.core.cache_manager.ScanCacheManager.get_cached_scan", return_value={"clip_dur": 10.0, "raw_subtitles": [{"text": "hello"}]}):
        window.selected_files = ["d:/clip1.mp4", "d:/clip2.mp4"]
        window._check_project_cache_status()
        assert "100%" in window.lbl_cache_badge.text()
        assert "100%" in window.lbl_s1_cache_status.text()


def test_tab_sfx_pad_and_preview(app_window):
    """Kiểm tra Tab 3 SFX Soundboard: Có đủ các nút Pad (>=8), phát preview và phát signal insert."""
    window = app_window
    tab_sfx = window.tab_sfx
    
    assert len(tab_sfx.pad_buttons) >= 8
    assert "whoosh" in tab_sfx.pad_buttons
    assert "ding" in tab_sfx.pad_buttons
    
    # 1. Bấm Pad Pop
    played_events = []
    tab_sfx.preview_played.connect(lambda sid: played_events.append(sid))
    tab_sfx.pad_buttons["pop"].click()
    
    assert tab_sfx.selected_sfx_id == "pop"
    assert "pop" in played_events
    
    # 2. Bấm nút chèn SFX vào Playhead
    insert_events = []
    tab_sfx.insert_sfx_requested.connect(lambda path, trk, vol: insert_events.append((path, trk, vol)))
    tab_sfx.btn_insert_playhead.click()
    
    assert len(insert_events) == 1
    assert "pop.wav" in insert_events[0][0]
    assert insert_events[0][1] == 2 # Track 2 mặc định
    assert insert_events[0][2] == -12.0 # -12dB mặc định

def test_tab_titles_single_insert(app_window):
    """Kiểm tra Tab 2 Titles: Nhập text và phát signal chèn tại Playhead."""
    window = app_window
    tab_titles = window.tab_titles
    
    title_events = []
    tab_titles.insert_title_requested.connect(lambda txt, pid, dur: title_events.append((txt, pid, dur)))
    
    tab_titles.txt_single_title.setText("ResolveFlow Test Title")
    tab_titles.slide_title_dur.setValue(40) # 4.0s
    tab_titles.btn_insert_title_playhead.click()
    
    assert len(title_events) == 1
    assert title_events[0][0] == "ResolveFlow Test Title"
    assert title_events[0][2] == 4.0

def test_tab_export_signals(app_window):
    """Kiểm tra Tab 4 Export: Tín hiệu áp dụng LUT và gửi Render Job."""
    window = app_window
    tab_export = window.tab_export
    
    lut_events = []
    tab_export.apply_lut_requested.connect(lambda lut: lut_events.append(lut))
    tab_export.combo_lut.setCurrentIndex(1) # Warm Vlog
    tab_export.btn_apply_lut.click()
    
    assert len(lut_events) == 1
    assert lut_events[0] == "warm_vlog"
    
    render_events = []
    tab_export.render_requested.connect(lambda p, c: render_events.append((p, c)))
    tab_export.txt_custom_render_name.setText("My_TikTok_Video")
    tab_export.btn_start_render.click()
    
    assert len(render_events) == 1
    assert render_events[0][0] == "tiktok_916"
    assert render_events[0][1] == "My_TikTok_Video"

def test_tab_titles_install_and_copy(app_window, monkeypatch, tmp_path):
    """Kiểm tra chức năng cài đặt presets vào DaVinci Resolve và Copy Fusion Node."""
    from unittest.mock import patch
    window = app_window
    monkeypatch.setenv("APPDATA", str(tmp_path))
    
    # 1. Bấm nút Cài Đặt 7 Presets
    install_events = []
    window.tab_titles.install_presets_requested.connect(lambda: install_events.append(True))
    with patch("PySide6.QtWidgets.QMessageBox.information"):
        window.tab_titles.btn_install_presets.click()
    assert len(install_events) == 1
    
    # 2. Bấm nút Copy Fusion Node
    idx = window.combo_text_preset.findData("karaoke_pop")
    if idx >= 0:
        window.combo_text_preset.setCurrentIndex(idx)
    copy_events = []
    window.tab_titles.copy_fusion_node_requested.connect(lambda pid: copy_events.append(pid))
    with patch("PySide6.QtWidgets.QMessageBox.information"):
        window.tab_titles.btn_copy_fusion.click()
def test_tab_titles_visual_technique_cards_and_filtering(app_window):
    """Kiểm tra Tab 2: Thư viện Visual Technique Cards, bộ lọc Category và Search."""
    window = app_window
    tab_titles = window.tab_titles

    # 1. Có đủ các card kỹ xảo
    assert len(tab_titles.card_widgets) >= 12
    card_ids = [c.preset.id for c in tab_titles.card_widgets]
    assert "kinetic_hormozi" in card_ids
    assert "highlighter_swipe" in card_ids
    assert "paper_cutout" in card_ids

    # 2. Lọc theo Category Pills (highlighter_paper)
    tab_titles._on_category_pill_clicked("highlighter_paper")
    visible_cards = [c for c in tab_titles.card_widgets if not c.isHidden()]
    assert len(visible_cards) >= 2
    assert all(c.preset.category == "highlighter_paper" for c in visible_cards)

    # 3. Tìm kiếm theo từ khóa 'Hormozi'
    tab_titles._on_category_pill_clicked("all")
    tab_titles.txt_search.setText("Hormozi")
    visible_search_cards = [c for c in tab_titles.card_widgets if not c.isHidden()]
    assert len(visible_search_cards) == 1
    assert visible_search_cards[0].preset.id == "kinetic_hormozi"

    # 4. Click chọn Card Hormozi -> Cập nhật combo_text_preset
    tab_titles.txt_search.clear()
    tab_titles._on_card_selected("kinetic_hormozi")
    assert window.combo_text_preset.currentData() == "kinetic_hormozi"

def test_tab_export_cards_and_palette(app_window):
    """Kiểm tra Tab 4: Thư viện Color Looks, Palette Swatches và Category filtering."""
    window = app_window
    tab_export = window.tab_export

    # 1. Có đủ 10 Cards màu
    assert len(tab_export.card_widgets) >= 10
    look_ids = [c.look.id for c in tab_export.card_widgets]
    assert "cyberpunk_neon" in look_ids
    assert "korean_pastel" in look_ids
    assert "moody_dark" in look_ids

    # 2. Lọc Category Pills (cyber)
    tab_export._on_category_pill_clicked("cyber")
    visible_cards = [c for c in tab_export.card_widgets if not c.isHidden()]
    assert len(visible_cards) >= 2
    assert all(c.look.category == "cyber" for c in visible_cards)

    # 3. Tìm kiếm theo từ khóa 'Pastel'
    tab_export._on_category_pill_clicked("all")
    tab_export.txt_search.setText("Pastel")
    visible_search_cards = [c for c in tab_export.card_widgets if not c.isHidden()]
    assert len(visible_search_cards) == 1
    assert visible_search_cards[0].look.id == "korean_pastel"

    # 4. Click chọn Card Korean Pastel -> Cập nhật combo_lut
    tab_export.txt_search.clear()
    tab_export._on_card_selected("korean_pastel")
    assert tab_export.combo_lut.currentData() == "korean_pastel"











def test_review_toolbar_workflow(app_window):
    from src.core.ai_director import ProposedSegment
    app_window.proposed_segments = [
        ProposedSegment(id=i, start=i * 2.0, end=i * 2.0 + 2.0, text=f"t{i}", decision="keep",
                        confidence=0.95, reason="ok", approved=True)
        for i in range(3)
    ]
    app_window._populate_review_table()
    app_window._set_review_visible(True)
    assert app_window.review_toolbar.isHidden() is False

    app_window._review_bulk(app_window.review_state.cut_all)
    assert not any(p.approved for p in app_window.proposed_segments)
    cb = app_window.table_review.cellWidget(0, 0).findChild(type(app_window.check_cache))
    assert cb.isChecked() is False

    app_window._review_undo()
    assert all(p.approved for p in app_window.proposed_segments)
    app_window._reset_workflow_phase()
    assert app_window.review_toolbar.isHidden() is True


def test_pacing_combo_present(app_window):
    assert app_window.combo_pacing.currentData() == "balanced"
    assert app_window.combo_pacing.count() == 3


def test_recipe_roundtrip_keeps_pacing_and_repeats(app_window):
    from src.core.recipe_manager import Recipe
    r = Recipe(id="t", name="t", pacing="fast", remove_repeated_phrases=False)
    app_window._apply_recipe_to_ui(r)
    assert app_window.combo_pacing.currentData() == "fast"
    assert app_window.check_repeats.isChecked() is False


def test_new_vlog_controls_and_recipe_roundtrip(app_window):
    from src.core.recipe_manager import Recipe
    w = app_window
    assert w.check_scene_guard.isChecked() is True
    assert w.check_fill_gaps.isChecked() is False
    assert w.combo_hook_total.currentData() == 20.0
    w._apply_recipe_to_ui(Recipe(id="v", name="v", scene_guard=False, fill_gaps=True, vlog_hook_total=30.0))
    assert w.check_scene_guard.isChecked() is False
    assert w.check_fill_gaps.isChecked() is True
    assert w.combo_hook_total.currentData() == 30.0


def _run_worker_with_active_silence(tmp_path, scene_guard):
    import os
    import numpy as np
    from unittest.mock import patch
    from src.core.autocut import CutSegment
    video = os.path.join(tmp_path, "vlog.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")
    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=True, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, speed_up_silence=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, enable_subtitles=False,
        ai_mode="silence_only", use_cache=False, scene_guard=scene_guard
    )
    segs = [CutSegment(start=0, end=3, action="keep"), CutSegment(start=3, end=5, action="cut"),
            CutSegment(start=5, end=10, action="keep")]
    activity = np.full(10, 0.3, dtype=np.float32)   # cảnh đang chuyển động suốt clip
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=[]), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=segs), \
         patch("src.ui.app.compute_visual_activity", return_value=activity), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()
    return open(os.path.join(tmp_path, "vlog_Timeline_CatLoc.edl"), encoding="utf-8").read()


def test_pipeline_scene_guard_keeps_active_silence(tmp_path):
    edl = _run_worker_with_active_silence(tmp_path, scene_guard=True)
    assert sum(1 for ln in edl.splitlines() if " V " in ln) == 1     # 3 đoạn gộp thành 1 cảnh liền mạch, không giật


def test_pipeline_without_scene_guard_still_cuts(tmp_path):
    edl = _run_worker_with_active_silence(tmp_path, scene_guard=False)
    assert sum(1 for ln in edl.splitlines() if " V " in ln) == 2     # vẫn cắt khoảng lặng như cũ


def _run_worker_story(tmp_path, intent, api_key=None, story_target=60.0):
    import os
    import numpy as np
    from unittest.mock import patch
    from src.core.autocut import CutSegment
    video = os.path.join(tmp_path, "story.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")
    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=True, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, speed_up_silence=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, enable_subtitles=True,
        ai_mode="silence_only", use_cache=False, scene_guard=False, story_intent=intent,
        story_target=story_target, api_key=api_key
    )
    subs = [{"start": 2.0, "end": 8.0, "text": "Chào mừng các bạn đến với vlog hôm nay", "words": []},
            {"start": 30.0, "end": 36.0, "text": "Wow nhìn này không thể tin được", "words": []},
            {"start": 70.0, "end": 76.0, "text": "Cảnh ở đây rất đẹp", "words": []},
            {"start": 105.0, "end": 112.0, "text": "Tóm lại hãy đăng ký kênh nhé", "words": []}]
    segs = [CutSegment(start=0, end=120, action="keep")]
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=120.0), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=subs), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=segs), \
         patch("src.ui.app.compute_visual_activity", return_value=np.full(120, 0.2, dtype=np.float32)), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()
    return tmp_path


def test_pipeline_story_cold_open_exports_extra_timeline(tmp_path):
    import os
    _run_worker_story(tmp_path, "cold_open")
    arranged = os.path.join(tmp_path, "story_Timeline_SapXep.fcpxml")
    assert os.path.exists(arranged) and os.path.exists(os.path.join(tmp_path, "story_KeHoach_SapXep.txt"))
    assert os.path.exists(os.path.join(tmp_path, "story_Timeline_CatLoc.fcpxml"))   # timeline gốc vẫn được giữ
    plan = open(os.path.join(tmp_path, "story_KeHoach_SapXep.txt"), encoding="utf-8").read()
    assert "Hook" in plan and "[chép]" in plan


def test_pipeline_story_keep_does_not_export_arranged(tmp_path):
    import os
    _run_worker_story(tmp_path, "keep")
    assert not os.path.exists(os.path.join(tmp_path, "story_Timeline_SapXep.fcpxml"))


def test_pipeline_story_llm_failure_falls_back_to_heuristic(tmp_path):
    import os
    from unittest.mock import patch
    with patch("src.core.llm_director.LLMSemanticSelector.complete_json", side_effect=Exception("mạng lỗi")):
        _run_worker_story(tmp_path, "cold_open", api_key="fake")
    assert os.path.exists(os.path.join(tmp_path, "story_Timeline_SapXep.fcpxml"))


def test_story_controls_and_workflow_defaults(app_window):
    from src.core.recipe_manager import Recipe
    w = app_window
    assert w.combo_story_intent.currentData() == "keep" and w.txt_api_key.text() == ""
    w.combo_workflow.setCurrentIndex(w.combo_workflow.findData("shorts"))
    assert w.combo_story_intent.currentData() == "shorts"
    w.combo_workflow.setCurrentIndex(w.combo_workflow.findData("vlog"))
    assert w.combo_story_intent.currentData() == "cold_open"
    w._apply_recipe_to_ui(Recipe(id="s", name="s", story_intent="rising_action", story_target=90.0))
    assert w.combo_story_intent.currentData() == "rising_action" and w.combo_story_target.currentData() == 90.0


def test_video_type_control_and_recipe(app_window):
    from src.core.recipe_manager import Recipe
    w = app_window
    assert w.combo_video_type.currentData() == "auto" and w.combo_video_type.count() == 4
    w._apply_recipe_to_ui(Recipe(id="vt", name="vt", video_type="vlog"))
    assert w.combo_video_type.currentData() == "vlog"


def _run_worker_video_type(tmp_path, video_type, activity_value):
    import os
    import numpy as np
    from unittest.mock import patch
    from src.core.autocut import CutSegment
    video = os.path.join(tmp_path, "vt.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")
    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=True, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, speed_up_silence=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, enable_subtitles=False,
        ai_mode="silence_only", use_cache=False, scene_guard=True, video_type=video_type
    )
    segs = [CutSegment(start=0, end=30, action="keep"), CutSegment(start=30, end=31, action="cut"),
            CutSegment(start=31, end=60, action="keep")]
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=60.0), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=[]), \
         patch("src.core.autocut.SilenceDetector.detect_silence_from_wav", return_value=segs), \
         patch("src.ui.app.compute_visual_activity", return_value=np.full(60, activity_value, dtype=np.float32)), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()
    edl = open(os.path.join(tmp_path, "vt_Timeline_CatLoc.edl"), encoding="utf-8").read()
    return sum(1 for ln in edl.splitlines() if " V " in ln)


def test_pipeline_talkshow_cuts_1s_pause_but_vlog_leaves_it(tmp_path_factory):
    talk = _run_worker_video_type(tmp_path_factory.mktemp("t"), "auto", 0.02)    # hình tĩnh -> talk: cắt nhát 1s
    vlog = _run_worker_video_type(tmp_path_factory.mktemp("v"), "auto", 0.3)     # hình chuyển động -> vlog: giữ nguyên
    assert talk == 2 and vlog == 1


def test_loudnorm_pipeline_uses_correct_normalizer_signature(tmp_path):
    import os
    from unittest.mock import patch, MagicMock
    video = os.path.join(tmp_path, "ln.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")
    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=False, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, enable_subtitles=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, ai_mode="silence_only",
        use_cache=False, scene_guard=False, enable_loudnorm=True, loudnorm_preset="youtube_tiktok"
    )
    metrics = MagicMock(measured_i=-20.0, measured_tp=-3.0, target_i=-14.0, target_tp=-1.0, gain_delta_i=6.0)
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=10.0), \
         patch("src.core.autocut.get_media_metadata", return_value={"duration": 10.0, "fps": 30.0, "start_seconds": 0.0, "has_audio": True}), \
         patch("src.core.audio_normalizer.AudioNormalizer.normalize_audio_file", return_value=metrics) as norm, \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()
    kwargs = norm.call_args.kwargs
    assert kwargs["input_audio_path"].endswith(".wav") and kwargs["output_audio_path"].endswith("ln_Normalized.wav")
    assert kwargs["target"].target_i == -14.0


def test_playhead_changed_handler_does_not_crash(app_window):
    app_window._on_mini_timeline_playhead_changed(12.5)    # trước đây NameError: seconds_to_timecode
    assert "00:00:12" in app_window.txt_console.toPlainText()


def _run_worker_story_no_cut(tmp_path, intent, target=60.0):
    import os
    import numpy as np
    from unittest.mock import patch
    video = os.path.join(tmp_path, "nocut.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")
    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=False, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, speed_up_silence=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, enable_subtitles=True,
        ai_mode="silence_only", use_cache=False, story_intent=intent, story_target=target
    )
    subs = [{"start": 2.0, "end": 8.0, "text": "Chào mừng các bạn đến với vlog hôm nay", "words": []},
            {"start": 30.0, "end": 36.0, "text": "Wow nhìn này không thể tin được", "words": []},
            {"start": 70.0, "end": 76.0, "text": "Cảnh ở đây rất đẹp", "words": []},
            {"start": 105.0, "end": 112.0, "text": "Tóm lại hãy đăng ký kênh nhé", "words": []}]
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=120.0), \
         patch("src.core.autocut.get_media_metadata", return_value={"duration": 120.0, "fps": 30.0, "start_seconds": 0.0, "has_audio": True}), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=subs), \
         patch("src.ui.app.compute_visual_activity", return_value=np.full(120, 0.2, dtype=np.float32)), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()
    return tmp_path


def test_story_arranger_works_without_silence_cut(tmp_path):
    import os
    _run_worker_story_no_cut(tmp_path, "cold_open")
    assert os.path.exists(os.path.join(tmp_path, "nocut_Timeline_Goc_CoSub.fcpxml"))      # timeline gốc, không cắt
    assert os.path.exists(os.path.join(tmp_path, "nocut_Timeline_SapXep.fcpxml"))         # timeline đã sắp xếp
    plan = open(os.path.join(tmp_path, "nocut_KeHoach_SapXep.txt"), encoding="utf-8").read()
    assert "Hook" in plan


def test_shorts_intent_without_silence_cut_builds_short_timeline(tmp_path):
    import os
    import re
    _run_worker_story_no_cut(tmp_path, "shorts", target=30.0)
    xml = open(os.path.join(tmp_path, "nocut_Timeline_SapXep.fcpxml"), encoding="utf-8").read()
    total = sum(int(n) / int(d) for n, d in re.findall(r'<asset-clip[^>]*duration="(\d+)/(\d+)s"', xml))
    assert 10.0 < total <= 30.5       # video 120s được rút còn <= 30s theo ngân sách


def test_story_review_table_ui_interactions(app_window):
    from src.core.story_arranger import Block, ArrangedItem, Arrangement
    blocks = [
        Block(id=0, t0=0.0, t1=15.0, text="Chào mừng đến với vlog", role="intro", reason="Mở đầu"),
        Block(id=1, t0=15.0, t1=40.0, text="Cảnh đẹp tuyệt vời", role="build", reason="Diễn biến"),
        Block(id=2, t0=40.0, t1=60.0, text="Wow không thể tin được", role="hook", reason="Hook cao trào"),
        Block(id=3, t0=60.0, t1=80.0, text="Tạm biệt và hẹn gặp lại", role="outro", reason="Kết thúc"),
    ]
    items = [
        ArrangedItem(block_id=2, t0=40.0, t1=60.0, role="hook", reason="Hook đắt giá"),
        ArrangedItem(block_id=0, t0=0.0, t1=15.0, role="intro", reason="Mở đầu"),
        ArrangedItem(block_id=1, t0=15.0, t1=40.0, role="build", reason="Diễn biến"),
        ArrangedItem(block_id=3, t0=60.0, t1=80.0, role="outro", reason="Kết thúc"),
    ]
    arr = Arrangement(intent="shorts", items=items, total_seconds=75.0, changed=True)

    app_window._populate_story_review_table(blocks, arr, target_seconds=60.0)
    assert not app_window.table_story_review.isHidden()
    assert app_window.table_story_review.rowCount() == 4

    # Kiểm tra Move Down item 0 (Hook)
    app_window.table_story_review.selectRow(0)
    app_window._on_story_move_down()
    assert app_window.story_review_state.items[0].block_id == 0
    assert app_window.story_review_state.items[1].block_id == 2

    # Kiểm tra Undo
    app_window._story_undo()
    assert app_window.story_review_state.items[0].block_id == 2

    # Kiểm tra Toggle disable item 0
    app_window.table_story_review.selectRow(0)
    app_window._story_toggle_selected()
    assert app_window.story_review_state.items[0].enabled is False
    assert "Đang bật 3/4 khối" in app_window.lbl_story_summary.text()

    # Kiểm tra Enable All
    app_window._story_enable_all()
    assert all(it.enabled for it in app_window.story_review_state.items)
    assert "Đang bật 4/4 khối" in app_window.lbl_story_summary.text()


def test_story_arrangement_override_in_pipeline(tmp_path):
    import os
    import re
    from unittest.mock import patch
    from src.core.story_arranger import ArrangedItem, Arrangement
    video = os.path.join(tmp_path, "custom_story.mp4")
    with open(video, "w", encoding="utf-8") as f:
        f.write("mock")

    # Chỉ bật 1 phân đoạn 20s
    custom_items = [
        ArrangedItem(block_id=0, t0=10.0, t1=30.0, role="hook", reason="Tùy biến", enabled=True),
        ArrangedItem(block_id=1, t0=50.0, t1=90.0, role="build", reason="Bỏ qua", enabled=False),
    ]
    override = Arrangement(intent="shorts", items=custom_items, total_seconds=20.0, changed=True)

    worker = PipelineWorker(
        video_paths=[video], model_size="tiny", language="Auto", run_cut=False, silence_db=-35.0,
        min_duration=0.5, split_mode="characters", split_limit=42, speed_up_silence=False,
        enable_broll=False, enable_sfx=False, enable_vlog_hook=False, enable_subtitles=True,
        ai_mode="silence_only", use_cache=False, story_intent="shorts", story_target=60.0,
        story_arrangement_override=override
    )
    subs = [{"start": 12.0, "end": 28.0, "text": "Câu thoại nằm trong phân đoạn 10-30s", "words": []}]
    with patch("src.core.audio.AudioExtractor.extract_audio"), \
         patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=120.0), \
         patch("src.core.autocut.get_media_metadata", return_value={"duration": 120.0, "fps": 30.0, "start_seconds": 0.0, "has_audio": True}), \
         patch("src.core.transcriber.ResolveTranscriber.load_model"), \
         patch("src.core.transcriber.ResolveTranscriber.transcribe", return_value=subs), \
         patch("src.ui.app.compute_visual_activity", return_value=None), \
         patch("src.core.resolve_api.ResolveAutomation.ensure_resolve_running", return_value=False), \
         patch("src.core.resolve_api.ResolveAutomation.import_edl_to_timeline", return_value=False):
        worker.run()

    fcpxml_path = os.path.join(tmp_path, "custom_story_Timeline_SapXep.fcpxml")
    assert os.path.exists(fcpxml_path)
    xml = open(fcpxml_path, encoding="utf-8").read()
    durations = [int(n) / int(d) for n, d in re.findall(r'<asset-clip[^>]*duration="(\d+)/(\d+)s"', xml)]
    assert sum(durations) == 20.0     # Đúng 20.0s do chỉ lấy khối 1 (khối 2 đã tắt)


def test_project_folder_ui_and_drag_drop(app_window, tmp_path):
    proj_dir = tmp_path / "Vlog_DuLich_Hue"
    proj_dir.mkdir()

    intro_dir = proj_dir / "01_MoDau"
    intro_dir.mkdir()
    (intro_dir / "clip1.mp4").write_text("mock")

    broll_dir = proj_dir / "B-Roll_SongHuong"
    broll_dir.mkdir()
    (broll_dir / "sc1.mov").write_text("mock")

    # Nạp folder dự án vào giao diện
    app_window._load_project_folder(str(proj_dir))
    assert app_window.project_structure is not None
    assert app_window.project_structure.root_name == "Vlog_DuLich_Hue"
    assert len(app_window.selected_files) == 2
    assert "Vlog_DuLich_Hue" in app_window.lbl_file.text()
    assert "2 nhóm" in app_window.lbl_file.text()


def test_google_drive_dialog_and_import(app_window, tmp_path):
    from src.ui.widgets.drive_dialog import GoogleDriveImportDialog
    dlg = GoogleDriveImportDialog(app_window)
    
    # Test invalid URL
    dlg.txt_url.setText("invalid_url")
    assert dlg.btn_download.isEnabled() is False

    # Test valid Drive URL
    dlg.txt_url.setText("https://drive.google.com/file/d/1MockFileID1234567890/view")
    assert dlg.btn_download.isEnabled() is True
    assert "Tệp Video Đơn" in dlg.lbl_info.text()

    # Test import callback on main window
    mock_file = tmp_path / "Drive_Clip.mp4"
    mock_file.write_text("mock")
    app_window._on_google_drive_import_completed(str(mock_file), is_folder=False)
    assert app_window.selected_files == [str(mock_file)]
    assert str(mock_file) in app_window.lbl_file.text()



