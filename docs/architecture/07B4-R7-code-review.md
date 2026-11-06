# G07-07B4-R7 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review, pure-function probes, and offscreen regression only. No Resolve launch, IPC, FusionScript, or timeline access.

## Independent verification

All submitted SHA-256 values matched for the report, engine, receipt service, tests, design document, and both logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **42 passed in 1.31s**, exit 0.
- Same 18-suite regression as R3: **294 passed in 27.37s**, exit 0. No Resolve call was made.
- Direct probes found two malformed START/FINISH metadata cases accepted by the pure WAL validator.

## Improvements verified

R7 closes the earlier terminal-continuation and action-payload gaps: FINISH now blocks subsequent events, commits/failures compare complete action payloads, and ACTION_FAILED requires a live matching pending action. The focused and regression suites pass independently.

## Blocking residual

### B4-R7-RES-01 — WAL admission does not enforce a unique initial START or strict FINISH action index

In `_validate_journal_wal_state_machine()` (`resolve_execution_engine.py:1035-1036`), every `START` is accepted with an unconditional `continue`; there is no state requiring exactly one START as the first event or checking its `total_actions` against `planned_count`. A direct probe with `START, ACTION_PENDING(0), START, FINISH(HALTED_IN_DOUBT_OBSERVER_TIMEOUT)` was accepted as a valid in-doubt ledger.

For the permitted in-doubt FINISH, the `action_index` field is optional and compared with ordinary equality without a strict type gate (`:1056-1062`). With one prior commit and pending action 1, a FINISH carrying `action_index=True` was accepted because `True == 1` in Python. Thus malformed control metadata can still pass reconciliation despite strict typing elsewhere.

**Required:** Require exactly one START before all action events, with strict schema/type validation and `total_actions == planned_count`; reject duplicate or misplaced START. For `HALTED_IN_DOUBT_OBSERVER_TIMEOUT`, require the expected FINISH payload shape, require `type(action_index) is int` (excluding bool), and exact-match it to the sole pending action. Add zero-mutation end-to-end probes for both cases.

## Gate status

G07-07B4-R7 is **not accepted** pending this WAL boundary closure. Preserve `ready_to_execute=False`, MOCK ONLY, zero UI/live Resolve wiring, and keep G07/M1/M2/M3 open.
