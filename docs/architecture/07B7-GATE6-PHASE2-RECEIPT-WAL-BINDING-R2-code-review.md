# Gate 6 Phase 2 Receipt/WAL Binding Design R2 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-RECEIPT-WAL-BINDING-DESIGN-R1.md` (Revision R2)
- **Submitted SHA-256:** `ba5d7c727dff11396610c187ebe07b068e409bc85f7b9d6097df2b1f3b8a6b01`
- **Mode:** Documentation and static source review only. No tests, Resolve, sockets, network, host, systemd, or production-state access.

## Prior finding disposition

The R2 document corrects the earlier notification-boundary description, adds the separately-fsynced `ACTION_COMMITTED` crash state, and defines terminal branches and mixed schema handling. The prior findings are addressed in the design text.

## Findings

### PHASE2-RECEIPT-WAL-R2-01 — HIGH: Required action digest is not present in the receipt schema produced by the accepted source

Section 2.4 requires recovery to compare the `ACTION_PENDING.action_payload_digest_sha256` against a receipt claim at `receipt.json["adapter_result"]["action_payload_digest_sha256"]` or a top-level receipt field. The accepted source does not emit either claim. `LiveCanaryDispatchService` constructs the completed receipt from `execution_id`, `status`, `adapter_result`, target UIDs, and timestamp (`src/services/live_canary_dispatch_service.py:1037–1044`). The adapter result contains `action_id` and `action_idempotency_hash`, but not `action_payload_digest_sha256` (`src/services/live_resolve_canary_adapter.py:1348–1361`). The ABORTED receipt also has no action digest (`live_canary_dispatch_service.py:984–991`).

As written, the recovery invariant cannot be satisfied from the proposed receipt shape. A receipt hash binds bytes, but it does not create the missing action-payload claim.

**Required correction:** Specify an exact receipt schema change that includes the action payload digest for each receipt-bearing branch, including its trusted source value and exact equality checks against `ACTION_PENDING` and `FINISH`; or explicitly remove that binding requirement and justify the reduced claim. Keep the change as a future implementation requirement; do not imply current artifacts already contain the field.

### PHASE2-RECEIPT-WAL-R2-02 — HIGH: A regular-file batch write is not an all-or-nothing durability primitive

Section 4.1.2 and the sequence diagram propose one “atomic” append/write containing `ACTION_COMMITTED + FINISH`, claiming this eliminates the durable intermediate state. A write to a regular file, including with `O_APPEND`, does not provide crash-atomic persistence of an arbitrary multi-record payload. It may be short or only partly durable at a crash/power loss; one `fsync` after the write does not make the preceding bytes an atomic transaction. Therefore a complete first record or partial second record may still be observed, and the failure mode cannot be removed merely by using one write call.

**Required correction:** Remove the all-or-nothing claim. Define fail-closed recovery for every prefix/torn-tail outcome of a combined append, including the case where `ACTION_COMMITTED` is complete but `FINISH` is absent or partial. Alternatively, specify a single self-contained terminal commit record and still define torn-record recovery. Do not enable recovery or notification based on an assumed atomic batch append.

## Safety disposition

The R2 design maintains the correct distinction between current notification behavior and a proposed future intent/ack protocol, and its current-source recommendation remains administrative reconciliation. The two findings above must be resolved before accepting the receipt-binding design.

No tests were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected. No Resolve, Oracle socket, network, host, systemd, or production key/state operations were performed.
