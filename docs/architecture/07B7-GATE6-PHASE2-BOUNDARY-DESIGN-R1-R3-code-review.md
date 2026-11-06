# Gate 6 Phase 2 Governance Boundary Design R1 R3 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R3  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No tests or operational checks were run; no host, key, socket, network, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace before this review entry:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `03036e13998e22286e39b020738edba940325b32febf907ae51a029e15dbc32d`
- `.bridge/task.md` — `0febeef13c540fc2a33fe5b765b23cbf01283411c28be217c389f50d260c7fd4`
- `.bridge/report.md` before this review entry — `5055434e50873ba392dd3af5ce9e277d244daffb12c21a58748fa34780935ccd`

## Findings

### PHASE2-BOUNDARY-R3-01 — Crash matrix conflates process-held flock with durable target exclusion — MEDIUM

Section 3.2, rows 147–149, describes a “local lock held” after process crashes and says Boundary 5A has a “local lock released” even though the terminal notification has not yet been acknowledged. These statements do not match the accepted operation ordering.

The source releases the target `flock` only after `_notify_oracle_terminal()` returns successfully (`src/services/live_canary_dispatch_service.py:1045–1062`; the pre-mutation abort path does the same at `:1198–1210`). A crash at Boundary 5A—after the receipt is durable but before `NOTIFY_ORACLE_INTENT` is fsynced—therefore occurs before the normal release step. Conversely, process termination closes the descriptor and the kernel releases the `flock`; it cannot remain physically held across a crash. Durable journal state, not a surviving process-held flock, provides restart exclusion: `is_target_locked_or_in_doubt()` treats `RESERVED`, `ACTIVE`, `EXHAUSTED`, `IN_DOUBT_LOCKED`, and an `.in_doubt` marker as locked (`src/services/durable_dispatch_journal.py:591–623`). The accepted restart matrix also says Boundary 5A permits one first send and release only after a verified `ACCEPTED` response (`docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md:314`).

The combined Boundary 2–3 row also merges distinct durable states (`RESERVED` with no WAL at Boundary 2 versus `ACTION_PENDING` at Boundary 3) and labels both “lock held.” The source ordering writes `ACTION_PENDING` before acquiring the `flock`; after a crash the descriptor lock is released, while the durable journal state is what recovery must inspect.

This is a documentation accuracy and recovery-contract issue; this review does not claim a demonstrated runtime bypass. Please revise the matrix to distinguish (a) kernel `flock` ownership, which ends when the process exits, from (b) durable logical target exclusion reconstructed from the ledger/WAL/marker. Preserve the accepted rules: Boundary 5A allows exactly one first notification send because no notification intent was persisted, and the target remains logically excluded until a verified Oracle acknowledgement; Boundary 5B remains no-resend and `IN_DOUBT_LOCKED`. Split Boundaries 2 and 3 or state their distinct on-disk records and recovery actions explicitly.

## Remediation status and decision

The R2 findings are closed in this revision: the accepted `signer_public.sock` / `signer_public.lock` endpoint is named correctly; Oracle execution-ID replay state is no longer described as a target lock; and `/etc/resolveflow/trusted_roots/` is clearly an unapproved proposal rather than an accepted DEP-01 path. WO5 remains disconnected with zero incoming edges. The remaining finding above prevents acceptance of the R3 design until its crash-state/lock semantics are reconciled with source and the accepted DEP-04 matrix.

No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
