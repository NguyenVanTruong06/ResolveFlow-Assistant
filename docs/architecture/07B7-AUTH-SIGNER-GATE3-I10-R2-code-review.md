# G07-AUTH-SIGNER-GATE3-I10 Revision R2 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Local-Test Descriptor-Anchored Revocation Issuer Key Reader  
**Reviewed:** 2026-11-03  
**Review type:** Static source, tests, and submitted-evidence review; tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `6cfc92f6b8b6d1dd21b0c771f55840cffe3b03e633041b70500944d111ccb3cf`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `747e9e8842085f545498b642dd74b98432cb884a5cd98ed40d90527103fee6b6`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `9900f436c90fb89ac3d1dfc52b086357da600a437de5bc4c2bf51ed2b4dba1d7`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `4d9600fd5b4a2d88bb9347f39db62e1348dc5e36483715e0feeb62e8a870bef1`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I10/pytest.log` — SHA-256 `a4020a704153db644d894abe85b2ef05a5f4feed45bab78b2ba8a4c6f4e6f748`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I10-R1-code-review.md` — SHA-256 `a59f434ceb17040b3e503cf10f41f2f7bc5f78a3e9953ffecd0f6022d1f1c5e7`.

Submitted artifact hashes match the workspace. The submitted log reports 211 passed, 0 failed, exit code 0. This is reported evidence only; it was not reproduced during this static review.

## R1 finding disposition

`I10-R1-01` is resolved for paths rejected lexically: the helper checks banned path strings and candidate membership in the temp namespace before candidate path queries, and the new tests intercept path-based helpers for outside candidates. The production-path test input was replaced by a non-production sentinel.

## Remaining finding

### I10-R2-01 — Final candidate identity check reopens the untrusted path namespace — HIGH

After descriptor-relative, `O_NOFOLLOW` traversal, `_open_anchor_dir_under_temp_root()` calls `os.stat(norm_candidate)` at `src/services/trial_authorization_signer_service.py:1966` and compares that path lookup with `os.fstat(dir_fd)`. This contradicts the helper's stated invariant that it never calls `os.stat()` on the candidate and reintroduces a path-based time-of-check/time-of-use boundary: if a traversed component is renamed or replaced with a symlink between the descriptor walk and this call, `os.stat()` follows the current pathname and can query a location outside the temp-root descriptor tree before the inode mismatch is raised. The new regression test proves no path query occurs for lexically rejected outside inputs, but does not cover this in-root traversal race.

**Required change:** Remove the candidate-path `os.stat()` comparison or replace it with a descriptor-relative, no-follow identity check using a retained parent directory descriptor and the final basename (`stat`/`fstatat` relative to that descriptor). Do not resolve or stat the candidate by absolute path at any point. Add a fault-injection test that swaps/replaces the final path after descriptor traversal and proves no candidate absolute-path filesystem helper is called and no outside target is followed.

## Review conclusion and safety boundary

The R1 outside-path preflight issue is corrected, and the public-key reader otherwise retains the bounded descriptor read, metadata checks, single-close handling, strict PEM parsing, and public-key-only return contract. I10 R2 is **not accepted** until `I10-R2-01` is resolved within the existing two-file allowlist.

No tests were run during this review. No Resolve, IPC, network, key loading, or filesystem access to a production path was performed by the reviewer. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only.
