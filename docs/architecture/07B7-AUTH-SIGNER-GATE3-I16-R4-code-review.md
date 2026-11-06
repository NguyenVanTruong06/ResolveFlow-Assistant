# Gate 3-I16 Revision R4 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I16 — Local-Test Trial Authorization Issuance Core  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static review; submitted test evidence checked, tests not rerun.

## Artifacts reviewed

| Artifact | SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `26a457fa300e21e0fa672c6a2f17c18d22eeee4a4e35413b6cb4409ef999625f` | Matches workspace |
| `.bridge/report.md` | `83da2830d208fb9636cc9ec9ec31a8030a9980777076628192703cedd6b586b5` | Matches workspace |
| `src/services/trial_authorization_signer_service.py` | `c25942cabc02a83109d6cebf2c59de1d4683e3924734a411d9a8808374ada602` | Matches workspace |
| `tests/services/test_trial_authorization_signer_service.py` | `44c8f274fc3fbf169eccac1d984331ee86a67448e13ef297c5795ffe37433cd0` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I16/pytest.log` | `1e7601ca6b9808e74f1516e86d22a42714c6e581d1e857e2252531ff570e0965` | Matches workspace; reports 343 passed, 2 warnings, exit 0 |

## Finding

### I16-R4-01 — BLOCKER: Admission registration still accepts caller-forged verification evidence

R4 adds `_registered_issuance_admissions`, but `LocalTestChallengeAuthority.register_issuance_admission_for_local_test()` at `src/services/trial_authorization_signer_service.py:5299-5344` authorizes registration by comparing its `_sentinel` argument to the module-level `_ISSUANCE_TICKET_SENTINEL` (`:4330`). That sentinel is an ordinary module attribute and can be imported by any caller. The registration function then checks only that `admission_evidence` has the exact DTO type and `verified is True` (`:5329-5333`); it does not establish that the evidence was returned by `verify_trial_authorization_admission_for_local_test()`.

A caller can therefore import the sentinel, construct an exact-type `TrialAuthorizationAdmissionEvidence` with `verified=True` and matching claims, call the registration method, then use the normal ticket creation and signing methods after challenge consumption/reservation. The registration stores the caller's object identity, so the later identity check in `create_issuance_signing_ticket()` proves only that the caller is reusing the object they registered—not that I12 verified it. The regression test setup itself imports and passes `_ISSUANCE_TICKET_SENTINEL` (`tests/services/test_trial_authorization_signer_service.py:10238-10247`), demonstrating that the gate is accessible through the module API.

This leaves the R3 finding materially unresolved: a token can be signed without successful fresh I12 verification, contrary to workorder §3.1 and the required issuance sequence.

**Required correction:** Remove caller-supplied sentinel registration as the provenance mechanism. Make successful I12 verification and admission registration one indivisible operation that constructs/records authority-owned evidence only after the verifier returns, without exposing a caller-invocable registration API that trusts a marker or DTO flag. Add a regression test that imports every relevant module attribute, attempts to register fabricated exact-type evidence with the exposed sentinel, and proves it cannot create a ticket or produce a signature. Preserve the explicit limitation that this local-test design is not an OS/process isolation boundary.

## Review notes

The R4 registry adds object-identity checks and the reported suite covers fabricated admission without prior registration. It does not cover the exposed-sentinel registration path. The supplied test log is recorded as evidence and was not rerun. I16 remains unaccepted; no later work or live operation is authorized.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.
