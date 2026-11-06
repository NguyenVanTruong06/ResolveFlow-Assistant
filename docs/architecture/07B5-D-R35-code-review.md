# G07-07B5-D-R35 — Codex Static Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of the R35 changes to Section 2.3 and Section 2.6. No files under `src/` or `tests/` were changed. No pytest, prototype validator, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `f826c1a4fa4f83d648c7d2ef0b086cee925305b051ace9da7d5dbb3cf9a5404a`
- `docs/architecture/07B5-D-R34-code-review.md`: `22daa9d0717010a26fde65930277b8b950badbde6eef7e3d30ee356c8d4678ee`
- `docs/architecture/07B5-D-R33-code-review.md`: `d370136657893155ae7ce8814a82b8df9a50072af78eb161a168306ddeadca65`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `1d2de504d7c3b1891e418930b1b9896c2a07e8be8e5c2e7fa556eb0a3efc115e`
- `.bridge/report.md`: `d4ef985e07060097fb2cb326845a74a45cacbd1b8f1fc5befce25d83e55a060e`
- `scratch/test_r35_validator.py`: `52945050c3b6b2864b9ca0c9c697594d2c215382951eb3ea5727a58623461d2d`

## Findings

### R35-01 — Shared `flock` descriptor does not serialize same-process threads — BLOCKER

Both scanner and writer call `fcntl.flock(self._lock_fd, LOCK_EX)` (writer around lines 1819–1821; scanner around lines 1931–1934), but the lock is one descriptor stored on the ledger instance (opened around lines 1777–1779). On Linux, `flock` ownership is associated with the open file description. Concurrent calls through the same open file description do not provide mutual exclusion between threads sharing that descriptor. Therefore a scanner and writer invoked concurrently in the same process can both enter their critical sections, despite the stated invariant that scanner inspection/mutation cannot overlap any writer operation. Separate ledger instances in separate processes do not address the same-instance thread case.

**Required correction:** Specify a process-local mutex shared by all users of this ledger lock descriptor, acquired before `flock` and released in a single `finally` path. Define lock ordering, reentrancy policy, and behavior when either acquisition fails. Add a same-instance scanner-versus-writer interleaving case in addition to the cross-process case.

### R35-02 — Proof `action_id` is validated syntactically but never bound to the authorized action — BLOCKER

`PlannedTimelineAction` has an `action_id` field (around line 1528). The proof normalizer checks that `proof["action_id"]` is a canonical UUIDv4 (around line 2121), but `RecoveryCoordinator.verify_and_consume_proof()` only compares action indexes and payload digests (lines 2245–2254); it never checks `norm_proof["action_id"] == action.action_id`. A proof with a valid HMAC, matching digest/index/geometry, and a different action ID therefore passes the shown action identity checks. This leaves an explicit proof identity field outside the coordinator’s action binding.

**Required correction:** Require exact proof-to-action `action_id` equality before deriving an outcome or minting `VerifiedReadbackEvidence`. If the intended meaning of proof `action_id` differs from `PlannedTimelineAction.action_id`, rename/document that field and bind it to its actual authoritative identifier. Add a negative boundary fixture for a mismatched but otherwise validly signed proof.

### R35-03 — No-replace collision path still suppresses cleanup failure and leaves recovery state implicit — HIGH

When `os.link()` reports `FileExistsError`, the code attempts to unlink the temporary file but suppresses any `OSError` (lines 1901–1909). It then raises the already-consumed error and releases the global lock in the outer `finally`, without an explicit state for the remaining temp/reservation files. The scanner’s temp-file rule preserves a temp whose filename PID is alive (lines 1938–1949); thus a failed unlink can leave an orphan indefinitely while this process remains alive. This conflicts with R34-05’s requirement to propagate cleanup failures and keep the durability state explicit.

**Required correction:** Do not swallow the unlink error. Report a structured cleanup/durability failure that identifies the still-owned temp and reservation, and specify how the same process later retries cleanup without ever touching the pre-existing destination. Define whether the reservation is retained or safely released on this collision branch, with fsync and restart-repair behavior.

### R35-04 — The global lock file itself is opened by path without no-follow or descriptor anchoring — HIGH

The ledger directory descriptor is opened at line 1774, but `.ledger_access.lock` is then opened using the joined absolute path at lines 1777–1779. The operation does not use `dir_fd=self._dir_fd`, `O_NOFOLLOW`, or a post-open `fstat` regular-file/inode check. A replaced or symlinked lock pathname can cause participants to lock different underlying files or follow an unintended file, defeating the shared serialization guarantee at the exact authority boundary that R35 adds.

**Required correction:** Create/open the lock relative to the already-validated ledger directory descriptor using `O_NOFOLLOW | O_CLOEXEC` (and `O_CREAT` as needed), require `S_ISREG`, validate owner/mode/link count, and ensure every scanner/writer instance resolves the same stable lock inode. Fail closed on replacement or mismatch.

## Resolved R34 items observed in the document

The R35 design now removes the obsolete 14-field subsection in favor of the 16-field `"1.0"` DTO (Section 2.6 around lines 1196–1219 and 1663–1710); the proof verifier checks exact schema version and canonical UTC timestamp (around lines 2103–2184); source endpoints and record geometry are cross-bound (around lines 2265–2284); and the scanner uses bounded reads with explicit operational I/O handling (around lines 1931–2054). The no-replace hard-link publication and `_write_all` usage are also present. These are static observations, not execution results.

## Decision and safety boundary

R35 is **not accepted** until R35-01 through R35-04 are resolved and the bounded negative/interleaving cases are recorded. Keep `ready_to_execute=False`; keep G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported 6/6 prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
