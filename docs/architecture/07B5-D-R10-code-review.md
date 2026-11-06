# Codex review — G07-07B5-D-R10

**Decision: CHANGES_REQUIRED**  
**Review mode:** static design/source-contract review only. Submitted report/design SHA-256 values match. No pytest, app, Resolve, IPC, FusionScript, AI, or network execution.

## Evidence checked

- `.bridge/report.md`: `bccb66a897ffe45ba886f1eefe14e877fbdb23058929c5901ca36a98b7646e9b` — matches.
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `0568d47adeddc56ebbb89ca66d056f615df6efb97e18c3271fcb23d160c7d08c` — matches.
- Read-only source check of `src/core/contracts/execution_receipt_contracts.py` and `src/services/execution_journal_service.py` confirms the persisted journal contract described below.

R10 resolves the prior receipt identity gap with a separate trial wrapper, keeps approval and authorization identities distinct, and adds descriptor-based bounded WAL reading. One blocking source-contract mismatch remains.

## Blocking finding

### R10-01 — Proposed WAL reader is incompatible with the persisted `ExecutionJournalService` format

The design's `validate_wal_to_eof()` expects each JSONL row to contain top-level `sequence_number`, `timestamp_ns`, `execution_id`, `authorization_id`, `target_project_uid`, `target_timeline_uid`, and `plan_idempotency_key`, with action fields such as `authorized_record_frame` at top level. The existing `ExecutionJournalService` serializes `JournalEntry.to_dict()` in a different schema: `seq`, `stage`, `action_type`, `timestamp_iso`, and nested `action_params`, with optional `resolve_item_id`, track fields, errors, and notes. The existing valid stage set also includes `ACTION_FAILED`, rollback stages, and `RECOVERY_NOTE`, which R10's `WAL_STAGE_ALLOWED_KEYS` rejects. The current engine writes these existing `JournalEntry` records through that service; the proposed B6 slice does not define a replacement/versioned trial-journal writer or a distinct WAL path.

Consequently, the validator cannot validate the WAL that the existing service writes. A normal existing `START` record would fail the R10 required-field/allowlist checks, while a valid `ACTION_FAILED` or recovery record would be classified as an unknown stage. Conversely, the custom top-level identity fields have no defined producer/persistence contract in the current journal path. The design therefore does not yet establish an implementable complete-WAL proof.

**Required correction:** Choose and document one coherent persistence contract:

1. **Recommended:** Reuse the existing `JournalEntry` / `ExecutionJournalService` JSONL schema. Specify how exact trial authorization and target identity are bound into each row (or into a versioned, integrity-bound journal header), then define the bounded descriptor reader and complete stage machine against the actual `seq`, `timestamp_iso`, `action_params`, optional fields, and all applicable stages. Preserve the existing torn-tail and byte-retention rules; or
2. Define a separate, explicitly versioned `TrialExecutionJournal` schema and writer, a distinct path/ownership/locking protocol, and update the proposed implementation slice/callgraph so that every producer and consumer uses that schema. Do not describe this as validation of the existing `ExecutionJournalService` WAL.

In either case, add source-grounded examples showing the exact bytes emitted for START, ACTION_PENDING, and terminal/failure paths, and demonstrate how a complete valid journal maps to `PENDING_ABSENT_CONFIRMED`, `PENDING_CONFIRMED`, or fail-closed `IN_DOUBT_WAL_CORRUPTED`.

## Required R11 deliverable

Revise `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` to resolve R10-01, including the journal producer, schema, path, bounded reader, stage machine, identity binding, and crash/recovery matrix. Update `.bridge/report.md` and handoff metadata to match. Keep the UI's 31 receipt/ledger predicates and the exact descriptor checks unless the source-grounded journal contract requires a documented adjustment.

**Constraints remain binding:** DESIGN ONLY; zero edits under `src/` and `tests/`; no pytest/app/Resolve/IPC/FusionScript/AI/network execution. Preserve `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN. Keep `G07-07B6-I` deferred until design acceptance; any `G07-07B7-LIVE` requires separate explicit written user authorization.
