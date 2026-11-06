"""Signed Approval Envelope Contract (G07-07B7-I1)

Implements the frozen, version-1 SignedApprovalEnvelope contract defined in
docs/architecture/07B7-independent-authorization-signing-workflow-plan.md §4.1.

Safety Boundary:
- Pure contract layer: frozen immutable data structures and strict type gates.
- No signer runtime, key store, socket/RPC, Dispatcher, Resolve IPC, or live operations.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional

from src.core.contracts.edit_plan_approval_contracts import (
    AppOwnedApprovalRecord,
    approval_record_to_dict,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)

RE_LOWER_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_LOWER_HEX_128: re.Pattern = re.compile(r"^[0-9a-f]{128}$")

SIGNED_APPROVAL_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "approval_record",
    "plan_digest_sha256",
    "proposal_content_digest_sha256",
    "director_key_id",
    "director_signature",
    "signed_at_iso",
})

SIGNED_APPROVAL_SIGNING_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "approval_record",
    "plan_digest_sha256",
    "proposal_content_digest_sha256",
    "director_key_id",
    "signed_at_iso",
})


class SignedApprovalEnvelopeValidationError(AcceptanceGateError, ValueError):
    """Raised when SignedApprovalEnvelope fails schema, type, format, or bounds checks."""
    pass


@dataclass(frozen=True)
class SignedApprovalEnvelope:
    """Frozen, version-1 envelope binding an AppOwnedApprovalRecord with Director signature.

    Defined in 07B7 R7 §4.1:
    - schema_version: Strictly 1 (rejects bool-as-int).
    - approval_record: Frozen 6-field AppOwnedApprovalRecord (07B5 contract object).
    - plan_digest_sha256: 64-character lowercase hex digest of TimelineExecutionPlan.
    - proposal_content_digest_sha256: 64-character lowercase hex digest of proposal snapshot.
    - director_key_id: Non-empty identifier of Director signing key.
    - director_signature: 128-character lowercase hex Ed25519 digital signature.
    - signed_at_iso: Strict UTC ISO-8601 timestamp with explicit zero-offset suffix.
    """
    schema_version: int
    approval_record: AppOwnedApprovalRecord
    plan_digest_sha256: str
    proposal_content_digest_sha256: str
    director_key_id: str
    director_signature: str
    signed_at_iso: str

    def __post_init__(self) -> None:
        # 1. schema_version: strictly int, strictly 1, rejecting bool-as-int
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_SCHEMA_VERSION_TYPE: schema_version must be int (rejecting bool), got {type(self.schema_version).__name__}"
            )
        if self.schema_version != 1:
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version must be strictly 1, got {self.schema_version}"
            )

        # 2. approval_record: strictly AppOwnedApprovalRecord
        if not isinstance(self.approval_record, AppOwnedApprovalRecord):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_APPROVAL_RECORD_TYPE: approval_record must be AppOwnedApprovalRecord, got {type(self.approval_record).__name__}"
            )

        # 3. plan_digest_sha256: strictly 64-char lowercase hex
        if type(self.plan_digest_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(self.plan_digest_sha256):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_PLAN_DIGEST_FORMAT: plan_digest_sha256 must be a 64-character lowercase hex string, got {self.plan_digest_sha256!r}"
            )

        # 4. proposal_content_digest_sha256: strictly 64-char lowercase hex
        if type(self.proposal_content_digest_sha256) is not str or not RE_LOWER_HEX_64.fullmatch(self.proposal_content_digest_sha256):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_PROPOSAL_DIGEST_FORMAT: proposal_content_digest_sha256 must be a 64-character lowercase hex string, got {self.proposal_content_digest_sha256!r}"
            )

        # 5. director_key_id: non-empty str without leading/trailing whitespace
        if type(self.director_key_id) is not str or not self.director_key_id or self.director_key_id != self.director_key_id.strip():
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_DIRECTOR_KEY_ID_FORMAT: director_key_id must be a non-empty string without whitespace padding, got {self.director_key_id!r}"
            )

        # 6. director_signature: strictly 128-char lowercase hex
        if type(self.director_signature) is not str or not RE_LOWER_HEX_128.fullmatch(self.director_signature):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_DIRECTOR_SIGNATURE_FORMAT: director_signature must be a 128-character lowercase hex string, got {self.director_signature!r}"
            )

        # 7. signed_at_iso: strict UTC ISO-8601 validation
        try:
            parse_iso_utc_timestamp(self.signed_at_iso, "signed_at_iso")
        except Exception as exc:
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_SIGNED_AT_TIMESTAMP_INVALID: signed_at_iso '{self.signed_at_iso}' is not a valid strict UTC ISO-8601 timestamp: {exc}"
            ) from exc

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the complete signed envelope to a fresh plain JSON mapping.

        Mutating the returned mapping does not affect the frozen instance or subsequent serializations.
        """
        return {
            "approval_record": approval_record_to_dict(self.approval_record),
            "director_key_id": self.director_key_id,
            "director_signature": self.director_signature,
            "plan_digest_sha256": self.plan_digest_sha256,
            "proposal_content_digest_sha256": self.proposal_content_digest_sha256,
            "schema_version": self.schema_version,
            "signed_at_iso": self.signed_at_iso,
        }

    def canonical_signing_payload_dict(self) -> Dict[str, Any]:
        """Returns the fresh plain JSON mapping of signing fields excluding director_signature."""
        return {
            "approval_record": approval_record_to_dict(self.approval_record),
            "director_key_id": self.director_key_id,
            "plan_digest_sha256": self.plan_digest_sha256,
            "proposal_content_digest_sha256": self.proposal_content_digest_sha256,
            "schema_version": self.schema_version,
            "signed_at_iso": self.signed_at_iso,
        }

    def canonical_signing_payload_bytes(self) -> bytes:
        """Returns the deterministic RFC 8785 canonical bytes of the signing payload.

        Crucial Invariant: director_signature is strictly excluded from these bytes.
        """
        return canonical_json_bytes(self.canonical_signing_payload_dict())

    def canonical_envelope_bytes(self) -> bytes:
        """Returns the deterministic RFC 8785 canonical bytes of the complete signed envelope.

        Includes director_signature for envelope digesting.
        """
        return canonical_json_bytes(self.to_dict())

    def compute_envelope_digest(self) -> str:
        """Computes the SHA-256 hex digest of the complete canonical envelope bytes."""
        return hashlib.sha256(self.canonical_envelope_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedApprovalEnvelope:
        """Constructs and validates a SignedApprovalEnvelope from a plain mapping.

        Rejects missing keys, unknown keys, or invalid types fail-closed.
        """
        if not isinstance(data, Mapping):
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_PAYLOAD_NOT_MAPPING: expected mapping, got {type(data).__name__}"
            )

        actual_keys = frozenset(data.keys())
        if actual_keys != SIGNED_APPROVAL_ENVELOPE_KEYS:
            missing = sorted(SIGNED_APPROVAL_ENVELOPE_KEYS - actual_keys)
            extra = sorted(actual_keys - SIGNED_APPROVAL_ENVELOPE_KEYS)
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_SCHEMA_FIELD_MISMATCH: missing={missing}, extra={extra}"
            )

        rec_raw = data["approval_record"]
        if isinstance(rec_raw, AppOwnedApprovalRecord):
            approval_rec = rec_raw
        elif isinstance(rec_raw, Mapping):
            rec_expected = {
                "approval_id",
                "proposal_id",
                "proposal_revision",
                "approved_at_iso",
                "accepted_omission_ids",
                "approval_scope",
            }
            if set(rec_raw.keys()) != rec_expected:
                raise SignedApprovalEnvelopeValidationError(
                    "ERR_APPROVAL_RECORD_KEYS: keys in approval_record mapping must match AppOwnedApprovalRecord fields exactly"
                )
            omissions = rec_raw["accepted_omission_ids"]
            if not isinstance(omissions, (tuple, list)):
                raise SignedApprovalEnvelopeValidationError(
                    f"ERR_APPROVAL_RECORD_INVALID: accepted_omission_ids must be list or tuple, got {type(omissions).__name__}"
                )
            for idx, item in enumerate(omissions):
                if type(item) is not str:
                    raise SignedApprovalEnvelopeValidationError(
                        f"ERR_APPROVAL_RECORD_INVALID: accepted_omission_ids[{idx}] must be strictly str (no coercion), got {type(item).__name__}"
                    )
            try:
                approval_rec = AppOwnedApprovalRecord(
                    approval_id=rec_raw["approval_id"],
                    proposal_id=rec_raw["proposal_id"],
                    proposal_revision=rec_raw["proposal_revision"],
                    approved_at_iso=rec_raw["approved_at_iso"],
                    accepted_omission_ids=tuple(omissions),
                    approval_scope=rec_raw["approval_scope"],
                )
            except Exception as exc:
                raise SignedApprovalEnvelopeValidationError(
                    f"ERR_APPROVAL_RECORD_INVALID: failed constructing AppOwnedApprovalRecord: {exc}"
                ) from exc
        else:
            raise SignedApprovalEnvelopeValidationError(
                f"ERR_APPROVAL_RECORD_TYPE: approval_record must be AppOwnedApprovalRecord or Mapping, got {type(rec_raw).__name__}"
            )

        return cls(
            schema_version=data["schema_version"],
            approval_record=approval_rec,
            plan_digest_sha256=data["plan_digest_sha256"],
            proposal_content_digest_sha256=data["proposal_content_digest_sha256"],
            director_key_id=data["director_key_id"],
            director_signature=data["director_signature"],
            signed_at_iso=data["signed_at_iso"],
        )
