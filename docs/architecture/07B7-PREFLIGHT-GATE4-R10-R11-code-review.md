# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R11

**Review date:** 2026-11-05  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `33d28a58e6f1b440ee132116f74ca969ee56662ad783ea087a54e6859b1834c2` | Matches submitted R11 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `9b98ceacdd8ab854d585be75d51c309e4d4084bf5dd0cb23b184f18378a237e5` | Matches submitted R11 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `a261f5488553b4562b36ae9a19c4396cbe25a3214c34cad4a90c18f76efc7470` | Reports 14 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `974ce41ad7d1cff80b84162f4ff4e439dfbe442983334a27d0ba843ab755c2e0` | R11 handoff and hashes present |

## Finding

### G4-R10-R11-01 — HIGH: Argument serialization can still fail after an adapter call with no call event

R11 handles an exception from `safe_serialize_val(ret)` by appending `API_CALL_SERIALIZATION_FAILURE`. However, the same collision-prone serializer is still called for `args` only after the adapter method has executed: in the `API_CALL_EXCEPTION` branch (`safe_serialize_val(args)` around lines 598–600), in the new return-serialization-failure event (`around line 618`), and in the normal `API_CALL` event (`around line 613`). If an allowed adapter method receives an argument containing a mapping with colliding keys such as `1` and `"1"`, the method executes first and argument serialization then raises `ValueError`. No API call outcome event is appended; the runner writes a failure terminal with incremented call counters, while the verifier derives no corresponding call event and rejects the counter reconciliation. The existing direct/nested collision tests exercise colliding *return values* with empty/simple arguments, so they do not cover this path.

**Required:** Serialize and validate arguments before invoking the adapter method, so an unsupported/colliding argument is rejected without any adapter side effect; or ensure every post-call argument-serialization failure is captured in a structured call event and included in derived counters. Add a regression test with colliding keys in an allowed method argument, prove the adapter is not invoked if prevalidation is chosen, and confirm the resulting failure log has a valid chain and consistent terminal counters.

## Decision and lifecycle state

- Gate 4 R10 Revision R11: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 14/14 test result is acknowledged as reported; this review did not rerun tests.
- G4-R10-R10-01 is addressed for return-value serialization; argument serialization remains an unhandled post-call failure path.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
