# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R8

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `e07009ebe7f2958af6f0f788b77fe2ae1c8fa5d5c7707fee329a81233fe7cfa0` | Matches submitted R8 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `7adb6bc2571caa8108af65035bd59470168e396ff473a50abb8f5e23d36e83fc` | Matches submitted R8 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `f70e53f50ffc5124e1721abe60a0a0644cef8b0f7f6ee88e21cf77ae966e8bd7` | Reports 14 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `a713246d0d7073227244ffe747f84a89461627cec90b8a920bba9c969c7ff5c2` | R8 handoff and hashes present |

## Findings

### G4-R10-R8-01 — HIGH: Blacklist snapshot still depends on rebindable module globals

R8 makes the blacklist values immutable, but `Gate4PreflightRunner.__init__()` snapshots `_CANONICAL_INCIDENT_BLACKLIST` by reading the module global at construction, and `run()` also checks the separately rebindable `BLACKLISTED_UIDS` global. A caller can rebind both module names to empty frozensets before constructing the runner; the runner then snapshots the replacement and both membership checks accept a formerly blacklisted UID. The test patches only `BLACKLISTED_UIDS`, leaving `_CANONICAL_INCIDENT_BLACKLIST` untouched, so the second public binding remains an untested bypass.

**Required:** Capture the canonical denylist in closure-owned immutable state initialized independently of caller-rebindable module names, and have all runner validation consult only that state. Add a regression test that rebinds both exposed names before runner construction and proves the known incident UID is still rejected.

### G4-R10-R8-02 — HIGH: JCS number formatting diverges for large integral binary64 values

`_rfc8785_number_to_str()` special-cases every integral float below `1e21` by converting it to an exact Python integer (`str(int(val))`, around lines 181–184). RFC 8785 requires the ECMAScript shortest round-trip representation for the IEEE-754 value, not the full exact integer expansion of that binary64 value. For example, a float whose shortest decimal is `1000000000000000100` can have an exact binary64 integer value of `1000000000000000128`; converting with `int()` emits the latter and diverges from JCS. The known-answer tests cover common values and exponent thresholds but omit large integral binary64 rounding cases. The function also serializes arbitrary Python integers directly, although JCS operates on the interoperable IEEE-754/ I-JSON number domain.

**Required:** Implement number serialization against the ECMAScript/JCS shortest representation for the binary64 value (or use an approved conforming JCS implementation) and reject unsupported integer values outside the permitted interoperable numeric domain. Add known-answer tests around 2^53, large integral binary64 values such as the example above, and boundary values near 1e21, with expected bytes fixed independently of the implementation under test.

## Decision and lifecycle state

- Gate 4 R10 Revision R8: `CHANGES_REQUIRED`; do not use the runner for a live preflight until these findings are resolved and reviewed.
- The submitted 14/14 test result is acknowledged as reported; this review did not rerun tests.
- The R7 findings prompted immutable blacklist/JCS changes, but the remaining issues above mean the corresponding guarantees are not yet established.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
