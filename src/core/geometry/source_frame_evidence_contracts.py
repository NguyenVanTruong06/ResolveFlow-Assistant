"""src/core/geometry/source_frame_evidence_contracts.py
Hợp đồng dữ liệu header luồng video từ công cụ ffprobe (G06-06C2b-I).

Quy tắc kiến trúc:
1. Pure Python standard library (dataclasses, fractions, typing).
2. Tái sử dụng RationalFPS từ src/core/contracts/time_contracts.py.
3. Không import UI (PySide6), filesystem IO, network, QSettings, subprocess, hay Resolve API.
4. Bất biến hoàn toàn (frozen dataclass, defensive copies).
5. Phân định rõ: VideoStreamHeader là siêu dữ liệu được container báo cáo (reported metadata),
   chưa phải bằng chứng khung hình nguyên bản đã qua kiểm định (unverified evidence).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.frame_geometry_contracts import validate_fps_instance

MAX_METADATA_STREAMS: int = 256
MAX_METADATA_TEXT_BYTES: int = 1024


def _validate_header_fps(fps: object, field_name: str) -> RationalFPS:
    """Xác thực nghiêm ngặt nominal instance RationalFPS tại biên header mà không rò rỉ bare error."""
    if not isinstance(fps, RationalFPS):
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} phải là instance của RationalFPS, "
            f"nhận: {type(fps).__name__ if fps is not None else 'None'}"
        )
    num = getattr(fps, "numerator", None)
    den = getattr(fps, "denominator", None)
    if type(num) is not int or type(num) is bool:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} numerator phải là int (không phải bool), "
            f"nhận: {type(num).__name__ if num is not None else 'None'}"
        )
    if type(den) is not int or type(den) is bool:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} denominator phải là int (không phải bool), "
            f"nhận: {type(den).__name__ if den is not None else 'None'}"
        )
    if num <= 0 or den <= 0:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} numerator và denominator phải dương (> 0)"
        )
    try:
        return validate_fps_instance(fps, "ERR_INVALID_VIDEO_METADATA")
    except ValueError as err:
        msg = str(err)
        if not msg.startswith("ERR_INVALID_VIDEO_METADATA:"):
            raise ValueError(f"ERR_INVALID_VIDEO_METADATA: {field_name} không hợp lệ") from None
        raise


@dataclass(frozen=True)
class VideoStreamHeader:
    """Siêu dữ liệu tiêu đề (header) của một stream video do ffprobe báo cáo.

    Quy định:
    - Đúng 6 trường chuẩn tắc (không có trường phụ, không có cờ verified hay is_cfr):
      + stream_index: int (strict int >= 0)
      + codec_name: Optional[str]
      + r_frame_rate: Optional[RationalFPS]
      + avg_frame_rate: Optional[RationalFPS]
      + reported_nb_frames: Optional[int] (strict int > 0)
      + time_base: Optional[Fraction]
    - Bất biến hoàn toàn (frozen dataclass).
    - Validation toàn diện tại constructor (ngăn chặn malformed nominal instances).
    - Sở hữu bản sao (defensive copies) cho các đối tượng phức hợp.
    """
    stream_index: int
    codec_name: Optional[str]
    r_frame_rate: Optional[RationalFPS]
    avg_frame_rate: Optional[RationalFPS]
    reported_nb_frames: Optional[int]
    time_base: Optional[Fraction]

    def __post_init__(self) -> None:
        # 1. stream_index: strict int >= 0 (cấm bool)
        if type(self.stream_index) is not int or type(self.stream_index) is bool or self.stream_index < 0:
            raise ValueError(
                f"ERR_INVALID_VIDEO_METADATA: stream_index phải là int >= 0 (không phải bool), "
                f"nhận: {type(self.stream_index).__name__}"
            )

        # 2. codec_name: Optional nonblank bounded UTF-8 str without NUL
        if self.codec_name is not None:
            if type(self.codec_name) is not str:
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: codec_name phải là str hoặc None, "
                    f"nhận: {type(self.codec_name).__name__}"
                )
            try:
                encoded = self.codec_name.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("ERR_INVALID_VIDEO_METADATA: codec_name chứa ký tự không thể mã hóa UTF-8")

            if len(encoded) > MAX_METADATA_TEXT_BYTES:
                raise ValueError(
                    f"ERR_PROBE_METADATA_LIMIT: codec_name vượt quá giới hạn {MAX_METADATA_TEXT_BYTES} bytes"
                )
            if not self.codec_name or self.codec_name.isspace() or "\x00" in self.codec_name:
                raise ValueError(
                    "ERR_INVALID_VIDEO_METADATA: codec_name không được rỗng, khoảng trắng, hoặc chứa NUL byte"
                )

        # 3. r_frame_rate: Optional RationalFPS (strict member check + owned copy)
        if self.r_frame_rate is not None:
            owned_r_fps = _validate_header_fps(self.r_frame_rate, "r_frame_rate")
            object.__setattr__(self, "r_frame_rate", owned_r_fps)

        # 4. avg_frame_rate: Optional RationalFPS (strict member check + owned copy)
        if self.avg_frame_rate is not None:
            owned_avg_fps = _validate_header_fps(self.avg_frame_rate, "avg_frame_rate")
            object.__setattr__(self, "avg_frame_rate", owned_avg_fps)

        # 5. reported_nb_frames: Optional strict int > 0 (cấm bool)
        if self.reported_nb_frames is not None:
            if (
                type(self.reported_nb_frames) is not int
                or type(self.reported_nb_frames) is bool
                or self.reported_nb_frames <= 0
            ):
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: reported_nb_frames phải là int > 0 (không phải bool), "
                    f"nhận: {type(self.reported_nb_frames).__name__}"
                )

        # 6. time_base: Optional Fraction (strict member check num > 0, den > 0 + owned copy)
        if self.time_base is not None:
            if not isinstance(self.time_base, Fraction):
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: time_base phải là Fraction hoặc None, "
                    f"nhận: {type(self.time_base).__name__}"
                )
            num = getattr(self.time_base, "numerator", None)
            den = getattr(self.time_base, "denominator", None)
            if type(num) is not int or type(num) is bool:
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: time_base numerator phải là int (không phải bool), "
                    f"nhận: {type(num).__name__ if num is not None else 'None'}"
                )
            if type(den) is not int or type(den) is bool:
                raise ValueError(
                    f"ERR_INVALID_VIDEO_METADATA: time_base denominator phải là int (không phải bool), "
                    f"nhận: {type(den).__name__ if den is not None else 'None'}"
                )
            if den <= 0 or num <= 0:
                raise ValueError(
                    "ERR_INVALID_VIDEO_METADATA: time_base numerator và denominator phải dương (> 0)"
                )
            object.__setattr__(self, "time_base", Fraction(num, den))
