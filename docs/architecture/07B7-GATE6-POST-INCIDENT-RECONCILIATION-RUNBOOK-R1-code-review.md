# Gate 6 Post-Incident Reconciliation Runbook R1 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-POST-INCIDENT-RECONCILIATION-RUNBOOK-R1.md`
- **Submitted SHA-256:** `5e47d2050397e577095d5ffcc1de5c9c2db169bec28e6179cf0a1115d6c464ae`
- **Mode:** Documentation and static source review only. No tests, Resolve, sockets, network, host, systemd, or production-state access.

## Findings

### POST-INCIDENT-RUNBOOK-R1-01 — BLOCKER: Runbook attributes proposed recovery logic to the accepted implementation

The runbook says STATE-02 recovery tracks `unfinalized_committed` and forces `IN_DOUBT_LOCKED` (`§2 STATE-02`, lines 18–20). That tracker exists only in the accepted receipt/WAL design proposal; it is not in the current `DurableDispatchJournal` implementation. In accepted source, `_recover_from_files()` removes an execution from `unmatched_pending` for `ACTION_COMMITTED` (`src/services/durable_dispatch_journal.py:552–555`) and then only latches executions still in `unmatched_pending` (`:561–589`). Thus a complete `ACTION_COMMITTED` without `FINISH` can leave the ledger `ACTIVE` without a recovered `.in_doubt` marker. Similarly, after a `FINISH` record, source removes the unmatched entry, so STATE-03 does not automatically latch `IN_DOUBT_LOCKED` either.

**Required correction:** Separate actual current-source behavior from recommended human disposition and proposed future behavior in every state row. For these cases, state that the current code leaves the target durably excluded by `ACTIVE` but does not perform the proposed in-doubt latching; the runbook must require a hard stop and separately authorized manual review. Do not present proposed recovery behavior as implemented.

### POST-INCIDENT-RUNBOOK-R1-02 — HIGH: Unapproved authorities and sign-off governance are presented as established requirements

Section 5 creates `Dispatcher Custodian`, `Signer Oracle Custodian`, and an `Incident Reconciliation Board`, then requires three-party signatures and a board disposition. These roles and authority mechanisms are not established in the accepted contracts cited by the workorder. The runbook presents them as mandatory authority rather than marking them as an unapproved governance proposal. It also directs a selected disposition to mark the ledger `EXHAUSTED` and asks for an exact shell command (§6), without an accepted trust anchor or approved procedure authorizing that production-state mutation.

**Required correction:** Do not invent or assert authority. Either keep this document strictly classificatory and require all state-changing disposition to stop pending a separately approved governance workorder, or label proposed roles/signatures as unresolved and non-operative. No ledger edit, marker removal, or lock clearing may be authorized by this runbook design.

### POST-INCIDENT-RUNBOOK-R1-03 — HIGH: Evidence boundary contradicts itself about live Resolve access

The evidence checklist requires a contemporaneous screenshot and physical GUI observation on `DISPLAY=:1` (§3.1, lines 91–97), while §3.2 says all evaluations are on frozen diagnostic copies and administrators do not access Resolve state or production paths. The document does not distinguish pre-existing submitted visual evidence from a future live observation. As written, an operator could interpret the runbook as authorizing a new live Resolve inspection, which this workorder and current Gate 6 status do not authorize.

**Required correction:** Distinguish existing, already-authorized evidence from any future live observation. Under this workorder, require only submitted evidence copies; any new GUI observation or access to production paths must remain a hard stop pending a separate, explicit workorder and authorization.

## Safety disposition

The runbook’s no-retry, no-replay, and no-automatic-repair defaults align with Option A. The source-state, authority, and evidence-boundary issues above must be corrected before accepting the runbook.

No tests were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected. No Resolve, Oracle socket, network, host, systemd, or production key/state operations were performed.
