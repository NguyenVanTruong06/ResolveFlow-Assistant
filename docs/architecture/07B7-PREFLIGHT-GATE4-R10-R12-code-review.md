# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R12

**Review date:** 2026-11-05  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `23a61e79a1892110dd778e4ac2760cb5938ceed66adb49b73878faa74c364c2d` | Matches submitted R12 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `c760442b81be89bec0cb803cff294f3b7dc8629bb18cfcf30e3fd55272994ebc` | Matches submitted R12 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `cbb6779bd2921d7ae16c87e095d8a4317d6d59f85512a122fa215de829f0b123` | Reports 14 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `a2a527645f2957e637045e1927a6e5258689f0d65dbf4c86325b022ab014d023` | R12 handoff and hashes present |

## Finding

### G4-R10-R12-01 — HIGH: Keyword arguments bypass prevalidation and are omitted from audit records

`_proxy_call` accepts both `*args` and `**kwargs`, but R12 only serializes `args` before invoking the adapter (`src/tools/gate4_preflight_runner.py`, around lines 588–605). All call outcome records also contain only `serialized_args`; `kwargs` are never validated or written to the audit log. A call such as `proxy.SetSetting(k="timelineResolutionWidth", v={1: "int", "1": "str"})` has an empty positional tuple, so the prevalidation succeeds, then the adapter method executes with the colliding mapping in `kwargs`; the subsequent event records `arguments: []` and omits the actual values. This bypasses the new no-side-effect guarantee for colliding input data and violates the workorder's requirement to audit the exact arguments.

The R12 regression test exercises a collision passed positionally and therefore does not cover this keyword-argument path.

**Required:** Prevalidate and serialize a structured argument payload containing both positional and keyword arguments before the adapter call. Store both in every call event. On serialization failure, emit `ARGUMENT_SERIALIZATION_FAILURE`, do not invoke the adapter, and keep terminal counters verifiable. Add a regression test passing the collision in keyword arguments and assert zero adapter invocation, complete failure event details, and a valid failure audit chain.

## Decision and lifecycle state

- Gate 4 R10 Revision R12: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 14/14 test result is acknowledged as reported; this review did not rerun tests.
- G4-R10-R11-01 is resolved for positional arguments. Keyword arguments remain outside the prevalidation and audit record boundary.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
