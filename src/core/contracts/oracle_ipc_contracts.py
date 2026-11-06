"""Oracle and Signer IPC Protocol Contracts & Wire Framing.

Implements AF_UNIX length-prefixed framing and immutable request/response DTOs
per docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md §§3.2-3.3.
"""

from __future__ import annotations

import datetime
import json
import re
import socket
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_json_bytes,
)

# ------------------------------------------------------------------------------
# Protocol Framing Constants & Limits
# ------------------------------------------------------------------------------
FRAME_HEADER_SIZE: int = 4
MIN_FRAME_SIZE: int = 1
MAX_FRAME_SIZE: int = 65536  # 64 KiB
DEFAULT_SOCKET_TIMEOUT: float = 5.0

# ------------------------------------------------------------------------------
# Error Codes
# ------------------------------------------------------------------------------
ERR_FRAME_TOO_LARGE: str = "ERR_FRAME_TOO_LARGE"
ERR_EMPTY_FRAME: str = "ERR_EMPTY_FRAME"
ERR_INVALID_FRAME: str = "ERR_INVALID_FRAME"
ERR_SOCKET_TIMEOUT: str = "ERR_SOCKET_TIMEOUT"
ERR_UNEXPECTED_FIELD: str = "ERR_UNEXPECTED_FIELD"
ERR_SCHEMA_VERSION_INVALID: str = "ERR_SCHEMA_VERSION_INVALID"
ERR_INVALID_NONCE: str = "ERR_INVALID_NONCE"
ERR_INVALID_UUID: str = "ERR_INVALID_UUID"
ERR_INVALID_TIMESTAMP: str = "ERR_INVALID_TIMESTAMP"
ERR_INVALID_DIGEST: str = "ERR_INVALID_DIGEST"
ERR_INVALID_SIGNATURE: str = "ERR_INVALID_SIGNATURE"
ERR_PEER_UNAUTHORIZED: str = "ERR_PEER_UNAUTHORIZED"
ERR_DAEMON_ALREADY_RUNNING: str = "ERR_DAEMON_ALREADY_RUNNING"
ERR_CONCURRENCY_LIMIT_EXCEEDED: str = "ERR_CONCURRENCY_LIMIT_EXCEEDED"
ERR_AUTHORIZATION_REVOKED: str = "ERR_AUTHORIZATION_REVOKED"
ERR_TRIAL_KEY_REVOKED: str = "ERR_TRIAL_KEY_REVOKED"
ERR_DIRECTOR_KEY_REVOKED: str = "ERR_DIRECTOR_KEY_REVOKED"
ERR_OPERATOR_KEY_REVOKED: str = "ERR_OPERATOR_KEY_REVOKED"
ERR_REVOCATION_VERIFICATION_FAILED: str = "ERR_REVOCATION_VERIFICATION_FAILED"
ERR_LEDGER_POISONED: str = "ERR_LEDGER_POISONED"
ERR_TERMINAL_STATE_INVALID: str = "ERR_TERMINAL_STATE_INVALID"
ERR_TERMINAL_STATE_CONFLICT: str = "ERR_TERMINAL_STATE_CONFLICT"
ERR_EXECUTION_NOT_FOUND: str = "ERR_EXECUTION_NOT_FOUND"
ERR_EXECUTION_ALREADY_TERMINAL: str = "ERR_EXECUTION_ALREADY_TERMINAL"
ERR_IDENTITY_BINDING_MISMATCH: str = "ERR_IDENTITY_BINDING_MISMATCH"

# ------------------------------------------------------------------------------
# Validation Regexes
# ------------------------------------------------------------------------------
_HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HEX_128_PATTERN = re.compile(r"^[0-9a-f]{128}$")
_ISO_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class OracleProtocolError(Exception):
    """Base exception for Oracle IPC framing, validation, and protocol errors."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail or ""


# ------------------------------------------------------------------------------
# Framing Helper Functions
# ------------------------------------------------------------------------------
def encode_framed_message(payload_bytes: bytes) -> bytes:
    """Encodes payload bytes with a 4-byte big-endian unsigned length prefix.

    Raises:
        OracleProtocolError: if length is 0 (ERR_EMPTY_FRAME) or > 65536 (ERR_FRAME_TOO_LARGE).
    """
    if not isinstance(payload_bytes, (bytes, bytearray)):
        raise OracleProtocolError(ERR_INVALID_FRAME, "Payload must be bytes")
    n = len(payload_bytes)
    if n < MIN_FRAME_SIZE:
        raise OracleProtocolError(ERR_EMPTY_FRAME, "Frame payload length cannot be zero")
    if n > MAX_FRAME_SIZE:
        raise OracleProtocolError(
            ERR_FRAME_TOO_LARGE,
            f"Frame payload length {n} exceeds maximum allowed {MAX_FRAME_SIZE} bytes",
        )
    return struct.pack(">I", n) + bytes(payload_bytes)


def read_exact_bytes(sock: socket.socket, num_bytes: int) -> bytes:
    """Reads exactly num_bytes from socket, raising on premature EOF or timeout."""
    buf = bytearray()
    while len(buf) < num_bytes:
        try:
            chunk = sock.recv(num_bytes - len(buf))
        except socket.timeout as exc:
            raise OracleProtocolError(ERR_SOCKET_TIMEOUT, "Socket recv timed out") from exc
        except OSError as exc:
            raise OracleProtocolError(ERR_INVALID_FRAME, f"Socket read error: {exc}") from exc
        if not chunk:
            if len(buf) == 0:
                raise EOFError("Peer closed connection")
            raise OracleProtocolError(
                ERR_INVALID_FRAME,
                f"Premature EOF: expected {num_bytes} bytes, received {len(buf)} bytes",
            )
        buf.extend(chunk)
    return bytes(buf)


def decode_framed_message(sock: socket.socket) -> bytes:
    """Reads and decodes a single length-prefixed frame from a stream socket.

    Raises:
        OracleProtocolError: on timeout, frame too large, empty frame, or framing error.
        EOFError: on clean peer close prior to frame header.
    """
    header_bytes = read_exact_bytes(sock, FRAME_HEADER_SIZE)
    (frame_len,) = struct.unpack(">I", header_bytes)
    if frame_len < MIN_FRAME_SIZE:
        raise OracleProtocolError(ERR_EMPTY_FRAME, "Frame length header specifies 0 bytes")
    if frame_len > MAX_FRAME_SIZE:
        raise OracleProtocolError(
            ERR_FRAME_TOO_LARGE,
            f"Frame header specifies {frame_len} bytes (max {MAX_FRAME_SIZE})",
        )
    return read_exact_bytes(sock, frame_len)


# ------------------------------------------------------------------------------
# Field Validation Helpers
# ------------------------------------------------------------------------------
def _validate_hex_64(val: str, field_name: str) -> None:
    if not isinstance(val, str) or not _HEX_64_PATTERN.match(val):
        raise OracleProtocolError(
            ERR_INVALID_DIGEST if "digest" in field_name or "revision" in field_name else ERR_INVALID_NONCE,
            f"Field '{field_name}' must be a 64-character lowercase hex string, got {val!r}",
        )


def _validate_uuid_v4(val: str, field_name: str) -> None:
    if not isinstance(val, str):
        raise OracleProtocolError(ERR_INVALID_UUID, f"Field '{field_name}' must be a UUID string")
    try:
        u = uuid.UUID(val, version=4)
        if str(u) != val.lower():
            raise ValueError()
    except Exception:
        raise OracleProtocolError(
            ERR_INVALID_UUID,
            f"Field '{field_name}' must be a valid lowercase UUIDv4 string, got {val!r}",
        )


def _validate_iso_utc(val: str, field_name: str) -> datetime.datetime:
    if not isinstance(val, str) or not _ISO_Z_PATTERN.match(val):
        raise OracleProtocolError(
            ERR_INVALID_TIMESTAMP,
            f"Field '{field_name}' must be an ISO-8601 UTC timestamp ending in 'Z', got {val!r}",
        )
    try:
        dt = datetime.datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Missing tzinfo")
        return dt
    except Exception as exc:
        raise OracleProtocolError(
            ERR_INVALID_TIMESTAMP,
            f"Field '{field_name}' is not a valid ISO timestamp: {exc}",
        ) from exc


def parse_strict_json(raw_bytes: bytes) -> Dict[str, Any]:
    """Parses JSON bytes rejecting duplicate keys."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleProtocolError(ERR_INVALID_FRAME, f"Non-UTF8 frame payload: {exc}") from exc

    def _pairs_hook(pairs: list[Tuple[str, Any]]) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        for k, v in pairs:
            if k in d:
                raise OracleProtocolError(ERR_UNEXPECTED_FIELD, f"Duplicate JSON key: {k!r}")
            d[k] = v
        return d

    try:
        data = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise OracleProtocolError(ERR_INVALID_FRAME, f"Malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OracleProtocolError(ERR_INVALID_FRAME, f"Expected JSON object, got {type(data).__name__}")
    return data


_parse_strict_json = parse_strict_json


# ------------------------------------------------------------------------------
# Oracle Request Contract (Schema Version 2)
# ------------------------------------------------------------------------------
ORACLE_REQUEST_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "request_nonce",
    "requested_at_iso",
    "signed_authorization",
    "action_payload_digest_sha256",
    "target_project_uid",
    "target_timeline_uid",
    "proposal_revision",
})


@dataclass(frozen=True)
class OracleVerificationRequest:
    """Dispatcher-to-Signer Oracle verification request DTO (Schema Version 2)."""

    schema_version: int
    request_id: str
    request_nonce: str
    requested_at_iso: str
    signed_authorization: TrialExecutionAuthorization
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    proposal_revision: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 2:
            raise OracleProtocolError(
                ERR_SCHEMA_VERSION_INVALID,
                f"schema_version must be exact int 2, got {self.schema_version!r}",
            )
        _validate_uuid_v4(self.request_id, "request_id")
        _validate_hex_64(self.request_nonce, "request_nonce")
        _validate_iso_utc(self.requested_at_iso, "requested_at_iso")
        if not isinstance(self.signed_authorization, TrialExecutionAuthorization):
            raise OracleProtocolError(
                "ERR_INVALID_AUTHORIZATION_DTO",
                f"signed_authorization must be TrialExecutionAuthorization, got {type(self.signed_authorization).__name__}",
            )
        _validate_hex_64(self.action_payload_digest_sha256, "action_payload_digest_sha256")
        if not isinstance(self.target_project_uid, str) or not self.target_project_uid:
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_project_uid must be a non-empty string")
        if not isinstance(self.target_timeline_uid, str) or not self.target_timeline_uid:
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_timeline_uid must be a non-empty string")
        _validate_hex_64(self.proposal_revision, "proposal_revision")

    def to_dict(self) -> Dict[str, Any]:
        """Serializes request to strict dictionary."""
        # Convert TrialExecutionAuthorization to dict
        auth_dict = {
            "authorization_id": self.signed_authorization.authorization_id,
            "key_id": self.signed_authorization.key_id,
            "operator_gesture_digest_sha256": self.signed_authorization.operator_gesture_digest_sha256,
            "approval_record_id": self.signed_authorization.approval_record_id,
            "approval_record_digest_sha256": self.signed_authorization.approval_record_digest_sha256,
            "proposal_id": self.signed_authorization.proposal_id,
            "proposal_revision": self.signed_authorization.proposal_revision,
            "proposal_content_digest_sha256": self.signed_authorization.proposal_content_digest_sha256,
            "plan_idempotency_key": self.signed_authorization.plan_idempotency_key,
            "plan_digest_sha256": self.signed_authorization.plan_digest_sha256,
            "action_payload_digest_sha256": self.signed_authorization.action_payload_digest_sha256,
            "target_project_uid": self.signed_authorization.target_project_uid,
            "target_project_name": self.signed_authorization.target_project_name,
            "target_timeline_uid": self.signed_authorization.target_timeline_uid,
            "target_timeline_name": self.signed_authorization.target_timeline_name,
            "target_timeline_fps_numerator": self.signed_authorization.target_timeline_fps_numerator,
            "target_timeline_fps_denominator": self.signed_authorization.target_timeline_fps_denominator,
            "observed_timeline_start_frame": self.signed_authorization.observed_timeline_start_frame,
            "authorized_record_frame": self.signed_authorization.authorized_record_frame,
            "source_file_path": self.signed_authorization.source_file_path,
            "source_file_sha256": self.signed_authorization.source_file_sha256,
            "authorized_action_type": self.signed_authorization.authorized_action_type,
            "authorized_track_type": self.signed_authorization.authorized_track_type,
            "authorized_track_index": self.signed_authorization.authorized_track_index,
            "authorized_timeline_frame_start": self.signed_authorization.authorized_timeline_frame_start,
            "authorized_timeline_frame_duration": self.signed_authorization.authorized_timeline_frame_duration,
            "authorized_source_frame_start": self.signed_authorization.authorized_source_frame_start,
            "authorized_source_frame_duration": self.signed_authorization.authorized_source_frame_duration,
            "execution_id": self.signed_authorization.execution_id,
            "issued_at_iso": self.signed_authorization.issued_at_iso,
            "valid_until_iso": self.signed_authorization.valid_until_iso,
            "token_nonce": self.signed_authorization.token_nonce,
            "authorization_hmac": self.signed_authorization.authorization_hmac,
        }
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "proposal_revision": self.proposal_revision,
            "request_id": self.request_id,
            "request_nonce": self.request_nonce,
            "requested_at_iso": self.requested_at_iso,
            "schema_version": self.schema_version,
            "signed_authorization": auth_dict,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleVerificationRequest:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Request must be a dictionary")
        extra_keys = set(data.keys()) - ORACLE_REQUEST_FIELDS
        if extra_keys:
            raise OracleProtocolError(
                ERR_UNEXPECTED_FIELD,
                f"Unexpected fields in Oracle request: {sorted(extra_keys)}",
            )
        missing_keys = ORACLE_REQUEST_FIELDS - set(data.keys())
        if missing_keys:
            raise OracleProtocolError(
                ERR_INVALID_FRAME,
                f"Missing required fields in Oracle request: {sorted(missing_keys)}",
            )

        auth_raw = data["signed_authorization"]
        if not isinstance(auth_raw, dict):
            raise OracleProtocolError("ERR_INVALID_AUTHORIZATION", "signed_authorization must be a dict")

        try:
            auth_dto = TrialExecutionAuthorization(**auth_raw)
        except Exception as exc:
            raise OracleProtocolError("ERR_INVALID_AUTHORIZATION", f"Invalid authorization claims: {exc}") from exc

        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            request_nonce=data["request_nonce"],
            requested_at_iso=data["requested_at_iso"],
            signed_authorization=auth_dto,
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            proposal_revision=data["proposal_revision"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> OracleVerificationRequest:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


# ------------------------------------------------------------------------------
# Oracle Response Contract (Schema Version 2)
# ------------------------------------------------------------------------------
ORACLE_RESPONSE_ALL_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "response_id",
    "request_id",
    "request_nonce",
    "authorization_digest_sha256",
    "action_payload_digest_sha256",
    "target_project_uid",
    "target_timeline_uid",
    "proposal_revision",
    "verification_result",
    "is_valid",
    "reason_code",
    "verified_at_iso",
    "valid_until_iso",
    "oracle_signature",
})

ORACLE_RESPONSE_CLAIM_FIELDS: frozenset[str] = frozenset(
    ORACLE_RESPONSE_ALL_FIELDS - {"oracle_signature"}
)


@dataclass(frozen=True)
class OracleVerificationResponse:
    """Signer Oracle-to-Dispatcher response DTO (Schema Version 2)."""

    schema_version: int
    response_id: str
    request_id: str
    request_nonce: str
    authorization_digest_sha256: str
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    proposal_revision: str
    verification_result: str
    is_valid: bool
    reason_code: str
    verified_at_iso: str
    valid_until_iso: str
    oracle_signature: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 2:
            raise OracleProtocolError(
                ERR_SCHEMA_VERSION_INVALID,
                f"schema_version must be exact int 2, got {self.schema_version!r}",
            )
        _validate_uuid_v4(self.response_id, "response_id")
        _validate_uuid_v4(self.request_id, "request_id")
        _validate_hex_64(self.request_nonce, "request_nonce")
        _validate_hex_64(self.authorization_digest_sha256, "authorization_digest_sha256")
        _validate_hex_64(self.action_payload_digest_sha256, "action_payload_digest_sha256")
        if not isinstance(self.target_project_uid, str) or not self.target_project_uid:
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_project_uid must be a non-empty string")
        if not isinstance(self.target_timeline_uid, str) or not self.target_timeline_uid:
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_timeline_uid must be a non-empty string")
        _validate_hex_64(self.proposal_revision, "proposal_revision")
        if self.verification_result not in ("VERIFIED_VALID", "VERIFICATION_FAILED"):
            raise OracleProtocolError(
                "ERR_INVALID_RESULT",
                f"verification_result must be 'VERIFIED_VALID' or 'VERIFICATION_FAILED', got {self.verification_result!r}",
            )
        if type(self.is_valid) is not bool:
            raise OracleProtocolError("ERR_INVALID_BOOL", f"is_valid must be bool, got {type(self.is_valid).__name__}")
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise OracleProtocolError("ERR_INVALID_REASON", "reason_code must be a non-empty string")
        t_ver = _validate_iso_utc(self.verified_at_iso, "verified_at_iso")
        t_val = _validate_iso_utc(self.valid_until_iso, "valid_until_iso")
        if t_val <= t_ver:
            raise OracleProtocolError(
                ERR_INVALID_TIMESTAMP,
                f"valid_until_iso ({self.valid_until_iso}) must be strictly after verified_at_iso ({self.verified_at_iso})",
            )
        if not isinstance(self.oracle_signature, str) or not _HEX_128_PATTERN.match(self.oracle_signature):
            raise OracleProtocolError(
                ERR_INVALID_SIGNATURE,
                f"oracle_signature must be a 128-character lowercase hex string, got {self.oracle_signature!r}",
            )

    def canonical_claims_dict(self) -> Dict[str, Any]:
        """Returns the strictly ordered dictionary of 14 non-signature claims."""
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "authorization_digest_sha256": self.authorization_digest_sha256,
            "is_valid": self.is_valid,
            "proposal_revision": self.proposal_revision,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "request_nonce": self.request_nonce,
            "response_id": self.response_id,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "valid_until_iso": self.valid_until_iso,
            "verification_result": self.verification_result,
            "verified_at_iso": self.verified_at_iso,
        }

    def canonical_claims_bytes(self) -> bytes:
        """Returns RFC 8785 canonical JSON bytes of the 14 claims covered by the Ed25519 signature."""
        return canonical_json_bytes(self.canonical_claims_dict())

    def to_dict(self) -> Dict[str, Any]:
        d = self.canonical_claims_dict()
        d["oracle_signature"] = self.oracle_signature
        return d

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleVerificationResponse:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Response must be a dictionary")
        extra_keys = set(data.keys()) - ORACLE_RESPONSE_ALL_FIELDS
        if extra_keys:
            raise OracleProtocolError(
                ERR_UNEXPECTED_FIELD,
                f"Unexpected fields in Oracle response: {sorted(extra_keys)}",
            )
        missing_keys = ORACLE_RESPONSE_ALL_FIELDS - set(data.keys())
        if missing_keys:
            raise OracleProtocolError(
                ERR_INVALID_FRAME,
                f"Missing required fields in Oracle response: {sorted(missing_keys)}",
            )

        return cls(
            schema_version=data["schema_version"],
            response_id=data["response_id"],
            request_id=data["request_id"],
            request_nonce=data["request_nonce"],
            authorization_digest_sha256=data["authorization_digest_sha256"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            proposal_revision=data["proposal_revision"],
            verification_result=data["verification_result"],
            is_valid=data["is_valid"],
            reason_code=data["reason_code"],
            verified_at_iso=data["verified_at_iso"],
            valid_until_iso=data["valid_until_iso"],
            oracle_signature=data["oracle_signature"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> OracleVerificationResponse:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


# ------------------------------------------------------------------------------
# Oracle Terminal Notification Request Contract (Schema Version 2)
# ------------------------------------------------------------------------------
ORACLE_TERMINAL_REQUEST_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "request_nonce",
    "execution_id",
    "authorization_id",
    "terminal_state",
    "proposal_revision",
    "target_project_uid",
    "target_timeline_uid",
    "action_payload_digest_sha256",
    "requested_at_iso",
})

VALID_TERMINAL_STATES: frozenset[str] = frozenset({
    "COMPLETED",
    "ABORTED",
    "IN_DOUBT",
})

VALID_TERMINAL_RESPONSE_STATUSES: frozenset[str] = frozenset({
    "ACCEPTED",
    "TERMINAL_ALREADY_RECORDED",
    "REJECTED",
    "FAILED",
})


@dataclass(frozen=True)
class OracleTerminalNotificationRequest:
    """Dispatcher-to-Signer Oracle terminal notification request DTO (Schema Version 2)."""

    schema_version: int
    request_id: str
    request_nonce: str
    execution_id: str
    authorization_id: str
    terminal_state: str
    proposal_revision: str
    target_project_uid: str
    target_timeline_uid: str
    action_payload_digest_sha256: str
    requested_at_iso: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 2:
            raise OracleProtocolError(
                ERR_SCHEMA_VERSION_INVALID,
                f"schema_version must be exact int 2, got {self.schema_version!r}",
            )
        _validate_uuid_v4(self.request_id, "request_id")
        _validate_hex_64(self.request_nonce, "request_nonce")
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise OracleProtocolError("ERR_INVALID_EXECUTION_ID", "execution_id must be a non-empty string")
        if not isinstance(self.authorization_id, str) or not self.authorization_id.strip():
            raise OracleProtocolError("ERR_INVALID_AUTHORIZATION_ID", "authorization_id must be a non-empty string")
        if not isinstance(self.terminal_state, str) or self.terminal_state not in VALID_TERMINAL_STATES:
            raise OracleProtocolError(
                ERR_TERMINAL_STATE_INVALID,
                f"terminal_state must be one of {sorted(VALID_TERMINAL_STATES)}, got {self.terminal_state!r}",
            )
        _validate_hex_64(self.proposal_revision, "proposal_revision")
        if not isinstance(self.target_project_uid, str) or not self.target_project_uid.strip():
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_project_uid must be a non-empty string")
        if not isinstance(self.target_timeline_uid, str) or not self.target_timeline_uid.strip():
            raise OracleProtocolError("ERR_INVALID_TARGET", "target_timeline_uid must be a non-empty string")
        _validate_hex_64(self.action_payload_digest_sha256, "action_payload_digest_sha256")
        _validate_iso_utc(self.requested_at_iso, "requested_at_iso")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "authorization_id": self.authorization_id,
            "execution_id": self.execution_id,
            "proposal_revision": self.proposal_revision,
            "request_id": self.request_id,
            "request_nonce": self.request_nonce,
            "requested_at_iso": self.requested_at_iso,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "terminal_state": self.terminal_state,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleTerminalNotificationRequest:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Request must be a dictionary")
        extra_keys = set(data.keys()) - ORACLE_TERMINAL_REQUEST_FIELDS
        if extra_keys:
            raise OracleProtocolError(
                ERR_UNEXPECTED_FIELD,
                f"Unexpected fields in Oracle terminal request: {sorted(extra_keys)}",
            )
        missing_keys = ORACLE_TERMINAL_REQUEST_FIELDS - set(data.keys())
        if missing_keys:
            raise OracleProtocolError(
                ERR_INVALID_FRAME,
                f"Missing required fields in Oracle terminal request: {sorted(missing_keys)}",
            )
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            request_nonce=data["request_nonce"],
            execution_id=data["execution_id"],
            authorization_id=data["authorization_id"],
            terminal_state=data["terminal_state"],
            proposal_revision=data["proposal_revision"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            requested_at_iso=data["requested_at_iso"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> OracleTerminalNotificationRequest:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


# ------------------------------------------------------------------------------
# Oracle Terminal Notification Response Contract (Schema Version 2)
# ------------------------------------------------------------------------------
ORACLE_TERMINAL_RESPONSE_ALL_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "response_id",
    "request_id",
    "request_nonce",
    "execution_id",
    "terminal_state",
    "status",
    "reason_code",
    "recorded_at_iso",
    "oracle_signature",
})

ORACLE_TERMINAL_RESPONSE_CLAIM_FIELDS: frozenset[str] = frozenset(
    ORACLE_TERMINAL_RESPONSE_ALL_FIELDS - {"oracle_signature"}
)


@dataclass(frozen=True)
class OracleTerminalNotificationResponse:
    """Signer Oracle-to-Dispatcher terminal notification response DTO (Schema Version 2)."""

    schema_version: int
    response_id: str
    request_id: str
    request_nonce: str
    execution_id: str
    terminal_state: str
    status: str
    reason_code: str
    recorded_at_iso: str
    oracle_signature: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 2:
            raise OracleProtocolError(
                ERR_SCHEMA_VERSION_INVALID,
                f"schema_version must be exact int 2, got {self.schema_version!r}",
            )
        _validate_uuid_v4(self.response_id, "response_id")
        _validate_uuid_v4(self.request_id, "request_id")
        _validate_hex_64(self.request_nonce, "request_nonce")
        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise OracleProtocolError("ERR_INVALID_EXECUTION_ID", "execution_id must be a non-empty string")
        if type(self.terminal_state) is not str or self.terminal_state not in VALID_TERMINAL_STATES:
            raise OracleProtocolError(
                ERR_TERMINAL_STATE_INVALID,
                f"terminal_state must be one of {sorted(VALID_TERMINAL_STATES)}, got {self.terminal_state!r}",
            )
        if type(self.status) is not str or self.status not in VALID_TERMINAL_RESPONSE_STATUSES:
            raise OracleProtocolError(
                "ERR_INVALID_STATUS",
                f"status must be one of {sorted(VALID_TERMINAL_RESPONSE_STATUSES)}, got {self.status!r}",
            )
        if type(self.reason_code) is not str or not self.reason_code.strip():
            raise OracleProtocolError("ERR_INVALID_REASON", "reason_code must be a non-empty string")

        if self.status in ("ACCEPTED", "TERMINAL_ALREADY_RECORDED"):
            if self.reason_code != "OK":
                raise OracleProtocolError(
                    "ERR_INVALID_REASON",
                    f"reason_code must be 'OK' when status is {self.status!r}, got {self.reason_code!r}",
                )
        elif self.status in ("REJECTED", "FAILED"):
            if self.reason_code == "OK" or not self.reason_code.startswith("ERR_"):
                raise OracleProtocolError(
                    "ERR_INVALID_REASON",
                    f"reason_code must start with 'ERR_' when status is {self.status!r}, got {self.reason_code!r}",
                )
        _validate_iso_utc(self.recorded_at_iso, "recorded_at_iso")
        if not isinstance(self.oracle_signature, str) or not _HEX_128_PATTERN.match(self.oracle_signature):
            raise OracleProtocolError(
                ERR_INVALID_SIGNATURE,
                f"oracle_signature must be a 128-character lowercase hex string, got {self.oracle_signature!r}",
            )

    def canonical_claims_dict(self) -> Dict[str, Any]:
        """Returns the strictly ordered dictionary of 9 non-signature claims."""
        return {
            "execution_id": self.execution_id,
            "reason_code": self.reason_code,
            "recorded_at_iso": self.recorded_at_iso,
            "request_id": self.request_id,
            "request_nonce": self.request_nonce,
            "response_id": self.response_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "terminal_state": self.terminal_state,
        }

    def canonical_claims_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_claims_dict())

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.canonical_claims_dict())
        d["oracle_signature"] = self.oracle_signature
        return d

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OracleTerminalNotificationResponse:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Response must be a dictionary")
        extra_keys = set(data.keys()) - ORACLE_TERMINAL_RESPONSE_ALL_FIELDS
        if extra_keys:
            raise OracleProtocolError(
                ERR_UNEXPECTED_FIELD,
                f"Unexpected fields in Oracle terminal response: {sorted(extra_keys)}",
            )
        missing_keys = ORACLE_TERMINAL_RESPONSE_ALL_FIELDS - set(data.keys())
        if missing_keys:
            raise OracleProtocolError(
                ERR_INVALID_FRAME,
                f"Missing required fields in Oracle terminal response: {sorted(missing_keys)}",
            )
        return cls(
            schema_version=data["schema_version"],
            response_id=data["response_id"],
            request_id=data["request_id"],
            request_nonce=data["request_nonce"],
            execution_id=data["execution_id"],
            terminal_state=data["terminal_state"],
            status=data["status"],
            reason_code=data["reason_code"],
            recorded_at_iso=data["recorded_at_iso"],
            oracle_signature=data["oracle_signature"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> OracleTerminalNotificationResponse:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


def decode_oracle_request(raw_bytes: bytes) -> Union[OracleVerificationRequest, OracleTerminalNotificationRequest]:
    """Strictly parses and discriminates an incoming framed Oracle socket request."""
    data = _parse_strict_json(raw_bytes)
    if "terminal_state" in data:
        return OracleTerminalNotificationRequest.from_dict(data)
    elif "signed_authorization" in data:
        return OracleVerificationRequest.from_dict(data)
    else:
        raise OracleProtocolError(
            ERR_UNEXPECTED_FIELD,
            "Request JSON must contain either 'signed_authorization' for verification or 'terminal_state' for terminal notification",
        )


# ------------------------------------------------------------------------------
# Signer Public Endpoint Contracts
# ------------------------------------------------------------------------------
PING_REQUEST_ALLOWED_FIELDS: frozenset[str] = frozenset({"schema_version", "ping_nonce"})


@dataclass(frozen=True)
class SignerPublicPingRequest:
    """Health ping request DTO for public client endpoint."""
    schema_version: int = 2
    ping_nonce: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise OracleProtocolError(ERR_SCHEMA_VERSION_INVALID, "schema_version must be 2")
        if not self.ping_nonce:
            raise OracleProtocolError(ERR_INVALID_NONCE, "ping_nonce required")

    def to_dict(self) -> Dict[str, Any]:
        return {"ping_nonce": self.ping_nonce, "schema_version": self.schema_version}

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicPingRequest:
        if set(data.keys()) != {"schema_version", "ping_nonce"}:
            raise OracleProtocolError(ERR_UNEXPECTED_FIELD, "Invalid fields for PingRequest")
        return cls(schema_version=data["schema_version"], ping_nonce=data["ping_nonce"])

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicPingRequest:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


@dataclass(frozen=True)
class SignerPublicPingResponse:
    """Health ping response DTO for public client endpoint."""
    schema_version: int = 2
    ping_nonce: str = ""
    status: str = "PONG"
    daemon_status: str = "OPERATIONAL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "daemon_status": self.daemon_status,
            "ping_nonce": self.ping_nonce,
            "schema_version": self.schema_version,
            "status": self.status,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicPingResponse:
        if set(data.keys()) != {"schema_version", "ping_nonce", "status", "daemon_status"}:
            raise OracleProtocolError(ERR_UNEXPECTED_FIELD, "Invalid fields for PingResponse")
        return cls(
            schema_version=data["schema_version"],
            ping_nonce=data["ping_nonce"],
            status=data["status"],
            daemon_status=data["daemon_status"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicPingResponse:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


# ------------------------------------------------------------------------------
# Signer Public Challenge & Consent Contracts
# ------------------------------------------------------------------------------
CHALLENGE_REQUEST_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "expected_director_key_id",
    "operator_id",
    "operator_key_id",
    "approval_envelope",
    "proposal",
    "execution_plan",
    "director_public_key_pem",
    "ttl_seconds",
})


@dataclass(frozen=True)
class SignerPublicChallengeRequest:
    schema_version: int
    request_id: str
    expected_director_key_id: str
    operator_id: str
    operator_key_id: str
    approval_envelope: Dict[str, Any]
    proposal: Dict[str, Any]
    execution_plan: Dict[str, Any]
    director_public_key_pem: Optional[str] = None
    ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise OracleProtocolError(ERR_SCHEMA_VERSION_INVALID, "schema_version must be exact int 2")
        _validate_uuid_v4(self.request_id, "request_id")
        if not isinstance(self.expected_director_key_id, str) or not self.expected_director_key_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "expected_director_key_id must be non-empty str")
        if not isinstance(self.operator_id, str) or not self.operator_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "operator_id must be non-empty str")
        if not isinstance(self.operator_key_id, str) or not self.operator_key_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "operator_key_id must be non-empty str")
        if not isinstance(self.approval_envelope, dict):
            raise OracleProtocolError("ERR_INVALID_PARAMS", "approval_envelope must be dict")
        if not isinstance(self.proposal, dict):
            raise OracleProtocolError("ERR_INVALID_PARAMS", "proposal must be dict")
        if not isinstance(self.execution_plan, dict):
            raise OracleProtocolError("ERR_INVALID_PARAMS", "execution_plan must be dict")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "approval_envelope": self.approval_envelope,
            "execution_plan": self.execution_plan,
            "expected_director_key_id": self.expected_director_key_id,
            "operator_id": self.operator_id,
            "operator_key_id": self.operator_key_id,
            "proposal": self.proposal,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "ttl_seconds": self.ttl_seconds,
        }
        if self.director_public_key_pem is not None:
            d["director_public_key_pem"] = self.director_public_key_pem
        return d

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicChallengeRequest:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Request must be dict")
        extra = set(data.keys()) - CHALLENGE_REQUEST_ALLOWED_FIELDS
        if extra:
            raise OracleProtocolError(ERR_UNEXPECTED_FIELD, f"Unexpected fields: {sorted(extra)}")
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            expected_director_key_id=data["expected_director_key_id"],
            operator_id=data["operator_id"],
            operator_key_id=data["operator_key_id"],
            approval_envelope=data["approval_envelope"],
            proposal=data["proposal"],
            execution_plan=data["execution_plan"],
            director_public_key_pem=data.get("director_public_key_pem"),
            ttl_seconds=float(data.get("ttl_seconds", 300.0)),
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicChallengeRequest:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


@dataclass(frozen=True)
class SignerPublicChallengeResponse:
    schema_version: int
    request_id: str
    status: str
    challenge_id: str
    client_nonce: str
    preallocated_authorization_id: str
    preallocated_execution_id: str
    issued_at_iso: str
    expires_at_iso: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise OracleProtocolError(ERR_SCHEMA_VERSION_INVALID, "schema_version must be exact int 2")
        _validate_uuid_v4(self.request_id, "request_id")
        if not isinstance(self.preallocated_authorization_id, str) or not self.preallocated_authorization_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "preallocated_authorization_id must be non-empty str")
        if not isinstance(self.preallocated_execution_id, str) or not self.preallocated_execution_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "preallocated_execution_id must be non-empty str")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "client_nonce": self.client_nonce,
            "expires_at_iso": self.expires_at_iso,
            "issued_at_iso": self.issued_at_iso,
            "preallocated_authorization_id": self.preallocated_authorization_id,
            "preallocated_execution_id": self.preallocated_execution_id,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "status": self.status,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicChallengeResponse:
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            status=data["status"],
            challenge_id=data["challenge_id"],
            client_nonce=data["client_nonce"],
            preallocated_authorization_id=data["preallocated_authorization_id"],
            preallocated_execution_id=data["preallocated_execution_id"],
            issued_at_iso=data["issued_at_iso"],
            expires_at_iso=data["expires_at_iso"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicChallengeResponse:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


CONSENT_REQUEST_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "schema_version",
    "request_id",
    "challenge_id",
    "consent_envelope",
    "operator_public_key_pem",
    "ttl_seconds",
})


@dataclass(frozen=True)
class SignerPublicConsentRequest:
    schema_version: int
    request_id: str
    challenge_id: str
    consent_envelope: Dict[str, Any]
    operator_public_key_pem: Optional[str] = None
    ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise OracleProtocolError(ERR_SCHEMA_VERSION_INVALID, "schema_version must be exact int 2")
        _validate_uuid_v4(self.request_id, "request_id")
        if not isinstance(self.challenge_id, str) or not self.challenge_id:
            raise OracleProtocolError("ERR_INVALID_PARAMS", "challenge_id must be non-empty str")
        if not isinstance(self.consent_envelope, dict):
            raise OracleProtocolError("ERR_INVALID_PARAMS", "consent_envelope must be dict")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "challenge_id": self.challenge_id,
            "consent_envelope": self.consent_envelope,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "ttl_seconds": self.ttl_seconds,
        }
        if self.operator_public_key_pem is not None:
            d["operator_public_key_pem"] = self.operator_public_key_pem
        return d

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicConsentRequest:
        if not isinstance(data, dict):
            raise OracleProtocolError(ERR_INVALID_FRAME, "Request must be dict")
        extra = set(data.keys()) - CONSENT_REQUEST_ALLOWED_FIELDS
        if extra:
            raise OracleProtocolError(ERR_UNEXPECTED_FIELD, f"Unexpected fields: {sorted(extra)}")
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            challenge_id=data["challenge_id"],
            consent_envelope=data["consent_envelope"],
            operator_public_key_pem=data.get("operator_public_key_pem"),
            ttl_seconds=float(data.get("ttl_seconds", 300.0)),
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicConsentRequest:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)


@dataclass(frozen=True)
class SignerPublicConsentResponse:
    schema_version: int
    request_id: str
    status: str
    signed_authorization: Dict[str, Any]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise OracleProtocolError(ERR_SCHEMA_VERSION_INVALID, "schema_version must be exact int 2")
        _validate_uuid_v4(self.request_id, "request_id")
        if not isinstance(self.signed_authorization, dict):
            raise OracleProtocolError("ERR_INVALID_PARAMS", "signed_authorization must be dict")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "signed_authorization": self.signed_authorization,
            "status": self.status,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignerPublicConsentResponse:
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            status=data["status"],
            signed_authorization=data["signed_authorization"],
        )

    @classmethod
    def from_json_bytes(cls, raw_bytes: bytes) -> SignerPublicConsentResponse:
        data = _parse_strict_json(raw_bytes)
        return cls.from_dict(data)
