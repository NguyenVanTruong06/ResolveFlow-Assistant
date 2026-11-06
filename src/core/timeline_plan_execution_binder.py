"""src/core/timeline_plan_execution_binder.py
Bộ liên kết kế hoạch thi công timeline offline và báo cáo Dry-Run thuần Python (G07-07B2-I).

Quy tắc kiến trúc:
1. Pure Python standard library (dataclasses, fractions, hashlib, json, math, types, typing).
2. Tái sử dụng trọn vẹn các hợp đồng đã nghiệm thu:
   - RationalFPS, TimeRangeFrames từ src/core/contracts/time_contracts.py
   - QuantizedCFRSpan, validate_fps_instance, validate_fraction_instance từ src/core/geometry/frame_geometry_contracts.py
   - EditPlanProposal, compute_proposal_revision, normalize_edit_plan_proposal từ src/core/contracts/edit_plan_proposal_contracts.py
   - AppOwnedApprovalRecord, verify_proposal_approval_binding, VALID_APPROVAL_SCOPE từ src/core/contracts/edit_plan_approval_contracts.py
   - TargetTrackReservation, SourceFileDigestBinding, CatalogAssetDigestBinding từ src/core/contracts/execution_receipt_contracts.py
   - SourceFrameEvidence, PlanGeometryAssemblyResult, PlanGeometryIssue, PlanTimelineSegmentGeometry từ src/core/geometry/plan_geometry_contracts.py
   - assemble_plan_frame_geometry từ src/core/geometry/plan_geometry_assembler.py
3. Tuyệt đối KHÔNG import UI (PySide6), filesystem IO, network, QSettings, subprocess, hoặc DaVinci Resolve API.
4. Bất biến hoàn toàn (frozen dataclass, MappingProxyType, tuple defensive copies).
5. Ràng buộc an toàn: Cờ ready_to_execute=False bảo toàn 100%, không bao giờ xuất hiện hay suy diễn ready_to_execute=True.
6. Chính sách va chạm Fail-Closed nghiêm ngặt trên ảnh chụp chiếm dụng có thẩm quyền:
   - Chỉ khi snapshot có trạng thái AUTHORITATIVE, đúng project/timeline/FPS và track đích nằm trong scanned_tracks thì mới coi track là rỗng.
   - Vắng mặt, STALE, AMBIGUOUS, hoặc track chưa quét -> BẮT BUỘC CHẶN. CẤM đoán mò track rỗng.
7. Xử lý bỏ qua trung thực D01/D02/D03: Primary footage tuyệt đối cấm omission; B-roll/SFX thiếu chỉ được bỏ qua khi người dùng đã chấp thuận trong accepted_omission_ids.
8. Bounded serialization: Giới hạn trần DoS/OOM MAX_DRY_RUN_JSON_BYTES = 5 MiB (bằng trần chấp nhận, +1 từ chối), MAX_DRY_RUN_NODES = 100,000, sort_keys=True.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    VALID_APPROVAL_SCOPE,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.execution_receipt_contracts import (
    CatalogAssetDigestBinding,
    SourceFileDigestBinding,
    TargetTrackReservation,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.geometry.frame_geometry_contracts import validate_fps_instance
from src.core.geometry.plan_geometry_assembler import assemble_plan_frame_geometry
from src.core.geometry.plan_geometry_contracts import (
    PlanGeometryAssemblyResult,
    PlanGeometryIssue,
    PlanTimelineSegmentGeometry,
    SourceFrameEvidence,
)

# Giới hạn trần dung lượng và bộ đếm
MAX_PLANNED_ACTIONS: int = 4096
MAX_TRACK_RESERVATIONS: int = 64
MAX_DRY_RUN_JSON_BYTES: int = 5 * 1024 * 1024       # 5 MiB
MAX_DRY_RUN_NODES: int = 100_000
MAX_STRING_BYTES: int = 512 * 1024                  # 512 KiB per string ceiling

VALID_OCCUPANCY_STATUSES: frozenset[str] = frozenset({
    "AUTHORITATIVE",
    "STALE",
    "AMBIGUOUS",
    "UNAVAILABLE",
})

VALID_BINDING_STATUSES: frozenset[str] = frozenset({
    "BOUND_VERIFIED",
    "BOUND_BLOCKED",
})

VALID_ACTION_TYPES: frozenset[str] = frozenset({
    "INSERT_CLIP",
    "RESERVE_TRACK",
    "IMPORT_CARRIER",
    "SET_CLIP_COLOR",
    "NO_OP",
})

VALID_TRACK_TYPES: frozenset[str] = frozenset({"video", "audio"})


def _validate_clean_str(val: Any, field_name: str, allow_blank: bool = False) -> str:
    """Xác thực chuỗi UTF-8 hợp lệ, loại trừ NUL, lone surrogates, kiểm tra giới hạn trần."""
    if type(val) is not str:
        raise ValueError(f"ERR_INVALID_TYPE: {field_name} phải là kiểu str, nhận: {type(val).__name__ if val is not None else 'None'}")
    if "\0" in val:
        raise ValueError(f"ERR_INVALID_STRING: {field_name} chứa ký tự NUL không hợp lệ")
    try:
        encoded = val.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"ERR_INVALID_UTF8: {field_name} không thể mã hóa UTF-8 (lone surrogate): {e}") from e
    if len(encoded) > MAX_STRING_BYTES:
        raise ValueError(f"ERR_STRING_OVERFLOW: {field_name} có kích thước {len(encoded)} bytes, vượt quá giới hạn {MAX_STRING_BYTES}")
    if not allow_blank and not val.strip():
        raise ValueError(f"ERR_BLANK_FIELD: {field_name} không được để trống hoặc chỉ chứa khoảng trắng")
    return val


def _validate_non_negative_int(val: Any, field_name: str) -> int:
    """Xác thực số nguyên không âm (loại trừ bool)."""
    if type(val) is not int or type(val) is bool or val < 0:
        raise ValueError(f"ERR_INVALID_NUMBER: {field_name} phải là số nguyên int >= 0, nhận: {val!r}")
    return val


def _validate_positive_int(val: Any, field_name: str) -> int:
    """Xác thực số nguyên dương >= 1 (loại trừ bool)."""
    if type(val) is not int or type(val) is bool or val < 1:
        raise ValueError(f"ERR_INVALID_NUMBER: {field_name} phải là số nguyên int >= 1, nhận: {val!r}")
    return val


# ==============================================================================
# HỢP ĐỒNG DTO BẤT BIẾN (IMMUTABLE VALUE OBJECTS)
# ==============================================================================

@dataclass(frozen=True)
class TargetSpec:
    """Thông số kỹ thuật đích danh nghĩa của DaVinci Resolve (Immutable Value Object)."""
    project_name: str
    project_id: str
    timeline_name: str
    timeline_id: str
    timeline_fps: RationalFPS
    timeline_resolution: Tuple[int, int]
    start_timecode_frame: int = 0
    video_track_mapping: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({"a_roll": 1, "b_roll": 2})
    )
    audio_track_mapping: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({"dialogue": 1, "sfx": 2, "bgm": 3})
    )

    def __post_init__(self) -> None:
        p_name = _validate_clean_str(self.project_name, "project_name", allow_blank=False)
        p_id = _validate_clean_str(self.project_id, "project_id", allow_blank=False)
        t_name = _validate_clean_str(self.timeline_name, "timeline_name", allow_blank=False)
        t_id = _validate_clean_str(self.timeline_id, "timeline_id", allow_blank=False)
        object.__setattr__(self, "project_name", p_name)
        object.__setattr__(self, "project_id", p_id)
        object.__setattr__(self, "timeline_name", t_name)
        object.__setattr__(self, "timeline_id", t_id)

        owned_fps = validate_fps_instance(self.timeline_fps, "ERR_INVALID_TIMELINE_FPS")
        object.__setattr__(self, "timeline_fps", owned_fps)

        if not isinstance(self.timeline_resolution, (tuple, list)) or len(self.timeline_resolution) != 2:
            raise ValueError(f"ERR_INVALID_RESOLUTION: timeline_resolution phải là tuple(width, height), nhận: {self.timeline_resolution!r}")
        w, h = self.timeline_resolution
        if type(w) is not int or type(w) is bool or w < 16 or type(h) is not int or type(h) is bool or h < 16:
            raise ValueError(f"ERR_INVALID_RESOLUTION: độ phân giải (width, height) phải là số nguyên >= 16, nhận: {self.timeline_resolution!r}")
        object.__setattr__(self, "timeline_resolution", (w, h))

        stc = _validate_non_negative_int(self.start_timecode_frame, "start_timecode_frame")
        object.__setattr__(self, "start_timecode_frame", stc)

        # Video track mapping
        if not isinstance(self.video_track_mapping, Mapping):
            raise ValueError(f"ERR_INVALID_MAPPING: video_track_mapping phải là Mapping, nhận: {type(self.video_track_mapping).__name__}")
        v_map: dict[str, int] = {}
        for k, v in self.video_track_mapping.items():
            clean_k = _validate_clean_str(k, "video_track_mapping key")
            clean_v = _validate_positive_int(v, f"video_track_mapping[{clean_k}]")
            v_map[clean_k] = clean_v
        object.__setattr__(self, "video_track_mapping", MappingProxyType(v_map))

        # Audio track mapping
        if not isinstance(self.audio_track_mapping, Mapping):
            raise ValueError(f"ERR_INVALID_MAPPING: audio_track_mapping phải là Mapping, nhận: {type(self.audio_track_mapping).__name__}")
        a_map: dict[str, int] = {}
        for k, v in self.audio_track_mapping.items():
            clean_k = _validate_clean_str(k, "audio_track_mapping key")
            clean_v = _validate_positive_int(v, f"audio_track_mapping[{clean_k}]")
            a_map[clean_k] = clean_v
        object.__setattr__(self, "audio_track_mapping", MappingProxyType(a_map))


@dataclass(frozen=True)
class OccupiedTrackSpan:
    """Khoảng thời gian khung hình bị chiếm dụng bởi một clip hiện hữu trên track Resolve."""
    track_type: str
    track_index: int
    start_frame: int
    end_frame: int
    item_name: str = ""

    def __post_init__(self) -> None:
        tt = _validate_clean_str(self.track_type, "track_type").lower()
        if tt not in VALID_TRACK_TYPES:
            raise ValueError(f"ERR_INVALID_TRACK_TYPE: track_type phải là 'video' hoặc 'audio', nhận: {tt!r}")
        object.__setattr__(self, "track_type", tt)

        idx = _validate_positive_int(self.track_index, "track_index")
        object.__setattr__(self, "track_index", idx)

        sf = _validate_non_negative_int(self.start_frame, "start_frame")
        ef = _validate_non_negative_int(self.end_frame, "end_frame")
        if ef <= sf:
            raise ValueError(f"ERR_INVALID_RANGE: end_frame ({ef}) phải lớn hơn start_frame ({sf})")
        object.__setattr__(self, "start_frame", sf)
        object.__setattr__(self, "end_frame", ef)

        name = _validate_clean_str(self.item_name, "item_name", allow_blank=True)
        object.__setattr__(self, "item_name", name)


@dataclass(frozen=True)
class TimelineOccupancySnapshot:
    """Ảnh chụp trạng thái chiếm dụng track trên timeline Resolve (Immutable Value Object)."""
    snapshot_id: str
    project_id: str
    timeline_id: str
    timeline_fps: RationalFPS
    captured_at_iso: str
    occupied_spans: Tuple[OccupiedTrackSpan, ...] = ()
    scanned_tracks: Tuple[Tuple[str, int], ...] = ()
    status: str = "UNAVAILABLE"

    def __post_init__(self) -> None:
        s_id = _validate_clean_str(self.snapshot_id, "snapshot_id", allow_blank=False)
        p_id = _validate_clean_str(self.project_id, "project_id", allow_blank=False)
        t_id = _validate_clean_str(self.timeline_id, "timeline_id", allow_blank=False)
        ts = _validate_clean_str(self.captured_at_iso, "captured_at_iso", allow_blank=False)
        object.__setattr__(self, "snapshot_id", s_id)
        object.__setattr__(self, "project_id", p_id)
        object.__setattr__(self, "timeline_id", t_id)
        object.__setattr__(self, "captured_at_iso", ts)

        owned_fps = validate_fps_instance(self.timeline_fps, "ERR_INVALID_TIMELINE_FPS")
        object.__setattr__(self, "timeline_fps", owned_fps)

        st = _validate_clean_str(self.status, "status", allow_blank=False)
        if st not in VALID_OCCUPANCY_STATUSES:
            raise ValueError(f"ERR_INVALID_STATUS: status phải thuộc {sorted(VALID_OCCUPANCY_STATUSES)}, nhận: {st!r}")
        object.__setattr__(self, "status", st)

        # Defensive copies for occupied_spans and scanned_tracks
        spans = []
        for span in self.occupied_spans:
            if not isinstance(span, OccupiedTrackSpan):
                raise ValueError(f"ERR_INVALID_SPAN: phần tử occupied_spans phải là OccupiedTrackSpan, nhận: {type(span).__name__}")
            spans.append(span)
        object.__setattr__(self, "occupied_spans", tuple(spans))

        tracks = []
        for trk in self.scanned_tracks:
            if not isinstance(trk, (tuple, list)) or len(trk) != 2:
                raise ValueError(f"ERR_INVALID_TRACK: scanned_tracks phải chứa tuple(track_type, track_index), nhận: {trk!r}")
            tt, ti = trk
            tt_clean = _validate_clean_str(tt, "scanned_tracks track_type").lower()
            ti_clean = _validate_positive_int(ti, "scanned_tracks track_index")
            tracks.append((tt_clean, ti_clean))
        object.__setattr__(self, "scanned_tracks", tuple(tracks))


@dataclass(frozen=True)
class PlannedTimelineAction:
    """Thao tác thi công timeline đơn lẻ được lên kế hoạch tất định (Immutable Value Object)."""
    action_index: int
    action_type: str
    target_track_type: str
    target_track_index: int
    clip_id: str
    source_file_path: str
    source_range: TimeRangeFrames
    timeline_range: TimeRangeFrames
    applied_speed: Fraction
    role: str
    action_idempotency_hash: str
    action_params: Mapping[str, Any]

    def __post_init__(self) -> None:
        idx = _validate_non_negative_int(self.action_index, "action_index")
        object.__setattr__(self, "action_index", idx)

        at = _validate_clean_str(self.action_type, "action_type")
        if at not in VALID_ACTION_TYPES:
            raise ValueError(f"ERR_INVALID_ACTION_TYPE: action_type phải thuộc {sorted(VALID_ACTION_TYPES)}, nhận: {at!r}")
        object.__setattr__(self, "action_type", at)

        tt = _validate_clean_str(self.target_track_type, "target_track_type").lower()
        if tt not in VALID_TRACK_TYPES:
            raise ValueError(f"ERR_INVALID_TRACK_TYPE: target_track_type phải là 'video' hoặc 'audio', nhận: {tt!r}")
        object.__setattr__(self, "target_track_type", tt)

        ti = _validate_positive_int(self.target_track_index, "target_track_index")
        object.__setattr__(self, "target_track_index", ti)

        cid = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
        sfp = _validate_clean_str(self.source_file_path, "source_file_path", allow_blank=False)
        object.__setattr__(self, "clip_id", cid)
        object.__setattr__(self, "source_file_path", sfp)

        if not isinstance(self.source_range, TimeRangeFrames):
            raise ValueError("ERR_INVALID_RANGE: source_range phải là TimeRangeFrames")
        if not isinstance(self.timeline_range, TimeRangeFrames):
            raise ValueError("ERR_INVALID_RANGE: timeline_range phải là TimeRangeFrames")

        if not isinstance(self.applied_speed, Fraction):
            raise ValueError(f"ERR_INVALID_SPEED: applied_speed phải là Fraction, nhận: {type(self.applied_speed).__name__}")
        if self.applied_speed <= 0:
            raise ValueError(f"ERR_INVALID_SPEED: applied_speed phải dương (> 0), nhận: {self.applied_speed}")

        r = _validate_clean_str(self.role, "role", allow_blank=False)
        object.__setattr__(self, "role", r)

        h = _validate_clean_str(self.action_idempotency_hash, "action_idempotency_hash", allow_blank=False)
        object.__setattr__(self, "action_idempotency_hash", h)

        if not isinstance(self.action_params, Mapping):
            raise ValueError("ERR_INVALID_PARAMS: action_params phải là Mapping")
        object.__setattr__(self, "action_params", MappingProxyType(dict(self.action_params)))


@dataclass(frozen=True)
class PlannedOmissionRecord:
    """Bản ghi tài sản bổ trợ bị bỏ qua trung thực trong bản dựng thô (Immutable Value Object)."""
    demand_id: str
    slot: str
    clip_id: Optional[str]
    reason: str
    accepted_by_approval_id: str
    intended_timeline_range: Optional[TimeRangeFrames] = None

    def __post_init__(self) -> None:
        did = _validate_clean_str(self.demand_id, "demand_id", allow_blank=False)
        s = _validate_clean_str(self.slot, "slot", allow_blank=False)
        r = _validate_clean_str(self.reason, "reason", allow_blank=True)
        aid = _validate_clean_str(self.accepted_by_approval_id, "accepted_by_approval_id", allow_blank=False)
        object.__setattr__(self, "demand_id", did)
        object.__setattr__(self, "slot", s)
        object.__setattr__(self, "reason", r)
        object.__setattr__(self, "accepted_by_approval_id", aid)

        if self.clip_id is not None:
            cid = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
            object.__setattr__(self, "clip_id", cid)

        if self.intended_timeline_range is not None:
            if not isinstance(self.intended_timeline_range, TimeRangeFrames):
                raise ValueError("ERR_INVALID_RANGE: intended_timeline_range phải là TimeRangeFrames")


@dataclass(frozen=True)
class PlanBindingIssue:
    """Báo cáo vấn đề/lỗi phát hiện trong quá trình liên kết kế hoạch thi công."""
    severity: str
    issue_code: str
    field_path: str
    clip_id: Optional[str] = None
    track_info: Optional[str] = None
    message: str = ""

    def __post_init__(self) -> None:
        sev = _validate_clean_str(self.severity, "severity", allow_blank=False)
        if sev not in ("BLOCKING", "WARNING"):
            raise ValueError(f"ERR_INVALID_SEVERITY: severity phải là 'BLOCKING' hoặc 'WARNING', nhận: {sev!r}")
        object.__setattr__(self, "severity", sev)

        code = _validate_clean_str(self.issue_code, "issue_code", allow_blank=False)
        path = _validate_clean_str(self.field_path, "field_path", allow_blank=True)
        msg = _validate_clean_str(self.message, "message", allow_blank=True)
        object.__setattr__(self, "issue_code", code)
        object.__setattr__(self, "field_path", path)
        object.__setattr__(self, "message", msg)

        if self.clip_id is not None:
            cid = _validate_clean_str(self.clip_id, "clip_id", allow_blank=False)
            object.__setattr__(self, "clip_id", cid)
        if self.track_info is not None:
            tinfo = _validate_clean_str(self.track_info, "track_info", allow_blank=False)
            object.__setattr__(self, "track_info", tinfo)


@dataclass(frozen=True)
class TimelineExecutionPlan:
    """Kế hoạch thi công timeline trọn gói bất biến (Immutable Root DTO)."""
    plan_id: str
    idempotency_key: str
    proposal_id: str
    proposal_revision: str
    approval_id: str
    approved_at_iso: str
    target_spec: TargetSpec
    occupancy_snapshot_id: Optional[str]
    binding_status: str
    planned_actions: Tuple[PlannedTimelineAction, ...]
    planned_actions_count: int
    track_reservations: Tuple[TargetTrackReservation, ...]
    source_digests: Tuple[SourceFileDigestBinding, ...]
    catalog_digests: Tuple[CatalogAssetDigestBinding, ...]
    accepted_omissions: Tuple[PlannedOmissionRecord, ...]
    total_timeline_frames: int
    total_timeline_duration_sec: Fraction
    issues: Tuple[PlanBindingIssue, ...]
    created_at_iso: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        pid = _validate_clean_str(self.plan_id, "plan_id", allow_blank=False)
        ik = _validate_clean_str(self.idempotency_key, "idempotency_key", allow_blank=False)
        p_id = _validate_clean_str(self.proposal_id, "proposal_id", allow_blank=False)
        p_rev = _validate_clean_str(self.proposal_revision, "proposal_revision", allow_blank=False)
        a_id = _validate_clean_str(self.approval_id, "approval_id", allow_blank=False)
        a_ts = _validate_clean_str(self.approved_at_iso, "approved_at_iso", allow_blank=False)
        c_ts = _validate_clean_str(self.created_at_iso, "created_at_iso", allow_blank=False)
        object.__setattr__(self, "plan_id", pid)
        object.__setattr__(self, "idempotency_key", ik)
        object.__setattr__(self, "proposal_id", p_id)
        object.__setattr__(self, "proposal_revision", p_rev)
        object.__setattr__(self, "approval_id", a_id)
        object.__setattr__(self, "approved_at_iso", a_ts)
        object.__setattr__(self, "created_at_iso", c_ts)

        if not isinstance(self.target_spec, TargetSpec):
            raise ValueError(f"ERR_INVALID_SPEC: target_spec phải là TargetSpec, nhận: {type(self.target_spec).__name__}")

        if self.occupancy_snapshot_id is not None:
            sid = _validate_clean_str(self.occupancy_snapshot_id, "occupancy_snapshot_id", allow_blank=False)
            object.__setattr__(self, "occupancy_snapshot_id", sid)

        st = _validate_clean_str(self.binding_status, "binding_status", allow_blank=False)
        if st not in VALID_BINDING_STATUSES:
            raise ValueError(f"ERR_INVALID_STATUS: binding_status phải thuộc {sorted(VALID_BINDING_STATUSES)}, nhận: {st!r}")
        object.__setattr__(self, "binding_status", st)

        # Defensive tuples
        object.__setattr__(self, "planned_actions", tuple(self.planned_actions))
        object.__setattr__(self, "planned_actions_count", len(self.planned_actions))
        object.__setattr__(self, "track_reservations", tuple(self.track_reservations))
        object.__setattr__(self, "source_digests", tuple(self.source_digests))
        object.__setattr__(self, "catalog_digests", tuple(self.catalog_digests))
        object.__setattr__(self, "accepted_omissions", tuple(self.accepted_omissions))
        object.__setattr__(self, "issues", tuple(self.issues))

        ttf = _validate_non_negative_int(self.total_timeline_frames, "total_timeline_frames")
        object.__setattr__(self, "total_timeline_frames", ttf)

        if not isinstance(self.total_timeline_duration_sec, Fraction):
            raise ValueError(f"ERR_INVALID_DURATION: total_timeline_duration_sec phải là Fraction, nhận: {type(self.total_timeline_duration_sec).__name__}")
        if self.total_timeline_duration_sec < 0:
            raise ValueError("ERR_INVALID_DURATION: total_timeline_duration_sec không được âm")

    @property
    def project_uid(self) -> str:
        """Convenience alias for target_spec.project_id."""
        return self.target_spec.project_id

    @property
    def timeline_uid(self) -> str:
        """Convenience alias for target_spec.timeline_id."""
        return self.target_spec.timeline_id


# ==============================================================================
# HÀM XỬ LÝ IDEMPOTENCY & BINDING CHÍNH (CORE BINDER LOGIC)
# ==============================================================================

def compute_plan_idempotency_key(
    proposal_id: str,
    proposal_revision: str,
    approval_id: str,
    project_id: str,
    timeline_id: str,
) -> str:
    """Tính toán mã băm SHA-256 tất định cho kế hoạch thi công."""
    p_id = _validate_clean_str(proposal_id, "proposal_id", allow_blank=False)
    p_rev = _validate_clean_str(proposal_revision, "proposal_revision", allow_blank=False)
    a_id = _validate_clean_str(approval_id, "approval_id", allow_blank=False)
    proj_id = _validate_clean_str(project_id, "project_id", allow_blank=False)
    tl_id = _validate_clean_str(timeline_id, "timeline_id", allow_blank=False)

    payload = f"{p_id}:{p_rev}:{a_id}:{proj_id}:{tl_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_action_hash(
    action_index: int,
    clip_id: str,
    track_type: str,
    track_index: int,
    rec_in: int,
    rec_out: int,
    speed: Fraction,
) -> str:
    """Tính mã băm fingerprint tất định cho từng thao tác thi công."""
    payload = f"{action_index}:{clip_id}:{track_type}:{track_index}:{rec_in}:{rec_out}:{speed.numerator}/{speed.denominator}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bind_timeline_execution_plan(
    proposal: Union[Mapping[str, Any], EditPlanProposal],
    approval: Union[Mapping[str, Any], AppOwnedApprovalRecord],
    target_spec: TargetSpec,
    evidence_table: Mapping[str, SourceFrameEvidence],
    occupancy_snapshot: Optional[TimelineOccupancySnapshot] = None,
    catalog_evidence: Optional[Mapping[str, CatalogAssetDigestBinding]] = None,
    source_digests: Optional[Sequence[SourceFileDigestBinding]] = None,
    created_at_iso: Optional[str] = None,
) -> TimelineExecutionPlan:
    """Thực hiện liên kết kế hoạch thi công timeline thuần khiết in-memory (All-or-Nothing)."""
    now_iso = created_at_iso if created_at_iso is not None else datetime.now(timezone.utc).isoformat()
    issues: list[PlanBindingIssue] = []

    # 1. Chuẩn hóa và revalidate proposal
    try:
        owned_proposal = normalize_edit_plan_proposal(proposal)
    except Exception as e:
        issues.append(
            PlanBindingIssue(
                severity="BLOCKING",
                issue_code="ERR_MALFORMED_PROPOSAL",
                field_path="proposal",
                message=f"Lỗi chuẩn hóa proposal: {e}",
            )
        )
        return _make_blocked_plan("plan_err_init", "", "", "", "", target_spec, issues, now_iso)

    # 2. Revalidate approval record
    if not isinstance(approval, AppOwnedApprovalRecord):
        try:
            if isinstance(approval, Mapping):
                appr_rec = AppOwnedApprovalRecord(
                    approval_id=str(approval.get("approval_id", "")),
                    proposal_id=str(approval.get("proposal_id", "")),
                    proposal_revision=str(approval.get("proposal_revision", "")),
                    approved_at_iso=str(approval.get("approved_at_iso", "")),
                    accepted_omission_ids=tuple(approval.get("accepted_omission_ids", ())),
                    approval_scope=str(approval.get("approval_scope", VALID_APPROVAL_SCOPE)),
                )
            else:
                issues.append(
                    PlanBindingIssue(
                        severity="BLOCKING",
                        issue_code="ERR_INVALID_APPROVAL_TYPE",
                        field_path="approval",
                        message="approval phải là AppOwnedApprovalRecord hoặc Mapping hợp lệ",
                    )
                )
                return _make_blocked_plan("plan_err_init", "", owned_proposal.proposal_id, "", "", target_spec, issues, now_iso)
        except Exception as e:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_MALFORMED_APPROVAL",
                    field_path="approval",
                    message=f"Lỗi khởi tạo approval record: {e}",
                )
            )
            return _make_blocked_plan("plan_err_init", "", owned_proposal.proposal_id, "", "", target_spec, issues, now_iso)
    else:
        appr_rec = approval

    # 3. Xác minh tính toàn vẹn liên kết Proposal & Approval qua verify_proposal_approval_binding
    if not verify_proposal_approval_binding(owned_proposal, appr_rec):
        # Phân loại nguyên nhân cụ thể
        if appr_rec.proposal_id != owned_proposal.proposal_id:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_PROPOSAL_ID_MISMATCH",
                    field_path="approval.proposal_id",
                    message=f"approval.proposal_id ({appr_rec.proposal_id!r}) không khớp proposal.proposal_id ({owned_proposal.proposal_id!r})",
                )
            )
        expected_rev = compute_proposal_revision(owned_proposal)
        if appr_rec.proposal_revision != expected_rev:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_STALE_PROPOSAL_REVISION",
                    field_path="approval.proposal_revision",
                    message="Bản đề xuất đã bị sửa đổi sau khi duyệt (revision hash không khớp)",
                )
            )
        if appr_rec.approval_scope != VALID_APPROVAL_SCOPE:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_INVALID_APPROVAL_SCOPE",
                    field_path="approval.approval_scope",
                    message=f"approval_scope phải là {VALID_APPROVAL_SCOPE!r}, nhận: {appr_rec.approval_scope!r}",
                )
            )
        expected_omission_ids = tuple(str(item["demand_id"]) for item in owned_proposal.proposed_omissions)
        if appr_rec.accepted_omission_ids != expected_omission_ids:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_UNAPPROVED_OMISSION",
                    field_path="approval.accepted_omission_ids",
                    message="accepted_omission_ids trong approval không khớp với proposed_omissions của proposal",
                )
            )

        if not any(iss.severity == "BLOCKING" for iss in issues):
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_APPROVAL_BINDING_FAILED",
                    field_path="approval",
                    message="verify_proposal_approval_binding thất bại mà không xác định được mã lỗi cụ thể",
                )
            )
        return _make_blocked_plan(
            "plan_err_bind",
            "",
            owned_proposal.proposal_id,
            appr_rec.proposal_revision,
            appr_rec.approval_id,
            target_spec,
            issues,
            now_iso,
        )

    # 4. Xác thực TargetSpec
    if not isinstance(target_spec, TargetSpec):
        issues.append(
            PlanBindingIssue(
                severity="BLOCKING",
                issue_code="ERR_INVALID_TARGET_SPEC",
                field_path="target_spec",
                message="target_spec phải là TargetSpec hợp lệ",
            )
        )
        return _make_blocked_plan(
            "plan_err_target",
            "",
            owned_proposal.proposal_id,
            appr_rec.proposal_revision,
            appr_rec.approval_id,
            target_spec,
            issues,
            now_iso,
        )

    # 5. Xác thực ảnh chụp chiếm dụng TimelineOccupancySnapshot (Fail-Closed Policy)
    if occupancy_snapshot is None:
        issues.append(
            PlanBindingIssue(
                severity="BLOCKING",
                issue_code="ERR_OCCUPANCY_UNAVAILABLE",
                field_path="occupancy_snapshot",
                message="TimelineOccupancySnapshot is missing; cannot verify track availability without authoritative snapshot",
            )
        )
    else:
        if occupancy_snapshot.status != "AUTHORITATIVE":
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_OCCUPANCY_AMBIGUOUS",
                    field_path="occupancy_snapshot.status",
                    message=f"Snapshot status is {occupancy_snapshot.status}, must be AUTHORITATIVE",
                )
            )
        if occupancy_snapshot.project_id != target_spec.project_id or occupancy_snapshot.timeline_id != target_spec.timeline_id:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_OCCUPANCY_UNAVAILABLE",
                    field_path="occupancy_snapshot.project_id",
                    message="Snapshot target project/timeline does not match TargetSpec",
                )
            )
        if occupancy_snapshot.timeline_fps != target_spec.timeline_fps:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_TARGET_FPS_MISMATCH",
                    field_path="occupancy_snapshot.timeline_fps",
                    message=f"Snapshot timeline_fps ({occupancy_snapshot.timeline_fps}) does not match TargetSpec ({target_spec.timeline_fps})",
                )
            )

    # 6. Kiểm tra footage chính không được phép omission (D02 Boundary)
    source_clips_map: dict[str, Mapping[str, Any]] = {}
    for c in owned_proposal.source_binding.get("clips", ()):
        if isinstance(c, Mapping) and "clip_id" in c and isinstance(c["clip_id"], str):
            source_clips_map[c["clip_id"]] = c

    # Kiểm tra footage A-roll và global hook có bị thiếu trong evidence_table không
    vp = owned_proposal.video_plan
    primary_clip_ids: set[str] = set()
    hook = vp.get("global_hook")
    if isinstance(hook, Mapping) and "clip_id" in hook and isinstance(hook["clip_id"], str):
        primary_clip_ids.add(hook["clip_id"])
    for seg in vp.get("timeline_segments", ()):
        if isinstance(seg, Mapping) and "clip_id" in seg and isinstance(seg["clip_id"], str):
            primary_clip_ids.add(seg["clip_id"])

    for cid in primary_clip_ids:
        if cid not in evidence_table:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_MISSING_PRIMARY_FOOTAGE",
                    clip_id=cid,
                    field_path=f"evidence_table[{cid}]",
                    message=f"Primary footage {cid!r} is missing from evidence_table; primary footage can never be omitted",
                )
            )
        else:
            ev = evidence_table[cid]
            if ev.evidence_status != "VERIFIED_CFR":
                issues.append(
                    PlanBindingIssue(
                        severity="BLOCKING",
                        issue_code="ERR_MISSING_PRIMARY_FOOTAGE" if ev.evidence_status == "HEADER_REPORTED_ONLY" else "ERR_VFR_NOT_PERMITTED",
                        clip_id=cid,
                        field_path=f"evidence_table[{cid}].evidence_status",
                        message=f"Primary footage {cid!r} has status {ev.evidence_status!r}, must be VERIFIED_CFR",
                    )
                )

    # 7. Lắp ráp hình học Formula B qua assemble_plan_frame_geometry
    geom_result = assemble_plan_frame_geometry(owned_proposal, evidence_table, target_spec.timeline_fps)
    if geom_result.status != "GEOMETRY_VERIFIED":
        for g_iss in geom_result.issues:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code=g_iss.issue_code,
                    field_path=f"geometry.{g_iss.segment_index}",
                    clip_id=g_iss.clip_id,
                    message=g_iss.message,
                )
            )
        if not geom_result.issues:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code=f"ERR_{geom_result.status}",
                    field_path="geometry",
                    message=f"Geometry status is {geom_result.status}, must be GEOMETRY_VERIFIED",
                )
            )

    # 8. Xử lý Omissions trung thực (D01/D02/D03)
    accepted_omission_records: list[PlannedOmissionRecord] = []
    accepted_omission_ids_set = set(appr_rec.accepted_omission_ids)
    for omit in owned_proposal.proposed_omissions:
        did = str(omit.get("demand_id") or "")
        slot = str(omit.get("slot") or "broll")
        reason = str(omit.get("reason") or "Supplemental asset omission")
        cid = omit.get("clip_id")

        if did in accepted_omission_ids_set:
            accepted_omission_records.append(
                PlannedOmissionRecord(
                    demand_id=did,
                    slot=slot,
                    clip_id=str(cid) if cid is not None else None,
                    reason=reason,
                    accepted_by_approval_id=appr_rec.approval_id,
                )
            )
            issues.append(
                PlanBindingIssue(
                    severity="WARNING",
                    issue_code="WARN_SUPPLEMENTAL_OMISSION",
                    field_path=f"proposed_omissions[{did}]",
                    clip_id=str(cid) if cid is not None else None,
                    message=f"Approved omission of {slot} asset {did!r} ({reason})",
                )
            )
        else:
            issues.append(
                PlanBindingIssue(
                    severity="BLOCKING",
                    issue_code="ERR_UNAPPROVED_OMISSION",
                    field_path=f"proposed_omissions[{did}]",
                    message=f"Omission of {slot} asset {did!r} was not approved by user",
                )
            )

    # Nếu có bất kỳ lỗi BLOCKING nào từ các bước trên -> Chặn đứng All-or-Nothing
    has_blocking = any(iss.severity == "BLOCKING" for iss in issues)
    if has_blocking:
        return _make_blocked_plan(
            "plan_err_prep",
            "",
            owned_proposal.proposal_id,
            appr_rec.proposal_revision,
            appr_rec.approval_id,
            target_spec,
            issues,
            now_iso,
        )

    # 9. Phân bổ track và tạo danh sách PlannedTimelineAction
    planned_actions: list[PlannedTimelineAction] = []
    action_idx = 0
    timecode_offset = target_spec.start_timecode_frame
    tracks_used: set[Tuple[str, int]] = set()

    for seg in geom_result.assembled_segments:
        cid = seg.clip_id
        src_meta = source_clips_map.get(cid, {})
        file_path = str(src_meta.get("file_path", ""))

        target_track_type = "video"
        target_track_index = target_spec.video_track_mapping.get("a_roll", 1)
        role = "hook" if seg.origin == "global_hook" else "a_roll"

        # Tính toán khoảng timeline có tịnh tiến timecode offset
        tl_start = seg.timeline_range.start_frame + timecode_offset
        tl_end = seg.timeline_range.end_frame + timecode_offset
        tl_range = TimeRangeFrames(tl_start, tl_end)

        act_hash = compute_action_hash(
            action_idx,
            cid,
            target_track_type,
            target_track_index,
            tl_start,
            tl_end,
            seg.quantized_span.applied_speed,
        )

        action_params = {
            "clip_id": cid,
            "file_path": file_path,
            "source_in": seg.quantized_span.source_range.start_frame,
            "source_out": seg.quantized_span.source_range.end_frame,
            "timeline_in": tl_start,
            "timeline_out": tl_end,
            "speed": f"{seg.quantized_span.applied_speed.numerator}/{seg.quantized_span.applied_speed.denominator}",
            "role": role,
            "segment_index": seg.segment_index,
        }

        action = PlannedTimelineAction(
            action_index=action_idx,
            action_type="INSERT_CLIP",
            target_track_type=target_track_type,
            target_track_index=target_track_index,
            clip_id=cid,
            source_file_path=file_path,
            source_range=seg.quantized_span.source_range,
            timeline_range=tl_range,
            applied_speed=seg.quantized_span.applied_speed,
            role=role,
            action_idempotency_hash=act_hash,
            action_params=action_params,
        )
        planned_actions.append(action)
        tracks_used.add((target_track_type, target_track_index))
        action_idx += 1

    # Kiểm tra giới hạn trần số lượng planned actions
    if len(planned_actions) > MAX_PLANNED_ACTIONS:
        issues.append(
            PlanBindingIssue(
                severity="BLOCKING",
                issue_code="ERR_BUDGET_ACTIONS_OVERFLOW",
                field_path="planned_actions",
                message=f"Số lượng thao tác thi công ({len(planned_actions)}) vượt quá giới hạn trần {MAX_PLANNED_ACTIONS}",
            )
        )
        return _make_blocked_plan(
            "plan_err_budget",
            "",
            owned_proposal.proposal_id,
            appr_rec.proposal_revision,
            appr_rec.approval_id,
            target_spec,
            issues,
            now_iso,
        )

    # 10. Kiểm tra ảnh chụp chiếm dụng cho các track sử dụng
    if occupancy_snapshot is not None:
        scanned_tracks_set = set(occupancy_snapshot.scanned_tracks)
        for trk in tracks_used:
            if trk not in scanned_tracks_set:
                issues.append(
                    PlanBindingIssue(
                        severity="BLOCKING",
                        issue_code="ERR_OCCUPANCY_AMBIGUOUS",
                        track_info=f"{trk[0]}:{trk[1]}",
                        field_path="occupancy_snapshot.scanned_tracks",
                        message=f"Track {trk} was not explicitly scanned in authoritative snapshot; cannot assume free",
                    )
                )

        # 11. Kiểm tra va chạm ngoại vi (External collision with occupied_spans)
        for act in planned_actions:
            for occ in occupancy_snapshot.occupied_spans:
                if occ.track_type == act.target_track_type and occ.track_index == act.target_track_index:
                    # Công thức va chạm: max(start1, start2) < min(end1, end2)
                    if max(act.timeline_range.start_frame, occ.start_frame) < min(act.timeline_range.end_frame, occ.end_frame):
                        issues.append(
                            PlanBindingIssue(
                                severity="BLOCKING",
                                issue_code="ERR_TRACK_COLLISION",
                                track_info=f"{act.target_track_type}:{act.target_track_index}",
                                clip_id=act.clip_id,
                                field_path=f"planned_actions[{act.action_index}]",
                                message=f"Action [{act.timeline_range.start_frame}, {act.timeline_range.end_frame}) collides with existing item {occ.item_name!r} [{occ.start_frame}, {occ.end_frame})",
                            )
                        )

    # 12. Kiểm tra va chạm nội bộ (Internal collision between planned actions)
    for i in range(len(planned_actions)):
        for j in range(i + 1, len(planned_actions)):
            a1 = planned_actions[i]
            a2 = planned_actions[j]
            if a1.target_track_type == a2.target_track_type and a1.target_track_index == a2.target_track_index:
                if max(a1.timeline_range.start_frame, a2.timeline_range.start_frame) < min(a1.timeline_range.end_frame, a2.timeline_range.end_frame):
                    issues.append(
                        PlanBindingIssue(
                            severity="BLOCKING",
                            issue_code="ERR_PLAN_INTERNAL_COLLISION",
                            track_info=f"{a1.target_track_type}:{a1.target_track_index}",
                            field_path=f"planned_actions[{a1.action_index},{a2.action_index}]",
                            message=f"Internal collision between action {a1.action_index} and action {a2.action_index} on {a1.target_track_type}{a1.target_track_index}",
                        )
                    )

    # Nếu có lỗi BLOCKING sau kiểm tra va chạm -> Chặn đứng All-or-Nothing
    if any(iss.severity == "BLOCKING" for iss in issues):
        return _make_blocked_plan(
            "plan_err_collision",
            "",
            owned_proposal.proposal_id,
            appr_rec.proposal_revision,
            appr_rec.approval_id,
            target_spec,
            issues,
            now_iso,
        )

    # 13. Xây dựng TargetTrackReservation cho các dải track sử dụng
    track_reservations: list[TargetTrackReservation] = []
    for trk_type, trk_idx in sorted(tracks_used):
        trk_actions = [a for a in planned_actions if a.target_track_type == trk_type and a.target_track_index == trk_idx]
        if trk_actions:
            min_f = min(a.timeline_range.start_frame for a in trk_actions)
            max_f = max(a.timeline_range.end_frame for a in trk_actions)
            track_reservations.append(
                TargetTrackReservation(
                    track_type=trk_type,
                    track_index=trk_idx,
                    reserved_start_frame=min_f,
                    reserved_end_frame=max_f,
                    intended_usage=f"Automated Rough-Cut {trk_type.upper()}{trk_idx}",
                )
            )

    # 14. Tính toán khóa Idempotency tổng thể và Plan ID
    idempotency_key = compute_plan_idempotency_key(
        owned_proposal.proposal_id,
        appr_rec.proposal_revision,
        appr_rec.approval_id,
        target_spec.project_id,
        target_spec.timeline_id,
    )
    plan_id = f"plan_{idempotency_key[:16]}"

    total_frames = geom_result.total_timeline_frames if geom_result.total_timeline_frames is not None else 0
    total_dur_sec = geom_result.total_timeline_duration_sec if geom_result.total_timeline_duration_sec is not None else Fraction(0, 1)

    return TimelineExecutionPlan(
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        proposal_id=owned_proposal.proposal_id,
        proposal_revision=appr_rec.proposal_revision,
        approval_id=appr_rec.approval_id,
        approved_at_iso=appr_rec.approved_at_iso,
        target_spec=target_spec,
        occupancy_snapshot_id=occupancy_snapshot.snapshot_id if occupancy_snapshot else None,
        binding_status="BOUND_VERIFIED",
        planned_actions=tuple(planned_actions),
        planned_actions_count=len(planned_actions),
        track_reservations=tuple(track_reservations),
        source_digests=tuple(source_digests) if source_digests else (),
        catalog_digests=tuple(catalog_evidence.values()) if catalog_evidence else (),
        accepted_omissions=tuple(accepted_omission_records),
        total_timeline_frames=total_frames,
        total_timeline_duration_sec=total_dur_sec,
        issues=tuple(issues),
        created_at_iso=now_iso,
    )


def _make_blocked_plan(
    plan_id: str,
    idempotency_key: str,
    proposal_id: str,
    proposal_revision: str,
    approval_id: str,
    target_spec: TargetSpec,
    issues: list[PlanBindingIssue],
    created_at_iso: str,
) -> TimelineExecutionPlan:
    """Tạo kế hoạch bị chặn All-or-Nothing với planned_actions rỗng."""
    if not idempotency_key:
        idempotency_key = "0" * 64
    return TimelineExecutionPlan(
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        proposal_id=proposal_id if proposal_id else "unknown_proposal",
        proposal_revision=proposal_revision if proposal_revision else "0" * 64,
        approval_id=approval_id if approval_id else "unknown_approval",
        approved_at_iso=created_at_iso,
        target_spec=target_spec,
        occupancy_snapshot_id=None,
        binding_status="BOUND_BLOCKED",
        planned_actions=(),
        planned_actions_count=0,
        track_reservations=(),
        source_digests=(),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=0,
        total_timeline_duration_sec=Fraction(0, 1),
        issues=tuple(issues),
        created_at_iso=created_at_iso,
    )


# ==============================================================================
# XUẤT BẢN DRY-RUN JSON CÓ GIỚI HẠN TRẦN DUNG LƯỢNG (BOUNDED SERIALIZATION)
# ==============================================================================

def dry_run_report_to_dict(plan: TimelineExecutionPlan) -> dict[str, Any]:
    """Chuyển đổi TimelineExecutionPlan thành cấu trúc từ điển nguyên thủy JSON an toàn."""
    target_spec_dict = {
        "project_name": plan.target_spec.project_name,
        "project_id": plan.target_spec.project_id,
        "timeline_name": plan.target_spec.timeline_name,
        "timeline_id": plan.target_spec.timeline_id,
        "timeline_fps": f"{plan.target_spec.timeline_fps.numerator}/{plan.target_spec.timeline_fps.denominator}",
        "timeline_resolution": list(plan.target_spec.timeline_resolution),
        "start_timecode_frame": plan.target_spec.start_timecode_frame,
    }

    planned_actions_list = []
    for act in plan.planned_actions:
        planned_actions_list.append({
            "action_index": act.action_index,
            "action_type": act.action_type,
            "target_track_type": act.target_track_type,
            "target_track_index": act.target_track_index,
            "clip_id": act.clip_id,
            "source_file_path": act.source_file_path,
            "source_range_frames": [act.source_range.start_frame, act.source_range.end_frame],
            "timeline_range_frames": [act.timeline_range.start_frame, act.timeline_range.end_frame],
            "applied_speed": f"{act.applied_speed.numerator}/{act.applied_speed.denominator}",
            "role": act.role,
            "action_idempotency_hash": act.action_idempotency_hash,
        })

    track_reservations_list = []
    for res in plan.track_reservations:
        track_reservations_list.append({
            "track_type": res.track_type,
            "track_index": res.track_index,
            "reserved_start_frame": res.reserved_start_frame,
            "reserved_end_frame": res.reserved_end_frame,
            "intended_usage": res.intended_usage,
        })

    accepted_omissions_list = []
    for omit in plan.accepted_omissions:
        accepted_omissions_list.append({
            "demand_id": omit.demand_id,
            "slot": omit.slot,
            "reason": omit.reason,
            "accepted_by_approval_id": omit.accepted_by_approval_id,
        })

    issues_list = []
    for iss in plan.issues:
        issues_list.append({
            "severity": iss.severity,
            "issue_code": iss.issue_code,
            "field_path": iss.field_path,
            "clip_id": iss.clip_id,
            "track_info": iss.track_info,
            "message": iss.message,
        })

    return {
        "schema_version": plan.schema_version,
        "plan_id": plan.plan_id,
        "binding_status": plan.binding_status,
        "idempotency_key": plan.idempotency_key,
        "binding_metadata": {
            "proposal_id": plan.proposal_id,
            "proposal_revision": plan.proposal_revision,
            "approval_id": plan.approval_id,
            "approved_at_iso": plan.approved_at_iso,
            "created_at_iso": plan.created_at_iso,
        },
        "target_specification": target_spec_dict,
        "summary_metrics": {
            "planned_actions_count": plan.planned_actions_count,
            "total_timeline_frames": plan.total_timeline_frames,
            "total_timeline_duration_sec": f"{plan.total_timeline_duration_sec.numerator}/{plan.total_timeline_duration_sec.denominator}" if plan.total_timeline_duration_sec.denominator != 1 else str(plan.total_timeline_duration_sec.numerator),
            "track_reservations_count": len(plan.track_reservations),
            "omissions_count": len(plan.accepted_omissions),
            "issues_count": len(plan.issues),
        },
        "track_reservations": track_reservations_list,
        "planned_actions": planned_actions_list,
        "accepted_omissions": accepted_omissions_list,
        "issues": issues_list,
    }


def _count_json_nodes(obj: Any, budget: int) -> int:
    """Đếm số nodes trong cây JSON để bảo đảm không vượt quá ngân sách MAX_DRY_RUN_NODES."""
    count = 1
    if count > budget:
        raise ValueError(f"ERR_DRY_RUN_NODES_EXCEEDED: Vượt quá giới hạn {budget} nodes JSON")

    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            count += len(cur) * 2  # key + value
            if count > budget:
                raise ValueError(f"ERR_DRY_RUN_NODES_EXCEEDED: Vượt quá giới hạn {budget} nodes JSON")
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            count += len(cur)
            if count > budget:
                raise ValueError(f"ERR_DRY_RUN_NODES_EXCEEDED: Vượt quá giới hạn {budget} nodes JSON")
            stack.extend(cur)
    return count


def serialize_dry_run_report(
    plan: TimelineExecutionPlan,
    max_bytes: int = MAX_DRY_RUN_JSON_BYTES,
    max_nodes: int = MAX_DRY_RUN_NODES,
) -> str:
    """Xuất bản báo cáo Dry-Run dưới dạng chuỗi JSON sắp xếp khóa chuẩn tắc với kiểm tra biên an toàn."""
    data = dry_run_report_to_dict(plan)

    # 1. Kiểm tra giới hạn số nodes JSON
    _count_json_nodes(data, max_nodes)

    # 2. Serialize với sort_keys=True tất định
    json_str = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    # 3. Kiểm tra kích thước UTF-8: Bằng trần chấp nhận (<=), +1 bị từ chối (>)
    encoded = json_str.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"ERR_DRY_RUN_JSON_OVERFLOW: Kích thước báo cáo Dry-Run ({len(encoded)} bytes) "
            f"vượt quá trần cho phép ({max_bytes} bytes)"
        )

    return json_str


# ==============================================================================
# CANONICAL TIMELINE EXECUTION PLAN DIGEST (07B5 §2.6, 07B7 §5.2.1, Workorder R7)
# ==============================================================================

def serialize_canonical_plan_payload(plan: TimelineExecutionPlan) -> Dict[str, Any]:
    """
    Pure, deterministic canonical payload serializer for TimelineExecutionPlan (Workorder R7).
    Serializes the actual planned_actions and their full source-defined fields, including action_idempotency_hash.
    """
    if not isinstance(plan, TimelineExecutionPlan):
        raise TypeError(f"ERR_INVALID_PLAN_TYPE: expected TimelineExecutionPlan, got {type(plan).__name__}")

    actions_payload: List[Dict[str, Any]] = []
    for act in plan.planned_actions:
        actions_payload.append({
            "action_idempotency_hash": act.action_idempotency_hash,
            "action_index": act.action_index,
            "action_params": dict(act.action_params) if act.action_params else {},
            "action_type": act.action_type,
            "applied_speed": f"{act.applied_speed.numerator}/{act.applied_speed.denominator}",
            "clip_id": act.clip_id,
            "role": act.role,
            "source_file_path": act.source_file_path,
            "source_range_frames": [act.source_range.start_frame, act.source_range.end_frame],
            "target_track_index": act.target_track_index,
            "target_track_type": act.target_track_type,
            "timeline_range_frames": [act.timeline_range.start_frame, act.timeline_range.end_frame],
        })

    target_spec_dict = {
        "project_id": plan.target_spec.project_id,
        "project_name": plan.target_spec.project_name,
        "start_timecode_frame": plan.target_spec.start_timecode_frame,
        "timeline_fps": f"{plan.target_spec.timeline_fps.numerator}/{plan.target_spec.timeline_fps.denominator}",
        "timeline_id": plan.target_spec.timeline_id,
        "timeline_name": plan.target_spec.timeline_name,
        "timeline_resolution": list(plan.target_spec.timeline_resolution),
    }

    return {
        "approval_id": plan.approval_id,
        "binding_status": plan.binding_status,
        "idempotency_key": plan.idempotency_key,
        "plan_id": plan.plan_id,
        "planned_actions": actions_payload,
        "planned_actions_count": plan.planned_actions_count,
        "proposal_id": plan.proposal_id,
        "proposal_revision": plan.proposal_revision,
        "schema_version": plan.schema_version,
        "target_spec": target_spec_dict,
        "total_timeline_frames": plan.total_timeline_frames,
    }


def canonical_plan_bytes(plan: TimelineExecutionPlan) -> bytes:
    """Returns the RFC 8785 canonical bytes for a TimelineExecutionPlan."""
    from src.core.contracts.trial_authorization_contracts import canonical_json_bytes
    payload = serialize_canonical_plan_payload(plan)
    return canonical_json_bytes(payload)


def compute_canonical_plan_digest(plan: TimelineExecutionPlan) -> str:
    """Computes the SHA-256 hex digest of the canonical plan serialization (RFC 8785)."""
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()
