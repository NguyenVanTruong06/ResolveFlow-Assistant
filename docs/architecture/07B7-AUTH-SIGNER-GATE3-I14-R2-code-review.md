# Gate 3-I14 Revision R2 Static Code Review — Local-Test Authoritative Challenge Issuance Core

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the active I14 R2 workorder, implementation, tests, completion report, and supplied focused test log. All submitted SHA-256 values match the workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `0d3ae201c7fc663dd5741e0fcf66dd651b06952b24ef9aa049d30dc1d0541968` | Match |
| `.bridge/report.md` | `049e995bebb33bb59bf7cad0b04155528ebea26dbfbf0071470c2e34c19314df` | Match |
| `src/services/trial_authorization_signer_service.py` | `2cc9c8c58f9725cc2340ee7121c430a603a11d61501e744af66c38c9eed39b4f` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `151e58f8532b0f75d092422bb411381eb9ff3d45e7a51974ae80c2401c22234f` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I14/pytest.log` | `526ef33977e109b8d2e13ec29554a034e408e9f330208d68e0e6d2a44f032690` | Match |

The supplied log reports **277 passed, 2 warnings in 2.86s**, exit code 0. Tests were not rerun; this review is static. No Resolve, IPC, network, or live operation was performed.

## Prior findings

The R2 changes address the three findings from the initial I14 review:

- Verification-time skew and TTL checks were added, including a pre-registration elapsed-time check.
- Source digest lookup now rejects multiple bindings for the same action path.
- A unified historical identity set rejects cross-category collisions before identity-set or registry mutation.

## Finding

### I14-R2-01 — HIGH: Expiry can pass between the freshness check and registry insertion

In `issue_challenge()`, the final TTL check (including the monotonic elapsed-time and UTC expiry comparisons) occurs before `with self._lock:`. The subsequent lock acquisition can block behind another issuance or registry operation. If that wait crosses `expires_at_dt`, the code still adds all four IDs to the historical sets, stores the expired result in `_active_challenges`, and returns it. The current delayed-issuance test advances time before this check, so it does not cover delay while waiting for the registry lock.

This leaves a path where the challenge is already expired when returned, despite the workorder requiring the challenge to remain within its validity window when returned.

**Required correction:** perform a final trusted-clock and monotonic TTL check while holding the registry lock, immediately before the first mutation of `_all_issued_identities`, the category sets, or `_active_challenges`. On expiry, raise `ERR_CHALLENGE_ALREADY_EXPIRED` with all registry and historical sets unchanged. Add a deterministic regression test that holds the registry lock until the injected clock/monotonic time crosses the TTL, then confirms issuance rejects without mutation. Keep filesystem and cryptographic verification outside the lock.

## Positive observations

- I4 and read-only I11 remain in the required order and are each called once; the revocation directory is not advanced by this slice.
- Duplicate source digest bindings fail closed, and generated IDs are checked against a unified historical set under the registry lock.
- The implementation remains explicitly local-test-only and does not issue authorization, load production keys, connect to Resolve, or dispatch actions.

## Disposition

Fix I14-R2-01 within the existing I14 source/test allowlist and resubmit the focused evidence. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. Acceptance remains limited to the local-test challenge issuance/state-binding slice.
