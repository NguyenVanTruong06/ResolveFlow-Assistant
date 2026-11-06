# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10 Revision R6

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of submitted source, tests, workorder, report, and test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 workorder verified |
| `src/tools/gate4_preflight_runner.py` | `736b127e1dfd9f702ddf23f505e14d26fcf8ed635c5870b6cb51b4b1f4776e1e` | Matches submitted R6 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `274a454d1bde4dafd399d1cfc30c9cb4f4bbaca3e791e91b380f41c5d93cd107` | Matches submitted R6 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `80e7749b337f8e874d6895c2b5e9492dbc0f0eeed3b226b870b16485ddf0b1d1` | Reports 13 passed, 0 failed, exit 0; not rerun |
| `.bridge/report.md` | `08099b57a75da14615be537e3ef4a48fa7465d2794067c828d40b40ac1c0f4e8` | R6 handoff and hashes present |

## Finding

### G4-R10-R6-01 — BLOCKER: The role allowlist mapping remains mutable at module scope

R6 changes the individual method collections to `frozenset`, but `ALLOWED_METHODS_BY_ROLE` itself remains a module-level mutable `dict` (`src/tools/gate4_preflight_runner.py`, around line 77). The proxy factory reads this mapping whenever it creates a proxy (`create_audited_proxy()`, around line 393). A caller can therefore replace a role's entire value before proxy construction, for example:

```python
runner_module.ALLOWED_METHODS_BY_ROLE["Timeline"] = frozenset({"AppendToTimeline"})
```

New Timeline proxies will then authorize the injected method. Freezing each set does not freeze the mapping that selects the policy, so the claimed immutable, fixed role policy is not enforced.

**Required:** Make the role-to-allowlist mapping immutable and inaccessible to caller mutation (for example, a `MappingProxyType` over a private mapping, or a closure-owned immutable mapping). Ensure proxy creation uses that fixed policy and add a regression test proving attempts to replace/add role mappings fail and cannot authorize a blocked method.

## Decision and lifecycle state

- Gate 4 R10 Revision R6: `CHANGES_REQUIRED`; do not use the runner for a live preflight until this finding is resolved and reviewed.
- The submitted 13/13 test result is acknowledged as reported; this review did not rerun tests. The evidence log hash is identical to the prior R5 submission; this review does not infer from that alone whether the run was repeated.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
