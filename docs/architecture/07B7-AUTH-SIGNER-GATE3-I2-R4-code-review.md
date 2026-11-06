# Static Code Review — G07-AUTH-SIGNER-GATE3-I2 Revision R4

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the R4 workorder, report, approval-store implementation and tests, submitted pytest log, and bridge handoff. Submitted SHA-256 values match the current workspace for the workorder, report, both allowlisted files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I2/pytest.log`.

The submitted log records **82 passed, 0 failed in 0.46s, exit code 0**. This review inspected the submitted log and did not rerun tests.

The R3 findings are addressed: `get()` preserves a fallback reader-close error alongside the primary failure, and constructor cleanup preserves the initialization cause and supplies a diagnostic handle when cleanup fails. One descriptor-state ambiguity remains in that diagnostic contract.

## Finding

### I2-R4-01 — Diagnostic field labels ambiguous close outcomes as definitely unclosed (HIGH)

`ApprovalStoreInitializationError.unclosed_descriptors` is populated from `_cleanup_failures` (`src/services/authoritative_approval_store.py:70-86, 179-199`). `close()` records `(descriptor_type, fd, OSError)` after disowning the descriptor and making exactly one `os.close(fd)` attempt (`:598-614`). Under POSIX semantics, an `os.close()` error does not establish that the descriptor remains open; the kernel may already have released its number. Labeling that entry **unclosed** can lead a caller or operator inspecting the newly exposed diagnostic handle to retry-close the numeric descriptor, potentially closing a different resource after descriptor reuse. The test currently asserts only that `unclosed_descriptors` is non-empty (`tests/services/test_authoritative_approval_store.py:708-715`) and does not pin the close outcome as unknown.

**Required change:** represent the outcome explicitly as `CLOSE_OUTCOME_UNKNOWN` (or equivalent), rename the collection so it does not claim the descriptor is still open, and document that numeric descriptor values must never be retried. Add an assertion for this state to the constructor cleanup fault-injection test. Keep the existing single-close behavior.

## Decision and safety boundary

Do not accept Gate 3-I2 Revision R4 until the diagnostic state is unambiguous and its test evidence is refreshed. This review is limited to the approval-store slice; it does not authorize Director signing, later Signer/Dispatcher slices, key access, IPC, Resolve, Gate 4/5, or live operations.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, keys, or network operations were executed or accessed during review.
