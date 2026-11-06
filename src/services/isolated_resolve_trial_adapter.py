"""
src/services/isolated_resolve_trial_adapter.py
Isolated Mock-Only Trial Adapter & Gateway Integration for Isolated Resolve Gate (G07-07B6-I4).

Architectural Reference:
  - docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md (Section 2.3, 2.5, 2.6)
  - docs/architecture/07B6-mock-only-implementation-plan.md (Section 3.4, Slice I4)
  - Workorder: .bridge/task.md (G07-07B6-I4)

Safety Commitments:
  - 100% Mock-only isolated execution; zero live Resolve API, zero IPC, zero FusionScript, zero network, zero AI.
  - Mock gateway tripwire enforcement: rejects gateways lacking approved mock marker or containing live attributes.
  - Strict pre-dispatch ordering: tripwire -> coordinator authorization binding -> target lock check ->
    action assembly -> digest equality gate -> durable START/PENDING WAL records.
  - Pre-dispatch clean rejection: zero gateway calls, zero pending records, releases caller-owned target lock cleanly.
  - Post-dispatch uncertainty: on gateway exception after PENDING is durable, appends ACTION_FAILED, marks
    timeline IN_DOUBT_LOCKED on coordinator, strictly retains target exclusion lock (no clean release, no FINISH).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.contracts.execution_receipt_contracts import (
    JournalEntry,
)
from src.core.contracts.trial_authorization_contracts import (
    AcceptanceGateError,
    AuthorizationContract,
    PlannedTimelineAction,
    ReadbackReceiptVerificationError,
    TargetExclusionLockError,
    TargetLockReleaseError,
    TrialExecutionAuthorization,
    WholePlanReconciliationError,
    assemble_canonical_action_payload,
    canonical_action_payload_bytes,
)
from src.services.durable_trial_target_ledger import RecoveryCoordinator
from src.services.execution_journal_service import ExecutionJournalService


class MockGatewayAdmissionError(PermissionError, AcceptanceGateError):
    """Raised when a gateway fails mock-only prototype admission tripwires."""
    pass


class FakeResolveGateway:
    """
    Deterministic mock gateway satisfying ResolveGatewayClientProtocol for isolated trial testing.
    Exposes _is_approved_mock_gateway = True.
    Strictly forbids _automation, resolve, or _resolve_override attributes.
    Performs zero external or real Resolve calls.
    """
    _is_approved_mock_gateway: bool = True

    def __init__(
        self,
        configured_exception: Optional[Exception] = None,
        mock_item_id: str = "mock_item_001",
    ) -> None:
        self.configured_exception = configured_exception
        self.mock_item_id = mock_item_id
        self.call_count: int = 0
        self.dispatched_actions: List[PlannedTimelineAction] = []

    def query_active_timeline_authority(self) -> Dict[str, Any]:
        """Mock active timeline authority."""
        return {
            "project_name": "MockProject",
            "project_uid": "mock_proj_uid",
            "timeline_name": "MockTimeline",
            "timeline_uid": "mock_timeline_uid",
        }

    def query_track_occupancy_snapshot(self, target_spec: Any) -> Any:
        """Mock track occupancy snapshot query."""
        return None

    def execute_action(
        self,
        action: PlannedTimelineAction,
        timeout_sec: float = 15.0,
    ) -> Dict[str, Any]:
        """Dispatches action to the mock gateway."""
        self.call_count += 1
        self.dispatched_actions.append(action)
        if self.configured_exception is not None:
            raise self.configured_exception
        return {
            "success": True,
            "resolve_item_id": self.mock_item_id,
            "action_id": action.action_id,
            "action_index": action.action_index,
        }


class IsolatedResolveTrialAdapter:
    """
    Isolated Mock-Only Trial Adapter (G07-07B6-I4).
    Connects accepted authorization contracts, coordinator target locks,
    and ExecutionJournalService with a deterministic mock gateway.
    """

    def __init__(
        self,
        coordinator: RecoveryCoordinator,
        journal: ExecutionJournalService,
        fake_gateway: Any,
    ) -> None:
        if not isinstance(coordinator, RecoveryCoordinator):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_COORDINATOR: coordinator must be an instance of RecoveryCoordinator"
            )
        if not isinstance(journal, ExecutionJournalService):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_JOURNAL_SERVICE: journal must be an instance of ExecutionJournalService"
            )

        self._coordinator = coordinator
        self._journal = journal
        self._fake_gateway = fake_gateway
        self.validate_mock_tripwire()

    @property
    def coordinator(self) -> RecoveryCoordinator:
        return self._coordinator

    @property
    def journal(self) -> ExecutionJournalService:
        return self._journal

    @property
    def fake_gateway(self) -> Any:
        return self._fake_gateway

    def validate_mock_tripwire(self) -> bool:
        """
        Validates mock-only gateway boundary (I4-G1, I4-01).
        Enforces exact approved fake implementation, authentic bound dispatch method,
        approved mock marker, and prohibits live Resolve attributes.
        """
        # 1. Exact approved class enforcement (I4-01: non-forgeable adapter-owned check)
        if not isinstance(self._fake_gateway, FakeResolveGateway) or type(self._fake_gateway) is not FakeResolveGateway:
            raise MockGatewayAdmissionError(
                f"ERR_LIVE_ADAPTER_REJECTED: fake_gateway must be an exact instance of FakeResolveGateway, got {type(self._fake_gateway).__name__}"
            )

        # 2. Approved mock marker check
        if getattr(self._fake_gateway, "_is_approved_mock_gateway", None) is not True:
            raise MockGatewayAdmissionError(
                "ERR_LIVE_ADAPTER_REJECTED: fake_gateway must have _is_approved_mock_gateway=True"
            )

        # 3. Authentic dispatch method binding check
        execute_fn = getattr(self._fake_gateway, "execute_action", None)
        if not callable(execute_fn):
            raise MockGatewayAdmissionError(
                "ERR_LIVE_ADAPTER_REJECTED: fake_gateway must implement callable execute_action"
            )
        if getattr(execute_fn, "__func__", None) is not FakeResolveGateway.execute_action:
            raise MockGatewayAdmissionError(
                "ERR_LIVE_ADAPTER_REJECTED: fake_gateway.execute_action must be the authentic unbound FakeResolveGateway method"
            )

        # 4. Forbidden live attributes check
        for forbidden in ("_automation", "resolve", "_resolve_override"):
            if hasattr(self._fake_gateway, forbidden):
                raise MockGatewayAdmissionError(
                    f"ERR_LIVE_ADAPTER_REJECTED: fake_gateway contains forbidden live Resolve attribute '{forbidden}'"
                )
        return True

    def execute_isolated_action(
        self,
        action: PlannedTimelineAction,
        authorization: TrialExecutionAuthorization,
    ) -> Dict[str, Any]:
        """
        Public gated execution of a planned timeline action under trial authorization.
        Follows strict pre-dispatch ordering, durable WAL persistence, and post-dispatch in-doubt latch.
        """
        # 1. Validate mock gateway tripwire (I4-01)
        self.validate_mock_tripwire()

        if not isinstance(authorization, TrialExecutionAuthorization):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_AUTH_OBJECT: authorization must be an instance of TrialExecutionAuthorization"
            )
        if not isinstance(action, PlannedTimelineAction):
            raise ReadbackReceiptVerificationError(
                "ERR_INVALID_ACTION_OBJECT: action must be an instance of PlannedTimelineAction"
            )

        timeline_uid = authorization.target_timeline_uid
        execution_id = authorization.execution_id
        project_uid = authorization.target_project_uid
        journal_path = self._journal.journal_path(execution_id)
        durable_ledger = self._coordinator.durable_ledger

        # ======================================================================
        # PHASE 1A: Pre-existing History & Durable Ledger Gating (I4-R2-01, I4-R2-02)
        # If journal file already exists on disk, prior history is present.
        # As per I4-R2-01: preserve target exclusion lock (DO NOT release)
        # and reject before any new dispatch is possible.
        # ======================================================================
        if os.path.exists(journal_path):
            raise WholePlanReconciliationError(
                f"ERR_JOURNAL_ALREADY_EXISTS: journal for execution_id '{execution_id}' already has prior history; "
                f"target exclusion lock strictly preserved"
            )

        # Consult durable target ledger state across restarts (I4-R2-02, I4-R3-02)
        if durable_ledger is not None:
            existing_ledger = durable_ledger.get_ledger(project_uid, timeline_uid)
            if existing_ledger is not None:
                if existing_ledger.state == "EXHAUSTED":
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_LEDGER_EXHAUSTED: target '{timeline_uid}' is permanently exhausted; "
                        f"target exclusion lock strictly preserved"
                    )
                if existing_ledger.state == "ACTIVE":
                    raise WholePlanReconciliationError(
                        f"ERR_TARGET_ALREADY_ACTIVE: target '{timeline_uid}' is in active/in-doubt state under "
                        f"execution_id '{existing_ledger.execution_id}'; fresh lock acquisition/dispatch blocked"
                    )
                if existing_ledger.state == "RESERVED":
                    # Full identity tuple comparison against incoming authorization (I4-R3-02)
                    mismatches: List[str] = []
                    if existing_ledger.execution_id != execution_id:
                        mismatches.append(f"execution_id: expected {existing_ledger.execution_id}, got {execution_id}")
                    if existing_ledger.authorization_id != authorization.authorization_id:
                        mismatches.append(f"authorization_id: expected {existing_ledger.authorization_id}, got {authorization.authorization_id}")
                    if existing_ledger.approval_id != authorization.approval_record_id:
                        mismatches.append(f"approval_id: expected {existing_ledger.approval_id}, got {authorization.approval_record_id}")
                    if existing_ledger.proposal_id != authorization.proposal_id:
                        mismatches.append(f"proposal_id: expected {existing_ledger.proposal_id}, got {authorization.proposal_id}")
                    if existing_ledger.proposal_revision != authorization.proposal_revision:
                        mismatches.append(f"proposal_revision: expected {existing_ledger.proposal_revision}, got {authorization.proposal_revision}")
                    if existing_ledger.plan_idempotency_key != authorization.plan_idempotency_key:
                        mismatches.append(f"plan_idempotency_key: expected {existing_ledger.plan_idempotency_key}, got {authorization.plan_idempotency_key}")
                    if existing_ledger.target_project_uid != project_uid:
                        mismatches.append(f"target_project_uid: expected {existing_ledger.target_project_uid}, got {project_uid}")
                    if existing_ledger.target_timeline_uid != timeline_uid:
                        mismatches.append(f"target_timeline_uid: expected {existing_ledger.target_timeline_uid}, got {timeline_uid}")

                    if mismatches:
                        raise WholePlanReconciliationError(
                            f"ERR_TARGET_LEDGER_IDENTITY_MISMATCH: existing RESERVED ledger identity does not match incoming authorization: "
                            f"{'; '.join(mismatches)}; target exclusion lock strictly preserved"
                        )

        # ======================================================================
        # PHASE 1B: Pure Pre-Journal Validation Phase (I4-03, I4-05, I4-R2-01)
        # In this phase, zero journal records and zero ledger entries exist for this invocation.
        # If any validation fails here, the state is proven clean (no prior journal or ledger).
        # We cleanly release caller's target lock if held, and propagate the error.
        # ======================================================================
        try:
            # 2. Coordinator authorization binding gate (verifies 32 signed fields & HMAC)
            auth_contract = self._coordinator.bind_signed_authorization(authorization)

            # 3. Require calling flow to hold target lock
            if not self._coordinator.has_target_lock(timeline_uid):
                raise TargetExclusionLockError(
                    f"ERR_TARGET_NOT_LOCKED: calling flow has not acquired target lock for '{timeline_uid}'"
                )

            # 4. Assemble canonical 16-key action payload
            payload = assemble_canonical_action_payload(
                action, authorization, execution_id=execution_id
            )

            # 5. Digest verification gate against signed digest
            payload_bytes = canonical_action_payload_bytes(payload)
            computed_digest = hashlib.sha256(payload_bytes).hexdigest()
            if computed_digest.lower() != authorization.action_payload_digest_sha256.lower():
                raise WholePlanReconciliationError(
                    f"ERR_ACTION_DIGEST_MISMATCH: computed payload digest '{computed_digest}' != signed '{authorization.action_payload_digest_sha256}'"
                )

        except Exception as pre_journal_err:
            # Clean pre-dispatch rejection when state is proven clean:
            # Release lock cleanly ONLY if caller owns it on this timeline.
            if self._coordinator.has_target_lock(timeline_uid):
                try:
                    self._coordinator.release_target_lock(timeline_uid)
                except Exception as rel_err:
                    raise TargetLockReleaseError(
                        f"ERR_PRE_DISPATCH_RELEASE_FAILED: failed to release lock after pre-dispatch rejection: {rel_err}"
                    ) from rel_err
            raise pre_journal_err

        # ======================================================================
        # PHASE 2: Durable Ledger Activation & WAL Persistence (I4-02, I4-05, I4-R2-02)
        # Attempt to reserve & activate durable target ledger, then write START and ACTION_PENDING.
        # If append/fsync fails here, the persistence outcome is ambiguous.
        # We MUST NOT release the target lock! Exclusion must be preserved fail-closed!
        # ======================================================================
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            # 1. Durable target ledger transition to ACTIVE (I4-R2-02, I4-R3-02)
            if durable_ledger is not None:
                d_entry = durable_ledger.get_ledger(project_uid, timeline_uid)
                if d_entry is None:
                    durable_ledger.reserve_target(
                        target_project_uid=project_uid,
                        target_timeline_uid=timeline_uid,
                        authorization_id=authorization.authorization_id,
                        execution_id=execution_id,
                        approval_id=authorization.approval_record_id,
                        proposal_id=authorization.proposal_id,
                        proposal_revision=authorization.proposal_revision,
                        plan_idempotency_key=authorization.plan_idempotency_key,
                    )
                else:
                    # Defense-in-depth: Re-verify full identity tuple before activation (I4-R3-02)
                    mismatches = []
                    if d_entry.execution_id != execution_id:
                        mismatches.append(f"execution_id: expected {d_entry.execution_id}, got {execution_id}")
                    if d_entry.authorization_id != authorization.authorization_id:
                        mismatches.append(f"authorization_id: expected {d_entry.authorization_id}, got {authorization.authorization_id}")
                    if d_entry.approval_id != authorization.approval_record_id:
                        mismatches.append(f"approval_id: expected {d_entry.approval_id}, got {authorization.approval_record_id}")
                    if d_entry.proposal_id != authorization.proposal_id:
                        mismatches.append(f"proposal_id: expected {d_entry.proposal_id}, got {authorization.proposal_id}")
                    if d_entry.proposal_revision != authorization.proposal_revision:
                        mismatches.append(f"proposal_revision: expected {d_entry.proposal_revision}, got {authorization.proposal_revision}")
                    if d_entry.plan_idempotency_key != authorization.plan_idempotency_key:
                        mismatches.append(f"plan_idempotency_key: expected {d_entry.plan_idempotency_key}, got {authorization.plan_idempotency_key}")
                    if d_entry.target_project_uid != project_uid:
                        mismatches.append(f"target_project_uid: expected {d_entry.target_project_uid}, got {project_uid}")
                    if d_entry.target_timeline_uid != timeline_uid:
                        mismatches.append(f"target_timeline_uid: expected {d_entry.target_timeline_uid}, got {timeline_uid}")

                    if mismatches:
                        raise WholePlanReconciliationError(
                            f"ERR_TARGET_LEDGER_IDENTITY_MISMATCH: durable ledger identity does not match incoming authorization before activation: "
                            f"{'; '.join(mismatches)}"
                        )

                durable_ledger.activate_target(
                    target_project_uid=project_uid,
                    target_timeline_uid=timeline_uid,
                    execution_id=execution_id,
                )

            # 2. START record: matches exact source-consumer schema action_params={"total_actions": 1} (I4-02)
            start_entry = JournalEntry(
                seq=0,
                stage="START",
                action_type="NO_OP",
                timestamp_iso=now_iso,
                action_params={"total_actions": 1},
            )
            self._journal.append_entry(execution_id, start_entry)

            # 3. ACTION_PENDING record: exact 16-key canonical action payload in action_params
            pending_entry = JournalEntry(
                seq=1,
                stage="ACTION_PENDING",
                action_type=action.action_type,
                timestamp_iso=now_iso,
                action_params=dict(payload),
                track_type=action.track_type.lower(),
                track_index=action.track_index,
                resolve_item_id="",
            )
            self._journal.append_entry(execution_id, pending_entry)
        except Exception as persist_err:
            # Ambiguous append/fsync outcome on pending WAL:
            # Preserve target lock exclusion (DO NOT release lock) and propagate structured error (I4-05)
            raise AcceptanceGateError(
                f"ERR_JOURNAL_PERSISTENCE_AMBIGUOUS: failed to durably persist pending WAL records for '{execution_id}': {persist_err}"
            ) from persist_err

        # ======================================================================
        # PHASE 3: Gateway Dispatch & Post-Dispatch Durability Boundary (I4-04, I4-R2-03)
        # ACTION_PENDING is durable on disk.
        # The entire interval covering gateway call, result inspection, and
        # ACTION_COMMITTED append is protected under post-dispatch uncertainty.
        # ======================================================================
        gateway_res: Optional[Dict[str, Any]] = None
        dispatch_exc: Optional[Exception] = None

        try:
            gateway_res = self._fake_gateway.execute_action(action)
        except Exception as exc:
            dispatch_exc = exc

        # If gateway raised an exception
        if dispatch_exc is not None:
            incident_id = f"inc_{secrets.token_hex(16)}"
            fail_now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            journal_err: Optional[Exception] = None
            try:
                failed_entry = JournalEntry(
                    seq=2,
                    stage="ACTION_FAILED",
                    action_type=action.action_type,
                    timestamp_iso=fail_now_iso,
                    action_params=dict(payload),
                    error_code="ERR_GATEWAY_DISPATCH_EXCEPTION",
                    error_message=str(dispatch_exc),
                    track_type=action.track_type.lower(),
                    track_index=action.track_index,
                    resolve_item_id="",
                    note=f"incident_id={incident_id}",
                )
                self._journal.append_entry(execution_id, failed_entry)
            except Exception as j_err:
                journal_err = j_err

            # Mark timeline in doubt on coordinator regardless of journal failure
            coordinator_err: Optional[Exception] = None
            try:
                self._coordinator.mark_timeline_in_doubt(timeline_uid, incident_id)
            except Exception as c_err:
                coordinator_err = c_err

            if journal_err is not None or coordinator_err is not None:
                # Retain lock and propagate structured failure
                err_msg = (
                    f"ERR_POST_DISPATCH_HANDLING_FAILED: incident_id={incident_id}; "
                    f"journal_err={journal_err}; coordinator_err={coordinator_err}; "
                    f"original_dispatch_err={dispatch_exc}"
                )
                raise AcceptanceGateError(err_msg) from (coordinator_err or journal_err)

            # Retain target exclusion lock; DO NOT release, DO NOT write FINISH, DO NOT retry
            # Note: Durable ledger remains ACTIVE, preserving crash/restart lockout (I4-R2-02)
            return {
                "status": "TERMINAL_ACTION_FAILED_IN_DOUBT",
                "execution_id": execution_id,
                "incident_id": incident_id,
                "action_id": action.action_id,
                "action_index": action.action_index,
                "error_code": "ERR_GATEWAY_DISPATCH_EXCEPTION",
                "error_message": str(dispatch_exc),
                "lock_retained": True,
                "target_lock_state": "IN_DOUBT_LOCKED",
            }

        # Gateway returned successfully! Now persist ACTION_COMMITTED.
        # If writing/fsyncing ACTION_COMMITTED fails, this is a post-dispatch durability failure (I4-04, I4-R2-03).
        commit_now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        resolve_item_id = str((gateway_res or {}).get("resolve_item_id") or "mock_item_001")

        try:
            committed_entry = JournalEntry(
                seq=2,
                stage="ACTION_COMMITTED",
                action_type=action.action_type,
                timestamp_iso=commit_now_iso,
                action_params=dict(payload),
                resolve_item_id=resolve_item_id,
                track_type=action.track_type.lower(),
                track_index=action.track_index,
            )
            self._journal.append_entry(execution_id, committed_entry)
        except Exception as commit_persist_exc:
            # Post-dispatch commit durability failure (I4-04, I4-R2-03):
            # Gateway returned after dispatch, but commit could not be confirmed on disk.
            # Latch IN_DOUBT_LOCKED, retain OS target lock, do NOT append clean FINISH, do NOT retry.
            incident_id = f"inc_{secrets.token_hex(16)}"
            coordinator_latch_err: Optional[Exception] = None
            try:
                self._coordinator.mark_timeline_in_doubt(timeline_uid, incident_id)
            except Exception as latch_err:
                coordinator_latch_err = latch_err

            err_msg = (
                f"ERR_ACTION_COMMIT_PERSISTENCE_FAILED: gateway executed action but ACTION_COMMITTED append failed: "
                f"incident_id={incident_id}: commit_persist_exc={commit_persist_exc}; "
                f"coordinator_latch_err={coordinator_latch_err}"
            )
            commit_err = AcceptanceGateError(err_msg)
            # Surface true coordinator state; do not falsely claim IN_DOUBT_LOCKED if latch failed (I4-R2-03)
            actual_lock_state = "IN_DOUBT_LOCKED" if coordinator_latch_err is None else "LOCKED"
            setattr(commit_err, "incident_id", incident_id)
            setattr(commit_err, "target_lock_state", actual_lock_state)
            setattr(commit_err, "lock_retained", True)
            setattr(commit_err, "commit_error", commit_persist_exc)
            setattr(commit_err, "coordinator_latch_error", coordinator_latch_err)
            raise commit_err from commit_persist_exc

        # Terminalization & Ledger state (I4-R3-01):
        # A single action's ACTION_COMMITTED WAL record does not constitute a terminal
        # execution receipt (TrialExecutionReceipt/ExecutionReceipt).
        # Receipt persistence is outside the scope of this single-action adapter slice.
        # Therefore, the durable ledger remains ACTIVE on disk (preserving target exclusion
        # across crash/restart until explicit whole-plan receipt terminalization or reconciliation),
        # and the single-action success path is reported as not terminalized.
        return {
            "status": "COMMITTED",
            "execution_id": execution_id,
            "action_id": action.action_id,
            "action_index": action.action_index,
            "resolve_item_id": resolve_item_id,
            "action_payload_digest_sha256": authorization.action_payload_digest_sha256,
            "target_ledger_state": "ACTIVE",
            "terminalized": False,
        }
