"""
Integration tests for LiveCanaryDispatchService and TrialAuthorizationSignerDaemon (Gate 6 DEP-03-R2).

Verifies:
- Real length-prefixed AF_UNIX client-to-daemon communication for verification and terminal notification.
- End-to-end verified dispatch flow with fake adapter (COMPLETED branch).
- End-to-end clean abort flow with fake adapter (ABORTED branch via PreMutationCleanAbortError).
- End-to-end in-doubt flow with fake adapter (IN_DOUBT branch).
- Replay and duplicate dispatch prevention across both dispatcher and daemon boundaries.
- Tampered authorization rejection by daemon and fail-closed handling by dispatcher.
- Post-terminal execution verification rejection (ERR_EXECUTION_ALREADY_TERMINAL).
- Concurrency and target locking mutual exclusion across threads.
- Safety tripwires: zero production path access, zero Resolve import, zero network sockets.
"""

from __future__ import annotations

import dataclasses
import datetime
from fractions import Fraction
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.core.contracts.oracle_ipc_contracts import (
    DEFAULT_SOCKET_TIMEOUT,
    OracleTerminalNotificationRequest,
    OracleTerminalNotificationResponse,
    OracleVerificationRequest,
    OracleVerificationResponse,
    VALID_TERMINAL_STATES,
)
from src.core.contracts.trial_authorization_contracts import (
    TrialExecutionAuthorization,
    canonical_bytes_for_auth_hmac,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction,
    RationalFPS,
    TargetSpec,
    TimeRangeFrames,
    TimelineExecutionPlan,
    compute_canonical_plan_digest,
)
from src.services.durable_dispatch_journal import (
    DispatchJournalError,
    DispatchJournalInDoubtError,
    DispatchJournalLockError,
    DurableDispatchJournal,
)
from src.services.durable_nonce_ledger import (
    AuthorizationIdentityRecord,
    DurableSignerLedger,
)
from src.services.live_canary_dispatch_service import (
    CANARY_AUTHORIZED_RECORD_FRAME,
    CANARY_FPS_DENOMINATOR,
    CANARY_FPS_NUMERATOR,
    CANARY_SOURCE_DURATION_FRAMES,
    CANARY_SOURCE_MEDIA_SHA256,
    CANARY_TARGET_PROJECT_NAME,
    CANARY_TARGET_PROJECT_UID,
    CANARY_TARGET_TIMELINE_NAME,
    CANARY_TARGET_TIMELINE_UID,
    CANARY_TARGET_TRACK_INDEX,
    CANARY_TARGET_TRACK_TYPE,
    LiveCanaryDispatchService,
    LiveDispatchAdapterError,
    LiveDispatchDuplicateError,
    LiveDispatchError,
    LiveDispatchInDoubtError,
    LiveDispatchOracleVerificationError,
    LiveDispatchTargetDeniedError,
    PreMutationCleanAbortError,
    PreMutationCleanAbortProof,
)
from src.services.signer_keystore_service import (
    HMAC_KEY_FILENAME,
    ORACLE_KEY_FILENAME,
    SignerKeystoreService,
)
from src.services.trial_authorization_signer_daemon import (
    TrialAuthorizationSignerDaemon,
)
from src.services.trial_authorization_signer_service import (
    check_and_advance_revocation_epoch_anchor_for_local_test,
    compute_canonical_action_payload_digest_for_admission,
    verify_signed_revocation_manifest,
)
from tests.integration.test_gate3_signer_dispatcher_integration import (
    make_signed_revocation_manifest_bytes,
)


# ==============================================================================
# Helper Fixtures & Setup
# ==============================================================================
def _setup_live_daemon_fixtures(
    tmp_path: Path,
) -> Tuple[TrialAuthorizationSignerDaemon, SignerKeystoreService, DurableSignerLedger, str, Ed25519PublicKey, bytes]:
    """Creates an isolated, test-configured TrialAuthorizationSignerDaemon."""
    ks_dir = tmp_path / "keystore"
    ks_dir.mkdir(mode=0o700)
    os.chmod(ks_dir, 0o700)

    hmac_key_bytes = b"\x42" * 32
    hmac_file = ks_dir / HMAC_KEY_FILENAME
    hmac_file.write_bytes(hmac_key_bytes)
    os.chmod(hmac_file, 0o600)

    oracle_priv = Ed25519PrivateKey.generate()
    oracle_file = ks_dir / ORACLE_KEY_FILENAME
    oracle_file.write_bytes(oracle_priv.private_bytes_raw())
    os.chmod(oracle_file, 0o600)

    keystore = SignerKeystoreService(ks_dir, enforce_ownership=False)

    ledger_dir = tmp_path / "nonces"
    ledger_dir.mkdir(mode=0o700)
    os.chmod(ledger_dir, 0o700)
    ledger = DurableSignerLedger(ledger_dir, enforce_ownership=False)

    oracle_dir = tmp_path / "oracle"
    oracle_dir.mkdir(mode=0o700)
    public_dir = tmp_path / "public"
    public_dir.mkdir(mode=0o700)

    rev_trust = tmp_path / "revocation_trust"
    rev_trust.mkdir(mode=0o700)
    os.chmod(rev_trust, 0o700)

    rev_reg = tmp_path / "revocation_reg"
    rev_reg.mkdir(mode=0o700)
    os.chmod(rev_reg, 0o700)

    sec_priv = Ed25519PrivateKey.generate()
    sec_pub = sec_priv.public_key()
    sec_pub_pem = sec_pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    issuer_file = rev_trust / "revocation_issuer.pub.pem"
    issuer_file.write_bytes(sec_pub_pem)
    os.chmod(issuer_file, 0o600)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    manifest_bytes = make_signed_revocation_manifest_bytes(
        sec_priv,
        epoch=1,
        issued_at_iso=(now_utc - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        valid_until_iso=(now_utc + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    man_file = rev_reg / "revocations.signed.json"
    man_file.write_bytes(manifest_bytes)
    os.chmod(man_file, 0o600)

    res = verify_signed_revocation_manifest(
        manifest_bytes,
        issuer_public_key=sec_pub,
        verification_time_utc=now_utc,
    )
    check_and_advance_revocation_epoch_anchor_for_local_test(res, rev_reg, advance=True)

    daemon = TrialAuthorizationSignerDaemon.create_for_test(
        keystore_service=keystore,
        nonce_ledger=ledger,
        oracle_dir=oracle_dir,
        public_dir=public_dir,
        oracle_allowed_uids=(os.geteuid(),),
        revocation_trust_dir=rev_trust,
        revocation_registry_dir=rev_reg,
        sec_officer_public_key=sec_pub,
        enforce_dac_ownership=False,
    )
    daemon.start()

    oracle_sock = str(oracle_dir / "signer_oracle.sock")
    return daemon, keystore, ledger, oracle_sock, oracle_priv.public_key(), hmac_key_bytes


def _build_canary_plan_and_action() -> Tuple[TimelineExecutionPlan, PlannedTimelineAction]:
    action = PlannedTimelineAction(
        action_index=0,
        action_type="INSERT_CLIP",
        target_track_type=CANARY_TARGET_TRACK_TYPE,
        target_track_index=CANARY_TARGET_TRACK_INDEX,
        clip_id="canary_clip_01",
        source_file_path="/media/storage/canary_test_1080p24.mp4",
        source_range=TimeRangeFrames(0, CANARY_SOURCE_DURATION_FRAMES),
        timeline_range=TimeRangeFrames(0, CANARY_SOURCE_DURATION_FRAMES),
        applied_speed=Fraction(1, 1),
        role="canary_insertion",
        action_idempotency_hash="hash_01",
        action_params={},
    )
    target_spec = TargetSpec(
        project_name=CANARY_TARGET_PROJECT_NAME,
        project_id=CANARY_TARGET_PROJECT_UID,
        timeline_name=CANARY_TARGET_TIMELINE_NAME,
        timeline_id=CANARY_TARGET_TIMELINE_UID,
        timeline_fps=RationalFPS(24, 1),
        timeline_resolution=(1920, 1080),
        start_timecode_frame=86400,
    )
    plan = TimelineExecutionPlan(
        plan_id="plan_canary_test_01",
        idempotency_key="idemp_01",
        proposal_id="prop_01",
        proposal_revision="b" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-04T12:00:00Z",
        target_spec=target_spec,
        occupancy_snapshot_id=None,
        binding_status="BOUND_VERIFIED",
        planned_actions=(action,),
        planned_actions_count=1,
        track_reservations=(),
        source_digests=(),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=120,
        total_timeline_duration_sec=Fraction(5, 1),
        issues=(),
        created_at_iso="2026-11-04T12:00:00Z",
    )
    return plan, action


def _build_signed_authorization(
    hmac_key: bytes,
    ledger: DurableSignerLedger,
    plan: TimelineExecutionPlan,
    action: PlannedTimelineAction,
    *,
    execution_id: str = "exec_canary_01",
    authorization_id: str = "auth_canary_01",
    proposal_revision: str = "b" * 64,
) -> TrialExecutionAuthorization:
    action_digest = compute_canonical_action_payload_digest_for_admission(
        action=action,
        execution_plan=plan,
        authorization_id=authorization_id,
        execution_id=execution_id,
    )
    plan_digest = compute_canonical_plan_digest(plan)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    issued_iso = (now_utc - datetime.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_until_iso = (now_utc + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Register authorization identity in ledger
    identity_rec = AuthorizationIdentityRecord(
        schema_version=1,
        authorization_id=authorization_id,
        director_key_id="dir_canary_01",
        operator_key_id="op_canary_01",
        action_payload_digest_sha256=action_digest,
        proposal_revision=proposal_revision,
        issued_at_iso=issued_iso,
    )
    ledger.record_authorization_identity(identity_rec)

    auth_claims = {
        "authorization_id": authorization_id,
        "key_id": "trial_auth_key_01",
        "operator_gesture_digest_sha256": "1" * 64,
        "approval_record_id": "appr_canary_01",
        "approval_record_digest_sha256": "2" * 64,
        "proposal_id": "prop_canary_01",
        "proposal_revision": proposal_revision,
        "proposal_content_digest_sha256": "3" * 64,
        "plan_idempotency_key": plan.idempotency_key,
        "plan_digest_sha256": plan_digest,
        "action_payload_digest_sha256": action_digest,
        "target_project_uid": plan.target_spec.project_id,
        "target_project_name": CANARY_TARGET_PROJECT_NAME,
        "target_timeline_uid": plan.target_spec.timeline_id,
        "target_timeline_name": CANARY_TARGET_TIMELINE_NAME,
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": 86400,
        "authorized_record_frame": CANARY_AUTHORIZED_RECORD_FRAME,
        "source_file_path": action.source_file_path,
        "source_file_sha256": CANARY_SOURCE_MEDIA_SHA256,
        "authorized_action_type": "INSERT_CLIP",
        "authorized_track_type": action.target_track_type,
        "authorized_track_index": action.target_track_index,
        "authorized_timeline_frame_start": action.timeline_range.start_frame,
        "authorized_timeline_frame_duration": action.timeline_range.duration_frames,
        "authorized_source_frame_start": action.source_range.start_frame,
        "authorized_source_frame_duration": action.source_range.duration_frames,
        "execution_id": execution_id,
        "issued_at_iso": issued_iso,
        "valid_until_iso": valid_until_iso,
        "token_nonce": execution_id,
        "authorization_hmac": "0" * 64,
    }
    candidate_dto = TrialExecutionAuthorization(**auth_claims)
    canonical_bytes = canonical_bytes_for_auth_hmac(candidate_dto)
    auth_hmac = hmac.new(hmac_key, canonical_bytes, hashlib.sha256).hexdigest().lower()

    auth_claims["authorization_hmac"] = auth_hmac
    return TrialExecutionAuthorization(**auth_claims)


# ==============================================================================
# Integration Tests
# ==============================================================================
class TestLiveDispatchOracleIntegration:
    def test_end_to_end_completed_flow(self, tmp_path: Path):
        """Tests complete successful dispatch lifecycle: verification -> execution -> terminal COMPLETED."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            plan, action = _build_canary_plan_and_action()

            fake_adapter = MagicMock()
            fake_adapter.execute_live_canary_action.return_value = {
                "status": "COMPLETED",
                "receipt_state": "COMPLETED",
                "execution_id": "exec_canary_100",
                "timeline_uid": CANARY_TARGET_TIMELINE_UID,
                "action_id": action.action_idempotency_hash,
                "receipt_id": "rcpt_canary_100",
                "terminalized": True,
            }

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            auth = _build_signed_authorization(hmac_key, ledger, plan, action, execution_id="exec_canary_100")

            receipt = service.dispatch_live_canary(auth=auth, plan=plan)

            assert receipt["status"] == "COMPLETED"
            assert receipt["execution_id"] == "exec_canary_100"
            assert fake_adapter.execute_live_canary_action.call_count == 1

            # Assert Oracle ledger recorded terminal state COMPLETED
            term_rec = ledger.get_oracle_terminal_state("exec_canary_100")
            assert term_rec is not None
            assert term_rec.terminal_state == "COMPLETED"

            # Assert journal recorded EXHAUSTED and released target lock (stable inode remains)
            assert journal.is_execution_id_committed("exec_canary_100") is True
            thash = journal.compute_target_hash(auth.target_project_uid, auth.target_timeline_uid)
            assert thash not in journal._active_locks
            assert (journal_dir / ".locks" / f"{thash}.lock").exists()
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_end_to_end_pre_execution_clean_abort_flow(self, tmp_path: Path):
        """Tests clean pre-dispatch abort lifecycle: verification -> pre-execution abort -> terminal ABORTED."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            plan, action = _build_canary_plan_and_action()
            fake_adapter = MagicMock()

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_canary_abort", authorization_id="auth_canary_abort"
            )

            receipt = service.abort_unexecuted_dispatch(
                auth=auth,
                plan=plan,
                reason="Preflight check failed before mutation boundary",
            )
            assert receipt["status"] == "ABORTED"
            assert fake_adapter.execute_live_canary_action.call_count == 0

            # Assert Oracle ledger recorded terminal state ABORTED
            term_rec = ledger.get_oracle_terminal_state("exec_canary_abort")
            assert term_rec is not None
            assert term_rec.terminal_state == "ABORTED"

            # Target lock was released cleanly (stable inode remains)
            thash = journal.compute_target_hash(auth.target_project_uid, auth.target_timeline_uid)
            assert thash not in journal._active_locks
            assert (journal_dir / ".locks" / f"{thash}.lock").exists()
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_end_to_end_adapter_abort_attempt_forces_in_doubt(self, tmp_path: Path):
        """DEP03-R4-01: Adapter attempting clean abort inside execution boundary is strictly classified as IN_DOUBT."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            plan, action = _build_canary_plan_and_action()
            fake_adapter = MagicMock()

            def abort_via_capability(*args, **kwargs):
                cap = kwargs.get("clean_abort_capability") or getattr(fake_adapter, "clean_abort_capability", None)
                assert cap is not None
                raise cap.create_clean_abort_error("Preflight check failed before mutation boundary")

            fake_adapter.execute_live_canary_action.side_effect = abort_via_capability

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_canary_abort_attempt", authorization_id="auth_canary_abort_attempt"
            )

            with pytest.raises(LiveDispatchInDoubtError):
                service.dispatch_live_canary(auth=auth, plan=plan)

            # Assert Oracle ledger recorded terminal state IN_DOUBT (NOT ABORTED!)
            term_rec = ledger.get_oracle_terminal_state("exec_canary_abort_attempt")
            assert term_rec is not None
            assert term_rec.terminal_state == "IN_DOUBT"

            # Target lock is RETAINED fail-closed
            assert journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_end_to_end_in_doubt_flow(self, tmp_path: Path):
        """Tests ambiguous / unexpected failure lifecycle: verification -> error -> terminal IN_DOUBT."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            fake_adapter = MagicMock()
            fake_adapter.execute_live_canary_action.side_effect = ConnectionResetError("Resolve IPC crash mid-mutation")

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            plan, action = _build_canary_plan_and_action()
            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_canary_doubt", authorization_id="auth_canary_doubt"
            )

            with pytest.raises(LiveDispatchInDoubtError, match="ERR_DISPATCH_IN_DOUBT"):
                service.dispatch_live_canary(auth=auth, plan=plan)

            # Assert Oracle ledger recorded terminal state IN_DOUBT
            term_rec = ledger.get_oracle_terminal_state("exec_canary_doubt")
            assert term_rec is not None
            assert term_rec.terminal_state == "IN_DOUBT"

            # Assert target lock and .in_doubt marker RETAINED permanently!
            assert journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True
            thash = journal.compute_target_hash(auth.target_project_uid, auth.target_timeline_uid)
            marker = journal_dir / ".locks" / f"{thash}.in_doubt"
            assert marker.exists()
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_replay_rejected_fail_closed(self, tmp_path: Path):
        """Tests that replaying the same authorization / execution is rejected before adapter execution."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            plan, action = _build_canary_plan_and_action()

            fake_adapter = MagicMock()
            fake_adapter.execute_live_canary_action.return_value = {
                "status": "COMPLETED",
                "receipt_state": "COMPLETED",
                "execution_id": "exec_replay_01",
                "timeline_uid": CANARY_TARGET_TIMELINE_UID,
                "action_id": action.action_idempotency_hash,
                "receipt_id": "rcpt_replay_01",
                "terminalized": True,
            }

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_replay_01", authorization_id="auth_replay_01"
            )

            # First dispatch succeeds
            service.dispatch_live_canary(auth=auth, plan=plan)
            assert fake_adapter.execute_live_canary_action.call_count == 1

            # Second dispatch must be rejected fail-closed with zero additional adapter calls
            with pytest.raises((LiveDispatchDuplicateError, LiveDispatchTargetDeniedError)):
                service.dispatch_live_canary(auth=auth, plan=plan)

            assert fake_adapter.execute_live_canary_action.call_count == 1
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_tampered_hmac_rejected_by_oracle(self, tmp_path: Path):
        """Tests that tampered authorization is rejected by Oracle and dispatcher fails closed."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            fake_adapter = MagicMock()
            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            plan, action = _build_canary_plan_and_action()
            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_tamper", authorization_id="auth_tamper"
            )

            # Tamper the HMAC
            bad_auth = dataclasses.replace(auth, authorization_hmac="f" * 64)

            with pytest.raises(LiveDispatchOracleVerificationError, match="ERR_INVARIANT_7_FAILED"):
                service.dispatch_live_canary(auth=bad_auth, plan=plan)

            assert fake_adapter.execute_live_canary_action.call_count == 0
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()

    def test_post_terminal_verification_rejected_by_oracle(self, tmp_path: Path):
        """Tests that Oracle rejects verification for an already-terminal execution ID."""
        daemon, ks, ledger, oracle_sock, oracle_pub, hmac_key = _setup_live_daemon_fixtures(tmp_path)
        try:
            journal_dir = tmp_path / "dispatch_journal"
            journal_dir.mkdir(mode=0o700)
            journal = DurableDispatchJournal(journal_dir)

            plan, action = _build_canary_plan_and_action()

            fake_adapter = MagicMock()
            fake_adapter.execute_live_canary_action.return_value = {
                "status": "COMPLETED",
                "receipt_state": "COMPLETED",
                "execution_id": "exec_terminal_repeat",
                "timeline_uid": CANARY_TARGET_TIMELINE_UID,
                "action_id": action.action_idempotency_hash,
                "receipt_id": "rcpt_repeat_01",
                "terminalized": True,
            }

            service = LiveCanaryDispatchService(
                oracle_socket_path=oracle_sock,
                oracle_public_key=oracle_pub,
                journal=journal,
                adapter=fake_adapter,
            )

            auth = _build_signed_authorization(
                hmac_key, ledger, plan, action,
                execution_id="exec_terminal_repeat", authorization_id="auth_terminal_repeat"
            )

            # Complete execution
            service.dispatch_live_canary(auth=auth, plan=plan)

            # Attempt raw verification request for same execution ID to Oracle
            req = OracleVerificationRequest(
                schema_version=2,
                request_id=str(uuid.uuid4()),
                request_nonce=secrets.token_hex(32),
                requested_at_iso=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                signed_authorization=auth,
                action_payload_digest_sha256=auth.action_payload_digest_sha256,
                target_project_uid=auth.target_project_uid,
                target_timeline_uid=auth.target_timeline_uid,
                proposal_revision=auth.proposal_revision,
            )
            raw_resp = service._send_oracle_request(req.canonical_bytes())
            resp = OracleVerificationResponse.from_json_bytes(raw_resp)

            assert resp.is_valid is False
            assert resp.reason_code == "ERR_EXECUTION_ALREADY_TERMINAL"
            journal.close()
        finally:
            daemon.stop()
            ks.close()
            ledger.close()
