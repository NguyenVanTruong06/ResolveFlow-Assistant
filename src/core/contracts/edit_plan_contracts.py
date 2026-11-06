"""Hợp đồng dữ liệu kịch bản dựng ChunDVC — CreativeContext & ReferenceItem (G06-06C1a-R1).

Module pure Python chuẩn stdlib, định nghĩa:
- ReferenceItem: Tài liệu tham khảo cục bộ độc lập (role='reference_only', media_type='image'|'video').
- CreativeContext: Ngữ cảnh sáng tạo (free_brief, style_preset, target_duration_sec, rules, references, URLs).
- Các hàm chuẩn hóa, chuyển đổi từ điển và sinh mã băm chữ ký (signature) 64-hex SHA-256.

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI/Resolve proxy.
- Immutable & owned (frozen dataclasses, defensive copies, zero runtime aliases).
- Byte budget: MAX_CREATIVE_CONTEXT_BYTES = 32768.
- Bound intake counts: MAX_REFERENCE_ITEMS = 64, MAX_REFERENCE_URLS = 64, MAX_RULES_PER_KIND = 128.
- Strict sequences (list/tuple only), strict lexical paths (POSIX, Windows drive, UNC share).
- Unified validation across constructors and public helpers (zero trusted bypass).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Optional, Tuple, Union

# Giới hạn kích thước và số lượng intake siêu dữ liệu
MAX_REFERENCE_ITEMS: int = 64
MAX_REFERENCE_URLS: int = 64
MAX_RULES_PER_KIND: int = 128
MAX_CREATIVE_CONTEXT_BYTES: int = 32768

VALID_MEDIA_TYPES: Tuple[str, ...] = ("image", "video")
VALID_REFERENCE_ROLE: str = "reference_only"

ALLOWED_CONTEXT_KEYS: frozenset[str] = frozenset({
    "free_brief",
    "style_preset",
    "target_duration_sec",
    "must_keep_rules",
    "must_not_rules",
    "reference_items",
    "reference_urls",
})

ALLOWED_REFERENCE_KEYS: frozenset[str] = frozenset({
    "reference_id",
    "local_path",
    "media_type",
    "role",
    "user_note",
})


def _validate_str(val: Any, field_name: str, allow_blank: bool = True) -> str:
    """Xác thực chuỗi UTF-8 hợp lệ, không chứa ký tự NUL hoặc lone surrogates."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8 (lone surrogate): {e}") from e
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống hoặc chỉ chứa khoảng trắng")
    return val


def _is_lexical_absolute(p: str) -> bool:
    """Kiểm tra đường dẫn tuyệt đối thuần túy cú pháp (POSIX hoặc Windows/UNC share, zero IO)."""
    if type(p) is not str or not p.strip():
        return False
    # Nếu bắt đầu bằng // hoặc \\, đây là đường dẫn mạng/UNC: bắt buộc phải có đầy đủ server và share
    if p.startswith(("//", "\\\\")):
        win = PureWindowsPath(p)
        parts = [part for part in win.drive.replace("/", "\\").split("\\") if part]
        return win.is_absolute() and len(parts) >= 2
    return PurePosixPath(p).is_absolute() or PureWindowsPath(p).is_absolute()


def _validate_duration(dur: Any) -> Optional[Union[int, float]]:
    """Xác thực thời lượng mục tiêu: None hoặc số dương hữu hạn (int/float).
    
    Quy định:
    - Chặn bool, non-int-float, zero, negative, NaN, Inf.
    - Bọc phép chuyển/finite check có khả năng overflow TRƯỚC math.isnan/isinf; 10**400 và -10**400
      phải trả về controlled ValueError, không để rò rỉ OverflowError.
    - Finite positive int (kể cả 10**20) hợp lệ nếu serialized context nằm trong byte budget.
    """
    if dur is None:
        return None
    if isinstance(dur, bool) or not isinstance(dur, (int, float)):
        raise ValueError(f"ERR_INVALID_DURATION: target_duration_sec phải là int, float hoặc None, nhận: {type(dur).__name__}")

    if isinstance(dur, int):
        if dur <= 0:
            raise ValueError(f"ERR_INVALID_DURATION: target_duration_sec phải là số dương (> 0), nhận: {dur}")
        try:
            dur_float = float(dur)
            if math.isinf(dur_float):
                raise ValueError("ERR_DURATION_OVERFLOW: target_duration_sec vượt quá giới hạn số học float")
        except OverflowError as e:
            raise ValueError(f"ERR_DURATION_OVERFLOW: target_duration_sec vượt quá giới hạn số học: {e}") from e
        return dur

    # dur is float
    if math.isnan(dur) or math.isinf(dur):
        raise ValueError(f"ERR_INVALID_DURATION: target_duration_sec không được là NaN hoặc Inf, nhận: {dur}")
    if dur <= 0.0:
        raise ValueError(f"ERR_INVALID_DURATION: target_duration_sec phải là số dương (> 0), nhận: {dur}")
    return dur


def _validate_sequence_type(seq_val: Any, seq_name: str) -> None:
    """Yêu cầu chuỗi nghiêm ngặt là list hoặc tuple (loại trừ range, set, dict, str, bytes, bool, None)."""
    if isinstance(seq_val, (str, bytes, Mapping)) or not isinstance(seq_val, (list, tuple)):
        raise ValueError(f"ERR_INVALID_SEQUENCE: {seq_name} phải là list hoặc tuple, nhận: {type(seq_val).__name__}")


@dataclass(frozen=True)
class ReferenceItem:
    """Tài liệu tham khảo độc lập do người dùng cung cấp (Immutable Value Object).
    
    Quy định:
    - role bắt buộc là 'reference_only'. Tuyệt đối không dùng làm footage hay broll cắt dựng.
    - media_type chỉ nhận 'image' hoặc 'video'.
    - local_path là đường dẫn tuyệt đối cú pháp (POSIX, Windows drive, UNC share), không filesystem I/O.
    """
    reference_id: str
    local_path: str
    media_type: str
    role: str = VALID_REFERENCE_ROLE
    user_note: str = ""

    def __post_init__(self):
        # 1. Kiểm tra reference_id
        ref_id = _validate_str(self.reference_id, "reference_id", allow_blank=False)
        object.__setattr__(self, "reference_id", ref_id)

        # 2. Kiểm tra local_path
        path_str = _validate_str(self.local_path, "local_path", allow_blank=False)
        if not _is_lexical_absolute(path_str):
            raise ValueError(f"ERR_RELATIVE_PATH: local_path phải là đường dẫn tuyệt đối (POSIX/Windows/UNC), nhận: {path_str!r}")
        object.__setattr__(self, "local_path", path_str)

        # 3. Kiểm tra media_type (strict str)
        if type(self.media_type) is not str:
            raise ValueError(f"ERR_INVALID_MEDIA_TYPE: media_type phải là str, nhận: {type(self.media_type).__name__}")
        if self.media_type not in VALID_MEDIA_TYPES:
            raise ValueError(f"ERR_INVALID_MEDIA_TYPE: media_type phải thuộc {VALID_MEDIA_TYPES}, nhận: {self.media_type!r}")

        # 4. Kiểm tra role (strict str)
        if type(self.role) is not str:
            raise ValueError(f"ERR_INVALID_ROLE: role phải là str, nhận: {type(self.role).__name__}")
        if self.role != VALID_REFERENCE_ROLE:
            raise ValueError(f"ERR_INVALID_ROLE: role bắt buộc là {VALID_REFERENCE_ROLE!r}, nhận: {self.role!r}")

        # 5. Kiểm tra user_note (cho phép để trống)
        note_str = _validate_str(self.user_note, "user_note", allow_blank=True)
        object.__setattr__(self, "user_note", note_str)


def _revalidate_reference_item(item: Any, idx: Optional[int] = None) -> ReferenceItem:
    """Xác thực và tạo mới owned ReferenceItem độc lập (chặn typed bypass và alias)."""
    idx_str = f"[{idx}]" if idx is not None else ""
    if isinstance(item, ReferenceItem):
        return ReferenceItem(
            reference_id=item.reference_id,
            local_path=item.local_path,
            media_type=item.media_type,
            role=item.role,
            user_note=item.user_note,
        )
    if isinstance(item, (dict, Mapping)):
        # Kiểm tra unknown keys an toàn với mixed types
        bad_keys = sorted(repr(k) for k in item.keys() if not isinstance(k, str) or k not in ALLOWED_REFERENCE_KEYS)
        if bad_keys:
            raise ValueError(f"ERR_UNKNOWN_KEY: reference_items{idx_str} chứa unknown hoặc non-str keys: {bad_keys}")
        return ReferenceItem(
            reference_id=item.get("reference_id", ""),
            local_path=item.get("local_path", ""),
            media_type=item.get("media_type", ""),
            role=item.get("role", VALID_REFERENCE_ROLE),
            user_note=item.get("user_note", ""),
        )
    raise ValueError(f"ERR_INVALID_REFERENCE: reference_items{idx_str} phải là ReferenceItem hoặc Mapping, nhận: {type(item).__name__}")


def _serialize_canonical_dict(
    free_brief: str,
    style_preset: str,
    target_duration_sec: Optional[Union[int, float]],
    must_keep_rules: Tuple[str, ...],
    must_not_rules: Tuple[str, ...],
    reference_items: Tuple[ReferenceItem, ...],
    reference_urls: Tuple[str, ...],
) -> dict[str, Any]:
    """Tạo dictionary chứa JSON primitives nguyên bản (defensive copy, chuẩn canonical)."""
    return {
        "free_brief": free_brief,
        "style_preset": style_preset,
        "target_duration_sec": target_duration_sec,
        "must_keep_rules": list(must_keep_rules),
        "must_not_rules": list(must_not_rules),
        "reference_items": [
            {
                "reference_id": r.reference_id,
                "local_path": r.local_path,
                "media_type": r.media_type,
                "role": r.role,
                "user_note": r.user_note,
            }
            for r in reference_items
        ],
        "reference_urls": list(reference_urls),
    }


def _encode_canonical_json(canonical_dict: dict[str, Any]) -> bytes:
    """Mã hóa JSON canonical UTF-8 chuẩn xác."""
    return json.dumps(
        canonical_dict,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class CreativeContext:
    """Ngữ cảnh sáng tạo kịch bản dùng chung cho Web, Local và API (Immutable Value Object).
    
    Quy định:
    - free_brief có thẩm quyền cao nhất khi xảy ra mâu thuẫn yêu cầu.
    - empty style_preset và duration None biểu thị chưa thiết lập (không tự suy diễn mặc định).
    - Sequence chỉ nhận list hoặc tuple.
    - Bounds kiểm tra độ dài bằng len() trước lặp và kích thước UTF-8 tuần tự.
    """
    free_brief: str = ""
    style_preset: str = ""
    target_duration_sec: Optional[Union[int, float]] = None
    must_keep_rules: Tuple[str, ...] = field(default_factory=tuple)
    must_not_rules: Tuple[str, ...] = field(default_factory=tuple)
    reference_items: Tuple[ReferenceItem, ...] = field(default_factory=tuple)
    reference_urls: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        # 1. free_brief & style_preset (cho phép rỗng)
        brief_str = _validate_str(self.free_brief, "free_brief", allow_blank=True)
        object.__setattr__(self, "free_brief", brief_str)

        preset_str = _validate_str(self.style_preset, "style_preset", allow_blank=True)
        object.__setattr__(self, "style_preset", preset_str)

        # 2. target_duration_sec
        dur = _validate_duration(self.target_duration_sec)
        object.__setattr__(self, "target_duration_sec", dur)

        # 3. Pre-check sequence types & length bounds BEFORE ANY iteration
        for seq_name, seq_val, max_count in (
            ("must_keep_rules", self.must_keep_rules, MAX_RULES_PER_KIND),
            ("must_not_rules", self.must_not_rules, MAX_RULES_PER_KIND),
            ("reference_items", self.reference_items, MAX_REFERENCE_ITEMS),
            ("reference_urls", self.reference_urls, MAX_REFERENCE_URLS),
        ):
            _validate_sequence_type(seq_val, seq_name)
            if len(seq_val) > max_count:
                raise ValueError(f"ERR_COUNT_OVERFLOW: {seq_name} có {len(seq_val)} phần tử, vượt quá giới hạn tối đa (exceeds maximum {max_count})")

        # 4. must_keep_rules & must_not_rules validation & ownership
        owned_keep = tuple(_validate_str(r, "must_keep_rules item", allow_blank=False) for r in self.must_keep_rules)
        object.__setattr__(self, "must_keep_rules", owned_keep)

        owned_not = tuple(_validate_str(r, "must_not_rules item", allow_blank=False) for r in self.must_not_rules)
        object.__setattr__(self, "must_not_rules", owned_not)

        # 5. reference_items validation & unique IDs (revalidate each item to prevent typed bypass)
        seen_ref_ids = set()
        owned_refs = []
        for idx, raw_item in enumerate(self.reference_items):
            item = _revalidate_reference_item(raw_item, idx=idx)
            if item.reference_id in seen_ref_ids:
                raise ValueError(f"ERR_DUPLICATE_REFERENCE_ID: duplicate reference_id phát hiện: {item.reference_id!r}")
            seen_ref_ids.add(item.reference_id)
            owned_refs.append(item)
        object.__setattr__(self, "reference_items", tuple(owned_refs))

        # 6. reference_urls validation & ownership
        owned_urls = tuple(_validate_str(u, "reference_urls item", allow_blank=False) for u in self.reference_urls)
        object.__setattr__(self, "reference_urls", owned_urls)

        # 7. Check byte budget on canonical UTF-8 JSON representation
        canonical_dict = _serialize_canonical_dict(
            free_brief=self.free_brief,
            style_preset=self.style_preset,
            target_duration_sec=self.target_duration_sec,
            must_keep_rules=self.must_keep_rules,
            must_not_rules=self.must_not_rules,
            reference_items=self.reference_items,
            reference_urls=self.reference_urls,
        )
        raw_bytes = _encode_canonical_json(canonical_dict)
        if len(raw_bytes) > MAX_CREATIVE_CONTEXT_BYTES:
            raise ValueError(f"ERR_BYTE_BUDGET_OVERFLOW: Kích thước context {len(raw_bytes)} bytes vượt quá MAX_CREATIVE_CONTEXT_BYTES ({MAX_CREATIVE_CONTEXT_BYTES} bytes)")


def _revalidate_creative_context(context: Any) -> CreativeContext:
    """Tái xác thực contexttyped để bảo đảm không bypass validation hoặc giữ trạng thái mutate."""
    if not isinstance(context, CreativeContext):
        raise ValueError(f"ERR_INVALID_CONTEXT: Yêu cầu CreativeContext hợp lệ, nhận: {type(context).__name__}")
    return CreativeContext(
        free_brief=context.free_brief,
        style_preset=context.style_preset,
        target_duration_sec=context.target_duration_sec,
        must_keep_rules=context.must_keep_rules,
        must_not_rules=context.must_not_rules,
        reference_items=context.reference_items,
        reference_urls=context.reference_urls,
    )


def creative_context_to_dict(context: CreativeContext) -> dict[str, Any]:
    """Chuyển đổi CreativeContext thành dictionary chứa JSON primitives tươi mới (defensive copy)."""
    validated = _revalidate_creative_context(context)
    return _serialize_canonical_dict(
        free_brief=validated.free_brief,
        style_preset=validated.style_preset,
        target_duration_sec=validated.target_duration_sec,
        must_keep_rules=validated.must_keep_rules,
        must_not_rules=validated.must_not_rules,
        reference_items=validated.reference_items,
        reference_urls=validated.reference_urls,
    )


def compute_creative_context_signature(context: CreativeContext) -> str:
    """Tính toán mã băm SHA-256 (64 ký tự hex thường) chuẩn tắc của toàn bộ dữ liệu CreativeContext.
    
    Quy định:
    - Hoàn toàn tất định (deterministic), phụ thuộc 100% vào nội dung các trường.
    - Re-validate context trước khi sinh digest (ngăn chặn context lỗi/oversized).
    - Bảo toàn thứ tự danh sách rules, reference_items và reference_urls.
    - Zero I/O, không gọi hash tệp vật lý, không sinh thời gian ngẫu nhiên.
    """
    validated = _revalidate_creative_context(context)
    canonical_dict = _serialize_canonical_dict(
        free_brief=validated.free_brief,
        style_preset=validated.style_preset,
        target_duration_sec=validated.target_duration_sec,
        must_keep_rules=validated.must_keep_rules,
        must_not_rules=validated.must_not_rules,
        reference_items=validated.reference_items,
        reference_urls=validated.reference_urls,
    )
    raw_bytes = _encode_canonical_json(canonical_dict)
    return hashlib.sha256(raw_bytes).hexdigest().lower()


def normalize_creative_context(raw: Union[Mapping[str, Any], CreativeContext]) -> CreativeContext:
    """Chuẩn hóa mapping thô hoặc CreativeContext thành instance bất biến đã được xác thực an toàn.
    
    Quy định:
    - Nếu nhận CreativeContext typed: revalidate toàn bộ và tạo instance mới (không trả về reference raw).
    - Reject mọi unknown keys (an toàn với mixed-type keys) trong mapping gốc.
    - Precheck len của sequences TRƯỚC mọi thao tác copy/iteration (CountedList overflow có 0 iterations).
    - Chuyển đổi các dict con thành ReferenceItem độc lập và revalidate.
    - Phòng thủ sao chép: mutation trên input collection sau khi chuẩn hóa không làm đổi kết quả.
    """
    if isinstance(raw, CreativeContext):
        return _revalidate_creative_context(raw)

    if not isinstance(raw, (dict, Mapping)):
        raise ValueError(f"ERR_INVALID_MAPPING: raw input phải là Mapping hoặc CreativeContext, nhận: {type(raw).__name__}")

    # 1. Kiểm tra unknown keys an toàn với mixed types
    bad_keys = sorted(repr(k) for k in raw.keys() if not isinstance(k, str) or k not in ALLOWED_CONTEXT_KEYS)
    if bad_keys:
        raise ValueError(f"ERR_UNKNOWN_KEY: raw mapping chứa unknown hoặc non-str keys: {bad_keys}")

    # 2. Pre-check sequence types & length bounds TRƯỚC BẤT KỲ iteration/copy nào
    for seq_name, max_count in (
        ("must_keep_rules", MAX_RULES_PER_KIND),
        ("must_not_rules", MAX_RULES_PER_KIND),
        ("reference_items", MAX_REFERENCE_ITEMS),
        ("reference_urls", MAX_REFERENCE_URLS),
    ):
        if seq_name in raw:
            val = raw[seq_name]
            _validate_sequence_type(val, seq_name)
            if len(val) > max_count:
                raise ValueError(f"ERR_COUNT_OVERFLOW: {seq_name} có {len(val)} phần tử, vượt quá giới hạn tối đa (exceeds maximum {max_count})")

    # 3. Trích xuất và revalidate reference_items
    raw_refs = raw.get("reference_items", ())
    parsed_refs = tuple(_revalidate_reference_item(item, idx=idx) for idx, item in enumerate(raw_refs))

    # 4. Trích xuất rules và urls (không copy hay iterate nếu đã prechecked)
    raw_keep = raw.get("must_keep_rules", ())
    raw_not = raw.get("must_not_rules", ())
    raw_urls = raw.get("reference_urls", ())

    return CreativeContext(
        free_brief=raw.get("free_brief", ""),
        style_preset=raw.get("style_preset", ""),
        target_duration_sec=raw.get("target_duration_sec", None),
        must_keep_rules=tuple(raw_keep),
        must_not_rules=tuple(raw_not),
        reference_items=parsed_refs,
        reference_urls=tuple(raw_urls),
    )
