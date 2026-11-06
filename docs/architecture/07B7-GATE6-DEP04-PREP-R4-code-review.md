# Gate 6 DEP-04-PREP-R1 Revision R4 — Static Candidate Harness Review

**Workorder:** G07-07B7-GATE6-DEP04-PREP-R1 — Hash-Pinned Harness Preparation (Non-Executing)  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review only. The candidate was not executed, imported, compiled, or syntax-checked. No Resolve/Oracle connection, host operation, production state/key access, or live operation occurred.

## Artifact integrity

All submitted SHA-256 values match the workspace. Runtime and archived candidates are byte-identical:

- `.bridge/task.md`: `0dddb75ed63994b967c022845ae236b4275420b6aba23ae16b61d70a2017c60b`
- `/tmp/resolveflow-gate6/run_gate6.py`: `337bf1f0a35412041259bb3ad083a9abee69fac97f6f5e58743d05687278575c`
- `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`: `337bf1f0a35412041259bb3ad083a9abee69fac97f6f5e58743d05687278575c`
- `.bridge/report.md`: `789cc36c5db83f1d42a1ebb7b9be002720f7b446a2a7c8a7084e95360a0e9554`

## Findings

### DEP04-PREP-R4-01 — Gate evidence is still unauthenticated and does not bind the active execution — BLOCKER

R4 removes caller-selected evidence paths and compares some JSON claims to the authorization, but the records are still accepted as ordinary unsigned JSON or text:

- Gate 2 accepts `user_decision == "GRANTED"` and matching target/action fields from `.bridge/gate2-authorization.json` without verifying an authenticated signature, provenance seal, or separately trusted record identity (`run_gate6.py:293-318`). A caller able to write that file can manufacture the purported grant.
- Gate 4 accepts the pinned `.bridge/preflight-signoff-GATE4-R16-R3.json` via `json.load` without verifying its integrity/provenance. It checks target/topology and a timestamp window but does not validate a trusted signoff seal or independently verify the required zero-dispatch evidence (`:321-363`).
- The challenge nonce binding is conditional: `getattr(auth, "challenge_nonce", None)` makes the comparison optional (`:365-371`). `TrialExecutionAuthorization` in `src/core/contracts/trial_authorization_contracts.py` has no `challenge_nonce` field; therefore this accepted DTO cannot provide the claimed signed nonce binding and the check is skipped. The timestamp checks also do not reject future `issued_utc` values.
- Operator presence is accepted from unsigned `.bridge/operator-presence-attestation.json`, based on a status string, matching UIDs and a timestamp (`:391-416`). The age check accepts a future timestamp because it only rejects ages greater than 120 seconds. These fields do not establish that a human supplied a contemporaneous attestation.
- Gate 5 still accepts either of two substrings in `.bridge/task.md` rather than parsing and authenticating the active execution workorder, its status, the exact reviewed harness hash, and the authorized command (`:275-290`).

These checks do not establish trustworthy authorization or contemporaneous presence. Pinning a path does not authenticate the file contents. The current task is preparation-only, and the top-level dependency stop prevents reaching these checks today; that does not make them safe for a future execution workorder.

**Required remediation:** Define and verify trusted provenance for Gate 2, Gate 4, and operator-attestation records; bind them to the same authorization, challenge, target, action, and reviewed harness hash. Require the active Gate 5 workorder to be parsed and verified as an issued execution authorization, not detected by substring. Enforce timestamps within both bounds (`issued <= now <= expires`, and attestation time not in the future) and verify zero-dispatch evidence. If accepted contracts cannot express these bindings, stop and report the upstream dependency; do not invent unsigned JSON as authority.

### DEP04-PREP-R4-02 — Action idempotency hash is compared to a different identifier domain — MEDIUM

The new check requires `action.action_idempotency_hash` to equal either `auth.execution_id` or `auth.action_idempotency_hash` (`run_gate6.py:575-581`). The accepted `TrialExecutionAuthorization` DTO has no `action_idempotency_hash` field (`src/core/contracts/trial_authorization_contracts.py:745-790`), so the second comparison is always against `None`. The remaining condition requires a plan idempotency hash to equal the execution ID, although the accepted `PlannedTimelineAction` contract models these as separate values (`src/core/timeline_plan_execution_binder.py:276-315`). This can reject valid plans and does not implement a contract-defined binding.

**Required remediation:** Remove this cross-domain comparison and rely on the already recomputed canonical action payload digest binding to `auth.action_payload_digest_sha256`, or use a separately accepted contract field that explicitly binds the action idempotency hash. Do not infer equality between the hash and execution ID.

## Prior finding disposition

- **DEP04-PREP-R3-02:** Remediated for this preparation candidate. `run_harness()` raises `ERR_DEP01_PRINCIPAL_BOUNDARY_DEPENDENCY` before loading authorization, opening the Oracle public-key path/socket, or initializing the production journal. Do not remove this stop until a separate upstream workorder resolves the Signer-keystore principal boundary.
- **DEP04-PREP-R3-03:** The no-op was removed, but the replacement predicate has the contract mismatch described in DEP04-PREP-R4-02.

## Decision and safety state

Revision R4 is **not accepted** as a future execution candidate. It remains non-executing because the dependency stop is the first statement in `run_harness()`. No candidate command, import, compile, test, socket connection, or Resolve operation was performed during this review.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Do not issue a live execution workorder based on this candidate.
