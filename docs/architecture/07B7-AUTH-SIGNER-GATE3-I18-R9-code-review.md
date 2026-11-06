# Gate 3-I18 Revision R9 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R9 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **373 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; no independent test run was performed.

## Finding

### I18-R9-01 — `sock_node_fd` close failures are silently discarded

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:7067-7072, 7077-7084`

R9 correctly disowns `sock_node_fd` before its single `os.close()` attempt, preventing the prior double-close/reused-descriptor hazard. However, both the success path and startup-exception cleanup catch `OSError` from that close and silently `pass`. On success the server proceeds to report itself running; on failure the original error is raised without the descriptor-close failure in structured cleanup diagnostics. Since POSIX close failure does not provide a safe basis for retrying the numeric descriptor, the caller needs to retain the ambiguous cleanup outcome rather than suppress it.

This leaves an unreported descriptor-cleanup failure/leak possibility and conflicts with the socket server's structured cleanup error behavior and Workorder §3.3 requirement to preserve ambiguous/failed cleanup as structured errors.

**Required change:** Keep the single-close rule, but record a failed close attempt as a structured cleanup error (for example, `ERR_SOCKET_NODE_FD_CLOSE_FAILED`) and include it in `ERR_START_AND_CLEANUP_FAILED` or `ERR_CLEANUP_FAILED` as appropriate. On the success path, do not return a normal running result if closing the temporary ownership descriptor failed; transition through fail-closed cleanup and report the primary and cleanup outcomes without retrying the descriptor number. Add fault-injection tests for close failure on both successful-bind startup and another startup-failure path, asserting one close attempt and preserved structured diagnostics.

## Findings addressed in R9

- **I18-R8-01:** Addressed. Descriptor ownership is disowned before the one close attempt, and the submitted regression exercises eight paths with simulated descriptor-number reuse.
- **I18-R7-01:** The pinned inode is chmodded via `/proc/self/fd/<sock_node_fd>`; the pre-chmod replacement hook is checked before that mutation.
- **I18-R7-02:** The submitted source documents that Linux VFS lacks atomic compare-and-delete and uses an immediate pre-unlink identity recheck. This review treats the residual after-final-stat race as a documented local-test API limitation, not an atomic compare-delete guarantee.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
