# Static Code Review — G07-07B7-PREFLIGHT-GATE4-R15

**Review date:** 2026-11-05  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Static review of workorder, source, tests, report, and submitted test log. Tests were not rerun. No Resolve SDK/UI/IPC was used.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `3eb24a9867cc1172607f16eba6dac03eab0091e25cbffd85135f4d32869f88a2` | Matches R15 workorder |
| `src/tools/gate4_preflight_runner.py` | `90dcf0a0619a6d42076e3b5dc27f343fe5597b509336a0e1c7cc77da1136be62` | Matches submitted R15 hash |
| `tests/tools/test_gate4_preflight_runner.py` | `c68c95d64e4314cdeef7037c785b45f85eb538c0a155df24d94b33c0204e12d0` | Matches submitted R15 hash |
| `.bridge/ux-evidence/G07-07B7-PREFLIGHT-GATE4-R15/pytest.log` | `e8347e95ebf887f0e67a54182054c5a194af2719764d872bab98c2b68fe22bc1` | Reports 15 passed, 0 failed in 1.04s |
| `.bridge/report.md` | `f8941d7578f1fda7fe57bb6a28aa2164b1fba9377624590ed3594d75cde0643c` | R15 handoff and hashes present |

## Review result

Static inspection confirms the key write-ahead property: `_proxy_call` serializes both argument collections, appends `API_CALL_INTENT` through `append_event()` (which flushes and calls `fsync`), and only then invokes the adapter. The verifier checks unique call IDs, preceding intents, intent sequence, role/method, arguments, kwargs, outcome uniqueness, and unresolved intents. The submitted test log records the authorized 15-test suite as passing; it was not rerun during this review.

### G4-R15-01 — MEDIUM: Verifier does not bind `call_index` between intent and outcome

The intent requires and records `details.call_index`, and each outcome also records it. However, in `verify_audit_log_chain()` the outcome-to-intent checks compare `intent_seq`, role/method, and arguments/kwargs, but do not compare `details.call_index` with `intent_details.call_index`. The verifier also does not enforce that intent call indexes are valid, unique, and monotonically allocated. Consequently, a log whose outcome carries a different call index can still pass this linkage check, despite R15 requiring call identity and metadata to match one-to-one. The test asserts equality for naturally emitted records but does not mutate an outcome call index and prove the verifier rejects it.

**Required:** Validate the intent call index as an exact positive integer, enforce uniqueness/monotonic order across intents, require every outcome's call index to equal its associated intent's call index, and add a rehashed-log regression that changes only the outcome call index and is rejected. Keep the current pre-call fsync ordering and zero-invocation behavior on intent durability failure.

## Decision and lifecycle state

- Gate 4 R15: `CHANGES_REQUIRED` pending remediation of G4-R15-01 and a fresh authorized test run/log.
- No Resolve operation or Gate 5/6 action is authorized by this review.
- `ready_to_execute = False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
