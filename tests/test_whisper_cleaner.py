"""
Unit tests for Whisper Cleaner & Hallucination Filter (V2)
"""

import pytest
from src.core.whisper_cleaner import (
    is_whisper_hallucination,
    clean_whisper_segments,
    clean_whisper_segments_with_stats,
    format_filter_summary,
    has_foreign_script,
    is_noise_chatter,
    save_filter_report
)


def test_is_whisper_hallucination_detects_common_artifacts():
    assert is_whisper_hallucination("Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn")
    assert is_whisper_hallucination("bây giờ hãy subscribe cho kênh Ghiền Mì Gõ")
    assert is_whisper_hallucination("người đã quay lại video này, hãy subscribe cho kênh La La School")
    assert is_whisper_hallucination("Bye")
    assert is_whisper_hallucination("Bye-bye....")
    assert is_whisper_hallucination("Tạm biệt")
    assert is_whisper_hallucination("Cảm ơn các bạn đã xem video")
    assert is_whisper_hallucination("Phụ đề thực hiện bởi ChunDVC")
    assert is_whisper_hallucination("...")
    assert is_whisper_hallucination("   ")


def test_is_whisper_hallucination_preserves_legitimate_speech():
    assert not is_whisper_hallucination("Hello bạn nha")
    assert not is_whisper_hallucination("Một ngày ở đồng Nai")
    assert not is_whisper_hallucination("Tình hình là trời mưa rồi nhá các bạn")
    assert not is_whisper_hallucination("Đạp xe chúi chúi trong rừng chè mưa này")
    assert not is_whisper_hallucination("Cái xe của nó bị gì vậy?")
    assert not is_whisper_hallucination("Cảm ơn anh nha, đường này đẹp quá")


def test_rule_1_zero_duration():
    raw = [
        {"start": 1.0, "end": 1.02, "text": "Đoạn quá ngắn"},
        {"start": 2.0, "end": 2.5, "text": "Đoạn hợp lệ"}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw)
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Đoạn hợp lệ"
    assert stats["zero_duration"] == 1


def test_rule_2_foreign_script_scoped_to_vietnamese():
    # Khi language='vi', ký tự Hoa/Hàn/Nga bị loại
    raw = [
        {"start": 0.0, "end": 2.0, "text": "Đây là tiếng Việt"},
        {"start": 2.0, "end": 4.0, "text": "k飼 th工"},
        {"start": 4.0, "end": 6.0, "text": "Привет мир váš"}
    ]
    cleaned_vi, stats_vi, _ = clean_whisper_segments_with_stats(raw, language="vi")
    assert len(cleaned_vi) == 1
    assert cleaned_vi[0]["text"] == "Đây là tiếng Việt"
    assert stats_vi["foreign_script"] == 2

    # Khi language='zh' hoặc 'ru', không bị loại bởi foreign_script
    cleaned_zh, stats_zh, _ = clean_whisper_segments_with_stats(raw, language="zh")
    assert stats_zh["foreign_script"] == 0


def test_rule_3_no_speech_prob_and_avg_logprob():
    raw = [
        {"start": 0.0, "end": 2.0, "text": "Ảo giác âm thanh", "no_speech_prob": 0.75, "avg_logprob": -1.2},
        {"start": 2.0, "end": 4.0, "text": "Tiếng nói thật", "no_speech_prob": 0.1, "avg_logprob": -0.3},
        {"start": 4.0, "end": 6.0, "text": "Tiếng nói hơi nhỏ", "no_speech_prob": 0.4, "avg_logprob": -1.1}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw)
    assert len(cleaned) == 2
    assert cleaned[0]["text"] == "Tiếng nói thật"
    assert cleaned[1]["text"] == "Tiếng nói hơi nhỏ"
    assert cleaned[1].get("low_confidence") is True
    assert stats["no_speech"] == 1


def test_rule_4_repetition_loop_compression_ratio():
    raw = [
        {"start": 0.0, "end": 5.0, "text": "lặp lặp lặp lặp lặp lặp lặp", "compression_ratio": 2.8},
        {"start": 5.0, "end": 7.0, "text": "câu bình thường", "compression_ratio": 1.2}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw)
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "câu bình thường"
    assert stats["repetition_loop"] == 1


def test_rule_5_outro_pattern_ratio():
    raw = [
        # Boilerplate chiếm đa số -> loại
        {"start": 0.0, "end": 2.0, "text": "Cảm ơn các bạn đã theo dõi"},
        {"start": 2.0, "end": 4.0, "text": "Hãy subscribe kênh nha"},
        # Lời nói tự nhiên có chứa từ cảm ơn -> giữ lại
        {"start": 4.0, "end": 7.0, "text": "Cảm ơn anh nha, con đường này dẫn lên đỉnh đèo đẹp quá"}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw)
    assert len(cleaned) == 1
    assert "con đường này" in cleaned[0]["text"]
    assert stats["outro_pattern"] == 2


def test_rule_6_repeated_sentence_in_clip():
    # 7 lần "Thật quay dữ" trải dài > 30s -> loại cả 7
    raw_repeated = [
        {"start": float(i * 10), "end": float(i * 10 + 2), "text": "Thật quay dữ quá trời luôn"}
        for i in range(7)
    ]
    raw_repeated.append({"start": 80.0, "end": 83.0, "text": "Đoạn hội thoại khác"})

    cleaned, stats, _ = clean_whisper_segments_with_stats(raw_repeated, clip_duration=90.0)
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Đoạn hội thoại khác"
    assert stats["repeated_sentence"] == 7


def test_rule_6_short_questions_preserved():
    # Từ ngắn < 6 từ lặp lại tự nhiên (như 4x 'Hả?' hay 2x 'Đúng rồi') không được loại oan
    raw = [
        {"start": 1.0, "end": 1.5, "text": "Hả?"},
        {"start": 2.0, "end": 2.5, "text": "Hả?"},
        {"start": 5.0, "end": 5.5, "text": "Hả?"},
        {"start": 10.0, "end": 10.5, "text": "Hả?"},
        {"start": 12.0, "end": 14.0, "text": "Đúng rồi"}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw, clip_duration=20.0)
    assert len(cleaned) == 5
    assert stats["repeated_sentence"] == 0


def test_rule_7_noise_chatter():
    raw = [
        {"start": 0.0, "end": 2.0, "text": "l l l l l l l"},
        {"start": 2.0, "end": 4.0, "text": "dodolololdodol"},
        {"start": 4.0, "end": 5.0, "text": "....."},
        {"start": 5.0, "end": 7.0, "text": "Hôm nay trời nắng đẹp"}
    ]
    cleaned, stats, _ = clean_whisper_segments_with_stats(raw)
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Hôm nay trời nắng đẹp"
    assert stats["noise_chatter"] == 3


def test_stats_sum_matches_dropped_count():
    raw = [
        {"start": 0.0, "end": 0.02, "text": "Quá ngắn"},                        # zero_duration
        {"start": 1.0, "end": 3.0, "text": "k飼 th工"},                         # foreign_script
        {"start": 3.0, "end": 5.0, "text": "dodololol"},                       # noise_chatter
        {"start": 5.0, "end": 7.0, "text": "Hãy subscribe kênh của mình nha"},   # outro_pattern
        {"start": 7.0, "end": 9.0, "text": "Lặp", "compression_ratio": 2.9},   # repetition_loop
        {"start": 9.0, "end": 11.0, "text": "Khoảng lặng", "no_speech_prob": 0.8, "avg_logprob": -1.5}, # no_speech
        {"start": 11.0, "end": 14.0, "text": "Lời thoại thật rất tốt"}
    ]
    cleaned, stats, examples = clean_whisper_segments_with_stats(raw, language="vi")
    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Lời thoại thật rất tốt"
    total_dropped = len(raw) - len(cleaned)
    assert sum(stats.values()) == total_dropped == 6


def test_format_filter_summary():
    stats = {
        "zero_duration": 1,
        "foreign_script": 2,
        "no_speech": 0,
        "repetition_loop": 1,
        "outro_pattern": 3,
        "repeated_sentence": 0,
        "noise_chatter": 0
    }
    summary = format_filter_summary(7, stats)
    assert "Đã lọc 7 câu nhiễu" in summary
    assert "3 outro/subscribe" in summary
    assert "2 ký tự lạ" in summary
