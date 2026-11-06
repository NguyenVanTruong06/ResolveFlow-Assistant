# Gate 3-I17 Revision R7 Continuation — Static Code Review

**Decision: ACCEPTED (I17 R7 continuation only)**

## Scope and evidence

Reviewed the R7 continuation workorder, I17 source and test changes, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` match the handoff values at review time.

The submitted log records **424 passed, 0 failed, 0 xfailed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

## Review results

- All three revocation paths in `LocalTestOracleVerificationClient.verify_authorization()` pass `require_existing_anchor=True` to the accepted I11A API: registry composition loader, raw-manifest anchor check, and pre-verified evidence anchor check.
- Each path handles `RevocationAnchorAbsentError` as a revocation verification failure. The signed Oracle response is therefore not `VERIFIED_VALID`; the Dispatcher rejects it before adapter/gateway dispatch or completed receipt persistence.
- No path-based anchor or registry existence precheck is present in the reviewed Oracle method.
- The missing-anchor regression is no longer marked xfail and asserts zero gateway calls and no completed receipt. New tests cover missing-anchor failures for the registry and raw-manifest paths and spy on all three call paths to verify the required option is propagated.
- Existing I17 scope, plan-digest binding, Oracle response checks, mock-only receipt behavior, and safety boundaries remain in force.

No remaining finding was identified within the R7 continuation scope.

## Decision boundary and safety

This accepts the I17 R7 continuation under its existing two-file implementation/test allowlist. It does not close Gate 3 as a whole, accept Gate 6, or authorize live Resolve operations.

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
