"""Focused unit tests for Dispatcher Admission Verifier Core.

Workorder: G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-VERIFIER-CORE-R1
Mode: LOCAL_CODE_AND_TEMP_UNIT_TESTS_ONLY; PURE_IN_MEMORY_VERIFICATION;
      NO_TRUST_ROOT_OR_KEY_FILE_ACCESS; NO_KEY_GENERATION_OR_SIGNING;
      NO_LEDGER_WRITES; NO_SOCKET_OR_NETWORK; NO_HOST_OR_SYSTEMD;
      NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE.

Safety Boundary:
- Pure unit tests using synthetic in-memory values only.
- ZERO key generation, ZERO private key storage, ZERO trust-anchor files.
- Uses public RFC 8032 test vectors and deterministic fake verifier.
- Strictly no socket, IPC, filesystem writes, daemon/listener, or Resolve SDK calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import socket
from typing import Any, Dict, Mapping, Optional, Tuple

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import pytest

from src.core.contracts.dispatcher_admission_contracts import (
    DispatcherAdmissionContractError,
    DispatcherAdmissionRequest,
)
from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_execution_workorder_contracts import SignedExecutionWorkorderRecord
from src.core.contracts.signed_operator_consent_envelope import SignedOperatorConsentEnvelope
from src.core.contracts.signed_preflight_contracts import SignedPreflightEvidenceEnvelope
from src.services.dispatcher_admission_verifier_core import (
    ALL_UNRESOLVED_PREREQUISITES,
    CANARY_PERMITTED_COMMAND,
    CANARY_TARGET_PROJECT_NAME,
    CANARY_TARGET_PROJECT_UID,
    CANARY_TARGET_TIMELINE_NAME,
    CANARY_TARGET_TIMELINE_UID,
    ERR_GATE2_DIRECTOR_SIGNATURE_INVALID,
    ERR_GATE2_OPERATOR_SIGNATURE_INVALID,
    ERR_GATE4_EVIDENCE_EXPIRED,
    ERR_GATE4_EVIDENCE_FUTURE,
    ERR_GATE4_PREDICATE_UNSATISFIED,
    ERR_GATE4_SIGNATURE_INVALID,
    ERR_GATE5_ACTION_DIGEST_MISMATCH,
    ERR_GATE5_APPROVAL_BINDING_MISMATCH,
    ERR_GATE5_CANDIDATE_HASH_MISMATCH,
    ERR_GATE5_COMMAND_UNAUTHORIZED,
    ERR_GATE5_EXECUTION_ID_INVALID,
    ERR_GATE5_KEY_SEPARATION_VIOLATION,
    ERR_GATE5_PLAN_DIGEST_MISMATCH,
    ERR_GATE5_SIGNATURE_INVALID,
    ERR_GATE5_TARGET_MISMATCH,
    ERR_GATE5_WORKORDER_EXPIRED,
    ERR_GATE5_WORKORDER_FUTURE,
    ERR_KEY_MAPPING_AMBIGUOUS,
    ERR_KEY_MAPPING_INVALID,
    ERR_KEY_MAPPING_MISSING,
    ERR_OPERATOR_CHALLENGE_UNRESOLVED,
    ERR_TYPE_MISMATCH,
    AdmissionVerificationFacts,
    DispatcherAdmissionVerifierCore,
    DispatcherAdmissionVerifierError,
    InjectedKeyMapping,
    deterministic_test_verifier,
    normalize_ed25519_public_key_bytes,
    pure_ed25519_verify,
    verify_admission_request,
)

# Test Public Keys (32-byte deterministic raw test bytes; NO private keys)
TEST_DIR_PUB: bytes = b"D" * 32
TEST_OP_PUB: bytes = b"O" * 32
TEST_VER_PUB: bytes = b"V" * 32
TEST_TL_PUB: bytes = b"T" * 32
TEST_ORACLE_PUB: bytes = b"K" * 32
TEST_TRIAL_SIGNER_PUB: bytes = b"S" * 32

PLAN_DIGEST: str = "1" * 64
ACTION_DIGEST: str = "2" * 64
CANDIDATE_DIGEST: str = "3" * 64
PROPOSAL_DIGEST: str = "4" * 64
SOURCE_SHA: str = "5" * 64
AUDIT_HEAD_SHA: str = "6" * 64

TAU_REFERENCE: datetime = datetime(2026, 11, 5, 8, 20, 0, tzinfo=timezone.utc)


def make_test_key_mapping(
    dir_pub: bytes = TEST_DIR_PUB,
    op_pub: bytes = TEST_OP_PUB,
    ver_pub: bytes = TEST_VER_PUB,
    tl_pub: bytes = TEST_TL_PUB,
    oracle_pub: Optional[bytes] = None,
    trial_signer_pub: Optional[bytes] = None,
    key_id_map: Optional[Mapping[str, bytes]] = None,
) -> InjectedKeyMapping:
    return InjectedKeyMapping(
        director_public_key_bytes=dir_pub,
        operator_public_key_bytes=op_pub,
        verifier_public_key_bytes=ver_pub,
        tech_lead_public_key_bytes=tl_pub,
        oracle_public_key_bytes=oracle_pub,
        trial_signer_public_key_bytes=trial_signer_pub,
        key_id_map=key_id_map,
    )


def build_synthetic_envelopes(
    dir_pub: bytes = TEST_DIR_PUB,
    op_pub: bytes = TEST_OP_PUB,
    ver_pub: bytes = TEST_VER_PUB,
    tl_pub: bytes = TEST_TL_PUB,
    plan_digest: str = PLAN_DIGEST,
    action_digest: str = ACTION_DIGEST,
    candidate_digest: str = CANDIDATE_DIGEST,
    permitted_command: str = CANARY_PERMITTED_COMMAND,
    target_project_uid: str = CANARY_TARGET_PROJECT_UID,
    target_timeline_uid: str = CANARY_TARGET_TIMELINE_UID,
    target_project_name: str = CANARY_TARGET_PROJECT_NAME,
    target_timeline_name: str = CANARY_TARGET_TIMELINE_NAME,
    e3_predicates: Tuple[bool, bool, bool, bool, bool] = (True, True, True, True, True),
    e3_verified_at_iso: str = "2026-11-05T08:15:00Z",
    e3_valid_until_iso: str = "2026-11-05T08:45:00Z",
    e4_issued_at_iso: str = "2026-11-05T08:00:00Z",
    e4_valid_until_iso: str = "2026-11-05T09:00:00Z",
    e1_key_id: str = "dir_key_1",
    e2_key_id: str = "op_key_1",
    e3_key_id: str = "ver_key_1",
    e4_key_id: str = "tl_key_1",
    e4_execution_id: str = "00000000-0000-4000-8000-000000000004",
) -> Tuple[
    SignedApprovalEnvelope,
    SignedOperatorConsentEnvelope,
    SignedPreflightEvidenceEnvelope,
    SignedExecutionWorkorderRecord,
]:
    # 1. Approval Envelope
    rec = AppOwnedApprovalRecord(
        approval_id="appr_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_id="prop_01j7b9c4d2e1f8a3b5c7d9e0f1",
        proposal_revision="a1" * 32,
        approved_at_iso="2026-11-05T08:00:00Z",
        accepted_omission_ids=("omit_1",),
        approval_scope="ENTIRE_PROPOSAL",
    )
    env1_initial = SignedApprovalEnvelope(
        schema_version=1,
        approval_record=rec,
        plan_digest_sha256=plan_digest,
        proposal_content_digest_sha256=PROPOSAL_DIGEST,
        director_key_id=e1_key_id,
        director_signature="0" * 128,
        signed_at_iso="2026-11-05T08:00:05Z",
    )
    sig1 = hashlib.sha512(dir_pub + env1_initial.canonical_signing_payload_bytes()).hexdigest()
    env1 = SignedApprovalEnvelope(
        schema_version=1,
        approval_record=rec,
        plan_digest_sha256=plan_digest,
        proposal_content_digest_sha256=PROPOSAL_DIGEST,
        director_key_id=e1_key_id,
        director_signature=sig1,
        signed_at_iso="2026-11-05T08:00:05Z",
    )
    env1_digest = hashlib.sha256(env1.canonical_envelope_bytes()).hexdigest()

    # 2. Operator Consent Envelope
    env2_initial = SignedOperatorConsentEnvelope(
        schema_version=1,
        operator_id="op_vanntruongg",
        challenge_id="00000000-0000-4000-8000-000000000001",
        client_nonce="00000000-0000-4000-8000-000000000002",
        confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
        consent_timestamp_iso="2026-11-05T08:10:00Z",
        freshness_attestation="OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
        approval_envelope_digest_sha256=env1_digest,
        plan_digest_sha256=plan_digest,
        action_payload_digest_sha256=action_digest,
        target_project_name=target_project_name,
        target_project_uid=target_project_uid,
        target_timeline_name=target_timeline_name,
        target_timeline_uid=target_timeline_uid,
        source_file_path="/var/media/resolveflow/canary_sample.mov",
        source_file_sha256=SOURCE_SHA,
        action_type="INSERT_CLIP",
        track_type="video",
        track_index=1,
        record_frame=86400,
        duration_frames=120,
        operator_key_id=e2_key_id,
        operator_signature="0" * 128,
    )
    sig2 = hashlib.sha512(op_pub + env2_initial.canonical_signing_payload_bytes()).hexdigest()
    env2 = SignedOperatorConsentEnvelope(
        schema_version=1,
        operator_id="op_vanntruongg",
        challenge_id="00000000-0000-4000-8000-000000000001",
        client_nonce="00000000-0000-4000-8000-000000000002",
        confirmation_phrase="CONFIRM_CANARY_TRIAL_EXECUTION",
        consent_timestamp_iso="2026-11-05T08:10:00Z",
        freshness_attestation="OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED",
        approval_envelope_digest_sha256=env1_digest,
        plan_digest_sha256=plan_digest,
        action_payload_digest_sha256=action_digest,
        target_project_name=target_project_name,
        target_project_uid=target_project_uid,
        target_timeline_name=target_timeline_name,
        target_timeline_uid=target_timeline_uid,
        source_file_path="/var/media/resolveflow/canary_sample.mov",
        source_file_sha256=SOURCE_SHA,
        action_type="INSERT_CLIP",
        track_type="video",
        track_index=1,
        record_frame=86400,
        duration_frames=120,
        operator_key_id=e2_key_id,
        operator_signature=sig2,
    )

    # 3. Preflight Evidence Envelope
    env3_initial = SignedPreflightEvidenceEnvelope(
        schema_version=1,
        preflight_run_id="00000000-0000-4000-8000-000000000003",
        verifier_id="verifier_host_daemon",
        verifier_key_id=e3_key_id,
        verified_at_iso=e3_verified_at_iso,
        valid_until_iso=e3_valid_until_iso,
        target_project_uid=target_project_uid,
        target_project_name=target_project_name,
        target_timeline_uid=target_timeline_uid,
        target_timeline_name=target_timeline_name,
        target_timeline_fps_numerator=24,
        target_timeline_fps_denominator=1,
        observed_start_frame=86400,
        project_and_timeline_active=e3_predicates[0],
        empty_timeline_verified=e3_predicates[1],
        topology_verified=e3_predicates[2],
        incident_denylist_verified=e3_predicates[3],
        media_pool_source_verified=e3_predicates[4],
        source_file_sha256=SOURCE_SHA,
        audit_chain_head_sha256=AUDIT_HEAD_SHA,
        verifier_signature="0" * 128,
    )
    sig3 = hashlib.sha512(ver_pub + env3_initial.canonical_signing_payload_bytes()).hexdigest()
    env3 = SignedPreflightEvidenceEnvelope(
        schema_version=1,
        preflight_run_id="00000000-0000-4000-8000-000000000003",
        verifier_id="verifier_host_daemon",
        verifier_key_id=e3_key_id,
        verified_at_iso=e3_verified_at_iso,
        valid_until_iso=e3_valid_until_iso,
        target_project_uid=target_project_uid,
        target_project_name=target_project_name,
        target_timeline_uid=target_timeline_uid,
        target_timeline_name=target_timeline_name,
        target_timeline_fps_numerator=24,
        target_timeline_fps_denominator=1,
        observed_start_frame=86400,
        project_and_timeline_active=e3_predicates[0],
        empty_timeline_verified=e3_predicates[1],
        topology_verified=e3_predicates[2],
        incident_denylist_verified=e3_predicates[3],
        media_pool_source_verified=e3_predicates[4],
        source_file_sha256=SOURCE_SHA,
        audit_chain_head_sha256=AUDIT_HEAD_SHA,
        verifier_signature=sig3,
    )

    # 4. Execution Workorder Record
    env4_initial = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R1",
        execution_id=e4_execution_id,
        tech_lead_id="tech_lead_codex",
        tech_lead_key_id=e4_key_id,
        permitted_command=permitted_command,
        plan_digest_sha256=plan_digest,
        action_payload_digest_sha256=action_digest,
        target_project_uid=target_project_uid,
        target_timeline_uid=target_timeline_uid,
        reviewed_candidate_sha256=candidate_digest,
        issued_at_iso=e4_issued_at_iso,
        valid_until_iso=e4_valid_until_iso,
        tech_lead_signature="0" * 128,
    )
    sig4 = hashlib.sha512(tl_pub + env4_initial.canonical_signing_payload_bytes()).hexdigest()
    env4 = SignedExecutionWorkorderRecord(
        schema_version=1,
        workorder_id="G07-07B7-GATE6-EXEC-R1",
        execution_id=e4_execution_id,
        tech_lead_id="tech_lead_codex",
        tech_lead_key_id=e4_key_id,
        permitted_command=permitted_command,
        plan_digest_sha256=plan_digest,
        action_payload_digest_sha256=action_digest,
        target_project_uid=target_project_uid,
        target_timeline_uid=target_timeline_uid,
        reviewed_candidate_sha256=candidate_digest,
        issued_at_iso=e4_issued_at_iso,
        valid_until_iso=e4_valid_until_iso,
        tech_lead_signature=sig4,
    )

    return env1, env2, env3, env4


def make_valid_request(**kwargs: Any) -> DispatcherAdmissionRequest:
    env1, env2, env3, env4 = build_synthetic_envelopes(**kwargs)
    return DispatcherAdmissionRequest(
        schema_version=1,
        request_id="00000000-0000-4000-8000-000000000000",
        approval_envelope=env1,
        operator_consent_envelope=env2,
        preflight_evidence_envelope=env3,
        execution_workorder_record=env4,
    )


# ==============================================================================
# 1. Exact Nominal Type Gates & Subclass Rejection Tests
# ==============================================================================


def test_nominal_type_gate_rejects_non_request_object() -> None:
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify({"schema_version": 1})
    assert res.is_valid is False
    assert res.error_code == ERR_TYPE_MISMATCH
    assert "DispatcherAdmissionRequest" in (res.error_detail or "")


def test_nominal_type_gate_rejects_subclassed_request() -> None:
    class SubclassedRequest(DispatcherAdmissionRequest):
        pass

    valid_req = make_valid_request()
    bad_req = SubclassedRequest(
        schema_version=valid_req.schema_version,
        request_id=valid_req.request_id,
        approval_envelope=valid_req.approval_envelope,
        operator_consent_envelope=valid_req.operator_consent_envelope,
        preflight_evidence_envelope=valid_req.preflight_evidence_envelope,
        execution_workorder_record=valid_req.execution_workorder_record,
    )

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(bad_req)
    assert res.is_valid is False
    assert res.error_code == ERR_TYPE_MISMATCH
    assert "SubclassedRequest" in (res.error_detail or "")


def test_nominal_type_gate_rejects_subclassed_envelopes() -> None:
    class SubclassedApprovalEnvelope(SignedApprovalEnvelope):
        pass

    valid_req = make_valid_request()
    sub_env1 = SubclassedApprovalEnvelope(
        schema_version=valid_req.approval_envelope.schema_version,
        approval_record=valid_req.approval_envelope.approval_record,
        plan_digest_sha256=valid_req.approval_envelope.plan_digest_sha256,
        proposal_content_digest_sha256=valid_req.approval_envelope.proposal_content_digest_sha256,
        director_key_id=valid_req.approval_envelope.director_key_id,
        director_signature=valid_req.approval_envelope.director_signature,
        signed_at_iso=valid_req.approval_envelope.signed_at_iso,
    )

    with pytest.raises(DispatcherAdmissionContractError) as exc_info:
        DispatcherAdmissionRequest(
            schema_version=1,
            request_id=valid_req.request_id,
            approval_envelope=sub_env1,  # type: ignore[arg-type]
            operator_consent_envelope=valid_req.operator_consent_envelope,
            preflight_evidence_envelope=valid_req.preflight_evidence_envelope,
            execution_workorder_record=valid_req.execution_workorder_record,
        )
    assert exc_info.value.code == "ERR_TYPE_MISMATCH"


def test_snapshot_resists_post_init_mutation() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    # Initial verification succeeds
    res1 = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res1.is_valid is True

    # Mutate the input object post-init via object.__setattr__
    object.__setattr__(req.execution_workorder_record, "permitted_command", "malicious_command")

    # Verifying the mutated object fails closed
    res2 = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res2.is_valid is False
    assert res2.error_code in (ERR_GATE5_COMMAND_UNAUTHORIZED, ERR_GATE5_SIGNATURE_INVALID)


def test_post_construction_mutation_invalid_field_type_fails_closed() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    # Mutate field to int instead of str
    object.__setattr__(req.execution_workorder_record, "permitted_command", 12345)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_TYPE_MISMATCH
    assert "revalidation failed" in (res.error_detail or "")


def test_post_construction_mutation_coercion_bypass_rejected() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    # Mutate schema_version to bool (True) which would coerce to int 1 if not strictly gated
    object.__setattr__(req.execution_workorder_record, "schema_version", True)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_TYPE_MISMATCH


def test_mutation_during_or_after_snapshot_is_fully_isolated() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()

    # Define a verifier callback that attempts to mutate the caller-owned req during signature check
    def mutating_verifier(pubkey: bytes, msg: bytes, sig_bytes: bytes) -> bool:
        # Malicious concurrent mutation on caller-owned DTO
        object.__setattr__(req.execution_workorder_record, "permitted_command", "corrupted_after_snapshot")
        object.__setattr__(req.approval_envelope, "plan_digest_sha256", "0" * 64)
        return deterministic_test_verifier(pubkey, msg, sig_bytes)

    verifier = DispatcherAdmissionVerifierCore(key_map, mutating_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE, expected_candidate_sha256=CANDIDATE_DIGEST)

    # The snapshot was taken before any signature verification, so verification succeeded
    # and the verified facts contain strictly the original snapshot claims, NOT the corrupted ones!
    assert res.is_valid is True
    assert res.verified_claims["permitted_command"] == CANARY_PERMITTED_COMMAND
    assert res.verified_claims["plan_digest_sha256"] == PLAN_DIGEST


def test_no_facts_reported_for_claims_not_covered_by_verified_signature() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    # Tamper with plan_digest on envelope 1
    object.__setattr__(req.approval_envelope, "plan_digest_sha256", "9" * 64)
    res = verifier.verify(req, current_time=TAU_REFERENCE)

    # Fails closed on signature verification
    assert res.is_valid is False
    # Crucial invariant: zero claims/facts are reported for unverified signatures
    assert res.verified_claims == {}
    assert res.status == "VERIFICATION_FAILED"


# ==============================================================================
# 2. Injected Public Key Dependency & Normalization Tests
# ==============================================================================


def test_injected_key_mapping_rejects_non_bytes() -> None:
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        InjectedKeyMapping(
            director_public_key_bytes="not_bytes",  # type: ignore[arg-type]
            operator_public_key_bytes=TEST_OP_PUB,
            verifier_public_key_bytes=TEST_VER_PUB,
            tech_lead_public_key_bytes=TEST_TL_PUB,
        )
    assert exc.value.code == ERR_KEY_MAPPING_INVALID


def test_missing_role_key_in_dict_mapping() -> None:
    bad_dict = {
        "director": TEST_DIR_PUB,
        "operator": TEST_OP_PUB,
        "verifier": TEST_VER_PUB,
        # tech_lead missing
    }
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        DispatcherAdmissionVerifierCore(bad_dict, deterministic_test_verifier)
    assert exc.value.code == ERR_KEY_MAPPING_MISSING


def test_ambiguous_whitespace_or_case_collision_in_dict_mapping() -> None:
    ambig_dict = {
        "director": TEST_DIR_PUB,
        "operator": TEST_OP_PUB,
        "verifier": TEST_VER_PUB,
        "tech_lead": TEST_TL_PUB,
        "Director": b"X" * 32,  # case collision with conflicting key
    }
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        DispatcherAdmissionVerifierCore(ambig_dict, deterministic_test_verifier)
    assert exc.value.code == ERR_KEY_MAPPING_AMBIGUOUS


def test_invalid_key_bytes_length() -> None:
    bad_key_dict = {
        "director": b"too_short",
        "operator": TEST_OP_PUB,
        "verifier": TEST_VER_PUB,
        "tech_lead": TEST_TL_PUB,
    }
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        DispatcherAdmissionVerifierCore(bad_key_dict, deterministic_test_verifier)
    assert exc.value.code == ERR_KEY_MAPPING_INVALID


def test_normalize_spki_pem_and_der_public_keys() -> None:
    raw32 = b"P" * 32
    pub = Ed25519PublicKey.from_public_bytes(raw32)
    pem_bytes = pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    der_bytes = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    assert normalize_ed25519_public_key_bytes(raw32) == raw32
    assert normalize_ed25519_public_key_bytes(pem_bytes) == raw32
    assert normalize_ed25519_public_key_bytes(der_bytes) == raw32


# ==============================================================================
# 3. Key Separation & Custody Non-Transitivity Tests
# ==============================================================================


def test_key_separation_rejects_identical_public_keys() -> None:
    # Verifier and Tech Lead share same public key
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        InjectedKeyMapping(
            director_public_key_bytes=TEST_DIR_PUB,
            operator_public_key_bytes=TEST_OP_PUB,
            verifier_public_key_bytes=TEST_VER_PUB,
            tech_lead_public_key_bytes=TEST_VER_PUB,  # COLLISION!
        )
    assert exc.value.code == ERR_GATE5_KEY_SEPARATION_VIOLATION


def test_key_separation_rejects_oracle_overlap() -> None:
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        InjectedKeyMapping(
            director_public_key_bytes=TEST_DIR_PUB,
            operator_public_key_bytes=TEST_OP_PUB,
            verifier_public_key_bytes=TEST_VER_PUB,
            tech_lead_public_key_bytes=TEST_TL_PUB,
            oracle_public_key_bytes=TEST_TL_PUB,  # COLLISION with tech_lead!
        )
    assert exc.value.code == ERR_GATE5_KEY_SEPARATION_VIOLATION


def test_key_separation_rejects_shared_key_id_across_envelopes() -> None:
    # Verifier and Tech Lead envelopes share key_id
    req = make_valid_request(e3_key_id="shared_custody_key", e4_key_id="shared_custody_key")
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE5_KEY_SEPARATION_VIOLATION
    assert "Pairwise key separation violation" in (res.error_detail or "")


# ==============================================================================
# 4. Pure Ed25519 & RFC 8032 Vector Verification Tests
# ==============================================================================


def test_pure_ed25519_verify_rfc8032_public_vector() -> None:
    # RFC 8032 Section 7.1 Test 1 (Public Key + Signature for empty message)
    # Using public bytes only; zero private key generated or loaded.
    rfc_pub = bytes.fromhex("c5d3aab64db11c26202854829c9ad7def3b24447cd21958bfdfed2b2f9ba7510")
    rfc_sig = bytes.fromhex(
        "d247c1a21ff3b2d5d255365ae5157e1190869cc4bc73fb3342702d15daf77b36"
        "99e133a36bad97d8fc95403848fb6c104cdc3fb6f0c0d09d3bdbd5a65f2d4902"
    )
    empty_msg = b""

    # Valid verification
    assert pure_ed25519_verify(rfc_pub, empty_msg, rfc_sig) is True

    # Corrupted message fails
    assert pure_ed25519_verify(rfc_pub, b"non-empty", rfc_sig) is False

    # Corrupted signature fails
    corrupt_sig = bytearray(rfc_sig)
    corrupt_sig[0] ^= 0xFF
    assert pure_ed25519_verify(rfc_pub, empty_msg, bytes(corrupt_sig)) is False

    # Corrupted pubkey fails
    corrupt_pub = bytearray(rfc_pub)
    corrupt_pub[0] ^= 0xFF
    assert pure_ed25519_verify(bytes(corrupt_pub), empty_msg, rfc_sig) is False


def test_signature_verification_rejects_corrupted_director_sig() -> None:
    req = make_valid_request()
    # Flip bytes in director signature
    corrupt_sig = "f" * 128
    object.__setattr__(req.approval_envelope, "director_signature", corrupt_sig)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE2_DIRECTOR_SIGNATURE_INVALID


def test_signature_verification_rejects_corrupted_operator_sig() -> None:
    req = make_valid_request()
    corrupt_sig = "f" * 128
    object.__setattr__(req.operator_consent_envelope, "operator_signature", corrupt_sig)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE2_OPERATOR_SIGNATURE_INVALID


def test_signature_verification_rejects_corrupted_verifier_sig() -> None:
    req = make_valid_request()
    corrupt_sig = "f" * 128
    object.__setattr__(req.preflight_evidence_envelope, "verifier_signature", corrupt_sig)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE4_SIGNATURE_INVALID


def test_signature_verification_rejects_corrupted_tech_lead_sig() -> None:
    req = make_valid_request()
    corrupt_sig = "f" * 128
    object.__setattr__(req.execution_workorder_record, "tech_lead_signature", corrupt_sig)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE5_SIGNATURE_INVALID


# ==============================================================================
# 5. Signature-Byte Selection Invariant Tests
# ==============================================================================


def test_signature_byte_selection_excludes_detached_signature() -> None:
    req = make_valid_request()
    # Check that signature field names are NOT in canonical signing payload dicts
    assert "director_signature" not in req.approval_envelope.canonical_signing_payload_dict()
    assert "operator_signature" not in req.operator_consent_envelope.canonical_signing_payload_dict()
    assert "verifier_signature" not in req.preflight_evidence_envelope.canonical_signing_payload_dict()
    assert "tech_lead_signature" not in req.execution_workorder_record.canonical_signing_payload_dict()

    # Changing a detached signature string does not alter the canonical signing payload bytes
    orig_bytes = req.approval_envelope.canonical_signing_payload_bytes()
    object.__setattr__(req.approval_envelope, "director_signature", "a" * 128)
    assert req.approval_envelope.canonical_signing_payload_bytes() == orig_bytes


# ==============================================================================
# 6. Cross-Envelope Semantic Binding Mismatches Tests
# ==============================================================================


def test_cross_binding_rejects_target_project_uid_mismatch() -> None:
    # Alter project UID in operator consent
    rogue_uid = "00000000-0000-4000-8000-000000000099"
    req = make_valid_request()
    object.__setattr__(req.operator_consent_envelope, "target_project_uid", rogue_uid)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_TARGET_MISMATCH, ERR_GATE2_OPERATOR_SIGNATURE_INVALID)


def test_cross_binding_rejects_target_timeline_uid_mismatch() -> None:
    req = make_valid_request()
    rogue_uid = "00000000-0000-4000-8000-000000000099"
    object.__setattr__(req.operator_consent_envelope, "target_timeline_uid", rogue_uid)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_TARGET_MISMATCH, ERR_GATE2_OPERATOR_SIGNATURE_INVALID)


def test_cross_binding_rejects_target_project_name_mismatch() -> None:
    req = make_valid_request()
    object.__setattr__(req.operator_consent_envelope, "target_project_name", "ROGUE_PROJECT")

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_TARGET_MISMATCH, ERR_GATE2_OPERATOR_SIGNATURE_INVALID)


def test_cross_binding_rejects_target_timeline_name_mismatch() -> None:
    req = make_valid_request()
    object.__setattr__(req.operator_consent_envelope, "target_timeline_name", "ROGUE_TIMELINE")

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_TARGET_MISMATCH, ERR_GATE2_OPERATOR_SIGNATURE_INVALID)


def test_cross_binding_rejects_plan_digest_mismatch() -> None:
    req = make_valid_request()
    rogue_plan = "9" * 64
    object.__setattr__(req.execution_workorder_record, "plan_digest_sha256", rogue_plan)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_PLAN_DIGEST_MISMATCH, ERR_GATE5_SIGNATURE_INVALID)


def test_cross_binding_rejects_action_payload_digest_mismatch() -> None:
    req = make_valid_request()
    rogue_action = "9" * 64
    object.__setattr__(req.execution_workorder_record, "action_payload_digest_sha256", rogue_action)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_ACTION_DIGEST_MISMATCH, ERR_GATE5_SIGNATURE_INVALID)


def test_cross_binding_rejects_approval_envelope_digest_mismatch() -> None:
    req = make_valid_request()
    rogue_approval_digest = "9" * 64
    object.__setattr__(req.operator_consent_envelope, "approval_envelope_digest_sha256", rogue_approval_digest)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_APPROVAL_BINDING_MISMATCH, ERR_GATE2_OPERATOR_SIGNATURE_INVALID)


def test_cross_binding_rejects_unauthorized_permitted_command() -> None:
    req = make_valid_request()
    object.__setattr__(req.execution_workorder_record, "permitted_command", "unauthorized-cmd")

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE5_COMMAND_UNAUTHORIZED, ERR_GATE5_SIGNATURE_INVALID)


def test_cross_binding_checks_candidate_digest_claim() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    # Valid candidate claim matches
    res_ok = verifier.verify(req, current_time=TAU_REFERENCE, expected_candidate_sha256=CANDIDATE_DIGEST)
    assert res_ok.is_valid is True
    assert res_ok.verified_claims["reviewed_candidate_sha256"] == CANDIDATE_DIGEST

    # Mismatched candidate claim fails closed
    res_bad = verifier.verify(req, current_time=TAU_REFERENCE, expected_candidate_sha256="9" * 64)
    assert res_bad.is_valid is False
    assert res_bad.error_code == ERR_GATE5_CANDIDATE_HASH_MISMATCH


def test_cross_binding_rejects_unsatisfied_technical_predicates() -> None:
    req = make_valid_request()
    # Flip empty_timeline_verified to False
    object.__setattr__(req.preflight_evidence_envelope, "empty_timeline_verified", False)

    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)
    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is False
    assert res.error_code in (ERR_GATE4_PREDICATE_UNSATISFIED, ERR_GATE4_SIGNATURE_INVALID)


# ==============================================================================
# 7. Timestamp Validity Windows & Clock Skew Tests
# ==============================================================================


def test_gate4_rejects_future_timestamp_beyond_skew_tolerance() -> None:
    req = make_valid_request()
    # verified_at is 2026-11-05T08:15:00Z. Set tau to 08:14:50Z (10s in the past -> verified_at is 10s future, >5s)
    tau_early = datetime(2026, 11, 5, 8, 14, 50, tzinfo=timezone.utc)
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=tau_early)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE4_EVIDENCE_FUTURE


def test_gate4_rejects_expired_timestamp() -> None:
    req = make_valid_request()
    # valid_until is 2026-11-05T08:45:00Z. Set tau to 08:45:01Z
    tau_late = datetime(2026, 11, 5, 8, 45, 1, tzinfo=timezone.utc)
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=tau_late)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE4_EVIDENCE_EXPIRED


def test_gate5_rejects_future_timestamp_beyond_skew_tolerance() -> None:
    # Ensure Gate 4 is valid during tau_early so Gate 5 future check is isolated
    req = make_valid_request(
        e3_verified_at_iso="2026-11-05T07:50:00Z",
        e3_valid_until_iso="2026-11-05T08:15:00Z",
        e4_issued_at_iso="2026-11-05T08:00:00Z",
        e4_valid_until_iso="2026-11-05T09:00:00Z",
    )
    # issued_at is 2026-11-05T08:00:00Z. Set tau to 07:59:25Z (35s in past -> issued_at is 35s in future, >30s)
    tau_early = datetime(2026, 11, 5, 7, 59, 25, tzinfo=timezone.utc)
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=tau_early)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE5_WORKORDER_FUTURE


def test_gate5_rejects_expired_timestamp_zero_grace() -> None:
    # Ensure Gate 4 has longer window so Gate 5 expiry is isolated
    req = make_valid_request(
        e3_verified_at_iso="2026-11-05T08:40:00Z",
        e3_valid_until_iso="2026-11-05T09:10:00Z",
        e4_issued_at_iso="2026-11-05T08:00:00Z",
        e4_valid_until_iso="2026-11-05T09:00:00Z",
    )
    # valid_until is 2026-11-05T09:00:00Z. Set tau to 09:00:01Z
    tau_late = datetime(2026, 11, 5, 9, 0, 1, tzinfo=timezone.utc)
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=tau_late)
    assert res.is_valid is False
    assert res.error_code == ERR_GATE5_WORKORDER_EXPIRED


def test_tau_rejects_naive_datetime() -> None:
    req = make_valid_request()
    naive_tau = datetime(2026, 11, 5, 8, 20, 0)  # no tzinfo!
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=naive_tau)
    assert res.is_valid is False
    assert res.error_code == ERR_TYPE_MISMATCH


# ==============================================================================
# 8. Unresolved Prerequisites & Governance Boundary Tests
# ==============================================================================


def test_unresolved_prerequisites_always_present_in_facts() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=TAU_REFERENCE)
    assert res.is_valid is True
    assert res.status == "SIGNATURES_AND_BINDINGS_VALID"
    assert res.operational_admission_permitted is False
    assert len(res.unresolved_prerequisites) == len(ALL_UNRESOLVED_PREREQUISITES)
    assert "OPERATOR_CHALLENGE_LIFECYCLE_UNRESOLVED" in res.unresolved_prerequisites
    assert "GATE4_CUSTODY_UNRESOLVED" in res.unresolved_prerequisites


def test_require_operational_readiness_fails_closed() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    verifier = DispatcherAdmissionVerifierCore(key_map, deterministic_test_verifier)

    res = verifier.verify(req, current_time=TAU_REFERENCE, require_operational_readiness=True)
    assert res.is_valid is False
    assert res.error_code == ERR_OPERATOR_CHALLENGE_UNRESOLVED
    assert res.operational_admission_permitted is False
    assert "Operational admission cannot be granted" in (res.error_detail or "")


def test_facts_object_safety_invariant_enforcement() -> None:
    with pytest.raises(DispatcherAdmissionVerifierError) as exc:
        AdmissionVerificationFacts(
            is_valid=True,
            status="SIGNATURES_AND_BINDINGS_VALID",
            error_code=None,
            error_detail=None,
            verified_claims={},
            unresolved_prerequisites=ALL_UNRESOLVED_PREREQUISITES,
            operational_admission_permitted=True,  # VIOLATION!
        )
    assert exc.value.code == "ERR_SAFETY_INVARIANT_VIOLATED"


# ==============================================================================
# 9. In-Memory & Pure Boundary Proof Tests
# ==============================================================================


def test_pure_in_memory_execution_zero_sockets_zero_fs_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure socket.socket is never called
    def _forbidden_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Forbidden socket instantiation during verifier execution")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)

    req = make_valid_request()
    key_map = make_test_key_mapping()
    res = verify_admission_request(
        request=req,
        key_mapping=key_map,
        verifier=deterministic_test_verifier,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
    )
    assert res.is_valid is True
    assert res.status == "SIGNATURES_AND_BINDINGS_VALID"
    assert res.operational_admission_permitted is False


# ==============================================================================
# 10. Happy Path Full Verification
# ==============================================================================


def test_happy_path_verification() -> None:
    req = make_valid_request()
    key_map = make_test_key_mapping()
    res = verify_admission_request(
        request=req,
        key_mapping=key_map,
        verifier=deterministic_test_verifier,
        current_time=TAU_REFERENCE,
        expected_candidate_sha256=CANDIDATE_DIGEST,
    )

    assert res.is_valid is True
    assert res.status == "SIGNATURES_AND_BINDINGS_VALID"
    assert res.error_code is None
    assert res.error_detail is None
    assert res.operational_admission_permitted is False
    assert res.verified_claims["request_id"] == "00000000-0000-4000-8000-000000000000"
    assert res.verified_claims["target_project_uid"] == CANARY_TARGET_PROJECT_UID
    assert res.verified_claims["target_timeline_uid"] == CANARY_TARGET_TIMELINE_UID
    assert res.verified_claims["permitted_command"] == CANARY_PERMITTED_COMMAND
    assert len(res.unresolved_prerequisites) == 9
