"""Signed Preflight Evidence Envelope Contract.

Implements the frozen SignedPreflightEvidenceEnvelope contract specified in
Workorder G07-07B7-GATE6-DEP04-CONTRACTS-R1 and
docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md §2.3.2.

Safety Boundary:
- Pure contract layer: frozen immutable data structures and strict type gates.
- Strictly no key loading, cryptographic signature creation, verification,
  socket/IPC calls, Resolve SDK imports, or live operations.
- Enforces strict RFC 8785 canonical serialization via canonical_json_bytes.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)

RE_LOWER_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_LOWER_HEX_128: re.Pattern = re.compile(r"^[0-9a-f]{128}$")
RE_CANONICAL_UUID_V4: re.Pattern = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

PINNED_TARGET_PROJECT_UID: str = "4fd4272d-f0bc-445c-9490-5723cd6831c2"
PINNED_TARGET_PROJECT_NAME: str = "RF_TRIAL_AEC8F0A7A365D5F5"
PINNED_TARGET_TIMELINE_UID: str = "ba6a6775-8ec4-41a4-bf2f-704de902e68d"
PINNED_TARGET_TIMELINE_NAME: str = "RF_TRIAL_TL_AEC8F0A7A365D5F5"
PINNED_FPS_NUMERATOR: int = 24
PINNED_FPS_DENOMINATOR: int = 1
PINNED_OBSERVED_START_FRAME: int = 86400
MAX_PREFLIGHT_VALIDITY_SECONDS: float = 1800.0

ERR_GATE4_SIGNED_PREFLIGHT_CONTRACT_REQUIRED: str = "ERR_GATE4_SIGNED_PREFLIGHT_CONTRACT_REQUIRED"

SIGNED_PREFLIGHT_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "preflight_run_id",
    "verifier_id",
    "verifier_key_id",
    "verified_at_iso",
    "valid_until_iso",
    "target_project_uid",
    "target_project_name",
    "target_timeline_uid",
    "target_timeline_name",
    "target_timeline_fps_numerator",
    "target_timeline_fps_denominator",
    "observed_start_frame",
    "project_and_timeline_active",
    "empty_timeline_verified",
    "topology_verified",
    "incident_denylist_verified",
    "media_pool_source_verified",
    "source_file_sha256",
    "audit_chain_head_sha256",
    "verifier_signature",
})

SIGNED_PREFLIGHT_SIGNING_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "preflight_run_id",
    "verifier_id",
    "verifier_key_id",
    "verified_at_iso",
    "valid_until_iso",
    "target_project_uid",
    "target_project_name",
    "target_timeline_uid",
    "target_timeline_name",
    "target_timeline_fps_numerator",
    "target_timeline_fps_denominator",
    "observed_start_frame",
    "project_and_timeline_active",
    "empty_timeline_verified",
    "topology_verified",
    "incident_denylist_verified",
    "media_pool_source_verified",
    "source_file_sha256",
    "audit_chain_head_sha256",
})


class SignedPreflightValidationError(AcceptanceGateError, ValueError):
    """Raised when SignedPreflightEvidenceEnvelope fails schema, type, bounds, or predicate checks."""
    pass


@dataclass(frozen=True)
class SignedPreflightEvidenceEnvelope:
    """Frozen, version-1 envelope encapsulating audited Gate 4 preflight signoff."""

    schema_version: int
    preflight_run_id: str
    verifier_id: str
    verifier_key_id: str
    verified_at_iso: str
    valid_until_iso: str
    target_project_uid: str
    target_project_name: str
    target_timeline_uid: str
    target_timeline_name: str
    target_timeline_fps_numerator: int
    target_timeline_fps_denominator: int
    observed_start_frame: int
    project_and_timeline_active: bool
    empty_timeline_verified: bool
    topology_verified: bool
    incident_denylist_verified: bool
    media_pool_source_verified: bool
    source_file_sha256: str
    audit_chain_head_sha256: str
    verifier_signature: str

    def __post_init__(self) -> None:
        # 1. schema_version: strictly int 1 (rejecting bool-as-int)
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool):
            raise SignedPreflightValidationError(
                f"ERR_SCHEMA_VERSION_TYPE: schema_version must be int (rejecting bool), got {type(self.schema_version).__name__}"
            )
        if self.schema_version != 1:
            raise SignedPreflightValidationError(
                f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version must be strictly 1, got {self.schema_version}"
            )

        # 2. preflight_run_id: canonical UUIDv4
        if type(self.preflight_run_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.preflight_run_id):
            raise SignedPreflightValidationError(
                f"ERR_PREFLIGHT_RUN_ID_FORMAT: preflight_run_id must be canonical UUIDv4, got {self.preflight_run_id!r}"
            )

        # 3. verifier_id & verifier_key_id: non-empty str without whitespace padding
        if type(self.verifier_id) is not str or not self.verifier_id or self.verifier_id != self.verifier_id.strip():
            raise SignedPreflightValidationError(
                f"ERR_VERIFIER_ID_FORMAT: verifier_id must be non-empty string without whitespace padding, got {self.verifier_id!r}"
            )
        if type(self.verifier_key_id) is not str or not self.verifier_key_id or self.verifier_key_id != self.verifier_key_id.strip():
            raise SignedPreflightValidationError(
                f"ERR_VERIFIER_KEY_ID_FORMAT: verifier_key_id must be non-empty string without whitespace padding, got {self.verifier_key_id!r}"
            )

        # 4. Timestamps & validity interval
        try:
            dt_verified = parse_iso_utc_timestamp(self.verified_at_iso, "verified_at_iso")
        except Exception as exc:
            raise SignedPreflightValidationError(
                f"ERR_VERIFIED_AT_TIMESTAMP_INVALID: verified_at_iso '{self.verified_at_iso}' invalid: {exc}"
            ) from exc

        try:
            dt_until = parse_iso_utc_timestamp(self.valid_until_iso, "valid_until_iso")
        except Exception as exc:
            raise SignedPreflightValidationError(
                f"ERR_VALID_UNTIL_TIMESTAMP_INVALID: valid_until_iso '{self.valid_until_iso}' invalid: {exc}"
            ) from exc

        if dt_until <= dt_verified:
            raise SignedPreflightValidationError(
                f"ERR_VALIDITY_INTERVAL_INVALID: valid_until_iso '{self.valid_until_iso}' must be strictly after verified_at_iso '{self.verified_at_iso}'"
            )
        interval_seconds = (dt_until - dt_verified).total_seconds()
        if interval_seconds > MAX_PREFLIGHT_VALIDITY_SECONDS:
            raise SignedPreflightValidationError(
                f"ERR_VALIDITY_WINDOW_EXCEEDED: validity interval {interval_seconds}s exceeds maximum {MAX_PREFLIGHT_VALIDITY_SECONDS}s"
            )

        # 5. Pinned Target Identifiers & Names (strictly str type gated before equality checks)
        if type(self.target_project_uid) is not str:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_PROJECT_UID_TYPE: target_project_uid must be str, got {type(self.target_project_uid).__name__}"
            )
        if self.target_project_uid != PINNED_TARGET_PROJECT_UID:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_PROJECT_UID_MISMATCH: expected {PINNED_TARGET_PROJECT_UID!r}, got {self.target_project_uid!r}"
            )

        if type(self.target_project_name) is not str:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_PROJECT_NAME_TYPE: target_project_name must be str, got {type(self.target_project_name).__name__}"
            )
        if self.target_project_name != PINNED_TARGET_PROJECT_NAME:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_PROJECT_NAME_MISMATCH: expected {PINNED_TARGET_PROJECT_NAME!r}, got {self.target_project_name!r}"
            )

        if type(self.target_timeline_uid) is not str:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_TIMELINE_UID_TYPE: target_timeline_uid must be str, got {type(self.target_timeline_uid).__name__}"
            )
        if self.target_timeline_uid != PINNED_TARGET_TIMELINE_UID:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_TIMELINE_UID_MISMATCH: expected {PINNED_TARGET_TIMELINE_UID!r}, got {self.target_timeline_uid!r}"
            )

        if type(self.target_timeline_name) is not str:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_TIMELINE_NAME_TYPE: target_timeline_name must be str, got {type(self.target_timeline_name).__name__}"
            )
        if self.target_timeline_name != PINNED_TARGET_TIMELINE_NAME:
            raise SignedPreflightValidationError(
                f"ERR_TARGET_TIMELINE_NAME_MISMATCH: expected {PINNED_TARGET_TIMELINE_NAME!r}, got {self.target_timeline_name!r}"
            )

        # 6. FPS Numerator / Denominator & Observed Start Frame (rejecting bool-as-int)
        if type(self.target_timeline_fps_numerator) is not int or isinstance(self.target_timeline_fps_numerator, bool):
            raise SignedPreflightValidationError("ERR_FPS_NUMERATOR_TYPE: target_timeline_fps_numerator must be int")
        if self.target_timeline_fps_numerator != PINNED_FPS_NUMERATOR:
            raise SignedPreflightValidationError(
                f"ERR_FPS_NUMERATOR_VALUE: expected {PINNED_FPS_NUMERATOR}, got {self.target_timeline_fps_numerator}"
            )

        if type(self.target_timeline_fps_denominator) is not int or isinstance(self.target_timeline_fps_denominator, bool):
            raise SignedPreflightValidationError("ERR_FPS_DENOMINATOR_TYPE: target_timeline_fps_denominator must be int")
        if self.target_timeline_fps_denominator != PINNED_FPS_DENOMINATOR:
            raise SignedPreflightValidationError(
                f"ERR_FPS_DENOMINATOR_VALUE: expected {PINNED_FPS_DENOMINATOR}, got {self.target_timeline_fps_denominator}"
            )

        if type(self.observed_start_frame) is not int or isinstance(self.observed_start_frame, bool):
            raise SignedPreflightValidationError("ERR_START_FRAME_TYPE: observed_start_frame must be int")
        if self.observed_start_frame != PINNED_OBSERVED_START_FRAME:
            raise SignedPreflightValidationError(
                f"ERR_START_FRAME_VALUE: expected {PINNED_OBSERVED_START_FRAME}, got {self.observed_start_frame}"
            )

        # 7. Five Technical Predicate Booleans (must be strictly bool and True)
        predicates = (
            ("project_and_timeline_active", self.project_and_timeline_active),
            ("empty_timeline_verified", self.empty_timeline_verified),
            ("topology_verified", self.topology_verified),
            ("incident_denylist_verified", self.incident_denylist_verified),
            ("media_pool_source_verified", self.media_pool_source_verified),
        )
        for name, val in predicates:
            if type(val) is not bool:
                raise SignedPreflightValidationError(
                    f"ERR_PREDICATE_TYPE: {name} must be exact bool, got {type(val).__name__}"
                )
            if not val:
                raise SignedPreflightValidationError(
                    f"ERR_PREDICATE_FALSE: {name} must be True"
                )

        # 8. Digests & Signature
        if type(self.source_file_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(self.source_file_sha256):
            raise SignedPreflightValidationError(
                f"ERR_SOURCE_FILE_SHA256_FORMAT: source_file_sha256 must be 64-char lowercase hex, got {self.source_file_sha256!r}"
            )
        if type(self.audit_chain_head_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(self.audit_chain_head_sha256):
            raise SignedPreflightValidationError(
                f"ERR_AUDIT_CHAIN_HEAD_FORMAT: audit_chain_head_sha256 must be 64-char lowercase hex, got {self.audit_chain_head_sha256!r}"
            )
        if type(self.verifier_signature) is not str or not RE_LOWER_HEX_128.fullmatch(self.verifier_signature):
            raise SignedPreflightValidationError(
                f"ERR_VERIFIER_SIGNATURE_FORMAT: verifier_signature must be 128-char lowercase hex, got {self.verifier_signature!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the complete envelope to a fresh plain JSON mapping including verifier_signature."""
        return {
            "audit_chain_head_sha256": self.audit_chain_head_sha256,
            "empty_timeline_verified": self.empty_timeline_verified,
            "incident_denylist_verified": self.incident_denylist_verified,
            "media_pool_source_verified": self.media_pool_source_verified,
            "observed_start_frame": self.observed_start_frame,
            "preflight_run_id": self.preflight_run_id,
            "project_and_timeline_active": self.project_and_timeline_active,
            "schema_version": self.schema_version,
            "source_file_sha256": self.source_file_sha256,
            "target_project_name": self.target_project_name,
            "target_project_uid": self.target_project_uid,
            "target_timeline_fps_denominator": self.target_timeline_fps_denominator,
            "target_timeline_fps_numerator": self.target_timeline_fps_numerator,
            "target_timeline_name": self.target_timeline_name,
            "target_timeline_uid": self.target_timeline_uid,
            "topology_verified": self.topology_verified,
            "valid_until_iso": self.valid_until_iso,
            "verified_at_iso": self.verified_at_iso,
            "verifier_id": self.verifier_id,
            "verifier_key_id": self.verifier_key_id,
            "verifier_signature": self.verifier_signature,
        }

    def canonical_signing_payload_dict(self) -> Dict[str, Any]:
        """Returns the fresh plain JSON mapping of signing fields excluding verifier_signature."""
        return {
            "audit_chain_head_sha256": self.audit_chain_head_sha256,
            "empty_timeline_verified": self.empty_timeline_verified,
            "incident_denylist_verified": self.incident_denylist_verified,
            "media_pool_source_verified": self.media_pool_source_verified,
            "observed_start_frame": self.observed_start_frame,
            "preflight_run_id": self.preflight_run_id,
            "project_and_timeline_active": self.project_and_timeline_active,
            "schema_version": self.schema_version,
            "source_file_sha256": self.source_file_sha256,
            "target_project_name": self.target_project_name,
            "target_project_uid": self.target_project_uid,
            "target_timeline_fps_denominator": self.target_timeline_fps_denominator,
            "target_timeline_fps_numerator": self.target_timeline_fps_numerator,
            "target_timeline_name": self.target_timeline_name,
            "target_timeline_uid": self.target_timeline_uid,
            "topology_verified": self.topology_verified,
            "valid_until_iso": self.valid_until_iso,
            "verified_at_iso": self.verified_at_iso,
            "verifier_id": self.verifier_id,
            "verifier_key_id": self.verifier_key_id,
        }

    def canonical_signing_payload_bytes(self) -> bytes:
        """Returns the RFC 8785 canonical bytes of the signing fields excluding verifier_signature."""
        return canonical_json_bytes(self.canonical_signing_payload_dict())

    def canonical_envelope_bytes(self) -> bytes:
        """Returns the RFC 8785 canonical bytes of the complete envelope including verifier_signature."""
        return canonical_json_bytes(self.to_dict())

    def compute_envelope_digest(self) -> str:
        """Computes the SHA-256 hex digest of the complete canonical envelope bytes."""
        return hashlib.sha256(self.canonical_envelope_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedPreflightEvidenceEnvelope:
        """Constructs and validates a SignedPreflightEvidenceEnvelope from a plain mapping.

        Rejects missing keys, unknown keys, or invalid types fail-closed.
        """
        if not isinstance(data, Mapping):
            raise SignedPreflightValidationError(
                f"ERR_PAYLOAD_NOT_MAPPING: expected mapping, got {type(data).__name__}"
            )

        actual_keys = frozenset(data.keys())
        if actual_keys != SIGNED_PREFLIGHT_ENVELOPE_KEYS:
            missing = sorted(SIGNED_PREFLIGHT_ENVELOPE_KEYS - actual_keys)
            extra = sorted(actual_keys - SIGNED_PREFLIGHT_ENVELOPE_KEYS)
            raise SignedPreflightValidationError(
                f"ERR_SCHEMA_FIELD_MISMATCH: missing={missing}, extra={extra}"
            )

        return cls(
            schema_version=data["schema_version"],
            preflight_run_id=data["preflight_run_id"],
            verifier_id=data["verifier_id"],
            verifier_key_id=data["verifier_key_id"],
            verified_at_iso=data["verified_at_iso"],
            valid_until_iso=data["valid_until_iso"],
            target_project_uid=data["target_project_uid"],
            target_project_name=data["target_project_name"],
            target_timeline_uid=data["target_timeline_uid"],
            target_timeline_name=data["target_timeline_name"],
            target_timeline_fps_numerator=data["target_timeline_fps_numerator"],
            target_timeline_fps_denominator=data["target_timeline_fps_denominator"],
            observed_start_frame=data["observed_start_frame"],
            project_and_timeline_active=data["project_and_timeline_active"],
            empty_timeline_verified=data["empty_timeline_verified"],
            topology_verified=data["topology_verified"],
            incident_denylist_verified=data["incident_denylist_verified"],
            media_pool_source_verified=data["media_pool_source_verified"],
            source_file_sha256=data["source_file_sha256"],
            audit_chain_head_sha256=data["audit_chain_head_sha256"],
            verifier_signature=data["verifier_signature"],
        )
