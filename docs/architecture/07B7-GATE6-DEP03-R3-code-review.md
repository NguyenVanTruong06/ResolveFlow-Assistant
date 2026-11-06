# Gate 6 DEP-03-R2 Revision R3 — Static Code Review

**Workorder:** G07-07B7-GATE6-DEP03-R2 — LiveCanaryDispatchService Continuation  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review. The authorized pytest command was not rerun. No daemon, host service, production state, Resolve SDK/API, or live operation was accessed.

## Artifact integrity

All six submitted SHA-256 values match the allowlisted files present in the workspace:

- `src/services/live_canary_dispatch_service.py`: `b3dfed3801db11852ac6be0566895987d65f75ad2f77b35481c4ed82b9139bb3`
- `src/services/durable_dispatch_journal.py`: `08fff9817042b2cc70f1f0bcc53815089052dfd4a943034f72f9dc87a81f883a`
- `tests/services/test_live_canary_dispatch_service.py`: `c5d2d7bd76d83dc186ad5db2db8c0b2727e2f60788bbc221057005ff8d2569f5`
- `tests/integration/test_live_dispatch_oracle_integration.py`: `17ff9c29ba75192787daf174a288c1fb161115c71074b26f3e10c8d08093d306`
- `.bridge/ux-evidence/DEP03/pytest.log`: `8a952f9a7b3cf98f86978bc8d998313e15247f02163c407bd4c82f6ab7f9afd0`
- `.bridge/report.md`: `f4e005eae6d8fe54bc30bae0ebee4b00d344c8cbbeaee9e4b6aec32140316ecd`

The submitted log reports 29 passing tests. This is recorded as submitted evidence; it was not independently rerun in this static review.

## Findings

### DEP03-R3-01 — Clean-abort proof is caller-constructible — HIGH

`PreMutationCleanAbortProof` is an ordinary public class with a public initializer accepting caller-controlled `execution_id`, `reason`, and timestamp (`live_canary_dispatch_service.py:118-144`). The adapter exception handler accepts any exact instance whose `execution_id` matches (`:829-838`). An adapter can therefore raise `PreMutationCleanAbortError(PreMutationCleanAbortProof(execution_id=..., ...))` after crossing the mutation boundary, and the dispatcher will persist `ABORTED`, notify Oracle, and release the target lock as though zero mutation had been proven.

The exact-type checks prevent subclass spoofing but do not establish provenance or prove that the adapter created the proof before mutation. The added test constructs the proof directly, confirming that this path is externally available.

**Required remediation:** Make clean-abort evidence originate from a trusted, narrowly scoped adapter pre-mutation boundary and bind it to the exact execution/action/target. The dispatcher must validate provenance or an unforgeable authority-owned capability, not just the DTO's nominal type and execution ID. Any unproven exception must remain `IN_DOUBT` with the target locked. Add a regression that attempts to construct/raise a matching proof after the mutation boundary and proves no `ABORTED` receipt, no lock release, and an `IN_DOUBT` result.

### DEP03-R3-02 — Successful adapter result can omit action binding — HIGH

The completion validator only checks `action_id` if that key is present (`live_canary_dispatch_service.py:813-820`). It never requires the action identity to be returned, and it does not validate a required `action_idempotency_hash` claim. Thus a result with `status`/`receipt_state == "COMPLETED"`, matching execution and timeline IDs, a nonempty receipt ID, and `terminalized is True` can be accepted even when it contains no action binding at all. The result is then committed as `COMPLETED` and sent to Oracle (`:897-923`). This does not meet the workorder's requirement to bind the completion receipt to the exact single action.

**Required remediation:** Require an exact, documented action binding in every success result and compare it to the authorized action (including its idempotency hash). Reject absent, malformed, or mismatched binding as `IN_DOUBT`; never commit completion based on optional metadata. Add missing-field and mismatch regressions proving no successful receipt and retained target lock.

### DEP03-R3-03 — Existing journal nodes are not checked for required modes — HIGH

The journal verifies regular-file type and link count when reading or appending, and conditionally checks UID, but does not enforce the required `0600` mode on existing WAL, audit, state, receipt, marker, or lock nodes (`durable_dispatch_journal.py:301-340, 342-399, 405-431, 632-655, 680-826`). It also accepts an existing `.locks` directory without checking its mode (`:283-296`). `enforce_ownership` defaults to `False`, and even when enabled it does not add mode checks for these nodes. Consequently pre-existing permissive files/directories can be read or appended without failing closed, contrary to §3.4's unsafe-mode rejection and the claimed validation of mode before mutation.

**Required remediation:** Validate the descriptor-observed exact mode (and configured ownership policy) for `.locks` and every existing state, WAL, audit, receipt, marker, and lock file before reading or mutating it. Reject unsafe nodes without chmod/chown or overwrite. Add tests for permissive modes on each relevant node class and assert no append, replacement, lock acquisition, or success state escapes.

## Decision and safety state

Revision R3 is **not accepted** pending remediation of the findings above. The six R2 findings are materially addressed in the reviewed code, including pinned key/target bindings, digest recomputation, Oracle response bindings, descriptor-relative journal operations, and stable target-lock inode retention. The remaining findings block acceptance because they affect terminal-state truth and persistence security.

Do not issue a Gate 6 live-execution workorder or transition readiness based on this submission. Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. No live operation, host change, production key/state access, or Resolve access was performed as part of this review.
