# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R10

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED` — do not use this runner for a live Gate 4 preflight yet.  
**Review mode:** Static review of the allowlisted source/tests, reported hashes, and submitted test log. The test suite was not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3b84b7891b8eb298b4184f4fedf1b2121e3f19bbaeef2b14c2e811f2d165d10c` | R10 local-only workorder and allowlist match submission |
| `src/tools/gate4_preflight_runner.py` | `015f9e05e215b75beb7f397992240e5a99a4c66b2b20380c347c83d857bc5462` | Submitted hash matches inspected source |
| `tests/tools/test_gate4_preflight_runner.py` | `c564a5033ac89b9acd70946dabd79ad97f11efbc3f78aa39eddf0164ea593f9f` | Submitted hash matches inspected tests |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R10/pytest.log` | `c479e1cd74412cbebab65f835d0732bfafbf9bf3fdc32f6e3a8dffd68655f936` | Log reports the authorized focused suite: 9 passed, 0 failed, exit 0 |
| `.bridge/report.md` | `b986397b51f64bb1c92bd44f0fd363e586e0294125dd5920c97e936bc9d622b1` | R10 handoff and hashes are present |

## Findings

### G4-R10-01 — BLOCKER: Raw adapter objects escape the audited proxy

`AuditedResolveProxy` stores the raw adapter in the directly accessible `self._target` attribute (`src/tools/gate4_preflight_runner.py`, `AuditedResolveProxy.__init__`). Its `_wrap_return()` wraps a list only for `ImportMedia`; other list results are returned unchanged. In particular, `Timeline.GetItemListInTrack()` can return raw MediaPoolItem/clip objects that callers can use outside the audited allowlist. A caller can also access `proxy._target` directly and invoke methods without logging or policy checks. The current escape test covers one unclassified object returned by a method, but not direct access to `_target` or raw objects inside lists/dicts.

**Required:** Prevent ordinary public access to the underlying adapter and logger, and ensure every nested object in every supported return container is wrapped or rejected. Add regression tests for direct raw-target access and raw objects nested in list/dict results. Do not claim protection from arbitrary code execution in the same Python process; the contract here is that the runner's normal API surface cannot bypass its proxy.

### G4-R10-02 — HIGH: Preflight can report success with failed identity/profile/topology predicates

`Gate4PreflightRunner.run()` records project/timeline names and UIDs, profile settings, and track counts, but its success checks cover only empty V1/A1 lists, equal start/end frames, and zero markers (`run()`, around lines 696–780). It never compares names to the nonce-derived target names, rejects blacklisted/empty UIDs, checks project/timeline width-height-FPS values, or enforces video track count exactly one and audio count at most one. The method can therefore return `success=True` and `zero_dispatch_scope` even when mandatory Gate 4 predicates fail.

**Required:** Validate every required Gate 4 predicate before returning success, with exact expected identities/profile/topology and blacklist checks. Add negative fake-adapter tests for each failed class and assert no success evidence is returned.

### G4-R10-03 — HIGH: Audit verifier trusts terminal counters instead of deriving them from records

`verify_audit_log_chain()` validates record hashes and links, then takes `measured_*` counts directly from the terminal record (`details.get(...)`, around lines 608–617). It does not derive call, blocked-method, dispatch-attempt, or insertion-attempt counts from the verified event records or compare the terminal summary with those derived counts. A hash-consistent log whose terminal counters contradict its events can therefore be reported as valid with the terminal's uncorroborated zero counts. The verifier also accepts a structurally valid terminal without requiring the first record to be `AUDIT_START`.

**Required:** Derive counters from verified call/blocked-attempt records, require exactly one start record at sequence 0 and exactly one terminal record at the end, and fail verification if terminal summary fields differ from the derived totals. Add fixtures that alter terminal counts and recompute the record chain, and that omit/replace the start record.

### G4-R10-04 — MEDIUM: Insecure audit-directory mode is silently tolerated

`HashChainedAuditLogger.__init__()` attempts to set the audit directory to mode `0700` but suppresses every `OSError` from `os.chmod()` (`around lines 187–193). It then proceeds to create the audit file. If the directory remains accessible to other principals, the workorder's restrictive audit-storage requirement is not met, while logging still appears successful.

**Required:** Verify the resulting directory owner/mode and fail closed if the required protection cannot be established; do not suppress the permission error. Add a fault-injection test proving no adapter call is made and no successful result is produced when directory protection fails.

## Decision and lifecycle state

- Gate 4 R10: `CHANGES_REQUIRED`; no live preflight workorder should consume this runner until the findings are resolved and reviewed.
- The submitted 9/9 test result is acknowledged as reported; this review did not rerun tests.
- R9 remains historical: current empty timeline was documented as verified, while R9 zero-dispatch remains `UNVERIFIED`.
- No Resolve operation or Gate 5 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

