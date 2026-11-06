# Gate 6 Phase 2 Governance Boundary Design R1 R7 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R7  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No tests or operational checks were run; no host, key, socket, network, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace before this review entry:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `f2af1287970146d39d725f99250ae95867e8904ea759e23c1deefd11db2ad1d2`
- `.bridge/task.md` — `d97f291fd599ed1f4942690dd8e7ff43c0a1e6c7e0b51eaa031a87fd7e66b93d`
- `.bridge/report.md` before this review entry — `89d95f4e669175cfd60c8f9f76f1d3a638027c236930719e1a8a34505dba524f`

## Finding

### PHASE2-BOUNDARY-R7-01 — Proposed receipt digest validation is not bound by the accepted WAL schema — MEDIUM

Section 3.2, Boundary 5A-1, says recovery will verify the SHA-256 digest and `execution_id` binding of `execution_receipt.v1.json` “against WAL `FINISH(COMPLETED)`.” The accepted journal does not provide that verification contract:

- The accepted receipt filename is `receipt.json` (`src/services/durable_dispatch_journal.py:191`), not `execution_receipt.v1.json`.
- The `FINISH` record written by `commit_terminal()` contains `status`, `execution_id`, and `timestamp_iso`, but no receipt digest (`src/services/durable_dispatch_journal.py:796–804`). The corresponding WAL `ACTION_COMMITTED` record also contains no receipt digest (`:788–795`). A SHA-256 computed from a receipt cannot be checked “against” a WAL value that does not exist.

As written, the proposed repair claims a cryptographic binding that the current durable records cannot establish. Correct the filename and either (a) keep automated repair prohibited and require administrative reconciliation under the current schema, or (b) specify a separately reviewed WAL/receipt contract that durably commits the exact receipt digest and identity bindings before recovery relies on them. Until that contract is implemented and reviewed, recovery must not treat a locally computed receipt hash as proof of WAL binding.

## Remediation status and decision

The R6 finding is closed: Boundary 5A-1 now correctly records `ACTIVE` after `ACTION_PENDING`, and labels ledger repair as unimplemented. The new repair proposal still uses a nonexistent receipt filename and assumes a receipt-digest binding absent from the accepted WAL schema, so R7 is not accepted. Boundary 3 remains fail-closed as `IN_DOUBT_LOCKED`; `EXHAUSTED` remains ineligible for replay; WO5 remains disconnected with zero incoming edges.

No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
