# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R2

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of the submitted source, tests, report, workorder, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder/allowlist verified |
| `src/tools/gate4_preflight_runner.py` | `68db59d36fcaab08d25adb2a773928ef19de1806806e10a42f97898e05ba0a12` | Matches submitted R2 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `ebda7b840f8cac82fdffa6ab3ebc0c8c971e31642c5f416ff965bda6e9da1d5b` | Matches submitted R2 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `1871c716b78532c80326a30f9478f57fc5b69ef90087176da4490e99c25b966a` | Reports 12 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `e957d0cb83902e52aa6002e057af5d9d36997d8284e0faee30f014436ddcec10` | R2 handoff and hashes present |

## Findings

### G4-R10-R2-01 — BLOCKER: Module registry remains a direct raw-adapter escape

R2 removes raw targets from proxy instance attributes, but `_PROXY_STATE_REGISTRY` is a module-level dictionary whose values contain `(target, logger, role, methods)`. Any caller that can import the runner module can read `module._PROXY_STATE_REGISTRY[id(proxy)][0]` and invoke the raw adapter directly, bypassing the audited proxy and its deny-by-default policy. The current test checks `proxy._target` and `proxy._logger`, but does not test access through the registry.

**Required:** Keep target state in a closure/private state mechanism that is not exposed as an ordinary module attribute, or otherwise make the runner's supported API incapable of retrieving the raw target. Add a regression test exercising the module surface and prove the raw target cannot be retrieved or called without audit. The boundary need not claim protection against arbitrary code execution or deliberate Python runtime introspection.

### G4-R10-R2-02 — HIGH: Verifier accepts incomplete or multiple terminal summaries

`verify_audit_log_chain()` compares terminal counters only when each field is not `None`. A hash-consistent terminal record that omits `total_calls`, `allowed_calls`, `blocked_calls`, `dispatch_calls_attempted`, or `clip_insertions_attempted` can therefore be accepted without reconciling that value. The verifier also checks only that the *last* event is a terminal type; an earlier terminal record followed by more events and another terminal record is not rejected. This falls short of requiring exactly one terminal record at the end.

**Required:** Require all terminal summary fields with exact non-negative integer types and compare every value against independently derived event totals. Require exactly one terminal event and require it to be the final record. Add hash-recomputed fixtures for omitted terminal fields and a terminal record followed by additional events.

### G4-R10-R2-03 — MEDIUM: Audit directory check does not enforce the stated exact `0700` mode

After `chmod`, the logger checks only `(mode & 0o077) == 0`. This accepts owner modes such as `0500`, `0100`, or `0000`, despite the error text and R2 remediation stating that mode `0700` is verified. Depending on execution privileges, a log could be created in a directory that does not have the required owner read/write/search permissions.

**Required:** Require `stat.S_IMODE(dir_stat.st_mode) == 0o700` (and the required owner UID) before creating the log or proceeding. Add a no-op/ineffective chmod test for a mode with no group/world bits but not exactly `0700`, and assert construction fails before any adapter call.

## Decision and lifecycle state

- Gate 4 R10 Revision R2: `CHANGES_REQUIRED`; do not use this runner for a live preflight until the findings are resolved and reviewed.
- The submitted 12/12 test result is acknowledged as reported; this review did not rerun tests.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
