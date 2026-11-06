# Gate 6 DEP-02B Revision R3 — Static Code Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02B — Production Signer Daemon & Oracle Socket Server`
- **Workorder SHA-256:** `718813808941250a989a7ec172d6532346661eb70410cb0f16cef6dff20d950f`
- **Review mode:** Static source review. The submitted 56-pass pytest result was not rerun. No daemon, host provisioning, systemd, production path/key, or Resolve operation was accessed.

## Reviewed artifact hashes

- `src/services/trial_authorization_signer_daemon.py` — `69d77670d51eda68f0277c76392d4973401feeef623fa9710e8db3bd2a163e3c`
- `src/services/signer_keystore_service.py` — `98f36ce08d14a289e93f049b959e3bca7be50e91897a3b2c952e7e1913213446`
- `src/services/durable_nonce_ledger.py` — `d74231f342e88ae764ef358a3066cabf10870aedf18e2969e05a5a96a7ef75af`
- `src/core/contracts/oracle_ipc_contracts.py` — `f79940cb25aede16a64d60aff882097e2e5ab65cc8ea93bbb3f2fb0faec8eb84`
- `tests/services/test_trial_authorization_signer_daemon.py` — `9a2b63b6b39459a370581a3b1f3226478aac0acffd30debe625a4698d688a210`
- `tests/services/test_signer_keystore_service.py` — `35bc49e18d947021759dbb5f03fdabe77ae47a1029e267680476a46d599b6ea9`
- `tests/integration/test_signer_daemon_socket_integration.py` — `db90e0ee6931b74b19e593e1315149d8a1133201575867aacf90075f71fa5005`
- `.bridge/ux-evidence/DEP02B/pytest.log` — `e96a63620e89c9be1580a840e08357a8eae01ff029926da13e4d80b34b7e07df`
- `.bridge/report.md` as submitted — `65681adde3af5d9171bb32380337de142435e3fe40acfd3e1b1313979ba8477a`

## Summary

The submitted hashes match the current artifacts. R3 resolves the R2 public-constructor test-mode switch, uses duplicate-key-rejecting parsing and exact public request envelopes, passes `require_existing_anchor=True`, and checks Director/Operator IDs when the daemon has them. It also releases the shared lock before joining workers.

Two important gaps remain. The Director/Operator key IDs needed for the Oracle revocation decision are held only in process memory and are lost on daemon restart, while the signed authorization contract does not contain those IDs. On shutdown, the daemon proceeds to close descriptors after worker joins time out and records the condition only in an internal list.

## Findings

### DEP02B-R3-01 — BLOCKER — Oracle cannot check Director/Operator revocation after daemon restart

**Evidence:** `_process_oracle_verification()` obtains `director_key_id` and `operator_key_id` from `auth` using `getattr`, then falls back to `_issued_authorizations_keys` (`src/services/trial_authorization_signer_daemon.py:728-760`). `TrialExecutionAuthorization` has no Director or Operator key ID fields (`src/core/contracts/trial_authorization_contracts.py:744-798`). `_issued_authorizations_keys` is an in-memory dictionary initialized empty in `_init_common()` (`trial_authorization_signer_daemon.py:264-266`) and populated after consent issuance (`:1351-1357`). It is not restored from the durable ledger when the daemon starts.

**Impact:** After a daemon restart, a previously issued, still-valid HMAC authorization reaches the Oracle with `meta is None`; both key IDs remain `None`. `_check_key_revocation()` skips each key-set membership test when its ID is absent (`:714-722`), yet the Oracle can continue to the positive verification path. The new in-process checks therefore do not preserve the required Director/Operator revocation guarantee across the production daemon lifecycle.

**Required correction:** Persist an authoritative, integrity-protected mapping from each authorization ID to its Director and Operator key IDs as part of the issuance/consumption record, and restore and validate that mapping before serving Oracle requests. Bind the mapping to the authorization claims and reject positive verification whenever either identity is unavailable. If the accepted ledger API cannot safely retain and recover this binding within the allowlist, stop and report that dependency; do not rely on an ephemeral cache or an unsigned caller-supplied registration.

### DEP02B-R3-02 — HIGH — Shutdown continues teardown after worker drain timeout

**Evidence:** `stop()` joins listener and worker threads with timeouts, then records remaining workers only in `_cleanup_failures` (`src/services/trial_authorization_signer_daemon.py:1405-1422`). It nevertheless proceeds to unlink sockets and close lock/directory descriptors (`:1424-1458`). No structured error is raised for the remaining workers in this path.

**Impact:** A request handler still running after the timeout can continue accessing ledger or signer state after the daemon has torn down its sockets and descriptors. Callers of `stop()` receive no failure signal and may treat shutdown as complete even though worker activity remains.

**Required correction:** Do not close descriptors or report successful shutdown while workers remain. Make stopping state prevent in-flight issuance from crossing its commit/signing boundary after shutdown starts. If bounded draining expires, retain resources needed by remaining workers and return/raise a structured cleanup failure; only perform final teardown after all workers have exited.

## Prior R2 findings

- DEP02B-R2-02 is resolved: the production constructor no longer exposes test overrides; test settings use `create_for_test()`.
- DEP02B-R2-03 is resolved: the public handler uses `parse_strict_json()` and an exact field whitelist.
- DEP02B-R2-01 is improved: required-anchor verification and key-ID checks are called in the current process, but the identity binding is not durable across restart; see DEP02B-R3-01.
- DEP02B-R2-04 is improved: the shared lock is released before joins, but teardown still proceeds after timeout; see DEP02B-R3-02.

## Required disposition

Resolve both findings in a bounded revision and resubmit for review. Do not provision the host, start the daemon, access production key/state paths, run systemd, or proceed to DEP-03/DEP-04 on this submission.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests were run during this review; no daemon, production path/key, host, systemd, or Resolve operation was accessed.
