# Static Plan Review — G07-07B7-PLAN-D Revision R3

**Decision: CHANGES_REQUIRED**  
**Plan reviewed:** `docs/architecture/07B7-live-canary-readiness-plan.md`  
**Plan SHA-256 verified:** `7bbf4e937526d02f036fd23b766358bdb4516cd62c9fd0c9cdee6b958a71ef26`  
**Review scope:** Static review against the R2 findings, accepted 07B5 design, and accepted mock-only 07B6 boundaries. No tests, prototypes, Resolve, IPC, FusionScript, network, or AI were run.

## Summary

R3 resolves the five findings from the R2 review: it requires exact active project/timeline UID checks at admission and immediately before dispatch; defines the 07B5 terminal write order and four crash boundaries with zero-redispatch recovery; standardizes the uppercase 16-hex nonce and project-name regex; correctly describes naming as an admission filter while retaining human-process risk; and consistently names the eight gates from Gate 0 through Gate 7.

One blocker remains in failure terminalization. The plan permits a failed/aborted terminal path without clearly requiring the independent evidence that 07B5 requires after `ACTION_PENDING`, and the specified receipt and ledger values contradict the WAL's `ABORTED` outcome. The recovery table then treats a terminal `FINISHED_FAILED` WAL as sufficient authority to abort the receipt and exhaust the ledger. This must be made outcome-consistent and evidence-gated before acceptance.

## Finding

### B7-R3-01 — BLOCKER: Gate failed terminalization on independent clean-failure evidence and keep WAL, receipt, and ledger outcomes identical

**Locations:** Plan §5.2.2 lines 238–248; §5.3, especially crash boundary 2 and the restart invariants at lines 262 and 269; §6.2 lines 276–287.

The plan allows `FINISH.final_receipt_state="ABORTED"` with `note="HALTED_ON_ACTION_FAILURE"` (line 238–239). But the receipt writer is then specified with `state="COMPLETED"` unconditionally (line 242), and the ledger writer always uses `terminal_reason="COMPLETED"` (line 246). This creates a contradictory durable terminal tuple for the stated failure path: WAL says `ABORTED`, while receipt and ledger say successful completion.

There is also an authority gap. Under the accepted 07B5 design, a local WAL record is not independent proof that a post-pending action failed without mutation. A failure after `ACTION_PENDING` can be terminalized as `ABORTED` and release the target only when supported by independently authenticated evidence proving the required clean outcome. Ambiguous outcomes (including timeout, IPC loss, crash, or an unverified API failure) must stay `IN_DOUBT`, keep the ledger `ACTIVE`, retain the target exclusion, and must not be converted to `FINISH(ABORTED)` merely because the WAL says `ACTION_FAILED` or `FINISHED_FAILED`. The R3 crash boundary 2 instead calls the `FINISH` record “authoritative” and repairs `ABORTED`/`EXHAUSTED` from it without stating this independent evidence gate.

Section 6.2 does correctly require an in-doubt latch for timeout, API exception, and IPC crash, but it does not reconcile those rules with the generic failed terminal path and restart repair in §§5.2.2–5.3.

**Required correction:**

1. Define separate terminal branches for success, independently proven clean failure, and ambiguous failure. For any failure after `ACTION_PENDING`, require the exact independent, authenticated proof/evidence contract from 07B5 before writing a terminal `FINISH(ABORTED)` or releasing the target. Treat missing, stale, mismatched, or unverifiable evidence as `IN_DOUBT_LOCKED` with ledger `ACTIVE` and no automatic retry.
2. Derive the receipt state and ledger terminal reason from the validated terminal outcome. For success, require `FINISH(COMPLETED)` + receipt `COMPLETED` + ledger `EXHAUSTED/COMPLETED`. For an independently proven clean abort, require the exact 07B5-approved `FINISH(ABORTED)` + receipt `ABORTED` + corresponding terminal ledger reason. Do not write `COMPLETED` receipt/ledger metadata for an aborted outcome.
3. Update each crash-table row so restart repair validates the full WAL, receipt, ledger, identity, and independent-evidence tuple; the WAL alone must never authorize converting an ambiguous post-pending failure into a clean terminal state. Keep the lock held whenever the tuple cannot be proven consistent.

## Safety-boundary confirmation

This is a documentation-only review. No source or test files were modified and no tests or runtime actions were executed. `ready_to_execute=False` is preserved; Gate G07 and Milestones M1, M2, and M3 remain OPEN. This review does not authorize implementation or live canary execution.
