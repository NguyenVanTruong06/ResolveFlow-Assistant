# Gate 3-I18 Revision R8 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R8 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **372 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; no independent test run was performed.

## Finding

### I18-R8-01 — `sock_node_fd` is closed twice on multiple startup failure paths

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6964-6999, 7013-7057, 7060-7087, 7098-7103`

Several error branches explicitly call `os.close(sock_node_fd)` and then raise, but leave the local variable non-`None`. The enclosing `except` subsequently calls `os.close(sock_node_fd)` again. This occurs on the non-socket check, nonce-probe failure, post-probe identity mismatch, pre-chmod disappearance/replacement, chmod failure, and post-chmod verification failures.

POSIX close errors do not establish that the numeric descriptor remains open. In this multi-threaded process, another thread could reuse the number after the first close and before the outer cleanup; the second close could then close an unrelated descriptor. Even without reuse, the double-close violates the single-close lifecycle expected for descriptor ownership and can obscure the original cleanup state.

**Required change:** Centralize ownership of `sock_node_fd` in one cleanup path, or disown it (`sock_node_fd = None`) before the first close attempt in every branch. Ensure every path makes at most one `os.close()` attempt for that numeric descriptor, including close-error paths. Add fault-injection coverage that instruments close calls on each representative startup failure path and proves exactly one attempt, with descriptor-number reuse simulation where practical.

## R7 findings disposition

- **I18-R7-01:** Addressed. The socket-node descriptor remains pinned through the chmod operation, mode is changed through `/proc/self/fd/<fd>`, and the pre-chmod replacement test verifies the foreign socket is unchanged.
- **I18-R7-02:** The implementation now repeats descriptor-relative identity verification at the pre-unlink boundary and documents that Linux VFS does not offer atomic compare-and-unlink. The deterministic tests cover replacement before that final verification. This review records that as a documented local-test limitation: the re-stat narrows the check/use window but cannot make it mathematically atomic against a concurrent same-UID writer after the final stat. The submission should not describe this behavior as an atomic compare-and-delete guarantee or as eliminating every possible race.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
