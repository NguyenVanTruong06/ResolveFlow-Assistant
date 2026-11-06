# Gate 3-I17 Revision R6 — Static Code Review

**Decision: CHANGES_REQUIRED — BLOCKED ON ACCEPTED I8/I11 API DEPENDENCY**

## Scope and evidence

Reviewed the I17 workorder, R6 implementation and tests, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` match the handoff values at review time.

The submitted log records **412 passed, 1 xfailed, 2 warnings**, exit code 0. Tests were not rerun during this static review. The strict xfail is the missing-anchor case and records the unresolved requirement; it is not a passing demonstration of fail-closed behavior.

## Review finding

### I17-R6-01 — BLOCKER: Required anchor presence cannot be guaranteed with the accepted read-only API

R6 correctly removes all `Path.is_file()` checks from the Oracle verifier and reports the upstream dependency rather than retaining a path-based approximation. The remaining implementation calls the accepted `load_verify_and_anchor_revocation_manifest_for_local_test(..., advance_anchor=False)` or `check_and_advance_revocation_epoch_anchor_for_local_test(..., advance=False)` APIs.

The accepted I8/I11 implementation treats a missing anchor as successful on this non-mutating path: `src/services/trial_authorization_signer_service.py:2551–2554` returns when `existing_epoch is None and not advance`. Consequently, I17 cannot distinguish an existing verified anchor from an absent anchor atomically while the directory descriptor and lock are held. The strict xfailed test `test_i17_r4_01_oracle_rejects_missing_anchor_file_fails_closed` confirms this behavior. In the missing-anchor case, I17 cannot establish the required durable anti-rollback state before issuing a successful Oracle response.

R6's stop-and-report action is correct under the current allowlist and workorder. The dependency nevertheless prevents acceptance of the I17 contract.

**Required next step:** issue a separate bounded upstream workorder for the accepted I8/I11 primitive. Add an explicit read-only option such as `require_existing_anchor=True` that raises a structured `ERR_REVOCATION_ANCHOR_ABSENT` from the descriptor-relative anchor read while the directory descriptor and synchronization lock are held. Preserve the existing non-mutating bootstrap behavior for callers that need it. Add deterministic tests for missing-anchor rejection, existing-anchor success, and concurrent removal/replacement around the read. After that upstream API is reviewed and accepted, resume I17 within its existing two-file source/test allowlist, remove the strict xfail, and verify the dispatcher fails closed on absent anchor evidence.

## Decision and safety state

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

I17 R6 is **not accepted**. The path-probing remediation is accepted as a correction, but the anchor-presence requirement remains blocked by the upstream API dependency above. Do not widen I17's allowlist or approximate the check with a path-based preflight.
