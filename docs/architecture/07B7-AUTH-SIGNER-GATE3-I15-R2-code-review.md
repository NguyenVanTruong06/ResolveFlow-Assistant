# Gate 3-I15 Revision R2 Static Code Review — Local-Test Operator Consent Verification & Atomic Challenge Consumption Core

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the active I15 workorder, implementation, tests, completion report, and submitted focused test log. The workorder, source, test, and evidence hashes match the workspace. Current report SHA-256: `1b29002d49fbed7f29076045844d2cba271772c67a82c8cc13b1e5cd07b87d5c`.

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `2048764a0d9e4f4eb13e6ebcf335644d4ad90c59af2d4cedcfebcf5d7df24e68` | Match |
| `src/services/trial_authorization_signer_service.py` | `b712a6c3c5734f53e2f4c2b18b85ddf2f6efb826c25523da19890d9221f45399` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `004484063a916328c080203a3df63fc2bb451268c8d0c939e95a240d4f05db3d` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I15/pytest.log` | `a8d1a7eaf460d5ae7834f7e98dd551e17fd2b4cec7eca63f96190affc586e990` | Match |

The supplied log reports **311 passed, 2 warnings in 2.86s**, exit code 0. Tests were not rerun; this is a static review. No Resolve, IPC, network, or live operation was performed.

## Prior findings

The R2 changes resolve the three findings from the first I15 review: the ledger is closed on success and append failure, expiry is checked with monotonic elapsed time under the registry lock, and the operator key is normalized before directory validation or I11 access.

## Findings

### I15-R2-01 — MEDIUM: Combined append and close failure does not preserve cleanup failure structurally

When `consume_once()` raises and the subsequent `ledger.close()` also raises, the handler creates a `ChallengeConsumptionError` whose message includes both exception strings and whose `__cause__` is only `append_exc` (around `src/services/trial_authorization_signer_service.py:4945`). The close exception object is not attached as a structured `cleanup_error`/`cleanup_errors` attribute or cause. This loses its type and fields, including any descriptor cleanup outcome, despite the workorder requiring preservation of structured primary and cleanup errors. The tests exercise append failure with successful close and close failure after successful append, but not both failures together.

**Required correction:** retain the append exception as the structured primary cause and attach the close exception object as structured cleanup data on the raised error (or use an established aggregate durability error that preserves both). Add a combined fault-injection test asserting both original exception objects and their codes/outcomes remain inspectable, while the challenge is poisoned and no success result is returned.

### I15-R2-02 — HIGH: Lexical temp-path validation does not keep the nonce ledger physically under the temp root

The I15 method validates `nonce_ledger_directory` with `_validate_directory_lexically_under_temp_root()` and then passes the resulting path to `SignerConsumedNonceLedger`. That ledger constructor uses `os.path.abspath`, path-based `os.path.exists`/`os.path.islink`, and `os.open(self._ledger_dir, O_DIRECTORY | O_NOFOLLOW, ...)`. `O_NOFOLLOW` protects only the final path component; symlinked intermediate components are followed. A path such as `/tmp/attacker-controlled/link/ledger`, where `link` points to a directory outside the temporary root, passes the lexical check and can cause the ledger file to be created or used outside the temp root. This violates I15's explicit local-temp-only persistence boundary.

**Required correction:** ensure the directory used by the ledger is physically confined beneath a trusted temporary-root descriptor, with component-by-component no-follow traversal and descriptor-relative creation/opening. The current `SignerConsumedNonceLedger` API accepts a path and does not provide that guarantee. Per workorder §6, if this cannot be done using the accepted ledger API without an out-of-scope API change, stop and report the dependency rather than claiming I15 completion or broadening the allowlist silently. Add a symlinked-parent regression test proving no outside directory or file is accessed or created.

## Positive observations

- I15-R1-01 through I15-R1-03 are addressed as claimed: explicit close attempts occur on success and append-failure paths; the lock-held expiry gate checks both UTC expiry and monotonic elapsed time; operator-key normalization precedes path validation and I11.
- I11 remains read-only (`advance_anchor=False`), consent verification uses the captured instant, and the challenge is poisoned on ambiguous persistence outcomes.
- The slice remains local-test-only and does not issue authorization or dispatch actions.

## Disposition

Resolve I15-R2-01 within the existing source/test allowlist. For I15-R2-02, either demonstrate a safe descriptor-anchored route within the authorized contract or report the dependency for a separately approved bounded workorder; do not claim the temp-root invariant is met while intermediate symlinks can escape it. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. I15 acceptance remains limited to local-test consent verification and durable one-time challenge consumption.
