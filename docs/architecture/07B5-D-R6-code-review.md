# G07-07B5-D-R6 — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-03
- **Boundary:** Static design/source review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes match:

- `.bridge/report.md`: `77a41c04f8452bdc2cef2baf12f4b920c74091a686e496a34cc94cec268fdef6`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `61879aae9e46938a74aa8afadc6c59dda8d3c3d03559f8662b4baac831076325`

R6 closes several R5 gaps: it adds restart handling after durable revocation, distinguishes `ACTION_PENDING` admission from confirmed IPC delivery, rejects post-decode floats/non-finite values/surrogates, and expands the crash matrix. One blocking concurrency contradiction remains, plus bounded-input and uncertainty details to make the design implementable without freezing the UI or guessing state.

## Blocking residuals

### B5-D-R6-RES-01 — Cancellation cannot acquire the target lock while execution owns it

The cancellation flow requires `TargetLock(LOCK_SH)` followed by `RevocationLock(LOCK_EX)`. The engine flow holds `TargetLock(LOCK_EX)` before admission and retains it through dispatch/finalization; prior design semantics also retain target ownership while `IN_DOUBT`. Therefore the UI cancellation path blocks behind the engine and cannot record a pre-`ACTION_PENDING` cancellation. If the lock call is blocking on the UI thread, the interface can freeze; if it waits for an unresolved trial, it may never return. This contradicts the promised “revoke before linearization => zero IPC” behavior.

Redesign the cancellation arbitration so it can be accepted before the point without waiting for the execution-wide target lock. Specify a nonblocking/cancellable UI path and a lock/state protocol that serializes revoke against the engine's final revocation check and `ACTION_PENDING` fsync. A separate durable cancel-intent record or a revocation-lock-only arbitration may be considered, but must preserve exact target/execution identity and avoid lock inversion. Define behavior while the target is `IN_DOUBT` and prove that a late cancellation returns promptly as too late.

### B5-D-R6-RES-02 — The strict JSON decoder is not bounded by nesting/node complexity or integer range

The parser caps input bytes, but `_validate_decoded_graph()` recursively walks arbitrary nesting with no maximum depth/node count, and accepts arbitrary-size integers. A bounded 1 MiB JSON document can still trigger deep recursion or pathological integer processing. Define finite maximum nesting depth, total nodes, collection lengths, and integer ranges per schema before consumers use the graph. Convert parser recursion/limit failures into controlled fail-closed errors; do not return partially validated data.

### B5-D-R6-RES-03 — Uncertain revocation persistence needs a deterministic “token absent” recovery outcome

For CR-REV-02, the restart rule is explicit when `revocations.json` is corrupt or when the token is present, but not when atomic replace/fsync failed and the readable old file is valid yet does not contain the token. Define that branch using the exact execution/target/WAL identities. Since no `ACTION_PENDING` was durably admitted and the engine must not call the gateway after an unsuccessful pending fsync, the design should state whether it terminalizes the reservation as `ABORTED/EXHAUSTED` or retains it for operator action. Never treat an ambiguous write exception by itself as proof that revoke committed or did not commit.

## Accepted corrections

- `ACTION_PENDING` now represents irrevocable admission, not proof that IPC was delivered; the pending-to-gateway interval is `ADMITTED_PENDING_DISPATCH` and crash recovery is `IN_DOUBT`.
- Revocation restart repair checks for the exact authorization and `ACTION_PENDING` identity before terminalizing.
- The parser rejects duplicate keys, JSON constants/non-finite numbers, floats, NUL, and decoded lone surrogates.
- The crash matrix now includes durable revocation and post-pending uncertainty cases.
- `authorized_record_frame` remains the literal `recordFrame` sent to Resolve, and the API query/append race remains disclosed.

## Required next step

Assign `G07-07B5-D-R7` as DESIGN ONLY to resolve cancellation lock arbitration, bound JSON graph complexity, and define the valid-old-revocation-file/no-token recovery branch. Update the UI cancellation behavior and crash matrix. Keep `G07-07B6-I` deferred until Codex accepts the design. Any `G07-07B7-LIVE` remains a separate future workorder requiring explicit written user authorization.

Preserve `ready_to_execute=False`, the mock-only tripwire, zero Resolve calls, and G07/M1/M2/M3 OPEN. No implementation is authorized by this review.
