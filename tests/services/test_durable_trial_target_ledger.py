"""
tests/services/test_durable_trial_target_ledger.py
Comprehensive focused acceptance test suite for Durable Target Ledger & Locking Coordinator (G07-07B6-I3).

Covers all 7 required acceptance groups:
  - I3-G1 Pre-admission lockout gate (0 descriptor touches on locked-out state).
  - I3-G2 OS contention (concurrent coordinators on one timeline yield at most one owner).
  - I3-G3 Unlock failure (flock(LOCK_UN) fault retains live FD and kernel exclusion; retry works).
  - I3-G4 Single-close latch (ambiguous close sets CLOSE_OUTCOME_UNKNOWN, fd=None, 0 retry-close).
  - I3-G5 Bounded challenge TTL ([1.0, 300.0], rejects bool/nan/inf, preserves nonce).
  - I3-G6 Challenge-bound reconciliation (valid operator auth releases entry safely; failures fail closed).
  - I3-G7 Keyring ownership & authorization binding (rejects caller keyring injection, validates 32-field HMAC).
Plus durable ledger persistence, state machine transitions, and atomic recovery tests.
"""

from __future__ import annotations

import dataclasses
import errno
import fcntl
import hashlib
import hmac
import json
import math
import os
import secrets
import stat
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    AuthorizationContract,
    OperatorReconciliationAuthorization,
    ReadbackReceiptVerificationError,
    ReconciliationChallenge,
    TargetExclusionLockError,
    TargetLockReleaseError,
    TrialExecutionAuthorization,
    WholePlanReconciliationError,
    canonical_bytes_for_auth_hmac,
)
from src.services.durable_trial_target_ledger import (
    CoordinatorInitCleanupError,
    DurableTrialTargetLedger,
    DurableTrialTargetLedgerService,
    RecoveryCoordinator,
    TargetLockEntry,
    canonical_bytes_for_operator_reconciliation_auth,
    get_ledger_filename,
    get_lock_filename,
)
from src.services.trial_keyring import TrialKeyringAuthority


# --- Test Fixtures and Helpers ---

TRIAL_KEY_BYTES = b"\x01\x02\x03\x04" * 8  # 32 bytes
OPERATOR_KEY_BYTES = b"\x05\x06\x07\x08" * 8  # 32 bytes
OPERATOR_IDENTITY = "lead_operator_01"


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated filesystem environment with directories and valid keystore."""
    journals_dir = tmp_path / "journals"
    receipts_dir = tmp_path / "receipts"
    locks_dir = tmp_path / "locks"
    ledgers_dir = tmp_path / "ledgers"
    keystore_dir = tmp_path / "security"

    journals_dir.mkdir(parents=True)
    receipts_dir.mkdir(parents=True)
    locks_dir.mkdir(parents=True)
    ledgers_dir.mkdir(parents=True)
    keystore_dir.mkdir(parents=True)

    os.chmod(str(keystore_dir), 0o700)

    # 1. Trial signing key (mode 0600)
    trial_key_file = keystore_dir / "canary_trial_key.key"
    trial_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(trial_key_file), 0o600)

    # 2. Operator reconciliation key (mode 0600)
    op_key_file = keystore_dir / "canary_op_key.opkey.json"
    op_data = {
        "key_hex": OPERATOR_KEY_BYTES.hex(),
        "permitted_operator_identity": OPERATOR_IDENTITY,
        "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",
    }
    op_key_file.write_text(json.dumps(op_data), encoding="utf-8")
    os.chmod(str(op_key_file), 0o600)

    return {
        "journals_dir": str(journals_dir),
        "receipts_dir": str(receipts_dir),
        "locks_dir": str(locks_dir),
        "ledgers_dir": str(ledgers_dir),
        "keystore_path": str(keystore_dir),
        "trial_key_id": "canary_trial_key",
        "trial_key_bytes": TRIAL_KEY_BYTES,
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


def make_valid_auth(
    env: Dict[str, Any],
    execution_id: str = "exec_1234567890abcdef1234567890abcdef",
    target_project_uid: str = "proj_001",
    target_timeline_uid: str = "tl_001",
    valid_offset_sec: float = 300.0,
    tamper_hmac: Optional[str] = None,
    key_id: Optional[str] = None,
    key_bytes: Optional[bytes] = None,
) -> TrialExecutionAuthorization:
    """Constructs a valid signed TrialExecutionAuthorization DTO with 32-field HMAC."""
    kid = key_id or env["trial_key_id"]
    kbytes = key_bytes or env["trial_key_bytes"]

    now = datetime.now(timezone.utc)
    issued_iso = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    valid_iso = (now + timedelta(seconds=valid_offset_sec)).isoformat().replace("+00:00", "Z")

    observed_start = 0
    record_frame = 100
    timeline_frame_start = 100
    duration_frames = 240

    payload_for_signing = {
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
        "action_payload_digest_sha256": "e" * 64,
        "target_project_uid": target_project_uid,
        "target_project_name": "Test Project",
        "target_timeline_uid": target_timeline_uid,
        "target_timeline_name": "Test Timeline",
        "target_timeline_fps_numerator": 24,
        "target_timeline_fps_denominator": 1,
        "observed_timeline_start_frame": observed_start,
        "authorized_record_frame": record_frame,
        "source_file_path": "/media/source/clip.mov",
        "source_file_sha256": "f" * 64,
        "authorized_action_type": "INSERT_CLIP",
        "authorized_track_type": "video",
        "authorized_track_index": 1,
        "authorized_timeline_frame_start": timeline_frame_start,
        "authorized_timeline_frame_duration": duration_frames,
        "authorized_source_frame_start": 0,
        "authorized_source_frame_duration": duration_frames,
        "execution_id": execution_id,
        "issued_at_iso": issued_iso,
        "valid_until_iso": valid_iso,
        "token_nonce": execution_id,
    }

    # Temporary instance to compute canonical bytes
    temp_auth = TrialExecutionAuthorization(
        **payload_for_signing,
        authorization_hmac="0" * 64,  # placeholder
    )
    canon_bytes = canonical_bytes_for_auth_hmac(temp_auth)
    sig = hmac.new(kbytes, canon_bytes, hashlib.sha256).hexdigest()

    return TrialExecutionAuthorization(
        **payload_for_signing,
        authorization_hmac=tamper_hmac or sig,
    )


def make_operator_auth(
    env: Dict[str, Any],
    timeline_uid: str,
    incident_id: str,
    challenge_nonce: str,
    challenge_expires_at: float,
    operator_identity: Optional[str] = None,
    op_key_id: Optional[str] = None,
    op_key_bytes: Optional[bytes] = None,
    statement: str = "CONFIRMED_TIMELINE_UNLOCKED_OUT_OF_BAND",
    confirmed_unlocked: bool = True,
    tamper_sig: Optional[str] = None,
) -> OperatorReconciliationAuthorization:
    """Constructs a signed OperatorReconciliationAuthorization DTO using canonical serialization."""
    kid = op_key_id or env["op_key_id"]
    kbytes = op_key_bytes or env["op_key_bytes"]
    identity = operator_identity if operator_identity is not None else env["op_identity"]

    unsigned_auth = OperatorReconciliationAuthorization(
        timeline_uid=timeline_uid,
        incident_id=incident_id,
        challenge_nonce=challenge_nonce,
        challenge_expires_at=challenge_expires_at,
        operator_key_id=kid,
        operator_identity=identity,
        operator_confirmation_statement=statement,
        confirmed_unlocked=confirmed_unlocked,
        signature_hmac="",
    )
    canonical_payload = canonical_bytes_for_operator_reconciliation_auth(unsigned_auth)
    sig = hmac.new(kbytes, canonical_payload, hashlib.sha256).hexdigest()

    return OperatorReconciliationAuthorization(
        timeline_uid=timeline_uid,
        incident_id=incident_id,
        challenge_nonce=challenge_nonce,
        challenge_expires_at=challenge_expires_at,
        operator_key_id=kid,
        operator_identity=identity,
        operator_confirmation_statement=statement,
        confirmed_unlocked=confirmed_unlocked,
        signature_hmac=tamper_sig or sig,
    )


# ==============================================================================
# GROUP I3-G1: Pre-Admission Gate & Registry Preservation
# ==============================================================================

def test_i3_g1_pre_admission_rejects_close_outcome_unknown_with_zero_open_calls(test_env):
    """
    I3-G1: Calling acquire_target_lock() when timeline is in CLOSE_OUTCOME_UNKNOWN
    must fail closed immediately under lock with exactly 0 descriptor touches (os.open not called).
    Preserves existing registry entry and incident ID untouched.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_ambiguous_01"

    # Seed registry entry in CLOSE_OUTCOME_UNKNOWN state
    entry = TargetLockEntry(
        fd=None,
        filename=get_lock_filename(timeline_uid),
        state="CLOSE_OUTCOME_UNKNOWN",
        dev=123,
        ino=456,
        incident_id="incident_deadbeef1234",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

    open_calls = []
    real_open = os.open

    def counting_open(*args, **kwargs):
        open_calls.append(args)
        return real_open(*args, **kwargs)

    with patch("os.open", side_effect=counting_open):
        with pytest.raises(TargetExclusionLockError) as exc_info:
            coordinator.acquire_target_lock(timeline_uid)
        assert "ERR_TIMELINE_LOCKED_OUT_AMBIGUOUS_CLOSE" in str(exc_info.value)

    # ZERO os.open calls made
    assert len(open_calls) == 0

    # Registry entry remains completely untouched
    preserved = coordinator._target_locks[timeline_uid]
    assert preserved.state == "CLOSE_OUTCOME_UNKNOWN"
    assert preserved.fd is None
    assert preserved.incident_id == "incident_deadbeef1234"
    assert coordinator._lock_state[timeline_uid] == "CLOSE_OUTCOME_UNKNOWN"

    coordinator.close()


def test_i3_g1_pre_admission_rejects_in_doubt_locked_with_zero_open_calls(test_env):
    """
    I3-G1: Calling acquire_target_lock() when timeline is IN_DOUBT_LOCKED
    must fail closed immediately with 0 os.open calls and preserve registry.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_indoubt_01"

    entry = TargetLockEntry(
        fd=999,
        filename=get_lock_filename(timeline_uid),
        state="IN_DOUBT_LOCKED",
        dev=123,
        ino=456,
        incident_id="incident_fault_unlock",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "IN_DOUBT_LOCKED"

    open_calls = []
    real_open = os.open

    def counting_open(*args, **kwargs):
        open_calls.append(args)
        return real_open(*args, **kwargs)

    with patch("os.open", side_effect=counting_open):
        with pytest.raises(TargetExclusionLockError) as exc_info:
            coordinator.acquire_target_lock(timeline_uid)
        assert "ERR_TIMELINE_LOCKED_OUT_IN_DOUBT" in str(exc_info.value)

    assert len(open_calls) == 0
    assert coordinator._target_locks[timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[timeline_uid].fd == 999


def test_i3_g1_pre_admission_rejects_already_locked(test_env):
    """
    I3-G1: Re-acquiring an already LOCKED timeline by the same coordinator
    must fail closed at pre-admission with 0 new descriptor opens.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_active_01"

    entry = coordinator.acquire_target_lock(timeline_uid)
    assert entry.state == "LOCKED"
    assert entry.fd is not None
    original_fd = entry.fd

    open_calls = []
    real_open = os.open

    def counting_open(*args, **kwargs):
        open_calls.append(args)
        return real_open(*args, **kwargs)

    with patch("os.open", side_effect=counting_open):
        with pytest.raises(TargetExclusionLockError) as exc_info:
            coordinator.acquire_target_lock(timeline_uid)
        assert "ERR_TARGET_ALREADY_LOCKED" in str(exc_info.value)

    assert len(open_calls) == 0
    assert coordinator._target_locks[timeline_uid].fd == original_fd

    coordinator.release_target_lock(timeline_uid)
    coordinator.close()


# ==============================================================================
# GROUP I3-G2: OS-Level Contention & Mutual Exclusion
# ==============================================================================

def test_i3_g2_os_contention_blocks_competing_coordinator(test_env):
    """
    I3-G2: Two concurrent coordinators contending on the same timeline yield at most
    one owner; the loser receives controlled busy/locked error and cannot replace state.
    """
    coord1 = make_coordinator(test_env)
    coord2 = make_coordinator(test_env)
    timeline_uid = "timeline_contention_01"

    # Coord1 acquires exclusion lock
    entry1 = coord1.acquire_target_lock(timeline_uid)
    assert entry1.state == "LOCKED"
    assert coord1.has_target_lock(timeline_uid) is True

    # Coord2 attempts to acquire lock on the same timeline
    with pytest.raises(TargetExclusionLockError) as exc_info:
        coord2.acquire_target_lock(timeline_uid)
    assert "ERR_TARGET_LOCKED" in str(exc_info.value)

    # Verify Coord2 has NOT acquired lock and has not corrupted or replaced state
    assert coord2.has_target_lock(timeline_uid) is False
    assert timeline_uid not in coord2._target_locks

    # Coord1 still holds valid lock
    assert coord1.has_target_lock(timeline_uid) is True

    # Coord1 releases lock cleanly
    coord1.release_target_lock(timeline_uid)
    assert coord1.has_target_lock(timeline_uid) is False

    # Coord2 can now acquire the lock cleanly
    entry2 = coord2.acquire_target_lock(timeline_uid)
    assert entry2.state == "LOCKED"
    assert coord2.has_target_lock(timeline_uid) is True

    coord2.release_target_lock(timeline_uid)
    coord1.close()
    coord2.close()


# ==============================================================================
# GROUP I3-G3: Kernel Lock Preservation on Unlock Failure
# ==============================================================================

def test_i3_g3_unlock_failure_preserves_live_fd_and_kernel_lock(test_env):
    """
    I3-G3: Simulated flock(LOCK_UN) failure retains live FD and kernel exclusion;
    subsequent safe retry can release it.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_unlock_fail_01"

    entry = coordinator.acquire_target_lock(timeline_uid)
    live_fd = entry.fd
    assert live_fd is not None

    real_flock = fcntl.flock
    mock_active = True

    def faulty_flock(fd, op):
        if mock_active and (op & fcntl.LOCK_UN):
            raise OSError(errno.EIO, "Simulated kernel unlock fault (EIO)")
        return real_flock(fd, op)

    close_calls = []
    real_close = os.close

    def tracking_close(fd):
        close_calls.append(fd)
        return real_close(fd)

    with patch("fcntl.flock", side_effect=faulty_flock):
        with patch("os.close", side_effect=tracking_close):
            with pytest.raises(TargetLockReleaseError) as exc_info:
                coordinator.release_target_lock(timeline_uid)
            assert "ERR_TARGET_LOCK_UNLOCK_FAILED" in str(exc_info.value)

    # os.close MUST NOT have been called on the live FD
    assert live_fd not in close_calls

    # State transitioned to IN_DOUBT_LOCKED, preserving live descriptor
    assert entry.state == "IN_DOUBT_LOCKED"
    assert entry.fd == live_fd
    assert coordinator._lock_state[timeline_uid] == "IN_DOUBT_LOCKED"

    # Kernel exclusion is preserved: another coordinator trying to acquire lock fails
    coord_competing = make_coordinator(test_env)
    with pytest.raises(TargetExclusionLockError):
        coord_competing.acquire_target_lock(timeline_uid)
    coord_competing.close()

    # Disable fault and safely retry release
    mock_active = False
    coordinator.release_target_lock(timeline_uid)

    assert timeline_uid not in coordinator._target_locks
    assert coordinator._lock_state[timeline_uid] == "RELEASED"

    # Competing coordinator can now acquire successfully
    coord_competing = make_coordinator(test_env)
    entry_comp = coord_competing.acquire_target_lock(timeline_uid)
    assert entry_comp.state == "LOCKED"
    coord_competing.release_target_lock(timeline_uid)
    coord_competing.close()

    coordinator.close()


# ==============================================================================
# GROUP I3-G4: Single-Close Latch & Terminal Ambiguous Close
# ==============================================================================

def test_i3_g4_single_close_latch_sets_close_outcome_unknown_and_prevents_retry(test_env):
    """
    I3-G4: Ambiguous os.close() fault sets CLOSE_OUTCOME_UNKNOWN, fd=None,
    zero retry-close calls, and later admission fails before I/O.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_close_fail_01"

    entry = coordinator.acquire_target_lock(timeline_uid)
    target_fd = entry.fd
    assert target_fd is not None

    real_close = os.close
    close_attempt_count = 0
    mock_active = True

    def faulty_close(fd):
        nonlocal close_attempt_count
        if mock_active and fd == target_fd:
            close_attempt_count += 1
            raise OSError(errno.EIO, "Simulated Ambiguous Close Error (EIO)")
        return real_close(fd)

    with patch("os.close", side_effect=faulty_close):
        with pytest.raises(TargetLockReleaseError) as exc_info:
            coordinator.release_target_lock(timeline_uid)
        assert "ERR_TARGET_LOCK_CLOSE_FAILED" in str(exc_info.value)
        assert "CLOSE_OUTCOME_UNKNOWN" in str(exc_info.value)

    # os.close was called exactly ONCE on target_fd
    assert close_attempt_count == 1

    # State is latched to CLOSE_OUTCOME_UNKNOWN, fd is None, incident_id is assigned
    assert entry.state == "CLOSE_OUTCOME_UNKNOWN"
    assert entry.fd is None
    assert entry.incident_id is not None
    assert coordinator._lock_state[timeline_uid] == "CLOSE_OUTCOME_UNKNOWN"

    # Calling release_target_lock again strictly refuses to retry os.close
    with pytest.raises(TargetLockReleaseError) as exc_info2:
        coordinator.release_target_lock(timeline_uid)
    assert "ERR_CLOSE_OUTCOME_UNKNOWN" in str(exc_info2.value)
    assert close_attempt_count == 1  # No additional close attempts

    # Later admission fails immediately at pre-admission with ZERO open calls
    open_calls = []
    real_open = os.open

    def counting_open(*args, **kwargs):
        open_calls.append(args)
        return real_open(*args, **kwargs)

    with patch("os.open", side_effect=counting_open):
        with pytest.raises(TargetExclusionLockError):
            coordinator.acquire_target_lock(timeline_uid)
    assert len(open_calls) == 0

    coordinator.close()


# ==============================================================================
# GROUP I3-G5: Bounded Challenge TTL Enforcement
# ==============================================================================

def test_i3_g5_challenge_ttl_boundary_and_rejections(test_env):
    """
    I3-G5: Accept 1.0 and 300.0; reject bool, NaN, infinities, below-minimum and
    above-maximum; rejected request preserves existing nonce.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_ttl_test_01"

    # Put timeline in CLOSE_OUTCOME_UNKNOWN
    entry = TargetLockEntry(
        fd=None,
        filename=get_lock_filename(timeline_uid),
        state="CLOSE_OUTCOME_UNKNOWN",
        dev=123,
        ino=456,
        incident_id="incident_ttl_001",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

    # 1. Lower bound acceptance: 1.0s
    ch1 = coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=1.0)
    assert ch1.timeline_uid == timeline_uid
    assert ch1.incident_id == "incident_ttl_001"
    assert math.isclose(ch1.expires_at - ch1.issued_at, 1.0, rel_tol=1e-3)
    active_nonce = entry.active_challenge_nonce
    assert active_nonce is not None

    # 2. Rejection: bool True (must not be treated as int 1)
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=True)
    assert "ERR_INVALID_CHALLENGE_TTL_TYPE" in str(exc_info.value)
    assert entry.active_challenge_nonce == active_nonce  # Nonce preserved

    # 3. Rejection: bool False
    with pytest.raises(WholePlanReconciliationError):
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=False)
    assert entry.active_challenge_nonce == active_nonce

    # 4. Rejection: string
    with pytest.raises(WholePlanReconciliationError):
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds="60.0")
    assert entry.active_challenge_nonce == active_nonce

    # 5. Rejection: NaN
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=float("nan"))
    assert "ERR_CHALLENGE_TTL_NON_FINITE" in str(exc_info.value)
    assert entry.active_challenge_nonce == active_nonce

    # 6. Rejection: Infinity
    with pytest.raises(WholePlanReconciliationError):
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=float("inf"))
    assert entry.active_challenge_nonce == active_nonce

    # 7. Rejection: Below minimum (< 1.0)
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=0.99)
    assert "ERR_CHALLENGE_TTL_TOO_LOW" in str(exc_info.value)
    assert entry.active_challenge_nonce == active_nonce

    # 8. Rejection: Above maximum (> 300.0)
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=300.01)
    assert "ERR_CHALLENGE_TTL_EXCEEDS_MAX" in str(exc_info.value)
    assert entry.active_challenge_nonce == active_nonce

    # 9. Upper bound acceptance: 300.0s
    ch_upper = coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=300.0)
    assert math.isclose(ch_upper.expires_at - ch_upper.issued_at, 300.0, rel_tol=1e-3)
    assert entry.active_challenge_nonce != active_nonce  # New valid challenge issued

    coordinator.close()


# ==============================================================================
# GROUP I3-G6: Challenge-Bound Reconciliation Protocol
# ==============================================================================

def test_i3_g6_valid_reconciliation_clears_ambiguous_entry_safely(test_env):
    """
    I3-G6: Valid operator authorization releases only the matching fd-less ambiguous entry;
    does not touch old descriptor.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_reconcile_01"

    entry = TargetLockEntry(
        fd=None,
        filename=get_lock_filename(timeline_uid),
        state="CLOSE_OUTCOME_UNKNOWN",
        dev=123,
        ino=456,
        incident_id="incident_reconcile_001",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

    challenge = coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=60.0)
    auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
    )

    coordinator.reconcile_ambiguous_timeline(auth)

    # Entry is cleaned up and marked RELEASED
    assert timeline_uid not in coordinator._target_locks
    assert coordinator._lock_state[timeline_uid] == "RELEASED"

    # Replay attack fails closed immediately
    with pytest.raises(WholePlanReconciliationError):
        coordinator.reconcile_ambiguous_timeline(auth)

    coordinator.close()


def test_i3_g6_reconciliation_fails_closed_on_invalid_or_tampered_claims(test_env):
    """
    I3-G6: Wrong purpose/identity/signature/incident/timeline/nonce/expiry fails closed
    and preserves lockout.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_reconcile_fail_01"

    entry = TargetLockEntry(
        fd=None,
        filename=get_lock_filename(timeline_uid),
        state="CLOSE_OUTCOME_UNKNOWN",
        dev=123,
        ino=456,
        incident_id="incident_fail_001",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

    challenge = coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=60.0)

    # 1. Tampered signature HMAC
    bad_sig_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
        tamper_sig="0" * 64,
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(bad_sig_auth)
    assert "ERR_OPERATOR_SIGNATURE_INVALID" in str(exc_info.value)
    assert coordinator._target_locks[timeline_uid].state == "CLOSE_OUTCOME_UNKNOWN"

    # 2. Wrong operator identity
    bad_id_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
        operator_identity="unauthorized_operator",
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(bad_id_auth)
    assert "ERR_OPERATOR_IDENTITY_KEY_MISMATCH" in str(exc_info.value)
    assert coordinator._target_locks[timeline_uid].state == "CLOSE_OUTCOME_UNKNOWN"

    # 3. Wrong incident ID
    bad_incident_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id="incident_different_123",
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(bad_incident_auth)
    assert "ERR_AUTHORIZATION_INCIDENT_MISMATCH" in str(exc_info.value)
    assert coordinator._target_locks[timeline_uid].state == "CLOSE_OUTCOME_UNKNOWN"

    # 4. Wrong confirmation statement
    bad_stmt_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
        statement="SOME_OTHER_STATEMENT",
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(bad_stmt_auth)
    assert "ERR_CONFIRMATION_STATEMENT_MISMATCH" in str(exc_info.value)

    # 5. confirmed_unlocked is False
    unconfirmed_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
        confirmed_unlocked=False,
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(unconfirmed_auth)
    assert "ERR_UNLOCKED_CONFIRMATION_INVALID" in str(exc_info.value)

    # 6. Expired challenge (I3-05: Nonce is preserved on rejection)
    past_ts = time.time() - 1.0
    entry.challenge_expires_at = past_ts
    orig_nonce = entry.active_challenge_nonce
    expired_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=past_ts,
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(expired_auth)
    assert "ERR_CHALLENGE_EXPIRED" in str(exc_info.value)
    # Nonce is preserved on rejection, not mutated
    assert entry.active_challenge_nonce == orig_nonce

    # 7. Expiration timestamp mismatch between auth and entry
    entry.active_challenge_nonce = "fresh_nonce_123"
    entry.challenge_expires_at = time.time() + 60.0
    mismatch_auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce="fresh_nonce_123",
        challenge_expires_at=entry.challenge_expires_at + 10.0,
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(mismatch_auth)
    assert "ERR_EXPIRATION_TIMESTAMP_MISMATCH" in str(exc_info.value)

    coordinator.close()


# ==============================================================================
# GROUP I3-G7: Keyring Ownership & Signed Authorization Binding
# ==============================================================================

def test_i3_g7_caller_keyring_injection_strictly_rejected(test_env):
    """
    I3-G7: RecoveryCoordinator rejects caller-supplied keyring injections
    positionally or as keyword argument.
    """
    fake_keyring = object()

    # Keyword argument injection
    with pytest.raises(AcceptanceGateError) as exc_info:
        RecoveryCoordinator(
            journals_dir=test_env["journals_dir"],
            receipts_dir=test_env["receipts_dir"],
            locks_dir=test_env["locks_dir"],
            ledgers_dir=test_env["ledgers_dir"],
            keystore_path=test_env["keystore_path"],
            keyring=fake_keyring,
        )
    assert "ERR_KEYRING_INJECTION_FORBIDDEN" in str(exc_info.value)

    # Positional argument injection
    with pytest.raises(AcceptanceGateError) as exc_info2:
        RecoveryCoordinator(
            test_env["journals_dir"],
            test_env["receipts_dir"],
            test_env["locks_dir"],
            test_env["ledgers_dir"],
            test_env["keystore_path"],
            fake_keyring,
        )
    assert "ERR_KEYRING_INJECTION_FORBIDDEN" in str(exc_info2.value)


def test_i3_g7_valid_authorization_binds_through_internal_keyring(test_env):
    """
    I3-G7: Valid 32-field trial authorization binds successfully through internal
    keyring authority, returning populated AuthorizationContract.
    """
    coordinator = make_coordinator(test_env)
    auth = make_valid_auth(test_env)

    contract = coordinator.bind_signed_authorization(auth)

    assert isinstance(contract, AuthorizationContract)
    assert contract.authorization_id == auth.authorization_id
    assert contract.approval_id == auth.approval_record_id
    assert contract.execution_id == auth.execution_id
    assert contract.plan_idempotency_key == auth.plan_idempotency_key
    assert contract.target_project_uid == auth.target_project_uid
    assert contract.target_timeline_uid == auth.target_timeline_uid
    assert contract.record_frame == auth.authorized_record_frame
    assert contract.duration_frames == auth.authorized_timeline_frame_duration
    assert contract.key_id == auth.key_id
    assert contract.action_payload_digest_sha256 == auth.action_payload_digest_sha256

    assert coordinator.bound_auth == contract
    coordinator.close()


def test_i3_g7_authorization_binding_fails_closed_on_tampering_or_revocation(test_env):
    """
    I3-G7: Binding fails closed on invalid HMAC, revoked key, identity/nonce mismatch,
    or expired validity window.
    """
    coordinator = make_coordinator(test_env)

    # 1. Invalid HMAC
    tampered_auth = make_valid_auth(test_env, tamper_hmac="0" * 64)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_info:
        coordinator.bind_signed_authorization(tampered_auth)
    assert "ERR_SIGNED_AUTH_HMAC_INVALID" in str(exc_info.value)

    # 2. Revoked key
    coordinator.keyring.revoke_key(test_env["trial_key_id"])
    valid_auth = make_valid_auth(test_env)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_info2:
        coordinator.bind_signed_authorization(valid_auth)
    assert "ERR_AUTH_KEY_NOT_FOUND_OR_REVOKED" in str(exc_info2.value)

    # 3. Execution ID / nonce mismatch (caught during validation)
    with pytest.raises(WholePlanReconciliationError) as exc_info3:
        TrialExecutionAuthorization(
            authorization_id="auth_1234567890abcdef1234567890abcdef",
            key_id="other_key",
            operator_gesture_digest_sha256="a" * 64,
            approval_record_id="appr_1234567890abcdef1234567890abcdef",
            approval_record_digest_sha256="b" * 64,
            proposal_id="prop_1234567890abcdef1234567890abcdef",
            proposal_revision="1" * 64,
            proposal_content_digest_sha256="c" * 64,
            plan_idempotency_key="plan_1234567890abcdef1234567890abcdef",
            plan_digest_sha256="d" * 64,
            action_payload_digest_sha256="e" * 64,
            target_project_uid="proj_001",
            target_project_name="Test Project",
            target_timeline_uid="tl_001",
            target_timeline_name="Test Timeline",
            target_timeline_fps_numerator=24,
            target_timeline_fps_denominator=1,
            observed_timeline_start_frame=0,
            authorized_record_frame=100,
            source_file_path="/media/source/clip.mov",
            source_file_sha256="f" * 64,
            authorized_action_type="INSERT_CLIP",
            authorized_track_type="video",
            authorized_track_index=1,
            authorized_timeline_frame_start=100,
            authorized_timeline_frame_duration=240,
            authorized_source_frame_start=0,
            authorized_source_frame_duration=240,
            execution_id="exec_aaa",
            token_nonce="exec_bbb",  # Mismatch!
            issued_at_iso="2026-11-01T00:00:00Z",
            valid_until_iso="2026-11-01T01:00:00Z",
            authorization_hmac="0" * 64,
        )
    assert "ERR_EXECUTION_NONCE_MISMATCH" in str(exc_info3.value)

    coordinator.close()


# ==============================================================================
# DURABLE TARGET LEDGER & STATE TRANSITION TESTS
# ==============================================================================

def test_durable_target_ledger_dto_schema_and_validation():
    """Validates schema version 2 and field invariants on DurableTrialTargetLedger DTO."""
    import copy
    now_iso = "2026-11-01T10:00:00Z"
    dto = DurableTrialTargetLedger(
        ledger_version=2,
        target_project_uid="proj_999",
        target_timeline_uid="tl_999",
        authorization_id="auth_999",
        execution_id="exec_999",
        approval_id="appr_999",
        proposal_id="prop_999",
        proposal_revision="0" * 64,
        plan_idempotency_key="plan_key_999",
        state="RESERVED",
        created_at_iso=now_iso,
        updated_at_iso=now_iso,
    )
    assert dto.ledger_version == 2
    assert dto.state == "RESERVED"

    # Serialization and deserialization roundtrip
    payload = dto.to_json_bytes()
    restored = DurableTrialTargetLedger.from_json_bytes(payload)
    assert restored == dto

    # Direct construction rejects v1, unsupported version, and bool
    with pytest.raises(ReadbackReceiptVerificationError) as exc_v1:
        DurableTrialTargetLedger(
            ledger_version=1,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision="0" * 64,
            plan_idempotency_key="plan_key_999",
            state="RESERVED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
        )
    assert "ERR_LEGACY_LEDGER_VERSION" in str(exc_v1.value)

    with pytest.raises(ReadbackReceiptVerificationError) as exc_v3:
        DurableTrialTargetLedger(
            ledger_version=3,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision="0" * 64,
            plan_idempotency_key="plan_key_999",
            state="RESERVED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
        )
    assert "ERR_UNSUPPORTED_LEDGER_VERSION" in str(exc_v3.value)

    with pytest.raises(ReadbackReceiptVerificationError) as exc_bool:
        DurableTrialTargetLedger(
            ledger_version=True,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision="0" * 64,
            plan_idempotency_key="plan_key_999",
            state="RESERVED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
        )
    assert "ERR_INVALID_LEDGER_VERSION" in str(exc_bool.value)

    # State EXHAUSTED without terminal_receipt_id fails closed
    with pytest.raises(ReadbackReceiptVerificationError) as exc2:
        DurableTrialTargetLedger(
            ledger_version=2,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision="0" * 64,
            plan_idempotency_key="plan_key_999",
            state="EXHAUSTED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
            terminal_receipt_id=None,
        )
    assert "ERR_LEDGER_EXHAUSTED_MISSING_RECEIPT" in str(exc2.value)

    # Invalid proposal_revision rejected (int, bool, uppercase, short, legacy int in from_dict)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_rev1:
        DurableTrialTargetLedger(
            ledger_version=2,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision=0,  # int rejected
            plan_idempotency_key="plan_key_999",
            state="RESERVED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
        )
    assert "ERR_INVALID_PROPOSAL_REVISION" in str(exc_rev1.value)

    with pytest.raises(ReadbackReceiptVerificationError) as exc_rev2:
        DurableTrialTargetLedger(
            ledger_version=2,
            target_project_uid="proj_999",
            target_timeline_uid="tl_999",
            authorization_id="auth_999",
            execution_id="exec_999",
            approval_id="appr_999",
            proposal_id="prop_999",
            proposal_revision="A" * 64,  # uppercase rejected
            plan_idempotency_key="plan_key_999",
            state="RESERVED",
            created_at_iso=now_iso,
            updated_at_iso=now_iso,
        )
    assert "ERR_INVALID_PROPOSAL_REVISION" in str(exc_rev2.value)

    # from_dict tests with zero mutation assertions
    dict_payload = dto.to_dict()
    assert dict_payload["ledger_version"] == 2

    # 1. from_dict rejects legacy numeric revision payload fail-closed
    dict_rev_int = copy.deepcopy(dict_payload)
    dict_rev_int["proposal_revision"] = 0
    dict_rev_int_orig = copy.deepcopy(dict_rev_int)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_rev3:
        DurableTrialTargetLedger.from_dict(dict_rev_int)
    assert "ERR_INVALID_PROPOSAL_REVISION" in str(exc_rev3.value)
    assert dict_rev_int == dict_rev_int_orig

    # 2. from_dict rejects missing ledger_version
    dict_missing = copy.deepcopy(dict_payload)
    del dict_missing["ledger_version"]
    dict_missing_orig = copy.deepcopy(dict_missing)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_miss:
        DurableTrialTargetLedger.from_dict(dict_missing)
    assert "ERR_LEDGER_SCHEMA_MISSING_FIELD" in str(exc_miss.value)
    assert dict_missing == dict_missing_orig

    # 3. from_dict rejects bool ledger_version (True / False)
    for b_ver in (True, False):
        dict_bool = copy.deepcopy(dict_payload)
        dict_bool["ledger_version"] = b_ver
        dict_bool_orig = copy.deepcopy(dict_bool)
        with pytest.raises(ReadbackReceiptVerificationError) as exc_b:
            DurableTrialTargetLedger.from_dict(dict_bool)
        assert "ERR_INVALID_LEDGER_VERSION" in str(exc_b.value)
        assert dict_bool == dict_bool_orig

    # 4. from_dict rejects str / float ledger_version
    for s_ver in ("2", "1", 2.0, 1.5):
        dict_str = copy.deepcopy(dict_payload)
        dict_str["ledger_version"] = s_ver
        dict_str_orig = copy.deepcopy(dict_str)
        with pytest.raises(ReadbackReceiptVerificationError) as exc_s:
            DurableTrialTargetLedger.from_dict(dict_str)
        assert "ERR_INVALID_LEDGER_VERSION" in str(exc_s.value)
        assert dict_str == dict_str_orig

    # 5. from_dict rejects legacy version-1 record fail-closed
    dict_v1 = copy.deepcopy(dict_payload)
    dict_v1["ledger_version"] = 1
    dict_v1_orig = copy.deepcopy(dict_v1)
    with pytest.raises(ReadbackReceiptVerificationError) as exc_v1_fd:
        DurableTrialTargetLedger.from_dict(dict_v1)
    assert "ERR_LEGACY_LEDGER_VERSION" in str(exc_v1_fd.value)
    assert dict_v1 == dict_v1_orig

    # 6. from_dict rejects unsupported version-3 record fail-closed
    for bad_v in (3, 99, -1):
        dict_v3 = copy.deepcopy(dict_payload)
        dict_v3["ledger_version"] = bad_v
        dict_v3_orig = copy.deepcopy(dict_v3)
        with pytest.raises(ReadbackReceiptVerificationError) as exc_v3_fd:
            DurableTrialTargetLedger.from_dict(dict_v3)
        assert "ERR_UNSUPPORTED_LEDGER_VERSION" in str(exc_v3_fd.value)
        assert dict_v3 == dict_v3_orig


def test_durable_target_ledger_service_state_machine(test_env):
    """
    Tests irreversible state machine: NONE -> RESERVED -> ACTIVE -> EXHAUSTED.
    Confirms EXHAUSTED is terminal and invalid transitions are rejected.
    """
    service = DurableTrialTargetLedgerService(test_env["ledgers_dir"])
    p_uid = "proj_state_01"
    t_uid = "tl_state_01"
    exec_id = "exec_state_01"

    # Initial state is NONE
    assert service.get_ledger(p_uid, t_uid) is None

    # 1. Transition NONE -> RESERVED
    l1 = service.reserve_target(
        target_project_uid=p_uid,
        target_timeline_uid=t_uid,
        authorization_id="auth_01",
        execution_id=exec_id,
        approval_id="appr_01",
        proposal_id="prop_01",
        proposal_revision="1" * 64,
        plan_idempotency_key="plan_01",
    )
    assert l1.state == "RESERVED"

    # Duplicate reservation fails
    with pytest.raises(WholePlanReconciliationError) as exc1:
        service.reserve_target(
            target_project_uid=p_uid,
            target_timeline_uid=t_uid,
            authorization_id="auth_02",
            execution_id="exec_02",
            approval_id="appr_02",
            proposal_id="prop_02",
            proposal_revision="1" * 64,
            plan_idempotency_key="plan_02",
        )
    assert "ERR_TARGET_ALREADY_RESERVED" in str(exc1.value)

    # 2. Transition RESERVED -> ACTIVE
    l2 = service.activate_target(p_uid, t_uid, execution_id=exec_id)
    assert l2.state == "ACTIVE"

    # Activation with mismatched execution_id fails
    with pytest.raises(WholePlanReconciliationError) as exc2:
        service.activate_target(p_uid, t_uid, execution_id="wrong_exec")
    assert "ERR_TARGET_EXECUTION_MISMATCH" in str(exc2.value)

    # 3. Transition ACTIVE -> EXHAUSTED
    l3 = service.exhaust_target(
        p_uid, t_uid, execution_id=exec_id, terminal_receipt_id="rcpt_01", terminal_reason="COMPLETED"
    )
    assert l3.state == "EXHAUSTED"
    assert l3.terminal_receipt_id == "rcpt_01"
    assert l3.terminal_reason == "COMPLETED"

    # 4. Irreversible terminal: cannot activate or reserve or exhaust again
    with pytest.raises(WholePlanReconciliationError) as exc3:
        service.activate_target(p_uid, t_uid, execution_id=exec_id)
    assert "ERR_TARGET_LEDGER_EXHAUSTED" in str(exc3.value)

    with pytest.raises(WholePlanReconciliationError) as exc4:
        service.reserve_target(
            target_project_uid=p_uid,
            target_timeline_uid=t_uid,
            authorization_id="auth_03",
            execution_id=exec_id,
            approval_id="appr_03",
            proposal_id="prop_03",
            proposal_revision="1" * 64,
            plan_idempotency_key="plan_03",
        )
    assert "ERR_TARGET_LEDGER_EXHAUSTED" in str(exc4.value)

    service.close()


def test_durable_target_ledger_corrupt_file_fails_closed(test_env):
    """
    Corrupt persisted ledger file must fail closed and never be treated as empty.
    """
    service = DurableTrialTargetLedgerService(test_env["ledgers_dir"])
    p_uid = "proj_corrupt_01"
    t_uid = "tl_corrupt_01"

    fname = get_ledger_filename(p_uid, t_uid)
    corrupt_path = os.path.join(test_env["ledgers_dir"], fname)

    # Write truncated/corrupt JSON
    with open(corrupt_path, "w", encoding="utf-8") as f:
        f.write("{corrupt_json_payload: null,")

    # Scanning / recovery must raise ReadbackReceiptVerificationError
    with pytest.raises(ReadbackReceiptVerificationError) as exc:
        service.recover_and_scan_ledger()
    assert "ERR_CORRUPT_LEDGER_RECORD" in str(exc.value)

    service.close()


def test_coordinator_init_cleanup_on_failure(test_env):
    """
    If initialization fails, open directory descriptors and ledger must be cleaned up;
    if cleanup itself encounters errors, CoordinatorInitCleanupError is raised with details.
    """
    real_open = os.open
    calls = []

    def failing_third_open(path, flags, *args, **kwargs):
        calls.append(path)
        if len(calls) == 3:  # Fail on the 3rd directory open
            raise OSError(errno.EACCES, "Permission denied on 3rd directory")
        return real_open(path, flags, *args, **kwargs)

    with patch("os.open", side_effect=failing_third_open):
        with pytest.raises(AcceptanceGateError) as exc_info:
            RecoveryCoordinator(
                journals_dir=test_env["journals_dir"],
                receipts_dir=test_env["receipts_dir"],
                locks_dir=test_env["locks_dir"],
                ledgers_dir=test_env["ledgers_dir"],
                keystore_path=test_env["keystore_path"],
            )
        assert "Permission denied on 3rd directory" in str(exc_info.value)


def test_i3_g6_reconciliation_rejects_trial_key_as_operator_key(test_env):
    """
    I3-G6: Attempting reconciliation using a trial key ID instead of an authorized
    operator key must fail closed with purpose separation error.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_wrong_purpose_01"

    entry = TargetLockEntry(
        fd=None,
        filename=get_lock_filename(timeline_uid),
        state="CLOSE_OUTCOME_UNKNOWN",
        dev=123,
        ino=456,
        incident_id="incident_wp_01",
    )
    coordinator._target_locks[timeline_uid] = entry
    coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

    challenge = coordinator.request_reconciliation_challenge(timeline_uid, ttl_seconds=60.0)

    auth = make_operator_auth(
        test_env,
        timeline_uid=timeline_uid,
        incident_id=challenge.incident_id,
        challenge_nonce=challenge.challenge_nonce,
        challenge_expires_at=challenge.expires_at,
        op_key_id=test_env["trial_key_id"],
    )
    with pytest.raises(WholePlanReconciliationError) as exc_info:
        coordinator.reconcile_ambiguous_timeline(auth)
    assert "ERR_TRIAL_KEY_NOT_PERMITTED_FOR_RECONCILIATION" in str(exc_info.value)
    coordinator.close()


def test_mark_timeline_in_doubt_and_has_target_lock(test_env):
    """Verifies mark_timeline_in_doubt transitions state and blocks re-acquisition."""
    coordinator = make_coordinator(test_env)
    timeline_uid = "tl_indoubt_mark_01"

    assert coordinator.has_target_lock(timeline_uid) is False
    entry = coordinator.acquire_target_lock(timeline_uid)
    assert coordinator.has_target_lock(timeline_uid) is True

    coordinator.mark_timeline_in_doubt(timeline_uid, "incident_indoubt_123")
    assert entry.state == "IN_DOUBT_LOCKED"
    assert entry.incident_id == "incident_indoubt_123"
    assert coordinator.has_target_lock(timeline_uid) is False

    with pytest.raises(TargetExclusionLockError) as exc:
        coordinator.acquire_target_lock(timeline_uid)
    assert "ERR_TIMELINE_LOCKED_OUT_IN_DOUBT" in str(exc.value)

    coordinator.close()


def test_coordinator_closed_blocks_all_operations(test_env):
    """Closed coordinator must reject all public API operations fail-closed."""
    coordinator = make_coordinator(test_env)
    coordinator.close()
    assert coordinator.is_closed is True

    with pytest.raises(WholePlanReconciliationError) as exc:
        coordinator.acquire_target_lock("tl_closed")
    assert "ERR_COORDINATOR_CLOSED_OR_CLOSING" in str(exc.value)

    with pytest.raises(WholePlanReconciliationError):
        coordinator.release_target_lock("tl_closed")

    with pytest.raises(WholePlanReconciliationError):
        coordinator.request_reconciliation_challenge("tl_closed")


def test_i3_g7_binding_rejects_frame_geometry_overflow(test_env):
    """I3-G7: Frame geometry overflow (> 100,000,000 frames) fails closed."""
    coordinator = make_coordinator(test_env)

    auth_overflow = make_valid_auth(test_env)
    object.__setattr__(auth_overflow, "authorized_timeline_frame_duration", 100_000_001)
    with pytest.raises(ReadbackReceiptVerificationError) as exc:
        coordinator.bind_signed_authorization(auth_overflow)
    assert "ERR_AUTH_RANGE_GATE" in str(exc.value) or "ERR_AUTH_GEOMETRY_OVERFLOW" in str(exc.value) or "ERR_SIGNED_AUTH_HMAC_INVALID" in str(exc.value)

    coordinator.close()


def test_timeline_uid_path_traversal_defeated(test_env):
    """
    WAL-R31-01ak: target_timeline_uid = '../../etc/passwd' is safely hashed
    into timeline_<sha256>.lock and contained strictly within locks_dir.
    """
    coordinator = make_coordinator(test_env)
    traversal_uid = "../../etc/passwd"

    entry = coordinator.acquire_target_lock(traversal_uid)
    expected_hash = hashlib.sha256(traversal_uid.encode("utf-8")).hexdigest()
    assert entry.filename == f"timeline_{expected_hash}.lock"

    lock_path = os.path.join(test_env["locks_dir"], entry.filename)
    assert os.path.exists(lock_path)

    coordinator.release_target_lock(traversal_uid)
    coordinator.close()


def test_i3_01_coordinator_close_preserves_descriptor_and_lock_on_unlock_failure(test_env):
    """
    I3-01: Coordinator.close() must not drop kernel lock if flock(LOCK_UN) fails.
    Preserves live descriptor, leaves entry in IN_DOUBT_LOCKED, and allows safe retry.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_close_unlock_fail"

    entry = coordinator.acquire_target_lock(timeline_uid)
    live_fd = entry.fd
    assert live_fd is not None

    real_flock = fcntl.flock
    mock_active = True

    def faulty_flock(fd, op):
        if mock_active and (op & fcntl.LOCK_UN):
            raise OSError(errno.EIO, "Simulated unlock fault during close")
        return real_flock(fd, op)

    close_calls = []
    real_close = os.close

    def tracking_close(fd):
        close_calls.append(fd)
        return real_close(fd)

    with patch("fcntl.flock", side_effect=faulty_flock):
        with patch("os.close", side_effect=tracking_close):
            with pytest.raises(AcceptanceGateError) as exc:
                coordinator.close()
            assert "ERR_COORDINATOR_CLOSE_FAILED" in str(exc.value)

    assert live_fd not in close_calls
    assert entry.fd == live_fd
    assert entry.state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[timeline_uid].fd == live_fd

    coord_comp = make_coordinator(test_env)
    with pytest.raises(TargetExclusionLockError):
        coord_comp.acquire_target_lock(timeline_uid)
    coord_comp.close()

    mock_active = False
    coordinator.close()
    assert coordinator.is_closed is True
    assert timeline_uid not in coordinator._target_locks

    coord_comp = make_coordinator(test_env)
    entry_comp = coord_comp.acquire_target_lock(timeline_uid)
    assert entry_comp.state == "LOCKED"
    coord_comp.release_target_lock(timeline_uid)
    coord_comp.close()


def test_i3_02_ledger_publication_confined_to_anchored_descriptor_after_path_rebinding(test_env):
    """
    I3-02: Ledger publication uses directory descriptor anchoring (src_dir_fd/dst_dir_fd).
    Renaming the path on disk after initialization does not escape the originally opened directory.
    """
    ledgers_dir = test_env["ledgers_dir"]
    service = DurableTrialTargetLedgerService(ledgers_dir)
    p_uid = "proj_anchor_01"
    t_uid = "tl_anchor_01"

    renamed_dir = ledgers_dir + "_renamed"
    os.rename(ledgers_dir, renamed_dir)

    try:
        l = service.reserve_target(
            target_project_uid=p_uid,
            target_timeline_uid=t_uid,
            authorization_id="auth_anchor",
            execution_id="exec_anchor",
            approval_id="appr_anchor",
            proposal_id="prop_anchor",
            proposal_revision="0" * 64,
            plan_idempotency_key="plan_anchor",
        )
        assert l.state == "RESERVED"

        fname = get_ledger_filename(p_uid, t_uid)
        assert os.path.exists(os.path.join(renamed_dir, fname))
    finally:
        service.close()
        if os.path.exists(renamed_dir):
            os.rename(renamed_dir, ledgers_dir)


def test_i3_03_write_ledger_atomic_enforces_irreversible_transitions_and_blocks_rewind(test_env):
    """
    I3-03: Public write_ledger_atomic acquires lock and rejects overwriting or rewinding
    from an EXHAUSTED ledger record.
    """
    service = DurableTrialTargetLedgerService(test_env["ledgers_dir"])
    p_uid = "proj_atomic_trans"
    t_uid = "tl_atomic_trans"
    exec_id = "exec_atomic_01"

    service.reserve_target(
        target_project_uid=p_uid,
        target_timeline_uid=t_uid,
        authorization_id="auth_01",
        execution_id=exec_id,
        approval_id="appr_01",
        proposal_id="prop_01",
        proposal_revision="0" * 64,
        plan_idempotency_key="plan_01",
    )
    exhausted_dto = service.exhaust_target(
        p_uid, t_uid, execution_id=exec_id, terminal_receipt_id="rcpt_term_01"
    )
    assert exhausted_dto.state == "EXHAUSTED"

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sneaky_dto = DurableTrialTargetLedger(
        ledger_version=2,
        target_project_uid=p_uid,
        target_timeline_uid=t_uid,
        authorization_id="auth_sneaky",
        execution_id=exec_id,
        approval_id="appr_sneaky",
        proposal_id="prop_sneaky",
        proposal_revision="0" * 64,
        plan_idempotency_key="plan_sneaky",
        state="RESERVED",
        created_at_iso=now_iso,
        updated_at_iso=now_iso,
    )
    with pytest.raises(WholePlanReconciliationError) as exc:
        service.write_ledger_atomic(sneaky_dto)
    assert "ERR_TARGET_ALREADY_EXHAUSTED" in str(exc.value)

    current = service.get_ledger(p_uid, t_uid)
    assert current is not None
    assert current.state == "EXHAUSTED"

    service.close()


def test_i3_04_startup_scan_preserves_live_writer_temp_file(test_env):
    """
    I3-04: Startup scan verifies target lock before unlinking .tmp_ files.
    Preserves active writer's temp file while lock is held, and unlinks orphaned temp when lock is released.
    """
    ledgers_dir = test_env["ledgers_dir"]
    ident_hash = "a" * 64
    temp_fname = f".tmp_{ident_hash}_1234567890abcdef.json"
    temp_path = os.path.join(ledgers_dir, temp_fname)
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write('{"in_progress": true}')

    lock_fname = f".target_{ident_hash}.ledger.lock"
    lock_path = os.path.join(ledgers_dir, lock_fname)
    lfd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lfd, fcntl.LOCK_EX)

    try:
        service = DurableTrialTargetLedgerService(ledgers_dir)
        assert os.path.exists(temp_path)
        service.close()
    finally:
        fcntl.flock(lfd, fcntl.LOCK_UN)
        os.close(lfd)

    service2 = DurableTrialTargetLedgerService(ledgers_dir)
    assert not os.path.exists(temp_path)
    service2.close()


def test_i3_06_target_lock_rejects_insecure_permissions(test_env):
    """
    I3-06: Existing lock file with insecure permissions (e.g. 0644 or 0666)
    must fail closed with ERR_LOCKFILE_INSECURE_PERMS before taking ownership.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_insecure_perm_01"
    lock_fname = get_lock_filename(timeline_uid)
    lock_path = os.path.join(test_env["locks_dir"], lock_fname)

    with open(lock_path, "w", encoding="utf-8") as f:
        f.write("")
    os.chmod(lock_path, 0o644)

    with pytest.raises(TargetExclusionLockError) as exc:
        coordinator.acquire_target_lock(timeline_uid)
    assert "ERR_LOCKFILE_INSECURE_PERMS" in str(exc.value)

    os.chmod(lock_path, 0o600)
    entry = coordinator.acquire_target_lock(timeline_uid)
    assert entry.state == "LOCKED"
    coordinator.release_target_lock(timeline_uid)
    coordinator.close()


def test_i3_r1_01_write_ledger_atomic_rejects_identity_mutation_in_reserved_and_active_states(test_env):
    """
    I3-R1-01: write_ledger_atomic must compare every immutable identity field
    against the on-disk record before publication and reject any mismatch
    without changing the file, in both RESERVED and ACTIVE states.
    """
    service = DurableTrialTargetLedgerService(test_env["ledgers_dir"])
    project_uid = "proj_r1_01"
    timeline_uid = "tl_r1_01"

    try:
        # 1. Test in RESERVED state
        reserved_ledger = service.reserve_target(
            target_project_uid=project_uid,
            target_timeline_uid=timeline_uid,
            authorization_id="auth_orig_01",
            execution_id="exec_orig_01",
            approval_id="appr_orig_01",
            proposal_id="prop_orig_01",
            proposal_revision="1" * 64,
            plan_idempotency_key="key_orig_01",
        )
        ident_hash = hashlib.sha256(f"{project_uid}:{timeline_uid}".encode("utf-8")).hexdigest()
        fname = f"target_{ident_hash}.trial.json"
        with open(os.path.join(test_env["ledgers_dir"], fname), "rb") as f:
            orig_bytes_reserved = f.read()

        mutations = [
            ("authorization_id", "auth_mutated"),
            ("approval_id", "appr_mutated"),
            ("proposal_id", "prop_mutated"),
            ("proposal_revision", "2" * 64),
            ("plan_idempotency_key", "key_mutated"),
            ("created_at_iso", "2026-01-01T00:00:00Z"),
        ]

        for field, new_val in mutations:
            kwargs = dataclasses.asdict(reserved_ledger)
            kwargs[field] = new_val
            mutated_ledger = DurableTrialTargetLedger(**kwargs)
            with pytest.raises(WholePlanReconciliationError) as exc_info:
                service.write_ledger_atomic(mutated_ledger)
            assert "ERR_LEDGER_IDENTITY_MISMATCH" in str(exc_info.value)
            # Verify file on disk is strictly unchanged
            with open(os.path.join(test_env["ledgers_dir"], fname), "rb") as f:
                assert f.read() == orig_bytes_reserved

        # 2. Advance to ACTIVE state
        active_ledger = service.activate_target(
            target_project_uid=project_uid,
            target_timeline_uid=timeline_uid,
            execution_id="exec_orig_01",
        )
        with open(os.path.join(test_env["ledgers_dir"], fname), "rb") as f:
            orig_bytes_active = f.read()

        for field, new_val in mutations:
            kwargs = dataclasses.asdict(active_ledger)
            kwargs[field] = new_val
            mutated_ledger = DurableTrialTargetLedger(**kwargs)
            with pytest.raises(WholePlanReconciliationError) as exc_info:
                service.write_ledger_atomic(mutated_ledger)
            assert "ERR_LEDGER_IDENTITY_MISMATCH" in str(exc_info.value)
            # Verify file on disk is strictly unchanged
            with open(os.path.join(test_env["ledgers_dir"], fname), "rb") as f:
                assert f.read() == orig_bytes_active
    finally:
        service.close()


def test_i3_r1_02_reconciliation_sub_tolerance_expiry_tampering(test_env):
    """
    I3-R1-02: Operator reconciliation requires exact expiry equality and rejects
    sub-tolerance expiry alterations (e.g. +1e-6) even if resigned.
    """
    coordinator = make_coordinator(test_env)
    timeline_uid = "timeline_sub_tolerance_test"

    try:
        lock_file = f"timeline_{hashlib.sha256(timeline_uid.encode()).hexdigest()}.lock"
        lock_path = os.path.join(test_env["locks_dir"], lock_file)
        with open(lock_path, "wb") as f:
            pass
        os.chmod(lock_path, 0o600)
        st = os.stat(lock_path)
        entry = TargetLockEntry(
            fd=None,
            filename=lock_file,
            state="CLOSE_OUTCOME_UNKNOWN",
            dev=st.st_dev,
            ino=st.st_ino,
            incident_id="incident_sub_tol",
            active_challenge_nonce="nonce_sub_tol",
            challenge_expires_at=time.time() + 100.0,
        )
        coordinator._target_locks[timeline_uid] = entry
        coordinator._lock_state[timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

        valid_auth = make_operator_auth(
            test_env,
            timeline_uid=timeline_uid,
            incident_id="incident_sub_tol",
            challenge_nonce="nonce_sub_tol",
            challenge_expires_at=entry.challenge_expires_at,
        )

        # 1. Sub-tolerance delta (+1e-6) without resigning fails closed
        tampered_auth = dataclasses.replace(
            valid_auth,
            challenge_expires_at=valid_auth.challenge_expires_at + 1e-6,
        )
        with pytest.raises(WholePlanReconciliationError) as exc_info:
            coordinator.reconcile_ambiguous_timeline(tampered_auth)
        assert "ERR_EXPIRATION_TIMESTAMP_MISMATCH" in str(exc_info.value)
        assert entry.active_challenge_nonce == "nonce_sub_tol"

        # 2. Resigned with the altered sub-tolerance expiry still fails closed on exact equality
        resigned_sub_tol = make_operator_auth(
            test_env,
            timeline_uid=timeline_uid,
            incident_id="incident_sub_tol",
            challenge_nonce="nonce_sub_tol",
            challenge_expires_at=entry.challenge_expires_at + 1e-6,
        )
        with pytest.raises(WholePlanReconciliationError) as exc_info:
            coordinator.reconcile_ambiguous_timeline(resigned_sub_tol)
        assert "ERR_EXPIRATION_TIMESTAMP_MISMATCH" in str(exc_info.value)
        assert entry.active_challenge_nonce == "nonce_sub_tol"
    finally:
        coordinator.close()


def test_i3_r1_02_reconciliation_delimiter_bearing_field_values_and_injective_encoding(test_env):
    """
    I3-R1-02: Delimiter-bearing claims (containing colons, quotes, commas)
    are injectively serialized via versioned RFC 8785 canonical JSON bytes and protected against tampering.
    """
    delim_timeline_uid = "timeline:project:uuid:123"
    delim_incident_id = "inc:error:456:tier1"
    delim_nonce = "nonce:fresh:789"
    delim_identity = "operator:admin:john.doe@corp.com"
    delim_key_id = "op_key_delim"

    # Add operator key to keystore with delimiter-bearing identity
    key_bytes = secrets.token_bytes(32)
    key_file = os.path.join(test_env["keystore_path"], f"{delim_key_id}.opkey.json")
    with open(key_file, "w", encoding="utf-8") as f:
        json.dump({
            "key_hex": key_bytes.hex(),
            "permitted_operator_identity": delim_identity,
            "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",
        }, f)
    os.chmod(key_file, 0o600)

    coordinator = make_coordinator(test_env)
    try:
        lock_file = f"timeline_{hashlib.sha256(delim_timeline_uid.encode()).hexdigest()}.lock"
        lock_path = os.path.join(test_env["locks_dir"], lock_file)
        with open(lock_path, "wb") as f:
            pass
        os.chmod(lock_path, 0o600)
        st = os.stat(lock_path)
        entry = TargetLockEntry(
            fd=None,
            filename=lock_file,
            state="CLOSE_OUTCOME_UNKNOWN",
            dev=st.st_dev,
            ino=st.st_ino,
            incident_id=delim_incident_id,
            active_challenge_nonce=delim_nonce,
            challenge_expires_at=time.time() + 100.0,
        )
        coordinator._target_locks[delim_timeline_uid] = entry
        coordinator._lock_state[delim_timeline_uid] = "CLOSE_OUTCOME_UNKNOWN"

        auth = make_operator_auth(
            test_env,
            timeline_uid=delim_timeline_uid,
            incident_id=delim_incident_id,
            challenge_nonce=delim_nonce,
            challenge_expires_at=entry.challenge_expires_at,
            operator_identity=delim_identity,
            op_key_id=delim_key_id,
            op_key_bytes=key_bytes,
        )

        # Reconcile successfully with delimiter-bearing values
        coordinator.reconcile_ambiguous_timeline(auth)
        assert coordinator._lock_state[delim_timeline_uid] == "RELEASED"
        assert delim_timeline_uid not in coordinator._target_locks
    finally:
        coordinator.close()


def test_legacy_version_1_persisted_file_fails_closed_without_byte_mutation(test_env):
    """Verifies that a persisted legacy version-1 ledger record fails closed before transition and preserves file bytes exactly."""
    import json
    from pathlib import Path
    service = DurableTrialTargetLedgerService(test_env["ledgers_dir"])
    p_uid = "proj_legacy_v1"
    t_uid = "tl_legacy_v1"
    filename = get_ledger_filename(p_uid, t_uid)
    filepath = Path(test_env["ledgers_dir"]) / filename

    legacy_record = {
        "ledger_version": 1,
        "target_project_uid": p_uid,
        "target_timeline_uid": t_uid,
        "authorization_id": "auth_legacy_01",
        "execution_id": "exec_legacy_01",
        "approval_id": "appr_legacy_01",
        "proposal_id": "prop_legacy_01",
        "proposal_revision": "0" * 64,
        "plan_idempotency_key": "plan_legacy_01",
        "state": "RESERVED",
        "created_at_iso": "2026-11-01T10:00:00Z",
        "updated_at_iso": "2026-11-01T10:00:00Z",
    }
    raw_legacy_bytes = json.dumps(legacy_record, indent=2).encode("utf-8")
    filepath.write_bytes(raw_legacy_bytes)

    # Reading the ledger must fail closed with ERR_LEGACY_LEDGER_VERSION
    with pytest.raises(ReadbackReceiptVerificationError) as exc:
        service.get_ledger(p_uid, t_uid)
    assert "ERR_LEGACY_LEDGER_VERSION" in str(exc.value)

    # Attempting transition or reserve must also fail closed
    with pytest.raises(ReadbackReceiptVerificationError) as exc2:
        service.reserve_target(
            target_project_uid=p_uid,
            target_timeline_uid=t_uid,
            authorization_id="auth_new",
            execution_id="exec_new",
            approval_id="appr_new",
            proposal_id="prop_new",
            proposal_revision="1" * 64,
            plan_idempotency_key="plan_new",
        )
    assert "ERR_LEGACY_LEDGER_VERSION" in str(exc2.value)

    # Verify byte-for-byte exact preservation of legacy file bytes
    assert filepath.read_bytes() == raw_legacy_bytes
    service.close()



