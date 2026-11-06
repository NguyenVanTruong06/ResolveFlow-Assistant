"""src/core/geometry/frame_geometry_contracts.py
Hợp đồng hình học khung hình video thuần Python (G06-06C2a-I / G06-06C2a-R1).

Quy tắc kiến trúc:
1. Pure Python standard library (dataclasses, fractions, typing).
2. Tái sử dụng các hợp đồng thời gian chuẩn tắc từ src/core/contracts/time_contracts.py.
3. Không import UI (PySide6), filesystem IO, network, QSettings, hoặc Resolve API.
4. Bất biến hoàn toàn (frozen dataclass, defensive copies).
5. Strict type gates & revalidation cho typed instances để ngăn chặn malformed nominal objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames, pts_to_frame


def validate_fps_instance(fps: Any, error_code: str) -> RationalFPS:
    """Xác thực nghiêm ngặt nominal instance RationalFPS và trả về owned defensive copy.
    
    Bảo đảm:
    - fps bắt buộc là instance của RationalFPS.
    - numerator và denominator bắt buộc là strict int (không phải bool) và > 0.
    - Ngăn chặn triệt để malformed instances bị can thiệp qua object.__setattr__.
    """
    if not isinstance(fps, RationalFPS):
        raise ValueError(
            f"{error_code}: fps phải là instance của RationalFPS, "
            f"nhận: {type(fps).__name__ if fps is not None else 'None'}"
        )

    num = getattr(fps, "numerator", None)
    den = getattr(fps, "denominator", None)

    if type(num) is not int or type(num) is bool:
        raise ValueError(f"{error_code}: numerator phải là int (không phải bool), nhận: {type(num).__name__}")
    if type(den) is not int or type(den) is bool:
        raise ValueError(f"{error_code}: denominator phải là int (không phải bool), nhận: {type(den).__name__}")

    if num <= 0:
        raise ValueError(f"{error_code}: numerator phải dương (> 0), nhận: {num}")
    if den <= 0:
        raise ValueError(f"{error_code}: denominator phải dương (> 0), nhận: {den}")

    return RationalFPS(num, den)


def validate_source_range_instance(range_obj: Any, total_frames: int) -> TimeRangeFrames:
    """Xác thực nghiêm ngặt nominal instance TimeRangeFrames và trả về owned defensive copy.
    
    Bảo đảm:
    - range_obj bắt buộc là instance của TimeRangeFrames.
    - start_frame và end_frame bắt buộc là strict int (không phải bool) TRƯỚC mọi phép so sánh (<, >, >=).
    - Áp dụng các domain bounds codes:
      + start < 0 -> ERR_NEGATIVE_INTERVAL
      + end > total_frames -> ERR_SOURCE_BOUNDS_OVERFLOW
      + start >= end -> ERR_ZERO_FRAME_SEGMENT
    """
    if not isinstance(range_obj, TimeRangeFrames):
        raise ValueError(
            f"ERR_INVALID_SOURCE_RANGE: source_range phải là TimeRangeFrames, "
            f"nhận: {type(range_obj).__name__ if range_obj is not None else 'None'}"
        )

    start = getattr(range_obj, "start_frame", None)
    end = getattr(range_obj, "end_frame", None)

    # 1. Strict int member validation TRƯỚC mọi phép so sánh
    if type(start) is not int or type(start) is bool:
        raise ValueError(f"ERR_INVALID_SOURCE_RANGE: start_frame phải là int (không phải bool), nhận: {type(start).__name__}")
    if type(end) is not int or type(end) is bool:
        raise ValueError(f"ERR_INVALID_SOURCE_RANGE: end_frame phải là int (không phải bool), nhận: {type(end).__name__}")

    # 2. Domain bounds checks
    if start < 0:
        raise ValueError(f"ERR_NEGATIVE_INTERVAL: source_range.start_frame ({start}) không được âm (< 0)")
    if end > total_frames:
        raise ValueError(
            f"ERR_SOURCE_BOUNDS_OVERFLOW: source_range.end_frame ({end}) "
            f"vượt quá total_source_frames ({total_frames})"
        )
    if start >= end:
        raise ValueError(
            f"ERR_ZERO_FRAME_SEGMENT: source_range phải có start_frame < end_frame, "
            f"nhận: [{start}, {end})"
        )

    return TimeRangeFrames(start, end)


def validate_fraction_instance(
    val: Any,
    error_code: str,
    *,
    param_name: str = "fraction",
    allow_negative_numerator: bool = True,
) -> Fraction:
    """Xác thực nghiêm ngặt nominal instance Fraction và trả về canonical owned copy.
    
    Bảo đảm:
    - val bắt buộc là instance của Fraction.
    - numerator và denominator bắt buộc là strict int (không phải bool) TRƯỚC khi so sánh hoặc tính toán.
    - denominator bắt buộc > 0.
    - Nếu allow_negative_numerator == False: numerator bắt buộc > 0 (áp dụng cho speed).
    - Trả về bản sao độc lập Fraction(num, den).
    """
    if not isinstance(val, Fraction):
        raise ValueError(
            f"{error_code}: {param_name} phải là Fraction, "
            f"nhận: {type(val).__name__ if val is not None else 'None'}"
        )
    num = getattr(val, "numerator", None)
    den = getattr(val, "denominator", None)
    if type(num) is not int or type(num) is bool:
        raise ValueError(
            f"{error_code}: {param_name} numerator phải là int (không phải bool), "
            f"nhận: {type(num).__name__}"
        )
    if type(den) is not int or type(den) is bool:
        raise ValueError(
            f"{error_code}: {param_name} denominator phải là int (không phải bool), "
            f"nhận: {type(den).__name__}"
        )
    if den <= 0:
        raise ValueError(f"{error_code}: {param_name} denominator phải dương (> 0), nhận: {den}")
    if not allow_negative_numerator and num <= 0:
        raise ValueError(f"{error_code}: {param_name} phải dương (> 0), nhận: {num}/{den}")

    return Fraction(num, den)


def validate_speed_instance(speed: Any) -> Fraction:
    """Xác thực nghiêm ngặt instance Fraction speed và trả về canonical owned copy."""
    return validate_fraction_instance(
        speed,
        "ERR_INVALID_SPEED",
        param_name="applied_speed",
        allow_negative_numerator=False,
    )


@dataclass(frozen=True)
class QuantizedCFRSpan:
    """Đại diện một phân đoạn video CFR đã được lượng tử hóa khung hình theo Formula B.
    
    Quy định:
    - Đúng 6 trường chuẩn tắc:
      + source_fps: RationalFPS
      + timeline_fps: RationalFPS
      + source_range: TimeRangeFrames [src_in_f, src_out_f)
      + total_source_frames: int > 0
      + record_duration_frames: int > 0
      + applied_speed: Fraction > 0
    - Bất biến hoàn toàn (frozen dataclass).
    - Validation toàn diện thực thi thống nhất tại constructor (hỗ trợ direct instantiation an toàn).
    - Sở hữu bản sao (defensive copy) cho các đối tượng cấu thành để ngăn mutable alias bypass.
    """
    source_fps: RationalFPS
    timeline_fps: RationalFPS
    source_range: TimeRangeFrames
    total_source_frames: int
    record_duration_frames: int
    applied_speed: Fraction

    def __post_init__(self) -> None:
        # 1. total_source_frames validation (cấm bool) trước để dùng cho range check
        if type(self.total_source_frames) is not int or type(self.total_source_frames) is bool:
            raise ValueError(
                f"ERR_INVALID_TOTAL_FRAMES: total_source_frames phải là int (không phải bool), "
                f"nhận: {type(self.total_source_frames).__name__}"
            )
        if self.total_source_frames <= 0:
            raise ValueError(f"ERR_INVALID_TOTAL_FRAMES: total_source_frames phải dương (> 0), nhận: {self.total_source_frames}")

        # 2. source_fps & timeline_fps validation (strict member checks + owned copies)
        owned_src_fps = validate_fps_instance(self.source_fps, "ERR_INVALID_SOURCE_FPS")
        object.__setattr__(self, "source_fps", owned_src_fps)

        owned_tl_fps = validate_fps_instance(self.timeline_fps, "ERR_INVALID_TIMELINE_FPS")
        object.__setattr__(self, "timeline_fps", owned_tl_fps)

        # 3. source_range validation (strict member checks + domain bounds + owned copy)
        owned_range = validate_source_range_instance(self.source_range, self.total_source_frames)
        object.__setattr__(self, "source_range", owned_range)

        # 4. applied_speed validation (strict member checks + owned copy)
        owned_speed = validate_speed_instance(self.applied_speed)
        object.__setattr__(self, "applied_speed", owned_speed)

        # 5. record_duration_frames validation (cấm bool)
        if type(self.record_duration_frames) is not int or type(self.record_duration_frames) is bool:
            raise ValueError(
                f"ERR_INVALID_RECORD_DURATION: record_duration_frames phải là int (không phải bool), "
                f"nhận: {type(self.record_duration_frames).__name__}"
            )
        if self.record_duration_frames <= 0:
            raise ValueError(f"ERR_ZERO_RECORD_SPAN: record_duration_frames phải dương (> 0), nhận: {self.record_duration_frames}")

        # 6. Formula B Cross-Check (Toán học nhất quán bảo vệ direct instantiation)
        source_dur = owned_range.duration_frames
        exact_record_sec = Fraction(source_dur) / owned_src_fps.as_fraction() / owned_speed
        expected_rec_dur = pts_to_frame(exact_record_sec, owned_tl_fps)
        if self.record_duration_frames != expected_rec_dur:
            raise ValueError(
                f"ERR_INVALID_RECORD_DURATION: record_duration_frames ({self.record_duration_frames}) "
                f"không khớp với kết quả Formula B ({expected_rec_dur})"
            )
