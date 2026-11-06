# G07-07B6-I1 — Trial Authorization Contracts: Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Static review only. No tests, prototype, Resolve process, IPC, FusionScript, network, or AI calls were run during this review. The reported focused result (30 passed, exit 0) is recorded as handoff evidence and was not independently rerun.

The handoff hashes were checked against the workspace and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `cc3f98d459b658e7da8bfb00dd2269866e540cb286986867454a0b5da5f062ff` |
| `.bridge/report.md` | `3c8ccec85ad7290ddac77cdbd1cb9f619c402de39898db68167b10d3de7b68bb` |
| `src/core/contracts/trial_authorization_contracts.py` | `f669142a08526da37d3b8a2cffc402927047d8adee19ef925ac723c2f98f4066` |
| `tests/core/contracts/test_trial_authorization_contracts.py` | `bb36a7f9727e9ba7300261a433a2c8ee66aa716f69f5ae72b10127f52cf10a7f` |
| `.bridge/ux-evidence/G07-07B6-I1/pytest_focused.log` | `5b1d3c93ca2006f90daad3b9a2fc3b3d06d3add035c1b71f83b39d5bd04ef215` |

## Finding

### I1-01 — Authorization claims are serialized and compared without runtime type/range validation

**Severity: HIGH — acceptance blocker**

`TrialExecutionAuthorization` is frozen, but its declaration has no `__post_init__` or other validation boundary (`trial_authorization_contracts.py:524-577`). Python annotations do not enforce the declared `int` and `str` types. `canonical_bytes_for_auth_hmac()` copies claim values directly into JSON without validating the authorization DTO (`:646-685`). The assembler then compares action values to authorization claims before validating only the assembled payload (`:777-833`).

This permits Python's boolean/integer equality to bypass the intended signed-geometry binding. For example, an authorization with `authorized_track_index=True` compares equal to an action with `track_index=1`; the assembled payload contains the action's integer `1`, and its payload serializer accepts that integer. Likewise, `authorized_record_frame=False` compares equal to `record_frame=0`. The HMAC bytes encode the malformed authorization claim as a JSON boolean, while the dispatch payload encodes the action value as an integer. That violates the contract's strict primitive typing and means the assembler has not established that the signed claim itself is a valid integer claim before binding it.

This also falls short of the workorder's I1-G5 requirement for authorization “strict value/type validation” (`.bridge/task.md:54-60`). The current I1-G5 tests check the 33 dataclass fields, signature exclusion, and that changing each of the 32 claims changes serialized bytes (`test_trial_authorization_contracts.py:514-590`); they do not verify rejection of invalid claim types or values.

**Required correction:** Add one authoritative validation boundary for all 32 signed fields and the signature member. Enforce exact primitive types (especially `type(value) is int`, excluding `bool`), required non-empty strings, digest/identifier formats, frame/FPS ranges, and timestamp constraints as specified by the accepted 07B5 contract. Invoke it from both `canonical_bytes_for_auth_hmac()` and `assemble_canonical_action_payload()` before serialization or equality comparisons, so direct serializer calls cannot encode malformed authorization claims. Add focused I1-G5 cases for boolean-as-integer geometry, wrong primitive types, invalid ranges, and invalid timestamps; assert the assembler rejects them before producing a payload.

## Review conclusion

The 32-claim HMAC field set, separate `authorization_hmac` member, duplicate-key rejection, bounded JSON pre-scan, and 16-key action payload contract are present in the reviewed implementation. The missing authorization validation is a security-relevant contract gap, so I1 is **not accepted** pending correction and a new handoff. This review does not authorize I2, I3, I4, production/UI wiring, or a live canary.

## Safety boundary

No source, test, task, report, evidence, or status file was modified by this review. `ready_to_execute=False` remains required; Gate G07 and milestones M1/M2/M3 remain OPEN. The reported test evidence is not a Resolve behavior claim.
