# G07-07B4-R4 — Codex Code Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Safety boundary:** Static review and offscreen regression only. No Resolve launch, IPC, FusionScript, or timeline access.

## Independent verification

Submitted SHA-256 values matched for `.bridge/report.md`, engine, receipt service, test file, architecture document, and both submitted logs.

- Focused: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/services/test_resolve_execution_engine.py -q` — **34 passed in 1.04s**, exit 0.
- Same 18-suite regression as R3: **286 passed in 27.45s**, exit 0. No Resolve call was made.

## Findings

### B4-R4-RES-01 — Default HMAC key is public, so default attestation is forgeable

`DEFAULT_TRUST_ANCHOR_SECRET` is a literal in production source (`resolve_execution_engine.py:207`), and both `HmacEvidenceTrustProvider()` and `compute_attestation()` use it by default (`:225`, `:234`). The test artifact helper signs fixtures using that same exported default. An artifact author can import the source/helper, construct arbitrary target/outcome/freshness fields, compute a valid signature, and pass the default engine verifier. This is a known-key MAC, not a trust anchor. In addition, `raw_bytes` is not used by `verify_attestation()` and the signature covers a hand-built subset rather than a canonical full envelope, leaving unsigned artifact fields mutable.

**Required:** Fail closed if no externally provisioned trust verifier/key is supplied; remove production-known default secrets/signing convenience. Define a canonical, unambiguous signed payload that binds every authority-relevant observation field and the artifact bytes/schema. Tests may inject a test-only secret/provider, while proving the default path rejects without configured trust.

### B4-R4-RES-02 — Whole-plan terminal state uses a count, not a verified action ledger

`reconcile_target()` computes `committed_count = len(committed_entries)` then completes when `committed_count + 1 == planned_count` (`:948-965`). `replay_journal()` validates JSON parsing and sequence numbers but does not validate the semantic action-index sequence or uniqueness (`execution_journal_service.py:173-199`). Thus a duplicate `ACTION_COMMITTED` for one action plus confirmation of another can satisfy the count while a planned action is absent; multiple pending entries or a pending index inconsistent with committed entries are also not rejected as a complete-plan proof. Counting records is not a bijection between planned actions and durable commits.

**Required:** Validate the full journal action ledger against the planned action count and exact indices: no duplicate commits, gaps, contradictory/repeated pending outcomes, or action index outside the plan. `COMPLETED` requires a unique committed record for every planned index and no unresolved pending action. `ABORTED` requires authoritative proof that no action mutated and a consistent journal with zero commits. Any semantic ambiguity retains unresolved state and locks.

### B4-R4-RES-03 — Descriptor stat failure fails open

The bounded read itself correctly requests `max_bytes + 1` (`:324`), but `os.fstat()` errors are swallowed (`:329-336`). This defeats the claimed post-read descriptor-size verification when that check is unavailable or fault-injected. The path-level `getsize()` is not a substitute for a descriptor check under TOCTOU.

**Required:** Treat inability to obtain required descriptor metadata as a verification error; never continue to trust the file after failed `fstat`. Add a fault probe asserting no state transition, no unlock, and no journal mutation.

### B4-R4-RES-04 — Reconciliation can persist contradictory terminal records

The method writes the receipt terminal state first (`:983-991`), then separately writes target state `RECONCILED` (`:993-1008`), then releases in-memory unresolved ownership. If the target-state persistence fails after the receipt transition succeeds, the durable receipt is terminal while target state remains unresolved. This partial outcome is not covered by an atomic protocol or a recovery marker and conflicts with fail-closed persistence semantics.

**Required:** Specify and implement a crash-safe reconciliation transition protocol so partial persistence remains explicitly unresolved and restart admission can distinguish/recover it without replay. Inject failure between durable writes and verify consistent fail-closed state, retained lock/ownership where applicable, and zero replay.

## Gate status

G07-07B4-R4 is **not accepted**. The reported 286 passing tests are confirmed, but they do not establish an externally trusted default attestation, semantic journal/action bijection, fail-closed `fstat`, or crash-consistent multi-record reconciliation. Preserve `ready_to_execute=False`, MOCK ONLY, zero UI/live Resolve wiring, and keep G07/M1/M2/M3 open.
