"""
Live Canary Dispatch Service Contract (Gate 6 DEP-03).

Architecture Basis:
- docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md (§4.1-4.2, §5.1-5.2.3, §7.4)
- Workorder: .bridge/task.md (G07-07B7-GATE6-DEP03-R2)
- Remediation: docs/architecture/07B7-GATE6-DEP03-R2-code-review.md (DEP03-R2-01 to DEP03-R2-04)

Boundary:
- IMPLEMENTATION_AND_LOCAL_TEMP_TESTS_ONLY
- NO_LIVE_DISPATCH; NO_RESOLVE_IMPORT_OR_CONNECTION
- NO_HOST_PROVISIONING; NO_SYSTEMD_LIFECYCLE
- NO_PRODUCTION_KEY_OR_STATE_ACCESS; NO_NETWORK; NO_LIVE_OPERATIONS
- READY_TO_EXECUTE_FALSE

Implements `LiveCanaryDispatchService`, enforcing:
1. Pinned Ed25519 Oracle public key and pinned target identity (DEP03-R2-01).
2. Strict nominal DTO verification and non-fallback plan/action digest recomputation (DEP03-R2-02).
3. Authorization digest and proposal revision response binding checks (DEP03-R2-03).
4. Strict nominal adapter result validation and typed pre-mutation clean-abort proof (DEP03-R2-04).
5. 11-point acceptance invariant verification gate.
6. Communication with Signer Oracle over AF_UNIX socket using 4-byte framing.
7. Pre-mutation durability ordering via descriptor-anchored `DurableDispatchJournal`.
8. Oracle terminal-state notification on COMPLETED, ABORTED, and IN_DOUBT branches.
9. Fail-closed error handling and target locking on ambiguous outcomes.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import socket
import struct
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.core.contracts.oracle_ipc_contracts import (
    DEFAULT_SOCKET_TIMEOUT,
    OracleTerminalNotificationRequest,
    OracleTerminalNotificationResponse,
    OracleVerificationRequest,
    OracleVerificationResponse,
    VALID_TERMINAL_STATES,
    decode_framed_message,
    encode_framed_message,
    parse_strict_json,
)
from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_bytes_for_auth_hmac,
    parse_iso_utc_timestamp,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction,
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)
from src.services.durable_dispatch_journal import (
    DispatchJournalError,
    DispatchJournalInDoubtError,
    DispatchJournalLockError,
    DispatchJournalPoisonError,
    DispatchJournalSecurityError,
    DurableDispatchJournal,
)
from src.services.trial_authorization_signer_service import (
    compute_canonical_action_payload_digest_for_admission,
)

# Concrete backend Ed25519 key type in cryptography
_CANONICAL_ED25519_KEY_TYPE = type(Ed25519PrivateKey.generate().public_key())


# ------------------------------------------------------------------------------
# Authoritative Canary Target & Incident Blacklist Constants (Plan 07B7 §5.1 & §7.1)
# ------------------------------------------------------------------------------
CANARY_TARGET_PROJECT_UID = "4fd4272d-f0bc-445c-9490-5723cd6831c2"
CANARY_TARGET_TIMELINE_UID = "ba6a6775-8ec4-41a4-bf2f-704de902e68d"
CANARY_TARGET_PROJECT_NAME = "RF_TRIAL_AEC8F0A7A365D5F5"
CANARY_TARGET_TIMELINE_NAME = "RF_TRIAL_TL_AEC8F0A7A365D5F5"

CANARY_SOURCE_MEDIA_NAME = "canary_test_1080p24.mp4"
CANARY_SOURCE_MEDIA_SHA256 = "7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916"
CANARY_SOURCE_DURATION_FRAMES = 120
CANARY_TARGET_TRACK_TYPE = "video"
CANARY_TARGET_TRACK_INDEX = 1
CANARY_AUTHORIZED_RECORD_FRAME = 86400
CANARY_FPS_NUMERATOR = 24
CANARY_FPS_DENOMINATOR = 1

# Incident Blacklist
INCIDENT_TARGET_PROJECT_UID = "0ee71404-6405-47ca-89e1-aaeaac142398"
INCIDENT_TARGET_TIMELINE_UID = "e4e5a587-661d-4854-8f37-8a279d3f2ee1"
INCIDENT_TARGET_PROJECT_NAMES: FrozenSet[str] = frozenset({
    "RF_TRIAL_9A56C835B97208ED",
    "RF_TRIAL_CANARY_P01",
})
INCIDENT_TARGET_TIMELINE_NAMES: FrozenSet[str] = frozenset({
    "RF_TRIAL_TL_9A56C835B97208ED",
    "RF_TRIAL_TL_CANARY_T01",
})


# ------------------------------------------------------------------------------
# Clean Abort Proof Contract & Capability (DEP03-R2-04, DEP03-R3-01)
# ------------------------------------------------------------------------------
class PreMutationAbortCapability:
    """
    Unforgeable dispatcher-issued single-use capability that can only be issued
    by LiveCanaryDispatchService and consumed by the adapter strictly BEFORE
    crossing the mutation boundary (DEP03-R3-01).
    """

    def __init__(
        self,
        *,
        secret_hmac_key: bytes,
        execution_id: str,
        target_project_uid: str,
        target_timeline_uid: str,
        action_idempotency_hash: str,
    ) -> None:
        self._secret_hmac_key = secret_hmac_key
        self._execution_id = execution_id
        self._target_project_uid = target_project_uid
        self._target_timeline_uid = target_timeline_uid
        self._action_idempotency_hash = action_idempotency_hash
        self._nonce = secrets.token_hex(32)
        self._consumed = False
        self._mutation_boundary_crossed = False

    @property
    def mutation_boundary_crossed(self) -> bool:
        return self._mutation_boundary_crossed

    def enter_mutation_boundary(self) -> None:
        """Called when entering the mutation phase, permanently invalidating capability."""
        self._mutation_boundary_crossed = True

    def create_clean_abort_error(self, reason: str) -> PreMutationCleanAbortError:
        """
        Produces a proven clean abort error if and only if called strictly BEFORE
        the mutation boundary has been crossed.
        """
        if self._mutation_boundary_crossed:
            raise RuntimeError("Cannot create clean abort proof after crossing the mutation boundary")
        if self._consumed:
            raise RuntimeError("Capability already consumed")
        self._consumed = True

        claims = f"{self._execution_id}:{self._target_project_uid}:{self._target_timeline_uid}:{self._action_idempotency_hash}:{self._nonce}:{reason}"
        sig = hmac.new(self._secret_hmac_key, claims.encode("utf-8"), hashlib.sha256).hexdigest().lower()

        proof = PreMutationCleanAbortProof(
            execution_id=self._execution_id,
            target_project_uid=self._target_project_uid,
            target_timeline_uid=self._target_timeline_uid,
            action_idempotency_hash=self._action_idempotency_hash,
            reason=reason,
            nonce=self._nonce,
            provenance_signature=sig,
            aborted_at_iso=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return PreMutationCleanAbortError(proof)


class PreMutationCleanAbortProof:
    """
    Cryptographically verifiable proof of zero mutation generated by a trusted
    PreMutationAbortCapability before crossing the mutation boundary (DEP03-R3-01).
    """

    def __init__(
        self,
        *,
        execution_id: str,
        target_project_uid: str,
        target_timeline_uid: str,
        action_idempotency_hash: str,
        reason: str,
        nonce: str,
        provenance_signature: str,
        aborted_at_iso: str,
    ) -> None:
        self.execution_id = execution_id
        self.target_project_uid = target_project_uid
        self.target_timeline_uid = target_timeline_uid
        self.action_idempotency_hash = action_idempotency_hash
        self.reason = reason
        self.nonce = nonce
        self.provenance_signature = provenance_signature
        self.aborted_at_iso = aborted_at_iso

    def verify_provenance(
        self,
        *,
        secret_hmac_key: bytes,
        execution_id: str,
        project_uid: str,
        timeline_uid: str,
        action_hash: str,
    ) -> bool:
        if not (
            self.execution_id == execution_id
            and self.target_project_uid == project_uid
            and self.target_timeline_uid == timeline_uid
            and self.action_idempotency_hash == action_hash
        ):
            return False
        claims = f"{self.execution_id}:{self.target_project_uid}:{self.target_timeline_uid}:{self.action_idempotency_hash}:{self.nonce}:{self.reason}"
        expected_sig = hmac.new(secret_hmac_key, claims.encode("utf-8"), hashlib.sha256).hexdigest().lower()
        return hmac.compare_digest(self.provenance_signature, expected_sig)


class PreMutationCleanAbortError(Exception):
    """
    Exception indicating a proven clean abort prior to mutation boundary.
    Carries explicit PreMutationCleanAbortProof.
    """

    def __init__(self, proof: PreMutationCleanAbortProof) -> None:
        super().__init__(proof.reason)
        self.proof = proof


# ------------------------------------------------------------------------------
# Dispatch Exception Hierarchy
# ------------------------------------------------------------------------------
class LiveDispatchError(Exception):
    """Base exception for Live Canary Dispatch errors."""


class LiveDispatchSecurityError(LiveDispatchError):
    """Raised when key configuration, cryptographic check, or boundary constraint fails."""


class LiveDispatchScopeViolationError(LiveDispatchError):
    """Raised when action geometry, track, media digest, or plan violates strict canary scope."""


class LiveDispatchTargetDeniedError(LiveDispatchError):
    """Raised when target does not match approved canary or hits the incident blacklist."""


class LiveDispatchOracleVerificationError(LiveDispatchError):
    """Raised when Signer Oracle verification fails or returns invalid/mismatched claims."""


class LiveDispatchDuplicateError(LiveDispatchError):
    """Raised when execution ID has already been committed or target is active/exhausted."""


class LiveDispatchInDoubtError(LiveDispatchError):
    """Raised when mutation outcome is ambiguous and target lock must be retained."""


class LiveDispatchAdapterError(LiveDispatchError):
    """Raised when adapter execution fails or produces an invalid result."""


class LiveDispatchTerminalNotificationError(LiveDispatchError):
    """Oracle terminal-state notification failed or was ambiguous."""


# ------------------------------------------------------------------------------
# LiveCanaryDispatchService
# ------------------------------------------------------------------------------
class LiveCanaryDispatchService:
    """
    Supervised Live Canary Dispatcher.
    Enforces the complete 11-point acceptance invariant gate via AF_UNIX Oracle socket,
    verifies exact single-action geometry, establishes durable pre-mutation state
    via DurableDispatchJournal, dispatches via adapter, and notifies Signer Oracle
    of terminal state with fail-closed ambiguous outcome handling.
    """

    def __init__(
        self,
        *,
        oracle_socket_path: Union[str, Path],
        oracle_public_key: Ed25519PublicKey,
        journal: DurableDispatchJournal,
        adapter: Any,
        expected_target_project_uid: Optional[str] = None,
        expected_target_timeline_uid: Optional[str] = None,
        socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT,
    ) -> None:
        # DEP03-R2-01: Reject custom verifier objects/subclasses; require exact backend Ed25519PublicKey
        if type(oracle_public_key) is not _CANONICAL_ED25519_KEY_TYPE:
            raise LiveDispatchSecurityError(
                f"ERR_INVALID_ORACLE_KEY_TYPE: oracle_public_key must be exact backend Ed25519PublicKey, got {type(oracle_public_key)}"
            )

        # Validate raw key encoding (must be exactly 32 bytes) and normalize to fresh backend key
        try:
            raw_key_bytes = oracle_public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if len(raw_key_bytes) != 32:
                raise ValueError("Raw public key length is not 32 bytes")
            canonical_key = Ed25519PublicKey.from_public_bytes(raw_key_bytes)
        except Exception as exc:
            raise LiveDispatchSecurityError(f"ERR_INVALID_ORACLE_KEY_ENCODING: {exc}") from exc

        # DEP03-R2-01: Remove caller UID overrides or reject any value other than DEP-01 constants
        if expected_target_project_uid is not None and expected_target_project_uid != CANARY_TARGET_PROJECT_UID:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_OVERRIDE_DENIED: project UID override {expected_target_project_uid} != {CANARY_TARGET_PROJECT_UID}"
            )
        if expected_target_timeline_uid is not None and expected_target_timeline_uid != CANARY_TARGET_TIMELINE_UID:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_OVERRIDE_DENIED: timeline UID override {expected_target_timeline_uid} != {CANARY_TARGET_TIMELINE_UID}"
            )

        self._oracle_socket_path = Path(oracle_socket_path)
        self._oracle_public_key = canonical_key
        self._journal = journal
        self._adapter = adapter
        self._expected_target_project_uid = CANARY_TARGET_PROJECT_UID
        self._expected_target_timeline_uid = CANARY_TARGET_TIMELINE_UID
        self._expected_target_project_name = CANARY_TARGET_PROJECT_NAME
        self._expected_target_timeline_name = CANARY_TARGET_TIMELINE_NAME
        self._socket_timeout_seconds = socket_timeout_seconds
        self._lock = threading.Lock()

    @property
    def journal(self) -> DurableDispatchJournal:
        return self._journal

    @property
    def oracle_public_key(self) -> Ed25519PublicKey:
        return self._oracle_public_key

    # --------------------------------------------------------------------------
    # Oracle Socket IPC Helpers
    # --------------------------------------------------------------------------
    def _send_oracle_request(self, payload_bytes: bytes) -> bytes:
        """Sends length-prefixed request to AF_UNIX Oracle socket and receives response."""
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._socket_timeout_seconds)
        try:
            sock.connect(str(self._oracle_socket_path))
            sock.sendall(encode_framed_message(payload_bytes))
            return decode_framed_message(sock)
        except (socket.timeout, OSError, EOFError) as exc:
            raise LiveDispatchOracleVerificationError(
                f"ERR_ORACLE_IPC_FAILED: Socket communication failure: {exc}"
            ) from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _verify_oracle_verification_response(
        self,
        *,
        req: OracleVerificationRequest,
        auth: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
        action: PlannedTimelineAction,
        computed_action_digest: str,
        response_bytes: bytes,
    ) -> OracleVerificationResponse:
        """Enforces the complete 11-point acceptance invariant verification gate (§3.2, DEP-01 §4.2)."""
        resp = OracleVerificationResponse.from_json_bytes(response_bytes)

        # Invariant 1: bool(response.oracle_signature) is True and len == 128 lowercase hex
        sig_str = resp.oracle_signature
        if not sig_str or len(sig_str) != 128:
            raise LiveDispatchOracleVerificationError(
                "ERR_INVARIANT_1_FAILED: oracle_signature must be 128-char hex string"
            )
        try:
            sig_bytes = bytes.fromhex(sig_str)
        except ValueError as exc:
            raise LiveDispatchOracleVerificationError(
                "ERR_INVARIANT_1_FAILED: oracle_signature is not valid hex"
            ) from exc

        # Invariant 2: Valid Ed25519 signature over complete canonical RFC 8785 response payload
        claims_bytes = resp.canonical_claims_bytes()
        try:
            self._oracle_public_key.verify(sig_bytes, claims_bytes)
        except InvalidSignature as exc:
            raise LiveDispatchOracleVerificationError(
                "ERR_INVARIANT_2_FAILED: Ed25519 oracle signature verification failed"
            ) from exc

        # Invariant 3: response.request_id == req.request_id and response.request_nonce == req.request_nonce
        if resp.request_id != req.request_id:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_3_FAILED: request_id mismatch: {resp.request_id} != {req.request_id}"
            )
        if resp.request_nonce != req.request_nonce:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_3_FAILED: request_nonce mismatch: {resp.request_nonce} != {req.request_nonce}"
            )

        # Invariant 4: response.target_project_uid == auth.target_project_uid and target_timeline_uid matches
        if resp.target_project_uid != auth.target_project_uid:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_4_FAILED: target_project_uid mismatch: {resp.target_project_uid} != {auth.target_project_uid}"
            )
        if resp.target_timeline_uid != auth.target_timeline_uid:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_4_FAILED: target_timeline_uid mismatch: {resp.target_timeline_uid} != {auth.target_timeline_uid}"
            )

        # Invariant 5: response.action_payload_digest_sha256 == computed_action_payload_digest_sha256
        if not hmac.compare_digest(resp.action_payload_digest_sha256, computed_action_digest):
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_5_FAILED: action_payload_digest mismatch: {resp.action_payload_digest_sha256} != {computed_action_digest}"
            )

        # Invariant 6: Target identities match DEP-01 §5.1 and reject immutable incident blacklist (DEP03-R2-01)
        if resp.target_project_uid != self._expected_target_project_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: target_project_uid {resp.target_project_uid} != expected {self._expected_target_project_uid}"
            )
        if resp.target_timeline_uid != self._expected_target_timeline_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: target_timeline_uid {resp.target_timeline_uid} != expected {self._expected_target_timeline_uid}"
            )
        if auth.target_project_uid != self._expected_target_project_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: auth target_project_uid {auth.target_project_uid} != expected {self._expected_target_project_uid}"
            )
        if auth.target_timeline_uid != self._expected_target_timeline_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: auth target_timeline_uid {auth.target_timeline_uid} != expected {self._expected_target_timeline_uid}"
            )
        if auth.target_project_name != self._expected_target_project_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: auth target_project_name '{auth.target_project_name}' != '{self._expected_target_project_name}'"
            )
        if auth.target_timeline_name != self._expected_target_timeline_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: auth target_timeline_name '{auth.target_timeline_name}' != '{self._expected_target_timeline_name}'"
            )
        plan_proj_uid = getattr(plan, "target_project_uid", getattr(getattr(plan, "target_spec", None), "project_id", ""))
        plan_tl_uid = getattr(plan, "target_timeline_uid", getattr(getattr(plan, "target_spec", None), "timeline_id", ""))
        plan_proj_name = getattr(plan, "target_project_name", getattr(getattr(plan, "target_spec", None), "project_name", ""))
        plan_tl_name = getattr(plan, "target_timeline_name", getattr(getattr(plan, "target_spec", None), "timeline_name", ""))

        if plan_proj_uid != self._expected_target_project_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: plan target_project_uid '{plan_proj_uid}' != '{self._expected_target_project_uid}'"
            )
        if plan_tl_uid != self._expected_target_timeline_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: plan target_timeline_uid '{plan_tl_uid}' != '{self._expected_target_timeline_uid}'"
            )
        if plan_proj_name != self._expected_target_project_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: plan target_project_name '{plan_proj_name}' != '{self._expected_target_project_name}'"
            )
        if plan_tl_name != self._expected_target_timeline_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_6_FAILED: plan target_timeline_name '{plan_tl_name}' != '{self._expected_target_timeline_name}'"
            )

        # Incident blacklist checks
        if resp.target_project_uid == INCIDENT_TARGET_PROJECT_UID or resp.target_timeline_uid == INCIDENT_TARGET_TIMELINE_UID:
            raise LiveDispatchTargetDeniedError("ERR_INVARIANT_6_FAILED: target UID is on the immutable incident blacklist")
        if auth.target_project_name in INCIDENT_TARGET_PROJECT_NAMES or auth.target_timeline_name in INCIDENT_TARGET_TIMELINE_NAMES:
            raise LiveDispatchTargetDeniedError("ERR_INVARIANT_6_FAILED: target name is on the immutable incident blacklist")

        # Invariant 7: response.is_valid is True and response.reason_code == "OK"
        if resp.is_valid is not True or resp.reason_code != "OK":
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_7_FAILED: oracle rejected verification: is_valid={resp.is_valid}, reason={resp.reason_code}"
            )

        # Invariant 8: now_utc <= response.valid_until_iso and verification age <= 60 seconds
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        valid_until_dt = parse_iso_utc_timestamp(resp.valid_until_iso, "valid_until_iso")
        verified_at_dt = parse_iso_utc_timestamp(resp.verified_at_iso, "verified_at_iso")
        if now_utc > valid_until_dt:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_8_FAILED: verification expired at {resp.valid_until_iso}"
            )
        age_seconds = (now_utc - verified_at_dt).total_seconds()
        if age_seconds > 60.0:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_8_FAILED: verification age {age_seconds:.1f}s exceeds 60s max"
            )
        if age_seconds < -5.0:
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_8_FAILED: verification timestamp is in the future: {age_seconds:.1f}s"
            )

        # Invariant 9: response.verification_result == "VERIFIED_VALID"
        if resp.verification_result != "VERIFIED_VALID":
            raise LiveDispatchOracleVerificationError(
                f"ERR_INVARIANT_9_FAILED: verification_result is {resp.verification_result!r}, expected 'VERIFIED_VALID'"
            )

        # Invariant 10: Pinned oracle public key matches deployment configuration (verified in Invariant 2)

        # Invariant 11: execution_id not previously committed in durable journal and target not locked or in-doubt
        if self._journal.is_execution_id_committed(auth.execution_id):
            raise LiveDispatchDuplicateError(
                f"ERR_INVARIANT_11_FAILED: execution_id {auth.execution_id} is already committed"
            )
        if self._journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid):
            raise LiveDispatchTargetDeniedError(
                f"ERR_INVARIANT_11_FAILED: target {auth.target_project_uid}:{auth.target_timeline_uid} is locked or in-doubt"
            )

        # DEP03-R2-03: Authorization digest binding check
        auth_claims_bytes = canonical_bytes_for_auth_hmac(auth)
        recomputed_auth_digest = hashlib.sha256(auth_claims_bytes).hexdigest().lower()
        if not hmac.compare_digest(resp.authorization_digest_sha256, recomputed_auth_digest):
            raise LiveDispatchOracleVerificationError(
                f"ERR_ORACLE_AUTH_DIGEST_MISMATCH: oracle authorization digest {resp.authorization_digest_sha256} != recomputed {recomputed_auth_digest}"
            )

        # DEP03-R2-03: Proposal revision binding check
        if resp.proposal_revision != auth.proposal_revision:
            raise LiveDispatchOracleVerificationError(
                f"ERR_ORACLE_PROPOSAL_REVISION_MISMATCH: oracle proposal_revision {resp.proposal_revision} != auth {auth.proposal_revision}"
            )

        return resp

    def _validate_target_identity(
        self,
        *,
        plan: TimelineExecutionPlan,
        auth: TrialExecutionAuthorization,
    ) -> None:
        """Enforces pinned canary target identity and immutable incident blacklist (DEP03-R2-01)."""
        if auth.target_project_uid != self._expected_target_project_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_PROJECT_UID_MISMATCH: auth project UID '{auth.target_project_uid}' != '{self._expected_target_project_uid}'"
            )
        if auth.target_timeline_uid != self._expected_target_timeline_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_TIMELINE_UID_MISMATCH: auth timeline UID '{auth.target_timeline_uid}' != '{self._expected_target_timeline_uid}'"
            )
        if auth.target_project_name != self._expected_target_project_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_PROJECT_NAME_MISMATCH: auth project name '{auth.target_project_name}' != '{self._expected_target_project_name}'"
            )
        if auth.target_timeline_name != self._expected_target_timeline_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_TIMELINE_NAME_MISMATCH: auth timeline name '{auth.target_timeline_name}' != '{self._expected_target_timeline_name}'"
            )

        plan_proj_uid = getattr(plan, "target_project_uid", getattr(getattr(plan, "target_spec", None), "project_id", ""))
        plan_tl_uid = getattr(plan, "target_timeline_uid", getattr(getattr(plan, "target_spec", None), "timeline_id", ""))
        plan_proj_name = getattr(plan, "target_project_name", getattr(getattr(plan, "target_spec", None), "project_name", ""))
        plan_tl_name = getattr(plan, "target_timeline_name", getattr(getattr(plan, "target_spec", None), "timeline_name", ""))

        if plan_proj_uid != self._expected_target_project_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_PROJECT_UID_MISMATCH: plan project UID '{plan_proj_uid}' != '{self._expected_target_project_uid}'"
            )
        if plan_tl_uid != self._expected_target_timeline_uid:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_TIMELINE_UID_MISMATCH: plan timeline UID '{plan_tl_uid}' != '{self._expected_target_timeline_uid}'"
            )
        if plan_proj_name != self._expected_target_project_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_PROJECT_NAME_MISMATCH: plan project name '{plan_proj_name}' != '{self._expected_target_project_name}'"
            )
        if plan_tl_name != self._expected_target_timeline_name:
            raise LiveDispatchTargetDeniedError(
                f"ERR_TARGET_TIMELINE_NAME_MISMATCH: plan timeline name '{plan_tl_name}' != '{self._expected_target_timeline_name}'"
            )

        # Incident blacklist checks
        if auth.target_project_uid == INCIDENT_TARGET_PROJECT_UID or auth.target_timeline_uid == INCIDENT_TARGET_TIMELINE_UID:
            raise LiveDispatchTargetDeniedError("ERR_TARGET_DENIED: Target UID is on immutable incident blacklist")
        if auth.target_project_name in INCIDENT_TARGET_PROJECT_NAMES or auth.target_timeline_name in INCIDENT_TARGET_TIMELINE_NAMES:
            raise LiveDispatchTargetDeniedError("ERR_TARGET_DENIED: Target name is on immutable incident blacklist")

    # --------------------------------------------------------------------------
    # Single-Action Geometry Validation
    # --------------------------------------------------------------------------
    def _validate_single_action_geometry(
        self,
        *,
        plan: TimelineExecutionPlan,
        action: PlannedTimelineAction,
        auth: TrialExecutionAuthorization,
    ) -> None:
        """Enforces exact single-action canary geometry per DEP-01 §5.1 and Plan 07B7."""
        actions = getattr(plan, "planned_actions", getattr(plan, "actions", ()))
        if len(actions) != 1:
            raise LiveDispatchScopeViolationError(
                f"ERR_ACTION_COUNT_VIOLATION: plan has {len(actions)} actions, expected exactly 1"
            )
        if action.action_type != "INSERT_CLIP":
            raise LiveDispatchScopeViolationError(
                f"ERR_ACTION_TYPE_VIOLATION: action_type '{action.action_type}' != 'INSERT_CLIP'"
            )
        if action.target_track_type != CANARY_TARGET_TRACK_TYPE:
            raise LiveDispatchScopeViolationError(
                f"ERR_TRACK_TYPE_VIOLATION: target_track_type '{action.target_track_type}' != '{CANARY_TARGET_TRACK_TYPE}'"
            )
        if action.target_track_index != CANARY_TARGET_TRACK_INDEX:
            raise LiveDispatchScopeViolationError(
                f"ERR_TRACK_INDEX_VIOLATION: target_track_index {action.target_track_index} != {CANARY_TARGET_TRACK_INDEX}"
            )

        src_start = action.source_range.start_frame
        src_dur = action.source_range.duration_frames

        if src_start != 0:
            raise LiveDispatchScopeViolationError(
                f"ERR_SOURCE_START_VIOLATION: source_frame_start {src_start} != 0"
            )
        if src_dur != CANARY_SOURCE_DURATION_FRAMES:
            raise LiveDispatchScopeViolationError(
                f"ERR_DURATION_VIOLATION: source_duration_frames {src_dur} != {CANARY_SOURCE_DURATION_FRAMES}"
            )

        tl_start = action.timeline_range.start_frame
        tl_dur = action.timeline_range.duration_frames
        if tl_start != 0:
            raise LiveDispatchScopeViolationError(
                f"ERR_TIMELINE_START_VIOLATION: timeline_frame_start {tl_start} != 0"
            )
        if tl_dur != CANARY_SOURCE_DURATION_FRAMES:
            raise LiveDispatchScopeViolationError(
                f"ERR_TIMELINE_DURATION_VIOLATION: timeline_duration_frames {tl_dur} != {CANARY_SOURCE_DURATION_FRAMES}"
            )

        if auth.authorized_record_frame != CANARY_AUTHORIZED_RECORD_FRAME:
            raise LiveDispatchScopeViolationError(
                f"ERR_RECORD_FRAME_VIOLATION: authorized_record_frame {auth.authorized_record_frame} != {CANARY_AUTHORIZED_RECORD_FRAME}"
            )
        if auth.source_file_sha256 != CANARY_SOURCE_MEDIA_SHA256:
            raise LiveDispatchScopeViolationError(
                f"ERR_MEDIA_DIGEST_VIOLATION: source_file_sha256 {auth.source_file_sha256} != {CANARY_SOURCE_MEDIA_SHA256}"
            )

    # --------------------------------------------------------------------------
    # Oracle Terminal Notification
    # --------------------------------------------------------------------------
    def _notify_oracle_terminal(
        self,
        *,
        auth: TrialExecutionAuthorization,
        action_payload_digest_sha256: str,
        terminal_state: str,
    ) -> OracleTerminalNotificationResponse:
        """Sends OracleTerminalNotificationRequest and verifies signed acknowledgment."""
        if terminal_state not in VALID_TERMINAL_STATES:
            raise LiveDispatchTerminalNotificationError(
                f"ERR_INVALID_TERMINAL_STATE: {terminal_state} not in {sorted(VALID_TERMINAL_STATES)}"
            )

        req_id = str(uuid.uuid4())
        req_nonce = secrets.token_hex(32)
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        term_req = OracleTerminalNotificationRequest(
            schema_version=2,
            request_id=req_id,
            request_nonce=req_nonce,
            requested_at_iso=now_iso,
            execution_id=auth.execution_id,
            authorization_id=auth.authorization_id,
            proposal_revision=auth.proposal_revision,
            target_project_uid=auth.target_project_uid,
            target_timeline_uid=auth.target_timeline_uid,
            action_payload_digest_sha256=action_payload_digest_sha256,
            terminal_state=terminal_state,
        )

        resp_bytes = self._send_oracle_request(term_req.canonical_bytes())
        resp = OracleTerminalNotificationResponse.from_json_bytes(resp_bytes)

        # Validate signature
        sig_str = resp.oracle_signature
        if not sig_str or len(sig_str) != 128:
            raise LiveDispatchTerminalNotificationError("ERR_TERMINAL_SIG_FORMAT: oracle_signature is not 128-char hex")
        try:
            sig_bytes = bytes.fromhex(sig_str)
        except ValueError as exc:
            raise LiveDispatchTerminalNotificationError("ERR_TERMINAL_SIG_HEX: oracle_signature is not valid hex") from exc

        try:
            self._oracle_public_key.verify(sig_bytes, resp.canonical_claims_bytes())
        except InvalidSignature as exc:
            raise LiveDispatchTerminalNotificationError("ERR_TERMINAL_SIG_INVALID: Ed25519 signature verification failed") from exc

        # Bindings verification
        if resp.request_id != req_id or resp.request_nonce != req_nonce:
            raise LiveDispatchTerminalNotificationError("ERR_TERMINAL_NONCE_MISMATCH: request_id or nonce mismatch")
        if resp.execution_id != auth.execution_id:
            raise LiveDispatchTerminalNotificationError(
                f"ERR_TERMINAL_EXECUTION_MISMATCH: execution_id {resp.execution_id} != {auth.execution_id}"
            )
        if resp.terminal_state != terminal_state:
            raise LiveDispatchTerminalNotificationError(
                f"ERR_TERMINAL_STATE_MISMATCH: terminal_state {resp.terminal_state} != {terminal_state}"
            )
        status_val = getattr(resp, "status", getattr(resp, "notification_status", ""))
        if status_val not in ("ACCEPTED", "ALREADY_TERMINAL_SAME_STATE"):
            raise LiveDispatchTerminalNotificationError(
                f"ERR_TERMINAL_NOT_ACCEPTED: status is {status_val!r}, reason={resp.reason_code}"
            )
        if resp.reason_code != "OK":
            raise LiveDispatchTerminalNotificationError(
                f"ERR_TERMINAL_REASON_NOT_OK: reason_code is {resp.reason_code!r}"
            )

        return resp

    # --------------------------------------------------------------------------
    # Dispatch Lifecycle
    # --------------------------------------------------------------------------
    def dispatch_live_canary(
        self,
        *,
        auth: Optional[TrialExecutionAuthorization] = None,
        plan: Optional[TimelineExecutionPlan] = None,
        authorization: Optional[TrialExecutionAuthorization] = None,
        execution_plan: Optional[TimelineExecutionPlan] = None,
        canary_clip_item: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Executes single supervised live canary dispatch following the 8-phase protocol.
        Guarantees pre-mutation durability and fail-closed handling on all outcomes.
        """
        auth_obj = auth if auth is not None else authorization
        plan_obj = plan if plan is not None else execution_plan

        if auth_obj is None:
            raise LiveDispatchScopeViolationError("ERR_MISSING_AUTH: auth is required")
        if plan_obj is None:
            raise LiveDispatchScopeViolationError("ERR_MISSING_PLAN: plan is required")

        with self._lock:
            # DEP03-R2-02: Require strict nominal DTO types
            if type(auth_obj) is not TrialExecutionAuthorization:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_AUTH_TYPE: auth must be exact TrialExecutionAuthorization, got {type(auth_obj)}"
                )
            if type(plan_obj) is not TimelineExecutionPlan:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_PLAN_TYPE: plan must be exact TimelineExecutionPlan, got {type(plan_obj)}"
                )

            # 1. Single-action geometry validation
            actions = getattr(plan_obj, "planned_actions", getattr(plan_obj, "actions", ()))
            if len(actions) != 1:
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_COUNT_VIOLATION: plan has {len(actions)} actions, expected exactly 1"
                )
            action = actions[0]
            if type(action) is not PlannedTimelineAction:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_ACTION_TYPE: action must be exact PlannedTimelineAction, got {type(action)}"
                )

            self._validate_target_identity(plan=plan_obj, auth=auth_obj)
            self._validate_single_action_geometry(plan=plan_obj, action=action, auth=auth_obj)

            # DEP03-R2-02: Recompute canonical plan digest without fallback
            try:
                computed_plan_digest = compute_canonical_plan_digest(plan_obj)
            except Exception as exc:
                raise LiveDispatchScopeViolationError(
                    f"ERR_PLAN_DIGEST_COMPUTATION_FAILED: Failed to compute canonical plan digest: {exc}"
                ) from exc

            if not hmac.compare_digest(computed_plan_digest, auth_obj.plan_digest_sha256):
                raise LiveDispatchScopeViolationError(
                    f"ERR_PLAN_DIGEST_MISMATCH: computed plan digest {computed_plan_digest} != auth {auth_obj.plan_digest_sha256}"
                )

            # DEP03-R2-02: Recompute canonical action payload digest without fallback
            try:
                computed_action_digest = compute_canonical_action_payload_digest_for_admission(
                    action=action,
                    execution_plan=plan_obj,
                    authorization_id=auth_obj.authorization_id,
                    execution_id=auth_obj.execution_id,
                )
            except Exception as exc:
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_DIGEST_COMPUTATION_FAILED: Failed to compute canonical action payload digest: {exc}"
                ) from exc

            if not hmac.compare_digest(computed_action_digest, auth_obj.action_payload_digest_sha256):
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_DIGEST_MISMATCH: computed action digest {computed_action_digest} != auth {auth_obj.action_payload_digest_sha256}"
                )

            # Invariant 11 check: execution_id not previously committed and target not locked/in-doubt
            if self._journal.is_execution_id_committed(auth_obj.execution_id):
                raise LiveDispatchDuplicateError(
                    f"ERR_INVARIANT_11_FAILED: execution_id {auth_obj.execution_id} is already committed"
                )
            if self._journal.is_target_locked_or_in_doubt(auth_obj.target_project_uid, auth_obj.target_timeline_uid):
                raise LiveDispatchTargetDeniedError(
                    f"ERR_INVARIANT_11_FAILED: target {auth_obj.target_project_uid}:{auth_obj.target_timeline_uid} is locked or in-doubt"
                )

            # 3. Construct and send Oracle Verification Request
            req_id = str(uuid.uuid4())
            req_nonce = secrets.token_hex(32)
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            oracle_req = OracleVerificationRequest(
                schema_version=2,
                request_id=req_id,
                request_nonce=req_nonce,
                requested_at_iso=now_iso,
                signed_authorization=auth_obj,
                action_payload_digest_sha256=computed_action_digest,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
                proposal_revision=auth_obj.proposal_revision,
            )

            raw_oracle_resp = self._send_oracle_request(oracle_req.canonical_bytes())

            # 4. Enforce 11-point acceptance invariant gate (with authorization digest and proposal revision checks)
            oracle_resp = self._verify_oracle_verification_response(
                req=oracle_req,
                auth=auth_obj,
                plan=plan_obj,
                action=action,
                computed_action_digest=computed_action_digest,
                response_bytes=raw_oracle_resp,
            )

            # 5. Pre-mutation durability ordering (§5.2.2)
            # Step 1: Write-Ahead Audit Intent fsync
            self._journal.write_audit_intent(
                execution_id=auth_obj.execution_id,
                action_payload_digest_sha256=computed_action_digest,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
            )

            # Step 2: Durable Target Ledger Reservation fsync
            self._journal.reserve_target(
                execution_id=auth_obj.execution_id,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
            )

            # Step 3: Durable WAL Reservation fsync
            self._journal.write_action_pending(
                execution_id=auth_obj.execution_id,
                action_payload_digest_sha256=computed_action_digest,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
            )

            # Step 4: Target exclusion lock (stable inode)
            self._journal.acquire_target_lock(
                auth_obj.target_project_uid,
                auth_obj.target_timeline_uid,
            )

            # 6. Adapter execution boundary
            adapter_result: Optional[Dict[str, Any]] = None
            clean_abort = False
            abort_reason = ""
            in_doubt = False
            in_doubt_reason = ""

            # DEP03-R3-01: Issue unforgeable pre-mutation abort capability
            dispatch_secret = secrets.token_bytes(32)
            expected_action_hash = getattr(action, "action_idempotency_hash", getattr(action, "action_id", ""))
            expected_action_id = getattr(action, "action_id", getattr(action, "action_idempotency_hash", ""))
            clean_abort_cap = PreMutationAbortCapability(
                secret_hmac_key=dispatch_secret,
                execution_id=auth_obj.execution_id,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
                action_idempotency_hash=expected_action_hash,
            )

            # Pass capability to adapter if supported or set attribute
            if hasattr(self._adapter, "set_clean_abort_capability") and callable(self._adapter.set_clean_abort_capability):
                self._adapter.set_clean_abort_capability(clean_abort_cap)
            elif hasattr(self._adapter, "clean_abort_capability"):
                try:
                    setattr(self._adapter, "clean_abort_capability", clean_abort_cap)
                except Exception:
                    pass

            try:
                adapter_kwargs: Dict[str, Any] = {
                    "action": action,
                    "authorization": auth_obj,
                    "canary_clip_item": canary_clip_item,
                    "plan": plan_obj,
                }
                try:
                    sig = inspect.signature(self._adapter.execute_live_canary_action)
                    if "clean_abort_capability" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    ):
                        adapter_kwargs["clean_abort_capability"] = clean_abort_cap
                except Exception:
                    pass

                adapter_raw = self._adapter.execute_live_canary_action(**adapter_kwargs)

                # DEP03-R2-04 & DEP03-R3-02: Validate nominal adapter result and mandatory action binding
                if not isinstance(adapter_raw, dict):
                    in_doubt = True
                    in_doubt_reason = f"Adapter returned non-dict result: {type(adapter_raw)}"
                elif adapter_raw.get("status") != "COMPLETED" or adapter_raw.get("receipt_state") != "COMPLETED":
                    in_doubt = True
                    in_doubt_reason = f"Adapter returned non-COMPLETED status: {adapter_raw.get('status')}"
                elif adapter_raw.get("execution_id") != auth_obj.execution_id:
                    in_doubt = True
                    in_doubt_reason = f"Adapter result execution_id mismatch: {adapter_raw.get('execution_id')} != {auth_obj.execution_id}"
                elif adapter_raw.get("timeline_uid") != auth_obj.target_timeline_uid:
                    in_doubt = True
                    in_doubt_reason = f"Adapter result timeline_uid mismatch: {adapter_raw.get('timeline_uid')} != {auth_obj.target_timeline_uid}"
                elif not adapter_raw.get("receipt_id") or not isinstance(adapter_raw.get("receipt_id"), str):
                    in_doubt = True
                    in_doubt_reason = "Adapter result missing valid receipt_id string"
                elif adapter_raw.get("terminalized") is not True:
                    in_doubt = True
                    in_doubt_reason = "Adapter result terminalized flag is not True"
                else:
                    # DEP03-R3-02: Mandatory action binding validation
                    res_action_id = adapter_raw.get("action_id")
                    res_action_hash = adapter_raw.get("action_idempotency_hash")
                    if not res_action_id and not res_action_hash:
                        in_doubt = True
                        in_doubt_reason = "Adapter result missing mandatory action binding (neither action_id nor action_idempotency_hash present)"
                    elif res_action_id is not None and not isinstance(res_action_id, str):
                        in_doubt = True
                        in_doubt_reason = f"Adapter result action_id must be str, got {type(res_action_id)}"
                    elif res_action_hash is not None and not isinstance(res_action_hash, str):
                        in_doubt = True
                        in_doubt_reason = f"Adapter result action_idempotency_hash must be str, got {type(res_action_hash)}"
                    else:
                        action_bound = False
                        if res_action_id:
                            if res_action_id in (expected_action_id, expected_action_hash):
                                action_bound = True
                            else:
                                in_doubt = True
                                in_doubt_reason = f"Adapter result action_id mismatch: {res_action_id} not in {(expected_action_id, expected_action_hash)}"
                        if not in_doubt and res_action_hash:
                            if res_action_hash == expected_action_hash:
                                action_bound = True
                            else:
                                in_doubt = True
                                in_doubt_reason = f"Adapter result action_idempotency_hash mismatch: {res_action_hash} != {expected_action_hash}"
                        if not in_doubt and action_bound:
                            adapter_result = adapter_raw
            except Exception as exc:
                # DEP03-R4-01: Coupling boundary invalidation to actual adapter mutation cannot rely
                # on voluntary adapter notification. Because LiveResolveCanaryAdapter is not instrumented
                # with an unforgeable dispatcher-enforced mutation boundary callback, the ABORTED/unlock path
                # from adapter execution is disabled. All exceptions and abort attempts during adapter execution
                # are strictly classified fail-closed as IN_DOUBT. Target exclusion lock is retained permanently;
                # no ABORTED receipt is committed and no lock release occurs.
                in_doubt = True
                in_doubt_reason = f"Adapter execution exception: {type(exc).__name__}: {exc}"

            # 7 & 8: Terminalization and Oracle Terminal Notification
            if clean_abort:
                # Branch 2: Clean Abort
                receipt_data = {
                    "execution_id": auth_obj.execution_id,
                    "status": "ABORTED",
                    "reason": abort_reason,
                    "target_project_uid": auth_obj.target_project_uid,
                    "target_timeline_uid": auth_obj.target_timeline_uid,
                    "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self._journal.commit_terminal(
                    execution_id=auth_obj.execution_id,
                    terminal_state="ABORTED",
                    target_project_uid=auth_obj.target_project_uid,
                    target_timeline_uid=auth_obj.target_timeline_uid,
                    receipt_data=receipt_data,
                )
                # Notify Oracle
                self._notify_oracle_terminal(
                    auth=auth_obj,
                    action_payload_digest_sha256=computed_action_digest,
                    terminal_state="ABORTED",
                )
                self._journal.release_target_lock(auth_obj.target_project_uid, auth_obj.target_timeline_uid)
                raise LiveDispatchAdapterError(f"Action cleanly aborted before mutation: {abort_reason}")

            elif in_doubt or adapter_result is None:
                # Branch 3: In-Doubt
                self._journal.commit_terminal(
                    execution_id=auth_obj.execution_id,
                    terminal_state="IN_DOUBT",
                    target_project_uid=auth_obj.target_project_uid,
                    target_timeline_uid=auth_obj.target_timeline_uid,
                )
                self._journal.force_in_doubt_locked(
                    target_project_uid=auth_obj.target_project_uid,
                    target_timeline_uid=auth_obj.target_timeline_uid,
                    execution_id=auth_obj.execution_id,
                    reason=in_doubt_reason or "Unknown adapter outcome",
                )
                # Notify Oracle
                try:
                    self._notify_oracle_terminal(
                        auth=auth_obj,
                        action_payload_digest_sha256=computed_action_digest,
                        terminal_state="IN_DOUBT",
                    )
                except Exception:
                    pass  # Fail closed; lock is already retained
                raise LiveDispatchInDoubtError(
                    f"ERR_DISPATCH_IN_DOUBT: Mutation outcome is in-doubt: {in_doubt_reason}"
                )

            else:
                # Branch 1: Verified Success
                receipt_data = {
                    "execution_id": auth_obj.execution_id,
                    "status": "COMPLETED",
                    "adapter_result": adapter_result,
                    "target_project_uid": auth_obj.target_project_uid,
                    "target_timeline_uid": auth_obj.target_timeline_uid,
                    "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self._journal.commit_terminal(
                    execution_id=auth_obj.execution_id,
                    terminal_state="COMPLETED",
                    target_project_uid=auth_obj.target_project_uid,
                    target_timeline_uid=auth_obj.target_timeline_uid,
                    receipt_data=receipt_data,
                )

                # Step 8d: Oracle Terminal Notification
                self._notify_oracle_terminal(
                    auth=auth_obj,
                    action_payload_digest_sha256=computed_action_digest,
                    terminal_state="COMPLETED",
                )

                # Step 8e: Release target lock only after verified positive terminal ack
                self._journal.release_target_lock(auth_obj.target_project_uid, auth_obj.target_timeline_uid)

                return receipt_data

    def abort_unexecuted_dispatch(
        self,
        *,
        auth: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
        reason: str = "Pre-execution abort requested",
    ) -> Dict[str, Any]:
        """
        Cleanly aborts an authorized dispatch strictly BEFORE crossing the adapter boundary (§3.4, DEP03-R4-01).
        Zero mutation is proven by construction because the adapter execution boundary is never entered.
        Records ABORTED in journal, notifies Oracle of ABORTED, and releases target lock cleanly.
        """
        auth_obj = auth
        plan_obj = plan

        with self._lock:
            if type(auth_obj) is not TrialExecutionAuthorization:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_AUTH_TYPE: auth must be exact TrialExecutionAuthorization, got {type(auth_obj)}"
                )
            if type(plan_obj) is not TimelineExecutionPlan:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_PLAN_TYPE: plan must be exact TimelineExecutionPlan, got {type(plan_obj)}"
                )

            actions = getattr(plan_obj, "planned_actions", getattr(plan_obj, "actions", ()))
            if len(actions) != 1:
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_COUNT_VIOLATION: plan has {len(actions)} actions, expected exactly 1"
                )
            action = actions[0]
            if type(action) is not PlannedTimelineAction:
                raise LiveDispatchScopeViolationError(
                    f"ERR_INVALID_ACTION_TYPE: action must be exact PlannedTimelineAction, got {type(action)}"
                )

            self._validate_target_identity(plan=plan_obj, auth=auth_obj)
            self._validate_single_action_geometry(plan=plan_obj, action=action, auth=auth_obj)

            # Recompute canonical plan digest without fallback
            try:
                computed_plan_digest = compute_canonical_plan_digest(plan_obj)
            except Exception as exc:
                raise LiveDispatchScopeViolationError(
                    f"ERR_PLAN_DIGEST_COMPUTATION_FAILED: Failed to compute canonical plan digest: {exc}"
                ) from exc

            if not hmac.compare_digest(computed_plan_digest, auth_obj.plan_digest_sha256):
                raise LiveDispatchScopeViolationError(
                    f"ERR_PLAN_DIGEST_MISMATCH: computed plan digest {computed_plan_digest} != auth {auth_obj.plan_digest_sha256}"
                )

            try:
                computed_action_digest = compute_canonical_action_payload_digest_for_admission(
                    action=action,
                    execution_plan=plan_obj,
                    authorization_id=auth_obj.authorization_id,
                    execution_id=auth_obj.execution_id,
                )
            except Exception as exc:
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_DIGEST_COMPUTATION_FAILED: Failed to compute canonical action payload digest: {exc}"
                ) from exc

            if not hmac.compare_digest(computed_action_digest, auth_obj.action_payload_digest_sha256):
                raise LiveDispatchScopeViolationError(
                    f"ERR_ACTION_DIGEST_MISMATCH: computed action digest {computed_action_digest} != auth {auth_obj.action_payload_digest_sha256}"
                )

            # Invariant 11 check: execution_id not previously committed and target not locked/in-doubt
            if self._journal.is_execution_id_committed(auth_obj.execution_id):
                raise LiveDispatchDuplicateError(
                    f"ERR_INVARIANT_11_FAILED: execution_id {auth_obj.execution_id} is already committed"
                )
            if self._journal.is_target_locked_or_in_doubt(auth_obj.target_project_uid, auth_obj.target_timeline_uid):
                raise LiveDispatchTargetDeniedError(
                    f"ERR_INVARIANT_11_FAILED: target {auth_obj.target_project_uid}:{auth_obj.target_timeline_uid} is locked or in-doubt"
                )

            # Construct and send Oracle Verification Request to establish verified execution context
            req_id = str(uuid.uuid4())
            req_nonce = secrets.token_hex(32)
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            oracle_req = OracleVerificationRequest(
                schema_version=2,
                request_id=req_id,
                request_nonce=req_nonce,
                requested_at_iso=now_iso,
                signed_authorization=auth_obj,
                action_payload_digest_sha256=computed_action_digest,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
                proposal_revision=auth_obj.proposal_revision,
            )

            raw_oracle_resp = self._send_oracle_request(oracle_req.canonical_bytes())

            self._verify_oracle_verification_response(
                req=oracle_req,
                auth=auth_obj,
                plan=plan_obj,
                action=action,
                computed_action_digest=computed_action_digest,
                response_bytes=raw_oracle_resp,
            )

            # Pre-mutation durability ordering
            self._journal.write_audit_intent(
                execution_id=auth_obj.execution_id,
                action_payload_digest_sha256=computed_action_digest,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
            )
            self._journal.reserve_target(
                execution_id=auth_obj.execution_id,
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
            )
            self._journal.acquire_target_lock(
                auth_obj.target_project_uid,
                auth_obj.target_timeline_uid,
            )

            # Proven zero mutation: adapter was never invoked
            receipt_data = {
                "execution_id": auth_obj.execution_id,
                "status": "ABORTED",
                "reason": reason,
                "target_project_uid": auth_obj.target_project_uid,
                "target_timeline_uid": auth_obj.target_timeline_uid,
                "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            self._journal.commit_terminal(
                execution_id=auth_obj.execution_id,
                terminal_state="ABORTED",
                target_project_uid=auth_obj.target_project_uid,
                target_timeline_uid=auth_obj.target_timeline_uid,
                receipt_data=receipt_data,
            )
            self._notify_oracle_terminal(
                auth=auth_obj,
                action_payload_digest_sha256=computed_action_digest,
                terminal_state="ABORTED",
            )
            self._journal.release_target_lock(auth_obj.target_project_uid, auth_obj.target_timeline_uid)
            return receipt_data
