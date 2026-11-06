# Gate 6 Post-Incident Reconciliation Runbook R3 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-POST-INCIDENT-RECONCILIATION-RUNBOOK-R1.md` (Revision R3)
- **Submitted SHA-256:** `2dab5174d7fd2810b8fad11cda2a1868a216eda09180c1d1a336060c3517cb83` (matched workspace)
- **Mode:** Documentation and static source review only. No tests or runtime operations.

## Findings

### POST-INCIDENT-RUNBOOK-R3-01 — BLOCKER: Universal target-exclusion guarantee is not true for missing state

Section 4.2 says that whenever evidence is missing, conflicting, corrupted, or inconclusive, the target “remains permanently excluded” and is locked as `ACTIVE` or `IN_DOUBT_LOCKED`. Accepted source does not guarantee that for all such cases. `_recover_from_files()` only reconstructs an in-doubt target for an unmatched `ACTION_PENDING` (`src/services/durable_dispatch_journal.py:538–589`). A terminal WAL record removes that execution from `unmatched_pending` (`:552–555`); if the target ledger is absent, no target status is restored. `reserve_target()` blocks based on `_in_doubt_targets`, the loaded `_target_state`, or a reused execution ID, but does not reconstruct target exclusion from terminal WAL records (`:656–668`). With a missing ledger and a new execution ID, the blanket claim of permanent target exclusion is therefore unsupported. Section 8.1 also says “every failure mode fails closed safely,” which overstates the implementation.

**Required correction:** Replace the guaranteed-exclusion claim for incomplete/conflicting evidence with an operational stop: do not start or invoke the Dispatcher; do not infer that the target is locked from absent or partial artifacts. Distinguish states for which source demonstrably enforces exclusion from states that require the service to remain stopped pending separately authorized review. Narrow the “every failure mode” claim accordingly.

### POST-INCIDENT-RUNBOOK-R3-02 — MEDIUM: Strict JSON example still references a nonexistent parser and does not validate root type

Section 3.3's example `parse_line_strictly()` uses an `object_pairs_hook`, so it rejects duplicate keys, but it returns any JSON value; it does not enforce the promised dictionary root. The text then claims a non-dictionary root raises a fatal error. It also offers `src/utils/strict_json.py:parse_strict_json()` as an alternative, but that path does not exist in this workspace; the accepted parser is `src/core/contracts/oracle_ipc_contracts.py:parse_strict_json()` (`:182–204`).

**Required correction:** Add an explicit `isinstance(parsed, dict)` check that fails closed, remove the nonexistent parser reference, and state exactly which submitted JSON documents and JSONL records use the strict parser. Keep all processing on submitted copies only.

### POST-INCIDENT-RUNBOOK-R3-03 — MEDIUM: Quantitative crash-risk assertion has no supporting evidence

Section 8.1 calls the crash probability during the “~2ms” receipt-to-ledger window “statistically negligible,” but the runbook supplies no measurement, reliability model, workload, or source for either the duration or probability. This assertion is used to justify making Option A the **permanent** baseline and avoiding recovery implementation, despite the runbook documenting that some crash states are not automatically latched or excluded.

**Required correction:** Remove the unsupported probability/duration claim, or identify an auditable evidence basis and clearly limit what it establishes. Frame Option A as a current governance recommendation subject to review, not a permanent conclusion derived from an unsubstantiated risk estimate.

## Safety disposition

R3 correctly separates Oracle replay consumption from Dispatcher target locking, makes STATE-06 diagnostic-only, and provides an actual duplicate-key hook. The remaining findings must be addressed before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
