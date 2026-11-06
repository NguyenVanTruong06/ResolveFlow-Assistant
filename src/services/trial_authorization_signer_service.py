"""Trial Authorization Signer Service (G07-07B7-I4 / Gate 3-I5 / Gate 3-I6)

Implements the bounded verification and nonce ledger components of TrialAuthorizationSignerService
from docs/architecture/07B7-independent-authorization-signing-workflow-plan.md §§3.1–3.3, 4.2, 5, 6, 8, 10.

Core Responsibilities:
- Gate 3-I4: Verifies an existing SignedApprovalEnvelope against current proposal and plan,
  recomputing digests and validating Director Ed25519 signatures.
- Gate 3-I5: Pure Operator Consent verifier for SignedOperatorConsentEnvelope against
  explicit verification context (challenge and pending request expectations), enforcing
  the plan's §3.2 20 checks + verification-instant challenge expiry gate, instant-based
  freshness and clock-skew bounds, and Ed25519 signature verification over accepted RFC 8785 bytes.
- Gate 3-I6: Durable consumed-nonce ledger primitive (SignerConsumedNonceLedger and
  ConsumedAuthorizationNonceRecord) with atomic directory anchoring, 0700/0600 permissions,
  process-local and cross-process locking, fail-closed torn tail detection, and collision prevention.

Safety Boundary:
- Pure verification and local temporary ledger persistence slice.
- Zero authority token or TrialExecutionAuthorization issuance.
- Zero challenge generation or runtime issuance flow wiring.
- Zero real / persistent key loading; zero /var/lib/resolveflow access.
- Zero network, zero sockets/RPC, zero DaVinci Resolve IPC/UI.
- Zero key bytes or signatures echoed in exception messages, repr, or results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import ctypes
import errno
import fcntl
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import secrets
import stat
import socket
import struct
import tempfile
import time
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key

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
from src.core.contracts.signed_approval_envelope import (
    RE_LOWER_HEX_64,
    SignedApprovalEnvelope,
)
from src.core.contracts.signed_operator_consent_envelope import (
    SignedOperatorConsentEnvelope,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    StrictJsonComplexityError,
    StrictJsonParseError,
    TrialExecutionAuthorization,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
    parse_strict_canonical_json,
    validate_trial_execution_authorization,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction,
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)

CLOSE_OUTCOME_UNKNOWN: str = "CLOSE_OUTCOME_UNKNOWN"


class TrialSignerError(AcceptanceGateError, ValueError):
    """Base exception for all trial authorization signer service errors."""
    pass


class DirectorApprovalVerificationError(TrialSignerError):
    """Raised when Director approval envelope verification fails fail-closed."""
    pass


class OperatorConsentVerificationError(TrialSignerError):
    """Raised when Operator consent envelope verification fails fail-closed."""
    def __init__(self, message_or_code: str, detail: Optional[str] = None) -> None:
        if detail is not None:
            code = message_or_code
            full_msg = f"{code}: {detail}"
        else:
            full_msg = message_or_code
            code = full_msg.split(":", 1)[0].strip() if ":" in full_msg else "ERR_OPERATOR_CONSENT_VERIFICATION"
        super().__init__(full_msg)
        self.code = code


class SignerAdmissionVerificationError(TrialSignerError):
    """Raised when authorization admission verification or cross-binding fails fail-closed."""
    def __init__(self, message_or_code: str, detail: Optional[str] = None) -> None:
        if detail is not None:
            code = message_or_code
            full_msg = f"{code}: {detail}"
        else:
            full_msg = message_or_code
            code = full_msg.split(":", 1)[0].strip() if ":" in full_msg else "ERR_ADMISSION_VERIFICATION"
        super().__init__(full_msg)
        self.code = code


class ChallengeIssuanceError(TrialSignerError):
    """Raised when local-test challenge issuance or registry operations fail fail-closed."""
    def __init__(self, message_or_code: str, detail: Optional[str] = None) -> None:
        if detail is not None:
            code = message_or_code
            full_msg = f"{code}: {detail}"
        else:
            full_msg = message_or_code
            code = full_msg.split(":", 1)[0].strip() if ":" in full_msg else "ERR_CHALLENGE_ISSUANCE"
        super().__init__(full_msg)
        self.code = code


LocalTestChallengeIssuanceError = ChallengeIssuanceError


class ChallengeConsumptionError(TrialSignerError):
    """Raised when local-test operator consent verification or challenge consumption fails fail-closed."""
    def __init__(
        self,
        message_or_code: str,
        detail: Optional[str] = None,
        *,
        code: Optional[str] = None,
        primary_error: Optional[BaseException] = None,
        cleanup_error: Optional[BaseException] = None,
        cleanup_errors: Optional[List[BaseException]] = None,
    ) -> None:
        if detail is not None:
            c = message_or_code
            full_msg = f"{c}: {detail}"
        else:
            full_msg = message_or_code
            c = code or (full_msg.split(":", 1)[0].strip() if ":" in full_msg else "ERR_CHALLENGE_CONSUMPTION")
        super().__init__(full_msg)
        self.code = c
        self.primary_error = primary_error
        self.cleanup_error = cleanup_error
        if cleanup_errors is not None:
            self.cleanup_errors: List[BaseException] = list(cleanup_errors)
        elif cleanup_error is not None:
            self.cleanup_errors = [cleanup_error]
        else:
            self.cleanup_errors = []


LocalTestChallengeConsumptionError = ChallengeConsumptionError


class SignerLedgerError(TrialSignerError):
    """Base exception for all signer consumed-nonce ledger errors.

    Provides structured error code and preserves primary/cleanup errors when failures compound.
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        primary_error: Optional[BaseException] = None,
        cleanup_error: Optional[BaseException] = None,
        cleanup_errors: Optional[List[BaseException]] = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        elif ":" in message:
            self.code = message.split(":", 1)[0].strip()
        else:
            self.code = self.__class__.__name__

        self.primary_error = primary_error
        self.cleanup_error = cleanup_error
        if cleanup_errors is not None:
            self.cleanup_errors: List[BaseException] = list(cleanup_errors)
        elif cleanup_error is not None:
            self.cleanup_errors = [cleanup_error]
        else:
            self.cleanup_errors = []


class SignerNonceAlreadyConsumedError(SignerLedgerError):
    """Raised when a challenge_id, client_nonce, or approval pair has already been consumed."""
    pass


class RevocationManifestVerificationError(SignerLedgerError):
    """Raised when signed revocation manifest verification fails fail-closed."""
    pass


class RevocationEpochAnchorError(SignerLedgerError):
    """Base exception for all revocation epoch anchor errors."""
    pass


class RevocationRollbackError(RevocationEpochAnchorError):
    """Raised when incoming revocation manifest epoch is lower than existing epoch anchor."""
    pass


class RevocationEpochTamperingError(RevocationEpochAnchorError):
    """Raised when incoming revocation manifest epoch equals existing epoch anchor but has a different digest."""
    pass


class RevocationAnchorAbsentError(RevocationEpochAnchorError):
    """Raised when an existing revocation epoch anchor is required but absent."""
    pass


class SignerLedgerSecurityError(SignerLedgerError):
    """Raised when ledger directory or file permissions/symlinks/ownership violate security policy."""
    pass


class SignerLedgerCorruptionError(SignerLedgerError):
    """Raised when ledger contains malformed JSON, torn line, missing newline, duplicate keys, or invalid schema."""
    pass


class SignerLedgerDurabilityError(SignerLedgerError):
    """Raised when ledger write, fsync, flock, unlock, or close encounters OS/I/O durability errors."""
    pass


class SignerLedgerClosedError(SignerLedgerError):
    """Raised when ledger operation is attempted on a closed ledger."""
    pass


class SignerLedgerInitializationError(SignerLedgerError):
    """Raised when ledger initialization fails, preserving causes and cleanup failure state.

    WARNING: Descriptors listed in `ambiguous_close_descriptors` failed during close().
    Under POSIX semantics, an `os.close()` failure does NOT prove that the descriptor
    remains open; the outcome is strictly CLOSE_OUTCOME_UNKNOWN. Never retry os.close().
    """

    def __init__(
        self,
        message: str,
        *,
        primary_cause: BaseException,
        cleanup_error: Optional[BaseException] = None,
        cleanup_errors: Optional[List[BaseException]] = None,
        ambiguous_close_descriptors: Optional[List[Tuple[str, int, Exception, str]]] = None,
        ledger_instance: Optional[Any] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            primary_error=primary_cause,
            cleanup_error=cleanup_error,
            cleanup_errors=cleanup_errors,
        )
        self.primary_cause = primary_cause
        self.ambiguous_close_descriptors = ambiguous_close_descriptors or []
        self.ledger_instance = ledger_instance


# ==============================================================================
# Gate 3-I4: Director Approval Verification Core
# ==============================================================================

@dataclass(frozen=True)
class DirectorApprovalVerificationResult:
    """Immutable outcome of Director approval verification.

    Carries only booleans and identifiers confirming verification passed.
    Contains zero public key objects, private keys, authority tokens,
    execution authorizations, or dispatch capabilities.
    """
    verified: bool
    approval_id: str
    proposal_id: str
    plan_digest_sha256: str
    proposal_content_digest_sha256: str
    director_key_id: str

    def __bool__(self) -> bool:
        """Allows direct truthiness checks (e.g. `if result: ...`)."""
        return self.verified


def _safe_str_compare_digest(a: str, b: str) -> bool:
    """Constant-time string comparison that safely handles non-ASCII UTF-8 strings without raising TypeError."""
    if type(a) is not str or type(b) is not str:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _normalize_and_validate_ed25519_public_key(
    key: Any,
    error_cls: type[TrialSignerError],
    error_code: str,
    param_name: str,
) -> Ed25519PublicKey:
    """Validates that key is a genuine provider-backed Ed25519PublicKey, rejecting custom subclasses.

    Prevents custom subclasses from bypassing cryptographic signature verification via no-op verify() (I12-R1-01).
    Exports raw 32-byte public key and imports through provider, verifying exact concrete provider class identity.
    """
    if not isinstance(key, Ed25519PublicKey):
        raise error_cls(f"{error_code}: {param_name} must be Ed25519PublicKey, got {type(key).__name__}")
    try:
        raw_bytes = key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        if type(raw_bytes) is not bytes or len(raw_bytes) != 32:
            raise ValueError(f"{param_name} raw bytes invalid length: {len(raw_bytes) if type(raw_bytes) is bytes else type(raw_bytes)}")
        concrete_key = Ed25519PublicKey.from_public_bytes(raw_bytes)
    except Exception as exc:
        raise error_cls(f"{error_code}: {param_name} raw bytes export/import failed: {exc}") from exc
    if type(key) is not type(concrete_key):
        raise error_cls(f"{error_code}: {param_name} must be exact provider-backed Ed25519PublicKey, got custom subclass {type(key).__name__}")
    return concrete_key


def verify_director_approval(
    *,
    envelope: SignedApprovalEnvelope,
    proposal: EditPlanProposal,
    plan: TimelineExecutionPlan,
    public_key: Ed25519PublicKey,
    expected_director_key_id: str,
) -> DirectorApprovalVerificationResult:
    """Verifies a SignedApprovalEnvelope against proposal and plan data using Ed25519.

    Recomputes digests via source-owned contracts, verifies proposal-approval binding,
    and checks the Ed25519 signature over canonical RFC 8785 bytes without leaking key material.

    Args:
        envelope: Exact SignedApprovalEnvelope instance.
        proposal: Exact EditPlanProposal instance.
        plan: Exact TimelineExecutionPlan instance.
        public_key: Candidate Ed25519PublicKey instance for isolated verification.
        expected_director_key_id: Expected Director key identifier string.

    Returns:
        DirectorApprovalVerificationResult with verified=True if all invariants hold.

    Raises:
        DirectorApprovalVerificationError: If any type, format, digest, binding,
            key ID, or cryptographic check fails.
    """
    # 1. Exact nominal type gates (reject mappings, subclasses, and lookalikes)
    if type(envelope) is not SignedApprovalEnvelope:
        raise DirectorApprovalVerificationError(
            f"ERR_ENVELOPE_TYPE: envelope must be exact SignedApprovalEnvelope, got {type(envelope).__name__}"
        )

    if type(proposal) is not EditPlanProposal:
        raise DirectorApprovalVerificationError(
            f"ERR_PROPOSAL_TYPE: proposal must be exact EditPlanProposal, got {type(proposal).__name__}"
        )

    if type(plan) is not TimelineExecutionPlan:
        raise DirectorApprovalVerificationError(
            f"ERR_PLAN_TYPE: plan must be exact TimelineExecutionPlan, got {type(plan).__name__}"
        )

    public_key = _normalize_and_validate_ed25519_public_key(
        public_key, DirectorApprovalVerificationError, "ERR_PUBLIC_KEY_TYPE", "public_key"
    )

    if (
        type(expected_director_key_id) is not str
        or not expected_director_key_id
        or expected_director_key_id != expected_director_key_id.strip()
    ):
        raise DirectorApprovalVerificationError(
            f"ERR_EXPECTED_DIRECTOR_KEY_ID_FORMAT: expected_director_key_id must be non-empty without whitespace, got {expected_director_key_id!r}"
        )

    # 2. Key ID verification
    if not hmac.compare_digest(envelope.director_key_id, expected_director_key_id):
        raise DirectorApprovalVerificationError(
            f"ERR_DIRECTOR_KEY_ID_MISMATCH: envelope key_id '{envelope.director_key_id}' != expected '{expected_director_key_id}'"
        )

    # 3. Proposal-approval binding verification
    approval_record = envelope.approval_record
    if type(approval_record) is not AppOwnedApprovalRecord:
        raise DirectorApprovalVerificationError(
            f"ERR_APPROVAL_RECORD_TYPE: approval_record must be exact AppOwnedApprovalRecord, got {type(approval_record).__name__}"
        )

    try:
        binding_valid = verify_proposal_approval_binding(proposal, approval_record)
    except Exception as exc:
        raise DirectorApprovalVerificationError(
            f"ERR_PROPOSAL_APPROVAL_BINDING_EXCEPTION: binding verification raised {type(exc).__name__}: {exc}"
        ) from exc

    if not binding_valid:
        raise DirectorApprovalVerificationError(
            "ERR_PROPOSAL_APPROVAL_BINDING_FAILED: verify_proposal_approval_binding returned False"
        )

    # 4. Plan cross-checks: plan must match approval_id, proposal_id, and proposal_revision
    if not hmac.compare_digest(plan.approval_id, approval_record.approval_id):
        raise DirectorApprovalVerificationError(
            f"ERR_PLAN_APPROVAL_ID_MISMATCH: plan approval_id '{plan.approval_id}' != approval_record '{approval_record.approval_id}'"
        )

    if not hmac.compare_digest(plan.proposal_id, proposal.proposal_id):
        raise DirectorApprovalVerificationError(
            f"ERR_PLAN_PROPOSAL_ID_MISMATCH: plan proposal_id '{plan.proposal_id}' != proposal '{proposal.proposal_id}'"
        )

    if not hmac.compare_digest(plan.proposal_id, approval_record.proposal_id):
        raise DirectorApprovalVerificationError(
            f"ERR_RECORD_PROPOSAL_ID_MISMATCH: plan proposal_id '{plan.proposal_id}' != approval_record '{approval_record.proposal_id}'"
        )

    if not hmac.compare_digest(plan.proposal_revision, approval_record.proposal_revision):
        raise DirectorApprovalVerificationError(
            f"ERR_PLAN_PROPOSAL_REVISION_MISMATCH: plan proposal_revision '{plan.proposal_revision}' != approval_record '{approval_record.proposal_revision}'"
        )

    # 5. Digest recomputations using source-owned serializers
    try:
        recomputed_proposal_digest = compute_proposal_revision(proposal)
    except Exception as exc:
        raise DirectorApprovalVerificationError(
            f"ERR_PROPOSAL_DIGEST_RECOMPUTATION_FAILED: failed recomputing proposal revision: {exc}"
        ) from exc

    if not hmac.compare_digest(envelope.proposal_content_digest_sha256, recomputed_proposal_digest):
        raise DirectorApprovalVerificationError(
            "ERR_PROPOSAL_CONTENT_DIGEST_MISMATCH: envelope proposal_content_digest_sha256 != recomputed proposal digest"
        )

    if not hmac.compare_digest(approval_record.proposal_revision, recomputed_proposal_digest):
        raise DirectorApprovalVerificationError(
            "ERR_APPROVAL_RECORD_REVISION_MISMATCH: approval_record proposal_revision != recomputed proposal digest"
        )

    try:
        recomputed_plan_digest = compute_canonical_plan_digest(plan)
    except Exception as exc:
        raise DirectorApprovalVerificationError(
            f"ERR_PLAN_DIGEST_RECOMPUTATION_FAILED: failed recomputing plan digest: {exc}"
        ) from exc

    if not hmac.compare_digest(envelope.plan_digest_sha256, recomputed_plan_digest):
        raise DirectorApprovalVerificationError(
            "ERR_PLAN_DIGEST_MISMATCH: envelope plan_digest_sha256 != recomputed canonical plan digest"
        )

    # 6. Cryptographic signature verification over canonical RFC 8785 envelope bytes
    try:
        raw_sig = bytes.fromhex(envelope.director_signature)
    except ValueError as exc:
        raise DirectorApprovalVerificationError("ERR_DIRECTOR_SIGNATURE_HEX_INVALID: signature is not valid hex") from exc

    canonical_bytes = envelope.canonical_signing_payload_bytes()

    try:
        public_key.verify(raw_sig, canonical_bytes)
    except InvalidSignature as exc:
        raise DirectorApprovalVerificationError(
            "ERR_DIRECTOR_SIGNATURE_VERIFICATION_FAILED: signature does not verify under candidate public key"
        ) from exc
    except Exception as exc:
        raise DirectorApprovalVerificationError(
            f"ERR_DIRECTOR_SIGNATURE_ERROR: signature verification raised {type(exc).__name__}"
        ) from exc

    return DirectorApprovalVerificationResult(
        verified=True,
        approval_id=approval_record.approval_id,
        proposal_id=proposal.proposal_id,
        plan_digest_sha256=envelope.plan_digest_sha256,
        proposal_content_digest_sha256=envelope.proposal_content_digest_sha256,
        director_key_id=envelope.director_key_id,
    )


# ==============================================================================
# Gate 3-I5: Operator Consent Verification Core
# ==============================================================================


@dataclass(frozen=True)
class ActiveChallengeContext:
    """Immutable context representation of an active challenge issued to an operator.

    Holds the expected challenge token, client nonce, issued/expiry UTC timestamps,
    and consumed state. Optionally carries authoritative preallocated authorization_id
    and execution_id for canonical action payload cross-binding (I12-R3-01, I12-R4-01).
    """
    challenge_id: str
    client_nonce: str
    issued_at_iso: str
    expires_at_iso: str
    consumed: bool = False
    preallocated_authorization_id: Optional[str] = None
    preallocated_execution_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.challenge_id) is not str or not self.challenge_id:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"challenge_id must be a non-empty str, got {type(self.challenge_id).__name__}"
            )
        if type(self.client_nonce) is not str or not self.client_nonce:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"client_nonce must be a non-empty str, got {type(self.client_nonce).__name__}"
            )
        if type(self.issued_at_iso) is not str or not self.issued_at_iso:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"issued_at_iso must be a non-empty str, got {type(self.issued_at_iso).__name__}"
            )
        if type(self.expires_at_iso) is not str or not self.expires_at_iso:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"expires_at_iso must be a non-empty str, got {type(self.expires_at_iso).__name__}"
            )
        if type(self.consumed) is not bool:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"consumed must be a bool, got {type(self.consumed).__name__}"
            )
        if self.preallocated_authorization_id is not None:
            if (
                type(self.preallocated_authorization_id) is not str
                or not self.preallocated_authorization_id
                or self.preallocated_authorization_id != self.preallocated_authorization_id.strip()
            ):
                raise OperatorConsentVerificationError(
                    "ERR_CHALLENGE_CONTEXT_INVALID",
                    f"preallocated_authorization_id must be non-empty str without whitespace, got {self.preallocated_authorization_id!r}",
                )
        if self.preallocated_execution_id is not None:
            if (
                type(self.preallocated_execution_id) is not str
                or not self.preallocated_execution_id
                or self.preallocated_execution_id != self.preallocated_execution_id.strip()
            ):
                raise OperatorConsentVerificationError(
                    "ERR_CHALLENGE_CONTEXT_INVALID",
                    f"preallocated_execution_id must be non-empty str without whitespace, got {self.preallocated_execution_id!r}",
                )
        try:
            issued_dt = parse_iso_utc_timestamp(self.issued_at_iso, "challenge.issued_at_iso")
            expires_dt = parse_iso_utc_timestamp(self.expires_at_iso, "challenge.expires_at_iso")
        except Exception as exc:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID", f"invalid UTC timestamp in challenge context: {exc}"
            ) from exc

        if issued_dt > expires_dt:
            raise OperatorConsentVerificationError(
                "ERR_CHALLENGE_CONTEXT_INVALID",
                f"challenge issued_at ({self.issued_at_iso}) cannot be after expires_at ({self.expires_at_iso})",
            )


@dataclass(frozen=True)
class OperatorConsentVerificationContext:
    """Explicit, immutable verification context representing expected operator consent claims.

    Documentation of Trust Boundary:
    Context fields carrying expected digests and target claims are caller-prepared
    assertions in this isolated verification slice. They do not represent independently
    trusted provenance until resolved by an integrated signer in a later slice.
    """
    authoritative_operator_id: str
    expected_operator_key_id: str
    challenge: ActiveChallengeContext

    # Expected digests (asserted by caller or recomputed if DTOs provided)
    expected_approval_envelope_digest_sha256: str
    expected_plan_digest_sha256: str
    expected_action_payload_digest_sha256: str

    # Expected target & media claims
    expected_target_project_name: str
    expected_target_project_uid: str
    expected_target_timeline_name: str
    expected_target_timeline_uid: str
    expected_source_file_path: str
    expected_source_file_sha256: str
    expected_record_frame: int
    expected_duration_frames: int = 120
    expected_action_type: str = "INSERT_CLIP"
    expected_track_type: str = "video"
    expected_track_index: int = 1

    # Authoritative preallocated future identities for canonical action payload (I12-R3-01)
    expected_authorization_id: Optional[str] = None
    expected_execution_id: Optional[str] = None

    # Optional source-owned DTOs for authoritative recomputation
    approval_envelope: Optional[SignedApprovalEnvelope] = None
    execution_plan: Optional[TimelineExecutionPlan] = None

    def __post_init__(self) -> None:
        if (
            type(self.authoritative_operator_id) is not str
            or not self.authoritative_operator_id
            or self.authoritative_operator_id != self.authoritative_operator_id.strip()
        ):
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"authoritative_operator_id must be a non-empty str without whitespace padding, got {self.authoritative_operator_id!r}",
            )
        if (
            type(self.expected_operator_key_id) is not str
            or not self.expected_operator_key_id
            or self.expected_operator_key_id != self.expected_operator_key_id.strip()
        ):
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_operator_key_id must be a non-empty str without whitespace padding, got {self.expected_operator_key_id!r}",
            )
        if type(self.challenge) is not ActiveChallengeContext:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"challenge must be exact ActiveChallengeContext, got {type(self.challenge).__name__}",
            )

        # Inherit or strictly validate preallocated identities against challenge context (I12-R3-01, I12-R4-01)
        if self.expected_authorization_id is None:
            object.__setattr__(self, "expected_authorization_id", self.challenge.preallocated_authorization_id)
        elif self.expected_authorization_id != self.challenge.preallocated_authorization_id:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_authorization_id {self.expected_authorization_id!r} conflicts with challenge.preallocated_authorization_id {self.challenge.preallocated_authorization_id!r}",
            )

        if self.expected_execution_id is None:
            object.__setattr__(self, "expected_execution_id", self.challenge.preallocated_execution_id)
        elif self.expected_execution_id != self.challenge.preallocated_execution_id:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_execution_id {self.expected_execution_id!r} conflicts with challenge.preallocated_execution_id {self.challenge.preallocated_execution_id!r}",
            )

        if self.expected_authorization_id is not None:
            if (
                type(self.expected_authorization_id) is not str
                or not self.expected_authorization_id
                or self.expected_authorization_id != self.expected_authorization_id.strip()
            ):
                raise OperatorConsentVerificationError(
                    "ERR_CONTEXT_FIELD_INVALID",
                    f"expected_authorization_id must be a non-empty str without whitespace padding, got {self.expected_authorization_id!r}",
                )
        if self.expected_execution_id is not None:
            if (
                type(self.expected_execution_id) is not str
                or not self.expected_execution_id
                or self.expected_execution_id != self.expected_execution_id.strip()
            ):
                raise OperatorConsentVerificationError(
                    "ERR_CONTEXT_FIELD_INVALID",
                    f"expected_execution_id must be a non-empty str without whitespace padding, got {self.expected_execution_id!r}",
                )

        for name, val in [
            ("expected_approval_envelope_digest_sha256", self.expected_approval_envelope_digest_sha256),
            ("expected_plan_digest_sha256", self.expected_plan_digest_sha256),
            ("expected_action_payload_digest_sha256", self.expected_action_payload_digest_sha256),
            ("expected_source_file_sha256", self.expected_source_file_sha256),
        ]:
            if type(val) is not str or not RE_LOWER_HEX_64.fullmatch(val):
                raise OperatorConsentVerificationError(
                    "ERR_CONTEXT_FIELD_INVALID",
                    f"{name} must be a 64-character lowercase hex string, got {val!r}",
                )
        for name, val in [
            ("expected_target_project_name", self.expected_target_project_name),
            ("expected_target_project_uid", self.expected_target_project_uid),
            ("expected_target_timeline_name", self.expected_target_timeline_name),
            ("expected_target_timeline_uid", self.expected_target_timeline_uid),
            ("expected_source_file_path", self.expected_source_file_path),
        ]:
            if type(val) is not str or not val or val != val.strip():
                raise OperatorConsentVerificationError(
                    "ERR_CONTEXT_FIELD_INVALID",
                    f"{name} must be a non-empty str without whitespace padding, got {val!r}",
                )
        if type(self.expected_record_frame) is not int or isinstance(self.expected_record_frame, bool) or self.expected_record_frame < 0:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_record_frame must be an int >= 0, got {self.expected_record_frame!r}",
            )
        if type(self.expected_duration_frames) is not int or isinstance(self.expected_duration_frames, bool) or self.expected_duration_frames != 120:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_duration_frames must be strictly 120, got {self.expected_duration_frames!r}",
            )
        if type(self.expected_track_index) is not int or isinstance(self.expected_track_index, bool) or self.expected_track_index != 1:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_track_index must be strictly 1, got {self.expected_track_index!r}",
            )
        if type(self.expected_action_type) is not str or self.expected_action_type != "INSERT_CLIP":
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_action_type must be exact str 'INSERT_CLIP', got {self.expected_action_type!r} ({type(self.expected_action_type).__name__})",
            )
        if type(self.expected_track_type) is not str or self.expected_track_type != "video":
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"expected_track_type must be exact str 'video', got {self.expected_track_type!r} ({type(self.expected_track_type).__name__})",
            )
        if self.approval_envelope is not None and type(self.approval_envelope) is not SignedApprovalEnvelope:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"approval_envelope must be exact SignedApprovalEnvelope or None, got {type(self.approval_envelope).__name__}",
            )
        if self.execution_plan is not None and type(self.execution_plan) is not TimelineExecutionPlan:
            raise OperatorConsentVerificationError(
                "ERR_CONTEXT_FIELD_INVALID",
                f"execution_plan must be exact TimelineExecutionPlan or None, got {type(self.execution_plan).__name__}",
            )


@dataclass(frozen=True)
class OperatorConsentVerificationResult:
    """Immutable outcome of Operator consent verification.

    Carries only booleans, identifiers, and verified digests.
    Contains zero public key objects, private keys, authority tokens,
    execution authorizations, or dispatch capabilities.
    """
    verified: bool
    operator_id: str
    operator_key_id: str
    challenge_id: str
    client_nonce: str
    approval_envelope_digest_sha256: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    source_file_sha256: str

    def __bool__(self) -> bool:
        """Allows direct truthiness checks (e.g. `if result: ...`)."""
        return self.verified


def verify_operator_consent(
    *,
    envelope: SignedOperatorConsentEnvelope,
    context: OperatorConsentVerificationContext,
    public_key: Ed25519PublicKey,
    verification_time_utc: datetime,
) -> OperatorConsentVerificationResult:
    """Verifies a SignedOperatorConsentEnvelope against verification context and candidate public key.

    Implements the 20 checks + verification-instant challenge expiry gate defined in 07B7 R7 §3.2.
    Does not read the system clock; verification_time_utc must be explicitly supplied.
    Does not mutate challenges, consume nonces, load persistent keys, or issue execution authorization.

    Args:
        envelope: Exact SignedOperatorConsentEnvelope instance.
        context: Exact OperatorConsentVerificationContext instance.
        public_key: Candidate Ed25519PublicKey instance for isolated verification.
        verification_time_utc: Explicit timezone-aware UTC datetime of the verification instant.

    Returns:
        OperatorConsentVerificationResult with verified=True if all checks pass.

    Raises:
        OperatorConsentVerificationError: If any type, bounds, freshness, challenge expiry,
            binding, or cryptographic verification check fails.
    """
    # 0. Nominal type gates
    if type(envelope) is not SignedOperatorConsentEnvelope:
        raise OperatorConsentVerificationError(
            "ERR_ENVELOPE_TYPE", f"envelope must be exact SignedOperatorConsentEnvelope, got {type(envelope).__name__}"
        )

    if type(context) is not OperatorConsentVerificationContext:
        raise OperatorConsentVerificationError(
            "ERR_CONTEXT_TYPE", f"context must be exact OperatorConsentVerificationContext, got {type(context).__name__}"
        )

    public_key = _normalize_and_validate_ed25519_public_key(
        public_key, OperatorConsentVerificationError, "ERR_PUBLIC_KEY_TYPE", "public_key"
    )

    if (
        type(verification_time_utc) is not datetime
        or verification_time_utc.tzinfo is None
        or verification_time_utc.utcoffset() != timedelta(0)
    ):
        raise OperatorConsentVerificationError(
            "ERR_VERIFICATION_TIME_INVALID",
            f"verification_time_utc must be exact built-in timezone-aware UTC datetime, got {verification_time_utc!r} ({type(verification_time_utc).__name__})",
        )

    # 1. Challenge Active & Unconsumed Gate (07B7 §3.2 Check 1, §5.3, FIX-NEG-07)
    if context.challenge.consumed:
        raise OperatorConsentVerificationError(
            "ERR_SIGNER_NONCE_ALREADY_CONSUMED", "Active challenge has already been consumed"
        )

    # 2. Verification-Instant Challenge Expiry Gate (07B7 §3.2 Check 1, AUTH-SIGNER-R6-02, FIX-NEG-08)
    chal_issued_dt = parse_iso_utc_timestamp(context.challenge.issued_at_iso, "challenge.issued_at_iso")
    chal_exp_dt = parse_iso_utc_timestamp(context.challenge.expires_at_iso, "challenge.expires_at_iso")
    if verification_time_utc > chal_exp_dt:
        raise OperatorConsentVerificationError(
            "ERR_CHALLENGE_EXPIRED_AT_VERIFICATION",
            f"Verification instant {verification_time_utc.isoformat()} is strictly after challenge expiry {chal_exp_dt.isoformat()}",
        )

    # 3. Consent Timestamp Bounds Gate (07B7 §3.2 Check 1)
    consent_dt = parse_iso_utc_timestamp(envelope.consent_timestamp_iso, "consent_timestamp_iso")
    if consent_dt < chal_issued_dt or consent_dt > chal_exp_dt:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_TIMESTAMP_OUT_OF_BOUNDS",
            f"Consent timestamp {envelope.consent_timestamp_iso} is outside challenge window [{context.challenge.issued_at_iso}, {context.challenge.expires_at_iso}]",
        )

    # 4. Bounded Freshness & Clock Skew Gate (07B7 §3.2 Check 2, FIX-NEG-01, FIX-NEG-02)
    delta_t = (verification_time_utc - consent_dt).total_seconds()
    if delta_t < -5.0:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_FUTURE_TIMESTAMP",
            f"Consent timestamp is in the future beyond allowed 5s clock skew (delta_t={delta_t:.3f}s)",
        )
    if delta_t > 120.0:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_EXPIRED",
            f"Consent timestamp is stale beyond allowed 120s freshness window (delta_t={delta_t:.3f}s)",
        )

    # 5. Operator Identity & Key Binding (07B7 §3.2 Check 3, FIX-NEG-06)
    if envelope.operator_id != context.authoritative_operator_id:
        raise OperatorConsentVerificationError(
            "ERR_OPERATOR_ID_MISMATCH",
            f"Operator ID '{envelope.operator_id}' != authoritative '{context.authoritative_operator_id}'",
        )
    if not hmac.compare_digest(envelope.operator_key_id, context.expected_operator_key_id):
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_KEY_MISMATCH",
            f"Operator key ID '{envelope.operator_key_id}' != expected '{context.expected_operator_key_id}'",
        )

    # 6. Exact String Literals (07B7 §3.2 Check 5)
    if envelope.confirmation_phrase != "CONFIRM_CANARY_TRIAL_EXECUTION":
        raise OperatorConsentVerificationError(
            "ERR_CONFIRMATION_PHRASE_MISMATCH",
            f"Confirmation phrase mismatch: {envelope.confirmation_phrase!r}",
        )
    if envelope.freshness_attestation != "OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED":
        raise OperatorConsentVerificationError(
            "ERR_FRESHNESS_ATTESTATION_MISMATCH",
            f"Freshness attestation mismatch: {envelope.freshness_attestation!r}",
        )

    # 7. Challenge Token and Client Nonce Binding (07B7 §3.2 Check 6)
    if envelope.challenge_id != context.challenge.challenge_id:
        raise OperatorConsentVerificationError(
            "ERR_CHALLENGE_ID_MISMATCH",
            f"Challenge ID '{envelope.challenge_id}' != challenge context '{context.challenge.challenge_id}'",
        )
    if envelope.client_nonce != context.challenge.client_nonce:
        raise OperatorConsentVerificationError(
            "ERR_CLIENT_NONCE_MISMATCH",
            f"Client nonce '{envelope.client_nonce}' != challenge context '{context.challenge.client_nonce}'",
        )

    # 8. Approval Envelope Digest Recomputation & Binding (07B7 §3.2 Check 7)
    if context.approval_envelope is not None:
        recomputed_approval_digest = context.approval_envelope.compute_envelope_digest()
        if not hmac.compare_digest(context.expected_approval_envelope_digest_sha256.lower(), recomputed_approval_digest.lower()):
            raise OperatorConsentVerificationError(
                "ERR_APPROVAL_ENVELOPE_DIGEST_MISMATCH",
                "Context expected approval digest does not match recomputed approval envelope digest",
            )
        if not hmac.compare_digest(envelope.approval_envelope_digest_sha256.lower(), recomputed_approval_digest.lower()):
            raise OperatorConsentVerificationError(
                "ERR_APPROVAL_ENVELOPE_DIGEST_MISMATCH",
                "Envelope approval digest does not match recomputed approval envelope digest",
            )
    else:
        if not hmac.compare_digest(envelope.approval_envelope_digest_sha256.lower(), context.expected_approval_envelope_digest_sha256.lower()):
            raise OperatorConsentVerificationError(
                "ERR_APPROVAL_ENVELOPE_DIGEST_MISMATCH",
                "Envelope approval digest does not match context expected approval digest",
            )

    # 9. Plan Digest Recomputation & Binding (07B7 §3.2 Check 8)
    if context.execution_plan is not None:
        recomputed_plan_digest = compute_canonical_plan_digest(context.execution_plan)
        if not hmac.compare_digest(context.expected_plan_digest_sha256.lower(), recomputed_plan_digest.lower()):
            raise OperatorConsentVerificationError(
                "ERR_PLAN_DIGEST_MISMATCH",
                "Context expected plan digest does not match recomputed canonical plan digest",
            )
        if not hmac.compare_digest(envelope.plan_digest_sha256.lower(), recomputed_plan_digest.lower()):
            raise OperatorConsentVerificationError(
                "ERR_PLAN_DIGEST_MISMATCH",
                "Envelope plan digest does not match recomputed canonical plan digest",
            )
    else:
        if not hmac.compare_digest(envelope.plan_digest_sha256.lower(), context.expected_plan_digest_sha256.lower()):
            raise OperatorConsentVerificationError(
                "ERR_PLAN_DIGEST_MISMATCH",
                "Envelope plan digest does not match context expected plan digest",
            )

    # 10. Action Payload Digest Binding (07B7 §3.2 Check 9)
    if not hmac.compare_digest(envelope.action_payload_digest_sha256.lower(), context.expected_action_payload_digest_sha256.lower()):
        raise OperatorConsentVerificationError(
            "ERR_ACTION_PAYLOAD_DIGEST_MISMATCH",
            "Envelope action payload digest does not match context expected action payload digest",
        )

    # 11. Target Project Name & UID Equality (07B7 §3.2 Checks 10, 11)
    if envelope.target_project_name != context.expected_target_project_name:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Target project name '{envelope.target_project_name}' != expected '{context.expected_target_project_name}'",
        )
    if envelope.target_project_uid != context.expected_target_project_uid:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Target project UID '{envelope.target_project_uid}' != expected '{context.expected_target_project_uid}'",
        )

    # 12. Target Timeline Name & UID Equality (07B7 §3.2 Checks 12, 13, FIX-NEG-04)
    if envelope.target_timeline_name != context.expected_target_timeline_name:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Target timeline name '{envelope.target_timeline_name}' != expected '{context.expected_target_timeline_name}'",
        )
    if envelope.target_timeline_uid != context.expected_target_timeline_uid:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Target timeline UID '{envelope.target_timeline_uid}' != expected '{context.expected_target_timeline_uid}'",
        )

    # 13. Source Media Path & SHA-256 Equality (07B7 §3.2 Checks 14, 15, FIX-NEG-03)
    if envelope.source_file_path != context.expected_source_file_path:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Source file path '{envelope.source_file_path}' != expected '{context.expected_source_file_path}'",
        )
    if not hmac.compare_digest(envelope.source_file_sha256.lower(), context.expected_source_file_sha256.lower()):
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            "Source file SHA-256 digest mismatch",
        )

    # 14. Action Type & Geometry Equality (07B7 §3.2 Checks 16-20, FIX-NEG-05)
    if envelope.action_type != context.expected_action_type:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Action type '{envelope.action_type}' != expected '{context.expected_action_type}'",
        )
    if envelope.track_type != context.expected_track_type:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Track type '{envelope.track_type}' != expected '{context.expected_track_type}'",
        )
    if envelope.track_index != context.expected_track_index:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Track index {envelope.track_index} != expected {context.expected_track_index}",
        )
    if envelope.record_frame != context.expected_record_frame:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Record frame {envelope.record_frame} != expected {context.expected_record_frame}",
        )
    if envelope.duration_frames != context.expected_duration_frames:
        raise OperatorConsentVerificationError(
            "ERR_CONSENT_MUTATION_MISMATCH",
            f"Duration frames {envelope.duration_frames} != expected {context.expected_duration_frames}",
        )

    # 15. Cryptographic Ed25519 Signature Verification (07B7 §3.2 Check 4)
    try:
        raw_sig = bytes.fromhex(envelope.operator_signature)
    except ValueError as exc:
        raise OperatorConsentVerificationError(
            "ERR_OPERATOR_SIGNATURE_INVALID", "operator_signature is not valid hex"
        ) from exc

    canonical_signing_bytes = envelope.canonical_signing_payload_bytes()

    try:
        public_key.verify(raw_sig, canonical_signing_bytes)
    except InvalidSignature as exc:
        raise OperatorConsentVerificationError(
            "ERR_OPERATOR_SIGNATURE_INVALID",
            "Operator digital signature does not verify under candidate public key",
        ) from exc
    except Exception as exc:
        raise OperatorConsentVerificationError(
            "ERR_OPERATOR_SIGNATURE_ERROR",
            f"Operator signature verification raised {type(exc).__name__}",
        ) from exc

    return OperatorConsentVerificationResult(
        verified=True,
        operator_id=envelope.operator_id,
        operator_key_id=envelope.operator_key_id,
        challenge_id=envelope.challenge_id,
        client_nonce=envelope.client_nonce,
        approval_envelope_digest_sha256=envelope.approval_envelope_digest_sha256,
        plan_digest_sha256=envelope.plan_digest_sha256,
        action_payload_digest_sha256=envelope.action_payload_digest_sha256,
        target_project_uid=envelope.target_project_uid,
        target_timeline_uid=envelope.target_timeline_uid,
        source_file_sha256=envelope.source_file_sha256,
    )


class TrialAuthorizationSignerService:
    """Trial Authorization Signer Service boundary.

    In Gate 3-I4 / Gate 3-I5, exposes pure verification cores for:
    - Director approval envelope (verify_director_approval)
    - Operator consent envelope (verify_operator_consent)

    Full service daemon, durable nonce ledger, and authorization token issuance
    are reserved for subsequent slices.
    """

    @staticmethod
    def verify_director_approval(
        *,
        envelope: SignedApprovalEnvelope,
        proposal: EditPlanProposal,
        plan: TimelineExecutionPlan,
        public_key: Ed25519PublicKey,
        expected_director_key_id: str,
    ) -> DirectorApprovalVerificationResult:
        """Delegates to pure verify_director_approval function."""
        return verify_director_approval(
            envelope=envelope,
            proposal=proposal,
            plan=plan,
            public_key=public_key,
            expected_director_key_id=expected_director_key_id,
        )

    @staticmethod
    def verify_operator_consent(
        *,
        envelope: SignedOperatorConsentEnvelope,
        context: OperatorConsentVerificationContext,
        public_key: Ed25519PublicKey,
        verification_time_utc: datetime,
    ) -> OperatorConsentVerificationResult:
        """Delegates to pure verify_operator_consent function."""
        return verify_operator_consent(
            envelope=envelope,
            context=context,
            public_key=public_key,
            verification_time_utc=verification_time_utc,
        )

    @staticmethod
    def verify_signed_revocation_manifest(
        raw_manifest_bytes: bytes,
        issuer_public_key: Ed25519PublicKey,
        verification_time_utc: datetime,
    ) -> RevocationManifestVerificationResult:
        """Delegates to pure verify_signed_revocation_manifest function."""
        return verify_signed_revocation_manifest(
            raw_manifest_bytes=raw_manifest_bytes,
            issuer_public_key=issuer_public_key,
            verification_time_utc=verification_time_utc,
        )

    @staticmethod
    def assemble_canonical_action_payload_for_admission(
        *,
        action: PlannedTimelineAction,
        execution_plan: TimelineExecutionPlan,
        authorization_id: str,
        execution_id: str,
    ) -> Dict[str, Any]:
        """Delegates to pure assemble_canonical_action_payload_for_admission function."""
        return assemble_canonical_action_payload_for_admission(
            action=action,
            execution_plan=execution_plan,
            authorization_id=authorization_id,
            execution_id=execution_id,
        )

    @staticmethod
    def compute_canonical_action_payload_digest_for_admission(
        *,
        action: PlannedTimelineAction,
        execution_plan: TimelineExecutionPlan,
        authorization_id: str,
        execution_id: str,
    ) -> str:
        """Delegates to pure compute_canonical_action_payload_digest_for_admission function."""
        return compute_canonical_action_payload_digest_for_admission(
            action=action,
            execution_plan=execution_plan,
            authorization_id=authorization_id,
            execution_id=execution_id,
        )

    @staticmethod
    def verify_trial_authorization_admission_for_local_test(
        *,
        approval_envelope: SignedApprovalEnvelope,
        proposal: EditPlanProposal,
        execution_plan: TimelineExecutionPlan,
        director_public_key: Ed25519PublicKey,
        expected_director_key_id: str,
        operator_consent_envelope: SignedOperatorConsentEnvelope,
        operator_context: OperatorConsentVerificationContext,
        operator_public_key: Ed25519PublicKey,
        revocation_trust_directory: str | Path,
        revocation_registry_directory: str | Path,
        verification_time_utc: datetime,
    ) -> "TrialAuthorizationAdmissionEvidence":
        """Delegates to pure verify_trial_authorization_admission_for_local_test function."""
        return verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=execution_plan,
            director_public_key=director_public_key,
            expected_director_key_id=expected_director_key_id,
            operator_consent_envelope=operator_consent_envelope,
            operator_context=operator_context,
            operator_public_key=operator_public_key,
            revocation_trust_directory=revocation_trust_directory,
            revocation_registry_directory=revocation_registry_directory,
            verification_time_utc=verification_time_utc,
        )

    def __repr__(self) -> str:
        return "<TrialAuthorizationSignerService state=GATE3_I12_AUTHORIZATION_ADMISSION_CROSS_BINDING_CORE>"

    def __str__(self) -> str:
        return self.__repr__()


# ==============================================================================
# Gate 3-I6: Durable Signer Nonce Ledger Core
# ==============================================================================

CONSUMED_NONCE_RECORD_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "challenge_id",
    "client_nonce",
    "approval_record_id",
    "proposal_revision",
    "consumed_at_iso",
})


@dataclass(frozen=True)
class ConsumedAuthorizationNonceRecord:
    """Immutable record tracking a single consumed authorization nonce/approval pair.

    Enforces schema version 1 and exact built-in type gates.
    Contains zero private keys, public keys, signatures, envelopes, or authority tokens.
    """
    schema_version: int = 1
    challenge_id: str = ""
    client_nonce: str = ""
    approval_record_id: str = ""
    proposal_revision: str = ""
    consumed_at_iso: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise SignerLedgerError(
                f"ERR_RECORD_FIELD_INVALID: schema_version must be exact int 1, got {self.schema_version!r}"
            )
        for field_name in ("challenge_id", "client_nonce", "approval_record_id", "proposal_revision"):
            val = getattr(self, field_name)
            if type(val) is not str or not val or val != val.strip():
                raise SignerLedgerError(
                    f"ERR_RECORD_FIELD_INVALID: {field_name} must be a non-empty string without whitespace padding, got {val!r}"
                )
        if type(self.consumed_at_iso) is not str or not self.consumed_at_iso or self.consumed_at_iso != self.consumed_at_iso.strip():
            raise SignerLedgerError(
                f"ERR_RECORD_FIELD_INVALID: consumed_at_iso must be a non-empty string without whitespace padding, got {self.consumed_at_iso!r}"
            )
        try:
            parse_iso_utc_timestamp(self.consumed_at_iso, "consumed_at_iso")
        except Exception as e:
            raise SignerLedgerError(f"ERR_RECORD_FIELD_INVALID: invalid consumed_at_iso: {e}") from e

    def to_dict(self) -> Dict[str, Any]:
        """Serializes record to strict dictionary with exact 6 fields."""
        return {
            "schema_version": self.schema_version,
            "challenge_id": self.challenge_id,
            "client_nonce": self.client_nonce,
            "approval_record_id": self.approval_record_id,
            "proposal_revision": self.proposal_revision,
            "consumed_at_iso": self.consumed_at_iso,
        }

    def canonical_bytes(self) -> bytes:
        """Returns RFC 8785 canonical JSON UTF-8 bytes for the record without trailing newline."""
        return canonical_json_bytes(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConsumedAuthorizationNonceRecord:
        """Constructs and validates record from dictionary enforcing exact 6 keys."""
        if not isinstance(data, dict):
            raise SignerLedgerError(f"ERR_RECORD_SCHEMA_INVALID: expected dict, got {type(data).__name__}")
        if frozenset(data.keys()) != CONSUMED_NONCE_RECORD_KEYS:
            raise SignerLedgerError(
                f"ERR_RECORD_SCHEMA_INVALID: record must contain exact 6 keys {sorted(CONSUMED_NONCE_RECORD_KEYS)}, got {sorted(data.keys())}"
            )
        return cls(
            schema_version=data["schema_version"],
            challenge_id=data["challenge_id"],
            client_nonce=data["client_nonce"],
            approval_record_id=data["approval_record_id"],
            proposal_revision=data["proposal_revision"],
            consumed_at_iso=data["consumed_at_iso"],
        )


class SignerConsumedNonceLedger:
    """Durable, append-only consumed-nonce ledger primitive for TrialAuthorizationSignerService.

    Guarantees:
    - Anchored directory descriptor with O_NOFOLLOW | O_CLOEXEC flags.
    - Support for descriptor-backed directory mode (directory_fd): trusts the caller to have
      already performed trusted-root and no-follow ancestry validation (e.g., via
      _open_anchor_dir_under_temp_root). Duplicates caller's descriptor into ledger-owned state,
      retains the duplicated descriptor for the full ledger lifetime, and pins the directory inode
      against TOCTOU pathname replacement or symlink diversion attacks. Does not alter or close
      caller's descriptor.
    - Strict filesystem permissions (0700 dir, 0600 file) and current process UID ownership.
    - Mutual exclusion via process-local threading lock + OS fcntl.flock(LOCK_EX).
    - Multi-dimensional collision detection: rejects any duplicate challenge_id, client_nonce,
      or (approval_record_id, proposal_revision) pair.
    - Durable persistence: write-all loop (EINTR retries) followed by fsync(ledger_fd).
    - Fail-closed startup and append validation: rejects duplicate JSON keys, missing final newline,
      or torn lines with ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED (zero silent truncation or repair).
    - POSIX single-close semantics on all descriptors.
    """

    def __init__(
        self,
        ledger_dir: Optional[Union[str, Path]] = None,
        *,
        directory_fd: Optional[int] = None,
        filename: str = "signer_consumed_nonces.jsonl",
        create: bool = True,
    ) -> None:
        # I6-R4-01: Mutually exclusive directory input validation
        if ledger_dir is not None and directory_fd is not None:
            raise SignerLedgerSecurityError(
                "ERR_AMBIGUOUS_DIRECTORY_INPUT: cannot supply both ledger_dir and directory_fd"
            )
        if ledger_dir is None and directory_fd is None:
            raise SignerLedgerError(
                "ERR_MISSING_DIRECTORY_INPUT: either ledger_dir or directory_fd must be provided"
            )

        # I6-R1-01: Validate filename as an exact safe single basename without path separators or traversal
        if (
            type(filename) is not str
            or not filename
            or filename != filename.strip()
            or "/" in filename
            or "\\" in filename
            or filename in (".", "..")
            or "\0" in filename
            or os.path.basename(filename) != filename
        ):
            raise SignerLedgerSecurityError(
                f"ERR_INVALID_LEDGER_FILENAME: filename must be a safe single basename without path separators or traversal, got {filename!r}"
            )

        self._filename = filename
        self._lock = threading.RLock()
        self._dir_fd: Optional[int] = None
        self._ledger_fd: Optional[int] = None
        self._closed: bool = False
        self._lock_corrupted: bool = False
        self._cleanup_failures: List[Tuple[str, int, Exception, str]] = []

        self._file_offset: int = 0
        self._challenge_ids: Set[str] = set()
        self._client_nonces: Set[str] = set()
        self._approval_pairs: Set[Tuple[str, str]] = set()

        try:
            if directory_fd is not None:
                # I6-R4-02: Descriptor-backed directory mode
                # Reject bool, non-int, negative int, subclasses
                if isinstance(directory_fd, bool) or type(directory_fd) is not int or directory_fd < 0:
                    raise SignerLedgerSecurityError(
                        f"ERR_INVALID_DIRECTORY_FD: directory_fd must be an exact non-negative int, got {type(directory_fd).__name__}({directory_fd!r})"
                    )

                self._ledger_dir: Optional[str] = None

                # Duplicate caller's descriptor into ledger-owned state before use; never close or mutate caller's FD
                try:
                    self._dir_fd = os.dup(directory_fd)
                except OSError as de:
                    raise SignerLedgerSecurityError(
                        f"ERR_INVALID_DIRECTORY_FD: invalid or closed descriptor {directory_fd}: {de}"
                    ) from de

                # Set non-inheritable / CLOEXEC on ledger-owned descriptor
                try:
                    os.set_inheritable(self._dir_fd, False)
                    fcntl.fcntl(self._dir_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
                except OSError as fe:
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_CANNOT_OPEN: failed setting CLOEXEC on duplicated directory descriptor: {fe}"
                    ) from fe

                # Validate owned directory descriptor with fstat (no path-based calls)
                try:
                    dir_st = os.fstat(self._dir_fd)
                except OSError as se:
                    raise SignerLedgerSecurityError(
                        f"ERR_INVALID_DIRECTORY_FD: failed fstat on directory descriptor: {se}"
                    ) from se

                if not stat.S_ISDIR(dir_st.st_mode):
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_NOT_DIR: directory descriptor {directory_fd} is not a directory"
                    )
                if hasattr(os, "getuid") and dir_st.st_uid != os.getuid():
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_DIR_INVALID_OWNER: directory descriptor uid {dir_st.st_uid} != current uid {os.getuid()}"
                    )
                if (dir_st.st_mode & 0o777) != 0o700:
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_DIR_INSECURE_PERMS: directory descriptor mode must be strictly 0700, got {oct(dir_st.st_mode & 0o777)}"
                    )

            else:
                # Path mode (existing behavior preserved)
                if not isinstance(ledger_dir, (str, Path)) or not str(ledger_dir).strip():
                    raise SignerLedgerError("ERR_INVALID_LEDGER_DIR: ledger_dir must be a non-empty string or Path")

                self._ledger_dir = os.path.abspath(str(ledger_dir))

                # I6-R1-02: Atomic directory creation without silently repairing existing directory permissions
                if create and not os.path.exists(self._ledger_dir):
                    try:
                        os.makedirs(self._ledger_dir, mode=0o700, exist_ok=False)
                    except FileExistsError:
                        pass
                    except OSError as ce:
                        raise SignerLedgerError(f"ERR_CANNOT_CREATE_LEDGER_DIR: failed creating {self._ledger_dir}: {ce}") from ce

                if os.path.islink(self._ledger_dir):
                    raise SignerLedgerSecurityError(f"ERR_DIR_SYMLINK_DETECTED: ledger_dir '{self._ledger_dir}' is a symlink")

                dir_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )

                try:
                    self._dir_fd = os.open(self._ledger_dir, dir_flags)
                except OSError as e:
                    if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and os.path.islink(self._ledger_dir)):
                        raise SignerLedgerSecurityError(f"ERR_DIR_SYMLINK_DETECTED: ledger_dir '{self._ledger_dir}' is a symlink: {e}") from e
                    if e.errno == errno.ENOTDIR:
                        raise SignerLedgerSecurityError(f"ERR_LEDGER_NOT_DIR: ledger_dir '{self._ledger_dir}' is not a directory: {e}") from e
                    raise SignerLedgerError(f"ERR_LEDGER_CANNOT_OPEN: cannot open ledger directory '{self._ledger_dir}': {e}") from e

                # Validate directory descriptor
                dir_st = os.fstat(self._dir_fd)
                if not stat.S_ISDIR(dir_st.st_mode):
                    raise SignerLedgerSecurityError(f"ERR_LEDGER_NOT_DIR: ledger_dir '{self._ledger_dir}' is not a directory")
                if hasattr(os, "getuid") and dir_st.st_uid != os.getuid():
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_DIR_INVALID_OWNER: ledger_dir uid {dir_st.st_uid} != current uid {os.getuid()}"
                    )
                # I6-R1-02: Require directory mode to be strictly 0700 (owner rwx only)
                if (dir_st.st_mode & 0o777) != 0o700:
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_DIR_INSECURE_PERMS: ledger_dir mode must be strictly 0700, got {oct(dir_st.st_mode & 0o777)}"
                    )

            # I6-R1-03: Atomic exclusive creation first; on EEXIST open existing with O_NOFOLLOW
            created_by_us = False
            create_flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                self._ledger_fd = os.open(self._filename, create_flags, 0o600, dir_fd=self._dir_fd)
                created_by_us = True
            except OSError as le:
                if le.errno == errno.EEXIST:
                    open_flags = (
                        os.O_RDWR
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    try:
                        self._ledger_fd = os.open(self._filename, open_flags, dir_fd=self._dir_fd)
                    except OSError as oe:
                        if oe.errno == errno.ELOOP:
                            raise SignerLedgerSecurityError(
                                f"ERR_LEDGER_SYMLINK_DETECTED: ledger file '{self._filename}' is a symlink: {oe}"
                            ) from oe
                        if oe.errno in (errno.EISDIR, errno.ENOTDIR):
                            raise SignerLedgerSecurityError(
                                f"ERR_LEDGER_NOT_REGULAR: ledger file '{self._filename}' is not a regular file: {oe}"
                            ) from oe
                        raise SignerLedgerError(f"ERR_LEDGER_OPEN_FAILED: failed opening existing ledger file: {oe}") from oe
                elif le.errno in (errno.EISDIR, errno.ENOTDIR):
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_NOT_REGULAR: ledger file '{self._filename}' is not a regular file: {le}"
                    ) from le
                elif le.errno == errno.ELOOP:
                    raise SignerLedgerSecurityError(
                        f"ERR_LEDGER_SYMLINK_DETECTED: ledger file '{self._filename}' is a symlink: {le}"
                    ) from le
                else:
                    raise SignerLedgerError(f"ERR_LEDGER_OPEN_FAILED: failed opening ledger file: {le}") from le

            # Validate ledger file descriptor
            ledger_st = os.fstat(self._ledger_fd)
            if not stat.S_ISREG(ledger_st.st_mode):
                raise SignerLedgerSecurityError(f"ERR_LEDGER_NOT_REGULAR: ledger file '{self._filename}' is not a regular file")
            if ledger_st.st_nlink != 1:
                raise SignerLedgerSecurityError(
                    f"ERR_LEDGER_HARD_LINK_DETECTED: ledger file '{self._filename}' has link count {ledger_st.st_nlink} != 1"
                )
            if hasattr(os, "getuid") and ledger_st.st_uid != os.getuid():
                raise SignerLedgerSecurityError(
                    f"ERR_LEDGER_FILE_INVALID_OWNER: ledger uid {ledger_st.st_uid} != {os.getuid()}"
                )
            if (ledger_st.st_mode & 0o777) != 0o600:
                raise SignerLedgerSecurityError(
                    f"ERR_LEDGER_FILE_INSECURE_PERMS: ledger file mode must be strictly 0600, got {oct(ledger_st.st_mode & 0o777)}"
                )

            # If created by this instance, fsync directory descriptor to ensure durable entry
            if created_by_us:
                try:
                    os.fsync(self._dir_fd)
                except OSError as fe:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_DIR_FSYNC_FAILED: failed fsyncing ledger directory: {fe}") from fe

            # Initial scan and parse under exclusive lock
            try:
                fcntl.flock(self._ledger_fd, fcntl.LOCK_EX)
            except OSError as fe:
                raise SignerLedgerDurabilityError(f"ERR_LEDGER_LOCK_FAILED: failed acquiring initial file lock: {fe}") from fe

            primary_scan_exc: Optional[BaseException] = None
            try:
                self._sync_and_scan_locked()
            except BaseException as pe:
                primary_scan_exc = pe
                raise
            finally:
                unlock_err: Optional[SignerLedgerDurabilityError] = None
                try:
                    fcntl.flock(self._ledger_fd, fcntl.LOCK_UN)
                except OSError as ue:
                    self._lock_corrupted = True
                    self._closed = True
                    unlock_err = SignerLedgerDurabilityError(
                        f"ERR_LEDGER_UNLOCK_FAILED: failed releasing initial file lock: {ue}",
                        code="ERR_LEDGER_UNLOCK_FAILED",
                    )

                # I6-R2-01: Preserve primary initialization error and structured unlock failure without masking
                if primary_scan_exc is not None and unlock_err is not None:
                    self._lock_corrupted = True
                    self._closed = True
                    err_msg = f"{primary_scan_exc}; cleanup failure: {unlock_err}"
                    err = SignerLedgerDurabilityError(
                        err_msg,
                        code="ERR_LEDGER_UNLOCK_FAILED",
                        primary_error=primary_scan_exc,
                        cleanup_error=unlock_err,
                        cleanup_errors=[unlock_err],
                    )
                    err.__cause__ = primary_scan_exc
                    raise err from primary_scan_exc

                if unlock_err is not None:
                    raise unlock_err

        except BaseException as init_exc:
            cleanup_exc: Optional[Exception] = None
            try:
                self.close()
            except Exception as ce:
                cleanup_exc = ce

            if cleanup_exc is not None:
                msg = (
                    f"ERR_LEDGER_INIT_FAILED_WITH_CLEANUP_ERROR: initialization failed with {init_exc}; "
                    f"subsequent descriptor cleanup failed: {cleanup_exc}"
                )
                err = SignerLedgerInitializationError(
                    msg,
                    primary_cause=init_exc,
                    cleanup_error=cleanup_exc,
                    ambiguous_close_descriptors=list(self._cleanup_failures),
                    ledger_instance=self,
                )
                err.__cause__ = init_exc
                raise err from init_exc
            raise

    @classmethod
    def from_directory_fd(
        cls,
        directory_fd: int,
        *,
        filename: str = "signer_consumed_nonces.jsonl",
    ) -> "SignerConsumedNonceLedger":
        """Instantiates SignerConsumedNonceLedger from an already open, caller-verified directory descriptor.

        Trust boundary note: This method trusts the caller to have already validated that directory_fd
        is physically confined beneath a trusted root (e.g. via descriptor-anchored ancestry checks).
        This primitive duplicates and retains its own directory descriptor and performs all subsequent
        ledger operations relative to that descriptor, pinning the inode against path-swap attacks.
        """
        return cls(directory_fd=directory_fd, filename=filename)

    @property
    def ledger_dir(self) -> Optional[str]:
        """Returns the verified absolute path of the ledger directory, or None in descriptor mode."""
        return self._ledger_dir

    @property
    def directory_fd(self) -> Optional[int]:
        """Returns the internal directory descriptor retained by this ledger instance, or None if closed."""
        return self._dir_fd

    @property
    def cleanup_failures(self) -> List[Tuple[str, int, Exception, str]]:
        """Exposes diagnostic information about descriptors whose close() encountered errors."""
        return list(self._cleanup_failures)

    def _ensure_open_locked(self) -> None:
        """Checks open state; caller MUST already hold self._lock."""
        if self._closed or self._dir_fd is None or self._ledger_fd is None:
            raise SignerLedgerClosedError("ERR_LEDGER_CLOSED: operation attempted on closed SignerConsumedNonceLedger")
        if self._lock_corrupted:
            raise SignerLedgerError("ERR_LEDGER_LOCK_CORRUPTED: ledger lock state is corrupted; ledger is latched closed")

    def _sync_and_scan_locked(self) -> None:
        """Scans on-disk tail from self._file_offset to EOF, validating records strictly."""
        if self._ledger_fd is None:
            raise SignerLedgerClosedError("ERR_LEDGER_CLOSED: ledger descriptor is None")

        st = os.fstat(self._ledger_fd)
        if st.st_size < self._file_offset:
            raise SignerLedgerCorruptionError(
                f"ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: ledger file shrank from {self._file_offset} to {st.st_size}"
            )
        if st.st_size == self._file_offset:
            return

        os.lseek(self._ledger_fd, self._file_offset, os.SEEK_SET)
        to_read = st.st_size - self._file_offset
        chunks: List[bytes] = []
        read_total = 0
        while read_total < to_read:
            try:
                chunk = os.read(self._ledger_fd, to_read - read_total)
                if not chunk:
                    raise SignerLedgerCorruptionError(
                        "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: unexpected EOF while reading ledger tail"
                    )
                chunks.append(chunk)
                read_total += len(chunk)
            except OSError as oe:
                if oe.errno == errno.EINTR:
                    continue
                raise SignerLedgerDurabilityError(f"ERR_LEDGER_READ_FAILED: failed reading ledger tail: {oe}") from oe

        tail_bytes = b"".join(chunks)
        if not tail_bytes.endswith(b"\n"):
            raise SignerLedgerCorruptionError(
                "ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: missing final newline at end of ledger"
            )

        lines = tail_bytes[:-1].split(b"\n")
        for line in lines:
            if not line:
                raise SignerLedgerCorruptionError("ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: empty line in ledger")
            try:
                parsed = parse_strict_canonical_json(line)
            except Exception as pe:
                raise SignerLedgerCorruptionError(
                    f"ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: failed parsing record JSON: {pe}"
                ) from pe

            try:
                rec = ConsumedAuthorizationNonceRecord.from_dict(parsed)
            except Exception as ve:
                raise SignerLedgerCorruptionError(
                    f"ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED: invalid record schema: {ve}"
                ) from ve

            if rec.challenge_id in self._challenge_ids:
                raise SignerNonceAlreadyConsumedError(
                    f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: challenge_id '{rec.challenge_id}' duplicate in ledger"
                )
            if rec.client_nonce in self._client_nonces:
                raise SignerNonceAlreadyConsumedError(
                    f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: client_nonce '{rec.client_nonce}' duplicate in ledger"
                )
            pair = (rec.approval_record_id, rec.proposal_revision)
            if pair in self._approval_pairs:
                raise SignerNonceAlreadyConsumedError(
                    f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: approval_pair '{pair}' duplicate in ledger"
                )

            self._challenge_ids.add(rec.challenge_id)
            self._client_nonces.add(rec.client_nonce)
            self._approval_pairs.add(pair)

        self._file_offset = st.st_size

    def consume_once(self, record: ConsumedAuthorizationNonceRecord) -> None:
        """Appends and fsyncs record to ledger exclusively, detecting multi-dimensional collisions."""
        # I6-R1-04: Nominal type gate prevents subclasses from altering serialization
        if type(record) is not ConsumedAuthorizationNonceRecord:
            raise SignerLedgerError(
                f"ERR_INVALID_RECORD: expected exact ConsumedAuthorizationNonceRecord, got {type(record).__name__}"
            )

        with self._lock:
            self._ensure_open_locked()
            if self._ledger_fd is None:
                raise SignerLedgerClosedError("ERR_LEDGER_CLOSED: ledger descriptor is None")

            try:
                fcntl.flock(self._ledger_fd, fcntl.LOCK_EX)
            except OSError as fe:
                raise SignerLedgerDurabilityError(f"ERR_LEDGER_LOCK_FAILED: failed acquiring file lock: {fe}") from fe

            primary_exc: Optional[BaseException] = None
            try:
                # 1. Re-read on-disk tail under lock
                self._sync_and_scan_locked()

                # 2. Collision checks against updated in-memory index
                if record.challenge_id in self._challenge_ids:
                    raise SignerNonceAlreadyConsumedError(
                        f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: challenge_id '{record.challenge_id}' already consumed"
                    )
                if record.client_nonce in self._client_nonces:
                    raise SignerNonceAlreadyConsumedError(
                        f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: client_nonce '{record.client_nonce}' already consumed"
                    )
                pair = (record.approval_record_id, record.proposal_revision)
                if pair in self._approval_pairs:
                    raise SignerNonceAlreadyConsumedError(
                        f"ERR_SIGNER_NONCE_ALREADY_CONSUMED: approval_pair '{pair}' already consumed"
                    )

                # 3. Canonical bytes + newline
                raw_line = record.canonical_bytes() + b"\n"

                # 4. Seek to end of file
                os.lseek(self._ledger_fd, 0, os.SEEK_END)

                # 5. Write-all loop with EINTR retry
                total_written = 0
                while total_written < len(raw_line):
                    try:
                        written = os.write(self._ledger_fd, raw_line[total_written:])
                        if written <= 0:
                            raise SignerLedgerDurabilityError("ERR_LEDGER_WRITE_FAILED: 0 bytes written")
                        total_written += written
                    except OSError as we:
                        if we.errno == errno.EINTR:
                            continue
                        raise SignerLedgerDurabilityError(f"ERR_LEDGER_WRITE_FAILED: {we}") from we

                # 6. fsync ledger file
                try:
                    os.fsync(self._ledger_fd)
                except OSError as fse:
                    raise SignerLedgerDurabilityError(f"ERR_LEDGER_FSYNC_FAILED: {fse}") from fse

                # 7. Update in-memory state
                self._challenge_ids.add(record.challenge_id)
                self._client_nonces.add(record.client_nonce)
                self._approval_pairs.add(pair)
                self._file_offset += len(raw_line)

            except BaseException as pe:
                primary_exc = pe
                raise
            finally:
                unlock_err: Optional[SignerLedgerDurabilityError] = None
                try:
                    if self._ledger_fd is not None:
                        fcntl.flock(self._ledger_fd, fcntl.LOCK_UN)
                except OSError as ue:
                    self._lock_corrupted = True
                    self._closed = True
                    unlock_err = SignerLedgerDurabilityError(
                        f"ERR_LEDGER_UNLOCK_FAILED: failed releasing file lock: {ue}",
                        code="ERR_LEDGER_UNLOCK_FAILED",
                    )

                # I6-R1-05 & I6-R2-02: Preserve primary error and attach unlock failure with structured attributes
                if primary_exc is not None and unlock_err is not None:
                    err_msg = f"{primary_exc}; cleanup failure: {unlock_err}"
                    err = SignerLedgerDurabilityError(
                        err_msg,
                        code="ERR_LEDGER_UNLOCK_FAILED",
                        primary_error=primary_exc,
                        cleanup_error=unlock_err,
                        cleanup_errors=[unlock_err],
                    )
                    err.__cause__ = primary_exc
                    raise err from primary_exc

                if unlock_err is not None:
                    raise unlock_err

    def contains(self, record: ConsumedAuthorizationNonceRecord) -> bool:
        """Returns True if any dimension of record (challenge_id, client_nonce, or approval pair) is consumed."""
        # I6-R1-04: Nominal type gate prevents subclasses from altering comparison
        if type(record) is not ConsumedAuthorizationNonceRecord:
            raise SignerLedgerError(
                f"ERR_INVALID_RECORD: expected exact ConsumedAuthorizationNonceRecord, got {type(record).__name__}"
            )

        with self._lock:
            self._ensure_open_locked()
            if self._ledger_fd is None:
                raise SignerLedgerClosedError("ERR_LEDGER_CLOSED: ledger descriptor is None")

            try:
                fcntl.flock(self._ledger_fd, fcntl.LOCK_EX)
            except OSError as fe:
                raise SignerLedgerDurabilityError(f"ERR_LEDGER_LOCK_FAILED: failed acquiring file lock: {fe}") from fe

            primary_exc: Optional[BaseException] = None
            try:
                self._sync_and_scan_locked()
                pair = (record.approval_record_id, record.proposal_revision)
                return (
                    record.challenge_id in self._challenge_ids
                    or record.client_nonce in self._client_nonces
                    or pair in self._approval_pairs
                )
            except BaseException as pe:
                primary_exc = pe
                raise
            finally:
                unlock_err: Optional[SignerLedgerDurabilityError] = None
                try:
                    if self._ledger_fd is not None:
                        fcntl.flock(self._ledger_fd, fcntl.LOCK_UN)
                except OSError as ue:
                    self._lock_corrupted = True
                    self._closed = True
                    unlock_err = SignerLedgerDurabilityError(
                        f"ERR_LEDGER_UNLOCK_FAILED: failed releasing file lock: {ue}",
                        code="ERR_LEDGER_UNLOCK_FAILED",
                    )

                # I6-R1-05 & I6-R2-02: Preserve primary error and attach unlock failure with structured attributes
                if primary_exc is not None and unlock_err is not None:
                    err_msg = f"{primary_exc}; cleanup failure: {unlock_err}"
                    err = SignerLedgerDurabilityError(
                        err_msg,
                        code="ERR_LEDGER_UNLOCK_FAILED",
                        primary_error=primary_exc,
                        cleanup_error=unlock_err,
                        cleanup_errors=[unlock_err],
                    )
                    err.__cause__ = primary_exc
                    raise err from primary_exc

                if unlock_err is not None:
                    raise unlock_err

    def close(self) -> None:
        """Closes all retained descriptors exception-safely using single-close semantics."""
        with self._lock:
            if self._closed and self._ledger_fd is None and self._dir_fd is None:
                return

            self._closed = True

            cleanup_errors: List[str] = []
            if self._ledger_fd is not None:
                fd = self._ledger_fd
                self._ledger_fd = None  # Single-close semantics: immediately disown before calling os.close
                try:
                    os.close(fd)
                except OSError as ce:
                    self._cleanup_failures.append(("ledger_fd", fd, ce, CLOSE_OUTCOME_UNKNOWN))
                    cleanup_errors.append(f"ledger_fd({fd}): {ce}")

            if self._dir_fd is not None:
                fd = self._dir_fd
                self._dir_fd = None  # Single-close semantics: immediately disown before calling os.close
                try:
                    os.close(fd)
                except OSError as ce:
                    self._cleanup_failures.append(("dir_fd", fd, ce, CLOSE_OUTCOME_UNKNOWN))
                    cleanup_errors.append(f"dir_fd({fd}): {ce}")

            if cleanup_errors:
                raise SignerLedgerDurabilityError(
                    f"ERR_LEDGER_CLOSE_FAILED: descriptor cleanup failed: {'; '.join(cleanup_errors)}"
                )

    def __enter__(self) -> SignerConsumedNonceLedger:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ==============================================================================
# Gate 3-I7: Signed Revocation Manifest Verification Core
# ==============================================================================

MAX_REVOCATION_EPOCH: int = 9_007_199_254_740_991  # 2^53 - 1 (RFC 8785 safe integer limit)
REVOCATION_ISSUER_ROOT_ID: str = "security_officer_root"

REVOCATION_MANIFEST_NON_SIGNATURE_KEYS: frozenset[str] = frozenset({
    "epoch",
    "issued_at_iso",
    "issuer_id",
    "manifest_version",
    "revoked_authorization_ids",
    "revoked_director_key_ids",
    "revoked_operator_key_ids",
    "revoked_trial_key_ids",
    "valid_until_iso",
})

REVOCATION_MANIFEST_REQUIRED_KEYS: frozenset[str] = frozenset(
    REVOCATION_MANIFEST_NON_SIGNATURE_KEYS | {"manifest_signature"}
)


@dataclass(frozen=True)
class SignedRevocationManifest:
    """Immutable signed revocation manifest DTO.

    Contains exactly 9 non-signature claims + 1 signature field.
    Revocation collections are stored as immutable tuples, preventing post-verification tampering.
    Enforces strict primitive types, range gates, list uniqueness, and timestamp bounds.
    """
    epoch: int
    issued_at_iso: str
    issuer_id: str
    manifest_version: int
    revoked_authorization_ids: Tuple[str, ...]
    revoked_director_key_ids: Tuple[str, ...]
    revoked_operator_key_ids: Tuple[str, ...]
    revoked_trial_key_ids: Tuple[str, ...]
    valid_until_iso: str
    manifest_signature: str

    def __post_init__(self) -> None:
        # 1. Version gate: exact int 1, reject bool
        if type(self.manifest_version) is not int or isinstance(self.manifest_version, bool) or self.manifest_version != 1:
            raise RevocationManifestVerificationError(
                f"ERR_MANIFEST_FIELD_INVALID: manifest_version must be exact int 1, got {self.manifest_version!r}"
            )

        # 2. Epoch gate: exact int, 1 <= epoch <= 9_007_199_254_740_991, reject bool
        if (
            type(self.epoch) is not int
            or isinstance(self.epoch, bool)
            or not (1 <= self.epoch <= MAX_REVOCATION_EPOCH)
        ):
            raise RevocationManifestVerificationError(
                f"ERR_MANIFEST_FIELD_INVALID: epoch must be an int between 1 and {MAX_REVOCATION_EPOCH}, got {self.epoch!r}"
            )

        # 3. Issuer gate: exact str, strictly security_officer_root
        if type(self.issuer_id) is not str or self.issuer_id != REVOCATION_ISSUER_ROOT_ID:
            raise RevocationManifestVerificationError(
                f"ERR_MANIFEST_ISSUER_INVALID: issuer_id must strictly equal {REVOCATION_ISSUER_ROOT_ID!r}, got {self.issuer_id!r}"
            )

        # 4. List fields: exact list/tuple of non-empty, unpadded, unique strings converted to frozen tuple
        list_fields = [
            ("revoked_authorization_ids", self.revoked_authorization_ids),
            ("revoked_director_key_ids", self.revoked_director_key_ids),
            ("revoked_operator_key_ids", self.revoked_operator_key_ids),
            ("revoked_trial_key_ids", self.revoked_trial_key_ids),
        ]
        for field_name, items in list_fields:
            if type(items) not in (list, tuple):
                raise RevocationManifestVerificationError(
                    f"ERR_MANIFEST_FIELD_INVALID: {field_name} must be exact list or tuple, got {type(items).__name__}"
                )
            seen_items: Set[str] = set()
            for idx, item in enumerate(items):
                if type(item) is not str or not item or item != item.strip():
                    raise RevocationManifestVerificationError(
                        f"ERR_MANIFEST_FIELD_INVALID: {field_name}[{idx}] must be a non-empty, unpadded string, got {item!r}"
                    )
                if item in seen_items:
                    raise RevocationManifestVerificationError(
                        f"ERR_MANIFEST_FIELD_INVALID: duplicate entry {item!r} in {field_name}"
                    )
                seen_items.add(item)
            object.__setattr__(self, field_name, tuple(items))

        # 5. Timestamps: strict UTC ISO-8601 parsing & valid_until_iso > issued_at_iso
        if type(self.issued_at_iso) is not str or not self.issued_at_iso:
            raise RevocationManifestVerificationError("ERR_MANIFEST_FIELD_INVALID: issued_at_iso must be non-empty string")
        if type(self.valid_until_iso) is not str or not self.valid_until_iso:
            raise RevocationManifestVerificationError("ERR_MANIFEST_FIELD_INVALID: valid_until_iso must be non-empty string")

        try:
            issued_dt = parse_iso_utc_timestamp(self.issued_at_iso, "issued_at_iso")
        except Exception as e:
            raise RevocationManifestVerificationError(f"ERR_MANIFEST_FIELD_INVALID: invalid issued_at_iso: {e}") from e

        try:
            valid_dt = parse_iso_utc_timestamp(self.valid_until_iso, "valid_until_iso")
        except Exception as e:
            raise RevocationManifestVerificationError(f"ERR_MANIFEST_FIELD_INVALID: invalid valid_until_iso: {e}") from e

        if valid_dt <= issued_dt:
            raise RevocationManifestVerificationError(
                f"ERR_MANIFEST_FIELD_INVALID: valid_until_iso ({self.valid_until_iso}) must be strictly after issued_at_iso ({self.issued_at_iso})"
            )

        # 6. Signature format: 128 hex characters (uppercase or lowercase, 64 bytes)
        if (
            type(self.manifest_signature) is not str
            or len(self.manifest_signature) != 128
            or not all(c in "0123456789abcdefABCDEF" for c in self.manifest_signature)
        ):
            raise RevocationManifestVerificationError(
                "ERR_MANIFEST_SIGNATURE_INVALID: manifest_signature must be exactly 128 hexadecimal characters"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SignedRevocationManifest:
        """Constructs and validates SignedRevocationManifest from dictionary."""
        if not isinstance(data, dict):
            raise RevocationManifestVerificationError(f"ERR_MANIFEST_SCHEMA_INVALID: expected dict, got {type(data).__name__}")
        if frozenset(data.keys()) != REVOCATION_MANIFEST_REQUIRED_KEYS:
            raise RevocationManifestVerificationError(
                f"ERR_MANIFEST_SCHEMA_INVALID: manifest must contain exact 10 keys {sorted(REVOCATION_MANIFEST_REQUIRED_KEYS)}, got {sorted(data.keys())}"
            )
        # Wire boundary validation: incoming JSON must have exact list types for revocation collections
        for k in (
            "revoked_authorization_ids",
            "revoked_director_key_ids",
            "revoked_operator_key_ids",
            "revoked_trial_key_ids",
        ):
            if type(data[k]) is not list:
                raise RevocationManifestVerificationError(
                    f"ERR_MANIFEST_FIELD_INVALID: {k} must be exact list in JSON, got {type(data[k]).__name__}"
                )

        return cls(
            epoch=data["epoch"],
            issued_at_iso=data["issued_at_iso"],
            issuer_id=data["issuer_id"],
            manifest_version=data["manifest_version"],
            revoked_authorization_ids=tuple(data["revoked_authorization_ids"]),
            revoked_director_key_ids=tuple(data["revoked_director_key_ids"]),
            revoked_operator_key_ids=tuple(data["revoked_operator_key_ids"]),
            revoked_trial_key_ids=tuple(data["revoked_trial_key_ids"]),
            valid_until_iso=data["valid_until_iso"],
            manifest_signature=data["manifest_signature"],
        )

    def to_claims_dict(self) -> Dict[str, Any]:
        """Serializes the exact 9 non-signature claims into a dictionary."""
        return {
            "epoch": self.epoch,
            "issued_at_iso": self.issued_at_iso,
            "issuer_id": self.issuer_id,
            "manifest_version": self.manifest_version,
            "revoked_authorization_ids": list(self.revoked_authorization_ids),
            "revoked_director_key_ids": list(self.revoked_director_key_ids),
            "revoked_operator_key_ids": list(self.revoked_operator_key_ids),
            "revoked_trial_key_ids": list(self.revoked_trial_key_ids),
            "valid_until_iso": self.valid_until_iso,
        }


def canonical_revocation_manifest_claims_bytes(manifest: SignedRevocationManifest) -> bytes:
    """Returns RFC 8785 canonical JSON bytes for the exact 9 non-signature claims."""
    if type(manifest) is not SignedRevocationManifest:
        raise RevocationManifestVerificationError(
            f"ERR_MANIFEST_INVALID_TYPE: expected exact SignedRevocationManifest, got {type(manifest).__name__}"
        )
    return canonical_json_bytes(manifest.to_claims_dict())


@dataclass(frozen=True)
class RevocationManifestVerificationResult:
    """Immutable verification result exposing validated manifest and canonical claims digest.

    Contains zero private keys, key bytes, or signing capability.
    """
    verified: bool
    manifest: SignedRevocationManifest
    canonical_claims_digest_sha256: str
    _provenance_seal: str = ""

    @property
    def canonical_claims_sha256(self) -> str:
        return self.canonical_claims_digest_sha256


def verify_signed_revocation_manifest(
    raw_manifest_bytes: bytes,
    issuer_public_key: Ed25519PublicKey,
    verification_time_utc: datetime,
) -> RevocationManifestVerificationResult:
    """Pure verifier for signed revocation manifest per 07B7 §6.1.

    Validates schema, Ed25519 signature over the exact 9 canonical claims, and expiry.
    Performs zero filesystem I/O, system-clock lookup, or persistent writes.
    """
    if type(raw_manifest_bytes) is not bytes:
        raise RevocationManifestVerificationError(
            f"ERR_MANIFEST_BYTES_INVALID: raw_manifest_bytes must be exact bytes, got {type(raw_manifest_bytes).__name__}"
        )
    issuer_public_key = _normalize_and_validate_ed25519_public_key(
        issuer_public_key, RevocationManifestVerificationError, "ERR_ISSUER_KEY_INVALID", "issuer_public_key"
    )
    if type(verification_time_utc) is not datetime:
        raise RevocationManifestVerificationError(
            f"ERR_VERIFICATION_TIME_INVALID: verification_time_utc must be exact datetime, got {type(verification_time_utc).__name__}"
        )
    if verification_time_utc.tzinfo is None or verification_time_utc.utcoffset() != timedelta(0):
        raise RevocationManifestVerificationError(
            "ERR_VERIFICATION_TIME_INVALID: verification_time_utc must be timezone-aware at UTC offset zero"
        )

    # 1. Parse raw bytes strictly with complexity and duplicate-key defenses
    try:
        data = parse_strict_canonical_json(raw_manifest_bytes)
    except Exception as pe:
        raise RevocationManifestVerificationError(
            f"ERR_MANIFEST_PARSE_FAILED: failed parsing revocation manifest JSON: {pe}"
        ) from pe

    # 2. Schema and DTO validation
    manifest = SignedRevocationManifest.from_dict(data)

    # 3. Expiry check against explicit verification instant
    valid_until_dt = parse_iso_utc_timestamp(manifest.valid_until_iso, "valid_until_iso")
    if verification_time_utc > valid_until_dt:
        raise RevocationManifestVerificationError(
            f"ERR_MANIFEST_EXPIRED: manifest expired at {manifest.valid_until_iso}"
        )

    # 4. Signature verification over canonical 9 claims
    sig_bytes = bytes.fromhex(manifest.manifest_signature)
    signing_bytes = canonical_revocation_manifest_claims_bytes(manifest)

    try:
        issuer_public_key.verify(sig_bytes, signing_bytes)
    except InvalidSignature as exc:
        raise RevocationManifestVerificationError(
            "ERR_MANIFEST_SIGNATURE_INVALID: Ed25519 digital signature does not verify under issuer public key"
        ) from exc
    except Exception as exc:
        raise RevocationManifestVerificationError(
            f"ERR_MANIFEST_SIGNATURE_ERROR: signature verification raised {type(exc).__name__}"
        ) from exc

    canonical_digest = hashlib.sha256(signing_bytes).hexdigest().lower()
    seal = _compute_provenance_seal(manifest, canonical_digest)
    return RevocationManifestVerificationResult(
        verified=True,
        manifest=manifest,
        canonical_claims_digest_sha256=canonical_digest,
        _provenance_seal=seal,
    )


# ==============================================================================
# Gate 3-I8: Digest-Bound Durable Revocation Epoch Anchor Core
# ==============================================================================

_REVOCATION_EPOCH_ANCHOR_LOCK = threading.RLock()
_PROVENANCE_SECRET = os.urandom(32)


def _compute_provenance_seal(manifest: SignedRevocationManifest, canonical_digest: str) -> str:
    signing_bytes = canonical_revocation_manifest_claims_bytes(manifest)
    payload = signing_bytes + b":" + str(manifest.epoch).encode("ascii") + b":" + canonical_digest.encode("ascii")
    return hmac.new(_PROVENANCE_SECRET, payload, hashlib.sha256).hexdigest()


def _verify_provenance_seal(verified_result: RevocationManifestVerificationResult) -> bool:
    seal = getattr(verified_result, "_provenance_seal", None)
    if type(seal) is not str or len(seal) != 64:
        return False
    expected = _compute_provenance_seal(
        verified_result.manifest,
        getattr(verified_result, "canonical_claims_sha256", "")
        or getattr(verified_result, "canonical_claims_digest_sha256", ""),
    )
    return hmac.compare_digest(seal, expected)


def _safe_close_descriptor(
    fd: Optional[int],
    cleanup_failures: List[Tuple[str, int, Exception, str]],
    name: str,
) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError as ce:
        cleanup_failures.append((name, fd, ce, CLOSE_OUTCOME_UNKNOWN))


def _write_all_bytes(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(fd, data[offset:])
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise SignerLedgerDurabilityError(
                f"ERR_WRITE_FAILED: write failed during persistence: {e}",
                code="ERR_WRITE_FAILED",
                primary_error=e,
            ) from e
        if written <= 0:
            raise SignerLedgerDurabilityError(
                f"ERR_SHORT_WRITE: os.write returned {written} bytes while {len(data) - offset} bytes remaining",
                code="ERR_SHORT_WRITE",
            )
        offset += written


def _open_anchor_dir_under_temp_root(
    anchor_directory: str | Path,
    cleanup_failures: List[Tuple[str, int, Exception, str]],
) -> int:
    """I8-R3-01 / I10-R1-01: Proves anchor directory is a descendant of temp root via step-by-step descriptor traversal.

    Strict boundary:
    - Pure lexical check against banned production paths first, without touching the filesystem.
    - Pure lexical check against temp root namespace before any path-based filesystem query or open.
    - Resolves the system temp directory itself once to obtain temp_root_fd.
    - All candidate descendants are traversed step-by-step descriptor-relatively with O_NOFOLLOW.
    - Never calls os.path.realpath(), os.path.islink(), or os.stat() on the untrusted candidate path.
    """
    raw_str = str(anchor_directory)

    # 1. Lexical banned path check (zero filesystem query on candidate)
    for banned in ("/var/lib/resolveflow", "/run/resolveflow"):
        if banned in raw_str:
            raise SignerLedgerSecurityError(
                f"ERR_REVOCATION_ANCHOR_INSECURE_PATH: production path forbidden in local test primitive: {raw_str}"
            )

    # 2. Lexical normalization without touching filesystem
    norm_candidate = os.path.normpath(raw_str)

    # 3. Obtain approved system temp directory
    sys_temp = os.path.normpath(tempfile.gettempdir())
    real_temp = os.path.normpath(os.path.realpath(sys_temp))

    # Reject non-absolute paths or paths not lexically starting with sys_temp or real_temp
    is_under_sys = norm_candidate == sys_temp or norm_candidate.startswith(sys_temp + os.sep)
    is_under_real = norm_candidate == real_temp or norm_candidate.startswith(real_temp + os.sep)

    if not (is_under_sys or is_under_real):
        raise SignerLedgerSecurityError(
            f"ERR_ANCHOR_DIR_NOT_TEMP: anchor_directory must be within system temporary directory {real_temp!r}, got {raw_str!r}"
        )

    # 4. Open temp root descriptor
    temp_root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        temp_root_fd = os.open(real_temp, temp_root_flags)
    except OSError as e:
        raise SignerLedgerDurabilityError(f"ERR_CANNOT_OPEN_TEMP_ROOT: cannot open temp root {real_temp}: {e}") from e

    current_fd = temp_root_fd
    success = False
    try:
        temp_root_st = os.fstat(temp_root_fd)
        if not stat.S_ISDIR(temp_root_st.st_mode):
            raise SignerLedgerSecurityError(f"ERR_TEMP_ROOT_NOT_DIR: {real_temp} is not a directory")

        if norm_candidate in (sys_temp, real_temp):
            dir_fd = os.dup(temp_root_fd)
            dir_st = os.fstat(dir_fd)
            if (dir_st.st_mode & 0o022) != 0:
                _safe_close_descriptor(dir_fd, cleanup_failures, "insecure_dir_fd")
                raise SignerLedgerSecurityError(
                    f"ERR_DIR_INSECURE_PERMS: anchor_directory mode allows group or world write: {oct(dir_st.st_mode & 0o777)}"
                )
            success = True
            return dir_fd

        if is_under_real:
            rel = os.path.relpath(norm_candidate, real_temp)
        else:
            rel = os.path.relpath(norm_candidate, sys_temp)

        parts = rel.split(os.sep)

        step_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )

        for part in parts:
            if not part or part in (".", ".."):
                raise SignerLedgerSecurityError(f"ERR_DIR_PATH_TRAVERSAL: illegal component {part!r} in path traversal")
            try:
                next_fd = os.open(part, step_flags, dir_fd=current_fd)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise SignerLedgerSecurityError(f"ERR_DIR_SYMLINK_DETECTED: component {part!r} is a symlink or not a dir: {e}") from e
                raise SignerLedgerError(f"ERR_CANNOT_TRAVERSE_DIR: cannot open component {part!r}: {e}") from e

            try:
                step_st = os.fstat(next_fd)
                if not stat.S_ISDIR(step_st.st_mode):
                    raise SignerLedgerSecurityError(f"ERR_NOT_A_DIR: component {part!r} is not a directory")
                if step_st.st_dev != temp_root_st.st_dev:
                    raise SignerLedgerSecurityError(f"ERR_DIR_WRONG_DEVICE: component {part!r} device {step_st.st_dev} != temp device {temp_root_st.st_dev}")
                if hasattr(os, "getuid") and step_st.st_uid != os.getuid():
                    raise SignerLedgerSecurityError(f"ERR_DIR_INVALID_OWNER: component {part!r} uid {step_st.st_uid} != current uid {os.getuid()}")
                # I8-R4-01: Reject group or world write permissions on every component below temp root
                if (step_st.st_mode & 0o022) != 0:
                    raise SignerLedgerSecurityError(
                        f"ERR_DIR_INSECURE_PERMS: component {part!r} mode allows group or world write: {oct(step_st.st_mode & 0o777)}"
                    )
                # I10-R2-01: Descriptor-relative no-follow identity check using parent directory descriptor
                rel_st = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
                if step_st.st_ino != rel_st.st_ino or step_st.st_dev != rel_st.st_dev:
                    raise SignerLedgerSecurityError(f"ERR_DIR_INODE_SWAP: component {part!r} swapped during traversal")
            except BaseException:
                _safe_close_descriptor(next_fd, cleanup_failures, "step_fd")
                raise

            if current_fd != temp_root_fd:
                _safe_close_descriptor(current_fd, cleanup_failures, "intermediate_dir_fd")
            current_fd = next_fd

        dir_fd = current_fd
        dir_st = os.fstat(dir_fd)
        if (dir_st.st_mode & 0o022) != 0:
            raise SignerLedgerSecurityError(
                f"ERR_DIR_INSECURE_PERMS: anchor_directory mode allows group or world write: {oct(dir_st.st_mode & 0o777)}"
            )

        success = True
        return dir_fd
    finally:
        _safe_close_descriptor(temp_root_fd, cleanup_failures, "temp_root_fd")
        if not success and current_fd != temp_root_fd:
            _safe_close_descriptor(current_fd, cleanup_failures, "current_traversal_dir_fd")


def check_and_advance_revocation_epoch_anchor_for_local_test(
    verified_result: RevocationManifestVerificationResult,
    anchor_directory: str | Path,
    advance: bool = True,
    *,
    require_existing_anchor: bool = False,
) -> None:
    """Monotonic, digest-bound epoch-anchor primitive for verified revocation manifests (07B7 §6.2).

    Validates that verified_result is the exact accepted DTO type, epoch is a safe positive integer,
    digest matches the verified canonical claims SHA-256, and verified_result carries an authentic
    cryptographic provenance seal proving it was produced by verify_signed_revocation_manifest.
    Operates strictly within local test anchor_directory via descriptor-anchored file operations.
    Rejects rollback (lower epoch), detects tampering (same epoch with differing digest),
    idempotently accepts identical epoch/digest, and atomically advances to a greater epoch.
    When require_existing_anchor=True, rejects a missing anchor with ERR_REVOCATION_ANCHOR_ABSENT.
    """
    if type(require_existing_anchor) is not bool:
        raise SignerLedgerError(
            f"ERR_INVALID_PARAMETER: require_existing_anchor must be exact built-in bool, got {type(require_existing_anchor).__name__}",
            code="ERR_INVALID_PARAMETER",
        )
    if type(verified_result) is not RevocationManifestVerificationResult:
        raise SignerLedgerError(
            f"ERR_INVALID_RESULT_TYPE: verified_result must be exact RevocationManifestVerificationResult, got {type(verified_result).__name__}"
        )
    if verified_result.verified is not True:
        raise SignerLedgerError("ERR_RESULT_NOT_VERIFIED: verified_result.verified must be strictly True")

    manifest = verified_result.manifest
    if type(manifest) is not SignedRevocationManifest:
        raise SignerLedgerError(
            f"ERR_INVALID_MANIFEST_TYPE: manifest must be exact SignedRevocationManifest, got {type(manifest).__name__}"
        )

    # Revalidate epoch as strict safe positive integer
    epoch = manifest.epoch
    if type(epoch) is not int or isinstance(epoch, bool):
        raise SignerLedgerError(f"ERR_INVALID_EPOCH: epoch must be strict integer and not bool, got {type(epoch).__name__}")
    if not (1 <= epoch <= 9007199254740991):
        raise SignerLedgerError(f"ERR_INVALID_EPOCH: epoch must be within [1, 9007199254740991], got {epoch}")

    # Revalidate digest
    digest = getattr(verified_result, "canonical_claims_sha256", None) or getattr(
        verified_result, "canonical_claims_digest_sha256", None
    )
    if type(digest) is not str:
        raise SignerLedgerError(f"ERR_INVALID_DIGEST: digest must be string, got {type(digest).__name__}")
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise SignerLedgerError(
            f"ERR_INVALID_DIGEST_ENCODING: digest must be exactly 64 lowercase hex characters, got {digest!r}"
        )

    expected_digest = hashlib.sha256(canonical_revocation_manifest_claims_bytes(manifest)).hexdigest().lower()
    if digest != expected_digest:
        raise SignerLedgerError(
            f"ERR_DIGEST_MISMATCH: verified_result digest {digest} does not match canonical claims hash {expected_digest}"
        )

    # I8-R2-01: Reject forged/caller-constructed results lacking internal provenance seal
    if not _verify_provenance_seal(verified_result):
        raise SignerLedgerSecurityError(
            "ERR_UNVERIFIED_RESULT_FORGERY: verified_result lacks valid internal provenance seal from verify_signed_revocation_manifest"
        )

    # Validate directory argument
    if not isinstance(anchor_directory, (str, Path)) or not str(anchor_directory).strip():
        raise SignerLedgerError("ERR_INVALID_ANCHOR_DIR: anchor_directory must be a non-empty string or Path")

    cleanup_failures: List[Tuple[str, int, Exception, str]] = []

    # I8-R3-01: Open anchor directory through step-by-step descriptor traversal under temp root
    dir_fd = _open_anchor_dir_under_temp_root(anchor_directory, cleanup_failures)
    retained_dir_fd: Optional[int] = dir_fd
    lock_fd: Optional[int] = None
    dir_flocked: bool = False
    primary_exc: Optional[BaseException] = None

    try:
        with _REVOCATION_EPOCH_ANCHOR_LOCK:
            # I12-R3-02: Non-mutating interprocess synchronization contract
            # Lock the stable registry directory descriptor dir_fd itself:
            # - Writer (advance=True) takes LOCK_EX across all processes
            # - Reader (advance=False) takes LOCK_SH across all processes
            # This serializes read-only admission against concurrent anchor advancements
            # without creating any lock file on a fresh registry directory.
            try:
                fcntl.flock(dir_fd, fcntl.LOCK_EX if advance else fcntl.LOCK_SH)
                dir_flocked = True
            except OSError as e:
                raise SignerLedgerDurabilityError(
                    f"ERR_CANNOT_FLOCK_DIR: failed to acquire flock on registry directory: {e}"
                ) from e

            # I8-R1-01, I12-R2-01: Open lock file with O_NONBLOCK to prevent hanging on FIFOs
            # When advance is False (read-only verification), do NOT use O_CREAT so we do not mutate the directory.
            if advance:
                lock_flags = (
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    lock_fd = os.open("revocation_epoch.lock", lock_flags, 0o600, dir_fd=dir_fd)
                except OSError as e:
                    if e.errno == errno.ELOOP:
                        raise SignerLedgerSecurityError(f"ERR_LOCK_FILE_SYMLINK_DETECTED: lock file is a symlink: {e}") from e
                    raise SignerLedgerDurabilityError(f"ERR_CANNOT_OPEN_LOCK_FILE: cannot open anchor lock file: {e}") from e
            else:
                lock_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    lock_fd = os.open("revocation_epoch.lock", lock_flags, dir_fd=dir_fd)
                except FileNotFoundError:
                    lock_fd = None
                except OSError as e:
                    if e.errno == errno.ENOENT:
                        lock_fd = None
                    elif e.errno == errno.ELOOP:
                        raise SignerLedgerSecurityError(f"ERR_LOCK_FILE_SYMLINK_DETECTED: lock file is a symlink: {e}") from e
                    else:
                        raise SignerLedgerDurabilityError(f"ERR_CANNOT_OPEN_LOCK_FILE: cannot open anchor lock file: {e}") from e

            if lock_fd is not None:
                lock_st = os.fstat(lock_fd)
                if not stat.S_ISREG(lock_st.st_mode):
                    raise SignerLedgerSecurityError("ERR_LOCK_NOT_REGULAR_FILE: lock file is not a regular file")
                if hasattr(os, "getuid") and lock_st.st_uid != os.getuid():
                    raise SignerLedgerSecurityError("ERR_LOCK_INVALID_OWNER: lock file owner mismatch")
                if (lock_st.st_mode & 0o777) != 0o600:
                    raise SignerLedgerSecurityError(
                        f"ERR_LOCK_FILE_INSECURE_PERMS: lock file mode must be 0600, got {oct(lock_st.st_mode & 0o777)}"
                    )
                if lock_st.st_nlink != 1:
                    raise SignerLedgerSecurityError(f"ERR_LOCK_FILE_HARDLINK: lock file has multiple links: {lock_st.st_nlink}")

                # Acquire exclusive OS advisory lock if advancing, or shared lock if read-only
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX if advance else fcntl.LOCK_SH)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_CANNOT_FLOCK: failed to acquire flock on anchor lock: {e}") from e

            # Read anchor under lock
            anchor_name = "revocation_epoch.anchor"
            anchor_fd: Optional[int] = None
            existing_epoch: Optional[int] = None
            existing_digest: Optional[str] = None

            try:
                try:
                    # I8-R1-01: Nonblocking open strategy for anchor file
                    anchor_flags = (
                        os.O_RDONLY
                        | getattr(os, "O_NONBLOCK", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    anchor_fd = os.open(anchor_name, anchor_flags, dir_fd=dir_fd)
                except FileNotFoundError:
                    anchor_fd = None
                except OSError as e:
                    if e.errno == errno.ENOENT:
                        anchor_fd = None
                    elif e.errno == errno.ELOOP:
                        raise SignerLedgerSecurityError(f"ERR_ANCHOR_FILE_SYMLINK_DETECTED: anchor file is a symlink: {e}") from e
                    else:
                        raise SignerLedgerError(f"ERR_CANNOT_OPEN_ANCHOR: {e}") from e

                if anchor_fd is not None:
                    st_before = os.fstat(anchor_fd)
                    if not stat.S_ISREG(st_before.st_mode):
                        raise SignerLedgerSecurityError("ERR_ANCHOR_NOT_REGULAR_FILE: anchor file is not a regular file")
                    if hasattr(os, "getuid") and st_before.st_uid != os.getuid():
                        raise SignerLedgerSecurityError("ERR_ANCHOR_INVALID_OWNER: anchor file owner mismatch")
                    if (st_before.st_mode & 0o777) != 0o600:
                        raise SignerLedgerSecurityError(
                            f"ERR_ANCHOR_FILE_INSECURE_PERMS: anchor file mode must be 0600, got {oct(st_before.st_mode & 0o777)}"
                        )
                    if st_before.st_nlink != 1:
                        raise SignerLedgerSecurityError(f"ERR_ANCHOR_FILE_HARDLINK: anchor file has multiple links: {st_before.st_nlink}")
                    if st_before.st_size <= 0 or st_before.st_size > 256:
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: invalid file size {st_before.st_size}")

                    # I8-R1-02: Bounded read with strict max + 1-byte overflow probe, exact EOF
                    raw = b""
                    while len(raw) < 257:
                        try:
                            chunk = os.read(anchor_fd, 257 - len(raw))
                        except OSError as e:
                            if e.errno == errno.EINTR:
                                continue
                            raise
                        if not chunk:
                            break
                        raw += chunk

                    if len(raw) > 256:
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: anchor file exceeds maximum size: {len(raw)} bytes")
                    if len(raw) != st_before.st_size:
                        raise SignerLedgerCorruptionError(
                            f"ERR_REVOCATION_ANCHOR_MALFORMED: short read or size mismatch: read {len(raw)} vs st_size {st_before.st_size}"
                        )

                    # Metadata stability verification
                    st_after = os.fstat(anchor_fd)
                    if (
                        st_after.st_size != st_before.st_size
                        or st_after.st_ino != st_before.st_ino
                        or st_after.st_dev != st_before.st_dev
                        or st_after.st_mtime_ns != st_before.st_mtime_ns
                    ):
                        raise SignerLedgerCorruptionError("ERR_REVOCATION_ANCHOR_MUTATED: anchor file metadata changed during read")

                    # Close anchor_fd
                    afd = anchor_fd
                    anchor_fd = None
                    _safe_close_descriptor(afd, cleanup_failures, "anchor_read_fd")

                    try:
                        text = raw.decode("ascii")
                    except UnicodeDecodeError as ue:
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: non-ascii anchor content: {ue}") from ue

                    if not text.endswith("\n") or text.count("\n") != 1 or "\r" in text:
                        raise SignerLedgerCorruptionError("ERR_REVOCATION_ANCHOR_MALFORMED: line formatting invalid (must end with single newline, no cr)")

                    line = text[:-1]
                    parts = line.split(":")
                    if len(parts) != 2:
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: expected <epoch>:<digest>, got {line!r}")

                    epoch_s, digest_s = parts
                    if not epoch_s.isdigit() or str(int(epoch_s)) != epoch_s:
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: invalid epoch {epoch_s!r}")

                    existing_epoch = int(epoch_s)
                    if not (1 <= existing_epoch <= 9007199254740991):
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: epoch out of range: {existing_epoch}")

                    if len(digest_s) != 64 or not all(c in "0123456789abcdef" for c in digest_s):
                        raise SignerLedgerCorruptionError(f"ERR_REVOCATION_ANCHOR_MALFORMED: invalid digest encoding {digest_s!r}")

                    existing_digest = digest_s
            finally:
                if anchor_fd is not None:
                    afd = anchor_fd
                    anchor_fd = None
                    _safe_close_descriptor(afd, cleanup_failures, "anchor_read_fd")

            # Decision
            if existing_epoch is None:
                if require_existing_anchor:
                    raise RevocationAnchorAbsentError(
                        "ERR_REVOCATION_ANCHOR_ABSENT: required revocation epoch anchor is absent",
                        code="ERR_REVOCATION_ANCHOR_ABSENT",
                    )
                if not advance:
                    # I12-R1-03: Non-mutating read path: no existing anchor, valid bootstrap manifest, do not create anchor
                    return
                # Bootstrap: create initial record atomically with exclusive creation
                bootstrap_line = f"{epoch}:{digest}\n".encode("ascii")
                wr_flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                new_fd: Optional[int] = None
                try:
                    new_fd = os.open(anchor_name, wr_flags, 0o600, dir_fd=dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(f"ERR_ANCHOR_BOOTSTRAP_FAILED: cannot create anchor file: {e}") from e

                try:
                    _write_all_bytes(new_fd, bootstrap_line)
                    try:
                        os.fsync(new_fd)
                    except OSError as e:
                        raise SignerLedgerDurabilityError(
                            f"ERR_FILE_FSYNC_FAILED: fsync failed on new anchor file: {e}",
                            code="ERR_FILE_FSYNC_FAILED",
                            primary_error=e,
                        ) from e
                finally:
                    nfd = new_fd
                    new_fd = None
                    _safe_close_descriptor(nfd, cleanup_failures, "new_anchor_fd")

                try:
                    os.fsync(dir_fd)
                except OSError as e:
                    raise SignerLedgerDurabilityError(
                        f"ERR_DIR_FSYNC_FAILED: failed fsync on directory descriptor: {e}",
                        code="ERR_DIR_FSYNC_FAILED",
                        primary_error=e,
                    ) from e

            else:
                if epoch < existing_epoch:
                    raise RevocationRollbackError(
                        f"ERR_REVOCATION_ROLLBACK_DETECTED: incoming epoch {epoch} is lower than anchor epoch {existing_epoch}",
                        code="ERR_REVOCATION_ROLLBACK_DETECTED",
                    )
                elif epoch == existing_epoch:
                    if digest != existing_digest:
                        raise RevocationEpochTamperingError(
                            f"ERR_REVOCATION_SAME_EPOCH_TAMPERING: same epoch {existing_epoch} but digest differs: {digest} != {existing_digest}",
                            code="ERR_REVOCATION_SAME_EPOCH_TAMPERING",
                        )
                    # Equal epoch and digest: idempotent, return without mutation
                    return
                else:
                    if not advance:
                        # I12-R1-03: Non-mutating read path: epoch is higher than existing anchor, valid advance, do not mutate anchor
                        return
                    # epoch > existing_epoch: Atomic advance
                    tmp_name = f".anchor_tmp_{os.getpid()}_{uuid.uuid4().hex}"
                    tmp_flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0)
                    )
                    tmp_fd: Optional[int] = None
                    try:
                        tmp_fd = os.open(tmp_name, tmp_flags, 0o600, dir_fd=dir_fd)
                    except OSError as e:
                        raise SignerLedgerDurabilityError(f"ERR_ANCHOR_TEMP_FAILED: cannot create temp file {tmp_name}: {e}") from e

                    advance_line = f"{epoch}:{digest}\n".encode("ascii")
                    try:
                        _write_all_bytes(tmp_fd, advance_line)
                        try:
                            os.fsync(tmp_fd)
                        except OSError as e:
                            raise SignerLedgerDurabilityError(
                                f"ERR_FILE_FSYNC_FAILED: fsync failed on temp anchor file: {e}",
                                code="ERR_FILE_FSYNC_FAILED",
                                primary_error=e,
                            ) from e
                    finally:
                        tfd = tmp_fd
                        tmp_fd = None
                        _safe_close_descriptor(tfd, cleanup_failures, "tmp_anchor_fd")

                    try:
                        os.replace(tmp_name, anchor_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                    except OSError as e:
                        replace_err = SignerLedgerDurabilityError(f"ERR_ANCHOR_REPLACE_FAILED: atomic replace failed: {e}")
                        try:
                            os.unlink(tmp_name, dir_fd=dir_fd)
                        except OSError as ue:
                            unlink_err = SignerLedgerDurabilityError(f"ERR_ORPHAN_TEMP_UNLINK_FAILED: failed unlinking temp file {tmp_name}: {ue}")
                            cleanup_failures.append(("tmp_anchor_unlink", -1, unlink_err, "UNLINK_FAILED"))
                            replace_err.cleanup_errors.append(unlink_err)
                        raise replace_err from e

                    try:
                        os.fsync(dir_fd)
                    except OSError as e:
                        raise SignerLedgerDurabilityError(
                            f"ERR_DIR_FSYNC_FAILED: failed fsync on directory descriptor: {e}",
                            code="ERR_DIR_FSYNC_FAILED",
                            primary_error=e,
                        ) from e

    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError as ue:
                cleanup_failures.append(("lock_unlock", lock_fd, ue, "UNLOCK_FAILED"))
            lfd = lock_fd
            lock_fd = None
            _safe_close_descriptor(lfd, cleanup_failures, "lock_fd")

        if dir_flocked and retained_dir_fd is not None:
            try:
                fcntl.flock(retained_dir_fd, fcntl.LOCK_UN)
            except OSError as ue:
                cleanup_failures.append(("dir_unlock", retained_dir_fd, ue, "UNLOCK_FAILED"))

        if retained_dir_fd is not None:
            dfd = retained_dir_fd
            retained_dir_fd = None
            _safe_close_descriptor(dfd, cleanup_failures, "dir_fd")

        # I8-R1-04 & I8-R2-03: If cleanup failed, preserve primary exception if active; do not mask with cleanup error
        if cleanup_failures:
            if primary_exc is not None:
                if isinstance(primary_exc, SignerLedgerError):
                    if getattr(primary_exc, "primary_error", None) is None:
                        primary_exc.primary_error = getattr(primary_exc, "__cause__", None) or primary_exc
                    for _, _, err, _ in cleanup_failures:
                        primary_exc.cleanup_errors.append(err)
                else:
                    durability_err = SignerLedgerDurabilityError(
                        f"ERR_REVOCATION_ANCHOR_FAILURE: operation failed with {type(primary_exc).__name__}: {primary_exc}",
                        primary_error=primary_exc,
                        cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                    )
                    durability_err.__cause__ = primary_exc
                    raise durability_err from primary_exc
            else:
                err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
                raise SignerLedgerDurabilityError(
                    f"ERR_REVOCATION_ANCHOR_CLEANUP_FAILED: descriptor cleanup failed: {err_details}",
                    cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                )


def load_verify_and_anchor_revocation_manifest_for_local_test(
    registry_directory: str | Path,
    issuer_public_key: Ed25519PublicKey,
    verification_time_utc: datetime,
    advance_anchor: bool = True,
    *,
    require_existing_anchor: bool = False,
) -> RevocationManifestVerificationResult:
    """Descriptor-anchored fresh revocation registry reader and anchor orchestrator (07B7 §6.3).

    Freshly opens revocations.signed.json relative to securely traversed registry_directory,
    bounded-reads up to 1 MiB, verifies strict metadata stability and single-link 0600 regular file semantics,
    passes captured bytes to verify_signed_revocation_manifest(), advances the local epoch anchor via
    check_and_advance_revocation_epoch_anchor_for_local_test(), and returns the verified result.
    When require_existing_anchor=True, propagates failure if anchor does not already exist.
    Does not cache descriptors or verification results.
    """
    # 1. Parameter validations
    if type(require_existing_anchor) is not bool:
        raise SignerLedgerError(
            f"ERR_INVALID_PARAMETER: require_existing_anchor must be exact built-in bool, got {type(require_existing_anchor).__name__}",
            code="ERR_INVALID_PARAMETER",
        )
    if type(verification_time_utc) is not datetime:
        raise SignerLedgerError(
            f"ERR_INVALID_VERIFICATION_TIME: verification_time_utc must be exact built-in datetime, got {type(verification_time_utc).__name__}"
        )
    if verification_time_utc.tzinfo is None or verification_time_utc.utcoffset() != timedelta(0):
        raise SignerLedgerError(
            "ERR_TIMEZONE_NOT_UTC: verification_time_utc must be strictly timezone-aware UTC with zero offset"
        )
    if not isinstance(issuer_public_key, Ed25519PublicKey):
        raise SignerLedgerError(
            f"ERR_INVALID_KEY_TYPE: issuer_public_key must be Ed25519PublicKey, got {type(issuer_public_key).__name__}"
        )
    if not isinstance(registry_directory, (str, Path)) or not str(registry_directory).strip():
        raise SignerLedgerError("ERR_INVALID_REGISTRY_DIR: registry_directory must be a non-empty string or Path")

    cleanup_failures: List[Tuple[str, int, Exception, str]] = []

    # 2. Descriptor-anchored traversal under temp root
    dir_fd = _open_anchor_dir_under_temp_root(registry_directory, cleanup_failures)
    retained_dir_fd: Optional[int] = dir_fd
    reg_fd: Optional[int] = None
    primary_exc: Optional[BaseException] = None

    try:
        # 3. Open revocations.signed.json relative to dir_fd
        reg_flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            reg_fd = os.open("revocations.signed.json", reg_flags, dir_fd=dir_fd)
        except FileNotFoundError as fnf:
            raise SignerLedgerError(f"ERR_REGISTRY_FILE_NOT_FOUND: revocations.signed.json not found in registry_directory: {fnf}") from fnf
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise SignerLedgerError(f"ERR_REGISTRY_FILE_NOT_FOUND: revocations.signed.json not found: {e}") from e
            elif e.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SignerLedgerSecurityError(f"ERR_REGISTRY_SYMLINK_DETECTED: revocations.signed.json is a symlink: {e}") from e
            raise SignerLedgerDurabilityError(f"ERR_CANNOT_OPEN_REGISTRY: cannot open revocations.signed.json: {e}", primary_error=e) from e

        # 4. Stat & validate regular file properties
        try:
            st_before = os.fstat(reg_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_REGISTRY_PRE_READ_FSTAT_FAILED: pre-read fstat failed on revocations.signed.json: {fe}",
                code="ERR_REGISTRY_PRE_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if not stat.S_ISREG(st_before.st_mode):
            raise SignerLedgerSecurityError(f"ERR_REGISTRY_NOT_REGULAR_FILE: revocations.signed.json is not a regular file (mode: {oct(st_before.st_mode)})")
        if hasattr(os, "getuid") and st_before.st_uid != os.getuid():
            raise SignerLedgerSecurityError(f"ERR_REGISTRY_INVALID_OWNER: owner uid {st_before.st_uid} != current uid {os.getuid()}")
        if st_before.st_nlink != 1:
            raise SignerLedgerSecurityError(f"ERR_REGISTRY_HARDLINK_DETECTED: hard link count is {st_before.st_nlink}, must be 1")
        if (st_before.st_mode & 0o777) != 0o600:
            raise SignerLedgerSecurityError(f"ERR_REGISTRY_INSECURE_PERMS: mode must be 0600, got {oct(st_before.st_mode & 0o777)}")

        max_allowed_bytes = 1048576  # 1 MiB strict JSON budget
        if st_before.st_size <= 0:
            raise SignerLedgerCorruptionError(f"ERR_REGISTRY_FILE_MALFORMED: file size is 0 bytes")
        if st_before.st_size > max_allowed_bytes:
            raise SignerLedgerCorruptionError(f"ERR_REGISTRY_FILE_OVERSIZED: file size {st_before.st_size} exceeds {max_allowed_bytes} bytes")

        # 5. Bounded chunked read with EOF probe
        captured = b""
        read_budget = st_before.st_size + 1
        while len(captured) < read_budget:
            try:
                chunk = os.read(reg_fd, min(65536, read_budget - len(captured)))
            except OSError as re:
                if re.errno == errno.EINTR:
                    continue
                raise SignerLedgerDurabilityError(f"ERR_REGISTRY_READ_FAILED: failed reading file: {re}", primary_error=re) from re
            if not chunk:
                break
            captured += chunk

        if len(captured) > st_before.st_size:
            raise SignerLedgerCorruptionError(f"ERR_REGISTRY_FILE_OVERSIZED: file grew during read (read {len(captured)} vs expected {st_before.st_size})")
        if len(captured) != st_before.st_size:
            raise SignerLedgerCorruptionError(f"ERR_REGISTRY_SHORT_READ: read {len(captured)} bytes vs st_size {st_before.st_size}")

        # Metadata stability check
        try:
            st_after = os.fstat(reg_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_REGISTRY_POST_READ_FSTAT_FAILED: post-read fstat failed on revocations.signed.json: {fe}",
                code="ERR_REGISTRY_POST_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if (
            st_after.st_size != st_before.st_size
            or st_after.st_ino != st_before.st_ino
            or st_after.st_dev != st_before.st_dev
            or st_after.st_mtime_ns != st_before.st_mtime_ns
        ):
            raise SignerLedgerCorruptionError("ERR_REGISTRY_MUTATED_DURING_READ: metadata changed during read")

        # Close reg_fd promptly before verifier/anchor
        rfd = reg_fd
        reg_fd = None
        _safe_close_descriptor(rfd, cleanup_failures, "registry_fd")

        # I9-R1-01: Treat failure to close registry descriptor as a pre-verification failure.
        # Abort immediately before calling verifier or anchor updater.
        if cleanup_failures:
            err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
            durability_err = SignerLedgerDurabilityError(
                f"ERR_REGISTRY_CLOSE_FAILED: failed closing registry descriptor before verification: {err_details}",
                code="ERR_REGISTRY_CLOSE_FAILED",
                primary_error=cleanup_failures[0][2],
                cleanup_errors=[err for _, _, err, _ in cleanup_failures],
            )
            durability_err.__cause__ = cleanup_failures[0][2]
            raise durability_err

        # 6. Verify signed revocation manifest exactly once
        verified_result = verify_signed_revocation_manifest(
            raw_manifest_bytes=captured,
            issuer_public_key=issuer_public_key,
            verification_time_utc=verification_time_utc,
        )

        # 7. Check and advance revocation epoch anchor exactly once
        check_and_advance_revocation_epoch_anchor_for_local_test(
            verified_result=verified_result,
            anchor_directory=registry_directory,
            advance=advance_anchor,
            require_existing_anchor=require_existing_anchor,
        )

        return verified_result

    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        if reg_fd is not None:
            rfd = reg_fd
            reg_fd = None
            _safe_close_descriptor(rfd, cleanup_failures, "registry_fd")

        if retained_dir_fd is not None:
            dfd = retained_dir_fd
            retained_dir_fd = None
            _safe_close_descriptor(dfd, cleanup_failures, "registry_dir_fd")

        if cleanup_failures:
            if primary_exc is not None:
                if isinstance(primary_exc, SignerLedgerError):
                    if getattr(primary_exc, "primary_error", None) is None:
                        primary_exc.primary_error = getattr(primary_exc, "__cause__", None) or primary_exc
                    for _, _, err, _ in cleanup_failures:
                        primary_exc.cleanup_errors.append(err)
                else:
                    durability_err = SignerLedgerDurabilityError(
                        f"ERR_REGISTRY_ORCHESTRATION_FAILURE: operation failed with {type(primary_exc).__name__}: {primary_exc}",
                        primary_error=primary_exc,
                        cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                    )
                    durability_err.__cause__ = primary_exc
                    raise durability_err from primary_exc
            else:
                err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
                raise SignerLedgerDurabilityError(
                    f"ERR_REGISTRY_DESCRIPTOR_CLEANUP_FAILED: descriptor cleanup failed: {err_details}",
                    cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                )


# ==============================================================================
# Gate 3-I10: Local-Test Descriptor-Anchored Revocation Issuer Key Reader
# ==============================================================================

REVOCATION_ISSUER_PUBLIC_KEY_FILENAME: str = "revocation_issuer.pub.pem"
MAX_ISSUER_KEY_FILE_BYTES: int = 16384  # 16 KiB limit per Workorder G07-AUTH-SIGNER-GATE3-I10


def load_revocation_issuer_public_key_for_local_test(
    trust_directory: str | Path,
) -> Ed25519PublicKey:
    """Loads and parses an Ed25519 public key from revocation_issuer.pub.pem in trust_directory.

    Strict boundary:
    - Pure test-local primitive beneath temporary root; no production trust-root loading.
    - Descriptor-anchored traversal via _open_anchor_dir_under_temp_root().
    - Opened with O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC.
    - Enforces regular file, UID match, nlink == 1, exact mode 0600, 1 <= size <= 16384 bytes.
    - Bounded chunked read with EOF probe, post-read fstat stability check, single-close POSIX cleanup.
    - Parses strictly valid UTF-8 PEM public key, exactly one block, no trailing data, no private keys.
    - Rejects non-Ed25519 algorithms, multi-PEM files, malformed encodings.
    - Returns freshly parsed Ed25519PublicKey only after all descriptors are closed successfully.
    """
    # 1. Parameter validation
    if not isinstance(trust_directory, (str, Path)) or (
        isinstance(trust_directory, str) and not trust_directory.strip()
    ) or str(trust_directory).strip() == "":
        raise SignerLedgerError("ERR_INVALID_TRUST_DIR: trust_directory must be a non-empty string or Path")

    cleanup_failures: List[Tuple[str, int, Exception, str]] = []

    # 2. Descriptor-anchored traversal under temp root
    dir_fd = _open_anchor_dir_under_temp_root(trust_directory, cleanup_failures)
    retained_dir_fd: Optional[int] = dir_fd
    key_fd: Optional[int] = None
    primary_exc: Optional[BaseException] = None

    try:
        # 3. Open revocation_issuer.pub.pem relative to dir_fd
        key_flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            key_fd = os.open(REVOCATION_ISSUER_PUBLIC_KEY_FILENAME, key_flags, dir_fd=dir_fd)
        except FileNotFoundError as fnf:
            raise SignerLedgerError(
                f"ERR_ISSUER_KEY_FILE_NOT_FOUND: {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME} not found in trust_directory: {fnf}"
            ) from fnf
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise SignerLedgerError(
                    f"ERR_ISSUER_KEY_FILE_NOT_FOUND: {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME} not found: {e}"
                ) from e
            elif e.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SignerLedgerSecurityError(
                    f"ERR_ISSUER_KEY_SYMLINK_DETECTED: {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME} is a symlink: {e}"
                ) from e
            raise SignerLedgerDurabilityError(
                f"ERR_CANNOT_OPEN_ISSUER_KEY: cannot open {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME}: {e}",
                code="ERR_CANNOT_OPEN_ISSUER_KEY",
                primary_error=e,
            ) from e

        # 4. Stat & validate regular file properties
        try:
            st_before = os.fstat(key_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_ISSUER_KEY_PRE_READ_FSTAT_FAILED: pre-read fstat failed on {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME}: {fe}",
                code="ERR_ISSUER_KEY_PRE_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if not stat.S_ISREG(st_before.st_mode):
            raise SignerLedgerSecurityError(
                f"ERR_ISSUER_KEY_NOT_REGULAR_FILE: {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME} is not a regular file (mode: {oct(st_before.st_mode)})"
            )
        if hasattr(os, "getuid") and st_before.st_uid != os.getuid():
            raise SignerLedgerSecurityError(
                f"ERR_ISSUER_KEY_INVALID_OWNER: owner uid {st_before.st_uid} != current uid {os.getuid()}"
            )
        if st_before.st_nlink != 1:
            raise SignerLedgerSecurityError(
                f"ERR_ISSUER_KEY_HARDLINK_DETECTED: hard link count is {st_before.st_nlink}, must be 1"
            )
        if (st_before.st_mode & 0o777) != 0o600:
            raise SignerLedgerSecurityError(
                f"ERR_ISSUER_KEY_INSECURE_PERMS: mode must be 0600, got {oct(st_before.st_mode & 0o777)}"
            )

        if st_before.st_size <= 0:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_FILE_EMPTY: {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME} size is 0 bytes"
            )
        if st_before.st_size > MAX_ISSUER_KEY_FILE_BYTES:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_FILE_OVERSIZED: file size {st_before.st_size} exceeds {MAX_ISSUER_KEY_FILE_BYTES} bytes"
            )

        # 5. Bounded chunked read with EOF probe
        captured = b""
        read_budget = st_before.st_size + 1
        while len(captured) < read_budget:
            try:
                chunk = os.read(key_fd, min(4096, read_budget - len(captured)))
            except OSError as re:
                if re.errno == errno.EINTR:
                    continue
                raise SignerLedgerDurabilityError(
                    f"ERR_ISSUER_KEY_READ_FAILED: failed reading file: {re}",
                    code="ERR_ISSUER_KEY_READ_FAILED",
                    primary_error=re,
                ) from re
            if not chunk:
                break
            captured += chunk

        if len(captured) > st_before.st_size:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_FILE_OVERSIZED: file grew during read (read {len(captured)} vs expected {st_before.st_size})"
            )
        if len(captured) != st_before.st_size:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_SHORT_READ: read {len(captured)} bytes vs st_size {st_before.st_size}"
            )

        # Metadata stability check
        try:
            st_after = os.fstat(key_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_ISSUER_KEY_POST_READ_FSTAT_FAILED: post-read fstat failed on {REVOCATION_ISSUER_PUBLIC_KEY_FILENAME}: {fe}",
                code="ERR_ISSUER_KEY_POST_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if (
            st_after.st_size != st_before.st_size
            or st_after.st_ino != st_before.st_ino
            or st_after.st_dev != st_before.st_dev
            or st_after.st_mtime_ns != st_before.st_mtime_ns
        ):
            raise SignerLedgerCorruptionError("ERR_ISSUER_KEY_MUTATED_DURING_READ: metadata changed during read")

        # 6. Close key_fd promptly
        kfd = key_fd
        key_fd = None
        _safe_close_descriptor(kfd, cleanup_failures, "issuer_key_fd")

        # Also close dir_fd promptly before parsing and returning
        dfd = retained_dir_fd
        retained_dir_fd = None
        _safe_close_descriptor(dfd, cleanup_failures, "trust_dir_fd")

        # Strict descriptor close check: abort before parsing if any descriptor close failed!
        if cleanup_failures:
            err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
            durability_err = SignerLedgerDurabilityError(
                f"ERR_ISSUER_KEY_CLOSE_FAILED: failed closing descriptor before parsing: {err_details}",
                code="ERR_ISSUER_KEY_CLOSE_FAILED",
                primary_error=cleanup_failures[0][2],
                cleanup_errors=[err for _, _, err, _ in cleanup_failures],
            )
            durability_err.__cause__ = cleanup_failures[0][2]
            raise durability_err

        # 7. Decode and validate PEM format strictly
        try:
            text = captured.decode("utf-8")
        except UnicodeDecodeError as ue:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_INVALID_ENCODING: PEM is not valid UTF-8: {ue}"
            ) from ue

        # Reject private key PEM strictly
        if "PRIVATE KEY" in text:
            raise SignerLedgerSecurityError(
                "ERR_ISSUER_KEY_PRIVATE_KEY_PROHIBITED: private key material is strictly prohibited in issuer public key file"
            )

        begin_marker = "-----BEGIN PUBLIC KEY-----"
        end_marker = "-----END PUBLIC KEY-----"

        if text.count("-----BEGIN") > 1 or text.count("-----END") > 1:
            raise SignerLedgerCorruptionError(
                "ERR_ISSUER_KEY_MULTIPLE_OR_MALFORMED_PEM: multiple PEM blocks detected"
            )

        if begin_marker not in text or end_marker not in text:
            raise SignerLedgerCorruptionError(
                "ERR_ISSUER_KEY_MALFORMED: missing standard public key PEM delimiters"
            )

        begin_pos = text.find(begin_marker)
        end_pos = text.find(end_marker)

        if begin_pos > end_pos:
            raise SignerLedgerCorruptionError("ERR_ISSUER_KEY_MALFORMED: invalid PEM delimiter ordering")

        if text[:begin_pos].strip() != "":
            raise SignerLedgerCorruptionError("ERR_ISSUER_KEY_MALFORMED: unexpected data before PEM BEGIN delimiter")

        after_end = text[end_pos + len(end_marker):]
        if after_end.strip() != "":
            raise SignerLedgerCorruptionError("ERR_ISSUER_KEY_TRAILING_DATA: unexpected trailing data after PEM END delimiter")

        # 8. Parse public key using cryptography
        try:
            pub_key = load_pem_public_key(captured)
        except Exception as pe:
            raise SignerLedgerCorruptionError(
                f"ERR_ISSUER_KEY_MALFORMED_PEM: failed parsing PEM public key: {pe}"
            ) from pe

        if not isinstance(pub_key, Ed25519PublicKey):
            raise SignerLedgerSecurityError(
                f"ERR_ISSUER_KEY_ALGORITHM_INVALID: expected Ed25519PublicKey, got {type(pub_key).__name__}"
            )

        return pub_key

    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        if key_fd is not None:
            kfd = key_fd
            key_fd = None
            _safe_close_descriptor(kfd, cleanup_failures, "issuer_key_fd")

        if retained_dir_fd is not None:
            dfd = retained_dir_fd
            retained_dir_fd = None
            _safe_close_descriptor(dfd, cleanup_failures, "trust_dir_fd")

        if cleanup_failures:
            if primary_exc is not None:
                if isinstance(primary_exc, SignerLedgerError):
                    if getattr(primary_exc, "primary_error", None) is None:
                        primary_exc.primary_error = getattr(primary_exc, "__cause__", None) or primary_exc
                    for _, _, err, _ in cleanup_failures:
                        primary_exc.cleanup_errors.append(err)
                else:
                    durability_err = SignerLedgerDurabilityError(
                        f"ERR_ISSUER_KEY_ORCHESTRATION_FAILURE: operation failed with {type(primary_exc).__name__}: {primary_exc}",
                        primary_error=primary_exc,
                        cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                    )
                    durability_err.__cause__ = primary_exc
                    raise durability_err from primary_exc
            else:
                err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
                raise SignerLedgerDurabilityError(
                    f"ERR_ISSUER_KEY_DESCRIPTOR_CLEANUP_FAILED: descriptor cleanup failed: {err_details}",
                    cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                )


# ==============================================================================
# Gate 3-I11: Local-Test Revocation Trust-and-Registry Composition Core
# ==============================================================================

def load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
    trust_directory: str | Path,
    registry_directory: str | Path,
    verification_time_utc: datetime,
    advance_anchor: bool = True,
    *,
    require_existing_anchor: bool = False,
) -> RevocationManifestVerificationResult:
    """Gate 3-I11: Composes I10 local-test key reader and I9 fresh registry reader for local tests.

    Strict boundary:
    - Pure local test primitive beneath temporary root; no production trust root.
    - Validates verification_time_utc as exact built-in datetime with UTC offset zero.
    - Validates directory parameters as non-empty str | Path.
    - Calls load_revocation_issuer_public_key_for_local_test(trust_directory) exactly once.
    - If key loading fails, stops immediately without invoking registry reading, verification, or anchoring.
    - Calls load_verify_and_anchor_revocation_manifest_for_local_test() exactly once with the freshly read key.
    - Returns the exact verification result produced by I9 only after anchor update succeeds.
    - No caching of keys or verification results.
    """
    # 1. Verification time validation
    if type(require_existing_anchor) is not bool:
        raise SignerLedgerError(
            f"ERR_INVALID_PARAMETER: require_existing_anchor must be exact built-in bool, got {type(require_existing_anchor).__name__}",
            code="ERR_INVALID_PARAMETER",
        )
    if type(verification_time_utc) is not datetime or verification_time_utc.tzinfo is None:
        raise RevocationManifestVerificationError(
            "ERR_VERIFICATION_TIME_INVALID: verification_time_utc must be a timezone-aware datetime"
        )
    if verification_time_utc.utcoffset() != timedelta(0):
        raise RevocationManifestVerificationError(
            "ERR_VERIFICATION_TIME_INVALID: verification_time_utc must have a UTC offset of zero"
        )

    # 2. Directory parameters validation
    if not isinstance(trust_directory, (str, Path)) or (
        isinstance(trust_directory, str) and not trust_directory.strip()
    ) or str(trust_directory).strip() == "":
        raise SignerLedgerError("ERR_INVALID_TRUST_DIR: trust_directory must be a non-empty string or Path")

    if not isinstance(registry_directory, (str, Path)) or (
        isinstance(registry_directory, str) and not registry_directory.strip()
    ) or str(registry_directory).strip() == "":
        raise SignerLedgerError("ERR_INVALID_REGISTRY_DIR: registry_directory must be a non-empty string or Path")

    # 3. Read revocation issuer public key via I10 reader
    issuer_public_key = load_revocation_issuer_public_key_for_local_test(trust_directory)

    # 4. Read, verify, and anchor manifest via I9 reader
    return load_verify_and_anchor_revocation_manifest_for_local_test(
        registry_directory=registry_directory,
        issuer_public_key=issuer_public_key,
        verification_time_utc=verification_time_utc,
        advance_anchor=advance_anchor,
        require_existing_anchor=require_existing_anchor,
    )





# ==============================================================================
# Gate 3-I12: Local-Test Authorization Admission Cross-Binding Core
# ==============================================================================

@dataclass(frozen=True)
class TrialAuthorizationAdmissionEvidence:
    """Immutable verification evidence cross-binding Director, Operator, and Revocation results.

    Contains only verified results, identifiers, and cross-bound digests for audit.
    Explicitly carries zero authorization tokens, HMAC keys, private keys,
    signing capabilities, nonce-ledger handles, or dispatch capabilities.
    """
    verified: bool
    director_result: DirectorApprovalVerificationResult
    operator_result: OperatorConsentVerificationResult
    revocation_result: RevocationManifestVerificationResult
    proposal_id: str
    approval_id: str
    director_key_id: str
    operator_id: str
    operator_key_id: str
    challenge_id: str
    client_nonce: str
    plan_digest_sha256: str
    approval_envelope_digest_sha256: str
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    source_file_sha256: str
    revocation_epoch: int
    revocation_canonical_claims_digest_sha256: str

    def __bool__(self) -> bool:
        return self.verified


def _validate_directory_lexically_under_temp_root(
    path: Any,
    param_name: str,
    error_code: str,
    error_cls: type[TrialSignerError] = SignerAdmissionVerificationError,
) -> str:
    """Validates that path is a string/Path, non-empty, without whitespace,
    and lexically within the system temporary directory without touching the filesystem.

    Raises error_cls immediately if invalid (I12-R1-05).
    """
    if not isinstance(path, (str, Path)):
        raise error_cls(
            error_code,
            f"{param_name} must be str or Path, got {type(path).__name__}",
        )
    raw_str = str(path)
    if not raw_str or raw_str != raw_str.strip():
        raise error_cls(
            error_code,
            f"{param_name} must be non-empty without whitespace, got {path!r}",
        )
    for banned in ("/var/lib/resolveflow", "/run/resolveflow"):
        if banned in raw_str:
            raise error_cls(
                error_code,
                f"production path forbidden in local test primitive: {raw_str}",
            )
    norm_candidate = os.path.normpath(raw_str)
    if not os.path.isabs(norm_candidate):
        raise error_cls(
            error_code,
            f"{param_name} must be an absolute path, got {raw_str!r}",
        )
    sys_temp = os.path.normpath(tempfile.gettempdir())
    real_temp = os.path.normpath(os.path.realpath(sys_temp))
    is_under_sys = norm_candidate == sys_temp or norm_candidate.startswith(sys_temp + os.sep)
    is_under_real = norm_candidate == real_temp or norm_candidate.startswith(real_temp + os.sep)
    if not (is_under_sys or is_under_real):
        raise error_cls(
            error_code,
            f"{param_name} must be within system temporary directory {real_temp!r}, got {raw_str!r}",
        )
    return norm_candidate


def assemble_canonical_action_payload_for_admission(
    *,
    action: PlannedTimelineAction,
    execution_plan: TimelineExecutionPlan,
    authorization_id: str,
    execution_id: str,
) -> Dict[str, Any]:
    """Assembles the canonical 16-key action dispatch payload from source-owned DTOs for admission cross-binding.

    Defined in 07B5 §2.6 and 07B7 §3.1 / §8.1.
    All 16 fields are bound directly to source-owned data:
    - action geometry and track from PlannedTimelineAction in execution_plan.planned_actions
    - target project/timeline UIDs and idempotency key from execution_plan
    - authorization_id from authoritative pre-allocated future authorization_id
    - execution_id from authoritative pre-allocated future execution_id
    - schema_version pinned to '1.0'
    """
    if not isinstance(action, PlannedTimelineAction):
        raise SignerAdmissionVerificationError(
            "ERR_INVALID_ACTION_OBJECT",
            f"action must be PlannedTimelineAction, got {type(action).__name__}",
        )
    if not isinstance(execution_plan, TimelineExecutionPlan):
        raise SignerAdmissionVerificationError(
            "ERR_INVALID_PLAN_OBJECT",
            f"execution_plan must be TimelineExecutionPlan, got {type(execution_plan).__name__}",
        )
    if (
        type(authorization_id) is not str
        or not authorization_id
        or authorization_id != authorization_id.strip()
    ):
        raise SignerAdmissionVerificationError(
            "ERR_INVALID_AUTHORIZATION_ID",
            f"authorization_id must be non-empty str without whitespace padding, got {authorization_id!r}",
        )
    if (
        type(execution_id) is not str
        or not execution_id
        or execution_id != execution_id.strip()
    ):
        raise SignerAdmissionVerificationError(
            "ERR_INVALID_EXECUTION_ID",
            f"execution_id must be non-empty str without whitespace padding, got {execution_id!r}",
        )

    payload: Dict[str, Any] = {
        "action_index": action.action_index,
        "action_type": action.action_type,
        "authorization_id": authorization_id,
        "duration_frames": action.timeline_range.duration_frames,
        "execution_id": execution_id,
        "plan_idempotency_key": execution_plan.idempotency_key,
        "record_frame": action.timeline_range.start_frame,
        "role": action.role,
        "schema_version": "1.0",
        "source_frame_duration": action.source_range.duration_frames,
        "source_frame_start": action.source_range.start_frame,
        "source_path": action.source_file_path,
        "target_project_uid": execution_plan.target_spec.project_id,
        "target_timeline_uid": execution_plan.target_spec.timeline_id,
        "track_index": action.target_track_index,
        "track_type": action.target_track_type,
    }

    # Validate against canonical schema and primitive type gates immediately
    try:
        canonical_action_payload_bytes(payload)
    except Exception as exc:
        raise SignerAdmissionVerificationError(
            "ERR_ACTION_PAYLOAD_SCHEMA_INVALID",
            f"Canonical action payload validation failed: {exc}",
        ) from exc

    return payload


def compute_canonical_action_payload_digest_for_admission(
    *,
    action: PlannedTimelineAction,
    execution_plan: TimelineExecutionPlan,
    authorization_id: str,
    execution_id: str,
) -> str:
    """Computes the 64-character lowercase SHA-256 digest of the 16-key canonical action payload."""
    payload = assemble_canonical_action_payload_for_admission(
        action=action,
        execution_plan=execution_plan,
        authorization_id=authorization_id,
        execution_id=execution_id,
    )
    payload_bytes = canonical_action_payload_bytes(payload)
    return hashlib.sha256(payload_bytes).hexdigest().lower()


def verify_trial_authorization_admission_for_local_test(
    *,
    approval_envelope: SignedApprovalEnvelope,
    proposal: EditPlanProposal,
    execution_plan: TimelineExecutionPlan,
    director_public_key: Ed25519PublicKey,
    expected_director_key_id: str,
    operator_consent_envelope: SignedOperatorConsentEnvelope,
    operator_context: OperatorConsentVerificationContext,
    operator_public_key: Ed25519PublicKey,
    revocation_trust_directory: str | Path,
    revocation_registry_directory: str | Path,
    verification_time_utc: datetime,
) -> TrialAuthorizationAdmissionEvidence:
    """Gate 3-I12: Local-test-only authorization admission cross-binding preflight.

    Composes the Director approval verifier (I4), Operator consent verifier (I5),
    and Revocation trust/registry composition path (I11).
    Independently cross-binds results and fails closed if any source, plan, approval,
    operator, target, or revocation identity disagrees.

    Produces verification evidence only.
    Carries zero authorization tokens, HMAC keys, private keys, signing capabilities,
    nonce-ledger handles, or dispatch capabilities.
    """
    # 1. Lexical directory path policy checks FIRST before any file access or verifier call (I12-R1-05)
    _validate_directory_lexically_under_temp_root(
        revocation_trust_directory,
        "revocation_trust_directory",
        "ERR_TRUST_DIR_INVALID",
    )
    _validate_directory_lexically_under_temp_root(
        revocation_registry_directory,
        "revocation_registry_directory",
        "ERR_REGISTRY_DIR_INVALID",
    )

    # 2. Exact nominal type gates
    if type(approval_envelope) is not SignedApprovalEnvelope:
        raise SignerAdmissionVerificationError(
            "ERR_APPROVAL_ENVELOPE_TYPE",
            f"approval_envelope must be exact SignedApprovalEnvelope, got {type(approval_envelope).__name__}",
        )
    if type(proposal) is not EditPlanProposal:
        raise SignerAdmissionVerificationError(
            "ERR_PROPOSAL_TYPE",
            f"proposal must be exact EditPlanProposal, got {type(proposal).__name__}",
        )
    if type(execution_plan) is not TimelineExecutionPlan:
        raise SignerAdmissionVerificationError(
            "ERR_PLAN_TYPE",
            f"execution_plan must be exact TimelineExecutionPlan, got {type(execution_plan).__name__}",
        )

    # Validate provider-backed concrete key instances, rejecting custom subclasses (I12-R1-01)
    director_public_key = _normalize_and_validate_ed25519_public_key(
        director_public_key,
        SignerAdmissionVerificationError,
        "ERR_DIRECTOR_PUBLIC_KEY_TYPE",
        "director_public_key",
    )
    if (
        type(expected_director_key_id) is not str
        or not expected_director_key_id
        or expected_director_key_id != expected_director_key_id.strip()
    ):
        raise SignerAdmissionVerificationError(
            "ERR_EXPECTED_DIRECTOR_KEY_ID_FORMAT",
            f"expected_director_key_id must be non-empty str without whitespace, got {expected_director_key_id!r}",
        )

    if type(operator_consent_envelope) is not SignedOperatorConsentEnvelope:
        raise SignerAdmissionVerificationError(
            "ERR_OPERATOR_CONSENT_ENVELOPE_TYPE",
            f"operator_consent_envelope must be exact SignedOperatorConsentEnvelope, got {type(operator_consent_envelope).__name__}",
        )
    if type(operator_context) is not OperatorConsentVerificationContext:
        raise SignerAdmissionVerificationError(
            "ERR_OPERATOR_CONTEXT_TYPE",
            f"operator_context must be exact OperatorConsentVerificationContext, got {type(operator_context).__name__}",
        )

    operator_public_key = _normalize_and_validate_ed25519_public_key(
        operator_public_key,
        SignerAdmissionVerificationError,
        "ERR_OPERATOR_PUBLIC_KEY_TYPE",
        "operator_public_key",
    )

    if type(verification_time_utc) is not datetime:
        raise SignerAdmissionVerificationError(
            "ERR_VERIFICATION_TIME_INVALID",
            f"verification_time_utc must be exact built-in datetime, got {type(verification_time_utc).__name__}",
        )
    if verification_time_utc.tzinfo is None or verification_time_utc.utcoffset() != timedelta(0):
        raise SignerAdmissionVerificationError(
            "ERR_VERIFICATION_TIME_INVALID",
            f"verification_time_utc must be UTC with offset zero, got {verification_time_utc!r}",
        )

    # 3. Strict object identity check for operator context
    if operator_context.approval_envelope is not approval_envelope:
        raise SignerAdmissionVerificationError(
            "ERR_CONTEXT_APPROVAL_ENVELOPE_MISMATCH",
            "operator_context.approval_envelope must be the exact identical object reference as approval_envelope",
        )
    if operator_context.execution_plan is not execution_plan:
        raise SignerAdmissionVerificationError(
            "ERR_CONTEXT_PLAN_MISMATCH",
            "operator_context.execution_plan must be the exact identical object reference as execution_plan",
        )

    # 4. Director Approval Verification (I4)
    director_result = verify_director_approval(
        envelope=approval_envelope,
        proposal=proposal,
        plan=execution_plan,
        public_key=director_public_key,
        expected_director_key_id=expected_director_key_id,
    )

    # 5. Operator Consent Verification (I5)
    operator_result = verify_operator_consent(
        envelope=operator_consent_envelope,
        context=operator_context,
        public_key=operator_public_key,
        verification_time_utc=verification_time_utc,
    )

    # 6. Revocation Manifest Verification (I11 -> I10 + I9 -> I7 + I8) without mutating durable anchor (I12-R1-03)
    revocation_result = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
        trust_directory=revocation_trust_directory,
        registry_directory=revocation_registry_directory,
        verification_time_utc=verification_time_utc,
        advance_anchor=False,
    )

    # 7. Cross-binding verifications
    # 7a. Director approval ID and proposal ID match signed record and proposal
    if not _safe_str_compare_digest(director_result.approval_id, approval_envelope.approval_record.approval_id):
        raise SignerAdmissionVerificationError(
            "ERR_APPROVAL_ID_MISMATCH",
            f"Director result approval ID '{director_result.approval_id}' != approval record '{approval_envelope.approval_record.approval_id}'",
        )
    if not _safe_str_compare_digest(director_result.proposal_id, proposal.proposal_id):
        raise SignerAdmissionVerificationError(
            "ERR_PROPOSAL_ID_MISMATCH",
            f"Director result proposal ID '{director_result.proposal_id}' != proposal '{proposal.proposal_id}'",
        )
    if not _safe_str_compare_digest(approval_envelope.approval_record.proposal_id, proposal.proposal_id):
        raise SignerAdmissionVerificationError(
            "ERR_PROPOSAL_ID_MISMATCH",
            f"Approval record proposal ID '{approval_envelope.approval_record.proposal_id}' != proposal '{proposal.proposal_id}'",
        )

    # 7b. Plan digest equality across Director result, Operator result, and recomputed canonical digest
    recomputed_plan_digest = compute_canonical_plan_digest(execution_plan)
    if not _safe_str_compare_digest(director_result.plan_digest_sha256.lower(), operator_result.plan_digest_sha256.lower()):
        raise SignerAdmissionVerificationError(
            "ERR_PLAN_DIGEST_MISMATCH",
            f"Director result plan digest '{director_result.plan_digest_sha256}' != Operator result plan digest '{operator_result.plan_digest_sha256}'",
        )
    if not _safe_str_compare_digest(director_result.plan_digest_sha256.lower(), recomputed_plan_digest.lower()):
        raise SignerAdmissionVerificationError(
            "ERR_PLAN_DIGEST_MISMATCH",
            f"Director result plan digest '{director_result.plan_digest_sha256}' != recomputed canonical plan digest '{recomputed_plan_digest}'",
        )

    # 7c. Operator result approval envelope digest equals recomputed digest from exact approval envelope
    recomputed_approval_digest = approval_envelope.compute_envelope_digest()
    if not _safe_str_compare_digest(operator_result.approval_envelope_digest_sha256.lower(), recomputed_approval_digest.lower()):
        raise SignerAdmissionVerificationError(
            "ERR_APPROVAL_ENVELOPE_DIGEST_MISMATCH",
            f"Operator result approval digest '{operator_result.approval_envelope_digest_sha256}' != recomputed approval digest '{recomputed_approval_digest}'",
        )

    # 7d. Operator result key/operator/challenge/client-nonce IDs match consent envelope and challenge context (I12-R1-04)
    if (
        not _safe_str_compare_digest(operator_result.operator_id, operator_consent_envelope.operator_id)
        or not _safe_str_compare_digest(operator_result.operator_id, operator_context.authoritative_operator_id)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_OPERATOR_IDENTITY_MISMATCH",
            f"Operator result operator_id '{operator_result.operator_id}' mismatch with consent envelope or authoritative context",
        )
    if (
        not _safe_str_compare_digest(operator_result.operator_key_id, operator_consent_envelope.operator_key_id)
        or not _safe_str_compare_digest(operator_result.operator_key_id, operator_context.expected_operator_key_id)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_OPERATOR_KEY_ID_MISMATCH",
            f"Operator result key_id '{operator_result.operator_key_id}' mismatch with consent envelope or expected context",
        )
    if (
        not _safe_str_compare_digest(operator_result.challenge_id, operator_consent_envelope.challenge_id)
        or not _safe_str_compare_digest(operator_result.challenge_id, operator_context.challenge.challenge_id)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CHALLENGE_ID_MISMATCH",
            f"Operator result challenge_id '{operator_result.challenge_id}' mismatch with consent envelope or challenge context",
        )
    if (
        not _safe_str_compare_digest(operator_result.client_nonce, operator_consent_envelope.client_nonce)
        or not _safe_str_compare_digest(operator_result.client_nonce, operator_context.challenge.client_nonce)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CLIENT_NONCE_MISMATCH",
            f"Operator result client_nonce '{operator_result.client_nonce}' mismatch with consent envelope or challenge context",
        )

    # 7e. Target UIDs and names bound directly to approved execution_plan.target_spec (I12-R1-02)
    plan_target = execution_plan.target_spec
    if (
        not _safe_str_compare_digest(operator_result.target_project_uid, plan_target.project_id)
        or not _safe_str_compare_digest(operator_consent_envelope.target_project_uid, plan_target.project_id)
        or not _safe_str_compare_digest(operator_context.expected_target_project_uid, plan_target.project_id)
        or not _safe_str_compare_digest(operator_consent_envelope.target_project_name, plan_target.project_name)
        or not _safe_str_compare_digest(operator_context.expected_target_project_name, plan_target.project_name)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_TARGET_MUTATION_MISMATCH",
            f"Target project UID/name in consent ({operator_consent_envelope.target_project_uid!r}, {operator_consent_envelope.target_project_name!r}) "
            f"does not match execution_plan.target_spec ({plan_target.project_id!r}, {plan_target.project_name!r})",
        )
    if (
        not _safe_str_compare_digest(operator_result.target_timeline_uid, plan_target.timeline_id)
        or not _safe_str_compare_digest(operator_consent_envelope.target_timeline_uid, plan_target.timeline_id)
        or not _safe_str_compare_digest(operator_context.expected_target_timeline_uid, plan_target.timeline_id)
        or not _safe_str_compare_digest(operator_consent_envelope.target_timeline_name, plan_target.timeline_name)
        or not _safe_str_compare_digest(operator_context.expected_target_timeline_name, plan_target.timeline_name)
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_TARGET_MUTATION_MISMATCH",
            f"Target timeline UID/name in consent ({operator_consent_envelope.target_timeline_uid!r}, {operator_consent_envelope.target_timeline_name!r}) "
            f"does not match execution_plan.target_spec ({plan_target.timeline_id!r}, {plan_target.timeline_name!r})",
        )

    # 7f. Authorized action claims bound directly to planned actions in execution_plan (I12-R1-02)
    matching_action = None
    for action in execution_plan.planned_actions:
        if (
            action.action_type == operator_consent_envelope.action_type
            and action.target_track_type == operator_consent_envelope.track_type
            and action.target_track_index == operator_consent_envelope.track_index
            and action.source_file_path == operator_consent_envelope.source_file_path
            and action.timeline_range.start_frame == operator_consent_envelope.record_frame
            and action.timeline_range.duration_frames == operator_consent_envelope.duration_frames
        ):
            matching_action = action
            break

    if matching_action is None:
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_ACTION_NOT_IN_PLAN",
            f"Operator consent action ({operator_consent_envelope.action_type}, "
            f"track={operator_consent_envelope.track_type}:{operator_consent_envelope.track_index}, "
            f"source={operator_consent_envelope.source_file_path}, "
            f"record_frame={operator_consent_envelope.record_frame}, "
            f"duration={operator_consent_envelope.duration_frames}) does not match any planned action in execution_plan",
        )

    # 7e. Pre-allocated authorization and execution identity verification & anti-aliasing gates (I12-R3-01, 07B5 §2.3.1, 07B7 §8.1)
    if (
        operator_context.expected_authorization_id is None
        or not operator_context.expected_authorization_id
    ):
        raise SignerAdmissionVerificationError(
            "ERR_MISSING_AUTHORIZATION_ID",
            "operator_context must provide non-empty expected_authorization_id for canonical action binding",
        )
    if (
        operator_context.expected_execution_id is None
        or not operator_context.expected_execution_id
    ):
        raise SignerAdmissionVerificationError(
            "ERR_MISSING_EXECUTION_ID",
            "operator_context must provide non-empty expected_execution_id for canonical action binding",
        )

    # Strictly prohibit aliasing approval_id to authorization_id (07B5 §2.3.1, 07B7 §8.1)
    if _safe_str_compare_digest(
        operator_context.expected_authorization_id,
        approval_envelope.approval_record.approval_id,
    ):
        raise SignerAdmissionVerificationError(
            "ERR_IDENTITY_ALIASING",
            f"expected_authorization_id {operator_context.expected_authorization_id!r} "
            f"must not alias approval_id {approval_envelope.approval_record.approval_id!r}",
        )

    # Strictly prohibit aliasing client_nonce to execution_id (07B7 §8.1, I12-R3-01)
    if _safe_str_compare_digest(
        operator_context.expected_execution_id,
        operator_consent_envelope.client_nonce,
    ):
        raise SignerAdmissionVerificationError(
            "ERR_IDENTITY_ALIASING",
            f"expected_execution_id {operator_context.expected_execution_id!r} "
            f"must not alias client_nonce {operator_consent_envelope.client_nonce!r}",
        )

    if _safe_str_compare_digest(
        operator_context.expected_authorization_id,
        operator_context.expected_execution_id,
    ):
        raise SignerAdmissionVerificationError(
            "ERR_IDENTITY_ALIASING",
            f"expected_authorization_id {operator_context.expected_authorization_id!r} "
            f"must not alias expected_execution_id {operator_context.expected_execution_id!r}",
        )

    # Immutable challenge binding check (I12-R4-01)
    if (
        operator_context.expected_authorization_id != operator_context.challenge.preallocated_authorization_id
        or operator_context.expected_execution_id != operator_context.challenge.preallocated_execution_id
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CHALLENGE_IDENTITY_MISMATCH",
            "operator_context expected identities do not match challenge preallocated identities",
        )

    # Recompute canonical action payload digest from matched planned action and plan/target claims (I12-R2-02, I12-R3-01)
    recomputed_action_payload_digest = compute_canonical_action_payload_digest_for_admission(
        action=matching_action,
        execution_plan=execution_plan,
        authorization_id=operator_context.expected_authorization_id,
        execution_id=operator_context.expected_execution_id,
    )

    if not _safe_str_compare_digest(
        operator_consent_envelope.action_payload_digest_sha256.lower(),
        recomputed_action_payload_digest.lower(),
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_ACTION_PAYLOAD_DIGEST_MISMATCH",
            f"Operator consent action_payload_digest_sha256 '{operator_consent_envelope.action_payload_digest_sha256}' "
            f"does not match recomputed canonical action payload digest '{recomputed_action_payload_digest}'",
        )

    if not _safe_str_compare_digest(
        operator_result.action_payload_digest_sha256.lower(),
        recomputed_action_payload_digest.lower(),
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_ACTION_PAYLOAD_DIGEST_MISMATCH",
            f"Operator verification result action_payload_digest_sha256 '{operator_result.action_payload_digest_sha256}' "
            f"does not match recomputed canonical action payload digest '{recomputed_action_payload_digest}'",
        )

    if not _safe_str_compare_digest(
        operator_context.expected_action_payload_digest_sha256.lower(),
        recomputed_action_payload_digest.lower(),
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_ACTION_PAYLOAD_DIGEST_MISMATCH",
            f"Operator context expected_action_payload_digest_sha256 '{operator_context.expected_action_payload_digest_sha256}' "
            f"does not match recomputed canonical action payload digest '{recomputed_action_payload_digest}'",
        )

    # Verify operator context expectations match consent envelope
    if (
        not _safe_str_compare_digest(operator_result.source_file_sha256.lower(), operator_context.expected_source_file_sha256.lower())
        or not _safe_str_compare_digest(operator_consent_envelope.source_file_sha256.lower(), operator_context.expected_source_file_sha256.lower())
        or not _safe_str_compare_digest(operator_consent_envelope.source_file_path, operator_context.expected_source_file_path)
        or operator_consent_envelope.action_type != operator_context.expected_action_type
        or operator_consent_envelope.track_type != operator_context.expected_track_type
        or operator_consent_envelope.track_index != operator_context.expected_track_index
        or operator_consent_envelope.record_frame != operator_context.expected_record_frame
        or operator_consent_envelope.duration_frames != operator_context.expected_duration_frames
    ):
        raise SignerAdmissionVerificationError(
            "ERR_CONSENT_TARGET_MUTATION_MISMATCH",
            "Action claims mismatch between consent envelope and operator context",
        )

    # Bind source file SHA256 against execution_plan.source_digests if present
    plan_source_digests = [
        sd for sd in execution_plan.source_digests
        if sd.absolute_path == operator_consent_envelope.source_file_path
    ]
    if plan_source_digests:
        if not any(
            _safe_str_compare_digest(sd.full_sha256.lower(), operator_consent_envelope.source_file_sha256.lower())
            for sd in plan_source_digests
        ):
            raise SignerAdmissionVerificationError(
                "ERR_CONSENT_SOURCE_DIGEST_MISMATCH",
                f"Source file digest '{operator_consent_envelope.source_file_sha256}' does not match execution_plan.source_digests for '{operator_consent_envelope.source_file_path}'",
            )

    # 7g. Revocation checks: manifest does not revoke Director or Operator key ID
    manifest = revocation_result.manifest
    if (
        expected_director_key_id in manifest.revoked_director_key_ids
        or director_result.director_key_id in manifest.revoked_director_key_ids
        or approval_envelope.director_key_id in manifest.revoked_director_key_ids
    ):
        raise SignerAdmissionVerificationError(
            "ERR_DIRECTOR_KEY_REVOKED",
            f"Director key '{expected_director_key_id}' is revoked in manifest epoch {manifest.epoch}",
        )
    if (
        operator_context.expected_operator_key_id in manifest.revoked_operator_key_ids
        or operator_result.operator_key_id in manifest.revoked_operator_key_ids
        or operator_consent_envelope.operator_key_id in manifest.revoked_operator_key_ids
    ):
        raise SignerAdmissionVerificationError(
            "ERR_OPERATOR_KEY_REVOKED",
            f"Operator key '{operator_context.expected_operator_key_id}' is revoked in manifest epoch {manifest.epoch}",
        )

    # 8. Construct and return immutable verification evidence
    return TrialAuthorizationAdmissionEvidence(
        verified=True,
        director_result=director_result,
        operator_result=operator_result,
        revocation_result=revocation_result,
        proposal_id=director_result.proposal_id,
        approval_id=director_result.approval_id,
        director_key_id=director_result.director_key_id,
        operator_id=operator_result.operator_id,
        operator_key_id=operator_result.operator_key_id,
        challenge_id=operator_result.challenge_id,
        client_nonce=operator_result.client_nonce,
        plan_digest_sha256=director_result.plan_digest_sha256,
        approval_envelope_digest_sha256=operator_result.approval_envelope_digest_sha256,
        action_payload_digest_sha256=recomputed_action_payload_digest,
        target_project_uid=operator_result.target_project_uid,
        target_timeline_uid=operator_result.target_timeline_uid,
        source_file_sha256=operator_result.source_file_sha256,
        revocation_epoch=manifest.epoch,
        revocation_canonical_claims_digest_sha256=revocation_result.canonical_claims_digest_sha256,
    )


# ==============================================================================
# Gate 3-I13: Local-Test Descriptor-Anchored Signer Keystore Core
# ==============================================================================

SIGNER_HMAC_KEY_FILENAME: str = "trial_auth.key"
SIGNER_HMAC_KEY_BYTES_LEN: int = 32


class LocalTestSignerKeyHandle:
    """Opaque, redacted local-test key capability handle for verifying TrialExecutionAuthorization.

    Security limitation:
    An in-process Python object is not an OS isolation boundary. This local-test
    primitive must not claim protection against arbitrary code executing in the
    same process and must not be represented as production-ready key custody.
    The accepted R7 architecture still requires a separately identified
    _resolveflow_signer service identity and production keystore provisioning
    in a later, separately authorized workorder.
    """

    __slots__ = ("_verify_func",)

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        raise SignerLedgerSecurityError(
            "ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED: LocalTestSignerKeyHandle cannot be constructed directly. Use load_signer_key_handle_for_local_test()."
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SignerLedgerSecurityError(
            "ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED: LocalTestSignerKeyHandle cannot be constructed directly. Use load_signer_key_handle_for_local_test()."
        )

    def verify_trial_execution_authorization(self, auth: TrialExecutionAuthorization) -> bool:
        """Verifies HMAC signature of an already-constructed exact TrialExecutionAuthorization.

        Computes HMAC-SHA256 over canonical_bytes_for_auth_hmac(auth) and compares
        with auth.authorization_hmac using constant-time comparison.
        """
        verify_func = getattr(self, "_verify_func", None)
        if verify_func is None:
            raise SignerAdmissionVerificationError(
                "ERR_UNINITIALIZED_KEY_HANDLE",
                "LocalTestSignerKeyHandle has not been initialized through load_signer_key_handle_for_local_test()",
            )
        return verify_func(auth)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"LocalTestSignerKeyHandle is immutable; cannot modify attribute '{name}'")

    def __repr__(self) -> str:
        return "<LocalTestSignerKeyHandle: REDACTED>"

    def __str__(self) -> str:
        return "<LocalTestSignerKeyHandle: REDACTED>"


def load_signer_key_handle_for_local_test(
    keystore_directory: str | Path,
) -> LocalTestSignerKeyHandle:
    """Loads an opaque, redacted key handle for TrialExecutionAuthorization HMAC verification.

    Security limitation:
    An in-process Python object is not an OS isolation boundary. This local-test
    primitive must not claim protection against arbitrary code executing in the
    same process and must not be represented as production-ready key custody.
    The accepted R7 architecture still requires a separately identified
    _resolveflow_signer service identity and production keystore provisioning
    in a later, separately authorized workorder.

    Strict boundary:
    - Pure test-local primitive beneath temporary root; no production keystore loading.
    - Pure lexical check against banned production paths first, without touching the filesystem.
    - Descriptor-anchored traversal via _open_anchor_dir_under_temp_root() and O_NOFOLLOW.
    - Opened relative to verified directory descriptor using O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC.
    - Enforces regular file, UID match, nlink == 1, exact mode 0600, exactly 32 bytes.
    - Bounded read with EOF probe, post-read fstat stability check, single-close POSIX cleanup.
    - POSIX single-close semantics: relinquishes descriptor reference before close, preserves errors.
    - Returns opaque, redacted LocalTestSignerKeyHandle only after all descriptors are closed successfully.
    """
    if not isinstance(keystore_directory, (str, Path)) or (
        isinstance(keystore_directory, str) and not keystore_directory.strip()
    ) or str(keystore_directory).strip() == "":
        raise SignerLedgerError("ERR_INVALID_KEYSTORE_DIR: keystore_directory must be a non-empty string or Path")

    raw_str = str(keystore_directory)

    # 1. Lexical banned path check (zero filesystem query on candidate)
    for banned in ("/var/lib/resolveflow", "/run/resolveflow"):
        if banned in raw_str:
            raise SignerLedgerSecurityError(
                f"ERR_SIGNER_KEYSTORE_INSECURE_PATH: production path forbidden in local test primitive: {raw_str}"
            )

    cleanup_failures: List[Tuple[str, int, Exception, str]] = []

    # 2. Descriptor-anchored traversal under temp root
    dir_fd = _open_anchor_dir_under_temp_root(keystore_directory, cleanup_failures)
    retained_dir_fd: Optional[int] = dir_fd
    key_fd: Optional[int] = None
    primary_exc: Optional[BaseException] = None

    try:
        # 3. Open trial_auth.key relative to dir_fd
        key_flags = (
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            key_fd = os.open(SIGNER_HMAC_KEY_FILENAME, key_flags, dir_fd=dir_fd)
        except FileNotFoundError as fnf:
            raise SignerLedgerError(
                f"ERR_SIGNER_KEY_FILE_NOT_FOUND: {SIGNER_HMAC_KEY_FILENAME} not found in keystore_directory: {fnf}"
            ) from fnf
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise SignerLedgerError(
                    f"ERR_SIGNER_KEY_FILE_NOT_FOUND: {SIGNER_HMAC_KEY_FILENAME} not found: {e}"
                ) from e
            elif e.errno in (errno.ELOOP, errno.ENOTDIR):
                raise SignerLedgerSecurityError(
                    f"ERR_SIGNER_KEY_SYMLINK_DETECTED: {SIGNER_HMAC_KEY_FILENAME} is a symlink: {e}"
                ) from e
            raise SignerLedgerDurabilityError(
                f"ERR_CANNOT_OPEN_SIGNER_KEY: cannot open {SIGNER_HMAC_KEY_FILENAME}: {e}",
                code="ERR_CANNOT_OPEN_SIGNER_KEY",
                primary_error=e,
            ) from e

        # 4. Stat & validate regular file properties
        try:
            st_before = os.fstat(key_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_SIGNER_KEY_PRE_READ_FSTAT_FAILED: pre-read fstat failed on {SIGNER_HMAC_KEY_FILENAME}: {fe}",
                code="ERR_SIGNER_KEY_PRE_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if not stat.S_ISREG(st_before.st_mode):
            raise SignerLedgerSecurityError(
                f"ERR_SIGNER_KEY_NOT_REGULAR_FILE: {SIGNER_HMAC_KEY_FILENAME} is not a regular file (mode: {oct(st_before.st_mode)})"
            )
        if hasattr(os, "getuid") and st_before.st_uid != os.getuid():
            raise SignerLedgerSecurityError(
                f"ERR_SIGNER_KEY_INVALID_OWNER: owner uid {st_before.st_uid} != current uid {os.getuid()}"
            )
        if st_before.st_nlink != 1:
            raise SignerLedgerSecurityError(
                f"ERR_SIGNER_KEY_HARDLINK_DETECTED: hard link count is {st_before.st_nlink}, must be 1"
            )
        if stat.S_IMODE(st_before.st_mode) != 0o600:
            raise SignerLedgerSecurityError(
                f"ERR_SIGNER_KEY_INSECURE_PERMS: mode must be 0600, got {oct(stat.S_IMODE(st_before.st_mode))}"
            )

        if st_before.st_size != SIGNER_HMAC_KEY_BYTES_LEN:
            raise SignerLedgerCorruptionError(
                f"ERR_SIGNER_KEY_INVALID_LENGTH: file size {st_before.st_size} bytes != {SIGNER_HMAC_KEY_BYTES_LEN} bytes"
            )

        # 5. Bounded read with EOF probe (budget = 33 bytes)
        captured = b""
        read_budget = SIGNER_HMAC_KEY_BYTES_LEN + 1
        while len(captured) < read_budget:
            try:
                chunk = os.read(key_fd, min(4096, read_budget - len(captured)))
            except OSError as read_err:
                if read_err.errno == errno.EINTR:
                    continue
                raise SignerLedgerDurabilityError(
                    f"ERR_SIGNER_KEY_READ_FAILED: failed reading file: {read_err}",
                    code="ERR_SIGNER_KEY_READ_FAILED",
                    primary_error=read_err,
                ) from read_err
            if not chunk:
                break
            captured += chunk

        if len(captured) > SIGNER_HMAC_KEY_BYTES_LEN:
            raise SignerLedgerCorruptionError(
                f"ERR_SIGNER_KEY_FILE_OVERSIZED: file grew during read (read {len(captured)} vs expected {SIGNER_HMAC_KEY_BYTES_LEN})"
            )
        if len(captured) != SIGNER_HMAC_KEY_BYTES_LEN:
            raise SignerLedgerCorruptionError(
                f"ERR_SIGNER_KEY_SHORT_READ: read {len(captured)} bytes vs st_size {st_before.st_size}"
            )

        # 6. Post-read fstat stability check
        try:
            st_after = os.fstat(key_fd)
        except OSError as fe:
            raise SignerLedgerDurabilityError(
                f"ERR_SIGNER_KEY_POST_READ_FSTAT_FAILED: post-read fstat failed on {SIGNER_HMAC_KEY_FILENAME}: {fe}",
                code="ERR_SIGNER_KEY_POST_READ_FSTAT_FAILED",
                primary_error=fe,
            ) from fe

        if (
            st_after.st_size != st_before.st_size
            or st_after.st_ino != st_before.st_ino
            or st_after.st_dev != st_before.st_dev
            or st_after.st_mtime_ns != st_before.st_mtime_ns
        ):
            raise SignerLedgerCorruptionError("ERR_SIGNER_KEY_MUTATED_DURING_READ: metadata changed during read")

        # 7. POSIX single-close on descriptors
        kfd = key_fd
        key_fd = None
        _safe_close_descriptor(kfd, cleanup_failures, "signer_key_fd")

        dfd = retained_dir_fd
        retained_dir_fd = None
        _safe_close_descriptor(dfd, cleanup_failures, "keystore_dir_fd")

        if cleanup_failures:
            err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
            durability_err = SignerLedgerDurabilityError(
                f"ERR_SIGNER_KEY_CLOSE_FAILED: failed closing descriptor before returning handle: {err_details}",
                code="ERR_SIGNER_KEY_CLOSE_FAILED",
                primary_error=cleanup_failures[0][2],
                cleanup_errors=[err for _, _, err, _ in cleanup_failures],
            )
            durability_err.__cause__ = cleanup_failures[0][2]
            raise durability_err

        # Construct unforgeable closure over verified key bytes
        captured_key = bytes(captured)

        def _do_verify(auth: TrialExecutionAuthorization) -> bool:
            if type(auth) is not TrialExecutionAuthorization:
                raise SignerAdmissionVerificationError(
                    "ERR_INVALID_AUTH_TYPE",
                    f"expected TrialExecutionAuthorization instance, got {type(auth).__name__}",
                )
            if not isinstance(auth.authorization_hmac, str) or not re.fullmatch(r"[0-9a-f]{64}", auth.authorization_hmac):
                raise SignerAdmissionVerificationError(
                    "ERR_AUTH_CONTRACT_INVALID",
                    "authorization_hmac must be a 64-character lowercase hex string",
                )
            # canonical_bytes_for_auth_hmac internally validates auth via validate_trial_execution_authorization
            try:
                canonical_bytes = canonical_bytes_for_auth_hmac(auth)
            except Exception as e:
                raise SignerAdmissionVerificationError(
                    "ERR_AUTH_CONTRACT_INVALID",
                    f"authorization contract validation failed: {e}",
                ) from e

            expected_hmac = hmac.new(captured_key, canonical_bytes, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected_hmac, auth.authorization_hmac)

        handle = object.__new__(LocalTestSignerKeyHandle)
        object.__setattr__(handle, "_verify_func", _do_verify)
        return handle

    except BaseException as exc:
        primary_exc = exc
        raise
    finally:
        if key_fd is not None:
            kfd = key_fd
            key_fd = None
            _safe_close_descriptor(kfd, cleanup_failures, "signer_key_fd_cleanup")

        if retained_dir_fd is not None:
            dfd = retained_dir_fd
            retained_dir_fd = None
            _safe_close_descriptor(dfd, cleanup_failures, "keystore_dir_fd_cleanup")

        if cleanup_failures:
            if primary_exc is not None:
                if isinstance(primary_exc, SignerLedgerError):
                    if getattr(primary_exc, "primary_error", None) is None:
                        primary_exc.primary_error = getattr(primary_exc, "__cause__", None) or primary_exc
                    for _, _, err, _ in cleanup_failures:
                        primary_exc.cleanup_errors.append(err)
                else:
                    durability_err = SignerLedgerDurabilityError(
                        f"ERR_SIGNER_KEY_ORCHESTRATION_FAILURE: operation failed with {type(primary_exc).__name__}: {primary_exc}",
                        primary_error=primary_exc,
                        cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                    )
                    durability_err.__cause__ = primary_exc
                    raise durability_err from primary_exc
            else:
                err_details = "; ".join(f"{name}({fd}): {err}" for name, fd, err, _ in cleanup_failures)
                raise SignerLedgerDurabilityError(
                    f"ERR_SIGNER_KEY_DESCRIPTOR_CLEANUP_FAILED: descriptor cleanup failed: {err_details}",
                    cleanup_errors=[err for _, _, err, _ in cleanup_failures],
                )


# ==============================================================================
# Gate 3-I14: Local-Test Authoritative Challenge Issuance Core
# ==============================================================================

MIN_CHALLENGE_TTL_SECONDS: float = 1.0
MAX_CHALLENGE_TTL_SECONDS: float = 300.0
MAX_CHALLENGE_CLOCK_SKEW_SECONDS: float = 60.0


@dataclass(frozen=True)
class LocalTestChallengeIssuanceRequest:
    """Request DTO for local-test challenge issuance.

    Documentation of Test-Only Semantics:
    This DTO and its associated authority operate strictly within local-test fixtures.
    The operator identity, operator key ID, and operator public key are test inputs only
    and do not represent an authoritative operator registry or OS/process trust boundary.
    """
    approval_envelope: SignedApprovalEnvelope
    proposal: EditPlanProposal
    execution_plan: TimelineExecutionPlan
    director_public_key: Ed25519PublicKey
    expected_director_key_id: str
    operator_id: str
    operator_key_id: str
    revocation_trust_directory: Union[str, Path]
    revocation_registry_directory: Union[str, Path]
    verification_time_utc: datetime
    ttl_seconds: Union[int, float] = 60.0
    operator_public_key: Optional[Ed25519PublicKey] = None

    def __post_init__(self) -> None:
        # 1. Nominal type gates for DTOs
        if type(self.approval_envelope) is not SignedApprovalEnvelope:
            raise ChallengeIssuanceError(
                "ERR_APPROVAL_ENVELOPE_TYPE",
                f"approval_envelope must be exact SignedApprovalEnvelope, got {type(self.approval_envelope).__name__}",
            )
        if type(self.proposal) is not EditPlanProposal:
            raise ChallengeIssuanceError(
                "ERR_PROPOSAL_TYPE",
                f"proposal must be exact EditPlanProposal, got {type(self.proposal).__name__}",
            )
        if type(self.execution_plan) is not TimelineExecutionPlan:
            raise ChallengeIssuanceError(
                "ERR_PLAN_TYPE",
                f"execution_plan must be exact TimelineExecutionPlan, got {type(self.execution_plan).__name__}",
            )

        # 2. Key validation and type checks
        _normalize_and_validate_ed25519_public_key(
            self.director_public_key,
            ChallengeIssuanceError,
            "ERR_DIRECTOR_PUBLIC_KEY_TYPE",
            "director_public_key",
        )
        if (
            type(self.expected_director_key_id) is not str
            or not self.expected_director_key_id
            or self.expected_director_key_id != self.expected_director_key_id.strip()
        ):
            raise ChallengeIssuanceError(
                "ERR_EXPECTED_DIRECTOR_KEY_ID_FORMAT",
                f"expected_director_key_id must be non-empty str without whitespace padding, got {self.expected_director_key_id!r}",
            )

        # 3. Test operator identity validation
        if (
            type(self.operator_id) is not str
            or not self.operator_id
            or self.operator_id != self.operator_id.strip()
        ):
            raise ChallengeIssuanceError(
                "ERR_OPERATOR_ID_FORMAT",
                f"operator_id must be non-empty str without whitespace padding, got {self.operator_id!r}",
            )
        if (
            type(self.operator_key_id) is not str
            or not self.operator_key_id
            or self.operator_key_id != self.operator_key_id.strip()
        ):
            raise ChallengeIssuanceError(
                "ERR_OPERATOR_KEY_ID_FORMAT",
                f"operator_key_id must be non-empty str without whitespace padding, got {self.operator_key_id!r}",
            )
        if self.operator_public_key is not None:
            _normalize_and_validate_ed25519_public_key(
                self.operator_public_key,
                ChallengeIssuanceError,
                "ERR_OPERATOR_PUBLIC_KEY_TYPE",
                "operator_public_key",
            )

        # 4. Lexical directory path checks before verifier or filesystem calls
        _validate_directory_lexically_under_temp_root(
            self.revocation_trust_directory,
            "revocation_trust_directory",
            "ERR_TRUST_DIR_INVALID",
            error_cls=ChallengeIssuanceError,
        )
        _validate_directory_lexically_under_temp_root(
            self.revocation_registry_directory,
            "revocation_registry_directory",
            "ERR_REGISTRY_DIR_INVALID",
            error_cls=ChallengeIssuanceError,
        )

        # 5. Verification time UTC validation (exact built-in datetime, no subclasses, UTC offset zero)
        if type(self.verification_time_utc) is not datetime:
            raise ChallengeIssuanceError(
                "ERR_VERIFICATION_TIME_INVALID",
                f"verification_time_utc must be exact built-in datetime, got {type(self.verification_time_utc).__name__}",
            )
        if self.verification_time_utc.tzinfo is None or self.verification_time_utc.utcoffset() != timedelta(0):
            raise ChallengeIssuanceError(
                "ERR_VERIFICATION_TIME_INVALID",
                f"verification_time_utc must be UTC with offset zero, got {self.verification_time_utc!r}",
            )

        # 6. TTL bounds and validation (int/float, finite, not bool, 1.0 <= ttl <= 300.0)
        if type(self.ttl_seconds) not in (int, float) or isinstance(self.ttl_seconds, bool):
            raise ChallengeIssuanceError(
                "ERR_CHALLENGE_TTL_INVALID",
                f"ttl_seconds must be int or float (excluding bool), got {type(self.ttl_seconds).__name__}",
            )
        if not math.isfinite(self.ttl_seconds):
            raise ChallengeIssuanceError(
                "ERR_CHALLENGE_TTL_INVALID",
                f"ttl_seconds must be finite, got {self.ttl_seconds!r}",
            )
        if not (MIN_CHALLENGE_TTL_SECONDS <= self.ttl_seconds <= MAX_CHALLENGE_TTL_SECONDS):
            raise ChallengeIssuanceError(
                "ERR_CHALLENGE_TTL_OUT_OF_BOUNDS",
                f"ttl_seconds must be between {MIN_CHALLENGE_TTL_SECONDS} and {MAX_CHALLENGE_TTL_SECONDS}, got {self.ttl_seconds!r}",
            )


@dataclass(frozen=True)
class LocalTestChallengeIssuanceResult:
    """Immutable result from local-test challenge issuance.

    Documentation of Trust Boundary:
    Contains only ActiveChallengeContext and OperatorConsentVerificationContext.
    Carries zero private keys, HMAC keys, key bytes, signatures, or authorization tokens.
    """
    challenge: ActiveChallengeContext
    operator_context: OperatorConsentVerificationContext

    def __post_init__(self) -> None:
        if type(self.challenge) is not ActiveChallengeContext:
            raise ChallengeIssuanceError(
                "ERR_RESULT_FIELD_INVALID",
                f"challenge must be exact ActiveChallengeContext, got {type(self.challenge).__name__}",
            )
        if type(self.operator_context) is not OperatorConsentVerificationContext:
            raise ChallengeIssuanceError(
                "ERR_RESULT_FIELD_INVALID",
                f"operator_context must be exact OperatorConsentVerificationContext, got {type(self.operator_context).__name__}",
            )


@dataclass(frozen=True)
class LocalTestChallengeConsumptionResult:
    """Immutable result from local-test operator consent verification and atomic challenge consumption.

    Documentation of Trust Boundary:
    Contains only verification outcomes, identifier bindings, and digest references.
    Carries zero private keys, HMAC keys, key bytes, signatures, or authorization tokens.
    Does NOT authorize live actions, grant dispatch authority, or start processes.
    """
    challenge_id: str
    client_nonce: str
    preallocated_authorization_id: str
    preallocated_execution_id: str
    operator_id: str
    operator_key_id: str
    approval_record_id: str
    proposal_revision: str
    consumed_at_iso: str
    plan_digest_sha256: str
    action_payload_digest_sha256: str

    def __post_init__(self) -> None:
        for field_name, field_val in (
            ("challenge_id", self.challenge_id),
            ("client_nonce", self.client_nonce),
            ("preallocated_authorization_id", self.preallocated_authorization_id),
            ("preallocated_execution_id", self.preallocated_execution_id),
            ("operator_id", self.operator_id),
            ("operator_key_id", self.operator_key_id),
            ("approval_record_id", self.approval_record_id),
            ("proposal_revision", self.proposal_revision),
            ("consumed_at_iso", self.consumed_at_iso),
            ("plan_digest_sha256", self.plan_digest_sha256),
            ("action_payload_digest_sha256", self.action_payload_digest_sha256),
        ):
            if type(field_val) is not str or not field_val or field_val != field_val.strip():
                raise ChallengeConsumptionError(
                    "ERR_RESULT_FIELD_INVALID",
                    f"{field_name} must be non-empty str without whitespace padding, got {field_val!r}",
                )


_ISSUANCE_TICKET_SENTINEL = object()


@dataclass(frozen=True)
class LocalTestIssuanceSigningTicket:
    """Internal, non-forgeable, single-use ticket binding an issuance authorization operation
    to fresh authoritative admission verification evidence and atomic challenge consumption.

    Documentation of Trust Boundary:
    Holds in-memory references to verified admission evidence and consumption records.
    Direct caller instantiation or reuse outside authority-managed lifecycle is prohibited.
    Carries zero persistent keys or live DaVinci Resolve execution capabilities.
    """
    authority: Any
    challenge_id: str
    consumption_result: LocalTestChallengeConsumptionResult
    admission_evidence: TrialAuthorizationAdmissionEvidence
    approval_envelope: SignedApprovalEnvelope
    proposal: EditPlanProposal
    execution_plan: TimelineExecutionPlan
    operator_consent_envelope: SignedOperatorConsentEnvelope
    verification_time_utc: datetime
    ttl_seconds: float
    _ticket_secret: bytes = field(default_factory=lambda: os.urandom(32))
    _sentinel: Any = field(default=_ISSUANCE_TICKET_SENTINEL, repr=False)

    def __post_init__(self) -> None:
        if self._sentinel is not _ISSUANCE_TICKET_SENTINEL:
            raise SignerAdmissionVerificationError(
                "ERR_ISSUANCE_TICKET_FORGERY",
                "Direct or caller construction of LocalTestIssuanceSigningTicket is prohibited",
            )


@dataclass(frozen=True)
class _AuthoritativeAdmissionRegistration:
    """Internal record binding registered admission evidence to its verified context."""
    challenge_id: str
    admission_evidence: TrialAuthorizationAdmissionEvidence
    approval_envelope: SignedApprovalEnvelope
    proposal: EditPlanProposal
    execution_plan: TimelineExecutionPlan
    operator_consent_envelope: SignedOperatorConsentEnvelope
    verification_time_utc: datetime
    ttl_seconds: float
    _sentinel: Any = field(default=_ISSUANCE_TICKET_SENTINEL, repr=False)


class LocalTestChallengeAuthority:
    """Local-test-only authoritative challenge issuance primitive.

    Documentation of Trust Boundary:
    This authority operates strictly in-memory beneath local test fixture roots.
    It does NOT constitute an operating-system or process trust boundary.
    The caller-provided operator identity/key ID are test inputs only and
    do NOT represent an authoritative production operator registry.
    This authority issues challenges, constructs verification contexts, and
    provides local-test operator consent verification and atomic single-use
    challenge consumption (verify_and_consume_challenge_for_local_test);
    it does NOT issue TrialExecutionAuthorization, load HMAC keys, or dispatch
    operations.
    """

    def __init__(
        self,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        monotonic_clock: Optional[Callable[[], float]] = None,
        max_clock_skew_seconds: float = MAX_CHALLENGE_CLOCK_SKEW_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._all_challenge_records: Dict[str, LocalTestChallengeIssuanceResult] = {}
        self._active_challenges: Dict[str, LocalTestChallengeIssuanceResult] = {}
        self._all_issued_identities: Set[str] = set()
        self._issued_challenge_ids: Set[str] = set()
        self._issued_client_nonces: Set[str] = set()
        self._issued_authorization_ids: Set[str] = set()
        self._issued_execution_ids: Set[str] = set()
        self._poisoned_challenges: Set[str] = set()
        self._consumed_challenges: Set[str] = set()
        self._consumed_evidence_by_challenge_id: Dict[str, LocalTestChallengeConsumptionResult] = {}
        self._issued_evidence_challenge_ids: Set[str] = set()
        self._registered_issuance_admissions: Dict[str, _AuthoritativeAdmissionRegistration] = {}
        self._active_signing_tickets: Dict[str, LocalTestIssuanceSigningTicket] = {}

        if clock is None:
            self._clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        else:
            self._clock = clock

        if monotonic_clock is None:
            self._monotonic_clock: Callable[[], float] = time.monotonic
        else:
            self._monotonic_clock = monotonic_clock

        if (
            type(max_clock_skew_seconds) not in (int, float)
            or isinstance(max_clock_skew_seconds, bool)
            or not math.isfinite(max_clock_skew_seconds)
            or max_clock_skew_seconds < 0
        ):
            raise ValueError(
                f"max_clock_skew_seconds must be non-negative finite float/int, got {max_clock_skew_seconds!r}"
            )
        self._max_clock_skew_seconds = float(max_clock_skew_seconds)

    def issue_challenge(
        self,
        request: Optional[LocalTestChallengeIssuanceRequest] = None,
        *,
        approval_envelope: Optional[SignedApprovalEnvelope] = None,
        proposal: Optional[EditPlanProposal] = None,
        execution_plan: Optional[TimelineExecutionPlan] = None,
        director_public_key: Optional[Ed25519PublicKey] = None,
        expected_director_key_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        operator_key_id: Optional[str] = None,
        revocation_trust_directory: Optional[Union[str, Path]] = None,
        revocation_registry_directory: Optional[Union[str, Path]] = None,
        verification_time_utc: Optional[datetime] = None,
        ttl_seconds: Union[int, float] = 60.0,
        operator_public_key: Optional[Ed25519PublicKey] = None,
        **kwargs: Any,
    ) -> LocalTestChallengeIssuanceResult:
        """Issues an ActiveChallengeContext and OperatorConsentVerificationContext for local tests.

        Verifies director approval and fresh revocation status, validates the execution plan's
        canary scope, derives canonical digests, generates fresh identities, and atomically
        records the active challenge under a lock.
        """
        if kwargs:
            for k in kwargs:
                raise ChallengeIssuanceError(
                    f"ERR_CALLER_OVERRIDE_PROHIBITED: unknown or prohibited argument '{k}', caller cannot supply overrides"
                )

        if request is not None:
            if any(
                arg is not None
                for arg in (
                    approval_envelope,
                    proposal,
                    execution_plan,
                    director_public_key,
                    expected_director_key_id,
                    operator_id,
                    operator_key_id,
                    revocation_trust_directory,
                    revocation_registry_directory,
                    verification_time_utc,
                    operator_public_key,
                )
            ):
                raise ChallengeIssuanceError(
                    "ERR_REQUEST_TYPE: Cannot specify both request object and keyword arguments"
                )
            if type(request) is not LocalTestChallengeIssuanceRequest:
                raise ChallengeIssuanceError(
                    "ERR_REQUEST_TYPE",
                    f"request must be exact LocalTestChallengeIssuanceRequest, got {type(request).__name__}",
                )
            req = request
        else:
            req = LocalTestChallengeIssuanceRequest(
                approval_envelope=approval_envelope,  # type: ignore
                proposal=proposal,  # type: ignore
                execution_plan=execution_plan,  # type: ignore
                director_public_key=director_public_key,  # type: ignore
                expected_director_key_id=expected_director_key_id,  # type: ignore
                operator_id=operator_id,  # type: ignore
                operator_key_id=operator_key_id,  # type: ignore
                revocation_trust_directory=revocation_trust_directory,  # type: ignore
                revocation_registry_directory=revocation_registry_directory,  # type: ignore
                verification_time_utc=verification_time_utc,  # type: ignore
                ttl_seconds=ttl_seconds,
                operator_public_key=operator_public_key,
            )

        # Monotonic elapsed timer & trusted clock capture (I14-01)
        start_mono = self._monotonic_clock()
        now_utc = self._clock()
        if type(now_utc) is not datetime or now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
            raise ChallengeIssuanceError(
                f"ERR_CLOCK_INVALID: authority clock must return UTC datetime with offset zero, got {now_utc!r}"
            )

        # Validate verification_time_utc against current clock under bounded skew (I14-01)
        skew_seconds = (now_utc - req.verification_time_utc).total_seconds()
        if skew_seconds > self._max_clock_skew_seconds:
            raise ChallengeIssuanceError(
                f"ERR_VERIFICATION_TIME_STALE: verification_time_utc '{req.verification_time_utc.isoformat()}' "
                f"is stale (older than current clock time '{now_utc.isoformat()}' by {skew_seconds:.1f}s > {self._max_clock_skew_seconds}s)"
            )
        if skew_seconds < -self._max_clock_skew_seconds:
            raise ChallengeIssuanceError(
                f"ERR_VERIFICATION_TIME_FUTURE: verification_time_utc '{req.verification_time_utc.isoformat()}' "
                f"is in future (ahead of current clock time '{now_utc.isoformat()}' by {-skew_seconds:.1f}s > {self._max_clock_skew_seconds}s)"
            )

        expires_at_dt = req.verification_time_utc + timedelta(seconds=float(req.ttl_seconds))
        if now_utc >= expires_at_dt:
            raise ChallengeIssuanceError(
                f"ERR_CHALLENGE_ALREADY_EXPIRED: challenge validity window would already be expired at verification start ({now_utc.isoformat()} >= {expires_at_dt.isoformat()})"
            )

        # 1. Director Approval Verification (I4)
        try:
            director_result = verify_director_approval(
                envelope=req.approval_envelope,
                proposal=req.proposal,
                plan=req.execution_plan,
                public_key=req.director_public_key,
                expected_director_key_id=req.expected_director_key_id,
            )
        except DirectorApprovalVerificationError as exc:
            raise ChallengeIssuanceError(
                f"ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED: {exc}"
            ) from exc
        except Exception as exc:
            raise ChallengeIssuanceError(
                f"ERR_DIRECTOR_APPROVAL_VERIFICATION_FAILED: unexpected error: {exc}"
            ) from exc

        # 2. Revocation Manifest Verification (I11) in read-only mode (advance_anchor=False)
        try:
            revocation_result = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                trust_directory=req.revocation_trust_directory,
                registry_directory=req.revocation_registry_directory,
                verification_time_utc=req.verification_time_utc,
                advance_anchor=False,
            )
        except (RevocationManifestVerificationError, RevocationEpochAnchorError, SignerLedgerError) as exc:
            raise ChallengeIssuanceError(
                f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}"
            ) from exc
        except Exception as exc:
            raise ChallengeIssuanceError(
                f"ERR_REVOCATION_VERIFICATION_FAILED: unexpected error: {exc}"
            ) from exc

        # 3. Fail closed if either Director or test-only Operator key ID is revoked
        manifest = revocation_result.manifest
        if (
            req.expected_director_key_id in manifest.revoked_director_key_ids
            or req.approval_envelope.director_key_id in manifest.revoked_director_key_ids
            or director_result.director_key_id in manifest.revoked_director_key_ids
        ):
            raise ChallengeIssuanceError(
                f"ERR_DIRECTOR_KEY_REVOKED: Director key '{req.expected_director_key_id}' is revoked in epoch {manifest.epoch}"
            )

        if req.operator_key_id in manifest.revoked_operator_key_ids:
            raise ChallengeIssuanceError(
                f"ERR_OPERATOR_KEY_REVOKED: Operator key '{req.operator_key_id}' is revoked in epoch {manifest.epoch}"
            )

        # 4. Require execution plan's authorized canary scope
        if len(req.execution_plan.planned_actions) != 1:
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: execution plan must contain exactly 1 planned action, found {len(req.execution_plan.planned_actions)}"
            )

        action = req.execution_plan.planned_actions[0]
        if type(action) is not PlannedTimelineAction:
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: planned action must be PlannedTimelineAction, got {type(action).__name__}"
            )
        if action.action_type != "INSERT_CLIP":
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: action_type must be strictly 'INSERT_CLIP', got {action.action_type!r}"
            )
        if action.target_track_type != "video":
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: target_track_type must be strictly 'video', got {action.target_track_type!r}"
            )
        if type(action.target_track_index) is not int or isinstance(action.target_track_index, bool) or action.target_track_index != 1:
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: target_track_index must be strictly 1, got {action.target_track_index!r}"
            )
        if (
            type(action.timeline_range.start_frame) is not int
            or isinstance(action.timeline_range.start_frame, bool)
            or action.timeline_range.start_frame < 0
        ):
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: timeline_range.start_frame must be >= 0, got {action.timeline_range.start_frame!r}"
            )
        if (
            type(action.timeline_range.duration_frames) is not int
            or isinstance(action.timeline_range.duration_frames, bool)
            or action.timeline_range.duration_frames != 120
        ):
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: timeline_range.duration_frames must be strictly 120, got {action.timeline_range.duration_frames!r}"
            )
        if (
            type(action.source_range.start_frame) is not int
            or isinstance(action.source_range.start_frame, bool)
            or action.source_range.start_frame < 0
        ):
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: source_range.start_frame must be >= 0, got {action.source_range.start_frame!r}"
            )
        if (
            type(action.source_range.duration_frames) is not int
            or isinstance(action.source_range.duration_frames, bool)
            or action.source_range.duration_frames != 120
        ):
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: source_range.duration_frames must be strictly 120, got {action.source_range.duration_frames!r}"
            )
        if not action.source_file_path or type(action.source_file_path) is not str or action.source_file_path != action.source_file_path.strip():
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: invalid source_file_path {action.source_file_path!r}"
            )

        plan_target = req.execution_plan.target_spec
        for name, val in [
            ("project_id", plan_target.project_id),
            ("project_name", plan_target.project_name),
            ("timeline_id", plan_target.timeline_id),
            ("timeline_name", plan_target.timeline_name),
        ]:
            if type(val) is not str or not val or val != val.strip():
                raise ChallengeIssuanceError(
                    f"ERR_CANARY_SCOPE_VIOLATION: target_spec.{name} must be non-empty without whitespace, got {val!r}"
                )

        matching_source_digests = [
            sd for sd in req.execution_plan.source_digests
            if sd.absolute_path == action.source_file_path
        ]
        if not matching_source_digests:
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: source file path '{action.source_file_path}' has no entry in execution_plan.source_digests"
            )
        if len(matching_source_digests) > 1:
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: duplicate source digest entries ({len(matching_source_digests)}) found for source file path '{action.source_file_path}', ambiguous source binding prohibited"
            )
        source_file_sha256 = matching_source_digests[0].full_sha256.lower()
        if not RE_LOWER_HEX_64.fullmatch(source_file_sha256):
            raise ChallengeIssuanceError(
                f"ERR_CANARY_SCOPE_VIOLATION: source digest is not valid 64-char lowercase hex: {source_file_sha256!r}"
            )

        # 5. Generate fresh identities inside authority
        challenge_id = str(uuid.uuid4())
        client_nonce = str(uuid.uuid4())
        preallocated_authorization_id = f"auth_{uuid.uuid4().hex}"
        preallocated_execution_id = str(uuid.uuid4())

        for uuid_val, name in [
            (challenge_id, "challenge_id"),
            (client_nonce, "client_nonce"),
            (preallocated_execution_id, "preallocated_execution_id"),
        ]:
            try:
                parsed_u = uuid.UUID(uuid_val, version=4)
                if str(parsed_u) != uuid_val:
                    raise ValueError()
            except Exception:
                raise ChallengeIssuanceError(
                    f"ERR_IDENTITY_FORMAT: {name} must be canonical UUIDv4 string, got {uuid_val!r}"
                )

        if (
            not preallocated_authorization_id.startswith("auth_")
            or len(preallocated_authorization_id) <= 5
            or not all(c in "0123456789abcdef" for c in preallocated_authorization_id[5:])
        ):
            raise ChallengeIssuanceError(
                f"ERR_IDENTITY_FORMAT: authorization_id must match auth_<hex> format, got {preallocated_authorization_id!r}"
            )

        id_set = {challenge_id, client_nonce, preallocated_authorization_id, preallocated_execution_id}
        if len(id_set) != 4:
            raise ChallengeIssuanceError(
                "ERR_IDENTITY_ALIASING: Generated identities must be pairwise distinct"
            )

        if preallocated_authorization_id in manifest.revoked_authorization_ids:
            raise ChallengeIssuanceError(
                f"ERR_AUTHORIZATION_ID_REVOKED: Preallocated authorization ID '{preallocated_authorization_id}' is revoked in epoch {manifest.epoch}"
            )

        # 6. Derive digests and construct contexts
        approval_envelope_digest_sha256 = req.approval_envelope.compute_envelope_digest().lower()
        plan_digest_sha256 = compute_canonical_plan_digest(req.execution_plan).lower()
        action_payload_digest_sha256 = compute_canonical_action_payload_digest_for_admission(
            action=action,
            execution_plan=req.execution_plan,
            authorization_id=preallocated_authorization_id,
            execution_id=preallocated_execution_id,
        ).lower()

        issued_at_iso = req.verification_time_utc.isoformat().replace("+00:00", "Z")
        expires_at_dt = req.verification_time_utc + timedelta(seconds=float(req.ttl_seconds))
        expires_at_iso = expires_at_dt.isoformat().replace("+00:00", "Z")

        challenge_ctx = ActiveChallengeContext(
            challenge_id=challenge_id,
            client_nonce=client_nonce,
            issued_at_iso=issued_at_iso,
            expires_at_iso=expires_at_iso,
            consumed=False,
            preallocated_authorization_id=preallocated_authorization_id,
            preallocated_execution_id=preallocated_execution_id,
        )

        operator_ctx = OperatorConsentVerificationContext(
            authoritative_operator_id=req.operator_id,
            expected_operator_key_id=req.operator_key_id,
            challenge=challenge_ctx,
            expected_approval_envelope_digest_sha256=approval_envelope_digest_sha256,
            expected_plan_digest_sha256=plan_digest_sha256,
            expected_action_payload_digest_sha256=action_payload_digest_sha256,
            expected_target_project_name=plan_target.project_name,
            expected_target_project_uid=plan_target.project_id,
            expected_target_timeline_name=plan_target.timeline_name,
            expected_target_timeline_uid=plan_target.timeline_id,
            expected_source_file_path=action.source_file_path,
            expected_source_file_sha256=source_file_sha256,
            expected_record_frame=action.timeline_range.start_frame,
            expected_duration_frames=action.timeline_range.duration_frames,
            expected_action_type=action.action_type,
            expected_track_type=action.target_track_type,
            expected_track_index=action.target_track_index,
            expected_authorization_id=preallocated_authorization_id,
            expected_execution_id=preallocated_execution_id,
            approval_envelope=req.approval_envelope,
            execution_plan=req.execution_plan,
        )

        result = LocalTestChallengeIssuanceResult(
            challenge=challenge_ctx,
            operator_context=operator_ctx,
        )

        # 7. Atomic cross-category collision check, lock-held final expiry check, and registration under narrow lock (I14-03 / I14-R2-01)
        candidate_ids = {challenge_id, client_nonce, preallocated_authorization_id, preallocated_execution_id}
        with self._lock:
            # Final freshness check holding the registry lock immediately before any mutation (I14-R2-01)
            elapsed_mono = self._monotonic_clock() - start_mono
            current_insertion_utc = self._clock()
            if elapsed_mono >= float(req.ttl_seconds) or current_insertion_utc >= expires_at_dt:
                raise ChallengeIssuanceError(
                    f"ERR_CHALLENGE_ALREADY_EXPIRED: challenge validity window expired before issuance could complete "
                    f"(now '{current_insertion_utc.isoformat()}' >= expires_at '{expires_at_dt.isoformat()}', "
                    f"elapsed={elapsed_mono:.4f}s >= ttl={req.ttl_seconds}s)"
                )

            if (
                challenge_id in self._active_challenges
                or not candidate_ids.isdisjoint(self._all_issued_identities)
            ):
                raise ChallengeIssuanceError(
                    "ERR_CHALLENGE_COLLISION: Identity collision detected during challenge registration"
                )

            self._all_issued_identities.update(candidate_ids)
            self._issued_challenge_ids.add(challenge_id)
            self._issued_client_nonces.add(client_nonce)
            self._issued_authorization_ids.add(preallocated_authorization_id)
            self._issued_execution_ids.add(preallocated_execution_id)
            self._all_challenge_records[challenge_id] = result
            self._active_challenges[challenge_id] = result

        return result

    def _lookup_record(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> LocalTestChallengeIssuanceResult:
        if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
            raise ChallengeIssuanceError(
                "ERR_CHALLENGE_ID_INVALID",
                f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
            )

        if current_time_utc is not None:
            if type(current_time_utc) is not datetime:
                raise ChallengeIssuanceError(
                    "ERR_VERIFICATION_TIME_INVALID",
                    f"current_time_utc must be exact built-in datetime, got {type(current_time_utc).__name__}",
                )
            if current_time_utc.tzinfo is None or current_time_utc.utcoffset() != timedelta(0):
                raise ChallengeIssuanceError(
                    "ERR_VERIFICATION_TIME_INVALID",
                    "current_time_utc must be UTC with offset zero",
                )
        else:
            current_time_utc = self._clock()

        with self._lock:
            if challenge_id in self._poisoned_challenges:
                raise ChallengeIssuanceError(
                    f"ERR_CHALLENGE_POISONED: challenge '{challenge_id}' was poisoned by a previous ambiguous persistence outcome and cannot be used"
                )
            if challenge_id in self._consumed_challenges:
                raise ChallengeIssuanceError(
                    f"ERR_CHALLENGE_ALREADY_CONSUMED: challenge '{challenge_id}' has already been consumed"
                )
            record = self._active_challenges.get(challenge_id)
            if record is None:
                raise ChallengeIssuanceError(
                    f"ERR_CHALLENGE_NOT_FOUND: challenge '{challenge_id}' not found in active registry"
                )
            exp_dt = parse_iso_utc_timestamp(record.challenge.expires_at_iso, "challenge.expires_at_iso")
            if current_time_utc > exp_dt:
                del self._active_challenges[challenge_id]
                raise ChallengeIssuanceError(
                    f"ERR_CHALLENGE_EXPIRED: challenge '{challenge_id}' expired at {record.challenge.expires_at_iso} (lookup at {current_time_utc.isoformat()})"
                )
            return record

    def lookup_active_challenge(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> ActiveChallengeContext:
        """Looks up an active challenge by challenge_id."""
        return self._lookup_record(challenge_id, current_time_utc=current_time_utc).challenge

    def lookup_operator_context(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> OperatorConsentVerificationContext:
        """Looks up the OperatorConsentVerificationContext bound to challenge_id."""
        return self._lookup_record(challenge_id, current_time_utc=current_time_utc).operator_context

    def lookup_issuance_result(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> LocalTestChallengeIssuanceResult:
        """Looks up the full LocalTestChallengeIssuanceResult bound to challenge_id."""
        return self._lookup_record(challenge_id, current_time_utc=current_time_utc)

    def get_active_challenge(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> ActiveChallengeContext:
        return self.lookup_active_challenge(challenge_id, current_time_utc=current_time_utc)

    def get_operator_context(
        self,
        challenge_id: str,
        *,
        current_time_utc: Optional[datetime] = None,
    ) -> OperatorConsentVerificationContext:
        return self.lookup_operator_context(challenge_id, current_time_utc=current_time_utc)

    def verify_and_consume_challenge_for_local_test(
        self,
        *,
        challenge_id: str,
        consent_envelope: SignedOperatorConsentEnvelope,
        operator_public_key: Ed25519PublicKey,
        revocation_trust_directory: Union[str, Path],
        revocation_registry_directory: Union[str, Path],
        nonce_ledger_directory: Union[str, Path],
        **kwargs: Any,
    ) -> LocalTestChallengeConsumptionResult:
        """Verifies operator consent and atomically consumes active challenge for local tests.

        Documentation of Test-Only Semantics:
        This operation verifies operator consent against the exact active I14 challenge context
        using an ephemeral operator public key, validates fresh revocation state, and durably
        records a single-use consumed nonce ledger entry. It does NOT issue authorization tokens,
        sign HMACs, start background services, or dispatch operations.
        """
        if kwargs:
            for k in kwargs:
                raise ChallengeConsumptionError(
                    f"ERR_CALLER_OVERRIDE_PROHIBITED: unknown or prohibited argument '{k}', caller cannot supply overrides"
                )

        if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
            raise ChallengeConsumptionError(
                "ERR_CHALLENGE_ID_INVALID",
                f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
            )

        if type(consent_envelope) is not SignedOperatorConsentEnvelope:
            raise ChallengeConsumptionError(
                "ERR_CONSENT_ENVELOPE_INVALID",
                f"consent_envelope must be exact SignedOperatorConsentEnvelope, got {type(consent_envelope).__name__}",
            )

        normalized_operator_public_key = _normalize_and_validate_ed25519_public_key(
            operator_public_key,
            ChallengeConsumptionError,
            "ERR_OPERATOR_PUBLIC_KEY_INVALID",
            "operator_public_key",
        )

        _validate_directory_lexically_under_temp_root(
            revocation_trust_directory,
            "revocation_trust_directory",
            "ERR_PATH_TRAVERSAL",
            error_cls=ChallengeConsumptionError,
        )
        _validate_directory_lexically_under_temp_root(
            revocation_registry_directory,
            "revocation_registry_directory",
            "ERR_PATH_TRAVERSAL",
            error_cls=ChallengeConsumptionError,
        )
        _validate_directory_lexically_under_temp_root(
            nonce_ledger_directory,
            "nonce_ledger_directory",
            "ERR_PATH_TRAVERSAL",
            error_cls=ChallengeConsumptionError,
        )

        # Step-by-step descriptor-anchored traversal of nonce_ledger_directory under temp root (I15-R2-02 & I15-R4)
        # Guarantees intermediate components cannot contain symlinks escaping temp root.
        # Retains verified directory descriptor open until passed into descriptor-backed ledger primitive.
        cleanup_failures: List[Tuple[str, int, Exception, str]] = []
        try:
            verified_ledger_dir_fd = _open_anchor_dir_under_temp_root(
                nonce_ledger_directory, cleanup_failures
            )
        except (SignerLedgerSecurityError, SignerLedgerError, SignerLedgerDurabilityError) as exc:
            raise ChallengeConsumptionError(
                f"ERR_PATH_TRAVERSAL: nonce_ledger_directory failed descriptor-anchored temp root traversal: {exc}",
                code="ERR_PATH_TRAVERSAL",
                primary_error=exc,
            ) from exc

        def _safe_close_caller_fd() -> Optional[Exception]:
            nonlocal verified_ledger_dir_fd
            if verified_ledger_dir_fd is not None:
                fd_to_close = verified_ledger_dir_fd
                verified_ledger_dir_fd = None  # Single-close semantics: immediately disown before calling os.close
                try:
                    os.close(fd_to_close)
                except Exception as ce:
                    return ce
            return None

        try:
            start_mono = self._monotonic_clock()
            now_utc = self._clock()
            if type(now_utc) is not datetime or now_utc.tzinfo is None or now_utc.utcoffset() != timedelta(0):
                raise ChallengeConsumptionError(
                    f"ERR_CLOCK_INVALID: authority clock must return UTC datetime with offset zero, got {now_utc!r}"
                )

            # Lookup active challenge for verification context
            with self._lock:
                if challenge_id in self._poisoned_challenges:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_POISONED: challenge '{challenge_id}' was poisoned by a previous ambiguous persistence outcome and cannot be consumed"
                    )
                if challenge_id in self._consumed_challenges:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_ALREADY_CONSUMED: challenge '{challenge_id}' has already been consumed"
                    )
                active_result = self._active_challenges.get(challenge_id)
                if active_result is None:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_NOT_FOUND: challenge '{challenge_id}' not found in active registry"
                    )

                exp_dt = parse_iso_utc_timestamp(active_result.challenge.expires_at_iso, "challenge.expires_at_iso")
                if now_utc > exp_dt:
                    del self._active_challenges[challenge_id]
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_EXPIRED: challenge '{challenge_id}' expired at {active_result.challenge.expires_at_iso} (current verification time {now_utc.isoformat()})"
                    )
                initial_remaining_seconds = (exp_dt - now_utc).total_seconds()

            operator_context = active_result.operator_context

            # 1. Fresh Revocation Verification (I11) in read-only mode (advance_anchor=False)
            try:
                rev_result = load_verify_and_anchor_revocation_manifest_with_local_issuer_key_for_test(
                    trust_directory=revocation_trust_directory,
                    registry_directory=revocation_registry_directory,
                    verification_time_utc=now_utc,
                    advance_anchor=False,
                )
            except (RevocationManifestVerificationError, RevocationEpochAnchorError, SignerLedgerError) as exc:
                raise ChallengeConsumptionError(
                    f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}"
                ) from exc
            except Exception as exc:
                raise ChallengeConsumptionError(
                    f"ERR_REVOCATION_VERIFICATION_FAILED: unexpected error: {exc}"
                ) from exc

            manifest = rev_result.manifest
            director_key_id = operator_context.approval_envelope.director_key_id
            if director_key_id in manifest.revoked_director_key_ids:
                raise ChallengeConsumptionError(
                    f"ERR_DIRECTOR_KEY_REVOKED: Director key '{director_key_id}' is revoked in epoch {manifest.epoch}"
                )

            if operator_context.expected_operator_key_id in manifest.revoked_operator_key_ids:
                raise ChallengeConsumptionError(
                    f"ERR_OPERATOR_KEY_REVOKED: Operator key '{operator_context.expected_operator_key_id}' is revoked in epoch {manifest.epoch}"
                )

            preallocated_auth_id = operator_context.challenge.preallocated_authorization_id
            if preallocated_auth_id in manifest.revoked_authorization_ids:
                raise ChallengeConsumptionError(
                    f"ERR_AUTHORIZATION_ID_REVOKED: Preallocated authorization ID '{preallocated_auth_id}' is revoked in epoch {manifest.epoch}"
                )

            # 2. Operator Consent Verification (I5) using exact captured instant
            try:
                consent_result = verify_operator_consent(
                    envelope=consent_envelope,
                    context=operator_context,
                    public_key=normalized_operator_public_key,
                    verification_time_utc=now_utc,
                )
            except OperatorConsentVerificationError as exc:
                raise ChallengeConsumptionError(
                    f"ERR_OPERATOR_CONSENT_VERIFICATION_FAILED: {exc}"
                ) from exc
            except Exception as exc:
                raise ChallengeConsumptionError(
                    f"ERR_OPERATOR_CONSENT_VERIFICATION_FAILED: unexpected error: {exc}"
                ) from exc

            if type(consent_result) is not OperatorConsentVerificationResult or consent_result.verified is not True:
                raise ChallengeConsumptionError(
                    "ERR_OPERATOR_CONSENT_VERIFICATION_FAILED: consent verification did not return successful verified result"
                )

            # Defensive binding check between verifier result and I14 context
            if (
                consent_result.challenge_id != operator_context.challenge.challenge_id
                or consent_result.client_nonce != operator_context.challenge.client_nonce
                or consent_result.operator_id != operator_context.authoritative_operator_id
                or consent_result.operator_key_id != operator_context.expected_operator_key_id
                or consent_result.approval_envelope_digest_sha256 != operator_context.expected_approval_envelope_digest_sha256
                or consent_result.plan_digest_sha256 != operator_context.expected_plan_digest_sha256
                or consent_result.action_payload_digest_sha256 != operator_context.expected_action_payload_digest_sha256
                or consent_result.target_project_uid != operator_context.expected_target_project_uid
                or consent_result.target_timeline_uid != operator_context.expected_target_timeline_uid
                or consent_result.source_file_sha256 != operator_context.expected_source_file_sha256
            ):
                raise ChallengeConsumptionError(
                    "ERR_OPERATOR_CONSENT_BINDING_MISMATCH: verifier result fields do not match active challenge context"
                )

            # 3. Construct ConsumedAuthorizationNonceRecord
            consumed_at_iso = now_utc.isoformat().replace("+00:00", "Z")
            record_to_append = ConsumedAuthorizationNonceRecord(
                schema_version=1,
                challenge_id=operator_context.challenge.challenge_id,
                client_nonce=operator_context.challenge.client_nonce,
                approval_record_id=operator_context.approval_envelope.approval_record.approval_id,
                proposal_revision=operator_context.approval_envelope.approval_record.proposal_revision,
                consumed_at_iso=consumed_at_iso,
            )

            # 4. Atomic lock acquisition, re-verification, and ledger consume
            with self._lock:
                if challenge_id in self._poisoned_challenges:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_POISONED: challenge '{challenge_id}' was poisoned by an ambiguous persistence outcome"
                    )
                if challenge_id in self._consumed_challenges:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_ALREADY_CONSUMED: challenge '{challenge_id}' has already been consumed"
                    )

                current_active = self._active_challenges.get(challenge_id)
                if current_active is None:
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_NOT_FOUND: challenge '{challenge_id}' not found in active registry"
                    )
                if current_active is not active_result:
                    raise ChallengeConsumptionError(
                        "ERR_CHALLENGE_STATE_MODIFIED: active challenge state was modified during verification"
                    )

                # Lock-held final expiry check (monotonic elapsed & clock)
                elapsed_mono = self._monotonic_clock() - start_mono
                current_lock_utc = self._clock()
                exp_dt = parse_iso_utc_timestamp(current_active.challenge.expires_at_iso, "challenge.expires_at_iso")
                if current_lock_utc > exp_dt or elapsed_mono > initial_remaining_seconds:
                    del self._active_challenges[challenge_id]
                    raise ChallengeConsumptionError(
                        f"ERR_CHALLENGE_EXPIRED: challenge '{challenge_id}' expired (wall clock: {current_lock_utc.isoformat()} > {current_active.challenge.expires_at_iso}, or monotonic elapsed: {elapsed_mono:.3f}s > {initial_remaining_seconds:.3f}s)"
                    )

                # Atomic durable append via descriptor-backed SignerConsumedNonceLedger (I15-R4)
                # Guarantees verified directory descriptor remains authoritative; zero path reopening
                ledger: Optional[SignerConsumedNonceLedger] = None
                append_exc: Optional[Exception] = None
                try:
                    ledger = SignerConsumedNonceLedger(directory_fd=verified_ledger_dir_fd)
                    ledger.consume_once(record_to_append)
                except Exception as ae:
                    append_exc = ae

                # Cleanup descriptors exception-safely
                ledger_close_exc: Optional[Exception] = None
                if ledger is not None:
                    try:
                        ledger.close()
                    except Exception as lce:
                        ledger_close_exc = lce

                caller_close_exc = _safe_close_caller_fd()
                cleanup_errors: List[Exception] = [e for e in (ledger_close_exc, caller_close_exc) if e is not None]

                if append_exc is not None:
                    self._active_challenges.pop(challenge_id, None)
                    self._poisoned_challenges.add(challenge_id)
                    if cleanup_errors:
                        err = ChallengeConsumptionError(
                            f"ERR_NONCE_LEDGER_PERSISTENCE_FAILED: Failed to append nonce record durably: {append_exc}; descriptor cleanup also failed: {'; '.join(str(e) for e in cleanup_errors)}",
                            code="ERR_NONCE_LEDGER_PERSISTENCE_FAILED",
                            primary_error=append_exc,
                            cleanup_error=cleanup_errors[0],
                            cleanup_errors=cleanup_errors,
                        )
                        err.__cause__ = append_exc
                        raise err from append_exc
                    else:
                        err = ChallengeConsumptionError(
                            f"ERR_NONCE_LEDGER_PERSISTENCE_FAILED: Failed to append nonce record durably: {append_exc}",
                            code="ERR_NONCE_LEDGER_PERSISTENCE_FAILED",
                            primary_error=append_exc,
                        )
                        err.__cause__ = append_exc
                        raise err from append_exc

                if cleanup_errors:
                    # Append succeeded but descriptor cleanup failed: ambiguous persistence outcome!
                    self._active_challenges.pop(challenge_id, None)
                    self._poisoned_challenges.add(challenge_id)
                    err = ChallengeConsumptionError(
                        f"ERR_NONCE_LEDGER_PERSISTENCE_FAILED: Nonce record appended but descriptor cleanup failed (ambiguous persistence outcome): {'; '.join(str(e) for e in cleanup_errors)}",
                        code="ERR_NONCE_LEDGER_PERSISTENCE_FAILED",
                        primary_error=cleanup_errors[0],
                        cleanup_error=cleanup_errors[0],
                        cleanup_errors=cleanup_errors,
                    )
                    err.__cause__ = cleanup_errors[0]
                    raise err from cleanup_errors[0]

                # Mark challenge consumed and remove from active registry
                self._active_challenges.pop(challenge_id, None)
                self._consumed_challenges.add(challenge_id)

                consumption_result = LocalTestChallengeConsumptionResult(
                    challenge_id=operator_context.challenge.challenge_id,
                    client_nonce=operator_context.challenge.client_nonce,
                    preallocated_authorization_id=operator_context.challenge.preallocated_authorization_id,
                    preallocated_execution_id=operator_context.challenge.preallocated_execution_id,
                    operator_id=operator_context.authoritative_operator_id,
                    operator_key_id=operator_context.expected_operator_key_id,
                    approval_record_id=operator_context.approval_envelope.approval_record.approval_id,
                    proposal_revision=operator_context.approval_envelope.approval_record.proposal_revision,
                    consumed_at_iso=consumed_at_iso,
                    plan_digest_sha256=operator_context.expected_plan_digest_sha256,
                    action_payload_digest_sha256=operator_context.expected_action_payload_digest_sha256,
                )
                self._consumed_evidence_by_challenge_id[challenge_id] = consumption_result
                return consumption_result
        except Exception as pre_exc:
            caller_close_err = _safe_close_caller_fd()
            if caller_close_err is not None:
                if isinstance(pre_exc, ChallengeConsumptionError):
                    pre_exc.cleanup_error = caller_close_err
                    pre_exc.cleanup_errors = [caller_close_err]
                    raise pre_exc
                err = ChallengeConsumptionError(
                    f"Pre-ledger verification failed with {pre_exc}; descriptor cleanup failed: {caller_close_err}",
                    primary_error=pre_exc,
                    cleanup_error=caller_close_err,
                    cleanup_errors=[caller_close_err],
                )
                err.__cause__ = pre_exc
                raise err from pre_exc
            raise
        finally:
            _safe_close_caller_fd()

    def reserve_consumed_evidence_for_issuance(
        self,
        challenge_id: str,
        consumption_result: LocalTestChallengeConsumptionResult,
    ) -> LocalTestChallengeConsumptionResult:
        """Atomically validates object identity, claim equality, and single-use reservation
        of authority-owned consumption evidence for token issuance (Gate 3-I16).

        Enforces:
        - Exact nominal type check
        - challenge_id present in consumed registry
        - Object identity: consumption_result is authority-recorded result (rejects caller-created/copied)
        - Claim equality: every field matches authority-recorded evidence
        - Single-use: challenge_id not previously issued
        - Rejection if challenge was poisoned or unconsumed
        """
        with self._lock:
            if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_ID_INVALID",
                    f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
                )
            if type(consumption_result) is not LocalTestChallengeConsumptionResult:
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_RESULT_TYPE",
                    f"consumption_result must be exact LocalTestChallengeConsumptionResult, got {type(consumption_result).__name__}",
                )
            if challenge_id in self._poisoned_challenges:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_POISONED",
                    f"challenge '{challenge_id}' was poisoned by an ambiguous persistence outcome and cannot be used for issuance",
                )
            if challenge_id not in self._consumed_challenges:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_NOT_CONSUMED",
                    f"challenge '{challenge_id}' has not been consumed into the nonce ledger",
                )
            recorded = self._consumed_evidence_by_challenge_id.get(challenge_id)
            if recorded is None:
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_EVIDENCE_NOT_FOUND",
                    f"no authority-owned consumption evidence found for challenge '{challenge_id}'",
                )
            if consumption_result is not recorded:
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_EVIDENCE_FORGERY",
                    "consumption_result failed object-identity provenance verification (caller-constructed or copied result rejected)",
                )
            if challenge_id in self._issued_evidence_challenge_ids:
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_EVIDENCE_ALREADY_ISSUED",
                    f"consumption evidence for challenge '{challenge_id}' has already been used for authorization issuance",
                )
            # Complete claim equality check
            if (
                consumption_result.challenge_id != recorded.challenge_id
                or consumption_result.client_nonce != recorded.client_nonce
                or consumption_result.preallocated_authorization_id != recorded.preallocated_authorization_id
                or consumption_result.preallocated_execution_id != recorded.preallocated_execution_id
                or consumption_result.operator_id != recorded.operator_id
                or consumption_result.operator_key_id != recorded.operator_key_id
                or consumption_result.approval_record_id != recorded.approval_record_id
                or consumption_result.proposal_revision != recorded.proposal_revision
                or consumption_result.consumed_at_iso != recorded.consumed_at_iso
                or consumption_result.plan_digest_sha256 != recorded.plan_digest_sha256
                or consumption_result.action_payload_digest_sha256 != recorded.action_payload_digest_sha256
            ):
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_EVIDENCE_CLAIM_MISMATCH",
                    "consumption_result claims do not match authority-recorded evidence",
                )

            # Atomically mark as issued
            self._issued_evidence_challenge_ids.add(challenge_id)
            return recorded

    def verify_and_register_admission_for_local_test(
        self,
        *,
        challenge_id: str,
        approval_envelope: SignedApprovalEnvelope,
        proposal: EditPlanProposal,
        execution_plan: TimelineExecutionPlan,
        director_public_key: Ed25519PublicKey,
        expected_director_key_id: str,
        operator_consent_envelope: SignedOperatorConsentEnvelope,
        operator_public_key: Ed25519PublicKey,
        revocation_trust_directory: Union[str, Path],
        revocation_registry_directory: Union[str, Path],
        verification_time_utc: datetime,
        ttl_seconds: Union[int, float] = 300.0,
    ) -> TrialAuthorizationAdmissionEvidence:
        """Indivisible admission verification and authority registration for local-test issuance.

        Executes fresh I12 admission verification directly on the provided cryptographic artifacts
        and keys. Records the resulting verified evidence under authority lock only after the verifier
        returns verified evidence. Does NOT accept caller-supplied admission evidence or sentinel markers.

        Trust Boundary Limitation:
        This local-test design is not an operating system or process isolation boundary;
        in-process Python objects do not protect against arbitrary code execution in the same process.
        """
        if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
            raise SignerAdmissionVerificationError(
                "ERR_CHALLENGE_ID_INVALID",
                f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
            )
        if (
            type(ttl_seconds) not in (int, float)
            or isinstance(ttl_seconds, bool)
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise SignerAdmissionVerificationError(
                "ERR_TTL_INVALID",
                f"ttl_seconds must be a positive finite number, got {ttl_seconds!r}",
            )
        if type(verification_time_utc) is not datetime or verification_time_utc.tzinfo is None or verification_time_utc.utcoffset() != timedelta(0):
            raise SignerAdmissionVerificationError(
                "ERR_VERIFICATION_TIME_INVALID",
                f"verification_time_utc must be UTC datetime with offset zero, got {verification_time_utc!r}",
            )

        with self._lock:
            if challenge_id in self._poisoned_challenges:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_POISONED",
                    f"challenge '{challenge_id}' was poisoned and cannot be used",
                )
            record = self._all_challenge_records.get(challenge_id)
            if record is None:
                raise SignerAdmissionVerificationError(
                    f"ERR_CHALLENGE_LOOKUP_FAILED: challenge '{challenge_id}' is not registered with this authority",
                    f"challenge '{challenge_id}' is not registered with this authority",
                )
            operator_context = record.operator_context

        # Indivisible I12 verification executed directly by authority
        admission_evidence = verify_trial_authorization_admission_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=execution_plan,
            director_public_key=director_public_key,
            expected_director_key_id=expected_director_key_id,
            operator_consent_envelope=operator_consent_envelope,
            operator_context=operator_context,
            operator_public_key=operator_public_key,
            revocation_trust_directory=revocation_trust_directory,
            revocation_registry_directory=revocation_registry_directory,
            verification_time_utc=verification_time_utc,
        )
        if not admission_evidence or admission_evidence.verified is not True:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "Admission preflight verification did not return verified evidence",
            )
        if admission_evidence.challenge_id != challenge_id:
            raise SignerAdmissionVerificationError(
                "ERR_CHALLENGE_BINDING_MISMATCH",
                f"admission challenge_id '{admission_evidence.challenge_id}' does not match requested challenge_id '{challenge_id}'",
            )

        reg = _AuthoritativeAdmissionRegistration(
            challenge_id=challenge_id,
            admission_evidence=admission_evidence,
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=execution_plan,
            operator_consent_envelope=operator_consent_envelope,
            verification_time_utc=verification_time_utc,
            ttl_seconds=float(ttl_seconds),
        )
        with self._lock:
            self._registered_issuance_admissions[challenge_id] = reg
        return admission_evidence

    def register_issuance_admission_for_local_test(self, *args: Any, **kwargs: Any) -> None:
        """Prohibits caller-supplied admission registration.

        Caller-supplied admission evidence registration is strictly prohibited.
        Admission evidence can only be verified and recorded through the indivisible
        verify_and_register_admission_for_local_test() method.
        """
        raise SignerAdmissionVerificationError(
            "ERR_ADMISSION_REGISTRATION_PROHIBITED",
            "Caller-supplied admission registration is strictly prohibited; admission evidence can only be established via indivisible verification in verify_and_register_admission_for_local_test()",
        )

    def create_issuance_signing_ticket(
        self,
        *,
        challenge_id: str,
        consumption_result: LocalTestChallengeConsumptionResult,
        admission_evidence: TrialAuthorizationAdmissionEvidence,
        approval_envelope: SignedApprovalEnvelope,
        proposal: EditPlanProposal,
        execution_plan: TimelineExecutionPlan,
        operator_consent_envelope: SignedOperatorConsentEnvelope,
        verification_time_utc: datetime,
        ttl_seconds: float,
    ) -> LocalTestIssuanceSigningTicket:
        """Creates a one-time authoritative issuance signing ticket bound to fresh admission and consumption evidence."""
        with self._lock:
            if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_ID_INVALID",
                    f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
                )
            if challenge_id not in self._consumed_challenges:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_NOT_CONSUMED",
                    f"challenge '{challenge_id}' has not been consumed into the nonce ledger",
                )
            if challenge_id not in self._issued_evidence_challenge_ids:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_NOT_RESERVED",
                    f"challenge '{challenge_id}' has not been reserved for issuance",
                )
            recorded = self._consumed_evidence_by_challenge_id.get(challenge_id)
            if recorded is None or recorded is not consumption_result:
                raise SignerAdmissionVerificationError(
                    "ERR_CONSUMPTION_EVIDENCE_FORGERY",
                    f"consumption evidence for challenge '{challenge_id}' is not registered with this authority",
                )

            # Check that fresh I12 admission was registered by the authoritative issuance flow
            reg = self._registered_issuance_admissions.get(challenge_id)
            if reg is None:
                raise SignerAdmissionVerificationError(
                    "ERR_ADMISSION_NOT_REGISTERED",
                    f"No authoritative admission verification registered for challenge '{challenge_id}'",
                )
            if admission_evidence is not reg.admission_evidence:
                raise SignerAdmissionVerificationError(
                    "ERR_ADMISSION_EVIDENCE_FORGERY",
                    "admission_evidence failed object-identity provenance verification (caller-constructed or fabricated admission result rejected)",
                )
            if (
                approval_envelope is not reg.approval_envelope
                or proposal is not reg.proposal
                or execution_plan is not reg.execution_plan
                or operator_consent_envelope is not reg.operator_consent_envelope
                or verification_time_utc != reg.verification_time_utc
                or ttl_seconds != reg.ttl_seconds
            ):
                raise SignerAdmissionVerificationError(
                    "ERR_ADMISSION_CONTEXT_MISMATCH",
                    "Context arguments do not match the authoritative registered admission context",
                )

            # Atomically consume the admission registration (single-use)
            self._registered_issuance_admissions.pop(challenge_id)

            ticket = LocalTestIssuanceSigningTicket(
                authority=self,
                challenge_id=challenge_id,
                consumption_result=consumption_result,
                admission_evidence=admission_evidence,
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=execution_plan,
                operator_consent_envelope=operator_consent_envelope,
                verification_time_utc=verification_time_utc,
                ttl_seconds=ttl_seconds,
            )
            self._active_signing_tickets[challenge_id] = ticket
            return ticket

    def _consume_issuance_signing_ticket(
        self,
        ticket: LocalTestIssuanceSigningTicket,
    ) -> None:
        """Atomically validates and consumes the one-time issuance signing ticket under authority lock."""
        with self._lock:
            if type(ticket) is not LocalTestIssuanceSigningTicket:
                raise SignerAdmissionVerificationError(
                    "ERR_ISSUANCE_TICKET_FORGERY",
                    f"ticket must be exact LocalTestIssuanceSigningTicket, got {type(ticket).__name__}",
                )
            if getattr(ticket, "_sentinel", None) is not _ISSUANCE_TICKET_SENTINEL:
                raise SignerAdmissionVerificationError(
                    "ERR_ISSUANCE_TICKET_FORGERY",
                    "Invalid or forged issuance ticket sentinel",
                )
            if ticket.authority is not self:
                raise SignerAdmissionVerificationError(
                    "ERR_ISSUANCE_TICKET_FORGERY",
                    "Ticket authority does not match this authority instance",
                )
            active_ticket = self._active_signing_tickets.get(ticket.challenge_id)
            if active_ticket is None or active_ticket is not ticket:
                raise SignerAdmissionVerificationError(
                    "ERR_CHALLENGE_NOT_RESERVED",
                    f"Ticket for challenge '{ticket.challenge_id}' is not active or has already been consumed",
                )
            self._active_signing_tickets.pop(ticket.challenge_id)

    def issue_trial_execution_authorization_for_local_test(
        self,
        *,
        approval_envelope: SignedApprovalEnvelope,
        proposal: EditPlanProposal,
        execution_plan: TimelineExecutionPlan,
        director_public_key: Ed25519PublicKey,
        expected_director_key_id: str,
        operator_consent_envelope: SignedOperatorConsentEnvelope,
        operator_public_key: Ed25519PublicKey,
        challenge_id: str,
        signing_capability: LocalTestSignerHmacSigningCapability,
        verification_key_handle: LocalTestSignerKeyHandle,
        revocation_trust_directory: Union[str, Path],
        revocation_registry_directory: Union[str, Path],
        nonce_ledger_directory: Union[str, Path],
        verification_time_utc: datetime,
        ttl_seconds: Union[int, float] = 300.0,
        **kwargs: Any,
    ) -> TrialExecutionAuthorization:
        """Instance method on LocalTestChallengeAuthority delegating to issue_trial_execution_authorization_for_local_test."""
        return issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=execution_plan,
            director_public_key=director_public_key,
            expected_director_key_id=expected_director_key_id,
            operator_consent_envelope=operator_consent_envelope,
            operator_public_key=operator_public_key,
            challenge_authority=self,
            challenge_id=challenge_id,
            signing_capability=signing_capability,
            verification_key_handle=verification_key_handle,
            revocation_trust_directory=revocation_trust_directory,
            revocation_registry_directory=revocation_registry_directory,
            nonce_ledger_directory=nonce_ledger_directory,
            verification_time_utc=verification_time_utc,
            ttl_seconds=ttl_seconds,
            **kwargs,
        )


def issue_challenge_for_local_test(
    authority: LocalTestChallengeAuthority,
    request: LocalTestChallengeIssuanceRequest,
) -> LocalTestChallengeIssuanceResult:
    """Convenience helper to issue challenge on a LocalTestChallengeAuthority."""
    if type(authority) is not LocalTestChallengeAuthority:
        raise ChallengeIssuanceError(
            "ERR_AUTHORITY_TYPE",
            f"authority must be exact LocalTestChallengeAuthority, got {type(authority).__name__}",
        )
    return authority.issue_challenge(request)


def verify_and_consume_challenge_for_local_test(
    authority: LocalTestChallengeAuthority,
    *,
    challenge_id: str,
    consent_envelope: SignedOperatorConsentEnvelope,
    operator_public_key: Ed25519PublicKey,
    revocation_trust_directory: Union[str, Path],
    revocation_registry_directory: Union[str, Path],
    nonce_ledger_directory: Union[str, Path],
    **kwargs: Any,
) -> LocalTestChallengeConsumptionResult:
    """Convenience helper to verify operator consent and atomically consume active challenge."""
    if type(authority) is not LocalTestChallengeAuthority:
        raise ChallengeConsumptionError(
            f"ERR_AUTHORITY_TYPE: authority must be exact LocalTestChallengeAuthority, got {type(authority).__name__}"
        )
    return authority.verify_and_consume_challenge_for_local_test(
        challenge_id=challenge_id,
        consent_envelope=consent_envelope,
        operator_public_key=operator_public_key,
        revocation_trust_directory=revocation_trust_directory,
        revocation_registry_directory=revocation_registry_directory,
        nonce_ledger_directory=nonce_ledger_directory,
        **kwargs,
    )


class LocalTestSignerHmacSigningCapability:
    """Local-test-only HMAC signing capability for TrialExecutionAuthorization (Gate 3-I16).

    Documentation of Trust Boundary:
    An in-process Python object is NOT an operating-system or process isolation boundary.
    This local-test primitive must not claim protection against arbitrary code executing
    in the same process and must not be represented as production-ready key custody.
    The accepted R7 architecture still requires a separately identified _resolveflow_signer
    service identity and production keystore provisioning in a later, separately authorized workorder.
    This capability holds strictly in-memory ephemeral test key material; it has no key-export API,
    no raw key accessor, and never loads keys from disk, files, or production paths.
    """
    key_id: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise SignerLedgerSecurityError(
            "ERR_DIRECT_KEY_HANDLE_CONSTRUCTION_PROHIBITED: LocalTestSignerHmacSigningCapability cannot be constructed directly. Use create_signer_hmac_signing_capability_for_local_test()."
        )

    def sign_trial_execution_authorization(self, *args: Any, **kwargs: Any) -> str:
        """Prohibits direct signing of arbitrary authorizations outside the I16 issuance pipeline."""
        raise SignerAdmissionVerificationError(
            "ERR_DIRECT_SIGNING_PROHIBITED",
            "Direct signing of arbitrary authorizations is prohibited. Signatures can only be minted through issue_trial_execution_authorization_for_local_test() after admission and challenge consumption.",
        )

    def _sign_for_issuance(
        self,
        *,
        ticket: LocalTestIssuanceSigningTicket,
        auth: TrialExecutionAuthorization,
    ) -> str:
        """Internal bounded signing method: verifies one-time authority-owned issuance ticket,
        validates the complete fresh I12 admission result, and proves every single one of the
        32 HMAC-covered claims matches authoritative verified inputs (including issued_at_iso
        and valid_until_iso)."""
        if type(ticket) is not LocalTestIssuanceSigningTicket:
            raise SignerAdmissionVerificationError(
                "ERR_ISSUANCE_TICKET_FORGERY",
                f"ticket must be exact LocalTestIssuanceSigningTicket, got {type(ticket).__name__}",
            )
        if type(auth) is not TrialExecutionAuthorization:
            raise SignerAdmissionVerificationError(
                "ERR_INVALID_AUTH_TYPE",
                f"expected TrialExecutionAuthorization instance, got {type(auth).__name__}",
            )

        # 1. Atomically consume the one-time ticket from its authority under authority lock
        if not hasattr(ticket.authority, "_consume_issuance_signing_ticket"):
            raise SignerAdmissionVerificationError(
                "ERR_CHALLENGE_AUTHORITY_TYPE",
                "ticket authority does not implement _consume_issuance_signing_ticket",
            )
        ticket.authority._consume_issuance_signing_ticket(ticket)

        # 2. Complete fresh I12 admission verification validation
        adm = ticket.admission_evidence
        if type(adm) is not TrialAuthorizationAdmissionEvidence:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_EVIDENCE_TYPE",
                f"admission_evidence in ticket must be exact TrialAuthorizationAdmissionEvidence, got {type(adm).__name__}",
            )
        if (
            adm.verified is not True
            or adm.director_result is None
            or adm.director_result.verified is not True
            or adm.operator_result is None
            or adm.operator_result.verified is not True
            or adm.revocation_result is None
            or adm.revocation_result.verified is not True
        ):
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "ticket admission_evidence verification flags are invalid or unverified",
            )
        if adm.proposal_id != ticket.proposal.proposal_id:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                f"admission_evidence.proposal_id mismatch: {adm.proposal_id} != {ticket.proposal.proposal_id}",
            )
        if adm.approval_id != ticket.approval_envelope.approval_record.approval_id:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                f"admission_evidence.approval_id mismatch: {adm.approval_id} != {ticket.approval_envelope.approval_record.approval_id}",
            )
        if adm.challenge_id != ticket.challenge_id:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                f"admission_evidence.challenge_id mismatch: {adm.challenge_id} != {ticket.challenge_id}",
            )
        if adm.client_nonce != ticket.operator_consent_envelope.client_nonce:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                f"admission_evidence.client_nonce mismatch: {adm.client_nonce} != {ticket.operator_consent_envelope.client_nonce}",
            )
        if adm.plan_digest_sha256 != ticket.approval_envelope.plan_digest_sha256:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "admission_evidence.plan_digest_sha256 mismatch with approval envelope",
            )
        if adm.action_payload_digest_sha256 != ticket.operator_consent_envelope.action_payload_digest_sha256:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "admission_evidence.action_payload_digest_sha256 mismatch with operator consent envelope",
            )
        if adm.target_project_uid != ticket.execution_plan.target_spec.project_id:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "admission_evidence.target_project_uid mismatch with execution plan target spec",
            )
        if adm.target_timeline_uid != ticket.execution_plan.target_spec.timeline_id:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "admission_evidence.target_timeline_uid mismatch with execution plan target spec",
            )
        if adm.source_file_sha256 != ticket.operator_consent_envelope.source_file_sha256:
            raise SignerAdmissionVerificationError(
                "ERR_ADMISSION_VERIFICATION_FAILED",
                "admission_evidence.source_file_sha256 mismatch with operator consent envelope",
            )

        # 3. Calculate expected timestamps and values from ticket context
        effective_ttl = min(float(ticket.ttl_seconds), 300.0)
        expected_issued_at_iso = ticket.verification_time_utc.isoformat().replace("+00:00", "Z")
        expected_valid_until_iso = (ticket.verification_time_utc + timedelta(seconds=effective_ttl)).isoformat().replace("+00:00", "Z")
        computed_prop_rev = compute_proposal_revision(ticket.proposal)
        raw_approval_bytes = canonical_json_bytes(approval_record_to_dict(ticket.approval_envelope.approval_record))
        expected_approval_record_digest = hashlib.sha256(raw_approval_bytes).hexdigest()
        expected_operator_gesture_digest = ticket.operator_consent_envelope.compute_consent_digest()
        target_spec = ticket.execution_plan.target_spec

        # 4. Exhaustively verify ALL 32 CLAIMS on auth
        claim_checks = [
            ("authorization_id", auth.authorization_id, ticket.consumption_result.preallocated_authorization_id),
            ("key_id", auth.key_id, self.key_id),
            ("operator_gesture_digest_sha256", auth.operator_gesture_digest_sha256, expected_operator_gesture_digest),
            ("approval_record_id", auth.approval_record_id, ticket.approval_envelope.approval_record.approval_id),
            ("approval_record_digest_sha256", auth.approval_record_digest_sha256, expected_approval_record_digest),
            ("proposal_id", auth.proposal_id, ticket.proposal.proposal_id),
            ("proposal_revision", auth.proposal_revision, computed_prop_rev),
            ("proposal_content_digest_sha256", auth.proposal_content_digest_sha256, ticket.approval_envelope.proposal_content_digest_sha256),
            ("plan_idempotency_key", auth.plan_idempotency_key, ticket.execution_plan.idempotency_key),
            ("plan_digest_sha256", auth.plan_digest_sha256, ticket.approval_envelope.plan_digest_sha256),
            ("action_payload_digest_sha256", auth.action_payload_digest_sha256, ticket.operator_consent_envelope.action_payload_digest_sha256),
            ("target_project_uid", auth.target_project_uid, target_spec.project_id),
            ("target_project_name", auth.target_project_name, target_spec.project_name),
            ("target_timeline_uid", auth.target_timeline_uid, target_spec.timeline_id),
            ("target_timeline_name", auth.target_timeline_name, target_spec.timeline_name),
            ("target_timeline_fps_numerator", auth.target_timeline_fps_numerator, int(target_spec.timeline_fps.numerator)),
            ("target_timeline_fps_denominator", auth.target_timeline_fps_denominator, int(target_spec.timeline_fps.denominator)),
            ("observed_timeline_start_frame", auth.observed_timeline_start_frame, ticket.operator_consent_envelope.record_frame),
            ("authorized_record_frame", auth.authorized_record_frame, ticket.operator_consent_envelope.record_frame),
            ("source_file_path", auth.source_file_path, ticket.operator_consent_envelope.source_file_path),
            ("source_file_sha256", auth.source_file_sha256, ticket.operator_consent_envelope.source_file_sha256),
            ("authorized_action_type", auth.authorized_action_type, "INSERT_CLIP"),
            ("authorized_track_type", auth.authorized_track_type, "video"),
            ("authorized_track_index", auth.authorized_track_index, 1),
            ("authorized_timeline_frame_start", auth.authorized_timeline_frame_start, 0),
            ("authorized_timeline_frame_duration", auth.authorized_timeline_frame_duration, 120),
            ("authorized_source_frame_start", auth.authorized_source_frame_start, 0),
            ("authorized_source_frame_duration", auth.authorized_source_frame_duration, 120),
            ("execution_id", auth.execution_id, ticket.consumption_result.preallocated_execution_id),
            ("issued_at_iso", auth.issued_at_iso, expected_issued_at_iso),
            ("valid_until_iso", auth.valid_until_iso, expected_valid_until_iso),
            ("token_nonce", auth.token_nonce, ticket.consumption_result.preallocated_execution_id),
        ]
        assert len(claim_checks) == 32

        for claim_name, actual_val, expected_val in claim_checks:
            if actual_val != expected_val:
                raise SignerAdmissionVerificationError(
                    "ERR_SIGNING_CLAIM_MISMATCH",
                    f"Claim '{claim_name}' in candidate authorization ({actual_val!r}) does not match verified input ({expected_val!r})",
                )

        # Cross-check reserved evidence claims
        if ticket.consumption_result.proposal_revision != computed_prop_rev:
            raise SignerAdmissionVerificationError(
                "ERR_SIGNING_CLAIM_MISMATCH",
                f"consumption_result.proposal_revision mismatch: {ticket.consumption_result.proposal_revision} != {computed_prop_rev}",
            )
        if ticket.consumption_result.plan_digest_sha256 != ticket.approval_envelope.plan_digest_sha256:
            raise SignerAdmissionVerificationError(
                "ERR_SIGNING_CLAIM_MISMATCH",
                "consumption_result.plan_digest_sha256 mismatch with approval envelope",
            )
        if ticket.consumption_result.action_payload_digest_sha256 != ticket.operator_consent_envelope.action_payload_digest_sha256:
            raise SignerAdmissionVerificationError(
                "ERR_SIGNING_CLAIM_MISMATCH",
                "consumption_result.action_payload_digest_sha256 mismatch with consent envelope",
            )

        sign_func = getattr(self, "_sign_func", None)
        if sign_func is None:
            raise SignerAdmissionVerificationError(
                "ERR_UNINITIALIZED_SIGNING_CAPABILITY",
                "LocalTestSignerHmacSigningCapability has not been initialized through create_signer_hmac_signing_capability_for_local_test()",
            )
        return sign_func(auth)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"LocalTestSignerHmacSigningCapability is immutable; cannot modify attribute '{name}'")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"LocalTestSignerHmacSigningCapability is immutable; cannot delete attribute '{name}'")

    def __repr__(self) -> str:
        return "<LocalTestSignerHmacSigningCapability: REDACTED>"

    def __str__(self) -> str:
        return "<LocalTestSignerHmacSigningCapability: REDACTED>"


def create_signer_hmac_signing_capability_for_local_test(
    key_bytes: bytes,
    *,
    key_id: str = "ephemeral_test_signer_key_01",
) -> LocalTestSignerHmacSigningCapability:
    """Creates an opaque, redacted signing capability for local-test TrialExecutionAuthorization HMAC signing (Gate 3-I16).

    Accepts strictly exact 32-byte ephemeral in-memory bytes (type(key_bytes) is bytes and len == 32).
    Never loads keys from disk, files, environment, or persistent paths.
    """
    if type(key_bytes) is not bytes:
        raise SignerAdmissionVerificationError(
            "ERR_HMAC_KEY_INVALID",
            f"key_bytes must be exact bytes, got {type(key_bytes).__name__}",
        )
    if len(key_bytes) != 32:
        raise SignerAdmissionVerificationError(
            "ERR_HMAC_KEY_INVALID",
            f"key_bytes must be exactly 32 bytes, got {len(key_bytes)}",
        )
    if type(key_id) is not str or not key_id or key_id != key_id.strip():
        raise SignerAdmissionVerificationError(
            "ERR_KEY_ID_INVALID",
            f"key_id must be non-empty str without whitespace padding, got {key_id!r}",
        )

    captured_key = bytes(key_bytes)
    captured_key_id = str(key_id)

    def _do_sign(auth: TrialExecutionAuthorization) -> str:
        if type(auth) is not TrialExecutionAuthorization:
            raise SignerAdmissionVerificationError(
                "ERR_INVALID_AUTH_TYPE",
                f"expected TrialExecutionAuthorization instance, got {type(auth).__name__}",
            )
        # canonical_bytes_for_auth_hmac validates the 32 fields and excludes authorization_hmac
        canonical_bytes = canonical_bytes_for_auth_hmac(auth)
        return hmac.new(captured_key, canonical_bytes, hashlib.sha256).hexdigest()

    cap = object.__new__(LocalTestSignerHmacSigningCapability)
    object.__setattr__(cap, "key_id", captured_key_id)
    object.__setattr__(cap, "_sign_func", _do_sign)
    return cap


def issue_trial_execution_authorization_for_local_test(
    *,
    approval_envelope: SignedApprovalEnvelope,
    proposal: EditPlanProposal,
    execution_plan: TimelineExecutionPlan,
    director_public_key: Ed25519PublicKey,
    expected_director_key_id: str,
    operator_consent_envelope: SignedOperatorConsentEnvelope,
    operator_public_key: Ed25519PublicKey,
    challenge_authority: LocalTestChallengeAuthority,
    challenge_id: str,
    signing_capability: LocalTestSignerHmacSigningCapability,
    verification_key_handle: LocalTestSignerKeyHandle,
    revocation_trust_directory: Union[str, Path],
    revocation_registry_directory: Union[str, Path],
    nonce_ledger_directory: Union[str, Path],
    verification_time_utc: datetime,
    ttl_seconds: Union[int, float] = 300.0,
    **kwargs: Any,
) -> TrialExecutionAuthorization:
    """Gate 3-I16: Bounded local-test-only TrialExecutionAuthorization issuance core.

    Composes:
    1. Fresh I12 admission verification (verify_trial_authorization_admission_for_local_test).
    2. Fresh I15 atomic challenge consumption (verify_and_consume_challenge_for_local_test).
    3. Authority-owned consumption evidence verification and reservation.
    4. Cross-binding of approval, consent, plan, target, and consumption evidence.
    5. Canonical 32-claim TrialExecutionAuthorization construction.
    6. Local-test-only HMAC signing using LocalTestSignerHmacSigningCapability.
    7. Round-trip constant-time verification using LocalTestSignerKeyHandle before returning.

    Safety scope:
    Local test only; ephemeral in-memory test keys only; zero production keystore or key access;
    zero daemon/socket/RPC; zero dispatch; zero live Resolve operations.
    """
    if kwargs:
        for k in kwargs:
            raise SignerAdmissionVerificationError(
                "ERR_CALLER_OVERRIDE_PROHIBITED",
                f"unknown or prohibited argument '{k}', caller cannot supply overrides",
            )

    # Validate nominal types
    if type(challenge_authority) is not LocalTestChallengeAuthority:
        raise SignerAdmissionVerificationError(
            "ERR_CHALLENGE_AUTHORITY_TYPE",
            f"challenge_authority must be exact LocalTestChallengeAuthority, got {type(challenge_authority).__name__}",
        )
    if type(signing_capability) is not LocalTestSignerHmacSigningCapability:
        raise SignerAdmissionVerificationError(
            "ERR_SIGNING_CAPABILITY_TYPE",
            f"signing_capability must be exact LocalTestSignerHmacSigningCapability, got {type(signing_capability).__name__}",
        )
    if type(verification_key_handle) is not LocalTestSignerKeyHandle:
        raise SignerAdmissionVerificationError(
            "ERR_VERIFICATION_KEY_HANDLE_TYPE",
            f"verification_key_handle must be exact LocalTestSignerKeyHandle, got {type(verification_key_handle).__name__}",
        )
    if (
        type(ttl_seconds) not in (int, float)
        or isinstance(ttl_seconds, bool)
        or not math.isfinite(ttl_seconds)
        or ttl_seconds <= 0
    ):
        raise SignerAdmissionVerificationError(
            "ERR_TTL_INVALID",
            f"ttl_seconds must be a positive finite number, got {ttl_seconds!r}",
        )
    if type(verification_time_utc) is not datetime or verification_time_utc.tzinfo is None or verification_time_utc.utcoffset() != timedelta(0):
        raise SignerAdmissionVerificationError(
            "ERR_VERIFICATION_TIME_INVALID",
            f"verification_time_utc must be UTC datetime with offset zero, got {verification_time_utc!r}",
        )
    if type(challenge_id) is not str or not challenge_id or challenge_id != challenge_id.strip():
        raise SignerAdmissionVerificationError(
            "ERR_CHALLENGE_ID_INVALID",
            f"challenge_id must be non-empty str without whitespace padding, got {challenge_id!r}",
        )

    # Step 1 & 2: Indivisible fresh authoritative admission verification (I12) & authority registration
    admission_evidence = challenge_authority.verify_and_register_admission_for_local_test(
        challenge_id=challenge_id,
        approval_envelope=approval_envelope,
        proposal=proposal,
        execution_plan=execution_plan,
        director_public_key=director_public_key,
        expected_director_key_id=expected_director_key_id,
        operator_consent_envelope=operator_consent_envelope,
        operator_public_key=operator_public_key,
        revocation_trust_directory=revocation_trust_directory,
        revocation_registry_directory=revocation_registry_directory,
        verification_time_utc=verification_time_utc,
        ttl_seconds=float(ttl_seconds),
    )

    # Step 3: Consume before sign (I15 challenge consumption)
    consumption_result = challenge_authority.verify_and_consume_challenge_for_local_test(
        challenge_id=challenge_id,
        consent_envelope=operator_consent_envelope,
        operator_public_key=operator_public_key,
        revocation_trust_directory=revocation_trust_directory,
        revocation_registry_directory=revocation_registry_directory,
        nonce_ledger_directory=nonce_ledger_directory,
    )

    # Step 4: Non-forgeable-in-process consumption provenance check & single-use reservation
    reserved_evidence = challenge_authority.reserve_consumed_evidence_for_issuance(
        challenge_id, consumption_result
    )

    # Step 5: Complete Cross-Bindings & Invariants
    if reserved_evidence.challenge_id != challenge_id or reserved_evidence.challenge_id != operator_consent_envelope.challenge_id:
        raise SignerAdmissionVerificationError(
            "ERR_CHALLENGE_BINDING_MISMATCH",
            f"challenge_id mismatch: reserved '{reserved_evidence.challenge_id}', requested '{challenge_id}'",
        )
    if reserved_evidence.client_nonce != operator_consent_envelope.client_nonce or reserved_evidence.client_nonce != admission_evidence.client_nonce:
        raise SignerAdmissionVerificationError(
            "ERR_NONCE_BINDING_MISMATCH",
            "client_nonce mismatch between consumption result, consent envelope, and admission evidence",
        )
    if reserved_evidence.approval_record_id != approval_envelope.approval_record.approval_id or reserved_evidence.approval_record_id != admission_evidence.approval_id:
        raise SignerAdmissionVerificationError(
            "ERR_APPROVAL_BINDING_MISMATCH",
            "approval_record_id mismatch between consumption result, approval envelope, and admission evidence",
        )
    computed_proposal_revision = compute_proposal_revision(proposal)
    if reserved_evidence.proposal_revision != computed_proposal_revision or reserved_evidence.proposal_revision != approval_envelope.approval_record.proposal_revision:
        raise SignerAdmissionVerificationError(
            "ERR_PROPOSAL_REVISION_MISMATCH",
            "proposal_revision mismatch between consumption result, proposal, and approval envelope",
        )
    if (
        reserved_evidence.plan_digest_sha256 != approval_envelope.plan_digest_sha256
        or reserved_evidence.plan_digest_sha256 != operator_consent_envelope.plan_digest_sha256
        or reserved_evidence.plan_digest_sha256 != admission_evidence.plan_digest_sha256
    ):
        raise SignerAdmissionVerificationError(
            "ERR_PLAN_DIGEST_MISMATCH",
            "plan_digest_sha256 mismatch between consumption result, approval envelope, consent envelope, and admission evidence",
        )
    if (
        reserved_evidence.action_payload_digest_sha256 != operator_consent_envelope.action_payload_digest_sha256
        or reserved_evidence.action_payload_digest_sha256 != admission_evidence.action_payload_digest_sha256
    ):
        raise SignerAdmissionVerificationError(
            "ERR_ACTION_PAYLOAD_DIGEST_MISMATCH",
            "action_payload_digest_sha256 mismatch between consumption result, consent envelope, and admission evidence",
        )

    # Target and source bindings
    target_spec = execution_plan.target_spec
    if (
        operator_consent_envelope.target_project_uid != admission_evidence.target_project_uid
        or operator_consent_envelope.target_project_uid != target_spec.project_id
    ):
        raise SignerAdmissionVerificationError(
            "ERR_TARGET_PROJECT_UID_MISMATCH",
            "target_project_uid mismatch between consent envelope, admission evidence, and execution plan",
        )
    if operator_consent_envelope.target_project_name != target_spec.project_name:
        raise SignerAdmissionVerificationError(
            "ERR_TARGET_PROJECT_NAME_MISMATCH",
            "target_project_name mismatch between consent envelope and execution plan",
        )
    if (
        operator_consent_envelope.target_timeline_uid != admission_evidence.target_timeline_uid
        or operator_consent_envelope.target_timeline_uid != target_spec.timeline_id
    ):
        raise SignerAdmissionVerificationError(
            "ERR_TARGET_TIMELINE_UID_MISMATCH",
            "target_timeline_uid mismatch between consent envelope, admission evidence, and execution plan",
        )
    if operator_consent_envelope.target_timeline_name != target_spec.timeline_name:
        raise SignerAdmissionVerificationError(
            "ERR_TARGET_TIMELINE_NAME_MISMATCH",
            "target_timeline_name mismatch between consent envelope and execution plan",
        )
    if operator_consent_envelope.source_file_sha256 != admission_evidence.source_file_sha256:
        raise SignerAdmissionVerificationError(
            "ERR_SOURCE_FILE_SHA256_MISMATCH",
            "source_file_sha256 mismatch between consent envelope and admission evidence",
        )

    # Action scope restrictions
    if operator_consent_envelope.action_type != "INSERT_CLIP":
        raise SignerAdmissionVerificationError(
            "ERR_ACTION_TYPE_INVALID",
            f"action_type must be 'INSERT_CLIP', got {operator_consent_envelope.action_type!r}",
        )
    if operator_consent_envelope.track_type != "video":
        raise SignerAdmissionVerificationError(
            "ERR_TRACK_TYPE_INVALID",
            f"track_type must be 'video', got {operator_consent_envelope.track_type!r}",
        )
    if operator_consent_envelope.track_index != 1:
        raise SignerAdmissionVerificationError(
            "ERR_TRACK_INDEX_INVALID",
            f"track_index must be 1, got {operator_consent_envelope.track_index!r}",
        )
    if operator_consent_envelope.duration_frames != 120:
        raise SignerAdmissionVerificationError(
            "ERR_DURATION_FRAMES_INVALID",
            f"duration_frames must be 120, got {operator_consent_envelope.duration_frames!r}",
        )

    # Anti-aliasing check among identities
    auth_id = reserved_evidence.preallocated_authorization_id
    exec_id = reserved_evidence.preallocated_execution_id
    client_nonce = reserved_evidence.client_nonce
    approval_id = reserved_evidence.approval_record_id
    id_list = [auth_id, exec_id, client_nonce, approval_id]
    if len(set(id_list)) != 4:
        raise SignerAdmissionVerificationError(
            "ERR_IDENTITY_ALIASING",
            "authorization_id, execution_id, client_nonce, and approval_id must be pairwise distinct",
        )

    # Step 5b: Issue one-time unforgeable internal signing ticket bound to fresh admission & context
    ticket = challenge_authority.create_issuance_signing_ticket(
        challenge_id=challenge_id,
        consumption_result=consumption_result,
        admission_evidence=admission_evidence,
        approval_envelope=approval_envelope,
        proposal=proposal,
        execution_plan=execution_plan,
        operator_consent_envelope=operator_consent_envelope,
        verification_time_utc=verification_time_utc,
        ttl_seconds=ttl_seconds,
    )

    # Step 6: Construct 32 claims, build temp authorization, sign with signing_capability
    effective_ttl = min(float(ttl_seconds), 300.0)
    issued_dt = verification_time_utc
    valid_dt = verification_time_utc + timedelta(seconds=effective_ttl)
    issued_at_iso = issued_dt.isoformat().replace("+00:00", "Z")
    valid_until_iso = valid_dt.isoformat().replace("+00:00", "Z")

    approval_dict = approval_record_to_dict(approval_envelope.approval_record)
    raw_approval_bytes = canonical_json_bytes(approval_dict)
    approval_record_digest = hashlib.sha256(raw_approval_bytes).hexdigest()

    fps_num = int(target_spec.timeline_fps.numerator)
    fps_den = int(target_spec.timeline_fps.denominator)
    rec_frame = operator_consent_envelope.record_frame

    # Construct temporary DTO with placeholder HMAC for canonical payload extraction
    temp_auth = TrialExecutionAuthorization(
        authorization_id=auth_id,
        key_id=signing_capability.key_id,
        operator_gesture_digest_sha256=operator_consent_envelope.compute_consent_digest(),
        approval_record_id=approval_id,
        approval_record_digest_sha256=approval_record_digest,
        proposal_id=proposal.proposal_id,
        proposal_revision=computed_proposal_revision,
        proposal_content_digest_sha256=approval_envelope.proposal_content_digest_sha256,
        plan_idempotency_key=execution_plan.idempotency_key,
        plan_digest_sha256=approval_envelope.plan_digest_sha256,
        action_payload_digest_sha256=operator_consent_envelope.action_payload_digest_sha256,
        target_project_uid=operator_consent_envelope.target_project_uid,
        target_project_name=operator_consent_envelope.target_project_name,
        target_timeline_uid=operator_consent_envelope.target_timeline_uid,
        target_timeline_name=operator_consent_envelope.target_timeline_name,
        target_timeline_fps_numerator=fps_num,
        target_timeline_fps_denominator=fps_den,
        observed_timeline_start_frame=rec_frame,
        authorized_record_frame=rec_frame,
        source_file_path=operator_consent_envelope.source_file_path,
        source_file_sha256=operator_consent_envelope.source_file_sha256,
        authorized_action_type="INSERT_CLIP",
        authorized_track_type="video",
        authorized_track_index=1,
        authorized_timeline_frame_start=0,
        authorized_timeline_frame_duration=120,
        authorized_source_frame_start=0,
        authorized_source_frame_duration=120,
        execution_id=exec_id,
        issued_at_iso=issued_at_iso,
        valid_until_iso=valid_until_iso,
        token_nonce=exec_id,
        authorization_hmac="0" * 64,
    )

    # Compute HMAC over canonical RFC 8785 bytes of the 32 claims via bounded issuance method
    real_hmac = signing_capability._sign_for_issuance(
        ticket=ticket,
        auth=temp_auth,
    )

    # Construct final TrialExecutionAuthorization with real HMAC
    final_auth = TrialExecutionAuthorization(
        authorization_id=auth_id,
        key_id=signing_capability.key_id,
        operator_gesture_digest_sha256=operator_consent_envelope.compute_consent_digest(),
        approval_record_id=approval_id,
        approval_record_digest_sha256=approval_record_digest,
        proposal_id=proposal.proposal_id,
        proposal_revision=computed_proposal_revision,
        proposal_content_digest_sha256=approval_envelope.proposal_content_digest_sha256,
        plan_idempotency_key=execution_plan.idempotency_key,
        plan_digest_sha256=approval_envelope.plan_digest_sha256,
        action_payload_digest_sha256=operator_consent_envelope.action_payload_digest_sha256,
        target_project_uid=operator_consent_envelope.target_project_uid,
        target_project_name=operator_consent_envelope.target_project_name,
        target_timeline_uid=operator_consent_envelope.target_timeline_uid,
        target_timeline_name=operator_consent_envelope.target_timeline_name,
        target_timeline_fps_numerator=fps_num,
        target_timeline_fps_denominator=fps_den,
        observed_timeline_start_frame=rec_frame,
        authorized_record_frame=rec_frame,
        source_file_path=operator_consent_envelope.source_file_path,
        source_file_sha256=operator_consent_envelope.source_file_sha256,
        authorized_action_type="INSERT_CLIP",
        authorized_track_type="video",
        authorized_track_index=1,
        authorized_timeline_frame_start=0,
        authorized_timeline_frame_duration=120,
        authorized_source_frame_start=0,
        authorized_source_frame_duration=120,
        execution_id=exec_id,
        issued_at_iso=issued_at_iso,
        valid_until_iso=valid_until_iso,
        token_nonce=exec_id,
        authorization_hmac=real_hmac,
    )

    # Step 7: Round-trip verification using I13 verification handle
    if not verification_key_handle.verify_trial_execution_authorization(final_auth):
        raise SignerAdmissionVerificationError(
            "ERR_HMAC_VERIFICATION_FAILED",
            "Newly issued TrialExecutionAuthorization failed round-trip verification with verification_key_handle",
        )

    return final_auth


# ==============================================================================
# Gate 3-I18: Local-Test Signer Unix Domain Socket Server Interface
# ==============================================================================

MAX_SOCKET_FRAME_SIZE: int = 65536
SO_PEERCRED_CONST: int = getattr(socket, "SO_PEERCRED", 17)


class SignerSocketServerError(TrialSignerError):
    """Raised when LocalTestSignerSocketServer encounters an operational, security, or protocol error."""

    def __init__(
        self,
        message: str,
        code: str = "ERR_SIGNER_SOCKET_SERVER",
        details: Optional[Dict[str, Any]] = None,
        cleanup_errors: Optional[List[Exception]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.cleanup_errors = cleanup_errors or []


PROHIBITED_CLAIM_OVERRIDES: Set[str] = {
    "private_key",
    "secret",
    "signing_capability",
    "key_handle",
    "auth_hmac",
    "hmac_key",
    "secret_key",
    "is_valid",
    "verification_result",
}

METHOD_PARAM_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "request_challenge": {
        "required": {
            "expected_director_key_id": str,
            "operator_id": str,
            "operator_key_id": str,
            "approval_envelope": dict,
            "proposal": dict,
            "execution_plan": dict,
        },
        "optional": {
            "director_public_key_pem": str,
            "ttl_seconds": (int, float),
            "context_request_id": str,
        },
        "prohibited_overrides": {
            "challenge_id",
            "client_nonce",
            "preallocated_authorization_id",
            "preallocated_execution_id",
            "issued_at_iso",
            "expires_at_iso",
            "valid_until_iso",
        },
    },
    "submit_operator_consent": {
        "required": {
            "challenge_id": str,
            "consent_envelope": dict,
        },
        "optional": {
            "operator_public_key_pem": str,
        },
        "prohibited_overrides": {
            "client_nonce",
            "operator_id",
            "operator_key_id",
            "target_project_uid",
            "target_timeline_uid",
            "action_type",
            "source_file_path",
            "source_file_sha256",
            "preallocated_authorization_id",
            "preallocated_execution_id",
            "issued_at_iso",
            "valid_until_iso",
            "authorization_id",
            "execution_id",
        },
    },
    "verify_authorization_oracle": {
        "required": {
            "request_nonce": str,
            "signed_authorization": dict,
            "action_payload_digest_sha256": str,
            "target_project_uid": str,
            "target_timeline_uid": str,
        },
        "optional": {
            "requested_at_iso": str,
        },
        "prohibited_overrides": {
            "is_valid",
            "verification_result",
            "oracle_signature",
            "oracle_public_key",
            "auth_hmac",
            "signing_key",
            "signing_capability",
        },
    },
}


def _validate_method_params(
    method: str,
    params: Any,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validates method parameters against the declared Version-1 schema.

    Enforces exact required/optional keys and types, rejecting unknown fields,
    key material, and prohibited identity/claim overrides prior to handler invocation.
    """
    if not isinstance(params, dict):
        return False, "ERR_INVALID_PARAMS", "params must be a dictionary"

    schema = METHOD_PARAM_SCHEMAS.get(method)
    if schema is None:
        return False, "ERR_UNSUPPORTED_METHOD", f"unsupported method '{method}'"

    # 1. Prohibit universal secret/key material
    for forbidden_key in PROHIBITED_CLAIM_OVERRIDES:
        if forbidden_key in params:
            return (
                False,
                "ERR_KEY_MATERIAL_PROHIBITED",
                f"prohibited secret key material or override '{forbidden_key}' in params",
            )

    # 2. Prohibit method-specific identity/claim overrides
    prohibited_overrides = schema.get("prohibited_overrides", set())
    for override_key in prohibited_overrides:
        if override_key in params:
            return (
                False,
                "ERR_PARAM_OVERRIDE_PROHIBITED",
                f"caller cannot supply prohibited identity/claim override '{override_key}' in '{method}' params",
            )

    # 3. Check for unknown fields
    required_fields = schema.get("required", {})
    optional_fields = schema.get("optional", {})
    allowed_keys = set(required_fields.keys()) | set(optional_fields.keys())

    unknown_keys = set(params.keys()) - allowed_keys
    if unknown_keys:
        sorted_unknown = sorted(unknown_keys)
        return (
            False,
            "ERR_UNKNOWN_PARAM",
            f"unknown parameter fields {sorted_unknown} for method '{method}'",
        )

    # 4. Check for missing required fields
    missing_keys = set(required_fields.keys()) - set(params.keys())
    if missing_keys:
        sorted_missing = sorted(missing_keys)
        return (
            False,
            "ERR_MISSING_PARAM",
            f"missing required parameter fields {sorted_missing} for method '{method}'",
        )

    # 5. Type validation
    all_field_types = {**required_fields, **optional_fields}
    for key, val in params.items():
        expected_type = all_field_types[key]
        if isinstance(val, bool):
            is_bool_expected = (expected_type is bool) or (
                isinstance(expected_type, tuple) and bool in expected_type
            )
            if not is_bool_expected:
                return (
                    False,
                    "ERR_PARAM_TYPE_MISMATCH",
                    f"parameter '{key}' must be of type {expected_type}, got bool",
                )
        if not isinstance(val, expected_type):
            return (
                False,
                "ERR_PARAM_TYPE_MISMATCH",
                f"parameter '{key}' must be of type {expected_type}, got {type(val).__name__}",
            )
        if key == "ttl_seconds":
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val <= 0:
                return (
                    False,
                    "ERR_PARAM_VALUE_INVALID",
                    f"parameter 'ttl_seconds' must be a positive finite number, got {val!r}",
                )
        if isinstance(val, str) and not val.strip() and key != "director_public_key_pem":
            return (
                False,
                "ERR_PARAM_VALUE_INVALID",
                f"parameter '{key}' cannot be empty string",
            )

    return True, None, None


def _validate_and_anchor_socket_dir(
    socket_path: Union[str, Path],
    temp_root: Union[str, Path],
) -> Tuple[Path, Path, int, str]:
    """Validates socket path beneath temp_root and returns an open verified parent directory descriptor.

    Returns: (sock_path, root_path, parent_dir_fd, leaf_filename).
    The caller assumes ownership of parent_dir_fd and must close it on termination.
    """
    if not isinstance(socket_path, (str, Path)) or not str(socket_path).strip():
        raise SignerSocketServerError(
            "ERR_INVALID_SOCKET_PATH: socket_path must be non-empty string or Path",
            code="ERR_INVALID_SOCKET_PATH",
        )
    if not isinstance(temp_root, (str, Path)) or not str(temp_root).strip():
        raise SignerSocketServerError(
            "ERR_INVALID_TEMP_ROOT: temp_root must be non-empty string or Path",
            code="ERR_INVALID_TEMP_ROOT",
        )

    raw_socket_str = str(socket_path)
    raw_root_str = str(temp_root)

    # 1. Reject production paths lexically before any filesystem access
    for forbidden in ("/run", "/var", "/etc", "/usr", "/bin", "/sbin", "/opt", "/sys", "/proc", "/dev"):
        if raw_socket_str.startswith(forbidden) or raw_root_str.startswith(forbidden):
            raise SignerSocketServerError(
                f"ERR_FORBIDDEN_SOCKET_PATH: socket_path '{raw_socket_str}' in production or system directory is prohibited",
                code="ERR_FORBIDDEN_SOCKET_PATH",
            )
    if "/run/resolveflow" in raw_socket_str or "/var/lib/resolveflow" in raw_socket_str:
        raise SignerSocketServerError(
            "ERR_PRODUCTION_PATH_PROHIBITED: production path is strictly prohibited",
            code="ERR_PRODUCTION_PATH_PROHIBITED",
        )

    # 2. Pure lexical normalization and absolute path checks
    norm_sock_str = os.path.normpath(raw_socket_str)
    norm_root_str = os.path.normpath(raw_root_str)
    sock_path = Path(norm_sock_str)
    root_path = Path(norm_root_str)

    if not sock_path.is_absolute() or not root_path.is_absolute():
        raise SignerSocketServerError(
            f"ERR_PATH_NOT_ABSOLUTE: socket_path '{sock_path}' and temp_root '{root_path}' must be absolute",
            code="ERR_PATH_NOT_ABSOLUTE",
        )

    if ".." in sock_path.parts or ".." in root_path.parts:
        raise SignerSocketServerError(
            "ERR_PATH_TRAVERSAL_DETECTED: path components cannot contain '..'",
            code="ERR_PATH_TRAVERSAL_DETECTED",
        )

    # 3. Pure lexical containment check: MUST be relative_to(root_path)
    try:
        rel_path = sock_path.relative_to(root_path)
    except ValueError:
        raise SignerSocketServerError(
            f"ERR_PATH_OUTSIDE_TEMP_ROOT: socket_path '{sock_path}' must be beneath temp_root '{root_path}'",
            code="ERR_PATH_OUTSIDE_TEMP_ROOT",
        )

    if rel_path == Path(".") or len(rel_path.parts) == 0:
        raise SignerSocketServerError(
            f"ERR_SOCKET_CANNOT_BE_ROOT: socket_path '{sock_path}' cannot be the temp_root itself",
            code="ERR_SOCKET_CANNOT_BE_ROOT",
        )

    # 4. Validate trusted temp_root separately
    cur_root = root_path
    while True:
        try:
            st = os.lstat(str(cur_root))
            if stat.S_ISLNK(st.st_mode):
                raise SignerSocketServerError(
                    f"ERR_SYMLINK_IN_PATH: symlink detected in root '{cur_root}'",
                    code="ERR_SYMLINK_IN_PATH",
                )
        except FileNotFoundError as exc:
            raise SignerSocketServerError(
                f"ERR_TEMP_ROOT_NOT_DIR: root component '{cur_root}' does not exist",
                code="ERR_TEMP_ROOT_NOT_DIR",
            ) from exc
        if cur_root.parent == cur_root:
            break
        cur_root = cur_root.parent

    st_root = os.lstat(str(root_path))
    if not stat.S_ISDIR(st_root.st_mode):
        raise SignerSocketServerError(
            f"ERR_TEMP_ROOT_NOT_DIR: temp_root '{root_path}' must be an existing directory",
            code="ERR_TEMP_ROOT_NOT_DIR",
        )
    if (st_root.st_mode & 0o077) != 0:
        raise SignerSocketServerError(
            f"ERR_INSECURE_DIR_PERMS: temp_root '{root_path}' mode must be 0700, got {oct(st_root.st_mode & 0o777)}",
            code="ERR_INSECURE_DIR_PERMS",
        )

    # 5. Traverse in-root relative path descriptor-by-descriptor with no-follow semantics
    open_dir_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    try:
        current_fd = os.open(str(root_path), open_dir_flags)
    except OSError as exc:
        raise SignerSocketServerError(
            f"ERR_CANNOT_OPEN_ROOT: cannot open temp_root '{root_path}': {exc}",
            code="ERR_SYMLINK_IN_PATH" if getattr(exc, "errno", 0) == errno.ELOOP else "ERR_TEMP_ROOT_NOT_DIR",
        ) from exc

    open_fds = [current_fd]
    try:
        rel_parts = rel_path.parts
        dir_parts = rel_parts[:-1]
        leaf_part = rel_parts[-1]

        for part in dir_parts:
            try:
                st_part = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise SignerSocketServerError(
                    f"ERR_PARENT_NOT_DIR: parent directory component '{part}' does not exist",
                    code="ERR_PARENT_NOT_DIR",
                ) from exc

            if stat.S_ISLNK(st_part.st_mode):
                raise SignerSocketServerError(
                    f"ERR_SYMLINK_IN_PATH: symlink detected in path component '{part}'",
                    code="ERR_SYMLINK_IN_PATH",
                )
            if not stat.S_ISDIR(st_part.st_mode):
                raise SignerSocketServerError(
                    f"ERR_PARENT_NOT_DIR: component '{part}' must be a directory",
                    code="ERR_PARENT_NOT_DIR",
                )
            if (st_part.st_mode & 0o077) != 0:
                raise SignerSocketServerError(
                    f"ERR_INSECURE_DIR_PERMS: directory component '{part}' mode must be 0700, got {oct(st_part.st_mode & 0o777)}",
                    code="ERR_INSECURE_DIR_PERMS",
                )

            next_fd = os.open(part, open_dir_flags, dir_fd=current_fd)
            open_fds.append(next_fd)
            current_fd = next_fd

        st_parent = os.fstat(current_fd)
        if (st_parent.st_mode & 0o077) != 0:
            raise SignerSocketServerError(
                f"ERR_INSECURE_DIR_PERMS: parent directory mode must be 0700, got {oct(st_parent.st_mode & 0o777)}",
                code="ERR_INSECURE_DIR_PERMS",
            )

        try:
            st_leaf = os.stat(leaf_part, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(st_leaf.st_mode):
                raise SignerSocketServerError(
                    f"ERR_SYMLINK_IN_PATH: socket leaf '{leaf_part}' is a symlink",
                    code="ERR_SYMLINK_IN_PATH",
                )
        except FileNotFoundError:
            pass

        # Close all intermediate directory descriptors, preserving only current_fd (the immediate parent)
        for fd in open_fds[:-1]:
            try:
                os.close(fd)
            except OSError:
                pass
        open_fds.clear()

        return sock_path, root_path, current_fd, leaf_part
    finally:
        for fd in open_fds:
            try:
                os.close(fd)
            except OSError:
                pass


libc = ctypes.CDLL(None, use_errno=True)
_renameat2_func = getattr(libc, "renameat2", None)
if _renameat2_func is not None:
    _renameat2_func.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _renameat2_func.restype = ctypes.c_int

RENAME_NOREPLACE = 1


def _rename_no_replace(
    src_dir_fd: int,
    src_name: str,
    dst_dir_fd: int,
    dst_name: str,
) -> bool:
    """Atomically renames src_name to dst_name descriptor-relatively without replacing dst_name.

    Uses Linux renameat2(..., RENAME_NOREPLACE).
    Returns True if renamed successfully into a vacant dst_name.
    Returns False if dst_name already exists (errno == EEXIST).
    Raises SignerSocketServerError if unsupported or if filesystem lacks RENAME_NOREPLACE.
    """
    if _renameat2_func is None:
        raise SignerSocketServerError(
            "ERR_NO_REPLACE_RENAME_UNSUPPORTED: atomic renameat2 RENAME_NOREPLACE is required for race-safe tombstone restoration",
            code="ERR_NO_REPLACE_RENAME_UNSUPPORTED",
        )
    ret = _renameat2_func(
        src_dir_fd,
        src_name.encode("utf-8"),
        dst_dir_fd,
        dst_name.encode("utf-8"),
        RENAME_NOREPLACE,
    )
    if ret == 0:
        return True
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        return False
    if err == errno.ENOSYS:
        raise SignerSocketServerError(
            "ERR_NO_REPLACE_RENAME_UNSUPPORTED: renameat2 returned ENOSYS; filesystem or kernel lacks RENAME_NOREPLACE support",
            code="ERR_NO_REPLACE_RENAME_UNSUPPORTED",
        )
    raise OSError(err, f"renameat2 failed: {os.strerror(err)}")


def _restore_tombstone_safely(
    dir_fd: int,
    tombstone: str,
    target_leaf: str,
    context_desc: str,
) -> Tuple[bool, Optional[str], Optional[Exception]]:
    """Restores tombstone back to target_leaf without replacing target_leaf (I18-R6-02).

    Guarantees no-overwrite under concurrent recreation:
    - If target_leaf is vacant: atomically restores tombstone -> target_leaf.
    - If target_leaf was recreated: leaves target_leaf untouched, preserves
      tombstoned entry under a persistent .preserved_foreign_<pid>_<uuid> name,
      and reports a structured collision error.
    """
    try:
        if _rename_no_replace(dir_fd, tombstone, dir_fd, target_leaf):
            return True, None, None

        # Collision: target_leaf was recreated concurrently while tombstone was held
        preserved_name = f".preserved_foreign_{os.getpid()}_{uuid.uuid4().hex}"
        try:
            os.rename(tombstone, preserved_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except Exception:
            preserved_name = tombstone

        coll_err = SignerSocketServerError(
            f"ERR_TOMBSTONE_RESTORE_COLLISION: destination '{target_leaf}' was recreated while restoring foreign entry; foreign entry preserved as '{preserved_name}' ({context_desc})",
            code="ERR_TOMBSTONE_RESTORE_COLLISION",
            details={
                "target_leaf": target_leaf,
                "preserved_as": preserved_name,
                "context": context_desc,
            },
        )
        return False, preserved_name, coll_err
    except Exception as exc:
        return False, None, exc


def _validate_test_socket_path(
    socket_path: Union[str, Path],
    temp_root: Union[str, Path],
) -> Tuple[Path, Path]:
    """Validates that a Unix socket path is strictly and physically within a test temporary root."""
    sock_path, root_path, parent_fd, _ = _validate_and_anchor_socket_dir(socket_path, temp_root)
    try:
        os.close(parent_fd)
    except OSError:
        pass
    return sock_path, root_path


class LocalTestSignerSocketServer:
    """Local-test, in-process Unix domain socket server interface for Signer operations (G07-07B7-I18).

    Documentation of Safety & Test-Only Semantics:
    - In-process test server only: must be explicitly started and stopped by tests.
    - Zero daemonization, zero background subprocesses, zero OS service installation.
    - Operates strictly on temporary sockets beneath test-owned temporary roots; mode 0660.
    - Anchors socket and lock operations strictly to verified parent directory descriptor.
    - Authenticates local peers using SO_PEERCRED and an explicit UID allowlist.
    - Length-prefixed framing with RFC 8785 canonical JSON; strict duplicate-key rejection.
    - Exposes exactly three bounded methods with strict parameter schemas:
      1. request_challenge
      2. submit_operator_consent
      3. verify_authorization_oracle
    - Strictly prohibits passing or returning private keys, secret key handles, or HMAC signing capabilities.
    - Socket permissions (mode 0660) are applied directly to the pinned bound inode descriptor
      via /proc/self/fd/sock_node_fd without releasing the pinned identity, eliminating any window
      between verification and permission mutation (I18-R7-01).
    - Under the Linux VFS API, unlinkat does not support an atomic compare-and-delete flag.
      The server isolates candidates under unpredictable per-PID UUID tombstones (.tombstone_<pid>_<uuid>),
      verifies inode identity descriptor-relatively, and re-verifies identity at the pre-unlink boundary,
      narrowing the check/use window. As a documented local-test limitation, this window cannot be made
      mathematically atomic against a concurrent same-UID writer after the final stat, but the server
      fails closed without unlinking if an identity discrepancy is detected (I18-R7-02, I18-R8-01).
    """

    def __init__(
        self,
        *,
        socket_path: Union[str, Path],
        temp_root: Union[str, Path],
        challenge_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        consent_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        oracle_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        allowed_peer_uids: Optional[Set[int]] = None,
        peer_credential_provider: Optional[Callable[[socket.socket], int]] = None,
        clock: Optional[Callable[[], datetime]] = None,
        max_frame_bytes: int = MAX_SOCKET_FRAME_SIZE,
    ) -> None:
        self._socket_path, self._temp_root, self._parent_dir_fd, self._leaf_filename = (
            _validate_and_anchor_socket_dir(socket_path, temp_root)
        )
        self._lock_filename = f"{self._leaf_filename}.lock"
        self._lock_path = Path(f"{self._socket_path}.lock")
        self._challenge_handler = challenge_handler
        self._consent_handler = consent_handler
        self._oracle_handler = oracle_handler
        self._allowed_peer_uids: Set[int] = set(allowed_peer_uids) if allowed_peer_uids is not None else {os.getuid()}
        self._peer_credential_provider = peer_credential_provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_frame_bytes = max_frame_bytes

        self._server_sock: Optional[socket.socket] = None
        self._lock_fd: Optional[int] = None
        self._failed_lock_fd: Optional[int] = None
        self._failed_server_sock: Optional[socket.socket] = None
        self._bound_socket_identity: Optional[Tuple[int, int]] = None
        self._post_bind_hook: Optional[Callable[[], None]] = None
        self._post_probe_hook: Optional[Callable[[], None]] = None
        self._pre_chmod_hook: Optional[Callable[[], None]] = None
        self._pre_unlink_hook: Optional[Callable[[], None]] = None
        self._in_tombstone_hook: Optional[Callable[[], None]] = None
        self._pre_tombstone_unlink_hook: Optional[Callable[[], None]] = None
        self._in_stale_tombstone_hook: Optional[Callable[[], None]] = None
        self._pre_stale_tombstone_unlink_hook: Optional[Callable[[], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._seen_request_ids: Set[str] = set()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Starts the local-test socket server with exclusive singleton lock anchored to parent directory descriptor."""
        with self._lock:
            if self._running:
                raise SignerSocketServerError("ERR_SERVER_ALREADY_RUNNING", code="ERR_SERVER_ALREADY_RUNNING")

            # Ensure parent_dir_fd is open and valid
            if self._parent_dir_fd is None:
                self._socket_path, self._temp_root, self._parent_dir_fd, self._leaf_filename = (
                    _validate_and_anchor_socket_dir(self._socket_path, self._temp_root)
                )

            self._bound_socket_identity = None

            # 0. Verify parent directory pathname on disk still resolves to anchored descriptor (no swap window)
            try:
                st_disk = os.stat(str(self._socket_path.parent), follow_symlinks=False)
                st_fd = os.fstat(self._parent_dir_fd)
                if (st_disk.st_dev, st_disk.st_ino) != (st_fd.st_dev, st_fd.st_ino):
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_ANCHOR_DISCREPANCY: parent directory '{self._socket_path.parent}' inode discrepancy detected prior to bind",
                        code="ERR_SOCKET_ANCHOR_DISCREPANCY",
                    )
            except OSError as exc:
                if not isinstance(exc, SignerSocketServerError):
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_ANCHOR_DISCREPANCY: parent directory '{self._socket_path.parent}' stat check failed prior to bind: {exc}",
                        code="ERR_SOCKET_ANCHOR_DISCREPANCY",
                    ) from exc
                raise

            # 1. Acquire exclusive non-blocking advisory lock on persistent lock file descriptor-relative
            open_flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                self._lock_fd = os.open(self._lock_filename, open_flags, 0o600, dir_fd=self._parent_dir_fd)
            except OSError as exc:
                raise SignerSocketServerError(
                    f"ERR_CANNOT_OPEN_LOCK_FILE: cannot open '{self._lock_filename}' in anchored parent directory: {exc}",
                    code="ERR_CANNOT_OPEN_LOCK_FILE",
                ) from exc

            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise SignerSocketServerError(
                    f"ERR_SOCKET_LOCK_HELD: socket lock '{self._lock_path}' is held by another process",
                    code="ERR_SOCKET_LOCK_HELD",
                ) from exc

            # 2. While holding singleton lock, clean up any existing stale socket node descriptor-relative via tombstone isolation
            stale_tombstone = f".stale_tombstone_{os.getpid()}_{uuid.uuid4().hex}"
            try:
                os.rename(self._leaf_filename, stale_tombstone, src_dir_fd=self._parent_dir_fd, dst_dir_fd=self._parent_dir_fd)
                if self._in_stale_tombstone_hook is not None:
                    self._in_stale_tombstone_hook()
                try:
                    st_stale = os.stat(stale_tombstone, dir_fd=self._parent_dir_fd, follow_symlinks=False)
                    if not stat.S_ISSOCK(st_stale.st_mode):
                        # Not a socket: restore foreign entry without overwriting and fail closed (I18-R6-02)
                        restored, preserved, coll_err = _restore_tombstone_safely(
                            self._parent_dir_fd, stale_tombstone, self._leaf_filename, "stale non-socket entry"
                        )
                        cleanup_errors = self._cleanup_lock()
                        if coll_err is not None:
                            cleanup_errors.append(coll_err)
                        raise SignerSocketServerError(
                            f"ERR_NOT_A_SOCKET: existing entry '{self._leaf_filename}' is not a socket; will not overwrite foreign entry",
                            code="ERR_NOT_A_SOCKET",
                            cleanup_errors=cleanup_errors,
                        )

                    # Deterministic race hook: simulated replacement entry injected at post-tombstone-stat/pre-unlink boundary in stale cleanup (I18-R7-02)
                    if self._pre_stale_tombstone_unlink_hook is not None:
                        self._pre_stale_tombstone_unlink_hook()

                    # Re-verify stale tombstone identity immediately prior to unlink (I18-R7-02)
                    try:
                        st_pre_unlink = os.stat(stale_tombstone, dir_fd=self._parent_dir_fd, follow_symlinks=False)
                    except FileNotFoundError as exc:
                        cleanup_errors = self._cleanup_lock()
                        raise SignerSocketServerError(
                            f"ERR_SOCKET_DISAPPEARED: stale tombstone '{stale_tombstone}' disappeared prior to unlink: {exc}",
                            code="ERR_SOCKET_DISAPPEARED",
                            cleanup_errors=cleanup_errors,
                        ) from exc

                    if (st_pre_unlink.st_dev, st_pre_unlink.st_ino) != (st_stale.st_dev, st_stale.st_ino) or not stat.S_ISSOCK(st_pre_unlink.st_mode):
                        cleanup_errors = self._cleanup_lock()
                        raise SignerSocketServerError(
                            f"ERR_TOMBSTONE_IDENTITY_MISMATCH: stale tombstone '{stale_tombstone}' was replaced prior to unlink (expected ino={st_stale.st_ino}, found ino={st_pre_unlink.st_ino}); replacement preserved untouched",
                            code="ERR_TOMBSTONE_IDENTITY_MISMATCH",
                            cleanup_errors=cleanup_errors,
                        )

                    os.unlink(stale_tombstone, dir_fd=self._parent_dir_fd)
                except Exception as exc:
                    # If verification or unlinking failed, ensure restored safely without overwriting destination
                    if not (isinstance(exc, SignerSocketServerError) and exc.code in ("ERR_TOMBSTONE_IDENTITY_MISMATCH", "ERR_NOT_A_SOCKET")):
                        try:
                            _restore_tombstone_safely(
                                self._parent_dir_fd, stale_tombstone, self._leaf_filename, "stale verification error"
                            )
                        except Exception:
                            pass
                    raise
            except FileNotFoundError:
                pass
            except OSError as exc:
                if not isinstance(exc, SignerSocketServerError):
                    cleanup_errors = self._cleanup_lock()
                    raise SignerSocketServerError(
                        f"ERR_UNLINK_SOCKET_FAILED: cannot unlink '{self._leaf_filename}' in anchored parent directory: {exc}",
                        code="ERR_UNLINK_SOCKET_FAILED",
                        cleanup_errors=cleanup_errors,
                    ) from exc
                raise

            # 3. Create AF_UNIX stream socket, bind via anchored descriptor, and verify against anchored parent descriptor
            sock_node_fd: Optional[int] = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                proc_fd_dir = Path(f"/proc/self/fd/{self._parent_dir_fd}")
                if not proc_fd_dir.is_dir():
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_DESCRIPTOR_BIND_UNSUPPORTED: /proc/self/fd/{self._parent_dir_fd} is not accessible to anchor AF_UNIX bind",
                        code="ERR_SOCKET_DESCRIPTOR_BIND_UNSUPPORTED",
                    )
                bind_target = str(proc_fd_dir / self._leaf_filename)
                sock.bind(bind_target)
                sock.listen(128)
                sock.settimeout(0.5)

                # Open descriptor to the freshly bound socket node anchored in parent_dir_fd (I18-R6-01)
                open_flags = (
                    getattr(os, "O_PATH", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                try:
                    sock_node_fd = os.open(self._leaf_filename, open_flags, dir_fd=self._parent_dir_fd)
                except OSError as exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_OPEN_FAILED: cannot open descriptor to newly bound socket '{self._leaf_filename}': {exc}",
                        code="ERR_SOCKET_OPEN_FAILED",
                    ) from exc

                st_bound = os.fstat(sock_node_fd)
                if not stat.S_ISSOCK(st_bound.st_mode):
                    raise SignerSocketServerError("ERR_NOT_A_SOCKET", code="ERR_NOT_A_SOCKET")

                proven_bound_identity = (st_bound.st_dev, st_bound.st_ino)

                # Deterministic race hook: simulated replacement entry injected immediately after bind
                if self._post_bind_hook is not None:
                    self._post_bind_hook()

                # Cryptographic loopback ownership handshake:
                # Proves the directory entry at self._leaf_filename connects to this exact server socket,
                # preventing adoption of any rogue or colliding replacement entry created after bind (I18-R5-01).
                ownership_nonce = os.urandom(32)
                probe_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe_sock.settimeout(0.5)
                try:
                    probe_sock.connect(bind_target)
                    probe_conn, _ = sock.accept()
                    probe_sock.sendall(ownership_nonce)
                    received_nonce = probe_conn.recv(32)
                    probe_conn.close()
                    if received_nonce != ownership_nonce:
                        raise SignerSocketServerError(
                            f"ERR_SOCKET_OWNERSHIP_MISMATCH: loopback probe nonce mismatch on '{self._leaf_filename}'",
                            code="ERR_SOCKET_OWNERSHIP_MISMATCH",
                        )
                except (socket.timeout, OSError) as exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_OWNERSHIP_VERIFICATION_FAILED: failed loopback ownership verification on '{self._leaf_filename}': {exc}",
                        code="ERR_SOCKET_OWNERSHIP_VERIFICATION_FAILED",
                    ) from exc
                finally:
                    probe_sock.close()

                # Deterministic race hook: simulated replacement entry injected after successful probe handshake (I18-R6-01)
                if self._post_probe_hook is not None:
                    self._post_probe_hook()

                # Verify newly created socket exists in anchored parent descriptor
                try:
                    st_sock = os.stat(self._leaf_filename, dir_fd=self._parent_dir_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_ANCHOR_DISCREPANCY: bound socket '{self._leaf_filename}' not found in anchored parent directory descriptor",
                        code="ERR_SOCKET_ANCHOR_DISCREPANCY",
                    ) from exc

                if not stat.S_ISSOCK(st_sock.st_mode):
                    raise SignerSocketServerError("ERR_NOT_A_SOCKET", code="ERR_NOT_A_SOCKET")

                # Verify directory entry matches proven bound identity and has not been replaced (I18-R6-01)
                st_bound_check = os.fstat(sock_node_fd)
                if st_bound_check.st_nlink == 0 or (st_sock.st_dev, st_sock.st_ino) != proven_bound_identity:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_IDENTITY_MISMATCH: entry '{self._leaf_filename}' was replaced after probe (expected ino={proven_bound_identity[1]}, found ino={st_sock.st_ino}); foreign socket preserved",
                        code="ERR_SOCKET_IDENTITY_MISMATCH",
                    )

                # Deterministic race hook: simulated replacement entry injected immediately before chmod (I18-R7-01)
                if self._pre_chmod_hook is not None:
                    self._pre_chmod_hook()

                # Re-verify identity immediately prior to chmod without releasing pinned descriptor (I18-R7-01)
                try:
                    st_pre_chmod = os.stat(self._leaf_filename, dir_fd=self._parent_dir_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_DISAPPEARED: socket node '{self._leaf_filename}' disappeared before chmod: {exc}",
                        code="ERR_SOCKET_DISAPPEARED",
                    ) from exc

                if (st_pre_chmod.st_dev, st_pre_chmod.st_ino) != proven_bound_identity:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_IDENTITY_MISMATCH: entry '{self._leaf_filename}' was replaced before chmod (expected ino={proven_bound_identity[1]}, found ino={st_pre_chmod.st_ino}); foreign socket preserved",
                        code="ERR_SOCKET_IDENTITY_MISMATCH",
                    )

                # Apply mode 0660 directly to the proven bound inode via /proc/self/fd/sock_node_fd without releasing pinned descriptor (I18-R7-01)
                proc_sock_path = f"/proc/self/fd/{sock_node_fd}"
                try:
                    os.chmod(proc_sock_path, 0o660)
                except OSError as exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_CHMOD_FAILED: cannot set 0660 mode on bound socket inode via pinned descriptor: {exc}",
                        code="ERR_SOCKET_CHMOD_FAILED",
                    ) from exc

                # Verify mode and identity on both pinned descriptor and directory entry
                st_bound_after = os.fstat(sock_node_fd)
                st_sock_after = os.stat(self._leaf_filename, dir_fd=self._parent_dir_fd, follow_symlinks=False)
                if (st_sock_after.st_dev, st_sock_after.st_ino) != proven_bound_identity:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_IDENTITY_MISMATCH: entry '{self._leaf_filename}' identity changed during chmod",
                        code="ERR_SOCKET_IDENTITY_MISMATCH",
                    )
                if (st_bound_after.st_mode & 0o777) != 0o660 or (st_sock_after.st_mode & 0o777) != 0o660:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_INSECURE_PERMS: expected 0660, got {oct(st_sock_after.st_mode & 0o777)}",
                        code="ERR_SOCKET_INSECURE_PERMS",
                    )

                # Establish verified ownership of server-created socket entry (I18-R4-01, I18-R5-01, I18-R6-01, I18-R7-01, I18-R8-01, I18-R9-01)
                self._bound_socket_identity = proven_bound_identity
                fd_to_close = sock_node_fd
                sock_node_fd = None
                try:
                    os.close(fd_to_close)
                except OSError as close_exc:
                    raise SignerSocketServerError(
                        f"ERR_SOCKET_NODE_FD_CLOSE_FAILED: failed to close socket node descriptor {fd_to_close}: {close_exc}",
                        code="ERR_SOCKET_NODE_FD_CLOSE_FAILED",
                    ) from close_exc

                sock.settimeout(0.2)
                self._server_sock = sock
                self._running = True
            except Exception as exc:
                startup_cleanup_errors: List[Exception] = []
                if sock_node_fd is not None:
                    fd_to_close = sock_node_fd
                    sock_node_fd = None
                    try:
                        os.close(fd_to_close)
                    except OSError as close_exc:
                        startup_cleanup_errors.append(
                            SignerSocketServerError(
                                f"ERR_SOCKET_NODE_FD_CLOSE_FAILED: failed to close socket node descriptor {fd_to_close} during cleanup: {close_exc}",
                                code="ERR_SOCKET_NODE_FD_CLOSE_FAILED",
                            )
                        )
                if sock is not None and self._server_sock is None:
                    try:
                        sock.close()
                    except Exception as sock_exc:
                        startup_cleanup_errors.append(sock_exc)
                cleanup_errors = self._cleanup_all()
                all_cleanup_errors = startup_cleanup_errors + cleanup_errors
                if all_cleanup_errors:
                    err_msgs = [f"{type(e).__name__}: {e}" for e in all_cleanup_errors]
                    raise SignerSocketServerError(
                        f"ERR_START_AND_CLEANUP_FAILED: primary: {exc}; cleanup: {'; '.join(err_msgs)}",
                        code="ERR_START_AND_CLEANUP_FAILED",
                        details={
                            "primary_error": str(exc),
                            "cleanup_errors": err_msgs,
                        },
                        cleanup_errors=all_cleanup_errors,
                    ) from exc
                if isinstance(exc, SignerSocketServerError):
                    raise
                raise SignerSocketServerError(f"ERR_SOCKET_BIND_FAILED: {exc}", code="ERR_SOCKET_BIND_FAILED") from exc

            # 4. Start in-process accept loop thread
            self._thread = threading.Thread(target=self._serve_loop, name="LocalTestSignerSocketServer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stops the server, waits for thread termination, and cleans up temporary socket and lock.

        Preserves persistent lock file to avoid lock-inode split.
        Raises SignerSocketServerError if any cleanup operation fails.
        """
        with self._lock:
            if not self._running:
                if self._server_sock is None and self._lock_fd is None:
                    return
            self._running = False

        cleanup_errors: List[Exception] = []

        sock = self._server_sock
        self._server_sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception as exc:
                cleanup_errors.append(exc)
                self._failed_server_sock = sock

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        # Unlink socket node only after verifying entry ownership (I18-R4-01)
        socket_cleanup_errors = self._unlink_owned_socket_node()
        cleanup_errors.extend(socket_cleanup_errors)

        lock_errors = self._cleanup_lock()
        cleanup_errors.extend(lock_errors)

        if cleanup_errors:
            err_msgs = [f"{type(e).__name__}: {e}" for e in cleanup_errors]
            raise SignerSocketServerError(
                f"ERR_CLEANUP_FAILED: server cleanup failed: {'; '.join(err_msgs)}",
                code="ERR_CLEANUP_FAILED",
                details={"errors": err_msgs},
                cleanup_errors=cleanup_errors,
            )

    def close(self) -> None:
        """Releases all resources including pinned parent directory descriptor."""
        try:
            self.stop()
        finally:
            if self._parent_dir_fd is not None:
                fd = self._parent_dir_fd
                self._parent_dir_fd = None
                try:
                    os.close(fd)
                except OSError:
                    pass

    def __enter__(self) -> LocalTestSignerSocketServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            self.close()
        except SignerSocketServerError as cleanup_exc:
            if exc_val is not None:
                raise cleanup_exc from exc_val
            raise

    def __del__(self) -> None:
        if getattr(self, "_parent_dir_fd", None) is not None:
            try:
                os.close(self._parent_dir_fd)
            except OSError:
                pass

    def _unlink_owned_socket_node(self) -> List[Exception]:
        """Safely unlinks the server-created socket node using tombstone isolation (I18-R5-01, I18-R7-02, I18-R8-01).

        Narrows the check-then-act race window to protect foreign replacement entries:
        1. Renames self._leaf_filename to a unique per-server tombstone name in the anchored directory.
        2. Validates that the moved entry matches self._bound_socket_identity and is a socket.
        3. If matching: safely unlinks the isolated tombstone node (subject to the documented local-test
           limitation that Linux VFS lacks atomic compare-and-unlink).
        4. If non-matching (foreign replacement): restores the tombstone back to self._leaf_filename
           (or preserves both under .preserved_foreign_* on collision), preserving the foreign entry.
        """
        errors: List[Exception] = []
        if self._bound_socket_identity is None:
            # Server never established ownership of a verified bound socket node (e.g. bind failed)
            return errors

        if self._parent_dir_fd is None:
            errors.append(
                SignerSocketServerError(
                    "ERR_SOCKET_CLEANUP_NO_DESCRIPTOR: cannot unlink socket without anchored parent directory descriptor",
                    code="ERR_SOCKET_CLEANUP_NO_DESCRIPTOR",
                )
            )
            return errors

        expected_dev, expected_ino = self._bound_socket_identity
        self._bound_socket_identity = None

        # Deterministic race hook: simulated replacement entry injected immediately at pre-unlink boundary
        if self._pre_unlink_hook is not None:
            self._pre_unlink_hook()

        # Atomic tombstone rename isolation
        tombstone = f".tombstone_{os.getpid()}_{uuid.uuid4().hex}"
        try:
            os.rename(self._leaf_filename, tombstone, src_dir_fd=self._parent_dir_fd, dst_dir_fd=self._parent_dir_fd)
        except FileNotFoundError as exc:
            errors.append(
                SignerSocketServerError(
                    f"ERR_SOCKET_DISAPPEARED: bound socket '{self._leaf_filename}' disappeared prior to cleanup: {exc}",
                    code="ERR_SOCKET_DISAPPEARED",
                )
            )
            return errors
        except OSError as exc:
            errors.append(exc)
            return errors

        # Deterministic race hook: simulated collision entry injected while basename is vacant under tombstone (I18-R6-02)
        if self._in_tombstone_hook is not None:
            self._in_tombstone_hook()

        # Inspect isolated tombstone entry
        try:
            st = os.stat(tombstone, dir_fd=self._parent_dir_fd, follow_symlinks=False)
        except Exception as exc:
            restored, preserved, coll_err = _restore_tombstone_safely(
                self._parent_dir_fd, tombstone, self._leaf_filename, "inspection failed"
            )
            if coll_err is not None:
                errors.append(coll_err)
            errors.append(exc)
            return errors

        if not stat.S_ISSOCK(st.st_mode):
            # Foreign non-socket entry! Atomically restore back to original name without overwriting destination (I18-R6-02)!
            restored, preserved, coll_err = _restore_tombstone_safely(
                self._parent_dir_fd, tombstone, self._leaf_filename, "foreign non-socket entry"
            )
            if coll_err is not None:
                errors.append(coll_err)
            errors.append(
                SignerSocketServerError(
                    f"ERR_SOCKET_REPLACED_NON_SOCKET: entry '{self._leaf_filename}' is not a socket (mode={oct(st.st_mode)}); foreign entry preserved",
                    code="ERR_SOCKET_REPLACED_NON_SOCKET",
                )
            )
            return errors

        if (st.st_dev, st.st_ino) != (expected_dev, expected_ino):
            # Foreign replacement socket! Atomically restore back to original name without overwriting destination (I18-R6-02)!
            restored, preserved, coll_err = _restore_tombstone_safely(
                self._parent_dir_fd, tombstone, self._leaf_filename, "foreign replacement socket"
            )
            if coll_err is not None:
                errors.append(coll_err)
            errors.append(
                SignerSocketServerError(
                    f"ERR_SOCKET_IDENTITY_MISMATCH: entry '{self._leaf_filename}' was replaced (expected ino={expected_ino}, found ino={st.st_ino}); replacement preserved",
                    code="ERR_SOCKET_IDENTITY_MISMATCH",
                )
            )
            return errors

        # Deterministic race hook: simulated replacement entry injected immediately at post-tombstone-stat/pre-unlink boundary (I18-R7-02)
        if self._pre_tombstone_unlink_hook is not None:
            self._pre_tombstone_unlink_hook()

        # Re-verify tombstone identity immediately prior to unlink (I18-R7-02)
        try:
            st_pre_unlink = os.stat(tombstone, dir_fd=self._parent_dir_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            errors.append(
                SignerSocketServerError(
                    f"ERR_SOCKET_DISAPPEARED: tombstone '{tombstone}' disappeared prior to unlink: {exc}",
                    code="ERR_SOCKET_DISAPPEARED",
                )
            )
            return errors
        except OSError as exc:
            errors.append(exc)
            return errors

        if (st_pre_unlink.st_dev, st_pre_unlink.st_ino) != (expected_dev, expected_ino) or not stat.S_ISSOCK(st_pre_unlink.st_mode):
            # Tombstone entry was replaced at pre-unlink boundary!
            # DO NOT UNLINK! Preserve replacement completely untouched!
            errors.append(
                SignerSocketServerError(
                    f"ERR_TOMBSTONE_IDENTITY_MISMATCH: tombstone '{tombstone}' was replaced prior to unlink (expected ino={expected_ino}, found ino={st_pre_unlink.st_ino}); replacement preserved untouched",
                    code="ERR_TOMBSTONE_IDENTITY_MISMATCH",
                )
            )
            return errors

        # Verified owner! Safe to unlink isolated tombstone
        try:
            os.unlink(tombstone, dir_fd=self._parent_dir_fd)
        except Exception as exc:
            errors.append(exc)

        return errors

    def _cleanup_lock(self) -> List[Exception]:
        errors: List[Exception] = []
        fd = self._lock_fd
        self._lock_fd = None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception as exc:
                errors.append(exc)
            try:
                os.close(fd)
            except Exception as exc:
                errors.append(exc)
                self._failed_lock_fd = fd
        return errors

    def _cleanup_all(self) -> List[Exception]:
        errors: List[Exception] = []
        sock = self._server_sock
        self._server_sock = None
        if sock is not None:
            try:
                sock.close()
            except Exception as exc:
                errors.append(exc)
                self._failed_server_sock = sock

        # Unlink socket node only after verifying entry ownership (I18-R4-01)
        socket_errors = self._unlink_owned_socket_node()
        errors.extend(socket_errors)

        lock_errors = self._cleanup_lock()
        errors.extend(lock_errors)
        return errors

    def _serve_loop(self) -> None:
        while self._running:
            try:
                if self._server_sock is None:
                    break
                client_sock, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            # Handle client synchronously in worker or thread
            threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True).start()

    def _handle_client(self, client_sock: socket.socket) -> None:
        try:
            client_sock.settimeout(0.5)

            # 1. Peer authentication using SO_PEERCRED or injected provider
            peer_uid: Optional[int] = None
            if self._peer_credential_provider is not None:
                try:
                    peer_uid = self._peer_credential_provider(client_sock)
                except Exception as exc:
                    self._send_framed_error(client_sock, None, "ERR_PEER_AUTH_FAILED", f"Peer auth provider failed: {exc}")
                    return
            else:
                try:
                    cred_bytes = client_sock.getsockopt(
                        socket.SOL_SOCKET,
                        SO_PEERCRED_CONST,
                        struct.calcsize("3i"),
                    )
                    _, uid, _ = struct.unpack("3i", cred_bytes)
                    peer_uid = uid
                except Exception as exc:
                    self._send_framed_error(client_sock, None, "ERR_PEER_CRED_FAILED", f"SO_PEERCRED failed: {exc}")
                    return

            if peer_uid is None or peer_uid not in self._allowed_peer_uids:
                self._send_framed_error(
                    client_sock,
                    None,
                    "ERR_PEER_AUTH_DENIED",
                    f"Peer UID {peer_uid} is not authorized (allowed: {sorted(self._allowed_peer_uids)})",
                )
                return

            # 2. Read 4-byte big-endian frame length
            header = self._recv_exact(client_sock, 4)
            if header is None:
                return
            if len(header) < 4:
                self._send_framed_error(client_sock, None, "ERR_TRUNCATED_FRAME_HEADER", "Truncated frame header")
                return

            (msg_len,) = struct.unpack(">I", header)
            if msg_len == 0:
                self._send_framed_error(client_sock, None, "ERR_EMPTY_FRAME", "Frame payload length is 0")
                return
            if msg_len > self._max_frame_bytes:
                self._send_framed_error(
                    client_sock,
                    None,
                    "ERR_FRAME_TOO_LARGE",
                    f"Frame size {msg_len} exceeds maximum allowed {self._max_frame_bytes}",
                )
                return

            # 3. Read exact payload
            payload_bytes = self._recv_exact(client_sock, msg_len)
            if payload_bytes is None or len(payload_bytes) != msg_len:
                self._send_framed_error(client_sock, None, "ERR_TRUNCATED_PAYLOAD", "Payload truncated before declared length")
                return

            # 4. Parse strict canonical JSON with duplicate-key rejection
            try:
                req_data = parse_strict_canonical_json(payload_bytes, max_bytes=self._max_frame_bytes)
            except Exception as exc:
                self._send_framed_error(client_sock, None, "ERR_MALFORMED_JSON", f"Malformed JSON: {exc}")
                return

            # 5. Validate outer request schema
            req_keys = frozenset(req_data.keys())
            expected_keys = frozenset({"version", "request_id", "method", "params"})
            if req_keys != expected_keys:
                missing = sorted(expected_keys - req_keys)
                extra = sorted(req_keys - expected_keys)
                self._send_framed_error(
                    client_sock,
                    req_data.get("request_id"),
                    "ERR_SCHEMA_MISMATCH",
                    f"Outer request schema mismatch: missing={missing}, extra={extra}",
                )
                return

            req_version = req_data["version"]
            if type(req_version) is not int or req_version != 1:
                self._send_framed_error(
                    client_sock,
                    req_data.get("request_id"),
                    "ERR_UNSUPPORTED_VERSION",
                    f"Protocol version must be 1, got {req_version!r}",
                )
                return

            req_id = req_data["request_id"]
            if not isinstance(req_id, str) or not req_id.strip():
                self._send_framed_error(client_sock, None, "ERR_INVALID_REQUEST_ID", "request_id must be non-empty string")
                return

            with self._lock:
                if req_id in self._seen_request_ids:
                    self._send_framed_error(
                        client_sock,
                        req_id,
                        "ERR_REPLAYED_REQUEST_ID",
                        f"request_id '{req_id}' has already been processed",
                    )
                    return
                self._seen_request_ids.add(req_id)

            method = req_data["method"]
            params = req_data["params"]
            if not isinstance(params, dict):
                self._send_framed_error(client_sock, req_id, "ERR_INVALID_PARAMS", "params must be a dictionary")
                return

            # Prohibit passing private keys, HMAC keys, secret bytes, or capabilities in params
            def _scan_forbidden_secrets(obj: Any) -> Optional[str]:
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        k_lower = str(k).lower()
                        for pattern in ("private_key", "secret", "signing_key", "signing_capability", "key_handle", "auth_hmac"):
                            if pattern in k_lower:
                                return str(k)
                        nested = _scan_forbidden_secrets(v)
                        if nested:
                            return nested
                elif isinstance(obj, (list, tuple)):
                    for item in obj:
                        nested = _scan_forbidden_secrets(item)
                        if nested:
                            return nested
                return None

            bad_param_key = _scan_forbidden_secrets(params)
            if bad_param_key:
                self._send_framed_error(
                    client_sock,
                    req_id,
                    "ERR_KEY_MATERIAL_PROHIBITED",
                    f"Passing key material or private secrets is strictly prohibited (found key '{bad_param_key}')",
                )
                return

            # 5b. Validate exact method-specific parameter schema, rejecting unknown fields & claim overrides
            is_valid_params, p_code, p_msg = _validate_method_params(method, params)
            if not is_valid_params:
                self._send_framed_error(
                    client_sock,
                    req_id,
                    p_code or "ERR_INVALID_PARAMS",
                    p_msg or "Invalid method parameters",
                )
                return

            # 6. Route to accepted/injected method handler
            if method == "request_challenge":
                if self._challenge_handler is None:
                    self._send_framed_error(client_sock, req_id, "ERR_METHOD_NOT_CONFIGURED", "request_challenge not configured")
                    return
                try:
                    result = self._challenge_handler(params)
                    self._send_framed_success(client_sock, req_id, result)
                except Exception as exc:
                    self._send_framed_error(client_sock, req_id, getattr(exc, "code", "ERR_CHALLENGE_FAILED"), str(exc))

            elif method == "submit_operator_consent":
                if self._consent_handler is None:
                    self._send_framed_error(client_sock, req_id, "ERR_METHOD_NOT_CONFIGURED", "submit_operator_consent not configured")
                    return
                try:
                    result = self._consent_handler(params)
                    self._send_framed_success(client_sock, req_id, result)
                except Exception as exc:
                    self._send_framed_error(client_sock, req_id, getattr(exc, "code", "ERR_CONSENT_FAILED"), str(exc))

            elif method == "verify_authorization_oracle":
                if self._oracle_handler is None:
                    self._send_framed_error(client_sock, req_id, "ERR_METHOD_NOT_CONFIGURED", "verify_authorization_oracle not configured")
                    return
                try:
                    result = self._oracle_handler(params)
                    bad_leak = _scan_forbidden_secrets(result)
                    if bad_leak:
                        self._send_framed_error(client_sock, req_id, "ERR_INTERNAL_LEAK_PREVENTED", "Oracle response contained secret key material")
                        return
                    self._send_framed_success(client_sock, req_id, result)
                except Exception as exc:
                    self._send_framed_error(client_sock, req_id, getattr(exc, "code", "ERR_ORACLE_FAILED"), str(exc))

            else:
                self._send_framed_error(
                    client_sock,
                    req_id,
                    "ERR_UNSUPPORTED_METHOD",
                    f"Unsupported method '{method}'. Supported methods: request_challenge, submit_operator_consent, verify_authorization_oracle",
                )

        finally:
            try:
                client_sock.close()
            except OSError:
                pass

    def _recv_exact(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < num_bytes:
            try:
                chunk = sock.recv(num_bytes - len(buf))
            except socket.timeout:
                return bytes(buf) if buf else None
            except OSError:
                return None
            if not chunk:
                return bytes(buf) if buf else None
            buf.extend(chunk)
        return bytes(buf)

    def _send_framed_success(self, sock: socket.socket, req_id: Optional[str], result: Dict[str, Any]) -> None:
        resp = {
            "version": 1,
            "request_id": req_id or "",
            "status": "OK",
            "result": result,
        }
        payload = canonical_json_bytes(resp)
        frame = struct.pack(">I", len(payload)) + payload
        try:
            sock.sendall(frame)
        except OSError:
            pass

    def _send_framed_error(self, sock: socket.socket, req_id: Optional[str], error_code: str, message: str) -> None:
        resp = {
            "version": 1,
            "request_id": req_id or "",
            "status": "ERROR",
            "error_code": error_code,
            "message": message,
        }
        payload = canonical_json_bytes(resp)
        frame = struct.pack(">I", len(payload)) + payload
        try:
            sock.sendall(frame)
        except OSError:
            pass


class LocalTestSignerSocketClient:
    """Client helper for communicating with LocalTestSignerSocketServer over length-prefixed AF_UNIX."""

    def __init__(self, socket_path: Union[str, Path], timeout_seconds: float = 5.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout_seconds

    def send_request(
        self,
        method: str,
        params: Dict[str, Any],
        *,
        request_id: Optional[str] = None,
        version: int = 1,
        custom_envelope: Optional[Dict[str, Any]] = None,
        raw_payload_override: Optional[bytes] = None,
        length_prefix_override: Optional[int] = None,
    ) -> Dict[str, Any]:
        req_id = request_id or str(uuid.uuid4())
        if custom_envelope is not None:
            envelope = custom_envelope
        else:
            envelope = {
                "version": version,
                "request_id": req_id,
                "method": method,
                "params": params,
            }

        payload = raw_payload_override if raw_payload_override is not None else canonical_json_bytes(envelope)
        prefix_len = length_prefix_override if length_prefix_override is not None else len(payload)
        frame = struct.pack(">I", prefix_len) + payload

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout_seconds)
        try:
            sock.connect(self.socket_path)
            sock.sendall(frame)

            # Read 4-byte response header
            hdr = bytearray()
            while len(hdr) < 4:
                chunk = sock.recv(4 - len(hdr))
                if not chunk:
                    break
                hdr.extend(chunk)

            if len(hdr) < 4:
                raise SignerSocketServerError(
                    "ERR_CLIENT_DISCONNECTED: server closed connection without response header",
                    code="ERR_CLIENT_DISCONNECTED",
                )

            (resp_len,) = struct.unpack(">I", hdr)
            body = bytearray()
            while len(body) < resp_len:
                chunk = sock.recv(resp_len - len(body))
                if not chunk:
                    break
                body.extend(chunk)

            if len(body) < resp_len:
                raise SignerSocketServerError(
                    "ERR_CLIENT_TRUNCATED: server returned truncated response body",
                    code="ERR_CLIENT_TRUNCATED",
                )

            return parse_strict_canonical_json(bytes(body))
        finally:
            sock.close()




