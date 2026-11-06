# G07-AUTH-SIGNER-GATE3-I7 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Signed Revocation Manifest Verification Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `859b3701392b7260720edf5cd61a708011c60be1611828e55c3b987c0e716f57`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `7a7e2e47ab487a9c64a438eb195d943217c74968f4022c650b54b899791ec4fa`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `5b75b626544d383912cdbee51eb70b0ab8f14cf031138dde53ddc68ae6a2d971`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I7/pytest.log` — SHA-256 `e0aeb1fd0a80074c8f727e26e2ac63fb4a868dd3dd5061223dea1c3f21d5fa31`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `b3579665d02815fcfc19226cbe63d658f723130cddd2febfeb382a9639a81cdf`.

Submitted hashes match the workspace. The submitted log records 164 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## Findings

### I7-R1-01 — Frozen manifest and verification result expose mutable signed claim lists — HIGH

`SignedRevocationManifest` is declared `frozen=True`, but its four revocation-list members are ordinary mutable lists (`src/services/trial_authorization_signer_service.py:1537-1553`). `from_dict()` passes the parsed lists into the DTO directly (`:1633-1653`), and the returned frozen `RevocationManifestVerificationResult` exposes that same manifest object (`:1679-1687`, `:1750-1755`). A caller can therefore mutate `result.manifest.revoked_trial_key_ids` or another list after signature verification. The exposed verified claims then no longer match the verified signature or `canonical_claims_digest_sha256`, defeating the result/DTO immutability contract and making downstream revocation decisions unsafe.

**Required correction:** Store revocation collections immutably (for example, validate exact JSON lists at the parse boundary, then convert to tuples in the DTO) or otherwise ensure callers cannot mutate the verified claims. Ensure serialization and result APIs expose immutable values or defensive copies that cannot change the DTO. Add tests that mutation through the verified result and through a caller-retained input collection cannot alter the verified manifest or digest.

### I7-R1-02 — Valid uppercase hexadecimal signature encodings are rejected — MEDIUM

The manifest schema and workorder require a 128-character hexadecimal Ed25519 signature, but `SignedRevocationManifest.__post_init__()` accepts only lowercase `a-f` (`:1623-1631`). Uppercase hex is a valid representation of the same 64 signature bytes and is not excluded by 07B7 §6.1 or this workorder. Such a correctly signed manifest is rejected before verification, creating an undocumented serialization incompatibility.

**Required correction:** Accept both uppercase and lowercase hexadecimal characters for the exact 128-character signature field; decode to the same 64 bytes for Ed25519 verification. Add a test that uppercases a valid signature encoding and verifies successfully while retaining rejection of non-hex, odd-length, and wrong-length values.

## Positive review notes

- The parser uses the existing strict JSON parser, then enforces the exact ten-member schema, strict primitive/range checks, unique revocation IDs, pinned `issuer_id`, UTC timestamp ordering, and explicit verification-time expiry.
- The canonical serializer enumerates the nine non-signature claims and uses the existing RFC 8785 implementation; the verifier checks Ed25519 and returns a digest of those exact canonical bytes.
- The source remains within the two-file allowlist. It does not load a pinned key, read persistent files, update an epoch anchor, inspect the system clock, issue authorization, or wire production service behavior.

## Disposition and safety boundary

Do not mark I7 R1 accepted until `I7-R1-01` and `I7-R1-02` are corrected and reviewed within the existing two-file allowlist. This review covers only the pure verification core; it does not accept trust-root ownership, epoch anti-rollback, revocation enforcement in issuance, or production wiring.

`ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
