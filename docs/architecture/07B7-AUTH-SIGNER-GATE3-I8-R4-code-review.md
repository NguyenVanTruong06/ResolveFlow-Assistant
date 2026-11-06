# G07-AUTH-SIGNER-GATE3-I8 Revision R4 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Digest-Bound Durable Revocation Epoch Anchor Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `91b853659a3f2fffc5e11d8d086ec3f003484a330561038d1e13171637ecc8ac`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `5502103d1e7f489c6f20f28856e22c1896afa2f340750a1cc107be9be411697e`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `fb5d381d23fbda43510594b9c0369c84032c5eeb9f689fe62186572f55cf20b6`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I8/pytest.log` — SHA-256 `101bba2fc731aa41fef1f7a60f558ffa80ef4926b58ee855f4c2f84d972067cc`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `87de9125e98301539d99da2f8e539ad8465cfcda0f7192f2c31c471bbea5aa15`.
- Prior review: `docs/architecture/07B7-AUTH-SIGNER-GATE3-I8-R3-code-review.md` — SHA-256 `7032356834081b1319a823ef7c9ce653fce74ebb727b589a5694a0963ece6eaa`.

Submitted hashes match the workspace. The submitted log reports 185 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## R3 finding disposition

- `I8-R3-01` (prove descent beneath the temporary root): **RESOLVED** — `_open_anchor_dir_under_temp_root()` retains the temp-root descriptor and opens each relative component with `O_DIRECTORY | O_NOFOLLOW` using `dir_fd`; it checks directory type, UID/device, and compares final descriptor identity with the resolved path.
- `I8-R3-02` (multiprocessing queue race): **RESOLVED** — the test reads exactly ten results using bounded `Queue.get(timeout=10.0)`, then joins each worker and checks that it exited.

## Finding

### I8-R4-01 — Writable intermediate directories are not rejected during traversal — HIGH

In `_open_anchor_dir_under_temp_root()`, every traversed component is checked for directory type, device, and owner. The `st_mode & 0o022` group/world-write check is applied only to the final anchor directory after traversal. A group/world-writable intermediate component owned by the current UID can still be renamed or have its child entry replaced by another user after traversal. The retained final directory descriptor would then refer to a directory moved outside the intended temporary-root subtree while the operation continues. The final path `os.stat()` check only samples the pathname once and does not prevent a later rename.

**Required change:** Reject group/world-writable permissions on every traversed component below the temp root, before descending through it, as well as on the final directory. Preserve any explicitly necessary policy for the temp root itself. Add a test with a private temp root and a group/world-writable intermediate directory; assert failure before the anchor lock or anchor file is opened or created.

## Review conclusion and safety boundary

The R3 path traversal and multiprocessing synchronization findings are closed. The remaining writable-intermediate-directory case means the helper does not yet keep the anchor directory securely bound beneath the temporary root for the duration of the operation. I8 R4 is therefore **not accepted** pending correction within the existing two-file allowlist.

No tests were run during this review. No Resolve, IPC, network, key, or production filesystem operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize production trust-root loading, signer service wiring, authorization issuance, or live operations.
