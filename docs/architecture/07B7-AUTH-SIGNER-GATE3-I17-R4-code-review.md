# Gate 3-I17 Revision R4 — Static Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the I17 workorder, R4 implementation and tests, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` match the handoff values at review time.

The submitted log records **408 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

The R4 changes close the prior finding about accepting absent revocation evidence and unverified manifest DTOs: missing evidence and failed signature verification now result in a signed failure response, and all four identity categories are checked when evidence is accepted. One rollback protection gap remains.

## Finding

### I17-R4-01 — BLOCKER: Manifest verification omits the durable epoch anti-rollback check

In `LocalTestOracleVerificationClient.verify_authorization()` (`src/services/trusted_canary_dispatch_service.py:394–417`), the raw-manifest path calls `verify_signed_revocation_manifest()` and accepts its result when `verified` and `_verify_provenance_seal()` are true. The pre-verified-result path applies the same seal and expiry checks. The I7 verifier validates manifest schema, issuer signature, and expiry, while `_verify_provenance_seal()` proves only that the result came through that verifier; neither check establishes that the manifest epoch is current relative to the durable I8 epoch anchor.

Consequently, a previously valid, correctly signed, not-yet-expired manifest from an older epoch can be replayed and treated as authoritative. If a newer manifest revoked one of the four identities, replaying the older manifest can omit that revocation and allow the Oracle helper to sign `VERIFIED_VALID`. This does not satisfy the authoritative revocation registry and rollback protections specified by Plan R7 §§2 and 6.2 and implemented by the I8/I11 slices.

**Required correction:** bind every Oracle decision to fresh I11 revocation evidence that has passed the durable epoch-anchor rollback/tampering check, or invoke the accepted I11 composition for the registry on each authorization verification. Do not treat an I7-only signature/provenance seal as proof of current epoch state. Fail closed if the anchor is absent, stale, rolled back, mismatched, or cannot be checked. Add deterministic tests where a valid older signed manifest follows a higher anchored epoch and contains no revocation for the tested identity; assert the Oracle does not issue `VERIFIED_VALID`, with zero adapter/gateway calls and zero completed receipt. Also cover the unanchored-evidence case.

## Safety and disposition

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. The declared state remains `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

I17 R4 is **not accepted** pending correction of this finding and submission of updated source, tests, and authorized test evidence.
