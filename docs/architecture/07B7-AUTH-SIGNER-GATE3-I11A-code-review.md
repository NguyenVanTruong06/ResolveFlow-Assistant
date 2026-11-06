# Gate 3-I11A — Atomic Required-Anchor Verification Review

**Decision: ACCEPTED (I11A API slice only)**

## Scope and evidence

Reviewed the I11A workorder, source and test changes, report, and submitted focused pytest log. The supplied SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trial_authorization_signer_service.py`, `tests/services/test_trial_authorization_signer_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I11A/pytest.log` match the handoff values at review time.

The submitted log records **236 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

## Review results

- Both `check_and_advance_revocation_epoch_anchor_for_local_test()` and `load_verify_and_anchor_revocation_manifest_for_local_test()` expose the new keyword-only `require_existing_anchor: bool = False`, preserving existing call sites and default behavior.
- The parameter is checked using exact built-in `bool` validation before descriptor/filesystem access. The I11 composition wrapper also propagates it to the registry loader.
- When the anchor is absent and `require_existing_anchor=True`, the anchor checker raises `RevocationAnchorAbsentError` with code `ERR_REVOCATION_ANCHOR_ABSENT` only after the descriptor-relative anchor read determines that no anchor exists, while the existing synchronization lock is held. It does not bootstrap or mutate the directory in this case, including with `advance=True`.
- Existing default bootstrap/read-only behavior, present-anchor validation, rollback detection, and same-epoch digest tampering checks are preserved.
- The focused tests cover the required default, missing/present anchor, rollback/tampering, advance-plus-require, invalid parameter, no-path-probe, and loader propagation cases.

No remaining finding was identified within the I11A scope.

## Decision boundary and next step

This decision accepts only the bounded I11A API change. It does not accept or close I17 R6. I17 must pass `require_existing_anchor=True` through its descriptor-anchored revocation path, remove the strict xfail for the missing-anchor case, and submit a fresh authorized test log for review under the existing I17 workorder and allowlist.

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
