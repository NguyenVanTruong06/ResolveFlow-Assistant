# G07-AUTH-SIGNER-GATE3-I10 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Local-Test Descriptor-Anchored Revocation Issuer Key Reader  
**Reviewed:** 2026-11-03  
**Review type:** Static source, test, and submitted-evidence review; tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `716bc17e4b21d870bf8afa7b1d20f6dea9b00ccc11ce7723c764f3063d14f659`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `67fa02e6b1c2763e09e767bc611451a7b4c7593068fd65ecaa652fa5ac5157ce`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `fcfbcabd9ca5b737676ef6fb778aad1bfd8e8e0ce22a82396fe75fcad7583962`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `0fb18c490bff19a96766df556d4137ad46437e524d8edd6d1e77fd85cd80f2fc`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I10/pytest.log` — SHA-256 `539d9cd1747085c7a5dc1edb74781ae6f1bd83181c91f33e9bf2cd75e75727b4`.
- Predecessor review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I9-R2-code-review.md` — SHA-256 `1347af980a93ec4a1a28838e6e6291bb6c54f661d9af9f92afc259f9b7f0e988`.

The submitted hashes match the workspace. The submitted log reports 210 passed, 0 failed, exit code 0. This is reported evidence only; it was not reproduced during this static review.

## Finding

### I10-R1-01 — Temporary-root confinement resolves the requested path before rejecting outside paths — HIGH

`load_revocation_issuer_public_key_for_local_test()` delegates to `_open_anchor_dir_under_temp_root()` at `src/services/trial_authorization_signer_service.py:2544`. That helper first calls `os.path.realpath(str(anchor_directory))` (`:1863`) and `os.path.islink(str(anchor_directory))` (`:1876`), then checks whether the resolved path is beneath the temporary root (`:1865`). `realpath()` resolves filesystem components before the confinement rejection. The new test explicitly calls the API with `/var/lib/resolveflow/signer` (`tests/services/test_trial_authorization_signer_service.py:3278`), so the claimed test-local boundary performs filesystem path resolution against the prohibited production location before rejecting it. This conflicts with the I10 workorder's absolute prohibition on production filesystem access, even though the key basename is never opened.

**Required change:** Reject absolute paths outside the lexical temporary-root namespace before any path-resolution or filesystem query. Establish the descendant relationship by descriptor-relative, no-follow traversal from an opened temporary-root descriptor; do not use `realpath()` or `islink()` on the untrusted candidate before confinement is established. Replace the production-path test with a non-production sentinel or a safely isolated test that proves rejection occurs before any filesystem query/open on the candidate. Add a regression assertion that the candidate path is not passed to path-based filesystem helpers or `os.open()`.

## Resolved requirements and review notes

The new reader uses the fixed basename, opens relative to the returned directory descriptor with no-follow/nonblocking flags, checks file type/owner/link count/mode/size, performs a bounded read and metadata stability check, closes descriptors before PEM parsing, rejects private/multiple/trailing PEM data, and restricts the parsed object to Ed25519 public keys. Close failures abort before parsing and are not retried. No additional source finding was identified in the reviewed I10 code path.

The workorder asks for an exact `Ed25519PublicKey` type, while the implementation uses `isinstance()`. `cryptography` exposes `Ed25519PublicKey` as an abstract interface and returns a concrete backend implementation, so exact `type(value) is Ed25519PublicKey` would reject valid provider objects; the nominal interface check is appropriate here.

## Review conclusion and safety boundary

I10 R1 is **not accepted** until `I10-R1-01` is resolved within the existing two-file allowlist. Do not proceed to another implementation slice on the basis of this submission.

No tests were run during this review. No Resolve, IPC, network, key loading, or production file-content operation was performed by the reviewer. The implementation's current outside-path test does, however, invoke path-resolution helpers on a prohibited production path and must be changed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only.
