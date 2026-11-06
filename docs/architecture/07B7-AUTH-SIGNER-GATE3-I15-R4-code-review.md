# Gate 3-I15 Revision R4 Static Code Review

**Decision: ACCEPTED**

## Scope and evidence

Reviewed the I15-R4 workorder, implementation, tests, completion report, and submitted focused-test log. Submitted artifact hashes match the workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `103720cad64542bba568a5958a9df307334677da0c225690a0259413e7f640eb` | Match |
| `.bridge/report.md` | `3449780882bc3e608d34f3058b73c865c09bd4d55009d31664488c209ffa7832` | Match |
| `src/services/trial_authorization_signer_service.py` | `2cd508b27bd3ab49943e6097543c2cdfcd162013697d69d7720412a1aaeeb417` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `8f381d46ecb1faa8a05138b83d4bedd46712d4facbb6fc319502d6d6db8fca9f` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I15/pytest.log` | `bb1d039732dc595a27baeb215c6015742b2479a2f2cdcfeefcadfb3e7d4032ef` | Match |

The submitted log reports **323 passed, 2 warnings in 3.28s**, exit code 0. Tests were not rerun; this is a static review. No Resolve, IPC, network, or live operation was performed.

## Finding resolution

**I15-R4-R1-01 is resolved.** The close-fault cases now instrument the ledger-owned directory FD and inject errors at the underlying `os.close()` boundary, allowing the actual `SignerConsumedNonceLedger.close()` implementation to run. The tests assert descriptor disowning, `CLOSE_OUTCOME_UNKNOWN` diagnostics, single close attempts for caller and ledger descriptors, structured preservation of append and cleanup errors, challenge poisoning, and absence of a success result. Test-harness cleanup explicitly closes descriptors for which the injected syscall raised before invoking the real close, preventing test-process FD leaks.

## Review notes

- **Descriptor binding:** I15 retains the descriptor returned by `_open_anchor_dir_under_temp_root()` and passes it directly through `directory_fd` to the accepted I6 R4 ledger API. The deterministic component-swap race checks that the record lands in the original opened directory inode while the replacement sentinel stays empty.
- **Lifetime and cleanup:** Instrumentation confirms the caller FD remains open until the ledger duplicates it, both descriptors remain valid during consumption, and the caller FD is closed once. Pre-ledger verifier/expiry and cleanup-failure cases assert the expected active, purged, or poisoned challenge state.
- **Failure handling:** Ledger close faults are now exercised through the real close path. Append and cleanup failures remain inspectable in order, ambiguous outcomes poison the challenge, and no success result escapes.
- **Scope and safety:** Changes stay within the authorized I15 source and test files. The implementation remains local-test-only and does not issue authorization, access production keys, dispatch, or invoke Resolve.

## Disposition

I15-R4 is **ACCEPTED** for the bounded local-test operator-consent verification and atomic challenge-consumption scope. This review does not authorize production signer deployment, authorization issuance, Gate 4/5/6 activity, or live operations. Preserve `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only.
