# Gate 6 DEP-01 Architecture Design Re-review — R4 Amended

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01-R4 — Oracle Socket GID Ownership Design Reconciliation (amended)`
- **Workorder SHA-256:** `3cf444615caf157f51af76a5732f5ed797357e23f83191308e5d7dbf13ec086c`
- **Reviewed design:** `docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md` (SHA-256 `8e6f951c2cf7f698635b20c8743514b8e9dacb28ec3de4f87798bf8c146398a3`)
- **Report SHA-256 at review:** `a3ed9ad28bc935a1062b75c4bfa1cdf4befc4b0b3a78fb7436895c933f759838`
- **Review mode:** Static document review only. No source, configuration, host, socket, systemd, key, test, or Resolve operation was performed.

## Decision summary

The amended design resolves the prior finding DEP01-R4-01: each endpoint now has a persistent descriptor-anchored singleton lock, acquired nonblocking before stale-socket inspection, and the lock inode is retained after shutdown. The exact-child policy also accounts for lock files. One access-control defect remains: lock files are specified as mode `0660`, which grants group members write access and permits them to acquire the same advisory lock, contradicting the access matrix and allowing a peer process to deny daemon startup.

## Finding

### DEP01-R4A-01 — MEDIUM — Group-writable singleton locks allow peer lock denial

**Evidence:** The filesystem tree specifies all three lock files as `0660` (design lines 135, 139, 143). The access matrix then describes group members as read-only at lines 154, 157, and 160. Under POSIX mode semantics, `0660` grants the owning group both read and write (`rw-`); the matrix's `r--` entries are inaccurate. In particular, the dispatcher group can open and take `flock` on `signer_oracle.lock`, and members of `resolveflow_clients` can do so for the two client lock files.

**Impact:** A process with membership in the socket directory's group can acquire `LOCK_EX | LOCK_NB` on the persistent lock inode before the daemon starts. The legitimate daemon then fails with `ERR_DAEMON_ALREADY_RUNNING`. This permits denial of service by principals that are intentionally allowed only to connect to the corresponding socket. Group write permission is not needed for socket connection and grants an unintended lock-acquisition capability.

**Required correction:** Make lock-file access exclusive to the daemon owner (recommended mode `0600`) while preserving the directory-inherited GID if desired. Update the tree, lock creation/open mode and post-open `fstat` requirements, access matrix, and DEP-02A-DAC-REALIGN exact-child verification policy consistently. State explicitly that non-owner group members cannot open or lock the singleton inode. If a different mode is required, justify how peer lock acquisition is prevented; correcting only the matrix is insufficient.

## Remediations confirmed

- Per-endpoint singleton lock paths, UID/GID ownership, descriptor-relative open, regular-file validation, and nonblocking exclusive `flock` are specified.
- Lock contention fails closed before socket inspection or mutation.
- The lock descriptor is held for the daemon lifetime and the persistent lock file is not unlinked.
- Stale-socket cleanup is ordered after lock acquisition, and the DAC realignment policy accounts for lock entries.

## Required disposition

Revise the design and its follow-on DAC verifier requirements to remove group lock-acquisition access and make the mode/access descriptions consistent. Do not proceed to DEP-02B until this design finding and the required configuration/verifier reconciliation are accepted.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No source or configuration changes, tests, host operations, sockets, keys, systemd operations, or Resolve activity occurred during review.
