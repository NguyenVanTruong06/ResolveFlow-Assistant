# Static Code Review — G07-AUTH-SIGNER-GATE3-I1

**Decision: CHANGES_REQUIRED**

**Scope reviewed:** `SignedApprovalEnvelope` and `SignedOperatorConsentEnvelope`, their two focused test modules, the workorder, completion report, and submitted pytest log. This is a static review only; no tests, Resolve, IPC, services, keys, or network operations were run or accessed.

## Evidence and scope check

The submitted SHA-256 values match the current workspace for all four allowlisted implementation/test files, `.bridge/task.md`, `.bridge/report.md`, the handoff/resume status files, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I1/pytest.log`. The recorded test evidence states 109 passed, 0 failed, exit code 0. This review did not independently execute that command.

The implementation is limited to the four allowlisted files. It reuses `canonical_json_bytes`, provides separate signing-payload and complete-envelope serialization, and keeps signature verification and runtime responsibilities outside this slice. The review found the following issues that must be corrected before acceptance.

## Findings

### I1-01 — Approval parser coerces signed omission claims instead of enforcing primitive string types (HIGH)

In `src/core/contracts/signed_approval_envelope.py:208-219`, `from_dict()` accepts a list/tuple for `accepted_omission_ids` and converts every element using `str(x)` before constructing `AppOwnedApprovalRecord`. The underlying `AppOwnedApprovalRecord` contract requires each omission ID to be a string, but this conversion means inputs such as `[7]`, `[None]`, or `[True]` are accepted as `("7",)`, `("None",)`, and `("True",)`.

That is a lossy normalization at a signed-data boundary: the caller's parsed claims are changed before canonical signing bytes are produced, so verification can operate on a different payload from the supplied envelope. It also contradicts the workorder's strict primitive-type and exact-claim requirements.

**Required change:** reject any omission element unless `type(item) is str`, then pass the original strings unchanged to `AppOwnedApprovalRecord`. Add negative tests for integer, boolean, and null elements, including a check that `from_dict()` does not normalize them into a valid envelope.

### I1-02 — Consent frame accepts values outside interoperable JCS integer range (HIGH)

`src/core/contracts/signed_operator_consent_envelope.py:263-273` validates `record_frame` as a non-negative Python integer but places no upper bound on it. The reused `canonical_json_bytes()` in `src/core/contracts/trial_authorization_contracts.py:413-423` accepts integers through signed 64-bit range (`_validate_decoded_graph`, lines 367-371) and serializes with Python `json.dumps`. RFC 8785/JCS number serialization is based on IEEE-754 binary64; integers above the exact interoperable range `2^53-1` cannot be relied on to round-trip as the same canonical number across implementations.

Consequently, a consent envelope with a large `record_frame` can pass DTO validation and produce bytes that a different JCS implementation parses/serializes as a different number. That undermines cross-implementation signature verification. The current tests exercise ordinary frame values and do not pin this boundary.

**Required change:** impose a signed-contract bound that keeps every integer claim in the JCS safe-integer range (at minimum `record_frame <= 9007199254740991`, preferably the tighter documented project frame bound), or use a repository serializer that demonstrably implements the required RFC 8785 number semantics and rejects non-interoperable inputs. Add boundary tests at the maximum accepted value and the first rejected value. Keep the change within the existing allowlist; if it requires changing shared contracts outside it, stop and report that dependency.

## Disposition

Do not mark Gate 3-I1 accepted yet. Once both findings are addressed in the allowlisted files and accompanied by fresh focused/adjacent test evidence, submit a revision for review. This decision applies only to Gate 3-I1; it does not authorize Gate 3-I2 or later slices, Gate 4/5, or any live operation.

## Safety state

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No Resolve, IPC, key, network, or test execution occurred during this review.
