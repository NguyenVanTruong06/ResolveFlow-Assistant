# G07-07B4-R3 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review plus offscreen regression. No Resolve launch, IPC, or timeline access.

## Independent verification

All submitted SHA-256 values matched for the report, engine, receipt service, focused tests, architecture document, and both logs. The specified 18-suite regression was rerun:

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

Result: **283 passed in 26.72s, exit code 0**. No Resolve call was made.

## Improvements verified

The revision adds a file-backed evidence verifier, checks durable target execution identity and journal action index during restart reconciliation, fails closed above the receipt scan cap, reports corrupt target state, and moves durable-admission checks inside the engine cleanup scope.

## Blocking residuals

### B4-R3-RES-01 — A single action outcome is incorrectly promoted to a whole-plan terminal state

`reconcile_target()` derives the receipt terminal state solely from the ambiguous action outcome (`src/services/resolve_execution_engine.py:780-789`): `ACTION_COMMITTED_CONFIRMED` becomes `COMPLETED`; every other accepted outcome becomes `ABORTED`. The code does not verify that all planned actions are committed before `COMPLETED`, nor that zero actions were committed before `ABORTED`. For a multi-action plan where action 0 is committed and action 1 is pending, reconciling action 1 as not dispatched can mark the whole receipt `ABORTED`; reconciling an earlier pending action as committed can mark the plan `COMPLETED` while later actions remain unexecuted. This conflicts with the binding rule that any prior committed action must never be labeled `ABORTED`, and with truthful whole-plan completion.

**Required:** Keep a plan `IN_DOUBT`/nonterminal unless durable evidence proves the terminal state of the entire plan. An action-level readback alone must not complete or abort the whole execution. Add multi-action probes for prior commit + pending-not-dispatched and pending-committed-with-later-actions; assert no false terminal state, no replay, and no target unlock until the full plan is reconciled under an explicit safe policy.

### B4-R3-RES-02 — The evidence file is self-asserted, and its byte bound is vulnerable to growth

`BoundedFileEvidenceVerifier` recomputes a digest, but the proof itself supplies the expected digest and the artifact is plain JSON with no signature/trust anchor or independently app-owned provenance (`src/services/resolve_execution_engine.py:221-321`). A caller can create a matching JSON artifact and matching digest containing the desired outcome. Also, the verifier calls `getsize()` and then unbounded `f.read()` (`:245-252`); a file that grows after the size check can exceed the claimed 10 MiB read bound. Freshness accepts an artifact with no `is_stale` field because only literal `True` is rejected (`:316-319`).

**Required:** Establish trusted provenance for the evidence (for example, an app-owned or signed evidence envelope verified against a configured trust anchor); self-supplied JSON plus its own hash is integrity, not authenticity. Require explicit freshness/observation identity and strict typed schema. Read at most `max_bytes + 1` from the opened descriptor and reject overflow, with pre/post descriptor checks as appropriate. Add forged-artifact, absent freshness, bool-as-int action index, and file-growth/TOCTOU probes.

### B4-R3-RES-03 — Strict proof primitive types are not enforced

`validate_reconciliation_proof()` compares `schema_version != 1` and action indices using normal Python equality without strict type checks (`src/services/resolve_execution_engine.py:335-373, 752-767`). Thus `schema_version=True` passes as `1`, and a boolean `action_index=True` can compare equal to integer `1` in proof and JSON evidence. That weakens the exact action binding this repair is intended to establish.

**Required:** Enforce exact `int` types (excluding bool) for schema version and action indices before equality or lookup, and strict string types for all identity/hash/outcome fields. Add positive and negative type probes at both proof and decoded artifact boundaries.

## Gate status

G07-07B4-R3 is **not accepted** yet. The 283 passing tests verify current coverage but do not cover whole-plan terminal-state truth, trusted evidence provenance, growth-safe bounded reads, or bool/int type confusion. Preserve `ready_to_execute=False`; no UI or live Resolve wiring is authorized. G07/M1/M2/M3 remain open.
