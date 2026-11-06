# Gate 6 DEP-02B Revision R4 — Static Code Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02B — Production Signer Daemon & Oracle Socket Server`
- **Workorder SHA-256:** `718813808941250a989a7ec172d6532346661eb70410cb0f16cef6dff20d950f`
- **Review mode:** Static source review. The submitted 60-pass pytest result was not rerun. No daemon, host provisioning, systemd, production path/key, or Resolve operation was accessed.

## Reviewed artifact hashes

- `src/services/trial_authorization_signer_daemon.py` — `76fe14b1c4781ed607afaf89f67c0d26481303cd3f5cdcccf09642b763aec1ca`
- `src/services/signer_keystore_service.py` — `98f36ce08d14a289e93f049b959e3bca7be50e91897a3b2c952e7e1913213446`
- `src/services/durable_nonce_ledger.py` — `255b9327ff1b51cde47773ef63484659a47e7454692a7b804824defd77bc299a`
- `src/core/contracts/oracle_ipc_contracts.py` — `f79940cb25aede16a64d60aff882097e2e5ab65cc8ea93bbb3f2fb0faec8eb84`
- `tests/services/test_trial_authorization_signer_daemon.py` — `6123ce8b5d4549f6e1081c6de9e86495650eb518c7287a0fdc2c7b2f245b29d4`
- `tests/services/test_signer_keystore_service.py` — `35bc49e18d947021759dbb5f03fdabe77ae47a1029e267680476a46d599b6ea9`
- `tests/integration/test_signer_daemon_socket_integration.py` — `e230b9d8aa9c6b0dce53b483ad77db99b2e0b200e96b46516837b8995c188d7a`
- `.bridge/ux-evidence/DEP02B/pytest.log` — `031833151cd614debd37b60a56a66de3118527bbc24ea90406f2ea8d31d11629`
- `.bridge/report.md` as submitted — `232ea4a3fbce76a9fcf1902dfeae71886269f2a27a3542431918f745e1867494`

## Summary

The submitted hashes match the current artifacts. R4 resolves DEP02B-R3-01: authorization-to-Director/Operator identity bindings are durably recorded and recovered from the ledger, and Oracle verification requires those bindings and an existing revocation anchor. The public production constructor no longer exposes the test-mode flag, and the strict public-envelope parsing changes from R3 are present.

One shutdown race remains. The `_stopping` checks occur before ledger writes and signing operations, but the checks are not atomic with those operations. A request can pass its check, then `stop()` can set `_stopping`, and the request can still commit a positive Oracle result or an authorization identity record and return success.

## Finding

### DEP02B-R4-01 — HIGH — Check-then-act race allows positive operations after shutdown begins

**Evidence:** `stop()` sets `_running = False` and `_stopping = True` under `self._lock` at `src/services/trial_authorization_signer_daemon.py:1436-1444`. Oracle verification checks these flags at `:740-742`, releases the lock, performs validation, then checks again at `:890-892` and releases the lock before writing the terminal `VERIFIED_VALID` ledger record at `:894-905`. Similarly, consent issuance checks the stop state before writing the consumed nonce (`:1328-1342`), before signing the HMAC (`:1393-1403`), and before writing the durable identity record (`:1408-1423`), with each check separated from the subsequent operation by releasing `self._lock`.

**Impact:** If shutdown starts immediately after the final state check, Oracle processing can still persist `VERIFIED_VALID` and construct a positive signed response, or consent processing can persist the identity binding and return a newly signed authorization, while `_stopping` is already true. The drain timeout handling retains descriptors when workers remain, but it does not close this check/commit race for a worker that completes before or during the drain.

**Required correction:** Coordinate shutdown with in-flight privileged operations using an operation reservation/counter or equivalent synchronization. Atomically prevent new commit/sign operations once stopping begins and ensure any operation that has already reserved commit completion is accounted for before `stop()` transitions to teardown. Recheck or serialize the state transition at the actual ledger/signing boundary; do not rely on a separate flag check followed by an unlocked side effect. Add deterministic race tests for Oracle terminal recording and consent authorization issuance when `stop()` begins between the precheck and commit.

## Prior R3 findings

- DEP02B-R3-01 is resolved: the durable identity ledger restores Director/Operator key IDs after restart, and Oracle fails closed when the record is absent or incomplete.
- DEP02B-R3-02 is partially resolved: worker drain timeout now raises and retains descriptors, but the check/commit race identified in DEP02B-R4-01 remains.

## Required disposition

Resolve DEP02B-R4-01 in a bounded revision and resubmit for review. Do not provision the host, start the daemon, access production key/state paths, run systemd, or proceed to DEP-03/DEP-04 on this submission.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests were run during this review; no daemon, production path/key, host, systemd, or Resolve operation was accessed.
