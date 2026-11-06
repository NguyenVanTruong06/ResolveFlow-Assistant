# Gate 6 Post-Incident Reconciliation Runbook R4 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-POST-INCIDENT-RECONCILIATION-RUNBOOK-R1.md` (Revision R4)
- **Submitted SHA-256:** `823c6136d8e63f1d6022655f48a02e27ad48e89eb7d1dae02f4f686362b2dd6d` (matched workspace)
- **Mode:** Documentation and static source review only. No tests or runtime operations.

## Findings

### POST-INCIDENT-RUNBOOK-R4-01 — HIGH: Dispatcher shutdown is overstated as preventing all Resolve mutation

Section 4.2 states “Zero mutation can occur against DaVinci Resolve while the service remains shut down.” The stop rule only constrains this Dispatcher service. It does not technically prevent a human from editing through the Resolve GUI, another process from using the Resolve API, or another authorized mechanism from changing the project. The runbook itself prohibits new GUI inspection for this workorder, but that does not establish a system-wide mutation boundary.

**Required correction:** Limit the claim to “no Dispatcher-mediated mutation can be initiated while the Dispatcher service is confirmed stopped.” State that other mutation paths are outside the guarantee and remain prohibited by scope; do not describe them as technically impossible.

### POST-INCIDENT-RUNBOOK-R4-02 — MEDIUM: Strict parsing scope omits submitted structured evidence files

Section 3.1 requires `execution.log` and `.in_doubt` marker copies, and the accepted journal writes marker content as JSON (`src/services/durable_dispatch_journal.py:581–588, 861–871`). Section 3.3 requires strict parsing for target ledger, receipt, WAL, and selected Oracle JSONL files, but omits `execution.log` and marker JSON from the strict duplicate-key/root-type procedure. Hashing alone verifies byte identity against a supplied digest; it does not validate their structure or reject ambiguous duplicate keys when interpreting them.

**Required correction:** Enumerate every submitted structured artifact that the classifier interprets, including `execution.log` and marker JSON when present, and require the same duplicate-key and root/schema validation. If a file is hash-only and never parsed, say so explicitly and prohibit deriving classification facts from its contents.

## Safety disposition

R4 addresses the prior findings on missing-state exclusion, parser root validation, and unsupported crash-probability claims. The remaining scope and evidence-integrity claims must be narrowed before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
