import pytest
from pydantic import ValidationError
from src.core.transcriber import ModelConfig, ResolveTranscriber

def test_model_config_valid() -> None:
    """
    Kiểm tra cấu hình hợp lệ của Pydantic ModelConfig.
    """
    config = ModelConfig(model_size="tiny", device="cpu", compute_type="float32")
    assert config.model_size == "tiny"
    assert config.device == "cpu"
    assert config.compute_type == "float32"

def test_model_config_invalid() -> None:
    """
    Kiểm tra xem Pydantic có ném ra lỗi ValidationError khi truyền tham số không hợp lệ hay không.
    """
    with pytest.raises(ValidationError):
        # 'invalid_size' không nằm trong tùy chọn định sẵn
        ModelConfig(model_size="invalid_size")

    with pytest.raises(ValidationError):
        # 'invalid_device' không được hỗ trợ
        ModelConfig(device="invalid_device")
