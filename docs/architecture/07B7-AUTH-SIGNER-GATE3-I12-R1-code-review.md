# G07-AUTH-SIGNER-GATE3-I12 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Local-Test Authorization Admission Cross-Binding Core  
**Review date:** 2026-11-03  
**Review type:** Static source, tests, workorder, and submitted-evidence review. Tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `b1016139213be1b94cdbfc2c0409225f7c9585933ec3c4c363aa526e8cd485dd`.
- Completion report: `.bridge/report.md` — SHA-256 `e7eda0860ed563052ba537051f826aedf5e48c6d55a45b3c7242882837ef668a`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `def3addc36425dc44466d6f8030628d697bce6652481cb569fa5931495e7aa2a`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `dafa32d1b2f3eb5c7b52e349a28013d977521fd25fe7db69d5ec6597255ddcdb`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `7e228f9f34bbce4d1157f8bb07afd8e1aee4fe7d22282689d3f13d18722317b2`.
- Prerequisite review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I11-R1-code-review.md` — SHA-256 `c99a4cc2939a461fb43b1007d1c7129cbb7e19829a9c9091a6c9b8791d0e974e`.

The submitted source, test, and log hashes match the workspace. The submitted log reports 226 passed, 0 failed, exit code 0. This is submitted evidence only; it was not reproduced during this static review.

## Findings

### I12-R1-01 — BLOCKER: Caller-implemented Ed25519 key subclasses can bypass signature verification

At `src/services/trial_authorization_signer_service.py:2969` and `:2993`, the admission function accepts public keys using `isinstance(key, Ed25519PublicKey)`. The downstream I4/I5 verifiers use the same check and invoke the supplied object's `verify()` method. Since `Ed25519PublicKey` is an extensible interface, a caller-defined subclass can implement `verify()` as a no-op, pass these checks, and cause invalid Director or Operator signatures to be reported as verified. This defeats the cryptographic checks at the boundary that produces the admission evidence.

Use a representation whose verification implementation is controlled by the cryptographic provider—for example, accept validated encoded public-key bytes and import them through the provider—or otherwise enforce trusted concrete key provenance. Add negative tests using a custom subclass whose `verify()` accepts every signature. Ensure the same protection applies to both I4 and I5 calls.

### I12-R1-02 — BLOCKER: Operator target/action claims are not bound to the approved plan

At `src/services/trial_authorization_signer_service.py:3140–3185`, target UIDs/names, source path/digest, action digest, action type, track, and geometry are compared only with `OperatorConsentVerificationContext`. That context explicitly documents those fields as caller-prepared assertions without independently trusted provenance at `:466–469`. The admission function never derives or compares these claims against the approved `execution_plan` or its source/action data.

The submitted happy-path fixture demonstrates the gap: it creates the plan via `make_valid_fixture()` (`tests/services/test_trial_authorization_signer_service.py:3963`) but supplies separate target, media, and action claims (`:3976–3987`), then accepts the matching operator consent/context. A valid Director approval and a valid consent over unrelated caller assertions can therefore produce `verified=True` evidence for a tuple that is not established as the action authorized by the Director-approved plan.

Bind every authorization-relevant operator claim to source-owned plan/action data, or stop and report the missing authoritative contract rather than treating caller context as the binding source. Add a negative test where a valid signed consent/context disagrees with the plan's target, source, or action and assert no evidence is returned.

### I12-R1-03 — HIGH: I11 advances the durable revocation anchor before admission cross-binding completes

The I11 call occurs at `src/services/trial_authorization_signer_service.py:3060–3065`, before the remaining cross-binding and revoked-key checks at `:3067–3207`. I11 is not read-only: `load_verify_and_anchor_revocation_manifest_for_local_test()` calls `check_and_advance_revocation_epoch_anchor_for_local_test()` (`:2530–2535`). Thus a later mismatch or revoked Director/Operator key rejects the admission but may already have advanced the durable epoch anchor.

This contradicts the I12 workorder's verification-only/no-state-mutation requirement. The interface must be resolved before this slice can be accepted: provide a non-mutating I11 verification/read path for admission, or explicitly revise the contract to authorize the monotonic anchor update as the sole side effect and define its ordering. Add a test proving the selected contract for a rejected cross-binding/revoked-key case.

### I12-R1-04 — MEDIUM: Valid non-ASCII operator IDs fail with an unstructured `TypeError`

At `src/services/trial_authorization_signer_service.py:3108–3109`, `hmac.compare_digest()` receives operator IDs as Python strings. It raises `TypeError` for non-ASCII strings. The consent contract accepts any non-empty, unpadded string (`src/core/contracts/signed_operator_consent_envelope.py:167–170`), and `OperatorConsentVerificationContext` has the same broad string rule. A valid identity such as `operator_đặng` can pass I5 verification but fails here with an unstructured exception, after the I11 call and its anchor side effect.

Use exact string equality for identifiers, or compare explicitly encoded UTF-8 bytes after validating their types. Add a non-ASCII identity success case and confirm the function returns its normal structured evidence.

### I12-R1-05 — HIGH: Registry-path policy validation occurs after verifier and trust-key access

At `src/services/trial_authorization_signer_service.py:3009–3018`, the registry-directory gate checks only type, emptiness, and outer whitespace. It does not apply the local-temporary-root/path policy before calling Director and Operator verification. More significantly, the I11 composition reads the issuer public-key file first and only then processes the registry directory (`:2878–2885`). An invalid or forbidden registry path can therefore trigger verifier calls and trust-file access before rejection, contrary to the I12 workorder's requirement that invalid path inputs be rejected before file access and verifier calls.

Validate both directories against the allowed temporary-root policy at the I12 boundary before any verifier or key-reader call, using a path-validation operation that does not touch candidate filesystem paths. Add spies proving invalid trust and registry paths cause zero I4/I5/I11 calls and zero key-file access.

## Review conclusion and limits

**I12 R1 is not accepted.** The findings affect signature authenticity, cross-binding to the approved plan, state mutation on rejected admission, and required pre-I/O validation. Address the findings in a bounded revision and resubmit for review.

No tests were run during this review. No Resolve, IPC, network, private-key, or production trust-root operation was performed. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only.
