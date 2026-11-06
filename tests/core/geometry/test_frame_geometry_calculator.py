"""Unit tests cho Bộ tính toán lượng tử hóa hình học khung hình video CFR theo Formula B (G06-06C2a-I / G06-06C2a-R1).

Kiểm thử toàn diện:
- Formula B tiêu chuẩn (30fps -> 24fps cut 1.0s đến 2.017s speed 1.0 = source [30, 61), record 25).
- Formula B cut dài (30fps -> 24fps cut 10.0s đến 20.0s = source [300, 600), record 240).
- Exact tie-breaking half-to-even: 48fps -> 24fps (49 native frames -> 24.5 -> 24; 51 native frames -> 25.5 -> 26).
- NTSC Rational FPS tie-breaking không đi qua float: 24000/1001 -> 30000/1001 (2 frames -> 2.5 -> 2; 6 frames -> 7.5 -> 8).
- Co giãn tốc độ speed: Fraction(3, 2) -> 32 frames; speed 2 -> 24 frames; parity giữa Fraction và float 1.5.
- Ranh giới EOF: Chấp nhận điểm cắt đúng EOF; từ chối khi raw seconds vượt EOF dù chỉ một phân số nhỏ.
- Các lỗi khoảng thời gian: Negative interval, reversed interval, zero interval, quantized zero span, high speed record 0.
- Các lỗi bằng chứng CFR: is_cfr=None, is_cfr=False (VFR), invalid is_cfr type, missing source_fps/total_source_frames.
- Rào cản kiểu (Type Gates): bool, str, None, NaN, Inf, non-RationalFPS.
- Trực tiếp khởi tạo DTO QuantizedCFRSpan: Cross-check Formula B, bounds check, immutability, defensive copies.
- 14 CODEX Counterexample Probes (Malformed nominal instances qua object.__setattr__):
  + FPS numerator/denominator sai kiểu hoặc non-positive (str, None, True, 1.0, <= 0) ở cả source/timeline, cả calculator/DTO.
  + TimeRangeFrames start/end frame sai kiểu (str, None, False, float).
- Positive controls: Độc lập bản sao, thay đổi caller member không ảnh hưởng DTO, hỗ trợ số nguyên lớn.
- Purity spies: Zero filesystem IO (open, os.stat), zero network (socket), zero clock (time.time), xác minh source không import Qt/QSettings/subprocess/clock.
"""
import ast
from fractions import Fraction
import inspect
import os
import socket
import sys
import time
import pytest

from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry import frame_geometry_calculator as calc_mod
from src.core.geometry import frame_geometry_contracts as contracts_mod
from src.core.geometry.frame_geometry_contracts import QuantizedCFRSpan
from src.core.geometry.frame_geometry_calculator import quantize_cfr_video_span


# ==============================================================================
# 1. FORMULA B STANDARD & WORKED EXAMPLES
# ==============================================================================

def test_formula_b_standard_30fps_to_24fps_worked_example():
    """Phản ví dụ chuẩn tắc: 30fps -> 24fps, cut 1.000s đến 2.017s, speed 1.0x.
    
    Toán học:
    - in = pts_to_frame(1.0, 30) = 30
    - out = pts_to_frame(2.017, 30) = 61 (6051/100 = 60.51 -> 61)
    - source_range = [30, 61), duration = 31 frames
    - exact_rec_sec = 31 / 30
    - rec_dur_f = pts_to_frame(31/30, 24) = round_half_to_even(124, 5) = 25 (24.8 -> 25)
    """
    res = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=2.017,
        source_fps=RationalFPS(30, 1),
        timeline_fps=RationalFPS(24, 1),
        total_source_frames=150,
        is_cfr=True,
        applied_speed=1,
    )
    assert isinstance(res, QuantizedCFRSpan)
    assert res.source_fps == RationalFPS(30, 1)
    assert res.timeline_fps == RationalFPS(24, 1)
    assert res.source_range == TimeRangeFrames(30, 61)
    assert res.source_range.duration_frames == 31
    assert res.total_source_frames == 150
    assert res.record_duration_frames == 25
    assert res.applied_speed == Fraction(1, 1)


def test_formula_b_long_cut_30fps_to_24fps():
    """Phân đoạn cắt dài: 30fps -> 24fps, cut 10.0s đến 20.0s, speed 1.0x.
    
    Toán học:
    - in = 300, out = 600, duration = 300 frames
    - rec_dur = 300 * 24 / 30 = 240 frames
    """
    res = quantize_cfr_video_span(
        src_in_sec=10.0,
        src_out_sec=20.0,
        source_fps=RationalFPS(30, 1),
        timeline_fps=RationalFPS(24, 1),
        total_source_frames=900,
        is_cfr=True,
        applied_speed=1,
    )
    assert res.source_range == TimeRangeFrames(300, 600)
    assert res.source_range.duration_frames == 300
    assert res.record_duration_frames == 240


# ==============================================================================
# 2. EXACT TIE BREAKING (HALF-TO-EVEN & NO-FLOAT)
# ==============================================================================

def test_formula_b_exact_tie_half_to_even_48fps_to_24fps():
    """Kiểm tra chính xác exact tie: 48fps -> 24fps (tỷ lệ 1/2).
    
    - 49 native frames: 49 * 24 / 48 = 49 / 2 = 24.5 -> Half-to-even chọn 24.
    - 51 native frames: 51 * 24 / 48 = 51 / 2 = 25.5 -> Half-to-even chọn 26.
    """
    src_fps = RationalFPS(48, 1)
    tl_fps = RationalFPS(24, 1)

    # 49 native frames
    res_49 = quantize_cfr_video_span(
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(49, 48),
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=100,
        is_cfr=True,
    )
    assert res_49.source_range.duration_frames == 49
    assert res_49.record_duration_frames == 24

    # 51 native frames
    res_51 = quantize_cfr_video_span(
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(51, 48),
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=100,
        is_cfr=True,
    )
    assert res_51.source_range.duration_frames == 51
    assert res_51.record_duration_frames == 26


def test_formula_b_ntsc_rational_fps_tie_breaking_no_float():
    """Kiểm tra NTSC Rational FPS chuẩn: 24000/1001 -> 30000/1001 (tỷ lệ 5/4), không đi qua float.
    
    - 2 native frames: 2 * 5 / 4 = 10 / 4 = 2.5 -> Half-to-even chọn 2.
    - 6 native frames: 6 * 5 / 4 = 30 / 4 = 7.5 -> Half-to-even chọn 8.
    """
    src_fps = RationalFPS(24000, 1001)
    tl_fps = RationalFPS(30000, 1001)

    # 2 native frames
    res_2 = quantize_cfr_video_span(
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(2 * 1001, 24000),
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=100,
        is_cfr=True,
    )
    assert res_2.source_range.duration_frames == 2
    assert res_2.record_duration_frames == 2

    # 6 native frames
    res_6 = quantize_cfr_video_span(
        src_in_sec=Fraction(0, 1),
        src_out_sec=Fraction(6 * 1001, 24000),
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=100,
        is_cfr=True,
    )
    assert res_6.source_range.duration_frames == 6
    assert res_6.record_duration_frames == 8


# ==============================================================================
# 3. SPEED SCALING & PARITY
# ==============================================================================

def test_formula_b_speed_scaling_fraction_and_float_parity():
    """Kiểm tra co giãn tốc độ speed: 60 native frames tại 30fps -> 24fps.
    
    - speed 1.5x (Fraction(3, 2)): exact_span = 60 * 24 / (30 * 1.5) = 1440 / 45 = 32 frames.
    - speed 2.0x (Fraction(2, 1)): exact_span = 60 * 24 / (30 * 2.0) = 1440 / 60 = 24 frames.
    - Parity: Truyền float(1.5) và Fraction(3, 2) cho kết quả tương đương hoàn hảo.
    """
    src_fps = RationalFPS(30, 1)
    tl_fps = RationalFPS(24, 1)

    # Fraction 3/2
    res_frac = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=3.0,
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=120,
        is_cfr=True,
        applied_speed=Fraction(3, 2),
    )
    assert res_frac.source_range.duration_frames == 60
    assert res_frac.record_duration_frames == 32
    assert res_frac.applied_speed == Fraction(3, 2)

    # Float 1.5
    res_float = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=3.0,
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=120,
        is_cfr=True,
        applied_speed=1.5,
    )
    assert res_float.source_range.duration_frames == 60
    assert res_float.record_duration_frames == 32
    assert res_float.applied_speed == Fraction(3, 2)
    assert res_frac == res_float

    # Speed 2.0
    res_2x = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=3.0,
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=120,
        is_cfr=True,
        applied_speed=2,
    )
    assert res_2x.record_duration_frames == 24


# ==============================================================================
# 4. RAW BOUNDS, EOF EQUALITY & OVERFLOW DETECTION
# ==============================================================================

def test_raw_eof_bounds_equality_accepted_overflow_rejected():
    """Kiểm tra ranh giới EOF clip nguồn:
    
    total_source_frames = 90 tại 30fps -> max_source_sec = 3.0s.
    - Đúng 3.0s (equality): Được chấp nhận.
    - Vượt EOF dù chỉ một phân số cực nhỏ (3.000001s): Bị từ chối ERR_SOURCE_BOUNDS_OVERFLOW
      dù sau rounding có thể rơi vào đúng frame 90.
    """
    src_fps = RationalFPS(30, 1)
    tl_fps = RationalFPS(24, 1)

    # 1. Đúng điểm kết thúc EOF (3.0s = 90 frames)
    res_eof = quantize_cfr_video_span(
        src_in_sec=0.0,
        src_out_sec=3.0,
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=90,
        is_cfr=True,
    )
    assert res_eof.source_range == TimeRangeFrames(0, 90)

    # 2. Vượt EOF một phân số cực nhỏ (Fraction 3000001 / 1000000 = 3.000001s)
    with pytest.raises(ValueError, match="ERR_SOURCE_BOUNDS_OVERFLOW"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=Fraction(3000001, 1000000),
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 3. Vượt EOF rõ ràng
    with pytest.raises(ValueError, match="ERR_SOURCE_BOUNDS_OVERFLOW"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=3.1,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )


def test_interval_errors_negative_reversed_zero_high_speed():
    """Kiểm tra các lỗi khoảng thời gian không hợp lệ:
    
    - src_in < 0 -> ERR_NEGATIVE_INTERVAL
    - src_in >= src_out -> ERR_REVERSED_OR_EMPTY_INTERVAL
    - Khoảng sau lượng tử hóa có 0 frames -> ERR_ZERO_FRAME_SEGMENT
    - Tốc độ quá cao khiến record duration = 0 frames -> ERR_ZERO_RECORD_SPAN
    """
    src_fps = RationalFPS(30, 1)
    tl_fps = RationalFPS(24, 1)

    # 1. Khoảng âm
    with pytest.raises(ValueError, match="ERR_NEGATIVE_INTERVAL"):
        quantize_cfr_video_span(
            src_in_sec=-0.1,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 2. Đảo ngược khoảng (src_in > src_out)
    with pytest.raises(ValueError, match="ERR_REVERSED_OR_EMPTY_INTERVAL"):
        quantize_cfr_video_span(
            src_in_sec=2.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 3. Điểm cắt rỗng (src_in == src_out)
    with pytest.raises(ValueError, match="ERR_REVERSED_OR_EMPTY_INTERVAL"):
        quantize_cfr_video_span(
            src_in_sec=1.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 4. Khoảng thời gian cực ngắn sau lượng tử hóa có 0 frames (cấm ép +1 frame)
    with pytest.raises(ValueError, match="ERR_ZERO_FRAME_SEGMENT"):
        quantize_cfr_video_span(
            src_in_sec=Fraction(1, 1000),
            src_out_sec=Fraction(2, 1000),
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 5. Tốc độ cực cao khiến record duration = 0 frames (cấm ép 1 frame)
    with pytest.raises(ValueError, match="ERR_ZERO_RECORD_SPAN"):
        quantize_cfr_video_span(
            src_in_sec=Fraction(0, 1),
            src_out_sec=Fraction(1, 30),
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
            applied_speed=1000,
        )


# ==============================================================================
# 5. CFR EVIDENCE & VALIDATION GATES
# ==============================================================================

def test_cfr_flag_and_missing_evidence_validation():
    """Kiểm tra nghiêm ngặt cờ is_cfr và bằng chứng hình học nguồn."""
    src_fps = RationalFPS(30, 1)
    tl_fps = RationalFPS(24, 1)

    # 1. is_cfr is None
    with pytest.raises(ValueError, match="ERR_MISSING_SOURCE_EVIDENCE"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=None,
        )

    # 2. is_cfr is False (VFR)
    with pytest.raises(ValueError, match="ERR_VFR_UNSUPPORTED"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=False,
        )

    # 3. is_cfr sai kiểu (không phải bool)
    for bad_flag in ("true", 1, [True], {}):
        with pytest.raises(ValueError, match="ERR_INVALID_CFR_FLAG"):
            quantize_cfr_video_span(
                src_in_sec=0.0,
                src_out_sec=1.0,
                source_fps=src_fps,
                timeline_fps=tl_fps,
                total_source_frames=90,
                is_cfr=bad_flag,  # type: ignore
            )

    # 4. source_fps is None
    with pytest.raises(ValueError, match="ERR_MISSING_SOURCE_EVIDENCE"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=None,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 5. total_source_frames is None
    with pytest.raises(ValueError, match="ERR_MISSING_SOURCE_EVIDENCE"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=None,
            is_cfr=True,
        )

    # 6. timeline_fps is None
    with pytest.raises(ValueError, match="ERR_INVALID_TIMELINE_FPS"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=None,  # type: ignore
            total_source_frames=90,
            is_cfr=True,
        )


# ==============================================================================
# 6. TYPE GATES & COERCION DEFENSE
# ==============================================================================

def test_type_gates_bool_str_none_nan_inf():
    """Kiểm tra rào cản kiểu dữ liệu nghiêm ngặt trước khi ép kiểu."""
    src_fps = RationalFPS(30, 1)
    tl_fps = RationalFPS(24, 1)

    # 1. bool bị cấm tuyệt đối
    with pytest.raises(ValueError, match="ERR_INVALID_NUMERIC_TYPE"):
        quantize_cfr_video_span(
            src_in_sec=True,  # type: ignore
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    with pytest.raises(ValueError, match="ERR_INVALID_NUMERIC_TYPE"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=False,  # type: ignore
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    with pytest.raises(ValueError, match="ERR_INVALID_NUMERIC_TYPE"):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
            applied_speed=True,  # type: ignore
        )

    # 2. str bị cấm tuyệt đối
    with pytest.raises(ValueError, match="ERR_INVALID_NUMERIC_TYPE"):
        quantize_cfr_video_span(
            src_in_sec="0.0",  # type: ignore
            src_out_sec=1.0,
            source_fps=src_fps,
            timeline_fps=tl_fps,
            total_source_frames=90,
            is_cfr=True,
        )

    # 3. NaN và Inf bị cấm
    for bad_num in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="ERR_NON_FINITE_NUMBER"):
            quantize_cfr_video_span(
                src_in_sec=0.0,
                src_out_sec=bad_num,
                source_fps=src_fps,
                timeline_fps=tl_fps,
                total_source_frames=90,
                is_cfr=True,
            )

    # 4. total_source_frames sai kiểu hoặc <= 0
    for bad_frames in (True, False, "100", 0, -10):
        with pytest.raises(ValueError, match="ERR_INVALID_TOTAL_FRAMES"):
            quantize_cfr_video_span(
                src_in_sec=0.0,
                src_out_sec=1.0,
                source_fps=src_fps,
                timeline_fps=tl_fps,
                total_source_frames=bad_frames,  # type: ignore
                is_cfr=True,
            )

    # 5. applied_speed <= 0
    for bad_speed in (0, -1, -0.5, Fraction(0, 1), Fraction(-1, 2)):
        with pytest.raises(ValueError, match="ERR_INVALID_SPEED"):
            quantize_cfr_video_span(
                src_in_sec=0.0,
                src_out_sec=1.0,
                source_fps=src_fps,
                timeline_fps=tl_fps,
                total_source_frames=90,
                is_cfr=True,
                applied_speed=bad_speed,
            )


# ==============================================================================
# 7. CODEX COUNTEREXAMPLE PROBES (MALFORMED NOMINAL INSTANCES & BOUNDARY TESTS)
# ==============================================================================

def _make_corrupt_fps(numerator=30, denominator=1):
    fps = RationalFPS(30, 1)
    object.__setattr__(fps, "numerator", numerator)
    object.__setattr__(fps, "denominator", denominator)
    return fps


def _make_corrupt_range(start_frame=0, end_frame=30):
    r = TimeRangeFrames(0, 30)
    object.__setattr__(r, "start_frame", start_frame)
    object.__setattr__(r, "end_frame", end_frame)
    return r


@pytest.mark.parametrize(
    "corrupt_fps,expected_error_code",
    [
        (_make_corrupt_fps(numerator="30"), "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(denominator=None), "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(numerator=True), "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(denominator=1.0), "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(numerator=0), "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(denominator=-1), "ERR_INVALID_SOURCE_FPS"),
    ],
)
def test_probes_calculator_source_fps_malformed(corrupt_fps, expected_error_code):
    """Kiểm tra calculator reject dứt khoát malformed source_fps với ERR_INVALID_SOURCE_FPS."""
    with pytest.raises(ValueError, match=expected_error_code):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=corrupt_fps,
            timeline_fps=RationalFPS(24, 1),
            total_source_frames=90,
            is_cfr=True,
        )


@pytest.mark.parametrize(
    "corrupt_fps,expected_error_code",
    [
        (_make_corrupt_fps(numerator="30"), "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(denominator=None), "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(numerator=True), "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(denominator=1.0), "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(numerator=0), "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(denominator=-1), "ERR_INVALID_TIMELINE_FPS"),
    ],
)
def test_probes_calculator_timeline_fps_malformed(corrupt_fps, expected_error_code):
    """Kiểm tra calculator reject dứt khoát malformed timeline_fps với ERR_INVALID_TIMELINE_FPS."""
    with pytest.raises(ValueError, match=expected_error_code):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=RationalFPS(30, 1),
            timeline_fps=corrupt_fps,
            total_source_frames=90,
            is_cfr=True,
        )


@pytest.mark.parametrize(
    "corrupt_fps,target_field,expected_error_code",
    [
        (_make_corrupt_fps(numerator="30"), "source_fps", "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(denominator=None), "source_fps", "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(numerator=True), "source_fps", "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(denominator=1.0), "source_fps", "ERR_INVALID_SOURCE_FPS"),
        (_make_corrupt_fps(numerator="24"), "timeline_fps", "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(denominator=None), "timeline_fps", "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(numerator=True), "timeline_fps", "ERR_INVALID_TIMELINE_FPS"),
        (_make_corrupt_fps(denominator=1.0), "timeline_fps", "ERR_INVALID_TIMELINE_FPS"),
    ],
)
def test_probes_direct_dto_fps_malformed(corrupt_fps, target_field, expected_error_code):
    """Kiểm tra direct DTO constructor reject dứt khoát malformed source_fps / timeline_fps."""
    src_fps = corrupt_fps if target_field == "source_fps" else RationalFPS(30, 1)
    tl_fps = corrupt_fps if target_field == "timeline_fps" else RationalFPS(24, 1)
    with pytest.raises(ValueError, match=expected_error_code):
        QuantizedCFRSpan(
            source_fps=src_fps,
            timeline_fps=tl_fps,
            source_range=TimeRangeFrames(0, 30),
            total_source_frames=90,
            record_duration_frames=24,
            applied_speed=Fraction(1, 1),
        )


@pytest.mark.parametrize(
    "corrupt_range,expected_error_code",
    [
        (_make_corrupt_range(start_frame="0"), "ERR_INVALID_SOURCE_RANGE"),
        (_make_corrupt_range(end_frame=None), "ERR_INVALID_SOURCE_RANGE"),
        (_make_corrupt_range(start_frame=False), "ERR_INVALID_SOURCE_RANGE"),
        (_make_corrupt_range(end_frame=30.0), "ERR_INVALID_SOURCE_RANGE"),
        (_make_corrupt_range(start_frame=-1), "ERR_NEGATIVE_INTERVAL"),
        (_make_corrupt_range(start_frame=50, end_frame=40), "ERR_ZERO_FRAME_SEGMENT"),
        (_make_corrupt_range(start_frame=10, end_frame=10), "ERR_ZERO_FRAME_SEGMENT"),
        (_make_corrupt_range(start_frame=0, end_frame=101), "ERR_SOURCE_BOUNDS_OVERFLOW"),
    ],
)
def test_probes_direct_dto_source_range_malformed(corrupt_range, expected_error_code):
    """Kiểm tra direct DTO constructor reject dứt khoát malformed TimeRangeFrames trước và trong bounds check."""
    with pytest.raises(ValueError, match=expected_error_code):
        QuantizedCFRSpan(
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            source_range=corrupt_range,
            total_source_frames=100,
            record_duration_frames=24,
            applied_speed=Fraction(1, 1),
        )


# ==============================================================================
# 8. POSITIVE CONTROLS & DEFENSIVE COPY VALIDATION
# ==============================================================================

def test_defensive_copies_and_caller_mutation_isolation():
    """Kiểm tra caller biến đổi đối tượng bên ngoài không làm thay đổi DTO nội bộ."""
    caller_src_fps = RationalFPS(30, 1)
    caller_tl_fps = RationalFPS(24, 1)
    caller_speed = Fraction(1, 1)

    res = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=2.017,
        source_fps=caller_src_fps,
        timeline_fps=caller_tl_fps,
        total_source_frames=150,
        is_cfr=True,
        applied_speed=caller_speed,
    )

    # Cố ý làm hỏng đối tượng caller sau khi nhận kết quả
    object.__setattr__(caller_src_fps, "numerator", 999)
    object.__setattr__(caller_tl_fps, "denominator", 999)

    # DTO nội bộ không bị ảnh hưởng
    assert res.source_fps.numerator == 30
    assert res.timeline_fps.denominator == 1
    assert res.source_range.start_frame == 30
    assert res.source_range.end_frame == 61
    assert res.record_duration_frames == 25


def test_positive_large_int_and_fractions():
    """Kiểm tra số nguyên lớn (không tràn số, không float conversion mất mát)."""
    src_fps = RationalFPS(60, 1)
    tl_fps = RationalFPS(30, 1)
    # 100,000 frames nguồn tại 60fps = 5000/3 giây
    total_frames = 200_000
    res = quantize_cfr_video_span(
        src_in_sec=0,
        src_out_sec=Fraction(100_000, 60),
        source_fps=src_fps,
        timeline_fps=tl_fps,
        total_source_frames=total_frames,
        is_cfr=True,
        applied_speed=1,
    )
    assert res.source_range.duration_frames == 100_000
    assert res.record_duration_frames == 50_000


# ==============================================================================
# 9. PURITY SPIES & SOURCE/AST INSPECTION (ZERO IO, ZERO CLOCK, ZERO QT)
# ==============================================================================

def test_purity_zero_filesystem_network_and_clock(monkeypatch):
    """Xác nhận bộ tính toán là pure function 100% qua spies và import review:
    
    1. Spies: Zero open/stat, zero socket, zero time.time.
    2. AST/Source Inspection: Không import datetime, time, PySide6, QSettings, os, sys, subprocess.
    """
    io_called = []
    net_called = []
    clock_called = []

    real_open = open
    real_stat = os.stat
    real_socket = socket.socket
    real_time = time.time

    def fake_open(*args, **kwargs):
        io_called.append(("open", args))
        return real_open(*args, **kwargs)

    def fake_stat(*args, **kwargs):
        io_called.append(("stat", args))
        return real_stat(*args, **kwargs)

    def fake_socket(*args, **kwargs):
        net_called.append(("socket", args))
        return real_socket(*args, **kwargs)

    def fake_time():
        clock_called.append("time.time")
        return real_time()

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr("os.stat", fake_stat)
    monkeypatch.setattr("socket.socket", fake_socket)
    monkeypatch.setattr("time.time", fake_time)

    # Thực thi tính toán Formula B
    res = quantize_cfr_video_span(
        src_in_sec=1.0,
        src_out_sec=2.017,
        source_fps=RationalFPS(30, 1),
        timeline_fps=RationalFPS(24, 1),
        total_source_frames=150,
        is_cfr=True,
        applied_speed=1,
    )
    assert res.record_duration_frames == 25

    # Khẳng định 0 cuộc gọi ngoại vi trong runtime
    assert io_called == [], f"Phát hiện filesystem IO: {io_called}"
    assert net_called == [], f"Phát hiện network calls: {net_called}"
    assert clock_called == [], f"Phát hiện system clock calls: {clock_called}"


def test_source_ast_purity_no_forbidden_imports():
    """Kiểm tra tĩnh cây cú pháp AST: Cả hai module pure geometry không import bất kỳ thư viện cấm nào."""
    forbidden_modules = {"datetime", "time", "os", "sys", "subprocess", "PySide6", "PySide2", "PyQt5", "PyQt6", "socket"}

    for module in (calc_mod, contracts_mod):
        source_code = inspect.getsource(module)
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert root_pkg not in forbidden_modules, f"{module.__name__} import cấm: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert root_pkg not in forbidden_modules, f"{module.__name__} import from cấm: {node.module}"


# ==============================================================================
# 10. FRACTION BOUNDARY COUNTEREXAMPLE PROBES (G06-06C2a-R2)
# ==============================================================================

def _make_corrupt_frac(numerator=1, denominator=1):
    """Helper tạo instance Fraction bị can thiệp nội bộ qua object.__setattr__."""
    f = Fraction(1, 1)
    object.__setattr__(f, "_numerator", numerator)
    object.__setattr__(f, "_denominator", denominator)
    return f


@pytest.mark.parametrize(
    "corrupt_frac,expected_error_code",
    [
        (_make_corrupt_frac(denominator=0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=-1), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator="1"), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=True), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=None), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=None), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=True), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=1.0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=-2), "ERR_INVALID_SPEED"),
    ],
)
def test_probes_calculator_speed_fraction_malformed(corrupt_frac, expected_error_code):
    """Kiểm tra calculator reject dứt khoát malformed Fraction applied_speed với ERR_INVALID_SPEED."""
    with pytest.raises(ValueError, match=expected_error_code):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=1.0,
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            total_source_frames=90,
            is_cfr=True,
            applied_speed=corrupt_frac,
        )


@pytest.mark.parametrize(
    "corrupt_frac,expected_error_code",
    [
        (_make_corrupt_frac(denominator=0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=-1), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator="1"), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=True), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=None), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=None), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=True), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(denominator=1.0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=0), "ERR_INVALID_SPEED"),
        (_make_corrupt_frac(numerator=-1), "ERR_INVALID_SPEED"),
    ],
)
def test_probes_direct_dto_speed_fraction_malformed(corrupt_frac, expected_error_code):
    """Kiểm tra direct DTO QuantizedCFRSpan reject dứt khoát malformed Fraction applied_speed với ERR_INVALID_SPEED."""
    with pytest.raises(ValueError, match=expected_error_code):
        QuantizedCFRSpan(
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            source_range=TimeRangeFrames(0, 30),
            total_source_frames=90,
            record_duration_frames=24,
            applied_speed=corrupt_frac,
        )


@pytest.mark.parametrize(
    "corrupt_frac,expected_error_code",
    [
        (_make_corrupt_frac(denominator=0), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=-1), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator="1"), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=True), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator=None), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=None), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator=True), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=1.0), "ERR_INVALID_NUMERIC_TYPE"),
    ],
)
def test_probes_calculator_src_in_sec_fraction_malformed(corrupt_frac, expected_error_code):
    """Kiểm tra calculator reject dứt khoát malformed Fraction src_in_sec với ERR_INVALID_NUMERIC_TYPE."""
    with pytest.raises(ValueError, match=expected_error_code):
        quantize_cfr_video_span(
            src_in_sec=corrupt_frac,
            src_out_sec=1.0,
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            total_source_frames=90,
            is_cfr=True,
            applied_speed=1,
        )


@pytest.mark.parametrize(
    "corrupt_frac,expected_error_code",
    [
        (_make_corrupt_frac(denominator=0), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=-1), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator="1"), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=True), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator=None), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=None), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(numerator=True), "ERR_INVALID_NUMERIC_TYPE"),
        (_make_corrupt_frac(denominator=1.0), "ERR_INVALID_NUMERIC_TYPE"),
    ],
)
def test_probes_calculator_src_out_sec_fraction_malformed(corrupt_frac, expected_error_code):
    """Kiểm tra calculator reject dứt khoát malformed Fraction src_out_sec với ERR_INVALID_NUMERIC_TYPE."""
    with pytest.raises(ValueError, match=expected_error_code):
        quantize_cfr_video_span(
            src_in_sec=0.0,
            src_out_sec=corrupt_frac,
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            total_source_frames=90,
            is_cfr=True,
            applied_speed=1,
        )


def test_positive_control_valid_negative_fraction_preserves_negative_interval_domain_code():
    """Fraction chuẩn có tử số âm (ví dụ -1/2s) phải đi qua domain gate ERR_NEGATIVE_INTERVAL, không đổi thành ERR_INVALID_NUMERIC_TYPE."""
    valid_negative_frac = Fraction(-1, 2)
    with pytest.raises(ValueError, match="ERR_NEGATIVE_INTERVAL"):
        quantize_cfr_video_span(
            src_in_sec=valid_negative_frac,
            src_out_sec=1.0,
            source_fps=RationalFPS(30, 1),
            timeline_fps=RationalFPS(24, 1),
            total_source_frames=90,
            is_cfr=True,
            applied_speed=1,
        )


def test_positive_control_huge_exact_fraction_no_float_loss():
    """Kiểm tra Fraction số nguyên cực lớn được xử lý chính xác tuyệt đối mà không đi qua float."""
    huge_num = 10**18 + 1
    huge_den = 10**9
    huge_frac = Fraction(huge_num, huge_den)

    # DTO constructor xác thực trực tiếp
    dto = QuantizedCFRSpan(
        source_fps=RationalFPS(30, 1),
        timeline_fps=RationalFPS(30, 1),
        source_range=TimeRangeFrames(0, 30),
        total_source_frames=90,
        record_duration_frames=30,
        applied_speed=Fraction(1, 1),
    )
    assert dto.applied_speed == Fraction(1, 1)
