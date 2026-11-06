# G07-AUTH-SIGNER-GATE3-I6 Revision R2 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Durable Signer Nonce Ledger Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `675ea1829ce141e0e3b2c75ce085ca2963754e1e8323584f9ddc0ac770a59731`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `5a82d217f469df422d39eebf0d6c63977abc9667bcb2458dcbde7d51f014e60f`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `44472c1254f8e580d596054ae5ed5ef0a47a4f44afb376ab599ba1b1586e7546`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I6/pytest.log` — SHA-256 `93fe6d9c06333c36a3388501b61cc3f8beee241e15ad46ece91c9164dee2f055`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `487a91c2d32ac63709edd9d1c348aa3e19a2bd8558302f96bd13a4e216378965`.

Submitted hashes match the workspace. The submitted log records 155 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R1 finding disposition

- `I6-R1-01` resolved: filename is checked as an exact safe basename before filesystem access.
- `I6-R1-02` resolved: pre-existing directory permissions are not repaired, and the opened directory must have exact mode `0700`.
- `I6-R1-03` resolved: creation uses descriptor-relative `O_CREAT | O_EXCL`; `EEXIST` reopens with `O_NOFOLLOW` and validates the descriptor.
- `I6-R1-04` resolved: `consume_once()` and `contains()` require exact `ConsumedAuthorizationNonceRecord` type.
- `I6-R1-05` resolved on `consume_once()` and `contains()`'s operation path insofar as both errors appear in the aggregate message and the primary error is retained as `__cause__`; see residual findings below regarding constructor cleanup and structured preservation.

## Findings

### I6-R2-01 — Initial scan unlock failure still masks the primary initialization error — HIGH

During constructor startup scan, `_sync_and_scan_locked()` executes inside a `try/finally`; if the scan raises (for example, `ERR_SIGNER_LEDGER_TORN_TAIL_DETECTED`) and `flock(LOCK_UN)` also raises, the `finally` raises `ERR_LEDGER_UNLOCK_FAILED` (`src/services/trial_authorization_signer_service.py:1104-1118`). The outer initialization handler therefore receives the unlock exception as `init_exc`; the original parse/corruption failure is lost. This is the same dual-failure case that R2 fixes for `consume_once()` and `contains()`, but the constructor path remains uncovered.

**Required correction:** Preserve the scan exception as the primary cause and attach the unlock failure as a separately inspectable cleanup error while latching the ledger unusable. Add a constructor fault-injection test combining a corrupt/torn ledger with unlock failure and assert both failures remain observable.

### I6-R2-02 — Combined operation and unlock failures do not retain structured cleanup error — MEDIUM

In `consume_once()` and `contains()`, the combined-error branch builds a new `SignerLedgerDurabilityError` from formatted text and chains only `primary_exc` (`:1304-1325`, `:1354-1375`). The `unlock_err` object and its code are not stored on the raised exception; callers cannot reliably inspect the cleanup failure without parsing message text. The workorder requires structured cleanup failures to be exposed while preserving the primary error.

**Required correction:** Preserve both exception objects in stable attributes or a documented aggregate (for example `primary_error` and `cleanup_errors`), retaining the primary error as cause and the unlock error as a structured cleanup member. Extend the combined-failure tests to assert object/type/code access, not only substrings in `str(exc)`.

## Positive review notes

- `I6-R1-01` through `I6-R1-04` are implemented at the relevant boundaries with negative tests.
- Ledger create-or-open now uses exclusive descriptor-relative creation; the directory is fsynced only when this instance creates the ledger.
- Append ordering remains lock → tail scan → collision checks → canonical line/write-all → file fsync → in-memory index update → unlock.
- The submitted tests cover the reported R1 cases, including unsafe basenames, no permission repair, exact `0700`, record subclasses, and combined collision/unlock failure. They do not cover constructor scan/unlock dual failure or structured access to both error objects.

## Disposition and safety boundary

Do not mark I6 R2 accepted until `I6-R2-01` and `I6-R2-02` are corrected and reviewed within the existing two-file allowlist. Acceptance remains limited to the local temporary-directory ledger primitive; it does not authorize production wiring or runtime issuance.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
