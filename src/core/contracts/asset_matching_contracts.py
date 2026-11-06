"""
src/core/contracts/asset_matching_contracts.py
=============================================================================
Hợp đồng dữ liệu thuần Python 3.12 cho hệ thống Tra Cứu & Khớp Tài Nguyên
Ngoại Tuyến (Offline Asset Matcher Contracts).

RÀNG BUỘC KIẾN TRÚC:
- Dùng dataclasses (frozen=True) thuần túy Python Standard Library.
- TUYỆT ĐỐI KHÔNG import PySide6 / UI / ResolveGateway / Network / Filesystem I/O.
- Bất biến (immutable), không chứa runtime paths (file_path, preview_path)
  hay executable flags trong candidate output.
=============================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Optional, Tuple


def _validate_utf8_string(val: Any, name: str, *, allow_empty: bool = False) -> str:
    """Thẩm định chuỗi UTF-8 nghiêm ngặt, chặn lone surrogates và chuỗi rỗng khi không cho phép."""
    if not isinstance(val, str):
        raise ValueError(f"{name} bắt buộc phải là chuỗi ký tự (str), nhận được: {type(val).__name__}.")
    try:
        val.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError) as exc:
        raise ValueError(f"{name} chứa ký tự UTF-8 không hợp lệ (lone surrogate): {exc}") from exc
    if not allow_empty and not val.strip():
        raise ValueError(f"{name} không được là chuỗi rỗng.")
    return val


def _validate_duration_bound(val: Any, name: str, *, allow_zero: bool = True) -> float:
    """Thẩm định chặn số thực hữu hạn, chặn bool, NaN, Inf và OverflowError từ bigint."""
    if type(val) is bool or not isinstance(val, (int, float)):
        raise ValueError(f"{name} phải là số thực hữu hạn, nhận được: {type(val).__name__}.")
    try:
        f_val = float(val)
    except OverflowError as exc:
        raise ValueError(f"{name} vượt quá giới hạn số thực hữu hạn (overflow).") from exc

    if math.isnan(f_val) or math.isinf(f_val):
        raise ValueError(f"{name} phải là số thực hữu hạn, nhận được: {val!r}.")

    if allow_zero:
        if f_val < 0.0:
            raise ValueError(f"{name} phải là số không âm hữu hạn (>= 0.0).")
    else:
        if f_val <= 0.0:
            raise ValueError(f"{name} phải là số dương hữu hạn (> 0.0).")
    return f_val


def validate_match_request(request: Any) -> None:
    """
    Thẩm định nghiêm ngặt toàn diện đối tượng OfflineAssetMatchRequest.
    Được dùng chung ở cả Request constructor (__post_init__) và match entry gate.
    Bắt buộc request phải là một instance thực sự của OfflineAssetMatchRequest (nominal type gate).
    """
    if not isinstance(request, OfflineAssetMatchRequest):
        raise ValueError(
            f"request bắt buộc phải là một instance của OfflineAssetMatchRequest, nhận được: {type(request).__name__}."
        )

    # 1. Thẩm định các trường định danh và văn bản bắt buộc
    _validate_utf8_string(request.request_id, "request_id", allow_empty=False)
    _validate_utf8_string(request.category, "category", allow_empty=False)
    _validate_utf8_string(request.query_text, "query_text", allow_empty=False)
    _validate_utf8_string(request.scene_context, "scene_context", allow_empty=True)
    _validate_utf8_string(request.emotion_or_mood, "emotion_or_mood", allow_empty=True)

    # 2. Thẩm định template_type
    tt = request.template_type
    if tt is not None:
        _validate_utf8_string(tt, "template_type", allow_empty=False)
        if tt not in ("text", "trans"):
            raise ValueError("template_type nếu có chỉ được nhận 'text' hoặc 'trans'.")

    # 3. Thẩm định subcategory_constraint
    sub_c = request.subcategory_constraint
    if sub_c is not None:
        _validate_utf8_string(sub_c, "subcategory_constraint", allow_empty=False)

    # 4. Thẩm định min/max duration
    min_d = request.min_duration_sec
    f_min: Optional[float] = None
    if min_d is not None:
        f_min = _validate_duration_bound(min_d, "min_duration_sec", allow_zero=True)

    max_d = request.max_duration_sec
    f_max: Optional[float] = None
    if max_d is not None:
        f_max = _validate_duration_bound(max_d, "max_duration_sec", allow_zero=False)

    if f_min is not None and f_max is not None:
        if f_min > f_max:
            raise ValueError("min_duration_sec không được lớn hơn max_duration_sec.")

    # 5. Thẩm định top_k
    top_k = request.top_k
    if type(top_k) is not int or top_k < 1 or top_k > 20:
        raise ValueError("top_k phải là số nguyên trong khoảng từ 1 đến 20 (không nhận bool).")

    # 6. Thẩm định allow_offline
    allow_off = request.allow_offline
    if type(allow_off) is not bool:
        raise ValueError("allow_offline phải là giá trị boolean (không nhận kiểu khác).")


@dataclass(frozen=True)
class OfflineAssetMatchRequest:
    """Yêu cầu tra cứu tài nguyên ngoại tuyến từ Planner hoặc Director State."""
    request_id: str
    category: str
    query_text: str
    scene_context: str = ""
    emotion_or_mood: str = ""
    template_type: Optional[str] = None
    subcategory_constraint: Optional[str] = None
    min_duration_sec: Optional[float] = None
    max_duration_sec: Optional[float] = None
    top_k: int = 5
    allow_offline: bool = False

    def __post_init__(self):
        validate_match_request(self)


@dataclass(frozen=True)
class OfflineAssetCandidate:
    """Ứng viên tài nguyên được chấm điểm và lọc từ kho catalog thật."""
    asset_id: str
    name: str
    category: str
    subcategory: str
    source_kind: str
    availability: str
    match_score: int
    matched_signals: Tuple[str, ...]
    duration_sec: Optional[float]
    pack_id: Optional[str]
    pack_name: str
    source_id: Optional[str] = None
    origin_library_id: Optional[str] = None
    template_type: Optional[str] = None
    is_native_guidance: bool = False
    requires_reconnect: bool = False

    def __post_init__(self):
        if type(self.match_score) is not int or self.match_score < 0 or self.match_score > 100:
            raise ValueError("match_score phải là số nguyên trong khoảng [0, 100].")
        if not isinstance(self.matched_signals, tuple):
            raise ValueError("matched_signals phải là tuple.")


@dataclass(frozen=True)
class OfflineAssetMatchResult:
    """Kết quả hoàn chỉnh của một lượt tra cứu tài nguyên ngoại tuyến."""
    request_id: str
    category: str
    query_text: str
    candidates: Tuple[OfflineAssetCandidate, ...]
    is_matched: bool
    unmatched_reason: Optional[str] = None
    suggested_action: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    rejected_record_count: int = 0

    def __post_init__(self):
        expected_matched = len(self.candidates) > 0
        if self.is_matched != expected_matched:
            raise ValueError(f"is_matched ({self.is_matched}) không khớp với candidates count ({len(self.candidates)}).")
        if self.is_matched and self.unmatched_reason is not None:
            raise ValueError("unmatched_reason bắt buộc phải là None khi is_matched=True.")
        if not self.is_matched and self.unmatched_reason is None:
            raise ValueError("unmatched_reason bắt buộc phải có khi is_matched=False.")
