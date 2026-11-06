# G07-07B5-D-R38 — Codex Static Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R38. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `caa14b6ed4c7fa8822aa16a1c0f24f62ac729b8e44d36806dee684eaf2c861d6`
- `docs/architecture/07B5-D-R37-code-review.md`: `257cf943be55e345fdf49098bfb8165e0dcf6a9199c159c4897a7b06b2a9ba3b`
- `docs/architecture/07B5-D-R36-code-review.md`: `6114513e68648f3907917558b08a7d9a0f3e03d473e9bde5b16ad6259c62dcf5`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `2ee7cac2585d8e96c5da3ed377201c27d6c41f2a8e823c75ca0a0cd300b69d58`
- `.bridge/report.md`: `49a92595da2e8f0fff3504fe789b10342b2450fa790a6a9e8d286a0c53bf9b37`
- `scratch/test_r38_validator.py`: `20febba3b3f971aea39b5b054f82669b5d592f0dd3da9dd0f91a0a3d12a795cb`

## Findings

### R38-01 — Coordinator lifecycle gate does not cover all public operations — BLOCKER

The new coordinator gate and active-operation tracking are used by `verify_and_consume_proof()` (around lines 2437–2443). The shown implementations of `bind_signed_authorization()` (lines 2602–2641), `acquire_target_lock()` (2644–2660), `release_target_lock()` (2662–2676), and `execute_restart_repair()` (2885 onward) do not call `_check_not_closed()`, acquire `_coordinator_lock`, or increment `_active_operation_threads`. Thus `RecoveryCoordinator.close()` can acquire its RLock, observe no active operation, and close coordinator descriptors while one of these methods is running. These methods also remain callable after successful close. In particular, `acquire_target_lock()` passes `_locks_dir_fd` to `os.open`; when it is `None`, the call is no longer anchored to the locks directory and can resolve a relative filename from the process working directory. WAL operations with `_journals_dir_fd is None` have the same loss of directory anchoring.

**Required correction:** Apply one coordinator operation guard to every public operation named by the workorder, including `bind_signed_authorization`, target-lock acquire/release, and `execute_restart_repair`. Ensure close waits for all such operations and prevents new ones. Use a private close-only target-lock release path or an explicit closing-owner capability so ordinary APIs remain gated while close safely drains owned locks. Add post-close and close-versus-each-operation cases; assert no path operation falls back to `dir_fd=None`.

### R38-02 — Cleanup failure reopens the API after descriptors may already be closed — BLOCKER

The ledger close sets `_closed = True` only when `cleanup_errors` is empty, but always resets `_closing = False` (lines 1905–1912). It records directory `fsync` failures and continues closing descriptors. Therefore a close that fails after partial teardown leaves `_closed=False`, `_closing=False`, and potentially `_dir_fd`, `_quarantine_dir_fd`, or `_lock_fd` set to `None`; public methods then pass `_check_not_closed()` and attempt operations on missing descriptors. `RecoveryCoordinator.close()` has the same pattern: it closes some descriptors, then on errors resets `_closing=False` while `_closed` remains false (lines 2574–2599). The retry state is not safe for normal operations in between attempts.

**Required correction:** Distinguish a pre-teardown lock-acquisition failure (where resetting `_closing=False` is safe because all descriptors remain intact) from a partial cleanup failure. After partial teardown, keep the public gate closed permanently or in a dedicated `CLOSE_FAILED` state that rejects every operational API; permit only a close-cleanup retry/diagnostic path. Do not publish `_closed=True` unless all required cleanup succeeds, and do not reopen the operation gate with missing descriptors. Add injected fsync and close failures at each descriptor position.

### R38-03 — Target-lock release still suppresses errors and discards lock ownership — HIGH

`RecoveryCoordinator.close()` relies on `release_target_lock()` and collects exceptions from it (lines 2567–2572). But `release_target_lock()` suppresses both `flock(LOCK_UN)` and `os.close(fd)` errors, then unconditionally deletes `_target_locks[timeline_uid]` and sets the state to `RELEASED` (lines 2665–2676). The caller therefore cannot collect the failure and may report coordinator close success while target-lock release is uncertain. This also contradicts the earlier target-lock invariant that release persistence failure retains the descriptor and `IN_DOUBT_LOCKED` ownership.

**Required correction:** Propagate structured unlock/close failures. Preserve the target descriptor and registry entry in an in-doubt/closing state unless release is confirmed. Have coordinator close retain its own close gate and report the target UID and unresolved ownership rather than marking the coordinator closed.

### R38-04 — Constructor cleanup failures leave descriptors owned only by an object whose construction fails — HIGH

`RecoveryCoordinator.__init__()` now preserves FD attributes when `os.close()` fails and raises `ERR_COORDINATOR_INIT_FAILED_WITH_CLEANUP_ERRORS` (lines 2390–2416). However, because `__init__()` raises, callers receive only the exception, not a usable coordinator reference through which to inspect or retry those retained descriptors. The integer FD values remain open in the process, but the design provides no explicit owner or cleanup handle for them, so the claimed diagnostic/retry ownership is not operationally reachable.

**Required correction:** Make failed initialization cleanup ownership reachable: for example, have a dedicated cleanup error carry a stable cleanup-owner object/handle with retry and diagnostics, or ensure the construction factory owns and retries cleanup before returning the exception. Include the unreleased descriptor identifiers and do not claim leak-free cleanup when close failed. Add a test proving the retained descriptors can actually be enumerated and retried after constructor failure.

## R37 findings reviewed

The ledger now rejects same-thread reentrant close, fails closed when acquiring its OS lock fails, and wraps `load_record()` in the thread mutex plus a shared `flock`. Its constructor and the coordinator constructor collect cleanup errors. Those R37 changes are present, but R38’s coordinator gate coverage and partial-close states still allow operations to race teardown or proceed with unanchored/missing descriptors.

## Decision and safety boundary

R38 is **not accepted** until R38-01 through R38-04 are resolved. Keep `ready_to_execute=False`; keep G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported 6/6 prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
