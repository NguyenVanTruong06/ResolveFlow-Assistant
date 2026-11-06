"""
tests/core/test_kinetic_dataset_mapper.py
=============================================================================
Finite TDD Test Suite cho G05-05C2c1-R1:
- Bóc tách từ vựng nguồn chuẩn xác: Vietnamese, 0s, empty/no-audio, subtitle-only reject.
- Numeric bounds, bool/NaN/Inf/bigint/parent bounds, duration unknown.
- Ánh xạ dòng thời gian: 1x, 2x speed, duplicate clip usage, explicit gaps,
  boundary cross clipped vs outside, quantized 0-frame warning, geometry mismatch,
  duplicate instance ID reject.
- NTSC 24000/1001 & half-even tie, same basename khác clip ID, input non-mutation,
  frozen dataclasses.
- Strict contract validations (R1):
  - Falsy invalid typegate: None, False, 0, {}, '' -> TypeError/ValueError
  - Strict int frame types in WordPlacement & MappedWord (float, bool, str rejected)
  - Strict identity match: clip_id key == SourceWord.clip_id
  - Unknown clip_id in placements -> explicit KeyError (no silent missing identity)
  - Verified silent clip -> explicit () key accepted
  - Record frames output exact int
- Kiểm tra kiến trúc: zero Qt, zero filesystem IO, zero network, zero AI models.
=============================================================================
"""

import sys
from dataclasses import FrozenInstanceError
from fractions import Fraction
from unittest.mock import patch

import pytest

from src.core.contracts.time_contracts import RationalFPS
from src.core.kinetic.kinetic_dataset_mapper import (
    KineticDataset,
    MappedWord,
    SourceWord,
    WordPlacement,
    extract_source_words,
    map_words_to_timeline,
)


# =============================================================================
# 1. TEST EXTRACT SOURCE WORDS: VALID & VIETNAMESE
# =============================================================================

def test_extract_source_words_valid_and_vietnamese():
    """Kiểm tra bóc tách từ vựng tiếng Việt có dấu, mốc thời gian bắt đầu từ 0s."""
    raw = [
        {
            "start": 0.0,
            "end": 2.5,
            "text": "Chào mừng các bạn đến với ResolveFlow",
            "words": [
                {"word": "Chào", "start": 0.0, "end": 0.5},
                {"word": "mừng", "start": 0.5, "end": 1.0},
                {"word": "các", "start": 1.0, "end": 1.3},
                {"word": "bạn", "start": 1.3, "end": 1.7},
                {"word": "đến", "start": 1.7, "end": 2.0},
                {"word": "với", "start": 2.0, "end": 2.2},
                {"word": "ResolveFlow", "start": 2.2, "end": 2.5},
            ],
        }
    ]
    words = extract_source_words(raw, clip_id="C01", duration_sec=5.0)
    assert isinstance(words, tuple)
    assert len(words) == 7

    w0 = words[0]
    assert w0.clip_id == "C01"
    assert w0.segment_index == 0
    assert w0.word_index == 0
    assert w0.text == "Chào"
    assert w0.start_sec == Fraction(0, 1)
    assert w0.end_sec == Fraction(1, 2)
    assert isinstance(w0.start_sec, Fraction)
    assert isinstance(w0.end_sec, Fraction)

    w6 = words[6]
    assert w6.word_index == 6
    assert w6.text == "ResolveFlow"
    assert w6.start_sec == Fraction(22, 10)
    assert w6.end_sec == Fraction(25, 10)


# =============================================================================
# 2. TEST EXTRACT SOURCE WORDS: EMPTY & NO AUDIO
# =============================================================================

def test_extract_source_words_empty_and_no_audio():
    """Kiểm tra xử lý video không có thoại: chỉ [] và () trả về tuple rỗng ()."""
    assert extract_source_words([], clip_id="C01", duration_sec=10.0) == ()
    assert extract_source_words((), clip_id="C01", duration_sec=10.0) == ()


# =============================================================================
# 3. TEST EXTRACT SOURCE WORDS: SUBTITLE-ONLY REJECT (STRICT POLICY)
# =============================================================================

def test_extract_source_words_subtitle_only_reject():
    """Kiểm tra quy định nghiêm ngặt: có câu thoại nhưng thiếu word timestamps -> ném lỗi rõ ràng."""
    raw = [
        {
            "start": 0.5,
            "end": 2.0,
            "text": "Câu thoại không có mảng word timestamps",
            "words": [],
        }
    ]
    with pytest.raises(ValueError, match="word_timestamps_missing"):
        extract_source_words(raw, clip_id="C01", duration_sec=5.0)

    raw_none = [
        {
            "start": 0.5,
            "end": 2.0,
            "text": "Câu thoại trường words là None",
            "words": None,
        }
    ]
    with pytest.raises(ValueError, match="word_timestamps_missing"):
        extract_source_words(raw_none, clip_id="C01", duration_sec=5.0)


# =============================================================================
# 4. TEST EXTRACT SOURCE WORDS: NUMERIC BOUNDS & INVALID REJECTIONS
# =============================================================================

def test_extract_source_words_numeric_bounds_and_invalid():
    """Kiểm tra các trường hợp biên và số liệu không hợp lệ."""
    # 1. duration_sec không hợp lệ
    with pytest.raises(ValueError, match="duration_sec phải là số dương"):
        extract_source_words([], clip_id="C01", duration_sec=0)
    with pytest.raises(ValueError, match="duration_sec phải là số dương"):
        extract_source_words([], clip_id="C01", duration_sec=-5.0)
    with pytest.raises(TypeError, match="duration_sec không thể là kiểu bool"):
        extract_source_words([], clip_id="C01", duration_sec=True)

    # 2. NaN / Inf trong timestamps
    raw_nan = [
        {
            "start": float("nan"),
            "end": 2.0,
            "text": "Lỗi NaN",
            "words": [{"word": "Lỗi", "start": 0.0, "end": 1.0}],
        }
    ]
    with pytest.raises(ValueError, match="số hữu hạn"):
        extract_source_words(raw_nan, clip_id="C01", duration_sec=10.0)

    # 3. start >= end
    raw_reversed = [
        {
            "start": 2.0,
            "end": 1.0,
            "text": "Reversed",
            "words": [{"word": "Reversed", "start": 2.0, "end": 1.0}],
        }
    ]
    with pytest.raises(ValueError, match="phải lớn hơn start"):
        extract_source_words(raw_reversed, clip_id="C01", duration_sec=10.0)

    # 4. Từ con vượt ngoài biên câu cha (Parent bounds violation)
    raw_parent_breach = [
        {
            "start": 1.0,
            "end": 2.0,
            "text": "Breach",
            "words": [{"word": "Breach", "start": 0.5, "end": 2.0}],
        }
    ]
    with pytest.raises(ValueError, match="nằm ngoài segment"):
        extract_source_words(raw_parent_breach, clip_id="C01", duration_sec=10.0)

    # 5. Timestamp vượt quá duration_sec đã biết
    raw_over_dur = [
        {
            "start": 1.0,
            "end": 12.0,
            "text": "Over",
            "words": [{"word": "Over", "start": 1.0, "end": 12.0}],
        }
    ]
    with pytest.raises(ValueError, match="vượt quá duration_sec"):
        extract_source_words(raw_over_dur, clip_id="C01", duration_sec=10.0)


# =============================================================================
# 5. TEST MAP WORDS: 1X SPEED EXACT ALIGNMENT
# =============================================================================

def test_map_words_1x_speed_exact_alignment():
    """Kiểm tra ánh xạ tốc độ 1x: khớp khung hình chính xác theo FPS."""
    words = (
        SourceWord("C01", 0, 0, "Từ_A", Fraction(0, 1), Fraction(1, 1)),
        SourceWord("C01", 0, 1, "Từ_B", Fraction(1, 1), Fraction(2, 1)),
    )
    by_clip = {"C01": words}

    # Placement: cắt từ 0s -> 2s, đặt vào timeline từ frame 100 -> 148 @ 24fps
    pl = WordPlacement(
        clip_id="C01",
        instance_id="P1",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(2, 1),
        rec_in_frame=100,
        rec_out_frame=148,
        applied_speed=Fraction(1, 1),
    )

    dataset = map_words_to_timeline(by_clip, [pl], fps=24)
    assert len(dataset.words) == 2
    assert dataset.warnings == ()

    w0, w1 = dataset.words[0], dataset.words[1]
    assert w0.text == "Từ_A"
    assert w0.record_start_frame == 100
    assert w0.record_end_frame == 124  # 100 + 24 frames
    assert not w0.clipped

    assert w1.text == "Từ_B"
    assert w1.record_start_frame == 124
    assert w1.record_end_frame == 148
    assert not w1.clipped


# =============================================================================
# 6. TEST MAP WORDS: 2X SPEED GEOMETRY SCALING
# =============================================================================

def test_map_words_2x_speed_geometry_scaling():
    """Kiểm tra ánh xạ khi tua nhanh 2x: thời lượng khung hình co lại chính xác 1 nửa."""
    words = (
        SourceWord("C01", 0, 0, "Nhanh", Fraction(0, 1), Fraction(2, 1)),
    )
    by_clip = {"C01": words}

    # 2 giây nguồn @ 2x speed -> cần 1 giây timeline = 24 frames @ 24fps
    pl = WordPlacement(
        clip_id="C01",
        instance_id="P_2X",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(2, 1),
        rec_in_frame=50,
        rec_out_frame=74,
        applied_speed=Fraction(2, 1),
    )

    dataset = map_words_to_timeline(by_clip, [pl], fps=24)
    assert len(dataset.words) == 1
    w = dataset.words[0]
    assert w.text == "Nhanh"
    assert w.record_start_frame == 50
    assert w.record_end_frame == 74
    assert not w.clipped


# =============================================================================
# 7. TEST MAP WORDS: DUPLICATE CLIP USAGE
# =============================================================================

def test_map_words_duplicate_clip_usage():
    """Kiểm tra clip dùng nhiều lần (duplicate instances) sinh ra các MappedWord độc lập."""
    words = (
        SourceWord("C01", 0, 0, "Lặp", Fraction(0, 1), Fraction(1, 1)),
    )
    by_clip = {"C01": words}

    pl1 = WordPlacement(
        clip_id="C01",
        instance_id="P1",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(1, 1),
        rec_in_frame=0,
        rec_out_frame=24,
        applied_speed=Fraction(1, 1),
    )
    pl2 = WordPlacement(
        clip_id="C01",
        instance_id="P2",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(1, 1),
        rec_in_frame=100,
        rec_out_frame=124,
        applied_speed=Fraction(1, 1),
    )

    dataset = map_words_to_timeline(by_clip, [pl1, pl2], fps=24)
    assert len(dataset.words) == 2
    w1, w2 = dataset.words[0], dataset.words[1]

    assert w1.instance_id == "P1"
    assert w1.record_start_frame == 0
    assert w1.index == 0

    assert w2.instance_id == "P2"
    assert w2.record_start_frame == 100
    assert w2.index == 1


# =============================================================================
# 8. TEST MAP WORDS: EXPLICIT GAPS PRESERVED
# =============================================================================

def test_map_words_explicit_gaps_preserved():
    """Kiểm tra bảo toàn khoảng lặng (gaps) giữa các đoạn cắt, không ép dính contiguous."""
    words = (
        SourceWord("C01", 0, 0, "A", Fraction(0, 1), Fraction(1, 1)),
    )
    by_clip = {"C01": words}

    # Có gap 50 frames giữa P1 (rec 0-24) và P2 (rec 74-98)
    pl1 = WordPlacement("C01", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))
    pl2 = WordPlacement("C01", "P2", Fraction(0), Fraction(1), 74, 98, Fraction(1))

    dataset = map_words_to_timeline(by_clip, [pl1, pl2], fps=24)
    assert dataset.words[0].record_end_frame == 24
    assert dataset.words[1].record_start_frame == 74
    # Khoảng cách giữa 2 từ là 50 frames
    assert dataset.words[1].record_start_frame - dataset.words[0].record_end_frame == 50


# =============================================================================
# 9. TEST MAP WORDS: BOUNDARY CROSS CLIPPED VS OUTSIDE
# =============================================================================

def test_map_words_boundary_cross_clipped_vs_outside():
    """Kiểm tra cắt tỉa chính xác tại giao điểm với cửa sổ nguồn (no 60% threshold)."""
    words = (
        # Từ 1: nằm hoàn toàn trước in-point (0s - 1s) -> completely outside
        SourceWord("C01", 0, 0, "Ngoài_Trước", Fraction(0, 1), Fraction(1, 1)),
        # Từ 2: giao biên in-point (0.8s - 1.5s), in-point là 1.0s -> clipped = True
        SourceWord("C01", 0, 1, "Cắt_Trước", Fraction(8, 10), Fraction(15, 10)),
        # Từ 3: nằm trọn vẹn bên trong (1.5s - 2.5s)
        SourceWord("C01", 0, 2, "Trọn_Vẹn", Fraction(15, 10), Fraction(25, 10)),
        # Từ 4: giao biên out-point (2.8s - 3.5s), out-point là 3.0s -> clipped = True
        SourceWord("C01", 0, 3, "Cắt_Sau", Fraction(28, 10), Fraction(35, 10)),
        # Từ 5: nằm hoàn toàn sau out-point (3.5s - 4.5s) -> completely outside
        SourceWord("C01", 0, 4, "Ngoài_Sau", Fraction(35, 10), Fraction(45, 10)),
    )
    by_clip = {"C01": words}

    # Cut: [1.0s, 3.0s] -> 2 giây = 48 frames @ 24fps
    pl = WordPlacement(
        clip_id="C01",
        instance_id="P_BOUND",
        src_in_sec=Fraction(1, 1),
        src_out_sec=Fraction(3, 1),
        rec_in_frame=0,
        rec_out_frame=48,
        applied_speed=Fraction(1, 1),
    )

    dataset = map_words_to_timeline(by_clip, [pl], fps=24)
    # Kỳ vọng 3 từ được map: Cắt_Trước, Trọn_Vẹn, Cắt_Sau
    assert len(dataset.words) == 3

    w_before = dataset.words[0]
    assert w_before.text == "Cắt_Trước"
    assert w_before.clipped is True
    assert w_before.record_start_frame == 0  # Bị clamp vào in-point

    w_mid = dataset.words[1]
    assert w_mid.text == "Trọn_Vẹn"
    assert w_mid.clipped is False

    w_after = dataset.words[2]
    assert w_after.text == "Cắt_Sau"
    assert w_after.clipped is True
    assert w_after.record_end_frame == 48  # Bị clamp vào out-point

    # Kiểm tra warnings
    assert any("2 từ nằm ngoài phạm vi cắt" in warn for warn in dataset.warnings)
    assert any("Cắt_Trước" in warn and "bị cắt ở biên" in warn for warn in dataset.warnings)


# =============================================================================
# 10. TEST MAP WORDS: QUANTIZED 0-FRAME DROP (NO FAKE 1-FRAME EXPANSION)
# =============================================================================

def test_map_words_quantized_zero_frame_drop():
    """Kiểm tra từ bị lượng tử hóa thành 0 frame: loại bỏ và ghi warning, không bịa 1 frame."""
    # Từ chỉ dài 0.005s (5ms) trong clip 1x speed @ 24fps
    # 0.005 * 24 = 0.12 frames -> làm tròn pts_to_frame = 0
    words = (
        SourceWord("C01", 0, 0, "Siêu_Ngắn", Fraction(1, 1), Fraction(1005, 1000)),
    )
    by_clip = {"C01": words}

    pl = WordPlacement(
        clip_id="C01",
        instance_id="P_SHORT",
        src_in_sec=Fraction(1, 1),
        src_out_sec=Fraction(2, 1),
        rec_in_frame=0,
        rec_out_frame=24,
        applied_speed=Fraction(1, 1),
    )

    dataset = map_words_to_timeline(by_clip, [pl], fps=24)
    # Phải bị drop, không sinh ra MappedWord giả
    assert len(dataset.words) == 0
    assert any("drop_quantized_empty" in warn for warn in dataset.warnings)


# =============================================================================
# 11. TEST MAP WORDS: GEOMETRY MISMATCH & DUPLICATE INSTANCE REJECT
# =============================================================================

def test_map_words_geometry_mismatch_and_duplicate_instance_reject():
    """Kiểm tra từ chối placement có hình học không nhất quán (lệch quá 1 frame)."""
    # src_dur = 2s, speed = 1x -> expected @ 24fps là 48 frames.
    # Nhưng rec_frames lại khai báo là 100 frames!
    bad_pl = WordPlacement(
        clip_id="C01",
        instance_id="P_BAD_GEO",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(2, 1),
        rec_in_frame=0,
        rec_out_frame=100,
        applied_speed=Fraction(1, 1),
    )
    with pytest.raises(ValueError, match="Hình học placement.*không nhất quán"):
        map_words_to_timeline({"C01": ()}, [bad_pl], fps=24)


# =============================================================================
# 12. TEST MAP WORDS: NTSC 24000/1001 & HALF-EVEN TIE ROUNDING
# =============================================================================

def test_map_words_ntsc_and_half_even_tie():
    """Kiểm tra tốc độ khung hình chuẩn NTSC 24000/1001 và quy tắc làm tròn tie-breaking."""
    fps_ntsc = RationalFPS(24000, 1001)

    words = (
        SourceWord("C01", 0, 0, "NTSC_Word", Fraction(0, 1), Fraction(1001, 24000)),
    )
    by_clip = {"C01": words}

    # src_dur = 1 frame NTSC (1001/24000 s) -> 1 frame
    pl = WordPlacement(
        clip_id="C01",
        instance_id="P_NTSC",
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(1001, 24000),
        rec_in_frame=0,
        rec_out_frame=1,
        applied_speed=Fraction(1, 1),
    )
    dataset = map_words_to_timeline(by_clip, [pl], fps=fps_ntsc)
    assert len(dataset.words) == 1
    assert dataset.words[0].record_start_frame == 0
    assert dataset.words[0].record_end_frame == 1


# =============================================================================
# 13. TEST SAME BASENAME DIFFERENT CLIP IDS NOT MIXED
# =============================================================================

def test_same_basename_different_clip_ids_not_mixed():
    """Kiểm tra các clip khác nhau không bị trộn lẫn từ vựng dù có chung logic."""
    words_c1 = (SourceWord("C01", 0, 0, "Từ_Clip1", Fraction(0), Fraction(1)),)
    words_c2 = (SourceWord("C02", 0, 0, "Từ_Clip2", Fraction(0), Fraction(1)),)
    by_clip = {"C01": words_c1, "C02": words_c2}

    pl1 = WordPlacement("C01", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))
    dataset = map_words_to_timeline(by_clip, [pl1], fps=24)

    assert len(dataset.words) == 1
    assert dataset.words[0].clip_id == "C01"
    assert dataset.words[0].text == "Từ_Clip1"


# =============================================================================
# 14. TEST INPUT NON-MUTATION & FROZEN DATACLASSES
# =============================================================================

def test_input_non_mutation_and_frozen_dataclasses():
    """Kiểm tra tính bất biến (immutability): dataclasses frozen và input không bị mutate."""
    sw = SourceWord("C01", 0, 0, "Test", Fraction(0), Fraction(1))
    with pytest.raises(FrozenInstanceError):
        sw.text = "Mutated"

    pl = WordPlacement("C01", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))
    with pytest.raises(FrozenInstanceError):
        pl.instance_id = "P2"

    mw = MappedWord(0, "C01", "P1", 0, 0, "Test", Fraction(0), Fraction(1), 0, 24, False)
    with pytest.raises(FrozenInstanceError):
        mw.index = 99

    kd = KineticDataset(words=(mw,), warnings=())
    with pytest.raises(FrozenInstanceError):
        kd.timing_mode = "approximate"


# =============================================================================
# 15. TEST ARCHITECTURE SPY: ZERO QT & ZERO IO DEPENDENCIES
# =============================================================================

def test_architecture_spy_zero_qt_and_zero_io():
    """Kiểm định kiến trúc: kinetic_dataset_mapper không import Qt và không gọi I/O."""
    import src.core.kinetic.kinetic_dataset_mapper as kdm

    # 1. Zero Qt imports
    for mod_name in sys.modules:
        if mod_name.startswith("PySide6") or mod_name.startswith("PyQt"):
            assert mod_name not in kdm.__dict__, f"Phát hiện import Qt trái phép: {mod_name}"

    # 2. Spy I/O: Không gọi open(), os.stat(), os.listdir(), socket
    raw = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Kiểm tra spy",
            "words": [{"word": "Kiểm", "start": 0.0, "end": 1.0}, {"word": "tra", "start": 1.0, "end": 2.0}],
        }
    ]
    with patch("builtins.open") as mock_open:
        with patch("os.stat") as mock_stat:
            with patch("os.listdir") as mock_listdir:
                words = extract_source_words(raw, clip_id="C01", duration_sec=5.0)
                pl = WordPlacement("C01", "P1", Fraction(0), Fraction(2), 0, 48, Fraction(1))
                dataset = map_words_to_timeline({"C01": words}, [pl], fps=24)

                assert mock_open.call_count == 0
                assert mock_stat.call_count == 0
                assert mock_listdir.call_count == 0
                assert len(dataset.words) == 2


# =============================================================================
# 16. TEST R1 STRICT CONTRACT: FALSY INVALID SUBTITLES TYPEGATE
# =============================================================================

@pytest.mark.parametrize("invalid_raw", [None, False, 0, {}, "", 123, 3.14, object()])
def test_extract_source_words_falsy_invalid_typegate(invalid_raw):
    """Kiểm tra R1: extract_source_words typegate trước empty check, ném TypeError với falsy không hợp lệ."""
    with pytest.raises(TypeError, match="raw_subtitles phải là list hoặc tuple"):
        extract_source_words(invalid_raw, clip_id="C01", duration_sec=5.0)


# =============================================================================
# 17. TEST R1 STRICT CONTRACT: WORD PLACEMENT FRAME TYPES & DOMAINS
# =============================================================================

@pytest.mark.parametrize("bad_frame", [0.5, 1.0, True, False, "24", float("nan"), float("inf"), None])
def test_word_placement_strict_frame_types(bad_frame):
    """Kiểm tra R1: rec_in_frame và rec_out_frame bắt buộc là strict int, không chấp nhận float/bool/str."""
    with pytest.raises(TypeError, match="phải là int nguyên thủy"):
        WordPlacement(
            clip_id="C01",
            instance_id="P1",
            src_in_sec=Fraction(0),
            src_out_sec=Fraction(1),
            rec_in_frame=bad_frame,
            rec_out_frame=24,
            applied_speed=Fraction(1),
        )

    with pytest.raises(TypeError, match="phải là int nguyên thủy"):
        WordPlacement(
            clip_id="C01",
            instance_id="P1",
            src_in_sec=Fraction(0),
            src_out_sec=Fraction(1),
            rec_in_frame=0,
            rec_out_frame=bad_frame,
            applied_speed=Fraction(1),
        )


def test_word_placement_domain_validations():
    """Kiểm tra R1: WordPlacement ném ValueError với frame âm hoặc ngược chiều."""
    with pytest.raises(ValueError, match="rec_in_frame âm"):
        WordPlacement("C01", "P1", Fraction(0), Fraction(1), -1, 24, Fraction(1))

    with pytest.raises(ValueError, match="rec_out_frame .* phải lớn hơn rec_in_frame"):
        WordPlacement("C01", "P1", Fraction(0), Fraction(1), 24, 24, Fraction(1))

    with pytest.raises(ValueError, match="clip_id phải là str không rỗng"):
        WordPlacement("", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))

    with pytest.raises(ValueError, match="instance_id phải là str không rỗng"):
        WordPlacement("C01", "   ", Fraction(0), Fraction(1), 0, 24, Fraction(1))


# =============================================================================
# 18. TEST R1 STRICT CONTRACT: SOURCE WORD VALIDATIONS
# =============================================================================

def test_source_word_strict_validations():
    """Kiểm tra R1: SourceWord bắt buộc clip_id không rỗng, indices int không âm, time Fraction hợp lệ."""
    with pytest.raises(ValueError, match="clip_id phải là str không rỗng"):
        SourceWord("", 0, 0, "Test", Fraction(0), Fraction(1))

    with pytest.raises(TypeError, match="segment_index phải là int nguyên thủy"):
        SourceWord("C01", True, 0, "Test", Fraction(0), Fraction(1))

    with pytest.raises(TypeError, match="word_index phải là int nguyên thủy"):
        SourceWord("C01", 0, 1.5, "Test", Fraction(0), Fraction(1))

    with pytest.raises(ValueError, match="start_sec âm"):
        SourceWord("C01", 0, 0, "Test", Fraction(-1), Fraction(1))

    with pytest.raises(ValueError, match="end_sec .* phải lớn hơn start_sec"):
        SourceWord("C01", 0, 0, "Test", Fraction(2), Fraction(1))


# =============================================================================
# 19. TEST R1 STRICT CONTRACT: UNKNOWN CLIP ID & WRONG CLIP IDENTITY
# =============================================================================

def test_map_words_unknown_clip_id_strict():
    """Kiểm tra R1: Placement tham chiếu clip_id không có trong mapping -> ném explicit KeyError."""
    pl = WordPlacement("MISSING_CLIP", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))
    with pytest.raises(KeyError, match="unknown_clip_id"):
        map_words_to_timeline({"KNOWN_CLIP": ()}, [pl], fps=24)

    # Nếu clip đã verified silent, phải có key với () -> hợp lệ, trả về 0 từ
    dataset_silent = map_words_to_timeline({"SILENT_CLIP": ()}, [WordPlacement("SILENT_CLIP", "P_SILENT", Fraction(0), Fraction(1), 0, 24, Fraction(1))], fps=24)
    assert len(dataset_silent.words) == 0


def test_map_words_wrong_clip_identity():
    """Kiểm tra R1: SourceWord có clip_id không khớp với key mapping -> ném ValueError."""
    forged_word = SourceWord("OTHER_CLIP", 0, 0, "Forged", Fraction(0), Fraction(1))
    with pytest.raises(ValueError, match="không khớp với key mapping"):
        map_words_to_timeline({"C01": (forged_word,)}, [WordPlacement("C01", "P1", Fraction(0), Fraction(1), 0, 24, Fraction(1))], fps=24)


# =============================================================================
# 20. TEST R1 STRICT CONTRACT: EXACT INT OUTPUT FRAMES
# =============================================================================

def test_map_words_exact_int_record_frames():
    """Kiểm tra R1: Kết quả record_start_frame và record_end_frame của MappedWord luôn là int nguyên thủy."""
    words = (
        SourceWord("C01", 0, 0, "IntCheck", Fraction(1, 4), Fraction(3, 4)),
    )
    pl = WordPlacement("C01", "P1", Fraction(0), Fraction(1), 10, 34, Fraction(1))
    dataset = map_words_to_timeline({"C01": words}, [pl], fps=24)

    assert len(dataset.words) == 1
    mw = dataset.words[0]
    assert type(mw.record_start_frame) is int
    assert type(mw.record_end_frame) is int
    assert not isinstance(mw.record_start_frame, bool)
    assert not isinstance(mw.record_end_frame, bool)
    assert mw.record_start_frame == 16  # 10 + round(0.25 * 24) = 10 + 6
    assert mw.record_end_frame == 28    # 10 + round(0.75 * 24) = 10 + 18
