"""Hợp đồng DTO Bất biến Biên bản Tiền Đột biến & Nhật ký Thao tác (G07-07A6-I).

Module pure Python chuẩn stdlib, định nghĩa:
- ExecutionReceipt: Frozen DTO biểu diễn biên bản nhận chứng tiền đột biến bất biến.
- JournalEntry: Frozen DTO biểu diễn bản ghi nhật ký thao tác thi công append-only.
- SourceFileDigestBinding & CatalogAssetDigestBinding: Ràng buộc bằng chứng băm 5-stat/SHA.
- TargetTrackReservation: Đăng ký đặt chỗ track đích trên timeline Resolve.
- TornTailDiagnostic & JournalReplayResult: Chẩn đoán dòng cuối bị rách phi phá hủy.
- compute_idempotency_key: Hàm dẫn xuất khóa idempotency bền vững từ nội dung phê duyệt.
- Serialize / deserialize JSON/JSONL với giới hạn kích thước trần (bounded).

Tuân thủ nghiêm ngặt:
- Zero IO, zero network, zero Qt/UI, zero Resolve scripting API.
- Bất biến & sở hữu độc lập (frozen dataclass, tuple, zero mutable aliases).
- Giới hạn trần dung lượng: MAX_RECEIPT_BYTES (5 MiB), MAX_LINE_BYTES (64 KiB), MAX_JOURNAL_RECORDS (50,000).
- Khóa idempotency bền vững: ngăn chặn chạy trùng lặp kể cả khi client retry bằng UUID mới.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

SCHEMA_VERSION: int = 2

# Giới hạn trần kích thước tệp và bộ nhớ để phòng vệ DoS/OOM
MAX_RECEIPT_BYTES: int = 5 * 1024 * 1024       # 5 MiB
MAX_LINE_BYTES: int = 64 * 1024               # 64 KiB
MAX_JOURNAL_BYTES: int = 50 * 1024 * 1024     # 50 MiB
MAX_JOURNAL_RECORDS: int = 50_000             # 50,000 bản ghi
MAX_TRACK_RESERVATIONS: int = 64
MAX_SOURCE_DIGESTS: int = 1024
MAX_CATALOG_DIGESTS: int = 1024

RE_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_UUID_OR_ID: re.Pattern = re.compile(r"^[a-zA-Z0-9_\-]+$")
RE_ISO_TIMESTAMP: re.Pattern = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)

VALID_RECEIPT_STATES: Tuple[str, ...] = (
    "INITIALIZED",  # Biên bản đã tạo và ghi đĩa, chưa phát lệnh Resolve API
    "EXECUTING",    # Đang thực thi các bước đột biến
    "COMPLETED",    # Toàn bộ kế hoạch thi công thành công 100%
    "ROLLED_BACK",  # Thất bại và đã hoàn tất hoàn tác bù trừ (lift delete)
    "ABORTED",      # Hủy bỏ trước khi đột biến hoặc do lỗi không thể khắc phục
)

VALID_JOURNAL_STAGES: Tuple[str, ...] = (
    "START",
    "ACTION_PENDING",
    "ACTION_COMMITTED",
    "ACTION_FAILED",
    "ROLLBACK_PENDING",
    "ROLLBACK_DONE",
    "ROLLBACK_FAILED",
    "FINISH",
    "RECOVERY_NOTE",
)

VALID_ACTION_TYPES: Tuple[str, ...] = (
    "RESERVE_TRACK",
    "IMPORT_CARRIER",
    "INSERT_CLIP",
    "IMPORT_FUSION_COMP",
    "SET_CLIP_COLOR",
    "NO_OP",
)


def _validate_str(val: Any, field_name: str, allow_blank: bool = False, max_len: int = 4096) -> str:
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là str, nhận: {type(val).__name__}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL")
    try:
        val.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8: {exc}") from exc
    if len(val) > max_len:
        raise ValueError(f"ERR_STRING_TOO_LONG: {field_name} vượt quá giới hạn {max_len} ký tự (nhận {len(val)})")
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống")
    return val


def _validate_int(val: Any, field_name: str, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    if type(val) is not int or type(val) is bool:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là int (không chấp nhận bool), nhận: {type(val).__name__}")
    if min_val is not None and val < min_val:
        raise ValueError(f"ERR_INT_OUT_OF_RANGE: {field_name} ({val}) < giá trị tối thiểu {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"ERR_INT_OUT_OF_RANGE: {field_name} ({val}) > giá trị tối đa {max_val}")
    return val


def _validate_iso_timestamp(val: Any, field_name: str) -> str:
    s = _validate_str(val, field_name, allow_blank=False)
    if not RE_ISO_TIMESTAMP.match(s):
        raise ValueError(f"ERR_INVALID_TIMESTAMP: {field_name} không đúng định dạng ISO-8601: {s!r}")
    return s


def compute_idempotency_key(
    proposal_id: str,
    proposal_revision: str,
    approval_id: str,
    project_id: str,
    timeline_id: str,
) -> str:
    """Dẫn xuất khóa idempotency bền vững (SHA-256) từ thông tin phê duyệt và đích Resolve.

    Ngăn chặn việc client vô tình gọi lại hoặc retry bằng execution UUID mới đối với
    cùng một nội dung đã duyệt và cùng một timeline đích.
    """
    p_id = _validate_str(proposal_id, "proposal_id")
    if type(proposal_revision) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: proposal_revision phải là str, nhận: {type(proposal_revision).__name__}")
    if not RE_HEX_64.match(proposal_revision):
        raise ValueError(f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision phải là 64-hex ký tự thường: {proposal_revision!r}")
    a_id = _validate_str(approval_id, "approval_id")
    proj_id = _validate_str(project_id, "project_id")
    tl_id = _validate_str(timeline_id, "timeline_id")

    payload = f"{p_id}:{proposal_revision}:{a_id}:{proj_id}:{tl_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SourceFileDigestBinding:
    """Ràng buộc kiểm chứng toàn vẹn của tệp video nguồn tại thời điểm thi công."""
    clip_id: str
    absolute_path: str
    full_sha256: str
    stream_index: int
    stat_identity: Tuple[int, int, int, int, int]  # (dev, ino, size, mtime_ns, ctime_ns)

    def __post_init__(self):
        _validate_str(self.clip_id, "clip_id")
        _validate_str(self.absolute_path, "absolute_path")
        s = _validate_str(self.full_sha256, "full_sha256").lower()
        if not RE_HEX_64.match(s):
            raise ValueError(f"ERR_INVALID_SHA256: full_sha256 phải là 64-hex ký tự thường: {s!r}")
        _validate_int(self.stream_index, "stream_index", min_val=0)
        if type(self.stat_identity) is not tuple or len(self.stat_identity) != 5:
            raise ValueError("ERR_INVALID_STAT_IDENTITY: stat_identity phải là tuple 5 phần tử (dev, ino, size, mtime_ns, ctime_ns)")
        for idx, item in enumerate(self.stat_identity):
            _validate_int(item, f"stat_identity[{idx}]", min_val=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "absolute_path": self.absolute_path,
            "full_sha256": self.full_sha256,
            "stream_index": self.stream_index,
            "stat_identity": list(self.stat_identity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceFileDigestBinding:
        if type(data) is not dict:
            raise ValueError("SourceFileDigestBinding data phải là dict")
        raw_stat = data.get("stat_identity")
        if not isinstance(raw_stat, (list, tuple)) or len(raw_stat) != 5:
            raise ValueError("stat_identity phải là list hoặc tuple 5 phần tử")
        return cls(
            clip_id=str(data.get("clip_id", "")),
            absolute_path=str(data.get("absolute_path", "")),
            full_sha256=str(data.get("full_sha256", "")),
            stream_index=int(data.get("stream_index", 0)),
            stat_identity=tuple(int(x) for x in raw_stat),
        )


@dataclass(frozen=True)
class CatalogAssetDigestBinding:
    """Ràng buộc kiểm chứng tài nguyên catalog (B-roll, SFX, Title Macro)."""
    asset_id: str
    absolute_path: str
    full_sha256: str
    stat_identity: Tuple[int, int, int, int, int]

    def __post_init__(self):
        _validate_str(self.asset_id, "asset_id")
        _validate_str(self.absolute_path, "absolute_path")
        s = _validate_str(self.full_sha256, "full_sha256").lower()
        if not RE_HEX_64.match(s):
            raise ValueError(f"ERR_INVALID_SHA256: full_sha256 phải là 64-hex ký tự thường: {s!r}")
        if type(self.stat_identity) is not tuple or len(self.stat_identity) != 5:
            raise ValueError("ERR_INVALID_STAT_IDENTITY: stat_identity phải là tuple 5 phần tử")
        for idx, item in enumerate(self.stat_identity):
            _validate_int(item, f"stat_identity[{idx}]", min_val=0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "absolute_path": self.absolute_path,
            "full_sha256": self.full_sha256,
            "stat_identity": list(self.stat_identity),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CatalogAssetDigestBinding:
        if type(data) is not dict:
            raise ValueError("CatalogAssetDigestBinding data phải là dict")
        raw_stat = data.get("stat_identity")
        if not isinstance(raw_stat, (list, tuple)) or len(raw_stat) != 5:
            raise ValueError("stat_identity phải là list hoặc tuple 5 phần tử")
        return cls(
            asset_id=str(data.get("asset_id", "")),
            absolute_path=str(data.get("absolute_path", "")),
            full_sha256=str(data.get("full_sha256", "")),
            stat_identity=tuple(int(x) for x in raw_stat),
        )


@dataclass(frozen=True)
class TargetTrackReservation:
    """Đăng ký giữ chỗ trên track đích của Resolve."""
    track_type: str  # "video" | "audio"
    track_index: int
    reserved_start_frame: int
    reserved_end_frame: int
    intended_usage: str

    def __post_init__(self):
        tt = _validate_str(self.track_type, "track_type").lower()
        if tt not in ("video", "audio"):
            raise ValueError(f"ERR_INVALID_TRACK_TYPE: track_type phải là 'video' hoặc 'audio', nhận: {tt!r}")
        _validate_int(self.track_index, "track_index", min_val=1)
        _validate_int(self.reserved_start_frame, "reserved_start_frame", min_val=0)
        _validate_int(self.reserved_end_frame, "reserved_end_frame", min_val=self.reserved_start_frame)
        _validate_str(self.intended_usage, "intended_usage")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_type": self.track_type,
            "track_index": self.track_index,
            "reserved_start_frame": self.reserved_start_frame,
            "reserved_end_frame": self.reserved_end_frame,
            "intended_usage": self.intended_usage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TargetTrackReservation:
        if type(data) is not dict:
            raise ValueError("TargetTrackReservation data phải là dict")
        return cls(
            track_type=str(data.get("track_type", "")),
            track_index=int(data.get("track_index", 1)),
            reserved_start_frame=int(data.get("reserved_start_frame", 0)),
            reserved_end_frame=int(data.get("reserved_end_frame", 0)),
            intended_usage=str(data.get("intended_usage", "")),
        )


@dataclass(frozen=True)
class ExecutionReceipt:
    """Biên bản nhận chứng tiền đột biến bất biến (Pre-Mutation Receipt)."""
    execution_id: str
    idempotency_key: str
    proposal_id: str
    proposal_revision: str
    approval_id: str
    approved_at_iso: str
    project_name: str
    project_id: str
    timeline_name: str
    timeline_id: str
    timeline_fps_numerator: int
    timeline_fps_denominator: int
    timeline_resolution: Tuple[int, int]
    source_digests: Tuple[SourceFileDigestBinding, ...]
    catalog_digests: Tuple[CatalogAssetDigestBinding, ...]
    track_reservations: Tuple[TargetTrackReservation, ...]
    planned_actions_count: int
    created_at_iso: str
    state: str = "INITIALIZED"
    schema_version: int = SCHEMA_VERSION
    completed_at_iso: Optional[str] = None
    last_error_code: str = ""
    last_error_message: str = ""

    def __post_init__(self):
        _validate_str(self.execution_id, "execution_id")
        ik = _validate_str(self.idempotency_key, "idempotency_key").lower()
        if not RE_HEX_64.match(ik):
            raise ValueError(f"ERR_INVALID_IDEMPOTENCY_KEY: idempotency_key phải là 64-hex hash: {ik!r}")
        _validate_str(self.proposal_id, "proposal_id")
        if type(self.proposal_revision) is not str:
            raise ValueError(f"ERR_INVALID_TYPE: proposal_revision phải là str, nhận: {type(self.proposal_revision).__name__}")
        if not RE_HEX_64.match(self.proposal_revision):
            raise ValueError(f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision phải là 64-hex ký tự thường: {self.proposal_revision!r}")
        _validate_str(self.approval_id, "approval_id")
        _validate_iso_timestamp(self.approved_at_iso, "approved_at_iso")
        _validate_str(self.project_name, "project_name")
        _validate_str(self.project_id, "project_id")
        _validate_str(self.timeline_name, "timeline_name")
        _validate_str(self.timeline_id, "timeline_id")
        _validate_int(self.timeline_fps_numerator, "timeline_fps_numerator", min_val=1)
        _validate_int(self.timeline_fps_denominator, "timeline_fps_denominator", min_val=1)
        if type(self.timeline_resolution) is not tuple or len(self.timeline_resolution) != 2:
            raise ValueError("ERR_INVALID_RESOLUTION: timeline_resolution phải là tuple (width, height)")
        _validate_int(self.timeline_resolution[0], "width", min_val=1)
        _validate_int(self.timeline_resolution[1], "height", min_val=1)

        if type(self.source_digests) is not tuple:
            raise ValueError("ERR_INVALID_DIGESTS: source_digests phải là tuple")
        if len(self.source_digests) > MAX_SOURCE_DIGESTS:
            raise ValueError(f"ERR_TOO_MANY_SOURCES: source_digests vượt quá giới hạn {MAX_SOURCE_DIGESTS}")
        for item in self.source_digests:
            if not isinstance(item, SourceFileDigestBinding):
                raise ValueError("ERR_INVALID_TYPE: item trong source_digests phải là SourceFileDigestBinding")

        if type(self.catalog_digests) is not tuple:
            raise ValueError("ERR_INVALID_DIGESTS: catalog_digests phải là tuple")
        if len(self.catalog_digests) > MAX_CATALOG_DIGESTS:
            raise ValueError(f"ERR_TOO_MANY_CATALOGS: catalog_digests vượt quá giới hạn {MAX_CATALOG_DIGESTS}")
        for item in self.catalog_digests:
            if not isinstance(item, CatalogAssetDigestBinding):
                raise ValueError("ERR_INVALID_TYPE: item trong catalog_digests phải là CatalogAssetDigestBinding")

        if type(self.track_reservations) is not tuple:
            raise ValueError("ERR_INVALID_RESERVATIONS: track_reservations phải là tuple")
        if len(self.track_reservations) > MAX_TRACK_RESERVATIONS:
            raise ValueError(f"ERR_TOO_MANY_TRACKS: track_reservations vượt quá giới hạn {MAX_TRACK_RESERVATIONS}")
        for item in self.track_reservations:
            if not isinstance(item, TargetTrackReservation):
                raise ValueError("ERR_INVALID_TYPE: item trong track_reservations phải là TargetTrackReservation")

        _validate_int(self.planned_actions_count, "planned_actions_count", min_val=0)
        _validate_iso_timestamp(self.created_at_iso, "created_at_iso")

        if self.state not in VALID_RECEIPT_STATES:
            raise ValueError(f"ERR_INVALID_RECEIPT_STATE: state {self.state!r} không nằm trong {VALID_RECEIPT_STATES}")
        if type(self.schema_version) is not int or type(self.schema_version) is bool:
            raise ValueError(f"ERR_INVALID_TYPE: schema_version phải là int (không chấp nhận bool), nhận: {type(self.schema_version).__name__}")
        if self.schema_version == 1:
            raise ValueError("ERR_LEGACY_SCHEMA_VERSION: schema_version 1 không còn được hỗ trợ (fail-closed, yêu cầu version 2)")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version phải là {SCHEMA_VERSION}, nhận: {self.schema_version}")

        if self.completed_at_iso is not None:
            _validate_iso_timestamp(self.completed_at_iso, "completed_at_iso")
        if self.last_error_code:
            _validate_str(self.last_error_code, "last_error_code")
        if self.last_error_message:
            _validate_str(self.last_error_message, "last_error_message", allow_blank=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "idempotency_key": self.idempotency_key,
            "proposal_id": self.proposal_id,
            "proposal_revision": self.proposal_revision,
            "approval_id": self.approval_id,
            "approved_at_iso": self.approved_at_iso,
            "project_name": self.project_name,
            "project_id": self.project_id,
            "timeline_name": self.timeline_name,
            "timeline_id": self.timeline_id,
            "timeline_fps": {
                "numerator": self.timeline_fps_numerator,
                "denominator": self.timeline_fps_denominator,
            },
            "timeline_resolution": list(self.timeline_resolution),
            "source_digests": [item.to_dict() for item in self.source_digests],
            "catalog_digests": [item.to_dict() for item in self.catalog_digests],
            "track_reservations": [item.to_dict() for item in self.track_reservations],
            "planned_actions_count": self.planned_actions_count,
            "created_at_iso": self.created_at_iso,
            "state": self.state,
            "completed_at_iso": self.completed_at_iso,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionReceipt:
        if type(data) is not dict:
            raise ValueError("ExecutionReceipt data phải là dict")

        if "schema_version" not in data:
            raise ValueError("ERR_MISSING_SCHEMA_VERSION: schema_version bắt buộc phải có")
        raw_schema_version = data["schema_version"]
        if type(raw_schema_version) is not int or type(raw_schema_version) is bool:
            raise ValueError(f"ERR_INVALID_TYPE: schema_version phải là int (không chấp nhận bool), nhận: {type(raw_schema_version).__name__}")
        if raw_schema_version == 1:
            raise ValueError("ERR_LEGACY_SCHEMA_VERSION: schema_version 1 không còn được hỗ trợ (fail-closed, yêu cầu version 2)")
        if raw_schema_version != SCHEMA_VERSION:
            raise ValueError(f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version phải là {SCHEMA_VERSION}, nhận: {raw_schema_version}")

        fps_dict = data.get("timeline_fps", {})
        if not isinstance(fps_dict, dict):
            raise ValueError("timeline_fps phải là dict có numerator và denominator")

        res_raw = data.get("timeline_resolution", (1920, 1080))
        if not isinstance(res_raw, (list, tuple)) or len(res_raw) != 2:
            raise ValueError("timeline_resolution phải là list/tuple 2 phần tử")

        raw_sources = data.get("source_digests", [])
        if not isinstance(raw_sources, list):
            raise ValueError("source_digests phải là list")
        source_digests = tuple(SourceFileDigestBinding.from_dict(x) for x in raw_sources)

        raw_catalogs = data.get("catalog_digests", [])
        if not isinstance(raw_catalogs, list):
            raise ValueError("catalog_digests phải là list")
        catalog_digests = tuple(CatalogAssetDigestBinding.from_dict(x) for x in raw_catalogs)

        raw_tracks = data.get("track_reservations", [])
        if not isinstance(raw_tracks, list):
            raise ValueError("track_reservations phải là list")
        track_reservations = tuple(TargetTrackReservation.from_dict(x) for x in raw_tracks)

        raw_rev = data.get("proposal_revision")
        if type(raw_rev) is not str:
            raise ValueError(f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision phải là 64-hex ký tự thường (nhận non-str: {type(raw_rev).__name__})")
        if not RE_HEX_64.match(raw_rev):
            raise ValueError(f"ERR_INVALID_PROPOSAL_REVISION: proposal_revision phải là 64-hex ký tự thường: {raw_rev!r}")

        return cls(
            schema_version=raw_schema_version,
            execution_id=str(data.get("execution_id", "")),
            idempotency_key=str(data.get("idempotency_key", "")),
            proposal_id=str(data.get("proposal_id", "")),
            proposal_revision=raw_rev,
            approval_id=str(data.get("approval_id", "")),
            approved_at_iso=str(data.get("approved_at_iso", "")),
            project_name=str(data.get("project_name", "")),
            project_id=str(data.get("project_id", "")),
            timeline_name=str(data.get("timeline_name", "")),
            timeline_id=str(data.get("timeline_id", "")),
            timeline_fps_numerator=int(fps_dict.get("numerator", 0)),
            timeline_fps_denominator=int(fps_dict.get("denominator", 0)),
            timeline_resolution=(int(res_raw[0]), int(res_raw[1])),
            source_digests=source_digests,
            catalog_digests=catalog_digests,
            track_reservations=track_reservations,
            planned_actions_count=int(data.get("planned_actions_count", 0)),
            created_at_iso=str(data.get("created_at_iso", "")),
            state=str(data.get("state", "INITIALIZED")),
            completed_at_iso=data.get("completed_at_iso"),
            last_error_code=str(data.get("last_error_code", "")),
            last_error_message=str(data.get("last_error_message", "")),
        )


def receipt_to_json(receipt: ExecutionReceipt) -> str:
    """Chuyển đổi ExecutionReceipt thành chuỗi UTF-8 JSON chuẩn."""
    if not isinstance(receipt, ExecutionReceipt):
        raise ValueError("ERR_INVALID_RECEIPT: đối tượng phải là ExecutionReceipt")
    d = receipt.to_dict()
    text = json.dumps(d, ensure_ascii=False, indent=2)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError(f"ERR_RECEIPT_TOO_LARGE: kích thước receipt {len(encoded)} vượt quá {MAX_RECEIPT_BYTES}")
    return text


def receipt_from_json(json_text: str) -> ExecutionReceipt:
    """Phục hồi ExecutionReceipt từ chuỗi JSON với kiểm tra biên độ an toàn."""
    if type(json_text) is not str:
        raise ValueError("json_text phải là chuỗi str")
    encoded = json_text.encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError(f"ERR_RECEIPT_TOO_LARGE: kích thước chuỗi receipt {len(encoded)} vượt quá {MAX_RECEIPT_BYTES}")
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"ERR_INVALID_JSON: cú pháp JSON không hợp lệ: {exc}") from exc
    return ExecutionReceipt.from_dict(data)


STAGE_ALLOWED_KEYS: Dict[str, Set[str]] = {
    "START": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
    },
    "FINISH": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "note",
        "error_code",
        "error_message",
    },
    "ACTION_PENDING": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "track_type",
        "track_index",
        "resolve_item_id",
        "note",
    },
    "ACTION_COMMITTED": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "resolve_item_id",
        "track_type",
        "track_index",
        "note",
    },
    "ACTION_FAILED": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "resolve_item_id",
        "track_type",
        "track_index",
        "error_code",
        "error_message",
        "note",
    },
    "ROLLBACK_PENDING": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "resolve_item_id",
        "track_type",
        "track_index",
        "note",
    },
    "ROLLBACK_DONE": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "resolve_item_id",
        "track_type",
        "track_index",
        "note",
    },
    "ROLLBACK_FAILED": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "resolve_item_id",
        "track_type",
        "track_index",
        "error_code",
        "error_message",
        "note",
    },
    "RECOVERY_NOTE": {
        "seq",
        "stage",
        "action_type",
        "timestamp_iso",
        "action_params",
        "note",
    },
}


def reject_duplicate_journal_keys(ordered_pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Hook cho json.loads từ chối phát hiện khóa trùng lặp trong bản ghi JSON nhật ký hoặc action_params."""
    res = {}
    for key, val in ordered_pairs:
        if key in res:
            raise ValueError(f"ERR_JOURNAL_DUPLICATE_KEY: Phát hiện khóa trùng lặp trong bản ghi JSON nhật ký: {key!r}")
        res[key] = val
    return res


def validate_raw_journal_record(data: Any) -> None:
    """Thẩm định cấu trúc và kiểu dữ liệu nguyên thủy của đối tượng JSON thô trước khi chuyển đổi sang JournalEntry (B4-R9-RES-01).

    Quy định:
    1. Cấm hoàn toàn mọi hình thức ép kiểu ngầm (bool -> int, float -> int, int -> str).
    2. Enforce tập khóa cho phép nghiêm ngặt theo từng stage (STAGE_ALLOWED_KEYS).
    3. Từ chối mọi khóa lạ hoặc không xác định.
    4. Yêu cầu đầy đủ các trường bắt buộc tối thiểu (core keys: seq, stage, action_type, timestamp_iso, action_params).
    """
    if type(data) is not dict:
        raise ValueError(f"ERR_JOURNAL_RECORD_TYPE: Bản ghi nhật ký phải là dict, nhận: {type(data).__name__}")

    # Core keys
    for req_key in ("seq", "stage", "action_type", "timestamp_iso", "action_params"):
        if req_key not in data:
            raise ValueError(f"ERR_JOURNAL_MISSING_KEY: Bản ghi nhật ký thiếu khóa bắt buộc: {req_key!r}")

    raw_stage = data["stage"]
    if type(raw_stage) is not str or raw_stage not in VALID_JOURNAL_STAGES:
        raise ValueError(f"ERR_JOURNAL_INVALID_STAGE: stage {raw_stage!r} không nằm trong {VALID_JOURNAL_STAGES}")

    # Allowed keys per stage
    allowed_keys = STAGE_ALLOWED_KEYS.get(raw_stage)
    if allowed_keys is None:
        raise ValueError(f"ERR_JOURNAL_UNKNOWN_STAGE: Stage không xác định: {raw_stage!r}")

    unknown_keys = set(data.keys()) - allowed_keys
    if unknown_keys:
        raise ValueError(
            f"ERR_JOURNAL_UNKNOWN_KEY: Bản ghi stage {raw_stage!r} chứa khóa không hợp lệ: {sorted(unknown_keys)}"
        )

    # Strict primitive types
    raw_seq = data["seq"]
    if type(raw_seq) is not int or type(raw_seq) is bool or raw_seq < 0:
        raise ValueError(
            f"ERR_JOURNAL_FIELD_TYPE: seq phải là số nguyên thuần túy >= 0, nhận: {type(raw_seq).__name__} ({raw_seq!r})"
        )

    raw_action_type = data["action_type"]
    if type(raw_action_type) is not str or raw_action_type not in VALID_ACTION_TYPES:
        raise ValueError(
            f"ERR_JOURNAL_FIELD_TYPE: action_type {raw_action_type!r} không nằm trong {VALID_ACTION_TYPES}"
        )

    raw_ts = data["timestamp_iso"]
    if type(raw_ts) is not str or not raw_ts.strip():
        raise ValueError("ERR_JOURNAL_FIELD_TYPE: timestamp_iso phải là chuỗi không rỗng")
    if not RE_ISO_TIMESTAMP.match(raw_ts):
        raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: timestamp_iso không đúng định dạng ISO-8601: {raw_ts!r}")

    raw_params = data["action_params"]
    if type(raw_params) is not dict:
        raise ValueError("ERR_JOURNAL_FIELD_TYPE: action_params phải là dict")

    if "resolve_item_id" in data:
        raw_item = data["resolve_item_id"]
        if type(raw_item) is not str:
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: resolve_item_id phải là chuỗi str, nhận: {type(raw_item).__name__}")

    if "track_type" in data:
        raw_tt = data["track_type"]
        if type(raw_tt) is not str:
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: track_type phải là chuỗi str, nhận: {type(raw_tt).__name__}")
        if raw_tt and raw_tt.lower() not in ("video", "audio"):
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: track_type phải là 'video' hoặc 'audio', nhận: {raw_tt!r}")

    if "track_index" in data:
        raw_ti = data["track_index"]
        if type(raw_ti) is not int or type(raw_ti) is bool or raw_ti < 0:
            raise ValueError(
                f"ERR_JOURNAL_FIELD_TYPE: track_index phải là int thuần túy >= 0, nhận: {type(raw_ti).__name__} ({raw_ti!r})"
            )

    if "error_code" in data:
        raw_ec = data["error_code"]
        if type(raw_ec) is not str:
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: error_code phải là chuỗi str, nhận: {type(raw_ec).__name__}")

    if "error_message" in data:
        raw_em = data["error_message"]
        if type(raw_em) is not str:
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: error_message phải là chuỗi str, nhận: {type(raw_em).__name__}")

    if "note" in data:
        raw_note = data["note"]
        if type(raw_note) is not str:
            raise ValueError(f"ERR_JOURNAL_FIELD_TYPE: note phải là chuỗi str, nhận: {type(raw_note).__name__}")


@dataclass(frozen=True)
class JournalEntry:
    """Bản ghi nhật ký thao tác append-only trong tệp .journal.jsonl."""
    seq: int
    stage: str
    action_type: str
    timestamp_iso: str
    action_params: Dict[str, Any]
    resolve_item_id: str = ""
    track_type: str = ""
    track_index: int = 0
    error_code: str = ""
    error_message: str = ""
    note: str = ""

    def __post_init__(self):
        _validate_int(self.seq, "seq", min_val=0)
        if self.stage not in VALID_JOURNAL_STAGES:
            raise ValueError(f"ERR_INVALID_STAGE: stage {self.stage!r} không nằm trong {VALID_JOURNAL_STAGES}")
        if self.action_type not in VALID_ACTION_TYPES:
            raise ValueError(f"ERR_INVALID_ACTION_TYPE: action_type {self.action_type!r} không nằm trong {VALID_ACTION_TYPES}")
        _validate_iso_timestamp(self.timestamp_iso, "timestamp_iso")
        if type(self.action_params) is not dict:
            raise ValueError("ERR_INVALID_PARAMS: action_params phải là dict")

        if self.resolve_item_id:
            _validate_str(self.resolve_item_id, "resolve_item_id")
        if self.track_type:
            tt = _validate_str(self.track_type, "track_type").lower()
            if tt not in ("video", "audio"):
                raise ValueError(f"ERR_INVALID_TRACK_TYPE: track_type phải là 'video' hoặc 'audio': {tt!r}")
        if self.track_index != 0:
            _validate_int(self.track_index, "track_index", min_val=1)
        if self.error_code:
            _validate_str(self.error_code, "error_code")
        if self.error_message:
            _validate_str(self.error_message, "error_message", allow_blank=True)
        if self.note:
            _validate_str(self.note, "note", allow_blank=True)

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "seq": self.seq,
            "stage": self.stage,
            "action_type": self.action_type,
            "timestamp_iso": self.timestamp_iso,
            "action_params": self.action_params,
        }
        if self.resolve_item_id:
            res["resolve_item_id"] = self.resolve_item_id
        if self.track_type:
            res["track_type"] = self.track_type
        if self.track_index != 0:
            res["track_index"] = self.track_index
        if self.error_code:
            res["error_code"] = self.error_code
        if self.error_message:
            res["error_message"] = self.error_message
        if self.note:
            res["note"] = self.note
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JournalEntry:
        validate_raw_journal_record(data)
        return cls(
            seq=data["seq"],
            stage=data["stage"],
            action_type=data["action_type"],
            timestamp_iso=data["timestamp_iso"],
            action_params=data["action_params"],
            resolve_item_id=data.get("resolve_item_id", ""),
            track_type=data.get("track_type", ""),
            track_index=data.get("track_index", 0),
            error_code=data.get("error_code", ""),
            error_message=data.get("error_message", ""),
            note=data.get("note", ""),
        )


def journal_entry_to_json_line(entry: JournalEntry) -> str:
    """Chuyển đổi JournalEntry thành một dòng JSON kết thúc bằng \\n có giới hạn MAX_LINE_BYTES."""
    if not isinstance(entry, JournalEntry):
        raise ValueError("ERR_INVALID_ENTRY: đối tượng phải là JournalEntry")
    line = json.dumps(entry.to_dict(), ensure_ascii=False)
    encoded = (line + "\n").encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise ValueError(f"ERR_LINE_TOO_LARGE: kích thước dòng {len(encoded)} vượt quá {MAX_LINE_BYTES} bytes")
    return line + "\n"


@dataclass(frozen=True)
class TornTailDiagnostic:
    """Chẩn đoán chi tiết khi phát hiện dòng nhật ký cuối cùng bị ngắt quãng phi phá hủy."""
    is_torn: bool
    line_number: int
    raw_bytes: bytes
    error_message: str


@dataclass(frozen=True)
class JournalReplayResult:
    """Kết quả đọc và phân tích toàn bộ nhật ký thao tác thi công."""
    entries: Tuple[JournalEntry, ...]
    torn_tail: Optional[TornTailDiagnostic] = None
    is_clean: bool = True
    total_bytes_read: int = 0

    def __post_init__(self):
        if type(self.entries) is not tuple:
            raise ValueError("entries phải là tuple")
