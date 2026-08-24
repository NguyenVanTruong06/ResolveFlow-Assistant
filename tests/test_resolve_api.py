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

def test_insert_subtitles_to_timeline_no_timeline(tmp_path) -> None:
    """
    Kiểm tra insert_subtitles_to_timeline khi không có timeline hoạt động.
    """
    automator = ResolveAutomation()
    subtitles = [
        {"start": 1.0, "end": 2.0, "text": "Test line."}
    ]
    config = SubtitleConfig()
    output_file = os.path.join(tmp_path, "output.srt")
    
    logs = []
    def log_cb(msg):
        logs.append(msg)
        
    with patch.object(automator, 'get_active_timeline', return_value=None):
        res = automator.insert_subtitles_to_timeline(
            subtitles, 
            config, 
            output_srt_path=output_file, 
            log_callback=log_cb
        )
        assert res is True
        
    assert os.path.exists(output_file)
    assert any("Không tìm thấy Timeline" in log for log in logs)
    assert any("Đã xuất file phụ đề SRT cục bộ thành công" in log for log in logs)
    assert any(os.path.abspath(output_file) in log for log in logs)

def test_import_edl_to_timeline(tmp_path) -> None:
    """
    Kiểm tra import_edl_to_timeline khi Resolve không chạy.
    """
    automator = ResolveAutomation()
    edl_file = os.path.join(tmp_path, "test.edl")
    video_file = os.path.join(tmp_path, "test.mp4")
    with open(edl_file, "w") as f:
        f.write("")
    with open(video_file, "w") as f:
        f.write("")
        
    logs = []
    def log_cb(msg):
        logs.append(msg)
        
    with patch.object(automator, 'connect', return_value=False):
        res = automator.import_edl_to_timeline(
            edl_path=edl_file,
            video_path=video_file,
            timeline_name="Test Timeline",
            log_callback=log_cb
        )
        assert res is False
        
    assert any("Không thể kết nối tới ứng dụng DaVinci Resolve" in log for log in logs)

def test_split_subtitles() -> None:
    from src.core.resolve_api import split_subtitles
    raw_subtitles = [
        {
            "start": 0.0,
            "end": 4.5,
            "text": "Hello world this is a test segment for resolve flow.",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.6, "end": 1.0},
                {"word": "this", "start": 1.1, "end": 1.5},
                {"word": "is", "start": 1.6, "end": 2.0},
                {"word": "a", "start": 2.1, "end": 2.2},
                {"word": "test", "start": 2.3, "end": 2.8},
                {"word": "segment", "start": 2.9, "end": 3.5},
                {"word": "for", "start": 3.6, "end": 3.9},
                {"word": "resolve", "start": 4.0, "end": 4.2},
                {"word": "flow.", "start": 4.3, "end": 4.5}
            ]
        }
    ]
    wrapped = split_subtitles(raw_subtitles, max_chars=15)
    for seg in wrapped:
        assert len(seg["text"]) <= 15
        assert seg["start"] < seg["end"]

def test_is_vertical_video(tmp_path) -> None:
    from src.core.resolve_api import is_vertical_video
    assert is_vertical_video("non_existent.mp4") is False
