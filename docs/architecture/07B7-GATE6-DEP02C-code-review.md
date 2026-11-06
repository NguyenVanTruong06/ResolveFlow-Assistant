# Gate 6 DEP-02C — Static Code Review

**Workorder:** G07-07B7-GATE6-DEP02C — Oracle Terminal-State Notification Protocol & Durable Ledger  
**Review disposition:** `CHANGES_REQUIRED`  
**Scope:** Static review of the seven-file DEP-02C allowlist. No tests, daemon, host services, Resolve SDK/API, or production state were accessed in this review.

## Artifact integrity

The submitted SHA-256 values match the files present in the workspace for all seven allowlisted artifacts:

- `src/core/contracts/oracle_ipc_contracts.py`: `59fb1cbc4ef7e57aa986895abad25b94535b2f2ffec54de67861dba49f829502`
- `src/services/durable_nonce_ledger.py`: `28a0f4a7f1fa65b622b90a30cb59d67c8369103b63fea5858037a65d91442f1d`
- `src/services/trial_authorization_signer_daemon.py`: `1c279d234ee61c2a8b355df9eb08d4fa6eefb63c44736440265b35c6d414eb2a`
- `tests/services/test_trial_authorization_signer_daemon.py`: `12bc4d06c3b15bcbaf7e65e991f2d2746a43f8e600cdb5ae055d3225ed561bd3`
- `tests/integration/test_signer_daemon_socket_integration.py`: `3f97610048c24c3432ac8d033a401a33c4a7626a68a9db9e70e25c794b34ddc7`
- `.bridge/ux-evidence/DEP02C/pytest.log`: `ab6d969c94f6b531b29cf44828b14d5c0f16bfbd33be3c09e3ef739b5892d559`
- `.bridge/report.md`: `02d85a7bbefc656ddce3d190da892de2ce0156a2f9df441ab19bd0da29afd0b5`

The supplied test evidence reports 76 passing tests. It was inspected as submitted evidence and was not rerun during this static review.

## Findings

### DEP02C-01 — Terminal ledger accepts records without a durable authorization identity — MEDIUM

`DurableSignerLedger.record_oracle_terminal_state()` treats the authorization identity record as optional: at [durable_nonce_ledger.py](</media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/services/durable_nonce_ledger.py:751>), identity fields are checked only when `auth_id_rec is not None`. Recovery repeats this optional check at [durable_nonce_ledger.py](</media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/services/durable_nonce_ledger.py:600>). Thus a terminal record can be appended and later recovered as authoritative for a verified execution even when its required durable authorization-identity binding is absent.

The daemon request handler currently checks that identity before its own append, but the ledger is the durable authority and its public transition/recovery paths must enforce the invariant themselves. This contradicts Workorder §3.2, which requires a matching durable identity binding and fail-closed recovery.

**Required remediation:** Require the authorization identity record to exist in both the append path and recovery path. Validate authorization ID, action digest, and proposal revision against it; reject missing identity as a structured security/corruption error. Add unit regressions for both direct transition and restart recovery with a verified execution but no identity record. Preserve legacy nonterminal execution records as specified.

### DEP02C-02 — Terminal response DTO accepts undefined status and terminal-state values — MEDIUM

The request DTO correctly limits `terminal_state`, but `OracleTerminalNotificationResponse.__post_init__()` only checks that `terminal_state`, `status`, and `reason_code` are nonempty strings at [oracle_ipc_contracts.py](</media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/core/contracts/oracle_ipc_contracts.py:697>). The response parser therefore accepts values outside the protocol vocabulary, including an unrecognized terminal state or a status other than `ACCEPTED`, `TERMINAL_ALREADY_RECORDED`, `REJECTED`, or `FAILED`. A downstream client relying on the DTO's strict schema can receive a parsed but semantically invalid response.

**Required remediation:** Validate the response `terminal_state` against `VALID_TERMINAL_STATES` and `status` against a fixed immutable response-status set. Enforce the defined status/reason combinations where the protocol distinguishes accepted, idempotent, rejected, and failed outcomes. Add direct-construction and JSON parsing tests for unknown state/status and inconsistent combinations, while preserving valid signed round trips.

## Decision and safety state

DEP-02C is **not accepted** pending both findings. Do not resume DEP-03 implementation until a remediation revision is reviewed and accepted, then issue a separate DEP-03 continuation workorder.

No live or production operation is authorized by this review. Preserve `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
