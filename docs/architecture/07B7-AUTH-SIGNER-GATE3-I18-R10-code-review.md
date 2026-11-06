# Gate 3-I18 Revision R10 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `ACCEPTED` for the bounded local-test I18 slice  
**Review scope:** Static review of submitted R10 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **375 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; no independent test run was performed.

## I18-R9-01 disposition

**Finding:** Closed.

The success and startup-failure paths now disown `sock_node_fd` before making exactly one `os.close()` attempt. A close failure is no longer swallowed: successful-bind startup raises `ERR_SOCKET_NODE_FD_CLOSE_FAILED`, enters fail-closed cleanup, and does not report the server as running. If a primary startup error already exists, the close failure is retained in the structured cleanup diagnostics under `ERR_START_AND_CLEANUP_FAILED` without retrying the numeric descriptor.

The added fault-injection tests cover close failure on both successful-bind startup and a primary startup-failure path, checking single-close behavior and preservation of both errors. The prior descriptor-reuse regression remains present.

## Review of retained safety boundaries

- I18 remains limited to the explicitly started local-test server and operator console. This acceptance does not demonstrate OS-principal separation or production DAC/service isolation.
- The source and report explicitly acknowledge that Linux VFS does not provide atomic compare-and-unlink. Tombstone handling performs a final identity recheck and fails closed when replacement is observed; this is a documented local-test limitation, not an atomic compare-delete guarantee.
- The submitted safety state remains `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

## Decision boundary

I18 R10 is **ACCEPTED for this bounded local-test slice only**. This does not close Gate 3 or authorize live operations, production keys, Resolve IPC, or later gates. Gate 3 closeout still requires the separately authorized I19 integration and closeout review.

No tests were rerun, and no Resolve IPC, production key access, network, or external process was used during this review.
