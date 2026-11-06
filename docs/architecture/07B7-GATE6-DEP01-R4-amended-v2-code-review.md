# Gate 6 DEP-01 Architecture Design Re-review — R4 Amended V2

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01-R4 — Oracle Socket GID Ownership Design Reconciliation (amended V2)`
- **Workorder SHA-256:** `3cf444615caf157f51af76a5732f5ed797357e23f83191308e5d7dbf13ec086c`
- **Reviewed design:** `docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md` (SHA-256 `423f64282b27d6ed256b2d5e336d8b7954f35e2c89ed25e8ea514912dc4c9416`)
- **Report SHA-256 at review:** `1adf48d624adbed93d2b1c466b176a49d29e75f39542f8ede6e6b044dd338031` (as submitted)
- **Review mode:** Static document review only. No source, configuration, host, socket, systemd, key, test, or Resolve operation was performed.

## Decision summary

The design corrects DEP01-R4A-01: lock files are now specified as `0600`, and the access matrix and DAC realignment policy deny group/other access. A sequencing issue remains in the lock-open procedure: it calls `fchmod` before validating the opened inode's type and ownership. This can mutate a pre-existing node that should instead be rejected as foreign or malformed.

## Finding

### DEP01-R4V2-01 — MEDIUM — Lock inode is chmodded before its identity is validated

**Evidence:** Section 2.3.1 states that immediately after descriptor-relative `os.open`, the daemon calls `os.fchmod(lock_fd, 0o600)`, and only afterward calls `os.fstat` to verify regular-file type, service UID, and expected GID (design lines 189–199).

**Impact:** The procedure mutates the inode before establishing that it is the expected daemon-owned regular lock file. For example, an existing openable non-regular node or a regular node with unexpected ownership/group can have its mode changed before the procedure detects the mismatch. That contradicts the fail-closed foreign-node rule and makes the subsequent mode assertion partly self-fulfilling: the code has just forced the value it then checks.

**Required correction:** Specify validation-first ordering. After opening, `fstat` and verify regular-file type, expected owner, and expected GID before any mutation. For an existing node with unexpected mode, preferably fail closed without changing it; if mode repair is intentionally allowed, explicitly constrain it to an already validated daemon-owned regular inode and explain the policy. Then perform a final `fstat` to verify mode `0600` before attempting `flock`. Keep the no-unlink guarantee.

## Remediations confirmed

- Singleton lock paths remain persistent and descriptor-anchored.
- `0600` mode is consistently represented in the directory tree, access matrix, open procedure, and DEP-02A-DAC-REALIGN exact-child requirements.
- Group and other principals are denied lock-file access; the earlier peer lock-denial finding is resolved.
- The lock is acquired nonblocking before socket inspection or cleanup and held for the daemon lifetime.

## Required disposition

Revise the design to validate the opened lock inode before chmod or any other mutation, and clarify whether a pre-existing mode mismatch is rejected or repaired after identity validation. Do not proceed to DEP-02B until this finding and the design are accepted.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No source or configuration changes, tests, host operations, sockets, keys, systemd operations, or Resolve activity occurred during review.
