"""src/core/geometry/source_frame_evidence_parser.py
Bộ phân tích cú pháp tiêu đề luồng video (ffprobe video header parser) thuần túy (G06-06C2b-I).

Quy tắc kiến trúc:
1. Pure Python standard library (re, typing, fractions, collections.abc).
2. Tái sử dụng RationalFPS từ src/core/contracts/time_contracts.py và VideoStreamHeader từ source_frame_evidence_contracts.py.
3. Không import UI (PySide6), filesystem IO, network, QSettings, subprocess, hay Resolve API.
4. Bất biến hoàn toàn (frozen dataclass, defensive copies).
5. Phân định rõ ràng: Header metadata là thông tin được container báo cáo (reported metadata),
   không tự ý suy diễn là bằng chứng CFR hay số khung hình chính xác tuyệt đối.
"""
from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
import re
from typing import Any, Optional, Set

from src.core.contracts.time_contracts import RationalFPS
from src.core.geometry.source_frame_evidence_contracts import (
    MAX_METADATA_STREAMS,
    MAX_METADATA_TEXT_BYTES,
    VideoStreamHeader,
)

RE_ASCII_FRACTION = re.compile(r"^[0-9]+/[0-9]+$")
RE_CANONICAL_POSITIVE_INT = re.compile(r"^[1-9][0-9]*$")


def _check_bounded_text(val: str, field_name: str) -> None:
    """Kiểm tra độ dài byte UTF-8 và tính hợp lệ của chuỗi văn bản tiêu đề."""
    try:
        encoded = val.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"ERR_INVALID_VIDEO_METADATA: {field_name} chứa ký tự không hợp lệ UTF-8")

    if len(encoded) > MAX_METADATA_TEXT_BYTES:
        raise ValueError(
            f"ERR_PROBE_METADATA_LIMIT: {field_name} vượt quá giới hạn {MAX_METADATA_TEXT_BYTES} bytes"
        )


def _parse_rational_rate(raw_val: Any, field_name: str) -> Optional[RationalFPS]:
    """Phân tích chuỗi N/D tỷ lệ khung hình ffprobe (r_frame_rate, avg_frame_rate)."""
    if raw_val is None:
        return None
    if type(raw_val) is not str:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} phải là str hoặc None, "
            f"nhận: {type(raw_val).__name__ if raw_val is not None else 'None'}"
        )

    _check_bounded_text(raw_val, field_name)

    # Sentinels báo hiệu không xác định
    if raw_val == "N/A" or raw_val == "0/0":
        return None

    # Khớp chính xác định dạng phân số ASCII N/D (không dấu, không khoảng trắng, không số thực)
    if not RE_ASCII_FRACTION.fullmatch(raw_val):
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: {field_name} không đúng định dạng N/D số nguyên ASCII"
        )

    n_str, d_str = raw_val.split("/")
    num = int(n_str)
    den = int(d_str)

    if den == 0:
        if num > 0:
            raise ValueError(f"ERR_INVALID_VIDEO_METADATA: {field_name} có mẫu số bằng 0 với tử số dương")
        return None

    # den > 0
    if num == 0:
        # Tử số bằng 0 với mẫu số dương -> Không xác định (unknown)
        return None

    return RationalFPS(num, den)


def _parse_time_base(raw_val: Any) -> Optional[Fraction]:
    """Phân tích chuỗi N/D cơ sở thời gian ffprobe (time_base)."""
    if raw_val is None:
        return None
    if type(raw_val) is not str:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: time_base phải là str hoặc None, "
            f"nhận: {type(raw_val).__name__ if raw_val is not None else 'None'}"
        )

    _check_bounded_text(raw_val, "time_base")

    if raw_val == "N/A" or raw_val == "0/0":
        return None

    if not RE_ASCII_FRACTION.fullmatch(raw_val):
        raise ValueError(
            "ERR_INVALID_VIDEO_METADATA: time_base không đúng định dạng N/D số nguyên ASCII"
        )

    n_str, d_str = raw_val.split("/")
    num = int(n_str)
    den = int(d_str)

    if den == 0:
        if num > 0:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: time_base có mẫu số bằng 0 với tử số dương")
        return None

    if num == 0:
        return None

    return Fraction(num, den)


def _parse_reported_nb_frames(raw_val: Any) -> Optional[int]:
    """Phân tích số lượng khung hình do container báo cáo (reported_nb_frames)."""
    if raw_val is None:
        return None

    if type(raw_val) is int and type(raw_val) is not bool:
        if raw_val == 0:
            return None
        if raw_val < 0:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: reported_nb_frames không được âm")
        return raw_val

    if type(raw_val) is str:
        _check_bounded_text(raw_val, "nb_frames")
        if raw_val == "N/A" or raw_val == "0":
            return None
        if RE_CANONICAL_POSITIVE_INT.fullmatch(raw_val):
            return int(raw_val)
        raise ValueError(
            "ERR_INVALID_VIDEO_METADATA: nb_frames chuỗi không đúng chuẩn số nguyên dương ASCII"
        )

    raise ValueError(
        f"ERR_INVALID_VIDEO_METADATA: nb_frames phải là int hoặc str hợp lệ, "
        f"nhận: {type(raw_val).__name__}"
    )


def _parse_codec_name(raw_val: Any) -> Optional[str]:
    """Xác thực và trả về codec_name nguyên bản."""
    if raw_val is None:
        return None
    if type(raw_val) is not str:
        raise ValueError(
            f"ERR_INVALID_VIDEO_METADATA: codec_name phải là str hoặc None, "
            f"nhận: {type(raw_val).__name__}"
        )

    _check_bounded_text(raw_val, "codec_name")

    if not raw_val or raw_val.isspace() or "\x00" in raw_val:
        raise ValueError("ERR_INVALID_VIDEO_METADATA: codec_name không được rỗng, khoảng trắng, hoặc chứa NUL")

    return raw_val


def parse_ffprobe_video_header(
    payload: Mapping[str, Any],
    *,
    selected_stream_index: Optional[int] = None,
) -> VideoStreamHeader:
    """Phân tích cú pháp tải trọng JSON từ ffprobe thành VideoStreamHeader bất biến.

    Quy định chọn lọc và phân tích:
    1. payload bắt buộc là Mapping; trường streams bắt buộc là list hoặc tuple.
    2. Kiểm tra giới hạn số lượng streams <= MAX_METADATA_STREAMS (256) TRƯỚC khi duyệt.
    3. Xác thực cấu trúc toàn bộ các stream entries (index int >= 0, không trùng lặp, codec_type nonblank str).
    4. Lọc stream video hợp lệ: codec_type == 'video' và không phải attached picture (disposition.attached_pic != 1).
    5. Chọn stream:
       - selected_stream_index is None: Chọn stream video hợp lệ có index nhỏ nhất (xác định, độc lập thứ tự danh sách).
       - selected_stream_index is not None: Bắt buộc strict int >= 0; phải khớp chính xác stream video hợp lệ.
         Nếu không có hoặc chỉ định stream audio/attached picture -> ERR_NO_VIDEO_STREAM (không fallback ngầm).
    6. Phân tích chi tiết (r_frame_rate, avg_frame_rate, reported_nb_frames, time_base, codec_name) CHỈ trên
       stream đã chọn. Siêu dữ liệu của các stream không chọn không làm hỏng stream hợp lệ.
    """
    # 1. Kiểm tra cấu trúc payload gốc
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"ERR_INVALID_PROBE_PAYLOAD: payload phải là Mapping, "
            f"nhận: {type(payload).__name__ if payload is not None else 'None'}"
        )

    streams = payload.get("streams")
    if not isinstance(streams, (list, tuple)):
        raise ValueError(
            f"ERR_INVALID_PROBE_PAYLOAD: trường 'streams' phải là list hoặc tuple, "
            f"nhận: {type(streams).__name__ if streams is not None else 'None'}"
        )

    # 2. Kiểm tra độ dài streams trước khi duyệt (0 iteration nếu tràn)
    if len(streams) > MAX_METADATA_STREAMS:
        raise ValueError(
            f"ERR_PROBE_METADATA_LIMIT: số lượng streams ({len(streams)}) "
            f"vượt quá giới hạn tối đa {MAX_METADATA_STREAMS}"
        )

    if len(streams) == 0:
        raise ValueError("ERR_NO_VIDEO_STREAM: payload streams rỗng, không chứa stream nào")

    # 3. Kiểm tra tính hợp lệ cấu trúc của toàn bộ stream entries và lọc video streams
    seen_indexes: Set[int] = set()
    eligible_video_streams: list[Mapping[str, Any]] = []

    for entry in streams:
        if not isinstance(entry, Mapping):
            raise ValueError(
                f"ERR_INVALID_VIDEO_METADATA: stream entry phải là Mapping, "
                f"nhận: {type(entry).__name__}"
            )

        # 3.1. Xác thực index
        idx = entry.get("index")
        if type(idx) is not int or type(idx) is bool or idx < 0:
            raise ValueError(
                f"ERR_INVALID_VIDEO_METADATA: stream index phải là int >= 0 (không phải bool), "
                f"nhận: {type(idx).__name__ if idx is not None else 'None'}"
            )
        if idx in seen_indexes:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: phát hiện trùng lặp stream index")
        seen_indexes.add(idx)

        # 3.2. Xác thực codec_type
        codec_type = entry.get("codec_type")
        if type(codec_type) is not str:
            raise ValueError(
                f"ERR_INVALID_VIDEO_METADATA: codec_type phải là str, "
                f"nhận: {type(codec_type).__name__ if codec_type is not None else 'None'}"
            )
        _check_bounded_text(codec_type, "codec_type")
        if not codec_type or codec_type.isspace() or "\x00" in codec_type:
            raise ValueError("ERR_INVALID_VIDEO_METADATA: codec_type không được rỗng, khoảng trắng, hoặc chứa NUL")

        # 3.3. Xác thực disposition (CHỈ cho stream video; bỏ qua trên non-video streams)
        if codec_type == "video":
            is_attached_pic = False
            if "disposition" in entry:
                disposition = entry["disposition"]
                if not isinstance(disposition, Mapping):
                    raise ValueError(
                        f"ERR_INVALID_VIDEO_METADATA: disposition phải là Mapping, "
                        f"nhận: {type(disposition).__name__ if disposition is not None else 'None'}"
                    )
                if "attached_pic" in disposition:
                    attached_val = disposition["attached_pic"]
                    if type(attached_val) is not int or type(attached_val) is bool or attached_val not in (0, 1):
                        raise ValueError(
                            f"ERR_INVALID_VIDEO_METADATA: disposition.attached_pic phải là int 0 hoặc 1 (không phải bool), "
                            f"nhận: {type(attached_val).__name__ if attached_val is not None else 'None'}"
                        )
                    if attached_val == 1:
                        is_attached_pic = True
                else:
                    is_attached_pic = False
            else:
                is_attached_pic = False

            # 3.4. Lọc stream video hợp lệ
            if not is_attached_pic:
                eligible_video_streams.append(entry)

    # 4. Xác thực và chọn stream video mục tiêu
    target_entry: Optional[Mapping[str, Any]] = None

    if selected_stream_index is not None:
        if (
            type(selected_stream_index) is not int
            or type(selected_stream_index) is bool
            or selected_stream_index < 0
        ):
            raise ValueError(
                f"ERR_INVALID_STREAM_SELECTION: selected_stream_index phải là int >= 0 (không phải bool), "
                f"nhận: {type(selected_stream_index).__name__ if selected_stream_index is not None else 'None'}"
            )
        for v_entry in eligible_video_streams:
            if v_entry.get("index") == selected_stream_index:
                target_entry = v_entry
                break
        if target_entry is None:
            raise ValueError(
                "ERR_NO_VIDEO_STREAM: không tìm thấy video stream hợp lệ với index đã chọn"
            )
    else:
        if not eligible_video_streams:
            raise ValueError("ERR_NO_VIDEO_STREAM: payload không chứa video stream hợp lệ nào")
        # Chọn stream có index nhỏ nhất deterministically
        target_entry = min(eligible_video_streams, key=lambda e: e.get("index"))

    # 5. Phân tích chi tiết các trường siêu dữ liệu trên stream đã chọn
    chosen_index = target_entry["index"]
    codec_name = _parse_codec_name(target_entry.get("codec_name"))
    r_fps = _parse_rational_rate(target_entry.get("r_frame_rate"), "r_frame_rate")
    avg_fps = _parse_rational_rate(target_entry.get("avg_frame_rate"), "avg_frame_rate")
    reported_nb = _parse_reported_nb_frames(target_entry.get("nb_frames"))
    time_base = _parse_time_base(target_entry.get("time_base"))

    return VideoStreamHeader(
        stream_index=chosen_index,
        codec_name=codec_name,
        r_frame_rate=r_fps,
        avg_frame_rate=avg_fps,
        reported_nb_frames=reported_nb,
        time_base=time_base,
    )
