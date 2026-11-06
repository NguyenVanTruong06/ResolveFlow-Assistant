# G07-07B6-I4 Revision R3 Code Review

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static code review; no tests, Resolve application, IPC, FusionScript, or network calls were run.  
**Basis:** R2 review and the R3 source, tests, report, and submitted logs.

## Artifact verification

The submitted hashes match the current files:

| Artifact | SHA-256 |
|---|---|
| `src/services/isolated_resolve_trial_adapter.py` | `922f208a5a5c9de77017656839d105bdba0d872209112e181cd5701b17f33814` |
| `tests/services/test_isolated_resolve_trial_adapter.py` | `7b5ee2330d6616f37ba861ea27269935f59b8771fce031d4107d2238442459d6` |
| `.bridge/report.md` | `c89fc7441813e91f4210b0851f8d1e5bd820f64f91646238003fe82e9fccdd87` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_focused.log` | `daa0fa8893be275fc315516ca1d454144b1f883d2f8059962f62cf5217e713f9` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_combined.log` | `d29910721519ba383b8d0048e4a34d6b5a5f7035f756e252830dcd29539dac8f` |

The supplied logs end with 29 focused and 158 combined tests passing. I read the submitted outputs; I did not rerun them.

## R2 findings reassessed

- **I4-R2-01:** The existing-journal branch now exits before the clean-rejection handler and preserves the current target lock. The parameterized history test asserts retention.
- **I4-R2-02:** The adapter now writes `RESERVED`/`ACTIVE` to the durable ledger before WAL pending, leaves it ACTIVE on uncertain outcomes, and blocks later adapter admission when the ledger is ACTIVE or EXHAUSTED. The fresh-coordinator test exercises this behavior.
- **I4-R2-03:** The commit-persistence handler now surfaces both commit and coordinator-latch errors and reports `LOCKED` if the in-memory latch fails.

These changes address the R2 findings at the same-process/adapter-admission level. The following remaining contract defects prevent acceptance.

## Findings

### I4-R3-01 — BLOCKER: Ledger is exhausted without a durable terminal receipt and is bound to the wrong identifier

**Location:** `src/services/isolated_resolve_trial_adapter.py:447-461`; baseline: `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md:753-789, 811, 881`.

After writing ACTION_COMMITTED, the adapter immediately transitions the ledger to EXHAUSTED. It does not create or durably persist a `TrialExecutionReceipt`/`ExecutionReceipt` in this path. It passes `authorization.authorization_id` as `terminal_receipt_id`, while the accepted design defines `receipt_id == execution_id` and the ledger's `terminal_receipt_id` as the receipt's ID. The design also keeps `authorization_id` distinct from receipt identity.

This can leave a durable EXHAUSTED ledger that names a nonexistent receipt and cannot satisfy the accepted cross-record identity checks. A committed WAL record by itself is not the required terminal receipt.

**Required correction:** Do not mark EXHAUSTED until the terminal receipt is durably written and read back successfully. Use the receipt's actual `receipt_id` (which must equal `execution_id`) as `terminal_receipt_id`. If receipt persistence is outside this slice's approved API/scope, leave the ledger ACTIVE and report the success path as not terminalized; do not synthesize a receipt ID from `authorization_id`.

### I4-R3-02 — HIGH: Reusing a RESERVED ledger checks only execution_id, not its full identity binding

**Location:** `src/services/isolated_resolve_trial_adapter.py:217-235, 286-305`.

Phase 1A allows an existing RESERVED ledger when `existing_ledger.execution_id == execution_id`. Phase 2 then skips `reserve_target()` and calls `activate_target()`, whose I3 implementation checks the execution ID but does not compare the remaining ledger identity fields. The adapter never verifies that the existing reservation's `authorization_id`, `approval_id`, `proposal_id`, `proposal_revision`, and `plan_idempotency_key` match the newly bound authorization.

If a stale or inconsistent RESERVED record shares the execution ID but differs in another authority-bearing field, the adapter can activate it and proceed to dispatch under a different authorization binding than the durable reservation records.

**Required correction:** Before activation, compare the complete durable ledger identity tuple against the bound authorization contract and the action's target. Reject any mismatch without dispatch or ledger mutation. Add a test with matching execution ID but a changed authorization/approval/proposal/plan field.

## Positive observations

- The three R2 changes are visible in source: pre-existing journals no longer flow through the clean unlock handler; durable ACTIVE state is written before ACTION_PENDING; and commit/latch errors are both surfaced.
- The source START record remains compatible with the current engine schema.
- The adapter remains behind the exact fake gateway tripwire; the I4 files and supplied logs match the handoff hashes.

## Decision and safety state

**CHANGES_REQUIRED.** Resolve I4-R3-01 and I4-R3-02 and submit another revision for review. The submitted tests and logs do not establish a valid receipt-to-ledger terminal binding or full identity equality for an existing reservation.

This review does not accept I4 or authorize UI/production wiring, later implementation slices, or live Resolve canary. Preserve `ready_to_execute=False`; Gate G07 and milestones M1/M2/M3 remain OPEN. No source/test files were changed and no tests were run during this review.
