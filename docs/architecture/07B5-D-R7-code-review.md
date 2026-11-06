# Codex review — G07-07B5-D-R7

**Decision: CHANGES_REQUIRED**  
**Review mode:** static document review only. No production/test files were changed; no tests, app, Resolve, IPC, FusionScript, AI, or network were run.

## Evidence checked

The submitted SHA-256 values match the files on disk:

- `.bridge/report.md`: `2b4c7ec580c24a126399d6b30122ef107eb97938afbca1a0cb016fb2dfd8e0f1`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `4000555e782846ddd3fdec1ce6fd1edf5283329f3aa21d3101c76ea32e9730df`

R7 materially improves the design: cancellation no longer waits for `TargetLock`; engine admission and cancellation share a `RevocationLock`; the decoder defines finite graph limits; and CR-REV-02c now distinguishes token absence from corrupt evidence. Exact `authorized_record_frame` dispatch and the documented Resolve query/append race are retained.

## Blocking findings

### R7-01 — Cancellation races with engine-owned receipt/ledger finalization

In §2.5, Case B writes the durable revocation under `RevocationLock`, releases that lock, and then asks the cancellation worker to update the receipt to `ABORTED` and target ledger to `EXHAUSTED` on a best-effort basis. The engine also owns those same records and, after acquiring `RevocationLock`, can observe the token and perform the same terminal transitions. Because the cancellation worker deliberately does not hold `TargetLock`, the post-lock writes can race with engine transitions or restart repair. “Best effort” does not define which writer wins, how conflicting state transitions are prevented, or what the UI may claim if terminal persistence fails.

**Required correction:** Make durable revocation the cancellation worker’s only state-changing responsibility while holding `RevocationLock`; define one authoritative owner for receipt/ledger terminalization (preferably the engine/restart-repair path under `TargetLock`). Specify the user-visible result as “revocation recorded / cancellation requested” until terminal state is durably verified. If another ownership model is chosen, define its serialization and crash recovery without making the UI wait for `TargetLock`.

### R7-02 — Parser limits are enforced after `json.loads` constructs the graph

`parse_strict_canonical_json()` calls `json.loads()` first and only then invokes `_validate_decoded_graph()`. Thus `MAX_NESTING_DEPTH`, `MAX_TOTAL_NODES`, and `MAX_CONTAINER_LENGTH` do not constrain parser construction. Deep input can raise `RecursionError` before the custom depth check; a broad shallow document can build a graph far beyond the node/container caps before rejection. The outer 1 MiB byte cap limits total input, but it does not implement the stated early complexity bounds or guarantee parser failures are normalized to the declared controlled errors.

**Required correction:** Define a bounded pre-parse strategy (for example, a lexical structural scan that correctly handles quoted strings/escapes, plus bounded integer-token handling) or a streaming parser that enforces depth/node/container limits before graph construction. Explicitly normalize parser recursion, malformed syntax, and oversized integer-token failures into controlled fail-closed errors. Preserve duplicate-key rejection and guarantee no partial graph is returned.

### R7-03 — “No ACTION_PENDING” must be proven from a complete, exact WAL

CR-REV-02c treats a WAL with no `ACTION_PENDING` as proof of zero IPC and permits `ABORTED`/`EXHAUSTED`. The design says unreadable/corrupt WAL becomes in-doubt, but does not explicitly cover a valid-prefix journal with a torn/incomplete trailing record, sequence gaps, an invalid stage transition, duplicate records, or records whose full execution/authorization/target/plan identity does not match the revocation request. A parser that merely finds no matching pending event in such a journal cannot prove absence.

**Required correction:** Define “no pending” only after strict validation of the entire WAL through EOF: byte framing and torn-tail policy, sequence continuity, stage-machine validity, exact full identity binding, and no conflicting/unknown records. Any incomplete, malformed, mismatched, or ambiguous history must remain locked and classify as `IN_DOUBT`; do not terminalize or claim zero IPC.

## Required R8 deliverable

Revise `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md` and its crash/lock diagrams to resolve R7-01 through R7-03. Add bounded traces for cancellation winning before admission, engine admission winning first, cancellation persistence failure, crash after durable revocation but before terminalization, and each malformed/torn-WAL case. Update `.bridge/report.md` and handoff metadata with evidence and status.

**Constraints remain binding:** DESIGN ONLY; zero edits under `src/` and `tests/`; no pytest/app/Resolve/IPC/FusionScript/AI/network execution; preserve `ready_to_execute=False`, mock-only tripwire, and G07/M1/M2/M3 OPEN. Keep G07-07B6-I deferred until this design is accepted. Any future G07-07B7-LIVE still requires separate explicit written user authorization.
