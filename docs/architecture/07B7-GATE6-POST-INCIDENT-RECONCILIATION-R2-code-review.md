# Gate 6 Post-Incident Reconciliation Runbook R2 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-POST-INCIDENT-RECONCILIATION-RUNBOOK-R1.md` (Revision R2)
- **Submitted SHA-256:** `ef816618f516afdf08a923c1e67378cfd370bf3d466a5468c3d0a6001c8bd0b3` (matched workspace)
- **Mode:** Documentation and static source review only. No tests or runtime operations.

## Findings

### POST-INCIDENT-RUNBOOK-R2-01 — HIGH: Target exclusion is attributed to the Signer daemon

Section 1.1 says durable logical target exclusion is enforced by both `DurableDispatchJournal` and `TrialAuthorizationSignerDaemon`. The accepted source assigns target reservation, target state, `.in_doubt` markers, and replay exclusion to `DurableDispatchJournal` (`src/services/durable_dispatch_journal.py:614–623, 659–665`). The Signer ledger consumes execution IDs and records Oracle verification/terminal state; it does not enforce Dispatcher target locks. Section 7.4 repeats the inaccurate coupling by saying the Oracle execution record is permanently consumed, implying that this itself is target exclusion.

**Required correction:** Attribute target exclusion solely to the Dispatcher journal. Describe Signer Oracle replay consumption separately from target locking, and avoid implying the Oracle's consumed execution ID protects a target from redispatch.

### POST-INCIDENT-RUNBOOK-R2-02 — BLOCKER: STATE-06 still implies an unapproved Oracle state transition

STATE-06 says the orphaned Oracle execution “requires offline administrative attestation to terminalize Oracle record.” Section 5.1–5.2 correctly says no accepted reconciliation authority exists and no state-changing action can be authorized. No accepted wire contract or administrative procedure authorizes terminalizing this orphan. The state row therefore contradicts the runbook's own stop condition and can be read as authorizing a production ledger mutation.

**Required correction:** Make STATE-06 diagnostic-only: classify the execution ID as consumed and nonterminal in the submitted copies, then stop. State explicitly that no terminalization, ledger edit, or other mutation is authorized; any future procedure requires a separate reviewed governance workorder. Align §7.4 accordingly.

### POST-INCIDENT-RUNBOOK-R2-03 — MEDIUM: Strict duplicate-key parsing is underspecified

Section 3.3 instructs use of Python `json.loads()` “with strict duplicate-key rejection” but provides no hook or parser implementation. Plain `json.loads()` accepts duplicate object keys and retains the last value, so following the sample as written would not satisfy the stated integrity check. The repository's accepted strict parser demonstrates the necessary `object_pairs_hook` rejection (`src/core/contracts/oracle_ipc_contracts.py:182–198`).

**Required correction:** Specify use of `parse_strict_json()` for supported JSON records, or provide an explicit `object_pairs_hook` that rejects duplicate keys for each JSONL line. Keep the procedure offline and read-only.

## Safety disposition

Revision R2 corrected the prior recovery-state attribution, removed operative invented authorities, and clearly prohibited new live Resolve observations. Those remediations are retained. The findings above must be resolved before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
