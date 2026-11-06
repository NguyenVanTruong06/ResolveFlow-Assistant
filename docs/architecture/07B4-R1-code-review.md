# G07-07B4-R1 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review and offscreen regression only. No Resolve launch, IPC, timeline access, or production/test edits in this review.

## Independent verification

Submitted SHA-256 values match for the report, engine, receipt service, focused tests, architecture document, and both evidence logs. The prescribed 18-suite regression was rerun:

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

Result: **277 passed in 26.10s, exit code 0**. Static search found no UI import of `resolve_execution_engine`; no Resolve call was made.

## Accepted improvements, within this mock-only slice

The implementation adds target-scoped advisory locks, per-idempotency advisory locking around receipt creation, action-boundary occupancy checks, post-dispatch persistence failure classification, retained in-process lock ownership, and a marker-based fake-gateway admission check. The focused tests cover the principal paths and the regression set remains green.

## Blocking residuals

### B4-R1-RES-01 — Unresolved release accepts a label instead of verifiable evidence

`UnresolvedTargetRegistry.resolve_and_release()` (`src/services/resolve_execution_engine.py:204-229`) releases both locks when the caller passes a plain mapping whose only relevant value is `verified_by == "OPERATOR_RECONCILIATION"`. It does not bind the proof to the stored execution ID, target, in-doubt action, or a verified physical readback/reconciliation artifact. The current positive test supplies descriptive strings and is accepted by this one-field gate. Any caller can construct the same mapping, so this does not prove the timed-out action physically ended or was reconciled.

**Required:** Use a strict owned proof object bound to the exact unresolved record and target, containing independently validated reconciliation evidence (including the action/outcome identity and an authoritative physical-completion or operator-readback record). Reject plain dictionaries, missing/extra/mismatched identity, stale proof, and incomplete evidence. The positive test may use a fake evidence provider, but must exercise the same validation contract.

### B4-R1-RES-02 — Process restart drops target-level unresolved admission

`GLOBAL_UNRESOLVED_TARGET_REGISTRY` is in-memory only (`src/services/resolve_execution_engine.py:170-247`). A process crash releases OS advisory locks and destroys this registry. On restart, `execute_plan()` checks the idempotency key for the submitted plan, but it does not discover other nonterminal receipts for the same target. A different plan/idempotency key for that project/timeline can therefore acquire the now-free target lock and dispatch while the prior action may still be physically unresolved.

**Required:** Before admitting a target, consult durable receipt/journal evidence for that target and fail closed on any nonterminal or ambiguous execution, even when the new request has a different idempotency key. Persist enough target ownership/reconciliation state to survive process restart, or derive it safely from bounded durable artifacts. Add a fresh-engine/restart simulation that leaves a pending/EXECUTING record, constructs a new registry and engine, submits a different plan for the same target, and proves zero dispatch until valid reconciliation.

## Gate status

G07-07B4-R1 is **not accepted as a complete safety repair** until both residuals are closed. In particular, no live Resolve wiring or execution authorization is granted. Preserve `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain open.
