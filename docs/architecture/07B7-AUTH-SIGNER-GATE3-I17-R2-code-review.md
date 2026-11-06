# Gate 3-I17 Revision R2 — Static Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the I17 R2 workorder, implementation, tests, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` matched the handoff values at review time.

The submitted log records **396 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

The R2 changes address the prior plan-binding and placeholder-receipt-metadata findings: the plan is now required and checked against the authorization/action, its canonical digest is recomputed before Oracle admission, and receipt fields are sourced from the plan and authorization.

## Findings

### I17-R2-01 — HIGH: Oracle public-key subclass can bypass response-signature verification

In `src/services/trusted_canary_dispatch_service.py:417`, the constructor accepts `oracle_public_key` using `isinstance(oracle_public_key, Ed25519PublicKey)`. In `_verify_oracle_response_11_points` (around line 773), the service calls the object's overridable `.verify()` method directly.

A caller-supplied `Ed25519PublicKey` subclass can override `verify()` to return without checking the signature. The remaining response-field checks do not establish that the Oracle signed the response, so a forged `VERIFIED_VALID` response can pass the first of the required 11 checks and proceed toward the adapter. This defeats the stated signature-verification gate for a caller-controlled key object.

**Required correction:** normalize the supplied key to a provider-created concrete Ed25519 public-key object from its raw public bytes, or reject anything other than the provider's exact concrete type. Verify only through that normalized object. Add a negative regression test using a subclass whose `verify()` is a no-op; it must be rejected before any adapter/gateway or receipt-completion call.

### I17-R2-02 — HIGH: Local test Oracle signs `VERIFIED_VALID` without validating authorization HMAC or revocation

`LocalTestOracleVerificationClient.verify_authorization()` (`src/services/trusted_canary_dispatch_service.py:274–299`) computes the canonical authorization digest, then derives `is_valid` from the validity window, request/action-payload digest equality, and target UID equality. It does not verify `auth.authorization_hmac` with the I13 verification-only capability and does not check whether the authorization, Director key, or Operator key is revoked. It then signs the resulting response (`:336–341`).

Thus this helper can emit an authentic Oracle signature and `VERIFIED_VALID` for an authorization carrying an invalid HMAC or revoked identities, provided the other checked values match. The Dispatcher correctly checks the Oracle response signature and its 11 response claims, but those checks cannot compensate for an Oracle response whose decision was made without the required authorization verification. Current happy-path and regression evidence therefore does not establish this part of the Plan R7 Oracle contract.

**Required correction:** make the local Oracle helper verify the authorization with an accepted verification-only capability and check the relevant verified revocation state before signing `VERIFIED_VALID`; otherwise it must return a signed failure response. Add negative tests for invalid HMAC and revoked authorization/key identities, asserting no adapter/gateway call and no completed receipt. Keep this entirely local-test-only and use ephemeral test material.

## Safety and disposition

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. The findings concern the local-test Oracle verification boundary and the validity of the acceptance evidence; they do not change the declared state: `ready_to_execute=False`, Gate G07 and M1/M2/M3 remain `OPEN`, and Gate 6 remains `NOT ACCEPTED` and incident-only.

I17 R2 is **not accepted** pending correction of both findings and submission of updated source, tests, and authorized test evidence.
