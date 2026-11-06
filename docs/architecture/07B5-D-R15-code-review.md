# G07-07B5-D-R15 — Codex Review

**Decision: CHANGES_REQUIRED**  
**Reviewed:** 2026-11-03  
**Submitted report SHA-256:** `32bc1798c84b3aead20e96eccf85504730513271a8bbb52b05bf67cdf99164ae`  
**Design SHA-256:** `f57d5aa0ede467a1551363121293bedca5c8ea23bb0dc393aadcc625591434ff`

## Review scope

Static review only. All four submitted reference hashes were recomputed and match disk. Reviewed the R15 outcome validator, clean-rejection policy, rollback readback record, result classifications and crash matrix against the source-aligned journal contract. No tests, application, Resolve, IPC, FusionScript, or network activity was run.

## Findings

### R15-01 — `ERR_APPEND_TIMELINE_REJECTED` is not proven pre-mutation by the design (blocking)

The R15 `PROVEN_PRE_MUTATION_REJECTION_CODES` includes `ERR_APPEND_TIMELINE_REJECTED`, while its own example describes `AppendToTimeline` returning `False` after the action was dispatched. The WAL validator then treats the producer-supplied `mutation_clean_rejected: true` boolean plus that code as proof of zero mutation and permits `TERMINAL_RECONCILED_FAILED` / target release. The design does not establish that this Resolve response guarantees no side effect, nor does it require a post-call readback for this path. A generic API failure after the durable `ACTION_PENDING` boundary is not inherently a pre-mutation rejection.

**Required:** classify only failures that are proven before IPC dispatch as clean by construction. For any post-dispatch response, including `AppendToTimeline(False)`, require authoritative readback proving no inserted/partial item and safe target occupancy, or classify IN_DOUBT and retain exclusion. Do not let a boolean written by the same producer serve as the proof.

### R15-02 — Persisted readback booleans are not independently verifiable after restart (blocking)

The R15 rollback proof is persisted as ordinary JSONL fields (`physical_readback_item_absent`, `physical_readback_track_occupancy_verified`, frame bounds, target item ID). The existing `ExecutionJournalService` serialization is not an authenticated attestation of the Resolve observation. On restart the proposed validator checks those stored values but does not require a fresh Resolve readback or a trusted signature binding the observation to the timeline UID, exact item UID, frame range, observer, and observation time. A forged or stale WAL record can therefore claim both booleans are true and reach `TERMINAL_RECONCILED_ROLLED_BACK`, allowing the ledger to release the target despite a surviving clip.

**Required:** specify the trust boundary for physical observations. Before releasing exclusion, either perform a fresh authoritative readback on the currently bound project/timeline and exact item identity, or verify a separately authenticated, freshness-bound readback receipt (target UIDs, item UID, observation timestamp/nonce, frame range and result). If neither is available after restart, remain IN_DOUBT. Treat WAL booleans as claims unless independently authenticated.

## Decision and next workorder

R15 closes the structural outcome-field gaps and defines a physical rollback query, but the clean-failure and rollback-release decisions still rely on unauthenticated post-dispatch/WAL assertions. R15 is **not accepted**. Do not start G07-07B6-I. Keep `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN.

The next bounded task is **G07-07B5-D-R16 — Trusted Mutation Outcome & Fresh Rollback Reconciliation Evidence**. The DESIGN ONLY workorder is in `.bridge/task.md`.
