# Gate 3-I6 Revision R4 Static Code Review — Descriptor-Anchored Consumed-Nonce Ledger Directory Interface

**Decision: ACCEPTED**

## Scope and evidence

Reviewed the I6-R4 workorder, implementation, focused tests, completion report, and supplied test log. All submitted SHA-256 values match the current workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `4d4bf4b1f3a769a71fbbfeb63999954d537ac388420b8da16e940d71b99fc634` | Match |
| `.bridge/report.md` | `22a98f32aad522f28f9f9d13a2141596fa10b8919c69e687c6c403e77089c4f2` | Match |
| `src/services/trial_authorization_signer_service.py` | `5f267823f764e8141bca4e900bdbb43243deff6cefcedb0afba8176c843e880a` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `0ce0866418db6c498da65c25dc511834eadadc19bddb5a63b4fdede7db77a0ef` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I6-R4/pytest.log` | `61cd8612b962762ed327178b79b0457925361884faff0e528d1d2d63ff86a9c7` | Match |

The supplied log reports **202 passed, 2 warnings in 2.77s**, exit code 0. Tests were not rerun; this review is static. No Resolve, IPC, network, or live operation was performed.

## Review result

The descriptor-backed interface resolves the I15 dependency identified in the prior review:

- `SignerConsumedNonceLedger` accepts mutually exclusive path or directory-descriptor inputs; the existing path mode remains available to current callers.
- Descriptor mode validates the exact integer FD, duplicates it into ledger-owned state, sets close-on-exec/non-inheritable state, validates the retained directory via `fstat`, and does not close or mutate the caller's FD.
- Ledger file creation/open, scanning, append, and directory fsync use the retained descriptor with `dir_fd`; the constructor does not convert that descriptor back into a path.
- The path-swap regression test replaces the original pathname with a symlink after opening the original directory. The ledger continues to write to the original opened inode, and the outside sentinel remains untouched.
- Fault-injection and lifecycle tests cover caller-FD preservation, ledger-owned descriptor cleanup, single-close behavior, and torn-tail/collision handling in descriptor mode.

No remaining in-scope blocker was identified. The trust boundary is appropriately documented: callers must verify descriptor ancestry before passing the FD; this class pins the directory inode but does not itself prove ancestry.

## Acceptance boundary

I6 R4 is accepted only for the descriptor-backed `SignerConsumedNonceLedger` interface and its regression coverage. This acceptance does not wire the API into I15, accept I15, establish production signer deployment, authorize Dispatcher integration, or authorize Gate 4–7/live operations. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only.
