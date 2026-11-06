# Gate 3-I16 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I16 — Local-Test Trial Authorization Issuance Core  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static code review; submitted test evidence checked, tests not rerun.

## Artifacts reviewed

| Artifact | Submitted SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `26a457fa300e21e0fa672c6a2f17c18d22eeee4a4e35413b6cb4409ef999625f` | Matches workspace |
| `src/services/trial_authorization_signer_service.py` | `3767d9f55a5b509a5e36ef518c467c8383421b47097bb4810a9cbbcae1e303fd` | Matches workspace |
| `tests/services/test_trial_authorization_signer_service.py` | `3b17641834eca1781afcc4266177dda53316f14dda5836e7cfff6e84ef21e9cb` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I16/pytest.log` | `4e3e6a10b0844d181c233dae4bc8d3995e23a41a32946b83b9d4c95b75c1736b` | Matches workspace; reports 338 passed, 2 warnings, exit 0 |
| `.bridge/report.md` | `1e96f3f4d14d8b5e6b82b3f3a0a244203416b2ae55e969c526e35a123f624c52` | Matches workspace |

## Findings

### I16-01 — BLOCKER: Signing capability bypasses admission and challenge consumption

`src/services/trial_authorization_signer_service.py:5348-5358, 5373-5415` exposes `LocalTestSignerHmacSigningCapability.sign_trial_execution_authorization(auth)` and a public factory that creates this capability from caller-supplied key bytes. The signing method accepts any exact `TrialExecutionAuthorization` and signs its canonical claims; it does not require authority-owned consumed evidence, successful I12 admission, or the I16 one-time issuance reservation.

This creates a second token-minting path outside `issue_trial_execution_authorization_for_local_test()`. A caller holding the capability can construct a valid DTO and invoke the method directly, bypassing the workorder's consume-before-sign rule and its fail-closed challenge/identity bindings. The I16 path's round-trip verification does not close this bypass because the same capability remains independently callable.

**Required correction:** Make raw HMAC signing unavailable as a general public operation. Bind signing to the I16 issuance operation so the capability can sign only the exact internally constructed claims after successful admission, successful I15 consumption, and authority-owned single-use reservation. Add a regression test that attempts direct capability signing of a caller-created DTO and proves no signature/token can be obtained. Preserve the ephemeral-key-only limitation and do not claim process isolation.

## Review notes

The reported 338 passing tests are recorded as submitted evidence; they were not rerun during this static review. Artifact hashes match the submitted values. The scope and stated safety invariants otherwise remain local-test-only; this review does not accept I16 or authorize later slices, production key custody, dispatch, Resolve access, or live operations.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
