# Gate 6 DEP-02B Revision R2 — Static Code Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02B — Production Signer Daemon & Oracle Socket Server`
- **Workorder SHA-256:** `718813808941250a989a7ec172d6532346661eb70410cb0f16cef6dff20d950f`
- **Review mode:** Static source review only. The submitted 50-pass pytest result was not rerun. No daemon, host provisioning, systemd, production path/key, or Resolve operation was accessed.

## Reviewed artifact hashes

- `src/services/trial_authorization_signer_daemon.py` — `9d41c538509a4a6a1e6727040a29a94dbd7768139696115ff7dc8710e9b43c60`
- `src/services/signer_keystore_service.py` — `98f36ce08d14a289e93f049b959e3bca7be50e91897a3b2c952e7e1913213446`
- `src/services/durable_nonce_ledger.py` — `d74231f342e88ae764ef358a3066cabf10870aedf18e2969e05a5a96a7ef75af`
- `src/core/contracts/oracle_ipc_contracts.py` — `114efa937ee8b9140dd003025e38587423408cecdd76e1726bd07a93be287e39`
- `tests/services/test_trial_authorization_signer_daemon.py` — `f2cbcd36beb9ee65f8182626d41b3dac51c31ab90413968da3be6df093e2ce20`
- `tests/services/test_signer_keystore_service.py` — `35bc49e18d947021759dbb5f03fdabe77ae47a1029e267680476a46d599b6ea9`
- `tests/integration/test_signer_daemon_socket_integration.py` — `68d1b27cbbd88134718f64afb86468139d240677a2842efadc5758f8afb02b10`
- `.bridge/ux-evidence/DEP02B/pytest.log` — `895e16645d04588092797093ce24b503f3ecae2495ef387f1a64840828bf6677`
- `.bridge/report.md` as submitted — `b167f39bfa56942d00736fb55fd18a3f00e189e682ab9b26507ec694283ea9c7`

## Summary

Revision R2 addresses the prior public-endpoint, descriptor-binding, ambiguous-ledger-write, and unbounded-connection findings in substantial part. The public challenge/consent flow is now present, socket binding uses the pinned directory descriptor, ledger writes poison the instance after ambiguous failures, and listener concurrency is bounded.

The slice is not acceptable yet. The Oracle's final revocation gate omits Director and Operator key IDs and does not require the durable epoch anchor to exist. Also, a public constructor flag still enables the test-only relaxation path. The public wire parser and shutdown drain have remaining strictness/lifecycle gaps.

## Findings

### DEP02B-R2-01 — BLOCKER — Oracle revocation decision omits key identities and permits absent anchor

**Evidence:** `src/services/trial_authorization_signer_daemon.py:671-677` calls `_check_key_revocation()` with only `authorization_id` and `trial_key_id`; it does not pass `auth.director_key_id` or `auth.operator_key_id`. The helper supports checking both at `:612-653`, but that check is therefore not performed at the final Oracle decision point. In addition, both manifest-loading branches at `:629-641` pass `advance_anchor=False` without `require_existing_anchor=True`. The accepted I11 loader defaults that option to `False`, allowing an absent anchor to follow its bootstrap/read-only behavior.

**Impact:** The daemon can issue a signed positive Oracle response for an authorization whose Director or Operator key is revoked, and can make that decision without proving that the durable anti-rollback anchor already exists. This leaves the final trusted boundary weaker than DEP-01's revocation and anti-rollback requirements.

**Required correction:** Pass the authorization's Director and Operator key IDs to the Oracle revocation check and require an existing durable epoch anchor atomically in every verification path. Fail closed on missing, stale, rolled-back, tampered, expired, or revoked state before recording a positive terminal result or signing `VERIFIED_VALID`. If the accepted API cannot meet this within the allowlist, stop and report the dependency rather than weakening the gate.

### DEP02B-R2-02 — HIGH — Test-only security relaxations remain caller-selectable on the production constructor

**Evidence:** `TrialAuthorizationSignerDaemon.__init__` publicly accepts `for_test` at `src/services/trial_authorization_signer_daemon.py:136-160`. Passing `for_test=True` skips the production invariant checks at `:162-172`; the resulting instance uses caller-provided peer policy at `:579-580`, and `_check_key_revocation()` permits a missing registry at `:622-625`. `create_for_test()` exists, but it delegates to this same publicly callable constructor with `for_test=True` (`:231-265`).

**Impact:** Any caller constructing the daemon can opt into the test relaxation path through the normal production class constructor. The presence of a named test factory does not isolate the relaxed configuration while the general constructor exposes the switch.

**Required correction:** Remove the public `for_test` escape hatch from the production constructor. Route test-only dependencies and relaxed peer/ownership policies through a test-specific construction path that cannot be selected by production configuration or ordinary production construction. Keep production peer UID, DAC enforcement, and mandatory revocation dependencies fixed and fail closed.

### DEP02B-R2-03 — MEDIUM — Public endpoint bypasses strict duplicate-key and envelope validation

**Evidence:** `_handle_public_client()` parses frames using `json.loads()` at `src/services/trial_authorization_signer_daemon.py:841-846`, instead of the duplicate-key-rejecting parser defined in `src/core/contracts/oracle_ipc_contracts.py`. It then dispatches using `data.get("method")` and accepts either `data["params"]` or the entire top-level object (`:847-867`), without validating an exact outer envelope schema.

**Impact:** Duplicate JSON keys are resolved using last-value semantics and unknown envelope fields can be silently ignored or routed into a different request branch. This violates the workorder's strict JSON and unknown-field rejection requirements and makes signed/request interpretation dependent on parser behavior.

**Required correction:** Parse public frames with duplicate-key rejection and validate an exact versioned envelope schema before selecting a handler. Reject duplicate keys, unknown top-level fields, missing fields, and conflicting method/payload forms before invoking any handler.

### DEP02B-R2-04 — MEDIUM — Shutdown joins workers while holding a lock they need and proceeds after timeout

**Evidence:** `stop()` holds `self._lock` while joining workers for up to two seconds each at `src/services/trial_authorization_signer_daemon.py:1275-1285`. The public consent handler acquires that same lock to read active challenge state at `:1134` and later to remove it at `:1266`.

**Impact:** A worker blocked waiting for `self._lock` cannot finish while `stop()` holds it. After the bounded join times out, `stop()` continues closing listeners and descriptors; when the lock is released, the worker can resume and continue consent verification/signing against daemon state after shutdown has progressed. The current drain is time-bounded but does not guarantee worker quiescence before teardown.

**Required correction:** Mark the daemon as stopping under the lock, release shared state locks before joining workers, and prevent handlers from beginning or completing issuance after shutdown starts. Do not close ledger/key/directory resources until all bounded workers have exited; if draining fails, leave the daemon stopped and report a structured failure without allowing post-stop authorization work.

## Positive observations

- Public challenge/consent issuance now composes the accepted Gate 3 challenge authority and one-time issuance flow.
- Endpoint directories are checked for owner/group/mode, and socket binding is performed via `/proc/self/fd/{dir_fd}/...` without a path-based fallback.
- The durable ledger marks itself poisoned after write/fsync uncertainty and blocks subsequent operations.
- Connection concurrency is bounded by a semaphore, and Oracle peer-credential retrieval failures deny the request.
- Reported test evidence is 50 passed, but it was not rerun during this static review.

## Required disposition

Resolve all findings above in a bounded revision and resubmit for review. Do not provision the host, start the daemon, access production key/state paths, run systemd, or proceed to DEP-03/DEP-04 on this submission.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests were run during this review; no daemon, production path/key, host, systemd, or Resolve operation was accessed.
