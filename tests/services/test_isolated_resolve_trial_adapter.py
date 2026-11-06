"""
tests/services/test_isolated_resolve_trial_adapter.py
Focused Test Suite for Isolated Resolve Trial Adapter & Gateway Integration (G07-07B6-I4).

Acceptance Groups:
  - I4-G1: Mock Gateway Tripwire Enforcement (marker verification, forbidden live attributes)
  - I4-G2: Coordinator Authorization Binding Gate (HMAC, revoked keys, expiration, nonce mismatch)
  - I4-G3: Action Assembly & Digest Equality Gate (geometry check, digest mismatch rejection)
  - I4-G4: Success Path WAL State Machine & Identity Envelope (START -> PENDING -> COMMITTED)
  - I4-G5: Clean Pre-Dispatch Rejection & Caller Target Lock Release (zero pending, zero dispatch)
  - I4-G6: Post-Dispatch Exception & In-Doubt Lock Retention (ACTION_FAILED, IN_DOUBT_LOCKED, no FINISH)
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.core.contracts.execution_receipt_contracts import (
    JournalEntry,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    PlannedTimelineAction,
    ReadbackReceiptVerificationError,
    TargetExclusionLockError,
    TargetLockReleaseError,
    TrialExecutionAuthorization,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
)
from src.services.durable_trial_target_ledger import (
    RecoveryCoordinator,
    TargetLockEntry,
)
from src.services.execution_journal_service import ExecutionJournalService
from src.services.isolated_resolve_trial_adapter import (
    FakeResolveGateway,
    IsolatedResolveTrialAdapter,
    MockGatewayAdmissionError,
)

TRIAL_KEY_BYTES = b"\x01\x02\x03\x04" * 8  # 32 bytes
OPERATOR_KEY_BYTES = b"\x05\x06\x07\x08" * 8  # 32 bytes
OPERATOR_IDENTITY = "lead_operator_01"


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated filesystem environment with directories and valid keystore."""
    base_dir = tmp_path / "base"
    journals_dir = base_dir / "journals"
    receipts_dir = base_dir / "receipts"
    locks_dir = base_dir / "locks"
    ledgers_dir = base_dir / "ledgers"
    keystore_dir = base_dir / "security"

    journals_dir.mkdir(parents=True)
    receipts_dir.mkdir(parents=True)
    locks_dir.mkdir(parents=True)
    ledgers_dir.mkdir(parents=True)
    keystore_dir.mkdir(parents=True)

    os.chmod(str(keystore_dir), 0o700)

    # 1. Valid active trial key (mode 0600)
    trial_key_file = keystore_dir / "canary_trial_key.key"
    trial_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(trial_key_file), 0o600)

    # 2. Revoked trial key (mode 0600)
    revoked_key_file = keystore_dir / "revoked_trial_key.key"
    revoked_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(revoked_key_file), 0o600)

    # Revocations list
    rev_file = keystore_dir / "revocations.json"
    rev_file.write_text(json.dumps({"revoked_key_ids": ["revoked_trial_key"]}), encoding="utf-8")
    os.chmod(str(rev_file), 0o600)

    # 3. Valid operator reconciliation key (mode 0600)
    op_key_file = keystore_dir / "canary_op_key.opkey.json"
    op_data = {
        "key_hex": OPERATOR_KEY_BYTES.hex(),
        "permitted_operator_identity": OPERATOR_IDENTITY,
        "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",
    }
    op_key_file.write_text(json.dumps(op_data), encoding="utf-8")
    os.chmod(str(op_key_file), 0o600)

    return {
        "base_dir": str(base_dir),
        "journals_dir": str(journals_dir),
        "receipts_dir": str(receipts_dir),
        "locks_dir": str(locks_dir),
        "ledgers_dir": str(ledgers_dir),
        "keystore_path": str(keystore_dir),
        "trial_key_id": "canary_trial_key",
        "trial_key_bytes": TRIAL_KEY_BYTES,
        "revoked_key_id": "revoked_trial_key",
        "op_key_id": "canary_op_key",
        "op_key_bytes": OPERATOR_KEY_BYTES,
        "op_identity": OPERATOR_IDENTITY,
    }


def make_coordinator(env: Dict[str, Any]) -> RecoveryCoordinator:
    """Helper to instantiate a valid RecoveryCoordinator."""
    return RecoveryCoordinator(
        journals_dir=env["journals_dir"],
        receipts_dir=env["receipts_dir"],
        locks_dir=env["locks_dir"],
        ledgers_dir=env["ledgers_dir"],
        keystore_path=env["keystore_path"],
    )


def make_action_and_matching_auth(
    env: Dict[str, Any],
    execution_id: str = "exec_1234567890abcdef1234567890abcdef",
    target_project_uid: str = "proj_001",
    target_timeline_uid: str = "tl_001",
    valid_offset_sec: float = 300.0,
    tamper_hmac: Optional[str] = None,
    tamper_digest: Optional[str] = None,
    key_id: Optional[str] = None,
    key_bytes: Optional[bytes] = None,
    action_index: int = 0,
    duration_frames: int = 240,
    record_frame: int = 100,
    mismatch_nonce: Optional[str] = None,
) -> tuple[PlannedTimelineAction, TrialExecutionAuthorization]:
    """
    Constructs a matching PlannedTimelineAction and signed TrialExecutionAuthorization.
    The authorization's action_payload_digest_sha256 binds exactly to the canonical
    action payload bytes, and is signed with HMAC-SHA256.
    """
    kid = key_id or env["trial_key_id"]
    kbytes = key_bytes or env["trial_key_bytes"]

    action = PlannedTimelineAction(
        action_id="act_1234567890abcdef",
        action_index=action_index,
        action_type="INSERT_CLIP",
        track_type="video",
        track_index=1,
        record_frame=record_frame,
        duration_frames=duration_frames,
        source_path="/media/source/clip.mov",
        source_frame_start=0,
        source_frame_duration=duration_frames,
        role="b_roll",
    )

    now = datetime.now(timezone.utc)
    issued_iso = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    valid_iso = (now + timedelta(seconds=valid_offset_sec)).isoformat().replace("+00:00", "Z")

    base_auth_dict = {
        "authorization_id": "auth_1234567890abcdef1234567890abcdef",
        "key_id": kid,
        "operator_gesture_digest_sha256": "a" * 64,
        "approval_record_id": "appr_1234567890abcdef1234567890abcdef",
        "approval_record_digest_sha256": "b" * 64,
        "proposal_id": "prop_1234567890abcdef1234567890abcdef",
        "proposal_revision": "1" * 64,
        "proposal_content_digest_sha256": "c" * 64,
        "plan_idempotency_key": "plan_1234567890abcdef1234567890abcdef",
        "plan_digest_sha256": "d" * 64,
        "action_payload_digest_sha256": "0" * 64,  # placeholder
        "target_project_uid": target_project_uid,
        "target_project_name": "Test Project",
        "target_timeline_uid": target_timeline_uid,
        "target_timeline_name": "Test Timeline",
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": 0,
        "authorized_record_frame": record_frame,
        "source_file_path": "/media/source/clip.mov",
        "source_file_sha256": "f" * 64,
        "authorized_action_type": "INSERT_CLIP",
        "authorized_track_type": "video",
        "authorized_track_index": 1,
        "authorized_timeline_frame_start": record_frame,
        "authorized_timeline_frame_duration": duration_frames,
        "authorized_source_frame_start": 0,
        "authorized_source_frame_duration": duration_frames,
        "execution_id": execution_id,
        "issued_at_iso": issued_iso,
        "valid_until_iso": valid_iso,
        "token_nonce": execution_id,
        "authorization_hmac": "0" * 64,  # placeholder
    }

    temp_auth = TrialExecutionAuthorization(**base_auth_dict)
    payload = assemble_canonical_action_payload(action, temp_auth, execution_id=execution_id)
    canonical_digest = hashlib.sha256(canonical_action_payload_bytes(payload)).hexdigest()

    base_auth_dict["action_payload_digest_sha256"] = tamper_digest or canonical_digest
    temp_auth2 = TrialExecutionAuthorization(**base_auth_dict)
    canon_bytes = canonical_bytes_for_auth_hmac(temp_auth2)
    sig = hmac.new(kbytes, canon_bytes, hashlib.sha256).hexdigest()
    base_auth_dict["authorization_hmac"] = tamper_hmac or sig

    auth = TrialExecutionAuthorization(**base_auth_dict)
    if mismatch_nonce:
        object.__setattr__(auth, "token_nonce", mismatch_nonce)
    return action, auth


# ==============================================================================
# GROUP I4-G1: Mock Gateway Tripwire Enforcement
# ==============================================================================

def test_i4_g1_tripwire_rejects_missing_approval_marker(test_env):
    """I4-G1: Rejects gateway when _is_approved_mock_gateway is missing or False."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    class BadGatewayNoMarker:
        pass

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, BadGatewayNoMarker())
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)

    coordinator.close()



def test_i4_g1_tripwire_rejects_gateway_with_automation_attribute(test_env):
    """I4-G1: Rejects gateway exposing _automation attribute."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    gateway = FakeResolveGateway()
    gateway._automation = "live_resolve_automation_handle"

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, gateway)
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)
    assert "_automation" in str(exc_info.value)

    coordinator.close()



def test_i4_g1_tripwire_rejects_gateway_with_resolve_attribute(test_env):
    """I4-G1: Rejects gateway exposing resolve attribute."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    gateway = FakeResolveGateway()
    gateway.resolve = object()

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, gateway)
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)
    assert "resolve" in str(exc_info.value)

    coordinator.close()



def test_i4_g1_tripwire_rejects_gateway_with_resolve_override_attribute(test_env):
    """I4-G1: Rejects gateway exposing _resolve_override attribute."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    gateway = FakeResolveGateway()
    gateway._resolve_override = object()

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, gateway)
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)
    assert "_resolve_override" in str(exc_info.value)

    coordinator.close()



def test_i4_g1_tripwire_rejects_foreign_class_with_forged_mock_marker(test_env):
    """I4-01: Rejects foreign non-FakeResolveGateway object that asserts _is_approved_mock_gateway=True."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    class ForgedMockGateway:
        _is_approved_mock_gateway = True

        def execute_action(self, action, timeout_sec=15.0):
            return {"success": True, "resolve_item_id": "forged_item"}

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, ForgedMockGateway())
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)
    assert "FakeResolveGateway" in str(exc_info.value)

    coordinator.close()



def test_i4_g1_tripwire_rejects_monkey_patched_execute_action(test_env):
    """I4-01: Rejects gateway when execute_action is monkey-patched with foreign function."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])

    gateway = FakeResolveGateway()

    def foreign_execute(action, timeout_sec=15.0):
        return {"success": True}

    gateway.execute_action = foreign_execute

    with pytest.raises(MockGatewayAdmissionError) as exc_info:
        IsolatedResolveTrialAdapter(coordinator, journal, gateway)
    assert "ERR_LIVE_ADAPTER_REJECTED" in str(exc_info.value)

    coordinator.close()



# ==============================================================================
# GROUP I4-G2: Coordinator Authorization Binding Gate
# ==============================================================================

def test_i4_g2_invalid_hmac_yields_zero_dispatch_and_zero_pending(test_env):
    """I4-G2: Authorization with tampered HMAC is rejected with 0 dispatch and 0 pending WAL."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g2_01"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        tamper_hmac="bad" * 21 + "b",
    )

    with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_SIGNED_AUTH_HMAC_INVALID" in str(exc_info.value)

    # Verify zero gateway calls
    assert gateway.call_count == 0
    # Verify zero pending WAL records
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g2_revoked_key_yields_zero_dispatch_and_zero_pending(test_env):
    """I4-G2: Authorization signed with revoked key is rejected with 0 dispatch and 0 pending WAL."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g2_01_rev"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        key_id=test_env["revoked_key_id"],
    )

    with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g2_expired_authorization_yields_zero_dispatch_and_zero_pending(test_env):
    """I4-G2: Expired authorization fails closed before dispatch and pending."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g2_02"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        valid_offset_sec=-10.0,  # expired
    )

    with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_AUTH_VALIDITY_WINDOW_EXPIRED" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g2_execution_nonce_mismatch_yields_zero_dispatch_and_zero_pending(test_env):
    """I4-G2: Execution ID != token_nonce is rejected before dispatch and pending."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g2_03"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        mismatch_nonce="exec_other_nonce_1234567890abcdef",
    )

    with pytest.raises(WholePlanReconciliationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_EXECUTION_NONCE_MISMATCH" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



# ==============================================================================
# GROUP I4-G3: Action Assembly & Digest Equality Gate
# ==============================================================================

def test_i4_g3_action_geometry_mismatch_rejected_before_dispatch(test_env):
    """I4-G3: Action geometry mismatch against auth is rejected before dispatch."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g3_01"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Mutate action geometry
    mismatched_action = dataclasses.replace(action, duration_frames=999)

    with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
        adapter.execute_isolated_action(mismatched_action, auth)
    assert "ERR_TIMELINE_DURATION_MISMATCH" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g3_action_digest_mismatch_rejected_before_dispatch(test_env):
    """I4-G3: Tampered action digest in auth is rejected with ERR_ACTION_DIGEST_MISMATCH."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g3_02"
    coordinator.acquire_target_lock(timeline_uid)

    # Sign with a different action payload digest
    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        tamper_digest="d" * 64,
    )

    with pytest.raises(WholePlanReconciliationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_ACTION_DIGEST_MISMATCH" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



# ==============================================================================
# GROUP I4-G4: Success Path WAL State Machine & Identity Envelope
# ==============================================================================

def test_i4_g4_success_path_ordered_wal_and_envelope(test_env):
    """
    I4-G4: Successful mock action execution writes ordered START -> ACTION_PENDING -> ACTION_COMMITTED,
    includes full five-field identity envelope, exact payload equality, and 1 gateway call.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(mock_item_id="item_resolve_test_42")
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g4_01"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    result = adapter.execute_isolated_action(action, auth)

    # Verify result dictionary
    assert result["status"] == "COMMITTED"
    assert result["execution_id"] == auth.execution_id
    assert result["action_id"] == action.action_id
    assert result["resolve_item_id"] == "item_resolve_test_42"
    assert result["target_ledger_state"] == "ACTIVE"
    assert result["terminalized"] is False
    assert gateway.call_count == 1

    # Confirm durable ledger remains ACTIVE on disk; single-action WAL commit is not a terminal receipt (I4-R3-01)
    d_entry = coordinator.durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert d_entry is not None
    assert d_entry.state == "ACTIVE"
    assert d_entry.terminal_receipt_id is None

    # Replay journal and verify exact records
    replay = journal.replay_journal(auth.execution_id)
    assert replay.is_clean is True
    assert len(replay.entries) == 3

    e_start, e_pending, e_committed = replay.entries

    # 1. START record verification (I4-02: exact source-consumer schema total_actions=1)
    assert e_start.seq == 0
    assert e_start.stage == "START"
    assert e_start.action_params == {"total_actions": 1}

    # Verify START control record envelope passes engine consumer validation (I4-02)
    from src.services.resolve_execution_engine import ResolveExecutionEngine
    ResolveExecutionEngine._validate_control_record_envelope(
        e_start, expected_stage="START", exec_id=auth.execution_id
    )

    # Verify pending journal state machine passes engine consumer validation (I4-02)
    committed_indices, committed_set, last_pending = ResolveExecutionEngine._validate_journal_wal_state_machine(
        [e_start, e_pending], planned_count=1, exec_id=auth.execution_id
    )
    assert committed_indices == []
    assert committed_set == set()
    assert last_pending == 0

    # 2. ACTION_PENDING record verification
    assert e_pending.seq == 1
    assert e_pending.stage == "ACTION_PENDING"
    assert e_pending.action_type == action.action_type
    assert e_pending.track_type == action.track_type.lower()
    assert e_pending.track_index == action.track_index

    # 3. ACTION_COMMITTED record verification
    assert e_committed.seq == 2
    assert e_committed.stage == "ACTION_COMMITTED"
    assert e_committed.resolve_item_id == "item_resolve_test_42"

    # Exact payload equality across PENDING and COMMITTED
    assert e_pending.action_params == e_committed.action_params

    # Verify all 16 canonical keys present in action_params
    expected_keys = {
        "action_index", "action_type", "authorization_id", "duration_frames",
        "execution_id", "plan_idempotency_key", "record_frame", "role",
        "schema_version", "source_frame_duration", "source_frame_start",
        "source_path", "target_project_uid", "target_timeline_uid",
        "track_index", "track_type"
    }
    assert set(e_pending.action_params.keys()) == expected_keys

    coordinator.close()



# ==============================================================================
# GROUP I4-G5: Clean Pre-Dispatch Rejection & Caller Target Lock Release
# ==============================================================================

def test_i4_g5_clean_pre_dispatch_rejection_releases_caller_lock(test_env):
    """
    I4-G5: On proven pre-dispatch rejection when caller owns lock,
    adapter releases the lock cleanly, leaving 0 pending records and 0 gateway calls.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g5_01"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    # Cause pre-dispatch failure via digest mismatch
    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        tamper_digest="9" * 64,
    )

    with pytest.raises(WholePlanReconciliationError):
        adapter.execute_isolated_action(action, auth)

    # Verify lock was cleanly released
    assert coordinator.has_target_lock(timeline_uid) is False
    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g5_target_not_locked_rejects_without_attempting_release(test_env):
    """I4-G5: When caller has not acquired lock, fails closed without releasing foreign state."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g5_02"
    # Target is NOT locked
    assert coordinator.has_target_lock(timeline_uid) is False

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    with pytest.raises(TargetExclusionLockError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_TARGET_NOT_LOCKED" in str(exc_info.value)

    assert gateway.call_count == 0
    assert not os.path.exists(journal.journal_path(auth.execution_id))

    coordinator.close()



def test_i4_g5_pre_dispatch_release_failure_propagates_structured_error(test_env):
    """I4-G5: If target lock release fails during pre-dispatch cleanup, fails closed."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g5_03"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        tamper_digest="8" * 64,
    )

    # Patch release_target_lock to simulate unlock fault
    with patch.object(coordinator, "release_target_lock", side_effect=TargetLockReleaseError("ERR_FLOCK_UN_FAILED")):
        with pytest.raises(TargetLockReleaseError) as exc_info:
            adapter.execute_isolated_action(action, auth)
        assert "ERR_PRE_DISPATCH_RELEASE_FAILED" in str(exc_info.value)

    coordinator.close()



@pytest.mark.parametrize("history_stage", ["START", "ACTION_PENDING", "ACTION_COMMITTED", "ACTION_FAILED", "FINISH", "TORN_TAIL"])
def test_i4_g5_preexisting_journal_history_rejected_before_dispatch(test_env, history_stage):
    """
    I4-03: When execution journal already has prior history (START, PENDING, COMMITTED,
    FAILED, FINISH, or torn-tail), fails closed with ERR_JOURNAL_ALREADY_EXISTS before
    gateway dispatch and cleanly releases caller lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = f"tl_g5_hist_{history_stage.lower()}"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Seed prior history
    journal_path = journal.journal_path(auth.execution_id)
    if history_stage == "TORN_TAIL":
        with open(journal_path, "w", encoding="utf-8") as f:
            f.write('{"seq": 0, "stage": "START", "action_params": {"total_actions": 1}\n{"seq": 1, "sta')
    else:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        start_ent = JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso, action_params={"total_actions": 1})
        journal.append_entry(auth.execution_id, start_ent)

        if history_stage in ("ACTION_PENDING", "ACTION_COMMITTED", "ACTION_FAILED", "FINISH"):
            pend_ent = JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params={"total_actions": 1})
            journal.append_entry(auth.execution_id, pend_ent)

        if history_stage in ("ACTION_COMMITTED", "FINISH"):
            com_ent = JournalEntry(seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params={"total_actions": 1}, resolve_item_id="item_01")
            journal.append_entry(auth.execution_id, com_ent)

        if history_stage == "ACTION_FAILED":
            fail_ent = JournalEntry(seq=2, stage="ACTION_FAILED", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params={"total_actions": 1}, error_code="ERR_FAIL")
            journal.append_entry(auth.execution_id, fail_ent)

        if history_stage == "FINISH":
            fin_ent = JournalEntry(seq=3, stage="FINISH", action_type="NO_OP", timestamp_iso=now_iso, action_params={}, note="COMPLETED_ALL_ACTIONS")
            journal.append_entry(auth.execution_id, fin_ent)

    # Invoke adapter: must fail closed before dispatch
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        adapter.execute_isolated_action(action, auth)
    assert "ERR_JOURNAL_ALREADY_EXISTS" in str(exc_info.value)

    # Verify zero gateway calls
    assert gateway.call_count == 0

    # Verify caller target lock is PRESERVED (exclusion held for unresolved/prior history - I4-R2-01)
    assert coordinator.has_target_lock(timeline_uid) is True

    coordinator.close()



def test_i4_g5_pending_wal_persistence_failure_retains_exclusion_lock(test_env):
    """
    I4-05: An append/fsync failure during START or ACTION_PENDING persistence represents
    an ambiguous persistence outcome. The adapter must NOT release the target lock cleanly;
    exclusion must be preserved and AcceptanceGateError propagated.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g5_persist_fail"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Simulate fsync / write failure on START
    with patch.object(journal, "append_entry", side_effect=IOError("Simulated write failure on START")):
        with pytest.raises(AcceptanceGateError) as exc_info:
            adapter.execute_isolated_action(action, auth)
        assert "ERR_JOURNAL_PERSISTENCE_AMBIGUOUS" in str(exc_info.value)

    # Verify zero gateway calls
    assert gateway.call_count == 0

    # Verify target lock was NOT released (exclusion preserved!)
    assert coordinator.has_target_lock(timeline_uid) is True

    coordinator.close()



def test_i4_g5_action_pending_append_failure_retains_exclusion_lock(test_env):
    """
    I4-05: If START succeeds but ACTION_PENDING append fails (e.g. disk error/torn tail),
    the adapter preserves exclusion and does NOT release the target lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g5_pending_fail"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    original_append = journal.append_entry

    def mock_append(exec_id, entry):
        if entry.stage == "ACTION_PENDING":
            raise IOError("Simulated partial write on ACTION_PENDING")
        return original_append(exec_id, entry)

    with patch.object(journal, "append_entry", side_effect=mock_append):
        with pytest.raises(AcceptanceGateError) as exc_info:
            adapter.execute_isolated_action(action, auth)
        assert "ERR_JOURNAL_PERSISTENCE_AMBIGUOUS" in str(exc_info.value)

    assert gateway.call_count == 0
    assert coordinator.has_target_lock(timeline_uid) is True

    coordinator.close()



# ==============================================================================
# GROUP I4-G6: Post-Dispatch Exception & In-Doubt Lock Retention
# ==============================================================================

def test_i4_g6_post_dispatch_exception_latches_in_doubt_and_retains_lock(test_env):
    """
    I4-G6: When fake gateway raises exception after ACTION_PENDING is durable:
    1. Appends ACTION_FAILED with exact payload equality
    2. Marks timeline IN_DOUBT_LOCKED on coordinator with unique incident ID
    3. Retains target exclusion lock (lock_retained=True)
    4. Does NOT call release_target_lock, does NOT write FINISH, does NOT retry
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(configured_exception=RuntimeError("Simulated Resolve gateway timeout"))
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g6_01"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    result = adapter.execute_isolated_action(action, auth)

    # Classification
    assert result["status"] == "TERMINAL_ACTION_FAILED_IN_DOUBT"
    assert result["execution_id"] == auth.execution_id
    assert result["action_id"] == action.action_id
    assert result["lock_retained"] is True
    assert result["target_lock_state"] == "IN_DOUBT_LOCKED"
    assert "inc_" in result["incident_id"]

    # Target exclusion lock strictly retained
    assert coordinator._target_locks[timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[timeline_uid].fd is not None
    assert coordinator.has_target_lock(timeline_uid) is False  # locked-out from normal acquisition

    # Exactly 1 call (no retry)
    assert gateway.call_count == 1

    # Verify journal contains START -> ACTION_PENDING -> ACTION_FAILED (no FINISH)
    replay = journal.replay_journal(auth.execution_id)
    assert replay.is_clean is True
    stages = [e.stage for e in replay.entries]
    assert stages == ["START", "ACTION_PENDING", "ACTION_FAILED"]
    assert "FINISH" not in stages

    e_pending = replay.entries[1]
    e_failed = replay.entries[2]

    # Exact payload equality between PENDING and FAILED
    assert e_pending.action_params == e_failed.action_params
    assert e_failed.error_code == "ERR_GATEWAY_DISPATCH_EXCEPTION"
    assert "Simulated Resolve gateway timeout" in e_failed.error_message
    assert f"incident_id={result['incident_id']}" in e_failed.note

    coordinator.close()



def test_i4_g6_post_dispatch_handling_failure_propagates_and_retains_lock(test_env):
    """
    I4-G6: If journal write fails during post-dispatch failure handling,
    coordinator still marks timeline in-doubt, exclusion lock is retained,
    and AcceptanceGateError is propagated without downgrading.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(configured_exception=RuntimeError("Gateway crash"))
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g6_02"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Let START and ACTION_PENDING succeed, but fail on ACTION_FAILED append
    original_append = journal.append_entry
    call_idx = 0

    def mock_append(exec_id, entry):
        nonlocal call_idx
        call_idx += 1
        if entry.stage == "ACTION_FAILED":
            raise IOError("Simulated disk full during ACTION_FAILED write")
        return original_append(exec_id, entry)

    with patch.object(journal, "append_entry", side_effect=mock_append):
        with pytest.raises(AcceptanceGateError) as exc_info:
            adapter.execute_isolated_action(action, auth)
        assert "ERR_POST_DISPATCH_HANDLING_FAILED" in str(exc_info.value)

    # Target exclusion lock strictly retained in IN_DOUBT_LOCKED state
    assert coordinator._target_locks[timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[timeline_uid].fd is not None
    assert coordinator.has_target_lock(timeline_uid) is False

    coordinator.close()



def test_i4_g6_post_dispatch_commit_persistence_failure_latches_in_doubt_and_retains_lock(test_env):
    """
    I4-04: When the fake gateway successfully executes the action, but appending/fsyncing
    ACTION_COMMITTED fails (e.g. disk write failure), this is a post-dispatch durability failure.
    The adapter must latch IN_DOUBT_LOCKED, retain the target exclusion lock, NOT write FINISH,
    NOT retry, and propagate a structured in-doubt failure.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(mock_item_id="item_resolve_404")
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g6_commit_fail"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    original_append = journal.append_entry

    def mock_append(exec_id, entry):
        if entry.stage == "ACTION_COMMITTED":
            raise IOError("Simulated disk full during ACTION_COMMITTED append")
        return original_append(exec_id, entry)

    with patch.object(journal, "append_entry", side_effect=mock_append):
        with pytest.raises(AcceptanceGateError) as exc_info:
            adapter.execute_isolated_action(action, auth)
        assert "ERR_ACTION_COMMIT_PERSISTENCE_FAILED" in str(exc_info.value)
        assert getattr(exc_info.value, "target_lock_state", None) == "IN_DOUBT_LOCKED"
        assert getattr(exc_info.value, "lock_retained", None) is True
        assert "inc_" in getattr(exc_info.value, "incident_id", "")

    # Target exclusion lock strictly retained in IN_DOUBT_LOCKED state
    assert coordinator._target_locks[timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[timeline_uid].fd is not None
    assert coordinator.has_target_lock(timeline_uid) is False  # locked-out

    # Gateway was dispatched exactly once (no retry)
    assert gateway.call_count == 1

    # Journal must not contain FINISH
    replay = journal.replay_journal(auth.execution_id)
    stages = [e.stage for e in replay.entries]
    assert "FINISH" not in stages

    coordinator.close()



def test_i4_r2_02_restart_blocks_admission_on_unresolved_history_and_durable_ledger(test_env):
    """
    I4-R2-02: After a crash or post-dispatch exception leaves an unresolved history on disk,
    the durable target ledger remains in ACTIVE state across process termination.
    A fresh coordinator instance and adapter started against the same directory structure
    must refuse fresh lock acquisition and admission until authorized reconciliation.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(configured_exception=RuntimeError("Gateway crash mid-execution"))
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_restart_01"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Execute action: enters in-doubt state
    result = adapter.execute_isolated_action(action, auth)
    assert result["status"] == "TERMINAL_ACTION_FAILED_IN_DOUBT"

    # Confirm durable ledger is ACTIVE on disk
    durable_ledger = coordinator.durable_ledger
    ledger_entry = durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert ledger_entry is not None
    assert ledger_entry.state == "ACTIVE"

    # Close coordinator, simulating process exit
    coordinator.close()


    # --- RESTART SIMULATION: fresh coordinator and fresh adapter instance ---
    fresh_coordinator = make_coordinator(test_env)
    fresh_journal = ExecutionJournalService(test_env["base_dir"])
    fresh_gateway = FakeResolveGateway()
    fresh_adapter = IsolatedResolveTrialAdapter(fresh_coordinator, fresh_journal, fresh_gateway)

    # Fresh coordinator acquires lock
    fresh_coordinator.acquire_target_lock(timeline_uid)

    # Attempt to admit a new action on this timeline with fresh authorization
    action2, auth2 = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
        execution_id="exec_second_attempt_1234567890abcdef",
    )

    # Admission MUST fail closed due to durable ACTIVE state or existing journal history
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        fresh_adapter.execute_isolated_action(action2, auth2)
    assert "ERR_TARGET_ALREADY_ACTIVE" in str(exc_info.value) or "ERR_JOURNAL_ALREADY_EXISTS" in str(exc_info.value)

    # Zero gateway calls on fresh gateway
    assert fresh_gateway.call_count == 0

    fresh_coordinator.close()


def test_i4_r2_03_commit_persistence_failure_with_failed_latch_preserves_lock_and_surfaces_errors(test_env):
    """
    I4-R2-03: When commit persistence fails AND marking in-doubt on coordinator also fails,
    the adapter does not suppress the coordinator error, accurately reports actual lock state
    ("LOCKED"), surfaces both errors in structured AcceptanceGateError, and retains the OS lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(mock_item_id="item_resolve_403")
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_g6_commit_fail_latch_fail"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    original_append = journal.append_entry

    def mock_append(exec_id, entry):
        if entry.stage == "ACTION_COMMITTED":
            raise IOError("Simulated disk error on commit append")
        return original_append(exec_id, entry)

    # Simulate both commit append failure and coordinator latch failure
    with patch.object(journal, "append_entry", side_effect=mock_append):
        with patch.object(coordinator, "mark_timeline_in_doubt", side_effect=RuntimeError("Coordinator lock poisoned")):
            with pytest.raises(AcceptanceGateError) as exc_info:
                adapter.execute_isolated_action(action, auth)
            err = exc_info.value
            assert "ERR_ACTION_COMMIT_PERSISTENCE_FAILED" in str(err)
            assert "Simulated disk error on commit append" in str(err)
            assert "Coordinator lock poisoned" in str(err)
            # Must reflect real state (LOCKED), NOT falsely claiming IN_DOUBT_LOCKED (I4-R2-03)
            assert getattr(err, "target_lock_state", None) == "LOCKED"
            assert getattr(err, "lock_retained", None) is True
            assert getattr(err, "commit_error", None) is not None
            assert getattr(err, "coordinator_latch_error", None) is not None

    # OS lock file descriptor is strictly retained
    assert coordinator._target_locks[timeline_uid].fd is not None

    # Gateway was dispatched exactly once
    assert gateway.call_count == 1

    coordinator.close()
@pytest.mark.parametrize("mismatched_field,mismatched_val", [
    ("authorization_id", "auth_diff_mismatch_9999"),
    ("approval_id", "appr_diff_mismatch_9999"),
    ("proposal_id", "prop_diff_mismatch_9999"),
    ("proposal_revision", "9" * 64),
    ("plan_idempotency_key", "plan_diff_mismatch_9999"),
])
def test_i4_r3_02_reserved_ledger_identity_mismatch_blocks_activation_and_dispatch(
    test_env, mismatched_field, mismatched_val
):
    """
    I4-R3-02: When an existing RESERVED ledger shares the execution ID but differs in any
    authority-bearing identity field (authorization_id, approval_id, proposal_id,
    proposal_revision, plan_idempotency_key), the adapter must reject admission
    fail-closed without dispatch, without mutating the ledger, and strictly preserve target lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = f"tl_r3_mismatch_{mismatched_field}"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Pre-reserve ledger with matching execution_id but one mismatched authority-bearing field
    reserve_kwargs = {
        "target_project_uid": auth.target_project_uid,
        "target_timeline_uid": timeline_uid,
        "authorization_id": auth.authorization_id,
        "execution_id": auth.execution_id,
        "approval_id": auth.approval_record_id,
        "proposal_id": auth.proposal_id,
        "proposal_revision": auth.proposal_revision,
        "plan_idempotency_key": auth.plan_idempotency_key,
    }
    reserve_kwargs[mismatched_field] = mismatched_val
    coordinator.durable_ledger.reserve_target(**reserve_kwargs)

    # Confirm ledger is in RESERVED state on disk
    d_before = coordinator.durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert d_before is not None
    assert d_before.state == "RESERVED"

    # Execution must fail closed with ERR_TARGET_LEDGER_IDENTITY_MISMATCH
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        adapter.execute_isolated_action(action, auth)

    err_msg = str(exc_info.value)
    assert "ERR_TARGET_LEDGER_IDENTITY_MISMATCH" in err_msg
    assert mismatched_field in err_msg

    # Zero gateway calls
    assert gateway.call_count == 0

    # Target exclusion lock is strictly preserved
    assert coordinator.has_target_lock(timeline_uid) is True

    # Ledger state on disk must NOT have transitioned to ACTIVE
    d_after = coordinator.durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert d_after is not None
    assert d_after.state == "RESERVED"

    coordinator.close()


def test_i4_r3_02_reserved_ledger_project_uid_mismatch_blocks_activation_and_dispatch(test_env):
    """
    I4-R3-02: If durable ledger on disk contains a mismatched target_project_uid,
    adapter must reject admission with ERR_TARGET_LEDGER_IDENTITY_MISMATCH and preserve lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway()
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_r3_mismatch_project_uid"
    coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Pre-reserve ledger with matching execution_id
    coordinator.durable_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )

    # Corrupt target_project_uid in the ledger entry returned by get_ledger
    original_get_ledger = coordinator.durable_ledger.get_ledger
    def mock_get_ledger(p_uid, t_uid):
        entry = original_get_ledger(p_uid, t_uid)
        if entry is not None:
            from dataclasses import replace
            return replace(entry, target_project_uid="forged_proj_uid_9999")
        return None

    with patch.object(coordinator.durable_ledger, "get_ledger", side_effect=mock_get_ledger):
        with pytest.raises(WholePlanReconciliationError) as exc_info:
            adapter.execute_isolated_action(action, auth)

        err_msg = str(exc_info.value)
        assert "ERR_TARGET_LEDGER_IDENTITY_MISMATCH" in err_msg
        assert "target_project_uid" in err_msg

    assert gateway.call_count == 0
    assert coordinator.has_target_lock(timeline_uid) is True
    coordinator.close()


def test_i4_r3_02_matching_reserved_ledger_activates_and_proceeds(test_env):
    """
    I4-R3-02: When an existing RESERVED ledger matches the complete identity tuple of
    the incoming authorization, adapter activates the reservation and dispatches successfully.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["base_dir"])
    gateway = FakeResolveGateway(mock_item_id="item_matching_res_01")
    adapter = IsolatedResolveTrialAdapter(coordinator, journal, gateway)

    timeline_uid = "tl_r3_matching_res_01"
    coordinator.acquire_target_lock(timeline_uid)

    action, auth = make_action_and_matching_auth(
        test_env,
        target_timeline_uid=timeline_uid,
    )

    # Pre-reserve ledger with 100% matching identity fields
    coordinator.durable_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )

    d_before = coordinator.durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert d_before is not None
    assert d_before.state == "RESERVED"

    result = adapter.execute_isolated_action(action, auth)
    assert result["status"] == "COMMITTED"
    assert result["target_ledger_state"] == "ACTIVE"
    assert result["terminalized"] is False
    assert gateway.call_count == 1

    d_after = coordinator.durable_ledger.get_ledger(auth.target_project_uid, timeline_uid)
    assert d_after is not None
    assert d_after.state == "ACTIVE"
    assert d_after.terminal_receipt_id is None

    coordinator.close()
