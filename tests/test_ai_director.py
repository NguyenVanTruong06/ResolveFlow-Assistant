import pytest
from unittest.mock import patch
from src.core.ai_director import AIDirector, AIDirectorConfig, BadTakeDetector
from src.core.llm_director import LLMSelectionError

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

def test_bad_take_detector_split_cards_not_false_positive():
    # Khi phụ đề bị chia thành các thẻ 4-5 từ, các câu có phần đầu tương tự không được bị coi là bad take
    subtitles = [
        {"start": 0.0, "end": 1.5, "text": "Chúng ta sẽ cùng tìm"},
        {"start": 1.6, "end": 3.2, "text": "hiểu phương pháp làm video này"},
        {"start": 3.5, "end": 5.0, "text": "Chúng ta sẽ thấy kết quả rất rõ."}
    ]
    bad_takes = BadTakeDetector.detect_bad_takes(subtitles)
    assert len(bad_takes) == 0

def test_ai_director_preserves_silence_lead_in_padding():
    config = AIDirectorConfig(mode="clean_talk", remove_bad_takes=False)
    director = AIDirector(config)
    subtitles = [
        {"start": 1.0, "end": 3.0, "text": "Xin chào các bạn đã quay trở lại."}
    ]
    # Dải âm thanh gốc từ SilenceDetector có đệm đầu 0.3s (từ 0.7s)
    silence_intervals = [(0.7, 3.3)]
    
    result = director.apply_approved_segments(
        subtitles=subtitles,
        proposed_segments=director.generate_proposed_segments(subtitles),
        silence_keep_intervals=silence_intervals,
        total_duration=5.0
    )
    # Đoạn giữ lại phải giữ nguyên vẹn mốc 0.7s không bị cắt cụt đầu câu
    assert result["keep_intervals"] == [(0.7, 3.3)]

def test_bad_take_detector_sequential_steps_not_bad_takes():
    # Các câu liệt kê như "Bước 1...", "Bước 2..." không được bị coi là nói vấp
    subtitles = [
        {"start": 1.0, "end": 3.0, "text": "Và tiếp theo chúng ta làm bước một."},
        {"start": 4.0, "end": 6.0, "text": "Và tiếp theo chúng ta làm bước hai."}
    ]
    bad_takes = BadTakeDetector.detect_bad_takes(subtitles)
    assert len(bad_takes) == 0

@patch("src.core.ai_director.LLMSemanticSelector.select_segments")
def test_ai_director_fallback_to_heuristic(mock_select_segments):
    mock_select_segments.side_effect = LLMSelectionError("Fake error")
    
    config = AIDirectorConfig(
        mode="viral_shorts",
        target_duration_seconds=60.0,
        api_key="fake-key"
    )
    director = AIDirector(config)
    subtitles = [
        {"start": 1.0, "end": 2.5, "text": "Câu 1"},
        {"start": 3.0, "end": 7.0, "text": "Câu 2"}
    ]
    
    proposed = director.generate_proposed_segments(subtitles)
    
    # Verify fallback to heuristic logic without crashing
    assert len(proposed) == 2
    assert getattr(director, "last_selection_method", "") == "heuristic"
    
    result = director.apply_approved_segments(
        subtitles=subtitles,
        proposed_segments=proposed,
        silence_keep_intervals=[],
        total_duration=10.0
    )
    assert result["stats"]["selection_method"] == "heuristic"
