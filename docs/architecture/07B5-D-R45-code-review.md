# G07-07B5-D-R45 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R45. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `aee47c4660e34b38b3e9ed2d9df71db78b54ab6778722d4f331930dc9e5df655`
- `docs/architecture/07B5-D-R44-code-review.md`: `7c1f152d14156dbfd86d55bada94712161029fc27655cb8b106cb60c78d6cfaf`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `dd35903583b4f3c4119b81cbf90e0bb9a5c210bc182a1c1fe95aec7383a5620c`
- `.bridge/report.md`: `0e9da167e04a53c52dbffe9917c14f695e79d8315b09ca43a788061789d1ef00`
- `scratch/test_r45_validator.py`: `88315069bc72cf41d913f902c8ae9d9345ff7556d1e0c0c59615e15449cb914a`

## Findings

### R45-01 — Operator authorization keys are not separated or identity-bound — BLOCKER

`reconcile_ambiguous_timeline()` resolves `authorization.operator_key_id` through the same generic `TrialKeyringAuthority.get_key()` used for trial authorization, proof, and ledger signatures (around lines 1694–1762 and 2654–2669). The keyring loader accepts every non-hidden `*.key` file into one untyped `_keys` map; it has no operator-key namespace, key-purpose restriction, or trusted mapping from a key ID to an allowed `operator_identity`. The authorization's `operator_identity` is only checked for non-empty text and is included as a caller-asserted string in the HMAC payload.

Therefore, any key accepted for trial signing is also accepted as an operator credential, and a signature under that key can claim any operator identity. This does not implement the stated independent operator credential boundary; it permits a trial/application signing credential to authorize clearing the ambiguous lockout.

**Required correction:** Define a distinct operator trust authority or an explicitly purpose-separated keyring entry type. Bind each operator key ID to its permitted operator identity and reconciliation purpose, reject trial-signing keys at this gate, and verify the identity/key binding before consuming the challenge. Add negative cases showing a valid trial authorization key cannot reconcile and a valid operator key cannot claim another operator identity.

### R45-02 — Challenge lifetime is caller-controlled and unbounded — HIGH

`request_reconciliation_challenge(timeline_uid, ttl_seconds=300.0)` computes `expires_at = time.time() + ttl_seconds` without validating the input type, finiteness, positivity, or a maximum (`around lines 2558–2575`). A caller can request an arbitrarily long-lived challenge (including `float("inf")`), and an operator-signed authorization for that challenge will pass the expiry comparison indefinitely. This defeats the described short-lived challenge window and extends the replay/exposure period beyond the design's stated 300-second policy.

**Required correction:** Enforce a primitive finite numeric TTL (`type` explicitly excluding bool), a positive lower bound, and a fixed maximum of 300 seconds before issuing or replacing any challenge. Reject NaN, infinities, booleans, non-numeric values, and over-limit values without changing the active challenge. Add boundary tests for exactly 300 seconds and for 0, negative, bool, NaN, infinity, and values above the cap.

## R44 finding reviewed

R45 removes the Coordinator's self-mint helper and verifies a signature against a keystore key, which addresses the specific self-signing path in R44. The key is not yet independent by purpose or identity, however, and the challenge lifetime has no enforceable upper bound.

## Decision and safety boundary

R45 is **not accepted** until R45-01 and R45-02 are resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
