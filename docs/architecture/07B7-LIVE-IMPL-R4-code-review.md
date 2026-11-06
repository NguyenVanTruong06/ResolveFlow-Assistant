# Static and Evidence Review — G07-07B7-LIVE-IMPL Revision R4

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and stored-evidence review only. No tests, Resolve application, IPC, live dispatch, or network operation was run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `.bridge/report.md` | `10518c1243ccd7bcdee448d8aab63e3a3561a2f864529dd985a54999ec4a1001` | Hash matches |
| `src/services/live_resolve_canary_adapter.py` | `02cac83f72867460a9b9490dcdbd6e3f0c048377d2549de2fffa86ad697ae485` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `b54a7e969c02c6376cfce0fc22d034ecd502681bd12cc314e0166d51689b8999` | Hash matches |
| Focused log | `3be439cf06c8bd5a9b192f12c7925ce166dc883e999a55901a02f9b3d4af0559` | Hash matches; stored log reports 44/44, not rerun |
| Combined regression log | `a10482ca7d72cb163f459f53c581567cad9f3f2098e8a0e74930238c240ea049` | Hash matches; stored log reports 224/224, not rerun |

The report and artifact hashes match the submitted values. The stored logs' final summaries match the reported counts. This verifies the submitted files and their hashes, not the original execution environment or live Resolve behavior.

## R3 finding closure check

- **R3-01 — Full ledger identity and terminal tuple:** The repair path now compares the ledger's project, timeline, execution, authorization, plan, approval, proposal, and revision fields. For an `EXHAUSTED` ledger it loads the existing receipt and checks WAL/receipt/ledger terminal state and receipt ID before releasing the lock. These changes address the reported R3 gap.
- **R3-02 — Payload binding and readback item ID:** Restart parsing now requires the action payload digest, checks its canonical digest, compares the canonical payload plus action ID and digest between PENDING and outcome records, and compares fresh physical readback's item ID to `ACTION_COMMITTED.resolve_item_id`. One action identity gap remains (finding below).
- **R3-03 — Durable proof primitive types:** `DurableProofRecord.from_dict()` now rejects non-dict input, enforces the exact 16-key set, gates integer fields with `type(value) is int`, gates the remaining fields as strings, and applies bounds before constructing the DTO. This addresses the reported coercion issue.

## Finding

### LIVE-IMPL-R4-01 — BLOCKER: Restart repair does not authoritatively bind `action_id` when `action` is omitted

**Location:** `src/services/live_resolve_canary_adapter.py:2033-2040, 2292-2316, 2361-2397`; `CANONICAL_ACTION_PAYLOAD_KEYS` at lines 169-186.

`execute_restart_repair()` declares `action: Optional[PlannedTimelineAction] = None`. During PENDING validation, it requires `action_id` to be a non-empty string, but compares it with `action.action_id` only when the optional argument is supplied. The canonical 16-field payload digest does not cover `action_id`; the code defines `action_id` separately and includes it only in equality checks between PENDING and COMMITTED/FAILED. Thus, with the default `action=None`, a WAL can carry any non-empty action ID consistently in both records and pass those checks. The subsequent readback comparison proves the observed item matches the WAL's committed item ID, but does not establish that this WAL action ID is the authorized action identity.

This leaves the exact action-identity binding from R3-02 conditional on caller input, despite the R4 report describing it as unconditional. A restart repair must not treat a self-consistent but unanchored WAL action ID as authorized.

**Required correction:** Make the expected action identity authoritative on every repair path: require and validate the signed `PlannedTimelineAction`, or add an independently authenticated action-ID claim to the authorization/proof contract and compare it directly with PENDING and outcome records. Do not accept a caller-supplied or WAL-only value as its own authority. Add a negative regression case invoking repair without `action` against a WAL whose PENDING/COMMITTED action IDs are consistently substituted; it must remain in doubt and retain the target lock.

## Decision and gate state

R4 closes the reported ledger tuple and DTO coercion findings, and the stored test evidence has the claimed counts. However, restart repair still accepts an action identity that is not bound to signed authorization when its optional `action` argument is omitted. Gate 3 Revision R4 is **not accepted** pending correction.

Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain **OPEN**. This review performed no Resolve launch, IPC, or live dispatch.
