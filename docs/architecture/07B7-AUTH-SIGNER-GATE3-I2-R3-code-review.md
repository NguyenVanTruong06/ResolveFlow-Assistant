# Static Code Review — G07-AUTH-SIGNER-GATE3-I2 Revision R3

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the active workorder, completion report, implementation, tests, submitted focused/adjacent pytest log, and bridge handoff. Submitted SHA-256 values match the current workspace for the workorder, report, both allowlisted files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I2/pytest.log`.

The submitted log records **80 passed, 0 failed in 0.46s, exit code 0**. This review inspected the recorded evidence and did not rerun tests.

The two R2 findings are addressed in the principal paths: failed `flock(LOCK_UN)` is surfaced and latches the store, and close attempts disown the numeric descriptor before calling `os.close()`, avoiding blind retries. The following cleanup paths still hide or lose failure context.

## Findings

### I2-R3-01 — Reader cleanup close failures are swallowed when another read-path error occurs (HIGH)

In `src/services/authoritative_approval_store.py:463-470`, the fallback `finally` path disowns `ffd` and calls `os.close()`, but suppresses any `OSError`. This path runs when opening/reading/fstat/schema preparation has already raised before reaching the explicit close at lines 455-461. As a result, a primary corruption or I/O error can conceal a second ambiguous close failure, despite the workorder requirement to surface cleanup/durability errors and preserve both failure contexts.

**Required change:** capture the fallback close error after disowning the descriptor, preserve it together with the primary exception, and latch the store or otherwise report the unresolved close state. Add fault-injection coverage combining a read/fstat failure with a close failure; verify one close attempt only and both errors are observable.

### I2-R3-02 — Constructor cleanup can mask initialization failure and lose the cleanup handle (HIGH)

In `src/services/authoritative_approval_store.py:159-160`, the constructor catches an initialization/validation exception and calls `self.close()` before re-raising. If `close()` raises while cleaning either descriptor, that cleanup exception replaces the original initialization failure. Since construction then fails and the caller receives no store instance, any ambiguous descriptor cleanup state has no reachable owner/diagnostic handle.

**Required change:** retain the original initialization exception as primary, attach structured cleanup failures and descriptor close outcomes, and expose a reachable cleanup/diagnostic handle when cleanup is incomplete. Add a test where lock-file validation fails and descriptor close also fails; assert both failures remain inspectable and no unsafe close retry occurs.

## Decision and safety boundary

Do not accept Gate 3-I2 Revision R3 until these cleanup cases are addressed and focused plus adjacent evidence is refreshed. This decision applies only to the approval-store slice; it does not authorize later signer/authorization slices, key access, IPC, Resolve, Gate 4/5, or live operations.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, keys, or network operations were executed or accessed during this review.
