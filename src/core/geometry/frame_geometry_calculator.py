"""src/core/geometry/frame_geometry_calculator.py
Bộ tính toán lượng tử hóa hình học khung hình video CFR theo Formula B (G06-06C2a-I / G06-06C2a-R1).

Quy tắc kiến trúc:
1. Pure Python standard library (math, fractions, typing).
2. Tái sử dụng các hợp đồng thời gian chuẩn tắc từ src/core/contracts/time_contracts.py.
3. Không import UI (PySide6), filesystem IO, network, QSettings, hoặc Resolve API.
4. Lượng tử hóa Formula B (Source-Frame-First) chính xác:
   - source bounds [src_in_sec, src_out_sec) -> half-even native frames [src_in_f, src_out_f)
   - exact rational source span / speed rescale -> record duration frames via pts_to_frame.
5. Fail-closed nghiêm ngặt: ném ValueError với mã lỗi chuẩn khi thiếu bằng chứng hoặc vi phạm ranh giới.
"""
from __future__ import annotations

from fractions import Fraction
import math
from typing import Any, Optional, Union

from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames, pts_to_frame
from src.core.geometry.frame_geometry_contracts import (
    QuantizedCFRSpan,
    validate_fps_instance,
    validate_fraction_instance,
    validate_speed_instance,
)


def _validate_and_convert_fraction(val: Any, param_name: str) -> Fraction:
    """Xác thực kiểu số và chuyển đổi an toàn sang Fraction hữu hạn."""
    if type(val) is bool:
        raise ValueError(f"ERR_INVALID_NUMERIC_TYPE: {param_name} không thể là kiểu bool: {val!r}")
    if val is None:
        raise ValueError(f"ERR_INVALID_NUMERIC_TYPE: {param_name} không thể là None")
    if type(val) is int and type(val) is not bool:
        return Fraction(val, 1)
    if isinstance(val, Fraction):
        if param_name == "applied_speed":
            return validate_speed_instance(val)
        return validate_fraction_instance(
            val,
            "ERR_INVALID_NUMERIC_TYPE",
            param_name=param_name,
            allow_negative_numerator=True,
        )
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError(f"ERR_NON_FINITE_NUMBER: {param_name} float phải là số hữu hạn (không NaN/Inf), nhận: {val}")
        return Fraction(str(val))
    raise ValueError(f"ERR_INVALID_NUMERIC_TYPE: {param_name} phải là int, float hoặc Fraction, nhận: {type(val).__name__}")


def quantize_cfr_video_span(
    src_in_sec: Union[int, float, Fraction],
    src_out_sec: Union[int, float, Fraction],
    *,
    source_fps: Optional[RationalFPS],
    timeline_fps: RationalFPS,
    total_source_frames: Optional[int],
    is_cfr: Optional[bool],
    applied_speed: Union[int, float, Fraction] = 1,
) -> QuantizedCFRSpan:
    """Lượng tử hóa một phân đoạn cắt video CFR theo chính sách Công thức B (Source-Frame-First).
    
    Quy định:
    - Bắt buộc caller cung cấp đầy đủ thông số hình học: source_fps, timeline_fps, total_source_frames, is_cfr.
    - is_cfr == True mới được tính toán; None/missing -> ERR_MISSING_SOURCE_EVIDENCE; False -> ERR_VFR_UNSUPPORTED;
      loại khác bool -> ERR_INVALID_CFR_FLAG.
    - Kiểm tra bounds giây nguồn bằng Fraction chính xác: 0 <= src_in_sec < src_out_sec <= total_source_frames / source_fps.
      Bất kỳ vượt trần EOF nào ở raw seconds (dù rất nhỏ) đều ném ERR_SOURCE_BOUNDS_OVERFLOW.
    - Lượng tử hóa khung hình nguồn nguyên bản qua pts_to_frame (round-half-to-even):
      source_in_f = pts_to_frame(in_frac, source_fps)
      source_out_f = pts_to_frame(out_frac, source_fps)
      Nếu source_out_f <= source_in_f -> ERR_ZERO_FRAME_SEGMENT.
    - Tái tỷ lệ sang record duration bằng số học phân số thuần túy (Fraction arithmetic):
      exact_record_sec = Fraction(source_dur_f) / source_fps.as_fraction() / speed_frac
      record_dur_f = pts_to_frame(exact_record_sec, timeline_fps)
      Nếu record_dur_f <= 0 -> ERR_ZERO_RECORD_SPAN.
    - Zero IO, zero network, zero system clock, zero QSettings, fail-closed an toàn.
    """
    # 1. Kiểm tra cờ is_cfr
    if is_cfr is None:
        raise ValueError("ERR_MISSING_SOURCE_EVIDENCE: is_cfr không được để trống (None)")
    if type(is_cfr) is not bool:
        raise ValueError(f"ERR_INVALID_CFR_FLAG: is_cfr phải là kiểu bool, nhận: {type(is_cfr).__name__}")
    if is_cfr is False:
        raise ValueError("ERR_VFR_UNSUPPORTED: Clip có tốc độ khung hình biến thiên (VFR) không được hỗ trợ trong lát cắt CFR")

    # 2. Kiểm tra source_fps & total_source_frames (strict validation + owned copy)
    if source_fps is None:
        raise ValueError("ERR_MISSING_SOURCE_EVIDENCE: source_fps không được để trống (None)")
    owned_source_fps = validate_fps_instance(source_fps, "ERR_INVALID_SOURCE_FPS")

    if total_source_frames is None:
        raise ValueError("ERR_MISSING_SOURCE_EVIDENCE: total_source_frames không được để trống (None)")
    if type(total_source_frames) is not int or type(total_source_frames) is bool:
        raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_source_frames phải là int (không phải bool), nhận: {type(total_source_frames).__name__}")
    if total_source_frames <= 0:
        raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_source_frames phải dương (> 0), nhận: {total_source_frames}")

    # 3. Kiểm tra timeline_fps (strict validation + owned copy)
    if timeline_fps is None:
        raise ValueError("ERR_INVALID_TIMELINE_FPS: timeline_fps không được để trống (None)")
    owned_timeline_fps = validate_fps_instance(timeline_fps, "ERR_INVALID_TIMELINE_FPS")

    # 4. Xác thực và chuyển đổi các tham số thời gian sang Fraction
    in_frac = _validate_and_convert_fraction(src_in_sec, "src_in_sec")
    out_frac = _validate_and_convert_fraction(src_out_sec, "src_out_sec")
    speed_frac = _validate_and_convert_fraction(applied_speed, "applied_speed")

    if speed_frac <= 0:
        raise ValueError(f"ERR_INVALID_SPEED: applied_speed phải dương (> 0), nhận: {speed_frac}")

    # 5. Kiểm tra ranh giới raw seconds bằng Fraction (không epsilon, không clamp)
    max_source_sec = Fraction(total_source_frames) / owned_source_fps.as_fraction()
    if in_frac < 0:
        raise ValueError(f"ERR_NEGATIVE_INTERVAL: src_in_sec ({in_frac}) không được âm (< 0)")
    if in_frac >= out_frac:
        raise ValueError(f"ERR_REVERSED_OR_EMPTY_INTERVAL: src_in_sec ({in_frac}) phải nhỏ hơn src_out_sec ({out_frac})")
    if out_frac > max_source_sec:
        raise ValueError(f"ERR_SOURCE_BOUNDS_OVERFLOW: src_out_sec ({out_frac}) vượt quá thời lượng tối đa của clip nguồn ({max_source_sec})")

    # 6. Lượng tử hóa khung hình nguồn (Formula B - Source-Frame-First)
    source_in_f = pts_to_frame(in_frac, owned_source_fps)
    source_out_f = pts_to_frame(out_frac, owned_source_fps)

    if source_in_f < 0:
        raise ValueError(f"ERR_NEGATIVE_INTERVAL: source_in_f sau lượng tử hóa ({source_in_f}) < 0")
    if source_out_f > total_source_frames:
        raise ValueError(f"ERR_SOURCE_BOUNDS_OVERFLOW: source_out_f sau lượng tử hóa ({source_out_f}) vượt quá total_source_frames ({total_source_frames})")
    if source_in_f >= source_out_f:
        raise ValueError(f"ERR_ZERO_FRAME_SEGMENT: Khoảng khung hình nguồn sau lượng tử hóa có độ dài <= 0 ([{source_in_f}, {source_out_f}))")

    source_range = TimeRangeFrames(source_in_f, source_out_f)

    # 7. Tái tỷ lệ thời lượng record timeline qua số học phân số thuần túy
    exact_record_sec = Fraction(source_range.duration_frames) / owned_source_fps.as_fraction() / speed_frac
    record_dur_f = pts_to_frame(exact_record_sec, owned_timeline_fps)

    if record_dur_f <= 0:
        raise ValueError(f"ERR_ZERO_RECORD_SPAN: record_duration_frames sau quy đổi và speed có độ dài <= 0 ({record_dur_f})")

    # 8. Trả về DTO bất biến chuẩn tắc
    return QuantizedCFRSpan(
        source_fps=owned_source_fps,
        timeline_fps=owned_timeline_fps,
        source_range=source_range,
        total_source_frames=total_source_frames,
        record_duration_frames=record_dur_f,
        applied_speed=speed_frac,
    )
