# Gate 6 DEP-01 Live Dispatcher & Oracle Boundary Design Review — R3

- **Review date:** 2026-11-05
- **Decision:** `ACCEPTED_FOR_IMPLEMENTATION_WORKORDER_ONLY`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01 — Live Dispatcher & Oracle Boundary Design`
- **Workorder SHA-256:** `9fb570d7551108de234e7c56e96f4637ea3b3a2e8965c1d8ca90173d746c3f32`
- **Reviewed design revision:** R3
- **Design SHA-256:** `e1a75a9eb8a3498681ff3876932a6ddff9a293c34a08224ea85feecd31793967`
- **Submission report SHA-256 at review:** `f9c24f449bb1fe3fe2381b117077c1981775df5532f9f4d5c3aa0da52a33739c`
- **Review mode:** Static design review only. No tests, Resolve imports/connections, IPC, harness, key access, provisioning, or live operations were performed.

## Decision summary

R3 resolves both findings from the R2 review. Separate signer-client and dispatcher-client directories give each daemon ownership of its own socket entry while preserving runner connect access and denying runner traversal into the Oracle directory. The persistence section now specifies file-content `fsync`, atomic publication, parent-directory `fsync`, pre-mutation ordering, and recovery/authority precedence for the target ledger, WAL, receipts, and in-doubt marker. The wire field names, three-principal matrix, restart/replay rules, and exact follow-on workorder allowlists remain consistent with the prior accepted review scope.

The design is accepted only as an architectural basis for subsequent bounded implementation workorders. It does not establish that the described principals, permissions, services, or live boundary exist, and it does not authorize Gate 6 execution or provisioning by itself.

## Previous findings disposition

- **DEP01-01:** Resolved. The request/response schemas and invariant use `request_id` and `request_nonce` consistently; the Ed25519 signature claim set is enumerated.
- **DEP01-02:** Resolved. The revised directory tree and access matrix separate Oracle, signer-client, and dispatcher-client paths, with each daemon able to manage its own socket and the runner limited to client traversal/connect.
- **DEP01-03:** Resolved at design level. Durable execution IDs, target ledger/WAL states, in-doubt markers, restart recovery, and signer synchronization are specified.
- **DEP01-04:** Resolved. DEP-02A, DEP-02B, DEP-03, and DEP-04 each provide exact proposed artifact paths and dependency order.
- **DEP01-R2-01:** Resolved. The dispatcher has owner `rwx` on its dedicated socket directory; it no longer needs write access to a signer-owned shared directory.
- **DEP01-R2-02:** Resolved. Authoritative state files require file `fsync`, atomic replacement, and parent-directory `fsync` before mutation; the in-doubt marker and restart recovery are included.

## Boundary and authorization limits

- DEP-01 acceptance authorizes only issuance of a separate bounded implementation workorder, beginning with DEP-02A as sequenced in the design.
- Each implementation/provisioning workorder remains subject to its own allowlist, verification, and review.
- DEP-04/Gate 6 execution requires a later independent workorder, fresh Gate 2 authorization, fresh challenge, and all stated preconditions. This review does not authorize those steps.
- The host gap declaration remains controlling: the target OS users, groups, services, directories, and sockets have not been established by this design review.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests, Resolve SDK imports, Resolve IPC/UI activity, harness creation, key access, provisioning, or live operations were performed for this review.
