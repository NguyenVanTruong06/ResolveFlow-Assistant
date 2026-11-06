# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R4

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `38da5acc9f118f5baa5f36cfb3f62142d98cdd8b7084dbb6ddd1d4ccf81ee819` | Matches submitted R4 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `92e7cb1bb2abe946dc4d45abcb4bfeaf8c07ecd6506e81f976855b36e47d47ee` | Matches submitted R4 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `e0e5a8a3454fbc060d5f18a11443d23a10009172643cc34e39652b9802d21c77` | Reports 13 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `4779bed96272e4b06e8181605d43f23e746edeadaa8ec01958b5fd331c2115e1` | R4 handoff and hashes present |

## Finding

### G4-R10-R4-01 — BLOCKER: Proxy identity marker and registration are forgeable through module APIs

R4 replaces class-name detection with a marker and registered-ID check, but both parts of that check are caller-reachable. `_PROXY_CONSTRUCTION_KEY` is a module-level object, `_AuditedProxyMarker` is an importable module attribute, and `HashChainedAuditLogger.register_proxy()` accepts any object without proving it was created by `create_audited_proxy()` (`src/tools/gate4_preflight_runner.py`, around lines 356–379). A caller can subclass the marker using the exposed key, call `logger.register_proxy(forged_object)`, then have an adapter return that object. `is_registered_proxy()` will accept it and `_wrap_return_value()` will return it raw, allowing its methods to run outside the audited allowlist. The new regression test only attempts subclassing without the key, so it does not cover this path.

**Required:** Move marker construction authority and registration state behind a mechanism unavailable through ordinary module attributes or caller-invocable registration methods. Only the proxy factory should be able to register the exact proxy it just created. Add a regression test that attempts to use every exposed marker/key/registration surface to register a forged object and verifies that the object is wrapped or rejected and its unapproved methods never execute raw.

## Decision and lifecycle state

- Gate 4 R10 Revision R4: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 13/13 test result is acknowledged as reported; this review did not rerun tests.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
