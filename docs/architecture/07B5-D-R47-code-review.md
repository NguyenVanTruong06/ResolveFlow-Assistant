# G07-07B5-D-R47 — Codex Static Design Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.6 for R47. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `e7163c0a6a4cfbc14f03b42a2c4258c8ce5ef125825d3fb270650aef7fd8cdfd`
- `docs/architecture/07B5-D-R46-code-review.md`: `5ea72e513819ca7e05bab146259d5153db6c59f4f197c10c900e55d21cd1cc93`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `d1f244f4fcc5dc36f994062859cb977af8e44d6f0b27bc3ce2a01be9ec43afed`
- `.bridge/report.md`: `22d0ee2333968a4c797437bcfb140fd80b520676953bcb479caf31ba4229ee4c`
- `scratch/test_r47_validator.py`: `5028f8bea2a2b7a6fe9e3a33ce92dce4755ef553a6110b607b1a7f8c50e14a7c`

## Finding

### R47-01 — Duplicate JSON member names are accepted in operator-key descriptors — HIGH

The `*.opkey.json` loader calls `json.loads(content.decode("utf-8"))` without an `object_pairs_hook` that rejects duplicate names (around lines 1759–1775). Python's default decoder collapses duplicate members into a dictionary, generally keeping the last value. The subsequent exact key-set, type, purpose, and key-length checks therefore cannot detect a descriptor such as two `purpose` or `key_hex` members. Different tools may select different occurrences, so an operator reviewing or provisioning the descriptor can see a different effective key/purpose from the Coordinator. This leaves the sensitive trust-store parser short of the strict, unambiguous schema it claims to enforce.

**Required correction:** Parse operator-key JSON with duplicate-member rejection before constructing the dictionary (for example, reuse the design's `reject_duplicate_json_keys` helper). Normalize duplicate-key failures to a controlled `ERR_OPKEY_DUPLICATE_FIELD`/malformed-descriptor error and reject the key before inserting it into `_operator_keys`. Add negative fixtures with duplicated `purpose`, `key_hex`, and `permitted_operator_identity`, including conflicting first/last values; verify the key is not loaded and reconciliation state remains unchanged.

## R46 findings reviewed

R47 removes the privileged default for missing `purpose`, enforces the exact three-field schema, validates field types and allowlisted purpose, applies strict hex syntax and decoded length bounds, and checks the challenge TTL before replacing the active nonce. The remaining gap is duplicate-member acceptance in the JSON trust descriptor.

## Decision and safety boundary

R47 is **not accepted** until R47-01 is resolved. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
