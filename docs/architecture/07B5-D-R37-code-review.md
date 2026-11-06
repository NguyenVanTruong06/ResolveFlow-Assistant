# G07-07B5-D-R37 — Codex Static Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R37. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `2b9e6d2f4829bc152117de169c3ce1e7fb73bfc451f5857fe52fa52ec6e63a22`
- `docs/architecture/07B5-D-R36-code-review.md`: `6114513e68648f3907917558b08a7d9a0f3e03d473e9bde5b16ad6259c62dcf5`
- `docs/architecture/07B5-D-R35-code-review.md`: `231ede8eb56ebe4ce3fcc78cf720376da594d018d3b9c4efe32ce890f06f7b2a`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `b8fb4c09a2c00f91c0444d15db15677b5f1f2b7e9f4047d18de497108b8e1184`
- `.bridge/report.md`: `1595e162a61482e0edda372efd81bbaadee90f64fc921a7b68866c0aeb142417`
- `scratch/test_r37_validator.py`: `4a876cbb921b359600901e1bd79bdcc038c3571fa9fe2fa4cb2cfaeba703ab5b`

## Findings

### R37-01 — `close()` ignores OS-lock acquisition failure and closes descriptors anyway — BLOCKER

In `DurableConsumedReceiptLedger.close()`, lines 1848–1853 attempt `flock(LOCK_EX)` but swallow `OSError` with `pass`. The method then fsyncs and closes `_lock_fd`, `_quarantine_dir_fd`, and `_dir_fd` (lines 1855–1892). If lock acquisition failed, `close()` has not established that an operation in another process has finished. Closing `_lock_fd` can release this process’s lock ownership while a scanner/writer remains active, and closing the directory descriptors invalidates its in-flight file operations. This recreates R36-01 under the exact failure path the new lifecycle is supposed to guard.

**Required correction:** Treat failure to acquire the OS lock as a close failure: do not close shared descriptors or report the ledger closed. Preserve `_closing`/retry state in a documented fail-closed way, surface a structured error, and allow a safe retry after the lock service recovers. Add an injected `flock(LOCK_EX)` failure case proving descriptors remain open and no close-success state is published.

### R37-02 — Reentrant `close()` can close descriptors from inside an active operation — BLOCKER

The design uses `threading.RLock()` specifically to permit reentrant close (lines 1795–1798 and 1841–1852). If the same thread calls `close()` while inside `record_consumed_evidence()` or `recover_and_scan_ledger()`, the RLock is acquired recursively. `flock(LOCK_EX)` on the same open file description already held by that operation does not wait for the operation to unwind. `close()` can therefore set `_closing`, fsync and close the descriptors while the outer operation is still executing; its later `finally` may then attempt `flock(LOCK_UN)` on a closed descriptor. The RLock prevents a self-deadlock but does not make this reentrant close safe.

**Required correction:** Do not permit descriptor teardown while the current thread owns an active ledger operation. Track active-operation depth/ownership and either reject same-thread close with a controlled error or defer teardown until the outermost operation exits. Preserve cross-thread close waiting. Replace the current “reentrant close” test expectation with a test that proves no descriptor closes before the operation’s `finally` completes.

### R37-03 — `load_record()` leaves its protected section before reading and verifying the record — HIGH

The `with self._thread_lock` block in `load_record()` covers only the closed-state check and `os.open()` (lines 2177–2182). The file read, JSON/schema validation, HMAC verification, and DTO construction occur after the mutex is released (lines 2186 onward). The method also does not take the inter-process `flock`. A concurrent same-instance close can therefore close the ledger while `load_record()` is still running, and another process’s scanner/writer is not serialized against the read. In addition, the shown `except FileNotFoundError` at line 2183 is indented outside the `try` at line 2181, so the displayed Python code block is syntactically invalid as written.

**Required correction:** Keep the complete descriptor read and validation within the intended lifecycle/concurrency guard, using the same lock ordering as scanner/writer where ledger consistency requires it. Correct the `except` indentation and include a boundary case for load-versus-close and load-versus-scanner. If reads are intentionally lock-free, define a safe open-descriptor snapshot contract and make close wait for in-flight readers before returning.

### R37-04 — `RecoveryCoordinator.close()` remains unsynchronized with coordinator operations — HIGH

The constructor now stages its descriptors and catches initialization errors (lines 2324–2366), but the public `RecoveryCoordinator.close()` at lines 2493–2509 still releases target locks and closes `_journals_dir_fd`, `_receipts_dir_fd`, and `_locks_dir_fd` without a coordinator closed/closing gate or operation lock. Coordinator methods use these descriptors during WAL and receipt transactions. A concurrent close can invalidate those descriptors in the middle of an operation even if the nested ledger’s own `close()` waits correctly.

**Required correction:** Give the coordinator an operation-lifecycle gate and serialize close against all operations that use its descriptors and target-lock registry. Close the durable ledger and coordinator descriptors only after active operations finish; reject new calls after closing begins. Do not ignore close errors or claim successful closure while resources may remain active.

### R37-05 — Coordinator constructor cleanup discards cleanup failures and clears descriptor ownership — HIGH

In `RecoveryCoordinator.__init__()`’s exception handler (lines 2347–2366), failures from `os.close(fd)` and `_durable_ledger.close()` are swallowed, and descriptor attributes are then set to `None`. The original initialization error is re-raised without disclosing that cleanup may have failed. Consequently, the claimed “zero descriptors leaked” result is not established when a cleanup syscall itself fails, and ownership information is discarded.

**Required correction:** Collect cleanup failures, retain enough ownership data for safe retry/diagnosis, and raise a structured initialization error containing both the original cause and cleanup failures. Do not mark the coordinator fully closed or clear an FD attribute when closing that descriptor failed.

## R36 findings reviewed

The design now initializes ledger descriptors to `None`, has an exception cleanup boundary for ledger construction, adds the `_closed`/`_closing` gates, and uses a mutex plus OS lock for writer/scanner entrypoints. These are useful changes, but the failure and reentrant paths above prevent acceptance of the close-lifecycle claim. The constructor cleanup claim for `RecoveryCoordinator` also remains incomplete because its cleanup errors are suppressed.

## Decision and safety boundary

R37 is **not accepted** until R37-01 through R37-05 are resolved. Keep `ready_to_execute=False`; keep G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported 5/5 prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
