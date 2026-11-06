# Gate 3-I12 Revision R6 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Review scope:** R5 challenge provenance secret handling, challenge identity allocation, and the admission provenance gate.  
**Review mode:** Static source/artifact review only. No tests, Resolve, IPC, or live operations were run.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `ba43b64d0be5ee4f01dfd6cb43fae2b7b4d397aa9986ffe1c5f6498272cdcd8e`
- Completion report: `.bridge/report.md` — SHA-256 `e6750a5e0baaba2e3ee2ace468b1698c7f4d100eaaa8fe86203c05e60e179606`
- Source: `src/services/trial_authorization_signer_service.py` — SHA-256 `4af6fd666505a8688199659ddf9d8652e3fbef39b061519fa1e76ae158ac3c38`
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `6c364bb7deac773db461bf8b73ee4b8419a78607dc95b5a1ddade610f474f91a`
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `5897d6d7aa5f7541da06e870440ae97cb63071f4bf347e118ce89d178aef96c9`; report states 236 passed, 2 warnings. Not rerun during this review.

## Finding

### I12-R6-01 — BLOCKER: Provenance secret remains readable and replaceable by admission callers in the same Python process

R6 removes the source-literal key and initializes `_SIGNER_CHALLENGE_PROVENANCE_SECRET` with `os.urandom(32)` (`src/services/trial_authorization_signer_service.py:449`). However, this is still a module-level Python attribute. `verify_challenge_provenance()` computes the expected seal from that same global (`:1160-1182`), and the module also exposes the seal-generation helper. Python’s leading underscore is a naming convention, not an access boundary: any caller able to import/use the admission module in this process can read `module._SIGNER_CHALLENGE_PROVENANCE_SECRET` and calculate a valid HMAC. The caller can also replace the module attribute; verification then uses the replacement value. There is no process, OS identity, capability, or protected key-store boundary separating the admission caller from this value in this slice.

The new negative test only computes a seal using the former guessed source literal and checks tampering (`tests/services/test_trial_authorization_signer_service.py:5634-5733`). It does not test reading the runtime module attribute, replacing it, or using the public `issue_operator_challenge()` entry point. Therefore it does not establish the report’s claim that possession of the public module/API surface cannot mint accepted provenance.

**Required correction:** Keep the provenance key outside the caller-accessible Python module/process boundary, or use an independently verifiable signer credential whose private signing capability is unavailable to admission callers. If that requires the separately isolated signer service or key boundary outside I12’s allowlist, stop and report the dependency rather than treating an underscore-prefixed module global as secret. Add negative coverage for the actual supported caller boundary and confirm that the verifier can validate issuer provenance without exposing a minting capability to admission code.

## R5 findings status

- Caller override parameters for `preallocated_authorization_id` and `preallocated_execution_id` have been removed; the issuance method now generates both internally (`:1076-1089, 1114-1117`). Context binding and admission equality checks remain present. The static review found no remaining caller override for those two identities.
- R5-01 is **not closed** because the replacement runtime secret is accessible to any code with access to the shared Python module/process. The challenge issuance method remains in the same service module, so the claimed unforgeable provenance boundary is not achieved.

## Safety and scope

The submitted workorder/report retain the verification-only/local-test boundary, `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED`/incident-only. This review authorizes no authorization issuance, key provisioning, Resolve access, Gate 4–7 activity, or live operations. Submitted test results are recorded as evidence only and were not independently reproduced.
