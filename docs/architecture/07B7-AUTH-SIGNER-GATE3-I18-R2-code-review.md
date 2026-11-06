# Gate 3-I18 Revision R2 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of the submitted I18 R2 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

The submitted SHA-256 values match the current files for `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log reports **358 passed, 0 failed, 2 warnings**. The warnings are Python's multiprocessing `fork()` deprecation warnings. This review treats the test output as submitted evidence and does not independently reproduce it.

## Findings

### I18-R2-01 — Socket and lock operations are not anchored to the verified directory descriptor

**Priority:** High  
**Location:** `src/services/trial_authorization_signer_service.py:6370-6436, 6501-6545`

`_validate_test_socket_path()` walks the candidate parent directories using directory descriptors and no-follow flags, but closes every descriptor in its `finally` block and returns only path strings/`Path` objects. `start()` subsequently opens the singleton lock by absolute path, checks/unlinks the socket with `Path.exists()`/`Path.unlink()`, binds by pathname, chmods by pathname, and verifies with `Path.stat()`.

Thus validation and use are separated by a path-resolution window. If a writable path component is replaced after validation and before `start()` uses it, the lock/socket operations resolve the new path rather than the directory inode that was verified. This defeats the workorder's explicit requirement that the socket be created under a verified temporary-directory descriptor and that the created socket be verified through descriptor-relative checks.

**Required change:** Keep an owned verified parent-directory descriptor for the server lifecycle; create/open the lock and inspect/unlink entries relative to that descriptor, and verify the bound socket entry against the pinned directory. Ensure cleanup uses the same anchored directory. If the platform/API cannot bind and verify the AF_UNIX pathname without reopening an untrusted path, stop and report that API dependency instead of treating the earlier traversal as sufficient. Add a deterministic directory-component swap test covering the interval between validation and bind/cleanup.

### I18-R2-02 — Per-method `params` schemas and override rejection are missing

**Priority:** High  
**Location:** `src/services/trial_authorization_signer_service.py:6799-6859`; regression-test example at `tests/services/test_trial_authorization_signer_service.py:11264-11310`

The server enforces an exact outer envelope schema, but after checking only that `params` is a dictionary, it forwards the entire dictionary to the selected handler. Secret-like keys are screened, but ordinary unknown fields and identity/claim override fields are not rejected at the protocol boundary. The routing test demonstrates arbitrary method-specific payloads (`{"target": "p1"}`, `{"consent": "env1"}`, and `{"req": "test"}`) being passed through without a declared schema.

This does not meet Workorder §3.2/§4, which requires rejection of unknown fields and identity/claim overrides before protected handlers are invoked. It also leaves the socket protocol's accepted input surface dependent on each injected handler's undocumented validation.

**Required change:** Define exact version-1 parameter key/type schemas for each of the three methods and reject unknown or prohibited identity/claim override keys before invoking any handler. Add tests proving each invalid request returns an error and invokes no protected handler.

## Verified remediations

- **I18-01 lexical ordering:** The candidate path is normalized and checked with `relative_to(temp_root)` before candidate-component probes. The submitted tripwire test covers outside-root probes and symlink rejection.
- **I18-02 persistent lock inode:** `_cleanup_lock()` no longer unlinks the lock file; the reported inode-contention test covers stop/start reuse.
- **I18-03 cleanup diagnostics:** Cleanup failures are surfaced as structured errors, and failed socket/lock handles are retained for diagnosis rather than silently discarded.

These remediations do not close findings I18-R2-01 or I18-R2-02 above.

## Safety state

No live operation was performed or authorized by this review. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. I18 is **not accepted** pending remediation and review.
