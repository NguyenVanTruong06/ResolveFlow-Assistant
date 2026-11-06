"""
Tests for Trusted Canary Dispatch Service (Gate 3 Workorder I17).

Architecture Basis: Accepted Plan 07B7 Revision R7 §2, §4-6, §10;
Accepted Consolidated Integration Audit R2, Workorder I17.

Mode:
    LOCAL_TEST_ONLY; MOCK_GATEWAY_ONLY; NO_PRODUCTION_KEYS;
    NO_SOCKET_OR_DAEMON; NO_LIVE_RESOLVE; NO_LIVE_DISPATCH.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from dataclasses import dataclass
from fractions import Fraction

from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    SourceFileDigestBinding,
    TargetTrackReservation,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.contracts.trial_authorization_contracts import (
    PlannedTimelineAction,
    TrialExecutionAuthorization,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction as BinderPlannedAction,
    TargetSpec,
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)
from src.services.durable_trial_target_ledger import RecoveryCoordinator
from src.services.execution_journal_service import ExecutionJournalService
from src.services.execution_receipt_service import ExecutionReceiptService, ReceiptNotFoundError
from src.services.isolated_resolve_trial_adapter import (
    FakeResolveGateway,
    IsolatedResolveTrialAdapter,
)
from src.services.live_resolve_canary_adapter import LiveResolveCanaryAdapter
from src.services.trial_authorization_signer_service import (
    LocalTestSignerKeyHandle,
    RevocationManifestVerificationResult,
    SignedRevocationManifest,
    canonical_revocation_manifest_claims_bytes,
    check_and_advance_revocation_epoch_anchor_for_local_test,
    load_signer_key_handle_for_local_test,
    load_verify_and_anchor_revocation_manifest_for_local_test,
    verify_signed_revocation_manifest,
    _verify_provenance_seal,
)
from src.services.trusted_canary_dispatch_service import (
    INCIDENT_TARGET_PROJECT_NAMES,
    INCIDENT_TARGET_PROJECT_UID,
    INCIDENT_TARGET_TIMELINE_NAMES,
    INCIDENT_TARGET_TIMELINE_UID,
    CanaryDispatchResult,
    DispatcherDispatchError,
    DispatcherDuplicateDispatchError,
    DispatcherOracleVerificationError,
    DispatcherReceiptPersistenceError,
    DispatcherScopeViolationError,
    DispatcherSecurityError,
    DispatcherTargetDeniedError,
    DispatcherTripwireError,
    LocalTestOracleVerificationClient,
    OracleVerificationRequest,
    OracleVerificationResponse,
    TrustedCanaryDispatchService,
    canonical_bytes_for_oracle_response,
)

TRIAL_KEY_BYTES = b"\x11\x22\x33\x44" * 8  # 32 bytes ephemeral trial key
OPERATOR_KEY_BYTES = b"\x55\x66\x77\x88" * 8  # 32 bytes ephemeral operator key


# ==============================================================================
# Fixtures and Helpers
# ==============================================================================


@pytest.fixture
def env(tmp_path: Path) -> Dict[str, Any]:
    """Isolated filesystem environment for local-test Dispatcher."""
    os.chmod(str(tmp_path), 0o700)
    base_dir = tmp_path / "trial_base"
    journals_dir = base_dir / "journals"
    receipts_dir = base_dir / "receipts"
    locks_dir = base_dir / "locks"
    ledgers_dir = base_dir / "ledgers"
    keystore_dir = base_dir / "security"

    for d in (base_dir, journals_dir, receipts_dir, locks_dir, ledgers_dir, keystore_dir):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(str(d), 0o700)

    # 1. Ephemeral trial key
    trial_key_file = keystore_dir / "canary_trial_key.key"
    trial_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(trial_key_file), 0o600)

    # Ephemeral I13 signer key file for LocalTestSignerKeyHandle
    signer_key_file = keystore_dir / "trial_auth.key"
    signer_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(signer_key_file), 0o600)
    signer_key_handle = load_signer_key_handle_for_local_test(keystore_dir)

    # 2. Ephemeral Revocation Issuer and verified revocation manifest
    rev_issuer_sk = Ed25519PrivateKey.generate()
    rev_issuer_pk = rev_issuer_sk.public_key()
    manifest_data = {
        "epoch": 1,
        "issued_at_iso": "2026-11-04T00:00:00Z",
        "issuer_id": "security_officer_root",
        "manifest_version": 1,
        "revoked_authorization_ids": [],
        "revoked_director_key_ids": [],
        "revoked_operator_key_ids": [],
        "revoked_trial_key_ids": [],
        "valid_until_iso": "2026-11-05T00:00:00Z",
    }
    dummy_manifest = SignedRevocationManifest(
        epoch=1,
        issued_at_iso="2026-11-04T00:00:00Z",
        issuer_id="security_officer_root",
        manifest_version=1,
        revoked_authorization_ids=(),
        revoked_director_key_ids=(),
        revoked_operator_key_ids=(),
        revoked_trial_key_ids=(),
        valid_until_iso="2026-11-05T00:00:00Z",
        manifest_signature="0" * 128,
    )
    claims_bytes = canonical_revocation_manifest_claims_bytes(dummy_manifest)
    sig = rev_issuer_sk.sign(claims_bytes).hex()
    manifest_bytes = canonical_json_bytes(dict(manifest_data, manifest_signature=sig))
    verified_revocation = verify_signed_revocation_manifest(
        raw_manifest_bytes=manifest_bytes,
        issuer_public_key=rev_issuer_pk,
        verification_time_utc=datetime.now(timezone.utc),
    )

    # 3. Epoch anchor directory with durable epoch anchor matching verified_revocation (I17-R4-01)
    anchor_dir = base_dir / "revocation_anchor"
    anchor_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(anchor_dir), 0o700)
    check_and_advance_revocation_epoch_anchor_for_local_test(
        verified_result=verified_revocation,
        anchor_directory=anchor_dir,
        advance=True,
    )

    # 4. Ephemeral Ed25519 oracle key pair
    oracle_sk = Ed25519PrivateKey.generate()
    oracle_pk = oracle_sk.public_key()

    coordinator = RecoveryCoordinator(
        journals_dir=str(journals_dir),
        receipts_dir=str(receipts_dir),
        locks_dir=str(locks_dir),
        ledgers_dir=str(ledgers_dir),
        keystore_path=str(keystore_dir),
    )
    journal = ExecutionJournalService(str(base_dir))
    gateway = FakeResolveGateway(mock_item_id="mock_resolve_item_canary_01")
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)
    receipt_service = ExecutionReceiptService(str(base_dir))
    oracle_client = LocalTestOracleVerificationClient(
        oracle_private_key=oracle_sk,
        signer_key_handle=signer_key_handle,
        revocation_evidence=verified_revocation,
        anchor_directory=anchor_dir,
        director_key_id="dir_canary_key_01",
        operator_key_id="op_canary_key_01",
    )

    dispatcher = TrustedCanaryDispatchService(
        adapter=adapter,
        oracle_client=oracle_client,
        oracle_public_key=oracle_pk,
        receipt_service=receipt_service,
    )

    yield {
        "base_dir": str(base_dir),
        "coordinator": coordinator,
        "journal": journal,
        "gateway": gateway,
        "adapter": adapter,
        "receipt_service": receipt_service,
        "oracle_sk": oracle_sk,
        "oracle_pk": oracle_pk,
        "oracle_client": oracle_client,
        "dispatcher": dispatcher,
        "signer_key_handle": signer_key_handle,
        "keystore_dir": str(keystore_dir),
        "rev_issuer_sk": rev_issuer_sk,
        "rev_issuer_pk": rev_issuer_pk,
        "manifest_bytes": manifest_bytes,
        "verified_revocation": verified_revocation,
        "anchor_dir": anchor_dir,
        "director_key_id": "dir_canary_key_01",
        "operator_key_id": "op_canary_key_01",
    }

    coordinator.close()


def make_valid_plan(
    action: PlannedTimelineAction,
    target_project_uid: str = "proj_canary_valid_001",
    target_project_name: str = "RF_TRIAL_CANARY_P02",
    target_timeline_uid: str = "tl_canary_valid_001",
    target_timeline_name: str = "RF_TRIAL_TL_CANARY_T02",
    timeline_fps_numerator: int = 24,
    timeline_fps_denominator: int = 1,
    timeline_resolution: Tuple[int, int] = (1920, 1080),
    plan_id: str = "plan_canary_valid_001",
    idempotency_key: str = "a" * 64,
    proposal_id: str = "prop_canary_valid_001",
    proposal_revision: str = "1" * 64,
    approval_id: str = "appr_canary_valid_001",
    approved_at_iso: str = "2026-11-04T00:00:00Z",
    source_file_sha256: str = "5" * 64,
    stat_identity: Tuple[int, int, int, int, int] = (64769, 1234567, 1048576, 1700000000, 0),
) -> TimelineExecutionPlan:
    """Builds a real, valid TimelineExecutionPlan binding the single canary action."""
    target_spec = TargetSpec(
        project_name=target_project_name,
        project_id=target_project_uid,
        timeline_name=target_timeline_name,
        timeline_id=target_timeline_uid,
        timeline_fps=RationalFPS(timeline_fps_numerator, timeline_fps_denominator),
        timeline_resolution=timeline_resolution,
        start_timecode_frame=0,
    )
    binder_action = BinderPlannedAction(
        action_index=0,
        action_type=action.action_type,
        target_track_type=action.track_type,
        target_track_index=action.track_index,
        clip_id=action.action_id,
        source_file_path=action.source_path,
        source_range=TimeRangeFrames(action.source_frame_start, action.source_frame_start + action.source_frame_duration),
        timeline_range=TimeRangeFrames(0, action.duration_frames),
        applied_speed=Fraction(1, 1),
        role=action.role,
        action_idempotency_hash="b" * 64,
        action_params={},
    )
    track_res = TargetTrackReservation(
        track_type=action.track_type,
        track_index=action.track_index,
        reserved_start_frame=action.record_frame,
        reserved_end_frame=action.record_frame + action.duration_frames,
        intended_usage="LOCAL_TEST_MOCK_CANARY",
    )
    source_binding = SourceFileDigestBinding(
        clip_id=action.action_id,
        absolute_path=action.source_path,
        full_sha256=source_file_sha256.lower(),
        stream_index=0,
        stat_identity=stat_identity,
    )
    return TimelineExecutionPlan(
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        proposal_id=proposal_id,
        proposal_revision=proposal_revision,
        approval_id=approval_id,
        approved_at_iso=approved_at_iso,
        target_spec=target_spec,
        occupancy_snapshot_id=None,
        binding_status="BOUND_VERIFIED",
        planned_actions=(binder_action,),
        planned_actions_count=1,
        track_reservations=(track_res,),
        source_digests=(source_binding,),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=action.duration_frames,
        total_timeline_duration_sec=Fraction(action.duration_frames, timeline_fps_numerator),
        issues=(),
        created_at_iso=approved_at_iso,
    )


def make_valid_action_and_auth(
    target_project_uid: str = "proj_canary_valid_001",
    target_project_name: str = "RF_TRIAL_CANARY_P02",
    target_timeline_uid: str = "tl_canary_valid_001",
    target_timeline_name: str = "RF_TRIAL_TL_CANARY_T02",
    execution_id: str = "exec_canary_valid_001",
    record_frame: int = 86400,
    observed_start_frame: int = 86400,
    duration_frames: int = 120,
    source_start: int = 0,
    source_duration: int = 120,
    action_type: str = "INSERT_CLIP",
    track_type: str = "video",
    track_index: int = 1,
    plan_idempotency_key: str = "a" * 64,
    source_file_sha256: str = "5" * 64,
    timeline_resolution: Tuple[int, int] = (1920, 1080),
    stat_identity: Tuple[int, int, int, int, int] = (64769, 1234567, 1048576, 1700000000, 0),
) -> Tuple[PlannedTimelineAction, TrialExecutionAuthorization, TimelineExecutionPlan]:
    """Builds a strictly valid action, matching authoritative TimelineExecutionPlan, and matching I16 TrialExecutionAuthorization."""
    action = PlannedTimelineAction(
        action_id="act_canary_001",
        action_index=0,
        action_type=action_type,
        track_type=track_type,
        track_index=track_index,
        record_frame=record_frame,
        duration_frames=duration_frames,
        source_path="/media/canary/canary_test_1080p24.mp4",
        source_frame_start=source_start,
        source_frame_duration=source_duration,
        role="b_roll",
    )

    plan = make_valid_plan(
        action=action,
        target_project_uid=target_project_uid,
        target_project_name=target_project_name,
        target_timeline_uid=target_timeline_uid,
        target_timeline_name=target_timeline_name,
        timeline_fps_numerator=24,
        timeline_fps_denominator=1,
        timeline_resolution=timeline_resolution,
        idempotency_key=plan_idempotency_key,
        source_file_sha256=source_file_sha256,
        stat_identity=stat_identity,
    )
    plan_digest = compute_canonical_plan_digest(plan)

    now = datetime.now(timezone.utc)
    issued_iso = (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    valid_iso = (now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z")

    base_auth: Dict[str, Any] = {
        "authorization_id": "auth_canary_valid_001",
        "key_id": "canary_trial_key",
        "operator_gesture_digest_sha256": "1" * 64,
        "approval_record_id": plan.approval_id,
        "approval_record_digest_sha256": "2" * 64,
        "proposal_id": plan.proposal_id,
        "proposal_revision": plan.proposal_revision,
        "proposal_content_digest_sha256": "3" * 64,
        "plan_idempotency_key": plan_idempotency_key,
        "plan_digest_sha256": plan_digest,
        "action_payload_digest_sha256": "0" * 64,  # placeholder
        "target_project_uid": target_project_uid,
        "target_project_name": target_project_name,
        "target_timeline_uid": target_timeline_uid,
        "target_timeline_name": target_timeline_name,
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": observed_start_frame,
        "authorized_record_frame": record_frame,
        "source_file_path": "/media/canary/canary_test_1080p24.mp4",
        "source_file_sha256": source_file_sha256,
        "authorized_action_type": action_type,
        "authorized_track_type": track_type,
        "authorized_track_index": track_index,
        "authorized_timeline_frame_start": record_frame - observed_start_frame,
        "authorized_timeline_frame_duration": duration_frames,
        "authorized_source_frame_start": source_start,
        "authorized_source_frame_duration": source_duration,
        "execution_id": execution_id,
        "issued_at_iso": issued_iso,
        "valid_until_iso": valid_iso,
        "token_nonce": execution_id,
        "authorization_hmac": "0" * 64,  # placeholder
    }

    # Step 1: assemble canonical action payload to compute action_payload_digest_sha256
    temp_auth1 = TrialExecutionAuthorization(**base_auth)
    payload = assemble_canonical_action_payload(action, temp_auth1, execution_id=execution_id)
    payload_bytes = canonical_action_payload_bytes(payload)
    action_digest = hashlib.sha256(payload_bytes).hexdigest()
    base_auth["action_payload_digest_sha256"] = action_digest

    # Step 2: compute canonical auth bytes and sign with TRIAL_KEY_BYTES
    temp_auth2 = TrialExecutionAuthorization(**base_auth)
    auth_bytes = canonical_bytes_for_auth_hmac(temp_auth2)
    sig = hmac.new(TRIAL_KEY_BYTES, auth_bytes, hashlib.sha256).hexdigest()
    base_auth["authorization_hmac"] = sig

    final_auth = TrialExecutionAuthorization(**base_auth)
    return action, final_auth, plan


# ==============================================================================
# GROUP 1: Happy Path Tests
# ==============================================================================


def test_happy_path_mock_canary_dispatch(env: Dict[str, Any]) -> None:
    """Happy path: valid authorization and action pass all 11 oracle checks and dispatch to mock gateway."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()

    # Pre-acquire coordinator target lock
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    result = dispatcher.dispatch_canary_action(action, auth, plan)

    # 1. Assert result DTO attributes
    assert isinstance(result, CanaryDispatchResult)
    assert result.status == "COMMITTED"
    assert result.execution_id == auth.execution_id
    assert result.authorization_id == auth.authorization_id
    assert result.action_id == action.action_id
    assert result.resolve_item_id == "mock_resolve_item_canary_01"
    assert result.mode == "MOCK_ONLY"
    assert result.terminalized is False
    assert result.target_project_uid == auth.target_project_uid
    assert result.target_timeline_uid == auth.target_timeline_uid

    # 2. Assert exactly one gateway call
    assert gateway.call_count == 1
    assert len(gateway.dispatched_actions) == 1
    assert gateway.dispatched_actions[0].action_id == action.action_id

    # 3. Assert receipt was created and updated in test directory
    assert result.receipt_id == auth.execution_id
    loaded_receipt = receipt_service.load_receipt(auth.execution_id)
    assert loaded_receipt.state == "EXECUTING"
    assert loaded_receipt.last_error_code == "MOCK_ONLY"
    assert loaded_receipt.last_error_message == "LOCAL_TEST_MOCK_DISPATCH_COMPLETED_NON_TERMINAL"
    assert loaded_receipt.completed_at_iso is None  # NOT claiming completed terminal readback


def test_happy_path_with_explicit_execution_plan(env: Dict[str, Any]) -> None:
    """Happy path when explicit matching TimelineExecutionPlan is passed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(execution_id="exec_with_plan_001")

    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    result = dispatcher.dispatch_canary_action(action, auth, plan)
    assert result.status == "COMMITTED"
    assert gateway.call_count == 1


# ==============================================================================
# GROUP 2: Parameterized 11-Point Oracle Acceptance Invariant Tests
# ==============================================================================


def test_oracle_check_1_invalid_signature_rejected(env: Dict[str, Any]) -> None:
    """Point 1: Invalid Ed25519 signature fails closed with zero gateway calls."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Inject corrupted signature
    tampered_sig = "a" * 128
    oracle_client.set_fault_override(oracle_signature=tampered_sig)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_SIGNATURE_INVALID" in str(exc_info.value)
    assert gateway.call_count == 0


@pytest.mark.parametrize("bad_is_valid", [False, 1, 0, "True", None])
def test_oracle_check_2_is_valid_not_true_rejected(env: Dict[str, Any], bad_is_valid: Any) -> None:
    """Point 2: is_valid not exact bool True fails closed with zero gateway calls."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(is_valid=bad_is_valid)

    with pytest.raises(DispatcherOracleVerificationError):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0


def test_oracle_check_3_verification_result_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 3: verification_result != 'VERIFIED_VALID' fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(verification_result="VERIFICATION_FAILED")

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_VERIFICATION_RESULT_INVALID" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_4_reason_code_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 4: reason_code != 'OK' fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(reason_code="ERR_KEY_REVOKED")

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_REASON_CODE_INVALID" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_5_request_id_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 5: Echoed request_id mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(request_id="mismatched_req_id_9999")

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_REQUEST_ID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_6_request_nonce_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 6: Echoed request_nonce mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(request_nonce="f" * 64)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_REQUEST_NONCE_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_7_auth_digest_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 7: Authorization digest mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(authorization_digest_sha256="e" * 64)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_AUTH_DIGEST_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_8_action_payload_digest_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 8: Action payload digest mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(action_payload_digest_sha256="d" * 64)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_ACTION_PAYLOAD_DIGEST_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_9_project_uid_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 9: Project UID mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(target_project_uid="proj_mismatched_other_001")

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_PROJECT_UID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_10_timeline_uid_mismatch_rejected(env: Dict[str, Any]) -> None:
    """Point 10: Timeline UID mismatch fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_fault_override(target_timeline_uid="tl_mismatched_other_001")

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_TIMELINE_UID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_11_future_verified_at_rejected(env: Dict[str, Any]) -> None:
    """Point 11: verified_at in future fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    future_iso = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    oracle_client.set_fault_override(verified_at_iso=future_iso)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_VERIFIED_AT_IN_FUTURE" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_11_stale_verified_at_rejected(env: Dict[str, Any]) -> None:
    """Point 11: verified_at older than 60s fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    stale_iso = (datetime.now(timezone.utc) - timedelta(seconds=65)).isoformat().replace("+00:00", "Z")
    oracle_client.set_fault_override(verified_at_iso=stale_iso)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_VERIFIED_AT_STALE" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_11_expired_valid_until_rejected(env: Dict[str, Any]) -> None:
    """Point 11: now past valid_until fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    past_iso = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    oracle_client.set_fault_override(valid_until_iso=past_iso)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_RESPONSE_EXPIRED" in str(exc_info.value)
    assert gateway.call_count == 0


def test_oracle_check_11_overlong_validity_window_rejected(env: Dict[str, Any]) -> None:
    """Point 11: validity window > 60s fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    overlong_iso = (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    oracle_client.set_fault_override(verified_at_iso=now_iso, valid_until_iso=overlong_iso)

    with pytest.raises(DispatcherOracleVerificationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_ORACLE_VALIDITY_WINDOW_INVALID" in str(exc_info.value)
    assert gateway.call_count == 0


# ==============================================================================
# GROUP 3: Scope and Plan Boundary Rejection Tests
# ==============================================================================


@pytest.mark.parametrize(
    "scope_kwargs, expected_err",
    [
        ({"action_type": "OVERWRITE_CLIP"}, "ERR_FORBIDDEN_ACTION_TYPE"),
        ({"track_type": "audio"}, "ERR_FORBIDDEN_TRACK_TYPE"),
        ({"track_index": 2}, "ERR_FORBIDDEN_TRACK_INDEX"),
        ({"duration_frames": 60}, "ERR_FORBIDDEN_TIMELINE_DURATION"),
        ({"source_frame_duration": 60}, "ERR_FORBIDDEN_SOURCE_DURATION"),
        ({"source_frame_start": 10}, "ERR_FORBIDDEN_SOURCE_START"),
        ({"record_frame": 86500}, "ERR_FORBIDDEN_TIMELINE_RELATIVE_START"),
    ],
)
def test_scope_violations_fail_closed(
    env: Dict[str, Any], scope_kwargs: Dict[str, Any], expected_err: str
) -> None:
    """Tests that any variation outside the exact single canary geometry is rejected."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    valid_action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    action_dict = {
        "action_id": valid_action.action_id,
        "action_index": valid_action.action_index,
        "action_type": valid_action.action_type,
        "track_type": valid_action.track_type,
        "track_index": valid_action.track_index,
        "record_frame": valid_action.record_frame,
        "duration_frames": valid_action.duration_frames,
        "source_path": valid_action.source_path,
        "source_frame_start": valid_action.source_frame_start,
        "source_frame_duration": valid_action.source_frame_duration,
        "role": valid_action.role,
    }
    action_dict.update(scope_kwargs)
    action = PlannedTimelineAction(**action_dict)

    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert expected_err in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_with_multiple_actions_rejected(env: Dict[str, Any]) -> None:
    """A plan containing more than 1 action violates single canary scope."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()

    extra_action = BinderPlannedAction(
        action_index=1,
        action_type="INSERT_CLIP",
        target_track_type="video",
        target_track_index=1,
        clip_id="act_extra_002",
        source_file_path="/media/canary/canary_test_1080p24.mp4",
        source_range=TimeRangeFrames(0, 120),
        timeline_range=TimeRangeFrames(120, 240),
        applied_speed=Fraction(1, 1),
        role="b_roll",
        action_idempotency_hash="c" * 64,
        action_params={},
    )
    object.__setattr__(plan, "planned_actions", (plan.planned_actions[0], extra_action))
    object.__setattr__(plan, "planned_actions_count", 2)

    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_PLAN_ACTION_COUNT_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_none_or_wrong_type_rejected(env: Dict[str, Any]) -> None:
    """Omission of plan or passing wrong type fails closed before oracle or gateway calls (I17-01)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, _ = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    for bad_plan in (None, "not_a_plan", {"planned_actions": []}):
        with pytest.raises(DispatcherScopeViolationError) as exc_info:
            dispatcher.dispatch_canary_action(action, auth, bad_plan)  # type: ignore
        assert "ERR_INVALID_PLAN_TYPE" in str(exc_info.value)
        assert gateway.call_count == 0


def test_plan_zero_actions_rejected(env: Dict[str, Any]) -> None:
    """Plan with zero actions fails closed with ERR_PLAN_ACTION_COUNT_MISMATCH (I17-01)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    object.__setattr__(plan, "planned_actions", ())
    object.__setattr__(plan, "planned_actions_count", 0)

    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)
    assert "ERR_PLAN_ACTION_COUNT_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_action_field_mismatches_rejected(env: Dict[str, Any]) -> None:
    """Mismatched action attributes in plan fail closed with zero gateway calls (I17-01)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, _ = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # 1. Mismatched clip_id
    bad_plan = make_valid_plan(action)
    mismatched_action = BinderPlannedAction(
        action_index=0,
        action_type=action.action_type,
        target_track_type=action.track_type,
        target_track_index=action.track_index,
        clip_id="different_clip_id_002",
        source_file_path=action.source_path,
        source_range=TimeRangeFrames(0, 120),
        timeline_range=TimeRangeFrames(0, 120),
        applied_speed=Fraction(1, 1),
        role=action.role,
        action_idempotency_hash="b" * 64,
        action_params={},
    )
    object.__setattr__(bad_plan, "planned_actions", (mismatched_action,))
    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, bad_plan)
    assert "ERR_PLAN_ACTION_ID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_target_spec_mismatches_rejected(env: Dict[str, Any]) -> None:
    """Mismatched target specification fields in plan fail closed before oracle/gateway (I17-01)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, _ = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Project UID mismatch
    bad_plan = make_valid_plan(action, target_project_uid="different_proj_uid_999")
    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, bad_plan)
    assert "ERR_PLAN_PROJECT_UID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0

    # Timeline UID mismatch
    bad_plan2 = make_valid_plan(action, target_timeline_uid="different_tl_uid_999")
    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, bad_plan2)
    assert "ERR_PLAN_TIMELINE_UID_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_digest_tampered_fails_closed(env: Dict[str, Any]) -> None:
    """Tampered plan content causing plan digest mismatch fails closed (I17-01)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Tamper action_idempotency_hash in plan action
    tampered_action = BinderPlannedAction(
        action_index=0,
        action_type=action.action_type,
        target_track_type=action.track_type,
        target_track_index=action.track_index,
        clip_id=action.action_id,
        source_file_path=action.source_path,
        source_range=TimeRangeFrames(0, 120),
        timeline_range=TimeRangeFrames(0, 120),
        applied_speed=Fraction(1, 1),
        role=action.role,
        action_idempotency_hash="f" * 64,  # modified!
        action_params={},
    )
    object.__setattr__(plan, "planned_actions", (tampered_action,))

    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_PLAN_DIGEST_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_plan_source_digest_missing_or_tampered_fails_closed(env: Dict[str, Any]) -> None:
    """Missing or mismatched source digest in plan fails closed (I17-02)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # 1. Missing source digest entry
    object.__setattr__(plan, "source_digests", ())
    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)
    assert "ERR_PLAN_SOURCE_DIGEST_MISSING" in str(exc_info.value)
    assert gateway.call_count == 0

    # 2. Mismatched source sha256
    tampered_source = SourceFileDigestBinding(
        clip_id=action.action_id,
        absolute_path=action.source_path,
        full_sha256="9" * 64,  # different hash
        stream_index=0,
        stat_identity=(1, 2, 3, 4, 0),
    )
    object.__setattr__(plan, "source_digests", (tampered_source,))
    with pytest.raises(DispatcherScopeViolationError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)
    assert "ERR_PLAN_SOURCE_SHA256_MISMATCH" in str(exc_info.value)
    assert gateway.call_count == 0


def test_persisted_receipt_contains_authoritative_metadata_no_placeholders(
    env: Dict[str, Any],
) -> None:
    """Persisted ExecutionReceipt contains authoritative plan metadata with zero invented values (I17-02)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]
    gateway: FakeResolveGateway = env["gateway"]

    custom_res = (3840, 2160)
    custom_stat = (65535, 9876543, 2097152, 1712345678, 42)

    action, auth, plan = make_valid_action_and_auth(
        timeline_resolution=custom_res,
        stat_identity=custom_stat,
    )
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    result = dispatcher.dispatch_canary_action(action, auth, plan)
    assert result.status == "COMMITTED"
    assert gateway.call_count == 1

    loaded_receipt = receipt_service.load_receipt(auth.execution_id)
    # Assert authoritative resolution from plan (not placeholder 1920x1080)
    assert loaded_receipt.timeline_resolution == custom_res
    assert loaded_receipt.timeline_resolution != (1920, 1080)

    # Assert authoritative source digest and stat identity from plan (not placeholder (0, 0, 1024, 0, 0))
    assert len(loaded_receipt.source_digests) == 1
    src_binding = loaded_receipt.source_digests[0]
    assert src_binding.stat_identity == custom_stat
    assert src_binding.stat_identity != (0, 0, 1024, 0, 0)
    assert src_binding.full_sha256 == auth.source_file_sha256.lower()
    assert src_binding.clip_id == action.action_id
    assert src_binding.absolute_path == auth.source_file_path

    # Assert other plan bindings
    assert loaded_receipt.track_reservations == plan.track_reservations
    assert loaded_receipt.planned_actions_count == 1
    assert loaded_receipt.approved_at_iso == plan.approved_at_iso
    assert loaded_receipt.idempotency_key == plan.idempotency_key


# ==============================================================================
# GROUP 4: Target Denial (Blacklist) Tests
# ==============================================================================


def test_prior_incident_project_uid_rejected(env: Dict[str, Any]) -> None:
    """Incident project UID 0ee71404-6405-47ca-89e1-aaeaac142398 is rejected unconditionally."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(
        target_project_uid=INCIDENT_TARGET_PROJECT_UID,
        target_project_name="InnocuousProjectName",  # test renaming defense
    )
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherTargetDeniedError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_TARGET_PROJECT_UID_BLACKLISTED" in str(exc_info.value)
    assert gateway.call_count == 0


def test_prior_incident_timeline_uid_rejected(env: Dict[str, Any]) -> None:
    """Incident timeline UID e4e5a587-661d-4854-8f37-8a279d3f2ee1 is rejected unconditionally."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(
        target_timeline_uid=INCIDENT_TARGET_TIMELINE_UID,
        target_timeline_name="InnocuousTimelineName",  # test renaming defense
    )
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherTargetDeniedError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_TARGET_TIMELINE_UID_BLACKLISTED" in str(exc_info.value)
    assert gateway.call_count == 0


@pytest.mark.parametrize("incident_name", list(INCIDENT_TARGET_PROJECT_NAMES))
def test_prior_incident_project_names_rejected(env: Dict[str, Any], incident_name: str) -> None:
    """Recorded incident project names are rejected."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(
        target_project_uid="proj_fresh_uid_001",
        target_project_name=incident_name,
    )
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherTargetDeniedError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_TARGET_PROJECT_NAME_BLACKLISTED" in str(exc_info.value)
    assert gateway.call_count == 0


@pytest.mark.parametrize("incident_name", list(INCIDENT_TARGET_TIMELINE_NAMES))
def test_prior_incident_timeline_names_rejected(env: Dict[str, Any], incident_name: str) -> None:
    """Recorded incident timeline names are rejected."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(
        target_timeline_uid="tl_fresh_uid_001",
        target_timeline_name=incident_name,
    )
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherTargetDeniedError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_TARGET_TIMELINE_NAME_BLACKLISTED" in str(exc_info.value)
    assert gateway.call_count == 0


def test_custom_target_blacklist_enforced(env: Dict[str, Any]) -> None:
    """Custom blacklists configured in Dispatcher are strictly enforced."""
    custom_dispatcher = TrustedCanaryDispatchService(
        adapter=env["adapter"],
        oracle_client=env["oracle_client"],
        oracle_public_key=env["oracle_pk"],
        target_blacklist={
            "project_uids": ["custom_blacklisted_proj_uid"],
            "timeline_uids": ["custom_blacklisted_tl_uid"],
        },
    )

    action, auth, plan = make_valid_action_and_auth(target_project_uid="custom_blacklisted_proj_uid")
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(DispatcherTargetDeniedError):
        custom_dispatcher.dispatch_canary_action(action, auth, plan)


# ==============================================================================
# GROUP 5: Mock Tripwire and Adapter Isolation Tests
# ==============================================================================


def test_wrong_adapter_class_rejected(env: Dict[str, Any]) -> None:
    """Non-exact adapter class or subclass is rejected at initialization."""
    class SubclassedAdapter(IsolatedResolveTrialAdapter):
        pass

    subclassed = SubclassedAdapter(env["coordinator"], env["journal"], env["gateway"])

    with pytest.raises(DispatcherTripwireError) as exc_info:
        TrustedCanaryDispatchService(
            adapter=subclassed,
            oracle_client=env["oracle_client"],
            oracle_public_key=env["oracle_pk"],
        )
    assert "ERR_INVALID_ADAPTER_TYPE" in str(exc_info.value)


def test_live_resolve_adapter_rejected(env: Dict[str, Any]) -> None:
    """LiveResolveCanaryAdapter is strictly prohibited in Dispatcher."""
    class MockLiveAdapter(LiveResolveCanaryAdapter):
        def __init__(self):
            pass

    live_adapter = MockLiveAdapter()

    with pytest.raises(DispatcherTripwireError) as exc_info:
        TrustedCanaryDispatchService(
            adapter=live_adapter,  # type: ignore
            oracle_client=env["oracle_client"],
            oracle_public_key=env["oracle_pk"],
        )
    assert "ERR_INVALID_ADAPTER_TYPE" in str(exc_info.value) or "ERR_LIVE_ADAPTER_PROHIBITED" in str(exc_info.value)


def test_live_resolve_attributes_rejected_before_dispatch(env: Dict[str, Any]) -> None:
    """Injecting live Resolve attributes onto adapter or gateway triggers tripwire."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Monkey-patch live Resolve attribute onto gateway
    gateway.resolve = object()

    with pytest.raises(DispatcherTripwireError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_LIVE_RESOLVE_ATTRIBUTE_DETECTED" in str(exc_info.value) or "ERR_MOCK_TRIPWIRE_FAILED" in str(exc_info.value)
    assert gateway.call_count == 0

    del gateway.resolve


def test_wrong_gateway_type_rejected(env: Dict[str, Any]) -> None:
    """Gateway that is not exact FakeResolveGateway is rejected."""
    class ForeignFakeGateway(FakeResolveGateway):
        pass

    bad_gw = ForeignFakeGateway()
    adapter = IsolatedResolveTrialAdapter(env["coordinator"], env["journal"], env["gateway"])
    object.__setattr__(adapter, "_fake_gateway", bad_gw)

    with pytest.raises(DispatcherTripwireError) as exc_info:
        TrustedCanaryDispatchService(
            adapter=adapter,
            oracle_client=env["oracle_client"],
            oracle_public_key=env["oracle_pk"],
        )
    assert "ERR_INVALID_GATEWAY_TYPE" in str(exc_info.value) or "ERR_MOCK_TRIPWIRE_FAILED" in str(exc_info.value)


# ==============================================================================
# GROUP 6: One-Call Maximum and Replay Tests
# ==============================================================================


def test_duplicate_execution_id_dispatch_rejected(env: Dict[str, Any]) -> None:
    """Dispatching the same execution ID twice is rejected immediately."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(execution_id="exec_duplicate_001")
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    res1 = dispatcher.dispatch_canary_action(action, auth, plan)
    assert res1.status == "COMMITTED"
    assert gateway.call_count == 1

    # Second attempt with same execution_id must fail closed
    with pytest.raises(DispatcherDuplicateDispatchError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_DUPLICATE_EXECUTION_DISPATCH" in str(exc_info.value)
    assert gateway.call_count == 1  # Still 1, no second dispatch


def test_concurrent_dispatch_one_call_maximum(env: Dict[str, Any]) -> None:
    """Concurrent attempts to dispatch the same execution ID permit at most 1 call."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(execution_id="exec_concurrent_001")
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    successes = 0
    rejections = 0

    def attempt_dispatch():
        try:
            res = dispatcher.dispatch_canary_action(action, auth, plan)
            return ("SUCCESS", res)
        except DispatcherDuplicateDispatchError:
            return ("REJECTED", None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(attempt_dispatch) for _ in range(4)]
        for f in concurrent.futures.as_completed(futures):
            outcome, _ = f.result()
            if outcome == "SUCCESS":
                successes += 1
            else:
                rejections += 1

    assert successes == 1
    assert rejections == 3
    assert gateway.call_count == 1


# ==============================================================================
# GROUP 7: Post-Dispatch & Failure State Preservation Tests
# ==============================================================================


def test_gateway_exception_latches_in_doubt_and_raises(env: Dict[str, Any]) -> None:
    """When gateway raises an error post-oracle verification, Dispatcher raises DispatcherDispatchError
    and preserves in-doubt state without retrying or releasing exclusion."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]
    coordinator: RecoveryCoordinator = env["coordinator"]

    action, auth, plan = make_valid_action_and_auth(execution_id="exec_gw_err_001")
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Configure gateway to fail
    gateway.configured_exception = RuntimeError("Simulated DaVinci Resolve IPC Crash")

    with pytest.raises(DispatcherDispatchError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_DISPATCH_EXECUTION_FAILED" in str(exc_info.value)

    # Invariant: zero retries
    assert gateway.call_count == 1

    # Invariant: target lock preserved in-doubt
    assert coordinator._lock_state.get(auth.target_timeline_uid) == "IN_DOUBT_LOCKED"
    assert coordinator.durable_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid).state == "ACTIVE"


def test_receipt_transition_failure_preserves_uncertainty(env: Dict[str, Any]) -> None:
    """When receipt persistence transition fails after successful gateway execution,
    Dispatcher raises DispatcherReceiptPersistenceError and leaves outcome in doubt."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]
    gateway: FakeResolveGateway = env["gateway"]

    action, auth, plan = make_valid_action_and_auth(execution_id="exec_rcpt_err_001")
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Monkey-patch transition_state to raise
    def failing_transition(*args, **kwargs):
        raise OSError("Simulated Disk Full on Receipt Storage")

    receipt_service.transition_state = failing_transition  # type: ignore

    with pytest.raises(DispatcherReceiptPersistenceError) as exc_info:
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert "ERR_POST_DISPATCH_RECEIPT_TRANSITION_FAILED" in str(exc_info.value)
    assert gateway.call_count == 1  # Gateway had executed


# ==============================================================================
# GROUP 8: Safety and Key Isolation Tripwires
# ==============================================================================


def test_dispatcher_possesses_zero_hmac_keys(env: Dict[str, Any]) -> None:
    """Confirms Dispatcher contains zero HMAC signing keys or derivation state."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]

    forbidden_attributes = (
        "_hmac_key",
        "hmac_key",
        "_key_bytes",
        "key_bytes",
        "_signing_key",
        "signing_key",
        "_capability",
        "signing_capability",
    )
    for attr in forbidden_attributes:
        assert not hasattr(dispatcher, attr), f"Dispatcher leaked forbidden attribute '{attr}'"


def test_no_live_resolve_imports_or_sockets() -> None:
    """Static analysis asserting no DaVinci Resolve or socket imports in dispatcher service."""
    service_path = Path("src/services/trusted_canary_dispatch_service.py")
    content = service_path.read_text(encoding="utf-8")

    forbidden_tokens = [
        "import socket",
        "from socket import",
        "import bmd",
        "from bmd import",
        "import fusionscript",
        "from fusionscript import",
        "DaVinciResolve()",
        ".GetProjectManager()",
        "/var/lib/resolveflow",
        "/etc/resolveflow",
    ]
    for token in forbidden_tokens:
        assert token not in content, f"Forbidden live token '{token}' detected in dispatcher service"


# ==============================================================================
# GROUP 11: Gate 3-I17 Revision R2 Review Findings Regression Tests (I17-R2-01 & I17-R2-02)
# ==============================================================================


class _NoOpEd25519PublicKeySubclass(Ed25519PublicKey):
    """Custom Ed25519PublicKey subclass that overrides verify() to be a no-op."""

    def __init__(self, real_key: Ed25519PublicKey) -> None:
        self._real_key = real_key

    def verify(self, signature: bytes, data: bytes) -> None:
        """No-op verification intended to bypass signature validation."""
        pass

    def public_bytes(
        self,
        encoding: serialization.Encoding,
        format: serialization.PublicFormat,
    ) -> bytes:
        return self._real_key.public_bytes(encoding, format)

    def public_bytes_raw(self) -> bytes:
        return self._real_key.public_bytes_raw()

    def __copy__(self) -> _NoOpEd25519PublicKeySubclass:
        return self

    def __deepcopy__(self, memo: Any) -> _NoOpEd25519PublicKeySubclass:
        return self

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, _NoOpEd25519PublicKeySubclass) and self._real_key == other._real_key


def test_i17_r2_01_oracle_public_key_custom_subclass_rejected_at_construction(env: Dict[str, Any]) -> None:
    """I17-R2-01: Custom Ed25519PublicKey subclass is rejected at constructor with TypeError."""
    real_pk: Ed25519PublicKey = env["oracle_pk"]
    subclass_pk = _NoOpEd25519PublicKeySubclass(real_pk)

    with pytest.raises(TypeError, match="ERR_ORACLE_KEY_SUBCLASS_REJECTED"):
        TrustedCanaryDispatchService(
            adapter=env["adapter"],
            oracle_client=env["oracle_client"],
            oracle_public_key=subclass_pk,
            receipt_service=env["receipt_service"],
        )


def test_i17_r2_01_oracle_public_key_custom_subclass_rejected_before_adapter_or_receipt(env: Dict[str, Any]) -> None:
    """I17-R2-01: Custom Ed25519PublicKey subclass injected onto service fails closed before any adapter or receipt call."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    real_pk: Ed25519PublicKey = env["oracle_pk"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Inject subclass key whose verify() is a no-op
    object.__setattr__(dispatcher, "_oracle_public_key", _NoOpEd25519PublicKeySubclass(real_pk))

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_KEY_INVALID"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r2_02_oracle_rejects_invalid_hmac_and_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R2-02: Local test Oracle rejects authorization with invalid HMAC and emits signed failure response.

    Dispatcher fails closed with zero gateway calls and zero persisted receipts.
    """
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, valid_auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(valid_auth.target_timeline_uid)

    # Tamper with authorization HMAC
    tampered_hmac = "f" * 64 if valid_auth.authorization_hmac != "f" * 64 else "e" * 64
    invalid_auth = object.__new__(TrialExecutionAuthorization)
    for field_name in valid_auth.__dataclass_fields__:
        val = tampered_hmac if field_name == "authorization_hmac" else getattr(valid_auth, field_name)
        object.__setattr__(invalid_auth, field_name, val)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, invalid_auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(valid_auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def make_verified_revocation(
    issuer_sk: Ed25519PrivateKey,
    *,
    epoch: int = 1,
    valid_until_iso: str = "2026-11-05T00:00:00Z",
    issued_at_iso: str = "2026-11-04T00:00:00Z",
    issuer_id: str = "security_officer_root",
    revoked_authorization_ids: Tuple[str, ...] = (),
    revoked_director_key_ids: Tuple[str, ...] = (),
    revoked_operator_key_ids: Tuple[str, ...] = (),
    revoked_trial_key_ids: Tuple[str, ...] = (),
    verification_time_utc: Optional[datetime] = None,
    anchor_directory: Optional[Union[str, Path]] = None,
) -> RevocationManifestVerificationResult:
    dummy_manifest = SignedRevocationManifest(
        epoch=epoch,
        issued_at_iso=issued_at_iso,
        issuer_id=issuer_id,
        manifest_version=1,
        revoked_authorization_ids=revoked_authorization_ids,
        revoked_director_key_ids=revoked_director_key_ids,
        revoked_operator_key_ids=revoked_operator_key_ids,
        revoked_trial_key_ids=revoked_trial_key_ids,
        valid_until_iso=valid_until_iso,
        manifest_signature="0" * 128,
    )
    claims_bytes = canonical_revocation_manifest_claims_bytes(dummy_manifest)
    sig = issuer_sk.sign(claims_bytes).hex()
    manifest_data = {
        "epoch": epoch,
        "issued_at_iso": issued_at_iso,
        "issuer_id": issuer_id,
        "manifest_version": 1,
        "revoked_authorization_ids": list(revoked_authorization_ids),
        "revoked_director_key_ids": list(revoked_director_key_ids),
        "revoked_operator_key_ids": list(revoked_operator_key_ids),
        "revoked_trial_key_ids": list(revoked_trial_key_ids),
        "valid_until_iso": valid_until_iso,
        "manifest_signature": sig,
    }
    raw_bytes = canonical_json_bytes(manifest_data)
    ver_time = verification_time_utc or datetime.now(timezone.utc)
    res = verify_signed_revocation_manifest(
        raw_manifest_bytes=raw_bytes,
        issuer_public_key=issuer_sk.public_key(),
        verification_time_utc=ver_time,
    )
    if anchor_directory is not None:
        check_and_advance_revocation_epoch_anchor_for_local_test(
            verified_result=res,
            anchor_directory=anchor_directory,
            advance=True,
        )
    return res


def test_i17_r2_02_oracle_rejects_revoked_authorization_id(env: Dict[str, Any]) -> None:
    """I17-R2-02: Local test Oracle checks revoked authorization IDs in verified revocation evidence."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    rev_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=2,
        anchor_directory=env["anchor_dir"],
        revoked_authorization_ids=(auth.authorization_id,),
    )
    oracle_client.set_verified_revocation_evidence(rev_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r2_02_oracle_rejects_revoked_trial_key_id(env: Dict[str, Any]) -> None:
    """I17-R2-02: Local test Oracle checks revoked trial key IDs in verified revocation evidence."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    rev_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=2,
        anchor_directory=env["anchor_dir"],
        revoked_trial_key_ids=(auth.key_id,),
    )
    oracle_client.set_verified_revocation_evidence(rev_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r2_02_oracle_rejects_revoked_director_key_id(env: Dict[str, Any]) -> None:
    """I17-R2-02: Local test Oracle checks revoked Director key IDs in verified revocation evidence."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    rev_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=2,
        anchor_directory=env["anchor_dir"],
        revoked_director_key_ids=(env["director_key_id"],),
    )
    oracle_client.set_verified_revocation_evidence(rev_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r2_02_oracle_rejects_revoked_operator_key_id(env: Dict[str, Any]) -> None:
    """I17-R2-02: Local test Oracle checks revoked Operator key IDs in verified revocation evidence."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    rev_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=2,
        anchor_directory=env["anchor_dir"],
        revoked_operator_key_ids=(env["operator_key_id"],),
    )
    oracle_client.set_verified_revocation_evidence(rev_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r3_01_oracle_rejects_absent_revocation_evidence(env: Dict[str, Any]) -> None:
    """I17-R3-01: Local test Oracle rejects authorization when revocation evidence is absent."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    oracle_client.set_verified_revocation_evidence(None)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r3_01_oracle_rejects_fabricated_invalid_revocation_signature(env: Dict[str, Any]) -> None:
    """I17-R3-01: Local test Oracle rejects raw revocation manifest with invalid digital signature."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Manifest signed by a different (untrusted) key
    untrusted_sk = Ed25519PrivateKey.generate()
    dummy_manifest = SignedRevocationManifest(
        epoch=1,
        issued_at_iso="2026-11-04T00:00:00Z",
        issuer_id="security_officer_root",
        manifest_version=1,
        revoked_authorization_ids=(),
        revoked_director_key_ids=(),
        revoked_operator_key_ids=(),
        revoked_trial_key_ids=(),
        valid_until_iso="2026-11-05T00:00:00Z",
        manifest_signature="0" * 128,
    )
    sig = untrusted_sk.sign(canonical_revocation_manifest_claims_bytes(dummy_manifest)).hex()
    manifest_data = {
        "epoch": 1,
        "issued_at_iso": "2026-11-04T00:00:00Z",
        "issuer_id": "security_officer_root",
        "manifest_version": 1,
        "revoked_authorization_ids": [],
        "revoked_director_key_ids": [],
        "revoked_operator_key_ids": [],
        "revoked_trial_key_ids": [],
        "valid_until_iso": "2026-11-05T00:00:00Z",
        "manifest_signature": sig,
    }
    raw_bytes = canonical_json_bytes(manifest_data)

    # Set raw manifest under authorized issuer public key (signature mismatch)
    oracle_client.set_raw_revocation_manifest(raw_bytes, env["rev_issuer_pk"])

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r3_01_oracle_rejects_forged_provenance_seal(env: Dict[str, Any]) -> None:
    """I17-R3-01: Local test Oracle rejects revocation evidence object with forged provenance seal."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Create dummy evidence with forged seal
    dummy_manifest = SignedRevocationManifest(
        epoch=1,
        issued_at_iso="2026-11-04T00:00:00Z",
        issuer_id="security_officer_root",
        manifest_version=1,
        revoked_authorization_ids=(),
        revoked_director_key_ids=(),
        revoked_operator_key_ids=(),
        revoked_trial_key_ids=(),
        valid_until_iso="2026-11-05T00:00:00Z",
        manifest_signature="0" * 128,
    )
    forged_evidence = RevocationManifestVerificationResult(
        verified=True,
        manifest=dummy_manifest,
        canonical_claims_digest_sha256="0" * 64,
        _provenance_seal="bad_seal_" + "0" * 55,
    )
    oracle_client.set_verified_revocation_evidence(forged_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r3_01_oracle_rejects_expired_stale_manifest(env: Dict[str, Any]) -> None:
    """I17-R3-01: Local test Oracle rejects expired/stale revocation manifest."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    past_dt = datetime.now(timezone.utc) - timedelta(hours=2)
    past_iso = past_dt.isoformat().replace("+00:00", "Z")
    far_past_iso = (past_dt - timedelta(days=1)).isoformat().replace("+00:00", "Z")

    # Manifest expired in the past
    stale_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=2,
        anchor_directory=env["anchor_dir"],
        issued_at_iso=far_past_iso,
        valid_until_iso=past_iso,
        verification_time_utc=past_dt - timedelta(hours=1),  # verified at that past moment
    )
    oracle_client.set_verified_revocation_evidence(stale_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r3_01_oracle_rejects_missing_director_or_operator_key_identity(env: Dict[str, Any]) -> None:
    """I17-R3-01: Local test Oracle rejects authorization when Director or Operator key identity is missing."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # 1. Missing director_key_id
    oracle_client.set_bound_identities(director_key_id="", operator_key_id="op_canary_key_01")
    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)
    assert gateway.call_count == 0

    # 2. Missing operator_key_id
    oracle_client.set_bound_identities(director_key_id="dir_canary_key_01", operator_key_id="")
    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)
    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r4_01_oracle_rejects_unanchored_evidence_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R4-01: Oracle rejects unanchored revocation evidence and fails closed."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Disable anchor directory on Oracle client (unanchored evidence)
    oracle_client.set_anchor_directory(None)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r4_01_oracle_rejects_epoch_rollback_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R4-01: Oracle rejects manifest with lower epoch than durable anchor (anti-rollback)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Advance anchor to epoch 5
    make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=5,
        anchor_directory=env["anchor_dir"],
    )

    # Provide validly signed manifest from epoch 4 containing NO revocations
    rolled_back_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=4,
        anchor_directory=None,
    )
    oracle_client.set_verified_revocation_evidence(rolled_back_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r4_01_oracle_rejects_tampered_epoch_digest_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R4-01: Oracle rejects manifest with same epoch but differing claims digest (tampering)."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # env anchor is at epoch 1. Create a different valid manifest at epoch 1 with different content
    tampered_evidence = make_verified_revocation(
        env["rev_issuer_sk"],
        epoch=1,
        issued_at_iso="2026-11-04T01:00:00Z",  # Different claims -> different digest
        anchor_directory=None,
    )
    oracle_client.set_verified_revocation_evidence(tampered_evidence)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r4_01_oracle_rejects_missing_anchor_file_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R4-01: Oracle rejects authorization when anchor directory has no anchor file."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Point to an empty anchor directory (no anchor file)
    empty_anchor_dir = Path(env["base_dir"]) / "empty_anchor"
    empty_anchor_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(empty_anchor_dir), 0o700)
    oracle_client.set_anchor_directory(empty_anchor_dir)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert len(list((Path(env["base_dir"]) / "receipts").glob("*.json"))) == 0


def test_i17_r4_01_oracle_verifies_valid_anchored_evidence_with_registry_loader(env: Dict[str, Any]) -> None:
    """I17-R4-01: Oracle successfully verifies authorization via registry composition loader."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    # Set up registry directory with revocations.signed.json and revocation_epoch.anchor
    reg_dir = Path(env["base_dir"]) / "revocation_registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(reg_dir), 0o700)

    # Write revocations.signed.json
    reg_manifest_file = reg_dir / "revocations.signed.json"
    reg_manifest_file.write_bytes(env["manifest_bytes"])
    os.chmod(str(reg_manifest_file), 0o600)

    # Initialize anchor in reg_dir
    check_and_advance_revocation_epoch_anchor_for_local_test(
        verified_result=env["verified_revocation"],
        anchor_directory=reg_dir,
        advance=True,
    )

    # Switch Oracle to use registry loader path
    oracle_client.set_registry_directory(reg_dir)
    oracle_client._revocation_issuer_public_key = env["rev_issuer_pk"]

    result = dispatcher.dispatch_canary_action(action, auth, plan)

    assert isinstance(result, CanaryDispatchResult)
    assert result.status == "COMMITTED"
    assert gateway.call_count == 1
    receipt = receipt_service.load_receipt(auth.execution_id)
    assert receipt.execution_id == auth.execution_id


def test_i17_r7_registry_loader_missing_anchor_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R7: Registry loader path with missing anchor fails closed with ERR_REVOCATION_ANCHOR_ABSENT."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    reg_dir = Path(env["base_dir"]) / "revocation_registry_no_anchor"
    reg_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(reg_dir), 0o700)

    reg_manifest_file = reg_dir / "revocations.signed.json"
    reg_manifest_file.write_bytes(env["manifest_bytes"])
    os.chmod(str(reg_manifest_file), 0o600)

    # Note: anchor is NOT initialized
    oracle_client.set_registry_directory(reg_dir)
    oracle_client._revocation_issuer_public_key = env["rev_issuer_pk"]

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert not (reg_dir / "revocation_epoch.anchor").exists()


def test_i17_r7_raw_manifest_missing_anchor_fails_closed(env: Dict[str, Any]) -> None:
    """I17-R7: Raw manifest path with missing anchor fails closed with ERR_REVOCATION_ANCHOR_ABSENT."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]
    receipt_service: ExecutionReceiptService = env["receipt_service"]

    action, auth, plan = make_valid_action_and_auth()
    env["coordinator"].acquire_target_lock(auth.target_timeline_uid)

    empty_anchor_dir = Path(env["base_dir"]) / "empty_anchor_raw"
    empty_anchor_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(empty_anchor_dir), 0o700)

    oracle_client.set_raw_revocation_manifest(env["manifest_bytes"], env["rev_issuer_pk"])
    oracle_client.set_anchor_directory(empty_anchor_dir)

    with pytest.raises(DispatcherOracleVerificationError, match="ERR_ORACLE_IS_VALID_NOT_TRUE"):
        dispatcher.dispatch_canary_action(action, auth, plan)

    assert gateway.call_count == 0
    with pytest.raises(ReceiptNotFoundError):
        receipt_service.load_receipt(auth.execution_id)
    assert not (empty_anchor_dir / "revocation_epoch.anchor").exists()


def test_i17_r7_all_paths_pass_require_existing_anchor_true(env: Dict[str, Any], monkeypatch) -> None:
    """I17-R7: Confirms registry loader, raw manifest, and pre-verified paths pass require_existing_anchor=True."""
    dispatcher: TrustedCanaryDispatchService = env["dispatcher"]
    oracle_client: LocalTestOracleVerificationClient = env["oracle_client"]
    gateway: FakeResolveGateway = env["gateway"]

    # Track kwargs passed to check_and_advance and load_verify_and_anchor
    calls = []

    import src.services.trusted_canary_dispatch_service as tcds

    orig_check = tcds.check_and_advance_revocation_epoch_anchor_for_local_test
    orig_load = tcds.load_verify_and_anchor_revocation_manifest_for_local_test

    def spy_check(*args, **kwargs):
        calls.append(("check_and_advance", kwargs.get("require_existing_anchor")))
        return orig_check(*args, **kwargs)

    def spy_load(*args, **kwargs):
        calls.append(("load_verify_and_anchor", kwargs.get("require_existing_anchor")))
        return orig_load(*args, **kwargs)

    monkeypatch.setattr(tcds, "check_and_advance_revocation_epoch_anchor_for_local_test", spy_check)
    monkeypatch.setattr(tcds, "load_verify_and_anchor_revocation_manifest_for_local_test", spy_load)

    # 1. Pre-verified evidence path
    action1, auth1, plan1 = make_valid_action_and_auth(
        execution_id="exec_canary_r7_01",
        target_timeline_uid="tl_canary_r7_01",
        plan_idempotency_key="1" * 64,
    )
    env["coordinator"].acquire_target_lock(auth1.target_timeline_uid)
    res1 = dispatcher.dispatch_canary_action(action1, auth1, plan1)
    assert res1.status == "COMMITTED"
    assert ("check_and_advance", True) in calls

    # 2. Raw manifest path
    calls.clear()
    oracle_client.set_raw_revocation_manifest(env["manifest_bytes"], env["rev_issuer_pk"])
    action2, auth2, plan2 = make_valid_action_and_auth(
        execution_id="exec_canary_r7_02",
        target_timeline_uid="tl_canary_r7_02",
        plan_idempotency_key="2" * 64,
    )
    env["coordinator"].acquire_target_lock(auth2.target_timeline_uid)
    res2 = dispatcher.dispatch_canary_action(action2, auth2, plan2)
    assert res2.status == "COMMITTED"
    assert ("check_and_advance", True) in calls

    # 3. Registry loader path
    calls.clear()
    reg_dir = Path(env["base_dir"]) / "revocation_registry_spy"
    reg_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(str(reg_dir), 0o700)
    reg_file = reg_dir / "revocations.signed.json"
    reg_file.write_bytes(env["manifest_bytes"])
    os.chmod(str(reg_file), 0o600)
    orig_check(verified_result=env["verified_revocation"], anchor_directory=reg_dir, advance=True)

    oracle_client.set_registry_directory(reg_dir)
    oracle_client._revocation_issuer_public_key = env["rev_issuer_pk"]
    action3, auth3, plan3 = make_valid_action_and_auth(
        execution_id="exec_canary_r7_03",
        target_timeline_uid="tl_canary_r7_03",
        plan_idempotency_key="3" * 64,
    )
    env["coordinator"].acquire_target_lock(auth3.target_timeline_uid)
    res3 = dispatcher.dispatch_canary_action(action3, auth3, plan3)
    assert res3.status == "COMMITTED"
    assert ("load_verify_and_anchor", True) in calls


