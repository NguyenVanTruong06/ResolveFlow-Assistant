# Codex review — G07-07B5-D-R9

**Decision: CHANGES_REQUIRED**  
**Review mode:** static document/source-contract review only. Submitted report/design SHA-256 values match. No pytest, app, Resolve, IPC, FusionScript, AI, or network execution.

## Evidence checked

- `.bridge/report.md`: `9970a926524cfd2fdaab96baf518a5c06b114a187ab65b88214acf148f4f378a` — matches.
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `45180f8c4bb97f2af96725b7da8c1f91f598d5e5ab806d7785cce160b35a6a34` — matches.
- Read-only source contract check: `src/core/contracts/execution_receipt_contracts.py` defines receipt identity as `execution_id`, `idempotency_key`, `approval_id`, `project_id`, and `timeline_id`; it does not define `authorization_id`.

R9 corrects the prior scanner's literal/container/string accounting, recognizes valid escaped surrogate pairs, adds dual receipt/ledger polling, and specifies bounded WAL size plus EOF validation. Two residuals remain.

## Blocking findings

### R9-01 — UI terminal proof does not map to the actual receipt schema or compare both records' full identity

The ten UI predicates in §2.5 require `receipt.authorization_id`, but the existing `ExecutionReceipt` contract has `approval_id` and no `authorization_id`. The predicates also do not compare the receipt's actual `project_id`, `timeline_id`, and `idempotency_key` against the expected target/plan. Checking the ledger's target fields and receipt's execution ID alone does not prove that the receipt belongs to the same project, timeline, plan, or authorization represented by the ledger. The UI success message can therefore rely on a nonexistent field or accept a cross-record identity mismatch.

**Required correction:** Define the exact persisted trial receipt schema and its relationship to `ExecutionReceipt` (including whether a trial-specific wrapper adds `authorization_id`). Do not silently equate `approval_id` with `authorization_id`. The success predicate must validate every identity field actually persisted in both records against the expected request and cross-check all shared fields between them, in addition to `ABORTED`, `EXHAUSTED`, and `terminal_receipt_id == receipt.receipt_id`. Specify safe behavior for missing fields, read errors, and mismatches.

### R9-02 — WAL descriptor read can validate a truncated prefix and accepts non-strict primitive types

The WAL pseudocode obtains `st_size` with `fstat()` and calls `os.read(fd, file_size)` once, but does not require the returned byte count to equal `file_size` or verify the descriptor's size/identity remained stable. A permitted short read could return a newline-terminated `START` prefix while omitting a later `ACTION_PENDING`; the prefix can then satisfy the one-record no-pending rule. The design also opens the path before proving it is a regular file, and several equality checks accept booleans as integers in Python (`False == 0`, `True == 1`): `sequence_number`, `total_actions`, and `action_index` are not guarded with strict `type(value) is int` checks. Required fields are checked, but unexpected fields are not rejected despite the strict-envelope claim.

**Required correction:** Open safely without blocking on non-regular files; use `fstat` to require a regular file; perform a bounded exact read loop and fail if fewer/more bytes than the captured size are read; verify stable descriptor identity/size after reading. Reuse the strict control-record envelope validator or specify exact per-stage allowlists and strict primitive types, excluding bool for all integer fields. Only then permit `PENDING_ABSENT_CONFIRMED` or `PENDING_CONFIRMED`; every read instability or malformed envelope must remain `IN_DOUBT_WAL_CORRUPTED`.

## Required R10 deliverable

Update `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` and the relevant receipt/UI and WAL contracts to close R9-01 and R9-02. Add bounded traces/fixtures for receipt schema mismatch, cross-record project/timeline/plan mismatch, bool-valued WAL integers, unknown envelope fields, FIFO/non-regular WAL path, and short-read or size-change during descriptor reading. Update `.bridge/report.md` and handoff metadata.

**Constraints remain binding:** DESIGN ONLY; zero edits in `src/` and `tests/`; no pytest/app/Resolve/IPC/FusionScript/AI/network execution. Preserve `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN. Keep `G07-07B6-I` deferred until design acceptance; any `G07-07B7-LIVE` requires separate explicit written user authorization.
