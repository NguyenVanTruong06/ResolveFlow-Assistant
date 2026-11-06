# Gate 6 Phase 2 Governance Boundary Design R1 R5 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R5  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No tests or operational checks were run; no host, key, socket, network, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace before this review entry:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `37179ee9f06b9a81d7ded39811b371750a7df1468afa0cb521c31b97ddf1bf33`
- `.bridge/task.md` — `a155d446b64dc97c0f2007e632da1ef677c3a78c336be4fc37076667e6544260`
- `.bridge/report.md` before this review entry — `e4dda9f910a6f573a077e3ee9bbcf6d50fe12dc9c8b278361a6930e11f44ab19`

## Finding

### PHASE2-BOUNDARY-R5-01 — Boundary 5A does not define exact journal state at the receipt/notification gap — MEDIUM

Section 3.2, Boundary 5A, treats “receipt `COMPLETED` durable, before `NOTIFY_ORACLE_INTENT` fsync” as one restart state, then says to complete journal terminalization to `EXHAUSTED` / `COMPLETED` after Oracle ACK. The accepted source has an additional durable operation in this interval and commits the local terminal state before contacting Oracle:

- `DurableDispatchJournal.commit_terminal()` writes WAL `ACTION_COMMITTED`, WAL `FINISH`, publishes/fsyncs the receipt, and then publishes the target ledger as `EXHAUSTED` (`src/services/durable_dispatch_journal.py:763–819`).
- `dispatch_live_canary()` returns from `commit_terminal()` before `_notify_oracle_terminal()` and releases the kernel flock only after a successful notification (`src/services/live_canary_dispatch_service.py:1035–1062`).

Consequently, the stated interval after receipt fsync but before notification-intent fsync includes at least two distinct durable states: a crash may occur after receipt publication but before the `EXHAUSTED` target-ledger publication, or after `commit_terminal()` has completed but before notification intent is durable. R5 does not specify which WAL/receipt/target-ledger combination restart repair must observe in the first case, nor what exact local journal operation it performs after ACK in the second. The latter is already terminalized in the accepted source; the former may not be. Calling both “complete journal terminalization after ACK” obscures the source ordering and leaves a crash boundary underspecified.

Please split Boundary 5A at the target-ledger publication (or define an equivalent explicit sub-boundary), list the durable WAL, receipt, and target-ledger state at each point, and specify how recovery reconciles a receipt plus terminal WAL with a pre-terminal target ledger. For the post-`commit_terminal()` case, state that the local journal is already `EXHAUSTED`; a verified Oracle `ACCEPTED` response permits release of the process-held flock, while the one-shot target remains permanently ineligible for re-dispatch. Preserve the exactly-one first send only where no notification intent exists, and retain the no-resend rule for ambiguous Boundary 5B.

## Remediation status and decision

The R4 findings are closed: Boundary 3 no longer treats volatile capability state as crash evidence and now follows unmatched-`ACTION_PENDING` recovery to `IN_DOUBT_LOCKED`; `EXHAUSTED` is correctly described as permanently ineligible for another dispatch, distinct from release of the kernel flock. WO5 remains disconnected with zero incoming edges. The Boundary 5A crash interval still needs exact durable-state and recovery semantics before acceptance.

No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
