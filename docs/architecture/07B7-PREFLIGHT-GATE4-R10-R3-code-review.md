# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R3

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `7fb05d087034f2a7ecdd4a35095654df01980eb8c8e26e66be96f6b4b71a81ab` | Matches submitted R3 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `1fc804fcf233d26b3dcf7ce3217d3dfeed3d513d7b8aabf562e579b025c2c282` | Matches submitted R3 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `cdb70de9413749777dd8dce3294388ca61e1cc31daf16c68a85e953a2b9ee3c5` | Reports 12 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `d0f6a5d452cc03ef98a51a23f6d79f0ab47003e02dc94451f607cc030c7273f7` | R3 handoff and hashes present |

## Finding

### G4-R10-R3-01 — BLOCKER: Return-wrapper detection trusts a caller-controlled class name

`_wrap_return_value()` treats any object whose `type(val).__name__` equals `"_ClosureAuditedProxy"` as an already wrapped proxy and returns it unchanged (`src/tools/gate4_preflight_runner.py`, around lines 474–478). A raw adapter object can have that class name without being the closure proxy created by `create_audited_proxy()`. If an adapter returns such an object—for example inside the list returned by `GetItemListInTrack()`—the object escapes the audited boundary and its methods can be called without the proxy allowlist or audit logging. The current regression test verifies class behavior for genuine proxies and a differently named raw object, but does not exercise a raw object with the reserved class name.

**Required:** Recognize already wrapped proxies using an unforgeable identity owned by the runner implementation, rather than a caller-controlled type name. Any object that cannot be positively identified as a runner-created proxy must itself be wrapped or rejected. Add a fake-adapter regression test returning a raw object named `_ClosureAuditedProxy` (including nested in a container) and prove its methods cannot execute outside the audit boundary.

## Decision and lifecycle state

- Gate 4 R10 Revision R3: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 12/12 test result is acknowledged as reported; this review did not rerun tests.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
