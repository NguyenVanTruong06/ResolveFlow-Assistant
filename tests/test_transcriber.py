import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError
from src.core.transcriber import (
    ModelConfig,
    ResolveTranscriber,
    MODEL_DISK_SIZES,
    is_model_on_disk,
    suggest_default_model
)


def test_model_config_valid() -> None:
    config = ModelConfig(model_size="tiny", device="cpu", compute_type="float32")
    assert config.model_size == "tiny"
    assert config.device == "cpu"
    assert config.compute_type == "float32"


def test_model_config_supports_large_v3_turbo() -> None:
    config = ModelConfig(model_size="large-v3-turbo", device="cuda", compute_type="float16")
    assert config.model_size == "large-v3-turbo"
    assert config.device == "cuda"
    assert config.compute_type == "float16"


def test_model_config_invalid() -> None:
    with pytest.raises(ValidationError):
        ModelConfig(model_size="invalid_size")

    with pytest.raises(ValidationError):
        ModelConfig(device="invalid_device")


def test_model_disk_sizes_present() -> None:
    assert "small" in MODEL_DISK_SIZES
    assert "large-v3" in MODEL_DISK_SIZES
    assert "large-v3-turbo" in MODEL_DISK_SIZES
    assert "1.6" in MODEL_DISK_SIZES["large-v3-turbo"]


def test_suggest_default_model_returns_supported_model() -> None:
    model = suggest_default_model()
    assert model in ["small", "large-v3-turbo"]


def test_is_model_on_disk_returns_bool() -> None:
    # "small" is present on this machine, while non-existent model returns False
    res_small = is_model_on_disk("small")
    assert isinstance(res_small, bool)
    res_fake = is_model_on_disk("fake_non_existent_model_123")
    assert res_fake is False


def test_transcribe_preserves_signals_and_metadata(monkeypatch, tmp_path) -> None:
    config = ModelConfig(model_size="small", device="cpu", compute_type="float32")
    transcriber = ResolveTranscriber(config)

    # Mock WhisperModel
    mock_model = MagicMock()
    transcriber.model = mock_model

    # Mock segment object
    mock_seg = MagicMock()
    mock_seg.start = 1.0
    mock_seg.end = 3.0
    mock_seg.text = "Xin chào các bạn đây là tiếng nói thật"
    mock_seg.avg_logprob = -0.42
    mock_seg.no_speech_prob = 0.05
    mock_seg.compression_ratio = 1.15
    mock_seg.temperature = 0.0
    mock_seg.words = []

    mock_info = MagicMock()
    mock_info.duration = 10.0
    mock_info.language = "vi"
    mock_info.language_probability = 0.98

    mock_model.transcribe.return_value = ([mock_seg], mock_info)

    dummy_wav = str(tmp_path / "test.wav")
    with open(dummy_wav, "wb") as f:
        f.write(b"RIFFdummydata")

    results = transcriber.transcribe(dummy_wav, language="vi")

    assert len(results) == 1
    r = results[0]
    assert r["start"] == 1.0
    assert r["end"] == 3.0
    assert r["text"] == "Xin chào các bạn đây là tiếng nói thật"
    assert r["avg_logprob"] == -0.42
    assert r["no_speech_prob"] == 0.05
    assert r["compression_ratio"] == 1.15
    assert r["temperature"] == 0.0
    assert transcriber.last_detected_language == "vi"
    assert transcriber.last_language_probability == 0.98
