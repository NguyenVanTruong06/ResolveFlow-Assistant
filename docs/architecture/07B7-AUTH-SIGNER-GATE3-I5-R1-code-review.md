# G07-AUTH-SIGNER-GATE3-I5 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Operator Consent Verification Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `0f4881f38c9ac99e86221e1efba600b0a9a659fb83ddd070433e4c1a4258a434`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `423863120184a00ddf8a58456ea8b7c786b8eaf3465ababcf53358e51ba2c77f`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `2c566c92c72e5bd163274b92c8d4f8f7ebcf62aa89ad5d60e9e3d62f065fe7d8`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I5/pytest.log` — SHA-256 `d368517f6ca21a0f7cf108ce939f4415f42e379b67a4679935c69e3b4efa7f59`.

The submitted hashes match the workspace. The log records 138 passed, 0 failed, exit code 0. Tests were not rerun during this review.

## Findings

### I5-R1-01 — Context scope literals lack primitive string type gates

**Severity: MEDIUM (contract correctness)**

`OperatorConsentVerificationContext.__post_init__()` checks the values of `expected_action_type` and `expected_track_type` using equality alone (`src/services/trial_authorization_signer_service.py:419-427`). It does not require `type(value) is str`. A caller can supply a non-string object with custom equality behavior that compares equal to the required literal; the later comparisons at `:674-682` also use equality alone. This contradicts the workorder's strict malformed-context/type-gate requirement and makes the context validation contract dependent on user-defined comparison methods.

**Required correction:** Require exact built-in `str` for both fields before comparing against `"INSERT_CLIP"` and `"video"`; add negative tests using non-string equality lookalikes. Preserve the envelope's own literal validation.

### I5-R1-02 — Verification instant permits an overridable `datetime` subclass

**Severity: MEDIUM (freshness gate robustness)**

The verifier accepts `isinstance(verification_time_utc, datetime)` and then invokes `tzinfo`, `utcoffset()`, and subtraction (`:508-516`, `:542`). A datetime subclass can override these operations, so a value that is not a plain timestamp can influence the UTC and freshness checks. The API deliberately receives this value from a future composition boundary; accepting an overridable object weakens the exact-instant premise of the freshness and challenge-expiry gates.

**Required correction:** Require `type(verification_time_utc) is datetime` (or first normalize a strictly validated primitive representation to a built-in UTC datetime) before timezone and arithmetic checks. Add a negative test with a datetime subclass; retain acceptance of ordinary timezone-aware UTC `datetime` values and rejection of naive/non-zero-offset values.

## Positive review notes

- The verifier checks consent freshness and challenge expiry against UTC instants, uses the explicit time input, and does not read the system clock (`:518-552`).
- It binds challenge ID/nonce, operator ID/key ID, target/source/action/geometry claims, and uses `hmac.compare_digest` for digests (`:554-699`).
- It verifies the Ed25519 signature over `SignedOperatorConsentEnvelope.canonical_signing_payload_bytes()` (`:700-721`). Approval and plan digests are recomputed through accepted DTO/source-owned helpers when those DTOs are supplied (`:590-628`).
- The returned result contains no key object or authorization capability. This slice remains explicitly non-authoritative: expected context claims and candidate public key are caller/composition inputs, and the implementation does not consume challenges or issue authorization.

## Disposition and safety boundary

Do not mark I5 R1 accepted until I5-R1-01 and I5-R1-02 are fixed within the existing two-file allowlist and reviewed. This decision does not allege a production authorization bypass: the workorder explicitly limits I5 to a pure verification core with no trusted-key ownership or authorization issuance.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
