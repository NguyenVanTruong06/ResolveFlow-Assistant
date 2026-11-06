# Gate 6 DEP-04-UPSTREAM-R1 Revision R2 — Static Design Review

**Workorder:** G07-07B7-GATE6-DEP04-UPSTREAM-R1 — Trusted Provenance & Dispatcher Recovery Boundary Design  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static document and source-contract review only. No code, tests, harness, host, production path, key, socket, or Resolve runtime was accessed or executed.

## Artifact integrity

All submitted hashes match the workspace:

- `docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md`: `02e37521ad5731a20feb605d99c197d715b49e73ec774b4744e5808f169bed2e`
- `.bridge/report.md`: `e5ea154e092c0152c997ca1eea5d63aa1160f8e62a4ec0bc3185cb40ad481799`
- `.bridge/task.md`: `199162d1096ce221cd170eb7a503d757abd55884d78772eda9f3dfae70da5b43`

## Findings

### DEP04-UPSTREAM-R1-R2-01 — Terminal notification replay after an ambiguous send violates DEP-03 — BLOCKER

R2 adds `NOTIFY_ORACLE_INTENT`, which distinguishes a definite non-send before that intent from a possible send after it. However, Boundary 5B still directs restart recovery to “Re-send” the same terminal notification and allows one idempotent attempt (`design §3.4, line 315`). The accepted DEP-03 workorder explicitly prohibits automatically retrying terminal notification after an ambiguous outcome. A persisted intent proves only that the send was about to be attempted; it cannot establish whether the Oracle received it or committed the terminal record before the process lost its acknowledgement.

Repeating the request with the same IDs remains a retry. It may return `TERMINAL_ALREADY_RECORDED`, but that does not override the DEP-03 no-retry rule or establish whether a prior timeout was safe to repeat. The required fail-closed behavior is to retain the target lock and mark local state in-doubt when delivery is ambiguous, with no second request.

**Required remediation:** Keep `NOTIFY_ORACLE_INTENT` to distinguish definitely unsent from possibly sent. Permit a single send only when durable state proves no send was attempted. For any state after a possible send without a recorded acknowledgement, do not resend; retain the target lock/in-doubt condition and leave resolution to an explicitly authorized future reconciliation procedure. Do not claim that idempotency converts the prohibited retry into a first attempt.

### DEP04-UPSTREAM-R1-R2-02 — Boundary 1 relies on an Oracle expiry that the accepted durable ledger does not implement — BLOCKER

Boundary 1 says an orphan `VERIFIED_VALID` execution record “expires after `valid_until_iso`” and therefore needs no terminal notification (`design §3.4, line 310`). The accepted `OracleExecutionRecord` schema in `src/services/durable_nonce_ledger.py:127–150` contains `verified_at_iso` but no `valid_until_iso` or expiry state. `record_oracle_execution()` durably appends the record and adds the execution ID to `_verified_execution_ids` (`:675–719`); the accepted API provides retrieval of the record, but no expiry/removal or orphan-reconciliation transition (`:856–868`). A response's `valid_until_iso` is not a deletion or expiry rule for the durable Oracle record.

Consequently, a crash after Oracle verification but before local intent leaves a permanent verified, nonterminal Oracle entry under the accepted implementation. The current wire API offers no way for the Dispatcher to discover such orphan entries, and the design's “no local execution record to send notification” does not resolve the durable Oracle state.

**Required remediation:** Treat the orphan as a persistent unresolved state unless an accepted API provides explicit, durable expiry or reconciliation semantics. Report the missing Oracle orphan-discovery/terminalization capability as an upstream dependency and define a separately reviewed contract/workorder if it must be resolved. Do not infer ledger expiry from authorization or response validity timestamps.

### DEP04-UPSTREAM-R1-R2-03 — Follow-on DAG does not gate live execution on unresolved trust-root provisioning — HIGH

R2 correctly labels Gate 4 signing and Gate 5 Tech Lead trust-root bootstrapping as unresolved, blocking dependencies (`design §§2.1, 2.3, 2.5`). However, the proposed DAG proceeds from contracts, adapter decoupling, and host DAC to harness realignment and then a live execution directive (`§4.1`); the listed contracts workorder adds DTOs/tests only, and the host workorder does not establish either signing authority or its trust bootstrap (`§4.2`). The DAG therefore implies a route to live execution that its own dependency table says cannot be authorized.

**Required remediation:** Mark the DAG terminally blocked after the design/contract stages unless and until the missing authority owners and trust-bootstrap mechanisms are approved. Add explicit bounded prerequisites/workorders and allowlists for those decisions only when a legitimate authority and custodian are identified; otherwise state that no execution workorder can follow. Do not treat contract definitions alone as provisioning a signing authority.

## Prior finding disposition

- **DEP04-UPSTREAM-R1-01:** The enum and unsupported-reconciliation-protocol corrections are accepted. R2 correctly restricts terminal states to `COMPLETED`, `ABORTED`, `IN_DOUBT` and response statuses to `ACCEPTED`, `TERMINAL_ALREADY_RECORDED`, `REJECTED`, `FAILED`.
- **DEP04-UPSTREAM-R1-03:** The design removes the invented preflight service principal and marks Gate 4/Gate 5 trust roots unresolved. This is directionally correct; the DAG must remain blocked until those dependencies are genuinely resolved as stated above.

## Decision and safety state

Revision R2 is **not accepted** as an implementation-authorizing design. The terminal enum corrections and principal-boundary clarification are retained, but the retry rule, assumed Oracle-record expiry, and DAG ordering remain incompatible with accepted constraints.

No tests, scripts, candidate harness, imports, compilation, socket operations, Resolve calls, host provisioning, or production key/state access were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
