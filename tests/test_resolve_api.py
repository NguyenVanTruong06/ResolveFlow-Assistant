import os
import pytest
from unittest.mock import patch
from pydantic import ValidationError
from src.core.resolve_api import SubtitleConfig, ResolveAutomation

def test_subtitle_config_validation() -> None:
    """
    Kiểm tra tính hợp lệ của cấu hình Pydantic SubtitleConfig.
    """
    config = SubtitleConfig(max_chars_per_line=30, font_size=50, color_hex="#FF0000")
    assert config.max_chars_per_line == 30
    assert config.font_size == 50
    assert config.color_hex == "#FF0000"

    with pytest.raises(ValidationError):
        # max_chars_per_line quá nhỏ (giới hạn dưới là 10)
        SubtitleConfig(max_chars_per_line=5)

def test_generate_srt(tmp_path) -> None:
    """
    Xác thực thuật toán sinh tệp SRT từ dữ liệu giây thô.
    Đặc biệt kiểm tra định dạng thời gian HH:MM:SS,mmm.
    """
    subtitles = [
        {"start": 1.5, "end": 4.25, "text": "Chào các bạn."},
        {"start": 5.1234, "end": 8.9, "text": "ResolveFlow Assistant đang chạy."}
    ]

    output_file = os.path.join(tmp_path, "test.srt")
    ResolveAutomation.generate_srt(subtitles, output_file)

    assert os.path.exists(output_file)

    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Xác thực cấu trúc tệp SRT
    expected_lines = [
        "1",
        "00:00:01,500 --> 00:00:04,250",
        "Chào các bạn.",
        "",
        "2",
        "00:00:05,123 --> 00:00:08,900",
        "ResolveFlow Assistant đang chạy."
    ]
    for expected in expected_lines:
        assert expected in content

def test_connect_fail() -> None:
    """
    Đảm bảo khi không có ứng dụng DaVinci Resolve chạy nền, hàm connect trả về False
    mà không làm sập (crash) ứng dụng.
    """
    automator = ResolveAutomation()
    # Mocking sys.path và module import để tránh nạp SDK thực tế khi test offline
    with patch('os.path.exists', return_value=False):
        connected = automator.connect()
        assert connected is False
