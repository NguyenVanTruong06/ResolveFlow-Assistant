# Codex review — G07-07B5-D-R8

**Decision: CHANGES_REQUIRED**  
**Review mode:** static design review only. Submitted report/design SHA-256 values match. No pytest, app, Resolve, IPC, FusionScript, AI, or network execution.

## Evidence checked

- `.bridge/report.md`: `d69d28f0bc37ef3728a078bd648a35ec2321ac7573b6b04fcd2d8736bf8fa111` — matches.
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `592e40aca1fabc52f21b6a3a05fea6f2b34182511a58b6c919aa56287e06549c` — matches.

R8 resolves the R7 writer-ownership direction: CancellationWorker only persists revocation, while the TargetLock holder owns terminal receipt/ledger transitions. It also introduces pre-parse scanning and a WAL-to-EOF validation contract. Three residuals remain.

## Blocking findings

### R8-01 — UI reports terminal success before verifying both durable records

In §2.5 the UI polling rule says that observing `Receipt == ABORTED` is sufficient to announce both `Receipt ABORTED` and `Ledger EXHAUSTED`. The engine/restart-repair sequence writes these records separately, so the UI can observe the receipt in the interval before the ledger transition has durably completed. This contradicts the stated distinction between “revocation durably recorded” and “terminal state durably verified.”

**Required correction:** Report full cancellation completion only after independently reading and validating both receipt and ledger, with matching execution/authorization/target/terminal-receipt identities and terminal states. Otherwise retain the pending/revocation-recorded status or report a bounded wait timeout. State what happens if either read fails or the identities disagree.

### R8-02 — Pre-scan does not enforce its limits accurately

The proposed scanner increments `total_nodes` for containers, strings, and numbers, but not JSON literals (`true`, `false`, `null`). It counts commas and compares that count directly to `MAX_CONTAINER_LENGTH`, allowing one extra element/member (e.g. 513 values have 512 commas). Inside strings, escaped characters do not increment `current_string_len`, so strings composed of `\\u0061` or short escapes can exceed the decoded string limit before `json.loads`; the post-parse validator catches them only after allocation. The scanner also rejects every `\\uD800`–`\\uDFFF` code unit, including valid high/low surrogate pairs representing a Unicode scalar, although the policy is to reject lone surrogates.

**Required correction:** Use a grammar-aware bounded scanner or streaming parser that counts every JSON value and decoded string scalar exactly, counts array items/object members without off-by-one, and distinguishes valid surrogate pairs from lone surrogates. Add explicit boundary examples for literals, escaped strings, 512/513 members, nested combinations, and valid/lone surrogate escapes. Keep the post-decode graph validation as defense in depth.

### R8-03 — WAL absence/presence is not yet established by complete validated evidence

The helper contract says full validation to EOF, but the pseudocode has shortcuts and incomplete validation:

- A missing WAL path and a zero-byte WAL both return `True` as proof of no pending. The design does not establish how a genuinely never-created journal is distinguished from a missing/truncated/corrupt expected journal for an `EXECUTING` receipt and `ACTIVE` target.
- When a row has `stage == ACTION_PENDING`, the function returns `False` immediately, without validating later bytes/records to EOF. It also does not apply the complete strict record/control-envelope schema before treating the row as a valid pending event.
- For the no-pending case, a single row is called “valid START” after only checking sequence, selected identity fields, and stage; required/forbidden fields and the exact START envelope are not checked.
- `f.read()` loads the entire WAL without a file-size bound, and `.strip()` silently normalizes record bytes instead of validating exact framing.

As written, malformed trailing data can be classified as a normal late cancellation, and a malformed START or unexpectedly missing journal can support a zero-IPC terminalization claim.

**Required correction:** Define trusted evidence for a never-created/empty WAL versus a missing or unexpectedly empty WAL. Bound the descriptor read before allocation. Validate exact framing and every record schema, identity, sequence, stage transition, and EOF before returning either absence or presence. Do not return early on a pending row; record the finding, continue validation, then classify. Any missing-expected, malformed, torn, gapped, mismatched, over-limit, or ambiguous history must remain locked and `IN_DOUBT_WAL_CORRUPTED`; only a fully validated history may prove zero IPC or a valid pending admission.

## Required R9 deliverable

Update `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`, especially the cancellation UI state machine, lexical scanner, and WAL verifier/crash matrix, to resolve R8-01 through R8-03. Include concise bounded traces and exact fail-closed outcomes for each boundary case. Update `.bridge/report.md` and handoff metadata with the design status and evidence.

**Constraints remain binding:** DESIGN ONLY; zero edits in `src/` and `tests/`; no pytest/app/Resolve/IPC/FusionScript/AI/network execution. Preserve `ready_to_execute=False`, the mock-only tripwire, and G07/M1/M2/M3 OPEN. Keep `G07-07B6-I` deferred until design acceptance; any `G07-07B7-LIVE` requires separate explicit written user authorization.
