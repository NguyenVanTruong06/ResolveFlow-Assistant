# G07-07B6-I1-R1 — Trial Authorization Contracts: Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Static review only. No tests, prototype, Resolve process, IPC, FusionScript, network, or AI calls were run during this review. The reported focused result (38 passed, exit 0) is recorded as handoff evidence and was not independently rerun.

The supplied hashes were checked against the workspace and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `cc3f98d459b658e7da8bfb00dd2269866e540cb286986867454a0b5da5f062ff` |
| `.bridge/report.md` | `5f4ef67424e7ac68596d5828513b4f7a4f7debd3066ecf8065205e4c16ec15aa` |
| `src/core/contracts/trial_authorization_contracts.py` | `13a43658fad2054054bb375641634cdcacfa400ec87f53a2f641db1d5cef22e1` |
| `tests/core/contracts/test_trial_authorization_contracts.py` | `bd7358dac8393f3d035423fc4613219a060137b31f7bf6abbb18611a1231dbfa` |
| `.bridge/ux-evidence/G07-07B6-I1/pytest_focused.log` | `9e29251b2637563c8e6cb63aa7a3ddf3a44cda230f0bc083660382c893e5da90` |

## Previous finding status

**I1-01: Resolved.** The implementation adds `validate_trial_execution_authorization()` and calls it from the authorization dataclass initialization, `canonical_bytes_for_auth_hmac()`, and `assemble_canonical_action_payload()`. The validator enforces exact integer types, geometry/range constraints, the canary action scope, string/digest constraints, execution/nonce equality, and temporal ordering. `PlannedTimelineAction` also has construction-time validation, and the assembler checks action integer types before comparing them to authorization values. The new focused cases cover the prior bool-as-int bypass and the listed validation boundaries.

## Finding

### I1-R1-01 — Authorization timestamps are ordered as strings, not validated as UTC instants

**Severity: HIGH — acceptance blocker**

At `src/core/contracts/trial_authorization_contracts.py:581-585`, the validator checks only `auth.valid_until_iso <= auth.issued_at_iso`. Both fields are checked as non-empty strings, but neither is parsed or validated as an ISO-8601 UTC timestamp before this lexical comparison.

Consequently malformed values such as `issued_at_iso="not-a-time"` and `valid_until_iso="z"` satisfy the ordering check and can pass DTO construction and HMAC serialization. Lexical ordering also does not establish chronological ordering when timestamp strings use different offsets or formats. The accepted 07B5 contract identifies these as UTC timestamps and the authorization validity window is security-sensitive; a malformed or ambiguously zoned value must not be treated as a valid interval.

**Required correction:** Parse both fields as timestamps at the authoritative validation boundary; require the accepted explicit UTC representation (canonical `Z` or `+00:00`, with zero offset), reject naive/invalid timestamps, normalize to UTC instants, then require `valid_until > issued_at`. Add focused cases for malformed strings, naive timestamps, non-zero offsets, equivalent instants expressed with different accepted UTC suffixes, and a genuinely reversed interval. Keep the check active at DTO construction, HMAC serialization, and assembly.

## Review conclusion

The R1 correction closes I1-01. The remaining timestamp-validation gap means the authorization DTO can still accept malformed validity claims, so this slice is **not accepted** pending correction and resubmission. This review does not authorize I2, I3, I4, production/UI wiring, or a live canary.

## Safety boundary

No source, test, task, report, evidence, or status file was modified by this review. `ready_to_execute=False` remains required; Gate G07 and milestones M1/M2/M3 remain OPEN. The reported test evidence is not a Resolve behavior claim.
