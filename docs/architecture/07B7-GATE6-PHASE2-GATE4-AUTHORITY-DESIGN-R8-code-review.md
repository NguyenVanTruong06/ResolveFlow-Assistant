# Gate 6 Phase 2 Gate 4 Authority Design R8 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R8)
- **Submitted SHA-256:** `dd5d2643041411f00d1fcf4dba90f3c523452961f746ab0ab5498229c27c584f` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Finding

### GATE4-AUTH-DESIGN-R8-01 — MEDIUM: Mutual peer authentication is claimed but only server-side peer validation is specified

Section 7.3 requires the Dispatcher to inspect `SO_PEERCRED` on accept and require client UID 1000. It later says mutual authentication is guaranteed via `SO_PEERCRED`, but does not require the client to inspect the connected peer credentials and verify server UID 902 before trusting `DispatcherAdmissionResponse`. The stated server-side check authenticates the client to the Dispatcher; by itself it does not authenticate the Dispatcher to the client. The response carries the irreversible-consumption confirmation, so the client-side peer identity check is material to trusting that response.

**Required correction:** Explicitly require both checks on the connected AF_UNIX socket: Dispatcher verifies peer UID 1000, and client verifies peer UID 902 (and, if required by the service model, the expected process credentials) before accepting any response. Define failure as fail-closed before interpreting response bytes as admission/consumption confirmation. Alternatively, replace “mutual authentication” with the precise one-way claim and document the separate server-authentication basis in DAC/path ownership terms.

## Safety disposition

R8 resolves both R7 findings: the endpoint path and DAC match the accepted DEP-01/DEP-02A layout, and the unprovisioned seventh Dispatcher signing key has been removed. The remaining peer-authentication specification finding above should be resolved before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
