# G07-07B4-R6 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review, direct pure-function probes, and offscreen regression only. No Resolve launch, IPC, FusionScript, or timeline access.

## Independent verification

All submitted SHA-256 values matched for the report, engine, tests, architecture document, and both logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **41 passed in 1.21s**, exit 0.
- Same 18-suite regression as R3: **293 passed in 29.78s**, exit 0. No Resolve call was made.
- Direct pure-function probes found two malformed WAL histories accepted by `_validate_journal_wal_state_machine()`.

## Improvements verified

R6 now requires exact canonical artifact bytes, rejects duplicate JSON keys, binds proof operator/timestamp to signed fields, and checks action indices and transitions through a WAL validator. The reported test suites pass independently.

## Blocking residual

### B4-R6-RES-01 — WAL validator accepts terminal-stage continuation and mismatched action payloads

The validator's docstring promises to reject events after `FINISH` (`resolve_execution_engine.py` around `_validate_journal_wal_state_machine`, lines 962-965), but the implementation handles `FINISH` by rejecting only `COMPLETED_ALL_ACTIONS` and otherwise continuing without setting a terminal flag (around lines 1008-1013). A direct probe with `START`, `ACTION_PENDING(0)`, `FINISH(note="HALTED_ON_ACTION_FAILURE")` returned a valid unresolved pending index instead of rejecting the completed history.

The `ACTION_COMMITTED` comparison checks only `action_type`, `track_type`, and `track_index`; it does not compare `entry.action_params` with the preceding pending record (around lines 1090-1104). A direct probe changed `source_path` between pending and commit while keeping those three fields identical; the validator accepted the ledger.

These violate the stated stage-machine/WAL identity contract: reconciliation could treat a terminated attempt as live in-doubt work, or accept a commit record whose operation payload differs from the durable pre-dispatch intent.

**Required:** Track and reject any stage after a terminal `FINISH`; define which non-completed FINISH values are valid and ensure they cannot leave a reconcilable pending action. Compare the full normalized authority-bearing action payload between `ACTION_PENDING` and `ACTION_COMMITTED` (or a canonical payload digest), not only type/track fields. Also reject `ACTION_FAILED` with no matching pending action or a mismatched action index. Add zero-mutation probes for terminal continuation, payload tampering, and failed-stage mismatch.

## Gate status

G07-07B4-R6 is **not accepted** yet. Exact attestation and proof audit binding are verified, but the WAL state machine still accepts terminal continuation and action-payload mismatch. Preserve `ready_to_execute=False`, MOCK ONLY, zero UI/live Resolve wiring, and keep G07/M1/M2/M3 open.
