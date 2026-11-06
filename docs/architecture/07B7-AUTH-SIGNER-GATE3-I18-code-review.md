# Gate 3-I18 — Operator Console & Signer Socket Server Interface Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the I18 workorder, operator console, socket server implementation, tests, report, and submitted pytest log. All submitted SHA-256 values for the workorder, report, four implementation/test files, and I18 evidence log match the handoff values at review time.

The submitted log records **351 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

The local-test-only boundary, operator confirmation flow, framing limit, canonical JSON parsing, peer-UID check, and three-method allowlist are present. The findings below concern socket-path validation and server singleton/cleanup behavior.

## Findings

### I18-01 — HIGH: Candidate path is probed before containment is established

`_validate_test_socket_path()` (`src/services/trial_authorization_signer_service.py:6290–6320`) calls `root_path.is_dir()` and then `is_symlink()` on every component of the candidate socket path before it performs `sock_path.relative_to(root_path)`. For a candidate outside `temp_root`, these path-based filesystem probes occur before the function rejects it. A symlinked `temp_root` is also queried by `is_dir()` before the symlink rejection loop.

This contradicts the I18 workorder requirement to reject outside-temp paths and symlinks before candidate-path filesystem probes, and the implementation docstring's claim that containment is established before those probes.

**Required correction:** do lexical normalization and containment checks before querying candidate components. Validate the trusted temporary root separately, then traverse the in-root relative path descriptor-by-descriptor with no-follow semantics. Avoid path-based `resolve()`, `is_symlink()`, `stat()`, or equivalent calls on an uncontained candidate. Add a tripwire test that intercepts candidate-path filesystem operations and proves an outside-root candidate is rejected with zero probes; include symlinked-root and in-root symlink cases.

### I18-02 — HIGH: Unlinking the singleton lock file permits lock-inode split

`_cleanup_lock()` unlocks and closes the descriptor and then unlinks `self._lock_path` (`src/services/trial_authorization_signer_service.py:6499–6515`). This makes the lock pathname unstable. During shutdown, another server can open and lock the old inode after the first server unlocks it; the first server can then unlink that pathname while the second still holds the inode. A third server can create a new file at the same path and acquire a lock on the new inode, allowing two server instances to hold what appears to be the same singleton lock.

**Required correction:** keep the singleton lock file persistent for the lifetime of the temporary test directory; release/close the lock descriptor but do not unlink the lock file. Continue removing only the server-owned socket node on clean shutdown. Add a deterministic concurrent stop/start test proving every server instance contends on the same lock inode and no second instance starts while another holds it. Update the existing test that currently expects the lock pathname to disappear.

### I18-03 — HIGH: Cleanup failures are swallowed and shutdown is reported as clean

`stop()`, `_cleanup_lock()`, and `_cleanup_all()` (`src/services/trial_authorization_signer_service.py:6474–6531`) catch and ignore socket-close, `flock(LOCK_UN)`, descriptor-close, and unlink failures. They then clear object state and may unlink the lock path despite an ambiguous lock-descriptor close. Callers receive no structured indication that socket or lock cleanup failed, contrary to the workorder's requirement to preserve ambiguous/failed cleanup as structured errors.

**Required correction:** preserve primary and cleanup failures on a structured `SignerSocketServerError`; do not claim successful stop or discard diagnostic ownership state after an ambiguous close. Apply single-close semantics to owned descriptors, and do not unlink the persistent singleton lock file. Add fault-injection coverage for socket close, unlock, lock descriptor close, and socket unlink failures, including combined primary/cleanup errors.

## Safety and disposition

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. The declared state remains `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

I18 is **not accepted** pending correction of these findings and submission of updated source, tests, and authorized evidence. This review does not authorize a production daemon, OS identity deployment, production socket, or live operation.
