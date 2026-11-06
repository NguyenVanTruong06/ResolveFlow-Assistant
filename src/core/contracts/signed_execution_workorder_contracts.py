"""Signed Execution Workorder Record Contract.

Implements the frozen SignedExecutionWorkorderRecord contract specified in
Workorder G07-07B7-GATE6-DEP04-CONTRACTS-R1 and
docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md §2.5.2.

Safety Boundary:
- Pure contract layer: frozen immutable data structures and strict type gates.
- Strictly no key loading, cryptographic signature creation, verification,
  socket/IPC calls, Resolve SDK imports, command execution, or live operations.
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
RE_WORKORDER_ID: re.Pattern = re.compile(r"^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$")

PINNED_TARGET_PROJECT_UID: str = "4fd4272d-f0bc-445c-9490-5723cd6831c2"
PINNED_TARGET_TIMELINE_UID: str = "ba6a6775-8ec4-41a4-bf2f-704de902e68d"
MAX_WORKORDER_VALIDITY_SECONDS: float = 3600.0

ERR_GATE5_EXECUTION_UNAUTHORIZED: str = "ERR_GATE5_EXECUTION_UNAUTHORIZED"

SIGNED_EXECUTION_WORKORDER_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "workorder_id",
    "tech_lead_id",
    "tech_lead_key_id",
    "issued_at_iso",
    "valid_until_iso",
    "target_project_uid",
    "target_timeline_uid",
    "reviewed_candidate_sha256",
    "permitted_command",
    "plan_digest_sha256",
    "action_payload_digest_sha256",
    "execution_id",
    "tech_lead_signature",
})

SIGNED_EXECUTION_WORKORDER_SIGNING_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "workorder_id",
    "tech_lead_id",
    "tech_lead_key_id",
    "issued_at_iso",
    "valid_until_iso",
    "target_project_uid",
    "target_timeline_uid",
    "reviewed_candidate_sha256",
    "permitted_command",
    "plan_digest_sha256",
    "action_payload_digest_sha256",
    "execution_id",
})


class SignedExecutionWorkorderValidationError(AcceptanceGateError, ValueError):
    """Raised when SignedExecutionWorkorderRecord fails schema, type, bounds, or grammar checks."""
    pass


@dataclass(frozen=True)
class SignedExecutionWorkorderRecord:
    """Frozen, version-1 envelope encapsulating Release Authority execution signoff."""

    schema_version: int
    workorder_id: str
    tech_lead_id: str
    tech_lead_key_id: str
    issued_at_iso: str
    valid_until_iso: str
    target_project_uid: str
    target_timeline_uid: str
    reviewed_candidate_sha256: str
    permitted_command: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str
    execution_id: str
    tech_lead_signature: str

    def __post_init__(self) -> None:
        # 1. schema_version: strictly int 1 (rejecting bool-as-int)
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool):
            raise SignedExecutionWorkorderValidationError(
                f"ERR_SCHEMA_VERSION_TYPE: schema_version must be int (rejecting bool), got {type(self.schema_version).__name__}"
            )
        if self.schema_version != 1:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version must be strictly 1, got {self.schema_version}"
            )

        # 2. workorder_id: strict grammar G07-07B7-GATE6-EXEC-R<n>
        if type(self.workorder_id) is not str or not RE_WORKORDER_ID.fullmatch(self.workorder_id):
            raise SignedExecutionWorkorderValidationError(
                f"ERR_WORKORDER_ID_FORMAT: workorder_id must match {RE_WORKORDER_ID.pattern}, got {self.workorder_id!r}"
            )

        # 3. Non-empty string fields without surrounding whitespace
        for field_name in ("tech_lead_id", "tech_lead_key_id", "permitted_command", "execution_id"):
            val = getattr(self, field_name)
            if type(val) is not str or not val or val != val.strip():
                raise SignedExecutionWorkorderValidationError(
                    f"ERR_{field_name.upper()}_FORMAT: {field_name} must be non-empty string without whitespace padding, got {val!r}"
                )

        # 4. Timestamps & validity interval
        try:
            dt_issued = parse_iso_utc_timestamp(self.issued_at_iso, "issued_at_iso")
        except Exception as exc:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_ISSUED_AT_TIMESTAMP_INVALID: issued_at_iso '{self.issued_at_iso}' invalid: {exc}"
            ) from exc

        try:
            dt_until = parse_iso_utc_timestamp(self.valid_until_iso, "valid_until_iso")
        except Exception as exc:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_VALID_UNTIL_TIMESTAMP_INVALID: valid_until_iso '{self.valid_until_iso}' invalid: {exc}"
            ) from exc

        if dt_until <= dt_issued:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_VALIDITY_INTERVAL_INVALID: valid_until_iso '{self.valid_until_iso}' must be strictly after issued_at_iso '{self.issued_at_iso}'"
            )
        interval_seconds = (dt_until - dt_issued).total_seconds()
        if interval_seconds > MAX_WORKORDER_VALIDITY_SECONDS:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_VALIDITY_WINDOW_EXCEEDED: validity interval {interval_seconds}s exceeds maximum {MAX_WORKORDER_VALIDITY_SECONDS}s"
            )

        # 5. Pinned Target Identifiers (strictly str type gated before equality checks)
        if type(self.target_project_uid) is not str:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_TARGET_PROJECT_UID_TYPE: target_project_uid must be str, got {type(self.target_project_uid).__name__}"
            )
        if self.target_project_uid != PINNED_TARGET_PROJECT_UID:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_TARGET_PROJECT_UID_MISMATCH: expected {PINNED_TARGET_PROJECT_UID!r}, got {self.target_project_uid!r}"
            )

        if type(self.target_timeline_uid) is not str:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_TARGET_TIMELINE_UID_TYPE: target_timeline_uid must be str, got {type(self.target_timeline_uid).__name__}"
            )
        if self.target_timeline_uid != PINNED_TARGET_TIMELINE_UID:
            raise SignedExecutionWorkorderValidationError(
                f"ERR_TARGET_TIMELINE_UID_MISMATCH: expected {PINNED_TARGET_TIMELINE_UID!r}, got {self.target_timeline_uid!r}"
            )

        # 6. Digests & Signature format
        for field_name in ("reviewed_candidate_sha256", "plan_digest_sha256", "action_payload_digest_sha256"):
            val = getattr(self, field_name)
            if type(val) is not str or not RE_LOWER_HEX_64.fullmatch(val):
                raise SignedExecutionWorkorderValidationError(
                    f"ERR_{field_name.upper()}_FORMAT: {field_name} must be 64-char lowercase hex, got {val!r}"
                )

        if type(self.tech_lead_signature) is not str or not RE_LOWER_HEX_128.fullmatch(self.tech_lead_signature):
            raise SignedExecutionWorkorderValidationError(
                f"ERR_TECH_LEAD_SIGNATURE_FORMAT: tech_lead_signature must be 128-char lowercase hex, got {self.tech_lead_signature!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the complete record to a fresh plain JSON mapping including tech_lead_signature."""
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "execution_id": self.execution_id,
            "issued_at_iso": self.issued_at_iso,
            "permitted_command": self.permitted_command,
            "plan_digest_sha256": self.plan_digest_sha256,
            "reviewed_candidate_sha256": self.reviewed_candidate_sha256,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "tech_lead_id": self.tech_lead_id,
            "tech_lead_key_id": self.tech_lead_key_id,
            "tech_lead_signature": self.tech_lead_signature,
            "valid_until_iso": self.valid_until_iso,
            "workorder_id": self.workorder_id,
        }

    def canonical_signing_payload_dict(self) -> Dict[str, Any]:
        """Returns the fresh plain JSON mapping of signing fields excluding tech_lead_signature."""
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "execution_id": self.execution_id,
            "issued_at_iso": self.issued_at_iso,
            "permitted_command": self.permitted_command,
            "plan_digest_sha256": self.plan_digest_sha256,
            "reviewed_candidate_sha256": self.reviewed_candidate_sha256,
            "schema_version": self.schema_version,
            "target_project_uid": self.target_project_uid,
            "target_timeline_uid": self.target_timeline_uid,
            "tech_lead_id": self.tech_lead_id,
            "tech_lead_key_id": self.tech_lead_key_id,
            "valid_until_iso": self.valid_until_iso,
            "workorder_id": self.workorder_id,
        }

    def canonical_signing_payload_bytes(self) -> bytes:
        """Returns the RFC 8785 canonical bytes of the signing fields excluding tech_lead_signature."""
        return canonical_json_bytes(self.canonical_signing_payload_dict())

    def canonical_envelope_bytes(self) -> bytes:
        """Returns the RFC 8785 canonical bytes of the complete record including tech_lead_signature."""
        return canonical_json_bytes(self.to_dict())

    def compute_envelope_digest(self) -> str:
        """Computes the SHA-256 hex digest of the complete canonical envelope bytes."""
        return hashlib.sha256(self.canonical_envelope_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedExecutionWorkorderRecord:
        """Constructs and validates a SignedExecutionWorkorderRecord from a plain mapping.

        Rejects missing keys, unknown keys, or invalid types fail-closed.
        """
        if not isinstance(data, Mapping):
            raise SignedExecutionWorkorderValidationError(
                f"ERR_PAYLOAD_NOT_MAPPING: expected mapping, got {type(data).__name__}"
            )

        actual_keys = frozenset(data.keys())
        if actual_keys != SIGNED_EXECUTION_WORKORDER_KEYS:
            missing = sorted(SIGNED_EXECUTION_WORKORDER_KEYS - actual_keys)
            extra = sorted(actual_keys - SIGNED_EXECUTION_WORKORDER_KEYS)
            raise SignedExecutionWorkorderValidationError(
                f"ERR_SCHEMA_FIELD_MISMATCH: missing={missing}, extra={extra}"
            )

        return cls(
            schema_version=data["schema_version"],
            workorder_id=data["workorder_id"],
            tech_lead_id=data["tech_lead_id"],
            tech_lead_key_id=data["tech_lead_key_id"],
            issued_at_iso=data["issued_at_iso"],
            valid_until_iso=data["valid_until_iso"],
            target_project_uid=data["target_project_uid"],
            target_timeline_uid=data["target_timeline_uid"],
            reviewed_candidate_sha256=data["reviewed_candidate_sha256"],
            permitted_command=data["permitted_command"],
            plan_digest_sha256=data["plan_digest_sha256"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            execution_id=data["execution_id"],
            tech_lead_signature=data["tech_lead_signature"],
        )
