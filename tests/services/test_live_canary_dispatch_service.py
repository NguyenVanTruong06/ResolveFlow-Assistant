"""
Unit tests for LiveCanaryDispatchService and DurableDispatchJournal (Gate 6 DEP-03-R2).

Verifies:
- Safety tripwires: zero Resolve SDK import, zero production paths.
- DEP03-R2-01: Oracle public key type/encoding pinning and target UID/name immutability.
- DEP03-R2-02: Nominal DTO type enforcement and plan/action digest recomputation (no fallback).
- DEP03-R2-03: Oracle verification response authorization-digest and proposal-revision bindings.
- DEP03-R2-04: Nominal adapter result validation and typed pre-mutation clean-abort proof.
- DEP03-R2-05: Descriptor-anchored directory traversal and persistence (symlink rejection).
- DEP03-R2-06: Target lock stable-inode multi-process coordination (no unlinking).
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
from fractions import Fraction
import hashlib
import json
import os
import secrets
import stat
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from src.core.contracts.oracle_ipc_contracts import (
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
    DispatchJournalCorruptionError,
    DispatchJournalError,
    DispatchJournalInDoubtError,
    DispatchJournalLockError,
    DispatchJournalPoisonError,
    DispatchJournalSecurityError,
    DurableDispatchJournal,
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
    INCIDENT_TARGET_PROJECT_UID,
    INCIDENT_TARGET_TIMELINE_UID,
    LiveCanaryDispatchService,
    LiveDispatchAdapterError,
    LiveDispatchDuplicateError,
    LiveDispatchError,
    LiveDispatchInDoubtError,
    LiveDispatchOracleVerificationError,
    LiveDispatchScopeViolationError,
    LiveDispatchSecurityError,
    LiveDispatchTargetDeniedError,
    PreMutationCleanAbortError,
    PreMutationCleanAbortProof,
)
from src.services.trial_authorization_signer_service import (
    compute_canonical_action_payload_digest_for_admission,
)


# ==============================================================================
# Helper Factories
# ==============================================================================
def _make_valid_action(
    *,
    source_duration_frames: int = CANARY_SOURCE_DURATION_FRAMES,
    source_frame_start: int = 0,
    timeline_frame_start: int = 0,
    target_track_type: str = CANARY_TARGET_TRACK_TYPE,
    target_track_index: int = CANARY_TARGET_TRACK_INDEX,
    action_type: str = "INSERT_CLIP",
) -> PlannedTimelineAction:
    return PlannedTimelineAction(
        action_index=0,
        action_type=action_type,
        target_track_type=target_track_type,
        target_track_index=target_track_index,
        clip_id="canary_clip_01",
        source_file_path="/media/storage/canary_test_1080p24.mp4",
        source_range=TimeRangeFrames(source_frame_start, source_duration_frames),
        timeline_range=TimeRangeFrames(timeline_frame_start, source_duration_frames),
        applied_speed=Fraction(1, 1),
        role="canary_insertion",
        action_idempotency_hash="hash_01",
        action_params={},
    )


def _make_valid_plan(
    action: Optional[PlannedTimelineAction] = None,
    actions: Optional[List[PlannedTimelineAction]] = None,
    *,
    project_id: str = CANARY_TARGET_PROJECT_UID,
    project_name: str = CANARY_TARGET_PROJECT_NAME,
    timeline_id: str = CANARY_TARGET_TIMELINE_UID,
    timeline_name: str = CANARY_TARGET_TIMELINE_NAME,
    fps: RationalFPS = RationalFPS(24, 1),
    start_timecode_frame: int = 86400,
) -> TimelineExecutionPlan:
    if actions is not None:
        action_tuple = tuple(actions)
    else:
        act = action if action is not None else _make_valid_action()
        action_tuple = (act,)

    target_spec = TargetSpec(
        project_name=project_name,
        project_id=project_id,
        timeline_name=timeline_name,
        timeline_id=timeline_id,
        timeline_fps=fps,
        timeline_resolution=(1920, 1080),
        start_timecode_frame=start_timecode_frame,
    )
    return TimelineExecutionPlan(
        plan_id="plan_canary_01",
        idempotency_key="idemp_01",
        proposal_id="prop_01",
        proposal_revision="a" * 64,
        approval_id="appr_01",
        approved_at_iso="2026-11-04T12:00:00Z",
        target_spec=target_spec,
        occupancy_snapshot_id=None,
        binding_status="BOUND_VERIFIED",
        planned_actions=action_tuple,
        planned_actions_count=len(action_tuple),
        track_reservations=(),
        source_digests=(),
        catalog_digests=(),
        accepted_omissions=(),
        total_timeline_frames=120,
        total_timeline_duration_sec=Fraction(5, 1),
        issues=(),
        created_at_iso="2026-11-04T12:00:00Z",
    )


def _make_valid_authorization(
    plan: TimelineExecutionPlan,
    action: PlannedTimelineAction,
    *,
    execution_id: str = "exec_canary_01",
    authorization_id: str = "auth_canary_01",
    proposal_revision: str = "a" * 64,
    source_file_sha256: str = CANARY_SOURCE_MEDIA_SHA256,
) -> TrialExecutionAuthorization:
    action_digest = compute_canonical_action_payload_digest_for_admission(
        action=action,
        execution_plan=plan,
        authorization_id=authorization_id,
        execution_id=execution_id,
    )
    plan_digest = compute_canonical_plan_digest(plan)

    return TrialExecutionAuthorization(
        authorization_id=authorization_id,
        key_id="trial_auth_key_01",
        operator_gesture_digest_sha256="1" * 64,
        approval_record_id="appr_01",
        approval_record_digest_sha256="2" * 64,
        proposal_id="prop_01",
        proposal_revision=proposal_revision,
        proposal_content_digest_sha256="3" * 64,
        plan_idempotency_key=plan.idempotency_key,
        plan_digest_sha256=plan_digest,
        action_payload_digest_sha256=action_digest,
        target_project_uid=plan.target_spec.project_id,
        target_project_name=plan.target_spec.project_name,
        target_timeline_uid=plan.target_spec.timeline_id,
        target_timeline_name=plan.target_spec.timeline_name,
        target_timeline_fps_numerator=24,
        target_timeline_fps_denominator=1,
        observed_timeline_start_frame=86400,
        authorized_record_frame=CANARY_AUTHORIZED_RECORD_FRAME,
        source_file_path=action.source_file_path,
        source_file_sha256=source_file_sha256,
        authorized_action_type="INSERT_CLIP",
        authorized_track_type=action.target_track_type,
        authorized_track_index=action.target_track_index,
        authorized_timeline_frame_start=action.timeline_range.start_frame,
        authorized_timeline_frame_duration=action.timeline_range.duration_frames,
        authorized_source_frame_start=action.source_range.start_frame,
        authorized_source_frame_duration=action.source_range.duration_frames,
        execution_id=execution_id,
        issued_at_iso="2026-11-04T12:00:00Z",
        valid_until_iso="2026-11-06T12:00:00Z",
        token_nonce=execution_id,
        authorization_hmac="5" * 64,
    )


def _sign_oracle_verification_response(
    oracle_priv: Ed25519PrivateKey,
    *,
    req_id: str,
    req_nonce: str,
    auth: TrialExecutionAuthorization,
    action_digest: str,
    verified_at_iso: Optional[str] = None,
    valid_until_iso: Optional[str] = None,
    is_valid: bool = True,
    reason_code: str = "OK",
    verification_result: str = "VERIFIED_VALID",
    tampered_auth_digest: Optional[str] = None,
    tampered_proposal_revision: Optional[str] = None,
) -> OracleVerificationResponse:
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    verified_iso = verified_at_iso or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_iso = valid_until_iso or (now_utc + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    auth_claims_bytes = canonical_bytes_for_auth_hmac(auth)
    auth_digest = tampered_auth_digest or hashlib.sha256(auth_claims_bytes).hexdigest().lower()
    prop_rev = tampered_proposal_revision or auth.proposal_revision

    candidate = OracleVerificationResponse(
        schema_version=2,
        response_id=str(uuid.uuid4()),
        request_id=req_id,
        request_nonce=req_nonce,
        authorization_digest_sha256=auth_digest,
        action_payload_digest_sha256=action_digest,
        target_project_uid=auth.target_project_uid,
        target_timeline_uid=auth.target_timeline_uid,
        proposal_revision=prop_rev,
        verification_result=verification_result,
        is_valid=is_valid,
        reason_code=reason_code,
        verified_at_iso=verified_iso,
        valid_until_iso=valid_iso,
        oracle_signature="0" * 128,
    )
    sig_bytes = oracle_priv.sign(candidate.canonical_claims_bytes())
    sig_hex = sig_bytes.hex().lower()

    d = candidate.to_dict()
    d["oracle_signature"] = sig_hex
    return OracleVerificationResponse.from_dict(d)


# ==============================================================================
# Test Suite 1: Safety and Inertness Tripwires
# ==============================================================================
class TestLiveDispatchSafetyTripwires:
    def test_zero_resolve_import(self):
        modules_to_check = [
            "src/services/live_canary_dispatch_service.py",
            "src/services/durable_dispatch_journal.py",
        ]
        forbidden_substrings = ["davinci", "fusionscript", "bmd", "resolve_api"]
        for mod_path in modules_to_check:
            with open(mod_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=mod_path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for sub in forbidden_substrings:
                            assert sub not in alias.name.lower()
                elif isinstance(node, ast.ImportFrom):
                    mod_name = node.module or ""
                    for sub in forbidden_substrings:
                        assert sub not in mod_name.lower()

    def test_zero_production_path_access(self):
        assert not os.path.exists("/run/resolveflow")
        assert not os.path.exists("/var/lib/resolveflow")

    def test_fake_adapter_call_count_zero_on_invariant_failure(self, tmp_path: Path):
        fake_adapter = MagicMock()
        oracle_priv = Ed25519PrivateKey.generate()
        journal = DurableDispatchJournal(tmp_path / "journal")
        service = LiveCanaryDispatchService(
            oracle_socket_path=tmp_path / "oracle.sock",
            oracle_public_key=oracle_priv.public_key(),
            journal=journal,
            adapter=fake_adapter,
        )
        bad_action = _make_valid_action(source_duration_frames=240)
        plan = _make_valid_plan(bad_action)
        auth = _make_valid_authorization(plan, bad_action)

        with pytest.raises(LiveDispatchScopeViolationError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert fake_adapter.execute_live_canary_action.call_count == 0


# ==============================================================================
# Test Suite 2: Finding DEP03-R2-01 — Oracle Key and Target Identity Pinning
# ==============================================================================
class TestDEP03R201KeyAndTargetPinning:
    def test_reject_oracle_public_key_subclass_or_fake(self, tmp_path: Path):
        """Reject custom verifier objects or subclasses of Ed25519PublicKey."""
        class DuckTypeFakeKey:
            def verify(self, sig, data):
                return None
            def public_bytes(self, encoding, format):
                return b"0" * 32

        class SubclassedKey(Ed25519PublicKey):
            def verify(self, sig, data):
                return None
            def public_bytes(self, encoding, format):
                return b"0" * 32
            def public_bytes_raw(self):
                return b"0" * 32
            def __eq__(self, other):
                return True
            def __copy__(self):
                return self
            def __deepcopy__(self, memo):
                return self

        journal = DurableDispatchJournal(tmp_path / "j")
        with pytest.raises(LiveDispatchSecurityError, match="ERR_INVALID_ORACLE_KEY_TYPE"):
            LiveCanaryDispatchService(
                oracle_socket_path=tmp_path / "s",
                oracle_public_key=DuckTypeFakeKey(),  # type: ignore
                journal=journal,
                adapter=MagicMock(),
            )

        with pytest.raises(LiveDispatchSecurityError, match="ERR_INVALID_ORACLE_KEY_TYPE"):
            LiveCanaryDispatchService(
                oracle_socket_path=tmp_path / "s",
                oracle_public_key=SubclassedKey(),
                journal=journal,
                adapter=MagicMock(),
            )

    def test_reject_target_uid_override(self, tmp_path: Path):
        """Reject caller attempt to override expected target UIDs."""
        key = Ed25519PrivateKey.generate().public_key()
        journal = DurableDispatchJournal(tmp_path / "j")
        with pytest.raises(LiveDispatchTargetDeniedError, match="ERR_TARGET_OVERRIDE_DENIED"):
            LiveCanaryDispatchService(
                oracle_socket_path=tmp_path / "s",
                oracle_public_key=key,
                journal=journal,
                adapter=MagicMock(),
                expected_target_project_uid="alternate_project_uid",
            )

    def test_reject_mismatched_target_names(self, tmp_path: Path):
        """Reject mismatched project name or timeline name in auth before adapter invocation."""
        fake_adapter = MagicMock()
        oracle_priv = Ed25519PrivateKey.generate()
        journal = DurableDispatchJournal(tmp_path / "j")
        service = LiveCanaryDispatchService(
            oracle_socket_path=tmp_path / "s",
            oracle_public_key=oracle_priv.public_key(),
            journal=journal,
            adapter=fake_adapter,
        )
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        bad_auth = dataclasses.replace(
            _make_valid_authorization(plan, act),
            target_project_name="UNAPPROVED_PROJECT_NAME",
        )
        with pytest.raises(LiveDispatchTargetDeniedError, match="ERR_TARGET_PROJECT_NAME_MISMATCH"):
            service.dispatch_live_canary(auth=bad_auth, plan=plan)

        assert fake_adapter.execute_live_canary_action.call_count == 0


# ==============================================================================
# Test Suite 3: Finding DEP03-R2-02 — Nominal DTO & Non-Fallback Digest Recomputation
# ==============================================================================
class TestDEP03R202NominalDTOAndDigests:
    @pytest.fixture
    def setup_service(self, tmp_path: Path):
        fake_adapter = MagicMock()
        oracle_priv = Ed25519PrivateKey.generate()
        journal = DurableDispatchJournal(tmp_path / "j")
        service = LiveCanaryDispatchService(
            oracle_socket_path=tmp_path / "s",
            oracle_public_key=oracle_priv.public_key(),
            journal=journal,
            adapter=fake_adapter,
        )
        return service, fake_adapter

    def test_reject_wrong_auth_dto_type(self, setup_service):
        service, fake_adapter = setup_service
        plan = _make_valid_plan()

        class DuckAuth:
            pass

        with pytest.raises(LiveDispatchScopeViolationError, match="ERR_INVALID_AUTH_TYPE"):
            service.dispatch_live_canary(auth=DuckAuth(), plan=plan)  # type: ignore
        assert fake_adapter.execute_live_canary_action.call_count == 0

    def test_reject_wrong_plan_dto_type(self, setup_service):
        service, fake_adapter = setup_service
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        auth = _make_valid_authorization(plan, act)

        class DuckPlan:
            pass

        with pytest.raises(LiveDispatchScopeViolationError, match="ERR_INVALID_PLAN_TYPE"):
            service.dispatch_live_canary(auth=auth, plan=DuckPlan())  # type: ignore
        assert fake_adapter.execute_live_canary_action.call_count == 0

    def test_reject_tampered_plan_digest(self, setup_service):
        """Plan-only tampering causes plan digest mismatch with zero adapter calls."""
        service, fake_adapter = setup_service
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        bad_auth = dataclasses.replace(_make_valid_authorization(plan, act), plan_digest_sha256="0" * 64)

        with pytest.raises(LiveDispatchScopeViolationError, match="ERR_PLAN_DIGEST_MISMATCH"):
            service.dispatch_live_canary(auth=bad_auth, plan=plan)

        assert fake_adapter.execute_live_canary_action.call_count == 0
        assert not service.journal.is_execution_id_committed(bad_auth.execution_id)

    def test_reject_tampered_action_digest(self, setup_service):
        """Action digest mismatch fails closed with zero adapter calls."""
        service, fake_adapter = setup_service
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        bad_auth = dataclasses.replace(_make_valid_authorization(plan, act), action_payload_digest_sha256="0" * 64)

        with pytest.raises(LiveDispatchScopeViolationError, match="ERR_ACTION_DIGEST_MISMATCH"):
            service.dispatch_live_canary(auth=bad_auth, plan=plan)

        assert fake_adapter.execute_live_canary_action.call_count == 0
        assert not service.journal.is_execution_id_committed(bad_auth.execution_id)


# ==============================================================================
# Test Suite 4: Finding DEP03-R2-03 — Oracle Response Auth Digest & Proposal Revision
# ==============================================================================
class TestDEP03R203OracleResponseBindings:
    @pytest.fixture
    def setup_gate(self, tmp_path: Path):
        fake_adapter = MagicMock()
        oracle_priv = Ed25519PrivateKey.generate()
        journal = DurableDispatchJournal(tmp_path / "j")
        service = LiveCanaryDispatchService(
            oracle_socket_path=tmp_path / "s",
            oracle_public_key=oracle_priv.public_key(),
            journal=journal,
            adapter=fake_adapter,
        )
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        auth = _make_valid_authorization(plan, act)
        computed_digest = compute_canonical_action_payload_digest_for_admission(
            action=act, execution_plan=plan, authorization_id=auth.authorization_id, execution_id=auth.execution_id
        )
        req = OracleVerificationRequest(
            schema_version=2,
            request_id=str(uuid.uuid4()),
            request_nonce=secrets.token_hex(32),
            requested_at_iso="2026-11-04T12:00:00Z",
            signed_authorization=auth,
            action_payload_digest_sha256=computed_digest,
            target_project_uid=auth.target_project_uid,
            target_timeline_uid=auth.target_timeline_uid,
            proposal_revision=auth.proposal_revision,
        )
        return service, oracle_priv, req, auth, plan, act, computed_digest, fake_adapter

    def test_reject_tampered_authorization_digest(self, setup_gate):
        """Signed Oracle response with tampered authorization_digest_sha256 fails closed."""
        service, oracle_priv, req, auth, plan, action, digest, fake_adapter = setup_gate
        resp = _sign_oracle_verification_response(
            oracle_priv,
            req_id=req.request_id,
            req_nonce=req.request_nonce,
            auth=auth,
            action_digest=digest,
            tampered_auth_digest="e" * 64,
        )
        with pytest.raises(LiveDispatchOracleVerificationError, match="ERR_ORACLE_AUTH_DIGEST_MISMATCH"):
            service._verify_oracle_verification_response(
                req=req, auth=auth, plan=plan, action=action, computed_action_digest=digest,
                response_bytes=resp.canonical_bytes()
            )

    def test_reject_mismatched_proposal_revision(self, setup_gate):
        """Signed Oracle response with mismatched proposal_revision fails closed."""
        service, oracle_priv, req, auth, plan, action, digest, fake_adapter = setup_gate
        resp = _sign_oracle_verification_response(
            oracle_priv,
            req_id=req.request_id,
            req_nonce=req.request_nonce,
            auth=auth,
            action_digest=digest,
            tampered_proposal_revision="f" * 64,
        )
        with pytest.raises(LiveDispatchOracleVerificationError, match="ERR_ORACLE_PROPOSAL_REVISION_MISMATCH"):
            service._verify_oracle_verification_response(
                req=req, auth=auth, plan=plan, action=action, computed_action_digest=digest,
                response_bytes=resp.canonical_bytes()
            )


# ==============================================================================
# Test Suite 5: Finding DEP03-R2-04 — Strict Adapter Result & Typed Clean Abort
# ==============================================================================
class TestDEP03R204AdapterOutcomeClassification:
    @pytest.fixture
    def setup_dispatcher(self, tmp_path: Path, monkeypatch):
        fake_adapter = MagicMock()
        oracle_priv = Ed25519PrivateKey.generate()
        journal = DurableDispatchJournal(tmp_path / "j")
        service = LiveCanaryDispatchService(
            oracle_socket_path=tmp_path / "s",
            oracle_public_key=oracle_priv.public_key(),
            journal=journal,
            adapter=fake_adapter,
        )
        act = _make_valid_action()
        plan = _make_valid_plan(act)
        auth = _make_valid_authorization(plan, act)

        # Mock socket IPC to return valid verification response and valid terminal response
        digest = compute_canonical_action_payload_digest_for_admission(
            action=act, execution_plan=plan, authorization_id=auth.authorization_id, execution_id=auth.execution_id
        )

        def mock_send(req_bytes: bytes) -> bytes:
            data = json.loads(req_bytes)
            if "terminal_state" in data:
                term_resp = OracleTerminalNotificationResponse(
                    schema_version=2,
                    response_id=str(uuid.uuid4()),
                    request_id=data["request_id"],
                    request_nonce=data["request_nonce"],
                    execution_id=data["execution_id"],
                    terminal_state=data["terminal_state"],
                    status="ACCEPTED",
                    reason_code="OK",
                    recorded_at_iso="2026-11-04T12:00:00Z",
                    oracle_signature="0" * 128,
                )
                sig = oracle_priv.sign(term_resp.canonical_claims_bytes()).hex().lower()
                d = term_resp.to_dict()
                d["oracle_signature"] = sig
                return OracleTerminalNotificationResponse.from_dict(d).canonical_bytes()
            else:
                ver_resp = _sign_oracle_verification_response(
                    oracle_priv, req_id=data["request_id"], req_nonce=data["request_nonce"],
                    auth=auth, action_digest=digest
                )
                return ver_resp.canonical_bytes()

        monkeypatch.setattr(service, "_send_oracle_request", mock_send)
        return service, fake_adapter, auth, plan, act

    def test_adapter_returns_none_forces_in_doubt(self, setup_dispatcher):
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = None

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_adapter_returns_non_dict_forces_in_doubt(self, setup_dispatcher):
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = "STATUS_OK"

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_adapter_mismatched_receipt_claims_forces_in_doubt(self, setup_dispatcher):
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = {
            "status": "COMPLETED",
            "receipt_state": "COMPLETED",
            "execution_id": "wrong_execution_id",
            "timeline_uid": auth.target_timeline_uid,
            "action_id": act.action_idempotency_hash,
            "receipt_id": "rcpt_1",
            "terminalized": True,
        }

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_forged_abort_exception_forces_in_doubt(self, setup_dispatcher):
        """Untyped exception containing PRE_DISPATCH_ABORT or ZeroMutationProven is classified as IN_DOUBT."""
        service, fake_adapter, auth, plan, act = setup_dispatcher

        class FakeZeroMutationProvenError(RuntimeError):
            pass

        fake_adapter.execute_live_canary_action.side_effect = FakeZeroMutationProvenError(
            "PRE_DISPATCH_ABORT: I claim zero mutation"
        )

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        # Proves target lock is RETAINED and not falsely aborted
        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_adapter_clean_abort_attempt_forces_in_doubt_and_retains_lock(self, setup_dispatcher):
        """DEP03-R4-01: Adapter execution exceptions cannot prove zero mutation; classified as IN_DOUBT with lock retained."""
        service, fake_adapter, auth, plan, act = setup_dispatcher

        def abort_pre_mutation(*args, **kwargs):
            cap = kwargs.get("clean_abort_capability") or getattr(fake_adapter, "clean_abort_capability", None)
            assert cap is not None
            raise cap.create_clean_abort_error("Preflight check failed before mutation boundary")

        fake_adapter.execute_live_canary_action.side_effect = abort_pre_mutation

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        # Proves target lock is RETAINED and marked IN_DOUBT (no ABORTED receipt or premature unlock)
        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True
        assert service.journal._target_state.get("status") == "IN_DOUBT_LOCKED"
        assert service.journal._target_state.get("terminal_result") == "IN_DOUBT"

    def test_post_mutation_clean_abort_attempt_without_voluntary_marker_forces_in_doubt(self, setup_dispatcher):
        """DEP03-R4-01: Adapter crosses mutation without calling voluntary marker and attempts abort proof; forces IN_DOUBT, lock retained."""
        service, fake_adapter, auth, plan, act = setup_dispatcher

        def mutate_and_abort(*args, **kwargs):
            cap = kwargs.get("clean_abort_capability") or getattr(fake_adapter, "clean_abort_capability", None)
            assert cap is not None
            # Adapter executes mutation WITHOUT calling cap.enter_mutation_boundary()
            # Then attempts to create and raise clean-abort proof using the capability
            raise cap.create_clean_abort_error("Attempting clean abort after unnotified mutation")

        fake_adapter.execute_live_canary_action.side_effect = mutate_and_abort

        with pytest.raises(LiveDispatchInDoubtError):
            service.dispatch_live_canary(auth=auth, plan=plan)

        # Target lock must be RETAINED and marked IN_DOUBT; no ABORTED receipt committed
        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_abort_unexecuted_dispatch_commits_aborted_and_releases_lock(self, setup_dispatcher):
        """Clean pre-execution abort before crossing adapter boundary commits ABORTED and cleanly releases lock."""
        service, fake_adapter, auth, plan, act = setup_dispatcher

        receipt = service.abort_unexecuted_dispatch(
            auth=auth,
            plan=plan,
            reason="Operator aborted before adapter dispatch",
        )

        assert receipt["status"] == "ABORTED"
        assert receipt["execution_id"] == auth.execution_id
        assert fake_adapter.execute_live_canary_action.call_count == 0

        # Lock was released cleanly
        thash = service.journal.compute_target_hash(auth.target_project_uid, auth.target_timeline_uid)
        assert thash not in service.journal._active_locks

    def test_adapter_result_missing_action_binding_forces_in_doubt(self, setup_dispatcher):
        """DEP03-R3-02: Missing action_id and action_idempotency_hash in adapter result forces IN_DOUBT, lock retained."""
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = {
            "status": "COMPLETED",
            "receipt_state": "COMPLETED",
            "execution_id": auth.execution_id,
            "timeline_uid": auth.target_timeline_uid,
            # action_id and action_idempotency_hash omitted
            "receipt_id": "rcpt_valid_01",
            "terminalized": True,
        }

        with pytest.raises(LiveDispatchInDoubtError, match="missing mandatory action binding"):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_adapter_result_mismatched_action_binding_forces_in_doubt(self, setup_dispatcher):
        """DEP03-R3-02: Mismatched action_id in adapter result forces IN_DOUBT, lock retained."""
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = {
            "status": "COMPLETED",
            "receipt_state": "COMPLETED",
            "execution_id": auth.execution_id,
            "timeline_uid": auth.target_timeline_uid,
            "action_id": "substituted_action_id_007",
            "receipt_id": "rcpt_valid_01",
            "terminalized": True,
        }

        with pytest.raises(LiveDispatchInDoubtError, match="action_id mismatch"):
            service.dispatch_live_canary(auth=auth, plan=plan)

        assert service.journal.is_target_locked_or_in_doubt(auth.target_project_uid, auth.target_timeline_uid) is True

    def test_valid_adapter_result_commits_completed(self, setup_dispatcher):
        """Strictly valid adapter result commits COMPLETED and releases target lock."""
        service, fake_adapter, auth, plan, act = setup_dispatcher
        fake_adapter.execute_live_canary_action.return_value = {
            "status": "COMPLETED",
            "receipt_state": "COMPLETED",
            "execution_id": auth.execution_id,
            "timeline_uid": auth.target_timeline_uid,
            "action_id": act.action_idempotency_hash,
            "receipt_id": "rcpt_valid_01",
            "terminalized": True,
        }

        receipt = service.dispatch_live_canary(auth=auth, plan=plan)
        assert receipt["status"] == "COMPLETED"
        assert receipt["execution_id"] == auth.execution_id
        thash = service.journal.compute_target_hash(auth.target_project_uid, auth.target_timeline_uid)
        assert thash not in service.journal._active_locks


# ==============================================================================
# Test Suite 6: Finding DEP03-R2-05 — Descriptor-Anchored Journal Persistence
# ==============================================================================
class TestDEP03R205DescriptorAnchoredJournal:
    def test_reject_symlink_state_dir(self, tmp_path: Path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir(mode=0o700)
        symlink_dir = tmp_path / "sym_dir"
        os.symlink(real_dir, symlink_dir)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_STATE_DIR_SYMLINK"):
            DurableDispatchJournal(symlink_dir)

    def test_reject_intermediate_symlink(self, tmp_path: Path):
        base_dir = tmp_path / "base"
        base_dir.mkdir(mode=0o700)
        sym_base = tmp_path / "sym_base"
        os.symlink(base_dir, sym_base)

        target_dir = sym_base / "child_journal"
        with pytest.raises(DispatchJournalSecurityError, match="ERR_STATE_DIR_SYMLINK"):
            DurableDispatchJournal(target_dir)

    def test_descriptor_anchored_wal_and_ledger(self, tmp_path: Path):
        journal = DurableDispatchJournal(tmp_path / "j")
        assert journal._state_dir_fd is not None
        assert journal._locks_dir_fd is not None

        # Write intent, reserve, wal
        journal.write_audit_intent(
            execution_id="e1", action_payload_digest_sha256="1" * 64,
            target_project_uid="p1", target_timeline_uid="t1"
        )
        journal.reserve_target(execution_id="e1", target_project_uid="p1", target_timeline_uid="t1")
        journal.write_action_pending(
            execution_id="e1", action_payload_digest_sha256="1" * 64,
            target_project_uid="p1", target_timeline_uid="t1"
        )

        assert journal.is_execution_id_committed("e1") is True
        journal.close()
        assert journal._state_dir_fd is None
        assert journal._locks_dir_fd is None


# ==============================================================================
# Test Suite 7: Finding DEP03-R2-06 — Target Lock Inode Stability
# ==============================================================================
class TestDEP03R206TargetLockInodeStability:
    def test_lock_inode_remains_stable_and_not_unlinked(self, tmp_path: Path):
        """Lock file is never unlinked; inode remains stable across acquire/release cycles."""
        state_dir = tmp_path / "j"
        journal = DurableDispatchJournal(state_dir)
        thash = journal.compute_target_hash("p1", "t1")

        # Acquire lock
        journal.acquire_target_lock("p1", "t1")
        lock_path = state_dir / ".locks" / f"{thash}.lock"
        assert lock_path.exists()
        ino_1 = lock_path.stat().st_ino

        # Release lock
        journal.release_target_lock("p1", "t1")
        # Inode MUST still exist on disk!
        assert lock_path.exists()
        ino_2 = lock_path.stat().st_ino
        assert ino_1 == ino_2

        # Re-acquire lock
        journal.acquire_target_lock("p1", "t1")
        ino_3 = lock_path.stat().st_ino
        assert ino_1 == ino_3
        journal.release_target_lock("p1", "t1")
        journal.close()

    def test_deterministic_multi_instance_coordination_on_same_inode(self, tmp_path: Path):
        """Two dispatcher instances coordinate on the exact same inode; contention is deterministic."""
        state_dir = tmp_path / "j"
        j1 = DurableDispatchJournal(state_dir)
        j2 = DurableDispatchJournal(state_dir)

        thash = j1.compute_target_hash("p1", "t1")
        j1.acquire_target_lock("p1", "t1")

        lock_path = state_dir / ".locks" / f"{thash}.lock"
        assert lock_path.exists()
        st_j1 = lock_path.stat()

        # j2 attempt to acquire fails immediately
        with pytest.raises(DispatchJournalLockError, match="ERR_TARGET_LOCK_CONTENTION"):
            j2.acquire_target_lock("p1", "t1")

        # j1 releases
        j1.release_target_lock("p1", "t1")

        # j2 acquires successfully on the EXACT SAME INODE
        j2.acquire_target_lock("p1", "t1")
        st_j2 = lock_path.stat()
        assert st_j1.st_ino == st_j2.st_ino

        j2.release_target_lock("p1", "t1")
        j1.close()
        j2.close()


# ==============================================================================
# Test Suite 8: Finding DEP03-R3-03 — Exact Node Modes (0700 / 0600) Enforcement
# ==============================================================================
class TestDEP03R303NodeModesEnforcement:
    def test_locks_directory_unsafe_mode_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing .locks directory with mode 0755 is rejected."""
        state_dir = tmp_path / "journal_unsafe_locks"
        state_dir.mkdir(mode=0o700)
        locks_dir = state_dir / ".locks"
        locks_dir.mkdir(mode=0o755)
        locks_dir.chmod(0o755)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_DIR_MODE"):
            DurableDispatchJournal(state_dir)

    def test_wal_file_unsafe_mode_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing WAL file with permissive mode 0644 is rejected."""
        state_dir = tmp_path / "journal_unsafe_wal"
        journal = DurableDispatchJournal(state_dir)
        wal_path = state_dir / journal.WAL_FILE_NAME
        wal_path.write_text("{\"test\": 1}\n")
        wal_path.chmod(0o644)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_FILE_MODE"):
            journal.write_action_pending(
                execution_id="ex1",
                action_payload_digest_sha256="0" * 64,
                target_project_uid="p1",
                target_timeline_uid="t1",
            )
        journal.close()

    def test_audit_log_unsafe_mode_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing audit log file with permissive mode 0644 is rejected."""
        state_dir = tmp_path / "journal_unsafe_audit"
        journal = DurableDispatchJournal(state_dir)
        audit_path = state_dir / journal.AUDIT_LOG_NAME
        audit_path.write_text("{\"intent\": 1}\n")
        audit_path.chmod(0o644)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_FILE_MODE"):
            journal.write_audit_intent(
                execution_id="ex1",
                action_payload_digest_sha256="0" * 64,
                target_project_uid="p1",
                target_timeline_uid="t1",
            )
        journal.close()

    def test_atomic_publish_unsafe_existing_file_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing state/ledger file with mode 0644 is rejected before replacement."""
        state_dir = tmp_path / "journal_unsafe_ledger"
        journal = DurableDispatchJournal(state_dir)
        ledger_path = state_dir / "target_ledger.v2.json"
        ledger_path.write_text("{}")
        ledger_path.chmod(0o644)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_FILE_MODE"):
            journal.reserve_target(
                execution_id="ex1",
                target_project_uid="p1",
                target_timeline_uid="t1",
            )
        journal.close()

    def test_lock_file_unsafe_mode_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing lock file with mode 0644 is rejected on acquire."""
        state_dir = tmp_path / "journal_unsafe_lock"
        journal = DurableDispatchJournal(state_dir)
        thash = journal.compute_target_hash("p1", "t1")
        lock_path = state_dir / ".locks" / f"{thash}.lock"
        lock_path.write_text("")
        lock_path.chmod(0o644)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_FILE_MODE"):
            journal.acquire_target_lock("p1", "t1")
        journal.close()

    def test_in_doubt_marker_unsafe_mode_rejected(self, tmp_path: Path):
        """DEP03-R3-03: Pre-existing in_doubt marker file with mode 0644 is rejected on startup."""
        state_dir = tmp_path / "journal_unsafe_marker"
        state_dir.mkdir(mode=0o700)
        locks_dir = state_dir / ".locks"
        locks_dir.mkdir(mode=0o700)
        marker_path = locks_dir / "abc123.in_doubt"
        marker_path.write_text("{}")
        marker_path.chmod(0o644)

        with pytest.raises(DispatchJournalSecurityError, match="ERR_UNSAFE_FILE_MODE"):
            DurableDispatchJournal(state_dir)

