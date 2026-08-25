import pytest
from src.core.ai_director import AIDirector, AIDirectorConfig, BadTakeDetector

def test_bad_take_detector_basic():
    # Giả lập người nói thử 2 lần câu chào
    subtitles = [
        {"start": 0.0, "end": 2.0, "text": "Hôm nay mình sẽ"},
        {"start": 2.5, "end": 6.0, "text": "Hôm nay mình sẽ hướng dẫn các bạn làm món ăn này cực ngon."}
    ]
    bad_takes = BadTakeDetector.detect_bad_takes(subtitles)
    assert 0 in bad_takes
    assert 1 not in bad_takes

def test_bad_take_detector_no_bad_takes():
    subtitles = [
        {"start": 0.0, "end": 3.0, "text": "Xin chào tất cả các bạn đã quay trở lại với kênh của mình."},
        {"start": 3.5, "end": 7.0, "text": "Hôm nay chúng ta sẽ cùng khám phá một công nghệ AI mới."}
    ]
    bad_takes = BadTakeDetector.detect_bad_takes(subtitles)
    assert len(bad_takes) == 0

def test_ai_director_clean_talk_process():
    config = AIDirectorConfig(
        mode="clean_talk",
        remove_bad_takes=True,
        enable_punch_in=True,
        punch_in_scale=1.15
    )
    director = AIDirector(config)
    subtitles = [
        {"start": 1.0, "end": 2.5, "text": "Chào mừng các bạn đến với"},
        {"start": 3.0, "end": 7.0, "text": "Chào mừng các bạn đến với video hướng dẫn DaVinci Resolve ngày hôm nay."},
        {"start": 8.0, "end": 12.0, "text": "Chúng ta hãy bắt đầu ngay thôi nào."}
    ]
    silence_intervals = [(0.8, 7.2), (7.8, 12.2)]
    
    result = director.process_semantic_cut(
        subtitles=subtitles,
        silence_keep_intervals=silence_intervals,
        total_duration=15.0,
        language="vi"
    )

    assert result["stats"]["removed_bad_takes"] == 1
    assert len(result["subtitles"]) == 2
    assert len(result["keep_intervals"]) >= 1
    assert len(result["markers"]) > 0

def test_ai_director_viral_shorts():
    config = AIDirectorConfig(
        mode="viral_shorts",
        target_duration_seconds=10.0
    )
    director = AIDirector(config)
    subtitles = [
        {"start": 0.0, "end": 4.0, "text": "Bí mật này sẽ giúp bạn tiết kiệm 50% thời gian dựng video."},
        {"start": 4.5, "end": 8.0, "text": "Đầu tiên là bạn phải sử dụng công cụ AI tự động này."},
        {"start": 8.5, "end": 15.0, "text": "Nó giúp bạn gọt giũa kịch bản và cắt sạch những câu nói vấp."},
        {"start": 15.5, "end": 25.0, "text": "Và sau cùng là xuất file EDL thẳng vào DaVinci Resolve."}
    ]
    result = director.process_semantic_cut(
        subtitles=subtitles,
        silence_keep_intervals=[(0.0, 25.0)],
        total_duration=25.0
    )
    # Tổng thời lượng của video ngắn phải được giới hạn
    total_kept = sum(e - s for s, e in result["keep_intervals"])
    assert total_kept <= 12.0
