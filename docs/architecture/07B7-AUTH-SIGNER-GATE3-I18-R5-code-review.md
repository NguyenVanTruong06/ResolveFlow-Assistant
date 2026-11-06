# Gate 3-I18 Revision R5 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R5 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **362 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This remains submitted evidence; this review did not independently rerun the suite.

## Finding

### I18-R5-01 — Socket ownership checks are separated from the filesystem mutations they authorize

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6792-6812, 6926-6984, 6756-6766`

R5 records `(st_dev, st_ino)` from a descriptor-relative `stat()` after `bind()`, and `_unlink_owned_socket_node()` checks type and identity before unlinking. However, each check and its consequential operation are separate name-based operations:

1. After `bind()`, another process with access to the test directory can replace the socket entry before the first `stat()`. If the replacement is itself a socket, the server records the replacement's identity as `_bound_socket_identity`, chmods it, and later treats it as its own entry.
2. During cleanup, an entry can be replaced after `_unlink_owned_socket_node()` verifies its type and `(st_dev, st_ino)` but before `os.unlink()`. The unlink then removes the replacement. The stale-entry path has the same `stat()`-then-`unlink()` gap.

The sequential replacement tests cover an entry replaced before cleanup begins, but do not exercise either race between identity check and operation. Thus the implementation does not establish that the entry being chmodded/unlinked is the same inode that was checked or created. This leaves I18-R4-01 open under concurrent same-UID filesystem mutation, even though all operations are now anchored to the correct directory descriptor.

**Required change:** Establish ownership in a way that cannot adopt a replacement entry after bind, and ensure cleanup cannot unlink a different entry after its identity check. Add deterministic race-injection tests at the post-bind/pre-identity and post-identity/pre-unlink boundaries, including a replacement socket node. If the available filesystem/API cannot provide the required conditional operation, stop and report that dependency rather than claiming ownership based on a non-atomic check-then-act sequence.

## Remediations verified

- The R4 path-anchoring changes remain: AF_UNIX bind uses `/proc/self/fd/<parent_fd>/<leaf>`, pre-bind parent identity is compared with the pinned descriptor, and no unanchored chmod fallback remains.
- TTL validation rejects booleans and non-positive/non-finite values before invoking the challenge handler.
- The I18-R4-01 implementation now avoids unconditional basename deletion on bind failure and preserves entries already observed to be replaced before cleanup.

These reduce the prior risks but do not close the check/use race in I18-R5-01.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
