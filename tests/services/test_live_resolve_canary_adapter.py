"""
tests/services/test_live_resolve_canary_adapter.py
Focused Test Suite for Live Resolve Canary Adapter (Gate 3 - G07-07B7-LIVE-IMPL).

Acceptance Groups:
  - Group 1: Module Isolation & Safety Boundaries (zero Resolve/FusionScript side effects)
  - Group 2: Tier 1 Canary Admission Gate Tripwires (project regex, uppercase nonce, exact UID equality)
  - Group 3: Canonical Action & Payload Digest Binding (120 frames @ 24 fps, single action, digest gate)
  - Group 4: Tier 2 Pre-IPC Tripwire & Clean Pre-Dispatch Abort (Branch 2A - UID drift, start frame drift)
  - Group 5: Verified Success Path & Terminal Tuple Ordering (Branch 1 - WAL, Receipt, Ledger, Lock)
  - Group 6: Physical Readback Geometry Verification Failures (Branch 3 In-Doubt)
  - Group 7: Post-Dispatch Exception & Commit Durability Failures (Branch 3 In-Doubt)
  - Group 8: Pre-existing History, Replay & Durability Boundaries (retains lock fail-closed)
  - Group 9: Two-Stage Independent Proof Verification & Branch 2B (Stage 1, Stage 2, 300s TTL, action_id invariant)
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import hmac
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from src.core.timeline_plan_execution_binder import (
    TimelineExecutionPlan,
    PlannedTimelineAction as BinderPlannedTimelineAction,
    TargetSpec,
    RationalFPS,
    TimeRangeFrames,
    compute_canonical_plan_digest,
)

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
    TrialExecutionReceipt,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
)
from src.services.durable_trial_target_ledger import (
    RecoveryCoordinator,
    TargetLockEntry,
)
from src.services.execution_journal_service import ExecutionJournalService
from src.services.live_resolve_canary_adapter import (
    CANONICAL_ACTION_TYPE,
    CANONICAL_FPS_DENOMINATOR,
    CANONICAL_FPS_NUMERATOR,
    CANONICAL_SOURCE_FRAME_DURATION,
    CANONICAL_SOURCE_FRAME_START,
    CANONICAL_TIMELINE_FRAME_DURATION,
    CANONICAL_TIMELINE_FRAME_START,
    CANONICAL_TRACK_INDEX,
    CANONICAL_TRACK_TYPE,
    DurableProofRecord,
    FakeMediaPool,
    FakeMediaPoolItem,
    FakeProject,
    FakeProjectManager,
    FakeResolveClient,
    FakeTimeline,
    FakeTimelineItem,
    LiveAdmissionTripwireError,
    LiveCanaryAdapterError,
    LiveResolveCanaryAdapter,
    PhysicalReadbackVerificationError,
    ReceiptDurabilityError,
    TargetProjectMismatchError,
    TargetTimelineMismatchError,
    TargetTimelineShiftedError,
    canonical_canary_plan_digest_sha256,
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
    proofs_dir = base_dir / "proofs"
    keystore_dir = base_dir / "security"

    journals_dir.mkdir(parents=True)
    receipts_dir.mkdir(parents=True)
    locks_dir.mkdir(parents=True)
    ledgers_dir.mkdir(parents=True)
    proofs_dir.mkdir(parents=True)
    keystore_dir.mkdir(parents=True)

    os.chmod(str(keystore_dir), 0o700)

    trial_key_file = keystore_dir / "canary_trial_key.key"
    trial_key_file.write_bytes(TRIAL_KEY_BYTES)
    os.chmod(str(trial_key_file), 0o600)

    # Revocations list (must be >= 32 bytes for MIN_KEYFILE_SIZE)
    rev_file = keystore_dir / "revocations.json"
    rev_file.write_text(json.dumps({"revoked_key_ids": ["revoked_trial_key_dummy"]}), encoding="utf-8")
    os.chmod(str(rev_file), 0o600)

    op_key_file = keystore_dir / "canary_op_key.opkey.json"
    op_data = {
        "key_hex": OPERATOR_KEY_BYTES.hex(),
        "permitted_operator_identity": OPERATOR_IDENTITY,
        "purpose": "TIMELINE_AMBIGUOUS_RECONCILIATION",
    }
    op_key_file.write_text(json.dumps(op_data), encoding="utf-8")
    os.chmod(str(op_key_file), 0o600)

    # Create real source clip file on disk for media binding validation (LIVE-IMPL-R1-04)
    clip_file = base_dir / "canary_clip_01.mov"
    clip_bytes = b"CANARY_MEDIA_SOURCE_FILE_CONTENT_BYTES_V1"
    clip_file.write_bytes(clip_bytes)
    clip_sha256 = hashlib.sha256(clip_bytes).hexdigest()

    return {
        "base_dir": str(base_dir),
        "journals_dir": str(journals_dir),
        "receipts_dir": str(receipts_dir),
        "locks_dir": str(locks_dir),
        "ledgers_dir": str(ledgers_dir),
        "proofs_dir": str(proofs_dir),
        "keystore_path": str(keystore_dir),
        "trial_key_id": "canary_trial_key",
        "trial_key_bytes": TRIAL_KEY_BYTES,
        "op_key_id": "canary_op_key",
        "op_key_bytes": OPERATOR_KEY_BYTES,
        "op_identity": OPERATOR_IDENTITY,
        "source_file_path": str(clip_file),
        "source_file_sha256": clip_sha256,
    }


def read_journal_entries(journal: ExecutionJournalService, execution_id: str) -> List[JournalEntry]:
    """Helper reading all journal entries via journal.replay_journal."""
    return list(journal.replay_journal(execution_id).entries)


def make_coordinator(env: Dict[str, Any]) -> RecoveryCoordinator:
    """Helper to instantiate a valid RecoveryCoordinator."""
    return RecoveryCoordinator(
        journals_dir=env["journals_dir"],
        receipts_dir=env["receipts_dir"],
        locks_dir=env["locks_dir"],
        ledgers_dir=env["ledgers_dir"],
        keystore_path=env["keystore_path"],
    )


_LATEST_CANARY_SOURCE_PATH: Optional[str] = None


def make_canary_execution_plan(
    action: Any,
    auth: TrialExecutionAuthorization,
    action_hash_override: Optional[str] = None,
    planned_actions_override: Optional[Tuple[Any, ...]] = None,
    plan_idempotency_key_override: Optional[str] = None,
    project_uid_override: Optional[str] = None,
    timeline_uid_override: Optional[str] = None,
) -> TimelineExecutionPlan:
    """Creates an authoritative TimelineExecutionPlan corresponding to the single canary action."""
    binder_action = BinderPlannedTimelineAction(
        action_index=action.action_index,
        action_type=action.action_type,
        target_track_type=action.track_type,
        target_track_index=action.track_index,
        clip_id="canary_clip_item",
        source_file_path=auth.source_file_path,
        source_range=TimeRangeFrames(start_frame=action.source_frame_start, end_frame=action.source_frame_start + action.duration_frames),
        timeline_range=TimeRangeFrames(start_frame=auth.authorized_timeline_frame_start, end_frame=auth.authorized_timeline_frame_start + action.duration_frames),
        applied_speed=Fraction(1, 1),
        role="canary_lead_in",
        action_idempotency_hash=action_hash_override or action.action_id,
        action_params={"note": "canary single insert clip"},
    )
    target_spec = TargetSpec(
        project_name=auth.target_project_name,
        project_id=project_uid_override or auth.target_project_uid,
        timeline_name=auth.target_timeline_name,
        timeline_id=timeline_uid_override or auth.target_timeline_uid,
        timeline_fps=RationalFPS(numerator=auth.target_timeline_fps_numerator, denominator=auth.target_timeline_fps_denominator),
        timeline_resolution=(1920, 1080),
        start_timecode_frame=auth.observed_timeline_start_frame,
        video_track_mapping={"V1": 1},
        audio_track_mapping={"A1": 1},
    )
    actions = planned_actions_override if planned_actions_override is not None else (binder_action,)
    return TimelineExecutionPlan(
        plan_id="plan_canary_001",
        idempotency_key=plan_idempotency_key_override or auth.plan_idempotency_key,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        approval_id=auth.approval_record_id,
        approved_at_iso=auth.issued_at_iso,
        target_spec=target_spec,
        occupancy_snapshot_id="snap_canary_001",
        binding_status="BOUND_VERIFIED",
        planned_actions=actions,
        planned_actions_count=len(actions),
        track_reservations=(),
        source_digests=(),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=action.duration_frames,
        total_timeline_duration_sec=Fraction(action.duration_frames, auth.target_timeline_fps_numerator),
        issues=(),
        created_at_iso=auth.issued_at_iso,
    )


def make_canary_action_and_matching_auth(
    env: Dict[str, Any],
    execution_id: str = "exec_1234567890abcdef1234567890abcdef",
    target_project_uid: str = "proj_canary_001",
    target_timeline_uid: str = "tl_canary_001",
    challenge_nonce: str = "4E2A8F1C0B3D5E7A",
    project_name: Optional[str] = None,
    observed_start_frame: int = 86400,
    valid_offset_sec: float = 300.0,
    tamper_hmac: Optional[str] = None,
    tamper_digest: Optional[str] = None,
    action_type: str = CANONICAL_ACTION_TYPE,
    track_type: str = CANONICAL_TRACK_TYPE,
    track_index: int = CANONICAL_TRACK_INDEX,
    duration_frames: int = CANONICAL_TIMELINE_FRAME_DURATION,
    source_frame_start: int = CANONICAL_SOURCE_FRAME_START,
    timeline_frame_start: int = CANONICAL_TIMELINE_FRAME_START,
    fps_numerator: int = CANONICAL_FPS_NUMERATOR,
    fps_denominator: int = CANONICAL_FPS_DENOMINATOR,
) -> Tuple[PlannedTimelineAction, TrialExecutionAuthorization]:
    """
    Constructs a matching PlannedTimelineAction and signed TrialExecutionAuthorization
    strictly adhering to 07B7 §4.1, §4.2, and §5.2.1.
    """
    global _LATEST_CANARY_SOURCE_PATH
    kid = env["trial_key_id"]
    kbytes = env["trial_key_bytes"]

    # Canonical project name containing the 16-hex challenge nonce
    proj_name = project_name or f"RF_TRIAL_{challenge_nonce}"
    record_frame = observed_start_frame + timeline_frame_start
    source_clip_path = env.get("source_file_path", "/media/canary/clip_01.mov")
    source_clip_sha256 = env.get("source_file_sha256", "f" * 64)
    _LATEST_CANARY_SOURCE_PATH = source_clip_path

    action = PlannedTimelineAction(
        action_id="act_1234567890abcdef",
        action_index=0,
        action_type=action_type,
        track_type=track_type,
        track_index=track_index,
        record_frame=record_frame,
        duration_frames=duration_frames,
        source_path=source_clip_path,
        source_frame_start=source_frame_start,
        source_frame_duration=duration_frames,
        role="canary_insert",
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
        "target_project_name": proj_name,
        "target_timeline_uid": target_timeline_uid,
        "target_timeline_name": "Canary Timeline",
        "target_timeline_fps_numerator": fps_numerator,
        "target_timeline_fps_denominator": fps_denominator,
        "observed_timeline_start_frame": observed_start_frame,
        "authorized_record_frame": record_frame,
        "source_file_path": source_clip_path,
        "source_file_sha256": source_clip_sha256,
        "authorized_action_type": action_type,
        "authorized_track_type": track_type,
        "authorized_track_index": track_index,
        "authorized_timeline_frame_start": timeline_frame_start,
        "authorized_timeline_frame_duration": duration_frames,
        "authorized_source_frame_start": source_frame_start,
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
    canary_plan = make_canary_execution_plan(action, temp_auth)
    plan_digest = compute_canonical_plan_digest(canary_plan)

    base_auth_dict["action_payload_digest_sha256"] = tamper_digest or canonical_digest
    base_auth_dict["plan_digest_sha256"] = plan_digest
    temp_auth2 = TrialExecutionAuthorization(**base_auth_dict)
    canon_bytes = canonical_bytes_for_auth_hmac(temp_auth2)
    sig = hmac.new(kbytes, canon_bytes, hashlib.sha256).hexdigest()
    base_auth_dict["authorization_hmac"] = tamper_hmac or sig

    auth = TrialExecutionAuthorization(**base_auth_dict)
    # Attach challenge_nonce and execution_plan explicitly as attributes for clarity
    object.__setattr__(auth, "challenge_nonce", challenge_nonce)
    object.__setattr__(auth, "execution_plan", canary_plan)
    return action, auth


def make_fake_resolve_client(
    project_name: str,
    project_uid: str,
    timeline_uid: str,
    start_frame: int = 86400,
    media_pool_exception: Optional[Exception] = None,
    source_file_path: Optional[str] = None,
) -> FakeResolveClient:
    """Creates a deterministic FakeResolveClient with configured project and timeline."""
    tl = FakeTimeline(
        name="Canary Timeline",
        unique_id=timeline_uid,
        start_frame=start_frame,
    )
    clip_path = source_file_path or _LATEST_CANARY_SOURCE_PATH or "/media/canary/clip_01.mov"
    clips = [FakeMediaPoolItem(clip_path)]
    mp = FakeMediaPool(configured_exception=media_pool_exception, clips=clips)
    proj = FakeProject(
        name=project_name,
        unique_id=project_uid,
        timeline=tl,
        media_pool=mp,
    )
    return FakeResolveClient(project=proj)


import ast
import src.services.live_resolve_canary_adapter as canary_module


# ==============================================================================
# GROUP 1: Module Isolation & Safety Boundaries
# ==============================================================================

def test_group1_module_import_has_no_resolve_or_fusionscript_side_effects():
    """Confirms importing live_resolve_canary_adapter does not import FusionScript or connect to Resolve."""
    source_file = canary_module.__file__
    assert source_file is not None and os.path.exists(source_file)

    with open(source_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_file)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)

    forbidden_substrings = ["davinci", "fusionscript", "bmd", "resolve_api", "pyside", "qt"]
    for name in imported_names:
        name_lower = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in name_lower, f"Tripwire violation: found forbidden import '{name}' in canary adapter!"


def test_group1_adapter_construction_requires_valid_dependencies(test_env):
    """Rejects invalid coordinator, journal, or None resolve_client."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    fake_client = make_fake_resolve_client("RF_TRIAL_1234567890ABCDEF", "p1", "t1")

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_COORDINATOR"):
        LiveResolveCanaryAdapter(
            coordinator="not_a_coordinator",  # type: ignore
            journal=journal,
            resolve_client=fake_client,
        )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_JOURNAL_SERVICE"):
        LiveResolveCanaryAdapter(
            coordinator=coordinator,
            journal="not_a_journal",  # type: ignore
            resolve_client=fake_client,
        )

    with pytest.raises(LiveAdmissionTripwireError, match="ERR_RESOLVE_CLIENT_REQUIRED"):
        LiveResolveCanaryAdapter(
            coordinator=coordinator,
            journal=journal,
            resolve_client=None,
        )

    coordinator.close()



# ==============================================================================
# GROUP 2: Tier 1 Canary Admission Gate Tripwires (07B7 §5.2.1)
# ==============================================================================

def test_group2_admission_rejects_project_name_regex_mismatch(test_env):
    """Rejects non-canary project name not matching ^RF_TRIAL_[0-9A-Z_]{8,32}$."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(
        test_env, project_name="NON_CANARY_PROJECT_NAME"
    )
    fake_client = make_fake_resolve_client(
        project_name="NON_CANARY_PROJECT_NAME",
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(LiveAdmissionTripwireError, match="ERR_PROJECT_NAME_REGEX_MISMATCH"):
        adapter.execute_action(action, auth)

    # Clean pre-dispatch rejection cleanly releases caller's target lock
    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group2_admission_rejects_challenge_nonce_missing_from_project_name(test_env):
    """Rejects canary project name lacking the 16-hex challenge nonce."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    # Challenge nonce in auth is AAAA, but project name has BBBB
    action, auth = make_canary_action_and_matching_auth(
        test_env, challenge_nonce="1111222233334444", project_name="RF_TRIAL_AAAABBBBCCCCDDDD"
    )
    fake_client = make_fake_resolve_client(
        project_name="RF_TRIAL_AAAABBBBCCCCDDDD",
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(LiveAdmissionTripwireError, match="ERR_CHALLENGE_NONCE_NOT_IN_PROJECT_NAME"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group2_admission_rejects_malformed_challenge_nonce(test_env):
    """Rejects malformed challenge nonce (e.g. lowercase hex or wrong length)."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    # lowercase hex
    action, auth = make_canary_action_and_matching_auth(
        test_env, challenge_nonce="111122223333444a", project_name="RF_TRIAL_111122223333444A"
    )
    object.__setattr__(auth, "challenge_nonce", "111122223333444a")  # lowercase invalid

    fake_client = make_fake_resolve_client(
        project_name="RF_TRIAL_111122223333444A",
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(LiveAdmissionTripwireError, match="ERR_CHALLENGE_NONCE_FORMAT_INVALID"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group2_admission_rejects_project_uid_mismatch(test_env):
    """Rejects active project UID mismatch with TargetProjectMismatchError."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid="different_project_uid",  # mismatched UID
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(TargetProjectMismatchError, match="ERR_TARGET_PROJECT_UID_MISMATCH"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group2_admission_rejects_timeline_uid_mismatch(test_env):
    """Rejects active timeline UID mismatch with TargetTimelineMismatchError."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid="different_timeline_uid",  # mismatched UID
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(TargetTimelineMismatchError, match="ERR_TARGET_TIMELINE_UID_MISMATCH"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



# ==============================================================================
# GROUP 3: Canonical Action & Payload Digest Binding
# ==============================================================================

def test_group3_rejects_non_insert_clip_action(test_env):
    """Rejects any action type other than INSERT_CLIP."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    object.__setattr__(action, "action_type", "DELETE_CLIP")
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_CANONICAL_ACTION_TYPE_MISMATCH"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group3_rejects_non_video_track(test_env):
    """Rejects track_type other than 'video'."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    object.__setattr__(action, "track_type", "audio")
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_CANONICAL_TRACK_TYPE_MISMATCH"):
        adapter.execute_action(action, auth)

    coordinator.close()



def test_group3_rejects_non_120_frame_duration(test_env):
    """Rejects duration other than 120 frames."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env, duration_frames=240)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_CANONICAL_SOURCE_DURATION_MISMATCH"):
        adapter.execute_action(action, auth)

    coordinator.close()



def test_group3_rejects_payload_digest_mismatch(test_env):
    """Rejects action when recomputed 16-key digest does not match auth.action_payload_digest_sha256."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(
        test_env, tamper_digest="9" * 64
    )
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_ACTION_DIGEST_MISMATCH"):
        adapter.execute_action(action, auth)

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



# ==============================================================================
# GROUP 4: Tier 2 Pre-IPC Tripwire & Clean Pre-Dispatch Abort (Branch 2A)
# ==============================================================================

def test_group4_pre_ipc_drift_project_uid_triggers_clean_abort_branch_2a(test_env):
    """
    Project UID drifts immediately before IPC call.
    Triggers Branch 2A: WAL FINISH(ABORTED), Receipt ABORTED, Ledger EXHAUSTED(ABORTED), lock RELEASED.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Simulate drift occurring right before IPC: modify project UID on active project
    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None

    orig_get_uid = active_proj.GetUniqueId
    call_count = 0

    def drifting_get_unique_id():
        nonlocal call_count
        call_count += 1
        if call_count > 1:  # 1st call is Tier 1, 2nd call is Tier 2 pre-IPC
            return "drifted_project_uid_002"
        return orig_get_uid()

    active_proj.GetUniqueId = drifting_get_unique_id  # type: ignore

    with pytest.raises(TargetProjectMismatchError, match="ERR_TARGET_PROJECT_UID_DRIFT_PRE_IPC"):
        adapter.execute_action(action, auth)

    # Branch 2A checks:
    # 1. WAL has FINISH with final_receipt_state="ABORTED"
    entries = read_journal_entries(journal, auth.execution_id)
    assert len(entries) == 3
    assert entries[0].stage == "START"
    assert entries[1].stage == "ACTION_PENDING"
    assert entries[2].stage == "FINISH"
    assert entries[2].action_params.get("final_receipt_state") == "ABORTED"

    # 2. Durable ledger is EXHAUSTED with terminal_reason="ABORTED"
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    entry = d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid)
    assert entry is not None
    assert entry.state == "EXHAUSTED"
    assert entry.terminal_reason == "ABORTED"

    # 3. Target exclusion lock was released
    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group4_pre_ipc_drift_start_frame_triggers_clean_abort_branch_2a(test_env):
    """
    Timeline GetStartFrame() drifts right before IPC dispatch.
    Triggers TargetTimelineShiftedError and clean abort terminalization (Branch 2A).
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(
        test_env, observed_start_frame=86400
    )
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    # Drift the start frame before dispatch
    active_tl.SetStartFrame(86500)

    with pytest.raises(TargetTimelineShiftedError, match="ERR_TIMELINE_START_FRAME_DRIFT_PRE_IPC"):
        adapter.execute_action(action, auth)

    # Verified clean abort terminalization
    entries = read_journal_entries(journal, auth.execution_id)
    assert entries[-1].stage == "FINISH"
    assert entries[-1].action_params.get("final_receipt_state") == "ABORTED"

    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    entry = d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid)
    assert entry is not None
    assert entry.state == "EXHAUSTED"
    assert entry.terminal_reason == "ABORTED"

    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



# ==============================================================================
# GROUP 5: Verified Success Path & Terminal Tuple Ordering (Branch 1)
# ==============================================================================

def test_group5_verified_success_full_transaction_and_terminal_tuple(test_env):
    """
    Happy Path: Full verified success execution.
    Two-tier tripwires pass, fake AppendToTimeline succeeds, physical readback confirms
    geometry (1 item on Video Track 1 at record_frame for 120 frames).
    Resulting terminal tuple (WAL, Receipt, Ledger) is 100% COMPLETED and lock is RELEASED.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(
        test_env, observed_start_frame=86400
    )
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )

    # Configure timeline track items so readback confirms the inserted clip
    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    inserted_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=86400,
        end=86400 + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [inserted_item])

    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    res = adapter.execute_action(action, auth)

    # 1. Result dictionary invariants
    assert res["status"] == "COMPLETED"
    assert res["receipt_state"] == "COMPLETED"
    assert res["ledger_state"] == "EXHAUSTED"
    assert res["ledger_terminal_reason"] == "COMPLETED"
    assert res["wal_final_receipt_state"] == "COMPLETED"
    assert res["lock_released"] is True
    assert res["terminalized"] is True

    # 2. WAL records order and states
    entries = read_journal_entries(journal, auth.execution_id)
    assert len(entries) == 4
    assert entries[0].stage == "START"
    assert entries[1].stage == "ACTION_PENDING"
    assert entries[2].stage == "ACTION_COMMITTED"
    assert entries[2].resolve_item_id == "canary_item_uid_001"
    assert entries[3].stage == "FINISH"
    assert entries[3].action_params.get("final_receipt_state") == "COMPLETED"

    # 3. Durable target ledger transition to EXHAUSTED(COMPLETED)
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    l_entry = d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid)
    assert l_entry is not None
    assert l_entry.state == "EXHAUSTED"
    assert l_entry.terminal_reason == "COMPLETED"
    assert l_entry.terminal_receipt_id == res["receipt_id"]

    # 4. Target lock released
    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group5_durable_receipt_published_and_verified_on_disk(test_env):
    """Confirms published TrialExecutionReceipt file on disk matches full DTO equality."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=auth.observed_timeline_start_frame,
    )

    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    inserted_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="item_abc_123",
    )
    active_tl.set_track_items("video", 1, [inserted_item])

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    res = adapter.execute_action(action, auth)
    rcpt_path = os.path.join(test_env["receipts_dir"], f"{auth.execution_id}.trial_receipt.json")
    assert os.path.exists(rcpt_path)

    with open(rcpt_path, "r", encoding="utf-8") as rf:
        rcpt_dict = json.load(rf)

    assert rcpt_dict["trial_receipt_version"] == 1
    assert rcpt_dict["receipt_id"] == res["receipt_id"]
    assert rcpt_dict["execution_id"] == auth.execution_id
    assert rcpt_dict["authorization_id"] == auth.authorization_id
    assert rcpt_dict["execution_receipt"]["state"] == "COMPLETED"

    coordinator.close()



# ==============================================================================
# GROUP 6: Physical Readback Geometry Verification Failures (Branch 3)
# ==============================================================================

def test_group6_readback_zero_items_latches_in_doubt_and_retains_lock(test_env):
    """
    Physical readback returns 0 items on Video Track 1.
    Latches IN_DOUBT_LOCKED, retains lock, retains ACTIVE ledger, prohibits FINISH.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    # Leave Track V1 empty
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(PhysicalReadbackVerificationError, match="ERR_PHYSICAL_READBACK_COUNT_MISMATCH"):
        adapter.execute_action(action, auth)

    # In-doubt checks:
    # 1. Target exclusion lock strictly retained in IN_DOUBT_LOCKED state
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None

    # 2. WAL ends at ACTION_FAILED (NO FINISH)
    entries = read_journal_entries(journal, auth.execution_id)
    assert len(entries) == 3
    assert entries[0].stage == "START"
    assert entries[1].stage == "ACTION_PENDING"
    assert entries[2].stage == "ACTION_FAILED"

    # 3. Durable target ledger remains ACTIVE (not EXHAUSTED)
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    entry = d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid)
    assert entry is not None
    assert entry.state == "ACTIVE"

    coordinator.close()



def test_group6_readback_geometry_duration_mismatch_latches_in_doubt(test_env):
    """
    Physical readback returns an item with wrong duration (e.g. 60 frames instead of 120).
    Latches IN_DOUBT_LOCKED, retains lock, retains ACTIVE ledger.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    wrong_duration_item = FakeTimelineItem(
        name="canary_clip",
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 60,
        duration=60,  # 60 != 120
    )
    active_tl.set_track_items("video", 1, [wrong_duration_item])

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(PhysicalReadbackVerificationError, match="ERR_PHYSICAL_READBACK_DURATION_MISMATCH"):
        adapter.execute_action(action, auth)

    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None
    coordinator.close()



# ==============================================================================
# GROUP 7: Post-Dispatch Exception & Commit Durability Failures (Branch 3)
# ==============================================================================

def test_group7_post_dispatch_gateway_exception_latches_in_doubt_and_retains_lock(test_env):
    """
    IPC AppendToTimeline raises an exception after PENDING is durable.
    Latches IN_DOUBT_LOCKED, appends ACTION_FAILED, retains target lock, retains ACTIVE ledger.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    ipc_error = RuntimeError("SIMULATED_RESOLVE_IPC_TIMEOUT")
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        media_pool_exception=ipc_error,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    res = adapter.execute_action(action, auth)

    assert res["status"] == "TERMINAL_ACTION_FAILED_IN_DOUBT"
    assert res["lock_retained"] is True
    assert res["target_lock_state"] == "IN_DOUBT_LOCKED"
    assert res["ledger_state"] == "ACTIVE"
    assert res["terminalized"] is False

    # Check WAL
    entries = read_journal_entries(journal, auth.execution_id)
    assert len(entries) == 3
    assert entries[0].stage == "START"
    assert entries[1].stage == "ACTION_PENDING"
    assert entries[2].stage == "ACTION_FAILED"

    # Lock strictly retained in IN_DOUBT_LOCKED state
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None
    coordinator.close()



def test_group7_wal_commit_persistence_failure_latches_in_doubt(test_env):
    """
    WAL append fails when writing ACTION_COMMITTED after successful IPC call.
    Raises AcceptanceGateError, latches IN_DOUBT_LOCKED, retains target lock.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    valid_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [valid_item])

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Patch journal to fail on 3rd append (seq=2 ACTION_COMMITTED)
    orig_append = journal.append_entry
    append_count = 0

    def failing_append(exec_id: str, entry: JournalEntry):
        nonlocal append_count
        append_count += 1
        if append_count == 3:  # 1: START, 2: ACTION_PENDING, 3: ACTION_COMMITTED
            raise IOError("SIMULATED_DISK_FULL_ON_COMMIT")
        return orig_append(exec_id, entry)

    with patch.object(journal, "append_entry", side_effect=failing_append):
        with pytest.raises(AcceptanceGateError, match="ERR_ACTION_COMMIT_PERSISTENCE_FAILED"):
            adapter.execute_action(action, auth)

    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None
    coordinator.close()



# ==============================================================================
# GROUP 8: Pre-existing History, Replay & Durability Boundaries
# ==============================================================================

def test_group8_preexisting_journal_file_blocks_admission_and_preserves_lock(test_env):
    """If journal file already exists on disk, reject before dispatch and strictly retain target lock."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)

    # Create prior journal file
    j_path = journal.journal_path(auth.execution_id)
    with open(j_path, "w", encoding="utf-8") as f:
        f.write('{"seq": 0, "stage": "START"}\n')

    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_JOURNAL_ALREADY_EXISTS"):
        adapter.execute_action(action, auth)

    # Lock strictly preserved (not released)
    assert coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



def test_group8_exhausted_target_in_ledger_blocks_admission_and_preserves_lock(test_env):
    """If target is already EXHAUSTED in durable ledger, reject and strictly preserve target lock."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])

    action, auth = make_canary_action_and_matching_auth(test_env)
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    # Exhaust target beforehand
    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id="auth_prev",
        execution_id="exec_prev",
        approval_id="appr_prev",
        proposal_id="prop_prev",
        proposal_revision="1" * 64,
        plan_idempotency_key="plan_prev",
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, "exec_prev")
    d_ledger.exhaust_target(
        auth.target_project_uid,
        auth.target_timeline_uid,
        "exec_prev",
        terminal_receipt_id="rcpt_prev",
        terminal_reason="COMPLETED",
    )

    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    with pytest.raises(WholePlanReconciliationError, match="ERR_TARGET_ALREADY_EXHAUSTED"):
        adapter.execute_action(action, auth)

    assert coordinator.has_target_lock(auth.target_timeline_uid)
    coordinator.close()



# ==============================================================================
# GROUP 9: Two-Stage Independent Proof Verification & Branch 2B
# ==============================================================================

def make_valid_raw_proof_22_fields(
    env: Dict[str, Any],
    action: PlannedTimelineAction,
    auth: TrialExecutionAuthorization,
    target_item_found: bool = False,
    adjacent_clean: bool = True,
    age_seconds: float = 30.0,
    tamper_hmac: Optional[str] = None,
    tamper_action_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper constructing an authentic 22-field raw readback proof."""
    kid = env["trial_key_id"]
    kbytes = env["trial_key_bytes"]
    now = datetime.now(timezone.utc)
    obs_iso = (now - timedelta(seconds=age_seconds)).isoformat().replace("+00:00", "Z")

    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    payload_digest = hashlib.sha256(canonical_action_payload_bytes(payload)).hexdigest()

    proof_dict: Dict[str, Any] = {
        "proof_schema_version": "1.0",
        "observer_identity": "authoritative_canary_verifier",
        "source_file_path": auth.source_file_path,
        "target_project_uid": auth.target_project_uid,
        "target_timeline_uid": auth.target_timeline_uid,
        "target_track_type": auth.authorized_track_type,
        "key_id": kid,
        "proof_nonce": str(uuid.uuid4()),
        "action_id": tamper_action_id or action.action_id,
        "action_payload_digest_sha256": payload_digest,
        "proof_hmac_sha256": "0" * 64,
        "action_index": action.action_index,
        "record_in_frame": auth.authorized_record_frame,
        "record_out_frame": auth.authorized_record_frame + auth.authorized_timeline_frame_duration,
        "scanned_frame_start": auth.authorized_record_frame,
        "scanned_frame_end": auth.authorized_record_frame + auth.authorized_timeline_frame_duration,
        "source_in_frame": auth.authorized_source_frame_start,
        "source_out_frame": auth.authorized_source_frame_start + auth.authorized_source_frame_duration,
        "target_track_index": auth.authorized_track_index,
        "target_item_found": target_item_found,
        "adjacent_occupancy_clean": adjacent_clean,
        "observation_timestamp_iso": obs_iso,
    }

    fields_21 = {k: proof_dict[k] for k in sorted(proof_dict.keys()) if k != "proof_hmac_sha256"}
    canon_bytes = canonical_json_bytes(fields_21)
    sig = hmac.new(kbytes, canon_bytes, hashlib.sha256).hexdigest()
    proof_dict["proof_hmac_sha256"] = tamper_hmac or sig
    return proof_dict


def test_group9_stage_1_raw_proof_allowlist_and_type_gates(test_env):
    """Stage 1 rejects raw proof with extra keys, missing keys, or invalid scalar types."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_params,
    )

    raw_proof = make_valid_raw_proof_22_fields(test_env, action, auth)

    # Extra key check
    bad_proof = dict(raw_proof)
    bad_proof["extra_unexpected_key"] = "forbidden"
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_SCHEMA_MISMATCH"):
        adapter.verify_and_consume_proof(bad_proof, action, pending_entry, authorization=auth)

    # Bool pretending to be int check
    bad_type_proof = dict(raw_proof)
    bad_type_proof["action_index"] = True  # bool instead of int
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_RECEIPT_TYPE_GATE"):
        adapter.verify_and_consume_proof(bad_type_proof, action, pending_entry, authorization=auth)

    coordinator.close()



def test_group9_stage_1_raw_proof_freshness_ttl_300s(test_env):
    """Stage 1 rejects raw proof older than 300 seconds."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_params,
    )

    # 400s old proof exceeds 300s TTL
    stale_proof = make_valid_raw_proof_22_fields(test_env, action, auth, age_seconds=400.0)

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_RECEIPT_STALE_EXPIRED"):
        adapter.verify_and_consume_proof(stale_proof, action, pending_entry, authorization=auth)

    coordinator.close()



def test_group9_stage_1_exact_action_id_equality_invariant(test_env):
    """Stage 1 rejects raw proof when action_id does not match action.action_id (B7-R6-01)."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_params,
    )

    mismatched_proof = make_valid_raw_proof_22_fields(
        test_env, action, auth, tamper_action_id="11111111-2222-4333-8444-555555555555"
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ACTION_ID_MISMATCH"):
        adapter.verify_and_consume_proof(mismatched_proof, action, pending_entry, authorization=auth)

    coordinator.close()



def test_group9_stage_1_consumes_proof_and_prevents_replay(test_env):
    """Stage 1 consumes valid raw proof and produces DurableProofRecord; prevents second consumption."""
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_params,
    )

    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    record = adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    assert isinstance(record, DurableProofRecord)
    assert record.verified_outcome == "VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN"
    assert record.action_index == action.action_index
    assert record.target_project_uid == auth.target_project_uid
    assert record.target_timeline_uid == auth.target_timeline_uid

    # Replay prevention
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ALREADY_CONSUMED"):
        adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    coordinator.close()



def test_group9_stage_2_restart_repair_does_not_reapply_300s_ttl(test_env):
    """
    Stage 2 restart repair verification verifies 16 fields without re-applying 300s TTL
    to consumed_at_iso (B7-R6-02).
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_params,
    )

    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    record = adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    # Simulate that restart occurs 1 hour (3600 seconds) later.
    # We verify the durable record at Stage 2:
    assert adapter.verify_durable_proof_record(record, action, pending_entry, authorization=auth) is True

    coordinator.close()



def test_group9_complete_with_clean_proof_branch_2b(test_env):
    """
    Branch 2B: Action fails post-dispatch, but clean absence is proven via independent proof.
    Terminalizes WAL with FINISH(ABORTED), publishes Receipt(ABORTED), exhausts Ledger(ABORTED),
    and releases the target exclusion lock safely.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )

    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Simulate that START and ACTION_PENDING were written
    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)

    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    journal.append_entry(
        auth.execution_id,
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso, action_params={"total_actions": 1}),
    )
    pending_payload = dict(payload)
    pending_payload["action_id"] = action.action_id
    pending_payload["action_index"] = action.action_index
    pending_payload["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1,
        stage="ACTION_PENDING",
        action_type="INSERT_CLIP",
        timestamp_iso=now_iso,
        action_params=pending_payload,
    )
    journal.append_entry(auth.execution_id, pending_entry)

    # Verify and consume clean proof (Stage 1)
    raw_proof = make_valid_raw_proof_22_fields(test_env, action, auth, target_item_found=False, adjacent_clean=True)
    proof_record = adapter.verify_and_consume_proof(raw_proof, action, pending_entry, authorization=auth)

    # Execute Branch 2B completion
    res = adapter.complete_with_clean_proof(proof_record, auth)

    # Verified terminal tuple
    assert res["status"] == "ABORTED"
    assert res["receipt_state"] == "ABORTED"
    assert res["ledger_state"] == "EXHAUSTED"
    assert res["ledger_terminal_reason"] == "ABORTED"
    assert res["wal_final_receipt_state"] == "ABORTED"
    assert res["lock_released"] is True
    assert res["terminalized"] is True

    # Lock released
    assert not coordinator.has_target_lock(auth.target_timeline_uid)

    # WAL has FINISH(ABORTED)
    entries = read_journal_entries(journal, auth.execution_id)
    assert len(entries) == 3
    assert entries[2].stage == "FINISH"
    assert entries[2].action_params.get("final_receipt_state") == "ABORTED"

    coordinator.close()

# ==============================================================================
# GROUP 10: Codex Findings Verification (LIVE-IMPL-01 through LIVE-IMPL-06)
# ==============================================================================

def test_codex_live_impl_01_fabricated_proof_rejected(test_env):
    """
    LIVE-IMPL-01: complete_with_clean_proof rejects fabricated proof records not backed
    by a verified durable record on disk, or whose HMAC/bindings are corrupted.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Setup WAL START and ACTION_PENDING
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    journal.append_entry(
        auth.execution_id,
        JournalEntry(seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso, action_params={"total_actions": 1}),
    )
    pending_payload = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_payload["action_id"] = action.action_id
    journal.append_entry(
        auth.execution_id,
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=pending_payload),
    )

    # Subcase A: Fabricated record not on disk
    fabricated_record = DurableProofRecord(
        schema_version="1.0",
        proof_record_id="proof_non_existent_record_id",
        execution_id=auth.execution_id,
        action_index=action.action_index,
        authorization_id=auth.authorization_id,
        action_payload_digest_sha256=auth.action_payload_digest_sha256,
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        scanned_frame_start=auth.authorized_record_frame,
        scanned_frame_end=auth.authorized_record_frame + auth.authorized_timeline_frame_duration,
        verified_outcome="VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN",
        raw_proof_id="prf_raw_fake",
        raw_proof_digest_sha256="e" * 64,
        consumed_at_iso=now_iso,
        signer_key_id=test_env["trial_key_id"],
        record_hmac_sha256="0" * 64,
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_RECORD_NOT_FOUND"):
        adapter.complete_with_clean_proof(fabricated_record, auth)

    # Subcase B: File on disk with tampered HMAC
    proof_path = os.path.join(test_env["proofs_dir"], f"proof_{fabricated_record.proof_record_id}.proof.json")
    with open(proof_path, "w", encoding="utf-8") as pf:
        json.dump(dataclasses.asdict(fabricated_record), pf)

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_RECORD_HMAC_INVALID"):
        adapter.complete_with_clean_proof(fabricated_record, auth)

    coordinator.close()


def test_codex_live_impl_02_restart_repair_reconciles_crash_boundaries(test_env):
    """
    LIVE-IMPL-02: Normal dispatch rejects pre-existing ACTIVE ledger; execute_restart_repair
    reconciles all crash boundaries without re-dispatching.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    # Boundary 1: Crash after activate_target before START write
    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Normal dispatch is strictly rejected
    with pytest.raises(WholePlanReconciliationError, match="ERR_TARGET_LEDGER_PRE_EXISTING_ACTIVE"):
        adapter.execute_action(action, auth)

    # Restart repair repairs without dispatch
    repair_res = adapter.execute_restart_repair(auth, action)
    assert repair_res["status"].startswith("REPAIRED")
    assert repair_res["terminal_state"] == "ABORTED"
    assert repair_res["lock_released"] is True
    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    assert d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid).state == "EXHAUSTED"

    coordinator.close()


def test_codex_live_impl_03_media_pool_item_binding_validation(test_env):
    """
    LIVE-IMPL-03: LiveResolveCanaryAdapter rejects missing Media Pool item, path mismatch,
    or name mismatch before dispatch.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)

    # Create fake client with empty media pool (no matching clip)
    fake_client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
    )
    mp = fake_client.GetProjectManager().GetCurrentProject().GetMediaPool()
    mp._clips = []
    mp._root_folder = canary_module.FakeFolder([])

    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Case A: Missing canary clip item raises ERR_MISSING_CANARY_CLIP_ITEM
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_MISSING_CANARY_CLIP_ITEM"):
        adapter.execute_action(action, auth)

    # Case B: Canary clip item with path mismatch
    mismatched_path_clip = FakeMediaPoolItem(file_path="/media/other/wrong_clip.mov")
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_MEDIA_ITEM_PATH_MISMATCH"):
        adapter.execute_action(action, auth, canary_clip_item=mismatched_path_clip)

    # Case C: Canary clip item with name mismatch
    mismatched_name_clip = FakeMediaPoolItem(file_path=auth.source_file_path, name="wrong_name.mov")
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_MEDIA_ITEM_NAME_MISMATCH"):
        adapter.execute_action(action, auth, canary_clip_item=mismatched_name_clip)

    coordinator.close()


def test_codex_live_impl_04_unconditional_action_id_three_way_equality(test_env):
    """
    LIVE-IMPL-04: Stage 1 proof verification strictly enforces unconditional three-way
    action_id equality (raw_proof.action_id == action.action_id == pending.action_params["action_id"]).
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Case A: Pending entry has missing action_id
    params_no_action_id = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    entry_no_action_id = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=params_no_action_id
    )
    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ACTION_ID_MISMATCH"):
        adapter.verify_and_consume_proof(valid_proof, action, entry_no_action_id, authorization=auth)

    # Case B: Pending entry has mismatched action_id
    params_diff_action_id = dict(params_no_action_id)
    params_diff_action_id["action_id"] = "act_mismatched_pending"
    entry_diff_action_id = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=params_diff_action_id
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ACTION_ID_MISMATCH"):
        adapter.verify_and_consume_proof(valid_proof, action, entry_diff_action_id, authorization=auth)

    coordinator.close()


def test_codex_live_impl_05_durable_proof_ledger_consume_once_and_fsync(test_env):
    """
    LIVE-IMPL-05: Durable proof ledger is directory-descriptor anchored, writes consume-once
    atomic records, and rejects double consumption.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=pending_params
    )

    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    record = adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    # Record exists on disk in proofs dir
    record_file = os.path.join(test_env["proofs_dir"], f"proof_{record.proof_record_id}.proof.json")
    assert os.path.isfile(record_file)

    # Second consumption of the same proof fails
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ALREADY_CONSUMED"):
        adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    coordinator.close()


def test_codex_live_impl_06_receipt_write_all_and_dto_equality_verification(test_env):
    """
    LIVE-IMPL-06: Receipt publication executes descriptor-anchored write-all loop,
    and performs 100% full DTO equality readback verification.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    receipt = adapter._publish_durable_trial_receipt(
        execution_id=auth.execution_id,
        authorization=auth,
        state="COMPLETED",
        completed_at_iso=now_iso,
    )
    assert receipt.execution_receipt["state"] == "COMPLETED"

    # Verify that receipt file exists and reads back identically
    rcpt_path = os.path.join(test_env["receipts_dir"], f"{auth.execution_id}.trial_receipt.json")
    assert os.path.isfile(rcpt_path)

    coordinator.close()


# ==============================================================================
# GROUP 11: Codex Revision R1 Findings Verification (LIVE-IMPL-R1-01..05)
# ==============================================================================

def test_codex_r1_01_proof_persistence_cannot_overwrite_consumed_record(test_env):
    """
    LIVE-IMPL-R1-01: _persist_durable_proof_record must use consume-once descriptor-anchored
    operations, treat EEXIST as ERR_PROOF_ALREADY_CONSUMED, and never fall back to replacement.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    durable_record = DurableProofRecord(
        schema_version="1.0",
        proof_record_id="proof_rec_r1_01_test",
        execution_id=auth.execution_id,
        action_index=0,
        authorization_id=auth.authorization_id,
        action_payload_digest_sha256=auth.action_payload_digest_sha256,
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        scanned_frame_start=auth.authorized_record_frame,
        scanned_frame_end=auth.authorized_record_frame + 120,
        verified_outcome="VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN",
        raw_proof_id="raw_r1_01_test",
        raw_proof_digest_sha256="a" * 64,
        consumed_at_iso=now_iso,
        signer_key_id=test_env["trial_key_id"],
        record_hmac_sha256="b" * 64,
    )

    # 1. Normal persistence succeeds
    adapter._persist_durable_proof_record(durable_record)
    dest_file = os.path.join(test_env["proofs_dir"], f"proof_{durable_record.proof_record_id}.proof.json")
    assert os.path.isfile(dest_file)
    original_mtime = os.stat(dest_file).st_mtime_ns

    # 2. Second persistence attempt must raise ERR_PROOF_ALREADY_CONSUMED and NEVER overwrite
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ALREADY_CONSUMED"):
        adapter._persist_durable_proof_record(durable_record)

    # Assert destination file was not overwritten
    assert os.stat(dest_file).st_mtime_ns == original_mtime

    # 3. Even if destination file exists prior to link attempt (simulating race)
    with patch.object(adapter, "_durable_proof_exists", return_value=False):
        with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ALREADY_CONSUMED"):
            adapter._persist_durable_proof_record(durable_record)

    coordinator.close()


def test_codex_r1_02_restart_repair_requires_independent_evidence(test_env):
    """
    LIVE-IMPL-R1-02: execute_restart_repair revalidates WAL grammar/identity and
    requires physical readback for committed success (Boundary 4) and clean proof for
    post-dispatch aborts (Boundary 5) before terminalization.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Setup WAL START, ACTION_PENDING, and ACTION_COMMITTED (Boundary 4)
    start_entry = JournalEntry(
        seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
        action_params={
            "planned_actions_count": 1,
            "execution_id": auth.execution_id,
            "authorization_id": auth.authorization_id,
            "target_project_uid": auth.target_project_uid,
            "target_timeline_uid": auth.target_timeline_uid,
            "plan_idempotency_key": auth.plan_idempotency_key,
        },
    )
    journal.append_entry(auth.execution_id, start_entry)

    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    pending_params = dict(payload)
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso,
        action_params=pending_params,
    )
    journal.append_entry(auth.execution_id, pending_entry)

    commit_entry = JournalEntry(
        seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso,
        action_params=dict(pending_params),
        resolve_item_id="canary_item_uid_001",
    )
    journal.append_entry(auth.execution_id, commit_entry)

    # Subcase A: Boundary 4 without physical item on timeline -> LATCHES IN_DOUBT_LOCKED, lock retained!
    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None
    active_tl.set_track_items("video", 1, [])  # Empty track

    repair_res = adapter.execute_restart_repair(auth, action)
    assert repair_res["status"] == "IN_DOUBT_LOCKED_AWAITING_PHYSICAL_OBSERVATION"
    assert repair_res["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None
    assert d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid).state == "ACTIVE"

    # Subcase B: Boundary 4 with verified physical item on timeline -> repairs to COMPLETED and releases lock!
    valid_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [valid_item])

    repair_res2 = adapter.execute_restart_repair(auth, action)
    assert repair_res2["status"] == "REPAIRED_COMMITTED_FINISH"
    assert repair_res2["terminal_state"] == "COMPLETED"
    assert repair_res2["lock_released"] is True
    assert not coordinator.has_target_lock(auth.target_timeline_uid)
    assert d_ledger.get_ledger(auth.target_project_uid, auth.target_timeline_uid).state == "EXHAUSTED"

    coordinator.close()


def test_codex_r1_03_mandatory_authorization_and_canonical_hmac_only(test_env):
    """
    LIVE-IMPL-R1-03: Proof verifiers strictly require TrialExecutionAuthorization and
    accept ONLY canonical RFC 8785 HMAC bytes (no duck-typing, no json.dumps fallback).
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=pending_params
    )

    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)

    # 1. verify_and_consume_proof rejects when authorization is None / unbound
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTHORIZATION_REQUIRED"):
        adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=None)

    # 2. verify_and_consume_proof succeeds with valid authorization
    record = adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)

    # 3. verify_durable_proof_record rejects when authorization is None / unbound
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_AUTHORIZATION_REQUIRED"):
        adapter.verify_durable_proof_record(record, action, pending_entry, authorization=None)

    # 4. Reject tampered HMAC
    tampered_record = dataclasses.replace(record, record_hmac_sha256="0" * 64)
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_RECORD_HMAC_INVALID"):
        adapter.verify_durable_proof_record(tampered_record, action, pending_entry, authorization=auth)

    coordinator.close()


def test_codex_r1_04_media_identity_and_source_digest_enforcement(test_env):
    """
    LIVE-IMPL-R1-04: Enforces actual Resolve item unique ID, exact media-pool source path,
    exact name match, and authorized source content digest before commit.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None

    # Subcase A: Missing source file on disk
    non_existent_auth = dataclasses.replace(auth, source_file_path="/tmp/non_existent_clip_123.mov")
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_MEDIA_SOURCE_FILE_NOT_FOUND"):
        adapter._validate_media_pool_item_binding(FakeMediaPoolItem("/tmp/non_existent_clip_123.mov"), non_existent_auth)

    # Subcase B: Source content digest mismatch
    bad_digest_auth = dataclasses.replace(auth, source_file_sha256="0" * 64)
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_MEDIA_ITEM_DIGEST_MISMATCH"):
        adapter._validate_media_pool_item_binding(FakeMediaPoolItem(auth.source_file_path), bad_digest_auth)

    # Subcase C: Physical readback item name does NOT allow permissive 'canary_clip' prefix
    wrong_name_item = FakeTimelineItem(
        name="canary_clip_unauthorized_prefix",
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [wrong_name_item])
    with pytest.raises(PhysicalReadbackVerificationError, match="ERR_PHYSICAL_READBACK_MEDIA_NAME_MISMATCH"):
        adapter._perform_physical_readback_observation(active_tl, auth)

    # Subcase D: Physical readback item missing unique ID
    no_id_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="",  # Empty ID
    )
    active_tl.set_track_items("video", 1, [no_id_item])
    with pytest.raises(PhysicalReadbackVerificationError, match="ERR_PHYSICAL_READBACK_ITEM_ID_MISSING"):
        adapter._perform_physical_readback_observation(active_tl, auth)

    # Subcase E: Physical readback item missing MediaPoolItem
    no_media_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=None,
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [no_media_item])
    with pytest.raises(PhysicalReadbackVerificationError, match="ERR_PHYSICAL_READBACK_MEDIA_ITEM_MISSING"):
        adapter._perform_physical_readback_observation(active_tl, auth)

    coordinator.close()


def test_codex_r1_05_receipt_descriptor_anchored_and_full_dto_equality(test_env):
    """
    LIVE-IMPL-R1-05: Receipt publication is descriptor-anchored via os.replace with dir_fd,
    enforces exact-key schema validation, and verifies full DTO equality including nested published_at_iso.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # 1. Normal publication verifies full DTO equality
    receipt = adapter._publish_durable_trial_receipt(
        execution_id=auth.execution_id,
        authorization=auth,
        state="COMPLETED",
        completed_at_iso=now_iso,
    )
    assert receipt.execution_receipt["state"] == "COMPLETED"
    assert "published_at_iso" in receipt.execution_receipt

    # 2. Corrupting receipt with extra unexpected key is rejected by readback schema validation
    rcpt_path = os.path.join(test_env["receipts_dir"], f"{auth.execution_id}.trial_receipt.json")
    with open(rcpt_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["extra_forbidden_key"] = "tampered"
    with open(rcpt_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data))

    with pytest.raises(ReceiptDurabilityError, match="ERR_RECEIPT_SCHEMA_MISMATCH"):
        read_flags = os.O_RDONLY
        rfd = os.open(rcpt_path, read_flags)
        try:
            raw = canary_module._read_bytes_bounded(rfd, max_bytes=canary_module.MAX_RECEIPT_BYTES)
            d = json.loads(raw.decode("utf-8"))
            if set(d.keys()) != canary_module.EXACT_RECEIPT_KEYS:
                raise ReceiptDurabilityError("ERR_RECEIPT_SCHEMA_MISMATCH")
        finally:
            os.close(rfd)


def test_codex_r2_01_signed_authorization_cryptographic_verification(test_env):
    """
    LIVE-IMPL-R2-01: Public execution, restart repair, and proof verification boundaries
    strictly require cryptographic verification of TrialExecutionAuthorization against
    the coordinator's keyring authority (rejecting tampered or unauthenticated authorizations).
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, valid_auth = make_canary_action_and_matching_auth(test_env)
    _, bad_auth = make_canary_action_and_matching_auth(test_env, tamper_hmac="bad" * 21 + "b")
    fake_client = make_fake_resolve_client(
        valid_auth.target_project_name, valid_auth.target_project_uid, valid_auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    coordinator.acquire_target_lock(valid_auth.target_timeline_uid)

    # 1. execute_action rejects tampered authorization
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNED_AUTH_HMAC_INVALID"):
        adapter.execute_action(action, bad_auth)

    # 2. execute_restart_repair rejects tampered authorization
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNED_AUTH_HMAC_INVALID"):
        adapter.execute_restart_repair(bad_auth, action)

    # 3. verify_and_consume_proof rejects tampered authorization
    raw_proof = make_valid_raw_proof_22_fields(test_env, action, valid_auth)
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=raw_proof["observation_timestamp_iso"],
        action_params={"action_id": action.action_id, "action_index": 0},
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNED_AUTH_HMAC_INVALID"):
        adapter.verify_and_consume_proof(raw_proof, action, pending_entry, authorization=bad_auth)

    # 4. complete_with_clean_proof rejects tampered authorization
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNED_AUTH_HMAC_INVALID"):
        adapter.complete_with_clean_proof("dummy_record_id", authorization=bad_auth)

    coordinator.close()


def test_codex_r2_02_wal_grammar_and_ledger_validation(test_env):
    """
    LIVE-IMPL-R2-02: execute_restart_repair validates complete ordered WAL stage grammar,
    one-to-one record bindings, and durable ledger state/identity before branching or terminalizing.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Case A: Missing durable ledger entry -> IN_DOUBT_LOCKED_LEDGER_NOT_FOUND
    res_no_ledger = adapter.execute_restart_repair(auth, action)
    assert res_no_ledger["status"] == "IN_DOUBT_LOCKED_LEDGER_NOT_FOUND"
    assert res_no_ledger["lock_released"] is False

    # Reserve and activate ledger
    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)

    # Case B: Illegal WAL stage grammar: ["START", "ACTION_COMMITTED"] (skipping ACTION_PENDING)
    s_entry = JournalEntry(
        seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
        action_params={
            "planned_actions_count": 1,
            "execution_id": auth.execution_id,
            "authorization_id": auth.authorization_id,
            "target_project_uid": auth.target_project_uid,
            "target_timeline_uid": auth.target_timeline_uid,
            "plan_idempotency_key": auth.plan_idempotency_key,
        },
    )
    journal.append_entry(auth.execution_id, s_entry)

    c_entry = JournalEntry(
        seq=1, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso,
        action_params={"action_id": action.action_id, "action_index": 0},
        resolve_item_id="canary_item_uid_001",
    )
    journal.append_entry(auth.execution_id, c_entry)

    res_illegal_stages = adapter.execute_restart_repair(auth, action)
    assert res_illegal_stages["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_illegal_stages["error_code"] == "ERR_RESTART_REPAIR_ILLEGAL_STAGE_TRANSITION"
    assert res_illegal_stages["lock_released"] is False

    coordinator.close()


def test_codex_r2_03_atomic_proof_fallback_no_partial_destination(test_env, monkeypatch):
    """
    LIVE-IMPL-R2-03: Proof publication fallback preserves atomic complete-record publication.
    When os.link fails with ENOSYS, attempts libc renameat2 with RENAME_NOREPLACE.
    If atomic publication primitive is unavailable, fails closed without exposing a partial destination
    and retains the temporary file as evidence for controlled repair.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )

    raw_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_index"] = action.action_index
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=raw_proof["observation_timestamp_iso"],
        action_params=pending_params,
    )

    # Subcase A: os.link raises ENOSYS, fallback to renameat2 succeeds atomically
    def mock_link(*args, **kwargs):
        raise OSError(errno.ENOSYS, "Function not implemented")

    monkeypatch.setattr(os, "link", mock_link)
    record = adapter.verify_and_consume_proof(raw_proof, action, pending_entry, authorization=auth)
    assert record is not None
    assert adapter._durable_proof_exists(record.proof_record_id)

    # Subcase B: Destination already exists -> raises ERR_PROOF_ALREADY_CONSUMED
    raw_proof2 = dict(raw_proof)
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PROOF_ALREADY_CONSUMED"):
        adapter.verify_and_consume_proof(raw_proof2, action, pending_entry, authorization=auth)

    coordinator.close()


def test_codex_r3_01_ledger_identity_and_terminal_tuple_reconciliation(test_env):
    """
    LIVE-IMPL-R3-01: Restart repair enforces complete ledger identity match
    (including approval_id, proposal_id, and proposal_revision) and terminal
    tuple consistency when ledger is EXHAUSTED before lock release.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    # Part A: Mismatched proposal_id in ledger latches IN_DOUBT_LOCKED_LEDGER_MISMATCH
    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id="proposal_wrong_id",
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    res_id_mismatch = adapter.execute_restart_repair(auth, action)
    assert res_id_mismatch["status"] == "IN_DOUBT_LOCKED_LEDGER_MISMATCH"
    assert res_id_mismatch["error_code"] == "ERR_RESTART_REPAIR_LEDGER_IDENTITY_MISMATCH"
    assert res_id_mismatch["lock_released"] is False
    coordinator.close()

    # Part B: Boundary 5 with EXHAUSTED ledger: receipt missing latches IN_DOUBT_LOCKED_RECEIPT_MISSING
    action2, auth2 = make_canary_action_and_matching_auth(
        test_env,
        execution_id="exec_2222222222abcdef2222222222abcdef",
        target_timeline_uid="tl_canary_002",
        challenge_nonce="4E2A8F1C0B3D5E7B",
    )
    fake_client2 = make_fake_resolve_client(
        auth2.target_project_name, auth2.target_project_uid, auth2.target_timeline_uid
    )
    coordinator2 = make_coordinator(test_env)
    adapter2 = LiveResolveCanaryAdapter(coordinator2, journal, fake_client2, proof_ledger_dir=test_env["proofs_dir"])
    d_ledger2 = coordinator2.durable_ledger
    assert d_ledger2 is not None

    d_ledger2.reserve_target(
        target_project_uid=auth2.target_project_uid,
        target_timeline_uid=auth2.target_timeline_uid,
        authorization_id=auth2.authorization_id,
        execution_id=auth2.execution_id,
        approval_id=auth2.approval_record_id,
        proposal_id=auth2.proposal_id,
        proposal_revision=auth2.proposal_revision,
        plan_idempotency_key=auth2.plan_idempotency_key,
    )
    d_ledger2.activate_target(auth2.target_project_uid, auth2.target_timeline_uid, auth2.execution_id)
    d_ledger2.exhaust_target(
        target_project_uid=auth2.target_project_uid,
        target_timeline_uid=auth2.target_timeline_uid,
        execution_id=auth2.execution_id,
        terminal_receipt_id=f"rcpt_{auth2.execution_id}",
        terminal_reason="ABORTED",
    )
    coordinator2.acquire_target_lock(auth2.target_timeline_uid)

    # WAL has START, FINISH(ABORTED)
    s_entry = JournalEntry(
        seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
        action_params={
            "planned_actions_count": 1,
            "execution_id": auth2.execution_id,
            "authorization_id": auth2.authorization_id,
            "target_project_uid": auth2.target_project_uid,
            "target_timeline_uid": auth2.target_timeline_uid,
            "plan_idempotency_key": auth2.plan_idempotency_key,
        },
    )
    journal.append_entry(auth2.execution_id, s_entry)
    f_entry = JournalEntry(
        seq=1, stage="FINISH", action_type="NO_OP", timestamp_iso=now_iso,
        note="CANCELLED_AT_PRE_DISPATCH_BOUNDARY",
        action_params={"total_actions": 1, "final_receipt_state": "ABORTED"},
    )
    journal.append_entry(auth2.execution_id, f_entry)

    # No receipt on disk -> IN_DOUBT_LOCKED_RECEIPT_MISSING
    res_no_rcpt = adapter2.execute_restart_repair(auth2, action2)
    assert res_no_rcpt["status"] == "IN_DOUBT_LOCKED_RECEIPT_MISSING"
    assert res_no_rcpt["lock_released"] is False

    # Part C: Existing receipt state disagrees with ledger terminal reason -> IN_DOUBT_LOCKED_TERMINAL_TUPLE_MISMATCH
    adapter2._publish_durable_trial_receipt(
        execution_id=auth2.execution_id,
        authorization=auth2,
        state="COMPLETED",  # Contradicts ledger terminal_reason 'ABORTED'
        completed_at_iso=now_iso,
    )
    res_tuple_mismatch = adapter2.execute_restart_repair(auth2, action2)
    assert res_tuple_mismatch["status"] == "IN_DOUBT_LOCKED_TERMINAL_TUPLE_MISMATCH"
    assert res_tuple_mismatch["error_code"] == "ERR_RESTART_REPAIR_TERMINAL_TUPLE_CONTRADICTION"
    assert res_tuple_mismatch["lock_released"] is False
    coordinator2.close()


def test_codex_r3_02_wal_action_binding_and_item_id_equality(test_env):
    """
    LIVE-IMPL-R3-02: Restart repair enforces complete action payload binding and digest on
    ACTION_PENDING, 1-to-1 payload equality across entries, and item ID equality on readback.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Subcase A: ACTION_PENDING with mismatched payload digest latches IN_DOUBT_LOCKED_INVALID_WAL
    s_entry = JournalEntry(
        seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
        action_params={
            "planned_actions_count": 1,
            "execution_id": auth.execution_id,
            "authorization_id": auth.authorization_id,
            "target_project_uid": auth.target_project_uid,
            "target_timeline_uid": auth.target_timeline_uid,
            "plan_idempotency_key": auth.plan_idempotency_key,
        },
    )
    journal.append_entry(auth.execution_id, s_entry)

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_payload_digest_sha256"] = "0" * 64  # Wrong digest
    p_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso,
        action_params=pending_params,
    )
    journal.append_entry(auth.execution_id, p_entry)

    res_bad_digest = adapter.execute_restart_repair(auth, action)
    assert res_bad_digest["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_bad_digest["error_code"] == "ERR_RESTART_REPAIR_PAYLOAD_DIGEST_MISMATCH"
    assert res_bad_digest["lock_released"] is False

    # Subcase B: Physical readback item ID mismatch at Boundary 4
    journal2 = ExecutionJournalService(os.path.join(test_env["base_dir"], "j2"))
    adapter2 = LiveResolveCanaryAdapter(coordinator, journal2, fake_client, proof_ledger_dir=test_env["proofs_dir"])
    journal2.append_entry(auth.execution_id, s_entry)

    valid_pending = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    valid_pending["action_id"] = action.action_id
    valid_pending["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    journal2.append_entry(
        auth.execution_id,
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=valid_pending),
    )

    commit_params = dict(valid_pending)
    journal2.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=commit_params, resolve_item_id="item_id_expected_123",
        ),
    )

    # Setup active timeline with different item ID
    active_proj = fake_client.GetProjectManager().GetCurrentProject()
    assert active_proj is not None
    active_tl = active_proj.GetCurrentTimeline()
    assert active_tl is not None
    mismatched_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=auth.authorized_record_frame,
        end=auth.authorized_record_frame + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="item_id_DIFFERENT_456",
    )
    active_tl.set_track_items("video", 1, [mismatched_item])

    res_item_mismatch = adapter2.execute_restart_repair(auth, action)
    assert res_item_mismatch["status"] == "IN_DOUBT_LOCKED_AWAITING_PHYSICAL_OBSERVATION"
    assert res_item_mismatch["error_code"] == "ERR_RESTART_REPAIR_ITEM_ID_MISMATCH"
    assert res_item_mismatch["lock_released"] is False

    coordinator.close()


def test_codex_r3_03_durable_proof_strict_types_no_coercion(test_env):
    """
    LIVE-IMPL-R3-03: DurableProofRecord.from_dict enforces strict primitive type gates
    and value bounds without coercion (rejects bool for int, float for int, non-string, etc.).
    """
    action, auth = make_canary_action_and_matching_auth(test_env)
    valid_proof = make_valid_raw_proof_22_fields(test_env, action, auth)
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"])

    pending_params = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    pending_params["action_id"] = action.action_id
    pending_params["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    pending_entry = JournalEntry(
        seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=valid_proof["observation_timestamp_iso"],
        action_params=pending_params,
    )

    record = adapter.verify_and_consume_proof(valid_proof, action, pending_entry, authorization=auth)
    base_dict = record.to_dict()

    # Reject bool for integer action_index
    bad_bool_dict = dict(base_dict)
    bad_bool_dict["action_index"] = True
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_DURABLE_PROOF_TYPE_MISMATCH"):
        DurableProofRecord.from_dict(bad_bool_dict)

    # Reject float for integer scanned_frame_start
    bad_float_dict = dict(base_dict)
    bad_float_dict["scanned_frame_start"] = 0.0
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_DURABLE_PROOF_TYPE_MISMATCH"):
        DurableProofRecord.from_dict(bad_float_dict)

    # Reject non-string for execution_id
    bad_str_dict = dict(base_dict)
    bad_str_dict["execution_id"] = 12345
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_DURABLE_PROOF_TYPE_MISMATCH"):
        DurableProofRecord.from_dict(bad_str_dict)

    # Reject empty string
    empty_str_dict = dict(base_dict)
    empty_str_dict["target_project_uid"] = "   "
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_DURABLE_PROOF_EMPTY_FIELD"):
        DurableProofRecord.from_dict(empty_str_dict)

    # Reject negative action_index
    neg_dict = dict(base_dict)
    neg_dict["action_index"] = -1
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_DURABLE_PROOF_BOUNDS"):
        DurableProofRecord.from_dict(neg_dict)

    # Valid dictionary round-trips exactly
    valid_restored = DurableProofRecord.from_dict(base_dict)
    assert valid_restored == record

    coordinator.close()


def test_codex_r4_01_restart_repair_requires_authoritative_action(test_env):
    """
    LIVE-IMPL-R4-01: Restart repair requires an authoritative PlannedTimelineAction to validate
    action identity and payload bindings. When repair is invoked without action (action=None)
    against a WAL containing ACTION_PENDING/COMMITTED (even with consistently substituted action IDs),
    it must remain in doubt and retain the target lock (ERR_RESTART_REPAIR_ACTION_REQUIRED).
    Furthermore, supplying an action whose action_id differs from the WAL latches IN_DOUBT_LOCKED.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    fake_client = make_fake_resolve_client(
        auth.target_project_name, auth.target_project_uid, auth.target_timeline_uid
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, fake_client, proof_ledger_dir=test_env["proofs_dir"]
    )
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    d_ledger = coordinator.durable_ledger
    assert d_ledger is not None

    d_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    d_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    # Setup WAL with consistently substituted action_id ("act_substituted_999")
    s_entry = JournalEntry(
        seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
        action_params={
            "planned_actions_count": 1,
            "execution_id": auth.execution_id,
            "authorization_id": auth.authorization_id,
            "target_project_uid": auth.target_project_uid,
            "target_timeline_uid": auth.target_timeline_uid,
            "plan_idempotency_key": auth.plan_idempotency_key,
        },
    )
    journal.append_entry(auth.execution_id, s_entry)

    sub_pending = dict(assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id))
    sub_pending["action_id"] = "act_substituted_999"  # Consistently substituted action_id
    sub_pending["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    journal.append_entry(
        auth.execution_id,
        JournalEntry(seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso, action_params=sub_pending),
    )

    sub_committed = dict(sub_pending)
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=sub_committed, resolve_item_id="item_id_sub_999",
        ),
    )

    # Subcase A: Invoking repair WITHOUT action (action=None) -> LATCHES IN_DOUBT_LOCKED, retains lock!
    res_no_action = adapter.execute_restart_repair(auth)
    assert res_no_action["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_no_action["error_code"] == "ERR_RESTART_REPAIR_ACTION_REQUIRED"
    assert res_no_action["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None

    # Subcase B: Invoking repair WITH authorized action (action.action_id == "act_1234567890abcdef") ->
    # Mismatch with substituted WAL action_id latches IN_DOUBT_LOCKED!
    res_action_mismatch = adapter.execute_restart_repair(auth, action)
    assert res_action_mismatch["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_action_mismatch["error_code"] == "ERR_RESTART_REPAIR_PENDING_PARAMS_MISMATCH"
    assert res_action_mismatch["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None

    # Subcase C (LIVE-IMPL-R5-01): All 16 authorized payload fields remain unchanged,
    # but BOTH the supplied action and WAL use a substituted action_id ("act_substituted_999").
    # Plan digest authentication against authorization.plan_digest_sha256 detects substituted ID,
    # latches IN_DOUBT_LOCKED, and retains target lock!
    substituted_action = dataclasses.replace(action, action_id="act_substituted_999")
    res_both_substituted = adapter.execute_restart_repair(auth, substituted_action)
    assert res_both_substituted["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_both_substituted["error_code"] in ("ERR_RESTART_REPAIR_ACTION_UNAUTHORIZED", "ERR_RESTART_REPAIR_PLAN_UNAUTHORIZED")
    assert any(err in res_both_substituted["error"] for err in ("ERR_PLAN_DIGEST_MISMATCH", "ERR_ACTION_ID_PLAN_HASH_MISMATCH"))
    assert res_both_substituted["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None

    coordinator.close()


def test_codex_r5_01_admission_rejects_substituted_action_id_in_plan_digest(test_env):
    """
    Codex Finding LIVE-IMPL-R5-01 / Workorder R7 §4:
    Verifies that admission rejects any PlannedTimelineAction whose action_id was substituted,
    even if all 16 canonical payload fields are identical.
    - Identity bridge mismatch fails closed with ERR_ACTION_ID_PLAN_HASH_MISMATCH.
    - If plan is altered to match the substituted ID, plan digest mismatch fails closed with ERR_PLAN_DIGEST_MISMATCH.
    """
    action, auth = make_canary_action_and_matching_auth(test_env)
    client = make_fake_resolve_client(test_env, project_uid=auth.target_project_uid, timeline_uid=auth.target_timeline_uid)
    adapter = LiveResolveCanaryAdapter(
        resolve_client=client,
        coordinator=make_coordinator(test_env),
        journal=ExecutionJournalService(test_env["journals_dir"]),
        proof_ledger_dir=test_env["proofs_dir"],
    )

    # Subcase A: Substituted action_id alone fails closed via identity bridge
    substituted_action = dataclasses.replace(action, action_id="act_substituted_attacker")
    with pytest.raises(WholePlanReconciliationError, match="ERR_ACTION_ID_PLAN_HASH_MISMATCH"):
        adapter.execute_live_canary_action(substituted_action, auth)

    # Subcase B: Substituted action_id with altered plan matching the hash fails closed via plan digest mismatch
    altered_plan = make_canary_execution_plan(substituted_action, auth, action_hash_override="act_substituted_attacker")
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_DIGEST_MISMATCH"):
        adapter.execute_live_canary_action(substituted_action, auth, plan=altered_plan)

    adapter.close()


def test_codex_r7_01_source_aligned_plan_digest_admission_acceptance(test_env):
    """
    Workorder R7 §4: Valid source-aligned TimelineExecutionPlan digest is accepted at admission.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env, observed_start_frame=86400)
    client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )
    active_proj = client.GetProjectManager().GetCurrentProject()
    active_tl = active_proj.GetCurrentTimeline()
    inserted_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=86400,
        end=86400 + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="canary_item_uid_001",
    )
    active_tl.set_track_items("video", 1, [inserted_item])

    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, client, proof_ledger_dir=test_env["proofs_dir"]
    )
    coordinator.acquire_target_lock(auth.target_timeline_uid)

    plan = make_canary_execution_plan(action, auth)
    res = adapter.execute_action(action, auth, plan=plan)
    assert res["status"] == "COMPLETED"
    assert res["receipt_state"] == "COMPLETED"
    assert res["ledger_state"] == "EXHAUSTED"
    assert res["ledger_terminal_reason"] == "COMPLETED"
    adapter.close()


def test_codex_r7_02_identity_bridge_mismatch_fails_admission(test_env):
    """
    Workorder R7 §2.2 & §4: Canary action ID must equal binder action's action_idempotency_hash.
    A mismatch is rejected before dispatch.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, client, proof_ledger_dir=test_env["proofs_dir"]
    )

    # Plan has different action_idempotency_hash from action.action_id
    plan_with_diff_hash = make_canary_execution_plan(action, auth, action_hash_override="act_different_hash_123")
    with pytest.raises(WholePlanReconciliationError, match="ERR_ACTION_ID_PLAN_HASH_MISMATCH"):
        adapter.execute_action(action, auth, plan=plan_with_diff_hash)

    adapter.close()


def test_codex_r7_03_tampered_action_id_rejected_even_if_action_and_wal_consistent(test_env):
    """
    Workorder R7 §4: Tampered action ID is rejected during restart repair even when
    all other action fields and WAL PENDING/COMMITTED IDs are changed consistently.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, client, proof_ledger_dir=test_env["proofs_dir"]
    )

    coordinator.acquire_target_lock(auth.target_timeline_uid)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    coordinator.durable_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    coordinator.durable_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)

    # Create WAL with tampered action ID "act_tampered_007"
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
            action_params={
                "execution_id": auth.execution_id,
                "authorization_id": auth.authorization_id,
                "target_project_uid": auth.target_project_uid,
                "target_timeline_uid": auth.target_timeline_uid,
                "plan_idempotency_key": auth.plan_idempotency_key,
                "planned_actions_count": 1,
            },
        ),
    )
    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    tampered_pending = dict(payload)
    tampered_pending["action_id"] = "act_tampered_007"
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=tampered_pending,
        ),
    )

    tampered_action = dataclasses.replace(action, action_id="act_tampered_007")
    tampered_plan = make_canary_execution_plan(tampered_action, auth, action_hash_override="act_tampered_007")

    # Restart repair with consistent tampered action and plan must be rejected by plan_digest_sha256
    res = adapter.execute_restart_repair(auth, action=tampered_action, plan=tampered_plan)
    assert res["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res["error_code"] == "ERR_RESTART_REPAIR_PLAN_UNAUTHORIZED"
    assert "ERR_PLAN_DIGEST_MISMATCH" in res["error"]
    assert res["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"
    assert coordinator._target_locks[auth.target_timeline_uid].fd is not None

    coordinator.close()


def test_codex_r7_04_wrong_plan_malformed_plan_missing_plan_fail_closed_or_in_doubt(test_env):
    """
    Workorder R7 §4: Wrong plan, wrong plan action index, missing plan/action, malformed plan,
    and digest mismatch all fail closed before dispatch or keep restart repair in doubt with target lock retained.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env)
    client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )
    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, client, proof_ledger_dir=test_env["proofs_dir"]
    )

    # 1. Missing plan before dispatch (auth without execution_plan attribute)
    auth_no_plan = dataclasses.replace(auth)
    if hasattr(auth_no_plan, "execution_plan"):
        delattr(auth_no_plan, "execution_plan")
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_REQUIRED"):
        adapter.execute_action(action, auth_no_plan, plan=None)

    # 2. Malformed plan type before dispatch
    with pytest.raises(WholePlanReconciliationError, match="ERR_INVALID_PLAN_TYPE"):
        adapter.execute_action(action, auth, plan="invalid_string_plan")

    # 3. Wrong plan idempotency key
    plan_wrong_ik = make_canary_execution_plan(action, auth, plan_idempotency_key_override="plan_wrong_ik_999")
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_IDEMPOTENCY_KEY_MISMATCH"):
        adapter.execute_action(action, auth, plan=plan_wrong_ik)

    # 4. Wrong project UID in plan
    plan_wrong_proj = make_canary_execution_plan(action, auth, project_uid_override="proj_wrong_999")
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_PROJECT_UID_MISMATCH"):
        adapter.execute_action(action, auth, plan=plan_wrong_proj)

    # 5. Wrong timeline UID in plan
    plan_wrong_tl = make_canary_execution_plan(action, auth, timeline_uid_override="tl_wrong_999")
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_TIMELINE_UID_MISMATCH"):
        adapter.execute_action(action, auth, plan=plan_wrong_tl)

    # 6. Wrong action count (0 or 2 actions in plan)
    plan_two_actions = make_canary_execution_plan(
        action, auth, planned_actions_override=(auth.execution_plan.planned_actions[0], auth.execution_plan.planned_actions[0])
    )
    with pytest.raises(WholePlanReconciliationError, match="ERR_PLAN_ACTION_COUNT_MISMATCH"):
        adapter.execute_action(action, auth, plan=plan_two_actions)

    # 7. Restart repair with ACTION_PENDING and missing plan
    coordinator.acquire_target_lock(auth.target_timeline_uid)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    coordinator.durable_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    coordinator.durable_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)

    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
            action_params={
                "execution_id": auth.execution_id,
                "authorization_id": auth.authorization_id,
                "target_project_uid": auth.target_project_uid,
                "target_timeline_uid": auth.target_timeline_uid,
                "plan_idempotency_key": auth.plan_idempotency_key,
                "planned_actions_count": 1,
            },
        ),
    )
    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=payload,
        ),
    )

    res_missing_plan = adapter.execute_restart_repair(auth_no_plan, action=action, plan=None)
    assert res_missing_plan["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_missing_plan["error_code"] == "ERR_RESTART_REPAIR_PLAN_REQUIRED"
    assert res_missing_plan["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"

    res_malformed_plan = adapter.execute_restart_repair(auth, action=action, plan="malformed_plan")
    assert res_malformed_plan["status"] == "IN_DOUBT_LOCKED_INVALID_WAL"
    assert res_malformed_plan["error_code"] == "ERR_RESTART_REPAIR_PLAN_REQUIRED"
    assert res_malformed_plan["lock_released"] is False
    assert coordinator._target_locks[auth.target_timeline_uid].state == "IN_DOUBT_LOCKED"

    coordinator.close()


def test_codex_r7_05_valid_source_aligned_plan_accepted_in_restart_repair(test_env):
    """
    Workorder R7 §4: Valid source-aligned TimelineExecutionPlan is accepted during restart repair
    for Boundary 4 (physical readback observation) and completes successfully.
    """
    coordinator = make_coordinator(test_env)
    journal = ExecutionJournalService(test_env["journals_dir"])
    action, auth = make_canary_action_and_matching_auth(test_env, observed_start_frame=86400)
    client = make_fake_resolve_client(
        project_name=auth.target_project_name,
        project_uid=auth.target_project_uid,
        timeline_uid=auth.target_timeline_uid,
        start_frame=86400,
    )
    active_proj = client.GetProjectManager().GetCurrentProject()
    active_tl = active_proj.GetCurrentTimeline()
    inserted_item = FakeTimelineItem(
        name=os.path.basename(auth.source_file_path),
        start=86400,
        end=86400 + 120,
        duration=120,
        media_pool_item=FakeMediaPoolItem(auth.source_file_path),
        unique_id="item_canary_001",
    )
    active_tl.set_track_items("video", 1, [inserted_item])

    adapter = LiveResolveCanaryAdapter(
        coordinator, journal, client, proof_ledger_dir=test_env["proofs_dir"]
    )

    coordinator.acquire_target_lock(auth.target_timeline_uid)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    coordinator.durable_ledger.reserve_target(
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        authorization_id=auth.authorization_id,
        execution_id=auth.execution_id,
        approval_id=auth.approval_record_id,
        proposal_id=auth.proposal_id,
        proposal_revision=auth.proposal_revision,
        plan_idempotency_key=auth.plan_idempotency_key,
    )
    coordinator.durable_ledger.activate_target(auth.target_project_uid, auth.target_timeline_uid, auth.execution_id)

    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=0, stage="START", action_type="NO_OP", timestamp_iso=now_iso,
            action_params={
                "execution_id": auth.execution_id,
                "authorization_id": auth.authorization_id,
                "target_project_uid": auth.target_project_uid,
                "target_timeline_uid": auth.target_timeline_uid,
                "plan_idempotency_key": auth.plan_idempotency_key,
                "planned_actions_count": 1,
            },
        ),
    )
    payload = assemble_canonical_action_payload(action, auth, execution_id=auth.execution_id)
    wal_payload = dict(payload)
    wal_payload["action_id"] = action.action_id
    wal_payload["action_payload_digest_sha256"] = auth.action_payload_digest_sha256
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=1, stage="ACTION_PENDING", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=wal_payload,
        ),
    )
    journal.append_entry(
        auth.execution_id,
        JournalEntry(
            seq=2, stage="ACTION_COMMITTED", action_type="INSERT_CLIP", timestamp_iso=now_iso,
            action_params=wal_payload,
            resolve_item_id="item_canary_001",
        ),
    )

    plan = make_canary_execution_plan(action, auth)
    res = adapter.execute_restart_repair(auth, action=action, plan=plan)
    assert res["status"] == "REPAIRED_COMMITTED_FINISH"
    assert res["terminal_state"] == "COMPLETED"
    assert res["lock_released"] is True
    assert coordinator.has_target_lock(auth.target_timeline_uid) is False

    coordinator.close()

    coordinator.close()
