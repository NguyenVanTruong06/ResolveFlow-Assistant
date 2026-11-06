# G07-07B4-R9 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Static review and offscreen tests only. No Resolve launch, IPC, FusionScript, UI wiring, or timeline access.

## Independent verification

Submitted SHA-256 values matched the report, engine, receipt service, test module, architecture document, and both evidence logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **44 passed in 1.25s**, exit 0.
- 18-suite regression command recorded for R3 — **296 passed in 27.35s**, exit 0.
- No Resolve runtime was contacted.

## R9 improvement verified

`_validate_control_record_envelope()` now validates the typed `JournalEntry` control record fields, and the WAL state-machine tests cover malformed control metadata and zero-mutation reconciliation behavior.

## Blocking residual

### B4-R9-RES-01 — JSON parsing erases malformed raw envelope values before validation

`ExecutionJournalService.replay_journal()` parses JSON and calls `JournalEntry.from_dict()`. That constructor currently coerces raw fields with `int(...)` / `str(...)` and ignores unknown keys. The later R9 validator therefore cannot establish that the bytes on disk had the required primitive types or exact field set.

Independent probe against the current production path:

| Raw START JSON field | Result after `JournalEntry.from_dict()` and `_validate_control_record_envelope()` |
| --- | --- |
| `seq: true` | **Accepted** as `seq=1` |
| `seq: 0.75` | **Accepted** as `seq=0` |
| `track_index: 0.0` | **Accepted** as `track_index=0` |
| unknown key `untrusted_extra: "x"` | **Accepted**; key silently dropped by `to_dict()` |

The probe used an otherwise valid START record with `total_actions: 0`. These are exact raw-record violations of the strict primitive-type and exact-envelope policy. The validator itself is not the failing part; validation is occurring after lossy normalization.

## Required repair

Validate the decoded raw JSON object before any scalar coercion or field dropping, then construct the typed entry only after the raw record passes. Apply exact per-stage allowed-key sets, strict primitive-type checks for all journal fields, reject unknown keys, and reject duplicate JSON keys. Ensure replay/reconciliation treats violations as corrupted evidence and preserves journal bytes, receipt/target state, unresolved ownership, and zero dispatch. Add unit and end-to-end probes for the cases above and corresponding FINISH controls.

## Gate status

G07-07B4-R9 is **not accepted** pending raw-record validation. Preserve MOCK ONLY, `ready_to_execute=False`, zero Resolve/IPC/FusionScript/UI wiring, and keep G07/M1/M2/M3 open.
