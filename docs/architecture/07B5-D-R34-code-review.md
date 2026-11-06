# G07-07B5-D-R34 — Codex Static Review

**Decision: CHANGES_REQUIRED**  
**Scope:** Static review of Section 2.3 and Section 2.6 for G07-07B5-D-R34. No source or test files were changed. No pytest, Resolve, IPC, FusionScript, application, AI, or network execution was performed.

## Reviewed inputs

The submitted artifact hashes were recomputed from the workspace and match the handoff:

- `.bridge/task.md`: `cd7b52c216aaeed17152f76a548cf0b506894d41106738b39404f77dcba11ac5`
- `docs/architecture/07B5-D-R33-code-review.md`: `d370136657893155ae7ce8814a82b8df9a50072af78eb161a168306ddeadca65`
- `docs/architecture/07B5-D-R32-code-review.md`: `9b0aab2268ca9a9ff503b7dc7ebdc716c279b73661d3335474f375e49110586a`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `06d4817f43a6c7ec8b1575e318801b1930ac7c8aa9b4c275d7858cc67dc48161`
- `.bridge/report.md`: `aef8d9f49c8d88cd83247dbb4087e3507794fc0cc908a33c5a0fb9afef8db861`
- `scratch/test_r34_validator.py`: `f47cc4a5105072d92acc2f6ed737926a0c2e7cd7847e0478c531e6604d41d09b`

## Findings

### R34-01 — Ledger access lock does not serialize writers with the recovery scanner — BLOCKER

`DurableConsumedReceiptLedger.recover_and_scan_ledger()` acquires `flock(LOCK_EX)` at design lines 1729–1732, but `record_consumed_evidence()` does not acquire that lock before creating a reservation, writing its temporary record, publishing, or releasing the reservation (lines 1620–1720). The reservation file does not substitute for the global scanner/writer lock: the scanner can run after its initial scan has started and quarantine or remove a live writer's files while that writer is publishing. PID/lease checks cannot protect a writer that is not serialized by the same lock.

**Required correction:** Define and show one lock protocol used by both scanner and every writer, with bounded acquisition, ownership and `finally` release semantics. Keep the per-key reservation for consume-once admission if needed, but do not let the scanner inspect or mutate ledger entries concurrently with a writer. Add interleaving traces for scan-vs-reserve, scan-vs-temp-write, and scan-vs-publish, including crash and lock-acquisition failure.

### R34-02 — Consume-once publication still has a check-then-replace overwrite race — BLOCKER

`record_consumed_evidence()` checks whether `record_name` exists at lines 1629–1637, then creates a separate reservation at lines 1639–1649, and later publishes with `os.replace()` at lines 1711–1718. `os.replace()` replaces an existing destination. A destination created after the initial lookup can therefore be overwritten, despite the claimed consume-once property. The initial lookup also contradicts the R34 workorder's explicit instruction to eliminate the destination pre-check and rely on atomic admission.

**Required correction:** Make final destination publication itself no-replace/atomic under the shared lock and reservation protocol (or use a platform primitive with equivalent semantics). Treat any pre-existing destination as consumed/corrupt evidence and preserve its bytes. Specify recovery for a crash after destination creation but before reservation cleanup. Do not claim O_EXCL consume-once based only on reservation creation.

### R34-03 — The “exact 22-key” verifier does not validate the schema version or canonical UTC timestamp — BLOCKER

The verifier compares the key set at lines 1881–1885, but does not validate `proof_schema_version`'s type or supported value. It also reads `observation_timestamp_iso` without a string type gate and calls `datetime.fromisoformat()` at lines 1925–1933 without requiring an explicit UTC offset or normalizing/rejecting non-UTC offsets. A naive timestamp parses, then subtraction from aware `now_utc` raises `TypeError` outside the parse `try`; a nonzero offset is accepted. This violates the stated exact-schema and canonical-UTC contract and permits an unhandled exception at a trust boundary.

**Required correction:** Validate `type(proof_schema_version) is str` and its exact supported version; validate timestamp as a strict string in the canonical UTC grammar (explicit `Z` or `+00:00`, with non-UTC offsets and naive values rejected); place parsing, offset checks, and age arithmetic inside the fail-closed exception boundary. Require malformed values to yield the documented controlled verification error.

### R34-04 — Proof binding omits the authorized source out-frame and overstates the insertion outcome — BLOCKER

The coordinator compares `source_file_path` and `source_in_frame` with the pending payload (lines 2032–2035), but never compares `source_out_frame`. Section 2.3 binds `authorized_source_frame_start` and `authorized_source_frame_duration` (lines 568–569), and the 16-field action payload includes `source_frame_start` and `source_frame_duration` (lines 623–629). Thus a validly signed proof can report a different source range while passing the shown cross-binding checks. Further, `target_item_found=True` plus `adjacent_occupancy_clean=True` is classified as `VERIFIED_TARGET_INSERTED_MATCHING_GEOMETRY` at lines 2047–2051 without a check that the observed item identity and its source/record geometry equal the authorized action.

**Required correction:** Bind both source endpoints (or start plus duration) to the signed authorization and pending action, validate their exact interval relation, and specify the evidence fields that prove the observed Resolve item is the authorized inserted item at the authorized record interval. Derive the inserted outcome only after all identity and geometry comparisons pass; otherwise return an inconclusive/fail-closed result.

### R34-05 — Ledger scanner suppresses cleanup and parse errors and uses unbounded-by-EOF reads — HIGH

The scanner treats corrupt or unreadable reservation metadata as dead at lines 1755–1773, and silently ignores `unlink()` errors for temporary files and reservations at lines 1744–1747 and 1784–1788. This conflicts with the workorder requirement not to swallow reservation cleanup/unlink/fsync failures. The proof-record path reads at most `MAX_RECEIPT_BYTES` once at lines 1801–1809, without checking `fstat`, regular-file type, actual EOF, or an extra byte; a larger or concurrently growing file can be parsed from a truncated prefix. Quarantine also catches every exception around verification and then mutates the ledger, so operational I/O failures are indistinguishable from corrupt content.

**Required correction:** Use a shared bounded descriptor reader that validates `S_ISREG`, size before/after, exact EOF and growth, and rejects oversize input. Separate content/schema/HMAC corruption from I/O/permission failures. Only quarantine verified-corrupt artifacts; propagate cleanup, rename, and fsync errors as structured durability failures and preserve an in-doubt state.

### R34-06 — Section 2.6 retains a conflicting 14-field DurableProofRecord specification — HIGH

Section 2.6 subsection 10 still defines a `DurableProofRecord` with 14 pre-HMAC fields, `schema_version="1.0.0"`, and `proof_nonce` (lines 1196–1219). The R34 DTO later defines exactly 16 fields, `schema_version="1.0"`, `proof_record_id`, and `signer_key_id` (lines 1472–1519), while the workorder requires one synchronized schema across DTO, serializer, writer, and reader. These are two incompatible normative-looking definitions in the same design document.

**Required correction:** Replace the older subsection with a pointer to the single authoritative 16-field schema, or mark and remove the obsolete definition explicitly. Ensure writer, reader, canonical HMAC projection, schema version, field count, and round-trip examples all refer to that one contract.

### R34-07 — Durable writer loop can spin forever on a zero-byte write — HIGH

The temporary-file writer at lines 1703–1707 increments `written` by `os.write()` without handling `InterruptedError`, a zero-byte write, or an invalid count. A zero-byte write leaves `written` unchanged and loops indefinitely while holding the reservation; interruption exits without the defined cleanup path for the temp file and reservation. This also does not implement the bounded `_write_all` behavior already required elsewhere in the design.

**Required correction:** Specify one shared `_write_all(fd, data)` helper that retries `EINTR`, rejects zero/negative or over-length writes, and is used for reservation and proof-record writes. Put temp, reservation, descriptor, and lock ownership under explicit cleanup state transitions; cleanup failures must remain visible as structured in-doubt durability errors.

## Decision and safety boundary

R34 is **not accepted**. The workorder's central properties—writer/scanner serialization, no-overwrite consume-once admission, strict proof validation and complete authorization binding—are not established by the current normative pseudocode. Keep `ready_to_execute=False`; keep G07 and M1/M2/M3 OPEN. This review grants no live Resolve authority and does not authorize implementation against a real Resolve instance.

The review is static only. No tests were run, so the reported prototype result is recorded as a handoff claim, not independently verified evidence.
