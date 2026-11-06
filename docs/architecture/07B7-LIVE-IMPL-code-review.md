# Static Code Review — G07-07B7-LIVE-IMPL (Gate 3)

**Decision: `CHANGES_REQUIRED`**  
**Review mode:** Static source and handoff review only. No tests, application, Resolve API, IPC, or network actions were run during this review.

## Reviewed artifacts

| Artifact | SHA-256 | Result |
|---|---|---|
| `src/services/live_resolve_canary_adapter.py` | `52227bee9e59f7009c92e325dba9cb19d35ba6cd8ed12df0931116e505d089c6` | Matches submitted hash |
| `tests/services/test_live_resolve_canary_adapter.py` | `738d096d7b7bb82f88b0acae7db4d880f5906e673978ab29a7d2b5e4c5bd65b9` | Matches submitted hash |
| `.bridge/report.md` | `d1f50476eecb7dbc05e2f54917c66616ca976ae8a8b24e4ad8238aa2a5ab01de` | Matches submitted hash |
| Focused test log | `bc3f2b49894baf0024ebf3b259dbb1d678943efb722a388c756a87447b015bd8` | Hash verified; reported 27/27 pass, not rerun |
| Combined test log | `998359328a5f0328422a57c42816d824a1dc99a488d0a36a179de282d200813f` | Hash verified; reported 192/192 pass, not rerun |

The implementation was reviewed against `.bridge/task.md`, the accepted 07B7 R7 plan (§§4, 5.2–5.3, 8–9), and the referenced 07B5 contracts. The submitted test counts are treated as handoff evidence, not as evidence independently reproduced in this review.

## Findings

### LIVE-IMPL-01 — BLOCKER: Clean-proof terminalization skips proof authentication

**Location:** `src/services/live_resolve_canary_adapter.py:1456-1495`

`complete_with_clean_proof()` checks only `proof_record.verified_outcome`. It does not call `verify_durable_proof_record()`, check the record HMAC, load the durable record, or bind the record to the supplied authorization and the current `ACTION_PENDING`. `DurableProofRecord` is a public constructible dataclass. A caller can therefore supply a fabricated record with the clean outcome and reach `FINISH(ABORTED)`, receipt publication, ledger exhaustion, and lock release without proving that the timeline is physically clean.

**Required correction:** Make clean terminalization consume only a coordinator-verified capability produced after descriptor-backed record loading, HMAC/schema verification, and full binding to the current authorization, action, and pending journal entry. Repeat the authority check at the terminalization boundary; never trust the DTO's outcome field by itself.

### LIVE-IMPL-02 — BLOCKER: Restart can redispatch from an existing ACTIVE ledger

**Location:** `src/services/live_resolve_canary_adapter.py:551-587, 753-792`; `src/services/durable_trial_target_ledger.py:741-744`

The journal existence check is the only history gate. If a crash occurs after `activate_target()` persists `ACTIVE` but before `START` is appended, there is no journal file. On retry, the adapter permits a same-identity `ACTIVE` ledger, and `activate_target()` explicitly returns the existing ledger for the same `execution_id`. The adapter then writes a new `START`/`ACTION_PENDING` and can dispatch the action again. This violates the plan's zero-redispatch rule at a reachable crash boundary.

**Required correction:** Treat any pre-existing `ACTIVE` or `RESERVED` ledger as repair-only and reject normal dispatch. Add a distinct restart-repair path that reconciles the existing ledger and journal state without dispatching. Prove the crash-after-activation/before-START boundary in tests.

### LIVE-IMPL-03 — BLOCKER: Dispatch media is not bound to the signed source

**Location:** `src/services/live_resolve_canary_adapter.py:505-510, 849-860, 877-902`

The signed action payload binds a source path and the authorization contains a source digest, but the dispatch path never verifies that `canary_clip_item` represents that path or digest. A caller may pass an unrelated Media Pool item. If omitted, the adapter substitutes `FakeMediaPoolItem`, then passes it to `AppendToTimeline`. Post-dispatch read-back verifies only item count and frame geometry; it does not match the read-back item identity/name to the authorized source. Thus an item from a different source can satisfy the geometry checks and be reported as the authorized canary.

**Required correction:** Remove the fake-item fallback from the live adapter. Require a Resolve-owned media item and verify its canonical source path and content digest against the signed authorization before dispatch; bind read-back to the dispatched/authorized media identity and expected source name. Keep all such checks fake-only in Gate 3 tests.

### LIVE-IMPL-04 — BLOCKER: Stage 1 proof does not enforce the complete signed identity and action binding

**Location:** `src/services/live_resolve_canary_adapter.py:1177-1239, 1302-1343, 1391-1454`

`verify_and_consume_proof()` has no authorization argument and does not compare the proof's target project/timeline UIDs, source path, source frame range, track, or record geometry with the authorization and pending action. It only binds action index and payload digest. The `action_id` comparison to `pending_entry.action_params` is conditional (`pending_action_id is not None`), so a pending record lacking that field bypasses the required three-way equality. Stage 2 similarly makes `authorization` optional and only checks target UIDs when it is supplied. The proof HMAC uses `json.dumps(sort_keys=True, separators=...)`, while the plan requires the canonical serializer contract.

**Required correction:** Route verification through the single authoritative verifier contract with `(raw_proof, action, pending_entry)` plus coordinator-owned authorization context. Require unconditional `raw_proof.action_id == action.action_id == pending.action_params['action_id']`; validate the full identity, source, track, and geometry projection; make authorization mandatory for Stage 2; and use the accepted canonical HMAC serializer.

### LIVE-IMPL-05 — BLOCKER: Proof consumption and repair are not durable or restart-capable

**Location:** `src/services/live_resolve_canary_adapter.py:491, 1331-1336, 1382-1388, 1391-1454, 1621-1637`

Replay prevention is an in-memory `_consumed_raw_proof_ids` set. `proof_ledger_dir` is optional, so proof verification can succeed without any durable record. When configured, the writer publishes with `os.replace()` to a reusable deterministic filename, has no consume-once destination creation or shared scanner/writer lock, does not fsync the directory, and no code reads that file during restart. `verify_durable_proof_record()` validates an object supplied by its caller; there is no `execute_restart_repair()` implementation to reconcile the crash boundaries required by the plan. A process restart therefore loses the replay set and cannot recover proof-authorized terminalization safely.

**Required correction:** Make the durable proof ledger mandatory and coordinator-owned; publish consume-once records atomically without replacement under the shared ledger lock and fsync the directory. Add bounded descriptor-backed read/verification and a restart-repair state machine that covers every plan crash boundary and never dispatches.

### LIVE-IMPL-06 — HIGH: Receipt publication does not meet the descriptor-backed durable receipt contract

**Location:** `src/services/live_resolve_canary_adapter.py:1552-1617`

Receipt publication obtains a path from `_receipts_dir`, uses `os.path.exists()`, path-based `os.open`/`os.replace`/`open`, and reads without a bounded descriptor reader. It writes with one `os.write()` call, so short writes are not handled. The read-back check compares only `receipt_id` and `execution_id`, despite the method claiming full DTO equality. Because receipt publication is the prerequisite for ledger exhaustion and lock release, a partial or inconsistent receipt can cross the terminal boundary without the source-required verification.

**Required correction:** Use the coordinator-owned receipts directory descriptor for no-follow relative create/publish/read operations, a bounded reader and write-all loop, file and directory fsync, and full deserialized DTO equality before allowing ledger exhaustion or lock release. Preserve the post-replace failure state as in-doubt and do not silently clean up ambiguous publication outcomes.

## Decision and gate state

Gate 3 is **not accepted**. The blockers above affect source authorization, proof authority, replay prevention, durable recovery, and terminal receipt safety; the submitted passing test counts do not close these static findings. Gate 4 preflight and Gate 5 execution-workorder issuance must remain pending until a revised implementation is reviewed and accepted.

No live Resolve access or dispatch is authorized by this review. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. The reported zero-Resolve/zero-IPC implementation boundary is acknowledged; no runtime operation was performed during this review.
