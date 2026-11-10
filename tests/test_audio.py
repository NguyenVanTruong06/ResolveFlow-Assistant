import json
import os
import subprocess
import pytest
from unittest.mock import patch
from src.core.audio import AudioExtractor


def test_get_audio_duration_success(tmp_path) -> None:
    """Kiểm tra đọc thời lượng thành công qua boundary run_ffprobe."""
    dummy_file = os.path.join(tmp_path, "dummy.wav")
    with open(dummy_file, "w", encoding="utf-8") as f:
        f.write("fake audio bytes")

    fake_res = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps({"format": {"duration": "2.5"}}),
        stderr="",
    )
    with patch("src.core.audio.run_ffprobe", return_value=fake_res) as mock_probe:
        duration = AudioExtractor.get_audio_duration(dummy_file)
        assert duration == 2.5
        mock_probe.assert_called_once()
        call_args = mock_probe.call_args[0][0]
        assert os.path.normpath(dummy_file) in call_args
        assert "-show_format" in call_args


def test_get_audio_duration_uses_sanitized_env(tmp_path) -> None:
    """Xác thực run_ffprobe thực thi subprocess với môi trường sạch đã loại bỏ ô nhiễm."""
    dummy_file = os.path.join(tmp_path, "dummy.wav")
    with open(dummy_file, "w", encoding="utf-8") as f:
        f.write("fake audio bytes")

    fake_res = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps({"format": {"duration": "4.2"}}),
        stderr="",
    )
    with patch("src.core.ffmpeg_utils.subprocess.run", return_value=fake_res) as mock_sub:
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/opt/resolve/libs:/usr/lib"}):
            duration = AudioExtractor.get_audio_duration(dummy_file)
            assert duration == 4.2
            mock_sub.assert_called_once()
            called_env = mock_sub.call_args[1].get("env", {})
            # Thư viện ô nhiễm phải bị lọc bỏ
            assert "/opt/resolve/libs" not in called_env.get("LD_LIBRARY_PATH", "")
            # Thư viện hệ thống hợp lệ được giữ lại
            assert "/usr/lib" in called_env.get("LD_LIBRARY_PATH", "")


def test_get_audio_duration_ffprobe_error_preserved(tmp_path) -> None:
    """Bảo toàn thông điệp lỗi stderr chi tiết từ ffprobe khi thất bại."""
    dummy_file = os.path.join(tmp_path, "corrupt.wav")
    with open(dummy_file, "w", encoding="utf-8") as f:
        f.write("corrupt header")

    fake_res = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=1,
        stdout="",
        stderr="Invalid data found when processing input",
    )
    with patch("src.core.audio.run_ffprobe", return_value=fake_res):
        with pytest.raises(RuntimeError) as exc_info:
            AudioExtractor.get_audio_duration(dummy_file)
        assert "Lỗi ffprobe khi đọc siêu dữ liệu" in str(exc_info.value)
        assert "Invalid data found when processing input" in str(exc_info.value)


def test_get_audio_duration_file_not_found() -> None:
    """Đảm bảo ném ra ngoại lệ FileNotFoundError khi đường dẫn tệp không tồn tại."""
    with pytest.raises(FileNotFoundError):
        AudioExtractor.get_audio_duration("non_existent_file.wav")
