# Gate 3-I13 Static Code Review — Local-Test Descriptor-Anchored Signer Keystore Core

**Decision: CHANGES_REQUIRED**

## Review scope and evidence

Reviewed the submitted I13 workorder, implementation, tests, report, and focused test log. The submitted SHA-256 values match the files present in the workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `dd82ce1240c3a6f28e5df2349e95db38ab2e42e829622c97a4ac050183d4f624` | Match |
| `.bridge/report.md` | `f877e8830e3f97e5d50d083d6be0f9a1ece7ffdeb2639ba04b52268f77007ab0` | Match |
| `src/services/trial_authorization_signer_service.py` | `676636c5e15cbd76841cef595df5e55c75481d4c4bb04c9eea3997f6477ffb36` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `57ce7745e06e4c0d38a781aeb591f8dcdce01ec345ab23cc7d9beda82a47856a` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I13/pytest.log` | `73b5c414091daf17ca19f6f257ccc55fd8f48d6a16e6daada41d5d7e731bdf14` | Match |

The submitted log ends with **246 passed, 2 warnings in 2.72s**, exit code 0. This was an artifact review; tests were not rerun. No Resolve, IPC, network, or live operation was performed.

## Findings

### I13-01 — BLOCKER: Public constructor bypasses descriptor-anchored key loading

`LocalTestSignerKeyHandle.__init__(key_bytes)` at `src/services/trial_authorization_signer_service.py:3713-3718` accepts arbitrary caller-provided bytes and stores them as the handle key. A caller can therefore instantiate the purported keystore-backed verifier directly, without `load_signer_key_handle_for_local_test()` or any descriptor, file mode, ownership, or path validation. The handle's `_key_bytes` slot is also directly readable and assignable through ordinary Python attribute access.

This contradicts the I13 requirement against caller-supplied key bytes/key-handle injection and defeats the core keystore-binding claim. The test that checks for a public `key_bytes` property does not cover direct construction or the `_key_bytes` slot.

**Required correction:** make successful handle construction available only through the validated loader's internal construction path; reject direct caller construction and add tests proving arbitrary caller bytes cannot create a usable handle. Keep the documented limitation that Python cannot defend against arbitrary introspection/code in the same process; do not describe this local primitive as an OS trust boundary.

### I13-02 — HIGH: “Exact 0600” check accepts special permission bits

At `src/services/trial_authorization_signer_service.py:3851-3853`, the mode check masks with `0o777`. This ignores setuid, setgid, and sticky bits, so a file such as mode `0o4600` passes even though the workorder requires the exact mode `0600`.

**Required correction:** compare the complete permission/special-bit mode (for example, `stat.S_IMODE(st_mode)`) to `0o600`, and add negative tests for special-bit variants.

### I13-03 — MEDIUM: Completion report overstates the no-key-access boundary

`.bridge/report.md` says “zero key access” in the safety invariants, while I13 deliberately opens and reads a test fixture key under `tmp_path`. This wording can be read as contradicting the implementation and obscures the important boundary between test-fixture access and production keystore access.

**Required correction:** in the next handoff report, say “zero production keystore/key access; ephemeral test fixture only.” Do not alter the I13 source/test allowlist to address this report wording; report it as a handoff correction.

## Positive observations

- The loader uses the existing descriptor-anchored temporary-root traversal, a fixed relative filename, `O_NOFOLLOW`, bounded read plus EOF probe, metadata stability checks, and single-close cleanup.
- The HMAC verification operation requires the exact authorization DTO and delegates canonicalization to the accepted contract serializer; it does not expose a signing method.
- Tests cover path rejection, symlink/hardlink/non-regular files, length and metadata failures, injected I/O/close failures, HMAC verification, and the local-test-only limitation.

## Required disposition

Fix I13-01 and I13-02 within the two-file I13 allowlist, add the stated negative tests, and correct the report wording on resubmission. Keep Gate 3 incomplete, `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. No challenge issuance, authorization issuance, production key access, process/service wiring, or live work is authorized by this review.
