from pathlib import Path
from unittest.mock import patch, MagicMock
from src.core.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path, is_ffmpeg_available
from src.core.audio_processor import AudioProcessor, inspect_audio_codec, is_linux_aac_conflict

def test_ffmpeg_utils_which():
    # Should use shutil.which, not hardcoded .exe
    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        assert get_ffmpeg_path() == "/usr/bin/ffmpeg"
        assert is_ffmpeg_available() is True

def test_inspect_audio_codec_mock():
    mock_probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"}
        ]
    }
    with patch("src.core.audio_processor.probe_streams", return_value=mock_probe):
        codec = inspect_audio_codec("mock_video.mp4")
        assert codec == "aac"

def test_is_linux_aac_conflict():
    with patch("src.core.audio_processor.inspect_audio_codec", return_value="aac"), \
         patch("sys.platform", "linux"):
        assert is_linux_aac_conflict("mock_video.mp4") is True

    with patch("src.core.audio_processor.inspect_audio_codec", return_value="pcm_s16le"), \
         patch("sys.platform", "linux"):
        assert is_linux_aac_conflict("mock_video.mp4") is False

    with patch("src.core.audio_processor.inspect_audio_codec", return_value="aac"), \
         patch("sys.platform", "win32"):
        assert is_linux_aac_conflict("mock_video.mp4") is False
