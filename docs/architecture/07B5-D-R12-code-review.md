# G07-07B5-D-R12 — Codex Review

**Decision: CHANGES_REQUIRED**  
**Reviewed:** 2026-11-02  
**Submitted report SHA-256:** `735e00f85dc34f9fd2387582eb6871867fc2db09231b8bd20d1c2c7e2fa74843`  
**Design SHA-256:** `85b40d5021ea3d23004d236629dd929c6db6572d991fbb033c5695af939abd9f`

## Review scope

Static review only. Both submitted hashes were recomputed and match the files on disk. Reviewed the R12 WAL grammar, descriptor-first absence protocol, examples, and boundary matrix against the existing `JournalEntry` / `ExecutionJournalService` source contract. No tests, application, Resolve, IPC, FusionScript, or network activity was run.

## Findings

### R12-01 — The permitted rollback history contradicts the validator (blocking)

The grammar lists a valid rollback history `START -> ACTION_PENDING -> ACTION_COMMITTED -> ROLLBACK_PENDING -> ROLLBACK_DONE -> FINISH`. The pseudocode is position-based: at `line_idx == 3`, it requires `FINISH`; at `line_idx >= 4`, it only permits `RECOVERY_NOTE`. Therefore the declared rollback history is rejected, and rollback stages are not actually supported by the proposed verifier. The terminal result also has no explicit rollback outcome, so a rolled-back committed action could be confused with success.

**Required:** either remove rollback stages from the canary WAL contract and explicitly fail closed on them, or define a coherent rollback grammar, exact indices/cardinality and terminal classification. Make the table, pseudocode, crash matrix and result enum agree. Do not classify a completed rollback as ordinary successful insertion.

### R12-02 — Action payload comparison is conditional and incomplete (blocking)

At the COMMITTED/FAILED transitions, the pseudocode compares selected payload keys only under `if k in pending_canonical_params`. It neither requires every mutation-relevant field to be present in PENDING nor compares the complete canonical action payload. For example, `source_frame_start` and `source_frame_duration` can be absent from PENDING and then appear or differ in COMMITTED without this loop rejecting them. The pending branch also validates only a subset of the signed geometry fields. This does not meet the claimed 1:1 payload binding.

**Required:** define a strict per-action payload schema with required keys and primitive/range validation; bind its canonical digest to the authorization; require exact equality of the complete payload projection at PENDING and COMMITTED/FAILED. Stage-specific result fields may differ only through an explicit separate allowlist. Missing keys must not skip comparison.

### R12-03 — ENOENT proof remains vulnerable to parent-path replacement (blocking)

The design opens the WAL by path, receives `ENOENT`, and only then opens `journals_dir` by path and checks that the resulting object is a directory with expected ownership. A directory can be replaced between those operations; the later descriptor does not prove which parent object was used by the failed WAL lookup. The proposed parent open also does not specify `O_NOFOLLOW` or comparison against a previously trusted directory identity. Thus the descriptor does not yet bind the missing basename lookup to the intended journal directory.

**Required:** anchor the trusted journal directory first (or use an already-held verified directory descriptor), validate its identity and non-symlink status, then open the expected basename relative to that descriptor with no-follow semantics. Treat only `ENOENT` from that anchored child lookup as absence and only under the allowed receipt/ledger state. Parent replacement, traversal and other lookup failures must remain fail-closed.

## Decision and next workorder

R12 closes the raw-substring issue and corrects the serializer examples, but the rollback grammar, action-payload binding, and absence-path authority remain unresolved. R12 is **not accepted**. Do not start G07-07B6-I. Keep `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN.

The next bounded task is **G07-07B5-D-R13 — WAL Grammar Consistency, Complete Action Binding & Anchored Absence**. Its DESIGN ONLY workorder is in `.bridge/task.md`.
