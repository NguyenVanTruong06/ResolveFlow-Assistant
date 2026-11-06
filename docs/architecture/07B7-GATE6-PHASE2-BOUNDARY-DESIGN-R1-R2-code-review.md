# Gate 6 Phase 2 Governance Boundary Design R1 R2 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1` Revision R2  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation and accepted-source cross-check only. No host, key, socket, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace:

- Design: `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` — `de6089a73ef63065cded53f62ce9a73f827b1019b10adf6550ef755b0b3797d2`
- `.bridge/task.md` — `58340f6d0fbf41d5b0f967952f617681655bfdc691096bdcc672be03f7dfc504`
- `.bridge/report.md` before this review entry — `c3356f8d490936ae7accf5339998b4f1d73aceb1e78ea8df18e93a2389c03e93`

## Findings

### PHASE2-BOUNDARY-R2-01 — Blocker D names the wrong accepted Signer client socket — HIGH

The Phase 2 design's authority/DAC matrix and D checklist use `/run/resolveflow/signer_client/signer_client.sock`. Accepted DEP-01 R4 §2.2.2 and its endpoint inventory specify `/run/resolveflow/signer_client/signer_public.sock` (with `signer_public.lock`). The wrong basename makes the proposed exact-child verifier reject the accepted endpoint or verify a node that the accepted daemon does not create. Correct every occurrence and re-check the exact-child names against DEP-01 and the accepted DEP-02A-DAC-REALIGN artifact/review.

### PHASE2-BOUNDARY-R2-02 — Orphan record is incorrectly described as a target-level Oracle lock — BLOCKER

Section 3.1 says an orphan `OracleExecutionRecord` permanently locks the target timeline at the Oracle and causes later canaries to fail with `ERR_TARGET_LOCKED_OR_IN_DOUBT`. The accepted `OracleExecutionRecord` schema and `DurableNonceLedger.is_execution_verified()` track/deny reuse of the same `execution_id`; the record does not create a target-level lock or index targets as locked. In `TrialAuthorizationSignerDaemon`, verification checks whether that execution ID was already verified and rejects it as already verified. Local target lock state is owned by the Dispatcher journal/ledger. Do not infer Oracle target-lock behavior from the per-execution replay ledger. Reword the risk precisely as permanent consumption of that execution ID and separately describe the local target-lock/recovery state supported by accepted code. Ground any target-level denial in an accepted API/source path.

### PHASE2-BOUNDARY-R2-03 — Proposed trust-root directory is falsely attributed to DEP-01 — MEDIUM

Section 4.3 calls `/etc/resolveflow/trusted_roots/` “as established in DEP-01 §2.1 for `oracle_public_key.pem`.” The accepted DEP-01/DEP04 design specifies the Oracle public key at `/etc/resolveflow/oracle_public_key.pem`; it does not establish the `trusted_roots/` directory or Gate 4/Gate 5 key filenames there. The document appropriately labels a broader trust-root publication model unresolved, but must not cite the new directory as an accepted fact. Mark it solely as an unapproved proposal/dependency and distinguish it from the accepted Oracle-key path; any change requires its own architecture decision/workorder.

## Verification boundary and decision

R2 closed the previous findings about adding an OS principal, the GID/mode/socket-directory model, and placing verifier keys inside Signer-private `0700` state. The design still preserves WO5's zero-incoming-edge disconnection and states that prerequisites remain unresolved.

R2 is **not accepted** until the source-grounded endpoint, Oracle ledger semantics, and trust-root attribution are corrected. No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
