# G07-07B6-I3 Revision R1 Code Review

**Decision: CHANGES_REQUIRED**  
**Review mode:** Static review only; no tests, Resolve, IPC, FusionScript, network, or AI were run.  
**Scope:** R1 implementation against the six findings in `docs/architecture/07B6-I3-code-review.md`, Workorder I3, and the accepted I3 plan/design.

## Artifact identity

All submitted SHA-256 values below were independently checked against the current files and match:

| Artifact | SHA-256 |
|---|---|
| `.bridge/task.md` | `d45aa5c7ea116a149e6c90c5b5abe6ad87b57509cc3378e96d2e0a511f8ca101` |
| `.bridge/report.md` | `09b43526cbec0474e982e04b2663afa8e0f24e521bf5e55444a021f56cec101e` |
| `src/services/durable_trial_target_ledger.py` | `77214ccc07578347d022c48caf2732b3f24bc8189b49942831a97688452bf66b` |
| `tests/services/test_durable_trial_target_ledger.py` | `2bed27ba37c673e243f7d1377108b1b810c7138c69d0c18548ab53f2151d572a` |
| `.bridge/ux-evidence/G07-07B6-I3/pytest_focused.log` | `6563d7f1b7b1e4aada3daa945f1058d2b5c488c81a99ac46be1030797bafb03c` |

The report states 26 focused tests and 126 combined I1+I2+I3 tests passed. These are submitted results; they were not rerun for this static review.

## R0 finding disposition

- **I3-01:** Addressed in `RecoveryCoordinator.close()`; unlock failure now retains the target FD and marks the entry `IN_DOUBT_LOCKED`.
- **I3-02:** Addressed; ledger replacement now uses `src_dir_fd` and `dst_dir_fd` anchored to `_dir_fd`.
- **I3-03:** Partially addressed; public writes now lock and reject terminal rewind, but do not preserve all identity fields (see I3-R1-01).
- **I3-04:** Addressed for live writers; scan checks the per-target lock before deleting a temp file and reports unlink failures.
- **I3-05:** Addressed; expired authorization no longer clears the active challenge.
- **I3-06:** Addressed; target lock mode is checked before the lock is accepted.

## Remaining findings

### I3-R1-01 — Existing ledger identity can be rewritten under the same execution ID (BLOCKER)

The new public `write_ledger_atomic()` checks target UIDs and `execution_id`, then permits an existing `RESERVED` or `ACTIVE` record to be replaced (`src/services/durable_trial_target_ledger.py:562-587`). It does not compare `authorization_id`, `approval_id`, `proposal_id`, `proposal_revision`, or `plan_idempotency_key` with the persisted record. A caller can therefore keep the execution ID and rewrite the durable authorization/approval/proposal binding while making an otherwise allowed state update. This defeats the ledger's purpose as durable identity evidence and contradicts the workorder's requirement to reject identity mismatch.

The regression test covers attempted rewind from `EXHAUSTED`, but not identity mutation in `RESERVED` or `ACTIVE` (`tests/services/test_durable_trial_target_ledger.py:1285-1333`).

**Required:** Compare every immutable identity field against the on-disk record before publication. Reject any mismatch without changing the file. Add tests that alter each bound identity field while preserving target UIDs and `execution_id` in both nonterminal states.

### I3-R1-02 — Operator signature does not bind the exact submitted expiry or an injective canonical encoding (HIGH)

The verifier accepts `authorization.challenge_expires_at` when it differs from the active challenge expiry by up to `1e-4` (`src/services/durable_trial_target_ledger.py:1252-1256`), but constructs the HMAC payload from `entry.challenge_expires_at`, not the submitted authorization field (`:1285-1293`). Consequently, distinct expiry values in the authorization DTO can pass under the same signature. In addition, the signed bytes are assembled by colon-delimited string concatenation rather than a canonical serialization of named fields; `operator_identity` and key identifiers are not constrained to exclude the delimiter. This does not satisfy the workorder's exact canonical-authorization-bytes binding and makes the encoding non-injective.

**Required:** Require exact expiry equality and compute the HMAC over a versioned canonical serialization of the complete, explicitly named authorization claims. Use the identical serializer for signing and verification. Add tampering tests for a sub-tolerance expiry change and delimiter-bearing field values.

## Decision and safety boundary

R1 resolves the six R0 findings, but the remaining identity-binding and signature-binding defects block acceptance of I3. Decision remains **CHANGES_REQUIRED**. Preserve `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain OPEN. This review does not authorize I4, production/UI wiring, or live Resolve work.
