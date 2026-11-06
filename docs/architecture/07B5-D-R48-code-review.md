# G07-07B5-D-R48 — Codex Static Design Review

**Decision: ACCEPTED**  
**Scope:** Static review of Section 2.6 for R48. No production or test files were changed. No prototype validator, pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

Recomputed SHA-256 values match the submitted handoff:

- `.bridge/task.md`: `95cb75aea9a0587af4bb648c78694fe9d96f58042d4d22b0b72f638bbf5add89`
- `docs/architecture/07B5-D-R47-code-review.md`: `f67bd21039c65856899c4de3891bb1a9468a135aadefb2266ee30b907384b32c`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `e3dc16133e68648535f64dfc03592ed2f449ff6ae50438605e09d7f074df2132`
- `.bridge/report.md`: `13e7b23cd2cfab886d61553c9d7a1626875973acbc07d091423d2ed238c8706c`
- `scratch/test_r48_validator.py`: `63c785eb5938deb2ebe94732dbf3459fa105e47bfcd1adf2cbceed95a5fe6e25`

## Review result

R48 resolves R47-01 in the reviewed design:

- `reject_opkey_duplicate_json_keys()` rejects a repeated member before a dictionary is produced.
- The `.opkey.json` loader passes this hook to `json.loads()` and re-raises the controlled `AcceptanceGateError` separately from generic parse errors.
- Exact schema, strict field types, allowed purpose, operator identity, hex syntax, and decoded key bounds are checked before `_operator_keys[kid]` is assigned.
- The duplicate-member fixtures cover duplicate `purpose`, `key_hex`, and operator identity, including conflicting values. The matrix records that the malformed key is excluded and reconciliation state remains unchanged.

No remaining design blocker was found in the R47 duplicate-key repair. The prior R46 schema and key-length corrections remain present in the reviewed path.

## Decision and safety boundary

R48 is **ACCEPTED** as a design review. Keep `ready_to_execute=False`; keep Gate G07 and milestones M1/M2/M3 OPEN. This acceptance grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The reported prototype validator result is a handoff claim only; it was not run or independently verified in this static review.
