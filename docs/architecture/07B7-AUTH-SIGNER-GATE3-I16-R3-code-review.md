# Gate 3-I16 Revision R3 Code Review

**Workorder:** G07-AUTH-SIGNER-GATE3-I16 — Local-Test Trial Authorization Issuance Core  
**Decision:** **CHANGES_REQUIRED**  
**Review type:** Static review; submitted test evidence checked, tests not rerun.

## Artifacts reviewed

| Artifact | Submitted SHA-256 | Review status |
|---|---|---|
| `.bridge/task.md` | `26a457fa300e21e0fa672c6a2f17c18d22eeee4a4e35413b6cb4409ef999625f` | Matches workspace |
| `.bridge/report.md` | `14732fba124c11a85efed90295d59995f441add9d98446c427f5e6ffe01fe2d9` | Matches workspace |
| `src/services/trial_authorization_signer_service.py` | `9b3edf753f10f32638240f1a1b754301c9c0363aa6f1a215bd4c7504812c28c2` | Matches workspace |
| `tests/services/test_trial_authorization_signer_service.py` | `02d4d8499f407223fb00b095825a7e21aa55d87a4427ccf0fcae4e73cbb1c4a7` | Matches workspace |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I16/pytest.log` | `9364fdaf1ac6777a7e8183ac3456b53955f7876cbe74a2b162937e822b6849ac` | Matches workspace; reports 342 passed, 2 warnings, exit 0 |

## Finding

### I16-R3-01 — BLOCKER: Ticket creation does not establish fresh I12 admission provenance

`LocalTestChallengeAuthority.create_issuance_signing_ticket()` at `src/services/trial_authorization_signer_service.py:5284-5333` is callable independently. Under the authority lock it checks that the challenge is consumed, the consumption result is the recorded object, and the evidence was reserved. It does not verify that `admission_evidence` is genuine, verified, or produced by the I16 issuance routine; nor does it validate the other ticket context arguments before storing them.

`_sign_for_issuance()` later checks that the supplied ticket is registered and single-use, and checks the admission DTO's exact type, `verified` booleans, and selected field equalities (`:5501-5565`). Those checks do not establish verifier provenance: the admission evidence and nested result DTOs can be constructed with matching claims and `verified=True`. A caller can therefore consume and reserve a genuine challenge, call the ticket factory directly with fabricated admission evidence/context, then submit a DTO matching that ticket to `_sign_for_issuance()`. This reaches HMAC signing without successful fresh I12 Director, Operator, and revocation verification.

This violates the workorder requirement that issuance itself perform fresh I12 verification and that signing be available only after that verified admission and successful I15 consumption. The new ticket proves authority registration and consumption, but not admission provenance.

**Required correction:** Make ticket creation an internal step of the issuance operation and bind its authority registration to the exact fresh I12 result and exact validated source objects from that invocation. Do not treat constructible `verified=True` DTO flags as proof. Ensure every signing entrypoint rejects a ticket whose admission was not registered by the successful I16 verification flow. Add a regression test that consumes/reserves a real challenge, invokes the ticket factory with a fabricated exact-type admission result and matching context, and proves no HMAC/signature or authorization is emitted.

## Review notes

The submitted tests report coverage for ticket forgery/reuse, all 32 claim checks, and forged/unverified admission variations. The remaining gap is provenance of a ticket created through the authority's callable factory: tests must exercise that path rather than only mutation/replacement of admission data after the issuance routine has created a ticket. Evidence is recorded as submitted and tests were not rerun.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize later slices, production key custody, dispatch, Resolve access, or live operations.
