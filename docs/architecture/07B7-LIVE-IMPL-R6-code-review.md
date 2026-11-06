# Static and Evidence Review — G07-07B7-LIVE-IMPL Revision R6

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and stored-evidence review only. No tests, Resolve application, IPC, live dispatch, or network operation was run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `.bridge/report.md` | `4a0dddef227616ba720a2cf074f0fadd585807116db742addb8c4299394530b1` | Hash matches |
| `src/services/live_resolve_canary_adapter.py` | `bef7600e286072e8736451ae1881a7434e19d996fb473adbc752580a0a58f0f6` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `7fd6305804bb4893dcabfd5b24a736d0ad75c85d28739533542d94bd5f38cb2c` | Hash matches |
| Focused log | `4d921c6b80d354988c495bde7cfc457446865dbe6802e8c047619851c45cf5d8` | Hash matches; stored log reports 46/46, not rerun |
| Combined regression log | `457fde55f0ebc2c89640bef07313b2cad5ac06571a311f721f89fbf96e089e50` | Hash matches; stored log reports 226/226, not rerun |
| Prior R5 review | `d2ade7ef7d4dc9a4aee46a015b5194865dba86773782da9ef61d146d98170fb0` | Hash matches |

The stored logs' final summaries match the report counts. This verifies the submitted artifacts and stored evidence, not the original execution environment or live Resolve behavior.

## R5 finding closure check

R6 adds `action_id` to a canonical canary-plan payload and compares its digest with `authorization.plan_digest_sha256` at admission and restart repair. It also retains the required `PlannedTimelineAction` gate and exact comparisons against WAL action IDs. This closes the specific R5 case only if the computed digest is the same canonical digest of the already-approved `TimelineExecutionPlan` that the authorization signer attested.

The source does not establish that equivalence. The remaining contract mismatch is recorded below.

## Finding

### LIVE-IMPL-R6-01 — BLOCKER: The new action-ID digest is not shown to be the signed `TimelineExecutionPlan` digest

**Location:** `src/services/live_resolve_canary_adapter.py:187-250, 1456-1461, 2379-2389`; baseline `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md:543, 732` and `docs/architecture/07B7-live-canary-readiness-plan.md:500`.

R6 computes `authorization.plan_digest_sha256` from a newly defined `CANONICAL_PLAN_PAYLOAD_KEYS` object assembled from one `PlannedTimelineAction` plus selected authorization fields (`planned_actions_count=1`, a fixed schema version, and action/source/target fields). The accepted baseline defines `plan_digest_sha256` as the digest of the actions in the `TimelineExecutionPlan` and requires recomputation from that plan. The adapter does not receive or validate a `TimelineExecutionPlan`, does not call a shared plan-digest serializer, and does not demonstrate that its new 16-field payload is the canonical serialization of that approved plan.

As a result, the comparison to the HMAC-protected `authorization.plan_digest_sha256` proves only that the supplied action matches this adapter-local encoding if the signer used the same encoding. It does not, from the reviewed source, prove that `action_id` belongs to the approved `TimelineExecutionPlan`. If the signer continues to produce the baseline plan digest, legitimate authorizations may fail; if the signer instead signs this adapter-local digest without recomputing the approved plan, the authorization no longer proves the approved plan's identity. The new check therefore does not yet establish the trust link R5-01 requires.

**Required correction:** Recompute the digest through the canonical serializer for the actual approved `TimelineExecutionPlan`, including its action IDs, and compare that value with the signed claim. Supply the verified immutable plan (or a narrowly scoped authenticated plan snapshot) to admission and restart repair. If the digest contract must change, update and review the shared authorization/minter contract explicitly; do not redefine `plan_digest_sha256` only inside this adapter. Add a test using a plan produced through the real binder/approved serializer, plus a negative case that substitutes `action_id` while keeping all other plan data fixed.

## Decision and gate state

The hashes and stored test summaries are consistent, and R6 adds the intended action-ID comparison. The code does not yet demonstrate that the digest it recomputes is the accepted, source-derived `TimelineExecutionPlan` digest. Gate 3 Revision R6 is **not accepted** pending alignment with that plan contract.

Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain **OPEN**. This review performed no Resolve launch, IPC, or live dispatch.
