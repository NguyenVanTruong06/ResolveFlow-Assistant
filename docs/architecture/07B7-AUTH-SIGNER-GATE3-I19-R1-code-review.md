# Gate 3 I19 Revision R1 Review

**Review date:** 2026-11-04  
**Workorder:** `G07-AUTH-SIGNER-GATE3-I19-R1 — Revision Contract & Deterministic Closeout Remediation`  
**Decision:** `CHANGES_REQUIRED` — I19-R1 and Gate 3 closeout are not accepted.  
**Scope:** Static review of the allowlisted implementation, tests, closeout dossier, report, and authorized test log. Tests were not rerun in this review.

## Evidence reviewed

The authorized log reports **687 passed, 0 failed, 0 skipped, 0 xfailed, 2 warnings, exit code 0**. The warnings are identified in the dossier as Python 3.12 `multiprocessing`/`fork()` deprecation warnings from the signer anchor concurrency test. The I19-R1 report lists the following observed hashes:

- `.bridge/task.md`: `e4762c119b84b2de68835ad8dcd8cb3076ba6267e7e2c0e5b78f650b1a231b3b`
- `src/core/contracts/execution_receipt_contracts.py`: `534eadea70ecef0c7fa59845f4d9ff93d8dd7060d0d5e8280c2e2b15bb2cd1cf`
- `src/services/durable_trial_target_ledger.py`: `7f25cbf7db9b90afceb97ba351220c29af3c603c558943e19150ffc9b5db02bf`
- `tests/integration/test_gate3_signer_dispatcher_integration.py`: `29a7b609d17c0af574a9b6d26e179518c22471d921a16de3ebb9109f7bdccbc1`
- `tests/tools/test_resolveflow_operator_console.py`: `be2c8aa11ff826a19faca14948cc168527abc83b8751ff2abeddf721a18f37aa`
- `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I19-R1/pytest.log`: `29f9e76b565c4744c52896dd845f7044d69586e7c91c5d041f11392603ccb6cc`

The submitted report records matching hashes for these artifacts. The positive integration path now keeps the durable ledger and receipt service enabled and asserts the persisted receipt and ledger carry the same digest revision as the plan and authorization. The two I18 negative tests now inject a fixed UTC clock. The closeout traceability rows cited in the prior review have been corrected.

## Finding

### I19-R1-01 — MEDIUM: Incompatible persisted field type retains schema version 1

`ExecutionReceipt.proposal_revision` has changed from `int` to a SHA-256 `str`, but `SCHEMA_VERSION` remains `1` (`src/core/contracts/execution_receipt_contracts.py:27, 260-267`). Likewise, `DurableTrialTargetLedger.proposal_revision` is now a digest string while `LEDGER_SCHEMA_VERSION` remains `1` (`src/services/durable_trial_target_ledger.py:64, 171-182`). The old version-1 representation used an integer; the new parser rejects that record by field type while still claiming the same serialized schema version.

This makes existing persisted version-1 receipt/ledger records indistinguishable by version from the new representation, despite the incompatible field contract. In addition, `ExecutionReceipt.from_dict()` still converts the version with `int(data.get("schema_version", SCHEMA_VERSION))` (`execution_receipt_contracts.py:411-412`), which accepts a boolean version and defaults a missing version to 1 rather than enforcing an explicit version boundary.

**Required:** Bump the receipt and durable-ledger schema versions for the digest-string representation. Require an exact built-in integer version field in serialized input, reject missing/boolean/unsupported versions with a structured error, and explicitly fail closed on legacy version-1 numeric revision records without rewriting them. Add tests covering version-1 legacy rejection, version-2 digest round-trip, and malformed version values. Do not perform data migration or access non-test data in this remediation.

## Decision and lifecycle state

The prior blockers are resolved in the submitted evidence: the dispatcher integration uses the durable target ledger and receipt service, clock-sensitive tests are deterministic, and the recorded suite passes. The remaining schema-version ambiguity affects persistent receipt/ledger interpretation and is within the remediation workorder’s explicit schema-version requirement. Gate 3 closeout therefore remains pending.

- I19-R1: `CHANGES_REQUIRED`; not accepted.
- Gate 3 and Gate G07: `OPEN` / not accepted.
- Milestones M1/M2/M3: `OPEN`.
- Gate 6: `NOT ACCEPTED` and incident-only.
- `ready_to_execute = False`; this review does not authorize Gate 4 or live operations.
