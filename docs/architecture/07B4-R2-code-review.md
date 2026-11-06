# G07-07B4-R2 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review and offscreen regression only; no Resolve launch, IPC, or timeline access.

## Independent verification

All submitted SHA-256 values matched for `.bridge/report.md`, the engine, receipt service, focused tests, design document, and both evidence logs. The prescribed 18-suite regression was rerun:

```text
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest \
  tests/services/test_resolve_execution_engine.py \
  tests/core/test_timeline_plan_execution_binder.py \
  tests/core/test_recovery_assessment_contracts.py \
  tests/services/test_recovery_classifier_service.py \
  tests/services/test_recovery_coordinator.py \
  tests/core/test_execution_receipt_contracts.py \
  tests/services/test_execution_receipt_journal_storage.py \
  tests/core/test_proposal_builder.py \
  tests/core/test_proposal_builder_source_evidence.py \
  tests/ui/test_common_proposal_review.py \
  tests/ui/test_director_preflight_shutdown.py \
  tests/ui/test_director_step4_approval_gate.py \
  tests/ui/test_director_step4_readiness_authority.py \
  tests/ui/test_preflight_worker.py \
  tests/ui/test_preapproval_evidence_worker.py \
  tests/ui/test_director_readiness_ui.py \
  tests/ui/test_recovery_assessment_ui.py \
  tests/ui/test_review_preapproval_evidence.py -q
```

Result: **280 passed in 25.96s, exit code 0**. No Resolve call was made.

## Improvements verified

The code now has an immutable proof DTO, target and execution identity comparisons for the in-memory registry path, durable target-state records, and restart admission checks. Existing tests cover the submitted scenarios. These changes do not yet make reconciliation evidence independently verifiable or restart admission fail-closed across the full artifact set.

## Blocking residuals

### B4-R2-RES-01 — Evidence digest is syntax-checked, not verified

`OperatorReconciliationProof` carries `readback_evidence_hash`, but `validate_reconciliation_proof()` only checks that it matches a 64-character lowercase-hex pattern (`src/services/resolve_execution_engine.py:231-234`). No evidence artifact is loaded, its digest recomputed, or its contents tied to an authoritative physical readback. Any caller can construct the dataclass with an arbitrary hash and a claimed outcome. The new object type prevents a plain dict but does not establish the truth of the reconciliation.

**Required:** Inject a bounded evidence verifier/provider. Verify the referenced artifact's bytes and digest, bind its target/execution/action identity to the unresolved durable record, and validate the claimed outcome against the evidence. A fake verifier may be used in tests; production remains unwired and mock-only.

### B4-R2-RES-02 — Post-restart reconciliation can overwrite a different unresolved execution

`reconcile_target()` uses the proof's target and execution ID, but when the in-memory registry is empty it does not first compare them to the durable target state's `active_execution_id`, nor does it verify `in_doubt_action_index` against the durable journal's pending action (`src/services/resolve_execution_engine.py:499-547`). It can transition the proof-named receipt (or silently skip its transition on error) and then persist `state="RECONCILED"`, potentially clearing durable ownership for a different unresolved execution on that target.

**Required:** Under the target lock, load and strictly validate the durable target record, resolve the exact active execution, replay its journal without modifying bytes, and require proof target/execution/action/outcome to match that evidence before any receipt or target-state mutation. Any missing, mismatched, corrupt, or ambiguous artifact must retain the unresolved state and locks. Add post-restart probes with a wrong execution ID, wrong action index, missing receipt, torn journal, and mismatched target-state active execution; all must produce zero state changes.

### B4-R2-RES-03 — Bounded scan silently ignores receipts beyond the newest 1,000

`_check_durable_target_admission()` sorts all receipt filenames by `getmtime()` and checks only `receipt_files[:1000]` (`src/services/resolve_execution_engine.py:439-446`). If there are more than 1,000 receipts, an older nonterminal receipt can be omitted. A missing or corrupt target-state file is treated as no state (`ExecutionReceiptService.get_target_state()`, `src/services/execution_receipt_service.py:189-199`), so that older unresolved execution can be missed. Also, metadata sort touches every filename and can raise before the outer execution `try/finally` begins (`resolve_execution_engine.py:731-753`).

**Required:** Treat invalid/unreadable target-state files and receipt counts above the scan cap as explicit fail-closed evidence errors. Do not select a recent subset as if it were complete. Ensure scan work and failure cleanup are bounded and that all acquired locks are released or retained under an explicit unresolved owner on every exception. Add >1,000 receipts with an oldest active target, corrupt target-state, stat/list failure, and exception-before-main-try probes.

## Gate status

G07-07B4-R2 is **not accepted** yet. The 280 passing tests establish regression status but do not close the three residual safety paths above. Preserve `ready_to_execute=False`; do not wire to UI or live Resolve. G07 and M1/M2/M3 remain open.
