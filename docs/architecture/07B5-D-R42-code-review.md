# G07-07B5-D-R42 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R42. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `95eac995d33803a0eb8955379d8d65e1fe680f127ff9d1449b8268368053f27a`
- `docs/architecture/07B5-D-R41-code-review.md`: `62e489f62e9e6283101a62b1f0e52852136419f76fe6ff0ec18d9fbd212d1048`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `d58b3c98faeba7986206155c0bd2cc2d9ac38f2d120eb2bc4a8dbb3dca5a5320`
- `.bridge/report.md`: `11dd665c8286cbd88b508ff29cd2572923a801a270726a64dec3d14a659240e2`
- `scratch/test_r42_validator.py`: `f4c6df42bb1638801e4cddbc6c6835e2b8a5eefcc832415b4a94c8579bbc939c`

## Finding

### R42-01 — `acquire_target_lock()` can overwrite a timeline's ambiguous-close state — BLOCKER

The release path now correctly makes an ambiguous `os.close()` outcome terminal for that registry entry: it sets `entry.fd = None`, records `CLOSE_OUTCOME_UNKNOWN`, and refuses subsequent release attempts. But `acquire_target_lock()` does not check either `_lock_state[timeline_uid]` or an existing `_target_locks[timeline_uid]` before opening the lock file (`around lines 2475–2502`). If the prior close error occurred after the OS released the descriptor, the lock can be acquired again. The method then unconditionally assigns a new `TargetLockEntry` and changes `_lock_state[timeline_uid]` to `LOCKED`, overwriting the unresolved entry and clearing the fail-closed condition. `execute_restart_repair()` reaches this path through `acquire_target_lock()`.

This contradicts the R42 requirement that all later operations on the affected timeline remain blocked after `CLOSE_OUTCOME_UNKNOWN`; the permanent refusal currently covers release, but not fresh lock admission.

**Required correction:** Under `_coordinator_lock`, before any `open()` or `flock()` call, reject admission if the timeline has an entry/state of `CLOSE_OUTCOME_UNKNOWN` or another unresolved lock state. Never replace an unresolved registry entry. Keep the block in force until an explicit, safe reconciliation policy can clear it; the ambiguous old FD must never be touched again. Add a same-Coordinator case where close actually frees the old FD, a new acquire would otherwise succeed, and assert the acquire is rejected without opening/replacing the registry entry.

## R41 finding reviewed

R42 resolves the same-inode descriptor reuse hazard on the release retry path by relinquishing `entry.fd` after the first close error and making that state non-retryable. The dual-Coordinator release case is addressed by this design. The remaining gap is admission through `acquire_target_lock()`, which can discard the terminal state.

## Decision and safety boundary

R42 is **not accepted** until R42-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
