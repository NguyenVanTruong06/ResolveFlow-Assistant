# G07-07B5-D-R16 — Codex Static Design Review

**Date:** 2026-11-02  
**Decision:** `CHANGES_REQUIRED`  
**Scope:** Design and source-contract review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

The submitted SHA-256 values match the current files:

- `.bridge/report.md`: `ab8485e1f6151a024556e4bb36fae7921b3379e0d59ad94b364f85e619c0dfe8`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `155f4bfa5f7b6575d63b2b9dc33f4b97c9fc3e1214ad986ef1752be487e66647`
- `docs/architecture/07B5-D-R15-code-review.md`: `d4ea21c0e3f1e5cb54aacd13ca30ed670a6f75e2bda30ab8eb9f19ac24ade41e`
- `.bridge/task.md`: `afe1918c4da4c4e8ea13cc95267f4f0a779565b0d7d58c62c3dcd391761272e9`

R16 correctly removes `ERR_APPEND_TIMELINE_REJECTED` from the pre-dispatch allowlist, distinguishes post-dispatch and ambiguous failures, states that WAL booleans are claims, and adds authenticated-readback and fresh-query concepts. The following gaps still prevent acceptance.

## Blocking findings

### R16-01 — An unauthenticated WAL error code can still release the target lock

The design correctly says the WAL is editable and its producer booleans are not proof (Sections 2.6.4–2.6.6, especially lines 921–935). However, the proposed terminal logic at lines 1806–1811 classifies an `ACTION_FAILED` as clean when its WAL `error_code` is in `PROVEN_PRE_MUTATION_REJECTION_CODES` and its WAL `mutation_clean_rejected` field is true. The FSA reaches `ACTION_FAILED` only after `ACTION_PENDING` (lines 1704–1710), so the recovery decision is being made from the same unauthenticated file whose tampering the threat model allows. An editor can change the error code and boolean on a dispatched action to a pre-dispatch code and reach `TERMINAL_RECONCILED_FAILED` without physical readback.

**Required:** A code name and boolean in JSONL cannot establish that dispatch was never attempted. Before releasing the lock for any `ACTION_FAILED` after durable `ACTION_PENDING`, require an authenticated, execution/action-bound producer attestation that proves the dispatch boundary was not crossed, or require fresh authoritative readback. Otherwise remain `IN_DOUBT`. Keep the pre-dispatch-by-construction path only where the no-dispatch fact is independently established and cannot be forged by editing the WAL.

### R16-02 — Readback receipts are replayable across executions

The receipt schema (lines 939–958) binds project, timeline, item, track, and frame range, but does not bind `authorization_id`, `execution_id`, `plan_idempotency_key`, `action_index`, or `action_payload_digest_sha256`. The verifier (lines 1449–1508) consequently cannot compare those values to the unresolved trial. It checks that the nonce is 64 hex characters and that the timestamp is within the TTL (lines 1464–1466, 1494–1504), but never records or rejects a previously consumed nonce/receipt ID. A valid clean observation from an earlier attempt on the same target and range can therefore be replayed during the 300-second window to clear a later post-dispatch failure; for the failed-append path, the expected `target_item_id` is explicitly the empty string (lines 1816–1824), leaving no item identity to distinguish attempts.

The HMAC key is also described only as `engine_session_key` (line 961), while the use case is verification after process restart. The design does not state how the same trusted key is recovered, identified, rotated, or rejected if unavailable.

**Required:** Bind each receipt to the full immutable trial/action identity, including authorization/execution/plan and action digest, and bind the observation nonce to a fresh challenge for that exact unresolved action. Define durable one-time consumption or an equivalent monotonic ledger check under target exclusion. Specify a restart-stable trusted key source/key ID (or explicitly require live readback whenever that key is unavailable); do not treat a format-valid nonce or TTL as replay prevention.

### R16-03 — The fresh-readback callback does not prove which Resolve target it observed

The recovery API accepts `fresh_resolve_query_func: Callable[[], Tuple[bool, bool, int]]` (line 1523). The terminal branches then release the lock solely when the returned `absent` and `clean` values are truthy (lines 1790–1799 and 1826–1836). This interface carries no project/timeline UID, exact item ID, track/range, enumeration-completeness status, observation time, or gateway identity. It also does not state that the query is dispatched through the owning ResolveGateway thread and that the active project/timeline UIDs are re-read immediately before enumeration. A callback can return `(True, True, ...)` without establishing any of those facts.

**Required:** Replace the boolean tuple with a strict, owned readback result bound to the requested target and action. Specify the ResolveGateway ownership/thread boundary, revalidate active project and timeline UIDs, require complete unambiguous track enumeration and exact interval checks, and fail closed on missing identity, partial results, or query exceptions. The caller must not be able to assert a clean result by supplying booleans.

### R16-04 — Receipt verification does not enforce the declared receipt schema

The declared DTO requires `receipt_id`, `observer_identity`, and `items_scanned_count` among other fields (lines 943–958). The verifier shown at lines 1460–1508 does not validate those fields or enforce an exact key set / schema allowlist. It also parses `observation_timestamp_iso` without requiring an explicit UTC offset (`datetime.fromisoformat` accepts naive timestamps), although the contract calls it UTC. A correctly HMAC-signed but incomplete or semantically mislabelled receipt can pass the checks that are present.

**Required:** Validate every required receipt field with strict primitive types and exact allowlisted schema; require the expected observer identity and a canonical UTC timestamp with explicit offset; validate receipt ID and scan count/range invariants. Validate `current_time_epoch` and `max_ttl_seconds` as finite trusted inputs. Add negative fixtures for omitted fields, unknown keys, wrong observer, naive timestamp, invalid scan count, replayed nonce, and wrong execution/action binding.

## Required invariants for the next design revision

- Preserve R16's three-way distinction between proven pre-dispatch, post-dispatch rejection, and ambiguous mutation, but make the evidence source independently verifiable.
- Preserve R14/R15 exact action payload, per-record identity, source path, directory anchor, stage-envelope, canonical serialization, and bounded WAL-to-EOF rules.
- Preserve literal signed `authorized_record_frame` → Resolve `recordFrame`, the final `GetStartFrame()` check, and the disclosed Resolve query/append race.
- Missing, stale, replayed, incomplete, wrong-target, or unverifiable evidence must retain the target exclusion and remain `IN_DOUBT`.

## Decision and next bounded workorder

R16 is **not accepted**. Do not begin `G07-07B6-I` or live Resolve work. Next: **G07-07B5-D-R17 — Authenticated Failure Evidence, Execution-Bound Readback & Restart-Safe Replay Defense**, DESIGN ONLY. Update Section 2.6, the crash matrix, and finite fixtures to close R16-01 through R16-04. Keep `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN. `G07-07B7-LIVE` still requires separate explicit written user authorization.
