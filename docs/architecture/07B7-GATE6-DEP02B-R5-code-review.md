# Gate 6 DEP-02B Revision R5 — Static Code Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02B — Production Signer Daemon & Oracle Socket Server`
- **Workorder SHA-256:** `718813808941250a989a7ec172d6532346661eb70410cb0f16cef6dff20d950f`
- **Review mode:** Static source review. The submitted 62-pass pytest result was not rerun. No daemon, host provisioning, systemd, production path/key, or Resolve operation was accessed.

## Reviewed artifact hashes

- `src/services/trial_authorization_signer_daemon.py` — `c44eadbc0d4215ba5047a87d0c3a4716de304ad4b7d9a5084ea720aa3364da14`
- `src/services/signer_keystore_service.py` — `98f36ce08d14a289e93f049b959e3bca7be50e91897a3b2c952e7e1913213446`
- `src/services/durable_nonce_ledger.py` — `255b9327ff1b51cde47773ef63484659a47e7454692a7b804824defd77bc299a`
- `src/core/contracts/oracle_ipc_contracts.py` — `f79940cb25aede16a64d60aff882097e2e5ab65cc8ea93bbb3f2fb0faec8eb84`
- `tests/services/test_trial_authorization_signer_daemon.py` — `6123ce8b5d4549f6e1081c6de9e86495650eb518c7287a0fdc2c7b2f245b29d4`
- `tests/services/test_signer_keystore_service.py` — `35bc49e18d947021759dbb5f03fdabe77ae47a1029e267680476a46d599b6ea9`
- `tests/integration/test_signer_daemon_socket_integration.py` — `83cc1d708d26eebfd3fc77ea80b2a421179ddfefb7a66ed2f56f9b40322f0582`
- `.bridge/ux-evidence/DEP02B/pytest.log` — `77a123e6d8c6ec86e6e3b342f4870c68f9443812cb2da80cf7674e50b3185498`
- `.bridge/report.md` as submitted — `58967b18cf5527ea62da8779853fcfe0c91fdf61d278935bfd917812bd3f64bd`

## Summary

Submitted hashes match the current artifacts. The consent path now keeps nonce persistence, HMAC signing, identity persistence, and ticket consumption within the daemon lock. The Oracle path also guards the terminal ledger write with the stopping-state check. The new race tests cover the case where `stop()` wins before the worker enters its commit section.

One commit-to-response race remains in the Oracle path: the lock is released after the durable `VERIFIED_VALID` record is written, while the positive Ed25519 response is constructed and signed afterward. Shutdown can begin in that gap, yet the handler can still emit a positive response.

## Finding

### DEP02B-R5-01 — HIGH — Positive Oracle response signing occurs outside the shutdown commit lock

**Evidence:** `_process_oracle_verification()` holds `self._lock` for the stopping check and `record_oracle_execution()` at `src/services/trial_authorization_signer_daemon.py:889-905`, then exits the lock. It calls `_build_oracle_response(... verification_result="VERIFIED_VALID", is_valid=True ...)` at `:907-915`; `_build_oracle_response()` signs the response claims with the Oracle private key at `:947-948`. Thus the terminal record is committed under the lock, but positive response signing is not.

**Impact:** Once the ledger write completes and releases the lock, `stop()` can set `_stopping=True` before the response is signed. The accepted worker then signs and sends `VERIFIED_VALID` while shutdown is underway. The submitted race test covers only the ordering where shutdown wins before commit; it does not exercise shutdown winning in the gap between terminal commit and response signing.

**Required correction:** Keep positive Oracle response construction/signing in the same shutdown-serialized critical section as the terminal ledger append, or introduce an explicit in-flight commit reservation that makes `stop()` wait until the signed response outcome is finalized before transitioning to stopping. Add a deterministic regression test that pauses immediately after the terminal ledger append, begins `stop()`, then proves no positive response is signed or returned after shutdown wins that boundary (and proves the opposite ordering completes the whole committed response before stop proceeds).

## Prior R4 finding

- DEP02B-R4-01 is substantially addressed: Oracle terminal append and consent issuance are protected by the daemon lock, and the R4 race tests cover shutdown winning before the worker enters the critical section. The remaining Oracle commit-to-response window is recorded above.

## Required disposition

Resolve DEP02B-R5-01 in a bounded revision and resubmit for review. Do not provision the host, start the daemon, access production key/state paths, run systemd, or proceed to DEP-03/DEP-04 on this submission.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests were run during this review; no daemon, production path/key, host, systemd, or Resolve operation was accessed.
