"""src/services/resolve_execution_engine.py
Động Cơ Thực Thi Kế Hoạch Resolve Có Nhật Ký Ghi Trước (WAL) - Lát Cắt Nguyên Mẫu Đóng Kín (G07-07B4-R1).

Quy tắc kiến trúc & Ranh giới an toàn:
1. Pure Python standard library + internal contracts:
   - Tái sử dụng ExecutionReceiptService, ExecutionJournalService, TimelineExecutionPlan, AppOwnedApprovalRecord.
   - Hoàn toàn KHÔNG import DaVinciResolveScript, fusionscript, hoặc PySide6.
2. Ranh giới an toàn tuyệt đối & Mock-Only Admission Gate (B4-R5):
   - Cờ ready_to_execute=False được bảo toàn 100%. Không kết nối trực tiếp DaVinci Resolve thật.
   - Bắt buộc kiểm tra marker _is_approved_mock_gateway == True và từ chối cấu trúc mọi adapter live Resolve.
3. Target-Scoped Exclusion & Atomic Admission (B4-R1):
   - Khóa cố vấn độc quyền (advisory lock) theo project_id + timeline_id (target lock).
   - Ngăn chặn triệt để hai kế hoạch hoặc hai execution_id khác nhau cùng thi công trên một timeline Resolve.
4. Thẩm định Chiếm dụng Track Toàn diện tại Từng Ranh giới Action (B4-R2 / CP-06):
   - Kiểm tra snapshot target project, timeline, FPS, và sự hiện diện của track đích trong scanned_tracks.
   - Đối chiếu va chạm không gian (external collision) với occupied_spans, phân biệt chính xác với các
     clip đã commit trong chính phiên hiện hành.
5. Xử lý Lỗi Lưu trữ Hậu Dispatch & Bảo Toàn Khóa (B4-R3):
   - Mọi lỗi ghi WAL hoặc Receipt sau khi Gateway đã xử lý đều được xếp loại IN_DOUBT_PERSISTENCE_FAILED.
   - Giữ nguyên khóa cố vấn (lock retained), bảo toàn byte nhật ký, cấm replay, cấm đột biến kế tiếp.
6. Registry Sở hữu Bất định Có Thể Định Danh (B4-R4):
   - Lưu trữ lock holder và thông tin bất định trong UnresolvedTargetRegistry ở cấp tiến trình.
   - Chặn đứng mọi lệnh thi công mới trên timeline đích cho đến khi có bằng chứng đối soát/hoàn tất vật lý.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable
import uuid

# Project contracts
from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    JournalEntry,
    compute_idempotency_key,
)
from src.core.contracts.time_contracts import RationalFPS
from src.core.timeline_plan_execution_binder import (
    OccupiedTrackSpan,
    PlannedTimelineAction,
    TargetSpec,
    TimelineExecutionPlan,
    TimelineOccupancySnapshot,
)
from src.services.execution_journal_service import (
    ExecutionJournalService,
    JournalStorageError,
)
from src.services.execution_receipt_service import (
    ExecutionLockActiveError,
    ExecutionLockContext,
    ExecutionReceiptService,
    IdempotencyDuplicateActiveError,
    IdempotencyDuplicateCompletedError,
    ReceiptStorageError,
    TargetStateCorruptError,
    ReceiptScanCapExceededError,
)

logger = logging.getLogger("ResolveFlow.ResolveExecutionEngine")


class ObserverTimeoutError(TimeoutError):
    """Ngoại lệ phát sinh khi luồng quan sát hết thời gian chờ mà chưa có phản hồi vật lý từ Resolve."""
    pass


class TargetTimelineShiftedError(RuntimeError):
    """Ngoại lệ khi dự án hoặc timeline đích trên Resolve bị dịch chuyển so với TargetSpec."""
    pass


class TargetFPSDriftError(RuntimeError):
    """Ngoại lệ khi FPS của timeline đích bị sai lệch so với TargetSpec."""
    pass


class UnexpectedTrackOccupancyError(RuntimeError):
    """Ngoại lệ khi phát hiện phương tiện lạ chiếm dụng track đích trước khi đột biến."""
    pass


class MockGatewayAdmissionError(PermissionError):
    """Ngoại lệ khi Gateway không vượt qua cổng thẩm định mock-only prototype."""
    pass


@runtime_checkable
class ResolveGatewayClientProtocol(Protocol):
    """Giao diện chuẩn tương tác với Resolve Gateway (thật hoặc giả lập)."""

    def query_active_timeline_authority(self) -> Dict[str, Any]:
        """Truy vấn thông tin thẩm quyền của timeline và project đang kích hoạt trên Resolve."""
        ...

    def query_track_occupancy_snapshot(self, target_spec: TargetSpec) -> TimelineOccupancySnapshot:
        """Truy vấn ảnh chụp chiếm dụng track đích để đối soát trước đột biến."""
        ...

    def execute_action(
        self,
        action: PlannedTimelineAction,
        timeout_sec: float = 15.0,
    ) -> Dict[str, Any]:
        """Phát lệnh thi công một planned action tới luồng Resolve Gateway."""
        ...


@dataclass(frozen=True)
class ExecutionEngineResult:
    """Kết quả hoàn tất hoặc tạm dừng của chu trình thi công có kiểm soát."""
    status: str
    execution_id: str
    idempotency_key: str
    execution_dispatched: bool
    committed_actions_count: int
    total_actions_count: int
    receipt: Optional[ExecutionReceipt] = None
    prior_receipt_id: Optional[str] = None
    prior_completed_at: Optional[str] = None
    error_code: str = ""
    error_message: str = ""
    in_doubt_action: Optional[PlannedTimelineAction] = None
    lock_retained: bool = False
    committed_item_ids: Tuple[str, ...] = field(default_factory=tuple)


class _EngineLockHolder:
    """Quản lý an toàn vòng đời của ExecutionLockContext, hỗ trợ giữ khóa (retain lock) khi timeout."""

    def __init__(self, lock_ctx: ExecutionLockContext):
        self._lock_ctx = lock_ctx
        self._acquired = False

    def acquire(self) -> None:
        self._lock_ctx.__enter__()
        self._acquired = True

    def release(self) -> None:
        if self._acquired:
            try:
                self._lock_ctx.__exit__(None, None, None)
            finally:
                self._acquired = False

    @property
    def is_acquired(self) -> bool:
        return self._acquired


_HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")

VALID_RECONCILIATION_OUTCOMES = frozenset({
    "ACTION_COMMITTED_CONFIRMED",
    "ACTION_NOT_DISPATCHED_CONFIRMED",
    "ACTION_FAILED_CONFIRMED",
    "TIMELINE_RESTORED_CLEAN",
    "RECONCILED_TERMINAL",
})


@dataclass(frozen=True)
class OperatorReconciliationProof:
    """Bằng chứng đối soát xác thực từ người vận hành (B4-R1-RES-01 & B4-R2-RES-01).

    Ràng buộc chặt chẽ theo danh tính mục tiêu, phiên thi công, thao tác dở dang và tệp artifact bằng chứng.
    Tuyệt đối từ chối dict tự do hoặc chuỗi nhãn không xác thực.
    """
    schema_version: int
    proof_id: str
    target_project_id: str
    target_timeline_id: str
    target_execution_id: str
    verified_outcome: str
    operator_id: str
    reconciliation_timestamp_iso: str
    readback_evidence_hash: str
    notes: str
    in_doubt_action_index: Optional[int] = None
    evidence_artifact_path: str = ""


class EvidenceVerificationError(ValueError):
    """Lỗi thẩm định tệp bằng chứng đối soát vật lý (B4-R2-RES-01 & B4-R3-RES-02)."""
    pass


class WholePlanReconciliationError(ValueError):
    """Lỗi đối soát kế hoạch toàn diện (B4-R3-RES-01).

    Ngăn chặn việc chuyển trạng thái biên bản thi công sang COMPLETED hoặc ABORTED
    khi kế hoạch chưa đạt điều kiện kết thúc toàn diện (whole-plan terminal condition).
    """
    pass


@runtime_checkable
class EvidenceTrustProvider(Protocol):
    """Protocol xác thực nguồn gốc tin cậy (trusted provenance) của bằng chứng đối soát (B4-R4-RES-01)."""

    def verify_attestation(
        self,
        artifact_data: Dict[str, Any],
        raw_bytes: bytes,
    ) -> bool:
        ...


class HmacEvidenceTrustProvider:
    """Hiện thực chứng thực bằng chứng nguồn tin cậy dùng HMAC-SHA256 (B4-R4-RES-01 & B4-R5-RES-01).

    Bắt buộc khóa bí mật được cấp phát từ bên ngoài (không hardcode mặc định trong mã nguồn).
    Ký duyệt trên payload chuẩn tắc (canonical envelope) bao hàm toàn bộ trường định danh
    và toàn vẹn nội dung.
    Ràng buộc byte chuẩn tắc tuyệt đối 1:1, từ chối mọi biến dạng khoảng trắng hoặc khóa trùng lặp.
    """

    REQUIRED_ATTESTATION_FIELDS = (
        "artifact_version",
        "observation_id",
        "observed_at_iso",
        "observer_identity",
        "target_project_id",
        "target_timeline_id",
        "target_execution_id",
        "verified_outcome",
        "operator_id",
        "reconciliation_timestamp_iso",
        "is_stale",
    )

    def __init__(self, secret_key: bytes):
        if not isinstance(secret_key, (bytes, bytearray)) or len(secret_key) < 16:
            raise ValueError("ERR_INVALID_TRUST_SECRET: secret_key phải là bytes có độ dài tối thiểu 16 bytes")
        self.secret_key = bytes(secret_key)

    @classmethod
    def canonical_serialize(cls, artifact_data: Dict[str, Any]) -> bytes:
        """Serialization chuẩn tắc duy nhất của phong bì bằng chứng: UTF-8, sort_keys=True, compact separators, zero whitespace."""
        return json.dumps(artifact_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @classmethod
    def compute_canonical_attestation(
        cls,
        artifact_data: Dict[str, Any],
        secret_key: bytes,
    ) -> str:
        """Tạo chữ ký chứng thực HMAC-SHA256 chuẩn tắc bảo vệ toàn bộ phong bì bằng chứng."""
        if not isinstance(secret_key, (bytes, bytearray)) or len(secret_key) < 16:
            raise ValueError("ERR_INVALID_TRUST_SECRET: secret_key phải là bytes có độ dài tối thiểu 16 bytes")

        for req_f in cls.REQUIRED_ATTESTATION_FIELDS:
            if req_f not in artifact_data:
                raise ValueError(f"ERR_ATTESTATION_MISSING_FIELD: Thiếu trường bắt buộc '{req_f}' trong phong bì bằng chứng")

        # Khóa tất cả các trường trong artifact trừ chữ ký attestation_signature
        dict_to_sign = {k: v for k, v in artifact_data.items() if k != "attestation_signature"}
        canonical_json = cls.canonical_serialize(dict_to_sign)
        return hmac.new(secret_key, canonical_json, hashlib.sha256).hexdigest().lower()

    def verify_attestation(
        self,
        artifact_data: Dict[str, Any],
        raw_bytes: bytes,
    ) -> bool:
        """Thẩm định chữ ký chứng thực và ràng buộc byte chuẩn tắc tuyệt đối 1:1 (B4-R4-RES-01 & B4-R5-RES-01).

        Yêu cầu:
        1. raw_bytes giải mã UTF-8 không lỗi.
        2. JSON không chứa khóa trùng lặp (duplicate keys).
        3. Dữ liệu giải mã khớp hoàn toàn với artifact_data.
        4. raw_bytes phải khớp 1:1 từng byte với canonical serialization (sort_keys=True, compact separators=(',', ':')).
           Bất kỳ thay đổi nào về khoảng trắng (whitespace), thứ tự khóa (key order), hoặc thụt dòng đều bị từ chối.
        5. Chữ ký HMAC-SHA256 trên canonical serialization của envelope (loại trừ attestation_signature) phải khớp chữ ký đính kèm.
        """
        def _reject_duplicate_keys(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError(f"ERR_DUPLICATE_KEY: Phát hiện khóa trùng lặp trong JSON: {k!r}")
                d[k] = v
            return d

        try:
            content_str = raw_bytes.decode("utf-8")
            decoded = json.loads(content_str, object_pairs_hook=_reject_duplicate_keys)
            if decoded != artifact_data:
                return False
        except Exception:
            return False

        # B4-R5-RES-01: Ràng buộc byte chuẩn tắc tuyệt đối 1:1
        try:
            expected_canonical_bytes = self.canonical_serialize(artifact_data)
            if raw_bytes != expected_canonical_bytes:
                return False
        except Exception:
            return False

        att = artifact_data.get("attestation_signature")
        if type(att) is not str or not att.strip():
            return False
        try:
            expected = self.compute_canonical_attestation(artifact_data, self.secret_key)
        except Exception:
            return False
        return hmac.compare_digest(att.strip().lower(), expected.lower())


@runtime_checkable
class ReconciliationEvidenceVerifier(Protocol):
    """Giao diện xác thực bằng chứng đối soát vật lý độc lập (B4-R2-RES-01)."""

    def verify_evidence(
        self,
        proof: OperatorReconciliationProof,
        expected_project_id: str,
        expected_timeline_id: str,
        expected_execution_id: str,
        expected_action_index: Optional[int],
    ) -> bool:
        ...


class BoundedFileEvidenceVerifier:
    """Bộ thẩm định bằng chứng đối soát vật lý từ tệp artifact bền vững (B4-R2-RES-01, B4-R3-RES-02, B4-R4-RES-01 & B4-R4-RES-03).

    Đọc tối đa max_bytes + 1 từ descriptor (chống TOCTOU/growth), kiểm tra fstat fail-closed,
    tính toán lại SHA-256 trên byte thực tế, thẩm định nguồn tin cậy qua trust_provider (fail-closed nếu chưa cấu hình),
    đối soát toàn vẹn nội dung JSON, danh tính quan sát, và kiểm tra kiểu dữ liệu nguyên thủy nghiêm ngặt.
    """

    def __init__(
        self,
        max_bytes: int = 10 * 1024 * 1024,
        trust_provider: Optional[EvidenceTrustProvider] = None,
    ):
        self.max_bytes = max_bytes
        self.trust_provider = trust_provider

    def verify_evidence(
        self,
        proof: OperatorReconciliationProof,
        expected_project_id: str,
        expected_timeline_id: str,
        expected_execution_id: str,
        expected_action_index: Optional[int],
    ) -> bool:
        # B4-R4-RES-01: Fail-closed nếu chưa cấu hình nguồn tin cậy
        if self.trust_provider is None:
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_TRUST_UNCONFIGURED: Chưa cấu hình nguồn tin cậy (trust provider) cho bộ thẩm định bằng chứng. "
                "Yêu cầu cung cấp EvidenceTrustProvider hợp lệ trước khi đối soát."
            )

        if type(proof.evidence_artifact_path) is not str or not proof.evidence_artifact_path.strip():
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_PATH_REQUIRED: evidence_artifact_path không được để trống hoặc chưa chỉ định"
            )

        art_path = proof.evidence_artifact_path.strip()
        if not os.path.exists(art_path):
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_NOT_FOUND: Tệp bằng chứng đối soát không tồn tại tại '{art_path}'"
            )

        if not os.path.isfile(art_path):
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_NOT_A_FILE: Đường dẫn bằng chứng '{art_path}' không phải tệp thông thường"
            )

        # B4-R3-RES-02 & B4-R4-RES-03: Đọc tối đa max_bytes + 1 từ descriptor và kiểm tra fstat fail-closed
        try:
            initial_sz = os.path.getsize(art_path)
            if initial_sz > self.max_bytes:
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_SIZE_EXCEEDED: Dung lượng tệp ({initial_sz} bytes) vượt quá giới hạn an toàn ({self.max_bytes} bytes)"
                )
            with open(art_path, "rb") as f:
                raw_bytes = f.read(self.max_bytes + 1)
                if len(raw_bytes) > self.max_bytes:
                    raise EvidenceVerificationError(
                        f"ERR_EVIDENCE_SIZE_EXCEEDED: Dung lượng tệp đọc từ descriptor ({len(raw_bytes)} bytes) vượt quá giới hạn ({self.max_bytes} bytes)"
                    )
                # B4-R4-RES-03: Bắt buộc fstat thành công; ném EvidenceVerificationError nếu lỗi
                try:
                    fd_stat = os.fstat(f.fileno())
                except OSError as exc:
                    raise EvidenceVerificationError(
                        f"ERR_EVIDENCE_DESCRIPTOR_STAT_FAILED: Không thể kiểm tra descriptor qua fstat: {exc}"
                    ) from exc

                if fd_stat.st_size > self.max_bytes:
                    raise EvidenceVerificationError(
                        f"ERR_EVIDENCE_SIZE_EXCEEDED: Tệp phát triển trong quá trình đọc ({fd_stat.st_size} bytes > {self.max_bytes} bytes)"
                    )
                if fd_stat.st_size < len(raw_bytes):
                    raise EvidenceVerificationError(
                        f"ERR_EVIDENCE_DESCRIPTOR_STAT_FAILED: fstat st_size ({fd_stat.st_size}) "
                        f"nhỏ hơn số bytes thực đọc ({len(raw_bytes)})"
                    )
        except EvidenceVerificationError:
            raise
        except OSError as exc:
            raise EvidenceVerificationError(f"ERR_EVIDENCE_READ_FAILED: Không thể đọc tệp bằng chứng: {exc}")

        # Tính toán độc lập SHA-256 trên byte tệp thực tế
        computed_hash = hashlib.sha256(raw_bytes).hexdigest().lower()
        if computed_hash != proof.readback_evidence_hash.lower():
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_HASH_MISMATCH: Mã băm SHA-256 tính toán từ tệp ({computed_hash}) "
                f"không khớp mã băm khai báo trong proof ({proof.readback_evidence_hash.lower()})"
            )

        # Giải mã và thẩm định ngữ nghĩa JSON với kiểm tra khóa trùng lặp (B4-R5-RES-01)
        def _reject_duplicate_keys(pairs):
            d = {}
            for k, v in pairs:
                if k in d:
                    raise ValueError(f"ERR_DUPLICATE_KEY: Phát hiện khóa trùng lặp trong JSON: {k!r}")
                d[k] = v
            return d

        try:
            content_str = raw_bytes.decode("utf-8")
            data = json.loads(content_str, object_pairs_hook=_reject_duplicate_keys)
        except Exception as exc:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_MALFORMED_JSON: Nội dung tệp bằng chứng không phải JSON hợp lệ: {exc}"
            )

        if not isinstance(data, dict):
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_INVALID_STRUCTURE: Nội dung artifact bằng chứng phải là JSON Object (dict)"
            )

        # B4-R3-RES-03 & B4-R5-RES-02: Thẩm định kiểu dữ liệu nguyên thủy tại ranh giới decoded artifact
        art_proj = data.get("target_project_id")
        art_tl = data.get("target_timeline_id")
        art_exec = data.get("target_execution_id")
        art_action = data.get("action_index")
        art_outcome = data.get("verified_outcome")
        art_op = data.get("operator_id")
        art_ts = data.get("reconciliation_timestamp_iso")

        for k, v in [
            ("target_project_id", art_proj),
            ("target_timeline_id", art_tl),
            ("target_execution_id", art_exec),
            ("verified_outcome", art_outcome),
            ("operator_id", art_op),
            ("reconciliation_timestamp_iso", art_ts),
        ]:
            if type(v) is not str:
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_FIELD_TYPE: Trường {k} trong artifact phải là str thuần, nhận: {type(v).__name__}"
                )
            if not v.strip():
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_FIELD_BLANK: Trường {k} trong artifact không được để trống"
                )

        try:
            datetime.fromisoformat(art_ts.replace("Z", "+00:00"))
        except Exception as exc:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_TIMESTAMP_INVALID: reconciliation_timestamp_iso không đúng định dạng ISO-8601: {exc}"
            )

        if "action_index" in data:
            if type(art_action) is not int:
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_ACTION_INDEX_TYPE: action_index trong artifact phải là int thuần (không chấp nhận bool hay float), nhận: {type(art_action).__name__}"
                )
            if art_action < 0:
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_ACTION_INDEX_INVALID: action_index trong artifact phải >= 0, nhận: {art_action}"
                )

        # B4-R3-RES-02: Thẩm định định danh quan sát (Observation Identity)
        obs_id = data.get("observation_id")
        obs_at = data.get("observed_at_iso")
        obs_ident = data.get("observer_identity")

        if type(obs_id) is not str or not obs_id.strip():
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_OBSERVATION_ID_REQUIRED: observation_id bắt buộc và phải là chuỗi không rỗng"
            )
        if type(obs_at) is not str or not obs_at.strip():
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_OBSERVED_AT_REQUIRED: observed_at_iso bắt buộc và phải là chuỗi không rỗng"
            )
        try:
            datetime.fromisoformat(obs_at.replace("Z", "+00:00"))
        except Exception as exc:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_OBSERVED_AT_INVALID: observed_at_iso không đúng định dạng ISO-8601: {exc}"
            )
        if type(obs_ident) is not str or not obs_ident.strip():
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_OBSERVER_IDENTITY_REQUIRED: observer_identity bắt buộc và phải là chuỗi không rỗng"
            )

        # B4-R3-RES-02: Thẩm định tính tươi mới bắt buộc (Freshness)
        if "is_stale" not in data:
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_FRESHNESS_MISSING: Trường 'is_stale' bắt buộc phải tồn tại trong artifact"
            )
        if type(data["is_stale"]) is not bool:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_FRESHNESS_TYPE: 'is_stale' phải là kiểu bool thuần, nhận: {type(data['is_stale']).__name__}"
            )
        if data["is_stale"] is not False:
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_STALE: Bằng chứng được đánh dấu là đã lỗi thời (is_stale=True)"
            )

        # B4-R3-RES-02 & B4-R4-RES-01: Xác thực nguồn gốc tin cậy (Trusted Provenance / Attestation)
        if not self.trust_provider.verify_attestation(data, raw_bytes):
            raise EvidenceVerificationError(
                "ERR_EVIDENCE_UNTRUSTED_PROVENANCE: Bằng chứng không có chứng thực từ nguồn tin cậy hợp lệ (forged/missing attestation)"
            )

        # Thẩm định danh tính artifact khớp với proof và expected durable state
        if art_proj != expected_project_id or art_proj != proof.target_project_id:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_PROJECT_MISMATCH: target_project_id trong artifact ({art_proj}) "
                f"không khớp proof/durable ({expected_project_id})"
            )

        if art_tl != expected_timeline_id or art_tl != proof.target_timeline_id:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_TIMELINE_MISMATCH: target_timeline_id trong artifact ({art_tl}) "
                f"không khớp proof/durable ({expected_timeline_id})"
            )

        if art_exec != expected_execution_id or art_exec != proof.target_execution_id:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_EXECUTION_MISMATCH: target_execution_id trong artifact ({art_exec}) "
                f"không khớp proof/durable ({expected_execution_id})"
            )

        if expected_action_index is not None:
            if art_action != expected_action_index or art_action != proof.in_doubt_action_index:
                raise EvidenceVerificationError(
                    f"ERR_EVIDENCE_ACTION_INDEX_MISMATCH: action_index trong artifact ({art_action}) "
                    f"không khớp proof/durable ({expected_action_index})"
                )

        if art_outcome != proof.verified_outcome:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_OUTCOME_MISMATCH: verified_outcome trong artifact ({art_outcome}) "
                f"không khớp proof ({proof.verified_outcome})"
            )

        # B4-R5-RES-02: Ràng buộc thẩm quyền kiểm toán (operator_id và timestamp) giữa proof và artifact đã ký
        if art_op != proof.operator_id:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_OPERATOR_MISMATCH: operator_id trong artifact ({art_op}) "
                f"không khớp proof ({proof.operator_id})"
            )

        if art_ts != proof.reconciliation_timestamp_iso:
            raise EvidenceVerificationError(
                f"ERR_EVIDENCE_TIMESTAMP_MISMATCH: reconciliation_timestamp_iso trong artifact ({art_ts}) "
                f"không khớp proof ({proof.reconciliation_timestamp_iso})"
            )

        self.last_verified_artifact = data
        return True


def validate_reconciliation_proof(proof: Any) -> None:
    """Thẩm định hợp đồng bằng chứng đối soát B4-R1-RES-01, B4-R2-RES-01 & B4-R3-RES-03.

    Từ chối plain dict, đối tượng thiếu/thừa trường, kiểu dữ liệu nguyên thủy không hợp lệ
    (cấm tuyệt đối bool giả mạo int), hash sai định dạng, outcome không hợp lệ.
    """
    if not isinstance(proof, OperatorReconciliationProof):
        raise TypeError(
            f"ERR_INVALID_PROOF_TYPE: Bằng chứng đối soát phải là thực thể OperatorReconciliationProof, "
            f"nhận: {type(proof).__name__}"
        )

    # B4-R3-RES-03: Kiểm tra kiểu chính xác (strict type check, cấm bool)
    if type(proof.schema_version) is not int:
        raise TypeError(
            f"ERR_PROOF_INVALID_SCHEMA_TYPE: schema_version phải là int thuần (không chấp nhận bool hay kiểu khác), "
            f"nhận: {type(proof.schema_version).__name__}"
        )
    if proof.schema_version != 1:
        raise ValueError(f"ERR_UNSUPPORTED_PROOF_SCHEMA: schema_version={proof.schema_version} không được hỗ trợ")

    if proof.in_doubt_action_index is not None:
        if type(proof.in_doubt_action_index) is not int:
            raise TypeError(
                f"ERR_PROOF_INVALID_ACTION_INDEX_TYPE: in_doubt_action_index phải là int thuần (không chấp nhận bool hay kiểu khác), "
                f"nhận: {type(proof.in_doubt_action_index).__name__}"
            )
        if proof.in_doubt_action_index < 0:
            raise ValueError(
                f"ERR_PROOF_INVALID_ACTION_INDEX: in_doubt_action_index phải là số nguyên không âm (>= 0), "
                f"nhận: {proof.in_doubt_action_index}"
            )

    string_fields = (
        ("proof_id", "ERR_PROOF_ID_REQUIRED"),
        ("target_project_id", "ERR_PROOF_TARGET_PROJECT_REQUIRED"),
        ("target_timeline_id", "ERR_PROOF_TARGET_TIMELINE_REQUIRED"),
        ("target_execution_id", "ERR_PROOF_TARGET_EXECUTION_REQUIRED"),
        ("verified_outcome", "ERR_PROOF_OUTCOME_REQUIRED"),
        ("operator_id", "ERR_PROOF_OPERATOR_REQUIRED"),
        ("reconciliation_timestamp_iso", "ERR_PROOF_TIMESTAMP_REQUIRED"),
        ("readback_evidence_hash", "ERR_PROOF_HASH_REQUIRED"),
        ("notes", "ERR_PROOF_NOTES_REQUIRED"),
        ("evidence_artifact_path", "ERR_PROOF_PATH_REQUIRED"),
    )
    for field_name, err_code in string_fields:
        val = getattr(proof, field_name, None)
        if type(val) is not str:
            raise TypeError(
                f"ERR_PROOF_FIELD_TYPE: Trường {field_name} phải là kiểu str thuần, "
                f"nhận: {type(val).__name__}"
            )
        if field_name not in ("notes", "evidence_artifact_path") and (not val or not val.strip()):
            raise ValueError(f"{err_code}: {field_name} không được để trống")
    if not proof.notes or not proof.notes.strip():
        raise ValueError("ERR_PROOF_NOTES_REQUIRED: notes giải trình đối soát không được để trống")

    if proof.verified_outcome not in VALID_RECONCILIATION_OUTCOMES:
        raise ValueError(
            f"ERR_PROOF_INVALID_OUTCOME: verified_outcome {proof.verified_outcome!r} không nằm trong danh mục "
            f"cho phép: {sorted(VALID_RECONCILIATION_OUTCOMES)}"
        )

    try:
        datetime.fromisoformat(proof.reconciliation_timestamp_iso.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"ERR_PROOF_INVALID_TIMESTAMP: reconciliation_timestamp_iso không đúng định dạng ISO-8601: {exc}")

    if not _HEX_64_PATTERN.match(proof.readback_evidence_hash):
        raise ValueError(
            "ERR_PROOF_INVALID_HASH: readback_evidence_hash phải là chuỗi 64 ký tự hex chữ thường (SHA-256)"
        )


@dataclass
class UnresolvedTargetRecord:
    """Bản ghi sở hữu bất định của timeline đích trong registry cấp tiến trình."""
    project_id: str
    timeline_id: str
    execution_id: str
    target_lock_holder: _EngineLockHolder
    exec_lock_holder: _EngineLockHolder
    in_doubt_action: Optional[PlannedTimelineAction]
    reason: str
    retained_at_iso: str


class UnresolvedTargetRegistry:
    """Registry cấp tiến trình quản lý các target timeline đang bị khóa giữ do sự cố bất định (B4-R4)."""

    def __init__(self):
        self._records: Dict[Tuple[str, str], UnresolvedTargetRecord] = {}

    def register_unresolved(
        self,
        project_id: str,
        timeline_id: str,
        execution_id: str,
        target_lock_holder: _EngineLockHolder,
        exec_lock_holder: _EngineLockHolder,
        in_doubt_action: Optional[PlannedTimelineAction],
        reason: str,
    ) -> None:
        key = (project_id, timeline_id)
        self._records[key] = UnresolvedTargetRecord(
            project_id=project_id,
            timeline_id=timeline_id,
            execution_id=execution_id,
            target_lock_holder=target_lock_holder,
            exec_lock_holder=exec_lock_holder,
            in_doubt_action=in_doubt_action,
            reason=reason,
            retained_at_iso=datetime.now(timezone.utc).isoformat(),
        )

    def is_target_unresolved(self, project_id: str, timeline_id: str) -> bool:
        return (project_id, timeline_id) in self._records

    def get_record(self, project_id: str, timeline_id: str) -> Optional[UnresolvedTargetRecord]:
        return self._records.get((project_id, timeline_id))

    def resolve_and_release(
        self,
        project_id: str,
        timeline_id: str,
        reconciliation_proof: Any,
        evidence_verifier: Optional[ReconciliationEvidenceVerifier] = None,
    ) -> bool:
        """Giải phóng khóa có chủ đích chỉ khi có bằng chứng đối soát hoặc hoàn tất vật lý xác thực (B4-R1-RES-01 & B4-R2-RES-01)."""
        key = (project_id, timeline_id)
        rec = self._records.get(key)
        if not rec:
            return False

        # Thẩm định bằng chứng xác thực hợp đồng chặt chẽ (từ chối plain dict hoặc đối tượng sai)
        validate_reconciliation_proof(reconciliation_proof)

        # Khớp danh tính đích
        if reconciliation_proof.target_project_id != project_id or reconciliation_proof.target_timeline_id != timeline_id:
            raise ValueError(
                f"ERR_PROOF_TARGET_MISMATCH: Target trong proof ({reconciliation_proof.target_project_id}, "
                f"{reconciliation_proof.target_timeline_id}) không khớp registry ({project_id}, {timeline_id})"
            )

        # Khớp execution ID
        if reconciliation_proof.target_execution_id != rec.execution_id:
            raise ValueError(
                f"ERR_PROOF_EXECUTION_MISMATCH: execution_id trong proof ({reconciliation_proof.target_execution_id}) "
                f"không khớp phiên bất định được lưu giữ ({rec.execution_id})"
            )

        # Khớp action index nếu có action đang dở dang
        expected_act_idx = rec.in_doubt_action.action_index if rec.in_doubt_action is not None else None
        if expected_act_idx is not None:
            if reconciliation_proof.in_doubt_action_index != expected_act_idx:
                raise ValueError(
                    f"ERR_PROOF_ACTION_MISMATCH: in_doubt_action_index trong proof "
                    f"({reconciliation_proof.in_doubt_action_index}) không khớp thao tác dở dang "
                    f"({expected_act_idx})"
                )

        # B4-R2-RES-01: Nếu có evidence_verifier, thẩm định digest và artifact bằng chứng
        if evidence_verifier is not None:
            evidence_verifier.verify_evidence(
                proof=reconciliation_proof,
                expected_project_id=project_id,
                expected_timeline_id=timeline_id,
                expected_execution_id=rec.execution_id,
                expected_action_index=expected_act_idx,
            )

        try:
            rec.target_lock_holder.release()
        except Exception:
            pass
        try:
            rec.exec_lock_holder.release()
        except Exception:
            pass

        del self._records[key]
        return True

    def clear_for_test(self) -> None:
        """Dành riêng cho dọn dẹp môi trường kiểm thử."""
        for rec in list(self._records.values()):
            try:
                rec.target_lock_holder.release()
            except Exception:
                pass
            try:
                rec.exec_lock_holder.release()
            except Exception:
                pass
        self._records.clear()


# Registry singleton trong không gian tiến trình
GLOBAL_UNRESOLVED_TARGET_REGISTRY = UnresolvedTargetRegistry()


class ResolveExecutionEngine:
    """Động cơ điều phối thi công tuần tự kế hoạch Timeline qua giao thức WAL 2 pha."""

    def __init__(
        self,
        receipt_service: ExecutionReceiptService,
        journal_service: ExecutionJournalService,
        gateway: ResolveGatewayClientProtocol,
        unresolved_registry: Optional[UnresolvedTargetRegistry] = None,
        evidence_verifier: Optional[ReconciliationEvidenceVerifier] = None,
        trust_provider: Optional[EvidenceTrustProvider] = None,
    ):
        if not isinstance(receipt_service, ExecutionReceiptService):
            raise ValueError("ERR_INVALID_SERVICE: receipt_service phải là ExecutionReceiptService")
        if not isinstance(journal_service, ExecutionJournalService):
            raise ValueError("ERR_INVALID_SERVICE: journal_service phải là ExecutionJournalService")
        if not isinstance(gateway, ResolveGatewayClientProtocol):
            raise ValueError("ERR_INVALID_GATEWAY: gateway phải tuân thủ ResolveGatewayClientProtocol")

        self.receipt_service = receipt_service
        self.journal_service = journal_service
        self.gateway = gateway
        self.unresolved_registry = unresolved_registry or GLOBAL_UNRESOLVED_TARGET_REGISTRY
        if evidence_verifier is not None:
            self.evidence_verifier = evidence_verifier
        else:
            self.evidence_verifier = BoundedFileEvidenceVerifier(trust_provider=trust_provider)

    def _validate_mock_gateway_admission(self) -> None:
        """B4-R5: Cổng thẩm định mock-only gate.

        Từ chối cấu trúc bất kỳ adapter nào thiếu marker phê duyệt hoặc chứa thành phần live Resolve.
        """
        if getattr(self.gateway, "_is_approved_mock_gateway", None) is not True:
            raise MockGatewayAdmissionError(
                "ERR_LIVE_RESOLVE_FORBIDDEN: ResolveExecutionEngine đang chạy ở chế độ prototype MOCK_ONLY. "
                "Yêu cầu FakeResolveGateway có _is_approved_mock_gateway=True."
            )

        # Negative probe: từ chối nếu có các trường của ResolveAutomation hoặc live Resolve bridge
        if hasattr(self.gateway, "_automation") or hasattr(self.gateway, "resolve") or hasattr(self.gateway, "_resolve_override"):
            raise MockGatewayAdmissionError(
                "ERR_LIVE_ADAPTER_REJECTED: Phát hiện adapter chứa thuộc tính Resolve live/automation. "
                "Bị từ chối cấu trúc tại cổng bảo vệ Mock-Only."
            )

    def _check_durable_target_admission(
        self, project_id: str, timeline_id: str
    ) -> Optional[Dict[str, str]]:
        """B4-R1-RES-02 & B4-R2-RES-03: Kiểm tra bằng chứng bền vững trên đĩa chống process restart bypass.

        Thẩm định trạng thái target state và biên bản receipt/journal tồn tại trước khi cho phép chiếm khóa.
        Fail-closed ngay lập tức nếu phát hiện phiên thi công dở dang, torn tail, vượt scan cap, hoặc file bị hỏng.
        """
        # 1. Kiểm tra target state record bền vững
        try:
            target_state = self.receipt_service.get_target_state(project_id, timeline_id)
        except TargetStateCorruptError as exc:
            return {
                "error_code": "ERR_TARGET_CORRUPT_EVIDENCE",
                "error_message": f"Tệp trạng thái target ({project_id}, {timeline_id}) bị hỏng hoặc biến dạng: {exc}",
            }
        except OSError as exc:
            return {
                "error_code": "ERR_TARGET_STORAGE_ACCESS_FAILED",
                "error_message": f"Không thể truy cập tệp trạng thái target ({project_id}, {timeline_id}): {exc}",
            }

        if target_state:
            state_val = str(target_state.get("state", ""))
            is_in_doubt = target_state.get("in_doubt") is True
            active_id = str(target_state.get("active_execution_id", ""))
            if is_in_doubt or state_val in ("INITIALIZED", "EXECUTING", "IN_DOUBT", "RECONCILING"):
                detail_msg = ""
                if active_id:
                    try:
                        replay = self.journal_service.replay_journal(active_id)
                        if replay.torn_tail is not None:
                            detail_msg = f" (phát hiện torn tail tại dòng cuối nhật ký của {active_id})"
                        elif replay.entries and replay.entries[-1].stage == "ACTION_PENDING":
                            detail_msg = f" (dừng lại tại ACTION_PENDING chưa commit)"
                    except Exception:
                        pass
                return {
                    "error_code": "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION",
                    "error_message": (
                        f"Timeline đích ({project_id}, {timeline_id}) có trạng thái sở hữu bền vững chưa giải tỏa: "
                        f"execution_id={active_id}, state={state_val}, in_doubt={is_in_doubt}{detail_msg}. "
                        f"Yêu cầu đối soát vật lý (OperatorReconciliationProof) trước khi thi công tiếp."
                    ),
                }

        # 2. Quét các tệp receipt của target để phát hiện phiên dở dang
        if os.path.exists(self.receipt_service.receipts_dir):
            try:
                filenames = os.listdir(self.receipt_service.receipts_dir)
            except OSError as exc:
                return {
                    "error_code": "ERR_TARGET_STORAGE_ACCESS_FAILED",
                    "error_message": f"Không thể truy cập thư mục receipts: {exc}",
                }

            receipt_files = [fn for fn in filenames if fn.endswith(".receipt.json")]
            
            # B4-R2-RES-03: Giới hạn cứng 1000 receipts; vượt quá thì fail-closed không cắt lát
            HARD_SCAN_CAP = 1000
            if len(receipt_files) > HARD_SCAN_CAP:
                return {
                    "error_code": "ERR_RECEIPT_SCAN_CAP_EXCEEDED",
                    "error_message": (
                        f"Số lượng tệp biên bản ({len(receipt_files)}) vượt quá giới hạn quét an toàn ({HARD_SCAN_CAP}). "
                        f"Từ chối tiếp nhận (fail closed) để tránh bỏ sót phiên dở dang."
                    ),
                }

            for fn in receipt_files:
                exec_id = fn[:-len(".receipt.json")]
                target_file_path = os.path.join(self.receipt_service.receipts_dir, fn)
                try:
                    rec = self.receipt_service.load_receipt(exec_id)
                except Exception as exc:
                    # B4-R1-RES-02 & B4-R2-RES-03: Bằng chứng biên bản bị biến dạng dữ liệu/corrupt -> Fail closed
                    return {
                        "error_code": "ERR_TARGET_CORRUPT_EVIDENCE",
                        "error_message": f"Phát hiện tệp biên bản bị hỏng/biến dạng dữ liệu tại {target_file_path}: {exc}",
                    }

                if rec.project_id == project_id and rec.timeline_id == timeline_id:
                    if rec.state in ("INITIALIZED", "EXECUTING"):
                        # Kiểm tra tệp journal tương ứng
                        try:
                            replay = self.journal_service.replay_journal(exec_id)
                            # Kiểm tra torn tail
                            if replay.torn_tail is not None:
                                return {
                                    "error_code": "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION",
                                    "error_message": (
                                        f"Phát hiện tệp nhật ký bị rách (torn tail) của phiên dở dang {exec_id} "
                                        f"trên timeline đích ({project_id}, {timeline_id})"
                                    ),
                                }
                            # Kiểm tra bản ghi cuối có phải ACTION_PENDING chưa commit không
                            if replay.entries and replay.entries[-1].stage == "ACTION_PENDING":
                                return {
                                    "error_code": "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION",
                                    "error_message": (
                                        f"Phát hiện phiên {exec_id} dừng lại ở ACTION_PENDING chưa commit "
                                        f"trên timeline đích ({project_id}, {timeline_id})"
                                    ),
                                }
                        except Exception as j_exc:
                            return {
                                "error_code": "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION",
                                "error_message": (
                                    f"Không thể đọc nhật ký của phiên {exec_id} trên timeline đích: {j_exc}"
                                ),
                            }

                        return {
                            "error_code": "ERR_TARGET_HAS_UNRESOLVED_DURABLE_EXECUTION",
                            "error_message": (
                                f"Phát hiện phiên thi công dở dang {exec_id} (state: {rec.state}) "
                                f"chưa giải tỏa trên timeline đích ({project_id}, {timeline_id})"
                            ),
                        }

        return None

    @classmethod
    def _validate_control_record_envelope(
        cls,
        entry: JournalEntry,
        expected_stage: str,
        exec_id: str,
    ) -> None:
        """Thẩm định cấu trúc phong bì (envelope) của bản ghi điều khiển WAL (START hoặc FINISH) - B4-R8-RES-01.

        Bản ghi điều khiển bắt buộc phải tuân thủ schema nghiêm ngặt:
        - seq: kiểu int thuần túy (loại trừ bool), seq >= 0.
        - stage: đúng stage mong đợi (START hoặc FINISH).
        - action_type: bắt buộc là 'NO_OP'.
        - timestamp_iso: chuỗi ISO 8601 hợp lệ, không rỗng.
        - resolve_item_id: bắt buộc là chuỗi rỗng ''.
        - track_type: bắt buộc là chuỗi rỗng ''.
        - track_index: bắt buộc là số nguyên 0 thuần túy (loại trừ bool).
        - error_code: đối với START và in-doubt FINISH, bắt buộc là chuỗi rỗng ''.
        - error_message: đối với START và in-doubt FINISH, bắt buộc là chuỗi rỗng ''.
        - note: đối với START, bắt buộc là chuỗi rỗng ''; đối với FINISH, bắt buộc là chuỗi str.
        - action_params: bắt buộc là dict.
        """
        stg = entry.stage
        if stg != expected_stage:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_STAGE_MISMATCH: Mong đợi stage {expected_stage!r} nhưng nhận {stg!r} tại sequence {entry.seq} của phiên {exec_id}"
            )

        # 1. Thẩm định seq: kiểu int thuần túy, loại trừ bool, seq >= 0
        if type(entry.seq) is not int or type(entry.seq) is bool or entry.seq < 0:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_SEQ_TYPE: Bản ghi điều khiển {stg} của phiên {exec_id} phải có seq là int thuần túy >= 0, nhận: {type(entry.seq).__name__} ({entry.seq!r})"
            )

        # 2. Thẩm định action_type: bắt buộc là NO_OP
        if type(entry.action_type) is not str or entry.action_type != "NO_OP":
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_ACTION_TYPE: Bản ghi điều khiển {stg} của phiên {exec_id} bắt buộc phải có action_type='NO_OP', nhận: {entry.action_type!r}"
            )

        # 3. Thẩm định timestamp_iso: chuỗi ISO 8601 hợp lệ
        if type(entry.timestamp_iso) is not str or not entry.timestamp_iso.strip():
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP: Bản ghi điều khiển {stg} của phiên {exec_id} thiếu timestamp_iso hợp lệ"
            )
        try:
            datetime.fromisoformat(entry.timestamp_iso.replace("Z", "+00:00"))
        except Exception as exc:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_TIMESTAMP: Bản ghi điều khiển {stg} của phiên {exec_id} có timestamp_iso không đúng định dạng ISO-8601: {exc}"
            )

        # 4. Thẩm định resolve_item_id: chuỗi rỗng ''
        if type(entry.resolve_item_id) is not str or entry.resolve_item_id != "":
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi điều khiển {stg} của phiên {exec_id} không được phép chứa resolve_item_id, nhận: {entry.resolve_item_id!r}"
            )

        # 5. Thẩm định track_type: chuỗi rỗng ''
        if type(entry.track_type) is not str or entry.track_type != "":
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi điều khiển {stg} của phiên {exec_id} không được phép chứa track_type, nhận: {entry.track_type!r}"
            )

        # 6. Thẩm định track_index: số nguyên 0 thuần túy, loại trừ bool
        if type(entry.track_index) is not int or type(entry.track_index) is bool or entry.track_index != 0:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi điều khiển {stg} của phiên {exec_id} không được phép chứa track_index khác 0, nhận: {entry.track_index!r}"
            )

        # 7. Thẩm định action_params: kiểu dict
        if type(entry.action_params) is not dict:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_params của bản ghi điều khiển {stg} phải là dict"
            )

        # 8. Thẩm định đối với START: note, error_code, error_message bắt buộc là ''
        if expected_stage == "START":
            if type(entry.note) is not str or entry.note != "":
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi START của phiên {exec_id} không được phép chứa note, nhận: {entry.note!r}"
                )
            if type(entry.error_code) is not str or entry.error_code != "":
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi START của phiên {exec_id} không được phép chứa error_code, nhận: {entry.error_code!r}"
                )
            if type(entry.error_message) is not str or entry.error_message != "":
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi START của phiên {exec_id} không được phép chứa error_message, nhận: {entry.error_message!r}"
                )

        # 9. Thẩm định đối với FINISH: note phải là chuỗi; nếu là observer timeout thì error_code và error_message bắt buộc là ''
        if expected_stage == "FINISH":
            if type(entry.note) is not str:
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi FINISH của phiên {exec_id} có trường note không phải chuỗi str: {type(entry.note).__name__}"
                )
            if entry.note == "HALTED_IN_DOUBT_OBSERVER_TIMEOUT":
                if type(entry.error_code) is not str or entry.error_code != "":
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi FINISH in-doubt của phiên {exec_id} không được phép chứa error_code, nhận: {entry.error_code!r}"
                    )
                if type(entry.error_message) is not str or entry.error_message != "":
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_CONTROL_RECORD_METADATA: Bản ghi FINISH in-doubt của phiên {exec_id} không được phép chứa error_message, nhận: {entry.error_message!r}"
                    )

    @classmethod
    def _validate_journal_wal_state_machine(
        cls,
        replay_entries: List[JournalEntry],
        planned_count: int,
        exec_id: str,
    ) -> Tuple[List[int], Set[int], int]:
        """Thẩm định máy trạng thái WAL nghiêm ngặt từng sự kiện trong nhật ký (B4-R4-RES-02, B4-R5-RES-03, B4-R6-RES-01, B4-R7-RES-01, B4-R8-RES-01).

        Quy tắc máy trạng thái:
        1. Bắt buộc có duy nhất một sự kiện START ở vị trí đầu tiên (B4-R7-RES-01, B4-R8-RES-01):
           Sự kiện đầu tiên trong nhật ký bắt buộc phải là START (ERR_JOURNAL_MISSING_START).
           Phong bì START bắt buộc chuẩn tắc: action_type='NO_OP', track_type='', track_index=0, resolve_item_id='', error/note='', timestamp_iso hợp lệ.
           Tham số total_actions bắt buộc phải là int thuần (loại trừ bool - ERR_JOURNAL_TOTAL_ACTIONS_TYPE),
           và phải khớp chính xác với planned_count (ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH).
           Cấm tuyệt đối bản ghi START trùng lặp hoặc đặt sai vị trí (ERR_JOURNAL_DUPLICATE_START).
        2. Chỉ cho phép các stages: START, ACTION_PENDING, ACTION_COMMITTED, ACTION_FAILED, FINISH.
           Các stage xung đột (ROLLBACK_PENDING, ROLLBACK_DONE, ROLLBACK_FAILED) bị từ chối với ERR_JOURNAL_CONFLICTING_STAGE.
           Các stage lạ bị từ chối với ERR_JOURNAL_UNKNOWN_STAGE.
        3. Đóng kín WAL tại stage kết thúc (Terminal WAL Closure - B4-R6-RES-01):
           Tuyệt đối cấm mọi bản ghi sau stage FINISH (ERR_JOURNAL_STAGE_AFTER_FINISH).
           Chỉ chấp nhận các note FINISH hợp lệ: COMPLETED_ALL_ACTIONS, HALTED_ON_ACTION_FAILURE,
           CANCELLED_AT_PRE_DISPATCH_BOUNDARY, HALTED_OCCUPANCY_SANITY_CHECK_FAILED, HALTED_IN_DOUBT_OBSERVER_TIMEOUT.
           Mọi note FINISH terminal hoàn tất/thất bại (không phải observer timeout) đều xung đột với trạng thái in-doubt (ERR_JOURNAL_CONFLICTING_STAGE)
           và không được phép để lại action dở dang có thể đối soát.
        4. Ràng buộc bản ghi FINISH in-doubt nghiêm ngặt (Strict In-Doubt Finish Binding - B4-R7-RES-01, B4-R8-RES-01):
           Phong bì FINISH bắt buộc chuẩn tắc: action_type='NO_OP', track_type='', track_index=0, resolve_item_id='', error_code/error_message='', timestamp_iso hợp lệ.
           Đối với HALTED_IN_DOUBT_OBSERVER_TIMEOUT:
           - Bắt buộc phải có đúng một action PENDING dở dang (ERR_JOURNAL_FINISH_WITHOUT_PENDING nếu không có).
           - Bắt buộc trường action_index phải tồn tại trong action_params (ERR_JOURNAL_ACTION_INDEX_MISSING).
           - Trường action_index bắt buộc là int thuần, loại trừ bool (ERR_JOURNAL_ACTION_INDEX_TYPE).
           - Trường action_index phải khớp tuyệt đối với action dở dang (ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS).
           - Cấm các trường thừa trong action_params của FINISH (ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS).
        5. Mỗi planned action i trong [0, planned_count - 1] phải tuân thủ nghiêm ngặt máy trạng thái:
           UNSEEN -> PENDING -> COMMITTED hoặc PENDING -> FAILED (hoặc dừng ở PENDING nếu đứt đoạn).
        6. Không cho phép commit-without-pending: Một action chỉ có thể chuyển sang COMMITTED nếu nó đang ở trạng thái PENDING.
        7. Không cho phép pending-after-commit: Nếu một action đã COMMITTED, bất kỳ ACTION_PENDING nào cho index đó đều bị từ chối.
        8. Không cho phép duplicate pending: Nếu một action đang ở trạng thái PENDING mà nhận thêm ACTION_PENDING (cùng index hoặc khác index khi chưa giải tỏa), bị từ chối.
        9. Không cho phép duplicate commit: Nếu một action đã COMMITTED mà nhận thêm ACTION_COMMITTED, bị từ chối.
        10. Tính nhất quán tuần tự: Các action phải thi công tuần tự 0, 1, ..., k.
            Action i chỉ có thể bắt đầu PENDING khi action i - 1 đã COMMITTED (với i > 0).
        11. Ràng buộc toàn vẹn tham số & payload (Exact Action Payload Binding - B4-R6-RES-01):
            Tham số action_type, track_type, track_index và toàn bộ payload action_params (khóa, giá trị, bytes chuẩn tắc UTF-8)
            của ACTION_COMMITTED hoặc ACTION_FAILED phải khớp tuyệt đối 1:1 với bản ghi ACTION_PENDING tương ứng.
            Mọi trường thừa, thiếu, hoặc biến đổi giá trị đều bị từ chối với ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS.
        12. Thẩm định ACTION_FAILED nghiêm ngặt (B4-R6-RES-01):
            ACTION_FAILED bắt buộc phải có một ACTION_PENDING đang hoạt động (không cho phép orphan ACTION_FAILED - ERR_JOURNAL_ORPHAN_ACTION_FAILED).
            Chỉ mục action trong ACTION_FAILED phải khớp chính xác với action đang pending (ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH).
        13. Kết thúc thẩm định: Phải có ĐÚNG MỘT action đang ở trạng thái PENDING (action dở dang / in-doubt).
            Index của action dở dang này phải bằng đúng số lượng actions đã commit (len(committed_indices)).

        Trả về:
            (committed_indices, committed_set, last_pending_act_idx)
        """
        if type(planned_count) is not int or planned_count <= 0:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_INVALID_PLANNED_COUNT: planned_actions_count={planned_count} không hợp lệ"
            )

        if not replay_entries:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_EMPTY: Tệp nhật ký của {exec_id} rỗng"
            )

        # B4-R7-RES-01 / B4-R8-RES-01: Thẩm định sự kiện START đầu tiên duy nhất & envelope điều khiển
        first_entry = replay_entries[0]
        if first_entry.stage != "START":
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_MISSING_START: Sự kiện đầu tiên trong nhật ký của {exec_id} phải là START, nhận: '{first_entry.stage}'"
            )

        # Thẩm định envelope cho bản ghi điều khiển START (B4-R8-RES-01)
        cls._validate_control_record_envelope(first_entry, expected_stage="START", exec_id=exec_id)

        start_params = first_entry.action_params if isinstance(first_entry.action_params, dict) else {}
        if "total_actions" not in start_params:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_TOTAL_ACTIONS_MISSING: Bản ghi START thiếu total_actions trong nhật ký của {exec_id}"
            )
        tot_act = start_params["total_actions"]
        if type(tot_act) is not int or type(tot_act) is bool:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_TOTAL_ACTIONS_TYPE: total_actions trong START phải là int thuần, nhận: {type(tot_act).__name__}"
            )
        if tot_act != planned_count:
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_TOTAL_ACTIONS_MISMATCH: total_actions trong START ({tot_act}) không khớp planned_count ({planned_count})"
            )
        if set(start_params.keys()) != {"total_actions"}:
            extra_start_keys = set(start_params.keys()) - {"total_actions"}
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_params trong START chứa trường thừa: {sorted(extra_start_keys)}"
            )

        VALID_TERMINAL_FINISH_NOTES = {
            "COMPLETED_ALL_ACTIONS",
            "HALTED_ON_ACTION_FAILURE",
            "CANCELLED_AT_PRE_DISPATCH_BOUNDARY",
            "HALTED_OCCUPANCY_SANITY_CHECK_FAILED",
        }
        VALID_IN_DOUBT_FINISH_NOTES = {
            "HALTED_IN_DOUBT_OBSERVER_TIMEOUT",
        }
        ALL_VALID_FINISH_NOTES = VALID_TERMINAL_FINISH_NOTES | VALID_IN_DOUBT_FINISH_NOTES

        action_states: Dict[int, str] = {i: "UNSEEN" for i in range(planned_count)}
        action_pending_entries: Dict[int, JournalEntry] = {}
        committed_indices: List[int] = []
        committed_set: Set[int] = set()
        current_pending_action_index: Optional[int] = None
        has_seen_finish: bool = False

        for idx, entry in enumerate(replay_entries):
            stage = entry.stage

            # Bỏ qua START đầu tiên (đã được thẩm định tiền trạm)
            if idx == 0:
                continue

            # B4-R7-RES-01: Từ chối bản ghi START trùng lặp hoặc đặt sai vị trí
            if stage == "START":
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_DUPLICATE_START: Phát hiện bản ghi START trùng lặp hoặc đặt sai vị trí tại dòng {idx} trong nhật ký của {exec_id}"
                )

            # 2. Cấm thao tác phát sinh sau stage FINISH (B4-R6-RES-01)
            if has_seen_finish:
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_STAGE_AFTER_FINISH: Phát hiện stage '{stage}' sau khi đã kết thúc với FINISH trong nhật ký của {exec_id}"
                )

            if stage in ("ROLLBACK_PENDING", "ROLLBACK_DONE", "ROLLBACK_FAILED"):
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_CONFLICTING_STAGE: Phát hiện stage xung đột '{stage}' trong nhật ký của {exec_id}"
                )

            if stage not in ("ACTION_PENDING", "ACTION_COMMITTED", "ACTION_FAILED", "FINISH"):
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_UNKNOWN_STAGE: Phát hiện stage không xác định '{stage}' trong nhật ký của {exec_id}"
                )

            if stage == "FINISH":
                has_seen_finish = True
                # Thẩm định envelope cho bản ghi điều khiển FINISH (B4-R8-RES-01)
                cls._validate_control_record_envelope(entry, expected_stage="FINISH", exec_id=exec_id)

                note = entry.note or ""
                if note not in ALL_VALID_FINISH_NOTES:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_UNKNOWN_STAGE: Bản ghi FINISH có note không xác định: '{note}' trong nhật ký của {exec_id}"
                    )

                if note in VALID_TERMINAL_FINISH_NOTES:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_CONFLICTING_STAGE: Phát hiện stage FINISH ({note}) xung đột với trạng thái in-doubt của {exec_id}"
                    )

                # B4-R7-RES-01: Thẩm định nghiêm ngặt FINISH in-doubt
                if current_pending_action_index is None:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_FINISH_WITHOUT_PENDING: FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) nhưng không có action dở dang nào trong nhật ký của {exec_id}"
                    )
                params = entry.action_params if isinstance(entry.action_params, dict) else {}
                if "action_index" not in params:
                    raise WholePlanReconciliationError(
                        "ERR_JOURNAL_ACTION_INDEX_MISSING: action_params trong FINISH (HALTED_IN_DOUBT_OBSERVER_TIMEOUT) thiếu action_index"
                    )
                f_idx = params["action_index"]
                if type(f_idx) is not int or type(f_idx) is bool:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_TYPE: action_index trong FINISH phải là int thuần, nhận: {type(f_idx).__name__}"
                    )
                if f_idx != current_pending_action_index:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_index trong FINISH ({f_idx}) không khớp với action đang dở dang ({current_pending_action_index})"
                    )
                if set(params.keys()) != {"action_index"}:
                    extra_finish_keys = set(params.keys()) - {"action_index"}
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_params trong FINISH chứa trường thừa: {sorted(extra_finish_keys)}"
                    )
                continue

            if stage == "ACTION_PENDING":
                params = entry.action_params if isinstance(entry.action_params, dict) else {}
                if "action_index" not in params:
                    raise WholePlanReconciliationError(
                        "ERR_JOURNAL_ACTION_INDEX_MISSING: action_params trong ACTION_PENDING thiếu action_index"
                    )
                p_idx = params["action_index"]
                if type(p_idx) is not int or type(p_idx) is bool:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_TYPE: action_index trong ACTION_PENDING phải là int thuần, nhận: {type(p_idx).__name__}"
                    )
                if p_idx < 0 or p_idx >= planned_count:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_OUT_OF_RANGE: action_index={p_idx} trong ACTION_PENDING ngoài phạm vi [0, {planned_count-1}]"
                    )

                # Kiểm tra: nếu action này đã committed -> PENDING_AFTER_COMMIT (B4-R5-RES-03)
                if p_idx in committed_set or action_states.get(p_idx) == "COMMITTED":
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_PENDING_AFTER_COMMIT: Phát hiện ACTION_PENDING cho action_index={p_idx} sau khi đã ACTION_COMMITTED"
                    )

                # Kiểm tra: nếu action này đang pending -> REPEATED_PENDING
                if current_pending_action_index == p_idx or action_states.get(p_idx) == "PENDING":
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_REPEATED_PENDING: Phát hiện ACTION_PENDING lặp lại cho cùng action_index={p_idx}"
                    )

                # Kiểm tra: nếu đang có action khác đang pending chưa giải tỏa -> MULTIPLE_UNRESOLVED_PENDING
                if current_pending_action_index is not None:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_MULTIPLE_UNRESOLVED_PENDING: Phát hiện nhiều bản ghi ACTION_PENDING chưa giải tỏa: "
                        f"action {current_pending_action_index} đang chờ giải tỏa thì nhận thêm action {p_idx}"
                    )

                # Kiểm tra tính tuần tự: action p_idx chỉ có thể bắt đầu khi đúng bằng số lượng action đã commit
                if p_idx != len(committed_indices):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_PENDING_INDEX_INCONSISTENT: Action dở dang (index={p_idx}) "
                        f"không khớp với vị trí kế tiếp sau {len(committed_indices)} actions đã commit"
                    )

                action_states[p_idx] = "PENDING"
                action_pending_entries[p_idx] = entry
                current_pending_action_index = p_idx

            elif stage == "ACTION_COMMITTED":
                params = entry.action_params if isinstance(entry.action_params, dict) else {}
                if "action_index" not in params:
                    raise WholePlanReconciliationError(
                        "ERR_JOURNAL_ACTION_INDEX_MISSING: action_params trong ACTION_COMMITTED thiếu action_index"
                    )
                act_idx = params["action_index"]
                if type(act_idx) is not int or type(act_idx) is bool:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_TYPE: action_index trong ACTION_COMMITTED phải là int thuần, nhận: {type(act_idx).__name__}"
                    )
                if act_idx < 0 or act_idx >= planned_count:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_OUT_OF_RANGE: action_index={act_idx} trong ACTION_COMMITTED ngoài phạm vi [0, {planned_count-1}]"
                    )

                # Kiểm tra: commit trùng lặp
                if act_idx in committed_set or action_states.get(act_idx) == "COMMITTED":
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_DUPLICATE_COMMIT: Phát hiện bản ghi commit trùng lặp cho action_index={act_idx}"
                    )

                # Kiểm tra tính tuần tự của chuỗi commit
                if act_idx != len(committed_indices):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_COMMIT_SEQUENCE_GAP: Thứ tự commit trong nhật ký không liên tục từ 0: "
                        f"nhận action_index={act_idx} nhưng mong đợi {len(committed_indices)}"
                    )

                # Kiểm tra commit without pending (B4-R5-RES-03)
                if action_states.get(act_idx) != "PENDING" or current_pending_action_index != act_idx:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_COMMIT_WITHOUT_PENDING: Phát hiện ACTION_COMMITTED cho action_index={act_idx} khi chưa có ACTION_PENDING hợp lệ"
                    )

                # Kiểm tra nhất quán tham số với bản ghi ACTION_PENDING trước đó (B4-R5-RES-03 & B4-R6-RES-01)
                pending_entry = action_pending_entries[act_idx]
                if (
                    entry.action_type != pending_entry.action_type
                    or entry.track_type != pending_entry.track_type
                    or entry.track_index != pending_entry.track_index
                ):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Tham số loại/track giữa ACTION_PENDING và ACTION_COMMITTED của action_index={act_idx} không nhất quán "
                        f"(pending: type={pending_entry.action_type}, track={pending_entry.track_type}#{pending_entry.track_index} vs "
                        f"committed: type={entry.action_type}, track={entry.track_type}#{entry.track_index})"
                    )

                # B4-R6-RES-01: Thẩm định toàn vẹn payload authority-bearing action_params
                p_params = pending_entry.action_params if isinstance(pending_entry.action_params, dict) else {}
                c_params = params
                if set(p_params.keys()) != set(c_params.keys()):
                    diff_keys = set(p_params.keys()) ^ set(c_params.keys())
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_params giữa ACTION_PENDING và ACTION_COMMITTED của action_index={act_idx} có trường lệch nhau: {sorted(diff_keys)}"
                    )
                for k, p_val in p_params.items():
                    c_val = c_params[k]
                    if p_val != c_val:
                        raise WholePlanReconciliationError(
                            f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Trường '{k}' trong action_params giữa ACTION_PENDING ({p_val!r}) và ACTION_COMMITTED ({c_val!r}) của action_index={act_idx} không khớp"
                        )
                if HmacEvidenceTrustProvider.canonical_serialize(p_params) != HmacEvidenceTrustProvider.canonical_serialize(c_params):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Payload canonical bytes giữa ACTION_PENDING và ACTION_COMMITTED của action_index={act_idx} không khớp"
                    )

                action_states[act_idx] = "COMMITTED"
                committed_indices.append(act_idx)
                committed_set.add(act_idx)
                current_pending_action_index = None

            elif stage == "ACTION_FAILED":
                # B4-R6-RES-01: Thẩm định ACTION_FAILED nghiêm ngặt
                if current_pending_action_index is None:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ORPHAN_ACTION_FAILED: Phát hiện ACTION_FAILED khi không có ACTION_PENDING nào đang hoạt động trong nhật ký của {exec_id}"
                    )

                params = entry.action_params if isinstance(entry.action_params, dict) else {}
                failed_idx = params.get("action_index")
                if failed_idx is None:
                    failed_idx = params.get("failed_action_index")
                if failed_idx is None:
                    raise WholePlanReconciliationError(
                        "ERR_JOURNAL_ACTION_INDEX_MISSING: action_params trong ACTION_FAILED thiếu action_index"
                    )
                if type(failed_idx) is not int or type(failed_idx) is bool:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_INDEX_TYPE: action_index trong ACTION_FAILED phải là int thuần, nhận: {type(failed_idx).__name__}"
                    )
                if failed_idx != current_pending_action_index:
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_ACTION_FAILED_INDEX_MISMATCH: ACTION_FAILED có index={failed_idx} không khớp với action đang pending={current_pending_action_index}"
                    )

                pending_entry = action_pending_entries[current_pending_action_index]
                if (
                    entry.action_type != pending_entry.action_type
                    or entry.track_type != pending_entry.track_type
                    or entry.track_index != pending_entry.track_index
                ):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Tham số loại/track giữa ACTION_PENDING và ACTION_FAILED của action_index={failed_idx} không nhất quán "
                        f"(pending: type={pending_entry.action_type}, track={pending_entry.track_type}#{pending_entry.track_index} vs "
                        f"failed: type={entry.action_type}, track={entry.track_type}#{entry.track_index})"
                    )

                # Chuẩn hóa params nếu failed_action_index được dùng thay vì action_index
                f_params = dict(params)
                if "failed_action_index" in f_params and "action_index" not in f_params:
                    f_params["action_index"] = f_params.pop("failed_action_index")

                p_params = pending_entry.action_params if isinstance(pending_entry.action_params, dict) else {}
                if set(p_params.keys()) != set(f_params.keys()):
                    diff_keys = set(p_params.keys()) ^ set(f_params.keys())
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: action_params giữa ACTION_PENDING và ACTION_FAILED của action_index={failed_idx} có trường lệch nhau: {sorted(diff_keys)}"
                    )
                for k, p_val in p_params.items():
                    f_val = f_params[k]
                    if p_val != f_val:
                        raise WholePlanReconciliationError(
                            f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Trường '{k}' trong action_params giữa ACTION_PENDING ({p_val!r}) và ACTION_FAILED ({f_val!r}) của action_index={failed_idx} không khớp"
                        )
                if HmacEvidenceTrustProvider.canonical_serialize(p_params) != HmacEvidenceTrustProvider.canonical_serialize(f_params):
                    raise WholePlanReconciliationError(
                        f"ERR_JOURNAL_INCONSISTENT_ACTION_PARAMS: Payload canonical bytes giữa ACTION_PENDING và ACTION_FAILED của action_index={failed_idx} không khớp"
                    )

                action_states[current_pending_action_index] = "FAILED"
                current_pending_action_index = None

        if current_pending_action_index is None:
            raise WholePlanReconciliationError(
                f"ERR_NO_PENDING_ACTION: Tệp nhật ký của {exec_id} không có bản ghi ACTION_PENDING dở dang nào chưa giải tỏa"
            )

        last_pending_act_idx = current_pending_action_index

        if last_pending_act_idx != len(committed_indices):
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_PENDING_INDEX_INCONSISTENT: Action dở dang (index={last_pending_act_idx}) "
                f"không khớp với vị trí kế tiếp sau {len(committed_indices)} actions đã commit"
            )

        return committed_indices, committed_set, last_pending_act_idx

    def reconcile_target(
        self,
        proof: OperatorReconciliationProof,
        evidence_verifier: Optional[ReconciliationEvidenceVerifier] = None,
    ) -> bool:
        """Giải tỏa và đối soát timeline đích có thẩm quyền (B4-R2-RES-01 & RES-02).

        Bắt buộc thực thi dưới target lock. Thẩm định chặt chẽ hồ sơ sở hữu bền vững trên đĩa,
        biên bản receipt, nhật ký WAL (replay read-only không sửa byte), và tệp artifact bằng chứng
        trước khi cho phép bất kỳ đột biến trạng thái nào.
        """
        validate_reconciliation_proof(proof)
        proj_id = proof.target_project_id
        tl_id = proof.target_timeline_id
        exec_id = proof.target_execution_id

        # 1. Chiếm target lock nếu chưa được giữ bởi registry trong tiến trình hiện hành
        is_held_in_registry = self.unresolved_registry.is_target_unresolved(proj_id, tl_id)
        target_lock_holder: Optional[_EngineLockHolder] = None

        if not is_held_in_registry:
            target_lock_ctx = self.receipt_service.acquire_target_lock(proj_id, tl_id)
            target_lock_holder = _EngineLockHolder(target_lock_ctx)
            target_lock_holder.acquire()

        try:
            # 2. Thẩm định hồ sơ sở hữu đích bền vững (durable target state)
            try:
                st = self.receipt_service.get_target_state(proj_id, tl_id)
            except TargetStateCorruptError as exc:
                raise ValueError(f"ERR_TARGET_STATE_CORRUPT: Hồ sơ target state bị hỏng: {exc}") from exc
            except OSError as exc:
                raise ValueError(f"ERR_TARGET_STORAGE_ACCESS_FAILED: Không thể đọc target state: {exc}") from exc

            if st is None:
                # Nếu không có file target state trên đĩa, kiểm tra xem in-memory registry có ghi nhận không
                if not self.unresolved_registry.is_target_unresolved(proj_id, tl_id):
                    raise ValueError(
                        f"ERR_TARGET_NOT_UNRESOLVED: Không tìm thấy phiên dở dang nào trên timeline đích ({proj_id}, {tl_id})"
                    )
                active_exec_id = exec_id
            else:
                if not isinstance(st, dict):
                    raise ValueError(f"ERR_TARGET_STATE_CORRUPT: Hồ sơ target state của ({proj_id}, {tl_id}) không phải JSON Object")
                if st.get("state") == "RECONCILED":
                    raise ValueError(f"ERR_TARGET_ALREADY_RECONCILED: Timeline ({proj_id}, {tl_id}) đã ở trạng thái RECONCILED")

                active_exec_id = str(st.get("active_execution_id", ""))
                if not active_exec_id:
                    raise ValueError("ERR_TARGET_STATE_INVALID: active_execution_id bị thiếu trong target state")

                if active_exec_id != exec_id:
                    raise ValueError(
                        f"ERR_PROOF_EXECUTION_MISMATCH: proof execution_id ({exec_id}) không khớp "
                        f"active_execution_id bền vững ({active_exec_id}) của timeline ({proj_id}, {tl_id})"
                    )

            # 3. Thẩm định Receipt trên đĩa
            try:
                curr_rec = self.receipt_service.load_receipt(exec_id)
            except Exception as exc:
                raise ValueError(f"ERR_RECEIPT_NOT_FOUND: Không thể tải biên bản của phiên {exec_id}: {exc}") from exc

            if curr_rec.project_id != proj_id or curr_rec.timeline_id != tl_id:
                raise ValueError(
                    f"ERR_RECEIPT_TARGET_MISMATCH: Biên bản {exec_id} thuộc ({curr_rec.project_id}, {curr_rec.timeline_id}) "
                    f"không khớp proof ({proj_id}, {tl_id})"
                )

            # B4-R4-RES-04: Thẩm định khả năng phục hồi sau sự cố crash giữa các pha bền vững
            is_crash_recovering = (
                st is not None
                and isinstance(st, dict)
                and st.get("state") == "RECONCILING"
                and st.get("reconciliation_proof_id") == proof.proof_id
                and curr_rec.state in ("COMPLETED", "ABORTED")
            )

            if not is_crash_recovering and curr_rec.state not in ("INITIALIZED", "EXECUTING"):
                raise ValueError(
                    f"ERR_RECEIPT_ALREADY_TERMINAL: Biên bản {exec_id} đã ở trạng thái kết thúc ({curr_rec.state})"
                )

            # 4. Replay Journal chỉ đọc (read-only replay không sửa byte)
            try:
                replay = self.journal_service.replay_journal(exec_id)
            except Exception as exc:
                raise WholePlanReconciliationError(f"ERR_JOURNAL_REPLAY_FAILED: Không thể đọc nhật ký của {exec_id}: {exc}") from exc

            if replay.torn_tail is not None:
                raise WholePlanReconciliationError(
                    f"ERR_JOURNAL_TORN_TAIL: Tệp nhật ký của {exec_id} bị rách đuôi (torn tail). "
                    f"Từ chối đối soát tự động khi chưa cách ly pháp y."
                )

            # B4-R4-RES-02 & B4-R5-RES-03: Thẩm định máy trạng thái WAL toàn bộ sổ hành vi
            planned_count = curr_rec.planned_actions_count
            committed_indices, committed_set, last_pending_act_idx = self._validate_journal_wal_state_machine(
                replay_entries=replay.entries,
                planned_count=planned_count,
                exec_id=exec_id,
            )

            if proof.in_doubt_action_index is not None and proof.in_doubt_action_index != last_pending_act_idx:
                raise ValueError(
                    f"ERR_ACTION_INDEX_MISMATCH: in_doubt_action_index trong proof ({proof.in_doubt_action_index}) "
                    f"không khớp action dở dang trong nhật ký ({last_pending_act_idx})"
                )

            # 5. Thẩm định tệp Artifact bằng chứng độc lập (B4-R2-RES-01, B4-R3-RES-02, B4-R4-RES-01 & B4-R4-RES-03)
            verifier = evidence_verifier or self.evidence_verifier
            verifier.verify_evidence(
                proof=proof,
                expected_project_id=proj_id,
                expected_timeline_id=tl_id,
                expected_execution_id=exec_id,
                expected_action_index=last_pending_act_idx,
            )

            # 6. B4-R3-RES-01 & B4-R4-RES-02: Thẩm định điều kiện kết thúc kế hoạch toàn diện (Whole-Plan Bijection)
            committed_count = len(committed_indices)

            if proof.verified_outcome == "ACTION_COMMITTED_CONFIRMED":
                full_committed_set = committed_set | {last_pending_act_idx}
                expected_full_set = set(range(planned_count))

                if full_committed_set == expected_full_set:
                    terminal_state = "COMPLETED"
                elif len(full_committed_set) < planned_count:
                    raise WholePlanReconciliationError(
                        f"ERR_RECONCILIATION_INCOMPLETE_PLAN: Action {last_pending_act_idx} được xác nhận COMMITTED, "
                        f"nhưng kế hoạch mới chỉ hoàn thành {len(full_committed_set)}/{planned_count} actions. "
                        f"Không thể chuyển receipt sang COMPLETED khi các actions sau chưa thi công. "
                        f"Target ({proj_id}, {tl_id}) tiếp tục bị khóa và unresolved."
                    )
                else:
                    raise WholePlanReconciliationError(
                        f"ERR_RECONCILIATION_ACTION_COUNT_OVERFLOW: Số actions committed ({len(full_committed_set)}) "
                        f"vượt quá số actions đã lập kế hoạch ({planned_count})."
                    )
            elif proof.verified_outcome in ("ACTION_NOT_DISPATCHED_CONFIRMED", "ACTION_FAILED_CONFIRMED"):
                if committed_count == 0 and last_pending_act_idx == 0:
                    terminal_state = "ABORTED"
                else:
                    if committed_count > 0:
                        raise WholePlanReconciliationError(
                            f"ERR_RECONCILIATION_CANNOT_ABORT_COMMITTED_ACTIONS: Action {last_pending_act_idx} "
                            f"xác nhận không điều phối/thất bại ({proof.verified_outcome}), nhưng đã có {committed_count} "
                            f"action(s) đã commit trước đó trên timeline. "
                            f"Receipt không thể chuyển sang ABORTED khi đã có đột biến. "
                            f"Target ({proj_id}, {tl_id}) tiếp tục bị khóa và unresolved pending whole-plan disposition."
                        )
                    else:
                        raise WholePlanReconciliationError(
                            f"ERR_RECONCILIATION_INCONSISTENT_ABORT: Không thể ABORT khi pending action index={last_pending_act_idx} != 0"
                        )
            else:
                raise ValueError(
                    f"ERR_PROOF_INVALID_OUTCOME: verified_outcome {proof.verified_outcome!r} không hợp lệ"
                )

            if is_crash_recovering and curr_rec.state != terminal_state:
                raise WholePlanReconciliationError(
                    f"ERR_RECONCILIATION_CRASH_RECOVERY_STATE_CONTRADICTION: Biên bản receipt có trạng thái {curr_rec.state} "
                    f"nhưng kết quả đối soát yêu cầu {terminal_state}"
                )

            # 7. Giao thức bền vững đa pha chống Crash (B4-R4-RES-04)
            # Pha 1: Đánh dấu ý định đối soát bền vững (RECONCILING, in_doubt=True)
            if not is_crash_recovering or (st and st.get("state") != "RECONCILING"):
                self.receipt_service.persist_target_state(
                    project_id=proj_id,
                    timeline_id=tl_id,
                    payload={
                        "project_id": proj_id,
                        "timeline_id": tl_id,
                        "active_execution_id": exec_id,
                        "state": "RECONCILING",
                        "in_doubt": True,
                        "target_terminal_state": terminal_state,
                        "reconciliation_proof_id": proof.proof_id,
                        "verified_outcome": proof.verified_outcome,
                        "operator_id": proof.operator_id,
                        "updated_at_iso": proof.reconciliation_timestamp_iso,
                    },
                )

            # Pha 2: Cập nhật Receipt dở dang sang trạng thái terminal (không ghi đè target state ở pha này)
            if curr_rec.state != terminal_state:
                self.receipt_service.transition_state(
                    exec_id,
                    new_state=terminal_state,
                    updated_at_iso=proof.reconciliation_timestamp_iso,
                    completed_at_iso=proof.reconciliation_timestamp_iso if terminal_state == "COMPLETED" else None,
                    last_error_code="RECONCILED_BY_OPERATOR",
                    last_error_message=f"Reconciled by {proof.operator_id}: {proof.verified_outcome} ({proof.proof_id})",
                    update_target_state=False,
                )

            # Pha 3: Cập nhật hồ sơ target state bền vững sang RECONCILED (in_doubt=False)
            self.receipt_service.persist_target_state(
                project_id=proj_id,
                timeline_id=tl_id,
                payload={
                    "project_id": proj_id,
                    "timeline_id": tl_id,
                    "active_execution_id": exec_id,
                    "state": "RECONCILED",
                    "in_doubt": False,
                    "reconciliation_proof_id": proof.proof_id,
                    "verified_outcome": proof.verified_outcome,
                    "operator_id": proof.operator_id,
                    "updated_at_iso": proof.reconciliation_timestamp_iso,
                },
            )

            # Pha 4: Giải phóng registry trong bộ nhớ nếu còn tồn tại
            if self.unresolved_registry.is_target_unresolved(proj_id, tl_id):
                self.unresolved_registry.resolve_and_release(
                    proj_id,
                    tl_id,
                    proof,
                    evidence_verifier=verifier,
                )

            return True
        finally:
            if target_lock_holder is not None and target_lock_holder.is_acquired:
                target_lock_holder.release()

    def execute_plan(
        self,
        plan: TimelineExecutionPlan,
        approval: AppOwnedApprovalRecord,
        execution_id: Optional[str] = None,
        is_cancellation_requested: Optional[Callable[[], bool]] = None,
        action_timeout_sec: float = 15.0,
    ) -> ExecutionEngineResult:
        """Thực thi một TimelineExecutionPlan đã nghiệm thu theo giao thức WAL an toàn tuyệt đối."""
        # ---------------------------------------------------------------------
        # 0. B4-R5: Thẩm định Cổng Mock-Only
        # ---------------------------------------------------------------------
        try:
            self._validate_mock_gateway_admission()
        except MockGatewayAdmissionError as exc:
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key if hasattr(plan, "idempotency_key") else "",
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions) if hasattr(plan, "planned_actions") else 0,
                error_code="ERR_MOCK_ONLY_GATEWAY_REQUIRED",
                error_message=str(exc),
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        # ---------------------------------------------------------------------
        # 1. CP-01: Revalidate Plan & Approval Binding
        # ---------------------------------------------------------------------
        if not isinstance(plan, TimelineExecutionPlan):
            raise ValueError("ERR_INVALID_PLAN: plan phải là TimelineExecutionPlan")
        if not isinstance(approval, AppOwnedApprovalRecord):
            raise ValueError("ERR_INVALID_APPROVAL: approval phải là AppOwnedApprovalRecord")

        if plan.binding_status != "BOUND_VERIFIED":
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_PLAN_NOT_VERIFIED",
                error_message=f"Kế hoạch chưa được xác thực ràng buộc an toàn (status: {plan.binding_status})",
            )

        if plan.proposal_id != approval.proposal_id:
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_STALE_PROPOSAL_REVISION",
                error_message="proposal_id trong kế hoạch không khớp bản ghi phê duyệt",
            )

        if plan.proposal_revision != approval.proposal_revision:
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_STALE_PROPOSAL_REVISION",
                error_message=f"proposal_revision ({plan.proposal_revision}) không khớp approval ({approval.proposal_revision})",
            )

        if plan.approval_id != approval.approval_id:
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_STALE_PROPOSAL_REVISION",
                error_message="approval_id trong kế hoạch không khớp bản ghi phê duyệt",
            )

        # ---------------------------------------------------------------------
        # 2. CP-03 / FLT-08: Idempotency Check & Duplicate Request Handling
        # ---------------------------------------------------------------------
        prior_idemp = self.receipt_service.check_idempotency(plan.idempotency_key)
        if prior_idemp:
            prior_state = prior_idemp.get("state")
            if prior_state == "COMPLETED":
                return ExecutionEngineResult(
                    status="DUPLICATE_REQUEST_PREVIOUSLY_COMPLETED",
                    execution_id=prior_idemp.get("execution_id", ""),
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    prior_receipt_id=prior_idemp.get("execution_id"),
                    prior_completed_at=prior_idemp.get("updated_at_iso"),
                    error_code="ERR_IDEMPOTENCY_DUPLICATE_COMPLETED",
                    error_message="Kịch bản đã được thi công hoàn tất trong phiên trước đó. Không có thao tác mới nào được gửi tới Resolve.",
                )
            if prior_state in ("INITIALIZED", "EXECUTING"):
                return ExecutionEngineResult(
                    status="FAILED_PRE_MUTATION",
                    execution_id=prior_idemp.get("execution_id", ""),
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_IDEMPOTENCY_DUPLICATE_ACTIVE",
                    error_message=f"Phát hiện phiên {prior_idemp.get('execution_id')} đang hoạt động hoặc dở dang trên kịch bản này.",
                )

        target_proj_id = plan.target_spec.project_id
        target_tl_id = plan.target_spec.timeline_id

        # ---------------------------------------------------------------------
        # 3. B4-R4: Kiểm tra Target Unresolved Registry
        # ---------------------------------------------------------------------
        if self.unresolved_registry.is_target_unresolved(target_proj_id, target_tl_id):
            unres_rec = self.unresolved_registry.get_record(target_proj_id, target_tl_id)
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=execution_id or "",
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_TARGET_HAS_UNRESOLVED_IN_DOUBT",
                error_message=(
                    f"Timeline đích ({target_proj_id}, {target_tl_id}) đang có phiên bất định "
                    f"chưa được giải tỏa: {unres_rec.execution_id if unres_rec else 'UNKNOWN'}. "
                    f"Lý do: {unres_rec.reason if unres_rec else ''}"
                ),
            )

        # ---------------------------------------------------------------------
        # 4. B4-R1: Chiếm Khóa Độc Quyền Theo Target Timeline (Target-Scoped Lock)
        # ---------------------------------------------------------------------
        actual_exec_id = execution_id or f"exec_{uuid.uuid4().hex[:16]}"
        target_lock_ctx = self.receipt_service.acquire_target_lock(target_proj_id, target_tl_id)
        target_lock_holder = _EngineLockHolder(target_lock_ctx)

        try:
            target_lock_holder.acquire()
        except ExecutionLockActiveError as exc:
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=actual_exec_id,
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_TARGET_TIMELINE_LOCKED",
                error_message=f"Timeline đích ({target_proj_id}, {target_tl_id}) đang bị chiếm bởi một phiên thi công khác: {exc}",
            )

        # Chiếm khóa theo execution_id
        exec_lock_ctx = self.receipt_service.acquire_lock(actual_exec_id)
        exec_lock_holder = _EngineLockHolder(exec_lock_ctx)

        try:
            exec_lock_holder.acquire()
        except ExecutionLockActiveError as exc:
            target_lock_holder.release()
            return ExecutionEngineResult(
                status="FAILED_PRE_MUTATION",
                execution_id=actual_exec_id,
                idempotency_key=plan.idempotency_key,
                execution_dispatched=False,
                committed_actions_count=0,
                total_actions_count=len(plan.planned_actions),
                error_code="ERR_EXECUTION_LOCKED",
                error_message=str(exc),
            )

        receipt: Optional[ExecutionReceipt] = None
        committed_ids: List[str] = []
        committed_spans: Set[Tuple[str, int, int, int]] = set()
        committed_count = 0
        journal_seq = 0
        lock_retained = False

        try:
            # -----------------------------------------------------------------
            # 4b. B4-R1-RES-02 & B4-R2-RES-03: Kiểm tra Bằng Chứng Bền Vững (Restart-Safe Target Admission)
            # -----------------------------------------------------------------
            durable_blocker = self._check_durable_target_admission(target_proj_id, target_tl_id)
            if durable_blocker:
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_PRE_MUTATION",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code=durable_blocker["error_code"],
                    error_message=durable_blocker["error_message"],
                )
            # -----------------------------------------------------------------
            # 5. CP-04 & CP-05: Target Identity & FPS Verification
            # -----------------------------------------------------------------
            try:
                authority = self.gateway.query_active_timeline_authority()
            except Exception as exc:
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_PRE_MUTATION",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_GATEWAY_AUTHORITY_UNAVAILABLE",
                    error_message=f"Không thể truy vấn thông tin thẩm quyền từ Gateway: {exc}",
                )

            active_proj_id = str(authority.get("project_id", ""))
            active_tl_id = str(authority.get("timeline_id", ""))
            if (active_proj_id != target_proj_id) or (active_tl_id != target_tl_id):
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_PRE_MUTATION",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_TARGET_TIMELINE_SHIFTED",
                    error_message=(
                        f"Target timeline shifted: plan đòi hỏi project={target_proj_id}, "
                        f"timeline={target_tl_id}; Resolve đang mở project={active_proj_id}, "
                        f"timeline={active_tl_id}"
                    ),
                )

            active_fps = authority.get("fps")
            if not isinstance(active_fps, RationalFPS) or (active_fps != plan.target_spec.timeline_fps):
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_PRE_MUTATION",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_TARGET_FPS_DRIFT",
                    error_message=(
                        f"Target timeline FPS drift: plan đòi hỏi {plan.target_spec.timeline_fps}; "
                        f"Resolve đang có {active_fps}"
                    ),
                )

            # -----------------------------------------------------------------
            # 6. Ghi Biên Bản Tiền Đột Biến (INITIALIZED -> EXECUTING)
            # -----------------------------------------------------------------
            try:
                receipt = ExecutionReceipt(
                    schema_version=2,
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    proposal_id=plan.proposal_id,
                    proposal_revision=plan.proposal_revision,
                    approval_id=plan.approval_id,
                    approved_at_iso=plan.approved_at_iso,
                    project_name=plan.target_spec.project_name,
                    project_id=plan.target_spec.project_id,
                    timeline_name=plan.target_spec.timeline_name,
                    timeline_id=plan.target_spec.timeline_id,
                    timeline_fps_numerator=plan.target_spec.timeline_fps.numerator,
                    timeline_fps_denominator=plan.target_spec.timeline_fps.denominator,
                    timeline_resolution=plan.target_spec.timeline_resolution,
                    source_digests=plan.source_digests,
                    catalog_digests=plan.catalog_digests,
                    track_reservations=plan.track_reservations,
                    planned_actions_count=len(plan.planned_actions),
                    created_at_iso=now_iso,
                    state="INITIALIZED",
                )
                self.receipt_service.create_receipt(receipt)
                receipt = self.receipt_service.transition_state(
                    actual_exec_id,
                    new_state="EXECUTING",
                    updated_at_iso=now_iso,
                )
            except (OSError, IOError, ReceiptStorageError) as exc:
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_HALTED",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_STORAGE_FSYNC_FAILED",
                    error_message=f"Thất bại khi ghi biên bản tiền đột biến xuống đĩa: {exc}",
                )

            # Ghi dòng nhật ký START
            try:
                start_entry = JournalEntry(
                    seq=journal_seq,
                    stage="START",
                    action_type="NO_OP",
                    timestamp_iso=now_iso,
                    action_params={"total_actions": len(plan.planned_actions)},
                )
                self.journal_service.append_entry(actual_exec_id, start_entry)
                journal_seq += 1
            except (OSError, IOError, JournalStorageError) as exc:
                self.receipt_service.transition_state(
                    actual_exec_id,
                    new_state="ABORTED",
                    updated_at_iso=datetime.now(timezone.utc).isoformat(),
                    last_error_code="ERR_STORAGE_FSYNC_FAILED",
                )
                exec_lock_holder.release()
                target_lock_holder.release()
                return ExecutionEngineResult(
                    status="FAILED_HALTED",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=False,
                    committed_actions_count=0,
                    total_actions_count=len(plan.planned_actions),
                    error_code="ERR_STORAGE_FSYNC_FAILED",
                    error_message=f"Thất bại khi ghi journal START xuống đĩa: {exc}",
                )

            # -----------------------------------------------------------------
            # 7. Action Execution Loop
            # -----------------------------------------------------------------
            for action in plan.planned_actions:
                action_now = datetime.now(timezone.utc).isoformat()

                # CP-07 / FLT-09: Cancellation Checkpoint tại Ranh Giới Tiền Dispatch
                if is_cancellation_requested and is_cancellation_requested():
                    try:
                        finish_entry = JournalEntry(
                            seq=journal_seq,
                            stage="FINISH",
                            action_type="NO_OP",
                            timestamp_iso=action_now,
                            action_params={"action_index": action.action_index},
                            note="CANCELLED_AT_PRE_DISPATCH_BOUNDARY",
                        )
                        self.journal_service.append_entry(actual_exec_id, finish_entry)
                        journal_seq += 1
                    except Exception:
                        pass

                    if committed_count == 0:
                        receipt = self.receipt_service.transition_state(
                            actual_exec_id,
                            new_state="ABORTED",
                            updated_at_iso=action_now,
                            last_error_code="CANCELLED_PRE_DISPATCH",
                            last_error_message="Hủy bỏ thi công trước khi dispatch thao tác đầu tiên",
                        )
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="CANCELLED_PRE_DISPATCH",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=False,
                            committed_actions_count=0,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            error_code="CANCELLED_PRE_DISPATCH",
                            error_message="Hủy bỏ thi công trước khi dispatch thao tác đầu tiên",
                        )
                    else:
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="INTERRUPTED_WITH_COMMITTED_ACTIONS",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code="INTERRUPTED_WITH_COMMITTED_ACTIONS",
                            error_message=f"Đã dừng thi công sau khi {committed_count} actions được commit. Giữ nguyên trạng thái EXECUTING.",
                        )

                # -------------------------------------------------------------
                # B4-R2 / CP-06: Thẩm định Chiếm dụng Track Toàn Diện
                # -------------------------------------------------------------
                try:
                    occ_snap = self.gateway.query_track_occupancy_snapshot(plan.target_spec)
                    if occ_snap.status != "AUTHORITATIVE":
                        raise UnexpectedTrackOccupancyError(f"ERR_OCCUPANCY_NOT_AUTHORITATIVE: status {occ_snap.status!r}")

                    if occ_snap.project_id != target_proj_id or occ_snap.timeline_id != target_tl_id:
                        raise UnexpectedTrackOccupancyError("ERR_OCCUPANCY_TARGET_MISMATCH: Snapshot target không khớp")

                    if occ_snap.timeline_fps != plan.target_spec.timeline_fps:
                        raise UnexpectedTrackOccupancyError("ERR_OCCUPANCY_FPS_MISMATCH: Snapshot FPS không khớp")

                    # Kiểm tra track đích nằm trong scanned_tracks
                    target_track = (action.target_track_type, action.target_track_index)
                    if target_track not in set(occ_snap.scanned_tracks):
                        raise UnexpectedTrackOccupancyError(
                            f"ERR_OCCUPANCY_TRACK_NOT_SCANNED: Track {target_track} không được quét trong authoritative snapshot"
                        )

                    # So khớp va chạm không gian (external collision check)
                    act_start = action.timeline_range.start_frame
                    act_end = action.timeline_range.end_frame
                    for occ in occ_snap.occupied_spans:
                        if occ.track_type == action.target_track_type and occ.track_index == action.target_track_index:
                            # Nếu đây là clip do chính phiên hiện hành đã commit -> Bỏ qua
                            occ_tuple = (occ.track_type, occ.track_index, occ.start_frame, occ.end_frame)
                            if occ_tuple in committed_spans:
                                continue

                            # Kiểm tra giao cắt
                            if max(act_start, occ.start_frame) < min(act_end, occ.end_frame):
                                raise UnexpectedTrackOccupancyError(
                                    f"ERR_TRACK_COLLISION: Action [{act_start}, {act_end}) va chạm với clip hiện hữu "
                                    f"{occ.item_name!r} [{occ.start_frame}, {occ.end_frame}) trên track {target_track}"
                                )

                except Exception as exc:
                    err_str = str(exc)
                    try:
                        finish_entry = JournalEntry(
                            seq=journal_seq,
                            stage="FINISH",
                            action_type="NO_OP",
                            timestamp_iso=action_now,
                            action_params={"action_index": action.action_index},
                            error_code="ERR_UNEXPECTED_TRACK_OCCUPANCY",
                            error_message=err_str,
                            note="HALTED_OCCUPANCY_SANITY_CHECK_FAILED",
                        )
                        self.journal_service.append_entry(actual_exec_id, finish_entry)
                        journal_seq += 1
                    except Exception:
                        pass

                    if committed_count == 0:
                        receipt = self.receipt_service.transition_state(
                            actual_exec_id,
                            new_state="ABORTED",
                            updated_at_iso=action_now,
                            last_error_code="ERR_UNEXPECTED_TRACK_OCCUPANCY",
                            last_error_message=err_str,
                        )
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="FAILED_HALTED",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=False,
                            committed_actions_count=0,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            error_code="ERR_UNEXPECTED_TRACK_OCCUPANCY",
                            error_message=err_str,
                        )
                    else:
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="INTERRUPTED_WITH_COMMITTED_ACTIONS",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code="ERR_UNEXPECTED_TRACK_OCCUPANCY",
                            error_message=err_str,
                        )

                # -------------------------------------------------------------
                # WAL Bước A: Ghi ACTION_PENDING và fsync bền vững trước dispatch
                # -------------------------------------------------------------
                try:
                    pending_params = dict(action.action_params)
                    pending_params["action_index"] = action.action_index
                    pending_params["clip_id"] = action.clip_id
                    pending_params["source_file_path"] = action.source_file_path
                    pending_entry = JournalEntry(
                        seq=journal_seq,
                        stage="ACTION_PENDING",
                        action_type=action.action_type,
                        timestamp_iso=action_now,
                        action_params=pending_params,
                        track_type=action.target_track_type,
                        track_index=action.target_track_index,
                    )
                    self.journal_service.append_entry(actual_exec_id, pending_entry)
                    journal_seq += 1
                except (OSError, IOError, JournalStorageError) as exc:
                    if committed_count == 0:
                        receipt = self.receipt_service.transition_state(
                            actual_exec_id,
                            new_state="ABORTED",
                            updated_at_iso=action_now,
                            last_error_code="ERR_STORAGE_FSYNC_FAILED",
                            last_error_message=str(exc),
                        )
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="FAILED_HALTED",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=False,
                            committed_actions_count=0,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            error_code="ERR_STORAGE_FSYNC_FAILED",
                            error_message=f"Thất bại fsync ACTION_PENDING: {exc}",
                        )
                    else:
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="INTERRUPTED_WITH_COMMITTED_ACTIONS",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code="ERR_STORAGE_FSYNC_FAILED",
                            error_message=f"Thất bại fsync ACTION_PENDING tại action {action.action_index}: {exc}",
                        )

                # -------------------------------------------------------------
                # Dispatch sang Resolve Gateway & Xử Lý Observer Timeout (FLT-06)
                # -------------------------------------------------------------
                try:
                    outcome = self.gateway.execute_action(action, timeout_sec=action_timeout_sec)
                except ObserverTimeoutError as exc:
                    # FLT-06 / B4-R4: Observer Timeout -> Giữ Lock trong Registry, Gateway BUSY, IN_DOUBT
                    lock_retained = True
                    finish_now = datetime.now(timezone.utc).isoformat()
                    try:
                        finish_entry = JournalEntry(
                            seq=journal_seq,
                            stage="FINISH",
                            action_type="NO_OP",
                            timestamp_iso=finish_now,
                            action_params={"action_index": action.action_index},
                            note="HALTED_IN_DOUBT_OBSERVER_TIMEOUT",
                        )
                        self.journal_service.append_entry(actual_exec_id, finish_entry)
                        journal_seq += 1
                    except Exception:
                        pass

                    # B4-R4: Lưu giữ quyền sở hữu trong registry tiến trình
                    self.unresolved_registry.register_unresolved(
                        project_id=target_proj_id,
                        timeline_id=target_tl_id,
                        execution_id=actual_exec_id,
                        target_lock_holder=target_lock_holder,
                        exec_lock_holder=exec_lock_holder,
                        in_doubt_action=action,
                        reason=f"Observer timeout ({action_timeout_sec}s): kết thúc vật lý chưa xác nhận.",
                    )
                    self.receipt_service.persist_target_state(
                        project_id=target_proj_id,
                        timeline_id=target_tl_id,
                        payload={
                            "project_id": target_proj_id,
                            "timeline_id": target_tl_id,
                            "active_execution_id": actual_exec_id,
                            "state": "IN_DOUBT",
                            "in_doubt": True,
                            "in_doubt_action_index": action.action_index,
                            "reason": f"Observer timeout ({action_timeout_sec}s): kết thúc vật lý chưa xác nhận.",
                            "updated_at_iso": finish_now,
                        },
                    )

                    return ExecutionEngineResult(
                        status="IN_DOUBT_OBSERVER_TIMEOUT",
                        execution_id=actual_exec_id,
                        idempotency_key=plan.idempotency_key,
                        execution_dispatched=True,
                        committed_actions_count=committed_count,
                        total_actions_count=len(plan.planned_actions),
                        receipt=receipt,
                        committed_item_ids=tuple(committed_ids),
                        error_code="ERR_OBSERVER_TIMEOUT_IN_DOUBT",
                        error_message=(
                            f"Thời gian quan sát ({action_timeout_sec}s) đã hết nhưng lệnh Resolve chưa kết thúc vật lý. "
                            f"Khóa cố vấn được giữ nguyên để bảo vệ timeline. Cấm đột biến kế tiếp."
                        ),
                        in_doubt_action=action,
                        lock_retained=True,
                    )
                except Exception as exc:
                    # Lỗi kết nối bất ngờ
                    lock_retained = True
                    self.unresolved_registry.register_unresolved(
                        project_id=target_proj_id,
                        timeline_id=target_tl_id,
                        execution_id=actual_exec_id,
                        target_lock_holder=target_lock_holder,
                        exec_lock_holder=exec_lock_holder,
                        in_doubt_action=action,
                        reason=f"Transport exception: {exc}",
                    )
                    self.receipt_service.persist_target_state(
                        project_id=target_proj_id,
                        timeline_id=target_tl_id,
                        payload={
                            "project_id": target_proj_id,
                            "timeline_id": target_tl_id,
                            "active_execution_id": actual_exec_id,
                            "state": "IN_DOUBT",
                            "in_doubt": True,
                            "in_doubt_action_index": action.action_index,
                            "reason": f"Transport exception: {exc}",
                            "updated_at_iso": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    return ExecutionEngineResult(
                        status="IN_DOUBT_OBSERVER_TIMEOUT",
                        execution_id=actual_exec_id,
                        idempotency_key=plan.idempotency_key,
                        execution_dispatched=True,
                        committed_actions_count=committed_count,
                        total_actions_count=len(plan.planned_actions),
                        receipt=receipt,
                        committed_item_ids=tuple(committed_ids),
                        error_code="ERR_GATEWAY_DISCONNECTED_IN_DOUBT",
                        error_message=f"Mất kết nối Gateway bất định trong lúc thực thi action {action.action_index}: {exc}",
                        in_doubt_action=action,
                        lock_retained=True,
                    )

                # -------------------------------------------------------------
                # WAL Bước B: B4-R3 - Bọc lỗi lưu trữ hậu dispatch
                # -------------------------------------------------------------
                post_now = datetime.now(timezone.utc).isoformat()
                is_success = outcome.get("success") is True

                if is_success:
                    created_item_id = str(outcome.get("item_id", ""))
                    commit_params = dict(action.action_params)
                    commit_params["action_index"] = action.action_index
                    commit_params["clip_id"] = action.clip_id
                    commit_params["source_file_path"] = action.source_file_path
                    commit_entry = JournalEntry(
                        seq=journal_seq,
                        stage="ACTION_COMMITTED",
                        action_type=action.action_type,
                        timestamp_iso=post_now,
                        action_params=commit_params,
                        resolve_item_id=created_item_id,
                        track_type=action.target_track_type,
                        track_index=action.target_track_index,
                    )

                    try:
                        self.journal_service.append_entry(actual_exec_id, commit_entry)
                        journal_seq += 1
                        committed_ids.append(created_item_id)
                        committed_spans.add((
                            action.target_track_type,
                            action.target_track_index,
                            action.timeline_range.start_frame,
                            action.timeline_range.end_frame,
                        ))
                        committed_count += 1
                    except (OSError, IOError, JournalStorageError) as exc:
                        # B4-R3: Hỏng lưu trữ đĩa khi ghi ACTION_COMMITTED -> Giữ Lock, Báo IN_DOUBT
                        lock_retained = True
                        self.unresolved_registry.register_unresolved(
                            project_id=target_proj_id,
                            timeline_id=target_tl_id,
                            execution_id=actual_exec_id,
                            target_lock_holder=target_lock_holder,
                            exec_lock_holder=exec_lock_holder,
                            in_doubt_action=action,
                            reason=f"Post-dispatch commit storage failure: {exc}",
                        )
                        self.receipt_service.persist_target_state(
                            project_id=target_proj_id,
                            timeline_id=target_tl_id,
                            payload={
                                "project_id": target_proj_id,
                                "timeline_id": target_tl_id,
                                "active_execution_id": actual_exec_id,
                                "state": "IN_DOUBT",
                                "in_doubt": True,
                                "in_doubt_action_index": action.action_index,
                                "reason": f"Post-dispatch commit storage failure: {exc}",
                                "updated_at_iso": post_now,
                            },
                        )
                        return ExecutionEngineResult(
                            status="IN_DOUBT_PERSISTENCE_FAILED",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code="ERR_POST_DISPATCH_STORAGE_FAILED",
                            error_message=f"Thất bại khi ghi ACTION_COMMITTED xuống journal sau khi Gateway đã chèn: {exc}",
                            in_doubt_action=action,
                            lock_retained=True,
                        )

                else:
                    # FLT-05: Thất bại tất định từ Resolve API
                    err_code = str(outcome.get("error_code", "ERR_RESOLVE_API_FAILED"))
                    err_msg = str(outcome.get("error_message", "Resolve API trả về kết quả rỗng hoặc False"))

                    failed_params = dict(action.action_params)
                    failed_params["action_index"] = action.action_index
                    failed_params["clip_id"] = action.clip_id
                    failed_params["source_file_path"] = action.source_file_path
                    failed_entry = JournalEntry(
                        seq=journal_seq,
                        stage="ACTION_FAILED",
                        action_type=action.action_type,
                        timestamp_iso=post_now,
                        action_params=failed_params,
                        track_type=action.target_track_type,
                        track_index=action.target_track_index,
                        error_code=err_code,
                        error_message=err_msg,
                    )
                    finish_entry = JournalEntry(
                        seq=journal_seq + 1,
                        stage="FINISH",
                        action_type="NO_OP",
                        timestamp_iso=post_now,
                        action_params={"failed_action_index": action.action_index},
                        note="HALTED_ON_ACTION_FAILURE",
                    )

                    try:
                        self.journal_service.append_entry(actual_exec_id, failed_entry)
                        journal_seq += 1
                        self.journal_service.append_entry(actual_exec_id, finish_entry)
                        journal_seq += 1
                    except (OSError, IOError, JournalStorageError) as exc:
                        lock_retained = True
                        self.unresolved_registry.register_unresolved(
                            project_id=target_proj_id,
                            timeline_id=target_tl_id,
                            execution_id=actual_exec_id,
                            target_lock_holder=target_lock_holder,
                            exec_lock_holder=exec_lock_holder,
                            in_doubt_action=action,
                            reason=f"Post-dispatch failure record storage failure: {exc}",
                        )
                        self.receipt_service.persist_target_state(
                            project_id=target_proj_id,
                            timeline_id=target_tl_id,
                            payload={
                                "project_id": target_proj_id,
                                "timeline_id": target_tl_id,
                                "active_execution_id": actual_exec_id,
                                "state": "IN_DOUBT",
                                "in_doubt": True,
                                "in_doubt_action_index": action.action_index,
                                "reason": f"Post-dispatch failure record storage failure: {exc}",
                                "updated_at_iso": post_now,
                            },
                        )
                        return ExecutionEngineResult(
                            status="IN_DOUBT_PERSISTENCE_FAILED",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code="ERR_POST_DISPATCH_STORAGE_FAILED",
                            error_message=f"Thất bại khi ghi ACTION_FAILED/FINISH xuống journal: {exc}",
                            in_doubt_action=action,
                            lock_retained=True,
                        )

                    if committed_count == 0:
                        try:
                            receipt = self.receipt_service.transition_state(
                                actual_exec_id,
                                new_state="ABORTED",
                                updated_at_iso=post_now,
                                last_error_code=err_code,
                                last_error_message=err_msg,
                            )
                        except Exception:
                            pass
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="FAILED_HALTED",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=0,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            error_code=err_code,
                            error_message=err_msg,
                        )
                    else:
                        exec_lock_holder.release()
                        target_lock_holder.release()
                        return ExecutionEngineResult(
                            status="INTERRUPTED_WITH_COMMITTED_ACTIONS",
                            execution_id=actual_exec_id,
                            idempotency_key=plan.idempotency_key,
                            execution_dispatched=True,
                            committed_actions_count=committed_count,
                            total_actions_count=len(plan.planned_actions),
                            receipt=receipt,
                            committed_item_ids=tuple(committed_ids),
                            error_code=err_code,
                            error_message=err_msg,
                        )

            # -----------------------------------------------------------------
            # 8. Hoàn Tất Hoàn Hảo (COMPLETED)
            # -----------------------------------------------------------------
            comp_now = datetime.now(timezone.utc).isoformat()
            finish_entry = JournalEntry(
                seq=journal_seq,
                stage="FINISH",
                action_type="NO_OP",
                timestamp_iso=comp_now,
                action_params={"total_committed": committed_count},
                note="COMPLETED_ALL_ACTIONS",
            )

            try:
                self.journal_service.append_entry(actual_exec_id, finish_entry)
                journal_seq += 1
                receipt = self.receipt_service.transition_state(
                    actual_exec_id,
                    new_state="COMPLETED",
                    updated_at_iso=comp_now,
                    completed_at_iso=comp_now,
                )
            except (OSError, IOError, JournalStorageError, ReceiptStorageError) as exc:
                lock_retained = True
                self.unresolved_registry.register_unresolved(
                    project_id=target_proj_id,
                    timeline_id=target_tl_id,
                    execution_id=actual_exec_id,
                    target_lock_holder=target_lock_holder,
                    exec_lock_holder=exec_lock_holder,
                    in_doubt_action=None,
                    reason=f"Post-dispatch final completion storage failure: {exc}",
                )
                self.receipt_service.persist_target_state(
                    project_id=target_proj_id,
                    timeline_id=target_tl_id,
                    payload={
                        "project_id": target_proj_id,
                        "timeline_id": target_tl_id,
                        "active_execution_id": actual_exec_id,
                        "state": "IN_DOUBT",
                        "in_doubt": True,
                        "in_doubt_action_index": None,
                        "reason": f"Post-dispatch final completion storage failure: {exc}",
                        "updated_at_iso": comp_now,
                    },
                )
                return ExecutionEngineResult(
                    status="IN_DOUBT_PERSISTENCE_FAILED",
                    execution_id=actual_exec_id,
                    idempotency_key=plan.idempotency_key,
                    execution_dispatched=True,
                    committed_actions_count=committed_count,
                    total_actions_count=len(plan.planned_actions),
                    receipt=receipt,
                    committed_item_ids=tuple(committed_ids),
                    error_code="ERR_POST_DISPATCH_STORAGE_FAILED",
                    error_message=f"Thất bại khi ghi FINISH/COMPLETED trạng thái cuối: {exc}",
                    lock_retained=True,
                )

            exec_lock_holder.release()
            target_lock_holder.release()

            return ExecutionEngineResult(
                status="COMPLETED",
                execution_id=actual_exec_id,
                idempotency_key=plan.idempotency_key,
                execution_dispatched=True,
                committed_actions_count=committed_count,
                total_actions_count=len(plan.planned_actions),
                receipt=receipt,
                committed_item_ids=tuple(committed_ids),
            )

        finally:
            if not lock_retained:
                if exec_lock_holder.is_acquired:
                    exec_lock_holder.release()
                if target_lock_holder.is_acquired:
                    target_lock_holder.release()
