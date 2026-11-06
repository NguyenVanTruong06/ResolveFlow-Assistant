# G07-07B6-PLAN-D-R1 — Codex Static Plan Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of plan V2 against the workorder, prior review, and accepted 07B5 architecture. This review did not run tests, prototype validators, Resolve, IPC, FusionScript, the application, AI, or network operations. No files under `src/` or `tests/` were changed.

## Reviewed inputs and integrity

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `67e671a041228d3db388d6c87f489de516d07be91519ac23e6a2186803b44871`
- `.bridge/report.md`: `52b0b53cab52c6347e12a014fcb32b5e2f68fd4a604106b317fb45faeae02308`
- `docs/architecture/07B6-mock-only-implementation-plan.md` (V2): `ddd267b7a174989e77bace742df380b78f3cd0f0e3d23baaf48a83d046ef19d3`
- `docs/architecture/07B6-PLAN-D-code-review.md`: `8f1ce6e3324143cfa5f96e83a0b87f037288601224007f09359a58cc231fc822`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `e3dc16133e68648535f64dfc03592ed2f449ff6ae50438605e09d7f074df2132`

## Findings

### B6-R1-01 — Authorization DTO and verifier do not preserve the accepted signed contract — BLOCKER

V2 correctly closes the two caller-injection paths from the prior review: `RecoveryCoordinator` constructs its own keyring (plan lines 128–140), and the adapter no longer receives a keyring (lines 161–174). However, the `TrialExecutionAuthorization` DTO still lists only a subset of its fields (lines 84–87). It omits required signed fields including `key_id`, `execution_id`, `token_nonce`, approval/proposal/plan digests, source path and digest, FPS bindings, authorized action details, and issued/valid-until timestamps as named by the accepted 32-field contract (07B5 lines 524–576).

The adapter flow then accepts that DTO and goes from lock check to action assembly/digest check, WAL writes, and gateway dispatch (plan lines 176–183). It does not require `coordinator.bind_signed_authorization(authorization)` or otherwise establish that the complete authorization HMAC, key status, and validity window were verified before dispatch. The existence of a coordinator binding API elsewhere in the plan (line 149) does not bind this execution path to it.

**Required correction:** Specify the complete 32-field frozen DTO exactly as the accepted design defines it. Make the adapter's pre-dispatch sequence require coordinator-owned verification/binding of that exact authorization and execution identity, including HMAC, revocation, and validity checks, before payload assembly, WAL `ACTION_PENDING`, or gateway dispatch. Add negative cases for omitted/extra authorization fields, invalid HMAC, revoked/unknown key, expired authorization, and execution/token nonce mismatch; each must prove zero dispatch and no pending WAL admission.

### B6-R1-02 — Post-dispatch gateway exception is incorrectly treated as clean and unlockable — BLOCKER

The I4 failure case says a `FakeResolveGateway` exception is caught, `ACTION_FAILED` is recorded, and the lock is safely released (plan line 274). The adapter flow similarly describes dispatch followed by commit, without defining an in-doubt branch if the gateway raises after accepting or partially processing the request (lines 176–183).

The accepted design explicitly treats post-dispatch rejection/exception as untrusted evidence: after `ACTION_PENDING`, an unauthenticated WAL failure record or producer assertion cannot prove that no mutation occurred. Without independent fresh readback evidence, the outcome must remain `TERMINAL_ACTION_FAILED_IN_DOUBT` and the target exclusion lock must be retained (07B5 lines 1058–1060 and 1062 onward). Releasing the lock here could admit a second action against a target whose first action may have taken effect.

**Required correction:** Split failure tests by phase. A proven pre-dispatch rejection may finish cleanly with no pending record and release the lock. Any exception after gateway dispatch must be classified as in-doubt, preserve target exclusion, and prohibit replay/re-admission absent the accepted proof/reconciliation path. Add assertions for zero false `FINISH`/clean terminal state and retained lock on the ambiguous path.

### B6-R1-03 — The listed test cases total 22, not the claimed 23 — MEDIUM

The report and V2 state that the matrix contains 23 cases and describe allocations I1=5, I2=5, I3=7, I4=5. Those allocations sum to 22. The visible matrix rows enumerate exactly those group counts (plan lines 253–274); no 23rd case is specified.

**Required correction:** Either add and define the missing 23rd case in the table and identify its owning slice, or correct the plan/report count and any handoff summary to 22. Keep the expected case count consistent across the matrix, plan, and report.

## Prior-review closure and safety assessment

V2 closes all three findings from `07B6-PLAN-D-code-review.md`: the coordinator owns its keyring, the adapter accepts a `PlannedTimelineAction` and enforces the canonical 16-field assembler/digest gate, and the matrix is now enumerated per module. The four-production/four-test allowlist and the prohibitions on UI wiring, engine-tripwire edits, and live Resolve integration remain intact. The plan preserves the mock-only boundary, `ready_to_execute=False`, and Gate G07/M1/M2/M3 OPEN.

Those controls do not address B6-R1-01 and B6-R1-02. Therefore this plan is not ready to authorize the implementation slice.

## Decision

`G07-07B6-PLAN-D` is **CHANGES_REQUIRED**. Revise the plan and report to close B6-R1-01 through B6-R1-03, then resubmit for static review. This review grants no implementation or live-Resolve authority. Until a revised plan is accepted and a separate bounded workorder is issued, keep `ready_to_execute=False`, Gate G07 OPEN, and M1/M2/M3 OPEN.

## Verification boundary

This was a document-only static review. Reported prototype/test results were not run or independently verified. No source or test files were edited, and no Git state-changing operation was performed.
