# G07-07B5-D — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Document and source review only. No production/test edits, pytest, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes matched:

- `.bridge/report.md`: `f212e01871cbc4457b4317e342e663458afdc2e7a1afe3c722249c6628459205`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `edcdb83534b491905688120678a1ee0db8a3f9222c010c33448dc9c2cf0c3c3f`

The document covers the requested callgraph, authorization preconditions, action subset, isolation/recovery, acceptance matrix, and proposed next slice. The following safety and source-contract issues must be resolved before approving implementation.

## Blocking findings

### B5-D-RES-01 — Existing approval DTO is not a signed operator authorization

The design diagrams and preconditions treat `AppOwnedApprovalRecord` as a signed approval with `SCOPE_PROPOSAL_DISPOSAL_TRIAL`, `operator_id`, and `plan_hash`. The actual contract in `src/core/contracts/edit_plan_approval_contracts.py` has six fields (`approval_id`, `proposal_id`, `proposal_revision`, `approved_at_iso`, `accepted_omission_ids`, `approval_scope`), and its only allowed scope is `ENTIRE_PROPOSAL`. It has no operator identity, signature, or canary scope. Do not relabel proposal approval as authorization to mutate Resolve. Specify a separate trial-authorization contract and its actual trust/consumption boundary, or explicitly defer operator authorization to a separate approved workorder.

### B5-D-RES-02 — Proposed adapter conflicts with the engine's deliberate mock-only tripwire

`ResolveExecutionEngine._validate_mock_gateway_admission()` requires `_is_approved_mock_gateway is True` and rejects gateways exposing `_automation`, `resolve`, or `_resolve_override`. A live `IsolatedResolveTrialAdapter` must not pass by setting the mock marker or hiding the live connection behind a wrapper. The proposed B6 scope does not include an authorized engine boundary that could safely distinguish mock mode from an explicitly approved live trial. Define a non-bypassable separation and migration plan; keep the existing mock-only engine contract unchanged until a separate live-authorization decision is approved.

### B5-D-RES-03 — Name prefixes do not prove a disposable project/timeline

The `RF_TRIAL_` / `RF_TRIAL_TL_` regex is only a naming check. A pre-existing user project can use those names. Bind admission to exact, independently observed project and timeline UIDs plus an operator-created trial record and evidence that the target is newly created and disposable. Define how the system prevents a stale/reused name or UID from authorizing a trial. Keep the deny-by-default behavior if that evidence is unavailable.

## Additional required corrections

1. The recovery example calls `engine.reconcile_target(proof)` twice. Remove the duplicate call and state that reconciliation is one operator-authorized attempt; subsequent calls follow the durable idempotent/reconciled behavior.
2. The design uses `FAILED_PRE_MUTATION` as a receipt state. This is not in `VALID_RECEIPT_STATES` (`INITIALIZED`, `EXECUTING`, `COMPLETED`, `ROLLED_BACK`, `ABORTED`). Map pre-dispatch denial to a supported state/result contract or label it as a non-persisted result; do not invent a receipt state implicitly.
3. Ensure the proposed single clip's frame-zero placement is tied to the actual Resolve timeline origin/timecode mapping and exact `PlannedTimelineAction` semantics. State how the imported Media Pool item's identity and file digest are bound to the action at dispatch time.

## Required next step

Complete `G07-07B5-D-R1` as **DESIGN ONLY**. Correct the source-grounded contracts, mock/live boundary, disposable target proof, state semantics, and duplicate reconciliation example. Keep the proposed B6 implementation deferred. Do not edit `src/` or `tests/`, run tests/apps, connect to Resolve, or enable `ready_to_execute`.

## Safety status

G07-07B5-D is not accepted for implementation. `ready_to_execute=False`, zero live Resolve wiring/calls, and G07/M1/M2/M3 OPEN remain mandatory. Any implementation of a live adapter or a real Resolve trial requires a separate explicit user authorization and separately reviewed workorder.
