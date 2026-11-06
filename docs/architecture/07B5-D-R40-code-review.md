# G07-07B5-D-R40 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R40. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `5a838e58dc39a315ca78a328b6d771f9f70360210e1c6396a74fbd73dc193b2f`
- `docs/architecture/07B5-D-R39-code-review.md`: `f716a757ddde6391095ad91c475f3b2f92a00c60100cc8f029fa1dd9232a873d`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `f3a1c724c55214168707041c3e8837bb1ce7e166cbbf48a2b9b43fc57f1003e6`
- `.bridge/report.md`: `115a942bdf1d3a503697dbde13e9dcd222ed4766171c8982d554bfa15ba72eff`
- `scratch/test_r40_validator.py`: `1462dcb9e75ba9186e4c3b8d1b5e6184d740f402dd2b818b5f620aae1861faf7`

## Finding

### R40-01 — Retrying `close(fd)` can act on a reused descriptor — HIGH

The design correctly preserves the target-lock descriptor when `flock(LOCK_UN)` fails. It also distinguishes `IN_DOUBT_CLOSE_FAILED` after unlock succeeds but `os.close(fd)` raises. However, the retry path for `IN_DOUBT_CLOSE_FAILED` calls `os.close(fd)` again using the retained integer (`07B5-isolated-resolve-acceptance-gate-design.md`, around lines 2418–2424). A failed `close(2)` does not universally prove that the descriptor remains open; on Linux, the descriptor may already have been released before a delayed close error is reported. If another operation reuses that descriptor number before retry, the retry can close an unrelated file or resource. The stored integer alone is not sufficient proof of continuing descriptor ownership.

**Required correction:** Define a safe close-error protocol that never blindly retries `os.close()` on an ambiguous descriptor number. Preserve and compare a stable identity captured before close (for example `st_dev`/`st_ino` plus the expected lock-file identity) before any retry action; treat `EBADF` or identity mismatch as no longer owned and do not close that number. If ownership cannot be established, retain an explicit `CLOSE_OUTCOME_UNKNOWN` state and require a safe re-open/reconciliation path rather than claiming retry is safe. Add a case where the first close reports an error after the FD has been closed and its number reused; prove the retry leaves the unrelated descriptor open.

## R39 findings reviewed

R40 addresses the two R39 findings in the documented paths: unlock failure now returns before `os.close(fd)` and retains the live lock descriptor; constructor cleanup errors carry the ledger object and retry it through `ledger.close()` before draining the remaining directory descriptors. The residual above is limited to the close-error retry path.

## Decision and safety boundary

R40 is **not accepted** until R40-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
