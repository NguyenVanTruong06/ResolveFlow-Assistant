# Static Plan Review — G07-07B7-PLAN-D Revision R6

**Decision: CHANGES_REQUIRED**  
**Plan reviewed:** `docs/architecture/07B7-live-canary-readiness-plan.md`  
**Plan SHA-256 verified:** `000dc9a9f50acd8bbbb42fb3d201f93106f0d2b880b18dd65e3e36a2ae43c96e`  
**Review scope:** Static review against the R5 finding and accepted 07B5 verifier, DTO, and restart-repair contracts. No tests, prototypes, Resolve, IPC, FusionScript, network, or AI were run.

## Summary

R6 resolves the R5 DTO mismatch: it checks `action_id` on the raw proof before persistence, then uses the exact 16-field `DurableProofRecord` and its authorized 16-key payload digest during restart repair. The crash table and MAT-13 reflect this two-stage approach.

Two details still conflict with the accepted 07B5 contract. The plan invokes the single authoritative verifier with the wrong argument list, omitting the action and pending journal record required for action binding. It also requires restart repair to reject a durable proof as stale after 300 seconds, although the 16-field record stores `consumed_at_iso`, not the raw proof’s observation timestamp. Those points need clarification before the plan is implementable without changing the accepted contract.

## Findings

### B7-R6-01 — BLOCKER: Use the accepted authoritative verifier signature with action and pending entry

**Location:** Plan §5.2.2, Stage 1, line 269.

The plan calls `RecoveryCoordinator.verify_and_consume_proof(raw_proof)`. The accepted 07B5 API is `verify_and_consume_proof(raw_proof, action, pending_entry)`. The action and parsed `ACTION_PENDING` record are required inputs to the authoritative verifier so it can check the proof’s `action_id`, payload digest, and target binding before it records consumed evidence. The next bullet describes a direct `raw_proof["action_id"]` comparison, but the stated method call does not provide the `pending_entry` (or the `PlannedTimelineAction`) that the contract requires.

**Required correction:** Specify the accepted call with all required inputs, for example `verify_and_consume_proof(raw_proof, action, pending_entry)`, and state that the verifier performs raw proof validation, exact action binding, semantic outcome derivation, and durable one-time evidence recording before terminalization. Keep the explicit `action_id` equality requirement as a verifier invariant, not as an unconnected caller-side check.

### B7-R6-02 — HIGH: Define restart freshness using fields actually retained by the 16-field proof record

**Locations:** Plan §5.2.2, Stage 2 freshness rule, line 292; crash-repair branches in §5.3.

Stage 2 reads only the accepted 16-field `DurableProofRecord`, which retains `consumed_at_iso` and `raw_proof_digest_sha256` but not the raw proof’s `observation_timestamp_iso`. The plan nevertheless says restart repair must reject a proof whose age exceeds the raw proof freshness TTL of 300 seconds. It does not define which timestamp is used. `consumed_at_iso` is when evidence was durably recorded; it does not establish when Resolve was observed. Rechecking that field against 300 seconds would also cause an otherwise valid durable record to become unusable after a prolonged restart outage.

The accepted verifier checks raw observation freshness when Stage 1 validates and consumes the raw proof. Restart repair can then authenticate the durable record and its HMAC as evidence of that completed validation. If restart is intended to re-evaluate observation age, the current DTO does not preserve the needed timestamp.

**Required correction:** State that the 300-second freshness check is performed on the raw proof during Stage 1 before durable consumption, and that restart repair validates the resulting durable record’s exact schema, HMAC, bindings, and one-time ledger state without reapplying raw-observation TTL. If the plan requires freshness to be re-evaluated after restart, first revise and review the 07B5 durable proof schema to retain and authenticate the original observation timestamp; do not infer it from `consumed_at_iso`.

## Safety-boundary confirmation

This is a documentation-only review. No source or test files were modified and no tests or runtime actions were executed. `ready_to_execute=False` is preserved; Gate G07 and Milestones M1, M2, and M3 remain OPEN. This review does not authorize implementation or live canary execution.
