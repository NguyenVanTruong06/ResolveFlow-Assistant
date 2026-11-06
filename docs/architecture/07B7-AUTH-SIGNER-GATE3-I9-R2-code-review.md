# G07-AUTH-SIGNER-GATE3-I9 Revision R2 — Static Code Review

**Decision: ACCEPTED**  
**Scope:** Descriptor-Anchored Fresh Revocation Registry Read Core  
**Reviewed:** 2026-11-03  
**Review type:** Static source, test, and submitted-evidence review; tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `7276acb753b3473c3494ae9f46541a978a387252f07ff51e7bdee386ce403be6`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `87030014a24755d49dda6f4334dc8ea7304d4abb235467cdfa95126c00f83621`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `8770d96eec4c1aacd41efc551d1d05bf7a72c33c7838f7a73654b8b20914201d`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `789afdc880736be3585a79a802cc67aae09fcffc08e35be1a8f71be079026fc6`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I9/pytest.log` — SHA-256 `4a2ecb4253ec050acca34ebc482726a32d13463ba4a58ff62abc464e546898b5`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I9-R1-code-review.md` — SHA-256 `b78ed95f6552826f6753d53986e519dd8f6d2352a6207f1a967c30d7d1cf0956`.

The submitted artifact hashes match the workspace. The submitted log reports 195 passed, 0 failed, exit code 0. This is reported evidence only; it was not reproduced during this static review.

## R1 findings

### I9-R1-01 — Resolved: registry close failure now aborts before verification and anchoring

The loader clears its local registry descriptor reference before calling the single-close helper. Immediately afterward it checks the collected cleanup failures and raises `SignerLedgerDurabilityError` with code `ERR_REGISTRY_CLOSE_FAILED`, preserving the close error as `primary_error`/`__cause__` and retaining cleanup errors. Both `verify_signed_revocation_manifest()` and `check_and_advance_revocation_epoch_anchor_for_local_test()` occur after this gate, so the close-failure path cannot reach either call. The implementation does not retry the ambiguous numeric descriptor.

The added fault-injection test covers successful bounded reads followed by a simulated close error with both an absent anchor and a pre-existing anchor; it checks that the absent anchor stays absent and the existing anchor bytes remain unchanged. Static inspection confirms the verifier call is also after the abort gate. The test does not spy on the verifier call directly, so that particular assertion is established by control-flow inspection rather than a call-count assertion.

### I9-R1-02 — Resolved: pre-read and post-read `fstat` failures are structured

The initial and final `os.fstat()` calls each have their own `OSError` boundary and emit distinct `SignerLedgerDurabilityError` codes: `ERR_REGISTRY_PRE_READ_FSTAT_FAILED` and `ERR_REGISTRY_POST_READ_FSTAT_FAILED`. Each retains the originating exception as `primary_error` and exception cause. Both errors arise before verifier and anchor calls. The new test injects faults at both boundaries and confirms the expected stable code and that no anchor is created.

## Review conclusion and limits

The R2 changes close both R1 findings. The implementation preserves the descriptor-relative bounded fresh read, metadata stability checks, single-close behavior, and the accepted I7 verifier / I8 anchor APIs. No unresolved blocker was found within this slice's workorder scope.

**I9 R2 is accepted for the local temporary-directory verification and anchor composition slice only.** This does not accept Gate 3 as a whole, create a production trust root, authorize signer/service integration, or authorize Gate 4–7 or live operations. Gate 6 remains `NOT ACCEPTED` and incident-only.

No tests were run during this review. No Resolve, IPC, network, private-key loading, or production filesystem operation was performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
