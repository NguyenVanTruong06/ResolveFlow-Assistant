# G07-07B5-D-R11 — Codex Review

**Decision: CHANGES_REQUIRED**  
**Reviewed:** 2026-11-02  
**Submitted report SHA-256:** `99ce068c92e6658ad8a2a6029f9471c04b77e2bfaa5239dcd9b16ab7eb7f3d06`  
**Design SHA-256:** `d42e73e8cc1872d253494366a3854b0f21c53391cb6d1e84bd6d70e818d179cf`

## Review scope

Static review only. Both submitted hashes were recomputed and match the files on disk. The review compared section 2.6 of the R11 design against `JournalEntry`, `STAGE_ALLOWED_KEYS`, `validate_raw_journal_record()`, `journal_entry_to_json_line()`, and the existing `ExecutionJournalService` persistence path. No tests, application, Resolve, IPC, FusionScript, or network activity was run.

## Findings

### R11-01 — Raw substring search can falsely prove that no dispatch occurred (blocking)

In the proposed `validate_wal_to_eof()` final classification, a two-line WAL is classified `PENDING_ABSENT_CONFIRMED` when the second raw line contains the bytes `CANCELLED_AT_PRE_DISPATCH_BOUNDARY` anywhere. This does not establish that the parsed record is the exact valid `FINISH` cancellation record. The phrase can occur in an unrelated string value, including `error_message` or nested `action_params`, and still satisfy the byte search. The schema validator checks allowed keys and primitive fields, but this branch does not verify the cancellation note/status as an exact semantic tuple.

**Required:** classify cancellation from the fully parsed record only, requiring the exact stage, note, terminal status, action index and permitted fields for the pre-dispatch cancellation form. Bind those values to the validated START and trial identity. Any other record or extra/conflicting stage must fail closed as `IN_DOUBT_WAL_CORRUPTED`; never infer zero IPC from a substring.

### R11-02 — WAL stage machine is not a complete grammar (blocking)

The pseudocode accepts repeated `ACTION_PENDING` records, allows `FINISH`, rollback stages and `RECOVERY_NOTE` as unconditional pass-throughs, and does not require the final record or its terminal status to agree with the action history. The `has_action_pending` boolean then collapses distinct histories into `PENDING_CONFIRMED`. This is not a complete validation of the journal lifecycle, despite the stated requirement to preserve and validate all supported journal stages.

**Required:** define a bounded grammar for the isolated one-action trial, including the exact allowed ordering and cardinality of START, pending, committed/failed, rollback, finish and recovery records. Specify which complete histories mean pending/dispatch uncertain and which are malformed or reconciled. Enforce action payload/identity equality across matching pending and terminal action records. Reject duplicate, contradictory, out-of-order and post-FINISH records.

### R11-03 — Existence check can turn path errors into false absence (blocking)

The design calls `os.path.exists(wal_path)` before descriptor-based opening and treats a false result as proof that the WAL was never created when receipt/ledger states are initial/reserved. `exists()` may report false for inaccessible paths and dangling symlinks; it also creates an exists/open race. Those conditions are not equivalent to a confirmed `ENOENT` for the expected regular-file path and cannot prove zero IPC.

**Required:** make descriptor opening the authority for absence. Treat only a narrowly verified `FileNotFoundError` for the expected path and parent context as absent; distinguish permission, I/O, symlink, non-regular-file, and other lookup failures as unavailable/corrupt evidence. Document how path traversal and parent-directory replacement are excluded. No access/stat error may produce `PENDING_ABSENT_CONFIRMED`.

### R11-04 — “Exact emitted bytes” examples do not match the existing serializer

The design labels seven compact, key-sorted JSON snippets as exact emitted bytes. The current `journal_entry_to_json_line()` uses `json.dumps(entry.to_dict(), ensure_ascii=False)` with default separators and dictionary insertion order. `JournalEntry.to_dict()` emits `seq`, `stage`, `action_type`, `timestamp_iso`, `action_params`, followed by optional fields in its implementation order. The examples instead sort keys and omit the serializer's spaces. They are semantically illustrative JSON, not byte-for-byte output from the cited producer.

**Required:** either provide examples generated according to the actual `to_dict()` and `journal_entry_to_json_line()` behavior, including the final newline, or explicitly label them semantic JSON examples and remove claims that they are exact source-emitted byte sequences. If the trial producer intentionally uses a different serializer, define its versioned writer and persistence contract instead of attributing its bytes to the existing service.

## Decision and next workorder

R11 resolves the prior producer/schema mismatch at the structural level, but the three blocking classification/evidence issues and the byte-example mismatch prevent acceptance. Do not start G07-07B6-I. Keep the mock-only tripwire and `ready_to_execute=False`; G07/M1/M2/M3 remain OPEN.

The next bounded task is **G07-07B5-D-R12 — Exact WAL Grammar, Absence Proof & Source-Emitted Serialization**. Its workorder is in `.bridge/task.md`. It is DESIGN ONLY and must stop for review before any implementation authorization.
