"""
tests/core/contracts/test_time_contracts.py
Kiểm thử đơn vị cho Hợp đồng Thời gian Thuần Python (G01-01B1-T-I).
Bao gồm:
1. RationalFPS: Chuẩn hóa phân số, NTSC aliases, explicit Fractions, bảo vệ kiểu, lỗi intstringlimit.
2. pts_to_frame: Round-half-to-even chính xác bằng số học phân số, signed coordinates, NTSC long duration, source PTS 4.666667.
3. frame_to_pts: Roundtrip chính xác dạng Fraction, không float drift.
4. TimeRangeFrames: Khoảng nửa mở [start, end), duration, contains, adjacency overlap=False, immutability.
5. Characterization: Đối chiếu hành vi giữa RationalFPS và TimelineMapper._parse_fps_fraction hiện hữu.
"""
from dataclasses import FrozenInstanceError
from fractions import Fraction
import math
import pytest

from src.core.contracts.time_contracts import (
    RationalFPS,
    pts_to_frame,
    frame_to_pts,
    TimeRangeFrames,
)
from src.core.timeline_mapper import TimelineMapper


# ==============================================================================
# 1. RATIONAL FPS: KHỞI TẠO, TỐI GIẢN & PARSING TỪ NHIỀU KIỂU
# ==============================================================================

def test_rational_fps_direct_construction_and_normalization():
    # Khởi tạo trực tiếp tối giản
    fps = RationalFPS(24, 1)
    assert fps.numerator == 24
    assert fps.denominator == 1
    assert fps.as_fraction() == Fraction(24, 1)
    assert fps.as_float() == 24.0

    # Tự động rút gọn phân số (gcd)
    fps_reduced = RationalFPS(48000, 2002)
    assert fps_reduced.numerator == 24000
    assert fps_reduced.denominator == 1001

    # Bất biến (Frozen)
    with pytest.raises(FrozenInstanceError):
        fps.numerator = 30  # type: ignore


def test_rational_fps_from_integer():
    for val in (24, 25, 30, 50, 60, 120):
        fps = RationalFPS.from_value(val)
        assert fps.numerator == val
        assert fps.denominator == 1
        assert fps.as_fraction() == Fraction(val, 1)


def test_rational_fps_from_explicit_fraction_preserves_exact_ratio():
    # Codex Rule: Fraction truyền tường minh phải giữ đúng tỷ lệ, không tự map 2997/100 sang 30000/1001
    frac = Fraction(2997, 100)
    fps = RationalFPS.from_value(frac)
    assert fps.numerator == 2997
    assert fps.denominator == 100
    assert fps.as_fraction() == frac

    # Chuỗi phân số tường minh "2997/100" cũng phải giữ nguyên
    fps_str = RationalFPS.from_value("2997/100")
    assert fps_str.numerator == 2997
    assert fps_str.denominator == 100

    # Phân số NTSC tường minh
    fps_ntsc = RationalFPS.from_value(Fraction(24000, 1001))
    assert fps_ntsc.numerator == 24000
    assert fps_ntsc.denominator == 1001


def test_rational_fps_from_metadata_float_and_decimal_string_aliases():
    # Float aliases metadata camera phổ biến
    fps_23 = RationalFPS.from_value(23.976)
    assert fps_23.numerator == 24000
    assert fps_23.denominator == 1001

    fps_23_98 = RationalFPS.from_value(23.98)
    assert fps_23_98.numerator == 24000
    assert fps_23_98.denominator == 1001

    fps_29 = RationalFPS.from_value(29.97)
    assert fps_29.numerator == 30000
    assert fps_29.denominator == 1001

    fps_59 = RationalFPS.from_value(59.94)
    assert fps_59.numerator == 60000
    assert fps_59.denominator == 1001

    # Chuỗi số thập phân metadata
    assert RationalFPS.from_value("23.976") == RationalFPS(24000, 1001)
    assert RationalFPS.from_value("29.97") == RationalFPS(30000, 1001)
    assert RationalFPS.from_value("59.94") == RationalFPS(60000, 1001)
    assert RationalFPS.from_value("24000/1001") == RationalFPS(24000, 1001)

    # Float gần giá trị thực của 24000/1001 (~23.976023976)
    fps_true_float = RationalFPS.from_value(24000 / 1001)
    assert fps_true_float.numerator == 24000
    assert fps_true_float.denominator == 1001


def test_rational_fps_from_other_finite_rates():
    # 12.5 fps -> 25/2
    fps_12_5 = RationalFPS.from_value(12.5)
    assert fps_12_5.numerator == 25
    assert fps_12_5.denominator == 2

    # 48.0 fps -> 48/1
    fps_48 = RationalFPS.from_value(48.0)
    assert fps_48.numerator == 48
    assert fps_48.denominator == 1


def test_rational_fps_rejects_invalid_values_strictly():
    # Cấm bool
    with pytest.raises(ValueError, match="bool"):
        RationalFPS.from_value(True)
    with pytest.raises(ValueError, match="bool"):
        RationalFPS.from_value(False)

    # Cấm None
    with pytest.raises(ValueError, match="None"):
        RationalFPS.from_value(None)

    # Cấm số <= 0
    with pytest.raises(ValueError):
        RationalFPS.from_value(0)
    with pytest.raises(ValueError):
        RationalFPS.from_value(-24)
    with pytest.raises(ValueError):
        RationalFPS.from_value(-29.97)

    # Cấm NaN / Inf
    with pytest.raises(ValueError):
        RationalFPS.from_value(float("nan"))
    with pytest.raises(ValueError):
        RationalFPS.from_value(float("inf"))
    with pytest.raises(ValueError):
        RationalFPS.from_value(float("-inf"))

    # Cấm chuỗi không hợp lệ
    with pytest.raises(ValueError):
        RationalFPS.from_value("")
    with pytest.raises(ValueError):
        RationalFPS.from_value("   ")
    with pytest.raises(ValueError):
        RationalFPS.from_value("invalid_fps")
    with pytest.raises(ValueError):
        RationalFPS.from_value("24/0")  # Mẫu số 0
    with pytest.raises(ValueError):
        RationalFPS.from_value("0/24")  # Tử số 0
    with pytest.raises(ValueError):
        RationalFPS.from_value("-24/1")

    # Số nguyên chuỗi cực lớn không ném lỗi ValueError int string limit
    huge_str = "1" + "0" * 5000
    with pytest.raises(ValueError):
        RationalFPS.from_value(huge_str)


# ==============================================================================
# 2. PTS_TO_FRAME: ROUND-HALF-TO-EVEN CHÍNH XÁC & KHÔNG GIAN TỌA ĐỘ
# ==============================================================================

def test_pts_to_frame_half_even_ties_positive_and_negative():
    fps_1 = RationalFPS(1, 1)

    # Mốc dương: round half to even
    # 0.5 -> 0 (0 chẵn)
    assert pts_to_frame(0.5, fps_1) == 0
    assert pts_to_frame(Fraction(1, 2), fps_1) == 0

    # 1.5 -> 2 (2 chẵn)
    assert pts_to_frame(1.5, fps_1) == 2
    assert pts_to_frame(Fraction(3, 2), fps_1) == 2

    # 2.5 -> 2 (2 chẵn)
    assert pts_to_frame(2.5, fps_1) == 2
    assert pts_to_frame(Fraction(5, 2), fps_1) == 2

    # 3.5 -> 4 (4 chẵn)
    assert pts_to_frame(3.5, fps_1) == 4
    assert pts_to_frame(Fraction(7, 2), fps_1) == 4

    # Mốc âm: đối xứng hoàn hảo
    # -0.5 -> 0 (0 chẵn)
    assert pts_to_frame(-0.5, fps_1) == 0
    assert pts_to_frame(Fraction(-1, 2), fps_1) == 0

    # -1.5 -> -2 (-2 chẵn)
    assert pts_to_frame(-1.5, fps_1) == -2
    assert pts_to_frame(Fraction(-3, 2), fps_1) == -2

    # -2.5 -> -2 (-2 chẵn)
    assert pts_to_frame(-2.5, fps_1) == -2
    assert pts_to_frame(Fraction(-5, 2), fps_1) == -2

    # -3.5 -> -4 (-4 chẵn)
    assert pts_to_frame(-3.5, fps_1) == -4
    assert pts_to_frame(Fraction(-7, 2), fps_1) == -4


def test_pts_to_frame_non_tie_values():
    fps_1 = RationalFPS(1, 1)
    assert pts_to_frame(0.4, fps_1) == 0
    assert pts_to_frame(0.6, fps_1) == 1
    assert pts_to_frame(-0.4, fps_1) == 0
    assert pts_to_frame(-0.6, fps_1) == -1


def test_pts_to_frame_ntsc_exact_and_source_pts_probe():
    fps_24000_1001 = RationalFPS(24000, 1001)

    # Probe Codex: sourcePTS 4.666667s ở 24000/1001 fps
    # 4.666667 * 24000 / 1001 = 111.88811988... -> 112 frames
    frame_probe = pts_to_frame(4.666667, fps_24000_1001)
    assert frame_probe == 112

    # Phân số đúng 1001 giây ở 24000/1001 fps phải đúng 24000 frames nguyên
    assert pts_to_frame(1001, fps_24000_1001) == 24000

    # 1001 / 24000 giây = đúng 1 frame
    assert pts_to_frame(Fraction(1001, 24000), fps_24000_1001) == 1


def test_pts_to_frame_long_duration_ntsc_precision():
    fps_24000_1001 = RationalFPS(24000, 1001)
    # Ở mốc 3600 giây (1 giờ):
    # 3600 * 24000 / 1001 = 86400000 / 1001 = 86313.686313... -> round 86314 frames
    frame_1h = pts_to_frame(3600, fps_24000_1001)
    assert frame_1h == 86314


def test_pts_to_frame_with_timeline_start_offset():
    # Khi Timeline 24fps có startFrame = 86400:
    # Caller cộng offset: record_frame = 86400 + pts_to_frame(...)
    fps_24 = RationalFPS(24, 1)
    pts = 10.5
    offset_frames = pts_to_frame(pts, fps_24)  # 10.5 * 24 = 252 frames
    assert offset_frames == 252
    record_frame = 86400 + offset_frames
    assert record_frame == 86652


def test_pts_to_frame_input_validation_and_no_mutation():
    fps = RationalFPS(30, 1)
    with pytest.raises(ValueError, match="bool"):
        pts_to_frame(True, fps)
    with pytest.raises(ValueError):
        pts_to_frame(None, fps)
    with pytest.raises(ValueError):
        pts_to_frame(float("nan"), fps)
    with pytest.raises(ValueError):
        pts_to_frame(float("inf"), fps)

    # Input không bị biến dạng
    f = Fraction(5, 3)
    f_copy = Fraction(5, 3)
    pts_to_frame(f, fps)
    assert f == f_copy


# ==============================================================================
# 3. FRAME_TO_PTS: ROUNDTRIP BẢO TOÀN DẠNG FRACTION
# ==============================================================================

def test_frame_to_pts_returns_exact_fraction():
    fps_24000_1001 = RationalFPS(24000, 1001)

    # Frame 112
    pts_frac = frame_to_pts(112, fps_24000_1001)
    assert isinstance(pts_frac, Fraction)
    # pts_frac = 112 * 1001 / 24000 = 112112 / 24000 = 7007 / 1500
    assert pts_frac == Fraction(7007, 1500)

    # Roundtrip: pts_to_frame(frame_to_pts(frame)) == frame
    for f in range(-50, 50):
        recovered = pts_to_frame(frame_to_pts(f, fps_24000_1001), fps_24000_1001)
        assert recovered == f, f"Roundtrip thất bại tại frame {f}"


def test_frame_to_pts_input_validation():
    fps = RationalFPS(25, 1)
    # Frame phải là int, không phải bool
    with pytest.raises(ValueError, match="bool"):
        frame_to_pts(True, fps)
    with pytest.raises(ValueError):
        frame_to_pts(1.5, fps)  # type: ignore
    with pytest.raises(ValueError):
        frame_to_pts(None, fps)  # type: ignore


# ==============================================================================
# 4. TIME_RANGE_FRAMES: KHOẢNG NỬA MỞ [START, END)
# ==============================================================================

def test_time_range_frames_valid_construction_and_properties():
    tr = TimeRangeFrames(100, 150)
    assert tr.start_frame == 100
    assert tr.end_frame == 150
    assert tr.duration_frames == 50

    # Signed range
    tr_signed = TimeRangeFrames(-50, 0)
    assert tr_signed.start_frame == -50
    assert tr_signed.end_frame == 0
    assert tr_signed.duration_frames == 50


def test_time_range_frames_contains():
    tr = TimeRangeFrames(10, 20)
    assert not tr.contains(9)
    assert tr.contains(10)   # Chứa start_frame
    assert tr.contains(15)
    assert tr.contains(19)
    assert not tr.contains(20)  # Nửa mở: KHÔNG chứa end_frame
    assert not tr.contains(21)


def test_time_range_frames_overlaps():
    base = TimeRangeFrames(10, 20)

    # Liền kề (Adjacency): end == start -> KHÔNG chồng lấn
    adjacent_left = TimeRangeFrames(0, 10)
    adjacent_right = TimeRangeFrames(20, 30)
    assert not base.overlaps(adjacent_left)
    assert not base.overlaps(adjacent_right)
    assert not adjacent_left.overlaps(base)

    # Chồng lấn thực sự
    overlap_left = TimeRangeFrames(5, 15)
    overlap_right = TimeRangeFrames(15, 25)
    contained = TimeRangeFrames(12, 18)
    surrounding = TimeRangeFrames(5, 25)
    assert base.overlaps(overlap_left)
    assert base.overlaps(overlap_right)
    assert base.overlaps(contained)
    assert base.overlaps(surrounding)

    # Tách rời (Disjoint)
    disjoint_far = TimeRangeFrames(30, 40)
    assert not base.overlaps(disjoint_far)


def test_time_range_frames_validation_and_immutability():
    # start >= end là lỗi
    with pytest.raises(ValueError):
        TimeRangeFrames(20, 20)  # Empty range
    with pytest.raises(ValueError):
        TimeRangeFrames(20, 10)  # Inverted range

    # Cấm bool
    with pytest.raises(ValueError, match="bool"):
        TimeRangeFrames(True, 10)  # type: ignore
    with pytest.raises(ValueError, match="bool"):
        TimeRangeFrames(0, False)  # type: ignore

    # Frozen
    tr = TimeRangeFrames(0, 10)
    with pytest.raises(FrozenInstanceError):
        tr.start_frame = 5  # type: ignore


# ==============================================================================
# 5. CHARACTERIZATION TEST: ĐỐI CHIẾU VỚI TIMELINEMAPPER HIỆN TẠI
# ==============================================================================

def test_characterization_compare_with_legacy_timeline_mapper():
    """
    Đối chiếu chính sách parsing của RationalFPS (strict) và TimelineMapper._parse_fps_fraction (legacy).
    Ghi nhận các điểm tương đồng và khác biệt có chủ đích.
    """
    # Các chuẩn FPS chung phổ biến: Cả hai đều cho kết quả Fraction giống nhau
    common_cases = [
        (30, Fraction(30, 1)),
        (30.0, Fraction(30, 1)),
        (24, Fraction(24, 1)),
        (24.0, Fraction(24, 1)),
        (29.97, Fraction(30000, 1001)),
        (23.976, Fraction(24000, 1001)),
        (59.94, Fraction(60000, 1001)),
        (Fraction(25, 1), Fraction(25, 1)),
    ]

    for val, expected_frac in common_cases:
        legacy_frac = TimelineMapper._parse_fps_fraction(val)
        strict_fps = RationalFPS.from_value(val)
        assert legacy_frac == expected_frac, f"Legacy mapper khác expected tại {val}"
        assert strict_fps.as_fraction() == expected_frac, f"Strict helper khác expected tại {val}"

    # Điểm KHÁC BIỆT CÓ CHỦ ĐÍCH:
    # 1. Invalid input: Legacy fallback về Fraction(30, 1); Strict helper ném ValueError rõ nghĩa
    invalid_input = "not_an_fps"
    assert TimelineMapper._parse_fps_fraction(invalid_input) == Fraction(30, 1)
    with pytest.raises(ValueError):
        RationalFPS.from_value(invalid_input)

    # 2. None input: Legacy fallback về Fraction(30, 1); Strict helper ném ValueError
    assert TimelineMapper._parse_fps_fraction(None) == Fraction(30, 1)
    with pytest.raises(ValueError):
        RationalFPS.from_value(None)

    # 3. Explicit non-standard fraction:
    # Truyền Fraction(2997, 100):
    # - Strict helper giữ nguyên Fraction(2997, 100) theo quyết định Codex
    # - Legacy mapper cũng giữ nguyên Fraction(2997, 100)
    explicit_frac = Fraction(2997, 100)
    assert RationalFPS.from_value(explicit_frac).as_fraction() == explicit_frac
    assert TimelineMapper._parse_fps_fraction(explicit_frac) == explicit_frac
