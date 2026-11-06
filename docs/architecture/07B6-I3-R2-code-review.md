# G07-07B6-I3 Revision R2 Code Review

**Decision: ACCEPTED**  
**Review mode:** Static review only. No tests, Resolve, IPC, FusionScript, network, or AI were run.  
**Scope:** R2 ledger/coordinator implementation against the accepted I3 workorder, plan, and design.

## Artifact identity

The submitted SHA-256 values were independently checked against the current files and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `d45aa5c7ea116a149e6c90c5b5abe6ad87b57509cc3378e96d2e0a511f8ca101` |
| `.bridge/report.md` | `e3e753f2c269750990e5ce350358b50b98a16b5e7f2c3d29b57b4dcc60124b5c` |
| `src/services/durable_trial_target_ledger.py` | `8ed4c2df6d0e4231f2dbf592014e0e0a81397ad9f1f27d38bea8c26deeab5bb6` |
| `tests/services/test_durable_trial_target_ledger.py` | `8357a09a3126500862c311f7dcc735756e364f035bab47c5f6e967630fefe348` |
| `.bridge/ux-evidence/G07-07B6-I3/pytest_focused.log` | `9eef7617902746547accfdeddc7d9f87cbdb6827a7d1cf133d15395a472bd676` |
| Prior review `docs/architecture/07B6-I3-R1-code-review.md` | `2f4777957df9e9548098398889c07a713ca779517b39bf157f7c6814e6887fcf` |

The submitted report records 29 focused tests and 129 combined I1+I2+I3 tests passing. These results were not rerun during this static review.

## Review results

Both remaining findings from R1 are resolved:

1. **I3-R1-01 — Ledger identity binding:** `write_ledger_atomic()` now compares the persisted target, execution, authorization, approval, proposal, revision, idempotency key, and creation timestamp before publication. Mismatches fail closed before replacing the file. The added regression coverage exercises the immutable-field mutations in both `RESERVED` and `ACTIVE` states and checks byte-for-byte preservation.
2. **I3-R1-02 — Operator authorization signature binding:** Expiry comparison is exact. Signing and verification use the same versioned canonical JSON serializer over explicitly named authorization claims, excluding the signature field. The tests cover sub-tolerance expiry changes and delimiter-bearing values.

The six findings from the initial I3 review also remain addressed in the reviewed source: unlock failure preserves the target FD and kernel lock; ledger replacement stays anchored to the directory descriptor; public ledger writes serialize and reject terminal rewinds; startup scanning checks the per-target lock before temp cleanup and reports unlink failures; rejected expired challenges preserve their nonce; and target lock permissions are checked.

No remaining blocking or high-priority issue was found in the bounded I3 scope.

## Acceptance boundary

G07-07B6-I3 is **ACCEPTED** for its mock-only ledger and locking-coordinator scope. This acceptance does not authorize I4, adapter/gateway integration, production or UI wiring, or a live Resolve canary. Preserve `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain OPEN.
