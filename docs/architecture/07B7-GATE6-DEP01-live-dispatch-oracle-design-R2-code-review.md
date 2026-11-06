# Gate 6 DEP-01 Live Dispatcher & Oracle Boundary Design Review — R2

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Reviewed workorder:** `G07-07B7-GATE6-DEP01 — Live Dispatcher & Oracle Boundary Design`
- **Workorder SHA-256:** `9fb570d7551108de234e7c56e96f4637ea3b3a2e8965c1d8ca90173d746c3f32`
- **Reviewed design revision:** R2
- **Design SHA-256:** `314e6b1297cf8f0c3519681165fff9973038dc04a735bbf387bd9c74f0dd0d80`
- **Submission report SHA-256 at review:** `a5d32eca5bdec58f5cc705a5ea54c1b6072afd8883985a5e7cb06f2b40dbcd22`
- **Review mode:** Static design review only. No tests, Resolve imports/connections, IPC, harness, key access, or live operations were performed.

## Decision summary

R2 resolves the four prior findings: wire correlation fields are now consistently named and the signature claims are enumerated; the three-principal access matrix separates client and oracle paths; durable execution state and restart recovery are described; and the follow-on workorders now list concrete paths. The design still has two implementation-blocking inconsistencies in the filesystem permissions and durability protocol. Until corrected, it cannot safely serve as the implementation contract.

This decision does not authorize provisioning, implementation, or Gate 6 execution. Gate 6 remains `NOT ACCEPTED` and incident-only.

## Findings

### DEP01-R2-01 — HIGH — Dispatcher cannot bind its client socket under the specified DAC

**Evidence:** The design requires `_resolveflow_dispatcher` to bind `/run/resolveflow/client/dispatcher.sock` (§2.3, line 149). The parent directory `/run/resolveflow/client/` is owned by `_resolveflow_signer:resolveflow_clients` with mode `0750` (§2.2, lines 111–112). The access matrix grants the dispatcher only `r-x` traversal, via a stated ACL/group, on that directory (§2.2, line 124); it grants no write permission. Creating, replacing, or unlinking a socket entry in a directory requires write and execute permission on that directory. The dispatcher’s socket ownership (`_resolveflow_dispatcher:resolveflow_clients`) does not grant it parent-directory write permission.

**Impact:** A deployment following this matrix cannot perform the specified dispatcher socket bind (and cannot safely clean up its socket entry). The dispatcher service therefore cannot start as designed. Granting broad write access to the shared client directory without revisiting isolation could also allow one service to replace the other service’s socket entry.

**Required correction:** Redesign the endpoint layout/ACL so the dispatcher can create and remove only its own socket entry while the runner can connect to it, and the signer can independently create its public endpoint. Prefer separate signer-client and dispatcher-client subdirectories with explicit owner/group/mode rows, or specify an equally narrow ACL and explain its unlink/replace implications. Update the directory tree, full access matrix, lifecycle steps, and threat model together. Preserve runner denial of the Oracle directory and preserve Plan R7 principal separation.

### DEP01-R2-02 — BLOCKER — Durable reservation omits fsync of ledger file contents

**Evidence:** The required pre-mutation ordering states that `ledger.reserve_target(...)` writes `target_ledger.v2.json` and performs a directory-descriptor `fsync()` (§5.2.2, line 358), then fsyncs the journal WAL before invoking Resolve (§5.2.2, lines 359–362). It does not require `fsync()` on the target-ledger file (or on the temporary file before atomic rename). Similarly, the in-doubt marker is described as written and `fsync()`ed, without specifying file and parent-directory sync ordering (§5.2.1, line 353; §5.2.2, lines 377–381).

**Impact:** A directory `fsync()` persists directory-entry changes; it does not establish that the reservation record’s file contents have reached durable storage. After a crash, a reservation or marker could be absent or incomplete even though the design permitted the Resolve call. That undermines the durable replay prevention and restart recovery guarantees the design relies on.

**Required correction:** Specify and require the complete durable replacement/creation sequence for each authoritative state file: write the full record, `fsync()` the file descriptor, atomically publish/rename as applicable, then `fsync()` the containing directory descriptor; check every error and fail before Resolve mutation unless the reservation and write-ahead journal state are durably committed. For `.in_doubt` markers, require file `fsync()` plus parent-directory `fsync()` and define how recovery handles a partial/missing marker using the durable ledger. State which record is authoritative if the audit log, target ledger, WAL, and signer execution ledger disagree.

## Required disposition

Revise the design to resolve both findings and submit R3 for review. Do not issue DEP-02A/02B implementation or provisioning workorders from this revision. Acceptance of a later design revision may authorize only a separate bounded workorder; it does not authorize Gate 6 execution.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No tests, Resolve SDK imports, Resolve IPC/UI activity, harness creation, key access, or live operations were performed for this review.
