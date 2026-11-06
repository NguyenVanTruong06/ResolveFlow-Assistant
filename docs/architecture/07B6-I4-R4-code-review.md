# G07-07B6-I4 Revision R4 Code Review

**Decision: ACCEPTED**  
**Review mode:** Independent static code review. No tests, prototype, Resolve application, IPC, FusionScript, network, or AI calls were run.  
**Scope:** I4 R4 adapter and its focused tests; specifically the two findings in `07B6-I4-R3-code-review.md`.

## Artifact integrity

The submitted hashes match the files present during this review:

| Artifact | SHA-256 |
|---|---|
| `src/services/isolated_resolve_trial_adapter.py` | `e1a7a68bccf77b1db2dd94411d603bcf739fd21b3cea6e83ad75400d3b31ae35` |
| `tests/services/test_isolated_resolve_trial_adapter.py` | `989caa328370240f855ed66f79c04ab3c3546b3a1ffddfa8e778866e64841ce8` |
| `.bridge/report.md` | `406f26f4c30d035127357ad872318260be2e4e947cb95291bc2fc7f6bcf8aa11` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_focused.log` | `8324cfad113f747e81594eb69fa9eaa9bacf3028a64f5455b1e875b742bac6c2` |
| `.bridge/ux-evidence/G07-07B6-I4/pytest_combined.log` | `8703b4769e0a6c931cc206abab44afebe9640f9a35989531ee5a519aca0cc202` |

The report and logs were inspected as submitted evidence; their test commands were not rerun.

## Findings reassessed

### I4-R3-01 — No EXHAUSTED transition without a durable terminal receipt

**Status: RESOLVED.**

At `src/services/isolated_resolve_trial_adapter.py:493-509`, the success path explicitly treats a single `ACTION_COMMITTED` record as nonterminal. It does not call `exhaust_target()` or synthesize a receipt ID. It returns `target_ledger_state: "ACTIVE"` and `terminalized: False`. The focused success-path assertions at `tests/services/test_isolated_resolve_trial_adapter.py:530-545` verify those values and confirm the persisted ledger remains ACTIVE with no `terminal_receipt_id`.

This matches the accepted receipt contract: `TrialExecutionReceipt.receipt_id == execution_id`, while `authorization_id` remains a distinct identity (`07B5-isolated-resolve-acceptance-gate-design.md:753-789`). The ledger contract requires a terminal receipt ID for EXHAUSTED (`:811, 828-834`). Keeping the ledger ACTIVE preserves the target admission barrier until a separate, authorized terminalization or reconciliation path exists.

### I4-R3-02 — Full identity comparison for an existing RESERVED ledger

**Status: RESOLVED.**

At `src/services/isolated_resolve_trial_adapter.py:231-255`, Phase 1A compares all eight required values: `execution_id`, `authorization_id`, `approval_id`, `proposal_id`, `proposal_revision`, `plan_idempotency_key`, `target_project_uid`, and `target_timeline_uid`. Any mismatch raises `ERR_TARGET_LEDGER_IDENTITY_MISMATCH` before the clean-rejection handler, so the target lock is retained and no ledger activation or gateway dispatch occurs.

Phase 2 repeats the complete tuple comparison immediately before `activate_target()` (`:306-351`), providing the requested defense in depth. The accompanying R4 tests cover authority-field mismatches, project UID mismatch, lock retention, no dispatch, unchanged RESERVED state, and the matching-reservation success path (`tests/services/test_isolated_resolve_trial_adapter.py:1100-1265`).

## Review conclusion

Both R3 findings are closed in source and have corresponding focused assertions. No additional blocker was identified within the reviewed R4 slice. The reported focused and combined test results are recorded as handoff evidence only; this review makes no claim of a fresh test run.

**ACCEPTED.** This accepts only G07-07B6-I4 R4 within its mock-only scope. It does not authorize Resolve access, UI or production wiring, live canary, or transition of Gate G07 / milestones M1, M2, or M3. Preserve `ready_to_execute=False` and keep those gates OPEN.
