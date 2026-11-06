# G07-07B5-D-R17 — Codex Static Design Review

**Date:** 2026-11-02  
**Decision:** `CHANGES_REQUIRED`  
**Scope:** Static design/source-contract review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

The submitted SHA-256 values match the workspace files:

- `.bridge/report.md`: `4583dcf7764d050284bd09ba841efbfe3de40f7cd77fa9d4a958600dc0f76c1a`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `bfe1d2765c6b528a9ff0749bf24b3f85a3c44fc57dc59bd84e060bf2a42cec12`
- `docs/architecture/07B5-D-R16-code-review.md`: `80c2ae43582c1a1e9ad02ae5c94dce91152c3990b3b87e1176cdfde8ff88b61f`
- `.bridge/task.md`: `4eb9568d44008077b2c54e3ba59e5a41648fb3ad27572a444d4f86cdacc56aed`

R17 closes the R16 WAL error-code issue and substantially improves receipt identity binding and schema checks. The remaining issues below still allow an observation to be replayed or fabricated at the recovery boundary, so the design is not accepted.

## Blocking findings

### R17-01 — `LiveResolveObservationResult` is caller-constructible, stale, and not bound to the action

The DTO at Section 2.6.6, lines 981–998, includes `gateway_identity` as a normal string with a default value. The verifier at lines 1608–1641 accepts the result if that string equals `RESOLVE_GATEWAY_WORKER_THREAD_V1`; a frozen dataclass and a self-asserted string do not prove that the ResolveGateway actually produced the observation. The validator receives `live_resolve_observation` as an ordinary optional argument (lines 1644–1659), so a fabricated or previously captured instance can be supplied.

The verifier checks target UIDs, item ID and range, but does not validate `observation_timestamp_iso` or its freshness, nor bind the observation to `authorization_id`, `execution_id`, `plan_idempotency_key`, `action_index` or `action_payload_digest_sha256`. This is especially exposed in the `ACTION_FAILED` path, which expects `target_item_id == ""` (lines 1960–1965): a stale clean observation for the same target and range can be replayed for a later failed action.

**Required:** Make the recovery coordinator request the observation from the owning ResolveGateway for the current unresolved action, rather than accepting an arbitrary DTO as proof. Bind the response to a fresh request ID/nonce and the full action identity; validate timestamp freshness and every field with strict primitive types. Define an app-owned provenance mechanism that cannot be satisfied merely by constructing the DTO or setting `gateway_identity`. Missing, mismatched, stale or unverifiable provenance must remain `IN_DOUBT`.

### R17-02 — Receipt replay defense is not durably consumed by the proposed verifier

The design says `receipt_id` and `observation_nonce` are persisted as consumed (lines 968–972), but the pseudocode does not implement that transition. `validate_wal_to_eof()` accepts `consumed_receipt_ids` as optional (lines 1644–1659) and silently substitutes a new empty set when it is absent (line 1896). `verify_authoritative_readback_receipt()` only checks membership (lines 1522–1538); it never adds either identifier or persists a ledger update. The terminal branches then return a successful classification directly after verification (lines 1936–1939 and 1970–1973).

Thus the same receipt can pass repeated calls when the optional set is omitted or reconstructed empty. The design also does not specify crash ordering between successful verification, durable consumption, terminal receipt/ledger update, and target-lock release. A crash at that boundary can leave the receipt reusable.

**Required:** Make the consumed-ID authority mandatory and durable; no default empty set. Define an atomic or crash-repairable protocol under the target lock that records both IDs before any terminal release. On restart, an already-consumed proof must never be replayed; if consumption is durable but terminalization is incomplete, require a fresh live observation or remain `IN_DOUBT`. Add crash cases on both sides of the consume/terminalize boundary.

### R17-03 — Timestamp rule claims UTC but accepts non-UTC offsets

The receipt schema says timestamps must be `Z` or `+00:00` (line 950), but the validation rule allows any `[+-]\d{2}:\d{2}` suffix (line 966). The verifier applies `RE_ISO_TIMESTAMP` and then parses the supplied offset (lines 1582–1590), so a timestamp such as `2026-11-02T15:00:00+07:00` is accepted even though the contract requires UTC representation. Also, the finite-time checks use `isinstance(value, (int, float))` (lines 1505–1509), which accepts `bool` in Python.

**Required:** Align the schema and implementation contract: either require canonical UTC (`Z` or `+00:00`) and reject nonzero offsets, or explicitly define normalization to UTC before signing and verification. Use strict numeric type gates that reject booleans for trusted time/TTL inputs. Include nonzero-offset and boolean-input fixtures.

## Required invariants for the next design revision

- Preserve the R17 rule that an unauthenticated WAL `error_code`/boolean after `ACTION_PENDING` cannot release the target lock.
- Preserve full receipt binding, exact schema, restart-safe key selection, source-aligned WAL parsing and fail-closed behavior.
- Preserve literal signed `authorized_record_frame` → Resolve `recordFrame`, the final `GetStartFrame()` check, and the disclosed Resolve query/append race.
- Missing, stale, replayed, incomplete, wrong-target or unverifiable evidence must retain the target exclusion and remain `IN_DOUBT`.

## Decision and next bounded workorder

R17 is **not accepted**. Do not begin `G07-07B6-I` or live Resolve work. Next: **G07-07B5-D-R18 — Live Observation Provenance, Durable Receipt Consumption & Canonical UTC**, DESIGN ONLY. Update Section 2.6, receipt-consumption crash ordering, the recovery matrix and finite fixtures. Keep `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN. `G07-07B7-LIVE` still requires separate explicit written user authorization.
