# Gate 6 Phase 2 Receipt/WAL Binding Design R1 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-RECEIPT-WAL-BINDING-DESIGN-R1.md`
- **Submitted SHA-256:** `88678fdc8ea2d064d99e27abbc41ae4bf4f802209e464073a6e1935bebb32be6`
- **Mode:** Documentation and static source review only. No tests, Resolve, sockets, network, host, systemd, or production-state access.

## Findings

### PHASE2-RECEIPT-WAL-R1-01 — BLOCKER: Notification-intent persistence is attributed to source but does not exist

The design states that `LiveCanaryDispatchService` writes and fsyncs `NOTIFY_ORACLE_INTENT` in `execution.log` before sending the terminal notification (§4.1, lines 216–220; crash rows 7–8). The reviewed source does not implement this record or any equivalent durable notification intent/ack state: `_notify_oracle_terminal()` constructs the request and calls `_send_oracle_request()` directly (`src/services/live_canary_dispatch_service.py:656–688`), and there are no `NOTIFY_ORACLE_INTENT` references in the journal or service. Consequently, current durable state cannot distinguish a crash immediately before send from a crash after Oracle received the request. The design’s “exactly one first send” and no-resend classifications cannot be grounded in current source.

**Required correction:** Separate current behavior from proposed future behavior. For the current implementation, classify notification outcome as ambiguous across the unrecorded send boundary and require administrative reconciliation; do not infer “zero send” from absence of an intent record. If proposing durable intent/ack records for a future implementation, specify their exact journal, schema, ordering, and recovery semantics as a separate proposed change, without claiming they already exist.

### PHASE2-RECEIPT-WAL-R1-02 — BLOCKER: Missing crash state between separately fsynced ACTION_COMMITTED and FINISH records

The design models `ACTION_COMMITTED` and `FINISH` as a combined WAL commit followed by a single fsync (§4.1–4.2). In the accepted source, `commit_terminal()` calls `_append_wal_record()` separately for the two records (`src/services/durable_dispatch_journal.py:786–804`), and each call writes and fsyncs its record and then fsyncs the state directory (`:402–420`). There is therefore a durable crash boundary after `ACTION_COMMITTED` and before `FINISH`.

This state is absent from the nine-row matrix. It is consequential: `_recover_from_files()` removes an execution from `unmatched_pending` as soon as it sees `ACTION_COMMITTED` (`:552–555`), even if no `FINISH`, receipt, or EXHAUSTED ledger publication exists. The submitted matrix’s “before FINISH fsync” row incorrectly assumes the only possible WAL state is unmatched `ACTION_PENDING`.

**Required correction:** Add this exact crash state to the matrix and define fail-closed handling. Specify whether a future implementation changes the append protocol to batch records atomically or keeps per-record fsync; in either case cover partial/torn append, durable `ACTION_COMMITTED` without `FINISH`, and recovery behavior. Do not authorize notification or ledger repair from that state absent verified receipt binding and an explicit durable protocol.

### PHASE2-RECEIPT-WAL-R1-03 — HIGH: Proposed schema does not define all terminal branches and record-version transitions

The design defines v2 `FINISH` for `COMPLETED` and `ABORTED`, and shows a v2 `ACTION_COMMITTED` record, but does not specify the v2 record sequence and required fields for the ABORTED path’s `ACTION_ABORTED` record. It also does not define how the schema-version policy applies to `IN_DOUBT`, for which current `commit_terminal()` writes no WAL FINISH and instead publishes `IN_DOUBT_LOCKED` plus a marker (`durable_dispatch_journal.py:846–870`). The generic claims that terminal records are v2 and that every specified field is mandatory are consequently ambiguous across terminal branches and legacy recovery.

**Required correction:** Define record-by-record schemas and version rules for COMPLETED, ABORTED, and IN_DOUBT, explicitly stating which branches produce a receipt and digest, which records remain non-terminal, and how mixed v1/v2 WAL histories are validated. If some branches are outside the receipt-binding schema, state that explicitly and define their recovery boundary.

## Scope and safety disposition

The design correctly identifies the accepted filename `receipt.json`, the current v1 absence of a WAL receipt digest, and the limitation that SHA-256 is unkeyed. Its recommendation to retain administrative-only reconciliation for Gate 6 is consistent with the existing fail-closed boundary, but the contradictory automated-repair and notification claims must be corrected before the design is accepted.

No tests were run, as this is a documentation/static-review workorder. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected. No Resolve, Oracle socket, network, host, systemd, or production key/state operations were performed.
