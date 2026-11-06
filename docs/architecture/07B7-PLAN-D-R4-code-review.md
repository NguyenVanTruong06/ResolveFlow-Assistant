# Static Plan Review — G07-07B7-PLAN-D Revision R4

**Decision: CHANGES_REQUIRED**  
**Plan reviewed:** `docs/architecture/07B7-live-canary-readiness-plan.md`  
**Plan SHA-256 verified:** `d361c8d6c8f663918d56dbd7cbcccffc3154e8daf3d33679d835bec3435c7a1b`  
**Review scope:** Static review against the R3 finding, accepted 07B5 design, and accepted mock-only 07B6 boundaries. No tests, prototypes, Resolve, IPC, FusionScript, network, or AI were run.

## Summary

R4 closes the R3 finding’s main gaps: it separates success, independently proven clean failure, and ambiguous failure; aligns the WAL, receipt, and ledger terminal states; requires an independent HMAC proof with the clean-absence outcome for post-pending abort; and extends restart repair to preserve the lock and avoid redispatch when proof is missing or invalid.

One safety-critical detail remains underspecified: the plan does not define the exact proof schema and full binding to the pending action that restart repair must verify. Requiring a valid HMAC, “six identity fields,” and the clean-absence outcome is not enough to show that the proof applies to this exact authorized action and its payload. Since this proof authorizes `ABORTED`, ledger exhaustion, and target-lock release, the verifier contract must be explicit.

## Finding

### B7-R4-01 — BLOCKER: Bind the independent failure proof to the exact pending action and accepted 07B5 verifier contract

**Locations:** Plan §5.2.2, clean-failure case 2B; §5.3 crash boundaries 1–3; §8 MAT-13. In particular, lines 262–263 and 286–288 require a valid HMAC, six identity fields, and `VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN` before repair can abort and release the target.

R4 does not enumerate the six fields or require the proof record to match the exact `ACTION_PENDING` payload. The accepted 07B5 contract requires strict proof-schema validation and action binding, including the execution/action identity, exact `action_id`, `action_index`, `action_payload_digest_sha256`, target project/timeline, and authorized geometry. It also defines a 16-field `DurableProofRecord` derived from the verified proof. The source contract explicitly rejects a proof whose `action_id` or payload digest does not match the pending action. R4’s wording could therefore be implemented as a signature/outcome check while accepting evidence for a different or altered action.

The proof gate authorizes writing `FINISH(ABORTED)`, publishing an `ABORTED` receipt, exhausting the ledger, and releasing the OS lock. A mismatch here could incorrectly classify a mutated or uncertain action as a clean failure.

**Required correction:** Specify that restart repair uses the accepted 07B5 authoritative verifier and validates the exact bounded proof schema; enumerate every required binding and compare it to the signed authorization and parsed `ACTION_PENDING` record, including at minimum `execution_id`, `authorization_id`, `action_id`, `action_index`, `action_payload_digest_sha256`, target project/timeline UIDs, and the authorized/scanned frame geometry. Require the canonical HMAC and proof-record integrity checks, and accept only the exact `VERIFIED_TARGET_ABSENT_AND_OCCUPANCY_CLEAN` outcome. State explicitly that any missing, extra, stale, replayed, mismatched, or unverifiable field keeps the receipt `EXECUTING`, ledger `ACTIVE`, and target `IN_DOUBT_LOCKED`, with zero redispatch. Reflect these gates in MAT-13 and every restart-repair branch that can release the lock.

## Safety-boundary confirmation

This is a documentation-only review. No source or test files were modified and no tests or runtime actions were executed. `ready_to_execute=False` is preserved; Gate G07 and Milestones M1, M2, and M3 remain OPEN. This review does not authorize implementation or live canary execution.
