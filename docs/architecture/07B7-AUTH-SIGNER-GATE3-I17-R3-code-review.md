# Gate 3-I17 Revision R3 — Static Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the current I17 workorder, implementation, tests, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` match the handoff values at review time.

The submitted log records **404 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

The R3 changes close the prior findings concerning Oracle public-key subclassing and checking authorization HMAC/revoked IDs when the supplied test data is configured. One revocation trust gap remains.

## Finding

### I17-R3-01 — BLOCKER: Revocation decision trusts an optional, unverified manifest

`LocalTestOracleVerificationClient.__init__()` accepts both `revocation_manifest` and the Director/Operator key IDs as optional (`src/services/trusted_canary_dispatch_service.py:294–297`). In `verify_authorization()` (`:419–431`), the manifest is consulted only when one was supplied, and its revocation lists are read directly. No issuer signature, validity window, freshness, issuer identity, or epoch/rollback status is verified at this boundary. The helper can therefore sign a `VERIFIED_VALID` response when no authoritative revocation state was supplied, or when a caller supplies a fabricated `SignedRevocationManifest` with empty lists. Optional Director/Operator key IDs also cause those revocation checks to be skipped.

This is materially different from the Plan R7 §2 Oracle contract, which requires checking the authoritative signed revocation registry. The R3 tests exercise rejection when configured IDs appear in supplied lists, but do not establish that the lists are authentic, current, or present. A signed Oracle response cannot repair this omission: it attests to the helper's unchecked decision.

**Required correction:** require complete verified revocation evidence for every authorization decision. Consume a verified I11 result, or verify the manifest with the authorized issuer public key and explicit verification time using the accepted verification core; fail closed if the manifest, issuer/key identities, freshness, or epoch state is missing or invalid. Do not accept a raw DTO or caller-maintained revocation lists as authoritative. Add negative tests for absent evidence, fabricated/invalid signature, expired/stale manifest, and missing Director/Operator key identity; each must prevent a `VERIFIED_VALID` response and yield zero adapter/gateway calls and zero completed receipt.

## Safety and disposition

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. The declared state remains `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

I17 R3 is **not accepted** pending correction of this finding and submission of updated source, tests, and authorized test evidence.
