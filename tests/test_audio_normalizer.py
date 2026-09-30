import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from src.core.audio_normalizer import (
    LoudnessTarget,
    LoudnessMetrics,
    AudioNormalizer,
    LOUDNESS_PRESETS
)


def test_loudness_target_validation_and_presets():
    tgt = LoudnessTarget()
    assert tgt.target_i == -14.0
    assert tgt.target_tp == -1.0
    assert tgt.target_lra == 11.0
    assert tgt.preset_name == "youtube_tiktok"

    # Presets
    assert "youtube_tiktok" in LOUDNESS_PRESETS
    assert "podcast_spotify" in LOUDNESS_PRESETS
    assert "broadcast_ebu_r128" in LOUDNESS_PRESETS

    ebu = AudioNormalizer.get_preset("broadcast_ebu_r128")
    assert ebu.target_i == -23.0
    assert ebu.target_lra == 7.0

    # Fallback to youtube_tiktok for unknown preset
    unknown = AudioNormalizer.get_preset("unknown_platform")
    assert unknown.target_i == -14.0


def test_parse_loudnorm_json_valid():
    sample_stderr = """
    [Parsed_loudnorm_0 @ 0000020084f39bc0] 
    {
        "input_i" : "-26.42",
        "input_tp" : "-4.21",
        "input_lra" : "8.70",
        "input_thresh" : "-37.10",
        "output_i" : "-14.00",
        "output_tp" : "-1.00",
        "output_lra" : "8.70",
        "output_thresh" : "-24.70",
        "normalization_type" : "dynamic",
        "target_offset" : "0.00"
    }
    """
    m = AudioNormalizer._parse_loudnorm_json(sample_stderr)
    assert m["input_i"] == -26.42
    assert m["input_tp"] == -4.21
    assert m["input_lra"] == 8.70
    assert m["output_i"] == -14.0
    assert m["output_tp"] == -1.0


def test_parse_loudnorm_json_invalid():
    with pytest.raises(ValueError):
        AudioNormalizer._parse_loudnorm_json("Some error output without any json")


def test_measure_loudness_nonexistent():
    with pytest.raises(FileNotFoundError):
        AudioNormalizer.measure_loudness("non_existent_audio_file.wav")


@patch("subprocess.run")
@patch("os.path.exists")
def test_measure_loudness_mocked(mock_exists, mock_run):
    mock_exists.return_value = True
    sample_stderr = """
    {
        "input_i" : "-22.50",
        "input_tp" : "-3.10",
        "input_lra" : "9.20",
        "input_thresh" : "-33.00",
        "output_i" : "-14.00",
        "output_tp" : "-1.00",
        "output_lra" : "9.20",
        "output_thresh" : "-24.50",
        "target_offset" : "0.00"
    }
    """
    mock_run.return_value = MagicMock(returncode=0, stderr=sample_stderr)

    res = AudioNormalizer.measure_loudness("dummy_audio.wav")
    assert res["input_i"] == -22.50
    assert res["input_tp"] == -3.10


@patch.object(AudioNormalizer, "measure_loudness")
@patch("subprocess.run")
def test_normalize_audio_file_pipeline_mocked(mock_run, mock_measure):
    mock_measure.return_value = {
        "input_i": -25.0,
        "input_tp": -3.5,
        "input_lra": 10.0,
        "input_thresh": -35.0,
        "output_i": -14.0,
        "output_tp": -1.0,
        "output_lra": 10.0,
        "output_thresh": -24.0,
        "target_offset": 0.0
    }

    with tempfile.TemporaryDirectory() as td:
        in_file = os.path.join(td, "input.wav")
        out_file = os.path.join(td, "output.wav")
        with open(in_file, "w") as f:
            f.write("audio_data")

        # Giả lập FFmpeg pass 2 tạo file output
        def fake_run(cmd, **kwargs):
            with open(out_file, "w") as f:
                f.write("normalized_audio_data")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        metrics = AudioNormalizer.normalize_audio_file(
            input_audio_path=in_file,
            output_audio_path=out_file,
            target=AudioNormalizer.get_preset("youtube_tiktok")
        )

        assert metrics.input_i == -25.0
        assert metrics.output_i == -14.0
        assert metrics.target_preset == "youtube_tiktok"
        assert os.path.exists(out_file)


@patch.object(AudioNormalizer, "measure_loudness")
@patch("subprocess.run")
def test_normalize_video_audio_mocked(mock_run, mock_measure):
    mock_measure.return_value = {
        "input_i": -20.0,
        "input_tp": -2.0,
        "input_lra": 8.0,
        "input_thresh": -30.0,
        "target_offset": 0.0
    }

    with tempfile.TemporaryDirectory() as td:
        in_vid = os.path.join(td, "input.mp4")
        out_vid = os.path.join(td, "output.mp4")
        with open(in_vid, "w") as f:
            f.write("video_content")

        def fake_run(cmd, **kwargs):
            with open(out_vid, "w") as f:
                f.write("normalized_video_content")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        ok = AudioNormalizer.normalize_video_audio(in_vid, out_vid)
        assert ok is True
        assert os.path.exists(out_vid)
