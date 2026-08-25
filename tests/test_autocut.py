import os
import wave
import struct
import pytest
import numpy as np
from unittest.mock import patch
from pydantic import ValidationError
from src.core.autocut import AudioCutConfig, SilenceDetector

def create_test_audio_wav(path: str, sample_rate: int = 16000) -> None:
    """
    Tạo tệp WAV chứa 3 phần:
    - Giây đầu tiên (0.0s - 1.0s): Tiếng ồn hình sin (Có âm thanh)
    - Giây thứ hai (1.0s - 2.0s): Im lặng tuyệt đối (Amplitude = 0)
    - Giây thứ ba (2.0s - 3.0s): Tiếng ồn hình sin (Có âm thanh)
    Tổng thời lượng: 3.0s. Đoạn im lặng nằm giữa từ 1.0s đến 2.0s.
    """
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1) # mono
        wav_file.setsampwidth(2) # 16-bit PCM
        wav_file.setframerate(sample_rate)
        
        # 1. Đoạn 1: Cường độ cao (Sin wave)
        for i in range(sample_rate):
            # Biên độ lớn 10000
            val = int(10000 * np.sin(2 * np.pi * 440 * i / sample_rate))
            wav_file.writeframes(struct.pack('<h', val))
            
        # 2. Đoạn 2: Im lặng (0)
        for _ in range(sample_rate):
            wav_file.writeframes(struct.pack('<h', 0))
            
        # 3. Đoạn 3: Cường độ cao (Sin wave)
        for i in range(sample_rate):
            val = int(10000 * np.sin(2 * np.pi * 440 * i / sample_rate))
            wav_file.writeframes(struct.pack('<h', val))

def test_audio_cut_config_validation() -> None:
    """
    Kiểm tra tính hợp lệ và cấu hình của Pydantic AudioCutConfig.
    """
    config = AudioCutConfig(min_silent_duration=1.0, silence_threshold_db=-40.0)
    assert config.min_silent_duration == 1.0
    assert config.silence_threshold_db == -40.0

    with pytest.raises(ValidationError):
        # min_silent_duration quá ngắn (giới hạn dưới là 0.1)
        AudioCutConfig(min_silent_duration=0.05)

def test_detect_silence(tmp_path) -> None:
    """
    Xác thực thuật toán lọc khoảng lặng trên tệp WAV mẫu.
    Với cấu hình không có padding, đoạn giữ lại phải xấp xỉ [(0.0, 1.0), (2.0, 3.0)].
    """
    wav_path = os.path.join(tmp_path, "test_cut.wav")
    create_test_audio_wav(wav_path)

    # Cấu hình không sử dụng padding để kiểm tra độ chính xác tuyệt đối của biên cắt
    config = AudioCutConfig(
        min_silent_duration=0.5,
        silence_threshold_db=-30.0,
        padding_seconds=0.0
    )

    keep_intervals = SilenceDetector.detect_silence_from_wav(wav_path, config)

    # Hệ thống phải trả về đúng 2 phân đoạn có tiếng nói
    assert len(keep_intervals) == 2
    
    # Kiểm tra biên độ thời gian của đoạn 1 (0.0s - 1.0s)
    # Cho phép sai số nhỏ do kích thước cửa sổ 50ms (0.05s)
    assert abs(keep_intervals[0][0] - 0.0) < 0.06
    assert abs(keep_intervals[0][1] - 1.0) < 0.06

    # Kiểm tra biên độ thời gian của đoạn 2 (2.0s - 3.0s)
    assert abs(keep_intervals[1][0] - 2.0) < 0.06
    assert abs(keep_intervals[1][1] - 3.0) < 0.06

def test_detect_silence_file_not_found() -> None:
    """
    Đảm bảo ném lỗi FileNotFoundError khi tệp không tồn tại.
    """
    config = AudioCutConfig()
    with pytest.raises(FileNotFoundError):
        SilenceDetector.detect_silence_from_wav("non_existent_audio.wav", config)

def test_seconds_to_timecode() -> None:
    from src.core.autocut import seconds_to_timecode
    assert seconds_to_timecode(0.0, 30.0) == "00:00:00:00"
    assert seconds_to_timecode(1.5, 30.0) == "00:00:01:15"
    assert seconds_to_timecode(3661.2, 25.0) == "01:01:01:05"

def test_edl_generation(tmp_path) -> None:
    from src.core.autocut import EDLGenerator
    from unittest.mock import patch
    video_path = os.path.join(tmp_path, "dummy.mp4")
    with open(video_path, "w") as f:
        f.write("")
        
    keep_intervals = [(1.0, 3.5), (5.0, 8.0)]
    output_edl = os.path.join(tmp_path, "test.edl")
    
    with patch('src.core.autocut.get_video_fps', return_value=30.0):
        EDLGenerator.create_edl(video_path, keep_intervals, output_edl)
        
    assert os.path.exists(output_edl)
    with open(output_edl, "r", encoding="utf-8") as f:
        content = f.read()
        
    assert "TITLE: Silence Cut" in content
    assert "001  AX       V     C        00:00:01:00 00:00:03:15 00:00:00:00 00:00:02:15" in content
    assert "* FROM CLIP NAME: dummy.mp4" in content

def test_multi_clip_edl(tmp_path) -> None:
    from src.core.autocut import EDLGenerator
    events = [
        {
            "video_path": "clip1.mp4",
            "src_in": 1.0,
            "src_out": 4.0,
            "rec_in": 0.0,
            "rec_out": 3.0,
            "fps": 30.0
        },
        {
            "video_path": "clip2.mp4",
            "src_in": 2.0,
            "src_out": 5.0,
            "rec_in": 3.0,
            "rec_out": 6.0,
            "fps": 30.0
        }
    ]
    output_edl = os.path.join(tmp_path, "multi.edl")
    EDLGenerator.create_multi_clip_edl(events, output_edl)
    
    assert os.path.exists(output_edl)
    with open(output_edl, "r", encoding="utf-8") as f:
        content = f.read()
    
    assert "TITLE: Silence Cut Multi-Clip" in content
    assert "001  AX       V     C        00:00:01:00 00:00:04:00 00:00:00:00 00:00:03:00" in content
    assert "* FROM CLIP NAME: clip1.mp4" in content
    assert "002  AX       V     C        00:00:02:00 00:00:05:00 00:00:03:00 00:00:06:00" in content
    assert "* FROM CLIP NAME: clip2.mp4" in content

def test_detect_intervals_with_speedup():
    from src.core.autocut import AudioCutConfig, SilenceDetector
    config = AudioCutConfig(speed_up_silence=True, silence_speed_multiplier=8.0)
    with patch('src.core.autocut.SilenceDetector.detect_silence_from_wav', return_value=[(2.0, 5.0), (10.0, 15.0)]):
        with patch('src.core.audio.AudioExtractor.get_audio_duration', return_value=20.0):
            segments = SilenceDetector.detect_intervals_with_speedup("dummy.wav", config, speed_multiplier=8.0)
            assert len(segments) >= 3
            speedup_segs = [s for s in segments if s["type"] == "speedup"]
            assert len(speedup_segs) >= 1
            assert speedup_segs[0]["speed"] == 8.0

