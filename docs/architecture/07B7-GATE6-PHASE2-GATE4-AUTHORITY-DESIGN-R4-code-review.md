# Gate 6 Phase 2 Gate 4 Authority Design R4 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R4)
- **Submitted SHA-256:** `fd1d71d525db58bfeed4d6642e71c25e07a5c89559e6a13f7a66659b10184fb4` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Finding

### GATE4-AUTH-DESIGN-R4-01 — HIGH: Replay-consumption ledger requirement conflicts with blanket mutable-state prohibition

Sections 6.1–6.2 require the Dispatcher to check `consumed_preflight_runs.jsonl` before admitting a `preflight_run_id`, then append and fsync the ID upon dispatch admission. Sections 7.1–7.2, however, prohibit opening mutable production state or journal files before full gate clearance and state that Phase 2 admission failures stop before accessing mutable state. The replay check cannot be performed without reading that ledger; a replay rejection itself therefore requires mutable-state access. The append/fsync step also needs an explicitly defined position relative to final admission and subsequent socket/journal operations.

**Required correction:** Define a narrow exception for the descriptor-anchored preflight-consumption ledger and specify the ordering explicitly: validate the bounded read-only trust anchor and envelope first; then read the replay ledger; reject replay before socket access or other journal changes; for a fresh run, durably consume it at a clearly defined admission point before any operation that could authorize dispatch. State exactly which ledger access is permitted and preserve fail-closed behavior for malformed, unavailable, or ambiguous ledger state. Reconcile this sequence with “full gate clearance” and the Phase 2 failure matrix so replay failures are not described as occurring before all mutable-state access.

## Safety disposition

R4 resolves `GATE4-AUTH-DESIGN-R3-01`: §7 now distinguishes bounded read-only trust-anchor access from mutable production state, and it no longer claims that trust-anchor failure detection occurs without reading that configured path. The replay-ledger sequencing conflict above remains. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
