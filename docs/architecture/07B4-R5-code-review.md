# G07-07B4-R5 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review and offscreen tests only. No Resolve launch, IPC, FusionScript, or timeline access.

## Independent verification

All submitted SHA-256 values matched for the report, engine, receipt service, tests, design document, and both logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **38 passed in 1.24s**, exit 0.
- Same 18-suite regression as R3: **290 passed in 28.36s**, exit 0. No Resolve call was made.
- Independent attestation probe: altered JSON whitespace/serialization while preserving the decoded object; `verify_attestation()` returned `True` for both original and modified byte strings.

## Improvements verified

R5 removes the source-embedded default key and fails closed without configured trust; reads are bounded and descriptor-stat errors fail closed; action indices are checked for bounds, duplicate commits, commit sequence, and the next unresolved pending index; and reconciliation now has a durable `RECONCILING` phase before receipt/target finalization.

## Blocking residuals

### B4-R5-RES-01 — Attestation does not bind the exact artifact bytes

`verify_attestation()` parses `raw_bytes`, checks that the decoded object equals `artifact_data`, then computes HMAC over canonical JSON (`resolve_execution_engine.py:263-283`). This verifies semantic object equivalence, not the bytes: whitespace, key order, and other serialization changes that parse to the same object still pass. I confirmed this with a fresh probe: both canonical and reformatted bytes returned `True`. The R5 workorder/report claims changing artifact bytes invalidates attestation.

**Required:** Define one accepted canonical byte encoding and require `raw_bytes == canonical_serialization(artifact_without_signature + signature envelope)` or sign an explicit digest of the exact raw payload. Reject noncanonical encodings and duplicate-key JSON. Add a regression probe showing any byte change fails, while the exact signed bytes pass.

### B4-R5-RES-02 — Proof audit identity/time are not bound to the attested observation

The verifier validates the artifact's attested `operator_id`/timestamp fields as part of its full-envelope signature, but it only compares target, execution, action, and outcome against the caller proof. `reconcile_target()` then records `proof.operator_id` and `proof.reconciliation_timestamp_iso` in durable target/receipt state. A caller can therefore reuse a valid artifact and submit a proof with a different operator or timestamp; the attestation remains valid while the durable audit record adopts caller-selected values.

**Required:** Compare every authority/audit field copied from proof into durable state with the signed artifact, or derive those values only from the verified artifact. Add tampering probes for operator and timestamp that assert zero durable state changes and retained unresolved ownership.

### B4-R5-RES-03 — Journal checks do not pair every commit with its pending event

The ledger scan validates committed indices, then collects only `ACTION_PENDING` records whose index is *not* in `committed_set` (`:1016-1036`). A repeated pending record for an already committed action is silently omitted from `unresolved_pending_indices`; the code does not require exactly one preceding pending event for every commit, nor reject pending-after-commit or commit-without-pending. Consequently a journal can pass the claimed action-index bijection even though its event history does not represent the expected WAL protocol.

**Required:** Validate the stage sequence as a state machine: each planned action has exactly one `ACTION_PENDING`, followed by at most one matching `ACTION_COMMITTED` or an explicitly unresolved final pending; no pending after commit, no commit without preceding pending, and no unknown/conflicting terminal stage. Add malformed journals for each ordering/pairing case and assert no receipt/target transition, unlock, or journal mutation.

## Gate status

G07-07B4-R5 is **not accepted**. The reported tests are confirmed, but exact-byte attestation, proof-to-attestation audit binding, and full WAL event pairing remain unproven. Preserve `ready_to_execute=False`, MOCK ONLY, zero UI/live Resolve wiring, and keep G07/M1/M2/M3 open.
