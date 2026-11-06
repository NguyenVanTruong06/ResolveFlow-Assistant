"""
src/ui/director/workers/preapproval_evidence_worker.py
=============================================================================
Worker QThread thu thập bằng chứng nguồn tiền phê duyệt trong ReviewPage (G07-07A4-I).
Thực thi kiểm tra 5-stat, băm SHA-256 toàn vẹn và quét decoded frames qua SourceEvidenceService
trên luồng nền (off-GUI), trả về tập snapshot bằng chứng đã được thẩm định độc quyền.
=============================================================================
"""

from __future__ import annotations

import logging
import math
import os
import types
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QThread, Signal

from src.core.cache_manager import is_canonical_sha256_hex
from src.core.geometry.source_evidence_service import (
    DEFAULT_MAX_DECODED_FRAMES,
    DEFAULT_STDERR_BUDGET_BYTES,
    DEFAULT_STDOUT_BUDGET_BYTES,
    DEFAULT_TIMEOUT_PER_FILE_SEC,
    MAX_ITEMS_PER_BATCH,
    SourceEvidenceBatchRequest,
    SourceEvidenceItemRequest,
    SourceEvidenceService,
    SourceExecutionEvidenceEnvelope,
)
from src.core.proposal_builder import (
    validate_and_normalize_source_evidence_snapshot,
)

logger = logging.getLogger(__name__)


def _freeze_nested_mapping(obj: Any) -> Any:
    """Đóng băng đệ quy mapping và sequence thành MappingProxyType và tuple bất biến."""
    if isinstance(obj, Mapping):
        return types.MappingProxyType({str(k): _freeze_nested_mapping(v) for k, v in obj.items()})
    elif isinstance(obj, (list, tuple)):
        return tuple(_freeze_nested_mapping(v) for v in obj)
    return obj


def _is_safe_finite_float(val: Any, min_val: Optional[float] = None, max_val: Optional[float] = None) -> bool:
    """Kiểm tra số thực hữu hạn an toàn, tránh OverflowError và TypeError."""
    if isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    try:
        f = float(val)
        if not math.isfinite(f):
            return False
    except (OverflowError, ValueError):
        return False
    if min_val is not None and f < min_val:
        return False
    if max_val is not None and f > max_val:
        return False
    return True


REQUIRED_REQUEST_FIELDS = (
    "request_id",
    "generation",
    "manifest_id",
    "input_signature",
    "items",
    "timeout_per_file_sec",
    "max_decoded_frames",
    "stdout_budget_bytes",
    "stderr_budget_bytes",
)

REQUIRED_ITEM_FIELDS = (
    "clip_id",
    "manifest_id",
    "file_path",
    "stream_index",
    "expected_full_sha256",
)

REQUIRED_TERMINAL_PAYLOAD_FIELDS = (
    "request_id",
    "generation",
    "input_signature",
    "status",
    "verified_evidence_by_clip",
    "raw_evidence_by_clip",
    "diagnostics",
    "verified_count",
    "total_count",
)


@dataclass(frozen=True)
class PreapprovalEvidenceRequest:
    """Yêu cầu thu thập bằng chứng nguồn tiền phê duyệt độc quyền (defensively owned)."""

    request_id: str
    generation: int
    manifest_id: str
    input_signature: str
    items: Tuple[SourceEvidenceItemRequest, ...]
    timeout_per_file_sec: float = DEFAULT_TIMEOUT_PER_FILE_SEC
    max_decoded_frames: int = DEFAULT_MAX_DECODED_FRAMES
    stdout_budget_bytes: int = DEFAULT_STDOUT_BUDGET_BYTES
    stderr_budget_bytes: int = DEFAULT_STDERR_BUDGET_BYTES

    def __post_init__(self) -> None:
        if type(self.request_id) is not str or not self.request_id or not self.request_id.strip() or "\0" in self.request_id:
            raise ValueError(f"ERR_INVALID_REQUEST_ID: request_id phải là str không rỗng, nhận: {self.request_id!r}")
        if type(self.generation) is not int or isinstance(self.generation, bool) or self.generation < 0:
            raise ValueError(f"ERR_INVALID_GENERATION: generation phải là int >= 0, nhận: {self.generation!r}")
        if type(self.manifest_id) is not str or not self.manifest_id or not self.manifest_id.strip() or "\0" in self.manifest_id:
            raise ValueError(f"ERR_INVALID_MANIFEST_ID: manifest_id phải là str không rỗng, nhận: {self.manifest_id!r}")
        if type(self.input_signature) is not str or isinstance(self.input_signature, bool) or not is_canonical_sha256_hex(self.input_signature):
            raise ValueError(f"ERR_INVALID_INPUT_SIGNATURE: input_signature phải là canonical SHA-256 hex 64 ký tự, nhận: {self.input_signature!r}")

        # Thẩm định ngân sách budgets khớp backend bounds
        if not _is_safe_finite_float(self.timeout_per_file_sec, min_val=0.001, max_val=300.0):
            raise ValueError(f"ERR_INVALID_BUDGET: timeout_per_file_sec phải là số thực hữu hạn trong [0.001, 300.0], nhận: {self.timeout_per_file_sec!r}")

        m_frames = self.max_decoded_frames
        if type(m_frames) is not int or isinstance(m_frames, bool) or m_frames < 1 or m_frames > 10_000_000:
            raise ValueError(f"ERR_INVALID_BUDGET: max_decoded_frames phải là int trong [1, 10000000], nhận: {m_frames!r}")

        stdout_b = self.stdout_budget_bytes
        if type(stdout_b) is not int or isinstance(stdout_b, bool) or stdout_b < 1024 or stdout_b > 256 * 1024 * 1024:
            raise ValueError(f"ERR_INVALID_BUDGET: stdout_budget_bytes phải là int trong [1024, 268435456], nhận: {stdout_b!r}")

        stderr_b = self.stderr_budget_bytes
        if type(stderr_b) is not int or isinstance(stderr_b, bool) or stderr_b < 1024 or stderr_b > 10 * 1024 * 1024:
            raise ValueError(f"ERR_INVALID_BUDGET: stderr_budget_bytes phải là int trong [1024, 10485760], nhận: {stderr_b!r}")

        if not isinstance(self.items, (list, tuple)):
            raise ValueError(f"ERR_INVALID_ITEMS: items phải là sequence, nhận: {type(self.items).__name__}")
        if len(self.items) > MAX_ITEMS_PER_BATCH:
            raise ValueError(f"ERR_COUNT_OVERFLOW: items có {len(self.items)} phần tử, vượt quá {MAX_ITEMS_PER_BATCH}")

        owned_items: List[SourceEvidenceItemRequest] = []
        seen_clip_ids = set()
        for idx, it in enumerate(self.items):
            if not isinstance(it, SourceEvidenceItemRequest):
                raise ValueError(f"ERR_INVALID_ITEM_TYPE: items[{idx}] phải là SourceEvidenceItemRequest, nhận: {type(it).__name__}")
            for req_f in REQUIRED_ITEM_FIELDS:
                if req_f not in it.__dict__:
                    raise ValueError(f"ERR_INVALID_ITEM: items[{idx}] thiếu thuộc tính {req_f!r}")
            if it.manifest_id != self.manifest_id:
                raise ValueError(f"ERR_MANIFEST_MISMATCH: items[{idx}].manifest_id ({it.manifest_id!r}) không khớp request ({self.manifest_id!r})")
            if it.expected_full_sha256 is not None:
                raise ValueError(f"ERR_PREBOUND_SHA: preapproval item clip_id '{it.clip_id}' có expected_full_sha256={it.expected_full_sha256!r}, phải là None")
            if it.clip_id in seen_clip_ids:
                raise ValueError(f"ERR_DUPLICATE_CLIP_ID: clip_id '{it.clip_id}' bị trùng lặp trong items")
            seen_clip_ids.add(it.clip_id)

            # Defensive clone để request sở hữu hoàn toàn item
            cloned = SourceEvidenceItemRequest(
                clip_id=it.clip_id,
                manifest_id=it.manifest_id,
                file_path=it.file_path,
                stream_index=it.stream_index,
                expected_full_sha256=it.expected_full_sha256,
            )
            owned_items.append(cloned)

        object.__setattr__(self, "items", tuple(owned_items))


@dataclass(frozen=True)
class PreapprovalEvidenceTerminalPayload:
    """Kết quả hoàn tất hoặc kết thúc của tiến trình quét bằng chứng tiền phê duyệt."""

    request_id: str
    generation: int
    input_signature: str
    status: str  # "completed" | "cancelled" | "failed"
    verified_evidence_by_clip: Mapping[str, Dict[str, Any]]
    raw_evidence_by_clip: Mapping[str, Any]
    diagnostics: Tuple[str, ...] = ()
    verified_count: int = 0
    total_count: int = 0


def normalize_preapproval_terminal_payload(
    payload: Any,
    *,
    expected_request: PreapprovalEvidenceRequest,
) -> PreapprovalEvidenceTerminalPayload:
    """Xác thực và chuẩn tắc hóa toàn diện terminal payload giữa worker và review page.

    Bắt buộc:
    - Bắt mọi vi phạm cấu trúc, type, bounds, bijection, digest, counter.
    - So sánh verified subset nhận được với verified subset suy dẫn (derived) từ raw graph.
    - Trả về PreapprovalEvidenceTerminalPayload bất biến (deeply frozen).
    """
    if not isinstance(payload, PreapprovalEvidenceTerminalPayload):
        raise ValueError(
            f"ERR_INVALID_TERMINAL_PAYLOAD: payload phải là PreapprovalEvidenceTerminalPayload, nhận: {type(payload).__name__}"
        )
    if not isinstance(expected_request, PreapprovalEvidenceRequest):
        raise ValueError(
            f"ERR_INVALID_REQUEST: expected_request phải là PreapprovalEvidenceRequest, nhận: {type(expected_request).__name__}"
        )

    for req_f in REQUIRED_TERMINAL_PAYLOAD_FIELDS:
        if req_f not in payload.__dict__:
            raise ValueError(f"ERR_INVALID_TERMINAL_PAYLOAD: payload thiếu thuộc tính {req_f!r}")

    # 1. Thẩm định định danh và generation
    if type(payload.request_id) is not str or payload.request_id != expected_request.request_id:
        raise ValueError(
            f"ERR_REQUEST_ID_MISMATCH: payload request_id {payload.request_id!r} không khớp {expected_request.request_id!r}"
        )
    if type(payload.generation) is not int or isinstance(payload.generation, bool) or payload.generation != expected_request.generation:
        raise ValueError(
            f"ERR_GENERATION_MISMATCH: payload generation {payload.generation!r} không khớp {expected_request.generation!r}"
        )
    if type(payload.input_signature) is not str or payload.input_signature != expected_request.input_signature:
        raise ValueError(
            f"ERR_SIGNATURE_MISMATCH: payload input_signature không khớp {expected_request.input_signature!r}"
        )

    # 2. Thẩm định trạng thái status
    if type(payload.status) is not str or payload.status not in ("completed", "cancelled", "failed"):
        raise ValueError(f"ERR_INVALID_STATUS: payload status {payload.status!r} không hợp lệ")

    # 3. Thẩm định diagnostics
    if not isinstance(payload.diagnostics, (tuple, list)) or isinstance(payload.diagnostics, (str, bytes)):
        raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics phải là sequence, nhận: {type(payload.diagnostics).__name__}")
    diag_list: List[str] = []
    for idx, d in enumerate(payload.diagnostics):
        if type(d) is not str:
            raise ValueError(f"ERR_INVALID_DIAGNOSTICS: diagnostics[{idx}] phải là str, nhận: {type(d).__name__}")
        diag_list.append(d)
    frozen_diag = tuple(diag_list)

    # 4. Thẩm định counters
    if type(payload.total_count) is not int or isinstance(payload.total_count, bool) or payload.total_count != len(expected_request.items):
        raise ValueError(
            f"ERR_COUNTER_MISMATCH: total_count={payload.total_count!r} không khớp số lượng clip kỳ vọng {len(expected_request.items)}"
        )
    if type(payload.verified_count) is not int or isinstance(payload.verified_count, bool) or payload.verified_count < 0:
        raise ValueError(f"ERR_COUNTER_MISMATCH: verified_count={payload.verified_count!r} không phải int >= 0")

    # 5. Xử lý trạng thái cancelled / failed
    if payload.status in ("cancelled", "failed"):
        if len(payload.verified_evidence_by_clip) != 0 or payload.verified_count != 0:
            raise ValueError("ERR_FAILED_PAYLOAD_CONTAINS_VERIFIED: payload cancelled/failed không được chứa verified clips")
        return PreapprovalEvidenceTerminalPayload(
            request_id=expected_request.request_id,
            generation=expected_request.generation,
            input_signature=expected_request.input_signature,
            status=payload.status,
            verified_evidence_by_clip=types.MappingProxyType({}),
            raw_evidence_by_clip=types.MappingProxyType({}),
            diagnostics=frozen_diag,
            verified_count=0,
            total_count=payload.total_count,
        )

    # Status completed
    if not isinstance(payload.raw_evidence_by_clip, Mapping):
        raise ValueError("ERR_RAW_EVIDENCE_NOT_MAPPING: raw_evidence_by_clip phải là Mapping")
    if not isinstance(payload.verified_evidence_by_clip, Mapping):
        raise ValueError("ERR_VERIFIED_EVIDENCE_NOT_MAPPING: verified_evidence_by_clip phải là Mapping")

    expected_clip_map: Dict[str, str] = {it.clip_id: it.file_path for it in expected_request.items}
    raw_keys = set(payload.raw_evidence_by_clip.keys())
    exp_keys = set(expected_clip_map.keys())
    if raw_keys != exp_keys:
        raise ValueError(
            f"ERR_RAW_EVIDENCE_BIJECTION: keys raw_evidence_by_clip không khớp expected items: {raw_keys ^ exp_keys}"
        )

    # Thẩm định raw envelopes và suy dẫn canonical verified subset
    derived_verified: Dict[str, Dict[str, Any]] = {}
    owned_raw: Dict[str, Any] = {}

    for it in expected_request.items:
        clip_id = it.clip_id
        exp_path = it.file_path
        env = payload.raw_evidence_by_clip[clip_id]

        if not isinstance(env, SourceExecutionEvidenceEnvelope):
            raise ValueError(f"ERR_INVALID_ENVELOPE: Clip '{clip_id}' envelope không đúng kiểu SourceExecutionEvidenceEnvelope.")
        if env.clip_id != clip_id or env.manifest_id != expected_request.manifest_id:
            raise ValueError(f"ERR_ENVELOPE_MISMATCH: Clip '{clip_id}' sai clip_id hoặc manifest_id.")
        if not env.file_path or os.path.normpath(env.file_path) != os.path.normpath(exp_path):
            raise ValueError(f"ERR_ENVELOPE_PATH_MISMATCH: Clip '{clip_id}' path '{env.file_path}' không khớp '{exp_path}'.")

        owned_raw[clip_id] = env

        fe = getattr(env, "frame_evidence", None)
        ev_status = getattr(fe, "evidence_status", None) if fe is not None else None

        if ev_status == "VERIFIED_CFR":
            # Phải thỏa mãn toàn vẹn CFR
            obs_sha = getattr(env, "observed_full_sha256", None)
            if (
                getattr(env, "complete_scan", False) is not True
                or getattr(env, "profile_policy", "") != "decoded_frame_uniform_ticks_v1"
                or obs_sha is None
                or not is_canonical_sha256_hex(str(obs_sha))
                or getattr(env, "freshness", None) is None
                or fe is None
                or getattr(fe, "manifest_id", None) != expected_request.manifest_id
                or getattr(fe, "clip_id", None) != clip_id
                or getattr(fe, "source_fps", None) is None
                or getattr(fe, "total_frames", None) is None
            ):
                raise ValueError(
                    f"ERR_CORRUPT_CFR_ENVELOPE: Clip '{clip_id}' claim VERIFIED_CFR nhưng envelope bị thiếu hoặc sai lệch toàn vẹn."
                )

            fps_val = fe.source_fps
            fps_num = fps_val.numerator if fps_val else 0
            fps_den = fps_val.denominator if fps_val else 1

            snap_dict = {
                "manifest_id": env.manifest_id,
                "clip_id": env.clip_id,
                "file_path": env.file_path,
                "stream_index": env.stream_index,
                "observed_full_sha256": str(obs_sha).lower(),
                "freshness": {
                    "file_size_bytes": env.freshness.file_size_bytes,
                    "mtime_ns": env.freshness.mtime_ns,
                    "ctime_ns": env.freshness.ctime_ns,
                    "device_id": env.freshness.device_id,
                    "inode_number": env.freshness.inode_number,
                },
                "frame_evidence": {
                    "manifest_id": fe.manifest_id,
                    "clip_id": fe.clip_id,
                    "source_fps": {"numerator": fps_num, "denominator": fps_den},
                    "total_frames": fe.total_frames,
                    "evidence_status": fe.evidence_status,
                },
                "complete_scan": True,
                "profile_policy": env.profile_policy,
            }
            norm_snap = validate_and_normalize_source_evidence_snapshot(
                snap_dict,
                clip_id=clip_id,
                expected_manifest_id=expected_request.manifest_id,
                expected_path=exp_path,
            )
            derived_verified[clip_id] = norm_snap
        else:
            # Unverified legitimate (HEADER_REPORTED_ONLY, v.v.)
            pass

    # So sánh verified_evidence_by_clip nhận được với derived_verified
    ver_keys = set(payload.verified_evidence_by_clip.keys())
    der_keys = set(derived_verified.keys())
    if ver_keys != der_keys:
        raise ValueError(
            f"ERR_VERIFIED_SUBSET_MISMATCH: keys verified ({ver_keys}) không khớp derived ({der_keys})"
        )

    for clip_id in der_keys:
        v_sn = payload.verified_evidence_by_clip[clip_id]
        norm_v_sn = validate_and_normalize_source_evidence_snapshot(
            v_sn,
            clip_id=clip_id,
            expected_manifest_id=expected_request.manifest_id,
            expected_path=expected_clip_map[clip_id],
        )
        if norm_v_sn != derived_verified[clip_id]:
            raise ValueError(
                f"ERR_VERIFIED_FACTS_MISMATCH: Clip '{clip_id}' facts không khớp snapshot suy dẫn từ raw."
            )
        # Thẩm định riêng biệt observed_full_sha256 so với raw envelope
        raw_env_sha = str(payload.raw_evidence_by_clip[clip_id].observed_full_sha256).lower()
        if str(norm_v_sn["observed_full_sha256"]).lower() != raw_env_sha:
            raise ValueError(
                f"ERR_DIGEST_MISMATCH: Clip '{clip_id}' SHA verified {norm_v_sn['observed_full_sha256']!r} không khớp raw envelope {raw_env_sha!r}"
            )

    if payload.verified_count != len(derived_verified):
        raise ValueError(
            f"ERR_COUNTER_MISMATCH: verified_count={payload.verified_count} không khớp len(derived_verified)={len(derived_verified)}"
        )

    return PreapprovalEvidenceTerminalPayload(
        request_id=expected_request.request_id,
        generation=expected_request.generation,
        input_signature=expected_request.input_signature,
        status="completed",
        verified_evidence_by_clip=_freeze_nested_mapping(derived_verified),
        raw_evidence_by_clip=_freeze_nested_mapping(owned_raw),
        diagnostics=frozen_diag,
        verified_count=len(derived_verified),
        total_count=len(expected_request.items),
    )


class PreapprovalEvidenceWorker(QThread):
    """Worker QThread chuyên trách thu thập bằng chứng nguồn trước khi phê duyệt kịch bản.

    Tuân thủ các nguyên tắc kiến trúc:
    1. Constructor Zero I/O: Không khởi tạo service hay kiểm tra đĩa trong hàm khởi tạo.
    2. Exactly-One Terminal: Phát tín hiệu terminal_signal đúng 1 lần duy nhất.
    3. Native Finished riêng biệt: Dùng signal finished của Qt cho việc giải phóng tài nguyên.
    4. Bắt gọn InterruptedError từ scan frame, chuyển thành terminal 'cancelled'.
    5. Chỉ trả về tập con verified_evidence_by_clip gồm các clip đạt chuẩn VERIFIED_CFR toàn diện.
    """

    progress = Signal(int, str)
    terminal_signal = Signal(object)

    def __init__(
        self,
        request: PreapprovalEvidenceRequest,
        *,
        service: Optional[SourceEvidenceService] = None,
        service_factory: Optional[Callable[[], SourceEvidenceService]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(request, PreapprovalEvidenceRequest):
            raise ValueError(
                f"ERR_INVALID_REQUEST: request phải là PreapprovalEvidenceRequest, nhận: {type(request).__name__}"
            )

        # Bắt các trường bị xóa khỏi instance (ví dụ qua object.__delattr__)
        for f_name in REQUIRED_REQUEST_FIELDS:
            if f_name not in request.__dict__:
                raise ValueError(f"ERR_INVALID_REQUEST: request thiếu thuộc tính {f_name!r}")

        # Thẩm định lại bounds ngân sách trên request (đề phòng bypass __post_init__)
        if not _is_safe_finite_float(request.timeout_per_file_sec, min_val=0.001, max_val=300.0):
            raise ValueError(f"ERR_INVALID_BUDGET: timeout_per_file_sec không hợp lệ: {request.timeout_per_file_sec!r}")

        for name, val, min_v, max_v in (
            ("max_decoded_frames", request.max_decoded_frames, 1, 10_000_000),
            ("stdout_budget_bytes", request.stdout_budget_bytes, 1024, 256 * 1024 * 1024),
            ("stderr_budget_bytes", request.stderr_budget_bytes, 1024, 10 * 1024 * 1024),
        ):
            if type(val) is not int or isinstance(val, bool) or val < min_v or val > max_v:
                raise ValueError(f"ERR_INVALID_BUDGET: {name} phải là int trong [{min_v}, {max_v}], nhận: {val!r}")

        if service_factory is not None and not callable(service_factory):
            raise ValueError("ERR_INVALID_FACTORY: service_factory nếu truyền phải là callable.")

        cloned_items = []
        for idx, it in enumerate(request.items):
            if not isinstance(it, SourceEvidenceItemRequest):
                raise ValueError(f"ERR_INVALID_ITEM_TYPE: items[{idx}] phải là SourceEvidenceItemRequest")
            for req_f in REQUIRED_ITEM_FIELDS:
                if req_f not in it.__dict__:
                    raise ValueError(f"ERR_INVALID_ITEM: items[{idx}] thiếu thuộc tính {req_f!r}")
            cloned_items.append(
                SourceEvidenceItemRequest(
                    clip_id=it.clip_id,
                    manifest_id=it.manifest_id,
                    file_path=it.file_path,
                    stream_index=it.stream_index,
                    expected_full_sha256=it.expected_full_sha256,
                )
            )

        self._request: PreapprovalEvidenceRequest = PreapprovalEvidenceRequest(
            request_id=request.request_id,
            generation=request.generation,
            manifest_id=request.manifest_id,
            input_signature=request.input_signature,
            items=tuple(cloned_items),
            timeout_per_file_sec=request.timeout_per_file_sec,
            max_decoded_frames=request.max_decoded_frames,
            stdout_budget_bytes=request.stdout_budget_bytes,
            stderr_budget_bytes=request.stderr_budget_bytes,
        )
        self._service: Optional[SourceEvidenceService] = service
        self._service_factory: Optional[Callable[[], SourceEvidenceService]] = service_factory

        self._request_id: str = self._request.request_id
        self._generation: int = self._request.generation
        self._manifest_id: str = self._request.manifest_id
        self._input_signature: str = self._request.input_signature
        self._expected_clip_map: Dict[str, str] = {it.clip_id: it.file_path for it in self._request.items}

        self._is_cancelled_flag: bool = False
        self._terminal_emitted: bool = False

    @property
    def request(self) -> PreapprovalEvidenceRequest:
        import copy
        return copy.deepcopy(self._request)

    def cancel(self) -> None:
        """Yêu cầu hủy cooperative an toàn."""
        self._is_cancelled_flag = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        return self._is_cancelled_flag or self.isInterruptionRequested()

    def _emit_terminal(self, payload: PreapprovalEvidenceTerminalPayload) -> None:
        if self._terminal_emitted:
            logger.warning("Bỏ qua phát terminal trùng lặp cho job %s.", self._request_id)
            return
        self._terminal_emitted = True
        self.terminal_signal.emit(payload)

    def _make_cancelled_payload(self, reason: str = "Tác vụ bị hủy bởi người dùng.") -> PreapprovalEvidenceTerminalPayload:
        return PreapprovalEvidenceTerminalPayload(
            request_id=self._request_id,
            generation=self._generation,
            input_signature=self._input_signature,
            status="cancelled",
            verified_evidence_by_clip=types.MappingProxyType({}),
            raw_evidence_by_clip=types.MappingProxyType({}),
            diagnostics=(reason,),
            verified_count=0,
            total_count=len(self._expected_clip_map),
        )

    def _make_failed_payload(self, reason: str) -> PreapprovalEvidenceTerminalPayload:
        return PreapprovalEvidenceTerminalPayload(
            request_id=self._request_id,
            generation=self._generation,
            input_signature=self._input_signature,
            status="failed",
            verified_evidence_by_clip=types.MappingProxyType({}),
            raw_evidence_by_clip=types.MappingProxyType({}),
            diagnostics=(reason,),
            verified_count=0,
            total_count=len(self._expected_clip_map),
        )

    def run(self) -> None:
        """Thực thi tác vụ quét bằng chứng trong luồng nền."""
        # Checkpoint 0: Kiểm tra hủy trước khi bắt đầu
        if self._is_cancelled():
            self._emit_terminal(self._make_cancelled_payload())
            return

        try:
            # 1. Khởi tạo service lazily trên luồng worker nếu chưa có
            svc = self._service
            if svc is None:
                if self._service_factory is not None:
                    if self._is_cancelled():
                        self._emit_terminal(self._make_cancelled_payload())
                        return
                    svc = self._service_factory()
                else:
                    svc = SourceEvidenceService()

            # Checkpoint 1: Sau khi có service
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_payload())
                return

            # 2. Xây dựng batch request
            batch_req = SourceEvidenceBatchRequest(
                items=self._request.items,
                timeout_per_file_sec=self._request.timeout_per_file_sec,
                max_decoded_frames=self._request.max_decoded_frames,
                stdout_budget_bytes=self._request.stdout_budget_bytes,
                stderr_budget_bytes=self._request.stderr_budget_bytes,
            )

            def _progress_cb(pct: int, msg: str) -> None:
                if not self._is_cancelled():
                    self.progress.emit(pct, msg)

            # 3. Thu thập bằng chứng qua service
            batch_res = svc.acquire_evidence_for_batch(
                batch_req,
                cancelled_fn=self._is_cancelled,
                progress_fn=_progress_cb,
            )

            # Checkpoint 2: Sau khi service trả về
            if self._is_cancelled() or getattr(batch_res, "status", None) == "cancelled":
                self._emit_terminal(self._make_cancelled_payload())
                return

            if getattr(batch_res, "status", None) != "completed":
                err_diag = "; ".join(str(d) for d in getattr(batch_res, "diagnostics", ())) or f"Producer status không hoàn tất ({getattr(batch_res, 'status', None)!r})"
                self._emit_terminal(self._make_failed_payload(f"ERR_PRODUCER_STATUS: {err_diag}"))
                return

            # 4. Thẩm định đồ thị kết quả và trích xuất candidate verified subset
            raw_map = getattr(batch_res, "evidence_by_clip", None)
            if not isinstance(raw_map, Mapping):
                self._emit_terminal(self._make_failed_payload("ERR_INTEGRITY: evidence_by_clip không phải Mapping."))
                return

            candidate_verified: Dict[str, Dict[str, Any]] = {}
            for clip_id, exp_path in self._expected_clip_map.items():
                env = raw_map.get(clip_id)
                if env is None:
                    continue
                fe = getattr(env, "frame_evidence", None)
                if fe is not None and getattr(fe, "evidence_status", None) == "VERIFIED_CFR":
                    obs_sha = getattr(env, "observed_full_sha256", None)
                    if (
                        getattr(env, "complete_scan", False) is True
                        and getattr(env, "profile_policy", "") == "decoded_frame_uniform_ticks_v1"
                        and obs_sha is not None
                        and is_canonical_sha256_hex(str(obs_sha))
                        and getattr(env, "freshness", None) is not None
                    ):
                        fps_val = fe.source_fps
                        fps_num = fps_val.numerator if fps_val else 0
                        fps_den = fps_val.denominator if fps_val else 1
                        candidate_verified[clip_id] = {
                            "manifest_id": getattr(env, "manifest_id", self._manifest_id),
                            "clip_id": clip_id,
                            "file_path": getattr(env, "file_path", exp_path),
                            "stream_index": getattr(env, "stream_index", 0),
                            "observed_full_sha256": str(obs_sha).lower(),
                            "freshness": {
                                "file_size_bytes": env.freshness.file_size_bytes,
                                "mtime_ns": env.freshness.mtime_ns,
                                "ctime_ns": env.freshness.ctime_ns,
                                "device_id": env.freshness.device_id,
                                "inode_number": env.freshness.inode_number,
                            },
                            "frame_evidence": {
                                "manifest_id": fe.manifest_id,
                                "clip_id": fe.clip_id,
                                "source_fps": {"numerator": fps_num, "denominator": fps_den},
                                "total_frames": fe.total_frames,
                                "evidence_status": fe.evidence_status,
                            },
                            "complete_scan": True,
                            "profile_policy": env.profile_policy,
                        }

            # Checkpoint 3: Trước khi phát terminal
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_payload())
                return

            candidate_payload = PreapprovalEvidenceTerminalPayload(
                request_id=self._request_id,
                generation=self._generation,
                input_signature=self._input_signature,
                status="completed",
                verified_evidence_by_clip=_freeze_nested_mapping(candidate_verified),
                raw_evidence_by_clip=_freeze_nested_mapping(dict(raw_map)),
                diagnostics=getattr(batch_res, "diagnostics", ()),
                verified_count=len(candidate_verified),
                total_count=len(self._expected_clip_map),
            )

            # Thẩm định qua single shared terminal normalizer
            normalized_payload = normalize_preapproval_terminal_payload(
                candidate_payload,
                expected_request=self._request,
            )
            self._emit_terminal(normalized_payload)

        except InterruptedError:
            self._emit_terminal(self._make_cancelled_payload())
        except Exception as exc:
            if self._is_cancelled():
                self._emit_terminal(self._make_cancelled_payload())
            else:
                self._emit_terminal(self._make_failed_payload(str(exc) or type(exc).__name__))
        finally:
            if not self._terminal_emitted:
                self._emit_terminal(self._make_failed_payload("Worker ngầm kết thúc mà không phát kết quả."))
