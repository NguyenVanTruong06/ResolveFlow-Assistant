# Static Plan Review — G07-07B7-PLAN-D Revision R5

**Decision: CHANGES_REQUIRED**  
**Plan reviewed:** `docs/architecture/07B7-live-canary-readiness-plan.md`  
**Plan SHA-256 verified:** `3f63a0701f465b4f007cf307bb8ea8e91c2b547fcb8164448b44123f42d839ef`  
**Review scope:** Static review against the R4 finding and the accepted 07B5 verifier/DTO contract. No tests, prototypes, Resolve, IPC, FusionScript, network, or AI were run.

## Summary

R5 substantially closes R4: it defines the 16-field durable proof record, canonical HMAC verification, proof freshness, the nine stated bindings, the clean-absence outcome, and fail-closed restart behavior with zero redispatch. MAT-13 and the crash table carry those requirements forward.

One contract inconsistency remains: the plan asks the restart verifier to read `record.action_id`, but the accepted 07B5 `DurableProofRecord` 16-field schema has no `action_id` field. As written, the exact 16-field DTO cannot satisfy all nine checks. The plan needs to distinguish fields checked on the original raw proof from bindings retained and checked from the durable record during restart repair.

## Finding

### B7-R5-01 — HIGH: Reconcile the required `action_id` check with the exact 16-field DurableProofRecord schema

**Locations:** Plan §5.2.2, case 2B, lines 265–276; §5.3 crash boundaries 1–3; MAT-13, line 395.

The plan declares an exact `DurableProofRecord` schema of 16 fields, then requires `record.action_id == pending_entry.action_params["action_id"]` as one of the nine restart bindings. In the accepted 07B5 schema, the 16 fields are `schema_version`, `proof_record_id`, `execution_id`, `action_index`, `authorization_id`, `action_payload_digest_sha256`, `target_project_uid`, `target_timeline_uid`, `scanned_frame_start`, `scanned_frame_end`, `verified_outcome`, `raw_proof_id`, `raw_proof_digest_sha256`, `consumed_at_iso`, `signer_key_id`, and `record_hmac_sha256`; there is no `action_id` member.

This creates an impossible or ambiguous implementation requirement: either the code accesses a field that cannot exist in the accepted DTO, or an implementer changes the durable schema outside the accepted contract. The raw proof does contain `action_id`, and 07B5 requires exact comparison with the planned action before consumption; the durable record also retains the authorized full-payload digest, which binds the 16-key payload including `action_id`.

**Required correction:** Keep the 07B5 16-field DTO exact. State that the raw proof’s `action_id` is checked directly against the planned action before it is consumed and persisted. During restart repair, validate the `DurableProofRecord` schema/HMAC and compare its `action_payload_digest_sha256` to both the signed authorization and the recomputed full digest of `ACTION_PENDING.action_params`; also validate `raw_proof_digest_sha256` according to the accepted recovery contract. Update the nine-point list and MAT-13 to describe this two-stage binding accurately. If the design instead intends to add `action_id` to `DurableProofRecord`, that is a 07B5 schema change requiring an explicit architecture revision and review, not an implicit plan interpretation.

## Safety-boundary confirmation

This is a documentation-only review. No source or test files were modified and no tests or runtime actions were executed. `ready_to_execute=False` is preserved; Gate G07 and Milestones M1, M2, and M3 remain OPEN. This review does not authorize implementation or live canary execution.
