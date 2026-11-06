# G07-07B6-I3 Code Review

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static review only. No tests, Resolve, IPC, FusionScript, network, or AI were run.  
**Scope reviewed:** `src/services/durable_trial_target_ledger.py`, `tests/services/test_durable_trial_target_ledger.py`, `.bridge/task.md`, and the accepted I3 plan/design contracts.

## Artifact identity

The submitted SHA-256 values were independently checked against the current files and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `d45aa5c7ea116a149e6c90c5b5abe6ad87b57509cc3378e96d2e0a511f8ca101` |
| `.bridge/report.md` | `4a2e6c099ff991388b30c93e313b9b9ec460a832cb5bb9bf5294f1c1b44eab5e` |
| `src/services/durable_trial_target_ledger.py` | `be7da023da72a5749b41ed3db942e0b7750d5b94a33420450537b2ac63255c88` |
| `tests/services/test_durable_trial_target_ledger.py` | `3c090a52453f145b4221790bb1f732824980c33a95eaea6eb34fedd1e18ec02e` |
| `.bridge/ux-evidence/G07-07B6-I3/pytest_focused.log` | `a2697417047d02e37b78be202c208fedd2c4cb811b2f605123a7019651e1621e` |

The report records 21 focused tests passing. That result is treated as submitted evidence; this review did not rerun the suite.

## Findings

### I3-01 — `close()` drops the kernel lock after an unlock error (BLOCKER)

`RecoveryCoordinator.close()` catches an `flock(LOCK_UN)` error, records it, and still calls `os.close(entry.fd)` (`src/services/durable_trial_target_ledger.py:1315-1325`). Closing that descriptor can release the kernel lock even though the unlock failed. This violates the I3 contract to preserve the live descriptor and exclusion on unlock failure, and can admit a second coordinator while the first target remains unresolved. The later `_close_failed` latch does not restore the lost exclusion.

**Required:** Use the same fail-closed release state machine as `release_target_lock()`: on unlock failure retain the descriptor and registry entry, do not call close, and preserve `IN_DOUBT_LOCKED`. Make `close()` retry-safe without weakening that rule.

### I3-02 — Ledger publication escapes the anchored directory descriptor (HIGH)

`write_ledger_atomic()` creates the temp file relative to `_dir_fd`, then switches to path-based `os.replace(tmp_path, final_path)` (`src/services/durable_trial_target_ledger.py:491-506`). If `ledgers_dir` is renamed or replaced after initialization, the replace can operate in a different directory from the one represented by `_dir_fd`; the subsequent `fsync(_dir_fd)` may sync the old directory instead. This breaks the claimed same-directory durable publication and descriptor anchoring.

**Required:** Perform replacement relative to the retained directory descriptor (`src_dir_fd`/`dst_dir_fd`), then fsync that same descriptor. Add a boundary test that changes the path binding after service initialization and verifies publication remains confined to the originally opened directory.

### I3-03 — Public ledger writer bypasses the irreversible transition rules (BLOCKER)

The public `write_ledger_atomic(ledger)` writes any DTO directly over the deterministic target record without acquiring `_lock_ledger()` or checking the existing state (`src/services/durable_trial_target_ledger.py:472-506`). A caller can therefore replace an `EXHAUSTED` record with a valid `RESERVED` or `ACTIVE` DTO, or race a state transition, despite the promised terminal sink and serialized read/modify/write contract.

**Required:** Make raw publication private and reachable only while the target lock is held, or make the write operation itself acquire/validate the transition under the OS lock. Reject all rewinds from `EXHAUSTED` and bind every update to the persisted identity/state.

### I3-04 — Startup scan can unlink a writer’s live temporary file (HIGH)

Construction invokes `recover_and_scan_ledger()` (`:325`), which unlinks every `.tmp_` entry and suppresses unlink errors (`:352-359`). Writers create those temp files before fsync and replace (`:480-506`) under per-target lockfiles; the scanner does not take a shared directory/scanner-writer lock. A second process starting during a write can unlink the live temp, causing publication to fail or leaving an incomplete transition. Suppressing cleanup errors also reports a successful scan without establishing the intended cleanup result.

**Required:** Serialize scanning/temporary-file cleanup with writers across processes, or use a recovery protocol that can prove a temp is orphaned before unlinking it. Propagate operational cleanup errors rather than treating them as absence.

### I3-05 — Expired authorization mutates challenge state on rejection (HIGH)

On an expired challenge, `reconcile_ambiguous_timeline()` clears `entry.active_challenge_nonce` before raising (`src/services/durable_trial_target_ledger.py:1152-1156`). The workorder requires rejected reconciliation attempts to preserve challenge state; only successful verification consumes the nonce (`.bridge/task.md:64-67`). The focused test currently asserts the mutation (`tests/services/test_durable_trial_target_ledger.py:739-752`), so it codifies behavior contrary to the contract. A rejected/stale request can invalidate the outstanding challenge and force an unnecessary re-issuance.

**Required:** Leave nonce and expiry unchanged for every rejected authorization. Consume them only after all validation succeeds; update the expiry test to assert state preservation.

### I3-06 — Target lock file permissions are not verified (HIGH)

`acquire_target_lock()` checks that the opened object is regular and owned by the current UID, but never checks the file mode (`src/services/durable_trial_target_ledger.py:904-912`). The workorder requires validation of owner, permissions, and inode identity before accepting the lock file (`.bridge/task.md:55-59`). An existing same-owner lock file with group/world access is accepted despite the required `0600` protection.

**Required:** Enforce the specified restrictive mode after `fstat()` and fail closed before taking ownership when it is too permissive. Cover the insecure-mode case.

## Acceptance boundary

The I1/I2 imports and authorization-binding path are present, and the implementation remains a mock-only I3 slice. The findings above affect terminal-state integrity, lock safety, and durable publication, so I3 is **not accepted** yet. `ready_to_execute=False` must remain unchanged; Gate G07 and M1/M2/M3 remain OPEN. This review grants no authorization for I4, UI/production wiring, or live Resolve work.
