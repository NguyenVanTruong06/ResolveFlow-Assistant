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

def test_workflow_modes_switch(qapp):
    window = ResolveFlowApp()
    
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

def test_master_intensity_slider(qapp):
    window = ResolveFlowApp()
    
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

def test_recipe_and_presets_ui(qapp):
    window = ResolveFlowApp()
    
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
