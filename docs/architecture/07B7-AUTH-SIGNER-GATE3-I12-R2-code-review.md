# G07-AUTH-SIGNER-GATE3-I12 Revision R2 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Local-Test Authorization Admission Cross-Binding Core  
**Review date:** 2026-11-03  
**Review type:** Static source, tests, workorder, and submitted-evidence review. Tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `11f8793fd379d5c2feff47f1c451b3a3f3a7506dcc920a6c9fee5cd09ae8a4c1`.
- Completion report: `.bridge/report.md` — SHA-256 `1225f70473694a59072a7ea91964d1da51f6d22b04bec64d8b43f45ec86e8840`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `3439e6c34ae9c807d5b4ce625a1a556e6c02ccfb16ad6351370a2cdb7d5fc7ad`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `c2e95d08bbf14b69cfc424007f9ad78f548aa9260bf2bb6a3ba7d561255dad61`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `ad22d1bb6a1f22a6c83f82196ca532fef726db51eed0b1216dd3122147c110be`.
- R1 review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I12-R1-code-review.md` — SHA-256 `689bb35871249f60df12ce29d8f00402a826e4ab325a54095b5a9ee67f0c642f`.

The submitted source, test, workorder, report, and log hashes match the workspace. The log reports 231 passed, 0 failed, exit code 0. This is submitted evidence only; it was not reproduced during this static review. The R1 changes address the reported public-key subclass, plan target/action, Unicode comparison, and pre-verifier path-policy findings. One residual state-mutation issue and one remaining digest-binding gap prevent acceptance.

## Findings

### I12-R2-01 — BLOCKER: Non-mutating revocation check still creates a persistent lock file

The I12 admission path passes `advance_anchor=False` at `src/services/trial_authorization_signer_service.py:3142–3148`. However, `check_and_advance_revocation_epoch_anchor_for_local_test()` unconditionally opens `revocation_epoch.lock` with `O_CREAT` at `:2127–2136`, before it reaches the `advance` branches at `:2268–2328`. On a fresh registry directory, a successful or rejected admission therefore creates a persistent directory entry even though the epoch anchor is not written. The R2 test at `tests/services/test_trial_authorization_signer_service.py:4944–4986` checks only that `revocation_epoch.anchor` is absent; it does not assert that the directory remains unchanged or that `revocation_epoch.lock` was not created.

This violates the workorder's verification-only requirement not to mutate revocation files or any other state. Make the admission path genuinely read-only, or explicitly revise the workorder to authorize and account for a lock-file side effect. Add tests comparing the registry directory contents and bytes before and after both successful and rejected admissions, including the no-existing-anchor case.

### I12-R2-02 — HIGH: Action payload digest is still only caller-asserted, not bound to the planned action

R2 now matches action type, track, source path, frame start, and duration to an entry in `execution_plan.planned_actions` (`src/services/trial_authorization_signer_service.py:3250–3272`). But the action payload digest is checked only against `operator_context.expected_action_payload_digest_sha256` and the signed consent envelope at `:3274–3285`. The context contract explicitly describes expected digest fields as caller-prepared assertions with no independently trusted provenance (`:466–478`). No canonical payload is assembled from the matched planned action and target claims, and no digest is recomputed and compared to the consent digest.

Consequently, a valid consent whose individual action fields match the Director-approved plan can still carry an arbitrary action-payload digest chosen in the caller context and signed by the Operator. The returned admission evidence then includes that unverified digest. Bind it to the source-owned canonical action payload for the matched planned action; if the accepted contracts do not provide enough data to compute it at this stage, stop and report that dependency rather than treating the caller assertion as plan-bound. Add a negative test where all individual fields match the plan but the consent digest differs from the recomputed canonical action payload digest.

## Review conclusion and limits

**I12 R2 is not accepted.** The non-mutating revocation path still changes the registry directory, and the action payload digest is not proven to describe the plan-bound action. Address both findings in a bounded revision and resubmit for review.

No tests were run during this review. No Resolve, IPC, network, private-key, or production trust-root operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only.
