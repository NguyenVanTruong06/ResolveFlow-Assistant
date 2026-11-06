"""src/core/source_catalog_readiness.py
Bộ thẩm định siêu dữ liệu nguồn và thư viện tài nguyên (G07 Source & Catalog Readiness).

RÀNG BUỘC KỸ THUẬT:
- Thuần Python 3.12, KHÔNG phụ thuộc PySide6 / UI / DaVinci Resolve API / Model / Network.
- Module có thực hiện I/O siêu dữ liệu có kiểm soát qua default hoặc injected providers (không ghi nhãn pure).
- Zero media-byte read, zero hashing dữ liệu video, zero ffprobe / decoder / transcode ngầm.
- ready_to_execute BẮT BUỘC False trong mọi kết quả của lát cắt G07-07A2a.
- Tuân thủ triệt để quyết định D01, D02, D03 (docs/architecture/06C-product-decisions.md)
  và các quyết định ràng buộc G07-07A2 (docs/architecture/07A2-codex-decisions.md).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.core.action_guard import ActionGuardResult, check_action_availability
from src.core.cache_manager import is_canonical_sha256_hex
from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    approval_record_to_dict,
    verify_proposal_approval_binding,
)
from src.core.contracts.edit_plan_proposal_contracts import (
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.time_contracts import RationalFPS

MAX_CATALOG_RECORDS: int = 100_000

# Metadata stat identity: (st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)
StatIdentity = Tuple[int, int, int, int, int]


@dataclass(frozen=True)
class ReadinessRequest:
    """Yêu cầu tiền kiểm nguồn và thư viện tài nguyên cho một kịch bản đã được phê duyệt."""

    request_id: str
    generation: int
    proposal: EditPlanProposal
    approval: AppOwnedApprovalRecord
    verify_source_execution: bool = False
    timeline_fps: Optional[RationalFPS] = None

    def __init__(
        self,
        request_id: str,
        generation: int,
        proposal: Union[EditPlanProposal, Mapping[str, Any]],
        approval: Union[AppOwnedApprovalRecord, Mapping[str, Any]],
        *,
        verify_source_execution: bool = False,
        timeline_fps: Optional[Any] = None,
    ):
        # 1. Thẩm định request_id (chuỗi UTF-8 không rỗng, không chỉ khoảng trắng, giữ nguyên thô không trim)
        if type(request_id) is not str or not request_id or not request_id.strip():
            raise ValueError("ERR_INVALID_REQUEST_ID: request_id phải là kiểu str không rỗng (không chỉ chứa khoảng trắng).")
        if "\0" in request_id:
            raise ValueError("ERR_INVALID_REQUEST_ID: request_id chứa ký tự NUL không hợp lệ.")
        try:
            request_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"ERR_INVALID_REQUEST_ID: request_id không thể mã hóa UTF-8: {exc}") from exc

        # 2. Thẩm định generation (int nghiêm ngặt >= 0, loại trừ bool)
        if type(generation) is not int or isinstance(generation, bool) or generation < 0:
            raise ValueError(
                f"ERR_INVALID_GENERATION: generation phải là int >= 0 (loại trừ bool), nhận: {generation!r} ({type(generation).__name__})"
            )

        # 3. Re-validate proposal qua contract cốt lõi (tạo bản sao độc lập)
        if not isinstance(proposal, (EditPlanProposal, Mapping)):
            raise ValueError(f"ERR_INVALID_PROPOSAL: proposal phải là EditPlanProposal hoặc Mapping, nhận: {type(proposal).__name__}")
        try:
            owned_prop = normalize_edit_plan_proposal(proposal)
        except Exception as exc:
            raise ValueError(f"ERR_PROPOSAL_VALIDATION_FAILED: proposal không hợp lệ: {exc}") from exc

        # 4. Re-validate approval qua AppOwnedApprovalRecord (bọc an toàn mọi truy cập thuộc tính)
        try:
            if isinstance(approval, AppOwnedApprovalRecord):
                for attr in ("approval_id", "proposal_id", "proposal_revision", "approved_at_iso", "accepted_omission_ids", "approval_scope"):
                    if not hasattr(approval, attr):
                        raise ValueError(f"approval thiếu trường bắt buộc {attr!r}")
                appr_dict = approval_record_to_dict(approval)
            elif isinstance(approval, Mapping):
                appr_dict = dict(approval)
            else:
                raise ValueError(f"approval phải là AppOwnedApprovalRecord hoặc Mapping, nhận: {type(approval).__name__}")

            owned_appr = AppOwnedApprovalRecord(
                approval_id=appr_dict["approval_id"],
                proposal_id=appr_dict["proposal_id"],
                proposal_revision=appr_dict["proposal_revision"],
                approved_at_iso=appr_dict["approved_at_iso"],
                accepted_omission_ids=tuple(appr_dict["accepted_omission_ids"]),
                approval_scope=appr_dict["approval_scope"],
            )
        except Exception as exc:
            raise ValueError(f"ERR_APPROVAL_VALIDATION_FAILED: approval không hợp lệ: {exc}") from exc

        # 5. So khớp revision và ràng buộc toàn vẹn giữa proposal và approval
        current_rev = compute_proposal_revision(owned_prop)
        if owned_appr.proposal_revision != current_rev:
            raise ValueError(
                f"ERR_REVISION_MISMATCH: approval.proposal_revision ({owned_appr.proposal_revision!r}) "
                f"không khớp với proposal_revision hiện hành ({current_rev!r})"
            )

        if not verify_proposal_approval_binding(owned_prop, owned_appr):
            raise ValueError("ERR_BINDING_FAILED: verify_proposal_approval_binding thất bại giữa proposal và approval.")

        if owned_appr.approval_scope != "ENTIRE_PROPOSAL":
            raise ValueError(
                f"ERR_INVALID_SCOPE: approval.approval_scope phải là 'ENTIRE_PROPOSAL', nhận: {owned_appr.approval_scope!r}"
            )

        # 6. Thẩm định opt-in verify_source_execution và timeline_fps
        if type(verify_source_execution) is not bool:
            raise ValueError(
                f"ERR_INVALID_BOOL: verify_source_execution phải là bool thuần túy, nhận: {type(verify_source_execution).__name__}"
            )

        owned_tl_fps: Optional[RationalFPS] = None
        if timeline_fps is not None:
            from src.core.geometry.frame_geometry_contracts import validate_fps_instance
            owned_tl_fps = validate_fps_instance(timeline_fps, "ERR_INVALID_TIMELINE_FPS")

        if verify_source_execution is True and owned_tl_fps is None:
            raise ValueError(
                "ERR_MISSING_TIMELINE_FPS: verify_source_execution=True bắt buộc phải cung cấp timeline_fps hợp lệ."
            )

        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "proposal", owned_prop)
        object.__setattr__(self, "approval", owned_appr)
        object.__setattr__(self, "verify_source_execution", verify_source_execution)
        object.__setattr__(self, "timeline_fps", owned_tl_fps)


@dataclass(frozen=True)
class SourceClipObservation:
    """Kết quả quan sát siêu dữ liệu của một clip nguồn trong kịch bản."""

    clip_id: str
    path: str
    duration_sec: float
    exists: bool
    is_regular_file: bool
    is_non_empty: bool
    is_readable: bool
    stat_identity: Optional[StatIdentity] = None  # (dev, ino, size, mtime_ns, ctime_ns)
    recheck_passed: bool = False
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self):
        if type(self.clip_id) is not str or not self.clip_id:
            raise ValueError("ERR_INVALID_CLIP_ID: clip_id phải là kiểu str không rỗng.")
        if type(self.path) is not str:
            raise ValueError("ERR_INVALID_PATH: path phải là kiểu str.")
        if type(self.duration_sec) not in (int, float) or isinstance(self.duration_sec, bool) or math.isnan(self.duration_sec) or math.isinf(self.duration_sec) or self.duration_sec < 0:
            raise ValueError("ERR_INVALID_DURATION: duration_sec phải là số hữu hạn >= 0.")
        for b_name in ("exists", "is_regular_file", "is_non_empty", "is_readable", "recheck_passed"):
            if type(getattr(self, b_name)) is not bool:
                raise ValueError(f"ERR_INVALID_BOOL: {b_name} phải là bool thuần túy.")
        if self.stat_identity is not None:
            if not isinstance(self.stat_identity, tuple) or len(self.stat_identity) != 5 or not all(type(x) is int for x in self.stat_identity):
                raise ValueError("ERR_INVALID_STAT_IDENTITY: stat_identity phải là tuple 5 số nguyên (dev, ino, size, mtime_ns, ctime_ns).")
        if type(self.error_code) is not str or type(self.error_message) is not str:
            raise ValueError("ERR_INVALID_ERROR: error_code và error_message phải là str.")


@dataclass(frozen=True)
class AssetAssignmentObservation:
    """Kết quả thẩm tra khả dụng và thẩm quyền của một tài nguyên thư viện được gán."""

    demand_id: str
    slot: str
    asset_id: str
    source_kind: str
    found_in_catalog: bool
    duplicate_in_catalog: bool
    provenance_matched: bool
    guard_allowed: bool
    guard_status: str  # "online" | "offline" | "missing" | "staging"
    resolved_master_path: str = ""
    master_stat_identity: Optional[StatIdentity] = None
    recheck_passed: bool = False
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self):
        for s_name in ("demand_id", "slot", "asset_id", "source_kind", "guard_status", "resolved_master_path", "error_code", "error_message"):
            if type(getattr(self, s_name)) is not str:
                raise ValueError(f"ERR_INVALID_STR: {s_name} phải là str.")
        if self.guard_status not in ("online", "offline", "missing", "staging"):
            raise ValueError(f"ERR_INVALID_GUARD_STATUS: guard_status không hợp lệ: {self.guard_status!r}")
        for b_name in ("found_in_catalog", "duplicate_in_catalog", "provenance_matched", "guard_allowed", "recheck_passed"):
            if type(getattr(self, b_name)) is not bool:
                raise ValueError(f"ERR_INVALID_BOOL: {b_name} phải là bool thuần túy.")
        if self.master_stat_identity is not None:
            if not isinstance(self.master_stat_identity, tuple) or len(self.master_stat_identity) != 5 or not all(type(x) is int for x in self.master_stat_identity):
                raise ValueError("ERR_INVALID_STAT_IDENTITY: master_stat_identity phải là tuple 5 số nguyên.")


@dataclass(frozen=True)
class SourceCatalogReadinessResult:
    """Bản ghi kết quả tiền kiểm khả dụng nguồn và thư viện tài nguyên (bất biến, không chứa proxy)."""

    request_id: str
    generation: int
    proposal_id: str
    proposal_revision: str
    approval_id: str
    status: str  # "completed" | "cancelled" | "failed"
    checks_passed: bool
    ready_to_execute: bool  # BẮT BUỘC False trong toàn bộ lát cắt G07-07A2a
    source_observations: Tuple[SourceClipObservation, ...]
    catalog_observations: Tuple[AssetAssignmentObservation, ...]
    diagnostics: Tuple[str, ...]
    source_execution_evidence: Optional[Mapping[str, Any]] = None
    source_geometry: Optional[Any] = None

    def __post_init__(self):
        if type(self.request_id) is not str or not self.request_id.strip():
            raise ValueError("ERR_INVALID_REQUEST_ID: request_id phải là str không rỗng.")
        if type(self.generation) is not int or isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError(f"ERR_INVALID_GENERATION: generation phải là int >= 0 (loại trừ bool), nhận: {self.generation!r}")
        if type(self.proposal_id) is not str or not self.proposal_id.strip():
            raise ValueError("ERR_INVALID_PROPOSAL_ID: proposal_id phải là str không rỗng.")
        if type(self.proposal_revision) is not str or len(self.proposal_revision) != 64:
            raise ValueError(f"ERR_INVALID_REVISION: proposal_revision phải là chuỗi SHA-256 64 ký tự, nhận: {self.proposal_revision!r}")
        if type(self.approval_id) is not str or not self.approval_id.strip():
            raise ValueError("ERR_INVALID_APPROVAL_ID: approval_id phải là str không rỗng.")
        if type(self.status) is not str or self.status not in ("completed", "cancelled", "failed"):
            raise ValueError(f"status không hợp lệ: {self.status!r}. Chỉ chấp nhận 'completed', 'cancelled', 'failed'.")
        if type(self.checks_passed) is not bool:
            raise ValueError(f"ERR_INVALID_CHECKS_PASSED: checks_passed phải là bool thuần túy, nhận: {self.checks_passed!r}")
        if self.status in ("failed", "cancelled") and self.checks_passed is True:
            raise ValueError(f"status={self.status!r} không thể có checks_passed=True.")
        if self.ready_to_execute is not False:
            raise ValueError("ready_to_execute BẮT BUỘC phải là False trong lát cắt G07-07A2a.")

        if type(self.source_observations) not in (list, tuple):
            raise ValueError(f"ERR_INVALID_SOURCE_OBSERVATIONS: source_observations phải là list hoặc tuple, nhận: {type(self.source_observations).__name__}")
        for obs in self.source_observations:
            if not isinstance(obs, SourceClipObservation):
                raise ValueError(f"ERR_INVALID_OBSERVATION_TYPE: source_observation phải là SourceClipObservation, nhận {type(obs).__name__}")

        if type(self.catalog_observations) not in (list, tuple):
            raise ValueError(f"ERR_INVALID_CATALOG_OBSERVATIONS: catalog_observations phải là list hoặc tuple, nhận: {type(self.catalog_observations).__name__}")
        for obs in self.catalog_observations:
            if not isinstance(obs, AssetAssignmentObservation):
                raise ValueError(f"ERR_INVALID_OBSERVATION_TYPE: catalog_observation phải là AssetAssignmentObservation, nhận {type(obs).__name__}")

        if type(self.diagnostics) not in (list, tuple):
            raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics phải là list hoặc tuple, nhận: {type(self.diagnostics).__name__}")
        for d in self.diagnostics:
            if type(d) is not str:
                raise ValueError(f"ERR_INVALID_DIAGNOSTIC_TYPE: diagnostic item phải là str, nhận: {type(d).__name__}")

        # Bản sao bảo vệ sequences bên trong (chống mutation từ caller alias)
        object.__setattr__(self, "source_observations", tuple(self.source_observations))
        object.__setattr__(self, "catalog_observations", tuple(self.catalog_observations))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

        if self.source_execution_evidence is not None:
            if not isinstance(self.source_execution_evidence, Mapping):
                raise ValueError(
                    f"ERR_INVALID_EVIDENCE_MAPPING: source_execution_evidence phải là Mapping, "
                    f"nhận: {type(self.source_execution_evidence).__name__}"
                )
            from src.core.geometry.source_evidence_service import SourceExecutionEvidenceEnvelope
            cloned_ev: Dict[str, Any] = {}
            for k, v in self.source_execution_evidence.items():
                if type(k) is not str:
                    raise ValueError("ERR_INVALID_KEY: key của source_execution_evidence phải là str.")
                if not isinstance(v, SourceExecutionEvidenceEnvelope):
                    raise ValueError(
                        f"ERR_INVALID_ENVELOPE: mục trong source_execution_evidence[{k!r}] "
                        f"phải là SourceExecutionEvidenceEnvelope, nhận: {type(v).__name__}"
                    )
                cloned_ev[k] = v
            import types
            object.__setattr__(self, "source_execution_evidence", types.MappingProxyType(cloned_ev))

        if self.source_geometry is not None:
            from src.core.geometry.plan_geometry_contracts import PlanGeometryAssemblyResult
            if not isinstance(self.source_geometry, PlanGeometryAssemblyResult):
                raise ValueError(
                    f"ERR_INVALID_GEOMETRY: source_geometry phải là PlanGeometryAssemblyResult, "
                    f"nhận: {type(self.source_geometry).__name__}"
                )
            object.__setattr__(self, "source_geometry", self.source_geometry)


def _check_bool(val: Any, name: str) -> bool:
    if type(val) is not bool:
        raise ValueError(f"ERR_INVALID_BOOL: {name} phải là bool thuần túy, nhận: {type(val).__name__}")
    return val


def normalize_readiness_result(result: Any) -> SourceCatalogReadinessResult:
    """Chuẩn hóa, thẩm định nghiêm ngặt và sao chép độc quyền đối tượng kết quả SourceCatalogReadinessResult.

    Tạo thể hiện SourceCatalogReadinessResult mới cùng các thể hiện SourceClipObservation
    và AssetAssignmentObservation mới hoàn toàn độc lập, giải phóng mọi liên kết tham chiếu
    với producer.
    """
    if result is None:
        raise ValueError("ERR_INVALID_RESULT: result không được là None.")
    for attr in (
        "request_id",
        "generation",
        "proposal_id",
        "proposal_revision",
        "approval_id",
        "status",
        "checks_passed",
        "ready_to_execute",
        "source_observations",
        "catalog_observations",
        "diagnostics",
    ):
        if not hasattr(result, attr):
            raise ValueError(f"ERR_INVALID_RESULT: result thiếu trường bắt buộc {attr!r}")

    req_id = getattr(result, "request_id")
    gen = getattr(result, "generation")
    prop_id = getattr(result, "proposal_id")
    prop_rev = getattr(result, "proposal_revision")
    appr_id = getattr(result, "approval_id")
    status = getattr(result, "status")
    checks_passed = getattr(result, "checks_passed")
    ready_to_execute = getattr(result, "ready_to_execute")
    raw_s_obs = getattr(result, "source_observations")
    raw_c_obs = getattr(result, "catalog_observations")
    raw_diag = getattr(result, "diagnostics")

    # Kiểm tra raw types trước
    if type(req_id) is not str or not req_id.strip():
        raise ValueError("ERR_INVALID_REQUEST_ID: request_id phải là str không rỗng.")
    try:
        req_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"ERR_INVALID_REQUEST_ID: request_id không thể mã hóa UTF-8: {exc}")

    if type(gen) is not int or isinstance(gen, bool) or gen < 0:
        raise ValueError(f"ERR_INVALID_GENERATION: generation phải là int >= 0 (loại trừ bool), nhận: {gen!r}")

    if type(prop_id) is not str or not prop_id.strip():
        raise ValueError("ERR_INVALID_PROPOSAL_ID: proposal_id phải là str không rỗng.")

    if type(prop_rev) is not str or len(prop_rev) != 64:
        raise ValueError(f"ERR_INVALID_REVISION: proposal_revision phải là chuỗi SHA-256 64 ký tự, nhận: {prop_rev!r}")

    if type(appr_id) is not str or not appr_id.strip():
        raise ValueError("ERR_INVALID_APPROVAL_ID: approval_id phải là str không rỗng.")

    if type(status) is not str or status not in ("completed", "cancelled", "failed"):
        raise ValueError(f"ERR_INVALID_STATUS: status không hợp lệ: {status!r}")

    if type(checks_passed) is not bool:
        raise ValueError(f"ERR_INVALID_CHECKS_PASSED: checks_passed phải là bool thuần túy, nhận: {checks_passed!r}")

    if status in ("failed", "cancelled") and checks_passed is True:
        raise ValueError(f"ERR_INCONSISTENT_STATUS: status {status!r} không thể có checks_passed=True.")

    if ready_to_execute is not False:
        raise ValueError("ERR_READY_TO_EXECUTE_MUST_BE_FALSE: ready_to_execute bắt buộc là False.")

    if type(raw_s_obs) not in (list, tuple):
        raise ValueError(f"ERR_INVALID_SOURCE_OBSERVATIONS: source_observations phải là list hoặc tuple, nhận: {type(raw_s_obs).__name__}")

    if type(raw_c_obs) not in (list, tuple):
        raise ValueError(f"ERR_INVALID_CATALOG_OBSERVATIONS: catalog_observations phải là list hoặc tuple, nhận: {type(raw_c_obs).__name__}")

    if type(raw_diag) not in (list, tuple):
        raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics phải là list hoặc tuple, nhận: {type(raw_diag).__name__}")

    cloned_s_obs: List[SourceClipObservation] = []
    for item in raw_s_obs:
        if not isinstance(item, SourceClipObservation):
            raise ValueError(f"ERR_INVALID_OBSERVATION_TYPE: mục trong source_observations phải là SourceClipObservation, nhận: {type(item).__name__}")
        for attr in ("clip_id", "path", "duration_sec", "exists", "is_regular_file", "is_non_empty", "is_readable", "recheck_passed", "error_code", "error_message"):
            if not hasattr(item, attr):
                raise ValueError(f"ERR_INVALID_OBSERVATION: SourceClipObservation thiếu trường {attr!r}")

        c_dur = getattr(item, "duration_sec")
        if type(c_dur) not in (int, float) or isinstance(c_dur, bool) or math.isnan(c_dur) or math.isinf(c_dur) or c_dur < 0:
            raise ValueError("ERR_INVALID_DURATION: duration_sec phải là số hữu hạn >= 0.")

        c_stat_id = getattr(item, "stat_identity")
        if c_stat_id is not None:
            if not isinstance(c_stat_id, tuple) or len(c_stat_id) != 5 or not all(type(x) is int for x in c_stat_id):
                raise ValueError("ERR_INVALID_STAT_IDENTITY: stat_identity phải là tuple 5 số nguyên.")
            c_stat_id = tuple(c_stat_id)

        cloned_item = SourceClipObservation(
            clip_id=str(getattr(item, "clip_id")),
            path=str(getattr(item, "path")),
            duration_sec=float(c_dur),
            exists=_check_bool(getattr(item, "exists"), "exists"),
            is_regular_file=_check_bool(getattr(item, "is_regular_file"), "is_regular_file"),
            is_non_empty=_check_bool(getattr(item, "is_non_empty"), "is_non_empty"),
            is_readable=_check_bool(getattr(item, "is_readable"), "is_readable"),
            stat_identity=c_stat_id,
            recheck_passed=_check_bool(getattr(item, "recheck_passed"), "recheck_passed"),
            error_code=str(getattr(item, "error_code")),
            error_message=str(getattr(item, "error_message")),
        )
        cloned_s_obs.append(cloned_item)

    cloned_c_obs: List[AssetAssignmentObservation] = []
    for item in raw_c_obs:
        if not isinstance(item, AssetAssignmentObservation):
            raise ValueError(f"ERR_INVALID_OBSERVATION_TYPE: mục trong catalog_observations phải là AssetAssignmentObservation, nhận: {type(item).__name__}")
        for attr in ("demand_id", "slot", "asset_id", "source_kind", "found_in_catalog", "duplicate_in_catalog", "provenance_matched", "guard_allowed", "guard_status", "resolved_master_path", "recheck_passed", "error_code", "error_message"):
            if not hasattr(item, attr):
                raise ValueError(f"ERR_INVALID_OBSERVATION: AssetAssignmentObservation thiếu trường {attr!r}")

        m_stat_id = getattr(item, "master_stat_identity")
        if m_stat_id is not None:
            if not isinstance(m_stat_id, tuple) or len(m_stat_id) != 5 or not all(type(x) is int for x in m_stat_id):
                raise ValueError("ERR_INVALID_STAT_IDENTITY: master_stat_identity phải là tuple 5 số nguyên.")
            m_stat_id = tuple(m_stat_id)

        cloned_item = AssetAssignmentObservation(
            demand_id=str(getattr(item, "demand_id")),
            slot=str(getattr(item, "slot")),
            asset_id=str(getattr(item, "asset_id")),
            source_kind=str(getattr(item, "source_kind")),
            found_in_catalog=_check_bool(getattr(item, "found_in_catalog"), "found_in_catalog"),
            duplicate_in_catalog=_check_bool(getattr(item, "duplicate_in_catalog"), "duplicate_in_catalog"),
            provenance_matched=_check_bool(getattr(item, "provenance_matched"), "provenance_matched"),
            guard_allowed=_check_bool(getattr(item, "guard_allowed"), "guard_allowed"),
            guard_status=str(getattr(item, "guard_status")),
            resolved_master_path=str(getattr(item, "resolved_master_path")),
            master_stat_identity=m_stat_id,
            recheck_passed=_check_bool(getattr(item, "recheck_passed"), "recheck_passed"),
            error_code=str(getattr(item, "error_code")),
            error_message=str(getattr(item, "error_message")),
        )
        cloned_c_obs.append(cloned_item)

    cloned_diag: List[str] = []
    for d in raw_diag:
        if type(d) is not str:
            raise ValueError(f"ERR_INVALID_DIAGNOSTIC_TYPE: diagnostic item phải là str, nhận: {type(d).__name__}")
        cloned_diag.append(d)

    raw_see = getattr(result, "source_execution_evidence", None)
    cloned_see: Optional[Dict[str, Any]] = None
    if raw_see is not None:
        if not isinstance(raw_see, Mapping):
            raise ValueError(f"ERR_INVALID_EVIDENCE_MAPPING: source_execution_evidence phải là Mapping, nhận: {type(raw_see).__name__}")
        from src.core.geometry.source_evidence_service import SourceExecutionEvidenceEnvelope
        cloned_see = {}
        for k, v in raw_see.items():
            if type(k) is not str:
                raise ValueError("ERR_INVALID_KEY: key của source_execution_evidence phải là str.")
            if not isinstance(v, SourceExecutionEvidenceEnvelope):
                raise ValueError(
                    f"ERR_INVALID_ENVELOPE: mục trong source_execution_evidence[{k!r}] phải là SourceExecutionEvidenceEnvelope"
                )
            cloned_see[k] = SourceExecutionEvidenceEnvelope(
                clip_id=v.clip_id,
                manifest_id=v.manifest_id,
                file_path=v.file_path,
                stream_index=v.stream_index,
                expected_full_sha256=v.expected_full_sha256,
                observed_full_sha256=v.observed_full_sha256,
                freshness=v.freshness,
                frame_evidence=v.frame_evidence,
                complete_scan=v.complete_scan,
                profile_policy=v.profile_policy,
                diagnostics=v.diagnostics,
            )

    raw_geom = getattr(result, "source_geometry", None)
    cloned_geom: Optional[Any] = None
    if raw_geom is not None:
        from src.core.geometry.plan_geometry_contracts import PlanGeometryAssemblyResult
        if not isinstance(raw_geom, PlanGeometryAssemblyResult):
            raise ValueError(f"ERR_INVALID_GEOMETRY: source_geometry phải là PlanGeometryAssemblyResult, nhận: {type(raw_geom).__name__}")
        cloned_geom = PlanGeometryAssemblyResult(
            status=raw_geom.status,
            proposal_id=raw_geom.proposal_id,
            proposal_revision=raw_geom.proposal_revision,
            timeline_fps=raw_geom.timeline_fps,
            assembled_segments=raw_geom.assembled_segments,
            total_timeline_frames=raw_geom.total_timeline_frames,
            total_timeline_duration_sec=raw_geom.total_timeline_duration_sec,
            issues=raw_geom.issues,
            unresolved_reviews=raw_geom.unresolved_reviews,
        )

    return SourceCatalogReadinessResult(
        request_id=req_id,
        generation=gen,
        proposal_id=prop_id,
        proposal_revision=prop_rev,
        approval_id=appr_id,
        status=status,
        checks_passed=checks_passed,
        ready_to_execute=False,
        source_observations=tuple(cloned_s_obs),
        catalog_observations=tuple(cloned_c_obs),
        diagnostics=tuple(cloned_diag),
        source_execution_evidence=cloned_see,
        source_geometry=cloned_geom,
    )


def _extract_stat_identity(st: Any) -> StatIdentity:
    dev = getattr(st, "st_dev", 0)
    ino = getattr(st, "st_ino", 0)
    size = getattr(st, "st_size", 0)
    mtime_ns = getattr(st, "st_mtime_ns", int(getattr(st, "st_mtime", 0.0) * 1e9))
    ctime_ns = getattr(st, "st_ctime_ns", int(getattr(st, "st_ctime", 0.0) * 1e9))
    return (dev, ino, size, mtime_ns, ctime_ns)


def _safe_stat_identity(path_str: str, stat_fn: Callable[[str], Any]) -> Tuple[Optional[StatIdentity], Optional[str]]:
    """Lấy danh tính stat đầy đủ (dev, ino, size, mtime_ns, ctime_ns) an toàn, trả về (identity, error_code)."""
    try:
        st = stat_fn(path_str)
        return _extract_stat_identity(st), None
    except FileNotFoundError:
        return None, "ERR_FILE_NOT_FOUND"
    except PermissionError:
        return None, "ERR_FILE_NOT_READABLE"
    except OSError:
        return None, "ERR_STAT_IO_ERROR"


def validate_execution_evidence_binding(
    evidence_by_clip: Mapping[str, Any],
    source_clips_raw: Sequence[Any],
    source_observations: Sequence[SourceClipObservation],
    proposal_manifest_id: str,
) -> List[str]:
    """Xác thực ngữ nghĩa và tính toàn vẹn ràng buộc của bằng chứng thi công nguồn (G07-07A3-R2).

    Dùng chung cho inspect_source_and_catalog_readiness (core) và PreflightWorker (worker terminal).
    """
    errors: List[str] = []
    if not isinstance(evidence_by_clip, Mapping):
        errors.append("ERR_INVALID_EVIDENCE_MAPPING: evidence_by_clip không phải Mapping")
        return errors

    expected_clips: Dict[str, Dict[str, Any]] = {}
    for c in source_clips_raw:
        if isinstance(c, (Mapping, dict)) and "clip_id" in c:
            cid = str(c.get("clip_id", ""))
            cpath = str(c.get("path") or c.get("current_path") or "")
            s_idx = c.get("stream_index")
            exp_sha = c.get("expected_full_sha256")
            expected_clips[cid] = {
                "file_path": cpath,
                "stream_index": s_idx,
                "expected_full_sha256": exp_sha,
            }

    # Đồ thị bijection
    batch_keys = set(evidence_by_clip.keys())
    expected_keys = set(expected_clips.keys())
    if batch_keys != expected_keys:
        errors.append(f"ERR_GRAPH_BIJECTION_FAILED: tập clip IDs {batch_keys!r} != kỳ vọng {expected_keys!r}")

    source_obs_by_clip = {obs.clip_id: obs for obs in source_observations if hasattr(obs, "clip_id")}

    for cid, c_info in expected_clips.items():
        env = evidence_by_clip.get(cid)
        if env is None:
            errors.append(f"ERR_MISSING_EVIDENCE: Không tìm thấy bằng chứng cho clip {cid!r}")
            continue

        # 1. Manifest binding
        env_manifest = getattr(env, "manifest_id", "")
        if env_manifest != proposal_manifest_id:
            errors.append(f"ERR_MANIFEST_ID_MISMATCH: clip {cid!r} envelope manifest_id {env_manifest!r} != {proposal_manifest_id!r}")

        # 2. Complete scan & profile policy
        if not getattr(env, "complete_scan", False):
            errors.append(f"ERR_INCOMPLETE_SOURCE_PROOF: clip {cid!r} có complete_scan là False")

        if getattr(env, "profile_policy", "") != "decoded_frame_uniform_ticks_v1":
            errors.append(f"ERR_UNTRUSTED_PROFILE_POLICY: clip {cid!r} có profile_policy {getattr(env, 'profile_policy', None)!r}")

        # 3. Path match
        env_path = getattr(env, "file_path", "")
        exp_path = c_info["file_path"]
        if not env_path or not exp_path or os.path.normpath(env_path) != os.path.normpath(exp_path):
            errors.append(f"ERR_SOURCE_PATH_MISMATCH: clip {cid!r} đường dẫn {env_path!r} != kịch bản {exp_path!r}")

        # 4. Stream match
        exp_stream = c_info["stream_index"]
        env_stream = getattr(env, "stream_index", None)
        if exp_stream is not None and env_stream != exp_stream:
            errors.append(f"ERR_SOURCE_STREAM_MISMATCH: clip {cid!r} stream_index {env_stream!r} != kịch bản {exp_stream!r}")

        # 5. Digest match
        exp_sha = c_info["expected_full_sha256"]
        env_exp_sha = getattr(env, "expected_full_sha256", None)
        env_obs_sha = getattr(env, "observed_full_sha256", None)
        if exp_sha is None:
            errors.append(f"ERR_UNBOUND_CONTENT_PROVENANCE: clip {cid!r} thiếu expected_full_sha256 trong kịch bản")
        else:
            exp_sha_lower = exp_sha.lower()
            if env_exp_sha is None or env_exp_sha.lower() != exp_sha_lower:
                errors.append(f"ERR_EXPECTED_DIGEST_MISMATCH: clip {cid!r} expected SHA {env_exp_sha!r} != kịch bản {exp_sha_lower!r}")
            if env_obs_sha is None or env_obs_sha.lower() != exp_sha_lower:
                errors.append(f"ERR_SOURCE_DIGEST_MISMATCH: clip {cid!r} observed SHA {env_obs_sha!r} != kỳ vọng {exp_sha_lower!r}")

        # 6. Frame evidence CFR & manifest
        fe = getattr(env, "frame_evidence", None)
        if fe is None:
            errors.append(f"ERR_MISSING_FRAME_EVIDENCE: clip {cid!r} thiếu frame_evidence")
        else:
            if getattr(fe, "evidence_status", "") != "VERIFIED_CFR":
                errors.append(f"ERR_SOURCE_NOT_CFR: clip {cid!r} frame_evidence không đạt VERIFIED_CFR ({getattr(fe, 'evidence_status', '')})")
            if getattr(fe, "manifest_id", "") != proposal_manifest_id:
                errors.append(f"ERR_FRAME_MANIFEST_MISMATCH: clip {cid!r} frame_evidence manifest {getattr(fe, 'manifest_id', '')!r} != {proposal_manifest_id!r}")
            if getattr(fe, "clip_id", "") != cid:
                errors.append(f"ERR_FRAME_CLIP_MISMATCH: clip {cid!r} frame_evidence clip_id {getattr(fe, 'clip_id', '')!r} != {cid!r}")

        # 7. Five-stat freshness
        fresh = getattr(env, "freshness", None)
        if fresh is None:
            errors.append(f"ERR_MISSING_SOURCE_FRESHNESS: clip {cid!r} thiếu freshness")
        else:
            s_obs = source_obs_by_clip.get(cid)
            if s_obs is not None and s_obs.stat_identity is not None:
                fresh_ident = (
                    fresh.device_id,
                    fresh.inode_number,
                    fresh.file_size_bytes,
                    fresh.mtime_ns,
                    fresh.ctime_ns,
                )
                if fresh_ident != s_obs.stat_identity:
                    errors.append(
                        f"ERR_SOURCE_FRESHNESS_MISMATCH: clip {cid!r} freshness {fresh_ident!r} "
                        f"không khớp stat_identity đã ghi nhận {s_obs.stat_identity!r}"
                    )

    return errors


def _recheck_catalog_authorities(
    *,
    storage_instance: Any,
    catalog_not_required: bool,
    storage_context_reader: Optional[Callable[[Any], Any]],
    initial_storage_context: Any,
    marker_file_path: Optional[str],
    initial_storage_marker_stat: Any,
    registry_file_path: Optional[str],
    initial_storage_registry_stat: Any,
    initial_storage_disk_cfg: Any,
    stat_fn: Callable[[str], Any],
    diagnostics: List[str],
    phase_label: str = "",
) -> bool:
    """Tái kiểm tra tính toàn vẹn của thẩm quyền catalog (ngữ cảnh, marker, registry, disk config).

    Trả về True nếu mọi kiểm tra hợp lệ hoặc không yêu cầu catalog; False nếu có bất kỳ biến động nào.
    """
    if storage_instance is None or catalog_not_required:
        return True

    all_ok = True
    try:
        # 1. Ngữ cảnh kho qua storage_context_reader
        if storage_context_reader is not None:
            try:
                curr_ctx = storage_context_reader(storage_instance)
                if initial_storage_context is not None and curr_ctx != initial_storage_context:
                    diagnostics.append(
                        f"ERR_STORAGE_CONFIG_SWAPPED: Cấu hình kho lưu trữ đã bị thay đổi{phase_label}."
                    )
                    all_ok = False
            except Exception as exc:
                diagnostics.append(f"ERR_STORAGE_CONTEXT_FAILED: Lỗi tái kiểm tra ngữ cảnh kho{phase_label}: {exc}")
                all_ok = False

        # 2. Marker file
        if marker_file_path and initial_storage_marker_stat is not None:
            curr_m, m_err = _safe_stat_identity(marker_file_path, stat_fn)
            if m_err or curr_m != initial_storage_marker_stat:
                diagnostics.append(
                    f"ERR_STORAGE_MARKER_MUTATED: Marker library.json của kho đã bị thay đổi{phase_label}."
                )
                all_ok = False
        elif not catalog_not_required and initial_storage_marker_stat is None:
            diagnostics.append(f"ERR_STORAGE_MARKER_UNVERIFIED: Không thể xác thực marker kho{phase_label}.")
            all_ok = False

        # 3. Registry file
        if registry_file_path and initial_storage_registry_stat is not None:
            curr_r, r_err = _safe_stat_identity(registry_file_path, stat_fn)
            if r_err or curr_r != initial_storage_registry_stat:
                diagnostics.append(
                    f"ERR_STORAGE_REGISTRY_MUTATED: Registry của kho đã bị thay đổi{phase_label}."
                )
                all_ok = False
        elif not catalog_not_required and initial_storage_registry_stat is None:
            diagnostics.append(f"ERR_STORAGE_REGISTRY_UNVERIFIED: Không thể xác thực registry kho{phase_label}.")
            all_ok = False

        # 4. Tệp cấu hình đĩa (disk config)
        if hasattr(storage_instance, "config_file") and storage_instance.config_file:
            cfg_file = Path(storage_instance.config_file)
            if not cfg_file.is_file():
                diagnostics.append(f"ERR_STORAGE_CONFIG_VANISHED: Tệp cấu hình kho đã bị xóa{phase_label}.")
                all_ok = False
            else:
                try:
                    from src.core.storage_manager import _strict_read_json
                    final_disk_cfg = _strict_read_json(cfg_file)
                    if initial_storage_disk_cfg is None or final_disk_cfg != initial_storage_disk_cfg:
                        diagnostics.append(
                            f"ERR_STORAGE_CONFIG_SWAPPED: Cấu hình kho trên đĩa đã bị thay đổi{phase_label}."
                        )
                        all_ok = False
                except Exception as exc:
                    diagnostics.append(
                        f"ERR_STORAGE_CONFIG_UNREADABLE: Không thể đọc cấu hình kho trên đĩa{phase_label}: {exc}"
                    )
                    all_ok = False
    except Exception as exc:
        diagnostics.append(f"ERR_STORAGE_RECHECK_FAILED: Lỗi khi tái kiểm tra cấu hình kho{phase_label}: {exc}")
        all_ok = False

    return all_ok


def inspect_source_and_catalog_readiness(
    request: ReadinessRequest,
    *,
    storage_provider: Optional[Callable[[], Any]] = None,
    storage_context_reader: Optional[Callable[[Any], Any]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    stat_fn: Optional[Callable[[str], Any]] = None,
    realpath_fn: Optional[Callable[[str], str]] = None,
    bundled_root: Optional[Union[str, Path]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    evidence_service: Optional[Any] = None,
) -> SourceCatalogReadinessResult:
    """Thẩm tra khả dụng siêu dữ liệu nguồn và catalog tài nguyên cho một ReadinessRequest đã được xác thực.

    Ràng buộc:
    - Thuần túy kiểm tra siêu dữ liệu trên hệ thống tệp tin và catalog.
    - Zero media byte reads, zero hash video, zero ffprobe, zero Resolve calls.
    - Nếu assignments rỗng: catalog not_required, 0 cuộc gọi storage_provider / config.
    - ready_to_execute luôn là False.
    """
    # 0. Thẩm định chặt chẽ request tại ngõ vào
    if not isinstance(request, ReadinessRequest):
        raise ValueError(f"ERR_INVALID_REQUEST: request phải là ReadinessRequest, nhận: {type(request).__name__}")
    if type(request.request_id) is not str or not request.request_id or not request.request_id.strip():
        raise ValueError("ERR_INVALID_REQUEST_ID: request_id không hợp lệ hoặc để trống.")
    if type(request.generation) is not int or isinstance(request.generation, bool) or request.generation < 0:
        raise ValueError("ERR_INVALID_GENERATION: generation phải là int >= 0 (loại trừ bool).")
    if not hasattr(request, "proposal") or not hasattr(request, "approval"):
        raise ValueError("ERR_INVALID_REQUEST: request thiếu thuộc tính proposal hoặc approval.")

    # Re-validate proposal and approval binding trước mọi thao tác I/O
    if not isinstance(request.approval, AppOwnedApprovalRecord):
        raise ValueError("ERR_APPROVAL_VALIDATION_FAILED: approval phải là AppOwnedApprovalRecord")

    for attr in ("approval_id", "proposal_id", "proposal_revision", "approved_at_iso", "accepted_omission_ids", "approval_scope"):
        if not hasattr(request.approval, attr):
            raise ValueError(f"ERR_APPROVAL_VALIDATION_FAILED: approval thiếu trường {attr!r}")

    expected_rev = compute_proposal_revision(request.proposal)
    if request.approval.proposal_revision != expected_rev:
        raise ValueError(
            f"ERR_REVISION_MISMATCH: approval.proposal_revision ({request.approval.proposal_revision!r}) "
            f"không khớp proposal_revision ({expected_rev!r})"
        )

    if not verify_proposal_approval_binding(request.proposal, request.approval):
        raise ValueError("ERR_BINDING_FAILED: verify_proposal_approval_binding thất bại giữa proposal và approval.")

    if request.approval.approval_scope != "ENTIRE_PROPOSAL":
        raise ValueError(f"ERR_INVALID_SCOPE: approval.approval_scope phải là 'ENTIRE_PROPOSAL', nhận: {request.approval.approval_scope!r}")

    # Thẩm định sớm các trường của chế độ deep trước khi thực hiện bất kỳ thao tác I/O siêu dữ liệu nào
    if request.verify_source_execution:
        if request.timeline_fps is None or not isinstance(request.timeline_fps, RationalFPS):
            raise ValueError(
                f"ERR_INVALID_TIMELINE_FPS: timeline_fps phải là RationalFPS khi verify_source_execution=True, "
                f"nhận: {request.timeline_fps!r}"
            )
        sb = getattr(request.proposal, "source_binding", None)
        source_clips_check = ()
        if isinstance(sb, (Mapping, dict)):
            source_clips_check = sb.get("clips", ()) or ()
        elif hasattr(sb, "clips"):
            source_clips_check = getattr(sb, "clips", ()) or ()

        for c_idx, c in enumerate(source_clips_check):
            c_sidx = c.get("stream_index") if isinstance(c, (Mapping, dict)) else getattr(c, "stream_index", None)
            if c_sidx is not None and (type(c_sidx) is not int or isinstance(c_sidx, bool) or c_sidx < 0):
                raise ValueError(f"ERR_INVALID_STREAM_INDEX: clip[{c_idx}] stream_index không được là bool hoặc âm, nhận: {c_sidx!r}")
            c_sha = c.get("expected_full_sha256") if isinstance(c, (Mapping, dict)) else getattr(c, "expected_full_sha256", None)
            if c_sha is not None and (type(c_sha) is not str or isinstance(c_sha, bool) or not is_canonical_sha256_hex(c_sha)):
                raise ValueError(f"ERR_INVALID_DIGEST: clip[{c_idx}] expected_full_sha256 không hợp lệ: {c_sha!r}")

    _stat = stat_fn or os.stat
    _realpath = realpath_fn or os.path.realpath
    _cancel_check = is_cancelled or (lambda: False)

    req_id = request.request_id
    gen = request.generation
    prop = request.proposal
    appr = request.approval
    prop_id = prop.proposal_id
    prop_rev = appr.proposal_revision
    appr_id = appr.approval_id

    diagnostics: List[str] = []

    # Checkpoint 0: Cancel trước khi bắt đầu
    if _cancel_check():
        return SourceCatalogReadinessResult(
            request_id=req_id,
            generation=gen,
            proposal_id=prop_id,
            proposal_revision=prop_rev,
            approval_id=appr_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=(),
            catalog_observations=(),
            diagnostics=("Tiến trình tiền kiểm đã bị hủy theo yêu cầu.",),
        )

    # -------------------------------------------------------------------------
    # 1. Thẩm định clip nguồn (proposal.source_binding.clips)
    # -------------------------------------------------------------------------
    source_clips_raw = prop.source_binding.get("clips", ())
    source_obs_list: List[SourceClipObservation] = []
    all_sources_ok = True

    total_sources = len(source_clips_raw)
    total_assignments = len(prop.proposed_asset_assignments)
    total_items = max(1, total_sources + total_assignments)

    for idx, clip in enumerate(source_clips_raw):
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(source_obs_list),
                catalog_observations=(),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang kiểm tra nguồn.",),
            )

        cid = str(clip.get("clip_id", ""))
        cpath = str(clip.get("path") or clip.get("current_path") or "")
        dur_val = clip.get("duration") if clip.get("duration") is not None else clip.get("duration_sec", 0.0)
        cdur = float(dur_val or 0.0)

        if progress_callback:
            percent = int((idx / total_items) * 100)
            progress_callback(percent, f"Đang kiểm tra clip nguồn {cid}...")

        # Thẩm định đường dẫn và tệp tin
        if not cpath.strip():
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=False,
                    is_regular_file=False,
                    is_non_empty=False,
                    is_readable=False,
                    error_code="ERR_SOURCE_PATH_EMPTY",
                    error_message=f"Đường dẫn nguồn của clip {cid!r} để trống.",
                )
            )
            all_sources_ok = False
            continue

        if not os.path.isabs(cpath):
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=False,
                    is_regular_file=False,
                    is_non_empty=False,
                    is_readable=False,
                    error_code="ERR_SOURCE_PATH_NOT_ABSOLUTE",
                    error_message=f"Đường dẫn clip {cid!r} không phải là đường dẫn tuyệt đối: {cpath!r}",
                )
            )
            all_sources_ok = False
            continue

        try:
            st = _stat(cpath)
            exists = True
            is_reg = stat.S_ISREG(st.st_mode)
            is_non_empty = st.st_size > 0
            is_readable = os.access(cpath, os.R_OK)
            stat_id = _extract_stat_identity(st)
            stat_err = None
        except FileNotFoundError:
            exists = False
            is_reg = False
            is_non_empty = False
            is_readable = False
            stat_id = None
        except PermissionError:
            exists = True
            is_reg = False
            is_non_empty = False
            is_readable = False
            stat_id = None
        except OSError:
            exists = False
            is_reg = False
            is_non_empty = False
            is_readable = False
            stat_id = None

        if not exists:
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=False,
                    is_regular_file=False,
                    is_non_empty=False,
                    is_readable=False,
                    error_code="ERR_SOURCE_NOT_FOUND",
                    error_message=f"Tệp nguồn của clip {cid!r} không tồn tại trên đĩa: {cpath!r}",
                )
            )
            all_sources_ok = False
        elif not is_reg:
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=True,
                    is_regular_file=False,
                    is_non_empty=is_non_empty,
                    is_readable=is_readable,
                    error_code="ERR_SOURCE_NOT_REGULAR_FILE",
                    error_message=f"Đường dẫn nguồn của clip {cid!r} không phải là tệp tin thông thường (regular file): {cpath!r}",
                )
            )
            all_sources_ok = False
        elif not is_non_empty:
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=True,
                    is_regular_file=True,
                    is_non_empty=False,
                    is_readable=is_readable,
                    error_code="ERR_SOURCE_EMPTY",
                    error_message=f"Tệp nguồn của clip {cid!r} có kích thước 0 byte: {cpath!r}",
                )
            )
            all_sources_ok = False
        elif not is_readable:
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=True,
                    is_regular_file=True,
                    is_non_empty=True,
                    is_readable=False,
                    error_code="ERR_SOURCE_NOT_READABLE",
                    error_message=f"Tiến trình không có quyền đọc tệp nguồn của clip {cid!r}: {cpath!r}",
                )
            )
            all_sources_ok = False
        else:
            source_obs_list.append(
                SourceClipObservation(
                    clip_id=cid,
                    path=cpath,
                    duration_sec=cdur,
                    exists=True,
                    is_regular_file=True,
                    is_non_empty=True,
                    is_readable=True,
                    stat_identity=stat_id,
                    recheck_passed=False,
                )
            )

    # -------------------------------------------------------------------------
    # 2. Thẩm định catalog tài nguyên (proposal.proposed_asset_assignments)
    # -------------------------------------------------------------------------
    catalog_obs_list: List[AssetAssignmentObservation] = []
    all_catalog_ok = True
    catalog_not_required = len(prop.proposed_asset_assignments) == 0

    storage_instance = None
    initial_storage_context: Optional[Any] = None
    initial_storage_marker_stat: Optional[StatIdentity] = None
    initial_storage_registry_stat: Optional[StatIdentity] = None
    marker_file_path: Optional[str] = None
    registry_file_path: Optional[str] = None
    initial_storage_disk_cfg: Optional[Dict[str, Any]] = None

    if catalog_not_required:
        diagnostics.append("Không có yêu cầu tài nguyên thư viện nào được gán; bỏ qua kiểm tra catalog.")
    else:
        # Checkpoint 1: Trước khi gọi storage provider / factory
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(source_obs_list),
                catalog_observations=(),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy trước khi nạp catalog.",),
            )

        # 2.1 Khởi tạo storage manager an toàn
        try:
            if storage_provider is not None:
                storage_instance = storage_provider()
            else:
                from src.core.storage_manager import StorageManager
                storage_instance = StorageManager()
        except Exception as exc:
            diagnostics.append(f"ERR_STORAGE_FACTORY_FAILED: Không thể khởi tạo StorageManager: {exc}")
            all_catalog_ok = False
            storage_instance = None

        if storage_instance is None:
            if not any(d.startswith("ERR_STORAGE_FACTORY_FAILED") for d in diagnostics):
                diagnostics.append("ERR_STORAGE_UNAVAILABLE: StorageManager không khả dụng (factory trả về None).")
            all_catalog_ok = False
            for assign in prop.proposed_asset_assignments:
                catalog_obs_list.append(
                    AssetAssignmentObservation(
                        demand_id=str(assign.get("demand_id", "")),
                        slot=str(assign.get("slot", "")),
                        asset_id=str(assign.get("asset_id", "")),
                        source_kind=str(assign.get("source_kind", "")),
                        found_in_catalog=False,
                        duplicate_in_catalog=False,
                        provenance_matched=False,
                        guard_allowed=False,
                        guard_status="offline",
                        error_code="ERR_STORAGE_UNAVAILABLE",
                        error_message="StorageManager không khả dụng (factory trả về None).",
                    )
                )

        # Checkpoint 2: Sau khi tạo factory, trước khi đọc assets
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(source_obs_list),
                catalog_observations=tuple(catalog_obs_list),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy sau khi nạp StorageManager.",),
            )

        if storage_instance is not None:
            # 2.2 Kiểm tra trạng thái vận hành sống của kho (không tin mù snapshot offline)
            try:
                from src.core.storage_manager import StorageState
                raw_state = storage_instance.get_state()
                if hasattr(raw_state, "value"):
                    st_val = str(raw_state.value).lower()
                else:
                    st_val = str(raw_state).lower()
            except Exception as exc:
                st_val = "error"
                diagnostics.append(f"ERR_STORAGE_STATE_QUERY_FAILED: Lỗi truy vấn trạng thái kho: {exc}")

            if st_val not in ("online", "read_only", "full"):
                err_msg = f"Kho tài nguyên ở trạng thái '{st_val}', không khả dụng cho thi công (yêu cầu 'online', 'read_only' hoặc 'full')."
                diagnostics.append(f"ERR_STORAGE_UNAVAILABLE: {err_msg}")
                all_catalog_ok = False
                for assign in prop.proposed_asset_assignments:
                    catalog_obs_list.append(
                        AssetAssignmentObservation(
                            demand_id=str(assign.get("demand_id", "")),
                            slot=str(assign.get("slot", "")),
                            asset_id=str(assign.get("asset_id", "")),
                            source_kind=str(assign.get("source_kind", "")),
                            found_in_catalog=False,
                            duplicate_in_catalog=False,
                            provenance_matched=False,
                            guard_allowed=False,
                            guard_status="offline",
                            error_code="ERR_STORAGE_OFFLINE",
                            error_message=err_msg,
                        )
                    )
            else:
                # 2.3 Ghi nhận danh tính ban đầu của marker, registry và cấu hình kho
                if storage_context_reader is not None:
                    try:
                        initial_storage_context = storage_context_reader(storage_instance)
                    except Exception as exc:
                        diagnostics.append(f"ERR_STORAGE_CONTEXT_FAILED: Lỗi đọc ngữ cảnh kho: {exc}")
                        all_catalog_ok = False

                active_root = getattr(storage_instance, "root", None)
                if not active_root or not isinstance(active_root, (str, Path)) or not str(active_root).strip():
                    diagnostics.append("ERR_STORAGE_ROOT_INVALID: Thư mục gốc của kho không hợp lệ hoặc để trống.")
                    all_catalog_ok = False
                else:
                    active_root_p = Path(active_root)
                    m_path = active_root_p / ".chundvc_meta" / "library.json"
                    r_path = active_root_p / ".chundvc_meta" / "registry.json"

                    if not m_path.is_file():
                        diagnostics.append(f"ERR_STORAGE_MARKER_MISSING: Không tìm thấy tệp marker library.json tại {m_path}.")
                        all_catalog_ok = False
                    else:
                        marker_file_path = str(m_path)
                        initial_storage_marker_stat, m_err = _safe_stat_identity(marker_file_path, _stat)
                        if m_err or initial_storage_marker_stat is None:
                            diagnostics.append(f"ERR_STORAGE_MARKER_STAT_FAILED: Lỗi đọc marker {m_path}: {m_err}")
                            all_catalog_ok = False

                    if not r_path.is_file():
                        diagnostics.append(f"ERR_STORAGE_REGISTRY_MISSING: Không tìm thấy tệp registry.json tại {r_path}.")
                        all_catalog_ok = False
                    else:
                        registry_file_path = str(r_path)
                        initial_storage_registry_stat, r_err = _safe_stat_identity(registry_file_path, _stat)
                        if r_err or initial_storage_registry_stat is None:
                            diagnostics.append(f"ERR_STORAGE_REGISTRY_STAT_FAILED: Lỗi đọc registry {r_path}: {r_err}")
                            all_catalog_ok = False

                if hasattr(storage_instance, "registry_file") and getattr(storage_instance, "registry_file"):
                    registry_file_path = str(getattr(storage_instance, "registry_file"))
                    if initial_storage_registry_stat is None:
                        initial_storage_registry_stat, _ = _safe_stat_identity(registry_file_path, _stat)
                if hasattr(storage_instance, "marker_file") and getattr(storage_instance, "marker_file"):
                    marker_file_path = str(getattr(storage_instance, "marker_file"))
                    if initial_storage_marker_stat is None:
                        initial_storage_marker_stat, _ = _safe_stat_identity(marker_file_path, _stat)

                cfg_file_attr = getattr(storage_instance, "config_file", None)
                if not cfg_file_attr or not isinstance(cfg_file_attr, (str, Path)):
                    diagnostics.append("ERR_STORAGE_CONFIG_FILE_MISSING: Kho thiếu thuộc tính config_file.")
                    all_catalog_ok = False
                else:
                    cfg_file = Path(cfg_file_attr)
                    if not cfg_file.is_file():
                        diagnostics.append(f"ERR_STORAGE_CONFIG_MISSING: Tệp cấu hình kho không tồn tại: {cfg_file}.")
                        all_catalog_ok = False
                    else:
                        try:
                            from src.core.storage_manager import _strict_read_json
                            initial_storage_disk_cfg = _strict_read_json(cfg_file)
                            if not isinstance(initial_storage_disk_cfg, dict):
                                diagnostics.append("ERR_STORAGE_CONFIG_INVALID: Nội dung tệp cấu hình kho không phải là dict.")
                                all_catalog_ok = False
                            else:
                                disk_root = initial_storage_disk_cfg.get("root")
                                disk_lib_id = initial_storage_disk_cfg.get("library_id")

                                # So sánh loaded config vs disk config trên chính core default
                                loaded_cfg = getattr(storage_instance, "_config", None)
                                if isinstance(loaded_cfg, Mapping):
                                    loaded_root = loaded_cfg.get("root")
                                    loaded_lib_id = loaded_cfg.get("library_id")
                                    if loaded_root != disk_root or loaded_lib_id != disk_lib_id:
                                        diagnostics.append("ERR_STORAGE_CONFIG_MISMATCH: Cấu hình kho đã tải không khớp cấu hình trên đĩa.")
                                        all_catalog_ok = False

                                if active_root and str(active_root) != str(disk_root):
                                    diagnostics.append("ERR_STORAGE_ROOT_MISMATCH: Thư mục gốc kho không khớp disk config.")
                                    all_catalog_ok = False

                                active_lib_id = getattr(storage_instance, "library_id", None)
                                if active_lib_id is not None and str(active_lib_id) != str(disk_lib_id):
                                    diagnostics.append("ERR_STORAGE_LIBRARY_MISMATCH: library_id kho không khớp disk config.")
                                    all_catalog_ok = False
                        except Exception as exc:
                            diagnostics.append(f"ERR_STORAGE_CONFIG_READ_FAILED: Lỗi đọc tệp cấu hình kho: {exc}")
                            all_catalog_ok = False

                # 2.4 Đọc assets(raise_on_error=True)
                raw_assets: List[Dict[str, Any]] = []
                try:
                    raw_assets = storage_instance.assets(raise_on_error=True)
                except Exception as exc:
                    diagnostics.append(f"ERR_STORAGE_ASSETS_FAILED: assets(raise_on_error=True) thất bại: {exc}")
                    all_catalog_ok = False

                if all_catalog_ok:
                    # Giới hạn an toàn danh mục
                    if len(raw_assets) > MAX_CATALOG_RECORDS:
                        diagnostics.append(
                            f"ERR_CATALOG_OVERFLOW: Danh mục kho chứa {len(raw_assets)} mục, vượt quá ngưỡng an toàn {MAX_CATALOG_RECORDS}."
                        )
                        all_catalog_ok = False
                    else:
                        # Lập chỉ mục và phát hiện ID trùng lặp
                        catalog_by_id: Dict[str, Dict[str, Any]] = {}
                        duplicate_ids: Set[str] = set()
                        seen_ids: Set[str] = set()

                        for item in raw_assets:
                            if _cancel_check():
                                return SourceCatalogReadinessResult(
                                    request_id=req_id,
                                    generation=gen,
                                    proposal_id=prop_id,
                                    proposal_revision=prop_rev,
                                    approval_id=appr_id,
                                    status="cancelled",
                                    checks_passed=False,
                                    ready_to_execute=False,
                                    source_observations=tuple(source_obs_list),
                                    catalog_observations=tuple(catalog_obs_list),
                                    diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang lập chỉ mục catalog.",),
                                )
                            if not isinstance(item, Mapping):
                                continue
                            raw_aid = item.get("id") if "id" in item else item.get("asset_id")
                            if raw_aid is None or type(raw_aid) is not str or not raw_aid:
                                continue
                            aid = raw_aid
                            if aid in seen_ids:
                                duplicate_ids.add(aid)
                            seen_ids.add(aid)
                            if aid not in catalog_by_id:
                                catalog_by_id[aid] = dict(item)

                        # Kiểm tra từng assignment
                        for idx, assign in enumerate(prop.proposed_asset_assignments):
                            if _cancel_check():
                                return SourceCatalogReadinessResult(
                                    request_id=req_id,
                                    generation=gen,
                                    proposal_id=prop_id,
                                    proposal_revision=prop_rev,
                                    approval_id=appr_id,
                                    status="cancelled",
                                    checks_passed=False,
                                    ready_to_execute=False,
                                    source_observations=tuple(source_obs_list),
                                    catalog_observations=tuple(catalog_obs_list),
                                    diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang đối chiếu tài nguyên.",),
                                )

                            did = str(assign.get("demand_id", ""))
                            slot = str(assign.get("slot", ""))
                            aid = str(assign.get("asset_id", ""))
                            skind = str(assign.get("source_kind", ""))

                            if progress_callback:
                                percent = int(((total_sources + idx) / total_items) * 100)
                                progress_callback(percent, f"Đang thẩm tra tài nguyên {aid}...")

                            if aid in duplicate_ids:
                                catalog_obs_list.append(
                                    AssetAssignmentObservation(
                                        demand_id=did,
                                        slot=slot,
                                        asset_id=aid,
                                        source_kind=skind,
                                        found_in_catalog=True,
                                        duplicate_in_catalog=True,
                                        provenance_matched=False,
                                        guard_allowed=False,
                                        guard_status="offline",
                                        error_code="ERR_DUPLICATE_ASSET_ID",
                                        error_message=f"Tài nguyên {aid!r} xuất hiện nhiều lần trong catalog (trùng lặp ID).",
                                    )
                                )
                                all_catalog_ok = False
                                continue

                            if aid not in catalog_by_id:
                                catalog_obs_list.append(
                                    AssetAssignmentObservation(
                                        demand_id=did,
                                        slot=slot,
                                        asset_id=aid,
                                        source_kind=skind,
                                        found_in_catalog=False,
                                        duplicate_in_catalog=False,
                                        provenance_matched=False,
                                        guard_allowed=False,
                                        guard_status="missing",
                                        error_code="ERR_ASSET_NOT_FOUND_IN_CATALOG",
                                        error_message=f"Tài nguyên {aid!r} không tồn tại trong catalog kho media.",
                                    )
                                )
                                all_catalog_ok = False
                                continue

                            cat_item = catalog_by_id[aid]

                            # Khớp source_kind và provenance nghiêm ngặt (raw exact matching)
                            prov_matched = True
                            prov_errs = []

                            if "source_kind" not in cat_item:
                                prov_matched = False
                                prov_errs.append("catalog item thiếu trường bắt buộc 'source_kind'")
                            else:
                                cat_skind = cat_item.get("source_kind")
                                if type(cat_skind) is not str or cat_skind != skind:
                                    prov_matched = False
                                    prov_errs.append(f"source_kind không khớp (yêu cầu: {skind!r}, catalog: {cat_skind!r})")

                            for prov_key in ("origin_library_id", "source_id", "pack_id"):
                                if prov_key in assign:
                                    req_prov_val = assign.get(prov_key)
                                    if req_prov_val is not None:
                                        if prov_key not in cat_item:
                                            prov_matched = False
                                            prov_errs.append(f"catalog item thiếu trường provenance {prov_key!r}")
                                        else:
                                            cat_prov_val = cat_item.get(prov_key)
                                            if type(req_prov_val) != type(cat_prov_val) or req_prov_val != cat_prov_val:
                                                prov_matched = False
                                                prov_errs.append(
                                                    f"{prov_key} không khớp (yêu cầu: {req_prov_val!r}, catalog: {cat_prov_val!r})"
                                                )

                            if not prov_matched:
                                catalog_obs_list.append(
                                    AssetAssignmentObservation(
                                        demand_id=did,
                                        slot=slot,
                                        asset_id=aid,
                                        source_kind=skind,
                                        found_in_catalog=True,
                                        duplicate_in_catalog=False,
                                        provenance_matched=False,
                                        guard_allowed=False,
                                        guard_status="offline",
                                        error_code="ERR_PROVENANCE_MISMATCH",
                                        error_message="; ".join(prov_errs),
                                    )
                                )
                                all_catalog_ok = False
                                continue

                            # Gọi ActionGuard intent='insert' trên bản ghi gốc của catalog (không đè metadata từ proposal)
                            try:
                                guard_res = check_action_availability(
                                    asset=cat_item,
                                    intent="insert",
                                    storage_provider=lambda: storage_instance,
                                    stat_fn=_stat,
                                    realpath_fn=_realpath,
                                    bundled_root=bundled_root,
                                )
                            except Exception as exc:
                                guard_res = ActionGuardResult(
                                    allowed=False,
                                    status="offline",
                                    result_code="ERR_GUARD_EXCEPTION",
                                    reason=f"Ngoại lệ khi gọi ActionGuard: {exc}",
                                )

                            resolved_p = guard_res.resolved_path

                            # Xác thực master file hợp lệ (không phải preview)
                            if not guard_res.allowed or guard_res.status != "online":
                                catalog_obs_list.append(
                                    AssetAssignmentObservation(
                                        demand_id=did,
                                        slot=slot,
                                        asset_id=aid,
                                        source_kind=skind,
                                        found_in_catalog=True,
                                        duplicate_in_catalog=False,
                                        provenance_matched=True,
                                        guard_allowed=False,
                                        guard_status=guard_res.status,
                                        resolved_master_path=resolved_p,
                                        error_code=guard_res.result_code,
                                        error_message=guard_res.reason,
                                    )
                                )
                                all_catalog_ok = False
                                continue

                            # Thẩm tra tệp master vật lý
                            master_exists = os.path.exists(resolved_p)
                            master_is_reg = False
                            master_stat_id: Optional[StatIdentity] = None
                            if master_exists:
                                try:
                                    m_st = _stat(resolved_p)
                                    master_is_reg = stat.S_ISREG(m_st.st_mode)
                                    master_stat_id = _extract_stat_identity(m_st)
                                except OSError:
                                    master_exists = False

                            if not master_exists or not master_is_reg:
                                catalog_obs_list.append(
                                    AssetAssignmentObservation(
                                        demand_id=did,
                                        slot=slot,
                                        asset_id=aid,
                                        source_kind=skind,
                                        found_in_catalog=True,
                                        duplicate_in_catalog=False,
                                        provenance_matched=True,
                                        guard_allowed=False,
                                        guard_status="missing",
                                        resolved_master_path=resolved_p,
                                        error_code="ERR_MASTER_NOT_REGULAR_FILE",
                                        error_message=f"Tệp master đã giải mã không tồn tại hoặc không phải regular file: {resolved_p!r}",
                                    )
                                )
                                all_catalog_ok = False
                                continue

                            catalog_obs_list.append(
                                AssetAssignmentObservation(
                                    demand_id=did,
                                    slot=slot,
                                    asset_id=aid,
                                    source_kind=skind,
                                    found_in_catalog=True,
                                    duplicate_in_catalog=False,
                                    provenance_matched=True,
                                    guard_allowed=True,
                                    guard_status="online",
                                    resolved_master_path=resolved_p,
                                    master_stat_identity=master_stat_id,
                                    recheck_passed=False,
                                )
                            )

    # -------------------------------------------------------------------------
    # 3. Vòng tái kiểm tra cuối cùng (Final Recheck Phase)
    # -------------------------------------------------------------------------
    if _cancel_check():
        return SourceCatalogReadinessResult(
            request_id=req_id,
            generation=gen,
            proposal_id=prop_id,
            proposal_revision=prop_rev,
            approval_id=appr_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=tuple(source_obs_list),
            catalog_observations=tuple(catalog_obs_list),
            diagnostics=("Tiến trình tiền kiểm đã bị hủy trước khi hoàn tất vòng tái kiểm tra.",),
        )

    # 3.1 Tái kiểm tra danh tính stat của toàn bộ clip nguồn
    updated_source_obs: List[SourceClipObservation] = []
    for idx, s_obs in enumerate(source_obs_list):
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(updated_source_obs + list(source_obs_list[idx:])),
                catalog_observations=tuple(catalog_obs_list),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang tái kiểm tra clip nguồn.",),
            )
        if s_obs.stat_identity is not None:
            curr_id, err = _safe_stat_identity(s_obs.path, _stat)
            if _cancel_check():
                return SourceCatalogReadinessResult(
                    request_id=req_id,
                    generation=gen,
                    proposal_id=prop_id,
                    proposal_revision=prop_rev,
                    approval_id=appr_id,
                    status="cancelled",
                    checks_passed=False,
                    ready_to_execute=False,
                    source_observations=tuple(updated_source_obs + [s_obs] + list(source_obs_list[idx + 1:])),
                    catalog_observations=tuple(catalog_obs_list),
                    diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang tái kiểm tra clip nguồn.",),
                )
            if err or curr_id != s_obs.stat_identity:
                updated_source_obs.append(
                    SourceClipObservation(
                        clip_id=s_obs.clip_id,
                        path=s_obs.path,
                        duration_sec=s_obs.duration_sec,
                        exists=s_obs.exists,
                        is_regular_file=s_obs.is_regular_file,
                        is_non_empty=s_obs.is_non_empty,
                        is_readable=s_obs.is_readable,
                        stat_identity=s_obs.stat_identity,
                        recheck_passed=False,
                        error_code="ERR_SOURCE_MUTATED_DURING_SCAN",
                        error_message=f"Tệp nguồn {s_obs.clip_id!r} đã bị thay đổi danh tính/kích thước trong quá trình quét.",
                    )
                )
                all_sources_ok = False
            else:
                updated_source_obs.append(
                    SourceClipObservation(
                        clip_id=s_obs.clip_id,
                        path=s_obs.path,
                        duration_sec=s_obs.duration_sec,
                        exists=s_obs.exists,
                        is_regular_file=s_obs.is_regular_file,
                        is_non_empty=s_obs.is_non_empty,
                        is_readable=s_obs.is_readable,
                        stat_identity=s_obs.stat_identity,
                        recheck_passed=True,
                    )
                )
        else:
            updated_source_obs.append(s_obs)

    # 3.2 Tái kiểm tra danh tính stat của toàn bộ master file tài nguyên
    updated_catalog_obs: List[AssetAssignmentObservation] = []
    for idx, c_obs in enumerate(catalog_obs_list):
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(updated_source_obs),
                catalog_observations=tuple(updated_catalog_obs + list(catalog_obs_list[idx:])),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang tái kiểm tra tài nguyên master.",),
            )
        if c_obs.master_stat_identity is not None and c_obs.resolved_master_path:
            curr_id, err = _safe_stat_identity(c_obs.resolved_master_path, _stat)
            if _cancel_check():
                return SourceCatalogReadinessResult(
                    request_id=req_id,
                    generation=gen,
                    proposal_id=prop_id,
                    proposal_revision=prop_rev,
                    approval_id=appr_id,
                    status="cancelled",
                    checks_passed=False,
                    ready_to_execute=False,
                    source_observations=tuple(updated_source_obs),
                    catalog_observations=tuple(updated_catalog_obs + [c_obs] + list(catalog_obs_list[idx + 1:])),
                    diagnostics=("Tiến trình tiền kiểm đã bị hủy khi đang tái kiểm tra tài nguyên master.",),
                )
            if err or curr_id != c_obs.master_stat_identity:
                updated_catalog_obs.append(
                    AssetAssignmentObservation(
                        demand_id=c_obs.demand_id,
                        slot=c_obs.slot,
                        asset_id=c_obs.asset_id,
                        source_kind=c_obs.source_kind,
                        found_in_catalog=c_obs.found_in_catalog,
                        duplicate_in_catalog=c_obs.duplicate_in_catalog,
                        provenance_matched=c_obs.provenance_matched,
                        guard_allowed=False,
                        guard_status="offline",
                        resolved_master_path=c_obs.resolved_master_path,
                        master_stat_identity=c_obs.master_stat_identity,
                        recheck_passed=False,
                        error_code="ERR_MASTER_MUTATED_DURING_SCAN",
                        error_message=f"Tệp master của tài nguyên {c_obs.asset_id!r} đã bị thay đổi trong quá trình quét.",
                    )
                )
                all_catalog_ok = False
            else:
                updated_catalog_obs.append(
                    AssetAssignmentObservation(
                        demand_id=c_obs.demand_id,
                        slot=c_obs.slot,
                        asset_id=c_obs.asset_id,
                        source_kind=c_obs.source_kind,
                        found_in_catalog=c_obs.found_in_catalog,
                        duplicate_in_catalog=c_obs.duplicate_in_catalog,
                        provenance_matched=c_obs.provenance_matched,
                        guard_allowed=c_obs.guard_allowed,
                        guard_status=c_obs.guard_status,
                        resolved_master_path=c_obs.resolved_master_path,
                        master_stat_identity=c_obs.master_stat_identity,
                        recheck_passed=True,
                    )
                )
        else:
            updated_catalog_obs.append(c_obs)

    # 3.3 Tái kiểm tra cấu hình kho trên đĩa và danh tính marker/registry
    if _cancel_check():
        return SourceCatalogReadinessResult(
            request_id=req_id,
            generation=gen,
            proposal_id=prop_id,
            proposal_revision=prop_rev,
            approval_id=appr_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=tuple(updated_source_obs),
            catalog_observations=tuple(updated_catalog_obs),
            diagnostics=("Tiến trình tiền kiểm đã bị hủy trước khi kiểm tra cấu hình kho.",),
        )

    if not _recheck_catalog_authorities(
        storage_instance=storage_instance,
        catalog_not_required=catalog_not_required,
        storage_context_reader=storage_context_reader,
        initial_storage_context=initial_storage_context,
        marker_file_path=marker_file_path,
        initial_storage_marker_stat=initial_storage_marker_stat,
        registry_file_path=registry_file_path,
        initial_storage_registry_stat=initial_storage_registry_stat,
        initial_storage_disk_cfg=initial_storage_disk_cfg,
        stat_fn=_stat,
        diagnostics=diagnostics,
        phase_label=" trong quá trình kiểm tra",
    ):
        all_catalog_ok = False

    # Checkpoint 3: Cancel trước khi hoàn tất
    if _cancel_check():
        return SourceCatalogReadinessResult(
            request_id=req_id,
            generation=gen,
            proposal_id=prop_id,
            proposal_revision=prop_rev,
            approval_id=appr_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=tuple(updated_source_obs),
            catalog_observations=tuple(updated_catalog_obs),
            diagnostics=("Tiến trình tiền kiểm đã bị hủy trước khi đóng gói kết quả.",),
        )

    # -------------------------------------------------------------------------
    # 3.5. Kiểm định bằng chứng thi công (Deep Execution Verification opt-in)
    # -------------------------------------------------------------------------
    source_execution_evidence_map: Optional[Mapping[str, Any]] = None
    assembled_source_geometry: Optional[Any] = None

    if request.verify_source_execution:
        if _cancel_check():
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(updated_source_obs),
                catalog_observations=tuple(updated_catalog_obs),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy trước khi thu thập bằng chứng thi công.",),
            )

        if evidence_service is None:
            from src.core.geometry.source_evidence_service import SourceEvidenceService
            evidence_svc = SourceEvidenceService()
        else:
            evidence_svc = evidence_service

        from src.core.geometry.source_evidence_service import (
            SourceEvidenceBatchRequest,
            SourceEvidenceItemRequest,
        )

        batch_items: List[SourceEvidenceItemRequest] = []
        for c in source_clips_raw:
            if isinstance(c, (Mapping, dict)) and "clip_id" in c:
                cid = str(c.get("clip_id", ""))
                cpath = str(c.get("path") or c.get("current_path") or "")
                s_idx = c.get("stream_index")
                exp_sha = c.get("expected_full_sha256")
                batch_items.append(
                    SourceEvidenceItemRequest(
                        clip_id=cid,
                        manifest_id=prop.manifest_id,
                        file_path=cpath,
                        stream_index=s_idx,
                        expected_full_sha256=exp_sha,
                    )
                )

        batch_req = SourceEvidenceBatchRequest(items=tuple(batch_items))
        batch_res = evidence_svc.acquire_evidence_for_batch(
            batch_req,
            cancelled_fn=_cancel_check,
            progress_fn=progress_callback,
        )

        if _cancel_check() or batch_res.status == "cancelled":
            return SourceCatalogReadinessResult(
                request_id=req_id,
                generation=gen,
                proposal_id=prop_id,
                proposal_revision=prop_rev,
                approval_id=appr_id,
                status="cancelled",
                checks_passed=False,
                ready_to_execute=False,
                source_observations=tuple(updated_source_obs),
                catalog_observations=tuple(updated_catalog_obs),
                diagnostics=("Tiến trình tiền kiểm đã bị hủy trong quá trình thu thập bằng chứng thi công.",),
            )

        source_execution_evidence_map = batch_res.evidence_by_clip
        if batch_res.diagnostics:
            diagnostics.extend(batch_res.diagnostics)

        # Thẩm định trạng thái lô thực thi
        if batch_res.status != "completed":
            all_sources_ok = False
            diagnostics.append(f"ERR_EVIDENCE_BATCH_FAILED: Trạng thái lô thu thập bằng chứng là {batch_res.status!r}.")

        # Thẩm định ngữ nghĩa bằng chứng thi công qua validator dùng chung
        binding_errors = validate_execution_evidence_binding(
            evidence_by_clip=batch_res.evidence_by_clip,
            source_clips_raw=source_clips_raw,
            source_observations=updated_source_obs,
            proposal_manifest_id=prop.manifest_id,
        )
        if binding_errors:
            all_sources_ok = False
            diagnostics.extend(binding_errors)

        # Tái kiểm tra freshness các tệp nguồn sau pha deep scan dài
        source_ident_by_path = {obs.path: obs.stat_identity for obs in updated_source_obs}
        for b_item in batch_items:
            try:
                curr_ident, err_code = _safe_stat_identity(b_item.file_path, _stat)
                init_ident = source_ident_by_path.get(b_item.file_path)
                if err_code or curr_ident != init_ident:
                    all_sources_ok = False
                    diagnostics.append(
                        f"ERR_SOURCE_MUTATED_DURING_DEEP_SCAN: Tệp nguồn {b_item.file_path} đã biến động sau khi quét bằng chứng."
                    )
            except Exception as exc:
                all_sources_ok = False
                diagnostics.append(f"ERR_SOURCE_RECHECK_FAILED: Không thể kiểm tra lại tệp nguồn {b_item.file_path}: {exc}")

        # Tái kiểm tra catalog authorities (ngữ cảnh, marker, registry, disk config) sau pha deep scan dài
        if not _recheck_catalog_authorities(
            storage_instance=storage_instance,
            catalog_not_required=catalog_not_required,
            storage_context_reader=storage_context_reader,
            initial_storage_context=initial_storage_context,
            marker_file_path=marker_file_path,
            initial_storage_marker_stat=initial_storage_marker_stat,
            registry_file_path=registry_file_path,
            initial_storage_registry_stat=initial_storage_registry_stat,
            initial_storage_disk_cfg=initial_storage_disk_cfg,
            stat_fn=_stat,
            diagnostics=diagnostics,
            phase_label=" sau khi quét bằng chứng",
        ):
            all_catalog_ok = False

        rechecked_catalog_obs: List[AssetAssignmentObservation] = []
        for c_obs in updated_catalog_obs:
            if c_obs.master_stat_identity is not None and c_obs.resolved_master_path:
                curr_master_stat, m_err = _safe_stat_identity(c_obs.resolved_master_path, _stat)
                if m_err or curr_master_stat != c_obs.master_stat_identity:
                    all_catalog_ok = False
                    diagnostics.append(
                        f"ERR_MASTER_MUTATED_DURING_DEEP_SCAN: Tệp master của tài nguyên {c_obs.asset_id!r} đã bị thay đổi trong quá trình quét bằng chứng."
                    )
                    rechecked_catalog_obs.append(
                        AssetAssignmentObservation(
                            demand_id=c_obs.demand_id,
                            slot=c_obs.slot,
                            asset_id=c_obs.asset_id,
                            source_kind=c_obs.source_kind,
                            found_in_catalog=c_obs.found_in_catalog,
                            duplicate_in_catalog=c_obs.duplicate_in_catalog,
                            provenance_matched=c_obs.provenance_matched,
                            guard_allowed=False,
                            guard_status="offline",
                            resolved_master_path=c_obs.resolved_master_path,
                            master_stat_identity=c_obs.master_stat_identity,
                            recheck_passed=False,
                            error_code="ERR_MASTER_MUTATED_DURING_DEEP_SCAN",
                            error_message=f"Tệp master của tài nguyên {c_obs.asset_id!r} đã bị thay đổi trong quá trình quét bằng chứng.",
                        )
                    )
                    continue
            rechecked_catalog_obs.append(c_obs)
        updated_catalog_obs = rechecked_catalog_obs

        # Lắp ráp hình học kịch bản toàn bản
        from src.core.geometry.plan_geometry_assembler import assemble_plan_frame_geometry
        evidence_table = {cid: env.frame_evidence for cid, env in batch_res.evidence_by_clip.items()}
        try:
            assembled_source_geometry = assemble_plan_frame_geometry(
                prop,
                evidence_table,
                request.timeline_fps,
            )
            if assembled_source_geometry.status != "GEOMETRY_VERIFIED":
                all_sources_ok = False
                for issue in assembled_source_geometry.issues:
                    issue_code = getattr(issue, "issue_code", getattr(issue, "code", "UNKNOWN_ISSUE"))
                    diagnostics.append(f"ERR_GEOMETRY_{issue_code}: {issue.message}")
        except Exception as exc:
            all_sources_ok = False
            diagnostics.append(f"ERR_GEOMETRY_ASSEMBLY_FAILED: Lỗi khi lắp ráp hình học kịch bản: {exc}")

    # -------------------------------------------------------------------------
    # 4. Đóng gói kết quả cuối cùng
    # -------------------------------------------------------------------------
    has_error_diag = any(d.startswith("ERR_") for d in diagnostics)
    checks_passed = all_sources_ok and (catalog_not_required or all_catalog_ok) and not has_error_diag

    # ready_to_execute BẮT BUỘC False trong mọi trường hợp của lát cắt này
    ready_to_execute = False

    if progress_callback:
        progress_callback(100, "Hoàn tất tiền kiểm khả dụng nguồn và thư viện tài nguyên.")

    # Checkpoint 4: Cancel sau callback tiến độ cuối cùng
    if _cancel_check():
        return SourceCatalogReadinessResult(
            request_id=req_id,
            generation=gen,
            proposal_id=prop_id,
            proposal_revision=prop_rev,
            approval_id=appr_id,
            status="cancelled",
            checks_passed=False,
            ready_to_execute=False,
            source_observations=tuple(updated_source_obs),
            catalog_observations=tuple(updated_catalog_obs),
            diagnostics=("Tiến trình tiền kiểm đã bị hủy theo yêu cầu.",),
            source_execution_evidence=source_execution_evidence_map,
            source_geometry=assembled_source_geometry,
        )

    return SourceCatalogReadinessResult(
        request_id=req_id,
        generation=gen,
        proposal_id=prop_id,
        proposal_revision=prop_rev,
        approval_id=appr_id,
        status="completed",
        checks_passed=checks_passed,
        ready_to_execute=ready_to_execute,
        source_observations=tuple(updated_source_obs),
        catalog_observations=tuple(updated_catalog_obs),
        diagnostics=tuple(diagnostics),
        source_execution_evidence=source_execution_evidence_map,
        source_geometry=assembled_source_geometry,
    )
