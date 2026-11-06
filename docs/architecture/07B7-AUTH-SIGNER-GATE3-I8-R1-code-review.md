# G07-AUTH-SIGNER-GATE3-I8 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Digest-Bound Durable Revocation Epoch Anchor Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `36d7bff905a9b056797cf157cf5eee6bf0e1c40e3def2674397deda17ea4b443`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `121d39ee78ee31efb04f7443db413b686465cd018fd6e63e7859beba70d3f8c8`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `e01e77f3f4fc30ca0e1ba3156bdd3d92ed18bae2446cc53260ad5abd51447e34`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I8/pytest.log` — SHA-256 `9e6cb231907efc98177de0190741ffc192f936e889f2faf3e8948b28803ed4a5`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `19e2a65b826305d3b10b1e1917e81b50ef2695bcf08b3aa9ba893cd99360d083`.

Submitted hashes match the workspace. The submitted log reports 177 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## Findings

### I8-R1-01 — FIFO anchor can block before file-type validation — BLOCKER

The anchor is opened with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`, then checked with `fstat()` for regular-file type. Opening an existing FIFO read-only can block before `fstat()` is reached, so a malformed anchor can hang the signer instead of failing closed. The lock-file open has the same missing nonblocking protection for non-regular objects on platforms where opening such an object can wait.

**Required change:** Add a safe nonblocking open strategy (`O_NONBLOCK` where supported) for lock and anchor descriptors before `fstat()`, and verify the descriptor is regular before any read or lock operation. Add a FIFO fixture that proves the call returns promptly with a structured error.

### I8-R1-02 — Anchor read is not bounded by EOF or protected against concurrent mutation — HIGH

The reader trusts the initial `st_size`, reads until that many bytes or an empty read, and then parses without requiring `len(raw) == st_size`, probing for an extra byte, or comparing descriptor metadata before and after the read. A short read or concurrent truncate/extend/write can therefore produce a parsed snapshot that was never a stable complete anchor record. This falls short of the workorder's bounded exact-read and stability requirement.

**Required change:** Read with a strict maximum plus one-byte overflow probe, require exact EOF, and compare relevant `fstat()` identity/size/metadata before and after reading. Reject short, extended, or changed records with a stable corruption/durability error. Add short-read, appended-byte, and metadata-change fault-injection cases.

### I8-R1-03 — Zero-byte write can loop forever — HIGH

`_write_all_bytes()` advances its offset by `os.write()`'s return value but does not reject zero. If a write returns 0 while data remains, the loop makes no progress indefinitely, violating the bounded failure behavior expected for write-all persistence.

**Required change:** Raise a structured short-write durability error immediately when `os.write()` returns 0 (or a negative value), while retaining EINTR retry behavior. Add a zero-write injection test.

### I8-R1-04 — Cleanup failures can replace the primary error; temp unlink errors are swallowed — HIGH

The outer `finally` raises `ERR_REVOCATION_ANCHOR_CLEANUP_FAILED` whenever cleanup fails, which replaces an already-active rollback, tamper, parse, or persistence exception rather than preserving it as the primary cause. Separately, the `os.unlink(tmp_name)` failure in the replace-error path is ignored. Both conflict with the workorder's explicit requirement to preserve primary and cleanup failures and surface cleanup errors.

**Required change:** Preserve the primary operation exception and attach structured cleanup error(s) and notes/attributes without masking its stable code. If temporary-file unlink fails, report both the replace failure and orphan-temp cleanup failure; do not suppress it. Add combined primary-plus-cleanup and failed-temp-unlink tests.

### I8-R1-05 — Path-based symlink precheck does not establish the promised directory boundary — HIGH

The function calls `os.path.abspath()` and `os.path.islink()` before opening the full directory path. This is a racy pathname check, and `O_NOFOLLOW` on the final `os.open(resolved_dir, ...)` component does not prevent symlink traversal in ancestor components. The literal substring denylist for `/var/lib/resolveflow` and `/run/resolveflow` also does not prove that the opened directory is a test temporary directory. Thus the API's local-test-only boundary is not enforced against path aliases or a directory swap.

**Required change:** Remove the path-based symlink precheck as a security decision. Establish the directory through descriptor-relative, no-follow traversal (or a comparably explicit canonical directory policy), validate the opened descriptor, and define/enforce a test-only path policy that cannot be bypassed by aliases. Keep all tests under `TemporaryDirectory`; add alias/symlink race or rejected-path cases. Do not access any production path while addressing this finding.

## Review conclusion and safety boundary

The implementation has the intended verified-result input gate, digest recomputation, rollback/equal-epoch comparisons, advisory locking, and atomic same-directory publication. However, the five findings above leave liveness, read consistency, write durability, error reporting, and the test-only path boundary short of the workorder. I8 R1 is therefore **not accepted** pending correction within the existing two-file allowlist.

No tests were run during this review. No Resolve, IPC, network, or key operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize production trust-root loading, service wiring, authorization issuance, or live operations.
