# G07-AUTH-SIGNER-GATE3-I8 Revision R3 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Digest-Bound Durable Revocation Epoch Anchor Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `86d3455cddbe6efe0f3c469ade3c86ea574c82819a79f0f00984293f78ceb47e`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `d8e60b11f56807cb3c19ac6d47a98dec536a11cc68c0c5f14f350b776df5bfe4`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `a6cc5d33ddf7d5d37e1bb058b5c4aa3f33a56e86da55e3fe22449ce8c80701f3`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I8/pytest.log` — SHA-256 `b560488daf8ce0df4a0972f5b38648ca70f8ebfea515aad9cf66c8a7b4d5b45b`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `3673c5aad2eee2fa5a3ea27be084c5b81e8cdefbfe0a1c88ac9137f335203c8d`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I8-R2-code-review.md` — SHA-256 `bd4a8cf7eefa20ff24a2b293c0e0d5a58e4b8ccbcb159f25e26b76d9c2529c6b`.

Submitted hashes match the workspace. The submitted log reports 184 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R2 finding disposition

- `I8-R2-01` (caller-constructed verification result): **RESOLVED for the defined local-process boundary** — the verifier adds an HMAC provenance seal over canonical claims, epoch, and digest; the anchor helper checks it before filesystem mutation, and a self-constructed result without the seal is rejected.
- `I8-R2-03` (structured persistence errors and cleanup preservation): **RESOLVED** — write and file-fsync failures are normalized to `SignerLedgerDurabilityError`; primary and cleanup errors are retained.
- `I8-R2-04` (cross-process locking evidence): **IMPLEMENTED, test synchronization needs a small reliability correction** — a spawned-process flock test now exists, but its result collection is racy (finding below).
- `I8-R2-02` (directory path race): **PARTIALLY RESOLVED** — the function now opens the resolved path and compares descriptors, but it still does not establish ancestry from the opened temp-root descriptor.

## Findings

### I8-R3-01 — Anchor directory is not proven to remain beneath the opened temporary root — HIGH

The helper opens and stats `real_temp`, then closes `temp_root_fd` before opening the anchor directory. The anchor directory is later opened by its full resolved pathname, and the only relationship checked against the temp-root descriptor is `st_dev` equality. The `os.stat(real_dir)` comparison proves that the opened descriptor matches that pathname at the instant of the comparison, not that the opened directory is a descendant of the previously opened temp root. An ancestor replacement/mount redirection between `realpath()` and open/stat can therefore defeat the test-only boundary; same-device equality is not an ancestry proof.

**Required change:** Keep the temp-root descriptor open and traverse each relative path component with descriptor-relative `openat` and `O_NOFOLLOW | O_DIRECTORY`, rejecting `..`, empty/absolute escapes, and mount/path changes as appropriate. Alternatively, explicitly narrow the contract so the path is trusted and remove the claim that aliases/races are prevented. Add a deterministic safe test for a redirected ancestor or mocked descriptor identity mismatch; do not touch production paths.

### I8-R3-02 — Cross-process test uses unreliable `Queue.empty()` result collection — MEDIUM

`test_cross_process_flock_concurrency` joins the worker processes and then drains results with `while not results_queue.empty()`. `multiprocessing.Queue.empty()` is documented as unreliable because feeder-thread delivery may lag process exit; the test can observe empty before all ten results are visible and fail intermittently. This weakens the required independent-process evidence even though the worker processes and flock calls are present.

**Required change:** Read exactly ten results using `get(timeout=...)`, assert each worker exited (and is no longer alive), and fail with a useful timeout diagnostic if a child did not report. Keep the test bounded and temporary-directory-only.

## Review conclusion and safety boundary

R3 resolves the forged-result construction path for ordinary in-process DTO construction and addresses structured persistence failures. The remaining directory-ancestry gap leaves the local-only filesystem boundary short of the workorder, and the multiprocess test can be flaky. I8 R3 is therefore **not accepted** pending correction within the existing two-file allowlist.

No tests were run during this review. No Resolve, IPC, network, key, or production filesystem operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize production trust-root loading, signer service wiring, authorization issuance, or live operations.
