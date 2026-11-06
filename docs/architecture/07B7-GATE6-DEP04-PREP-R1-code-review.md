# Gate 6 DEP-04-PREP-R1 — Static Candidate Harness Review

**Workorder:** G07-07B7-GATE6-DEP04-PREP-R1 — Hash-Pinned Harness Preparation (Non-Executing)  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review only. The candidate was not executed, imported, compiled, or syntax-checked. No Resolve/Oracle connection, host operation, production state/key access, or live operation occurred.

## Artifact integrity

The submitted SHA-256 values match the files present in the workspace. The runtime candidate and archived copy compare byte-identical:

- `.bridge/task.md`: `0dddb75ed63994b967c022845ae236b4275420b6aba23ae16b61d70a2017c60b`
- `/tmp/resolveflow-gate6/run_gate6.py`: `132194a9bbf4d9dd7d0bd50ff78555b5271b481da460382c1ce7a2c18e2a669e`
- `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`: `132194a9bbf4d9dd7d0bd50ff78555b5271b481da460382c1ce7a2c18e2a669e`
- `.bridge/report.md`: `369f4cad9b79ba3405a71dbb1633887e7d421bc80172cb99a1f31e5867e20962`

## Findings

### DEP04-PREP-R1-01 — Candidate imports unavailable digest APIs and constructs the wrong action DTO — BLOCKER

The candidate imports `compute_canonical_action_payload_digest_for_admission` and `compute_canonical_plan_digest` from `src.core.contracts.trial_authorization_contracts` (`run_gate6.py:44-50`). Static inspection shows those functions are defined in `src.services.trial_authorization_signer_service` and `src.core.timeline_plan_execution_binder`, respectively, not in the imported contracts module. Thus the candidate's guarded CLI path cannot load those imports successfully.

Separately, it imports `PlannedTimelineAction` from `trial_authorization_contracts` and calls it with fields including `speed`, `action_idempotency_hash`, and `action_params` (`:204-220`). That DTO does not define those constructor fields. The dispatcher requires the exact `PlannedTimelineAction` type from `timeline_plan_execution_binder`; that is a different class with a different constructor and geometry model. The candidate also constructs `TimelineExecutionPlan` with a partial set of fields (`:222-231`) rather than its required bound plan structure.

**Required remediation:** Import the accepted digest functions and nominal DTOs from their actual defining modules, then build a complete bound plan/action using the exact types and required fields consumed by `LiveCanaryDispatchService`. Add a separate reviewable contract/plan validation step in a later authorized verification workorder; do not execute this candidate under PREP-R1.

### DEP04-PREP-R1-02 — Live adapter construction is incompatible with its required interface — BLOCKER

`run_harness()` constructs `LiveResolveCanaryAdapter()` with no arguments (`run_gate6.py:324-325`). Its checked-in constructor requires `coordinator`, `journal`, and `resolve_client` (and subsequently establishes a proof-ledger directory). The call therefore cannot create the adapter; if reached, it fails before dispatch. The harness also does not construct or inject the accepted recovery coordinator, execution journal, Resolve client, or proof ledger dependencies.

**Required remediation:** Resolve the accepted adapter composition/deployment boundary in a bounded workorder and construct it only from the approved dependencies. Do not instantiate a Resolve client or call the adapter as part of PREP-R1. If the exact production composition cannot be expressed within the future workorder's allowlist and accepted APIs, stop and report that dependency rather than introducing a mock or bypass.

### DEP04-PREP-R1-03 — Readiness flag substitutes for required Gate 2/4/5 evidence — HIGH

The candidate's only execution authorization gate is the caller-controlled CLI flag `--ready-to-execute` (`run_gate6.py:355-399`), which sets the `ready_to_execute` argument checked at `:114-121`. It does not load or verify Gate 2 authorization evidence, current Gate 4 preflight/signoff and target freshness, or Gate 5's exact one-action workorder. It also does not require contemporaneous operator-presence attestation. A caller supplying a valid authorization file and the flag can therefore enter production-path initialization without the evidence gates required by DEP04-PREP-R1 §3 and DEP-01 §7.5.

**Required remediation:** Remove the boolean/CLI flag as a substitute for gate evidence. Require independently verifiable, current, target-bound Gate 2/4/5 evidence and reject absent, stale, mismatched, or unverified records before opening production paths or connecting to Oracle. The future execution workorder must separately pin the harness hash, set any execution switch itself, and require the operator's direct visual attestation immediately before issuing a fresh challenge. Do not add or mint those records in this preparation revision.

## Decision and safety state

The candidate is **not accepted** for future execution review pending remediation of the findings above. The archive is byte-identical to the runtime copy, but hash identity does not cure its interface and readiness defects. PREP-R1 remains non-executing; no candidate command, import, compile, test, socket connection, or Resolve operation was performed in this review.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. No future live execution workorder should rely on this candidate until a revised hash-pinned artifact is statically reviewed and runtime prerequisites are separately established.
