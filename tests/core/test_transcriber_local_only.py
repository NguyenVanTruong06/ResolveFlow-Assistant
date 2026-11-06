"""
Unit tests for ResolveTranscriber local_files_only offline enforcement.
Task: G05-05B-I1
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch
import pytest

from src.core.transcriber import ModelConfig, ResolveTranscriber


class FakeWhisperModel:
    """Fake WhisperModel spy recording constructor calls."""
    instances = []

    def __init__(self, model_size_or_path, device="cpu", compute_type="float32", local_files_only=False, **kwargs):
        self.model_size_or_path = model_size_or_path
        self.device = device
        self.compute_type = compute_type
        self.local_files_only = local_files_only
        self.kwargs = kwargs
        FakeWhisperModel.instances.append(self)

    def transcribe(self, *args, **kwargs):
        return ([], MagicMock())


@pytest.fixture(autouse=True)
def reset_fake_whisper():
    FakeWhisperModel.instances.clear()


def test_transcriber_load_model_default_false_compatibility():
    """Callers cũ không truyền local_files_only nhận mặc định False để tương thích ngược."""
    cfg = ModelConfig(model_size="small", device="cpu", compute_type="float32")
    transcriber = ResolveTranscriber(cfg)

    fake_fw = ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeWhisperModel

    with patch.dict(sys.modules, {"faster_whisper": fake_fw}):
        transcriber.load_model()

    assert len(FakeWhisperModel.instances) == 1
    inst = FakeWhisperModel.instances[0]
    assert inst.model_size_or_path == "small"
    assert inst.local_files_only is False


def test_transcriber_load_model_propagates_local_files_only_true():
    """Khi truyền local_files_only=True, cờ được chuyển vào WhisperModel constructor."""
    cfg = ModelConfig(model_size="small", device="cpu", compute_type="float32")
    transcriber = ResolveTranscriber(cfg)

    fake_fw = ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeWhisperModel

    with patch.dict(sys.modules, {"faster_whisper": fake_fw}):
        transcriber.load_model(local_files_only=True)

    assert len(FakeWhisperModel.instances) == 1
    inst = FakeWhisperModel.instances[0]
    assert inst.local_files_only is True


def test_transcriber_load_model_propagates_local_files_only_to_cpu_fallback():
    """Khi GPU thất bại và fallback sang CPU, cờ local_files_only=True tiếp tục được bảo toàn."""
    cfg = ModelConfig(model_size="small", device="cuda", compute_type="float16")
    transcriber = ResolveTranscriber(cfg)

    class CudaFailingWhisperModel(FakeWhisperModel):
        def transcribe(self, *args, **kwargs):
            if self.device == "cuda":
                raise RuntimeError("CUDA out of memory / cuBLAS not found")
            return ([], MagicMock())

    fake_fw = ModuleType("faster_whisper")
    fake_fw.WhisperModel = CudaFailingWhisperModel

    fake_c2 = ModuleType("ctranslate2")
    fake_c2.get_cuda_device_count = MagicMock(return_value=1)

    with patch.dict(sys.modules, {"faster_whisper": fake_fw, "ctranslate2": fake_c2}):
        transcriber.load_model(local_files_only=True)

    # 1 lần cho cuda, 1 lần cho cpu fallback
    assert len(FakeWhisperModel.instances) == 2
    cuda_inst = FakeWhisperModel.instances[0]
    cpu_inst = FakeWhisperModel.instances[1]

    assert cuda_inst.device == "cuda"
    assert cuda_inst.local_files_only is True
    assert cpu_inst.device == "cpu"
    assert cpu_inst.local_files_only is True
    assert transcriber.active_device == "cpu"


def test_transcriber_load_model_missing_local_model_raises_without_download():
    """Nếu model chưa có trên máy và local_files_only=True, ném ngoại lệ và không tải gì qua mạng."""
    cfg = ModelConfig(model_size="medium", device="cpu", compute_type="float32")
    transcriber = ResolveTranscriber(cfg)

    class MissingModelWhisper:
        def __init__(self, *args, **kwargs):
            if kwargs.get("local_files_only"):
                raise FileNotFoundError("Cannot find model on local disk and local_files_only is True")

    fake_fw = ModuleType("faster_whisper")
    fake_fw.WhisperModel = MissingModelWhisper

    with patch.dict(sys.modules, {"faster_whisper": fake_fw}):
        with pytest.raises(FileNotFoundError, match="local_files_only"):
            transcriber.load_model(local_files_only=True)
