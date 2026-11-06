# Gate 6 Phase 2 Governance Boundary Design R1 R6 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R6  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No tests or operational checks were run; no host, key, socket, network, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace before this review entry:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `4cf0524c5935e64790eb49d26c26520399cbbeb0836ead9c9a47e11ae2f5c5fd`
- `.bridge/task.md` — `9783b5b2a2fda4a0f0deb10891a39d1b8bfb968861231cfb177ccff89c9cbb76`
- `.bridge/report.md` before this review entry — `5817d4e1630c1636a97c37a941615cd14098db35eef1601dab0fca745f7c4a65`

## Finding

### PHASE2-BOUNDARY-R6-01 — Boundary 5A-1 reports an impossible pre-terminal ledger state and unimplemented recovery as established behavior — MEDIUM

Section 3.2, Boundary 5A-1, lists `ACTIVE` **or** `RESERVED` as the target-ledger state after `ACTION_COMMITTED`, `FINISH(COMPLETED)`, and receipt publication but before target-ledger `EXHAUSTED` publication. In the accepted source, `write_action_pending()` sets the target ledger to `ACTIVE` before dispatch (`src/services/durable_dispatch_journal.py:690–716`), and `commit_terminal()` is reached only after that step; thus `RESERVED` is not possible at this crash point. It describes a different, earlier boundary before the WAL `ACTION_PENDING` record.

The same row states that restart recovery repairs this receipt/WAL combination to `EXHAUSTED(COMPLETED)`. That is a proposed recovery requirement, not behavior implemented by the accepted `DurableDispatchJournal`: `_recover_from_files()` loads the target ledger and WAL, removes completed execution IDs from its unmatched-pending set, and only repairs unmatched `ACTION_PENDING` entries to `IN_DOUBT_LOCKED`; it does not reconcile terminal `FINISH(COMPLETED)` plus receipt against a pre-terminal `ACTIVE` ledger (`src/services/durable_dispatch_journal.py:518–589`).

Please make the 5A-1 state exact (`ACTIVE`), reserve `RESERVED` for Boundary 2, and label the receipt/WAL-to-ledger repair as a required future recovery behavior that is not yet implemented. Specify its fail-closed validation and durable write ordering, or defer it to a separately reviewed implementation workorder. Retain the single first terminal send only after the local records are consistently reconciled and confirm no notification intent exists.

## Remediation status and decision

The R5 finding is substantially addressed: Boundary 5A is split at the receipt/target-ledger publication gap, and the R4 findings remain closed. Boundary 3 follows unmatched-`ACTION_PENDING` recovery to `IN_DOUBT_LOCKED`; `EXHAUSTED` remains permanently ineligible for replay; WO5 remains disconnected with zero incoming edges. The remaining 5A-1 state/recovery discrepancy prevents acceptance.

No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
