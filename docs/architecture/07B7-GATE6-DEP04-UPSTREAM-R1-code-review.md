# Gate 6 DEP-04-UPSTREAM-R1 — Trusted Provenance & Dispatcher Recovery Boundary Design Review

**Workorder:** G07-07B7-GATE6-DEP04-UPSTREAM-R1  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static document and source-contract review only. No code, tests, harness, host, production path, key, socket, or Resolve runtime was accessed or executed.

## Artifact integrity

The submitted hashes match the workspace:

- `docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md`: `f3d19487e520a1f9a11c409d55cc10e703a2ef8113330dda8b68ee6b62be7b60`
- `.bridge/report.md`: `6ca6845cf5ba236822c666014832321fed5ef8c7ce17955aed92717255d554b4`
- `.bridge/task.md`: `199162d1096ce221cd170eb7a503d757abd55884d78772eda9f3dfae70da5b43`

## Findings

### DEP04-UPSTREAM-R1-01 — Proposed Oracle terminal protocol contradicts accepted DEP-02C contracts — BLOCKER

The design says it reuses the accepted Oracle terminal-notification protocol, but proposes values and APIs that the accepted contracts do not support:

- `src/core/contracts/oracle_ipc_contracts.py` permits terminal states only `COMPLETED`, `ABORTED`, and `IN_DOUBT` (`VALID_TERMINAL_STATES`, lines 542–546). The design's crash matrix sends terminal state `FAILED` (design §3.5, line 457), which cannot be represented by `OracleTerminalNotificationRequest`.
- The accepted response-status set is `ACCEPTED`, `TERMINAL_ALREADY_RECORDED`, `REJECTED`, and `FAILED` (`VALID_TERMINAL_RESPONSE_STATUSES`, contract lines 548–553). The design's sequence diagrams require response status `SUCCESS` (design §3.4), which is not valid.
- The proposed `OperatorReconciliationConsentEnvelope` and `ReconciliationClearanceRecord` (design §3.5, lines 461–467) are absent from the accepted DEP-02C protocol. The listed contracts workorder covers only preflight and execution-workorder contracts; it does not introduce or review a reconciliation-clearance contract or endpoint.

These are not implementation details: they make the specified dispatcher/oracle exchanges unrepresentable or cause DTO validation to reject them, and leave the proposed unlock authority undefined.

**Required remediation:** Reconcile every message, enum, acknowledgement, and recovery operation against the checked-in DEP-02C DTOs. Use only the accepted terminal states/statuses unless a separately reviewed contract workorder adds a new schema and endpoint. Add a bounded, reviewed reconciliation protocol workorder before specifying any Oracle-issued clearance or unlock flow. Do not implement an ad hoc second protocol.

### DEP04-UPSTREAM-R1-02 — Crash matrix is inconsistent with Oracle verification durability and no-retry rules — BLOCKER

The design's recovery matrix does not match the accepted DEP-03/DEP-02C ordering:

- `LiveCanaryDispatchService.dispatch_live_canary()` sends the Oracle verification request and receives/verifies its response before writing local `DISPATCH_ATTEMPT_INTENT`, target reservation, or `ACTION_PENDING` (`src/services/live_canary_dispatch_service.py:835–869`). The Signer records the verified execution durably while creating its positive response (`src/services/trial_authorization_signer_daemon.py:958–969`). Therefore a crash can occur after Oracle verification but before the first local intent; that state is missing from the matrix. The matrix's “Pre-Reservation” and “Post-Reservation, Pre-WAL” rows incorrectly say the Oracle was not notified / execution ID uncommitted (design §3.5, lines 454–455).
- The `Post-Receipt, Pre-Oracle Notification` row says restart should replay a terminal notification (design §3.5, line 459), but no durable notification-attempt state is specified to distinguish “definitely not sent” from “sent but acknowledgement lost.” Retrying after the latter is an ambiguous outcome and conflicts with DEP-03's no-automatic-retry rule.
- For mutation-time ambiguity, the design records `FAILED` at the Oracle (line 457) instead of the accepted `IN_DOUBT` terminal state.

Without these transitions, a consumed/verified execution can remain nonterminal at the Oracle, be incorrectly described as reusable, or receive a forbidden retry after an ambiguous send.

**Required remediation:** Redo the crash-boundary matrix from the actual source order, beginning with Oracle verification before local persistence. Specify a durable write-ahead terminal-notification intent and an unambiguous rule for whether one send is permitted after restart; if send outcome cannot be proven, retain the local lock/in-doubt state and do not retry. Define matching Oracle terminalization for every already-verified authorization using only accepted states.

### DEP04-UPSTREAM-R1-03 — Evidence signing principals and key custody are asserted without an established trust root — BLOCKER

The design presents new key owners and filesystem trust roots as established architecture even though its source audit identifies no existing contracts or deployed principal for them:

- Gate 4 is assigned a `Preflight Service Identity` and private key at `/etc/resolveflow/preflight/preflight.pem` (design §2.1, line 50), but DEP-01's principal model and the listed DEP-02A configuration provide Signer, Dispatcher, and interactive-user principals; they do not provision a preflight service principal or its key. The document itself says the signed preflight contract/key is absent (§2.3, lines 97–141).
- Gate 5 relies on a pinned Tech Lead public key at `/etc/resolveflow/tech_lead_public_key.pem` (§2.1, line 52), and the Oracle public key is alternately located under `/etc/resolveflow` and the Signer state tree (§§2.1, 3.1, 3.7). No accepted provisioning/trust-anchor mechanism establishes these paths, ownership, initial pin, rotation, or recovery.
- The Dispatcher is also said to sign its own proof/receipt (§3.2 table), but no Dispatcher signing-key custody, trust registration, or verification contract is defined.

This leaves release authority and preflight provenance dependent on newly asserted identities/keys, contrary to the workorder's instruction not to invent a trust source. It also leaves ambiguity about how the three-principal boundary is preserved.

**Required remediation:** Mark each absent issuer, key, and provisioning mechanism as an explicit dependency rather than an established path. Identify the actual accountable authority and trust-bootstrap owner, reconcile all key paths and DAC with DEP-01/DEP-02A, and decide whether Gate 4 signing runs under an existing principal or requires a separately approved principal. Do not issue implementation workorders for signing or verification until that trust root is approved.

## Decision

The design is **not accepted**. The provenance taxonomy and dispatcher-keystore separation are useful directions, but the terminal protocol, recovery state machine, and trust-root assignments require correction before they can serve as implementation authority. The proposed follow-on implementation DAG should not be activated from this revision.

No tests, scripts, candidate harness, imports, compilation, socket operations, Resolve calls, host provisioning, or production key/state access were performed during review. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
