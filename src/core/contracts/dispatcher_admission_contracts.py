"""Dispatcher Admission IPC Contracts.

Implements pure immutable DTOs and byte framing helpers for the Dispatcher
Admission IPC boundary as specified in Workorder
G07-07B7-GATE6-PHASE2-DISPATCHER-ADMISSION-CONTRACTS-R1 and
docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md §7.3.

Safety Boundary:
- Pure contract layer: frozen immutable data structures and strict type gates.
- Strictly no socket creation, bind, connect, accept, IPC, daemon/listener,
  network, host provisioning, account/group/DAC changes, or systemd lifecycle.
- Strictly no access to /var/lib/resolveflow, /run/resolveflow, production state,
  or DaVinci Resolve APIs.
- Enforces strict RFC 8785 canonical serialization via canonical_json_bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import struct
from typing import Any, Dict, Mapping, Optional, Type, TypeVar, Union

from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
from src.core.contracts.signed_operator_consent_envelope import SignedOperatorConsentEnvelope
from src.core.contracts.signed_preflight_contracts import SignedPreflightEvidenceEnvelope
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)

SCHEMA_VERSION_V1: int = 1

FRAME_HEADER_SIZE: int = 4
MIN_PAYLOAD_SIZE: int = 1
MAX_PAYLOAD_SIZE: int = 65536  # 64 KiB wire frame limit

RE_CANONICAL_UUID_V4: re.Pattern = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
RE_ERROR_CODE: re.Pattern = re.compile(r"^[A-Z0-9_]{3,64}$")

STATUS_ADMITTED: str = "ADMITTED"
STATUS_REJECTED: str = "REJECTED"
ALLOWED_STATUSES: frozenset[str] = frozenset({STATUS_ADMITTED, STATUS_REJECTED})

DISPATCHER_ADMISSION_REQUEST_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "approval_envelope",
    "operator_consent_envelope",
    "preflight_evidence_envelope",
    "execution_workorder_record",
})

DISPATCHER_ADMISSION_RESPONSE_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "status",
    "error_code",
    "execution_id",
    "preflight_run_id",
    "admitted_at_iso",
})


class DispatcherAdmissionContractError(AcceptanceGateError, ValueError):
    """Raised when Dispatcher Admission IPC DTOs or framing fail validation, schema, or bounds checks."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail or ""


def parse_strict_json(raw_bytes: bytes) -> Dict[str, Any]:
    """Parses UTF-8 JSON bytes rejecting duplicate keys fail-closed.

    Raises:
        DispatcherAdmissionContractError: on invalid UTF-8, malformed JSON, duplicate keys, or non-dict.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Expected bytes, got {type(raw_bytes).__name__}"
        )
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Non-UTF8 frame payload: {exc}"
        ) from exc

    def _pairs_hook(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                raise DispatcherAdmissionContractError(
                    "ERR_DUPLICATE_KEY", f"Duplicate JSON key: {k!r}"
                )
            d[k] = v
        return d

    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Malformed JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Expected JSON object, got {type(data).__name__}"
        )
    return data


@dataclass(frozen=True)
class DispatcherAdmissionRequest:
    """Frozen DTO carrying all pre-requisite gate envelopes for Dispatcher admission.

    Fields:
        schema_version: Strictly int 1 (rejects bool-as-int).
        request_id: Canonical UUIDv4 string.
        approval_envelope: SignedApprovalEnvelope (Gate 2).
        operator_consent_envelope: SignedOperatorConsentEnvelope (Presence).
        preflight_evidence_envelope: SignedPreflightEvidenceEnvelope (Gate 4).
        execution_workorder_record: SignedExecutionWorkorderRecord (Gate 5).
    """

    schema_version: int
    request_id: str
    approval_envelope: SignedApprovalEnvelope
    operator_consent_envelope: SignedOperatorConsentEnvelope
    preflight_evidence_envelope: SignedPreflightEvidenceEnvelope
    execution_workorder_record: SignedExecutionWorkorderRecord

    def __post_init__(self) -> None:
        # 1. Strict schema_version check (reject bool-as-int)
        if type(self.schema_version) is not int:
            raise DispatcherAdmissionContractError(
                "ERR_SCHEMA_VERSION_INVALID",
                f"schema_version must be exact int, got {type(self.schema_version).__name__}",
            )
        if self.schema_version != SCHEMA_VERSION_V1:
            raise DispatcherAdmissionContractError(
                "ERR_SCHEMA_VERSION_INVALID",
                f"schema_version must be exactly {SCHEMA_VERSION_V1}, got {self.schema_version}",
            )

        # 2. Canonical UUIDv4 request_id
        if type(self.request_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.request_id):
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_UUID",
                f"request_id must be canonical lowercase UUIDv4, got {self.request_id!r}",
            )

        # 3. Exact nominal nested envelope type gates (rejecting subclasses)
        if type(self.approval_envelope) is not SignedApprovalEnvelope:
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH",
                f"approval_envelope must be exact type SignedApprovalEnvelope, got {type(self.approval_envelope).__name__}",
            )
        if type(self.operator_consent_envelope) is not SignedOperatorConsentEnvelope:
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH",
                f"operator_consent_envelope must be exact type SignedOperatorConsentEnvelope, got {type(self.operator_consent_envelope).__name__}",
            )
        if type(self.preflight_evidence_envelope) is not SignedPreflightEvidenceEnvelope:
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH",
                f"preflight_evidence_envelope must be exact type SignedPreflightEvidenceEnvelope, got {type(self.preflight_evidence_envelope).__name__}",
            )
        if type(self.execution_workorder_record) is not SignedExecutionWorkorderRecord:
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH",
                f"execution_workorder_record must be exact type SignedExecutionWorkorderRecord, got {type(self.execution_workorder_record).__name__}",
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the request to a fresh plain dictionary with nested envelope dicts."""
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "approval_envelope": self.approval_envelope.to_dict(),
            "operator_consent_envelope": self.operator_consent_envelope.to_dict(),
            "preflight_evidence_envelope": self.preflight_evidence_envelope.to_dict(),
            "execution_workorder_record": self.execution_workorder_record.to_dict(),
        }

    def to_json_bytes(self) -> bytes:
        """Serializes using RFC 8785 canonical JSON bytes."""
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DispatcherAdmissionRequest:
        """Constructs and validates a DispatcherAdmissionRequest from a mapping."""
        if not isinstance(data, Mapping):
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH", f"Expected mapping, got {type(data).__name__}"
            )
        data_keys = set(data.keys())
        missing = DISPATCHER_ADMISSION_REQUEST_FIELDS - data_keys
        if missing:
            raise DispatcherAdmissionContractError(
                "ERR_MISSING_FIELD", f"Missing required fields: {sorted(missing)}"
            )
        extra = data_keys - DISPATCHER_ADMISSION_REQUEST_FIELDS
        if extra:
            raise DispatcherAdmissionContractError(
                "ERR_UNEXPECTED_FIELD", f"Unexpected fields in request: {sorted(extra)}"
            )

        # Parse nested envelopes using their accepted from_dict parsers
        try:
            approval_env = SignedApprovalEnvelope.from_dict(data["approval_envelope"])
        except Exception as exc:
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_NESTED_ENVELOPE", f"Failed to parse approval_envelope: {exc}"
            ) from exc

        try:
            operator_env = SignedOperatorConsentEnvelope.from_dict(data["operator_consent_envelope"])
        except Exception as exc:
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_NESTED_ENVELOPE", f"Failed to parse operator_consent_envelope: {exc}"
            ) from exc

        try:
            preflight_env = SignedPreflightEvidenceEnvelope.from_dict(data["preflight_evidence_envelope"])
        except Exception as exc:
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_NESTED_ENVELOPE", f"Failed to parse preflight_evidence_envelope: {exc}"
            ) from exc

        try:
            workorder_rec = SignedExecutionWorkorderRecord.from_dict(data["execution_workorder_record"])
        except Exception as exc:
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_NESTED_ENVELOPE", f"Failed to parse execution_workorder_record: {exc}"
            ) from exc

        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            approval_envelope=approval_env,
            operator_consent_envelope=operator_env,
            preflight_evidence_envelope=preflight_env,
            execution_workorder_record=workorder_rec,
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> DispatcherAdmissionRequest:
        """Parses strict JSON bytes and constructs a DispatcherAdmissionRequest."""
        parsed = parse_strict_json(raw_bytes)
        return cls.from_dict(parsed)


@dataclass(frozen=True)
class DispatcherAdmissionResponse:
    """Frozen DTO carrying admission decision from Dispatcher daemon.

    Fields:
        schema_version: Strictly int 1 (rejects bool-as-int).
        request_id: Canonical UUIDv4 echoing request.
        status: Exactly "ADMITTED" or "REJECTED".
        error_code: Non-empty grammar-validated string if REJECTED; None if ADMITTED.
        execution_id: Canonical UUIDv4 bound execution run ID.
        preflight_run_id: Canonical UUIDv4 bound Gate 4 run ID confirming consumption.
        admitted_at_iso: Strict UTC ISO-8601 timestamp.
    """

    schema_version: int
    request_id: str
    status: str
    error_code: Optional[str]
    execution_id: str
    preflight_run_id: str
    admitted_at_iso: str

    def __post_init__(self) -> None:
        # 1. Strict schema_version check
        if type(self.schema_version) is not int:
            raise DispatcherAdmissionContractError(
                "ERR_SCHEMA_VERSION_INVALID",
                f"schema_version must be exact int, got {type(self.schema_version).__name__}",
            )
        if self.schema_version != SCHEMA_VERSION_V1:
            raise DispatcherAdmissionContractError(
                "ERR_SCHEMA_VERSION_INVALID",
                f"schema_version must be exactly {SCHEMA_VERSION_V1}, got {self.schema_version}",
            )

        # 2. Canonical UUIDv4 request_id
        if type(self.request_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.request_id):
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_UUID",
                f"request_id must be canonical lowercase UUIDv4, got {self.request_id!r}",
            )

        # 3. Status validation
        if type(self.status) is not str or self.status not in ALLOWED_STATUSES:
            raise DispatcherAdmissionContractError(
                "ERR_STATUS_INVALID",
                f"status must be one of {sorted(ALLOWED_STATUSES)}, got {self.status!r}",
            )

        # 4. Status / error_code consistency
        if self.status == STATUS_ADMITTED:
            if self.error_code is not None:
                raise DispatcherAdmissionContractError(
                    "ERR_STATUS_ERROR_INCONSISTENT",
                    f"error_code must be None when status is {STATUS_ADMITTED!r}, got {self.error_code!r}",
                )
        elif self.status == STATUS_REJECTED:
            if type(self.error_code) is not str or not RE_ERROR_CODE.fullmatch(self.error_code):
                raise DispatcherAdmissionContractError(
                    "ERR_STATUS_ERROR_INCONSISTENT",
                    f"error_code must be non-empty matching grammar {RE_ERROR_CODE.pattern} when status is {STATUS_REJECTED!r}, got {self.error_code!r}",
                )

        # 5. Canonical UUIDv4 execution_id & preflight_run_id
        if type(self.execution_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.execution_id):
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_UUID",
                f"execution_id must be canonical lowercase UUIDv4, got {self.execution_id!r}",
            )
        if type(self.preflight_run_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.preflight_run_id):
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_UUID",
                f"preflight_run_id must be canonical lowercase UUIDv4, got {self.preflight_run_id!r}",
            )

        # 6. Timestamp validation
        if type(self.admitted_at_iso) is not str:
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH",
                f"admitted_at_iso must be str, got {type(self.admitted_at_iso).__name__}",
            )
        try:
            parse_iso_utc_timestamp(self.admitted_at_iso, "admitted_at_iso")
        except Exception as exc:
            raise DispatcherAdmissionContractError(
                "ERR_INVALID_TIMESTAMP",
                f"admitted_at_iso '{self.admitted_at_iso}' is not a valid strict UTC ISO-8601 timestamp: {exc}",
            ) from exc

    def to_dict(self) -> Dict[str, Any]:
        """Serializes response to a plain dictionary."""
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "status": self.status,
            "error_code": self.error_code,
            "execution_id": self.execution_id,
            "preflight_run_id": self.preflight_run_id,
            "admitted_at_iso": self.admitted_at_iso,
        }

    def to_json_bytes(self) -> bytes:
        """Serializes using RFC 8785 canonical JSON bytes."""
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DispatcherAdmissionResponse:
        """Constructs and validates a DispatcherAdmissionResponse from a mapping."""
        if not isinstance(data, Mapping):
            raise DispatcherAdmissionContractError(
                "ERR_TYPE_MISMATCH", f"Expected mapping, got {type(data).__name__}"
            )
        data_keys = set(data.keys())
        missing = DISPATCHER_ADMISSION_RESPONSE_FIELDS - data_keys
        if missing:
            raise DispatcherAdmissionContractError(
                "ERR_MISSING_FIELD", f"Missing required fields: {sorted(missing)}"
            )
        extra = data_keys - DISPATCHER_ADMISSION_RESPONSE_FIELDS
        if extra:
            raise DispatcherAdmissionContractError(
                "ERR_UNEXPECTED_FIELD", f"Unexpected fields in response: {sorted(extra)}"
            )

        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            status=data["status"],
            error_code=data["error_code"],
            execution_id=data["execution_id"],
            preflight_run_id=data["preflight_run_id"],
            admitted_at_iso=data["admitted_at_iso"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> DispatcherAdmissionResponse:
        """Parses strict JSON bytes and constructs a DispatcherAdmissionResponse."""
        parsed = parse_strict_json(raw_bytes)
        return cls.from_dict(parsed)


T = TypeVar("T", DispatcherAdmissionRequest, DispatcherAdmissionResponse)


def encode_framed_bytes(payload_bytes: bytes) -> bytes:
    """Encodes payload bytes with a 4-byte big-endian unsigned length prefix.

    Raises:
        DispatcherAdmissionContractError: if length is 0 (ERR_EMPTY_FRAME)
            or exceeds 65536 bytes (ERR_FRAME_TOO_LARGE).
    """
    if not isinstance(payload_bytes, (bytes, bytearray)):
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Payload must be bytes, got {type(payload_bytes).__name__}"
        )
    n = len(payload_bytes)
    if n < MIN_PAYLOAD_SIZE:
        raise DispatcherAdmissionContractError(
            "ERR_EMPTY_FRAME", "Frame payload length cannot be zero"
        )
    if n > MAX_PAYLOAD_SIZE:
        raise DispatcherAdmissionContractError(
            "ERR_FRAME_TOO_LARGE",
            f"Frame payload length {n} exceeds maximum allowed {MAX_PAYLOAD_SIZE} bytes",
        )
    return struct.pack(">I", n) + bytes(payload_bytes)


def encode_admission_frame(
    msg: Union[DispatcherAdmissionRequest, DispatcherAdmissionResponse]
) -> bytes:
    """Encodes a DispatcherAdmissionRequest or DispatcherAdmissionResponse to a framed byte sequence."""
    if type(msg) not in (DispatcherAdmissionRequest, DispatcherAdmissionResponse):
        raise DispatcherAdmissionContractError(
            "ERR_TYPE_MISMATCH",
            f"Expected exact type DispatcherAdmissionRequest or DispatcherAdmissionResponse, got {type(msg).__name__}",
        )
    return encode_framed_bytes(msg.to_json_bytes())


def decode_admission_frame(
    frame_bytes: bytes,
    expected_type: Type[T],
) -> T:
    """Decodes exactly one complete framed byte sequence into expected DTO type T.

    Raises:
        DispatcherAdmissionContractError: on short header, length violations,
            truncated payload, trailing bytes, or JSON/contract errors.
    """
    if not isinstance(frame_bytes, (bytes, bytearray)):
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME", f"Frame must be bytes, got {type(frame_bytes).__name__}"
        )
    total_len = len(frame_bytes)
    if total_len < FRAME_HEADER_SIZE:
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME",
            f"Frame length {total_len} is shorter than 4-byte header",
        )
    (payload_len,) = struct.unpack(">I", frame_bytes[:FRAME_HEADER_SIZE])
    if payload_len < MIN_PAYLOAD_SIZE:
        raise DispatcherAdmissionContractError(
            "ERR_EMPTY_FRAME", "Frame length header specifies 0 bytes"
        )
    if payload_len > MAX_PAYLOAD_SIZE:
        raise DispatcherAdmissionContractError(
            "ERR_FRAME_TOO_LARGE",
            f"Frame header specifies {payload_len} bytes (max {MAX_PAYLOAD_SIZE})",
        )
    actual_payload_len = total_len - FRAME_HEADER_SIZE
    if actual_payload_len < payload_len:
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME",
            f"Truncated frame payload: expected {payload_len} bytes, got {actual_payload_len}",
        )
    if actual_payload_len > payload_len:
        raise DispatcherAdmissionContractError(
            "ERR_INVALID_FRAME",
            f"Extra trailing bytes after frame payload: expected {payload_len} bytes, frame has {actual_payload_len}",
        )
    payload_bytes = bytes(frame_bytes[FRAME_HEADER_SIZE:])
    if expected_type not in (DispatcherAdmissionRequest, DispatcherAdmissionResponse):
        raise DispatcherAdmissionContractError(
            "ERR_TYPE_MISMATCH",
            f"expected_type must be DispatcherAdmissionRequest or DispatcherAdmissionResponse, got {expected_type}",
        )
    return expected_type.from_json_bytes(payload_bytes)
