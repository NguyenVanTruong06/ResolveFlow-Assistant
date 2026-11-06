# G07-AUTH-SIGNER-GATE3-I9 Revision R1 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Descriptor-Anchored Fresh Revocation Registry Read Core  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `7c0317453b6ac37aee3cebf2eff178c2e79a1d4e027e368d6bdabb1eecf89c53`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `3d95c269e0cb334280fc1c66900d45fb9ecc1417f4fd073f637fec54fbfb914d`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `32f4579d67deadb52ef50124b3760b24d26e9373222ee0d2c6fbadca08b11211`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I9/pytest.log` — SHA-256 `bd62b6b945379755afaba3666253f4ee5104127adc7aed3846208077637cc1ed`.
- Completion report: `.bridge/report.md` — SHA-256 at submission `fd31c87e7af8155f55a6fcdef186a67dee03e34ae54c77eea93df31fc11bc2aa`.
- Prior accepted slices: I7-R2 and I8-R5 reviews are referenced in `.bridge/task.md`.

Submitted hashes match the workspace. The submitted log reports 193 passed, 0 failed, exit code 0. Tests were not rerun during this static review.

## Findings

### I9-R1-01 — Registry close failure does not stop verification and anchor mutation — HIGH

After the registry bytes are read, the code clears `reg_fd` and calls `_safe_close_descriptor()`. If `os.close()` fails, this helper only appends to `cleanup_failures`; execution then proceeds to call the I7 verifier and I8 anchor updater. The `finally` block reports the cleanup failure only after the anchor may already have been created or advanced. The caller therefore receives an error even though the operation mutated durable state. The submitted cleanup test combines a read failure with a close failure, so the primary read error already prevents verification and does not cover the successful-read/failed-close path.

**Required change:** Treat failure to close the registry descriptor as a pre-verification failure and abort before calling either verifier or anchor updater. Preserve the close error and any other cleanup errors structurally, and never retry the numeric descriptor. Add a test that injects a close failure after a successful bounded read and asserts both downstream functions are not called and the anchor remains byte-for-byte unchanged or absent.

### I9-R1-02 — Registry fstat failures can escape without a stable structured error — MEDIUM

The `os.fstat(reg_fd)` calls before and after reading are outside an `OSError` normalization boundary. If either raises and cleanup succeeds, the raw `OSError` escapes rather than a stable `SignerLedgerError`-family code. This leaves the fresh-read API's operational failure contract inconsistent with its structured open/read/cleanup errors.

**Required change:** Normalize pre-read and post-read `fstat` failures to distinct stable structured codes (preserving the original exception as `primary_error`/`__cause__`). Add fault-injection cases for both stat boundaries and verify no verifier or anchor call occurs.

## Review conclusion and safety boundary

The implementation otherwise follows the intended I9 composition: each invocation performs a descriptor-relative bounded fresh read, checks regular-file ownership/link/mode and metadata stability, invokes I7 verification once, and invokes I8 anchoring once on the success path. However, cleanup failure can currently occur after durable mutation, and fstat I/O errors are not consistently structured. I9 R1 is therefore **not accepted** pending correction within the existing two-file allowlist.

No tests were run during this static review. No Resolve, IPC, network, key, or production filesystem operations were performed. Preserve `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`; Gate 3 remains incomplete; Gate 6 remains `NOT ACCEPTED` and incident-only. This review does not authorize production trust-root loading, signer service wiring, authorization issuance, or live operations.
