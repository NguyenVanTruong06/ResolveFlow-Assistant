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


def test_cuda_model_load_out_of_memory_falls_back_to_offline_cpu(monkeypatch):
    import sys
    from types import SimpleNamespace

    attempts = []
    cpu_model = object()

    def load_model(size, *, device, compute_type, local_files_only):
        attempts.append((size, device, compute_type, local_files_only))
        if device == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return cpu_model

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=load_model))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setattr("src.core.transcriber.register_nvidia_dll_dirs", lambda: None)
    transcriber = ResolveTranscriber(ModelConfig(model_size="medium"))

    transcriber.load_model(local_files_only=True)

    assert transcriber.model is cpu_model
    assert transcriber.active_device == "cpu"
    assert attempts == [("medium", "cuda", "float16", True), ("medium", "cpu", "int8", True)]


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
    import wave
    with wave.open(dummy_wav, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 1600)

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


def test_normalize_whisper_segment_numbers_converts_numpy_scalars() -> None:
    import numpy as np
    from src.core.transcriber import normalize_whisper_segment_numbers

    raw_seg = {
        "start": np.float64(1.25),
        "end": np.float64(3.75),
        "text": "Kiểm thử chuẩn hóa số học",
        "words": [
            {"word": "Kiểm", "start": np.float32(1.25), "end": np.float32(1.8), "probability": np.float64(0.99)},
            {"word": "thử", "start": np.float32(1.8), "end": np.float32(2.5), "probability": np.float64(0.95)},
        ],
        "avg_logprob": np.float64(-0.35),
        "no_speech_prob": np.float64(0.01),
        "compression_ratio": np.float64(1.05),
        "temperature": np.float64(0.0),
    }

    norm = normalize_whisper_segment_numbers(raw_seg)
    assert type(norm["start"]) is float
    assert norm["start"] == 1.25
    assert type(norm["end"]) is float
    assert norm["end"] == 3.75
    assert type(norm["words"][0]["start"]) is float
    assert type(norm["words"][0]["end"]) is float
    assert type(norm["words"][0]["probability"]) is float
    assert type(norm["avg_logprob"]) is float


def test_normalize_whisper_segment_numbers_rejects_bool_string_nan_inf() -> None:
    import numpy as np
    from src.core.transcriber import normalize_whisper_segment_numbers

    # Bác bỏ bool
    with pytest.raises(TypeError):
        normalize_whisper_segment_numbers({"start": True, "end": 2.0, "text": "bad"})

    # Bác bỏ np.bool_
    with pytest.raises(TypeError):
        normalize_whisper_segment_numbers({"start": 0.0, "end": np.bool_(False), "text": "bad"})

    # Bác bỏ string
    with pytest.raises(TypeError):
        normalize_whisper_segment_numbers({"start": "0.0", "end": "2.0", "text": "bad"})

    # Bác bỏ NaN
    with pytest.raises(ValueError):
        normalize_whisper_segment_numbers({"start": float("nan"), "end": 2.0, "text": "bad"})

    # Bác bỏ Infinity
    with pytest.raises(ValueError):
        normalize_whisper_segment_numbers({"start": 0.0, "end": float("inf"), "text": "bad"})


def test_transcriber_numpy_segments_real_scan_cache_save_and_reload(tmp_path) -> None:
    import os
    import json
    import numpy as np
    from src.core.transcriber import normalize_whisper_segment_numbers
    from src.core.cache_manager import ScanCacheManager, compute_full_file_sha256

    # 1. Tạo tệp giả lập thực tế trên đĩa
    sample_file = os.path.join(tmp_path, "sample_clip.mp4")
    with open(sample_file, "wb") as f:
        f.write(b"sample video media content for hashing" * 100)

    full_hash, prov = compute_full_file_sha256(sample_file)

    # 2. Segments có timestamp dạng np.float64 từ Whisper
    whisper_raw_subs = [
        {
            "start": np.float64(0.0),
            "end": np.float64(2.4),
            "text": "Câu thứ nhất",
            "words": [{"word": "Câu", "start": np.float64(0.0), "end": np.float64(0.8), "probability": np.float64(0.99)}],
        },
        {
            "start": np.float64(2.5),
            "end": np.float64(4.8),
            "text": "Câu thứ hai",
            "words": [{"word": "thứ", "start": np.float64(2.5), "end": np.float64(3.2), "probability": np.float64(0.98)}],
        },
    ]

    safe_subs = [normalize_whisper_segment_numbers(s) for s in whisper_raw_subs]

    # 3. Lưu vào ScanCacheManager thực tế trong tmp_path
    cache_dir = os.path.join(tmp_path, "cache")
    cm = ScanCacheManager(cache_dir=cache_dir)
    save_path = cm.save_scan_result(
        sample_file,
        {"clip_dur": 5.0, "raw_subtitles": safe_subs},
        model_size="medium",
        language="vi",
        verified_identity=(full_hash, prov),
    )
    assert save_path != ""
    assert os.path.exists(save_path)

    # Xác thực file JSON trên đĩa chứa native float, không còn numpy scalar
    with open(save_path, "r", encoding="utf-8") as f:
        loaded_raw = json.load(f)
    assert loaded_raw["cache_schema_version"] == 3
    assert loaded_raw["raw_subtitles"][0]["start"] == 0.0
    assert loaded_raw["raw_subtitles"][0]["end"] == 2.4

    # 4. Mở instance hoàn toàn mới và tải lại (cache hit)
    fresh_cm = ScanCacheManager(cache_dir=cache_dir)
    cached_data = fresh_cm.get_cached_scan(
        sample_file,
        model_size="medium",
        language="vi",
        verified_identity=(full_hash, prov),
    )
    assert cached_data is not None
    assert cached_data["clip_dur"] == 5.0
    assert len(cached_data["raw_subtitles"]) == 2
    assert cached_data["raw_subtitles"][0]["text"] == "Câu thứ nhất"
    assert cached_data["raw_subtitles"][1]["text"] == "Câu thứ hai"

    # 5. Khẳng định cache vẫn kiên quyết từ chối bool/string/NaN/Inf
    bad_payloads = [
        {"clip_dur": 5.0, "raw_subtitles": [{"start": True, "end": 2.0, "text": "err"}]},
        {"clip_dur": 5.0, "raw_subtitles": [{"start": 0.0, "end": "2.0", "text": "err"}]},
        {"clip_dur": float("nan"), "raw_subtitles": []},
        {"clip_dur": float("inf"), "raw_subtitles": []},
    ]
    for bad in bad_payloads:
        rej = cm.save_scan_result(
            sample_file,
            bad,
            model_size="medium",
            language="vi",
            verified_identity=(full_hash, prov),
        )
        assert rej == ""
