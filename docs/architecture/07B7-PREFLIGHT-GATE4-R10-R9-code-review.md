# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R9

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `854e58edfcd63c5a72ef42e578817868b73577326d8f63a902e12f8ccc510634` | Matches submitted R9 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `841a5c2f01d5f2137efcdd3deacae32da7d7220b18bd6fc36a8fca2c50edeab3` | Matches submitted R9 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `7366117e2ba4a4b9a819c8d1ee944edde56db0ad8016aed2f7a751da4eb5d6f4` | Reports 14 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `6b167579f4612123fb54abafa1c8b88f5ccd075e872d8af96503b327aecc1982` | R9 handoff and hashes present |

## Finding

### G4-R10-R9-01 — MEDIUM: Non-string map keys are coerced and can erase observed return data

R9 removes the large-integral-float `int(val)` path and adds the reported binary64 vector. However, `_rfc8785_canonical_str()` still stringifies non-string dictionary keys (`str(k)`) instead of rejecting them, and `safe_serialize_val()` performs the same coercion when preparing raw adapter return values for the audit log. A mapping containing both integer key `1` and string key `"1"` collapses to one entry during serialization. The audit then hashes and records a lossy object rather than the complete raw return value, despite the workorder requiring deterministic serialization of observed results. The same coercion also means `canonical_json_bytes()` accepts Python objects that are not valid JSON/JCS object shapes rather than failing closed.

**Required:** Require exact string keys for JCS objects, or encode non-string-keyed mappings in an explicit typed representation that cannot collide. Detect and reject key collisions before writing an audit event, so unsupported/lossy return values fail closed instead of producing a success record. Add regression tests for integer key `1` alongside string key `"1"` in direct and nested adapter return values.

## Decision and lifecycle state

- Gate 4 R10 Revision R9: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 14/14 test result is acknowledged as reported; this review did not rerun tests.
- G4-R10-R8-01 and the reported G4-R10-R8-02 case are addressed. This review found a separate lossy dictionary-key serialization gap in the audit evidence path.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
