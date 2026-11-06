# Gate 3-I16 Revision R1 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I16 — Local-Test Trial Authorization Issuance Core  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static review; submitted test evidence checked, tests not rerun.

## Artifacts reviewed

| Artifact | Submitted SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `26a457fa300e21e0fa672c6a2f17c18d22eeee4a4e35413b6cb4409ef999625f` | Matches workspace |
| `src/services/trial_authorization_signer_service.py` | `a99705fcd28848ecd309b7007c879a748fd637e6a37f9a26f4cc92ad96ab8c35` | Matches workspace |
| `tests/services/test_trial_authorization_signer_service.py` | `fb50337d43431079b1af7a010296416afd49e3d4c6d35a60fd32b26f4e80942a` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I16/pytest.log` | `b693f98c953095dae06a4eeb3f55e16d6a5a5723c2904df2c600de5bdb7ca1f4` | Matches workspace; reports 340 passed, 2 warnings, exit 0 |
| `.bridge/report.md` | `1b2af4402bf3aea2d7c802276c4776bddfde668616beeda0b8a35bb86e823b33` | Matches workspace |

## Findings

### I16-R1-01 — BLOCKER: `_sign_for_issuance()` permits signing caller-controlled claims

The public `sign_trial_execution_authorization()` now correctly rejects direct signing. However, `LocalTestSignerHmacSigningCapability._sign_for_issuance()` remains directly callable at `src/services/trial_authorization_signer_service.py:5357-5434`. It accepts a caller-supplied `TrialExecutionAuthorization` and verifies only six fields against the reserved consumption evidence: `authorization_id`, `execution_id`, `approval_record_id`, `proposal_revision`, `plan_digest_sha256`, and `action_payload_digest_sha256`.

The other HMAC-covered claims are not bound in this method to the fresh I12 admission result, consent, plan, or target context. A caller who has a legitimately consumed/reserved evidence object can therefore call this method with a DTO that preserves those six values but substitutes other claims (for example target UIDs/names, source path/hash, action/track/frame scope, operator gesture digest, approval digest, or validity timestamps) and obtain a valid HMAC outside the issuance pipeline. The method's leading underscore does not enforce the workorder's API invariant.

**Required correction:** Ensure the signing primitive cannot sign a caller-selected DTO. Bind it to the exact internally constructed claims from the successful I16 invocation, or move the signing operation into the issuance routine so only its validated DTO can reach the HMAC operation. Bind every HMAC-covered claim to the already verified inputs, not only the six consumption-evidence fields. Add a regression test that reserves genuine evidence, alters a non-six-field claim in a caller-created DTO, attempts the internal signing path, and proves no signature/token is returned.

## Review notes

The reported 340 passing tests are recorded as submitted evidence; they were not rerun during this static review. Artifact hashes match the submitted values. The direct public signing method is fixed, but this remaining bypass prevents I16 acceptance.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize later slices, production key custody, dispatch, Resolve access, or live operations.
