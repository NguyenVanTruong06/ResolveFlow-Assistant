# G07-AUTH-SIGNER-GATE3-I10 Revision R3 — Static Code Review

**Decision: ACCEPTED**  
**Scope:** Local-Test Descriptor-Anchored Revocation Issuer Key Reader  
**Reviewed:** 2026-11-03  
**Review type:** Static source, tests, and submitted-evidence review; tests were not executed during this review.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `85e9ebcf5f6722d2b6f501424a57cf4a125ffe130f5911424c81d8de0c95dc67`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `6940a31a6753edc2883f44f8a1cf74819ce36d2f51efe1227ab2e3ad7062cf83`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `6a19bc23d880ffad735139e30c28d780af271483d8b530ba55bdb0ebdc404ce3`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `cf3177331738f42c2e8fdd6a49278d87d8d858d3d8b0db4d9df5a0f96737efe5`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I10/pytest.log` — SHA-256 `073aae0292e037c66bcaa3c71fd0d4a529a67d5747bbb8b112372d0a0aa07b5a`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I10-R2-code-review.md` — SHA-256 `b8423b1763b93f21e033264fc28da9e07ea0daee06fb688e421172945512bf36`.

Submitted hashes match the workspace. The submitted log reports 212 passed, 0 failed, exit code 0. This is reported evidence only; it was not rerun during this review.

## Finding disposition

`I10-R2-01` is resolved. The final candidate-path `os.stat(norm_candidate)` has been removed. Each traversed component is now opened relative to the current parent descriptor with `O_NOFOLLOW`, validated by `fstat()`, and compared with `os.stat(part, dir_fd=current_fd, follow_symlinks=False)`. The helper no longer performs an absolute-path filesystem lookup on the candidate. Lexical rejection still precedes all candidate filesystem access for paths outside the temporary root.

The new `test_final_path_swap_after_traversal_uses_no_candidate_absolute_path_and_follows_no_outside_target` guards against absolute candidate `stat()` and injects an identity mismatch through the descriptor-relative lookup. The existing traversal tests retain coverage for inode mismatch, device mismatch, and insecure parent directories. Static inspection found no remaining I10 blocker within the two-file allowlist.

## Review conclusion and limits

**I10 R3 is accepted for the local temporary-directory public-key reader slice only.** Acceptance does not establish a production trust root, authorize key provisioning, signer/daemon/RPC integration, authorization issuance, Gate 4–7 work, or live operations. Gate 3 remains incomplete and Gate 6 remains `NOT ACCEPTED` and incident-only.

No tests were run during this review. No Resolve, IPC, network, key loading, or production filesystem operation was performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
