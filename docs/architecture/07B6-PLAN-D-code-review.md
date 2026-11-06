# G07-07B6-PLAN-D — Codex Static Plan Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of the G07-07B6-I implementation plan against the submitted workorder and accepted 07B5/R48 baseline. No tests, prototype validators, Resolve, IPC, FusionScript, application, AI, or network operations were run. No files under `src/` or `tests/` were changed.

## Reviewed inputs and integrity

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `67e671a041228d3db388d6c87f489de516d07be91519ac23e6a2186803b44871`
- `.bridge/report.md`: `8a67e54f014d127a79fa9c836152d867428486c53cae9b227e448d0416fe57c4`
- `docs/architecture/07B6-mock-only-implementation-plan.md`: `cb9762ff3a55df9e2327f42310272c208fabf563bdb39557b0169c95b045852d`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `e3dc16133e68648535f64dfc03592ed2f449ff6ae50438605e09d7f074df2132`
- `docs/architecture/07B5-D-R48-code-review.md`: `dc39ee25c013092a61849d7aaf6363f5d804c11eb459711ab329c13bd3ae23af`

## Findings

### B6-PLAN-01 — Caller-supplied keyring contradicts coordinator-owned trust authority — BLOCKER

The plan gives `RecoveryCoordinator.__init__(target_dir, keyring: TrialKeyringAuthority)` and gives `IsolatedResolveTrialAdapter.__init__(..., keyring: TrialKeyringAuthority, ...)` (plan lines 131–132 and 156–157). This makes the security-sensitive keyring an injectable dependency at both public boundaries.

The accepted design explicitly removes caller-supplied keyring/trust-anchor inputs: the coordinator loads its own `TrialKeyringAuthority` from the local keystore and performs authorization binding through `coordinator.bind_signed_authorization(...)` (07B5 lines 1064–1078). Its source-aligned constructor takes the trusted directory paths plus `keystore_path` and creates `self._keyring` internally (07B5 lines 2429–2444). Passing a different keyring to the adapter also creates a split trust authority between authorization verification and operator reconciliation.

**Required correction:** Specify the coordinator constructor with the accepted directory paths and `keystore_path`, with keyring construction and use private to the coordinator. Remove `keyring` from the adapter constructor; the adapter must use the coordinator's narrow authorization/verification API and must not receive key material or an independently selectable trust store. Add a negative acceptance case proving a caller cannot inject or substitute a keyring.

### B6-PLAN-02 — Adapter API bypasses the signed 16-field action assembler — BLOCKER

The plan exposes `execute_isolated_action(authorization, action_type: str, action_params: dict)` (plan lines 159–164), then describes writing those values to WAL and dispatching them. It does not require a `PlannedTimelineAction`, call the single `assemble_canonical_action_payload(action, auth, execution_id)` entry point, or check the resulting 16-key payload digest against the signed authorization before WAL admission/dispatch.

The accepted baseline makes that assembler the sole source of the exact 16-field action payload and requires strict schema/type checks and execution identity binding (07B5 lines 619–680). The admission contract recomputes the payload digest and compares it with `auth.action_payload_digest_sha256` before dispatch (07B5 lines 728–741). A generic caller-provided dictionary leaves the plan's most important authorization-to-action binding unspecified, even if the gateway is fake.

**Required correction:** Change the adapter API to accept the approved `PlannedTimelineAction` and the coordinator-bound authorization/execution context. Require the shared canonical assembler and digest equality before writing `ACTION_PENDING`; pass only the resulting immutable/canonical payload to the fake gateway. Add negative cases for missing/extra keys, altered non-geometry fields, action ID/index mismatch, and digest mismatch, all asserting zero gateway dispatch and no false terminal-success record.

### B6-PLAN-03 — Test matrix and report overstate per-module coverage — HIGH

The report claims “5 test groups per module” (report lines 36–42), but the plan's matrix has only three I1 cases (plan lines 225–228) and three I4 cases (plan lines 244–246); restart/recovery, lock contention, and cleanup-failure groups are not allocated to these modules. The workorder requires a finite matrix that covers those classes while keeping Resolve behind a fake gateway (`.bridge/task.md`, section 2.2), but does not require every category to apply identically to every module.

**Required correction:** Make the claim accurate by either (a) adding explicit applicable test cases for I1 and I4, or (b) describing the matrix as cross-module coverage and listing each group's owner/module, including an explicit “not applicable” rationale where appropriate. Ensure adapter tests cover failure/cleanup and WAL terminal behavior, not just the successful START/PENDING/COMMITTED path. Do not report a uniform five groups per module unless the matrix actually enumerates them.

## Safety and scope assessment

The plan correctly preserves the four-production/four-test-file allowlist, forbids edits to UI, the execution-engine tripwire, and live Resolve bridges, keeps the adapter unwired from production/UI paths, separates mock evidence from desktop and live-canary evidence, and reserves `G07-07B7-LIVE` for explicit written user authorization. The plan and report also retain `ready_to_execute=False` and Gate G07/M1/M2/M3 OPEN. These controls are necessary and remain unchanged, but they do not resolve the authority and action-binding blockers above.

## Decision

`G07-07B6-PLAN-D` is **CHANGES_REQUIRED**. Revise the plan and its report to close B6-PLAN-01 through B6-PLAN-03, then resubmit for static review. This review does not authorize implementation. Until the plan is accepted and a separate bounded workorder is issued, keep `ready_to_execute=False`, Gate G07 OPEN, and M1/M2/M3 OPEN. No live Resolve canary is authorized.

## Verification boundary

This was a document/source-text review only. Reported prototype or test results were not run or independently verified. No production or test files were edited, and no Git state-changing operation was performed.
