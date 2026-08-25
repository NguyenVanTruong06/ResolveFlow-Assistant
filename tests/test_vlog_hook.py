import os
import wave
import struct
import pytest
from unittest.mock import patch
from src.core.vlog_hook import VlogHookConfig, HookSegment, VlogHookGenerator

def create_mock_wav_with_peak(file_path: str, duration_sec: float = 6.0, peak_start: float = 2.0, peak_duration: float = 2.0):
    """
    Tạo tệp WAV giả lập 16kHz mono có một đoạn âm lượng bùng nổ (Peak) tại [peak_start, peak_start + peak_duration].
    """
    sample_rate = 16000
    total_samples = int(duration_sec * sample_rate)
    peak_start_sample = int(peak_start * sample_rate)
    peak_end_sample = int((peak_start + peak_duration) * sample_rate)

    with wave.open(file_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        
        frames = bytearray()
        for i in range(total_samples):
            if peak_start_sample <= i < peak_end_sample:
                # Biên độ lớn (Peak)
                val = 25000 if (i % 20 < 10) else -25000
            else:
                # Biên độ nhỏ (Yên tĩnh)
                val = 500 if (i % 40 < 20) else -500
            frames.extend(struct.pack("<h", val))
        wav.writeframes(frames)

def test_vlog_hook_config():
    config = VlogHookConfig(clip_duration=2.5, target_total_duration=30.0)
    assert config.clip_duration == 2.5
    assert config.target_total_duration == 30.0
    assert config.enabled is True

def test_score_subtitle_hook():
    # Test câu hỏi tò mò tiếng Việt
    score1, reason1 = VlogHookGenerator.score_subtitle_hook("Tại sao địa điểm này lại bí mật đến vậy?")
    assert score1 >= 5.0
    assert "tò mò" in reason1 or "bí mật" in reason1

    # Test cảm thán tiếng Anh
    score2, reason2 = VlogHookGenerator.score_subtitle_hook("Wow look at this amazing view!")
    assert score2 >= 4.0
    assert "wow" in reason2.lower() or "cảm thán" in reason2.lower()

    # Test câu bình thường
    score3, reason3 = VlogHookGenerator.score_subtitle_hook("Chúng tôi đi bộ từ từ vào nhà.")
    assert score3 < score1

def test_find_audio_energy_peak(tmp_path):
    wav_file = os.path.join(tmp_path, "test_energy.wav")
    create_mock_wav_with_peak(wav_file, duration_sec=6.0, peak_start=2.0, peak_duration=2.0)
    
    start, end, rms_score = VlogHookGenerator.find_audio_energy_peak(wav_file, target_duration=2.0)
    assert abs(start - 2.0) <= 0.5
    assert abs(end - 4.0) <= 0.5
    assert rms_score > 0.3

def test_extract_highlight_from_clip(tmp_path):
    wav_file = os.path.join(tmp_path, "clip1.wav")
    create_mock_wav_with_peak(wav_file, duration_sec=8.0, peak_start=3.0, peak_duration=2.0)
    
    subtitles = [
        {"start": 0.0, "end": 2.5, "text": "Hôm nay chúng ta đi đâu?"},
        {"start": 3.0, "end": 5.0, "text": "Wow các bạn nhìn này cảnh tượng không thể tin được!"},
        {"start": 5.5, "end": 7.5, "text": "Mọi người đang đi dạo."}
    ]

    with patch("src.core.audio.AudioExtractor.get_audio_duration", return_value=8.0):
        seg = VlogHookGenerator.extract_highlight_from_clip(
            video_path="mock_video.mp4",
            wav_path=wav_file,
            subtitles=subtitles,
            clip_duration=2.0
        )
        assert seg.video_path == "mock_video.mp4"
        assert seg.duration == 2.0
        assert seg.src_in >= 2.5 and seg.src_out <= 6.0
        assert "wow" in seg.text.lower() or "tò mò" in seg.reason.lower() or "RMS" in seg.reason

def test_generate_teaser_edl(tmp_path):
    segments = [
        HookSegment(
            video_path="clipA.mp4",
            src_in=2.0,
            src_out=4.0,
            duration=2.0,
            score=8.5,
            reason="Hook: Wow nhìn này",
            text="Wow nhìn này cảnh tượng đẹp quá"
        ),
        HookSegment(
            video_path="clipB.mp4",
            src_in=10.0,
            src_out=12.5,
            duration=2.5,
            score=7.0,
            reason="Âm thanh cao trào (RMS 0.65)",
            text=""
        )
    ]

    output_edl = os.path.join(tmp_path, "teaser.edl")
    with patch("src.core.vlog_hook.get_video_fps", return_value=30.0):
        edl_path, events = VlogHookGenerator.generate_teaser_edl(segments, output_edl, project_name="TravelVlog")

    assert os.path.exists(output_edl)
    assert len(events) == 2
    assert events[0]["rec_in"] == 0.0
    assert events[0]["rec_out"] == 2.0
    assert events[1]["rec_in"] == 2.0
    assert events[1]["rec_out"] == 4.5

    with open(output_edl, "r", encoding="utf-8") as f:
        content = f.read()

    assert "TITLE: TravelVlog_Vlog_Intro_Teaser" in content
    assert "* FROM CLIP NAME: clipA.mp4" in content
    assert "* FROM CLIP NAME: clipB.mp4" in content
    assert "* LOC:" in content
    assert "Magenta" in content
