# Gate 6 Phase 2 Gate 4 Authority Design R6 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R6)
- **Submitted SHA-256:** `09518806d60b72a6d61ef14d089eb4cd5f46c0aff0c93bf35ad4ed79c06adea1` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R6-01 — HIGH: Preflight is irreversibly consumed before Gate 5 authorization

Section 7.2 Step 5 durably consumes `preflight_run_id` and Step 6 declares Gate 4 clearance; only afterward does the flow proceed to Gate 5 workorder verification. A request that contains a valid, unused signed preflight envelope but lacks a valid Gate 5 release record can therefore consume the run before being rejected at Gate 5. Because the ledger is single-use and append-only, this permits a caller able to submit a dispatch request to burn the evidence needed by the later authorized request, causing denial of the one-shot trial.

**Required correction:** Validate every required non-mutating admission authorization, including the exact Gate 5 release record and candidate/plan/action bindings, before consuming the preflight run ID. Then define the atomic consume point immediately before the first operation that can create dispatch authority or mutate dispatch state. Keep any unresolved Gate 5 trust-anchor dependency fail-closed; do not treat Gate 4 consumption as clearance for later checks.

### GATE4-AUTH-DESIGN-R6-02 — MEDIUM: Dispatcher IPC admission boundary remains underspecified

Sections 7.1–7.2 assign Class B consumption to the Dispatcher after receipt of a “dispatch request over IPC,” but do not identify the accepted listener/socket endpoint, request schema and framing, authenticated peer UID policy, or the exact response that proves durable consumption to the caller. The existing oracle DTOs describe Signer Oracle requests, not this Dispatcher admission request. Without these properties, the handoff from the interactive runner to the only principal allowed to consume Class B cannot be implemented or reviewed against a defined trust boundary.

**Required correction:** Specify a separately reviewed Dispatcher IPC contract (endpoint, framing, strict request/response schemas, `SO_PEERCRED` peer policy, and response binding to the request ID and preflight run ID), or mark this boundary as an unresolved prerequisite and make the current design explicitly non-operational until that contract is accepted. Do not infer these properties from the Oracle socket protocol.

## Safety disposition

R6 resolves both R5 findings: the read/check/append sequence is serialized under an exclusive lock, and only `_resolveflow_dispatcher:902` accesses Class B. The findings above remain before design acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
