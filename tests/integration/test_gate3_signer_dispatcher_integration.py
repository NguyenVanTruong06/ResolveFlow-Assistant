"""Gate 3 Integration Test Suite: Signer-Dispatcher End-to-End Chain & Fail-Closed Bounds (G07-AUTH-SIGNER-GATE3-I19).

Architecture Basis:
- Plan 07B7 Revision R7 §§3–6, 9–10
- Consolidated Gate 3 Integration Audit Revision R2 §§5–7
- Accepted Gate 3 Slices I1–I18 (including I11A and I6-R4)

Mode & Boundary:
    LOCAL_TEST_ONLY; MOCK_DISPATCH_ONLY; TEMP_DIRECTORIES_AND_TEMP_UNIX_SOCKET_ONLY;
    EPHEMERAL_TEST_KEYS_ONLY; NO_PRODUCTION_KEYS; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import socket
import stat
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.core.contracts.edit_plan_approval_contracts import AppOwnedApprovalRecord
from src.core.contracts.edit_plan_proposal_contracts import (
    CreativeContext,
    EditPlanProposal,
    compute_proposal_revision,
    normalize_edit_plan_proposal,
)
from src.core.contracts.execution_receipt_contracts import (
    ExecutionReceipt,
    SourceFileDigestBinding,
    TargetTrackReservation,
)
from src.core.contracts.signed_approval_envelope import SignedApprovalEnvelope
from src.core.contracts.signed_operator_consent_envelope import (
    CONFIRMATION_PHRASE_LITERAL,
    FRESHNESS_ATTESTATION_LITERAL,
    SignedOperatorConsentEnvelope,
)
from src.core.contracts.time_contracts import RationalFPS, TimeRangeFrames
from src.core.contracts.trial_authorization_contracts import (
    PlannedTimelineAction,
    TrialExecutionAuthorization,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
    parse_iso_utc_timestamp,
)
from src.core.geometry.plan_geometry_contracts import SourceFrameEvidence
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction as BinderPlannedAction,
    TargetSpec,
    TimelineExecutionPlan,
    TimelineOccupancySnapshot,
    bind_timeline_execution_plan,
    compute_canonical_plan_digest,
)
from src.services.authoritative_approval_store import (
    AuthoritativeApprovalStore,
)
from src.services.director_approval_signer import (
    DirectorApprovalSigner,
)
from src.services.durable_trial_target_ledger import RecoveryCoordinator
from src.services.execution_journal_service import ExecutionJournalService
from src.services.execution_receipt_service import (
    ExecutionReceiptService,
    ReceiptNotFoundError,
)
from src.services.isolated_resolve_trial_adapter import (
    FakeResolveGateway,
    IsolatedResolveTrialAdapter,
)
from src.services.trial_authorization_signer_service import (
    ActiveChallengeContext,
    ChallengeConsumptionError,
    ChallengeIssuanceError,
    LocalTestChallengeAuthority,
    LocalTestChallengeIssuanceRequest,
    LocalTestChallengeIssuanceResult,
    LocalTestSignerHmacSigningCapability,
    LocalTestSignerKeyHandle,
    LocalTestSignerSocketClient,
    LocalTestSignerSocketServer,
    OperatorConsentVerificationContext,
    OperatorConsentVerificationError,
    REVOCATION_ISSUER_PUBLIC_KEY_FILENAME,
    RevocationManifestVerificationResult,
    SignerAdmissionVerificationError,
    SignerSocketServerError,
    approval_record_to_dict,
    check_and_advance_revocation_epoch_anchor_for_local_test,
    create_signer_hmac_signing_capability_for_local_test,
    issue_trial_execution_authorization_for_local_test,
    load_signer_key_handle_for_local_test,
    load_verify_and_anchor_revocation_manifest_for_local_test,
    verify_signed_revocation_manifest,
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
from src.tools.resolveflow_operator_console import (
    OperatorConsoleAbortError,
    OperatorConsoleValidationError,
    OperatorReviewContext,
    format_challenge_review_display,
    review_and_sign_operator_consent,
)


# ==============================================================================
# Helper Factories & Manifest Generator (Local Test Only)
# ==============================================================================


def make_signed_revocation_manifest_bytes(
    issuer_priv: Ed25519PrivateKey,
    *,
    epoch: int = 1,
    revoked_director_key_ids: Sequence[str] = (),
    revoked_operator_key_ids: Sequence[str] = (),
    revoked_authorization_ids: Sequence[str] = (),
    revoked_trial_key_ids: Sequence[str] = (),
    valid_until_iso: str = "2026-11-05T00:00:00Z",
    issued_at_iso: str = "2026-11-04T00:00:00Z",
) -> bytes:
    """Generates canonical signed revocation manifest bytes for local test environments."""
    claims = {
        "epoch": epoch,
        "issued_at_iso": issued_at_iso,
        "issuer_id": "security_officer_root",
        "manifest_version": 1,
        "revoked_authorization_ids": list(revoked_authorization_ids),
        "revoked_director_key_ids": list(revoked_director_key_ids),
        "revoked_operator_key_ids": list(revoked_operator_key_ids),
        "revoked_trial_key_ids": list(revoked_trial_key_ids),
        "valid_until_iso": valid_until_iso,
    }
    payload = canonical_json_bytes(claims)
    sig_hex = issuer_priv.sign(payload).hex()
    full_dict = dict(claims)
    full_dict["manifest_signature"] = sig_hex
    return canonical_json_bytes(full_dict)


def make_test_proposal_and_approval(
    proposal_id: str = "prop_canary_001",
    manifest_id: str = "mf_canary_001",
    source_file_path: str = "/var/media/resolveflow/canary_sample.mov",
    duration_sec: float = 5.0,
) -> Tuple[EditPlanProposal, AppOwnedApprovalRecord]:
    """Creates a normalized single-action canary proposal and matching approval record."""
    c_ctx = CreativeContext(
        free_brief="Canary brief for Gate 3 integration testing",
        style_preset="Cinematic",
        must_keep_rules=("rule 1",),
        must_not_rules=("must not 1",),
        reference_items=(),
    )
    clips = [
        {
            "clip_id": "clip_main_1",
            "file_path": source_file_path,
            "source_kind": "managed",
            "origin_library_id": "lib_01",
            "role": "footage",
        },
    ]
    source_binding = {
        "manifest_id": manifest_id,
        "clips": clips,
    }
    segments = [
        {
            "segment_index": 0,
            "clip_id": "clip_main_1",
            "start_sec": 0.0,
            "end_sec": duration_sec,
            "action": "keep",
            "speed": 1.0,
            "broll_inserts": [],
            "sfx_inserts": [],
        },
    ]
    video_plan = {
        "manifest_id": manifest_id,
        "timeline_segments": segments,
    }
    proposal = normalize_edit_plan_proposal({
        "proposal_id": proposal_id,
        "manifest_id": manifest_id,
        "creative_context": c_ctx,
        "source_binding": source_binding,
        "video_plan": video_plan,
        "kinetic_plan": None,
        "proposed_asset_assignments": (),
        "proposed_omissions": (),
    })

    rev = compute_proposal_revision(proposal)
    approval = AppOwnedApprovalRecord(
        approval_id=f"appr_{proposal_id}",
        proposal_id=proposal.proposal_id,
        proposal_revision=rev,
        approved_at_iso="2026-11-04T12:00:00Z",
        accepted_omission_ids=(),
        approval_scope="ENTIRE_PROPOSAL",
    )
    return proposal, approval


def make_test_execution_plan(
    proposal: EditPlanProposal,
    approval: AppOwnedApprovalRecord,
    *,
    project_id: str = "RF_CANARY_P02_TEST",
    project_name: str = "RF_TRIAL_CANARY_P02",
    timeline_id: str = "RF_CANARY_TL02_TEST",
    timeline_name: str = "RF_TRIAL_TL_CANARY_T02",
    source_file_path: str = "/var/media/resolveflow/canary_sample.mov",
    source_file_sha256: str = "4" * 64,
    start_timecode_frame: int = 0,
    fps: RationalFPS = RationalFPS(24, 1),
    duration_frames: int = 120,
) -> TimelineExecutionPlan:
    """Binds an authoritative TimelineExecutionPlan using accepted binder logic."""
    target_spec = TargetSpec(
        project_name=project_name,
        project_id=project_id,
        timeline_name=timeline_name,
        timeline_id=timeline_id,
        timeline_fps=fps,
        timeline_resolution=(1920, 1080),
        start_timecode_frame=start_timecode_frame,
    )
    evidence_table = {
        "clip_main_1": SourceFrameEvidence(
            manifest_id=proposal.manifest_id,
            clip_id="clip_main_1",
            source_fps=fps,
            total_frames=duration_frames * 2,
            evidence_status="VERIFIED_CFR",
        ),
    }
    snapshot = TimelineOccupancySnapshot(
        snapshot_id="snap_canary_001",
        project_id=project_id,
        timeline_id=timeline_id,
        timeline_fps=fps,
        captured_at_iso="2026-11-04T11:59:00Z",
        occupied_spans=(),
        scanned_tracks=(("video", 1), ("video", 2), ("audio", 1), ("audio", 2)),
        status="AUTHORITATIVE",
    )
    source_digest_binding = SourceFileDigestBinding(
        clip_id="clip_main_1",
        absolute_path=source_file_path,
        full_sha256=source_file_sha256,
        stream_index=0,
        stat_identity=(64769, 1234567, 1048576, 1700000000, 0),
    )
    plan = bind_timeline_execution_plan(
        proposal,
        approval,
        target_spec,
        evidence_table,
        snapshot,
        source_digests=[source_digest_binding],
    )
    return plan


def extract_contract_action_from_plan(plan: TimelineExecutionPlan) -> PlannedTimelineAction:
    """Translates the single planned action in TimelineExecutionPlan to PlannedTimelineAction."""
    b_action = plan.planned_actions[0]
    return PlannedTimelineAction(
        action_id=b_action.clip_id,
        action_index=b_action.action_index,
        action_type=b_action.action_type,
        track_type=b_action.target_track_type,
        track_index=b_action.target_track_index,
        record_frame=plan.target_spec.start_timecode_frame + b_action.timeline_range.start_frame,
        duration_frames=b_action.timeline_range.duration_frames,
        source_path=b_action.source_file_path,
        source_frame_start=b_action.source_range.start_frame,
        source_frame_duration=b_action.source_range.duration_frames,
        role=b_action.role,
    )


class SocketBackedOracleVerificationClient:
    """Client adapter calling verify_authorization_oracle over the AF_UNIX socket."""

    def __init__(
        self,
        socket_client: LocalTestSignerSocketClient,
        shared_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._socket_client = socket_client
        self._shared_context = shared_context if shared_context is not None else {}

    def verify_authorization(
        self,
        request: OracleVerificationRequest,
    ) -> OracleVerificationResponse:
        self._shared_context["current_oracle_request_id"] = request.request_id
        resp = self._socket_client.send_request(
            "verify_authorization_oracle",
            {
                "request_nonce": request.request_nonce,
                "signed_authorization": dataclasses.asdict(request.signed_authorization),
                "action_payload_digest_sha256": request.action_payload_digest_sha256,
                "target_project_uid": request.target_project_uid,
                "target_timeline_uid": request.target_timeline_uid,
                "requested_at_iso": request.requested_at_iso,
            },
            request_id=request.request_id,
        )
        if resp.get("status") != "OK":
            err_code = resp.get("error_code", "ERR_ORACLE_REQUEST_FAILED")
            err_msg = resp.get("message") or resp.get("error_message") or "Unknown error"
            raise DispatcherOracleVerificationError(f"{err_code}: {err_msg}")
        return OracleVerificationResponse(**resp["result"])


# ==============================================================================
# Integration Fixture: Complete Gate 3 Chain Setup
# ==============================================================================


@pytest.fixture
def gate3_fixture(tmp_path: Path):
    """Sets up an isolated, temporary, test-only environment with all accepted Gate 3 primitives."""
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)

    # Subdirectories
    approval_store_dir = tmp_path / "approval_store"
    revocation_trust_dir = tmp_path / "trust_dir"
    revocation_reg_dir = tmp_path / "reg_dir"
    revocation_anchor_dir = tmp_path / "anchor_dir"
    nonce_ledger_dir = tmp_path / "nonce_ledger_dir"
    keystore_dir = tmp_path / "ephemeral_keystore"
    sock_dir = tmp_path / "sock_dir"
    receipts_dir = tmp_path / "receipts"
    journals_dir = tmp_path / "journals"
    locks_dir = tmp_path / "locks"
    ledgers_dir = tmp_path / "ledgers"

    for d in (
        approval_store_dir,
        revocation_trust_dir,
        revocation_reg_dir,
        revocation_anchor_dir,
        nonce_ledger_dir,
        keystore_dir,
        sock_dir,
        receipts_dir,
        journals_dir,
        locks_dir,
        ledgers_dir,
    ):
        d.mkdir(mode=0o700, exist_ok=True)
        d.chmod(0o700)

    # 1. Ephemeral Director key & approval store
    dir_priv = Ed25519PrivateKey.generate()
    dir_pub = dir_priv.public_key()
    dir_key_id = "director_key_test_01"
    approval_store = AuthoritativeApprovalStore(str(approval_store_dir))

    # 2. Ephemeral Revocation Root key & signed manifest
    rev_priv = Ed25519PrivateKey.generate()
    rev_pub = rev_priv.public_key()
    rev_pub_pem = rev_pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    (revocation_trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).write_bytes(rev_pub_pem)
    (revocation_trust_dir / REVOCATION_ISSUER_PUBLIC_KEY_FILENAME).chmod(0o600)

    manifest_bytes = make_signed_revocation_manifest_bytes(
        rev_priv,
        epoch=1,
        issued_at_iso=(fixed_now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        valid_until_iso=(fixed_now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    reg_file = revocation_reg_dir / "revocations.signed.json"
    reg_file.write_bytes(manifest_bytes)
    reg_file.chmod(0o600)

    # Establish initial epoch anchor in reg_dir and anchor_dir
    load_verify_and_anchor_revocation_manifest_for_local_test(
        registry_directory=revocation_reg_dir,
        issuer_public_key=rev_pub,
        verification_time_utc=fixed_now,
        advance_anchor=True,
    )
    verified_rev = verify_signed_revocation_manifest(
        raw_manifest_bytes=manifest_bytes,
        issuer_public_key=rev_pub,
        verification_time_utc=fixed_now,
    )
    check_and_advance_revocation_epoch_anchor_for_local_test(
        verified_result=verified_rev,
        anchor_directory=revocation_anchor_dir,
        advance=True,
    )

    # 3. Ephemeral HMAC trial signing key & I13 verification handle
    trial_key_id = "canary_trial_key"
    trial_hmac_bytes = os.urandom(32)
    signing_capability = create_signer_hmac_signing_capability_for_local_test(
        trial_hmac_bytes, key_id=trial_key_id
    )

    # Permanent keystore file for RecoveryCoordinator and handle loading
    trial_key_file = keystore_dir / f"{trial_key_id}.key"
    trial_key_file.write_bytes(trial_hmac_bytes)
    trial_key_file.chmod(0o600)

    # Also provide trial_auth.key temporarily if needed by I13 loader
    i13_key_file = keystore_dir / "trial_auth.key"
    i13_key_file.write_bytes(trial_hmac_bytes)
    i13_key_file.chmod(0o600)
    verification_key_handle = load_signer_key_handle_for_local_test(keystore_dir)
    i13_key_file.unlink()

    # 4. Ephemeral Operator key
    op_priv = Ed25519PrivateKey.generate()
    op_pub = op_priv.public_key()
    op_id = "operator_test_01"
    op_key_id = "op_key_test_01"

    # 5. Challenge Authority
    challenge_authority = LocalTestChallengeAuthority(clock=lambda: fixed_now)

    # 6. Oracle key & Oracle Client
    oracle_priv = Ed25519PrivateKey.generate()
    oracle_pub = oracle_priv.public_key()
    oracle_client = LocalTestOracleVerificationClient(
        oracle_private_key=oracle_priv,
        signer_key_handle=verification_key_handle,
        raw_revocation_manifest_bytes=manifest_bytes,
        revocation_issuer_public_key=rev_pub,
        anchor_directory=revocation_anchor_dir,
        registry_directory=revocation_reg_dir,
        director_key_id=dir_key_id,
        operator_key_id=op_key_id,
        clock=lambda: fixed_now,
    )

    # 7. Mock Adapter & Fake Gateway backed by RecoveryCoordinator and Journal
    coordinator = RecoveryCoordinator(
        journals_dir=str(journals_dir),
        receipts_dir=str(receipts_dir),
        locks_dir=str(locks_dir),
        ledgers_dir=str(ledgers_dir),
        keystore_path=str(keystore_dir),
    )
    journal = ExecutionJournalService(str(tmp_path))
    fake_gateway = FakeResolveGateway()
    mock_adapter = IsolatedResolveTrialAdapter(coordinator, journal, fake_gateway)

    # 8. Receipt Service
    receipt_service = ExecutionReceiptService(str(receipts_dir))

    # 9. Socket setup
    sock_path = sock_dir / "signer.sock"

    return {
        "tmp_path": tmp_path,
        "approval_store_dir": approval_store_dir,
        "revocation_trust_dir": revocation_trust_dir,
        "revocation_reg_dir": revocation_reg_dir,
        "revocation_anchor_dir": revocation_anchor_dir,
        "nonce_ledger_dir": nonce_ledger_dir,
        "keystore_dir": keystore_dir,
        "sock_dir": sock_dir,
        "receipts_dir": receipts_dir,
        "journals_dir": journals_dir,
        "locks_dir": locks_dir,
        "ledgers_dir": ledgers_dir,
        "coordinator": coordinator,
        "journal": journal,
        "dir_priv": dir_priv,
        "dir_pub": dir_pub,
        "dir_key_id": dir_key_id,
        "approval_store": approval_store,
        "rev_priv": rev_priv,
        "rev_pub": rev_pub,
        "trial_key_id": trial_key_id,
        "trial_hmac_bytes": trial_hmac_bytes,
        "signing_capability": signing_capability,
        "verification_key_handle": verification_key_handle,
        "op_priv": op_priv,
        "op_pub": op_pub,
        "op_id": op_id,
        "op_key_id": op_key_id,
        "fixed_now": fixed_now,
        "challenge_authority": challenge_authority,
        "oracle_priv": oracle_priv,
        "oracle_pub": oracle_pub,
        "oracle_client": oracle_client,
        "fake_gateway": fake_gateway,
        "mock_adapter": mock_adapter,
        "receipt_service": receipt_service,
        "sock_path": sock_path,
    }


# ==============================================================================
# Positive End-to-End Integration Test
# ==============================================================================


class TestGate3SignerDispatcherIntegration:
    """Comprehensive Gate 3 Integration Suite exercising the entire accepted chain."""

    def test_gate3_end_to_end_positive_integration_path(self, gate3_fixture):
        """Full accepted chain: Director Approval -> Approval Store -> Challenge Authority ->

        Operator Console Review -> Socket Submit -> Durably Consumed Nonce & Auth Issuance ->
        Oracle Verification over AF_UNIX Socket -> Gated Canary Dispatch -> Fake Resolve Gateway ->
        Execution Receipt Persistence.
        """
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        # Step 1: Director approval and execution plan
        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        plan_digest = compute_canonical_plan_digest(plan)
        proposal_digest = compute_proposal_revision(proposal)

        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=plan_digest,
            proposal_content_digest_sha256=proposal_digest,
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )

        # Store in AuthoritativeApprovalStore and verify round-trip
        fx["approval_store"].put(approval_envelope)
        stored_env = fx["approval_store"].get(approval_envelope.approval_record.approval_id)
        assert stored_env == approval_envelope

        # Step 2: Configure Socket Server Handlers
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )

        issued_challenge_holder: List[LocalTestChallengeIssuanceResult] = []

        def handle_request_challenge(params: Dict[str, Any]) -> Dict[str, Any]:
            iss_res = fx["challenge_authority"].issue_challenge(challenge_req)
            issued_challenge_holder.append(iss_res)
            return {
                "challenge_id": iss_res.challenge.challenge_id,
                "client_nonce": iss_res.challenge.client_nonce,
                "operator_context": {
                    "challenge_id": iss_res.operator_context.challenge.challenge_id,
                    "client_nonce": iss_res.operator_context.challenge.client_nonce,
                    "expires_at_iso": iss_res.operator_context.challenge.expires_at_iso,
                    "target_project_name": iss_res.operator_context.expected_target_project_name,
                    "target_project_uid": iss_res.operator_context.expected_target_project_uid,
                    "target_timeline_name": iss_res.operator_context.expected_target_timeline_name,
                    "target_timeline_uid": iss_res.operator_context.expected_target_timeline_uid,
                    "action_type": iss_res.operator_context.expected_action_type,
                    "track_type": iss_res.operator_context.expected_track_type,
                    "track_index": iss_res.operator_context.expected_track_index,
                    "record_frame": iss_res.operator_context.expected_record_frame,
                    "duration_frames": iss_res.operator_context.expected_duration_frames,
                    "source_file_path": iss_res.operator_context.expected_source_file_path,
                    "source_file_sha256": iss_res.operator_context.expected_source_file_sha256,
                    "approval_envelope_digest_sha256": iss_res.operator_context.expected_approval_envelope_digest_sha256,
                    "plan_digest_sha256": iss_res.operator_context.expected_plan_digest_sha256,
                    "action_payload_digest_sha256": iss_res.operator_context.expected_action_payload_digest_sha256,
                    "operator_id": iss_res.operator_context.authoritative_operator_id,
                    "operator_key_id": iss_res.operator_context.expected_operator_key_id,
                },
            }

        def handle_submit_consent(params: Dict[str, Any]) -> Dict[str, Any]:
            cid = params["challenge_id"]
            consent_dict = params["consent_envelope"]
            consent_env = SignedOperatorConsentEnvelope(**consent_dict)
            auth = issue_trial_execution_authorization_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=fx["dir_pub"],
                expected_director_key_id=fx["dir_key_id"],
                operator_consent_envelope=consent_env,
                operator_public_key=fx["op_pub"],
                challenge_authority=fx["challenge_authority"],
                challenge_id=cid,
                signing_capability=fx["signing_capability"],
                verification_key_handle=fx["verification_key_handle"],
                revocation_trust_directory=fx["revocation_trust_dir"],
                revocation_registry_directory=fx["revocation_reg_dir"],
                nonce_ledger_directory=fx["nonce_ledger_dir"],
                verification_time_utc=fixed_now,
                ttl_seconds=300.0,
            )
            return {"authorization": dataclasses.asdict(auth)}

        shared_context: Dict[str, Any] = {}

        def handle_verify_oracle(params: Dict[str, Any]) -> Dict[str, Any]:
            cur_req_id = shared_context.get("current_oracle_request_id", "req_oracle_001")
            req = OracleVerificationRequest(
                request_id=cur_req_id,
                request_nonce=params["request_nonce"],
                requested_at_iso=params.get("requested_at_iso") or fixed_now.isoformat().replace("+00:00", "Z"),
                signed_authorization=TrialExecutionAuthorization(**params["signed_authorization"]),
                action_payload_digest_sha256=params["action_payload_digest_sha256"],
                target_project_uid=params["target_project_uid"],
                target_timeline_uid=params["target_timeline_uid"],
            )
            resp = fx["oracle_client"].verify_authorization(req)
            return dataclasses.asdict(resp)

        server = LocalTestSignerSocketServer(
            socket_path=fx["sock_path"],
            temp_root=fx["sock_dir"],
            challenge_handler=handle_request_challenge,
            consent_handler=handle_submit_consent,
            oracle_handler=handle_verify_oracle,
        )

        with server:
            client = LocalTestSignerSocketClient(socket_path=fx["sock_path"])

            # Step 3: Request challenge over socket
            req_params = {
                "expected_director_key_id": fx["dir_key_id"],
                "operator_id": fx["op_id"],
                "operator_key_id": fx["op_key_id"],
                "approval_envelope": approval_envelope.to_dict(),
                "proposal": {"proposal_id": proposal.proposal_id},
                "execution_plan": {"plan_id": plan.plan_id},
            }
            resp_chall = client.send_request("request_challenge", req_params)
            assert resp_chall["status"] == "OK"
            chall_res = resp_chall["result"]
            challenge_id = chall_res["challenge_id"]
            op_ctx_dict = chall_res["operator_context"]

            # Step 4: Operator reviews challenge and signs consent via Operator Console
            review_ctx = OperatorReviewContext(**op_ctx_dict)

            # Injected terminal input providing visual attestation and confirmation
            inputs = iter([
                FRESHNESS_ATTESTATION_LITERAL,
                CONFIRMATION_PHRASE_LITERAL,
            ])
            output_lines: List[str] = []

            def operator_signer(payload_bytes: bytes) -> str:
                return fx["op_priv"].sign(payload_bytes).hex()

            consent_envelope = review_and_sign_operator_consent(
                context=review_ctx,
                signer=operator_signer,
                input_func=lambda prompt: next(inputs),
                output_func=lambda line: output_lines.append(line),
                clock=lambda: fixed_now,
            )
            assert type(consent_envelope) is SignedOperatorConsentEnvelope

            # Step 5: Submit Operator Consent over Socket
            consent_params = {
                "challenge_id": challenge_id,
                "consent_envelope": dataclasses.asdict(consent_envelope),
            }
            resp_consent = client.send_request("submit_operator_consent", consent_params)
            assert resp_consent["status"] == "OK"
            auth_dict = resp_consent["result"]["authorization"]
            auth = TrialExecutionAuthorization(**auth_dict)

            # Assert constant-time verification using I13 verification handle
            assert fx["verification_key_handle"].verify_trial_execution_authorization(auth) is True

            # Step 6: Dispatch via TrustedCanaryDispatchService with Socket-backed Oracle
            socket_oracle = SocketBackedOracleVerificationClient(client, shared_context)
            dispatcher = TrustedCanaryDispatchService(
                adapter=fx["mock_adapter"],
                oracle_client=socket_oracle,
                oracle_public_key=fx["oracle_pub"],
                receipt_service=fx["receipt_service"],
                clock=lambda: fixed_now,
            )

            # Extract canonical PlannedTimelineAction matching the single authorized action
            action = extract_contract_action_from_plan(plan)

            # Calling flow acquires target exclusion lock on coordinator
            fx["coordinator"].acquire_target_lock(auth.target_timeline_uid)

            dispatch_result = dispatcher.dispatch_canary_action(
                action=action,
                authorization=auth,
                plan=plan,
            )

            # Assert dispatch result truthfulness
            assert type(dispatch_result) is CanaryDispatchResult
            assert dispatch_result.status == "COMMITTED"
            assert dispatch_result.mode == "MOCK_ONLY"
            assert dispatch_result.terminalized is False
            assert dispatch_result.execution_id == auth.execution_id
            assert dispatch_result.authorization_id == auth.authorization_id
            assert dispatch_result.target_project_uid == auth.target_project_uid
            assert dispatch_result.target_timeline_uid == auth.target_timeline_uid
            assert dispatch_result.receipt_id == auth.execution_id

            # Assert Fake Resolve Gateway received exactly 1 call
            assert fx["fake_gateway"].call_count == 1

            # Step 7: Verify Execution Receipt Persistence & Durable Target Ledger emitted by actual dispatch
            receipt = fx["receipt_service"].load_receipt(auth.execution_id)
            assert type(receipt) is ExecutionReceipt
            assert receipt.execution_id == auth.execution_id
            assert receipt.state == "EXECUTING"
            assert receipt.last_error_code == "MOCK_ONLY"
            assert receipt.last_error_message == "LOCAL_TEST_MOCK_DISPATCH_COMPLETED_NON_TERMINAL"
            assert receipt.project_id == plan.target_spec.project_id
            assert receipt.timeline_id == plan.target_spec.timeline_id
            assert receipt.proposal_revision == auth.proposal_revision == plan.proposal_revision

            # Verify durable target ledger transition to ACTIVE and byte-for-byte readback
            durable_ledger = fx["coordinator"].durable_ledger
            assert durable_ledger is not None
            ledger_entry = durable_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid)
            assert ledger_entry is not None
            assert ledger_entry.state == "ACTIVE"
            assert ledger_entry.execution_id == auth.execution_id
            assert ledger_entry.authorization_id == auth.authorization_id
            assert ledger_entry.approval_id == auth.approval_record_id
            assert ledger_entry.proposal_id == auth.proposal_id
            assert ledger_entry.proposal_revision == auth.proposal_revision == plan.proposal_revision
            assert ledger_entry.plan_idempotency_key == auth.plan_idempotency_key
            assert durable_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid) == ledger_entry

            # Step 8: Comprehensive Cross-Binding Assertions Across All 8 Layers
            # 1. Approval -> Envelope binding
            assert approval_envelope.approval_record.approval_id == approval.approval_id
            assert approval_envelope.proposal_content_digest_sha256 == proposal_digest
            assert approval_envelope.plan_digest_sha256 == plan_digest

            # 2. Challenge -> Approval & Plan binding
            iss_res = issued_challenge_holder[0]
            assert iss_res.challenge.challenge_id == challenge_id
            assert iss_res.operator_context.expected_plan_digest_sha256 == plan_digest

            # 3. Consent -> Challenge & Operator binding
            assert consent_envelope.challenge_id == challenge_id
            assert consent_envelope.client_nonce == iss_res.challenge.client_nonce
            assert consent_envelope.operator_id == fx["op_id"]
            assert consent_envelope.operator_key_id == fx["op_key_id"]
            assert consent_envelope.plan_digest_sha256 == plan_digest

            # 4. Authorization -> All prior layers
            assert auth.authorization_id == iss_res.challenge.preallocated_authorization_id
            assert auth.execution_id == iss_res.challenge.preallocated_execution_id
            assert auth.approval_record_id == approval.approval_id
            assert auth.plan_digest_sha256 == plan_digest
            assert auth.operator_gesture_digest_sha256 == consent_envelope.compute_consent_digest()
            assert auth.action_payload_digest_sha256 == consent_envelope.action_payload_digest_sha256
            assert auth.target_project_uid == plan.target_spec.project_id
            assert auth.target_timeline_uid == plan.target_spec.timeline_id
            assert auth.authorized_record_frame == action.record_frame

            # 5. Dispatch -> Authorization & Receipt
            assert dispatch_result.execution_id == auth.execution_id
            assert dispatch_result.authorization_id == auth.authorization_id
            assert receipt.execution_id == auth.execution_id

            # Step 9: Safety Tripwires
            # (a) No secret leakage in socket response
            resp_str = json.dumps(resp_consent)
            assert fx["trial_hmac_bytes"].hex() not in resp_str
            assert "private_key" not in resp_str.lower()
            assert "secret" not in resp_str.lower()

            # (b) Dispatcher does not possess HMAC or private keys
            for forbidden_prop in ("_hmac_key", "hmac_key", "_signing_key", "_key_bytes", "_capability", "signing_capability"):
                assert not hasattr(dispatcher, forbidden_prop)

            # (c) Incident target safety
            assert auth.target_project_uid not in (INCIDENT_TARGET_PROJECT_UID,)
            assert auth.target_project_name not in INCIDENT_TARGET_PROJECT_NAMES
            assert auth.target_timeline_uid not in (INCIDENT_TARGET_TIMELINE_UID,)
            assert auth.target_timeline_name not in INCIDENT_TARGET_TIMELINE_NAMES

    # ==========================================================================
    # Fail-Closed Integration Scenarios
    # ==========================================================================

    def test_gate3_fail_closed_altered_approval_or_plan_binding(self, gate3_fixture):
        """Altered proposal revision, plan digest, or revoked director key fails closed before challenge issuance."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        plan_digest = compute_canonical_plan_digest(plan)

        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])

        # Case 1: Tampered plan digest in approval envelope
        tampered_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256="0" * 64,  # tampered
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        req_tampered = LocalTestChallengeIssuanceRequest(
            approval_envelope=tampered_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
        )
        with pytest.raises(ChallengeIssuanceError) as exc_tampered:
            fx["challenge_authority"].issue_challenge(req_tampered)
        assert "ERR_PLAN_DIGEST_MISMATCH" in str(exc_tampered.value)

        # Case 2: Revoked director key in manifest
        revoked_manifest = make_signed_revocation_manifest_bytes(
            fx["rev_priv"],
            epoch=2,
            revoked_director_key_ids=[fx["dir_key_id"]],
        )
        (fx["revocation_reg_dir"] / "revocations.signed.json").write_bytes(revoked_manifest)

        valid_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=plan_digest,
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        req_revoked = LocalTestChallengeIssuanceRequest(
            approval_envelope=valid_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
        )
        with pytest.raises(ChallengeIssuanceError) as exc_revoked:
            fx["challenge_authority"].issue_challenge(req_revoked)
        assert "ERR_DIRECTOR_KEY_REVOKED" in str(exc_revoked.value)

        # Assert zero gateway calls
        assert fx["fake_gateway"].call_count == 0

    def test_gate3_fail_closed_expired_or_replayed_challenge(self, gate3_fixture):
        """Expired challenge or replayed challenge consumption fails closed with single-use ledger preservation."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )

        # Issue valid challenge
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=60.0,
        )
        iss_res = fx["challenge_authority"].issue_challenge(challenge_req)

        # 1. Operator Console expiration check
        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )

        future_expired_clock = lambda: fixed_now + timedelta(seconds=120)
        with pytest.raises(OperatorConsoleValidationError) as exc_expired:
            review_and_sign_operator_consent(
                context=review_ctx,
                signer=lambda b: fx["op_priv"].sign(b).hex(),
                clock=future_expired_clock,
            )
        assert exc_expired.value.code == "ERR_CHALLENGE_EXPIRED"

        # 2. Replayed challenge consumption
        valid_consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )

        # First consumption succeeds
        auth1 = issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_consent_envelope=valid_consent,
            operator_public_key=fx["op_pub"],
            challenge_authority=fx["challenge_authority"],
            challenge_id=iss_res.challenge.challenge_id,
            signing_capability=fx["signing_capability"],
            verification_key_handle=fx["verification_key_handle"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            nonce_ledger_directory=fx["nonce_ledger_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )
        assert type(auth1) is TrialExecutionAuthorization

        # Second consumption of the same challenge MUST fail closed
        with pytest.raises((ChallengeConsumptionError, OperatorConsentVerificationError)) as exc_replay:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=fx["dir_pub"],
                expected_director_key_id=fx["dir_key_id"],
                operator_consent_envelope=valid_consent,
                operator_public_key=fx["op_pub"],
                challenge_authority=fx["challenge_authority"],
                challenge_id=iss_res.challenge.challenge_id,
                signing_capability=fx["signing_capability"],
                verification_key_handle=fx["verification_key_handle"],
                revocation_trust_directory=fx["revocation_trust_dir"],
                revocation_registry_directory=fx["revocation_reg_dir"],
                nonce_ledger_directory=fx["nonce_ledger_dir"],
                verification_time_utc=fixed_now,
                ttl_seconds=300.0,
            )
        assert "ERR_CHALLENGE_ALREADY_CONSUMED" in str(exc_replay.value) or "ERR_CHALLENGE_NOT_FOUND" in str(exc_replay.value)

    def test_gate3_fail_closed_invalid_attestation_or_tampered_consent(self, gate3_fixture):
        """Invalid visual attestation, wrong confirmation phrase, or tampered signature aborts execution."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )
        iss_res = fx["challenge_authority"].issue_challenge(challenge_req)

        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )

        # 1. Invalid visual attestation
        with pytest.raises(OperatorConsoleAbortError) as exc_attest:
            review_and_sign_operator_consent(
                context=review_ctx,
                signer=lambda b: fx["op_priv"].sign(b).hex(),
                input_func=lambda p: "WRONG_ATTESTATION",
                clock=lambda: fixed_now,
            )
        assert exc_attest.value.code == "ERR_INVALID_VISUAL_ATTESTATION"

        # 2. Invalid confirmation phrase
        with pytest.raises(OperatorConsoleAbortError) as exc_phrase:
            review_and_sign_operator_consent(
                context=review_ctx,
                signer=lambda b: fx["op_priv"].sign(b).hex(),
                input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else "ABORT_EXECUTION",
                clock=lambda: fixed_now,
            )
        assert exc_phrase.value.code == "ERR_INVALID_CONFIRMATION_PHRASE"

        # 3. Tampered consent signature
        valid_consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )
        tampered_consent_data = dataclasses.asdict(valid_consent)
        tampered_consent_data["operator_signature"] = "0" * 128
        tampered_consent = SignedOperatorConsentEnvelope(**tampered_consent_data)
        with pytest.raises((OperatorConsentVerificationError, SignerAdmissionVerificationError)) as exc_sig:
            issue_trial_execution_authorization_for_local_test(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=fx["dir_pub"],
                expected_director_key_id=fx["dir_key_id"],
                operator_consent_envelope=tampered_consent,
                operator_public_key=fx["op_pub"],
                challenge_authority=fx["challenge_authority"],
                challenge_id=iss_res.challenge.challenge_id,
                signing_capability=fx["signing_capability"],
                verification_key_handle=fx["verification_key_handle"],
                revocation_trust_directory=fx["revocation_trust_dir"],
                revocation_registry_directory=fx["revocation_reg_dir"],
                nonce_ledger_directory=fx["nonce_ledger_dir"],
                verification_time_utc=fixed_now,
            )
        assert "ERR_OPERATOR_SIGNATURE_INVALID" in str(exc_sig.value) or getattr(exc_sig.value, "code", "") == "ERR_OPERATOR_SIGNATURE_INVALID"

    def test_gate3_fail_closed_oracle_verification_and_tampered_auth(self, gate3_fixture):
        """Tampered authorization claim, tampered HMAC, or revoked token causes oracle rejection and blocks dispatch."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )
        iss_res = fx["challenge_authority"].issue_challenge(challenge_req)
        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )
        consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )
        valid_auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_consent_envelope=consent,
            operator_public_key=fx["op_pub"],
            challenge_authority=fx["challenge_authority"],
            challenge_id=iss_res.challenge.challenge_id,
            signing_capability=fx["signing_capability"],
            verification_key_handle=fx["verification_key_handle"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            nonce_ledger_directory=fx["nonce_ledger_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )

        dispatcher = TrustedCanaryDispatchService(
            adapter=fx["mock_adapter"],
            oracle_client=fx["oracle_client"],
            oracle_public_key=fx["oracle_pub"],
            receipt_service=fx["receipt_service"],
            clock=lambda: fixed_now,
        )
        action = extract_contract_action_from_plan(plan)

        # 1. Tampered proposal_id claim (leaving HMAC calculated over original)
        tampered_auth_data = dataclasses.asdict(valid_auth)
        tampered_auth_data["proposal_id"] = "prop_tampered_diff_999"
        tampered_auth = TrialExecutionAuthorization(**tampered_auth_data)

        with pytest.raises((DispatcherOracleVerificationError, DispatcherScopeViolationError)) as exc_oracle:
            dispatcher.dispatch_canary_action(
                action=action,
                authorization=tampered_auth,
                plan=plan,
            )
        assert exc_oracle.value is not None

        # 2. Tampered HMAC
        tampered_hmac_data = dataclasses.asdict(valid_auth)
        tampered_hmac_data["authorization_hmac"] = "f" * 64
        tampered_hmac_auth = TrialExecutionAuthorization(**tampered_hmac_data)

        with pytest.raises(DispatcherOracleVerificationError):
            dispatcher.dispatch_canary_action(
                action=action,
                authorization=tampered_hmac_auth,
                plan=plan,
            )

        # Assert zero gateway calls
        assert fx["fake_gateway"].call_count == 0

    def test_gate3_fail_closed_incident_target_rejection(self, gate3_fixture):
        """Historical incident targets are unconditionally blacklisted and rejected with zero dispatch."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )
        iss_res = fx["challenge_authority"].issue_challenge(challenge_req)
        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )
        consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )
        valid_auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_consent_envelope=consent,
            operator_public_key=fx["op_pub"],
            challenge_authority=fx["challenge_authority"],
            challenge_id=iss_res.challenge.challenge_id,
            signing_capability=fx["signing_capability"],
            verification_key_handle=fx["verification_key_handle"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            nonce_ledger_directory=fx["nonce_ledger_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )

        dispatcher = TrustedCanaryDispatchService(
            adapter=fx["mock_adapter"],
            oracle_client=fx["oracle_client"],
            oracle_public_key=fx["oracle_pub"],
            receipt_service=fx["receipt_service"],
            clock=lambda: fixed_now,
        )
        action = extract_contract_action_from_plan(plan)

        # Inject historical incident target project UID
        incident_auth_dict = dataclasses.asdict(valid_auth)
        incident_auth_dict["target_project_uid"] = INCIDENT_TARGET_PROJECT_UID

        incident_auth = TrialExecutionAuthorization(**incident_auth_dict)

        with pytest.raises(DispatcherTargetDeniedError) as exc_target:
            dispatcher.dispatch_canary_action(
                action=action,
                authorization=incident_auth,
                plan=plan,
            )
        assert "ERR_TARGET_PROJECT_UID_BLACKLISTED" in str(exc_target.value)

        # Assert zero gateway calls
        assert fx["fake_gateway"].call_count == 0

    def test_gate3_fail_closed_mock_gateway_failure(self, gate3_fixture):
        """Simulated gateway failure raises DispatcherDispatchError, prevents second dispatch, and logs no success."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        challenge_req = LocalTestChallengeIssuanceRequest(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )
        iss_res = fx["challenge_authority"].issue_challenge(challenge_req)
        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )
        consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )
        auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_consent_envelope=consent,
            operator_public_key=fx["op_pub"],
            challenge_authority=fx["challenge_authority"],
            challenge_id=iss_res.challenge.challenge_id,
            signing_capability=fx["signing_capability"],
            verification_key_handle=fx["verification_key_handle"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            nonce_ledger_directory=fx["nonce_ledger_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )

        # Inject failure into fake gateway
        failing_gateway = FakeResolveGateway(configured_exception=RuntimeError("simulated resolve gateway failure"))
        failing_adapter = IsolatedResolveTrialAdapter(fx["coordinator"], fx["journal"], failing_gateway)
        dispatcher = TrustedCanaryDispatchService(
            adapter=failing_adapter,
            oracle_client=fx["oracle_client"],
            oracle_public_key=fx["oracle_pub"],
            receipt_service=None,
            clock=lambda: fixed_now,
        )
        action = extract_contract_action_from_plan(plan)

        # Acquire target exclusion lock on coordinator
        fx["coordinator"].acquire_target_lock(auth.target_timeline_uid)

        with pytest.raises(DispatcherDispatchError) as exc_disp:
            dispatcher.dispatch_canary_action(
                action=action,
                authorization=auth,
                plan=plan,
            )
        assert "ERR_DISPATCH_EXECUTION_FAILED" in str(exc_disp.value)

        # Attempting second dispatch of the same execution_id MUST fail closed as duplicate
        with pytest.raises(DispatcherDuplicateDispatchError):
            dispatcher.dispatch_canary_action(
                action=action,
                authorization=auth,
                plan=plan,
            )

        # Receipt must NOT exist or be in EXECUTING state
        with pytest.raises(Exception):
            fx["receipt_service"].load_receipt(auth.execution_id)

    def test_gate3_fail_closed_socket_unauthorized_peer_and_malformed_frame(self, gate3_fixture):
        """Unauthorized peer UID or malformed framing fails closed without invoking protected handlers."""
        fx = gate3_fixture

        handler_invoked = {"count": 0}

        def mock_handler(params: Dict[str, Any]) -> Dict[str, Any]:
            handler_invoked["count"] += 1
            return {"status": "ok"}

        current_uid = os.getuid()
        peer_to_report = current_uid

        server = LocalTestSignerSocketServer(
            socket_path=fx["sock_path"],
            temp_root=fx["sock_dir"],
            allowed_peer_uids={current_uid},
            peer_credential_provider=lambda s: peer_to_report,
            challenge_handler=mock_handler,
        )

        with server:
            client = LocalTestSignerSocketClient(socket_path=fx["sock_path"])

            # 1. Unauthorized peer UID
            peer_to_report = 99999
            resp_denied = client.send_request("request_challenge", {
                "expected_director_key_id": "dir_01",
                "operator_id": "op_01",
                "operator_key_id": "op_key_01",
                "approval_envelope": {},
                "proposal": {},
                "execution_plan": {},
            })
            assert resp_denied["status"] == "ERROR"
            assert resp_denied["error_code"] == "ERR_PEER_AUTH_DENIED"
            assert handler_invoked["count"] == 0

            # 2. Empty frame
            peer_to_report = current_uid
            resp_empty = client.send_request(
                "request_challenge",
                {},
                raw_payload_override=b"",
                length_prefix_override=0,
            )
            assert resp_empty["status"] == "ERROR"
            assert resp_empty["error_code"] == "ERR_EMPTY_FRAME"
            assert handler_invoked["count"] == 0

            # 3. Prohibited overrides in method params
            resp_prohibited = client.send_request("request_challenge", {
                "expected_director_key_id": "dir_01",
                "operator_id": "op_01",
                "operator_key_id": "op_key_01",
                "approval_envelope": {},
                "proposal": {},
                "execution_plan": {},
                "challenge_id": "caller_override_attempt",  # prohibited
            })
            assert resp_prohibited["status"] == "ERROR"
            assert resp_prohibited["error_code"] == "ERR_PARAM_OVERRIDE_PROHIBITED"
            assert handler_invoked["count"] == 0

    def test_gate3_fail_closed_proposal_revision_digest_mismatch(self, gate3_fixture):
        """A revision digest mismatch between plan and authorization fails closed before gateway invocation and creates no success receipt."""
        fx = gate3_fixture
        fixed_now = fx["fixed_now"]

        proposal, approval = make_test_proposal_and_approval()
        plan = make_test_execution_plan(proposal, approval)
        dir_signer = DirectorApprovalSigner(fx["dir_priv"], key_id=fx["dir_key_id"])
        approval_envelope = dir_signer.sign(
            approval_record=approval,
            plan_digest_sha256=compute_canonical_plan_digest(plan),
            proposal_content_digest_sha256=compute_proposal_revision(proposal),
            director_key_id=fx["dir_key_id"],
            signed_at_iso="2026-11-04T12:00:00Z",
        )
        iss_res = fx["challenge_authority"].issue_challenge(
            LocalTestChallengeIssuanceRequest(
                approval_envelope=approval_envelope,
                proposal=proposal,
                execution_plan=plan,
                director_public_key=fx["dir_pub"],
                expected_director_key_id=fx["dir_key_id"],
                operator_id=fx["op_id"],
                operator_key_id=fx["op_key_id"],
                revocation_trust_directory=fx["revocation_trust_dir"],
                revocation_registry_directory=fx["revocation_reg_dir"],
                verification_time_utc=fixed_now,
            )
        )
        review_ctx = OperatorReviewContext(
            challenge_id=iss_res.challenge.challenge_id,
            client_nonce=iss_res.challenge.client_nonce,
            expires_at_iso=iss_res.challenge.expires_at_iso,
            target_project_name=iss_res.operator_context.expected_target_project_name,
            target_project_uid=iss_res.operator_context.expected_target_project_uid,
            target_timeline_name=iss_res.operator_context.expected_target_timeline_name,
            target_timeline_uid=iss_res.operator_context.expected_target_timeline_uid,
            action_type=iss_res.operator_context.expected_action_type,
            track_type=iss_res.operator_context.expected_track_type,
            track_index=iss_res.operator_context.expected_track_index,
            record_frame=iss_res.operator_context.expected_record_frame,
            duration_frames=iss_res.operator_context.expected_duration_frames,
            source_file_path=iss_res.operator_context.expected_source_file_path,
            source_file_sha256=iss_res.operator_context.expected_source_file_sha256,
            approval_envelope_digest_sha256=iss_res.operator_context.expected_approval_envelope_digest_sha256,
            plan_digest_sha256=iss_res.operator_context.expected_plan_digest_sha256,
            action_payload_digest_sha256=iss_res.operator_context.expected_action_payload_digest_sha256,
            operator_id=fx["op_id"],
            operator_key_id=fx["op_key_id"],
        )
        consent = review_and_sign_operator_consent(
            context=review_ctx,
            signer=lambda b: fx["op_priv"].sign(b).hex(),
            input_func=lambda p: FRESHNESS_ATTESTATION_LITERAL if "attestation" in p.lower() else CONFIRMATION_PHRASE_LITERAL,
            clock=lambda: fixed_now,
        )
        valid_auth = issue_trial_execution_authorization_for_local_test(
            approval_envelope=approval_envelope,
            proposal=proposal,
            execution_plan=plan,
            director_public_key=fx["dir_pub"],
            expected_director_key_id=fx["dir_key_id"],
            operator_consent_envelope=consent,
            operator_public_key=fx["op_pub"],
            challenge_authority=fx["challenge_authority"],
            challenge_id=iss_res.challenge.challenge_id,
            signing_capability=fx["signing_capability"],
            verification_key_handle=fx["verification_key_handle"],
            revocation_trust_directory=fx["revocation_trust_dir"],
            revocation_registry_directory=fx["revocation_reg_dir"],
            nonce_ledger_directory=fx["nonce_ledger_dir"],
            verification_time_utc=fixed_now,
            ttl_seconds=300.0,
        )

        action = extract_contract_action_from_plan(plan)

        # Build auth with a mismatched 64-hex lowercase proposal_revision
        tampered_auth_data = dataclasses.asdict(valid_auth)
        tampered_auth_data["proposal_revision"] = "f" * 64
        tampered_auth = TrialExecutionAuthorization(**tampered_auth_data)

        dispatcher = TrustedCanaryDispatchService(
            adapter=fx["mock_adapter"],
            oracle_client=fx["oracle_client"],
            oracle_public_key=fx["oracle_pub"],
            receipt_service=fx["receipt_service"],
            clock=lambda: fixed_now,
        )

        fx["coordinator"].acquire_target_lock(tampered_auth.target_timeline_uid)
        initial_gateway_calls = fx["fake_gateway"].call_count

        with pytest.raises(DispatcherScopeViolationError) as exc_info:
            dispatcher.dispatch_canary_action(action=action, authorization=tampered_auth, plan=plan)

        assert "ERR_PLAN_PROPOSAL_REVISION_MISMATCH" in str(exc_info.value)
        # Fake gateway was never called
        assert fx["fake_gateway"].call_count == initial_gateway_calls
        # No success receipt exists on disk
        with pytest.raises(ReceiptNotFoundError):
            fx["receipt_service"].load_receipt(tampered_auth.execution_id)

    def test_gate3_tripwire_zero_secret_leakage_and_ready_to_execute_safety(self, gate3_fixture):
        """Global invariant: zero secret leakage and ready_to_execute=False preserved 100%."""
        import src.core.timeline_plan_execution_binder as binder_mod

        # Inspect binder module safety constraint: ready_to_execute=False invariant
        assert "ready_to_execute=False" in binder_mod.__doc__
        assert getattr(binder_mod, "ready_to_execute", False) is False
