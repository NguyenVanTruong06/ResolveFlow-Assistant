"""Dispatcher Admission Verifier Core (Workorder G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-VERIFIER-CORE-R1)

Pure deterministic in-memory core for validating the four nominal authorization
envelope DTOs, extracting their canonical signing bytes, verifying Ed25519
signatures using injected dependency public keys, and evaluating the accepted
semantic cross-binding matrix.

Safety Boundary & Governance Invariants:
- Purely in-memory, deterministic verification facts reporter.
- NEVER issues `ADMITTED` or `DispatcherAdmissionResponse(status="ADMITTED")`.
- NEVER consumes replay identifiers or writes to any ledger or journal.
- NEVER reads or accesses host trust anchors, private keys, `/etc/resolveflow`,
  `/var/lib/resolveflow`, `/run/resolveflow`, sockets, DaVinci Resolve, or UI.
- Injected key mappings are explicitly labeled as test/dependency inputs only,
  NOT trusted production roots.
- Fails closed when required dependencies, key mappings, or operator challenge/replay
  controls are unresolved.
- `operational_admission_permitted` is permanently False.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.core.contracts.dispatcher_admission_contracts import (
    DispatcherAdmissionContractError,
    DispatcherAdmissionRequest,
    RE_CANONICAL_UUID_V4,
)
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
from src.core.contracts.signed_operator_consent_envelope import SignedOperatorConsentEnvelope
from src.core.contracts.signed_preflight_contracts import (
    PINNED_TARGET_PROJECT_NAME as DEFAULT_PINNED_PROJECT_NAME,
    PINNED_TARGET_PROJECT_UID as DEFAULT_PINNED_PROJECT_UID,
    PINNED_TARGET_TIMELINE_NAME as DEFAULT_PINNED_TIMELINE_NAME,
    PINNED_TARGET_TIMELINE_UID as DEFAULT_PINNED_TIMELINE_UID,
    SignedPreflightEvidenceEnvelope,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)

# Pinned Architectural Constants (B-3 Design §3.3)
CANARY_TARGET_PROJECT_UID: str = DEFAULT_PINNED_PROJECT_UID
CANARY_TARGET_TIMELINE_UID: str = DEFAULT_PINNED_TIMELINE_UID
CANARY_TARGET_PROJECT_NAME: str = DEFAULT_PINNED_PROJECT_NAME
CANARY_TARGET_TIMELINE_NAME: str = DEFAULT_PINNED_TIMELINE_NAME
CANARY_PERMITTED_COMMAND: str = "canary-trial-execute"

MAX_PREFLIGHT_VALIDITY_SECONDS: float = 1800.0
GATE4_FUTURE_SKEW_TOLERANCE_SECONDS: float = 5.0

MAX_WORKORDER_VALIDITY_SECONDS: float = 3600.0
GATE5_FUTURE_SKEW_TOLERANCE_SECONDS: float = 30.0

RE_LOWER_HEX_64: re.Pattern = re.compile(r"^[0-9a-f]{64}$")
RE_LOWER_HEX_128: re.Pattern = re.compile(r"^[0-9a-f]{128}$")

# Unresolved Operational & Governance Prerequisite Identifiers (Task Directive §3.6)
UNRESOLVED_OPERATOR_CHALLENGE: str = "OPERATOR_CHALLENGE_LIFECYCLE_UNRESOLVED"
UNRESOLVED_OPERATOR_NONCE: str = "OPERATOR_NONCE_REPLAY_UNRESOLVED"
UNRESOLVED_OPERATOR_SESSION_PROOF: str = "OPERATOR_ACTIVE_SESSION_PROOF_UNRESOLVED"
UNRESOLVED_OPERATOR_TRUST_SOURCE: str = "OPERATOR_TRUST_SOURCE_UNRESOLVED"
UNRESOLVED_GATE4_CUSTODY: str = "GATE4_CUSTODY_UNRESOLVED"
UNRESOLVED_GATE5_TRUST_ANCHOR: str = "GATE5_HOST_TRUST_ANCHOR_UNRESOLVED"
UNRESOLVED_REVOCATION_STATUS: str = "REVOCATION_STATUS_UNRESOLVED"
UNRESOLVED_PROCESS_IDENTITY: str = "CANDIDATE_PROCESS_IDENTITY_UNRESOLVED"
UNRESOLVED_B2_ATTESTATION: str = "B2_SAME_UID_ATTESTATION_UNRESOLVED"

ALL_UNRESOLVED_PREREQUISITES: Tuple[str, ...] = (
    UNRESOLVED_OPERATOR_CHALLENGE,
    UNRESOLVED_OPERATOR_NONCE,
    UNRESOLVED_OPERATOR_SESSION_PROOF,
    UNRESOLVED_OPERATOR_TRUST_SOURCE,
    UNRESOLVED_GATE4_CUSTODY,
    UNRESOLVED_GATE5_TRUST_ANCHOR,
    UNRESOLVED_REVOCATION_STATUS,
    UNRESOLVED_PROCESS_IDENTITY,
    UNRESOLVED_B2_ATTESTATION,
)

# Standard Failure Codes
ERR_TYPE_MISMATCH: str = "ERR_TYPE_MISMATCH"
ERR_KEY_MAPPING_MISSING: str = "ERR_KEY_MAPPING_MISSING"
ERR_KEY_MAPPING_AMBIGUOUS: str = "ERR_KEY_MAPPING_AMBIGUOUS"
ERR_KEY_MAPPING_INVALID: str = "ERR_KEY_MAPPING_INVALID"
ERR_GATE5_KEY_SEPARATION_VIOLATION: str = "ERR_GATE5_KEY_SEPARATION_VIOLATION"
ERR_GATE2_DIRECTOR_SIGNATURE_INVALID: str = "ERR_GATE2_SIGNATURE_INVALID"
ERR_GATE2_OPERATOR_SIGNATURE_INVALID: str = "ERR_GATE2_SIGNATURE_INVALID"
ERR_GATE4_SIGNATURE_INVALID: str = "ERR_GATE4_SIGNATURE_INVALID"
ERR_GATE5_SIGNATURE_INVALID: str = "ERR_GATE5_SIGNATURE_INVALID"
ERR_GATE5_TARGET_MISMATCH: str = "ERR_GATE5_TARGET_MISMATCH"
ERR_GATE5_PLAN_DIGEST_MISMATCH: str = "ERR_GATE5_PLAN_DIGEST_MISMATCH"
ERR_GATE5_ACTION_DIGEST_MISMATCH: str = "ERR_GATE5_ACTION_DIGEST_MISMATCH"
ERR_GATE5_APPROVAL_BINDING_MISMATCH: str = "ERR_GATE5_APPROVAL_BINDING_MISMATCH"
ERR_GATE5_CANDIDATE_HASH_MISMATCH: str = "ERR_GATE5_CANDIDATE_HASH_MISMATCH"
ERR_GATE5_COMMAND_UNAUTHORIZED: str = "ERR_GATE5_COMMAND_UNAUTHORIZED"
ERR_GATE5_EXECUTION_ID_INVALID: str = "ERR_GATE5_EXECUTION_ID_INVALID"
ERR_GATE4_PREDICATE_UNSATISFIED: str = "ERR_GATE4_PREDICATE_UNSATISFIED"
ERR_GATE4_EVIDENCE_EXPIRED: str = "ERR_GATE4_EVIDENCE_EXPIRED"
ERR_GATE4_EVIDENCE_FUTURE: str = "ERR_GATE4_EVIDENCE_FUTURE"
ERR_GATE5_WORKORDER_EXPIRED: str = "ERR_GATE5_WORKORDER_EXPIRED"
ERR_GATE5_WORKORDER_FUTURE: str = "ERR_GATE5_WORKORDER_FUTURE"
ERR_OPERATOR_CHALLENGE_UNRESOLVED: str = "ERR_OPERATOR_CHALLENGE_UNRESOLVED"
ERR_OPERATOR_NONCE_UNRESOLVED: str = "ERR_OPERATOR_NONCE_UNRESOLVED"
ERR_GATE4_CUSTODY_UNRESOLVED: str = "ERR_GATE4_CUSTODY_UNRESOLVED"
ERR_PROCESS_ATTESTATION_UNRESOLVED: str = "ERR_PROCESS_ATTESTATION_UNRESOLVED"


class DispatcherAdmissionVerifierError(AcceptanceGateError, ValueError):
    """Raised when admission verification encounters an unrecoverable structural or contract error."""

    def __init__(self, code: str, detail: Optional[str] = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail or ""


class SignatureVerifierProtocol(Protocol):
    """Protocol for injected Ed25519 signature verification dependencies."""

    def __call__(self, public_key_bytes: bytes, message: bytes, signature_bytes: bytes) -> bool:
        ...


def pure_ed25519_verify(public_key_bytes: bytes, message: bytes, signature_bytes: bytes) -> bool:
    """Pure Ed25519 signature verification using public key bytes only (RFC 8032).

    Fails closed on any parse error, length violation, or invalid signature.
    """
    if not isinstance(public_key_bytes, (bytes, bytearray)) or len(public_key_bytes) != 32:
        return False
    if not isinstance(signature_bytes, (bytes, bytearray)) or len(signature_bytes) != 64:
        return False
    if not isinstance(message, (bytes, bytearray)):
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes(public_key_bytes))
        key.verify(bytes(signature_bytes), bytes(message))
        return True
    except (InvalidSignature, ValueError, Exception):
        return False


def deterministic_test_verifier(public_key_bytes: bytes, message: bytes, signature_bytes: bytes) -> bool:
    """Deterministic fake verifier for unit testing without key generation.

    Accepts a signature if signature_bytes matches SHA512(public_key_bytes + message).
    """
    if not isinstance(public_key_bytes, (bytes, bytearray)) or not isinstance(signature_bytes, (bytes, bytearray)):
        return False
    expected = hashlib.sha512(bytes(public_key_bytes) + bytes(message)).digest()
    return bytes(signature_bytes) == expected


def normalize_ed25519_public_key_bytes(raw: Union[bytes, bytearray]) -> bytes:
    """Normalizes raw 32-byte Ed25519 public key or SPKI (PEM/DER) to 32 raw public bytes.

    Raises DispatcherAdmissionVerifierError on invalid length, unknown format, or non-Ed25519 key.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise DispatcherAdmissionVerifierError(
            ERR_KEY_MAPPING_INVALID, f"Public key must be bytes, got {type(raw).__name__}"
        )
    b = bytes(raw)
    if len(b) == 32:
        return b
    if b.startswith(b"-----BEGIN PUBLIC KEY-----") or b.startswith(b"-----BEGIN CERTIFICATE-----"):
        try:
            pub = serialization.load_pem_public_key(b)
            if isinstance(pub, Ed25519PublicKey):
                return pub.public_bytes_raw()
        except Exception as exc:
            raise DispatcherAdmissionVerifierError(
                ERR_KEY_MAPPING_INVALID, f"Failed to parse PEM public key: {exc}"
            ) from exc
    try:
        pub = serialization.load_der_public_key(b)
        if isinstance(pub, Ed25519PublicKey):
            return pub.public_bytes_raw()
    except Exception:
        pass
    raise DispatcherAdmissionVerifierError(
        ERR_KEY_MAPPING_INVALID,
        f"Public key must be 32 raw bytes or Ed25519 SPKI, got {len(b)} bytes",
    )


@dataclass(frozen=True)
class InjectedKeyMapping:
    """Explicit container for dependency/test public keys.

    SAFETY NOTICE:
    These keys are injected test/dependency inputs only. They are NOT trusted production
    roots, host trust anchors, or authoritative identity certificates.
    Production trust-anchor provisioning remains an unresolved prerequisite.
    """

    director_public_key_bytes: bytes
    operator_public_key_bytes: bytes
    verifier_public_key_bytes: bytes
    tech_lead_public_key_bytes: bytes
    oracle_public_key_bytes: Optional[bytes] = None
    trial_signer_public_key_bytes: Optional[bytes] = None
    key_id_map: Optional[Mapping[str, bytes]] = None

    def __post_init__(self) -> None:
        # Validate that all required role keys are bytes
        for role, b in (
            ("director", self.director_public_key_bytes),
            ("operator", self.operator_public_key_bytes),
            ("verifier", self.verifier_public_key_bytes),
            ("tech_lead", self.tech_lead_public_key_bytes),
        ):
            if not isinstance(b, (bytes, bytearray)):
                raise DispatcherAdmissionVerifierError(
                    ERR_KEY_MAPPING_INVALID,
                    f"Public key for role {role} must be bytes, got {type(b).__name__}",
                )

        # Build list of normalized public key bytes for pairwise uniqueness check
        norm_keys: List[Tuple[str, bytes]] = [
            ("director", normalize_ed25519_public_key_bytes(self.director_public_key_bytes)),
            ("operator", normalize_ed25519_public_key_bytes(self.operator_public_key_bytes)),
            ("verifier", normalize_ed25519_public_key_bytes(self.verifier_public_key_bytes)),
            ("tech_lead", normalize_ed25519_public_key_bytes(self.tech_lead_public_key_bytes)),
        ]
        if self.oracle_public_key_bytes is not None:
            norm_keys.append(("oracle", normalize_ed25519_public_key_bytes(self.oracle_public_key_bytes)))
        if self.trial_signer_public_key_bytes is not None:
            norm_keys.append(("trial_signer", normalize_ed25519_public_key_bytes(self.trial_signer_public_key_bytes)))

        # Enforce pairwise key separation across all provided roles
        seen_keys: Dict[bytes, str] = {}
        for role_name, key_bytes in norm_keys:
            if key_bytes in seen_keys:
                raise DispatcherAdmissionVerifierError(
                    ERR_GATE5_KEY_SEPARATION_VIOLATION,
                    f"Key separation violation: role '{role_name}' shares public key bytes with role '{seen_keys[key_bytes]}'",
                )
            seen_keys[key_bytes] = role_name

        if self.key_id_map is not None:
            if not isinstance(self.key_id_map, Mapping):
                raise DispatcherAdmissionVerifierError(
                    ERR_KEY_MAPPING_INVALID, "key_id_map must be a Mapping"
                )
            # Verify no ambiguous empty or non-string key_ids
            for kid, kbytes in self.key_id_map.items():
                if not isinstance(kid, str) or not kid.strip() or kid != kid.strip():
                    raise DispatcherAdmissionVerifierError(
                        ERR_KEY_MAPPING_AMBIGUOUS, f"Invalid or ambiguous key_id in key_id_map: {kid!r}"
                    )
                if not isinstance(kbytes, (bytes, bytearray)):
                    raise DispatcherAdmissionVerifierError(
                        ERR_KEY_MAPPING_INVALID, f"Key bytes for {kid} must be bytes, got {type(kbytes).__name__}"
                    )


@dataclass(frozen=True)
class AdmissionVerificationFacts:
    """Pure factual verification report for an admission request.

    SAFETY INVARIANTS:
    - Reports only deterministic verification facts and cryptographic status.
    - NEVER issues ADMITTED or DispatcherAdmissionResponse.
    - NEVER consumes replay identifiers or writes to any ledger.
    - NEVER connects to or controls Resolve.
    - operational_admission_permitted is permanently False.
    """

    is_valid: bool
    status: str
    error_code: Optional[str]
    error_detail: Optional[str]
    verified_claims: Mapping[str, Any]
    unresolved_prerequisites: Tuple[str, ...]
    operational_admission_permitted: bool = False

    def __post_init__(self) -> None:
        if self.operational_admission_permitted is not False:
            raise DispatcherAdmissionVerifierError(
                "ERR_SAFETY_INVARIANT_VIOLATED",
                "operational_admission_permitted must permanently be False in verifier core",
            )


class DispatcherAdmissionVerifierCore:
    """Pure deterministic in-memory admission verifier core.

    Evaluates the four nominal authorization envelope DTOs against injected dependency
    public keys and the accepted semantic cross-binding matrix.
    """

    def __init__(
        self,
        key_mapping: Union[InjectedKeyMapping, Mapping[str, bytes]],
        verifier: Optional[SignatureVerifierProtocol] = None,
    ) -> None:
        if isinstance(key_mapping, InjectedKeyMapping):
            self._key_mapping = key_mapping
        elif isinstance(key_mapping, Mapping):
            self._key_mapping = self._build_injected_mapping_from_dict(key_mapping)
        else:
            raise DispatcherAdmissionVerifierError(
                ERR_KEY_MAPPING_INVALID,
                f"key_mapping must be InjectedKeyMapping or Mapping[str, bytes], got {type(key_mapping).__name__}",
            )
        self._verifier: SignatureVerifierProtocol = verifier if verifier is not None else pure_ed25519_verify

    @staticmethod
    def _build_injected_mapping_from_dict(d: Mapping[str, bytes]) -> InjectedKeyMapping:
        """Constructs an InjectedKeyMapping from a dictionary of key_id or role -> public_key_bytes."""
        if not isinstance(d, Mapping):
            raise DispatcherAdmissionVerifierError(ERR_KEY_MAPPING_INVALID, "Expected mapping for key_mapping")

        # Check for case collisions or whitespace ambiguity in keys
        clean_keys: Dict[str, bytes] = {}
        for k, v in d.items():
            if not isinstance(k, str) or not k.strip() or k != k.strip():
                raise DispatcherAdmissionVerifierError(
                    ERR_KEY_MAPPING_AMBIGUOUS, f"Ambiguous key identifier: {k!r}"
                )
            low_k = k.lower()
            if low_k in clean_keys and clean_keys[low_k] != v:
                raise DispatcherAdmissionVerifierError(
                    ERR_KEY_MAPPING_AMBIGUOUS, f"Ambiguous key mapping conflict for key: {k!r}"
                )
            clean_keys[low_k] = v

        def _get_key(candidates: Sequence[str], role: str) -> bytes:
            found: Optional[bytes] = None
            for c in candidates:
                if c in d:
                    if found is not None and found != d[c]:
                        raise DispatcherAdmissionVerifierError(
                            ERR_KEY_MAPPING_AMBIGUOUS, f"Conflicting keys mapped for role {role}"
                        )
                    found = d[c]
                elif c.lower() in clean_keys:
                    if found is not None and found != clean_keys[c.lower()]:
                        raise DispatcherAdmissionVerifierError(
                            ERR_KEY_MAPPING_AMBIGUOUS, f"Conflicting keys mapped for role {role}"
                        )
                    found = clean_keys[c.lower()]
            if found is None:
                raise DispatcherAdmissionVerifierError(
                    ERR_KEY_MAPPING_MISSING, f"Missing required public key for role '{role}' (looked for: {candidates})"
                )
            return found

        dir_key = _get_key(["director", "director_public_key", "dir_key_2026_primary"], "director")
        op_key = _get_key(["operator", "operator_public_key", "op_key_2026_ed25519"], "operator")
        ver_key = _get_key(["verifier", "verifier_public_key", "key_verifier_2026_ed25519"], "verifier")
        tl_key = _get_key(["tech_lead", "tech_lead_public_key", "key_techlead_2026_ed25519"], "tech_lead")

        oracle_key = d.get("oracle") or d.get("oracle_public_key")
        trial_signer_key = d.get("trial_signer") or d.get("trial_signer_public_key")

        return InjectedKeyMapping(
            director_public_key_bytes=dir_key,
            operator_public_key_bytes=op_key,
            verifier_public_key_bytes=ver_key,
            tech_lead_public_key_bytes=tl_key,
            oracle_public_key_bytes=oracle_key,
            trial_signer_public_key_bytes=trial_signer_key,
            key_id_map=dict(d),
        )

    def verify(
        self,
        request: Any,
        current_time: Optional[datetime] = None,
        expected_candidate_sha256: Optional[str] = None,
        require_operational_readiness: bool = False,
    ) -> AdmissionVerificationFacts:
        """Verifies an admission request purely in memory, reporting verification facts.

        Args:
            request: Must be exact nominal type DispatcherAdmissionRequest.
            current_time: Optional timezone-aware UTC datetime for timestamp bounds. Defaults to now.
            expected_candidate_sha256: Optional expected candidate digest claim (no disk read).
            require_operational_readiness: If True, fails closed on unresolved prerequisites.

        Returns:
            AdmissionVerificationFacts containing verification status, claims, and unresolved prerequisites.
        """
        # 1. Exact Nominal Type Gates (Strict rejection of subclasses and foreign types)
        if type(request) is not DispatcherAdmissionRequest:
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"request must be exact type DispatcherAdmissionRequest, got {type(request).__name__}",
            )

        env1 = request.approval_envelope
        env2 = request.operator_consent_envelope
        env3 = request.preflight_evidence_envelope
        env4 = request.execution_workorder_record

        if type(env1) is not SignedApprovalEnvelope:
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"approval_envelope must be exact type SignedApprovalEnvelope, got {type(env1).__name__}",
            )
        if type(env2) is not SignedOperatorConsentEnvelope:
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"operator_consent_envelope must be exact type SignedOperatorConsentEnvelope, got {type(env2).__name__}",
            )
        if type(env3) is not SignedPreflightEvidenceEnvelope:
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"preflight_evidence_envelope must be exact type SignedPreflightEvidenceEnvelope, got {type(env3).__name__}",
            )
        if type(env4) is not SignedExecutionWorkorderRecord:
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"execution_workorder_record must be exact type SignedExecutionWorkorderRecord, got {type(env4).__name__}",
            )

        # 2. Immutable Independent Snapshot Reconstruction & Complete Contract Revalidation
        # Build one independent, strictly typed snapshot per envelope from the contract's complete
        # field set, re-run full contract validation on that snapshot without type coercions, and
        # derive both detached signatures and canonical signing/envelope bytes exclusively from that
        # same verified snapshot.
        try:
            # Top-level request validation
            if type(request.schema_version) is not int or request.schema_version != 1:
                return self._fail(
                    ERR_TYPE_MISMATCH,
                    f"schema_version must be exact int 1, got {request.schema_version!r}",
                )
            request_id = request.request_id
            if type(request_id) is not str or not RE_CANONICAL_UUID_V4.fullmatch(request_id):
                return self._fail(
                    ERR_TYPE_MISMATCH,
                    f"request_id must be canonical lowercase UUIDv4, got {request_id!r}",
                )

            # 2.1 Envelope 1 Snapshot Reconstruction & Strict Validation
            raw_e1 = env1.to_dict()
            if not isinstance(raw_e1, dict):
                return self._fail(ERR_TYPE_MISMATCH, "Envelope 1 to_dict must return dict")
            snapshot_e1 = SignedApprovalEnvelope.from_dict(raw_e1)

            # 2.2 Envelope 2 Snapshot Reconstruction & Strict Validation
            raw_e2 = env2.to_dict()
            if not isinstance(raw_e2, dict):
                return self._fail(ERR_TYPE_MISMATCH, "Envelope 2 to_dict must return dict")
            snapshot_e2 = SignedOperatorConsentEnvelope.from_dict(raw_e2)

            # 2.3 Envelope 3 Snapshot Reconstruction & Strict Validation
            raw_e3 = env3.to_dict()
            if not isinstance(raw_e3, dict):
                return self._fail(ERR_TYPE_MISMATCH, "Envelope 3 to_dict must return dict")
            snapshot_e3 = SignedPreflightEvidenceEnvelope.from_dict(raw_e3)

            # 2.4 Envelope 4 Snapshot Reconstruction & Strict Validation
            raw_e4 = env4.to_dict()
            if not isinstance(raw_e4, dict):
                return self._fail(ERR_TYPE_MISMATCH, "Envelope 4 to_dict must return dict")
            snapshot_e4 = SignedExecutionWorkorderRecord.from_dict(raw_e4)
        except Exception as exc:
            err_str = str(exc)
            if "ERR_PREDICATE_FALSE" in err_str:
                return self._fail(
                    ERR_GATE4_PREDICATE_UNSATISFIED,
                    f"Gate 4 technical predicate unsatisfied: {exc}",
                )
            return self._fail(
                ERR_TYPE_MISMATCH,
                f"Snapshot reconstruction and contract revalidation failed: {exc}",
            )

        # 2.5 Extract claims, signatures, and canonical bytes EXCLUSIVELY from the revalidated snapshots
        # Zero coercions: snapshot fields are strictly typed by contract from_dict / __post_init__.
        # Envelope 1 claims
        e1_key_id = snapshot_e1.director_key_id
        e1_plan_digest = snapshot_e1.plan_digest_sha256
        e1_proposal_digest = snapshot_e1.proposal_content_digest_sha256
        e1_signed_at_iso = snapshot_e1.signed_at_iso
        e1_sig_hex = snapshot_e1.director_signature
        e1_signing_bytes = snapshot_e1.canonical_signing_payload_bytes()
        e1_envelope_bytes = snapshot_e1.canonical_envelope_bytes()

        # Envelope 2 claims
        e2_key_id = snapshot_e2.operator_key_id
        e2_operator_id = snapshot_e2.operator_id
        e2_challenge_id = snapshot_e2.challenge_id
        e2_client_nonce = snapshot_e2.client_nonce
        e2_confirmation_phrase = snapshot_e2.confirmation_phrase
        e2_consent_timestamp_iso = snapshot_e2.consent_timestamp_iso
        e2_freshness_attestation = snapshot_e2.freshness_attestation
        e2_approval_envelope_digest = snapshot_e2.approval_envelope_digest_sha256
        e2_plan_digest = snapshot_e2.plan_digest_sha256
        e2_action_payload_digest = snapshot_e2.action_payload_digest_sha256
        e2_target_project_name = snapshot_e2.target_project_name
        e2_target_project_uid = snapshot_e2.target_project_uid
        e2_target_timeline_name = snapshot_e2.target_timeline_name
        e2_target_timeline_uid = snapshot_e2.target_timeline_uid
        e2_source_file_path = snapshot_e2.source_file_path
        e2_source_file_sha256 = snapshot_e2.source_file_sha256
        e2_action_type = snapshot_e2.action_type
        e2_track_type = snapshot_e2.track_type
        e2_track_index = snapshot_e2.track_index
        e2_record_frame = snapshot_e2.record_frame
        e2_duration_frames = snapshot_e2.duration_frames
        e2_sig_hex = snapshot_e2.operator_signature
        e2_signing_bytes = snapshot_e2.canonical_signing_payload_bytes()

        # Envelope 3 claims
        e3_run_id = snapshot_e3.preflight_run_id
        e3_verifier_id = snapshot_e3.verifier_id
        e3_verifier_key_id = snapshot_e3.verifier_key_id
        e3_target_project_name = snapshot_e3.target_project_name
        e3_target_project_uid = snapshot_e3.target_project_uid
        e3_target_timeline_name = snapshot_e3.target_timeline_name
        e3_target_timeline_uid = snapshot_e3.target_timeline_uid
        e3_project_and_timeline_active = snapshot_e3.project_and_timeline_active
        e3_empty_timeline_verified = snapshot_e3.empty_timeline_verified
        e3_topology_verified = snapshot_e3.topology_verified
        e3_incident_denylist_verified = snapshot_e3.incident_denylist_verified
        e3_media_pool_source_verified = snapshot_e3.media_pool_source_verified
        e3_source_file_sha256 = snapshot_e3.source_file_sha256
        e3_audit_chain_head_sha256 = snapshot_e3.audit_chain_head_sha256
        e3_verified_at_iso = snapshot_e3.verified_at_iso
        e3_valid_until_iso = snapshot_e3.valid_until_iso
        e3_sig_hex = snapshot_e3.verifier_signature
        e3_signing_bytes = snapshot_e3.canonical_signing_payload_bytes()

        # Envelope 4 claims
        e4_workorder_id = snapshot_e4.workorder_id
        e4_execution_id = snapshot_e4.execution_id
        e4_tech_lead_id = snapshot_e4.tech_lead_id
        e4_tech_lead_key_id = snapshot_e4.tech_lead_key_id
        e4_permitted_command = snapshot_e4.permitted_command
        e4_plan_digest = snapshot_e4.plan_digest_sha256
        e4_action_payload_digest = snapshot_e4.action_payload_digest_sha256
        e4_target_project_uid = snapshot_e4.target_project_uid
        e4_target_timeline_uid = snapshot_e4.target_timeline_uid
        e4_reviewed_candidate_sha256 = snapshot_e4.reviewed_candidate_sha256
        e4_issued_at_iso = snapshot_e4.issued_at_iso
        e4_valid_until_iso = snapshot_e4.valid_until_iso
        e4_sig_hex = snapshot_e4.tech_lead_signature
        e4_signing_bytes = snapshot_e4.canonical_signing_payload_bytes()

        # 3. Key-ID Pairwise Separation & Custody Non-Transitivity Invariant
        key_id_roles = {
            "director": e1_key_id,
            "operator": e2_key_id,
            "verifier": e3_verifier_key_id,
            "tech_lead": e4_tech_lead_key_id,
        }
        for r1, kid1 in key_id_roles.items():
            for r2, kid2 in key_id_roles.items():
                if r1 != r2 and kid1 == kid2:
                    return self._fail(
                        ERR_GATE5_KEY_SEPARATION_VIOLATION,
                        f"Pairwise key separation violation: role '{r1}' and role '{r2}' share key_id '{kid1}'",
                    )

        # 4. Injected Public Key Resolution for Each Role
        dir_pub = self._resolve_pubkey("director", e1_key_id, self._key_mapping.director_public_key_bytes)
        op_pub = self._resolve_pubkey("operator", e2_key_id, self._key_mapping.operator_public_key_bytes)
        ver_pub = self._resolve_pubkey("verifier", e3_verifier_key_id, self._key_mapping.verifier_public_key_bytes)
        tl_pub = self._resolve_pubkey("tech_lead", e4_tech_lead_key_id, self._key_mapping.tech_lead_public_key_bytes)

        if dir_pub is None or op_pub is None or ver_pub is None or tl_pub is None:
            return self._fail(ERR_KEY_MAPPING_MISSING, "Failed to resolve one or more required public keys")

        # 4.1 Pairwise Public Key Uniqueness and Custody Non-Transitivity
        resolved_pubs: Dict[str, bytes] = {
            "director": dir_pub,
            "operator": op_pub,
            "verifier": ver_pub,
            "tech_lead": tl_pub,
        }
        if self._key_mapping.oracle_public_key_bytes is not None:
            resolved_pubs["oracle"] = normalize_ed25519_public_key_bytes(self._key_mapping.oracle_public_key_bytes)
        if self._key_mapping.trial_signer_public_key_bytes is not None:
            resolved_pubs["trial_signer"] = normalize_ed25519_public_key_bytes(self._key_mapping.trial_signer_public_key_bytes)

        seen_resolved: Dict[bytes, str] = {}
        for role_name, pbytes in resolved_pubs.items():
            if pbytes in seen_resolved:
                return self._fail(
                    ERR_GATE5_KEY_SEPARATION_VIOLATION,
                    f"Resolved public key collision between role '{role_name}' and role '{seen_resolved[pbytes]}'",
                )
            seen_resolved[pbytes] = role_name

        # 5. Ed25519 Pure Signature Verification over Canonical Signing Payloads
        # Envelope 1: Director
        if not self._verify_sig(dir_pub, e1_signing_bytes, e1_sig_hex):
            return self._fail(
                ERR_GATE2_DIRECTOR_SIGNATURE_INVALID,
                f"Director signature verification failed for key_id '{e1_key_id}'",
            )
        # Envelope 2: Operator
        if not self._verify_sig(op_pub, e2_signing_bytes, e2_sig_hex):
            return self._fail(
                ERR_GATE2_OPERATOR_SIGNATURE_INVALID,
                f"Operator signature verification failed for key_id '{e2_key_id}'",
            )
        # Envelope 3: Preflight Verifier
        if not self._verify_sig(ver_pub, e3_signing_bytes, e3_sig_hex):
            return self._fail(
                ERR_GATE4_SIGNATURE_INVALID,
                f"Preflight verifier signature verification failed for key_id '{e3_verifier_key_id}'",
            )
        # Envelope 4: Tech Lead
        if not self._verify_sig(tl_pub, e4_signing_bytes, e4_sig_hex):
            return self._fail(
                ERR_GATE5_SIGNATURE_INVALID,
                f"Tech lead signature verification failed for key_id '{e4_tech_lead_key_id}'",
            )

        # 6. Semantic Cross-Envelope Binding Matrix Evaluation (§3.3)
        # 6.1 Pinned Target Project UID
        if not (e2_target_project_uid == e3_target_project_uid == e4_target_project_uid == CANARY_TARGET_PROJECT_UID):
            return self._fail(
                ERR_GATE5_TARGET_MISMATCH,
                f"Target project UID mismatch: env2={e2_target_project_uid}, env3={e3_target_project_uid}, "
                f"env4={e4_target_project_uid}, pinned={CANARY_TARGET_PROJECT_UID}",
            )

        # 6.2 Pinned Target Timeline UID
        if not (e2_target_timeline_uid == e3_target_timeline_uid == e4_target_timeline_uid == CANARY_TARGET_TIMELINE_UID):
            return self._fail(
                ERR_GATE5_TARGET_MISMATCH,
                f"Target timeline UID mismatch: env2={e2_target_timeline_uid}, env3={e3_target_timeline_uid}, "
                f"env4={e4_target_timeline_uid}, pinned={CANARY_TARGET_TIMELINE_UID}",
            )

        # 6.3 Pinned Target Project Name
        if not (e2_target_project_name == e3_target_project_name == CANARY_TARGET_PROJECT_NAME):
            return self._fail(
                ERR_GATE5_TARGET_MISMATCH,
                f"Target project name mismatch: env2={e2_target_project_name}, env3={e3_target_project_name}, "
                f"pinned={CANARY_TARGET_PROJECT_NAME}",
            )

        # 6.4 Pinned Target Timeline Name
        if not (e2_target_timeline_name == e3_target_timeline_name == CANARY_TARGET_TIMELINE_NAME):
            return self._fail(
                ERR_GATE5_TARGET_MISMATCH,
                f"Target timeline name mismatch: env2={e2_target_timeline_name}, env3={e3_target_timeline_name}, "
                f"pinned={CANARY_TARGET_TIMELINE_NAME}",
            )

        # 6.5 Plan Digest Cross-Binding (Envelopes 1, 2, 4)
        if not (e1_plan_digest == e2_plan_digest == e4_plan_digest):
            return self._fail(
                ERR_GATE5_PLAN_DIGEST_MISMATCH,
                f"Plan digest mismatch across envelopes: env1={e1_plan_digest}, env2={e2_plan_digest}, env4={e4_plan_digest}",
            )

        # 6.6 Action Payload Digest Cross-Binding (Envelopes 2, 4)
        if e2_action_payload_digest != e4_action_payload_digest:
            return self._fail(
                ERR_GATE5_ACTION_DIGEST_MISMATCH,
                f"Action payload digest mismatch: env2={e2_action_payload_digest}, env4={e4_action_payload_digest}",
            )

        # 6.7 Approval Envelope Binding (Envelope 1 -> Envelope 2)
        e1_canonical_hash = hashlib.sha256(e1_envelope_bytes).hexdigest()
        if e2_approval_envelope_digest != e1_canonical_hash:
            return self._fail(
                ERR_GATE5_APPROVAL_BINDING_MISMATCH,
                f"Approval envelope digest mismatch in operator consent: expected {e1_canonical_hash}, got {e2_approval_envelope_digest}",
            )

        # 6.8 Permitted Command Enforcement (Envelope 4)
        if e4_permitted_command != CANARY_PERMITTED_COMMAND:
            return self._fail(
                ERR_GATE5_COMMAND_UNAUTHORIZED,
                f"Permitted command unauthorized: expected {CANARY_PERMITTED_COMMAND!r}, got {e4_permitted_command!r}",
            )

        # 6.9 Candidate Source Digest Verification (As input claim only; no disk I/O)
        if expected_candidate_sha256 is not None:
            if not isinstance(expected_candidate_sha256, str) or not RE_LOWER_HEX_64.fullmatch(expected_candidate_sha256):
                return self._fail(
                    ERR_GATE5_CANDIDATE_HASH_MISMATCH,
                    f"expected_candidate_sha256 must be 64-char lowercase hex, got {expected_candidate_sha256!r}",
                )
            if e4_reviewed_candidate_sha256 != expected_candidate_sha256:
                return self._fail(
                    ERR_GATE5_CANDIDATE_HASH_MISMATCH,
                    f"Reviewed candidate sha256 mismatch: workorder={e4_reviewed_candidate_sha256}, claim={expected_candidate_sha256}",
                )

        # 6.10 Execution ID Format
        if not RE_CANONICAL_UUID_V4.fullmatch(e4_execution_id):
            return self._fail(
                ERR_GATE5_EXECUTION_ID_INVALID,
                f"execution_id must be canonical lowercase UUIDv4, got {e4_execution_id!r}",
            )

        # 6.11 Gate 4 Technical Predicates
        if not (
            e3_project_and_timeline_active is True
            and e3_empty_timeline_verified is True
            and e3_topology_verified is True
            and e3_incident_denylist_verified is True
            and e3_media_pool_source_verified is True
        ):
            return self._fail(
                ERR_GATE4_PREDICATE_UNSATISFIED,
                "One or more Gate 4 preflight technical predicates evaluated to False",
            )

        # 6.12 Timestamp Validity Bounds & Dispatcher Clock Skew
        tau = current_time if current_time is not None else datetime.now(timezone.utc)
        if tau.tzinfo is None:
            return self._fail(ERR_TYPE_MISMATCH, "current_time must be timezone-aware UTC datetime")

        # Gate 4 Evidence Window Check (5.0s future tolerance; 1800.0s max window)
        try:
            t3_verified = parse_iso_utc_timestamp(e3_verified_at_iso, "verified_at_iso")
            t3_until = parse_iso_utc_timestamp(e3_valid_until_iso, "valid_until_iso")
        except Exception as exc:
            return self._fail(ERR_GATE4_EVIDENCE_EXPIRED, f"Gate 4 timestamp parsing failed: {exc}")

        if t3_until <= t3_verified:
            return self._fail(
                ERR_GATE4_EVIDENCE_EXPIRED,
                f"Gate 4 valid_until ({e3_valid_until_iso}) is not after verified_at ({e3_verified_at_iso})",
            )
        g4_interval = (t3_until - t3_verified).total_seconds()
        if g4_interval > MAX_PREFLIGHT_VALIDITY_SECONDS:
            return self._fail(
                ERR_GATE4_EVIDENCE_EXPIRED,
                f"Gate 4 validity window {g4_interval}s exceeds maximum {MAX_PREFLIGHT_VALIDITY_SECONDS}s",
            )
        if tau.timestamp() < t3_verified.timestamp() - GATE4_FUTURE_SKEW_TOLERANCE_SECONDS:
            return self._fail(
                ERR_GATE4_EVIDENCE_FUTURE,
                f"Gate 4 evidence verified_at ({e3_verified_at_iso}) is in the future relative to dispatcher tau ({tau.isoformat()})",
            )
        if tau > t3_until:
            return self._fail(
                ERR_GATE4_EVIDENCE_EXPIRED,
                f"Gate 4 evidence expired at {e3_valid_until_iso} (dispatcher tau={tau.isoformat()})",
            )

        # Gate 5 Workorder Window Check (30.0s future tolerance; 3600.0s max window; zero grace after expiry)
        try:
            t4_issued = parse_iso_utc_timestamp(e4_issued_at_iso, "issued_at_iso")
            t4_until = parse_iso_utc_timestamp(e4_valid_until_iso, "valid_until_iso")
        except Exception as exc:
            return self._fail(ERR_GATE5_WORKORDER_EXPIRED, f"Gate 5 timestamp parsing failed: {exc}")

        if t4_until <= t4_issued:
            return self._fail(
                ERR_GATE5_WORKORDER_EXPIRED,
                f"Gate 5 valid_until ({e4_valid_until_iso}) is not after issued_at ({e4_issued_at_iso})",
            )
        g5_interval = (t4_until - t4_issued).total_seconds()
        if g5_interval > MAX_WORKORDER_VALIDITY_SECONDS:
            return self._fail(
                ERR_GATE5_WORKORDER_EXPIRED,
                f"Gate 5 validity window {g5_interval}s exceeds maximum {MAX_WORKORDER_VALIDITY_SECONDS}s",
            )
        if tau.timestamp() < t4_issued.timestamp() - GATE5_FUTURE_SKEW_TOLERANCE_SECONDS:
            return self._fail(
                ERR_GATE5_WORKORDER_FUTURE,
                f"Gate 5 workorder issued_at ({e4_issued_at_iso}) is in the future relative to dispatcher tau ({tau.isoformat()})",
            )
        if tau > t4_until:
            return self._fail(
                ERR_GATE5_WORKORDER_EXPIRED,
                f"Gate 5 workorder expired at {e4_valid_until_iso} (dispatcher tau={tau.isoformat()})",
            )

        # 7. Unresolved Operational Prerequisites Gate
        if require_operational_readiness:
            return self._fail(
                ERR_OPERATOR_CHALLENGE_UNRESOLVED,
                "Operational admission cannot be granted: operator challenge/session lifecycle, "
                "nonce replay verification, Gate 4 custody, host trust anchors, and process attestation "
                "are unresolved architectural prerequisites.",
            )

        # 8. Success: Signatures & Cross-Bindings Validated as In-Memory Facts
        facts = {
            "request_id": request_id,
            "workorder_id": e4_workorder_id,
            "execution_id": e4_execution_id,
            "preflight_run_id": e3_run_id,
            "target_project_uid": e4_target_project_uid,
            "target_timeline_uid": e4_target_timeline_uid,
            "target_project_name": e2_target_project_name,
            "target_timeline_name": e2_target_timeline_name,
            "plan_digest_sha256": e4_plan_digest,
            "action_payload_digest_sha256": e4_action_payload_digest,
            "reviewed_candidate_sha256": e4_reviewed_candidate_sha256,
            "permitted_command": e4_permitted_command,
            "director_key_id": e1_key_id,
            "operator_key_id": e2_key_id,
            "verifier_key_id": e3_verifier_key_id,
            "tech_lead_key_id": e4_tech_lead_key_id,
            "gate4_valid_until_iso": e3_valid_until_iso,
            "gate5_valid_until_iso": e4_valid_until_iso,
        }

        return AdmissionVerificationFacts(
            is_valid=True,
            status="SIGNATURES_AND_BINDINGS_VALID",
            error_code=None,
            error_detail=None,
            verified_claims=facts,
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=False,
        )

    def _resolve_pubkey(self, role: str, key_id: str, default_bytes: bytes) -> Optional[bytes]:
        """Resolves public key bytes for a given role and key_id fail-closed."""
        if self._key_mapping.key_id_map is not None:
            if key_id in self._key_mapping.key_id_map:
                candidate = self._key_mapping.key_id_map[key_id]
            elif role in self._key_mapping.key_id_map:
                candidate = self._key_mapping.key_id_map[role]
            else:
                return None
            try:
                return normalize_ed25519_public_key_bytes(candidate)
            except DispatcherAdmissionVerifierError:
                return None
        try:
            return normalize_ed25519_public_key_bytes(default_bytes)
        except DispatcherAdmissionVerifierError:
            return None

    def _verify_sig(self, public_key_bytes: bytes, message: bytes, signature_hex: str) -> bool:
        """Invokes injected signature verifier fail-closed."""
        if not isinstance(signature_hex, str) or not RE_LOWER_HEX_128.fullmatch(signature_hex):
            return False
        try:
            sig_bytes = bytes.fromhex(signature_hex)
        except ValueError:
            return False
        try:
            return bool(self._verifier(public_key_bytes, message, sig_bytes))
        except Exception:
            return False

    def _fail(self, code: str, detail: str) -> AdmissionVerificationFacts:
        """Constructs a deterministic fail-closed AdmissionVerificationFacts."""
        return AdmissionVerificationFacts(
            is_valid=False,
            status="VERIFICATION_FAILED",
            error_code=code,
            error_detail=detail,
            verified_claims={},
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=False,
        )


def verify_admission_request(
    request: Any,
    key_mapping: Union[InjectedKeyMapping, Mapping[str, bytes]],
    verifier: Optional[SignatureVerifierProtocol] = None,
    current_time: Optional[datetime] = None,
    expected_candidate_sha256: Optional[str] = None,
    require_operational_readiness: bool = False,
) -> AdmissionVerificationFacts:
    """Convenience functional wrapper for DispatcherAdmissionVerifierCore."""
    core = DispatcherAdmissionVerifierCore(key_mapping=key_mapping, verifier=verifier)
    return core.verify(
        request=request,
        current_time=current_time,
        expected_candidate_sha256=expected_candidate_sha256,
        require_operational_readiness=require_operational_readiness,
    )
