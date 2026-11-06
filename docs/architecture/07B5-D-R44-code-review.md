# G07-07B5-D-R44 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R44. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `0516470fa97e63458286b1d950507267b02ba0ca0a81d2157c130d1c0a8d10ac`
- `docs/architecture/07B5-D-R43-code-review.md`: `21177268e5a7fae7cfb9e8c3e57e701eabdaec3a6dd12130f2680958d99f3247`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `9180d79bc529c70af8c0aef97cf523dadcb696e9a7d32e9aa53972a2666fd003`
- `.bridge/report.md`: `704cbf725e51545e273c68d8b4e3caa2f8b9c8b9b897f8d381316ea3dc367a6e`
- `scratch/test_r44_validator.py`: `9eed1f3f6c2cb46cc0c3c5f2079f9efa0c25ca63c4f437da13de8ac7e802a674`

## Finding

### R44-01 — Reconciliation authorization is self-mintable by any caller — BLOCKER

`create_operator_reconciliation_auth()` defaults `signing_secret` to the Coordinator's private `_instance_capability_secret`, and accepts caller-chosen `operator_identity`, confirmation statement, and `confirmed_unlocked` (`around lines 2582–2605`). The reconciliation method then validates the HMAC using that same Coordinator secret (`around lines 2673–2680`). Consequently, any code with access to the Coordinator can call the helper with an arbitrary operator label and the required phrase, receive a valid HMAC authorization, and clear the `CLOSE_OUTCOME_UNKNOWN` lockout. The signature proves only that the Coordinator's own helper signed the payload; it does not prove a human operator authenticated or confirmed anything. The optional caller-supplied `signing_secret` also creates an unnecessary misleading signing path, even though such a signature will not validate unless it matches the Coordinator secret.

This leaves the original authority gap unresolved: the structured object, challenge nonce, and HMAC are all constructible through the same caller-accessible Coordinator surface.

**Required correction:** Remove the public self-mint helper and do not expose the instance capability secret to the authorization-creation path. Define a real trusted operator boundary, such as an app-owned confirmation flow that returns a sealed one-shot authorization capability, or an independently trusted operator credential/key whose verification authority is not callable by arbitrary Coordinator clients. Bind the verified operator identity, exact timeline, incident, challenge nonce, expiry, and confirmation statement into the signed canonical payload. Add a negative case proving a caller with Coordinator access but no operator credential cannot mint an authorization accepted by `reconcile_ambiguous_timeline()`.

## R43 finding reviewed

R44 replaces the bare boolean with a typed, challenge-bound structure, strict boolean validation, timeline/incident binding, expiry, and replay checks. Those checks improve input integrity, but they do not establish operator authority while the same Coordinator offers a helper that signs arbitrary caller-provided claims with its own verification secret.

## Decision and safety boundary

R44 is **not accepted** until R44-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
