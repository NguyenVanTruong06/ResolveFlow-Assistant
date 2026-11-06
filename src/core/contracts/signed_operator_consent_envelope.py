"""Signed Operator Consent Envelope Contract (G07-07B7-I1)

Implements the frozen, version-1 SignedOperatorConsentEnvelope contract defined in
docs/architecture/07B7-independent-authorization-signing-workflow-plan.md §3.1.

Safety Boundary:
- Pure contract layer: frozen immutable data structures and strict type gates.
- Validates structure, grammar, bounds, and canonical bytes only.
- Does not verify digital signatures, decide freshness/challenge state, or access live resources.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
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
RE_CHALLENGE_ID: re.Pattern = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

CONFIRMATION_PHRASE_LITERAL: str = "CONFIRM_CANARY_TRIAL_EXECUTION"
FRESHNESS_ATTESTATION_LITERAL: str = "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED"
ACTION_TYPE_LITERAL: str = "INSERT_CLIP"
TRACK_TYPE_LITERAL: str = "video"
EXPECTED_TRACK_INDEX: int = 1
EXPECTED_DURATION_FRAMES: int = 120
MAX_JCS_SAFE_INTEGER: int = 9_007_199_254_740_991  # 2^53 - 1 (RFC 8785 IEEE-754 binary64 safe integer)

SIGNED_OPERATOR_CONSENT_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "operator_id",
    "challenge_id",
    "client_nonce",
    "confirmation_phrase",
    "consent_timestamp_iso",
    "freshness_attestation",
    "approval_envelope_digest_sha256",
    "plan_digest_sha256",
    "action_payload_digest_sha256",
    "target_project_name",
    "target_project_uid",
    "target_timeline_name",
    "target_timeline_uid",
    "source_file_path",
    "source_file_sha256",
    "action_type",
    "track_type",
    "track_index",
    "record_frame",
    "duration_frames",
    "operator_key_id",
    "operator_signature",
})

SIGNED_OPERATOR_CONSENT_SIGNING_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "operator_id",
    "challenge_id",
    "client_nonce",
    "confirmation_phrase",
    "consent_timestamp_iso",
    "freshness_attestation",
    "approval_envelope_digest_sha256",
    "plan_digest_sha256",
    "action_payload_digest_sha256",
    "target_project_name",
    "target_project_uid",
    "target_timeline_name",
    "target_timeline_uid",
    "source_file_path",
    "source_file_sha256",
    "action_type",
    "track_type",
    "track_index",
    "record_frame",
    "duration_frames",
    "operator_key_id",
})


class SignedOperatorConsentEnvelopeValidationError(AcceptanceGateError, ValueError):
    """Raised when SignedOperatorConsentEnvelope fails schema, type, format, or bounds checks."""
    pass


@dataclass(frozen=True)
class SignedOperatorConsentEnvelope:
    """Frozen, version-1 envelope encapsulating independently authenticated operator consent.

    Defined in 07B7 R7 §3.1:
    - schema_version: Strictly 1 (rejecting bool-as-int).
    - operator_id: Human operator identifier.
    - challenge_id: Cryptographic challenge token (grammar: ^[A-Za-z0-9_-]{4,64}$).
    - client_nonce: Canonical lowercase UUIDv4.
    - confirmation_phrase: Strictly 'CONFIRM_CANARY_TRIAL_EXECUTION'.
    - consent_timestamp_iso: UTC ISO-8601 timestamp with explicit zero-offset suffix.
    - freshness_attestation: Strictly 'OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED'.
    - approval_envelope_digest_sha256: 64-char lowercase hex digest of SignedApprovalEnvelope.
    - plan_digest_sha256: 64-char lowercase hex digest of TimelineExecutionPlan.
    - action_payload_digest_sha256: 64-char lowercase hex digest of canonical 16-key action payload.
    - target_project_name: Non-empty target project name.
    - target_project_uid: Non-empty target project UID.
    - target_timeline_name: Non-empty target timeline name.
    - target_timeline_uid: Non-empty target timeline UID.
    - source_file_path: Non-empty absolute path to canary source media.
    - source_file_sha256: 64-char lowercase hex digest of canary media bytes.
    - action_type: Strictly 'INSERT_CLIP'.
    - track_type: Strictly 'video'.
    - track_index: Strictly 1 (rejecting bool-as-int).
    - record_frame: Integer >= 0 (rejecting bool-as-int).
    - duration_frames: Strictly 120 (rejecting bool-as-int).
    - operator_key_id: Non-empty key identifier for Operator key.
    - operator_signature: 128-character lowercase hex Ed25519 signature.
    """
    schema_version: int
    operator_id: str
    challenge_id: str
    client_nonce: str
    confirmation_phrase: str
    consent_timestamp_iso: str
    freshness_attestation: str

    approval_envelope_digest_sha256: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str
    target_project_name: str
    target_project_uid: str
    target_timeline_name: str
    target_timeline_uid: str
    source_file_path: str
    source_file_sha256: str
    action_type: str
    track_type: str
    track_index: int
    record_frame: int
    duration_frames: int

    operator_key_id: str
    operator_signature: str

    def __post_init__(self) -> None:
        # 1. schema_version: strictly int, strictly 1, rejecting bool-as-int
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_SCHEMA_VERSION_TYPE: schema_version must be int (rejecting bool), got {type(self.schema_version).__name__}"
            )
        if self.schema_version != 1:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_UNSUPPORTED_SCHEMA_VERSION: schema_version must be strictly 1, got {self.schema_version}"
            )

        # 2. operator_id: non-empty string without whitespace padding
        if type(self.operator_id) is not str or not self.operator_id or self.operator_id != self.operator_id.strip():
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_OPERATOR_ID_FORMAT: operator_id must be a non-empty string without whitespace padding, got {self.operator_id!r}"
            )

        # 3. challenge_id: token grammar ^[A-Za-z0-9_-]{4,64}$
        if type(self.challenge_id) is not str or not RE_CHALLENGE_ID.fullmatch(self.challenge_id):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_CHALLENGE_ID_FORMAT: challenge_id must match token grammar ^[A-Za-z0-9_-]{{4,64}}$, got {self.challenge_id!r}"
            )

        # 4. client_nonce: canonical lowercase UUIDv4
        if type(self.client_nonce) is not str or not RE_CANONICAL_UUID_V4.fullmatch(self.client_nonce):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_CLIENT_NONCE_FORMAT: client_nonce must be a canonical lowercase UUIDv4 string, got {self.client_nonce!r}"
            )

        # 5. confirmation_phrase: strictly 'CONFIRM_CANARY_TRIAL_EXECUTION'
        if type(self.confirmation_phrase) is not str or self.confirmation_phrase != CONFIRMATION_PHRASE_LITERAL:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_CONFIRMATION_PHRASE_MISMATCH: confirmation_phrase must be strictly {CONFIRMATION_PHRASE_LITERAL!r}, got {self.confirmation_phrase!r}"
            )

        # 6. consent_timestamp_iso: strict UTC ISO-8601 validation
        try:
            parse_iso_utc_timestamp(self.consent_timestamp_iso, "consent_timestamp_iso")
        except Exception as exc:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_CONSENT_TIMESTAMP_INVALID: consent_timestamp_iso '{self.consent_timestamp_iso}' is not a valid strict UTC ISO-8601 timestamp: {exc}"
            ) from exc

        # 7. freshness_attestation: strictly 'OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED'
        if type(self.freshness_attestation) is not str or self.freshness_attestation != FRESHNESS_ATTESTATION_LITERAL:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_FRESHNESS_ATTESTATION_MISMATCH: freshness_attestation must be strictly {FRESHNESS_ATTESTATION_LITERAL!r}, got {self.freshness_attestation!r}"
            )

        # 8-10. SHA-256 digests: 64-char lowercase hex
        for field_name in (
            "approval_envelope_digest_sha256",
            "plan_digest_sha256",
            "action_payload_digest_sha256",
            "source_file_sha256",
        ):
            val = getattr(self, field_name)
            if type(val) is not str or not RE_LOWER_HEX_64.fullmatch(val):
                raise SignedOperatorConsentEnvelopeValidationError(
                    f"ERR_DIGEST_FORMAT: {field_name} must be a 64-character lowercase hex string, got {val!r}"
                )

        # 11-14. Target project/timeline names and UIDs: non-empty strings without whitespace padding
        for field_name in (
            "target_project_name",
            "target_project_uid",
            "target_timeline_name",
            "target_timeline_uid",
        ):
            val = getattr(self, field_name)
            if type(val) is not str or not val or val != val.strip():
                raise SignedOperatorConsentEnvelopeValidationError(
                    f"ERR_TARGET_STRING_FORMAT: {field_name} must be a non-empty string without whitespace padding, got {val!r}"
                )

        # 15. source_file_path: non-empty absolute path without whitespace padding
        if (
            type(self.source_file_path) is not str
            or not self.source_file_path
            or self.source_file_path != self.source_file_path.strip()
            or not os.path.isabs(self.source_file_path)
        ):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_SOURCE_FILE_PATH_FORMAT: source_file_path must be a non-empty absolute path without whitespace padding, got {self.source_file_path!r}"
            )

        # 17. action_type: strictly 'INSERT_CLIP'
        if type(self.action_type) is not str or self.action_type != ACTION_TYPE_LITERAL:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_ACTION_TYPE_MISMATCH: action_type must be strictly {ACTION_TYPE_LITERAL!r}, got {self.action_type!r}"
            )

        # 18. track_type: strictly 'video'
        if type(self.track_type) is not str or self.track_type != TRACK_TYPE_LITERAL:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_TRACK_TYPE_MISMATCH: track_type must be strictly {TRACK_TYPE_LITERAL!r}, got {self.track_type!r}"
            )

        # 19. track_index: strictly int == 1, rejecting bool-as-int
        if type(self.track_index) is not int or isinstance(self.track_index, bool):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_TRACK_INDEX_TYPE: track_index must be int (rejecting bool), got {type(self.track_index).__name__}"
            )
        if self.track_index != EXPECTED_TRACK_INDEX:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_TRACK_INDEX_BOUNDS: track_index must be strictly {EXPECTED_TRACK_INDEX}, got {self.track_index}"
            )

        # 20. record_frame: strictly int >= 0 and <= MAX_JCS_SAFE_INTEGER, rejecting bool-as-int
        if type(self.record_frame) is not int or isinstance(self.record_frame, bool):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_RECORD_FRAME_TYPE: record_frame must be int (rejecting bool), got {type(self.record_frame).__name__}"
            )
        if self.record_frame < 0:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_RECORD_FRAME_BOUNDS: record_frame must be non-negative (>= 0), got {self.record_frame}"
            )
        if self.record_frame > MAX_JCS_SAFE_INTEGER:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_RECORD_FRAME_BOUNDS: record_frame exceeds JCS safe integer limit ({MAX_JCS_SAFE_INTEGER}), got {self.record_frame}"
            )

        # 21. duration_frames: strictly int == 120, rejecting bool-as-int
        if type(self.duration_frames) is not int or isinstance(self.duration_frames, bool):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_DURATION_FRAMES_TYPE: duration_frames must be int (rejecting bool), got {type(self.duration_frames).__name__}"
            )
        if self.duration_frames != EXPECTED_DURATION_FRAMES:
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_DURATION_FRAMES_BOUNDS: duration_frames must be strictly {EXPECTED_DURATION_FRAMES}, got {self.duration_frames}"
            )

        # 22. operator_key_id: non-empty string without whitespace padding
        if type(self.operator_key_id) is not str or not self.operator_key_id or self.operator_key_id != self.operator_key_id.strip():
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_OPERATOR_KEY_ID_FORMAT: operator_key_id must be a non-empty string without whitespace padding, got {self.operator_key_id!r}"
            )

        # 23. operator_signature: strictly 128-char lowercase hex
        if type(self.operator_signature) is not str or not RE_LOWER_HEX_128.fullmatch(self.operator_signature):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_OPERATOR_SIGNATURE_FORMAT: operator_signature must be a 128-character lowercase hex string, got {self.operator_signature!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the complete signed consent envelope to a fresh plain JSON mapping.

        Mutating the returned mapping does not affect the frozen instance or subsequent serializations.
        """
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "action_type": self.action_type,
            "approval_envelope_digest_sha256": self.approval_envelope_digest_sha256,
            "challenge_id": self.challenge_id,
            "client_nonce": self.client_nonce,
            "confirmation_phrase": self.confirmation_phrase,
            "consent_timestamp_iso": self.consent_timestamp_iso,
            "duration_frames": self.duration_frames,
            "freshness_attestation": self.freshness_attestation,
            "operator_id": self.operator_id,
            "operator_key_id": self.operator_key_id,
            "operator_signature": self.operator_signature,
            "plan_digest_sha256": self.plan_digest_sha256,
            "record_frame": self.record_frame,
            "schema_version": self.schema_version,
            "source_file_path": self.source_file_path,
            "source_file_sha256": self.source_file_sha256,
            "target_project_name": self.target_project_name,
            "target_project_uid": self.target_project_uid,
            "target_timeline_name": self.target_timeline_name,
            "target_timeline_uid": self.target_timeline_uid,
            "track_index": self.track_index,
            "track_type": self.track_type,
        }

    def canonical_signing_payload_dict(self) -> Dict[str, Any]:
        """Returns the fresh plain JSON mapping of signing fields excluding operator_signature."""
        return {
            "action_payload_digest_sha256": self.action_payload_digest_sha256,
            "action_type": self.action_type,
            "approval_envelope_digest_sha256": self.approval_envelope_digest_sha256,
            "challenge_id": self.challenge_id,
            "client_nonce": self.client_nonce,
            "confirmation_phrase": self.confirmation_phrase,
            "consent_timestamp_iso": self.consent_timestamp_iso,
            "duration_frames": self.duration_frames,
            "freshness_attestation": self.freshness_attestation,
            "operator_id": self.operator_id,
            "operator_key_id": self.operator_key_id,
            "plan_digest_sha256": self.plan_digest_sha256,
            "record_frame": self.record_frame,
            "schema_version": self.schema_version,
            "source_file_path": self.source_file_path,
            "source_file_sha256": self.source_file_sha256,
            "target_project_name": self.target_project_name,
            "target_project_uid": self.target_project_uid,
            "target_timeline_name": self.target_timeline_name,
            "target_timeline_uid": self.target_timeline_uid,
            "track_index": self.track_index,
            "track_type": self.track_type,
        }

    def canonical_signing_payload_bytes(self) -> bytes:
        """Returns the deterministic RFC 8785 canonical bytes of the signing payload.

        Crucial Invariant: operator_signature is strictly excluded from these bytes.
        """
        return canonical_json_bytes(self.canonical_signing_payload_dict())

    def canonical_envelope_bytes(self) -> bytes:
        """Returns the deterministic RFC 8785 canonical bytes of the complete signed consent envelope.

        Includes operator_signature for operator_gesture_digest_sha256 computation.
        """
        return canonical_json_bytes(self.to_dict())

    def compute_consent_digest(self) -> str:
        """Computes the SHA-256 hex digest of the complete canonical envelope bytes.

        Matches operator_gesture_digest_sha256 in TrialExecutionAuthorization.
        """
        return hashlib.sha256(self.canonical_envelope_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignedOperatorConsentEnvelope:
        """Constructs and validates a SignedOperatorConsentEnvelope from a plain mapping.

        Rejects missing keys, unknown keys, or invalid types fail-closed.
        """
        if not isinstance(data, Mapping):
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_PAYLOAD_NOT_MAPPING: expected mapping, got {type(data).__name__}"
            )

        actual_keys = frozenset(data.keys())
        if actual_keys != SIGNED_OPERATOR_CONSENT_ENVELOPE_KEYS:
            missing = sorted(SIGNED_OPERATOR_CONSENT_ENVELOPE_KEYS - actual_keys)
            extra = sorted(actual_keys - SIGNED_OPERATOR_CONSENT_ENVELOPE_KEYS)
            raise SignedOperatorConsentEnvelopeValidationError(
                f"ERR_SCHEMA_FIELD_MISMATCH: missing={missing}, extra={extra}"
            )

        return cls(
            schema_version=data["schema_version"],
            operator_id=data["operator_id"],
            challenge_id=data["challenge_id"],
            client_nonce=data["client_nonce"],
            confirmation_phrase=data["confirmation_phrase"],
            consent_timestamp_iso=data["consent_timestamp_iso"],
            freshness_attestation=data["freshness_attestation"],
            approval_envelope_digest_sha256=data["approval_envelope_digest_sha256"],
            plan_digest_sha256=data["plan_digest_sha256"],
            action_payload_digest_sha256=data["action_payload_digest_sha256"],
            target_project_name=data["target_project_name"],
            target_project_uid=data["target_project_uid"],
            target_timeline_name=data["target_timeline_name"],
            target_timeline_uid=data["target_timeline_uid"],
            source_file_path=data["source_file_path"],
            source_file_sha256=data["source_file_sha256"],
            action_type=data["action_type"],
            track_type=data["track_type"],
            track_index=data["track_index"],
            record_frame=data["record_frame"],
            duration_frames=data["duration_frames"],
            operator_key_id=data["operator_key_id"],
            operator_signature=data["operator_signature"],
        )
