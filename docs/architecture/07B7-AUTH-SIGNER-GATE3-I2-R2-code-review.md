# Static Code Review — G07-AUTH-SIGNER-GATE3-I2 Revision R2

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the R2 workorder, completion report, approval-store implementation, allowlisted tests, submitted pytest log, and bridge status. Submitted SHA-256 values match the current workspace for the task, report, both allowlisted files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I2/pytest.log`.

The submitted log records **77 passed, 0 failed in 0.46s, exit code 0**. This review inspected the recorded evidence and did not rerun tests.

The four findings from the previous review are substantially addressed: temporary cleanup failures are surfaced, lock-file type/link-count/owner/mode are validated, the reader validates and bounds the opened descriptor, and public operations check open state while holding the lifecycle mutex. Two remaining lifecycle/locking issues prevent acceptance.

## Findings

### I2-R2-01 — Failed OS lock release is silently treated as completion (HIGH)

In `src/services/authoritative_approval_store.py:316-320` and `:455-460`, `fcntl.flock(..., LOCK_UN)` errors are swallowed. Consequently, `put()` or `get()` can return normally while the process-wide lock may remain held on the lock file. The in-process `RLock` is then released, allowing same-process callers to continue while other processes remain blocked; callers are not told that lock state is uncertain.

**Required change:** surface unlock failures as a structured store error, preserve the lock descriptor/ownership state, and latch the store against further operations until the lock state is safely resolved. Do not report the operation as successful when its required unlock failed. Add fault-injection coverage for both read and write paths and verify the store remains fail-closed.

### I2-R2-02 — Retrying `close(fd)` after an error can close a reused descriptor (BLOCKER)

The implementation intentionally retains and retries a numeric descriptor after `os.close()` raises. This occurs in `AuthoritativeApprovalStore.close()` (`:471-485`), and also in temporary/record cleanup: `put()` can retry a failed temp close in its exception/finally paths (`:253-258`, `:285-315`), while `get()` retries a failed record close in its `finally` (`:413-425`). A close error has an ambiguous outcome on POSIX: the kernel may already have released the descriptor number before returning the error. Retrying that number can close a different file descriptor opened in the meantime. The current close-failure test models an error that leaves the descriptor open, so it does not cover this kernel behavior.

**Required change:** adopt single-close semantics. Once `os.close(fd)` has been attempted and reports an error, never blindly close the same integer again. Record an explicit ambiguous/unknown close state, block operations that depend on the resource, and expose the unresolved cleanup state without claiming the descriptor is still safely owned. Update tests so they do not assert blind close retry; cover a simulated descriptor-number reuse and ensure an unrelated descriptor is never closed.

## Decision and safety boundary

Do not accept Gate 3-I2 Revision R2 until these findings are resolved and the focused plus adjacent evidence is refreshed. This review applies only to the approval-store slice and grants no authority for later signer, authorization, IPC, Resolve, Gate 4/5, or live-operation work.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, keys, or network operations were executed or accessed during this review.
