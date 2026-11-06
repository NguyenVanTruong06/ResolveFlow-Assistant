# G07-07B6-I2 — Purpose-Separated Keyring Authority: Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Static review only. No tests, prototypes, Resolve process, IPC, FusionScript, network, or AI calls were run. The reported focused result (50 passed, exit 0) is recorded as handoff evidence and was not independently rerun.

The supplied hashes were checked against the workspace and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `83f24c772797ff85f470fb5982e98d10d8bbcc158bdce7f1a4ebe02a63f53b2e` |
| `.bridge/report.md` | `cc9debd08dcccffc05e938818e695e51ebc46fa42cdb7adb5b499207d4f25367` |
| `src/services/trial_keyring.py` | `b3e058da0e1a17efa9873a745765a6e5cb9749cc82f3b9e8589cf6a926c3d7f0` |
| `tests/services/test_trial_keyring.py` | `d1c1037a66b7fa9a393145d012ba36bbafb7dae5c4d084f0cc2c409c6f24910c` |
| `.bridge/ux-evidence/G07-07B6-I2/pytest_focused.log` | `46a96d3363e0b31ec8517b4fdb31b4ecbd440ea84aea575ed284599b666c942d` |

## Finding

### I2-01 — Constructor cleanup errors are silently discarded when initialization already failed

**Severity: HIGH — acceptance blocker**

The constructor tracks the primary file-processing exception in `file_orig_exc`/`orig_exc`, then attempts to close `ffd` and `dir_fd` in `finally` (`src/services/trial_keyring.py:305-329`). If either `os.close()` raises while a primary initialization error is active, the close handler has no `else`, aggregation, or exception note; it simply exits. This suppresses the cleanup failure and re-raises only the original exception.

That behavior contradicts this workorder's requirement to preserve the original failure **and not silently ignore cleanup failures** (`.bridge/task.md:41-47`). It also means the caller cannot tell that descriptor cleanup was uncertain or incomplete. The existing cleanup test at `tests/services/test_trial_keyring.py:321-343` asserts only that the original permission error remains visible; it does not assert that the injected close error is surfaced or attached.

**Required correction:** Preserve both errors in a structured initialization/cleanup exception (or attach the cleanup failures as explicit, inspectable exception data/notes while retaining the primary error). Include descriptor identity and close outcome as known/unknown; do not blindly retry `close()` on an FD whose first close outcome is ambiguous. Add fault-injection coverage for a primary file-validation failure plus `ffd` close failure, and for a primary failure plus `dir_fd` close failure. The tests must assert the original failure and every cleanup failure remain observable, without claiming a descriptor is closed when its close outcome is uncertain.

## Review conclusion

The reviewed implementation otherwise provides the planned purpose-separated lookup APIs, strict operator-key schema and duplicate-member rejection, entropy bounds, descriptor-relative file opens, and fail-closed revocation parsing. The cleanup-error suppression is a resource-ownership and observability gap within the authorized I2 scope, so this slice is **not accepted** pending correction and resubmission. This review does not authorize I3, I4, coordinator/adapter integration, production/UI wiring, or a live canary.

## Safety boundary

No source, test, task, report, evidence, or status file was modified by this review. `ready_to_execute=False` remains required; Gate G07 and milestones M1/M2/M3 remain OPEN. The reported test evidence is not a Resolve behavior claim.
