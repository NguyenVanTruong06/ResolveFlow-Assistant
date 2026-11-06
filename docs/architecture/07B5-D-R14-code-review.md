# G07-07B5-D-R14 — Codex Review

**Decision: CHANGES_REQUIRED**  
**Reviewed:** 2026-11-02  
**Submitted report SHA-256:** `1e2a3d4bcab1490c702d502aa95aa928601b74d9c3927c6c3d05966c02ecc36a`  
**Design SHA-256:** `4e0f0f3a7fb5004293b299956c1b42eee69fbf94120288f7025c4cee080a85d4`

## Review scope

Static review only. The submitted R13 review/task hashes and R14 report/design hashes were recomputed and match disk. Reviewed the R14 WAL validator pseudocode, action payload/allowlist logic, result classifications and crash matrix. No tests, application, Resolve, IPC, FusionScript, or network activity was run.

## Findings

### R14-01 — Stage outcome allowlists do not validate required outcome values (blocking)

`validate_action_payload_projection()` uses the outcome sets to reject extra keys, but does not require or validate the outcome fields themselves. Consequently, an `ACTION_COMMITTED` record may omit `commit_status` or carry a value other than `SUCCESS` and still enter `COMMITTED`; `ROLLBACK_DONE` may omit `rollback_status` or carry `FAILED` and still enter `ROLLBACK_DONE`; and `ACTION_FAILED` does not require/validate its nested `failed_action_index`. The FSA then classifies from stage names and FINISH notes/status alone. This admits internally contradictory evidence.

**Required:** specify exact required outcome fields, strict primitive types, exact allowed values, and index equality for every action/rollback stage. Validate the full top-level stage envelope too (including required `action_type`, optional-field presence and forbidden fields). A stage name alone must not establish success or failure.

### R14-02 — Successful rollback classification lacks proof that the exact timeline item is gone (blocking)

The WAL grammar returns `TERMINAL_RECONCILED_ROLLED_BACK` after `ROLLBACK_DONE` and `FINISH(ROLLED_BACK)`, and the crash matrix allows the ledger to become `EXHAUSTED` and release the target. But the R14 `ROLLBACK_PENDING`/`ROLLBACK_DONE` examples do not carry the committed `resolve_item_id`; the validator only compares the 11 action-input fields and an optional `rollback_status`. It does not require a physical read-back receipt proving the specific inserted item was removed and the target occupancy is now safe. A successful API return or journal stage is historical evidence, not proof of current absence.

**Required:** bind rollback to the exact committed `resolve_item_id` and target identity, and define authoritative post-delete read-back evidence (including exact item absence and relevant occupancy checks) before classifying a rollback as reconciled or releasing target exclusion. If that evidence is unavailable or ambiguous, return an in-doubt result and retain the lock. Apply the same conservative distinction to any `ACTION_FAILED` path whose result does not prove that no timeline mutation occurred.

## Decision and next workorder

R14 addresses the four R13 findings in its declared identity/path/anchor and rollback-failure handling. However, the outcome fields remain permissive and a `ROLLBACK_DONE` journal record can still lead to a clean rollback classification without physical proof. R14 is **not accepted**. Do not start G07-07B6-I. Keep `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN.

The next bounded task is **G07-07B5-D-R15 — Exact Stage Outcomes & Physical Rollback Proof**. The DESIGN ONLY workorder is in `.bridge/task.md`.
