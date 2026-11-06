"""
tests/services/test_live_resolve_canary_adapter_decoupled.py
Comprehensive decoupled test suite for LiveResolveCanaryAdapter (G07-07B7-GATE6-DEP04-ADAPTER-DECOUPLE-R2).

Validates:
1. Constructor requires decoupled dependencies (DurableDispatchJournal, canonical Ed25519 key)
   and rejects legacy dependencies (RecoveryCoordinator, TrialKeyringAuthority, ExecutionJournalService, keystore_path).
2. Module import and construction with inert fakes perform zero Resolve SDK calls/imports,
   zero socket creation/connection, zero host/production path access, and zero import-time key generation.
3. Strict pre-mutation durable ordering (§5.2.2 Steps 1-4) precedes Resolve mutation, and terminal
   commit precedes returning terminalized=True.
4. Valid OracleVerificationResponse cryptographic Ed25519 verification against canonical payload,
   request correlation (request_id, request_nonce), freshness/expiry, nominal plan binding,
   and pinned public key. Tampered signature, claim mismatch, rejected result, wrong key type/subclass,
   expired response, stale response, and malformed response fail closed.
5. Recomputed digests (action payload, plan, authorization) enforce tamper protection before dispatch.
6. Ambiguity is classified IN_DOUBT without retry/rollback, retaining locks, and terminal state
   is delegated strictly to Dispatcher DurableDispatchJournal API.
7. All tests use tmp_path, inert fake Resolve objects, and ephemeral in-memory test keys only.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import hmac
import importlib
import os
import secrets
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.core.contracts.oracle_ipc_contracts import (
    OracleVerificationRequest,
    OracleVerificationResponse,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    PlannedTimelineAction,
    ReadbackReceiptVerificationError,
    TrialExecutionAuthorization,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
    canonical_bytes_for_auth_hmac,
    canonical_json_bytes,
)
from src.core.timeline_plan_execution_binder import (
    PlannedTimelineAction as BinderPlannedTimelineAction,
    RationalFPS,
    TargetSpec,
    TimelineExecutionPlan,
    TimeRangeFrames,
    compute_canonical_plan_digest,
)
from src.services.durable_dispatch_journal import DurableDispatchJournal
import src.services.live_resolve_canary_adapter as canary_module
from src.services.live_resolve_canary_adapter import (
    CANONICAL_ACTION_TYPE,
    CANONICAL_FPS_DENOMINATOR,
    CANONICAL_FPS_NUMERATOR,
    CANONICAL_SOURCE_FRAME_START,
    CANONICAL_TIMELINE_FRAME_DURATION,
    CANONICAL_TIMELINE_FRAME_START,
    CANONICAL_TRACK_INDEX,
    CANONICAL_TRACK_TYPE,
    FakeMediaPool,
    FakeMediaPoolItem,
    FakeProject,
    FakeProjectManager,
    FakeResolveClient,
    FakeTimeline,
    FakeTimelineItem,
    LiveAdmissionTripwireError,
    LiveResolveCanaryAdapter,
    TargetProjectMismatchError,
    TargetTimelineMismatchError,
    TargetTimelineShiftedError,
)


# ==============================================================================
# Test Fixtures & Helpers
# ==============================================================================

def make_test_bundle(
    tmp_path: Path,
    *,
    challenge_nonce: str = "4E2A8F1C0B3D5E7A",
    timeline_start_frame: int = 86400,
    valid_offset_sec: float = 300.0,
    verified_offset_sec: float = -10.0,
    tamper_oracle_sig: bool = False,
    tamper_action_digest: bool = False,
    tamper_plan_digest: bool = False,
    tamper_auth_digest: bool = False,
    tamper_project_uid: bool = False,
    tamper_timeline_uid: bool = False,
    tamper_proposal_rev: bool = False,
    verification_result: str = "VERIFIED_VALID",
    is_valid: bool = True,
    reason_code: str = "VALID",
    custom_request_id: Optional[str] = None,
    custom_request_nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """Builds a mutually consistent action, auth, plan, oracle request/response, and inert client."""
    clip_file = tmp_path / f"canary_clip_{secrets.token_hex(4)}.mov"
    clip_file.write_bytes(b"TEST_CANARY_CLIP_MEDIA_DATA_V1")
    clip_sha256 = hashlib.sha256(b"TEST_CANARY_CLIP_MEDIA_DATA_V1").hexdigest()

    project_uid = str(uuid.uuid4())
    timeline_uid = str(uuid.uuid4())
    execution_id = f"exec_{uuid.uuid4().hex}"
    proposal_revision = "1" * 64

    timeline_frame_start = CANONICAL_TIMELINE_FRAME_START
    duration_frames = CANONICAL_TIMELINE_FRAME_DURATION
    record_frame = timeline_start_frame + timeline_frame_start

    action = PlannedTimelineAction(
        action_id="act_1234567890abcdef",
        action_index=0,
        action_type=CANONICAL_ACTION_TYPE,
        track_type=CANONICAL_TRACK_TYPE,
        track_index=CANONICAL_TRACK_INDEX,
        record_frame=record_frame,
        duration_frames=duration_frames,
        source_path=str(clip_file),
        source_frame_start=CANONICAL_SOURCE_FRAME_START,
        source_frame_duration=duration_frames,
        role="canary_insert",
    )

    now = datetime.now(timezone.utc)
    issued_iso = (now + timedelta(seconds=verified_offset_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_iso = (now + timedelta(seconds=valid_offset_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")

    project_name = f"RF_TRIAL_{challenge_nonce}"
    timeline_name = "Canary Timeline"

    base_auth_dict = {
        "authorization_id": f"auth_{uuid.uuid4().hex}",
        "key_id": "k_ephemeral_test_key",
        "operator_gesture_digest_sha256": "a" * 64,
        "approval_record_id": f"appr_{uuid.uuid4().hex}",
        "approval_record_digest_sha256": "b" * 64,
        "proposal_id": f"prop_{uuid.uuid4().hex}",
        "proposal_revision": proposal_revision,
        "proposal_content_digest_sha256": "c" * 64,
        "plan_idempotency_key": f"plan_{uuid.uuid4().hex}",
        "plan_digest_sha256": "0" * 64,
        "action_payload_digest_sha256": "0" * 64,
        "target_project_uid": project_uid,
        "target_project_name": project_name,
        "target_timeline_uid": timeline_uid,
        "target_timeline_name": timeline_name,
        "target_timeline_fps_numerator": CANONICAL_FPS_NUMERATOR,
        "target_timeline_fps_denominator": CANONICAL_FPS_DENOMINATOR,
        "observed_timeline_start_frame": timeline_start_frame,
        "authorized_record_frame": record_frame,
        "source_file_path": str(clip_file),
        "source_file_sha256": clip_sha256,
        "authorized_action_type": CANONICAL_ACTION_TYPE,
        "authorized_track_type": CANONICAL_TRACK_TYPE,
        "authorized_track_index": CANONICAL_TRACK_INDEX,
        "authorized_timeline_frame_start": timeline_frame_start,
        "authorized_timeline_frame_duration": duration_frames,
        "authorized_source_frame_start": CANONICAL_SOURCE_FRAME_START,
        "authorized_source_frame_duration": duration_frames,
        "execution_id": execution_id,
        "issued_at_iso": issued_iso,
        "valid_until_iso": valid_iso,
        "token_nonce": execution_id,
        "authorization_hmac": "0" * 64,
    }

    temp_auth = TrialExecutionAuthorization(**base_auth_dict)
    payload = assemble_canonical_action_payload(action, temp_auth, execution_id=execution_id)
    canonical_action_digest = hashlib.sha256(canonical_action_payload_bytes(payload)).hexdigest()

    binder_action = BinderPlannedTimelineAction(
        action_index=0,
        action_type=CANONICAL_ACTION_TYPE,
        target_track_type=CANONICAL_TRACK_TYPE,
        target_track_index=CANONICAL_TRACK_INDEX,
        clip_id="canary_clip_item",
        source_file_path=str(clip_file),
        source_range=TimeRangeFrames(start_frame=0, end_frame=duration_frames),
        timeline_range=TimeRangeFrames(start_frame=timeline_frame_start, end_frame=timeline_frame_start + duration_frames),
        applied_speed=Fraction(1, 1),
        role="canary_lead_in",
        action_idempotency_hash=action.action_id,
        action_params={"note": "canary insert"},
    )
    target_spec = TargetSpec(
        project_name=project_name,
        project_id=project_uid,
        timeline_name=timeline_name,
        timeline_id=timeline_uid,
        timeline_fps=RationalFPS(numerator=CANONICAL_FPS_NUMERATOR, denominator=CANONICAL_FPS_DENOMINATOR),
        timeline_resolution=(1920, 1080),
        start_timecode_frame=timeline_start_frame,
        video_track_mapping={"V1": 1},
        audio_track_mapping={"A1": 1},
    )
    plan = TimelineExecutionPlan(
        plan_id="plan_canary_001",
        idempotency_key=base_auth_dict["plan_idempotency_key"],
        proposal_id=base_auth_dict["proposal_id"],
        proposal_revision=base_auth_dict["proposal_revision"],
        approval_id=base_auth_dict["approval_record_id"],
        approved_at_iso=base_auth_dict["issued_at_iso"],
        target_spec=target_spec,
        occupancy_snapshot_id="snap_canary_001",
        binding_status="BOUND_VERIFIED",
        planned_actions=(binder_action,),
        planned_actions_count=1,
        track_reservations=(),
        source_digests=(),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=duration_frames,
        total_timeline_duration_sec=Fraction(duration_frames, CANONICAL_FPS_NUMERATOR),
        issues=(),
        created_at_iso=base_auth_dict["issued_at_iso"],
    )
    plan_digest = compute_canonical_plan_digest(plan)

    base_auth_dict["action_payload_digest_sha256"] = canonical_action_digest
    base_auth_dict["plan_digest_sha256"] = "f" * 64 if tamper_plan_digest else plan_digest

    temp_auth2 = TrialExecutionAuthorization(**base_auth_dict)
    canon_bytes = canonical_bytes_for_auth_hmac(temp_auth2)
    sig = hmac.new(b"k" * 32, canon_bytes, hashlib.sha256).hexdigest()
    base_auth_dict["authorization_hmac"] = sig

    auth = TrialExecutionAuthorization(**base_auth_dict)
    object.__setattr__(auth, "challenge_nonce", challenge_nonce)
    object.__setattr__(auth, "execution_plan", plan)

    auth_digest = hashlib.sha256(canonical_bytes_for_auth_hmac(auth)).hexdigest()

    oracle_priv = Ed25519PrivateKey.generate()
    oracle_pub = oracle_priv.public_key()

    resp_auth_digest = "f" * 64 if tamper_auth_digest else auth_digest
    resp_action_digest = "e" * 64 if tamper_action_digest else canonical_action_digest
    resp_project_uid = str(uuid.uuid4()) if tamper_project_uid else project_uid
    resp_timeline_uid = str(uuid.uuid4()) if tamper_timeline_uid else timeline_uid
    resp_revision = "2" * 64 if tamper_proposal_rev else proposal_revision

    req_id = custom_request_id or str(uuid.uuid4())
    req_nonce = custom_request_nonce or ("0" * 64)

    oracle_req = OracleVerificationRequest(
        schema_version=2,
        request_id=req_id,
        request_nonce=req_nonce,
        requested_at_iso=issued_iso,
        signed_authorization=auth,
        action_payload_digest_sha256=canonical_action_digest,
        target_project_uid=project_uid,
        target_timeline_uid=timeline_uid,
        proposal_revision=proposal_revision,
    )

    dummy_resp = OracleVerificationResponse(
        schema_version=2,
        response_id=str(uuid.uuid4()),
        request_id=req_id,
        request_nonce=req_nonce,
        authorization_digest_sha256=resp_auth_digest,
        action_payload_digest_sha256=resp_action_digest,
        target_project_uid=resp_project_uid,
        target_timeline_uid=resp_timeline_uid,
        proposal_revision=resp_revision,
        verification_result=verification_result,
        is_valid=is_valid,
        reason_code=reason_code,
        verified_at_iso=issued_iso,
        valid_until_iso=valid_iso,
        oracle_signature="0" * 128,
    )
    raw_oracle_sig = oracle_priv.sign(dummy_resp.canonical_claims_bytes()).hex()
    if tamper_oracle_sig:
        last_char = "1" if raw_oracle_sig[-1] == "0" else "0"
        final_sig = raw_oracle_sig[:-1] + last_char
    else:
        final_sig = raw_oracle_sig

    oracle_resp = OracleVerificationResponse(
        schema_version=2,
        response_id=dummy_resp.response_id,
        request_id=dummy_resp.request_id,
        request_nonce=dummy_resp.request_nonce,
        authorization_digest_sha256=dummy_resp.authorization_digest_sha256,
        action_payload_digest_sha256=dummy_resp.action_payload_digest_sha256,
        target_project_uid=dummy_resp.target_project_uid,
        target_timeline_uid=dummy_resp.target_timeline_uid,
        proposal_revision=dummy_resp.proposal_revision,
        verification_result=dummy_resp.verification_result,
        is_valid=dummy_resp.is_valid,
        reason_code=dummy_resp.reason_code,
        verified_at_iso=dummy_resp.verified_at_iso,
        valid_until_iso=dummy_resp.valid_until_iso,
        oracle_signature=final_sig,
    )

    clip_item = FakeMediaPoolItem(str(clip_file))
    tl_item = FakeTimelineItem(
        start=record_frame,
        duration=duration_frames,
        end=record_frame + duration_frames,
        name=os.path.basename(str(clip_file)),
        media_pool_item=clip_item,
        unique_id="tl_item_unique_01",
    )
    tl = FakeTimeline(
        timeline_uid=timeline_uid,
        start_frame=timeline_start_frame,
        items_by_track={("video", 1): [tl_item]},
        name=timeline_name,
        unique_id=timeline_uid,
    )
    mp = FakeMediaPool(clips=[clip_item])
    proj = FakeProject(project_uid=project_uid, project_name=project_name, timeline=tl, media_pool=mp)
    client = FakeResolveClient(proj)

    return {
        "action": action,
        "auth": auth,
        "plan": plan,
        "oracle_req": oracle_req,
        "oracle_resp": oracle_resp,
        "oracle_priv": oracle_priv,
        "oracle_pub": oracle_pub,
        "client": client,
        "project_uid": project_uid,
        "timeline_uid": timeline_uid,
        "clip_file": clip_file,
        "request_id": req_id,
        "request_nonce": req_nonce,
    }


# ==============================================================================
# 1. Constructor Boundary & Dependency Decoupling Tests
# ==============================================================================

def test_constructor_requires_keyword_only_and_decoupled_dependencies(tmp_path):
    """Proves constructor enforces keyword-only signature and accepts only decoupled types."""
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    fake_client = FakeResolveClient()

    # 1. Valid decoupled construction
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=fake_client,
        pinned_oracle_public_key=pub,
        proof_ledger_dir=str(tmp_path / "proofs"),
    )
    assert adapter.journal is journal
    assert adapter.resolve_client is fake_client
    assert adapter.pinned_oracle_public_key is not None
    adapter.close()

    # 2. Positional call must raise TypeError
    with pytest.raises(TypeError):
        LiveResolveCanaryAdapter(journal, fake_client, pub)  # type: ignore[misc]

    # 3. Legacy kwargs must be rejected fail-closed
    with pytest.raises(TypeError):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=pub,
            coordinator=object(),  # type: ignore[call-arg]
        )

    with pytest.raises(TypeError):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=pub,
            TrialKeyringAuthority=object(),  # type: ignore[call-arg]
        )

    with pytest.raises(TypeError):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=pub,
            keystore_path="/tmp/keystore",  # type: ignore[call-arg]
        )

    with pytest.raises(TypeError):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=pub,
            execution_journal_service=object(),  # type: ignore[call-arg]
        )

    journal.close()


def test_constructor_rejects_invalid_types_and_subclasses(tmp_path):
    """Proves constructor validates exact backend types and normalizes public key."""
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    priv = Ed25519PrivateKey.generate()
    canonical_pub = priv.public_key()
    fake_client = FakeResolveClient()

    # 1. Non-DurableDispatchJournal journal raises ReadbackReceiptVerificationError
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_JOURNAL"):
        LiveResolveCanaryAdapter(
            journal=object(),  # type: ignore[arg-type]
            resolve_client=fake_client,
            pinned_oracle_public_key=canonical_pub,
        )

    # 2. None resolve_client raises LiveAdmissionTripwireError
    with pytest.raises(LiveAdmissionTripwireError, match="ERR_RESOLVE_CLIENT_REQUIRED"):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=None,
            pinned_oracle_public_key=canonical_pub,
        )

    # 3. Invalid key type raises ReadbackReceiptVerificationError
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_ORACLE_KEY_TYPE"):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key="not_a_key",  # type: ignore[arg-type]
        )

    # 4. Custom key subclass raises ReadbackReceiptVerificationError
    class CustomSubclassKey(Ed25519PublicKey):
        def public_bytes(self, encoding, format):
            return canonical_pub.public_bytes(encoding, format)

        def public_bytes_raw(self):
            return canonical_pub.public_bytes_raw()

        def verify(self, signature, data):
            pass

        def __eq__(self, other):
            return False

        def __copy__(self):
            return self

        def __deepcopy__(self, memo):
            return self

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_INVALID_ORACLE_KEY_TYPE"):
        LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=CustomSubclassKey(),
        )

    journal.close()


# ==============================================================================
# 2. Module Isolation & Safety Boundaries (DEP04-ADAPTER-DECOUPLE-R1-04, R1-05)
# ==============================================================================

def test_module_import_and_construction_safety_tripwires(tmp_path):
    """Proves zero Resolve SDK import, zero socket connection, zero production path access."""
    # 1. Inspect AST of live_resolve_canary_adapter
    source_file = canary_module.__file__
    assert source_file is not None and os.path.exists(source_file)

    with open(source_file, "r", encoding="utf-8") as f:
        src_text = f.read()
        tree = ast.parse(src_text, filename=source_file)

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module)

    forbidden = ["davinci", "fusionscript", "bmd", "resolve_api", "pyside", "qt"]
    for name in imported_names:
        for f in forbidden:
            assert f not in name.lower(), f"Forbidden import '{name}' in live_resolve_canary_adapter"

    # Static verification: Candidate module must not import or expose production/signer services
    assert "RecoveryCoordinator" not in imported_names
    assert "TrialKeyringAuthority" not in imported_names
    assert "signer_keystore_service" not in imported_names
    assert not hasattr(canary_module, "RecoveryCoordinator")
    assert not hasattr(canary_module, "TrialKeyringAuthority")
    assert not hasattr(LiveResolveCanaryAdapter, "_coordinator")
    assert not hasattr(LiveResolveCanaryAdapter, "_keyring")

    import inspect
    init_params = set(inspect.signature(LiveResolveCanaryAdapter.__init__).parameters.keys())
    assert "coordinator" not in init_params
    assert "TrialKeyringAuthority" not in init_params
    assert "keystore_path" not in init_params
    assert "execution_journal_service" not in init_params

    # 2. Trap socket creation: verify adapter import, construction, verification make zero socket calls
    def forbid_socket(*args, **kwargs):
        raise AssertionError("FORBIDDEN_NETWORK_CALL: LiveResolveCanaryAdapter must never open sockets!")

    # 3. Path tripwire: monkeypatch file system calls so touching /var/lib, /run, /etc raises AssertionError
    orig_stat = os.stat
    orig_open = builtins.open

    def guarded_stat(path, *args, **kwargs):
        p_str = str(path)
        for prohibited in ("/var/lib/resolveflow", "/run/resolveflow", "/etc/resolveflow"):
            if p_str.startswith(prohibited):
                raise AssertionError(f"PROHIBITED_PRODUCTION_PATH_ACCESSED: {p_str}")
        return orig_stat(path, *args, **kwargs)

    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    fake_client = FakeResolveClient()

    with patch.object(socket, "socket", side_effect=forbid_socket), patch.object(os, "stat", side_effect=guarded_stat):
        adapter = LiveResolveCanaryAdapter(
            journal=journal,
            resolve_client=fake_client,
            pinned_oracle_public_key=pub,
        )
        assert adapter is not None
        adapter.close()

    journal.close()


def test_module_import_and_execution_never_generates_private_keys():
    """Proves LiveResolveCanaryAdapter module never calls Ed25519PrivateKey.generate (DEP04-ADAPTER-DECOUPLE-R1-05)."""
    source_file = canary_module.__file__
    assert source_file is not None and os.path.exists(source_file)
    with open(source_file, "r", encoding="utf-8") as f:
        src_text = f.read()

    # 1. AST check: Zero calls to Ed25519PrivateKey.generate
    tree = ast.parse(src_text, filename=source_file)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "generate":
                if isinstance(func.value, ast.Name) and "private" in func.value.id.lower():
                    raise AssertionError(f"PROHIBITED_KEY_GENERATION: found call to {func.value.id}.generate()")

    # 2. Dynamic execution in isolated namespace with monkeypatched generate trap
    import types
    mod = types.ModuleType("isolated_canary_test")
    mod.__file__ = source_file
    with patch(
        "cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey.generate",
        side_effect=AssertionError("PROHIBITED_PRIVATE_KEY_GENERATION: module import must not generate private keys"),
    ), patch.dict(sys.modules, {"isolated_canary_test": mod}):
        exec(compile(src_text, source_file, "exec"), mod.__dict__)


# ==============================================================================
# 3. Pre-Mutation Durable Ordering & Terminal Commit Spy (DEP04-ADAPTER-DECOUPLE-R1-01)
# ==============================================================================

def test_pre_mutation_durable_sequence_and_terminal_commit_spy(tmp_path):
    """
    Proves exact durable-before-mutation sequence (§5.2.2 Steps 1-4) precedes AppendToTimeline,
    and commit_terminal precedes returning terminalized=True (DEP04-ADAPTER-DECOUPLE-R1-01).
    """
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    fake_client = bundle["client"]
    media_pool = fake_client.GetProjectManager().GetCurrentProject().GetMediaPool()

    call_order = []

    orig_write_audit_intent = journal.write_audit_intent
    orig_reserve_target = journal.reserve_target
    orig_write_action_pending = journal.write_action_pending
    orig_acquire_target_lock = journal.acquire_target_lock
    orig_append = media_pool.AppendToTimeline
    orig_commit_terminal = journal.commit_terminal
    orig_release_target_lock = journal.release_target_lock

    def spy_write_audit_intent(*args, **kwargs):
        call_order.append("write_audit_intent")
        return orig_write_audit_intent(*args, **kwargs)

    def spy_reserve_target(*args, **kwargs):
        call_order.append("reserve_target")
        return orig_reserve_target(*args, **kwargs)

    def spy_write_action_pending(*args, **kwargs):
        call_order.append("write_action_pending")
        return orig_write_action_pending(*args, **kwargs)

    def spy_acquire_target_lock(*args, **kwargs):
        call_order.append("acquire_target_lock")
        return orig_acquire_target_lock(*args, **kwargs)

    def spy_append(*args, **kwargs):
        call_order.append("AppendToTimeline")
        return orig_append(*args, **kwargs)

    def spy_commit_terminal(*args, **kwargs):
        call_order.append("commit_terminal")
        return orig_commit_terminal(*args, **kwargs)

    def spy_release_target_lock(*args, **kwargs):
        call_order.append("release_target_lock")
        return orig_release_target_lock(*args, **kwargs)

    journal.write_audit_intent = spy_write_audit_intent
    journal.reserve_target = spy_reserve_target
    journal.write_action_pending = spy_write_action_pending
    journal.acquire_target_lock = spy_acquire_target_lock
    media_pool.AppendToTimeline = spy_append
    journal.commit_terminal = spy_commit_terminal
    journal.release_target_lock = spy_release_target_lock

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=fake_client,
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    adapter.set_oracle_verification_response(
        bundle["oracle_resp"],
        bundle["oracle_req"],
    )

    res = adapter.execute_live_canary_action(
        action=bundle["action"],
        authorization=bundle["auth"],
        plan=bundle["plan"],
    )

    assert res["status"] == "COMPLETED"
    assert res["terminalized"] is True
    assert res["lock_released"] is True

    # Assert exact required order: Steps 1-4 precede mutation, commit_terminal precedes lock release
    expected_order = [
        "write_audit_intent",
        "reserve_target",
        "write_action_pending",
        "acquire_target_lock",
        "AppendToTimeline",
        "commit_terminal",
        "release_target_lock",
    ]
    assert call_order == expected_order

    # Verify journal final state: execution is committed, target lock released, ledger EXHAUSTED(COMPLETED)
    assert journal.is_execution_id_committed(bundle["auth"].execution_id) is True
    thash = journal.compute_target_hash(bundle["project_uid"], bundle["timeline_uid"])
    assert thash not in journal._active_locks
    assert journal._target_state["status"] == "EXHAUSTED"
    assert journal._target_state["terminal_result"] == "COMPLETED"

    journal.close()
    adapter.close()


def test_pre_dispatch_drift_commits_aborted_and_releases_lock(tmp_path):
    """Proves pre-dispatch drift commits ABORTED in journal and releases target lock (§5.2.2 Branch 2)."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    adapter.set_oracle_verification_response(
        bundle["oracle_resp"],
        bundle["oracle_req"],
    )

    with patch.object(
        adapter,
        "_validate_tier2_pre_ipc",
        side_effect=TargetProjectMismatchError("ERR_TARGET_PROJECT_UID_DRIFT_PRE_IPC"),
    ):
        with pytest.raises(TargetProjectMismatchError, match="ERR_TARGET_PROJECT_UID_DRIFT_PRE_IPC"):
            adapter.execute_live_canary_action(
                action=bundle["action"],
                authorization=bundle["auth"],
                plan=bundle["plan"],
            )

    # Target lock was released, execution was reserved/aborted
    thash = journal.compute_target_hash(bundle["project_uid"], bundle["timeline_uid"])
    assert thash not in journal._active_locks
    assert journal._target_state["status"] == "EXHAUSTED"
    assert journal._target_state["terminal_result"] == "ABORTED"
    assert journal.is_execution_id_committed(bundle["auth"].execution_id) is True

    journal.close()
    adapter.close()


# ==============================================================================
# 4. Oracle Verification Response Cryptographic & Correlation Verification
# ==============================================================================

def test_oracle_verification_response_valid_signature_succeeds(tmp_path):
    """Proves valid Oracle response verifies against canonical claims, request correlation, and pinned public key."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    # Direct verification with full DTOs
    verified = adapter.verify_oracle_verification_response(
        oracle_response=bundle["oracle_resp"],
        authorization=bundle["auth"],
        plan=bundle["plan"],
        action=bundle["action"],
        request=bundle["oracle_req"],
    )
    assert verified is True

    # Execution with adapter state
    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])
    res = adapter.execute_live_canary_action(
        action=bundle["action"],
        authorization=bundle["auth"],
        plan=bundle["plan"],
    )
    assert res["status"] == "COMPLETED"
    assert res["receipt_state"] == "COMPLETED"

    journal.close()
    adapter.close()


def test_oracle_verification_response_tampered_signature_fails_closed(tmp_path):
    """Proves tampered signature fails closed with ERR_ORACLE_SIGNATURE_INVALID."""
    bundle = make_test_bundle(tmp_path, tamper_oracle_sig=True)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_SIGNATURE_INVALID"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_wrong_key_fails_closed(tmp_path):
    """Proves response signed with a different key fails closed with ERR_ORACLE_SIGNATURE_INVALID."""
    bundle = make_test_bundle(tmp_path)
    different_pub = Ed25519PrivateKey.generate().public_key()
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=different_pub,
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_SIGNATURE_INVALID"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_rejected_outcome_fails_closed(tmp_path):
    """Proves response with verification_result != VERIFIED_VALID fails closed."""
    bundle = make_test_bundle(
        tmp_path,
        verification_result="VERIFICATION_FAILED",
        is_valid=False,
        reason_code="ERR_REJECTED",
    )
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_VERIFICATION_REJECTED"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_claim_mismatches_fail_closed(tmp_path):
    """Proves project UID, timeline UID, and proposal revision mismatches fail closed."""
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    # 1. Project UID mismatch
    b1 = make_test_bundle(tmp_path, tamper_project_uid=True)
    a1 = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=b1["client"],
        pinned_oracle_public_key=b1["oracle_pub"],
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_TARGET_PROJECT_MISMATCH"):
        a1.verify_oracle_verification_response(
            oracle_response=b1["oracle_resp"],
            authorization=b1["auth"],
            plan=b1["plan"],
            action=b1["action"],
            request=b1["oracle_req"],
        )
    a1.close()

    # 2. Timeline UID mismatch
    b2 = make_test_bundle(tmp_path, tamper_timeline_uid=True)
    a2 = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=b2["client"],
        pinned_oracle_public_key=b2["oracle_pub"],
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_TARGET_TIMELINE_MISMATCH"):
        a2.verify_oracle_verification_response(
            oracle_response=b2["oracle_resp"],
            authorization=b2["auth"],
            plan=b2["plan"],
            action=b2["action"],
            request=b2["oracle_req"],
        )
    a2.close()

    # 3. Proposal revision mismatch
    b3 = make_test_bundle(tmp_path, tamper_proposal_rev=True)
    a3 = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=b3["client"],
        pinned_oracle_public_key=b3["oracle_pub"],
    )
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_PROPOSAL_REVISION_MISMATCH"):
        a3.verify_oracle_verification_response(
            oracle_response=b3["oracle_resp"],
            authorization=b3["auth"],
            plan=b3["plan"],
            action=b3["action"],
            request=b3["oracle_req"],
        )
    a3.close()

    journal.close()


# ==============================================================================
# 5. Freshness, Expiry & Correlation Tests (DEP04-ADAPTER-DECOUPLE-R1-02)
# ==============================================================================

def test_oracle_verification_response_expired_fails_closed(tmp_path):
    """Proves expired Oracle response fails closed with ERR_ORACLE_RESPONSE_EXPIRED."""
    # Response valid until 5 seconds ago
    bundle = make_test_bundle(tmp_path, valid_offset_sec=-5.0, verified_offset_sec=-30.0)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_RESPONSE_EXPIRED"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_stale_fails_closed(tmp_path):
    """Proves response older than 60s fails closed with ERR_ORACLE_RESPONSE_STALE."""
    # Response was verified 120s ago, even if valid_until_iso is far in the future
    bundle = make_test_bundle(tmp_path, valid_offset_sec=300.0, verified_offset_sec=-120.0)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_RESPONSE_STALE"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_rejects_caller_controlled_now_utc(tmp_path):
    """
    Proves caller cannot bypass freshness or expiry by passing caller-controlled now_utc (DEP04-ADAPTER-DECOUPLE-R2-01).
    All methods must reject now_utc fail-closed with TypeError before any journal mutation or adapter call.
    """
    # Create an expired response (expired 10 seconds ago)
    bundle = make_test_bundle(tmp_path, valid_offset_sec=-10.0, verified_offset_sec=-30.0)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    # 1. Caller attempts to pass an artificial earlier now_utc to verify_oracle_verification_response
    with pytest.raises(TypeError, match="ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
            now_utc=datetime.now(timezone.utc) - timedelta(seconds=20),
        )

    # 2. Caller attempts to pass artificial now_utc to execute_live_canary_action
    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])
    with pytest.raises(TypeError, match="ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED"):
        adapter.execute_live_canary_action(
            action=bundle["action"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            now_utc=datetime.now(timezone.utc) - timedelta(seconds=20),
        )

    # 3. Caller attempts to pass artificial now_utc to execute_action
    with pytest.raises(TypeError, match="ERR_CALLER_CONTROLLED_CLOCK_PROHIBITED"):
        adapter.execute_action(
            action=bundle["action"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            now_utc=datetime.now(timezone.utc) - timedelta(seconds=20),
        )

    # 4. Assert zero journal mutations or locks acquired
    assert not journal.is_target_locked_or_in_doubt(bundle["project_uid"], bundle["timeline_uid"])
    assert not journal.is_execution_id_committed(bundle["auth"].execution_id)

    journal.close()
    adapter.close()


def test_constructor_injected_trusted_clock_enforces_freshness(tmp_path):
    """
    Proves trusted clock injected at construction enforces freshness deterministically,
    and cannot be overridden per-request (DEP04-ADAPTER-DECOUPLE-R2-01).
    """
    bundle = make_test_bundle(tmp_path, valid_offset_sec=30.0, verified_offset_sec=-10.0)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    # Injected clock is frozen 100 seconds in the future -> response is expired
    future_time = datetime.now(timezone.utc) + timedelta(seconds=100)
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
        clock=lambda: future_time,
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_RESPONSE_EXPIRED"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_pre_dispatch_drift_abort_commit_failure_latches_in_doubt(tmp_path):
    """Proves that if committing ABORTED terminal state fails, lock is retained and target is latched in-doubt."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )
    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])

    with patch.object(
        adapter,
        "_validate_tier2_pre_ipc",
        side_effect=TargetProjectMismatchError("ERR_TARGET_PROJECT_UID_DRIFT_PRE_IPC"),
    ), patch.object(
        journal,
        "commit_terminal",
        side_effect=OSError("Disk failure during commit_terminal(ABORTED)"),
    ):
        with pytest.raises(OSError, match="Disk failure during commit_terminal"):
            adapter.execute_live_canary_action(
                action=bundle["action"],
                authorization=bundle["auth"],
                plan=bundle["plan"],
            )

    # Lock is RETAINED, target is latched in-doubt fail-closed
    thash = journal.compute_target_hash(bundle["project_uid"], bundle["timeline_uid"])
    assert thash in journal._active_locks
    assert thash in journal._in_doubt_targets

    journal.close()
    adapter.close()


def test_oracle_verification_response_mismatched_request_id_fails_closed(tmp_path):
    """Proves mismatched request_id fails closed with ERR_ORACLE_REQUEST_ID_MISMATCH."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_REQUEST_ID_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            expected_request_id=str(uuid.uuid4()),  # Mismatched ID
            expected_request_nonce=bundle["request_nonce"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_mismatched_nonce_fails_closed(tmp_path):
    """Proves mismatched request_nonce fails closed with ERR_ORACLE_REQUEST_NONCE_MISMATCH."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_REQUEST_NONCE_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            expected_request_id=bundle["request_id"],
            expected_request_nonce="f" * 64,  # Mismatched nonce
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_missing_correlation_fails_closed(tmp_path):
    """Proves missing request correlation parameters fails closed with ERR_ORACLE_REQUEST_CORRELATION_MISSING."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_REQUEST_CORRELATION_MISSING"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
        )

    journal.close()
    adapter.close()


# ==============================================================================
# 6. Mandatory Plan Binding Tests (DEP04-ADAPTER-DECOUPLE-R1-03)
# ==============================================================================

def test_oracle_verification_response_missing_plan_fails_closed(tmp_path):
    """Proves missing plan argument fails closed with TypeError (DEP04-ADAPTER-DECOUPLE-R1-03)."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(TypeError, match="ERR_PLAN_REQUIRED"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=None,  # type: ignore[arg-type]
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_wrong_plan_type_fails_closed(tmp_path):
    """Proves wrong plan type fails closed with TypeError (DEP04-ADAPTER-DECOUPLE-R1-03)."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(TypeError, match="ERR_INVALID_PLAN_TYPE"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan="invalid_string_plan",  # type: ignore[arg-type]
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_oracle_verification_response_tampered_plan_digest_fails_closed(tmp_path):
    """Proves recomputed plan digest mismatch fails closed with ERR_PLAN_DIGEST_MISMATCH."""
    bundle = make_test_bundle(tmp_path, tamper_plan_digest=True)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_PLAN_DIGEST_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


# ==============================================================================
# 7. Recomputed Digest & Tampering Protection Tests
# ==============================================================================

def test_recomputed_action_payload_digest_tampering_rejected_before_dispatch(tmp_path):
    """Proves action payload digest is strictly recomputed and tampering fails closed before dispatch."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    # Tamper action: change role in action which enters canonical action payload
    tampered_action = PlannedTimelineAction(
        action_id=bundle["action"].action_id,
        action_index=bundle["action"].action_index,
        action_type=bundle["action"].action_type,
        track_type=bundle["action"].track_type,
        track_index=bundle["action"].track_index,
        record_frame=bundle["action"].record_frame,
        duration_frames=bundle["action"].duration_frames,
        source_path=bundle["action"].source_path,
        source_frame_start=bundle["action"].source_frame_start,
        source_frame_duration=bundle["action"].source_frame_duration,
        role="tampered_role_different",
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ACTION_DIGEST_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=tampered_action,
            request=bundle["oracle_req"],
        )

    # Verify zero journal state mutations
    assert not journal.is_target_locked_or_in_doubt(bundle["project_uid"], bundle["timeline_uid"])
    assert not journal.is_execution_id_committed(bundle["auth"].execution_id)

    journal.close()
    adapter.close()


def test_recomputed_oracle_action_digest_mismatch_fails_closed(tmp_path):
    """Proves tampering with action_payload_digest in Oracle response fails closed."""
    bundle = make_test_bundle(tmp_path, tamper_action_digest=True)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_ACTION_DIGEST_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


def test_recomputed_auth_digest_mismatch_fails_closed(tmp_path):
    """Proves tampering with authorization_digest in Oracle response fails closed."""
    bundle = make_test_bundle(tmp_path, tamper_auth_digest=True)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_ORACLE_AUTH_DIGEST_MISMATCH"):
        adapter.verify_oracle_verification_response(
            oracle_response=bundle["oracle_resp"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
            action=bundle["action"],
            request=bundle["oracle_req"],
        )

    journal.close()
    adapter.close()


# ==============================================================================
# 8. Ambiguity Classification, Target Locking & In-Doubt Latch Tests
# ==============================================================================

def test_dispatch_ambiguity_latches_in_doubt_locked(tmp_path):
    """Proves IPC dispatch failure is classified IN_DOUBT_LOCKED without retry or rollback."""
    bundle = make_test_bundle(tmp_path)
    bundle["client"].GetProjectManager().GetCurrentProject().GetMediaPool().should_raise_on_append = (
        RuntimeError("Simulated IPC dispatch socket drop")
    )

    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])

    res = adapter.execute_live_canary_action(
        action=bundle["action"],
        authorization=bundle["auth"],
        plan=bundle["plan"],
    )

    assert res["status"] == "TERMINAL_ACTION_FAILED_IN_DOUBT"
    assert res["receipt_state"] == "IN_DOUBT"
    assert res["target_lock_state"] == "IN_DOUBT_LOCKED"
    assert res["lock_retained"] is True

    # Assert target is latched in doubt in journal
    assert journal.is_target_locked_or_in_doubt(bundle["project_uid"], bundle["timeline_uid"]) is True
    thash = journal.compute_target_hash(bundle["project_uid"], bundle["timeline_uid"])
    assert thash in journal._in_doubt_targets
    assert journal._target_state["status"] == "IN_DOUBT_LOCKED"
    assert journal._target_state["in_doubt_reason"].startswith(f"incident_id={res['incident_id']}")
    # Kernel lock is retained
    assert thash in journal._active_locks

    journal.close()
    adapter.close()


def test_readback_ambiguity_latches_in_doubt_locked(tmp_path):
    """Proves physical readback verification failure latches IN_DOUBT_LOCKED and re-raises."""
    bundle = make_test_bundle(tmp_path)
    # Clear items in track so readback fails to find the appended clip
    tl = bundle["client"].GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    tl._items_by_track = {("video", 1): []}

    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")
    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])

    with pytest.raises(Exception):
        adapter.execute_live_canary_action(
            action=bundle["action"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
        )

    # Target must be latched in doubt in journal
    assert journal.is_target_locked_or_in_doubt(bundle["project_uid"], bundle["timeline_uid"]) is True

    journal.close()
    adapter.close()


def test_pre_existing_target_lock_or_committed_execution_rejects_dispatch(tmp_path):
    """Proves adapter immediately rejects dispatch if target is locked/in-doubt or already committed."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    # Latch target in doubt in journal beforehand
    journal.force_in_doubt_locked(
        target_project_uid=bundle["project_uid"],
        target_timeline_uid=bundle["timeline_uid"],
        execution_id="prior_exec_01",
        reason="Prior incident latch",
    )

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    adapter.set_oracle_verification_response(bundle["oracle_resp"], bundle["oracle_req"])

    with pytest.raises(WholePlanReconciliationError, match="ERR_TARGET_LOCKED_OR_IN_DOUBT"):
        adapter.execute_live_canary_action(
            action=bundle["action"],
            authorization=bundle["auth"],
            plan=bundle["plan"],
        )

    journal.close()
    adapter.close()


# ==============================================================================
# 9. Prohibited Legacy Methods Fail Closed
# ==============================================================================

def test_removed_legacy_signer_and_coordinator_methods_fail_closed(tmp_path):
    """Proves removed HMAC verification, coordinator, and restart repair methods fail closed."""
    bundle = make_test_bundle(tmp_path)
    journal = DurableDispatchJournal(state_dir=tmp_path / "journal")

    adapter = LiveResolveCanaryAdapter(
        journal=journal,
        resolve_client=bundle["client"],
        pinned_oracle_public_key=bundle["oracle_pub"],
    )

    # 1. verify_and_consume_proof
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNER_KEYRING_REMOVED"):
        adapter.verify_and_consume_proof(
            raw_proof={},
            action=bundle["action"],
            pending_entry=None,
            authorization=bundle["auth"],
        )

    # 2. verify_durable_proof_record
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_SIGNER_KEYRING_REMOVED"):
        adapter.verify_durable_proof_record(
            record=None,  # type: ignore[arg-type]
            action=bundle["action"],
            pending_entry=None,
            authorization=bundle["auth"],
        )

    # 3. complete_with_clean_proof
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_COORDINATOR_REMOVED"):
        adapter.complete_with_clean_proof(
            proof_record="proof_rec_01",
            authorization=bundle["auth"],
            action=bundle["action"],
        )

    # 4. execute_restart_repair
    with pytest.raises(ReadbackReceiptVerificationError, match="ERR_COORDINATOR_REMOVED"):
        adapter.execute_restart_repair(
            authorization=bundle["auth"],
            action=bundle["action"],
            plan=bundle["plan"],
        )

    journal.close()
    adapter.close()
