# Gate 6 Phase 2 Gate 4 Authority Design R5 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R5)
- **Submitted SHA-256:** `5c3b691e0c03df7af3398aaeb13c524cd35e189d6db946a12d51816c39d2fd7b` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R5-01 — HIGH: Replay check and consumption are not atomic across processes

Section 7.2 performs a read-only absence check in Step 4, then appends/fsyncs in Step 5, but defines no inter-process serialization or atomic consume operation. Two concurrent admission processes can both observe the same `preflight_run_id` as absent and both append it, so both receive Gate 4 clearance despite the single-use replay requirement. The target journal's later one-shot reservation is a separate defense and does not make this ledger check-and-consume operation atomic.

**Required correction:** Specify a cross-process serialization mechanism held continuously across replay lookup and durable append (for example, a stable-inode descriptor-anchored lock with exact DAC/type/ownership validation and fail-closed acquisition), or another atomic compare-and-consume design. Define crash behavior and require a durable append/fsync before clearance. Add concurrency/fault-injection tests in a later implementation workorder; do not claim the replay layer eliminates concurrent replay until this is specified.

### GATE4-AUTH-DESIGN-R5-02 — HIGH: Class B replay ledger is inaccessible to the interactive runner under the stated DAC

Section 7.1 assigns `consumed_preflight_runs.jsonl` to `_resolveflow_dispatcher:902`, mode `0600`, beneath `/var/lib/resolveflow/dispatcher/` which §2.2 specifies as mode `0700`. Yet §7.1 permits “Evaluators” to access the ledger and §7.2 places the replay check/append in the candidate runner's admission sequence. The `interactive_user:1000` runner cannot traverse the parent directory or read/write the ledger. The design does not say that these steps execute solely inside the `_resolveflow_dispatcher` process or define an authorized IPC boundary for requesting consumption.

**Required correction:** Assign replay lookup and consume exclusively to the dispatcher principal and define the call boundary that returns a durable-consumption result to the runner, or redesign the access model through a separately accepted DAC/interface decision. Do not grant UID 1000 direct access to the dispatcher-owned directory or ledger. Align the step actors, error handling, and ordering with the selected model.

## Safety disposition

R5 resolves `GATE4-AUTH-DESIGN-R4-01` by distinguishing Class A trust-anchor reads, Class B replay-ledger access, and prohibited Class C state, and by ordering signature verification before replay-ledger access. The findings above remain before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
