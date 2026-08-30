import os
import pytest
from unittest.mock import patch
from src.core.audio import AudioExtractor

@patch('ffmpeg.probe')
def test_get_audio_duration(mock_probe, tmp_path) -> None:
    """
    Kiểm tra chức năng đọc thời lượng tệp bằng cách giả lập tệp tin tồn tại 
    và mock lệnh gọi ffprobe bên dưới.
    """
    # Tạo tệp trống để os.path.exists vượt qua kiểm tra thành công
    dummy_file = os.path.join(tmp_path, "dummy.wav")
    with open(dummy_file, "w", encoding="utf-8") as f:
        f.write("")

    # Thiết lập giá trị trả về giả lập của ffprobe
    mock_probe.return_value = {
        "format": {
            "duration": "2.5"
        }
    }

    duration = AudioExtractor.get_audio_duration(dummy_file)
    assert duration == 2.5
    mock_probe.assert_called_once_with(dummy_file)

def test_get_audio_duration_file_not_found() -> None:
    """
    Đảm bảo hàm ném ra ngoại lệ FileNotFoundError khi đường dẫn tệp không tồn tại.
    """
    with pytest.raises(FileNotFoundError):
        AudioExtractor.get_audio_duration("non_existent_file.wav")
