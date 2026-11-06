# G07-AUTH-SIGNER-GATE3-I6 Revision R3 — Static Code Review

**Decision: ACCEPTED**  
**Scope:** Durable Signer Nonce Ledger Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `0e7426e5d0c269efd3ca075dad57c6e9af5fac158d8351a9f21a733dec18be6c`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `0005845d7002f8e8c6a6d3ccc5468fa6a6710794770a890cf6ab9da8d8c2be67`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `de9ff1d5a27d383cbe65c6048308db0fbc7de799de875f5521fa62bb82826929`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I6/pytest.log` — SHA-256 `852cf2df47cea9503622c531080df62b0a68e614c447088890e2813e10cd9906`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `99d82a01da8c2dcd2ac55390fe0982c3514af5ec8009bc0fae59dad8c182cf8d`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I6-R2-code-review.md` — SHA-256 `8be23f3688f1092e2976f21c606579a650621f13825cd1f4f06d9227d4000be0`.

Submitted hashes match the workspace. The submitted log records 157 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R2 finding disposition

### I6-R2-01 — Constructor scan/unlock dual failure — RESOLVED

The constructor now records the startup scan exception in `primary_scan_exc`. If unlock also fails, it latches the instance closed and raises a structured `SignerLedgerDurabilityError` carrying `primary_error`, `cleanup_error`, `cleanup_errors`, and the primary exception as `__cause__` (`src/services/trial_authorization_signer_service.py:1145-1179`). The new constructor fault-injection test asserts both the torn-tail and unlock error codes remain accessible.

### I6-R2-02 — Structured preservation of combined errors — RESOLVED

`SignerLedgerError` now exposes `code`, `primary_error`, `cleanup_error`, and `cleanup_errors`. The combined-failure paths in constructor startup scan, `consume_once()`, and `contains()` retain both error objects, preserve the primary as cause, and keep the fail-closed latch. Added tests inspect error objects, types, and codes rather than relying only on message text.

## Review conclusion and scope

Both R2 findings are closed. The R1 corrections remain present: safe basename validation, no silent permission repair and exact `0700` directory validation, exclusive descriptor-relative creation, and exact record type gates. The implementation and tests remain within the two-file I6 allowlist.

`ACCEPTED` for the bounded local temporary-directory ledger primitive only. This does not authorize production keystore access, challenge or authorization issuance, daemon/RPC wiring, Gate 4–7, or live Resolve operations. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
