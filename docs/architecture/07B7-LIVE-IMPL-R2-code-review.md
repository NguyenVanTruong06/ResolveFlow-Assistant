# Static and Evidence Review — G07-07B7-LIVE-IMPL Revision R2

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and stored-evidence review. No tests were run. No Resolve application, IPC, live dispatch, or network operation was performed.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `src/services/live_resolve_canary_adapter.py` | `13624cd057d69cf2f957b232ecad60d1a0d3be688713c90c7e6edaf4f127f690` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `b5f578b4c8e48c106ee454f7999ccf908128e9b54e2e9811a3a551784226acd5` | Hash matches |
| `.bridge/report.md` | `2329b1caff4a828259e3480a910beda58e2f9b74ad081af77e88867eb081a9aa` | Hash matches |
| Focused test log | `80367c65a07ed19ff3200cb7303aea4538d42cc6db817cb6395f678e5b72af47` | Hash matches; stored log reports 38/38, not rerun |
| Combined regression log | `48933229f7714a591c86a8f66dfee7ed0b31b51aa21dc52f82a930d2916ee8ef` | Hash matches; stored log reports 218/218, not rerun |

The stored logs' final summaries agree with the counts in the handoff. These are evidence-file checks only; they do not independently reproduce the test runs or prove live Resolve behavior. R1 findings were checked against the R2 source, along with the accepted 07B7 plan's authorization and restart-repair requirements.

## Findings

### LIVE-IMPL-R2-01 — BLOCKER: Live dispatch accepts a typed authorization without verifying its signature

**Location:** `src/services/live_resolve_canary_adapter.py:713-737, 1152-1246, 1890-1905`; authoritative verifier exists at `src/services/durable_trial_target_ledger.py:1376-1408`.

`execute_live_canary_action()` checks only `isinstance(authorization, TrialExecutionAuthorization)`, then uses its claims for project/timeline selection, geometry, source path, and dispatch. `_validate_tier1_admission()` checks the claim values and action-payload digest but never verifies `authorization_hmac`. The adapter does not call `RecoveryCoordinator.bind_signed_authorization()`, which is the available code path that validates the 32-field HMAC against the coordinator-owned keyring. `execute_restart_repair()` has the same type-only trust boundary; its proof verifiers likewise check the DTO type, not that the supplied authorization is the coordinator-bound signed authorization.

A caller can therefore supply a constructible authorization object with self-selected claims and a self-consistent payload digest, and pass the adapter's checks without proving that the claims were authorized by the trusted signer. The user's Gate 2 permission does not substitute for cryptographic validation of each execution authorization.

**Required correction:** At the adapter's public execution and repair boundaries, call the coordinator's signed-authorization verifier and require the returned/bound contract to match the exact authorization being used. Reject unbound, expired, revoked, or signature-invalid authorizations before any WAL write or Resolve dispatch. Ensure the proof verification and terminalization paths use that same coordinator-owned binding.

### LIVE-IMPL-R2-02 — BLOCKER: Restart repair still does not enforce the complete WAL state machine or ledger identity before terminalization

**Location:** `src/services/live_resolve_canary_adapter.py:1922-1985, 1987-2051, 2097-2301`.

The repair path validates sequence continuity, that the first entry is `START`, and the `START` identity envelope. It then chooses repair branches using stage membership (`"ACTION_COMMITTED" in stages`, `"FINISH" in stages`) rather than validating the complete ordered stage grammar, one-to-one `ACTION_PENDING`/outcome payload equality, and terminal-note/state allowlists. Thus malformed but sequential histories can reach a terminalization branch without proving a legal transition or a committed action bound to the authorization. Boundary 5 also permits `ABORTED` without proof whenever the note is not `HALTED_ON_ACTION_FAILURE` and the stage list contains neither `PENDING` spelling; it does not require the exact pre-dispatch cancellation tuple.

For the no-journal boundary, `existing_ledger` is loaded at line 1923 but never checked before the code writes `START`, `FINISH(ABORTED)`, publishes a receipt, and attempts to exhaust the ledger. The repair path does not first require a `RESERVED`/`ACTIVE` ledger whose full identity tuple matches the supplied authorization. This can create terminal history for a state that was never proven to be the claimed crash boundary; a later ledger error occurs only after the WAL and receipt writes.

**Required correction:** Under the target lock, validate the full bounded WAL grammar and every record's identity and payload bindings before branching. Require an exact valid ledger state and full identity match for each repair boundary, including the no-journal case. Accept only the explicitly authorized terminal tuples; all other histories must remain locked and in doubt without writing terminal WAL/receipt state.

### LIVE-IMPL-R2-03 — HIGH: Exclusive-create proof fallback exposes a partially written final record

**Location:** `src/services/live_resolve_canary_adapter.py:2361-2393`.

When `os.link()` is unavailable with `ENOSYS` or `EXDEV`, the fallback opens the final destination with `O_CREAT | O_EXCL` and writes the record directly into that visible name. This preserves no-overwrite semantics but is not atomic publication of a complete record. A process or machine failure after creation and before the full write/fsync leaves a partial destination. Subsequent consume-once checks treat that path as already consumed, while restart repair cannot load a valid proof; the clean proof can no longer be retried and the target remains stuck in doubt.

**Required correction:** Do not publish an incompletely written final file. Use a supported atomic no-replace publication primitive for the completed, fsynced temporary file; if none is available, fail closed before creating the final name and retain the temporary evidence for controlled repair. Add crash-boundary coverage for the fallback path.

## Decision and gate state

Revision R2 closes the five R1 findings at the inspected call sites for proof no-replace handling, fresh physical readback on the stated repair branches, explicit authorization parameters, media identity checks, and descriptor-relative receipt publication/readback. However, the missing signed-authorization verification and incomplete restart state/ledger validation remain blockers; the proof fallback also lacks atomic complete-record publication.

Gate 3 Revision R2 is **not accepted**. Gate 4 preflight, Gate 5 workorder issuance, Gate 6 supervised dispatch, and Gate 7 dual-attestation remain pending. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. No live Resolve operation was authorized or performed by this review.
