import pytest
from src.core.broll_sfx import BRollAnalyzer, SFXEngine

def test_broll_analyzer_extraction():
    subtitles = [
        {"start": 0.0, "end": 4.0, "text": "Hôm nay chúng ta sẽ tìm hiểu về công nghệ AI mới nhất."},
        {"start": 5.0, "end": 9.0, "text": "Thị trường bất động sản đang có những chuyển biến tích cực."},
        {"start": 10.0, "end": 14.0, "text": "Hãy chuẩn bị camera và máy tính để bắt đầu."}
    ]
    cues = BRollAnalyzer.extract_broll_cues(subtitles, min_interval_gap=4.0)

    assert len(cues) >= 2
    assert cues[0].keyword in ["công nghệ", "ai"]
    assert "B-roll cinematic 4k" in cues[0].search_prompt

def test_sfx_engine_generation():
    keep_intervals = [(0.0, 3.0), (3.5, 7.0), (7.5, 12.0)]
    punch_in_events = [
        {"start": 3.5, "scale": 1.15},
        {"start": 7.5, "scale": 1.15}
    ]
    sfx_cues = SFXEngine.generate_sfx_cues(
        keep_intervals=keep_intervals,
        punch_in_events=punch_in_events
    )

    assert len(sfx_cues) == 2
    assert sfx_cues[0].sfx_type == "whoosh"
    assert sfx_cues[0].time == 3.5
