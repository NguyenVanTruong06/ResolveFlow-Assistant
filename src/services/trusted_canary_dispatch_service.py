"""
Trusted Canary Dispatch Service (Gate 3 Workorder I17).

Architecture Basis: Accepted Plan 07B7 Revision R7 §2, §4-6, §10;
Accepted Consolidated Integration Audit R2, Workorder I17.

Boundary:
    LOCAL_TEST_ONLY; MOCK_GATEWAY_ONLY; NO_PRODUCTION_KEYS;
    NO_SOCKET_OR_DAEMON; NO_LIVE_RESOLVE; NO_LIVE_DISPATCH.

Implements `TrustedCanaryDispatchService` as a local-test-only Dispatcher that accepts
a valid I16 `TrialExecutionAuthorization`, obtains a signed oracle verification response
through an injected in-process test client, enforces the Plan R7 11-point acceptance invariant,
and dispatches at most the single authorized action through the accepted I4
`IsolatedResolveTrialAdapter` backed by `FakeResolveGateway`.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.core.contracts.execution_receipt_contracts import (
    CatalogAssetDigestBinding,
    ExecutionReceipt,
    SourceFileDigestBinding,
    TargetTrackReservation,
)
from src.core.contracts.trial_authorization_contracts import (
    PlannedTimelineAction,
    TrialExecutionAuthorization,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)
from src.core.timeline_plan_execution_binder import (
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)
from src.services.execution_receipt_service import ExecutionReceiptService
from src.services.isolated_resolve_trial_adapter import (
    FakeResolveGateway,
    IsolatedResolveTrialAdapter,
)
from src.services.live_resolve_canary_adapter import LiveResolveCanaryAdapter
from src.services.trial_authorization_signer_service import (
    LocalTestSignerKeyHandle,
    RevocationAnchorAbsentError,
    RevocationEpochTamperingError,
    RevocationManifestVerificationResult,
    RevocationRollbackError,
    SignedRevocationManifest,
    SignerLedgerCorruptionError,
    SignerLedgerDurabilityError,
    SignerLedgerError,
    SignerLedgerSecurityError,
    canonical_revocation_manifest_claims_bytes,
    check_and_advance_revocation_epoch_anchor_for_local_test,
    load_verify_and_anchor_revocation_manifest_for_local_test,
    verify_signed_revocation_manifest,
    _verify_provenance_seal,
)

# Authoritative Incident Target Identifiers from Plan 07B7 §7.1
INCIDENT_TARGET_PROJECT_UID = "0ee71404-6405-47ca-89e1-aaeaac142398"
INCIDENT_TARGET_TIMELINE_UID = "e4e5a587-661d-4854-8f37-8a279d3f2ee1"
INCIDENT_TARGET_PROJECT_NAMES: FrozenSet[str] = frozenset(
    {"RF_TRIAL_9A56C835B97208ED", "RF_TRIAL_CANARY_P01"}
)
INCIDENT_TARGET_TIMELINE_NAMES: FrozenSet[str] = frozenset(
    {"RF_TRIAL_TL_9A56C835B97208ED", "RF_TRIAL_TL_CANARY_T01"}
)

FORBIDDEN_RESOLVE_ATTRIBUTES: Tuple[str, ...] = (
    "_automation",
    "resolve",
    "_resolve_override",
    "fusion",
    "project_manager",
    "media_pool",
    "timeline",
    "GetProjectManager",
    "GetCurrentProject",
)


# ==============================================================================
# Dispatcher Exception Hierarchy
# ==============================================================================


class DispatcherSecurityError(Exception):
    """Base exception for all Dispatcher security violations."""


class DispatcherTripwireError(DispatcherSecurityError):
    """Raised when mock tripwire checks or adapter isolation boundaries fail."""


class DispatcherScopeViolationError(DispatcherSecurityError):
    """Raised when an action or plan violates the strictly bounded single canary scope."""


class DispatcherTargetDeniedError(DispatcherSecurityError):
    """Raised when a target matches blacklisted incident project or timeline identities."""


class DispatcherOracleVerificationError(DispatcherSecurityError):
    """Raised when any of the Plan R7 11-point oracle verification gates fail."""


class DispatcherDuplicateDispatchError(DispatcherSecurityError):
    """Raised when a request ID, request nonce, or execution ID has already been dispatched."""


class DispatcherDispatchError(Exception):
    """Raised when adapter execution fails post-oracle verification."""


class DispatcherReceiptPersistenceError(DispatcherDispatchError):
    """Raised when execution receipt persistence fails, leaving state in doubt."""


# ==============================================================================
# Oracle Request / Response DTOs and Canonical Serializer
# ==============================================================================


@dataclass(frozen=True)
class OracleVerificationRequest:
    """Immutable DTO for Dispatcher -> Signer Oracle verification request (Plan R7 §2)."""

    request_id: str
    request_nonce: str
    requested_at_iso: str
    signed_authorization: TrialExecutionAuthorization
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_REQUEST_ID: request_id must be non-empty str")
        if not isinstance(self.request_nonce, str) or len(self.request_nonce) != 64:
            raise DispatcherOracleVerificationError("ERR_INVALID_REQUEST_NONCE: request_nonce must be 64-hex str")
        parse_iso_utc_timestamp(self.requested_at_iso, "requested_at_iso")
        if type(self.signed_authorization) is not TrialExecutionAuthorization:
            raise DispatcherOracleVerificationError(
                "ERR_INVALID_AUTH_TYPE: signed_authorization must be exact TrialExecutionAuthorization"
            )
        if (
            not isinstance(self.action_payload_digest_sha256, str)
            or len(self.action_payload_digest_sha256) != 64
        ):
            raise DispatcherOracleVerificationError(
                "ERR_INVALID_PAYLOAD_DIGEST: action_payload_digest_sha256 must be 64-hex str"
            )
        if not isinstance(self.target_project_uid, str) or not self.target_project_uid.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_PROJECT_UID: target_project_uid must be non-empty str")
        if not isinstance(self.target_timeline_uid, str) or not self.target_timeline_uid.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_TIMELINE_UID: target_timeline_uid must be non-empty str")


@dataclass(frozen=True)
class OracleVerificationResponse:
    """Immutable DTO for Signer Oracle -> Dispatcher verification response (Plan R7 §2)."""

    response_id: str
    request_id: str
    request_nonce: str
    authorization_digest_sha256: str
    action_payload_digest_sha256: str
    target_project_uid: str
    target_timeline_uid: str
    verification_result: str
    is_valid: bool
    reason_code: str
    verified_at_iso: str
    valid_until_iso: str
    oracle_signature: str

    def __post_init__(self) -> None:
        if not isinstance(self.response_id, str) or not self.response_id.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_RESPONSE_ID: response_id must be non-empty str")
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_REQUEST_ID: request_id must be non-empty str")
        if not isinstance(self.request_nonce, str) or len(self.request_nonce) != 64:
            raise DispatcherOracleVerificationError("ERR_INVALID_REQUEST_NONCE: request_nonce must be 64-hex str")
        if (
            not isinstance(self.authorization_digest_sha256, str)
            or len(self.authorization_digest_sha256) != 64
        ):
            raise DispatcherOracleVerificationError("ERR_INVALID_AUTH_DIGEST: authorization_digest_sha256 must be 64-hex str")
        if (
            not isinstance(self.action_payload_digest_sha256, str)
            or len(self.action_payload_digest_sha256) != 64
        ):
            raise DispatcherOracleVerificationError("ERR_INVALID_PAYLOAD_DIGEST: action_payload_digest_sha256 must be 64-hex str")
        if not isinstance(self.target_project_uid, str) or not self.target_project_uid.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_PROJECT_UID: target_project_uid must be non-empty str")
        if not isinstance(self.target_timeline_uid, str) or not self.target_timeline_uid.strip():
            raise DispatcherOracleVerificationError("ERR_INVALID_TIMELINE_UID: target_timeline_uid must be non-empty str")
        if not isinstance(self.verification_result, str):
            raise DispatcherOracleVerificationError("ERR_INVALID_RESULT_TYPE: verification_result must be str")
        if type(self.is_valid) is not bool:
            raise DispatcherOracleVerificationError("ERR_INVALID_IS_VALID_TYPE: is_valid must be bool")
        if not isinstance(self.reason_code, str):
            raise DispatcherOracleVerificationError("ERR_INVALID_REASON_CODE: reason_code must be str")
        parse_iso_utc_timestamp(self.verified_at_iso, "verified_at_iso")
        parse_iso_utc_timestamp(self.valid_until_iso, "valid_until_iso")
        if not isinstance(self.oracle_signature, str) or len(self.oracle_signature) != 128:
            raise DispatcherOracleVerificationError("ERR_INVALID_SIGNATURE_HEX: oracle_signature must be 128-hex str")


def canonical_bytes_for_oracle_response(
    response: OracleVerificationResponse,
) -> bytes:
    """Serializes exactly the 12 non-signature fields of OracleVerificationResponse.

    Uses canonical RFC 8785 JSON formatting in alphabetical key order.
    """
    payload: Dict[str, Any] = {
        "action_payload_digest_sha256": response.action_payload_digest_sha256,
        "authorization_digest_sha256": response.authorization_digest_sha256,
        "is_valid": response.is_valid,
        "reason_code": response.reason_code,
        "request_id": response.request_id,
        "request_nonce": response.request_nonce,
        "response_id": response.response_id,
        "target_project_uid": response.target_project_uid,
        "target_timeline_uid": response.target_timeline_uid,
        "valid_until_iso": response.valid_until_iso,
        "verification_result": response.verification_result,
        "verified_at_iso": response.verified_at_iso,
    }
    return canonical_json_bytes(payload)


def _normalize_and_validate_ed25519_public_key(key: Any) -> Ed25519PublicKey:
    """Validates that key is a genuine provider-backed Ed25519PublicKey, rejecting custom subclasses.

    Prevents custom subclasses from bypassing cryptographic signature verification via no-op verify()
    (Finding I17-R2-01). Exports raw 32-byte public key and imports through provider, verifying
    exact concrete provider class identity.
    """
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError(f"ERR_ORACLE_KEY_INVALID: oracle_public_key must be an Ed25519PublicKey instance, got {type(key).__name__}")
    try:
        raw_bytes = key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if type(raw_bytes) is not bytes or len(raw_bytes) != 32:
            raise ValueError(f"raw bytes invalid length: {len(raw_bytes) if type(raw_bytes) is bytes else type(raw_bytes)}")
        concrete_key = Ed25519PublicKey.from_public_bytes(raw_bytes)
    except Exception as exc:
        raise TypeError(f"ERR_ORACLE_KEY_NORMALIZATION_FAILED: oracle_public_key raw bytes export/import failed: {exc}") from exc

    if type(key) is not type(concrete_key):
        raise TypeError(
            f"ERR_ORACLE_KEY_SUBCLASS_REJECTED: oracle_public_key must be exact provider-backed Ed25519PublicKey, "
            f"got custom subclass {type(key).__name__}"
        )
    return concrete_key


# ==============================================================================
# Oracle Client Protocol & Local Test Oracle Helper
# ==============================================================================


class OracleVerificationClient(Protocol):
    """Protocol for injected in-process test oracle client."""

    def verify_authorization(
        self,
        request: OracleVerificationRequest,
    ) -> OracleVerificationResponse:
        ...


class LocalTestOracleVerificationClient:
    """In-process test implementation of the Signer Verification Oracle (Plan R7 §2 / Gate 3-I17).

    Evaluates authorization claims, validates target IDs, verifies valid_until window,
    verifies authorization HMAC with accepted I13 LocalTestSignerKeyHandle,
    verifies authoritative revocation evidence using accepted I11 verification core,
    checks revocation status for authorization_id, trial_key_id, director_key_id, and operator_key_id,
    computes canonical 32-claim digest, and produces a validly signed OracleVerificationResponse.
    Provides test controls to inject faults for each of the 11 Plan R7 acceptance checks.
    """

    def __init__(
        self,
        oracle_private_key: Ed25519PrivateKey,
        signer_key_handle: Optional[LocalTestSignerKeyHandle] = None,
        revocation_evidence: Optional[RevocationManifestVerificationResult] = None,
        raw_revocation_manifest_bytes: Optional[bytes] = None,
        revocation_issuer_public_key: Optional[Ed25519PublicKey] = None,
        anchor_directory: Optional[Union[str, Path]] = None,
        registry_directory: Optional[Union[str, Path]] = None,
        director_key_id: Optional[str] = None,
        operator_key_id: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
        fault_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not isinstance(oracle_private_key, Ed25519PrivateKey):
            raise TypeError("oracle_private_key must be an instance of Ed25519PrivateKey")
        self._private_key = oracle_private_key
        self._signer_key_handle = signer_key_handle
        self._revocation_evidence = revocation_evidence
        self._raw_revocation_manifest_bytes = raw_revocation_manifest_bytes
        self._revocation_issuer_public_key = revocation_issuer_public_key
        self._anchor_directory = anchor_directory
        self._registry_directory = registry_directory
        self._director_key_id = director_key_id
        self._operator_key_id = operator_key_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fault_override: Dict[str, Any] = dict(fault_override or {})

    def set_fault_override(self, **kwargs: Any) -> None:
        self._fault_override.update(kwargs)

    def clear_fault_overrides(self) -> None:
        self._fault_override.clear()

    def set_signer_key_handle(self, handle: Optional[LocalTestSignerKeyHandle]) -> None:
        self._signer_key_handle = handle

    def set_anchor_directory(self, anchor_dir: Optional[Union[str, Path]]) -> None:
        self._anchor_directory = anchor_dir

    def set_registry_directory(self, reg_dir: Optional[Union[str, Path]]) -> None:
        self._registry_directory = reg_dir

    def set_verified_revocation_evidence(
        self,
        evidence: Optional[RevocationManifestVerificationResult],
    ) -> None:
        self._revocation_evidence = evidence
        self._raw_revocation_manifest_bytes = None
        self._revocation_issuer_public_key = None

    def set_raw_revocation_manifest(
        self,
        raw_bytes: Optional[bytes],
        issuer_pk: Optional[Ed25519PublicKey],
    ) -> None:
        self._raw_revocation_manifest_bytes = raw_bytes
        self._revocation_issuer_public_key = issuer_pk
        self._revocation_evidence = None

    def set_bound_identities(
        self,
        *,
        director_key_id: Optional[str] = None,
        operator_key_id: Optional[str] = None,
    ) -> None:
        if director_key_id is not None:
            self._director_key_id = director_key_id
        if operator_key_id is not None:
            self._operator_key_id = operator_key_id

    def verify_authorization(
        self,
        request: OracleVerificationRequest,
    ) -> OracleVerificationResponse:
        auth = request.signed_authorization
        now_dt = self._clock()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        # 1. Recompute 32-claim canonical bytes and digest
        auth_bytes = canonical_bytes_for_auth_hmac(auth)
        computed_auth_digest = hashlib.sha256(auth_bytes).hexdigest()

        # 2. Check validity window of the authorization
        valid_until_dt = parse_iso_utc_timestamp(auth.valid_until_iso, "valid_until_iso")
        is_window_valid = now_dt <= valid_until_dt

        # 3. Check payload digest and target UIDs
        payload_digest_match = hmac.compare_digest(
            request.action_payload_digest_sha256, auth.action_payload_digest_sha256
        )
        target_proj_match = request.target_project_uid == auth.target_project_uid
        target_tl_match = request.target_timeline_uid == auth.target_timeline_uid

        # 4. Verify HMAC using accepted I13 capability handle (I17-R2-02)
        hmac_valid = False
        if self._signer_key_handle is not None and isinstance(self._signer_key_handle, LocalTestSignerKeyHandle):
            try:
                hmac_valid = self._signer_key_handle.verify_trial_execution_authorization(auth)
            except Exception:
                hmac_valid = False

        # 5. Verify authoritative revocation evidence & epoch anchor (I17-R3-01, I17-R4-01 / Plan R7 §2, §6.2)
        revocation_verified = False
        revocation_failure_reason = ""
        verified_manifest: Optional[SignedRevocationManifest] = None

        active_anchor_dir = self._anchor_directory or self._registry_directory

        if self._registry_directory is not None and self._revocation_issuer_public_key is not None:
            # Safe composition path: load, verify signature, and check epoch anchor via I11 loader
            try:
                reg_result = load_verify_and_anchor_revocation_manifest_for_local_test(
                    registry_directory=self._registry_directory,
                    issuer_public_key=self._revocation_issuer_public_key,
                    verification_time_utc=now_dt,
                    advance_anchor=False,
                    require_existing_anchor=True,
                )
                if reg_result.verified is True and _verify_provenance_seal(reg_result):
                    verified_manifest = reg_result.manifest
                    revocation_verified = True
                else:
                    revocation_failure_reason = "ERR_REVOCATION_PROVENANCE_SEAL_INVALID"
            except RevocationAnchorAbsentError as exc:
                revocation_failure_reason = f"ERR_REVOCATION_ANCHOR_ABSENT: {exc}"
            except (RevocationRollbackError, RevocationEpochTamperingError) as exc:
                revocation_failure_reason = f"ERR_REVOCATION_EPOCH_CHECK_FAILED: {exc}"
            except Exception as exc:
                revocation_failure_reason = f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}"
        elif self._raw_revocation_manifest_bytes is not None and self._revocation_issuer_public_key is not None:
            # Verify raw manifest bytes through accepted I11 verification core and verify epoch anchor
            if active_anchor_dir is None:
                revocation_failure_reason = "ERR_REVOCATION_ANCHOR_ABSENT: unanchored evidence prohibited"
            else:
                try:
                    result = verify_signed_revocation_manifest(
                        raw_manifest_bytes=self._raw_revocation_manifest_bytes,
                        issuer_public_key=self._revocation_issuer_public_key,
                        verification_time_utc=now_dt,
                    )
                    if result.verified is True and _verify_provenance_seal(result):
                        check_and_advance_revocation_epoch_anchor_for_local_test(
                            verified_result=result,
                            anchor_directory=active_anchor_dir,
                            advance=False,
                            require_existing_anchor=True,
                        )
                        verified_manifest = result.manifest
                        revocation_verified = True
                    else:
                        revocation_failure_reason = "ERR_REVOCATION_PROVENANCE_SEAL_INVALID"
                except RevocationAnchorAbsentError as exc:
                    revocation_failure_reason = f"ERR_REVOCATION_ANCHOR_ABSENT: {exc}"
                except (RevocationRollbackError, RevocationEpochTamperingError) as exc:
                    revocation_failure_reason = f"ERR_REVOCATION_EPOCH_CHECK_FAILED: {exc}"
                except Exception as exc:
                    revocation_failure_reason = f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}"
        elif self._revocation_evidence is not None:
            # Consume pre-verified I11 result; validate provenance seal, instant freshness, and epoch anchor
            if active_anchor_dir is None:
                revocation_failure_reason = "ERR_REVOCATION_ANCHOR_ABSENT: unanchored evidence prohibited"
            else:
                evidence = self._revocation_evidence
                if (
                    type(evidence) is RevocationManifestVerificationResult
                    and evidence.verified is True
                    and _verify_provenance_seal(evidence)
                ):
                    try:
                        manifest_valid_until = parse_iso_utc_timestamp(evidence.manifest.valid_until_iso, "valid_until_iso")
                        if now_dt <= manifest_valid_until:
                            check_and_advance_revocation_epoch_anchor_for_local_test(
                                verified_result=evidence,
                                anchor_directory=active_anchor_dir,
                                advance=False,
                                require_existing_anchor=True,
                            )
                            verified_manifest = evidence.manifest
                            revocation_verified = True
                        else:
                            revocation_failure_reason = "ERR_REVOCATION_MANIFEST_EXPIRED"
                    except RevocationAnchorAbsentError as exc:
                        revocation_failure_reason = f"ERR_REVOCATION_ANCHOR_ABSENT: {exc}"
                    except (RevocationRollbackError, RevocationEpochTamperingError) as exc:
                        revocation_failure_reason = f"ERR_REVOCATION_EPOCH_CHECK_FAILED: {exc}"
                    except Exception as exc:
                        revocation_failure_reason = f"ERR_REVOCATION_VERIFICATION_FAILED: {exc}"
                else:
                    revocation_failure_reason = "ERR_REVOCATION_EVIDENCE_FORGERY"
        else:
            revocation_failure_reason = "ERR_REVOCATION_EVIDENCE_ABSENT"

        # 6. Check required Director and Operator key identities (I17-R3-01)
        identities_present = True
        identity_failure_reason = ""
        dir_kid = self._director_key_id
        op_kid = self._operator_key_id
        if not dir_kid or not isinstance(dir_kid, str) or not dir_kid.strip():
            identities_present = False
            identity_failure_reason = "ERR_MISSING_DIRECTOR_KEY_IDENTITY"
        elif not op_kid or not isinstance(op_kid, str) or not op_kid.strip():
            identities_present = False
            identity_failure_reason = "ERR_MISSING_OPERATOR_KEY_IDENTITY"

        # 7. Check revocation membership across all 4 required collections
        is_revoked = False
        revoked_reason = ""
        if revocation_verified and verified_manifest is not None and identities_present:
            if auth.authorization_id in verified_manifest.revoked_authorization_ids:
                is_revoked = True
                revoked_reason = "ERR_AUTHORIZATION_REVOKED"
            elif auth.key_id in verified_manifest.revoked_trial_key_ids:
                is_revoked = True
                revoked_reason = "ERR_TRIAL_KEY_REVOKED"
            elif dir_kid in verified_manifest.revoked_director_key_ids:
                is_revoked = True
                revoked_reason = "ERR_DIRECTOR_KEY_REVOKED"
            elif op_kid in verified_manifest.revoked_operator_key_ids:
                is_revoked = True
                revoked_reason = "ERR_OPERATOR_KEY_REVOKED"

        # Plan R7 Signer Oracle Decision
        if not hmac_valid:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = "ERR_AUTH_HMAC_INVALID"
        elif not revocation_verified:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = revocation_failure_reason or "ERR_REVOCATION_UNVERIFIED"
        elif not identities_present:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = identity_failure_reason
        elif is_revoked:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = revoked_reason or "ERR_IDENTITY_REVOKED"
        elif not is_window_valid:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = "ERR_VALIDITY_WINDOW_EXPIRED"
        elif not payload_digest_match:
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = "ERR_PAYLOAD_DIGEST_MISMATCH"
        elif not (target_proj_match and target_tl_match):
            is_valid = False
            verification_result = "VERIFICATION_FAILED"
            reason_code = "ERR_TARGET_UID_MISMATCH"
        else:
            is_valid = True
            verification_result = "VERIFIED_VALID"
            reason_code = "OK"

        now_iso = now_dt.isoformat().replace("+00:00", "Z")
        valid_until_resp_dt = now_dt.fromtimestamp(now_dt.timestamp() + 60.0, tz=timezone.utc)
        valid_until_resp_iso = valid_until_resp_dt.isoformat().replace("+00:00", "Z")

        # Build base dictionary allowing test overrides
        fields: Dict[str, Any] = {
            "response_id": str(uuid.uuid4()),
            "request_id": request.request_id,
            "request_nonce": request.request_nonce,
            "authorization_digest_sha256": computed_auth_digest,
            "action_payload_digest_sha256": request.action_payload_digest_sha256,
            "target_project_uid": request.target_project_uid,
            "target_timeline_uid": request.target_timeline_uid,
            "verification_result": verification_result,
            "is_valid": is_valid,
            "reason_code": reason_code,
            "verified_at_iso": now_iso,
            "valid_until_iso": valid_until_resp_iso,
        }
        fields.update(self._fault_override)

        # Build preliminary response object to serialize for signing
        temp_resp = OracleVerificationResponse(
            response_id=str(fields["response_id"]),
            request_id=str(fields["request_id"]),
            request_nonce=str(fields["request_nonce"]),
            authorization_digest_sha256=str(fields["authorization_digest_sha256"]),
            action_payload_digest_sha256=str(fields["action_payload_digest_sha256"]),
            target_project_uid=str(fields["target_project_uid"]),
            target_timeline_uid=str(fields["target_timeline_uid"]),
            verification_result=str(fields["verification_result"]),
            is_valid=fields["is_valid"],
            reason_code=str(fields["reason_code"]),
            verified_at_iso=str(fields["verified_at_iso"]),
            valid_until_iso=str(fields["valid_until_iso"]),
            oracle_signature="0" * 128,  # placeholder
        )

        resp_bytes = canonical_bytes_for_oracle_response(temp_resp)
        real_signature = self._private_key.sign(resp_bytes).hex()

        override_sig = self._fault_override.get("oracle_signature")
        final_sig = str(override_sig) if override_sig is not None else real_signature

        return OracleVerificationResponse(
            response_id=temp_resp.response_id,
            request_id=temp_resp.request_id,
            request_nonce=temp_resp.request_nonce,
            authorization_digest_sha256=temp_resp.authorization_digest_sha256,
            action_payload_digest_sha256=temp_resp.action_payload_digest_sha256,
            target_project_uid=temp_resp.target_project_uid,
            target_timeline_uid=temp_resp.target_timeline_uid,
            verification_result=temp_resp.verification_result,
            is_valid=temp_resp.is_valid,
            reason_code=temp_resp.reason_code,
            verified_at_iso=temp_resp.verified_at_iso,
            valid_until_iso=temp_resp.valid_until_iso,
            oracle_signature=final_sig,
        )


# ==============================================================================
# Dispatch Result DTO
# ==============================================================================


@dataclass(frozen=True)
class CanaryDispatchResult:
    """Truthful result DTO returned upon committed mock canary dispatch."""

    status: str
    execution_id: str
    authorization_id: str
    action_id: str
    resolve_item_id: str
    target_project_uid: str
    target_timeline_uid: str
    oracle_response_id: str
    mode: str = "MOCK_ONLY"
    terminalized: bool = False
    receipt_id: Optional[str] = None
    adapter_result: Optional[Dict[str, Any]] = None


# ==============================================================================
# Trusted Canary Dispatch Service Implementation
# ==============================================================================


class TrustedCanaryDispatchService:
    """Local-test-only Dispatcher service enforcing Plan R7 11-point oracle verification gate.

    Consumes valid I16 authorization, recomputes digests, validates single canary scope,
    enforces target blacklist, queries injected oracle client, and executes single action
    via accepted I4 mock adapter.
    """

    def __init__(
        self,
        adapter: IsolatedResolveTrialAdapter,
        oracle_client: OracleVerificationClient,
        oracle_public_key: Ed25519PublicKey,
        receipt_service: Optional[ExecutionReceiptService] = None,
        clock: Optional[Callable[[], datetime]] = None,
        target_blacklist: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        # 1. Strict nominal type and isolation checks
        if type(adapter) is not IsolatedResolveTrialAdapter:
            raise DispatcherTripwireError(
                f"ERR_INVALID_ADAPTER_TYPE: adapter must be exact IsolatedResolveTrialAdapter, got {type(adapter).__name__}"
            )
        if isinstance(adapter, LiveResolveCanaryAdapter):
            raise DispatcherTripwireError(
                "ERR_LIVE_ADAPTER_PROHIBITED: LiveResolveCanaryAdapter is strictly prohibited"
            )
        # Validate and normalize oracle_public_key, rejecting custom subclasses (I17-R2-01)
        normalized_pk = _normalize_and_validate_ed25519_public_key(oracle_public_key)
        if not hasattr(oracle_client, "verify_authorization") or not callable(
            getattr(oracle_client, "verify_authorization")
        ):
            raise TypeError("oracle_client must implement callable verify_authorization")

        if receipt_service is not None and not isinstance(receipt_service, ExecutionReceiptService):
            raise TypeError("receipt_service must be an instance of ExecutionReceiptService")

        self._adapter = adapter
        self._oracle_client = oracle_client
        self._oracle_public_key = normalized_pk
        self._receipt_service = receipt_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))

        # Initial mock tripwire validation
        self._validate_adapter_tripwire(self._adapter)

        # Parse target blacklist
        blacklist_map = dict(target_blacklist or {})
        self._blacklisted_project_uids: Set[str] = {
            s.strip().lower() for s in blacklist_map.get("project_uids", ())
        }
        self._blacklisted_timeline_uids: Set[str] = {
            s.strip().lower() for s in blacklist_map.get("timeline_uids", ())
        }
        self._blacklisted_project_names: Set[str] = {
            s.strip() for s in blacklist_map.get("project_names", ())
        }
        self._blacklisted_timeline_names: Set[str] = {
            s.strip() for s in blacklist_map.get("timeline_names", ())
        }

        # Atomic dispatch and replay tracking
        self._dispatched_execution_ids: Set[str] = set()
        self._dispatched_request_ids: Set[str] = set()
        self._dispatched_request_nonces: Set[str] = set()
        self._lock = threading.Lock()

        # Strict safety assertion: zero HMAC key possession
        for forbidden_prop in (
            "_hmac_key",
            "hmac_key",
            "_signing_key",
            "_key_bytes",
            "_capability",
            "signing_capability",
        ):
            if hasattr(self, forbidden_prop):
                raise DispatcherSecurityError(
                    f"ERR_DISPATCHER_KEY_POSSESSION_PROHIBITED: dispatcher must not possess '{forbidden_prop}'"
                )

    @property
    def adapter(self) -> IsolatedResolveTrialAdapter:
        return self._adapter

    @property
    def oracle_public_key(self) -> Ed25519PublicKey:
        return self._oracle_public_key

    def _validate_adapter_tripwire(self, adapter: Any) -> None:
        """Validates that the adapter and its gateway are exact approved fakes with zero live attributes."""
        if type(adapter) is not IsolatedResolveTrialAdapter:
            raise DispatcherTripwireError(
                f"ERR_INVALID_ADAPTER_TYPE: adapter must be exact IsolatedResolveTrialAdapter, got {type(adapter).__name__}"
            )
        if isinstance(adapter, LiveResolveCanaryAdapter):
            raise DispatcherTripwireError(
                "ERR_LIVE_ADAPTER_PROHIBITED: LiveResolveCanaryAdapter is strictly prohibited"
            )

        try:
            adapter.validate_mock_tripwire()
        except Exception as exc:
            raise DispatcherTripwireError(f"ERR_MOCK_TRIPWIRE_FAILED: adapter mock tripwire failed: {exc}") from exc

        fake_gw = getattr(adapter, "fake_gateway", None)
        if fake_gw is None or type(fake_gw) is not FakeResolveGateway:
            raise DispatcherTripwireError(
                f"ERR_INVALID_GATEWAY_TYPE: adapter.fake_gateway must be exact FakeResolveGateway, got {type(fake_gw).__name__}"
            )

        for attr in FORBIDDEN_RESOLVE_ATTRIBUTES:
            if hasattr(adapter, attr) or hasattr(fake_gw, attr):
                raise DispatcherTripwireError(
                    f"ERR_LIVE_RESOLVE_ATTRIBUTE_DETECTED: detected live Resolve attribute '{attr}'"
                )

    def _enforce_target_blacklist(
        self,
        project_name: str,
        project_uid: str,
        timeline_name: str,
        timeline_uid: str,
    ) -> None:
        """Enforces rejection of prior incident project and timeline targets (Plan R7 §7.2)."""
        p_uid = project_uid.strip().lower()
        t_uid = timeline_uid.strip().lower()
        p_name = project_name.strip()
        t_name = timeline_name.strip()

        if p_uid == INCIDENT_TARGET_PROJECT_UID.lower() or p_uid in self._blacklisted_project_uids:
            raise DispatcherTargetDeniedError(
                f"ERR_TARGET_PROJECT_UID_BLACKLISTED: project UID '{project_uid}' is blacklisted"
            )
        if t_uid == INCIDENT_TARGET_TIMELINE_UID.lower() or t_uid in self._blacklisted_timeline_uids:
            raise DispatcherTargetDeniedError(
                f"ERR_TARGET_TIMELINE_UID_BLACKLISTED: timeline UID '{timeline_uid}' is blacklisted"
            )
        if p_name in INCIDENT_TARGET_PROJECT_NAMES or p_name in self._blacklisted_project_names:
            raise DispatcherTargetDeniedError(
                f"ERR_TARGET_PROJECT_NAME_BLACKLISTED: project name '{project_name}' is blacklisted"
            )
        if t_name in INCIDENT_TARGET_TIMELINE_NAMES or t_name in self._blacklisted_timeline_names:
            raise DispatcherTargetDeniedError(
                f"ERR_TARGET_TIMELINE_NAME_BLACKLISTED: timeline name '{timeline_name}' is blacklisted"
            )

    def _enforce_scope_boundary(
        self,
        action: PlannedTimelineAction,
        authorization: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
    ) -> None:
        """Enforces strictly bounded single canary scope (Plan R7 §3.3).

        Exactly 1 action: INSERT_CLIP, video track 1, 120 timeline frames,
        120 source frames, source start 0, timeline relative start 0.
        Binds authoritative TimelineExecutionPlan, recomputes canonical plan digest,
        and verifies consistency with authorization claims.
        """
        if action.action_type != "INSERT_CLIP":
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_ACTION_TYPE: action_type must be 'INSERT_CLIP', got {action.action_type!r}"
            )
        if action.track_type.lower() != "video":
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_TRACK_TYPE: track_type must be 'video', got {action.track_type!r}"
            )
        if type(action.track_index) is not int or isinstance(action.track_index, bool) or action.track_index != 1:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_TRACK_INDEX: track_index must be 1, got {action.track_index}"
            )
        if (
            type(action.duration_frames) is not int
            or isinstance(action.duration_frames, bool)
            or action.duration_frames != 120
        ):
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_TIMELINE_DURATION: duration_frames must be 120, got {action.duration_frames}"
            )
        if (
            type(action.source_frame_duration) is not int
            or isinstance(action.source_frame_duration, bool)
            or action.source_frame_duration != 120
        ):
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_SOURCE_DURATION: source_frame_duration must be 120, got {action.source_frame_duration}"
            )
        if (
            type(action.source_frame_start) is not int
            or isinstance(action.source_frame_start, bool)
            or action.source_frame_start != 0
        ):
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_SOURCE_START: source_frame_start must be 0, got {action.source_frame_start}"
            )

        # Timeline-relative start frame must be exactly 0
        rel_start = action.record_frame - authorization.observed_timeline_start_frame
        if rel_start != 0:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_TIMELINE_RELATIVE_START: relative start frame must be 0, got {rel_start}"
            )

        # Authorization consistency
        if authorization.authorized_action_type != "INSERT_CLIP":
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_ACTION_TYPE: authorized_action_type must be 'INSERT_CLIP', got {authorization.authorized_action_type!r}"
            )
        if authorization.authorized_track_type != "video":
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_TRACK_TYPE: authorized_track_type must be 'video', got {authorization.authorized_track_type!r}"
            )
        if authorization.authorized_track_index != 1:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_TRACK_INDEX: authorized_track_index must be 1, got {authorization.authorized_track_index}"
            )
        if authorization.authorized_timeline_frame_duration != 120:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_TIMELINE_DURATION: authorized_timeline_frame_duration must be 120, got {authorization.authorized_timeline_frame_duration}"
            )
        if authorization.authorized_source_frame_duration != 120:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_SOURCE_DURATION: authorized_source_frame_duration must be 120, got {authorization.authorized_source_frame_duration}"
            )
        if authorization.authorized_source_frame_start != 0:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_SOURCE_START: authorized_source_frame_start must be 0, got {authorization.authorized_source_frame_start}"
            )
        if authorization.authorized_timeline_frame_start != 0:
            raise DispatcherScopeViolationError(
                f"ERR_FORBIDDEN_AUTH_TIMELINE_START: authorized_timeline_frame_start must be 0, got {authorization.authorized_timeline_frame_start}"
            )
        if action.record_frame != authorization.authorized_record_frame:
            raise DispatcherScopeViolationError(
                f"ERR_RECORD_FRAME_MISMATCH: action.record_frame ({action.record_frame}) != auth.authorized_record_frame ({authorization.authorized_record_frame})"
            )
        if action.source_path != authorization.source_file_path:
            raise DispatcherScopeViolationError(
                f"ERR_SOURCE_PATH_MISMATCH: action.source_path ({action.source_path!r}) != auth.source_file_path ({authorization.source_file_path!r})"
            )

        # Plan type and single canary action consistency checks (I17-01)
        if type(plan) is not TimelineExecutionPlan:
            raise DispatcherScopeViolationError(
                f"ERR_INVALID_PLAN_TYPE: plan must be exact TimelineExecutionPlan, got {type(plan).__name__}"
            )
        if plan.binding_status != "BOUND_VERIFIED":
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_BINDING_STATUS: plan binding_status must be 'BOUND_VERIFIED', got {plan.binding_status!r}"
            )
        if len(plan.planned_actions) != 1 or plan.planned_actions_count != 1:
            count = len(plan.planned_actions) if hasattr(plan, "planned_actions") else "None"
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_ACTION_COUNT_MISMATCH: plan must have exactly 1 planned action, got {count}"
            )

        plan_action = plan.planned_actions[0]
        if plan_action.clip_id != action.action_id:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_ACTION_ID_MISMATCH: plan action clip_id ({plan_action.clip_id!r}) != action.action_id ({action.action_id!r})"
            )
        if plan_action.action_type != action.action_type:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_ACTION_TYPE_MISMATCH: plan action_type ({plan_action.action_type!r}) != action.action_type ({action.action_type!r})"
            )
        if plan_action.target_track_type != action.track_type:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TRACK_TYPE_MISMATCH: plan track_type ({plan_action.target_track_type!r}) != action.track_type ({action.track_type!r})"
            )
        if plan_action.target_track_index != action.track_index:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TRACK_INDEX_MISMATCH: plan track_index ({plan_action.target_track_index}) != action.track_index ({action.track_index})"
            )
        if plan_action.source_file_path != action.source_path:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_PATH_MISMATCH: plan source_file_path ({plan_action.source_file_path!r}) != action.source_path ({action.source_path!r})"
            )
        if plan_action.source_range.start_frame != 0 or plan_action.source_range.start_frame != action.source_frame_start:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_START_MISMATCH: plan source start frame must be 0 and match action ({action.source_frame_start})"
            )
        plan_src_dur = plan_action.source_range.end_frame - plan_action.source_range.start_frame
        if plan_src_dur != action.source_frame_duration or plan_src_dur != 120:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_DURATION_MISMATCH: plan source duration ({plan_src_dur}) != action ({action.source_frame_duration})"
            )
        if plan_action.timeline_range.start_frame != 0:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TIMELINE_START_MISMATCH: plan timeline relative start frame must be 0, got {plan_action.timeline_range.start_frame}"
            )
        plan_tl_dur = plan_action.timeline_range.end_frame - plan_action.timeline_range.start_frame
        if plan_tl_dur != action.duration_frames or plan_tl_dur != 120:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TIMELINE_DURATION_MISMATCH: plan timeline duration ({plan_tl_dur}) != action ({action.duration_frames})"
            )

        # Plan target specification bindings vs authorization
        target_spec = plan.target_spec
        if target_spec.project_id != authorization.target_project_uid:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_PROJECT_UID_MISMATCH: plan project ID ({target_spec.project_id!r}) != auth ({authorization.target_project_uid!r})"
            )
        if target_spec.timeline_id != authorization.target_timeline_uid:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TIMELINE_UID_MISMATCH: plan timeline ID ({target_spec.timeline_id!r}) != auth ({authorization.target_timeline_uid!r})"
            )
        if target_spec.project_name != authorization.target_project_name:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_PROJECT_NAME_MISMATCH: plan project name ({target_spec.project_name!r}) != auth ({authorization.target_project_name!r})"
            )
        if target_spec.timeline_name != authorization.target_timeline_name:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TIMELINE_NAME_MISMATCH: plan timeline name ({target_spec.timeline_name!r}) != auth ({authorization.target_timeline_name!r})"
            )
        if (
            target_spec.timeline_fps.numerator != authorization.target_timeline_fps_numerator
            or target_spec.timeline_fps.denominator != authorization.target_timeline_fps_denominator
        ):
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_TIMELINE_FPS_MISMATCH: plan FPS ({target_spec.timeline_fps.numerator}/{target_spec.timeline_fps.denominator}) "
                f"!= auth ({authorization.target_timeline_fps_numerator}/{authorization.target_timeline_fps_denominator})"
            )
        if plan.idempotency_key != authorization.plan_idempotency_key:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_IDEMPOTENCY_MISMATCH: plan idempotency ({plan.idempotency_key!r}) != auth ({authorization.plan_idempotency_key!r})"
            )
        if plan.proposal_id != authorization.proposal_id:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_PROPOSAL_ID_MISMATCH: plan proposal_id ({plan.proposal_id!r}) != auth ({authorization.proposal_id!r})"
            )
        if plan.proposal_revision != authorization.proposal_revision:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_PROPOSAL_REVISION_MISMATCH: plan revision ({plan.proposal_revision!r}) != auth ({authorization.proposal_revision!r})"
            )
        if plan.approval_id != authorization.approval_record_id:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_APPROVAL_ID_MISMATCH: plan approval_id ({plan.approval_id!r}) != auth ({authorization.approval_record_id!r})"
            )

        # Plan source digests binding verification (I17-02)
        matching_source: Optional[SourceFileDigestBinding] = None
        for binding in plan.source_digests:
            if binding.absolute_path == authorization.source_file_path:
                matching_source = binding
                break
        if matching_source is None:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_DIGEST_MISSING: plan.source_digests missing entry for {authorization.source_file_path!r}"
            )
        if not hmac.compare_digest(matching_source.full_sha256.lower(), authorization.source_file_sha256.lower()):
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_SHA256_MISMATCH: plan source sha256 ({matching_source.full_sha256}) != auth ({authorization.source_file_sha256})"
            )
        if matching_source.clip_id != action.action_id:
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_SOURCE_CLIP_ID_MISMATCH: plan source clip_id ({matching_source.clip_id!r}) != action.action_id ({action.action_id!r})"
            )

        # Plan digest recomputation and constant-time verification (I17-01)
        computed_plan_digest = compute_canonical_plan_digest(plan)
        if not hmac.compare_digest(authorization.plan_digest_sha256, computed_plan_digest):
            raise DispatcherScopeViolationError(
                f"ERR_PLAN_DIGEST_MISMATCH: authorization.plan_digest_sha256 ({authorization.plan_digest_sha256}) "
                f"does not match recomputed plan digest ({computed_plan_digest})"
            )

    def _verify_oracle_response_11_points(
        self,
        request: OracleVerificationRequest,
        response: OracleVerificationResponse,
        computed_auth_digest: str,
        recomputed_action_payload_digest: str,
        now_dt: datetime,
    ) -> None:
        """Enforces the Plan R7 11-Point Dispatcher Acceptance Invariant.

        Fails closed with DispatcherOracleVerificationError if ANY condition is not met.
        """
        # Ensure self._oracle_public_key is genuine provider-backed concrete key (I17-R2-01)
        try:
            concrete_oracle_pk = _normalize_and_validate_ed25519_public_key(self._oracle_public_key)
        except Exception as exc:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_KEY_INVALID: oracle public key validation failed: {exc}"
            ) from exc

        # Point 1: Valid Ed25519 signature over canonical response claims
        resp_bytes = canonical_bytes_for_oracle_response(response)
        try:
            sig_bytes = bytes.fromhex(response.oracle_signature)
            concrete_oracle_pk.verify(sig_bytes, resp_bytes)
        except (InvalidSignature, ValueError) as sig_err:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_SIGNATURE_INVALID: response signature verification failed: {sig_err}"
            ) from sig_err

        # Point 2: is_valid is True (exact built-in bool)
        if type(response.is_valid) is not bool or response.is_valid is not True:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_IS_VALID_NOT_TRUE: response.is_valid must be bool True, got {response.is_valid!r}"
            )

        # Point 3: verification_result == "VERIFIED_VALID"
        if response.verification_result != "VERIFIED_VALID":
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_VERIFICATION_RESULT_INVALID: expected 'VERIFIED_VALID', got {response.verification_result!r}"
            )

        # Point 4: reason_code == "OK"
        if response.reason_code != "OK":
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_REASON_CODE_INVALID: expected 'OK', got {response.reason_code!r}"
            )

        # Point 5: Exact request_id match
        if response.request_id != request.request_id:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_REQUEST_ID_MISMATCH: expected {request.request_id!r}, got {response.request_id!r}"
            )

        # Point 6: Exact request_nonce match
        if response.request_nonce != request.request_nonce:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_REQUEST_NONCE_MISMATCH: expected {request.request_nonce!r}, got {response.request_nonce!r}"
            )

        # Point 7: Authorization digest match (constant time)
        if not hmac.compare_digest(response.authorization_digest_sha256, computed_auth_digest):
            raise DispatcherOracleVerificationError(
                "ERR_ORACLE_AUTH_DIGEST_MISMATCH: response authorization_digest_sha256 does not match computed authorization digest"
            )

        # Point 8: Recomputed action payload digest match (constant time)
        if not hmac.compare_digest(
            response.action_payload_digest_sha256, recomputed_action_payload_digest
        ):
            raise DispatcherOracleVerificationError(
                "ERR_ORACLE_ACTION_PAYLOAD_DIGEST_MISMATCH: response action_payload_digest_sha256 does not match recomputed payload digest"
            )

        # Point 9: Project UID match
        if (
            response.target_project_uid != request.target_project_uid
            or response.target_project_uid != request.signed_authorization.target_project_uid
        ):
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_PROJECT_UID_MISMATCH: expected {request.target_project_uid!r}, got {response.target_project_uid!r}"
            )

        # Point 10: Timeline UID match
        if (
            response.target_timeline_uid != request.target_timeline_uid
            or response.target_timeline_uid != request.signed_authorization.target_timeline_uid
        ):
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_TIMELINE_UID_MISMATCH: expected {request.target_timeline_uid!r}, got {response.target_timeline_uid!r}"
            )

        # Point 11: UTC Freshness and bounded validity window
        verified_at_dt = parse_iso_utc_timestamp(response.verified_at_iso, "verified_at_iso")
        valid_until_dt = parse_iso_utc_timestamp(response.valid_until_iso, "valid_until_iso")

        # Must have explicit zero-offset UTC tzinfo
        if verified_at_dt.tzinfo != timezone.utc or valid_until_dt.tzinfo != timezone.utc:
            raise DispatcherOracleVerificationError("ERR_ORACLE_TIMESTAMP_NOT_UTC: timestamps must be explicit UTC")

        # Re-sample current time to evaluate response freshness accurately
        eval_now_dt = self._clock()
        if eval_now_dt.tzinfo is None:
            eval_now_dt = eval_now_dt.replace(tzinfo=timezone.utc)

        age_seconds = (eval_now_dt - verified_at_dt).total_seconds()
        # Allow up to 1.0s clock skew tolerance for microsecond execution delays
        if age_seconds < -1.0:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_VERIFIED_AT_IN_FUTURE: verified_at_iso is {abs(age_seconds):.2f}s in the future"
            )
        if age_seconds > 60.0:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_VERIFIED_AT_STALE: verified_at_iso is stale ({age_seconds:.2f}s old > 60s)"
            )
        if eval_now_dt > valid_until_dt:
            raise DispatcherOracleVerificationError(
                "ERR_ORACLE_RESPONSE_EXPIRED: now_utc is past valid_until_iso"
            )

        validity_window = (valid_until_dt - verified_at_dt).total_seconds()
        if validity_window < 0.0 or validity_window > 60.0:
            raise DispatcherOracleVerificationError(
                f"ERR_ORACLE_VALIDITY_WINDOW_INVALID: validity window is {validity_window:.2f}s (must be 0-60s)"
            )

    def dispatch_canary_action(
        self,
        action: PlannedTimelineAction,
        authorization: TrialExecutionAuthorization,
        plan: TimelineExecutionPlan,
    ) -> CanaryDispatchResult:
        """Gated execution of the single authorized canary action.

        Enforces mock tripwire, target blacklist, bounded single canary scope,
        recomputed canonical digests, injected oracle verification with 11-point
        acceptance invariant, atomic single dispatch reservation, and truthful
        mock-only receipt persistence.
        """
        # Step 1: Pre-admission mock tripwire validation
        self._validate_adapter_tripwire(self._adapter)

        # Step 2: Strict nominal type validation
        if type(action) is not PlannedTimelineAction:
            raise DispatcherScopeViolationError(
                f"ERR_INVALID_ACTION_TYPE: action must be exact PlannedTimelineAction, got {type(action).__name__}"
            )
        if type(authorization) is not TrialExecutionAuthorization:
            raise DispatcherScopeViolationError(
                f"ERR_INVALID_AUTH_TYPE: authorization must be exact TrialExecutionAuthorization, got {type(authorization).__name__}"
            )
        if type(plan) is not TimelineExecutionPlan:
            raise DispatcherScopeViolationError(
                f"ERR_INVALID_PLAN_TYPE: plan must be exact TimelineExecutionPlan, got {type(plan).__name__}"
            )

        now_dt = self._clock()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        now_iso = now_dt.isoformat().replace("+00:00", "Z")

        # Step 3: Target blacklist enforcement
        self._enforce_target_blacklist(
            project_name=authorization.target_project_name,
            project_uid=authorization.target_project_uid,
            timeline_name=authorization.target_timeline_name,
            timeline_uid=authorization.target_timeline_uid,
        )

        # Step 4: Single canary scope and authoritative plan enforcement
        self._enforce_scope_boundary(action, authorization, plan=plan)

        # Step 5: Check one-call maximum before expensive crypto operations
        with self._lock:
            if authorization.execution_id in self._dispatched_execution_ids:
                raise DispatcherDuplicateDispatchError(
                    f"ERR_DUPLICATE_EXECUTION_DISPATCH: execution_id '{authorization.execution_id}' has already been dispatched"
                )

        # Step 6: Recompute canonical authorization digest & action payload digest
        auth_bytes = canonical_bytes_for_auth_hmac(authorization)
        computed_auth_digest = hashlib.sha256(auth_bytes).hexdigest()

        payload = assemble_canonical_action_payload(
            action=action, auth=authorization, execution_id=authorization.execution_id
        )
        payload_bytes = canonical_action_payload_bytes(payload)
        recomputed_action_payload_digest = hashlib.sha256(payload_bytes).hexdigest()

        if not hmac.compare_digest(
            authorization.action_payload_digest_sha256, recomputed_action_payload_digest
        ):
            raise DispatcherScopeViolationError(
                "ERR_PAYLOAD_DIGEST_MISMATCH: authorization.action_payload_digest_sha256 != recomputed payload digest"
            )

        # Step 7: Construct OracleVerificationRequest
        request_id = str(uuid.uuid4())
        request_nonce = secrets.token_hex(32)

        request = OracleVerificationRequest(
            request_id=request_id,
            request_nonce=request_nonce,
            requested_at_iso=now_iso,
            signed_authorization=authorization,
            action_payload_digest_sha256=recomputed_action_payload_digest,
            target_project_uid=authorization.target_project_uid,
            target_timeline_uid=authorization.target_timeline_uid,
        )

        with self._lock:
            if (
                request.request_id in self._dispatched_request_ids
                or request.request_nonce in self._dispatched_request_nonces
            ):
                raise DispatcherDuplicateDispatchError("ERR_REPLAYED_ORACLE_REQUEST_IDENTITY")

        # Step 8: Call injected in-process test oracle client
        response = self._oracle_client.verify_authorization(request)
        if type(response) is not OracleVerificationResponse:
            raise DispatcherOracleVerificationError(
                f"ERR_INVALID_ORACLE_RESPONSE_TYPE: expected OracleVerificationResponse, got {type(response).__name__}"
            )

        # Step 9: Enforce 11-point acceptance invariant (zero adapter/receipt calls on failure)
        self._verify_oracle_response_11_points(
            request=request,
            response=response,
            computed_auth_digest=computed_auth_digest,
            recomputed_action_payload_digest=recomputed_action_payload_digest,
            now_dt=now_dt,
        )

        # Step 10: Atomic single dispatch reservation
        with self._lock:
            if authorization.execution_id in self._dispatched_execution_ids:
                raise DispatcherDuplicateDispatchError(
                    f"ERR_DUPLICATE_EXECUTION_DISPATCH: execution_id '{authorization.execution_id}' raced and already dispatched"
                )
            if request.request_id in self._dispatched_request_ids:
                raise DispatcherDuplicateDispatchError("ERR_REPLAYED_ORACLE_REQUEST_ID")
            if request.request_nonce in self._dispatched_request_nonces:
                raise DispatcherDuplicateDispatchError("ERR_REPLAYED_ORACLE_REQUEST_NONCE")

            self._dispatched_execution_ids.add(authorization.execution_id)
            self._dispatched_request_ids.add(request.request_id)
            self._dispatched_request_nonces.add(request.request_nonce)

        # Step 11: Persist initial receipt record if receipt_service configured
        receipt_obj: Optional[ExecutionReceipt] = None
        if self._receipt_service is not None:
            try:
                receipt_obj = ExecutionReceipt(
                    execution_id=authorization.execution_id,
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
                    last_error_code="MOCK_ONLY",
                    last_error_message="LOCAL_TEST_MOCK_GATEWAY_NOT_TERMINAL",
                )
                self._receipt_service.create_receipt(receipt_obj)
            except Exception as rcpt_init_exc:
                # Pre-dispatch receipt initialization failed: fail closed before adapter call
                raise DispatcherReceiptPersistenceError(
                    f"ERR_PRE_DISPATCH_RECEIPT_CREATION_FAILED: {rcpt_init_exc}"
                ) from rcpt_init_exc

        # Step 12: Pre-dispatch tripwire re-check immediately before execution call
        self._validate_adapter_tripwire(self._adapter)

        # Step 13: Execute isolated action through accepted I4 mock adapter
        try:
            adapter_res = self._adapter.execute_isolated_action(action, authorization)
        except Exception as dispatch_exc:
            # Post-dispatch adapter error: preserve in-doubt state without retry or lock release
            raise DispatcherDispatchError(
                f"ERR_DISPATCH_EXECUTION_FAILED: adapter dispatch failed: {dispatch_exc}"
            ) from dispatch_exc

        # Check adapter returned status
        adapter_status = adapter_res.get("status") if isinstance(adapter_res, dict) else None
        if adapter_status != "COMMITTED":
            raise DispatcherDispatchError(
                f"ERR_DISPATCH_EXECUTION_FAILED: adapter returned status {adapter_status!r}, lock preserved in-doubt"
            )

        # Step 14: Transition receipt to reflect mock dispatch completion (non-terminal)
        if self._receipt_service is not None:
            commit_now_dt = self._clock()
            if commit_now_dt.tzinfo is None:
                commit_now_dt = commit_now_dt.replace(tzinfo=timezone.utc)
            commit_now_iso = commit_now_dt.isoformat().replace("+00:00", "Z")

            try:
                self._receipt_service.transition_state(
                    execution_id=authorization.execution_id,
                    new_state="EXECUTING",
                    updated_at_iso=commit_now_iso,
                    last_error_code="MOCK_ONLY",
                    last_error_message="LOCAL_TEST_MOCK_DISPATCH_COMPLETED_NON_TERMINAL",
                )
            except Exception as rcpt_trans_exc:
                # Post-dispatch receipt update failed: state is in doubt
                raise DispatcherReceiptPersistenceError(
                    f"ERR_POST_DISPATCH_RECEIPT_TRANSITION_FAILED: {rcpt_trans_exc}"
                ) from rcpt_trans_exc

        # Step 15: Return truthful result DTO
        resolve_item_id = str(adapter_res.get("resolve_item_id", "mock_item_001"))
        return CanaryDispatchResult(
            status="COMMITTED",
            execution_id=authorization.execution_id,
            authorization_id=authorization.authorization_id,
            action_id=action.action_id,
            resolve_item_id=resolve_item_id,
            target_project_uid=authorization.target_project_uid,
            target_timeline_uid=authorization.target_timeline_uid,
            oracle_response_id=response.response_id,
            mode="MOCK_ONLY",
            terminalized=False,
            receipt_id=receipt_obj.execution_id if receipt_obj is not None else None,
            adapter_result=adapter_res,
        )
