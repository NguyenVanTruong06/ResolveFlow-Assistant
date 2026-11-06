# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R5

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `a8e75534bcea18e63d768664377f4b48540e5ba842ff00f28dfab1adcbebfa70` | Matches submitted R5 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `d28b0462e585fe778eef7518d60ceff30d2fd9519c37ac6858b99eb99ec503bf` | Matches submitted R5 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `80e7749b337f8e874d6895c2b5e9492dbc0f0eeed3b226b870b16485ddf0b1d1` | Reports 13 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `c4242349ba4985e95c06452d87cc185b6e55b2f64798852cd8b99b8af515d0ba` | R5 handoff and hashes present |

## Finding

### G4-R10-R5-01 — BLOCKER: Caller-created permissive proxies are trusted across the audit boundary

R5 removes the marker and registration API from module scope, but the module still exports `create_audited_proxy` (also aliased as `AuditedResolveProxy`). Its public signature accepts caller-selected `allowed_methods`. The closure-wide `_is_audited_proxy()` accepts any object registered by this factory, without binding the proxy to the current runner/logger or checking that its allowlist is the role's fixed allowlist. `_wrap_return_value()` therefore returns such a proxy unchanged. A caller can create a proxy over an adapter object with an expanded allowlist (for example, an insertion/dispatch method) or a different logger, arrange for an adapter result to contain it, and have the runner treat it as already audited. The call can then execute under the caller-selected policy or be written to a different audit log rather than the current run's log.

The R5 regression tests cover marker/key visibility, external subclass attempts, and raw impostors, but do not cover a genuine factory-created proxy with a custom allowlist or a proxy created under another logger.

**Required:** Make the factory unavailable as a caller-controlled proxy constructor, or remove caller-supplied allowlists and bind proxy identity to the current runner/logger and fixed role policy. `_wrap_return_value()` must only pass through a proxy created by this run with the exact expected logger and role allowlist; otherwise wrap or reject it. Add regression tests for a custom-allowlist proxy and a cross-logger proxy returned directly and nested in a container, proving the raw method cannot execute outside the current audited policy/log.

## Decision and lifecycle state

- Gate 4 R10 Revision R5: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 13/13 test result is acknowledged as reported; this review did not rerun tests.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
