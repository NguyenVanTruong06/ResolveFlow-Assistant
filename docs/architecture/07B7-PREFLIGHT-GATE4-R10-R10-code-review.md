# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R10

**Review date:** 2026-11-05  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `c478173c21d9b19b222f9d48755e18d4b6737ec0054bbf2b2d061c27e690ca63` | Matches submitted R10 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `e32815199d9a2da489d67f9724a1aa377a2d92e02da6fa819568c25509924ad4` | Matches submitted R10 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `83972ba442459666831e7c39c70602f7e677555e9efe4953e868239c8cc28005` | Reports 14 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `f40b30dbbc5ee96a6e807a0fcbc06d98ab53ab799019991628071200a16cfbd5` | R10 handoff and hashes present |

## Finding

### G4-R10-R10-01 — HIGH: Return-serialization failure leaves an unlogged adapter call and inconsistent terminal counters

The new collision check correctly raises instead of silently dropping a colliding dictionary entry. However, in the proxy's allowed-call path, the adapter method executes and `total_calls`/`allowed_calls` are incremented before `safe_serialize_val(ret)` runs while building the `API_CALL` event (`src/tools/gate4_preflight_runner.py`, around lines 585–618). If that serialization raises `ValueError`, the raw adapter call has already happened, but no `API_CALL` or `API_CALL_EXCEPTION` event is appended. The exception then reaches `Gate4PreflightRunner.run()` and `close_failure()` writes a terminal record containing the already-incremented counters. `verify_audit_log_chain()` derives counters only from event records, so this failure log has terminal totals that do not match the observed event totals and is unverifiable. The R10 tests assert that `run()` raises for direct/nested collisions, but do not verify that the resulting audit log records the completed call and has a coherent failure terminal.

**Required:** Treat serialization failure after adapter invocation as a structured call outcome: record a durable `API_CALL_SERIALIZATION_FAILURE` (or equivalent) event with the call identity, return type, serialization error, and a safe indication that the raw result could not be serialized; derive counters consistently from that event. Ensure the failure terminal can pass structural/hash/counter verification while the run remains unsuccessful. Add direct and nested collision tests that verify the call is represented in the log, the terminal is `AUDIT_TERMINAL_FAILURE`, and `verify_audit_log_chain()` returns a valid, internally consistent failure log.

## Decision and lifecycle state

- Gate 4 R10 Revision R10: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 14/14 test result is acknowledged as reported; this review did not rerun tests.
- The R9 key-collision finding is addressed by rejecting collisions, but the new failure path must preserve audit completeness and counter consistency.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
