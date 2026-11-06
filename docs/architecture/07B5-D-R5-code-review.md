# G07-07B5-D-R5 — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Static design/source review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes match:

- `.bridge/report.md`: `c207e8d975b7c2fffe4727ffdd52fe9cf392cc1cc814d797fe5190c44c37a513`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `5fd87eca72cd50ca42654311e25753a29115e507d141be10a4f3d6200a2b0b67`

R5 addresses the R4 findings substantially: it defines the pending-WAL linearization point, rejects late cancellation, adds revocation atomic replacement, binds the signed frame directly to `recordFrame`, and introduces a shared canonical serializer. Remaining issues still block design acceptance.

## Blocking residuals

### B5-D-R5-RES-01 — Durable revocation can be committed without a restart terminalization rule

The pre-dispatch revoke path durably replaces `revocations.json`, then the engine transitions the receipt to `ABORTED` and the target ledger to `EXHAUSTED`. A crash after the revocation file is durable but before either or both terminal writes leaves a revoked authorization with an `ACTIVE` target and/or `EXECUTING` receipt. CR-PRE-04 describes the completed outcome but gives no crash-repair predicate for this intermediate state. Define recovery for each boundary: verify exact authorization/target/execution identity, prove no matching `ACTION_PENDING`, then terminalize idempotently in a safe order; if any identity or journal evidence is missing/ambiguous, retain the target and require reconciliation. Include fsync/replace errors with uncertain visibility, not only process crashes.

The UI's late-revoke test must match the exact target UID, execution/authorization ID, and WAL record, not merely detect any `ACTION_PENDING` in a journal. Define the lock/read protocol for that identity check.

### B5-D-R5-RES-02 — `ACTION_PENDING` means cancellation is closed, not that IPC was sent

The linearization point is before `gateway.execute_action(action)`. A process can stop after pending fsync and before entering the gateway. At that moment cancellation is correctly too late under the chosen contract, but the error text says the action has already been queued/sent, and CR-DIS-01 states it has been sent. That is not established by the WAL record.

Model the interval as `ADMITTED_PENDING_DISPATCH` (or equivalent): revocation is rejected after the point, while restart treats the physical outcome as `IN_DOUBT` until independently reconciled. State plainly that durable pending intent proves admission, not IPC delivery. Specify the exact UI result and recovery for a crash in this gap.

### B5-D-R5-RES-03 — Strict JSON parser does not enforce the validator's claimed rules after decode

`parse_strict_canonical_json()` rejects duplicate keys and decodes UTF-8, but then returns the parsed dictionary without calling `validate_canonical_primitives(data)`. Python's default `json.loads` also accepts `NaN`/`Infinity` through `parse_constant`; escaped lone surrogates such as `"\\ud800"` survive raw UTF-8 decoding and become surrogate code points after JSON parsing. Thus the parser currently contradicts its documented rejection rules. Validate the complete decoded graph before returning and provide a `parse_constant` rejection hook (plus strict schema/type validation at each DTO boundary). Add the same guarantee to revocation/keyring/authorization readers.

### B5-D-R5-RES-04 — The claimed complete crash matrix omits revocation-commit repair and uncertain write outcomes

The 11 rows do not enumerate the durable sub-boundaries inside CR-PRE-04: revocation temp write, file fsync, replace, parent fsync, receipt abort, and ledger exhaustion. A failed fsync/replace may leave old or new state visible; the caller cannot assume the operation did not commit. Expand the table to these boundaries and to the exact `ACTION_PENDING`-fsync / gateway-call gap. For each, specify permitted durable states, restart identity checks, whether dispatch is impossible/possible/unknown, and the only safe repair. Unknown combinations must remain fail-closed and must never be auto-labeled “not dispatched.”

## Accepted corrections

- The successful terminal order now makes receipt `COMPLETED` durable before ledger `EXHAUSTED`, with a restart finalization path.
- `authorized_record_frame` is now the literal `recordFrame` argument, with a final observed start-frame check and the Resolve API race disclosed.
- The key deletion statement now accurately distinguishes OS DAC from physical flash erasure and same-OS-user processes.

## Required next step

Assign `G07-07B5-D-R6` as DESIGN ONLY to close the revocation crash-repair path, distinguish authorization admission from IPC delivery, enforce post-decode strict JSON validation, and complete the uncertainty matrix. Keep `G07-07B6-I` deferred until Codex accepts the design. Any `G07-07B7-LIVE` remains a separate future workorder requiring explicit written user authorization.

Preserve `ready_to_execute=False`, the mock-only tripwire, zero Resolve calls, and G07/M1/M2/M3 OPEN. No implementation is authorized by this review.
