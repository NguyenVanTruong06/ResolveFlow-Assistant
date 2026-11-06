# G07-07B5-D-R46 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R46. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `8435efa396ea9901d936b412f98021254ed2a069428301fe8fa7d355a639f79a`
- `docs/architecture/07B5-D-R45-code-review.md`: `44ad572f06565fb4218e22e4abb7ee3b0cd3419a5163c55ce9d010303cd36392`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `f35e50802c4c4d6941c7e447e33579446d1394759b21ae25f6189e66c9241d92`
- `.bridge/report.md`: `32c7c913b2335e416f0e0675304bb97c643d6b503b9b60261f80a15a4ac98e92`
- `scratch/test_r46_validator.py`: `4f91cb6a9717283687827e6c2aee72c898a749a70b517722d5ea34dc966c34ef`

## Findings

### R46-01 — Missing operator-key purpose silently grants reconciliation authority — BLOCKER

The `*.opkey.json` loader builds an `OperatorKeyRecord` using `data.get("purpose", "TIMELINE_AMBIGUOUS_RECONCILIATION")` (around lines 1753–1761). A descriptor with no `purpose` field is therefore granted the privileged reconciliation purpose by default, and `get_operator_key()` plus the later purpose check accept it. This is fail-open behavior at the trust-store boundary: an incomplete or legacy key descriptor silently acquires authority to clear `CLOSE_OUTCOME_UNKNOWN`.

**Required correction:** Require the purpose field to be present, strictly typed, and exactly allowlisted; reject missing, unknown, or duplicate JSON fields. Validate the complete descriptor schema before inserting the key into `_operator_keys`. Add a negative fixture with a missing purpose field and assert load/reconciliation fails closed without changing the registry or challenge state.

### R46-02 — Operator key material has no decoded-length minimum — HIGH

The loader decodes `data["key_hex"]` with `bytes.fromhex()` but does not validate the decoded key length or type beyond whatever the decoder happens to accept (around lines 1753–1761). The surrounding file-size limit applies to the JSON document, not to the entropy of `key_bytes`; a short value such as a one-byte key can be installed and then used for HMAC authorization. This falls short of the existing minimum-secret-strength policy used by the trusted key stores and weakens the independent operator authentication boundary.

**Required correction:** Require `key_hex` to be a strict even-length hexadecimal string and require decoded operator key material to contain at least 32 bytes of cryptographic entropy (with an explicit bounded maximum). Reject malformed, short, or oversized material while loading the store, before it can authorize reconciliation. Add boundary cases for 31, 32, and maximum-length keys plus malformed hex and non-string values.

## R45 findings reviewed

R46 adds a separate `_operator_keys` namespace, binds each authorization identity to `permitted_operator_identity`, rejects trial-key IDs, and applies finite TTL bounds before replacing the active challenge. Those changes address R45-01 and R45-02 in principle. The operator descriptor parser still defaults a missing purpose to the privileged value and accepts under-strength decoded secrets, so the trust boundary is not yet fail-closed.

## Decision and safety boundary

R46 is **not accepted** until R46-01 and R46-02 are resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
