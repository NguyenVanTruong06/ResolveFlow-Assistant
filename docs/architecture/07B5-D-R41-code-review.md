# G07-07B5-D-R41 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R41. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `bb01c347c72981584634fd201131ebab8e858da59e9ddc3c46aaf8776d8ae62e`
- `docs/architecture/07B5-D-R40-code-review.md`: `11ef5e332fbf7c0266925ab5c1d4b05dd68037f1ff412c9744f9285a702648c5`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `ea1bd1fd21d73b9ee8566123779a42a82c2b432a61bcdd9979915a103ea0be19`
- `.bridge/report.md`: `a9b63de66756e792910265cb74faefc6aa3e5e65f3a85091aa279e7dd2a64b43`
- `scratch/test_r41_validator.py`: `40652e25abef49bab6c0c2bcf60066015da78a3bd161652b777ffaee2194f1f8`

## Finding

### R41-01 — Inode equality does not prove the FD is the same lock open-file description — BLOCKER

The retry protocol for `IN_DOUBT_CLOSE_FAILED` checks `fstat(fd)` and permits `os.close(fd)` when `(st_dev, st_ino)` still matches the values captured at acquisition (around lines 2426–2455). That detects reuse for a different inode, but it does not detect reuse for another open of the same lock file. A valid same-process sequence is:

1. Coordinator A successfully unlocks its target lock; its first `close(fd=N)` reports an error after the kernel has released descriptor slot N.
2. Coordinator B opens the same target lock file, acquires `flock(LOCK_EX)`, and receives the now-free descriptor number N.
3. Coordinator A retries. `fstat(N)` reports the same device and inode as A's saved fingerprint because B opened the same lock file.
4. A closes N, inadvertently closing B's descriptor and releasing B's target lock.

The inode check therefore does not establish ownership of the open descriptor being closed. This can invalidate another coordinator's active exclusion lock, despite the retry path reporting a verified match.

**Required correction:** Do not retry `close()` on an integer descriptor after an ambiguous close outcome based only on inode identity. Use a protocol that can prove ownership of the same open-file description and serializes all same-process opens/closes relevant to that lock; otherwise transition to a permanently non-retryable `CLOSE_OUTCOME_UNKNOWN` state and never close that descriptor number again. Add a two-coordinator same-process scenario for the sequence above and prove that B's lock remains held after A's retry/cleanup path.

## R40 finding reviewed

R41 correctly avoids closing the descriptor when `flock(LOCK_UN)` itself fails, and it adds a useful different-inode reuse check. The constructor cleanup handle now retains the ledger object and retries cleanup through `ledger.close()`. R40-01 remains unresolved for same-inode descriptor reuse after an ambiguous close error.

## Decision and safety boundary

R41 is **not accepted** until R41-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
