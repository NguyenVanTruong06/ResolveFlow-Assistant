# Gate 6 Phase 2 Governance Boundary Design R1 R4 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R4  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No tests or operational checks were run; no host, key, socket, network, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace before this review entry:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `9acbb60d6747a26c2c1c296587b33e51393bbf2f83fa136fcde3e5c4ded886b2`
- `.bridge/task.md` — `49148dc0ec800ea411c28179514e759ec31f8117bf25f69675812ccc3de98f3b`
- `.bridge/report.md` before this review entry — `377ef775e935503737a3e40f7584a2653e6d3769d9955c711237b5eaa25ab6a2`

## Findings

### PHASE2-BOUNDARY-R4-01 — In-memory abort capability is treated as durable crash evidence — HIGH

Section 3.2, Boundary 3, says an unconsumed clean-abort capability proves that no mutation occurred after a process crash, and prescribes writing `FINISH(ABORTED)`, an `ABORTED` receipt, and sending an abort notification. The capability is process memory: `PreMutationAbortCapability` stores `_consumed` and `_mutation_boundary_crossed` on the object (`src/services/live_canary_dispatch_service.py:126–164`). Those values do not survive process termination, and the `ACTION_PENDING` WAL record contains no durable mutation-boundary-consumed field. On recovery, `DurableDispatchJournal` treats any unmatched `ACTION_PENDING` as `IN_DOUBT_LOCKED` and writes/repairs the `.in_doubt` marker (`src/services/durable_dispatch_journal.py:536–589`).

Therefore the post-crash dispatcher cannot establish “unconsumed capability” from the current durable records. Claiming zero mutation and transitioning to `ABORTED` could misclassify an execution whose mutation boundary was crossed, contrary to the accepted fail-closed recovery rule. Keep Boundary 3 `IN_DOUBT_LOCKED` under the current contract, or specify a separately reviewed, durable and crash-verifiable mutation-boundary record before allowing an automated `ABORTED` recovery. Do not treat the in-memory capability as restart evidence.

### PHASE2-BOUNDARY-R4-02 — `EXHAUSTED` is incorrectly described as releasing logical target exclusion — MEDIUM

Section 3.2, Boundaries 2 and 3, says the target is “logically released” when the journal reaches `EXHAUSTED(ABORTED)`; Boundary 5A similarly discusses logical release after notification acknowledgement. But the accepted `is_target_locked_or_in_doubt()` implementation returns `True` for `EXHAUSTED` (`src/services/durable_dispatch_journal.py:591–616`), and `reserve_target()` rejects a prior `EXHAUSTED` state (`:659–664`). Terminalization and a positive Oracle acknowledgement may release the process-held kernel `flock`; they do not make this one-shot target eligible for another dispatch.

Please distinguish release of the ephemeral kernel `flock` from terminal exhaustion / replay prevention of the target. State explicitly whether an `EXHAUSTED` target remains permanently ineligible for another dispatch (as the accepted source currently enforces), and avoid describing `EXHAUSTED` as cleared logical exclusion unless a separately reviewed reset/reconciliation contract changes that behavior.

## Remediation status and decision

The R3 finding about kernel `flock` versus durable journal exclusion is addressed: R4 distinguishes process-exit lock release from persistent journal state and corrects the Boundary 5A ordering. The endpoint, Oracle execution-ID semantics, trust-root attribution, and disconnected WO5 remain correct. The two findings above prevent acceptance until the restart proof and terminal target-eligibility semantics are aligned with accepted source.

No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
