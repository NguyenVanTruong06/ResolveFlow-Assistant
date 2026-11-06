# G07-07B5-D-R3 — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Static design/source review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes match:

- `.bridge/report.md`: `74a15fd35a36029d5a47c4f6a0b4c427792fc29dfe8655369cb991c47ff8b316`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `7f01d9efe21a8c9e20c35544149003b1b98406255097dd8206365823ec63400d`

R3 addresses the R2 feedback materially. It labels `operator_id` as an unverified OS-session value, records the human-process freshness limitation, retains old keys for recovery, expands authorization bindings, and adds fail-closed ledger/revocation handling. The following protocol gaps remain blocking before implementation approval.

## Blocking residuals

### B5-D-R3-RES-01 — Terminal ledger transition precedes receipt terminal persistence

The 14-step success ordering writes WAL `FINISH`, changes the target ledger to `EXHAUSTED` at step 12, and only then persists receipt `COMPLETED` at step 13. A crash or receipt write failure between these steps leaves an `EXHAUSTED` target with a nonterminal `EXECUTING` receipt. Since `EXHAUSTED` is an irreversible sink and only `ACTIVE` is described as eligible for reconciliation, restart recovery has no legal path to finish or repair this state. The pre-dispatch failure path can similarly have a receipt/ledger split if either durable write succeeds alone.

Define crash-recoverable commit ordering or an explicit intermediate ledger state (for example, `FINALIZING`) with a deterministic restart repair rule. Every boundary between WAL, receipt, ledger and lock release needs a crash matrix. Never make the target permanently exhausted before the terminal receipt is durable and verifiable, unless `EXHAUSTED` explicitly permits a narrow idempotent finalization path that cannot dispatch or reopen a trial.

### B5-D-R3-RES-02 — Revocation is not serialized through the actual dispatch boundary

The revocation rule says a token revoked before ledger step 6 is caught, but the sequence moves `ACTIVE` at step 6, writes `ACTION_PENDING` at step 7, and dispatches at step 8. A revocation can therefore be committed after the check/step 6 and before dispatch while the action still proceeds. Specify the linearization point for cancellation/revocation, lock ordering for target and revocation locks, and a final revocation check that is atomic with admission to the irreversible dispatch boundary. Define outcomes for revocation racing with a pending WAL record or a dispatch already in flight; do not promise cancellation after dispatch.

### B5-D-R3-RES-03 — Authorization omits some values that determine the physical mutation

The authorization binds relative timeline frame geometry and FPS, but the physical `recordFrame` is computed as `timeline.GetStartFrame() + action.timeline_frame_start`. The shown authorization does not include the observed `GetStartFrame()` value, so that value can change after authorization while every listed digest remains valid. Also, `approval_record_id` is bound without a digest of the exact `AppOwnedApprovalRecord` fields (including its accepted omission IDs and `ENTIRE_PROPOSAL` scope). Bind and recompute the exact approval-record digest and every Resolve-derived value used to form the actual IPC payload, including timeline start frame and resulting physical record frame. If these values are guaranteed immutable by an API contract, cite and enforce that contract; otherwise reject drift.

### B5-D-R3-RES-04 — “Zeroed & purged” key deletion is not a reliable storage guarantee

The design promises that retired key material will be overwritten with zeroes and purged. Rewriting a JSON file cannot guarantee erasure on SSD wear-leveling, copy-on-write filesystems, snapshots, backups, or prior atomic-replace blocks; Python string/bytes copies also cannot reliably be zeroed. Replace this claim with a defensible key lifecycle: avoid claiming secure erasure, define a platform key store or envelope-encryption/key-destruction approach if cryptographic erasure is required, and specify residual local-account/backup exposure. Ensure the keyring file and parent directory are owner-only and opened without following symlinks; a file mode alone does not establish that boundary.

## Non-blocking consistency items

- The hard-preconditions table numbers two rows `4`; renumber the subsequent rows.
- The canonical JSON definition should explicitly reject non-finite values and non-strict primitive types, and the plan/proposal/approval digest algorithms should define exact schema/version and canonical bytes. The auth DTO currently has only primitive fields, but the recomputed upstream digest path must be equally deterministic.
- The pre-creation challenge plus human attestation is an acknowledged process control, not proof that Resolve created a new object. Keep the residual human-process risk visible in all acceptance claims; do not describe the target as mechanically proven never-used.

## Required next step

Assign `G07-07B5-D-R4` as DESIGN ONLY to close the crash-finalization, revocation race, exact approval/physical-frame binding, and key-custody claims. Update the crash-boundary matrix and acceptance cases. Keep `G07-07B6-I` deferred until Codex accepts the design. Any `G07-07B7-LIVE` remains a separate future workorder requiring explicit written user authorization.

Preserve `ready_to_execute=False`, the mock-only tripwire, zero Resolve calls, and G07/M1/M2/M3 OPEN. No implementation is authorized by this review.
