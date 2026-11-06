# Gate 3-I18 Revision R3 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R3 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **360 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is treated as submitted evidence; no independent test run was performed.

## Findings

### I18-R3-01 — Path-based AF_UNIX bind and chmod fallback can escape the pinned directory

**Priority:** High  
**Location:** `src/services/trial_authorization_signer_service.py:6733-6758`

R3 correctly retains `parent_dir_fd` and uses it for the lock, stale-socket removal, descriptor-relative metadata checks, and normal cleanup. However, socket creation still calls `sock.bind(str(self._socket_path))`. The descriptor-relative check at lines 6738–6745 detects a mismatch only after that pathname has already been resolved and the socket node created. If a parent component is swapped between validation and `bind`, the bind can create the socket under the replacement path; the code then closes the socket and cleans up only relative to the pinned original directory, leaving the newly created node at the replacement path. The new regression test checks that `start()` raises, but does not assert that no socket artifact was created outside the pinned directory.

There is also an absolute-path fallback at line 6756 after descriptor-relative `chmod` raises. This reintroduces unanchored path resolution at the point where mode 0660 is applied and can act on a replacement pathname instead of the verified directory entry.

**Required change:** Make binding and mode-setting satisfy the pinned-directory invariant, and ensure every failure path removes only the server-created socket node without touching an unverified path. If the supported AF_UNIX API cannot bind safely to the retained directory entry, stop and report that platform/API dependency rather than relying on post-bind detection or the path-based chmod fallback. Extend the swap regression to assert the replacement/outside directory receives no socket node or other mutation.

### I18-R3-02 — Boolean TTL passes the numeric parameter schema

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6281-6284, 6404-6412`

The `request_challenge` schema declares `ttl_seconds` as `(int, float)`, and validation uses `isinstance(val, expected_type)`. In Python, `isinstance(True, int)` is true, so canonical JSON `true` passes this protocol validation and is forwarded to the protected challenge handler as a TTL. This contradicts the schema's numeric TTL intent and the workorder requirement to reject invalid parameter types before calling protected handlers.

**Required change:** Use an exact numeric-type gate for TTL that explicitly excludes `bool` (for example, `type(value) in (int, float)` plus finite/range validation, or the accepted challenge API's exact validator before handler invocation). Add a regression proving `ttl_seconds: true` is rejected and the challenge handler is not called.

## Findings addressed in R3

- **I18-R2-02:** Per-method parameter schemas now reject missing, unknown, prohibited override, and mismatched-type fields before handler invocation. The added regression asserts protected handlers remain uncalled for invalid requests.
- **I18-02 / I18-03:** Persistent lock inode handling and structured cleanup reporting remain present in the reviewed source.

I18-R2-01 is materially improved by retaining the verified parent descriptor, but I18-R3-01 remains open because bind and fallback chmod still resolve an absolute pathname.

## Safety state

This review performed no live operation. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. I18 is **not accepted** pending remediation and review.
