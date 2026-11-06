# G07-07B4-I — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Scope reviewed:** `src/services/resolve_execution_engine.py`, its focused tests, the G07-07B4-I report, and the approved G07-07B3-D-R1 binding conditions.
- **Safety boundary:** This review did not start DaVinci Resolve, call Resolve IPC, or modify production/test code.

## Independent verification

The submitted SHA-256 values for `.bridge/report.md`, `src/services/resolve_execution_engine.py`, `tests/services/test_resolve_execution_engine.py`, and `pytest_18files.log` matched the handoff. The prescribed 18-suite command was rerun:

```text
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/test_resolve_execution_engine.py \
  tests/core/test_timeline_plan_execution_binder.py \
  tests/core/test_recovery_assessment_contracts.py \
  tests/services/test_recovery_classifier_service.py \
  tests/services/test_recovery_coordinator.py \
  tests/core/test_execution_receipt_contracts.py \
  tests/services/test_execution_receipt_journal_storage.py \
  tests/core/test_proposal_builder.py \
  tests/core/test_proposal_builder_source_evidence.py \
  tests/ui/test_common_proposal_review.py \
  tests/ui/test_director_preflight_shutdown.py \
  tests/ui/test_director_step4_approval_gate.py \
  tests/ui/test_director_step4_readiness_authority.py \
  tests/ui/test_preflight_worker.py \
  tests/ui/test_preapproval_evidence_worker.py \
  tests/ui/test_director_readiness_ui.py \
  tests/ui/test_recovery_assessment_ui.py \
  tests/ui/test_review_preapproval_evidence.py -q
```

Result: **268 passed in 23.79s, exit code 0**. This verifies regression behavior; it does not close the safety findings below.

## Blocking findings

### B4-R1 — Execution exclusion is per execution ID, not per target timeline

`ExecutionReceiptService._lock_path()` creates a different lock file for each `execution_id` (`src/services/execution_receipt_service.py:154-162`). `ResolveExecutionEngine.execute_plan()` accepts a caller-supplied execution ID and acquires that lock (`src/services/resolve_execution_engine.py:307-322`). Two distinct plans targeting the same project/timeline can therefore hold different locks and both dispatch. The idempotency pre-check is not a timeline mutex, and its check-then-create path is not an atomic reservation for concurrent submissions.

**Required:** Serialize admission by stable target identity (at least project ID + timeline ID), and make idempotency reservation atomic across processes. Demonstrate two different execution IDs/plans targeting the same timeline cannot both dispatch, including a barrier-controlled concurrent race.

### B4-R2 — CP-06 checks snapshot status but ignores occupancy evidence

The loop queries an occupancy snapshot and only checks `occ_snap.status == "AUTHORITATIVE"` (`src/services/resolve_execution_engine.py:527-532`). It does not verify snapshot project/timeline/FPS, scanned target track, or compare `occupied_spans` with the pending planned action and already committed actions. The accepted binder explicitly requires scanned-track proof and rejects external overlap (`src/core/timeline_plan_execution_binder.py:869-899`). A newly occupied target range can therefore pass CP-06 and still be dispatched.

**Required:** Validate snapshot identity and coverage, compare the pending action against authoritative occupancy, and account for only this execution's already committed items without treating them as external conflicts. Missing, stale, ambiguous, or colliding evidence must stop before the next `ACTION_PENDING`/dispatch.

### B4-R3 — Post-dispatch journal failures escape and release ownership

After `gateway.execute_action()` returns success, `append_entry(ACTION_COMMITTED)` is outside a failure boundary (`src/services/resolve_execution_engine.py:700-715`). A journal write/fsync failure propagates through the outer `finally`; because `lock_retained` remains false, the lock is released (`:816-819`). The durable log then ends at `ACTION_PENDING`, while the external action may already exist. Similar unhandled persistence failures occur around post-dispatch `ACTION_FAILED`/`FINISH` and final completion writes.

**Required:** Treat every persistence failure after dispatch as an unresolved outcome: never replay, never claim completion, keep the target-level admission blocked, preserve the last durable WAL bytes, and surface an explicit `IN_DOUBT` result. Add fault injection for commit append/fsync failure after a successful fake Gateway result and for final receipt/journal persistence failure.

### B4-R4 — Timeout lock is not retained as addressable engine ownership

On timeout the local `lock_holder` is marked retained and the method returns (`src/services/resolve_execution_engine.py:640-675`), but no engine/service registry retains the holder or exposes a physical-completion/reconciliation path. The descriptor may remain open until process exit, but the engine cannot identify, gate other target executions on, or deliberately release it after completion is confirmed. Since locks are also per execution ID (B4-R1), another execution ID can bypass it.

**Required:** Keep unresolved task and lock ownership in an explicit process-level owner registry keyed by target/execution; block all new dispatches for that target; define a safe release path that requires positive physical completion/reconciliation evidence. A timeout alone must not release ownership.

### B4-R5 — “Mock-only” is a convention, not a checked boundary

The engine accepts any runtime object satisfying `ResolveGatewayClientProtocol` (`src/services/resolve_execution_engine.py:91-115, 178-190`) and directly invokes its `execute_action()` method. No marker/capability gate distinguishes the approved fake Gateway from a live adapter. The current absence of UI references and Resolve imports reduces present reachability but does not enforce the workorder's injected-fake-only invariant at the engine entry point.

**Required:** Add an explicit prototype-only fake-gateway admission gate (with a positive fake control and negative arbitrary/live-like adapter probe), or otherwise make the entry point structurally unavailable to non-test adapters. Keep the module unwired and `ready_to_execute=False`.

## Acceptance status and limits

G07-07B4-I is **not accepted** yet. The passing 268-test suite and reported no-Resolve tripwire are recorded, but they do not cover the concurrency, live occupancy, post-dispatch storage failure, retained-owner lifecycle, or fake-only admission cases above. No live execution authorization is granted. `ready_to_execute=False` and Gate G07/M1/M2/M3 remain unchanged and open.
