import os
import sys
from unittest.mock import patch, MagicMock
import pytest
from src.core.validator import DryRunValidator, ValidationResult

def test_validate_empty_paths():
    res = DryRunValidator.validate_media_files([])
    assert res.is_valid is False
    assert len(res.errors) > 0
    assert "Không có tệp video nào được chọn" in res.errors[0]

@patch("os.path.exists")
def test_validate_nonexistent_file(mock_exists):
    mock_exists.return_value = False
    res = DryRunValidator.validate_media_files(["nonexistent.mp4"])
    assert res.is_valid is False
    assert any("Tệp không tồn tại" in err for err in res.errors)

@patch("os.path.exists")
@patch("os.path.getsize")
def test_validate_empty_file(mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 0
    res = DryRunValidator.validate_media_files(["empty.mp4"])
    assert res.is_valid is False
    assert any("Tệp video bị rỗng" in err for err in res.errors)

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_10bit_warning_windows(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    mock_get_info.return_value = {
        "codec_name": "h264",
        "pix_fmt": "yuv420p10le",
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "duration": 10.0
    }
    
    with patch("sys.platform", "win32"):
        res = DryRunValidator.validate_media_files(["video_10bit.mp4"])
        assert res.is_valid is True
        assert len(res.warnings) > 0
        assert "sử dụng codec 10-bit" in res.warnings[0]

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_10bit_no_warning_mac(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    mock_get_info.return_value = {
        "codec_name": "h264",
        "pix_fmt": "yuv420p10le",
        "fps": 30.0,
        "width": 1920,
        "height": 1080,
        "duration": 10.0
    }
    
    with patch("sys.platform", "darwin"):
        res = DryRunValidator.validate_media_files(["video_10bit.mp4"])
        assert res.is_valid is True
        assert len(res.warnings) == 0

@patch("os.path.exists")
@patch("os.path.getsize")
@patch("src.core.validator.DryRunValidator.get_video_stream_info")
def test_validate_mismatched_fps(mock_get_info, mock_getsize, mock_exists):
    mock_exists.return_value = True
    mock_getsize.return_value = 1000
    
    def side_effect(path):
        if "vid1" in path:
            return {
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "fps": 24.0,
                "width": 1920,
                "height": 1080,
                "duration": 10.0
            }
        else:
            return {
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "fps": 30.0,
                "width": 1920,
                "height": 1080,
                "duration": 10.0
            }
    mock_get_info.side_effect = side_effect
    
    res = DryRunValidator.validate_media_files(["vid1.mp4", "vid2.mp4"])
    assert res.is_valid is True
    assert len(res.warnings) > 0
    assert "Tốc độ khung hình (Frame Rate) không đồng nhất" in res.warnings[0]
