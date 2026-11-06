# Gate 6 DEP-02B Production Signer Daemon & Oracle Socket Server Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02B`
- **Workorder SHA-256:** `718813808941250a989a7ec172d6532346661eb70410cb0f16cef6dff20d950f`
- **Reviewed artifact hashes:**
  - `src/services/trial_authorization_signer_daemon.py` — `9347f4a8672f76ccce0a14717c878a26de8d69ed6ed179a48353a3d71e16d171`
  - `src/services/signer_keystore_service.py` — `98f36ce08d14a289e93f049b959e3bca7be50e91897a3b2c952e7e1913213446`
  - `src/services/durable_nonce_ledger.py` — `05e05e2b5aba5f1fab0e88c45cdd3ec8c0c66e478c886c8048fdce0ad8f2305e`
  - `src/core/contracts/oracle_ipc_contracts.py` — `81df69dcfe3de0a464f79c96819d228215d74518ac0b0e32ab952308aa2db176`
  - `tests/services/test_trial_authorization_signer_daemon.py` — `a6deb2edf28b8b31812aac70524d622f754c3a89b5588ed2275f0a9dbf79146d`
  - `tests/services/test_signer_keystore_service.py` — `35bc49e18d947021759dbb5f03fdabe77ae47a1029e267680476a46d599b6ea9`
  - `tests/integration/test_signer_daemon_socket_integration.py` — `21a32aa79b35eda0cb040fb3cdea111c0f7341719a6f242051af234c2155222c`
  - `.bridge/ux-evidence/DEP02B/pytest.log` — `96eb43ce495a6c97bbaaeea5cc87cf12a5b322629ac3f4f0f7077cb512c2469b`
  - `.bridge/report.md` as submitted — `c069fd00273e82f8a31b98c013cc41da58eeb904c3ce6276db6025ed7cbae8ef`
- **Review mode:** Static source review. The reported pytest result was not rerun. No daemon was started and no host, production path/key, systemd, or Resolve operation was performed.

## Decision summary

The allowlisted source and test files are present and their hashes match the handoff. Framing/contracts, temporary-directory test coverage, and several descriptor-relative checks are implemented. The slice is not ready for acceptance: the signer public endpoint does not implement the required challenge/consent issuance flow, the Oracle can return a signed `VERIFIED_VALID` response without checking revocation state, and further lifecycle/resource controls do not yet meet the workorder and DEP-01 design.

## Findings

### DEP02B-01 — BLOCKER — Public signer endpoint does not implement authorization issuance

**Evidence:** `src/services/trial_authorization_signer_daemon.py:565-588` parses public requests only to recognize `ping_nonce`; every other request gets `ERR_UNKNOWN_PUBLIC_REQUEST`. The workorder §3.3 requires the public endpoint to bind challenge issuance and operator-consent submission to accepted Gate 3 APIs. DEP-01 §2.3.3 and the architecture flow require the runner to submit plan/approval/consent through `signer_public.sock` and receive a signed authorization.

**Impact:** No production authorization issuance path exists through the daemon. The principal public API required by the design cannot be completed; a successful ping is not an issuance capability.

**Required correction:** Implement strict request/response schemas and handlers for the required challenge and consent/issuance operations, bind every field to authoritative consumed challenge/admission state through accepted Gate 3 APIs, and reject caller overrides. If those APIs cannot be safely composed within the allowlist, stop and report the dependency rather than treating ping as completion.

### DEP02B-02 — BLOCKER — Oracle signs valid responses without revocation verification

**Evidence:** `src/services/trial_authorization_signer_daemon.py:405-512` checks terminal execution state, HMAC, action digest, target IDs, proposal revision, and expiry, then records and signs `VERIFIED_VALID`. The daemon has no revocation manifest/registry/anchor dependency or verification call; its constructor at lines 72-130 accepts only keystore, nonce ledger, socket paths, and peer settings. DEP-01 threat cases T-07/T-09 require checking Director/Operator key revocation and the durable monotonic revocation epoch before a valid Oracle response is signed.

**Impact:** A still-unexpired authorization whose Director or Operator key has been revoked can receive a fresh Ed25519-signed `VERIFIED_VALID` response. This defeats the designed revocation gate at the final trusted decision point.

**Required correction:** Add authoritative, fresh revocation verification and durable epoch/anchor anti-rollback validation to the Oracle decision path before recording a verified execution or signing a positive response. Bind the decision to the verified Director and Operator key IDs and fail closed for absent, stale, rolled-back, tampered, or revoked state. Report any required upstream API or allowlist dependency instead of accepting unverified evidence.

### DEP02B-03 — HIGH — Endpoint binding is not consistently anchored to the verified directory descriptor

**Evidence:** In `_setup_endpoint`, the `dir_path` branch opens a directory descriptor but sets `real_dir_path = str(dir_path)` (`src/services/trial_authorization_signer_daemon.py:208-210`). It checks only that the descriptor refers to a directory (`:214-221`); it does not enforce the endpoint directory's owner, group, or exact `02750` mode. Binding and chmod then use `full_sock_path` (`:296-303`), with chmod errors swallowed, while later verification stats the basename relative to the previously opened descriptor (`:306-317`).

**Impact:** When configured with a path rather than a descriptor, the namespace used for bind/chmod can diverge from the pinned directory after a path change. Missing parent DAC checks also permit startup against a directory that does not satisfy the accepted setgid access boundary. The post-bind stat can fail after the socket was created elsewhere, leaving an untracked node; it does not make the earlier path operation descriptor-relative.

**Required correction:** Validate directory UID/GID/type and exact `02750` mode from the opened descriptor before creating lock/socket nodes. Derive the bind target from the pinned descriptor for both constructor input forms (or require a verified directory descriptor and fail closed if descriptor-relative binding is unavailable). Remove path-based chmod fallbacks; if a required operation fails, do not continue unanchored and clean up only a node whose identity has been established.

### DEP02B-04 — HIGH — Uncertain ledger writes do not poison the live ledger

**Evidence:** `DurableSignerLedger.record_consumed_nonce()` (`src/services/durable_nonce_ledger.py:348-367`) raises on a partial write or `fsync` error but leaves the ledger open and does not mark its state poisoned. `record_oracle_execution()` has the same behavior at `:389-408`. The in-memory indices are updated only after fsync, so after an ambiguous partial write/fsync failure a later call can append to the same descriptor and proceed as if the failed record were absent.

**Impact:** A partial record can be followed by another record, creating a torn/corrupt append-only ledger; a full write with failed fsync can be duplicated by a later retry. Restart recovery then rejects the ledger or the durable state may disagree with the live process's in-memory indices. This violates the workorder's fail-closed handling of ambiguous persistence outcomes and no-automatic-retry requirement.

**Required correction:** On any write, file-fsync, directory-fsync, or ambiguous close outcome, permanently poison the ledger instance and reject all further mutation/authorization operations. Preserve structured primary/cleanup errors and require a controlled restart/recovery path before service resumes. Add follow-up fault-injection assertions proving zero subsequent append and no success response after uncertainty.

### DEP02B-05 — HIGH — Peer-credential enforcement can be disabled by ordinary daemon configuration

**Evidence:** The constructor exposes `oracle_allowed_uids` and `enforce_dac_ownership` (`src/services/trial_authorization_signer_daemon.py:72-88`). In `_handle_oracle_client` (`:364-385`), UID rejection is conditional on the tuple being truthy; `oracle_allowed_uids=()` disables the check. The exception path also proceeds when `enforce_dac_ownership=False`. The workorder requires Oracle `SO_PEERCRED` authorization to require UID 902 and says test-only injected peer policy must not weaken production defaults.

**Impact:** A daemon instantiated with an empty allowed-UID tuple accepts any peer credential when the socket is reachable; a test-style configuration can also convert credential retrieval failure from deny to proceed. The code does not make those relaxations test-only or prevent them in production construction.

**Required correction:** Make production peer authorization unconditional and pinned to UID 902, and make credential lookup failure always deny. If tests need alternate credentials or ownership, route that through an explicitly test-only factory/configuration that cannot be selected by the production daemon factory or caller-controlled runtime configuration.

### DEP02B-06 — MEDIUM — Listener spawns an unbounded thread per accepted connection

**Evidence:** `_run_listener()` (`src/services/trial_authorization_signer_daemon.py:340-359`) creates and starts a new daemon thread for every accepted connection without a worker limit, queue bound, or admission semaphore. Each handler can hold a thread while waiting up to the configured frame timeout.

**Impact:** A permitted local client on the public socket can open many partial-frame connections and exhaust the Signer process's thread/memory resources, denying service to legitimate signer/oracle requests. The workorder explicitly requires bounded concurrency/resource use.

**Required correction:** Bound concurrent handlers and queued work, close/reject excess connections before spawning work, and ensure shutdown drains or cancels bounded workers without leaving request threads using ledger/key state after stop.

## Positive observations

- The daemon constructor/import path is inert with respect to starting listeners.
- Oracle request framing uses the strict contract parser and the response signer builds the documented signed-claim dictionary.
- Lock validation checks the opened lock descriptor before flock and does not unlink persistent lock files.
- The submitted test log hash matches the handoff; reported result is 35 passed. This review did not rerun it.

## Required disposition

Resolve all findings above in a bounded revision and resubmit for review. Do not deploy/start the daemon, access production keystore or ledger paths, provision the host, run systemd, or proceed to DEP-03/DEP-04 based on this submission.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests were run during this review; no daemon, production path/key, host, systemd, socket outside test evidence, or Resolve operation was accessed.
