# Gate 3-I18 Revision R4 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R4 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted test log records **360 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; this review did not rerun tests.

## Finding

### I18-R4-01 — Cleanup unlinks the socket basename without proving entry ownership

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6817-6820, 6864-6871, 6940-6950`

The startup failure cleanup (`_cleanup_all()`) and normal stop path unlink `_leaf_filename` relative to the pinned directory descriptor without first proving that the current directory entry is the socket node created by this server instance. On a bind failure, `_cleanup_all()` still attempts that unlink; on stop, another same-UID process could replace the entry after startup and the stop path would remove the replacement. The descriptor prevents escape from the pinned directory, but it does not establish ownership of the basename's current inode.

This conflicts with Workorder §3.2's requirement to close/unlink only server-owned temporary socket artifacts. It can delete a different entry in the test directory or leave ambiguous cleanup state while reporting an ordinary stop result.

**Required change:** Track the created socket entry identity (at least device and inode) as soon as a successful bind is verified. Before each unlink, use `stat(..., dir_fd=..., follow_symlinks=False)` and unlink only if the entry is a socket and its identity matches the recorded server-owned entry. On bind failure before ownership is established, do not unlink the basename. Treat disappearance or identity replacement as a structured ownership/cleanup error and preserve the replacement entry. Add tests for (a) a colliding/replaced entry after stale-entry removal but before bind failure cleanup and (b) replacement of the socket entry before `stop()`; both must prove the foreign entry remains untouched.

## Findings addressed in R4

- **I18-R3-01:** The server now retains the verified parent directory descriptor, uses `/proc/self/fd/<fd>/<leaf>` for AF_UNIX bind, performs a pre-bind path-to-descriptor identity check, removes the unanchored chmod fallback, and keeps lock/chmod/unlink operations descriptor-relative. The submitted component-swap regression asserts the replacement directory stays empty.
- **I18-R3-02:** TTL validation explicitly rejects booleans and non-positive/non-finite values before handler invocation; the submitted regression checks `true`, negative, zero, and string values.

These remediations close the prior findings. I18 remains unaccepted until I18-R4-01 is resolved and reviewed.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
