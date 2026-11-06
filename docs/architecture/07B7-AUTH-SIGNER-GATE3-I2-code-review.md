# Static Code Review — G07-AUTH-SIGNER-GATE3-I2

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the active workorder, completion report, `AuthoritativeApprovalStore`, its focused tests, submitted pytest log, and bridge handoff. Submitted hashes match the current workspace for `.bridge/task.md`, `.bridge/report.md`, both allowlisted implementation/test files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I2/pytest.log`.

The submitted log records **70 passed, 0 failed in 0.50s, exit code 0** for the focused store and adjacent contract suites. This is recorded evidence; tests were not rerun during this static review.

The implementation follows several key requirements: strict DTO type admission, canonical complete-envelope bytes, digest-derived filenames, descriptor-relative record publication, no-replace hard-link publication, strict JSON duplicate-key parsing, and same-ID concurrency coverage. The findings below prevent acceptance.

## Findings

### I2-01 — Temporary-file cleanup failures are silently discarded (HIGH)

In `src/services/authoritative_approval_store.py:245-252` and `:260-269`, failures from `os.unlink()` are swallowed. The same applies to `os.close(tmp_fd)` in the outer cleanup block. If publication succeeds but unlinking the temporary name fails, `put()` proceeds to directory fsync and returns success while leaving an orphan temporary file. If publication fails and cleanup also fails, the cleanup failure is lost. This contradicts the workorder requirement to surface cleanup/durability errors and creates an unreported partial state.

**Required change:** track whether the final record was published; preserve the primary exception and report cleanup failures structurally; never return success when temp unlink failed. Do not unlink a temp name after publication in a way that can obscure an uncertain directory durability outcome.

### I2-02 — Lock-file type and permissions are not fail-closed validated (HIGH)

In `src/services/authoritative_approval_store.py:125-137`, the lock descriptor is checked for owner and group/other permission bits, but not `stat.S_ISREG()` or a single-link invariant. If permissions are too broad, the code silently changes them with `os.fchmod()` instead of rejecting the unexpected pre-existing lock object. A non-regular or hard-linked object can therefore become the synchronization primitive without the required identity checks.

**Required change:** validate the opened lock descriptor with `fstat` (regular file, expected owner, restrictive exact mode, and no unexpected hard links) and fail closed on mismatch. Do not silently repair an existing object as part of opening the store.

### I2-03 — Record metadata checks are detached from the opened reader descriptor (HIGH)

In `src/services/authoritative_approval_store.py:284-341`, `get()` validates type, owner, mode, and size with `os.stat(..., follow_symlinks=False)` and then separately opens the basename. It never validates the resulting `ffd` with `fstat`, nor checks inode/device/size stability around the read. The path can change between the stat and open/read operations, so the properties checked are not proven to belong to the bytes parsed. The read loop also requests a fixed 4096 bytes through the cap boundary rather than limiting reads to `remaining + 1`.

**Required change:** perform descriptor-based type/owner/mode/size checks after open, bind the opened descriptor to the pre-open identity if retaining the pre-stat, and verify device/inode/size stability through EOF. Bound each read by the remaining allowance plus one byte. Treat descriptor close failures as structured failures rather than ignoring them.

### I2-04 — Close can race with public operations and discards uncertain descriptor ownership (HIGH)

`put()` and `get()` call `_ensure_open()` before acquiring `self._lock` (`:167` and `:278`), while `close()` acquires that lock and clears descriptors (`:380-403`). A caller can pass `_ensure_open()`, lose the race to `close()`, and then enter the critical section with `_lock_fd` and `_dir_fd` already cleared. In addition, `close()` marks `_closed = True` before cleanup and sets descriptor attributes to `None` even when `os.close()` raises, preventing safe retry or diagnosis of an ambiguous close outcome.

**Required change:** perform the closed-state gate inside the same lifecycle lock as the complete operation. Define exception-safe close states; on close failure retain accurate descriptor ownership/unknown-outcome state and block new operations without falsely claiming all descriptors are closed.

## Decision and safety boundary

Do not accept Gate 3-I2 until these findings are addressed and the focused plus adjacent test evidence is refreshed. This decision concerns only the approval-store slice; it does not authorize Director signing, Signer/Dispatcher services, keys, IPC, Gate 4/5, or live operations.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, keys, or network operations were executed or accessed during review.
