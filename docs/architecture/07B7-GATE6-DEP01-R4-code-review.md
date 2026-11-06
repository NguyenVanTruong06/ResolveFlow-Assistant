# Gate 6 DEP-01 Architecture Design Reconciliation Review — R4

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01-R4 — Oracle Socket GID Ownership Design Reconciliation`
- **Workorder SHA-256:** `3cf444615caf157f51af76a5732f5ed797357e23f83191308e5d7dbf13ec086c`
- **Reviewed design:** `docs/architecture/07B7-GATE6-DEP01-live-dispatch-oracle-design.md` (SHA-256 `3eafcbb6032b25ae67227816d7785ddfd0aac187d66d77f719b7212baed8bc7a`)
- **Report SHA-256 at review:** `ac5f58bb875aa6fbcc98c3ee771345ec4d991a3668b361dd0493265324a7128d`
- **Review mode:** Static document review only. No source, configuration, host, socket, systemd, key, test, or Resolve operation was performed.

## Decision summary

R4 resolves the socket-GID ownership mismatch by selecting Linux setgid socket directories (`02750`) while preserving zero supplementary groups for both daemons. It specifies descriptor-relative stale-node inspection, foreign-node refusal, post-bind GID/mode checks, and a DEP-02A DAC realignment before DEP-02B. One lifecycle gap remains: the stale-node protocol can unlink a live socket belonging to another concurrently running instance of the same daemon identity because no singleton lock is acquired before inspection and unlink.

## Finding

### DEP01-R4-01 — MEDIUM — Stale-socket cleanup can unlink a live same-principal endpoint

**Evidence:** Section 2.3.1 inspects the socket basename and unlinks it when it is a socket with the daemon's effective UID and expected GID (design lines 173–181). No stable per-daemon lock or other active-instance exclusion is required before this operation.

**Impact:** If a second signer or dispatcher instance starts while the first is live, both run under the same service UID. The second instance classifies the first instance's active socket as an owned stale node and unlinks its pathname before binding. Existing clients can lose the endpoint, and new clients can be redirected to a different instance. UID/GID/type checks cannot distinguish a crashed stale socket from a live socket owned by the same service principal.

**Required correction:** Specify and acquire a per-daemon singleton lock before stale-node inspection, unlink, bind, chmod, or shutdown cleanup. The lock must use a stable inode and nonblocking exclusive `flock`; hold it for the complete socket lifetime and do not unlink the lock file on shutdown. Define its path, owner, group, mode, descriptor-relative open/validation, and fail-closed behavior for lock contention. Include the lock-file entry in the runtime directory exact-child policy and in the DEP-02A-DAC-REALIGN allowlist/verifier policy. An already-active socket must cause startup to fail without unlinking or replacing it.

## Remediations confirmed

- The design selects `02750` setgid directories for all three socket directories and explains expected inherited socket GIDs 902/903/903.
- Both daemons retain primary GIDs 901/902 with zero supplementary groups.
- Oracle peer authorization remains restricted to dispatcher UID 902.
- The roadmap requires DEP-02A-DAC-REALIGN before DEP-02B.
- The design preserves the prohibition on host provisioning, systemd operations, key access, and live Resolve operations at this stage.

## Required disposition

Revise the design to specify singleton locking and include its runtime entry in the subsequent DAC verification/configuration workorder. Do not proceed to DEP-02B until the design and realigned verifier/configuration artifacts are accepted.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No source or configuration changes, tests, host operations, sockets, keys, systemd operations, or Resolve activity occurred during review.
