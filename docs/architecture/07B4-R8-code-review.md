# G07-07B4-R8 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review, direct pure-function probes, and offscreen regression only. No Resolve launch, IPC, FusionScript, or timeline access.

## Independent verification

All submitted SHA-256 values matched for the report, engine, receipt service, tests, architecture document, and both logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **43 passed in 1.26s**, exit 0.
- Same 18-suite regression as R3: **295 passed in 27.35s**, exit 0. No Resolve call was made.
- Direct probes found malformed control-record envelopes accepted by the pure WAL validator.

## Improvements verified

R8 now requires a first START record, validates its `total_actions`, rejects duplicate START, and strictly binds the in-doubt FINISH action index and action-parameter keys. The submitted tests pass independently.

## Blocking residual

### B4-R8-RES-01 — START/FINISH control-record envelope fields are not validated

The validator checks only `action_params` for START (`resolve_execution_engine.py` around lines 1015-1036) and only note/action-index parameters for the permitted in-doubt FINISH (`:1098-1130`). It does not require control records to use `action_type="NO_OP"`, empty track fields, default track index, empty resolve item/error fields, or an otherwise exact control-record envelope.

I directly invoked `_validate_journal_wal_state_machine()` with a valid START payload but `START.action_type="INSERT_CLIP"`, `track_type="video"`, `track_index=99`; the malformed record was accepted. A second direct probe used a valid in-doubt ledger but `FINISH.action_type="INSERT_CLIP"`, `track_type="video"`, `track_index=99`; it was also accepted. Both are invalid WAL control records despite passing the action-parameter checks.

**Required:** Define and enforce exact allowed fields/values for START and FINISH envelopes, including strict types for sequence, action type, timestamp, resolve item, track, error, and note fields as appropriate. Reject unexpected metadata and non-NO_OP/nonempty track values. Add direct plus end-to-end tests asserting malformed control records cause zero durable mutation and preserve unresolved ownership/journal bytes.

## Gate status

G07-07B4-R8 is **not accepted** until control-record envelopes are validated exactly. Preserve `ready_to_execute=False`, MOCK ONLY, zero UI/live Resolve wiring, and keep G07/M1/M2/M3 open.
