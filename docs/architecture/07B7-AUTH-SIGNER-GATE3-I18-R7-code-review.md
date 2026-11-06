# Gate 3-I18 Revision R7 — Static Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I18 — Operator Console & Signer Socket Server Interface  
**Decision:** `CHANGES_REQUIRED`  
**Review scope:** Static review of submitted R7 artifacts and submitted authorized test evidence. Tests were not rerun.

## Evidence checked

All submitted SHA-256 values match the current files, including `.bridge/task.md`, `.bridge/report.md`, both operator-console files, both signer-service files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I18/pytest.log`.

The submitted log records **369 passed, 0 failed, 2 warnings**. The warnings are Python multiprocessing `fork()` deprecation warnings. This is submitted evidence; no independent test run was performed.

## Findings

### I18-R7-01 — The pinned socket-node descriptor is closed before chmod

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:6991-7008`

R7 opens `sock_node_fd` and compares its `fstat()` identity with the descriptor-relative `stat()` after the loopback handshake. It then records the proven identity and closes `sock_node_fd` before calling `os.chmod(self._leaf_filename, ..., dir_fd=...)`. That chmod performs a fresh basename lookup. A concurrent replacement after the identity comparison but before chmod can therefore cause the chmod to modify a foreign socket. The subsequent post-chmod stat notices an identity change only after the foreign entry's mode has already been changed.

**Required change:** Apply mode 0660 to the same inode whose ownership was proven, without releasing the pinned identity between verification and mutation. If the supported platform API cannot chmod that pinned inode safely, fail closed before a path-based chmod and report the API dependency. Add a deterministic hook between the final identity comparison and chmod; replace the entry with a distinct socket and assert the foreign socket's mode and identity remain unchanged.

### I18-R7-02 — The tombstone name can be replaced after verification and before unlink

**Priority:** Medium  
**Location:** `src/services/trial_authorization_signer_service.py:7184-7228` (and the equivalent stale-cleanup path in `start()`)

Tombstone restoration now uses `renameat2(RENAME_NOREPLACE)` and protects a recreated destination from overwrite. However, the successful-owner path still performs `stat(tombstone)` followed by `unlink(tombstone)` as separate operations. A concurrent writer can replace the tombstone entry after it is verified but before `unlink`; cleanup will then delete the replacement. The injected collision tests recreate the original leaf while the candidate is under the tombstone, but do not replace the tombstone name itself between its verification and unlink. Stale cleanup also verifies the tombstone is a socket and then unlinks it by name.

**Required change:** Ensure the tombstone entry itself cannot be swapped between ownership verification and deletion, or stop and report the dependency if the stated concurrent-writer threat model cannot be supported by the available filesystem API. Add deterministic race-injection coverage at the post-tombstone-stat/pre-unlink boundary for both owned-socket cleanup and stale cleanup, proving a replacement entry is never removed.

## Findings addressed in R7

- **I18-R6-01:** The `O_PATH` descriptor now pins the socket inode across the nonce handshake and post-probe stat; a post-probe replacement detected before ownership registration is rejected.
- **I18-R6-02:** Tombstone restoration uses `renameat2(RENAME_NOREPLACE)` and preserves the moved foreign entry under a unique name if the original basename is concurrently recreated.

These changes close the specific R6 findings. The remaining findings above concern later mutation windows after those checks.

## Safety state

No live operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve IPC, production key access, network, or external process was used during this review.
