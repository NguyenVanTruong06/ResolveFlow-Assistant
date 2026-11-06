# G07-07B5-D-R2 — Codex Design Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed:** 2026-11-02
- **Boundary:** Static design/source review only. No tests, application, Resolve, IPC, FusionScript, AI, or network execution.

## Integrity check

Submitted hashes match:

- `.bridge/report.md`: `41ff4f0f2eba242aacf70dd3c8a006f8c556d95b0d384fc164c6b7b17822f7f1`
- `docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md`: `549bb296dfb93f6083e1ac378f026dc6534d6abc1b5a549fc98229f6a9c99ef4`

R2 materially improves the design: it specifies a file-backed target ledger with cross-process locking and durable replacement, acknowledges that Resolve exposes no historical audit trail, and distinguishes an app-minted HMAC token from human consent. These changes address the three R1 review themes at the design level. The remaining items below still block implementation approval because the authorization and freshness claims exceed the evidence described.

## Blocking residuals

### B5-D-R2-RES-01 — Consent is a self-assertion, not authenticated operator identity or proof of freshness

`OperatorConsentGesture.operator_id` is an entered string; a confirmation phrase, nonce, timestamp, and `freshness_attestation` can be constructed by the same local process. Hashing this object and placing the digest in an app HMAC proves only that the app minted a token for those supplied values. It does not independently authenticate the human or prove the claimed project/timeline creation history. R2 does acknowledge the Resolve API limitation, but still describes the human statement as a second provenance layer that closes the historical reuse risk.

Specify the actual trust level and mechanism: e.g. treat the gesture as explicit operator acknowledgment under the current OS session, label identity as unverified unless an OS-authenticated mechanism is added, and never describe the statement as technical proof of freshness. If the product requirement remains “cannot silently reuse an old target,” define a controlled creation flow with a pre-creation app reservation/challenge bound to the later project/timeline UIDs, and state what residual risk remains because Resolve offers no creation-history API. Otherwise narrow the claim and make the residual human-process risk explicit in the acceptance matrix and operator procedure.

### B5-D-R2-RES-02 — Key rotation invalidates restart recovery, while active authorization is required for reconciliation

The design stores a key at `.resolveflow/security/trial_secret.key` but also says to rotate/generate a new key on each restart/session end. It simultaneously permits restart recovery for a matching `RESERVED`/`ACTIVE` ledger entry and requires valid authorization. A restart would make the prior authorization unverifiable unless a prior key is retained or recovery has a separately specified proof path. Define key IDs and a durable keyring/rotation policy, including which keys remain valid for verifying an already-reserved/active authorization and when they may be retired. Define crash behavior during key rotation and fail closed if the required key is unavailable; do not strand an active target in a state that cannot be reconciled.

### B5-D-R2-RES-03 — HMAC payload does not bind the exact approved plan and source evidence

The shown `TrialExecutionAuthorization` and `canonical_bytes_for_hmac()` bind `plan_idempotency_key`, approval record ID, target UIDs/names, and allowed action type, but do not bind the canonical proposal revision/content digest, exact execution-plan digest/action parameters, source-file SHA-256, or timeline FPS/geometry. A key is only a safe substitute for those fields if the design proves it is a collision-resistant digest over the complete canonical values and verifies that relationship at admission. Specify the exact canonical signed envelope and verification equation, including all values that determine the physical mutation, and exact-schema/type rules. The authorization must fail if the approved proposal, plan, source bytes, target, or geometry differs.

### B5-D-R2-RES-04 — Durable ledger and revocation failure semantics remain incomplete

The ledger describes locking and atomic replace, but does not define fail-closed behavior for malformed/unknown-schema records, symlink/path substitution, lock/fsync/replace failure, or partial state transitions (`RESERVED -> ACTIVE -> EXHAUSTED`). The revocation file has no corresponding atomicity, integrity, or restart semantics. Define transition ordering relative to receipt/WAL writes and dispatch; on any uncertain persistence outcome retain target ownership and prohibit new trials, while allowing only the matching reconciliation path. Define ledger/revocation corruption as blocking, never as “no record.”

## Required next step

Assign `G07-07B5-D-R3` as DESIGN ONLY to resolve these four residuals and align the diagrams, lifecycle, recovery path, preconditions, and acceptance matrix. Keep `G07-07B6-I` deferred until the design is accepted. Any `G07-07B7-LIVE` remains a separate future workorder requiring explicit written user authorization.

Preserve `ready_to_execute=False`, the existing mock-only tripwire, zero Resolve calls, and G07/M1/M2/M3 OPEN. No implementation is authorized by this review.
