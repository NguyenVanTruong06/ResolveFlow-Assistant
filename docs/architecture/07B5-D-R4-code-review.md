# G07-07B5-D-R4 — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Static design/source review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes match:

- `.bridge/report.md`: `c1243ee6fdbf12f1e5db325bb725a16e8b1736f37245ce40e6b241c838524c81`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `1799810f4fc1cfca0832c737ef13e8ca1889a5bfd5e15366eddf5811e27e927f`

R4 corrects the normal success ordering (durable terminal receipt before `EXHAUSTED`), adds a restart repair for that boundary, binds approval digest and Resolve start frame, and replaces the physical-zeroing claim with an explicit DAC limit. It also numbers the eight preconditions correctly. The remaining protocol issues below still block implementation approval.

## Blocking residuals

### B5-D-R4-RES-01 — Revocation ledger can say revoked after the declared irreversible point

The engine holds `RevocationLock(LOCK_SH)` only while checking revocation and fsyncing `ACTION_PENDING`, then releases it before `gateway.execute_action()`. The cancellation flow independently appends the authorization ID under `RevocationLock(LOCK_EX)` and does not check whether the action has crossed the declared point. Therefore a cancel can be durably recorded after `ACTION_PENDING` but before IPC; the design then says the action is too late to cancel and still dispatches it, while the durable record says revoked.

Choose and specify one truthful linearization contract. If fsync of `ACTION_PENDING` makes authorization irrevocable, the cancellation path must detect that state and reject/report “too late” instead of recording a revocation. If revocation must remain effective until the IPC call is admitted, serialize cancellation against that admission without a gap and define process-crash/timeout outcomes. Do not claim an action was already dispatched merely because `ACTION_PENDING` is durable. Define global lock acquisition for both flows and avoid deadlock.

### B5-D-R4-RES-02 — Crash matrix covers nominal success, not failure/cancel/revocation finalization

The 10-row matrix follows the successful path. The design also has multi-write pre-dispatch endings (`receipt ABORTED` then ledger `EXHAUSTED`) for revocation, START-WAL failure, and other admission failures. A crash between those writes can leave a terminal `ABORTED` receipt with `RESERVED`/`ACTIVE` ledger, but no restart repair is specified. Likewise, fsync/replace errors can have an uncertain durable outcome, which is not the same as a clean crash between completed writes.

Extend the matrix to every durable boundary on success, pre-dispatch denial/cancel, persistence failure, and post-dispatch failure. Specify idempotent recovery predicates that verify exact target, execution, receipt, WAL and authorization identities before repairing. Unknown or contradictory combinations must remain blocked for operator reconciliation; recovery must never infer “not dispatched” solely from a failed write call.

### B5-D-R4-RES-03 — Signed physical frame is not explicitly the value sent to Resolve

The authorization binds `authorized_record_frame`, and preflight checks the observed `GetStartFrame()`. The adapter description still computes physical insertion as `timeline.GetStartFrame() + action.timeline_frame_start`. It does not state that the exact signed `authorized_record_frame` is the `recordFrame` argument actually sent to `AppendToTimeline`, nor what happens if Resolve state changes between the check and dispatch. Bind the actual IPC payload to the authorization value and revalidate target UID/start frame at the last safe boundary; if the API cannot make query-plus-append atomic, document the remaining race and require a no-interference operator protocol or fail closed on detectable drift.

### B5-D-R4-RES-04 — Revocation persistence and canonical encoder are underspecified

The revocation writer is described as mutating JSON and calling `fsync()` while locked, but lacks a durable atomic replace protocol (temp file, file fsync, replace, parent-directory fsync) and crash/corruption recovery. A torn or truncated revocation file must never be interpreted as an empty revocation set. Define bounded strict parsing, exact schema/type validation, duplicate-key rejection, atomic replacement, and fail-closed behavior.

The canonical encoder prose requires strict UTF-8/no surrogates and forbids non-finite values, but the shown `json.dumps(...).encode("utf-8")` relies on defaults (`ensure_ascii=True`, `allow_nan=True`). Define the actual shared encoder/validator (including `ensure_ascii=False`, `allow_nan=False`, strict primitive checks before serialization, and strict UTF-8 encoding) and use it consistently for the gesture, approval, proposal, plan, action, and authorization digests.

## Non-blocking recorded limit

- R4 properly acknowledges that local DAC is the protection boundary and that deleting a key from JSON does not guarantee physical erasure. Keep this local-account threat model explicit; `0600` protects against other OS users, not a malicious process running as the same user.

## Required next step

Assign `G07-07B5-D-R5` as DESIGN ONLY. Update the revocation race contract, complete success and failure crash matrices, bind the exact signed frame to the actual dispatch payload, and specify crash-safe revocation/canonical serialization. Keep `G07-07B6-I` deferred until Codex accepts the design. Any `G07-07B7-LIVE` remains a separate future workorder requiring explicit written user authorization.

Preserve `ready_to_execute=False`, the mock-only tripwire, zero Resolve calls, and G07/M1/M2/M3 OPEN. No implementation is authorized by this review.
