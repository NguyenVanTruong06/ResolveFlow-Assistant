# Gate 3-I14 Static Code Review — Local-Test Authoritative Challenge Issuance Core

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the submitted I14 workorder, implementation, tests, completion report, and focused test log. The supplied SHA-256 values match the current workspace:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `eb2773bca5110f13204a1d2b9070d5f03a327c243efbc2068f41e2308522b2d1` | Match |
| `.bridge/report.md` | `a0eee920ae3d2908e988edb4e1f2ba96eda2bbe9893533e80a77b7416ce75233` | Match |
| `src/services/trial_authorization_signer_service.py` | `ac4b7397eccb47c31519ef905853fc7be1b1f7300678d735e7dd379fcadeff72` | Match |
| `tests/services/test_trial_authorization_signer_service.py` | `80f10c2b168a0f8f69b2c2fe0e2d5e9b007eb9a72265bcf82e2577b62120d43a` | Match |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I14/pytest.log` | `1349c7574d901d1aae95adedca2c149db44489f5a3d7583c4d05c76a2ae21ea6` | Match |

The supplied log reports **270 passed, 2 warnings in 2.70s**, exit code 0. Tests were not rerun; this is a static review. No Resolve, IPC, network, or live operation was performed.

## Findings

### I14-01 — HIGH: Issuance can return an already-expired challenge and use stale revocation time

`LocalTestChallengeIssuanceRequest.__post_init__` validates that `verification_time_utc` is timezone-aware UTC, but does not bound it against the current time. `issue_challenge()` passes that caller-provided instant to the I11 revocation verifier and later sets `issued_at_iso` to the same value and `expires_at_iso = verification_time_utc + ttl` (around `src/services/trial_authorization_signer_service.py:4470`). It performs no freshness or elapsed-time check immediately before registry insertion/return.

Consequently, a stale timestamp can cause a revocation manifest that has since expired or been superseded to pass verification at the historical instant, and a slow I11 read (especially with the permitted 1-second TTL) can leave the challenge expired before it is returned. A future timestamp can also produce a challenge whose validity window has not begun yet. This violates workorder §3.2's requirement that the request still be within the challenge window when returned.

**Required correction:** use one captured, trusted UTC instant for I4/I11 and challenge `issued_at`; reject stale/future verification instants under a documented bounded skew, and check immediately before insertion that the challenge has not expired (using a monotonic elapsed-time guard or an injected test clock). Add deterministic tests for stale/future instants and delayed I11 crossing the TTL boundary. No challenge may be inserted on these failures.

### I14-02 — MEDIUM: Duplicate source-path digest bindings are silently resolved by first match

At `src/services/trial_authorization_signer_service.py` around lines 4360–4380, the code gathers `source_digests` matching `action.source_file_path`, rejects only an empty list, then uses `matching_source_digests[0]`. `TimelineExecutionPlan` does not enforce unique source paths, so an approved plan can contain multiple bindings for the same path, including conflicting hashes. The resulting operator context then depends on tuple order instead of rejecting an ambiguous source binding.

**Required correction:** require exactly one matching source-digest entry for the action path and reject duplicates with a stable `ERR_CANARY_SCOPE_VIOLATION` code. Add tests for duplicate same-digest and conflicting-digest entries; both should fail before challenge registration.

### I14-03 — MEDIUM: Registry collision checks do not enforce cross-category identity uniqueness

The current issuance checks each generated value only against the historical set for its own category (`challenge_id` against challenge IDs, `client_nonce` against nonce IDs, etc.; around lines 4488–4503). It checks pairwise distinctness among the four IDs generated for one issuance, but a new challenge ID can equal a previously issued client nonce, authorization ID, or execution ID without being detected. The workorder requires that concurrent issuance cannot alias these identities.

**Required correction:** under the registry lock, check each candidate against a unified historical identity set (or against all four category sets), and only then atomically add all four values. Add collision-injection tests for cross-category historical collisions and assert existing entries and all issuance sets remain unchanged on rejection.

## Positive observations

- The implementation calls I4 then read-only I11 in the required order, checks Director/Operator revocation, derives action digests from the approved plan, and uses a narrow lock for collision check plus registry insertion.
- The request and result DTOs are frozen and test-only limitations are documented. The supplied tests cover the main validity, scope, registry, concurrency, and safety paths.
- The read-only I11 path is explicitly requested with `advance_anchor=False`; no production key, Resolve, or dispatch path is wired into I14.

## Disposition

Fix I14-01 through I14-03 within the existing I14 source/test allowlist, add the specified regression cases, and resubmit the focused evidence. Preserve `ready_to_execute=False`, Gate G07 and M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` and incident-only. I14 acceptance, when achieved, remains limited to the local-test challenge issuance/state-binding slice.
