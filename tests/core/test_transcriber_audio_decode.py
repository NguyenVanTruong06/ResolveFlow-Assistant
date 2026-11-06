import os
import tempfile
import wave
import subprocess
import numpy as np
import pytest
from unittest.mock import MagicMock

from src.core.transcriber import (
    ModelConfig,
    ResolveTranscriber,
    load_audio_waveform,
)


def _create_wav_pcm16(path: str, duration_sec: float = 0.2, sr: int = 16000, channels: int = 1) -> None:
    n_samples = int(duration_sec * sr)
    # Sine wave or silence
    t = np.linspace(0, duration_sec, n_samples, endpoint=False)
    waveform = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
    if channels == 2:
        waveform = np.column_stack([waveform, waveform]).flatten()
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(waveform.tobytes())


def test_load_audio_waveform_valid_pcm16_fast_path(tmp_path) -> None:
    wav_path = str(tmp_path / "valid_16k_mono.wav")
    _create_wav_pcm16(wav_path, duration_sec=0.2, sr=16000, channels=1)

    arr = load_audio_waveform(wav_path)
    assert isinstance(arr, np.ndarray)
    assert arr.dtype == np.float32
    assert arr.ndim == 1
    assert len(arr) == int(0.2 * 16000)
    assert np.all(np.isfinite(arr))
    assert np.max(arr) <= 1.0
    assert np.min(arr) >= -1.0


def test_load_audio_waveform_stereo_resampled_via_pyav(tmp_path) -> None:
    wav_path = str(tmp_path / "stereo_44k.wav")
    _create_wav_pcm16(wav_path, duration_sec=0.25, sr=44100, channels=2)

    arr = load_audio_waveform(wav_path)
    assert isinstance(arr, np.ndarray)
    assert arr.dtype == np.float32
    assert arr.ndim == 1
    # Khoảng 0.25s @ 16kHz ~ 4000 samples (+/- sai số resampler frame boundary)
    assert 3800 <= len(arr) <= 4200
    assert np.all(np.isfinite(arr))


def test_load_audio_waveform_from_ndarray() -> None:
    input_arr = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    out = load_audio_waveform(input_arr)
    assert np.array_equal(out, input_arr)
    assert out.dtype == np.float32


def test_load_audio_waveform_rejects_non_finite() -> None:
    bad_arr = np.array([0.1, np.nan, 0.5], dtype=np.float32)
    with pytest.raises(ValueError, match="non-finite"):
        load_audio_waveform(bad_arr)

    bad_inf = np.array([0.1, np.inf, 0.5], dtype=np.float32)
    with pytest.raises(ValueError, match="non-finite"):
        load_audio_waveform(bad_inf)


def test_load_audio_waveform_rejects_invalid_shape() -> None:
    bad_2d = np.zeros((100, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="1D"):
        load_audio_waveform(bad_2d)


def test_load_audio_waveform_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_audio_waveform("/tmp/path_that_definitely_does_not_exist_12345.wav")


def test_load_audio_waveform_empty_file(tmp_path) -> None:
    empty_path = str(tmp_path / "empty.wav")
    with open(empty_path, "wb") as f:
        pass
    with pytest.raises(ValueError):
        load_audio_waveform(empty_path)


def test_transcribe_passes_ndarray_to_whisper_backend(tmp_path) -> None:
    wav_path = str(tmp_path / "speech.wav")
    _create_wav_pcm16(wav_path, duration_sec=0.1, sr=16000, channels=1)

    config = ModelConfig(model_size="small", device="cpu", compute_type="float32")
    transcriber = ResolveTranscriber(config)

    mock_model = MagicMock()
    mock_seg = MagicMock()
    mock_seg.start = 0.0
    mock_seg.end = 0.1
    mock_seg.text = "Hello world"
    mock_seg.avg_logprob = -0.3
    mock_seg.no_speech_prob = 0.01
    mock_seg.compression_ratio = 1.0
    mock_seg.temperature = 0.0
    mock_seg.words = []

    mock_info = MagicMock()
    mock_info.duration = 0.1
    mock_info.language = "en"
    mock_info.language_probability = 0.99

    mock_model.transcribe.return_value = ([mock_seg], mock_info)
    transcriber.model = mock_model

    results = transcriber.transcribe(wav_path, language="en")
    assert len(results) == 1
    assert results[0]["text"] == "Hello world"

    # Backend transcribe PHẢI nhận np.ndarray float32, KHÔNG nhận string audio_path
    call_args = mock_model.transcribe.call_args
    passed_audio = call_args[0][0]
    assert isinstance(passed_audio, np.ndarray)
    assert passed_audio.dtype == np.float32
    assert passed_audio.ndim == 1
    assert len(passed_audio) == 1600
    assert np.all(np.isfinite(passed_audio))
