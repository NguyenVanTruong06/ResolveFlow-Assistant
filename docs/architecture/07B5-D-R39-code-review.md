# G07-07B5-D-R39 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R39. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `c15710033e5afcfc656ddbd10ec6516bdbeda26839c5aa2024ba9119cd31c793`
- `docs/architecture/07B5-D-R38-code-review.md`: `39af90bb3dfd8bc339094df9b30267392512f3c1f69a78b68685ad884235bc8e`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `2e3c36e7278241dddf5309251c297b39c6428d47f6337586f4385b71bc4378b7`
- `.bridge/report.md`: `34641cb191d99f3eb67637f05c75b51f9da9d51301cb97e6c31b5450236e7fd0`
- `scratch/test_r39_validator.py`: `59d718a2bc552a86fe1f01cd81ab55ab897b23af4df04a311c75966d3a0a627d`

## Findings

### R39-01 — Target-lock ownership is lost when unlock fails — BLOCKER

`_release_target_lock_internal()` records an `flock(LOCK_UN)` error, but then unconditionally calls `os.close(fd)` before deciding whether to retain the entry (`07B5-isolated-resolve-acceptance-gate-design.md`, around lines 2367–2387). If unlock fails and close succeeds, closing the final descriptor releases the OS lock. The method then raises `TargetLockReleaseError` and retains the now-closed integer FD in `_target_locks`, labeling the target `IN_DOUBT_LOCKED` even though exclusion is no longer held. This violates the required fail-closed ownership guarantee and permits another process to acquire the target lock.

**Required correction:** Do not close the target-lock FD after an unlock failure. Preserve the live descriptor and its lock ownership in `_target_locks`, mark it `IN_DOUBT_LOCKED`, and surface the release error. If the OS reports an ambiguous close outcome, represent that separately and do not claim the lock is retained; define a recovery path that reopens and reacquires/verifies exclusion before any state can be cleared. Add distinct unlock-failure and close-failure cases, including proof that a competing process cannot acquire the lock after an unlock failure.

### R39-02 — Constructor cleanup handle omits failed durable-ledger cleanup — HIGH

When staged `RecoveryCoordinator.__init__()` cleanup calls `self._durable_ledger.close()` and that call fails, the exception is appended to `cleanup_errors`, but `_durable_ledger` is not added to `unclosed_descriptors` and `CoordinatorInitCleanupError.retry_cleanup()` only calls `os.close()` on descriptor integers in that mapping (`around lines 2322–2348 and 1368–1385`). Since construction raises, the caller has no coordinator reference to reach the ledger object or retry its synchronized close procedure. A ledger close failure may also leave multiple descriptors and lifecycle/lock state that cannot safely be recovered by blindly closing integer FDs.

**Required correction:** Make ownership of the ledger cleanup failure reachable from the raised exception. Carry a stable cleanup-owner/callback for the ledger (with retry and diagnostic state) alongside coordinator directory FDs, or use a construction factory that retains and drains all resources before raising. Do not reduce the ledger to a list of FD integers: retry must preserve its lock/lifecycle semantics and report any descriptor whose close outcome remains uncertain. Add a constructor-failure case where ledger close fails and demonstrate that the caller can retry cleanup through the exception handle.

## R38 findings reviewed

The R39 design adds lifecycle gates to the listed coordinator operations, distinguishes pre-teardown lock acquisition from teardown failure, and introduces a reachable exception for some constructor cleanup failures. Those changes address the intent of R38-01 and R38-02. R38-03 remains incomplete because the unlock-error path closes the lock descriptor, and R38-04 remains incomplete for failures owned by `_durable_ledger`.

## Decision and safety boundary

R39 is **not accepted** until R39-01 and R39-02 are resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
