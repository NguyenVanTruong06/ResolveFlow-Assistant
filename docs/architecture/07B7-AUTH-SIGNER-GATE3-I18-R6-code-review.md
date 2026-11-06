# Gate 3-I18 Revision R6 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R6 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **364 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; no independent test run was performed.

## Findings

### I18-R6-01 — The loopback proof is not bound to the inode recorded as owned

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6812-6856`

The nonce handshake connects to `bind_target` and proves that the connection reached a listener at the instant of the probe. The code then separately stats `_leaf_filename` and records that entry's `(st_dev, st_ino)` as `_bound_socket_identity`. A concurrent writer can replace the leaf after the successful handshake but before this stat. If the replacement is another socket, the server records the foreign socket's identity as its own, applies mode 0660 to it, and later cleanup treats it as its own entry. The probe test injects replacement immediately after bind, before the probe, so it does not cover replacement after successful probe but before the ownership stat.

**Required change:** Bind the ownership decision to the same socket node proved by the challenge, with no replaceable-name interval between proof and recording identity. Add a deterministic hook/test after successful nonce exchange and before the descriptor-relative stat, injecting a distinct listening socket; the server must fail closed, preserve the foreign node, and never record/adopt its identity. If the API cannot provide that guarantee under the stated concurrent-writer model, report the dependency or narrow the claimed threat boundary explicitly rather than treating the handshake as inode ownership proof.

### I18-R6-02 — Tombstone restoration can overwrite an entry created while the basename is vacant

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6761-6779, 7005-7057`

Stale cleanup and `_unlink_owned_socket_node()` move the basename to a tombstone with `os.rename()`, inspect the tombstone, then restore it with another ordinary `os.rename(tombstone, leaf, ...)` when it is foreign or inspection fails. POSIX rename replaces an existing destination. If another writer creates a new entry at `leaf` while the candidate is isolated under the tombstone name, the restore can overwrite and remove that newly created entry. The current pre-unlink injection happens before the first rename and does not exercise a destination collision during restore.

**Required change:** Restore with a no-replace operation and preserve both entries if the destination has been recreated; report a structured conflict without overwriting either entry. Add deterministic tests that create a new regular file and a new socket at `leaf` while the candidate is under the tombstone, then verify neither foreign entry is lost. Apply the same rule to stale cleanup and owned-socket cleanup. If no supported atomic no-replace restore is available, stop and report that dependency rather than claiming restoration is race-safe.

## Remediations verified

- **I18-R5-01:** R6 adds a nonce loopback probe, tombstone isolation, and two deterministic injection tests. These improve detection and cleanup behavior but do not close the post-probe/pre-stat adoption window or destination-overwrite race described above.
- Descriptor-relative directory anchoring, strict method parameter schemas, TTL validation, and structured cleanup errors remain present in the reviewed code.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
