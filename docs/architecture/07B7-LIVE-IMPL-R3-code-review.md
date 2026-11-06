# Static and Evidence Review — G07-07B7-LIVE-IMPL Revision R3

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and stored-evidence review only. No tests, Resolve application, IPC, live dispatch, or network operation was run.

## Reviewed artifacts

| Artifact | Submitted SHA-256 | Review result |
|---|---|---|
| `.bridge/report.md` | `c5f019a8938f66ff719d11524b679eb5c34e909de84de3c8470bf7e95b01b7af` | Hash matches |
| `src/services/live_resolve_canary_adapter.py` | `2870cbd422e5efdf308608650af6a2db21121cf1d1e1e2b365a23dc17add08f1` | Hash matches |
| `tests/services/test_live_resolve_canary_adapter.py` | `c7a6650475fae45a27e4ba1b4968524d683b37ae0ac4d52170ee0ab4272e6c23` | Hash matches |
| Focused test log | `43ba3e910acda6bd449cd4e184f7034bb6d112884065c3b8e45628607fe80c67` | Hash matches; stored log reports 41/41, not rerun |
| Combined regression log | `a120f0cdc406d85aeedceddea69f5db32685dc42a85e7edc342e5a3c8189945d` | Hash matches; stored log reports 221/221, not rerun |

The logs' final summaries match the reported counts. This verifies the submitted evidence files and hashes, not the execution environment or live Resolve behavior. The three R2 findings were checked against their corresponding R3 source paths.

## R2 finding closure check

- **R2-01, signed authorization:** The adapter now calls `_bind_and_verify_authorization()` at dispatch, restart repair, terminalization, and proof-verification boundaries; that method delegates to the coordinator's 32-field HMAC verifier. Closed at the inspected paths.
- **R2-02, restart repair:** R3 adds ledger prechecks, a bounded ordered stage grammar, and independent physical/proof checks. These changes close the specific R2 gaps, but remaining ledger and payload-binding gaps are recorded below.
- **R2-03, proof publication:** R3 uses descriptor-relative `link()` and `renameat2(RENAME_NOREPLACE)` fallback, and fails closed if no atomic no-replace primitive exists. The direct-write fallback is removed. Closed at the inspected path.

## Findings

### LIVE-IMPL-R3-01 — BLOCKER: Restart may release the lock with a terminal ledger that disagrees with the receipt/WAL

**Location:** `src/services/live_resolve_canary_adapter.py:2046-2063, 2546-2656`; terminal ledger fields are defined in `src/services/durable_trial_target_ledger.py:171-186`.

Restart identity comparison checks only project UID, timeline UID, execution ID, authorization ID, and plan idempotency key. The ledger DTO also binds `approval_id`, `proposal_id`, and `proposal_revision`, which are not compared to the signed authorization before repair. More seriously, Boundary 5 accepts an already `EXHAUSTED` ledger but does not compare its `terminal_receipt_id` or `terminal_reason` with the receipt and WAL outcome being reconciled. It republishes a receipt from the WAL and skips `exhaust_target()` when the ledger is already exhausted, then releases the target lock. An existing `EXHAUSTED/ABORTED` ledger can therefore coexist with a newly published `COMPLETED` receipt/WAL, or vice versa, while repair reports success and unlocks.

The accepted plan's restart invariant requires reading and reconciling the complete WAL/receipt/ledger tuple and releasing only when their terminal state and receipt identity agree.

**Required correction:** Compare every ledger identity field represented by the signed authorization, including approval/proposal identity and revision. Before the `EXHAUSTED` path releases the lock, read and validate the existing receipt and require `WAL.final_receipt_state == receipt.state == ledger.terminal_reason`, with `ledger.terminal_receipt_id == receipt.receipt_id`. On any mismatch, preserve the lock and return an in-doubt result; do not overwrite the conflicting receipt.

### LIVE-IMPL-R3-02 — BLOCKER: Restart WAL validation still permits incomplete action binding

**Location:** `src/services/live_resolve_canary_adapter.py:2240-2315, 2464-2537`.

The new FSA validates stage order, but `ACTION_PENDING` payload binding is not mandatory: the digest is compared only if `action_payload_digest_sha256` is present (lines 2263-2275), and action ID is checked only when the caller supplied `action`. `ACTION_COMMITTED` is checked for action type, index, action ID, and non-empty `resolve_item_id`, but its full payload is not required to equal the pending payload. On the success-repair branch, `_perform_physical_readback_observation()` returns the observed item ID, but the return value is discarded rather than compared with `ACTION_COMMITTED.resolve_item_id`.

Consequently, a sequential WAL with missing signed payload digest or incomplete action fields can pass repair validation; current physical presence alone does not establish that the recorded commit belongs to the exact pending action and item identity for this execution. This falls short of the 07B5 full payload binding and 07B7 restart evidence contract.

**Required correction:** Require the exact action payload schema and digest on every `ACTION_PENDING`; enforce unconditional action ID equality even when no action object was passed; compare every action payload field between pending and failed/committed outcome records; and require fresh readback's unique item ID to equal the committed WAL item ID before terminalizing success. Missing or mismatched evidence must remain in doubt.

### LIVE-IMPL-R3-03 — HIGH: Durable proof deserialization coerces types before HMAC verification

**Location:** `src/services/live_resolve_canary_adapter.py:338-364`.

`DurableProofRecord.from_dict()` checks the exact 16-key set but then applies `str()` and `int()` conversions instead of primitive type gates. For example, JSON `true` in `action_index` is converted to integer `1`; the subsequent HMAC is calculated over the normalized DTO, so a record whose signed numeric value was `1` can be altered from `1` to `true` in its serialized form and still verify. This makes the persisted representation malleable and does not satisfy the claimed strict 16-field DTO schema.

**Required correction:** Validate exact primitive types and value bounds for all 16 fields before constructing the DTO or calculating HMAC. Reject booleans for integer fields and reject non-string values rather than coercing them.

## Decision and gate state

Revision R3 closes the three R2 findings as reported, but restart can still release a lock without proving terminal tuple consistency, and it does not enforce full action-payload binding on every accepted WAL path. The durable proof reader also accepts noncanonical JSON scalar types through coercion.

Gate 3 Revision R3 is **not accepted**. Gate 4 preflight and later gates remain pending. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review performed no live Resolve, IPC, or dispatch operation.
