# Gate 3-I16 Revision R2 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I16 — Local-Test Trial Authorization Issuance Core  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static review; submitted test evidence checked, tests not rerun.

## Artifacts reviewed

| Artifact | Submitted SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `26a457fa300e21e0fa672c6a2f17c18d22eeee4a4e35413b6cb4409ef999625f` | Matches workspace |
| `.bridge/report.md` | `6ce52503badd238c79e67eb84adf061350c073d6bc910611e353f4e181fb4464` | Matches workspace |
| `src/services/trial_authorization_signer_service.py` | `ed67319ca36d6df36ba81ddd9178b6138823227f7978f1c4ec2b002e2ac4b2b2` | Matches workspace |
| `tests/services/test_trial_authorization_signer_service.py` | `e30522f22c96b8af519678ff275c09371bf7b84f5846e26b57584033d51cc418` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I16/pytest.log` | `d12dfa5247598c3cedc579d8c965dbf8e7ad2a038665a46141396d1a5b53783e` | Matches workspace; reports 341 passed, 2 warnings, exit 0 |

## Findings

### I16-R2-01 — BLOCKER: The issuance signing helper still lacks a complete, authoritative boundary

`LocalTestSignerHmacSigningCapability._sign_for_issuance()` at `src/services/trial_authorization_signer_service.py:5357-5498` is directly callable with a candidate DTO and caller-supplied context objects. It does not itself establish that `admission_evidence` came from a fresh I12 verification: it checks only its exact type, then does not read or bind any of its fields. The `approval_envelope`, `proposal`, `execution_plan`, and consent arguments are likewise type-checked and used as expected-value sources, but their signatures/admission are not verified in this method. A caller can consume and reserve a genuine challenge and then invoke this helper with substitute context and a forged exact-type admission evidence object.

The purported exhaustive `claim_checks` list (lines 5434-5466) also omits two of the 32 HMAC-covered claims: `issued_at_iso` and `valid_until_iso`. The helper therefore signs a caller-selected validity window without checking it against the issuance verification instant or the allowed TTL. This contradicts the workorder's fresh-admission, exact 32-claim, and valid issuance/expiry requirements. The 13 tampering tests do not exercise these omissions or an unverified admission context.

**Required correction:** Ensure no callable signing path accepts a caller-selected DTO/context outside the single issuance operation. Bind signing to a one-time internal issuance object/result that cannot be supplied by the caller, or perform HMAC signing directly after validation inside the issuance routine. Before signing, verify the complete fresh I12 result and bind every one of the 32 claims—including `issued_at_iso` and `valid_until_iso`—to the verified inputs and the captured UTC instant/TTL. Add regression tests for a forged/substituted admission context and altered issue/expiry timestamps through every signing entrypoint.

## Review notes

The previous R1 finding's non-evidence claim substitutions are partly addressed by the added checks, and direct `sign_trial_execution_authorization()` now rejects. The remaining callable helper and omitted claims still permit signing outside the required fully validated path, so I16 is not accepted. Submitted test results are recorded as evidence and were not rerun.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No authorization is granted for later slices, production key custody, dispatch, Resolve access, or live operations.
