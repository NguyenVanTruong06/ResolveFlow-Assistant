# Gate 6 DEP-01 Architecture Design Re-review — R4 Amended V3

- **Review date:** 2026-11-05
- **Decision:** `ACCEPTED_FOR_IMPLEMENTATION_WORKORDER_ONLY`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01-R4 — Oracle Socket GID Ownership Design Reconciliation (amended V3)`
- **Workorder SHA-256:** `3cf444615caf157f51af76a5732f5ed797357e23f83191308e5d7dbf13ec086c`
- **Reviewed design:** `docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md` (SHA-256 `41e22a87448c899ce8e1b1cbd9789bcb20bf3af3309c8958373bc9ec015c4123`)
- **Report SHA-256 at review:** `1e518ae4fde62e3e8e683b83703df348d883cf6db5d61873b1857c84ed47f615`
- **Review mode:** Static document review only. No source, configuration, host, socket, systemd, key, test, or Resolve operation was performed.

## Decision summary

Accepted for implementation-workorder planning. The design resolves DEP01-R4V2-01 by requiring descriptor-relative open followed immediately by `fstat` identity/type checks before any mutation. It rejects unexpected owner, group, or non-regular inode without changing or unlinking the node, then independently requires exact mode `0600` and fails closed on mismatch without `fchmod`. Only a validated lock inode proceeds to nonblocking exclusive `flock`.

The prior singleton-lock findings are also resolved: lock files are daemon-owner-only (`0600`), peer principals cannot open or acquire them, the inode persists across daemon shutdown, and socket cleanup follows successful lock acquisition. The exact-child policy includes the lock-file states and checks.

## Review disposition

- `DEP01-R4-01`: Resolved by persistent singleton locking before stale-socket cleanup.
- `DEP01-R4A-01`: Resolved by consistent `0600` lock mode and denial of group/other access.
- `DEP01-R4V2-01`: Resolved by validation-first ordering and fail-closed mode verification without inode mutation.

## Scope and limits

This decision accepts the architecture document for issuing bounded implementation workorders only. It does not accept implementation artifacts, authorize host provisioning, systemd lifecycle operations, daemon startup, key access, live Resolve calls, dispatch, or Gate 6 execution. DEP-02A-DAC-REALIGN remains a required prerequisite before DEP-02B.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- Static document review only; no source changes, tests, host operations, sockets, keys, systemd operations, or Resolve activity occurred.
