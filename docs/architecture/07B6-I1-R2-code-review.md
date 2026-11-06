# G07-07B6-I1-R2 — Trial Authorization Contracts: Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Static review only. No tests, prototype, Resolve process, IPC, FusionScript, network, or AI calls were run. The reported focused result (44 passed, exit 0) is handoff evidence and was not independently rerun.

The supplied hashes were checked against the workspace and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `cc3f98d459b658e7da8bfb00dd2269866e540cb286986867454a0b5da5f062ff` |
| `.bridge/report.md` | `bcabcfe12732de8cc688ea6b41bba432526a314c27da498a924ca9760fd66033` |
| `src/core/contracts/trial_authorization_contracts.py` | `239175e2323fcad46332543251e823ab8c37146b5a7aef96905ffa1daf410e0e` |
| `tests/core/contracts/test_trial_authorization_contracts.py` | `9fd757b835eb519abb4b6fd30857b45b6c35df8b51da7336465ed5580a11b1f3` |
| `.bridge/ux-evidence/G07-07B6-I1/pytest_focused.log` | `f28646a07be79b7c55e289322c5ab12113610ff264cb1215ea8b03e16b4af4a2` |
| Prior review `docs/architecture/07B6-I1-R1-code-review.md` | `a699fed16d5a3a9b25c11fff1459dc9f6afe7d420f4898a25e67a99ccc58109e` |

## Previous finding status

**I1-R1-01: Resolved.** The code now parses timestamps into timezone-aware datetimes, checks chronological order as UTC instants, and calls the parser from authorization validation and `OperatorConsentGesture.__post_init__()`. The authorization validator is invoked during DTO construction, HMAC serialization, and payload assembly. The added tests cover malformed, naive, non-zero-offset, equivalent-instant, reversed-interval, and gesture timestamp cases.

## Finding

### I1-R2-01 — UTC parser accepts the RFC 3339 “unknown local offset” marker and trims non-canonical input

**Severity: HIGH — acceptance blocker**

`parse_iso_utc_timestamp()` strips surrounding whitespace (`trial_authorization_contracts.py:471`) and explicitly accepts a `-00:00` suffix (`:472`). The workorder's stated contract for this field permits only an explicit UTC suffix `Z` or `+00:00`. In RFC 3339, `-00:00` communicates that the local offset is unknown; it is not an assertion that the timestamp is UTC. `datetime.fromisoformat()` then represents it as a zero-offset datetime, so the later `utcoffset() == 0` check cannot distinguish it from a UTC assertion.

As a result, values such as `2026-11-03T05:00:00-00:00` are accepted as authorization timestamps, and surrounding whitespace is silently normalized for validation while the original untrimmed string remains in the signed DTO. This makes the parser more permissive than the explicit accepted suffix/format contract.

**Required correction:** Accept only the exact allowed suffixes `Z` and `+00:00`; reject `-00:00`. Reject leading/trailing whitespace instead of stripping it, and validate the complete timestamp grammar before parsing so the input that is validated is exactly the input serialized and signed. Add focused negative tests for `-00:00`, leading/trailing whitespace, and positive controls for `Z` and `+00:00`; verify the failures occur at DTO construction and the serializer/assembler validation boundary.

## Review conclusion

The R1 timestamp parsing and instant-ordering correction is sound for the tested forms. The remaining acceptance of an unknown-offset marker and non-canonical whitespace violates the stated explicit-UTC contract, so I1 is **not accepted** pending correction and resubmission. This review does not authorize I2, I3, I4, production/UI wiring, or a live canary.

## Safety boundary

No source, test, task, report, evidence, or status file was modified by this review. `ready_to_execute=False` remains required; Gate G07 and milestones M1/M2/M3 remain OPEN. The reported test evidence is not a Resolve behavior claim.
