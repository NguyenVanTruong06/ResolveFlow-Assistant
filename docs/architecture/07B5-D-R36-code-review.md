# G07-07B5-D-R36 — Codex Static Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.3 and Section 2.6 for R36. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the handoff:

- `.bridge/task.md`: `f8211549e862d3f46cbda4e5234ba866aa53f6166695d314e54fc31bdce139e4`
- `docs/architecture/07B5-D-R35-code-review.md`: `231ede8eb56ebe4ce3fcc78cf720376da594d018d3b9c4efe32ce890f06f7b2a`
- `docs/architecture/07B5-D-R34-code-review.md`: `22daa9d0717010a26fde65930277b8b950badbde6eef7e3d30ee356c8d4678ee`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `b48f66de51fd73b7d5e3cf136fe9d137b288dcfe0a52c53227c233f3096ae18b`
- `.bridge/report.md`: `2410f64fcb1a0722c36a1f5f7e83adb591c7c3bc297061b44a30b36336469166`
- `scratch/test_r36_validator.py`: `8393a5015e2ac2771af902c22b2ca6f9e23d0654b0fdb2dfaf681af8c920ae6b`

## Findings

### R36-01 — `close()` can release the shared lock and invalidate descriptors during an active ledger operation — BLOCKER

The two-tier protocol now correctly serializes scanner and writer entrypoints: both acquire `_thread_lock` before `flock`, and unwind in reverse order (writer around lines 1845–1849 and 1966–1969; scanner around lines 1971–1976 and 2086–2090). However, `DurableConsumedReceiptLedger.close()` at lines 1807–1825 closes `_lock_fd`, `_quarantine_dir_fd`, and `_dir_fd` without acquiring that same mutex or OS lock, and there is no closed-state gate preventing new operations.

If shutdown calls `close()` while a writer or scanner is in its critical section, closing the lock descriptor can release the process's flock while the operation is still mutating files; closing directory descriptors can also make later operations fail partway through their durability sequence. Another process can then enter the scanner/writer critical section while the first operation is incomplete. This reopens the exact scanner-writer race R36 is intended to close.

**Required correction:** Define a serialized close lifecycle. Mark the ledger as closing so new calls fail closed; wait for or acquire the same in-process mutex and inter-process lock; close directory and lock descriptors only after all critical sections have exited; then mark it closed. Specify behavior for close invoked from an active operation to avoid self-deadlock. Add a bounded interleaving case for close-versus-writer and close-versus-scanner, including partial durability failure.

### R36-02 — Constructor failure after opening directory descriptors leaks those descriptors — HIGH

`__init__()` opens `_dir_fd` and `_quarantine_dir_fd` at lines 1787–1788, then opens and validates `.ledger_access.lock` at lines 1793–1805. The lock-file checks explicitly close `_lock_fd` before raising, but the directory descriptors are not closed in those failure branches. The same partial-initialization leak occurs if opening the quarantine directory or lock file fails after the ledger directory descriptor has already been opened.

This does not establish an authorization bypass, but repeated failed constructions can retain directory descriptors and complicate recovery/resource ownership. The constructor’s failure path should meet the same explicit descriptor-lifecycle discipline as the new lock protocol.

**Required correction:** Initialize descriptor attributes to `None` and wrap staged opens/validation in one exception-safe cleanup boundary that closes every successfully acquired descriptor before re-raising the controlled `ReceiptDurabilityError`. Preserve the original failure if cleanup also fails, and record/report cleanup failure without masking the fail-closed result.

## R35 findings reviewed

The four R35 findings appear addressed in the current design: the scanner and writer use a mutex plus OS flock; `action_id` is compared to `PlannedTimelineAction.action_id`; collision cleanup reports temp/reservation unlink errors; and the lock file is opened relative to the ledger directory descriptor with `O_NOFOLLOW` and validated with `fstat`. The exact proof schema/UTC verifier, source and record geometry binding, bounded descriptor reads, and 16-field DTO remain present in the reviewed sections. These are static observations, not runtime verification.

## Decision and safety boundary

R36 is **not accepted** until R36-01 and R36-02 are resolved. Keep `ready_to_execute=False`; keep G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype test result is a handoff claim only; it was not run or independently verified in this static review.
