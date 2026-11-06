# Static and Evidence Review — G07-07B7-LIVE-IMPL Revision R5

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and stored-evidence review only. No tests, Resolve application, IPC, live dispatch, or network operation was run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `.bridge/report.md` | `db8d8c74be308de959fb6051476a1b12c1c237bd4a4c35673a0ddde705caafc0` | Hash matches |
| `src/services/live_resolve_canary_adapter.py` | `bd9c56e856c5b44172b0a1e45e13938f08a262a3d065141f0fdb195e4fa6a6ec` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `6bc47ef7751e9fa8905653762dbfad43ceb4fcdaf39252546e48b61000f5b77d` | Hash matches |
| Focused log | `154e55b895a06ba2dba434e24b4c1be9440e9fa7b5a0df1a60d324c3b6456c6e` | Hash matches; stored log reports 45/45, not rerun |
| Combined regression log | `56969cfe83942a11c683fee56536b6d18fac589e2854e5cf3b651095ab393621` | Hash matches; stored log reports 225/225, not rerun |

The stored logs' final summaries match the report counts. This verifies the submitted artifacts and stored evidence, not the original execution environment or live Resolve behavior.

## R4 finding closure check

The R5 change now requires a `PlannedTimelineAction` when the WAL contains `ACTION_PENDING`, reconstructs and verifies the canonical payload digest against the signed authorization, and compares the supplied action ID with PENDING and any FAILED/COMMITTED outcome. This closes the R4 case where `execute_restart_repair(authorization)` accepted a WAL action ID without requiring an action object.

However, the supplied action ID itself is not cryptographically bound to that authorization. The residual issue is recorded below.

## Finding

### LIVE-IMPL-R5-01 — BLOCKER: Supplied `action_id` is still caller-selected and outside the signed payload

**Location:** `src/services/live_resolve_canary_adapter.py:2292-2348`; `src/core/contracts/trial_authorization_contracts.py:699-723, 974-1058`.

R5 requires `action` and compares `action.action_id` with WAL `action_id`, but `PlannedTimelineAction` is a caller-constructible frozen DTO, not a signed capability. `assemble_canonical_action_payload()` validates its action parameters against the authorization, yet its exact 16-key payload omits `action_id`; `canonical_bytes_for_auth_hmac()` likewise signs the authorization's 32 fields and has no action-ID claim. The R5 digest check therefore authenticates the action's type, geometry, source, target, and other payload fields, but not its ID.

A caller can provide a new `PlannedTimelineAction` with the same authorized 16 payload values and a substituted non-empty `action_id`. The assembler produces the same authorized digest, and the repair path then accepts the substituted ID if the WAL PENDING and COMMITTED/FAILED records carry that same value. Requiring an action object prevents omission but does not establish that its ID is the one authorized by the signed plan. The R5 report's description of a “Cryptographic Action-to-Authorization Binding” overstates what the code binds.

**Required correction:** Bind the action ID to a trusted, independently authenticated claim. For example, include it in the signed authorization/plan contract and canonical HMAC input, or verify it against a canonical signed plan whose digest is already authenticated. Then compare that authoritative value with the supplied action and every WAL record. Do not rely on equality between caller-supplied action data and caller-controlled WAL data. Add a negative case where all 16 authorized payload fields remain unchanged but both the supplied action and WAL use a substituted action ID; repair must stay in doubt and retain the target lock.

## Decision and gate state

R5 fixes the missing-action-argument path from R4, and the submitted hashes and stored test counts are consistent. The action identity remains unauthenticated, so Gate 3 Revision R5 is **not accepted** pending correction.

Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain **OPEN**. This review performed no Resolve launch, IPC, or live dispatch.
