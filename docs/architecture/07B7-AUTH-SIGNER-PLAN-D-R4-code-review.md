# Static Plan Review — G07-AUTH-SIGNER-PLAN-D Revision R4

**Decision: `CHANGES_REQUIRED`**  
**Scope:** Static review of the R4 plan, submission metadata, R3 review findings, and relevant frozen 07B5 contracts. No tests, prototypes, Resolve, IPC, network, or key contents were accessed.

## Artifact integrity

Submitted hashes match the files on disk:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `c7a02a26ed82d7cfb3f5b60612ab14c5f6a13c8be51b0fc29722bde04d272e44` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `6e25b0c97a71841e95522e2bcac8fb33a661cc9720ffeb1d867f81d5c9917779` | Match |

## R3 finding disposition

R4 corrects the incident UIDs, introduces distinct service UIDs, replaces client-supplied TTY descriptors with signed operator consent, adds a versioned approval envelope, and improves nonce-ledger coordination. The following issues remain in the new protocol and lifecycle definition.

## Findings

### AUTH-SIGNER-R4-01 — BLOCKER: Operator signature does not bind the approved plan and complete action

The operator prompt displays the source path/hash, geometry, canonical action-payload digest, and target names/UIDs (§3.2, lines 133–139). But the actual `gesture_payload` signed by `OperatorEd25519` contains only the challenge/client nonce, phrase, timestamp, freshness string, operator ID, and project/timeline UIDs (§3.2, lines 142–154). It omits the approval/envelope digest, plan digest, action-payload digest, source identity, action type, track, and frame geometry. A valid consent signature can therefore be reused or paired with changed action claims while still verifying against the signed gesture fields. The plan also does not define a versioned signed-consent envelope or state that `operator_gesture_digest_sha256` is the digest of the exact bytes the operator signed; the frozen `OperatorConsentGesture` DTO itself has no target/action fields.

**Required change:** Define a canonical signed operator-consent envelope that includes and is checked against the exact `SignedApprovalEnvelope` digest, plan digest, 16-key action-payload digest, execution/challenge nonce, source path/content digest, project/timeline UIDs and names, action, track, and geometry shown to the operator. The signer must compare every field to its pending request before consuming the challenge and issuing HMAC authorization. Set the 32-claim `operator_gesture_digest_sha256` to the digest of those exact signed envelope bytes, without changing the 07B5 claim schema.

### AUTH-SIGNER-R4-02 — BLOCKER: Dispatcher cannot connect to the verification socket as specified

The signer and dispatcher have distinct UIDs, but the internal socket is specified as mode `0600`, owned by `_resolveflow_signer:_resolveflow_dispatcher` (§2.2, line 110). POSIX `0600` grants access only to the owner; the dispatcher group receives no group permission, and the dispatcher UID is not the owner. Thus the dispatcher's required HMAC verification RPC is denied by the stated ACL. In addition, the “boolean verification receipt signed with an internal ephemeral session token” (§2.2, line 115) has no token/key agreement, request binding, replay check, or freshness format, so it is not a defined trusted response contract.

**Required change:** Specify a socket ACL that grants only the dispatcher service identity access (for example owner/group with appropriate `0660` bits or a precise ACL), while excluding the runner. Define peer-credential checks, framed/versioned request schema, and a verification response cryptographically or channel-authenticated and bound to a fresh dispatcher request nonce, hash of the full authorization, recomputed action-payload digest, target UIDs, and short expiry. Dispatcher must derive the 16-key digest from the actual plan/action itself and reject any mismatch before dispatch.

### AUTH-SIGNER-R4-03 — HIGH: Revocation design regressed to an undefined anchor

R3 specified a signed revocation manifest, issuer key, expiry, monotonic epoch comparison, descriptor-anchored fresh reads, and durable anchor update. R4 now mentions only `revocation_epoch.anchor` in the diagram and says the signer RPC “checks revocation” (§2.2, lines 108–116); there is no manifest schema, signing authority, expiry rule, atomic anchor update/read protocol, rollback handling, or operator/Director key revocation and rotation policy. The R4 allowlist similarly says “descriptor-anchored revocation checks” without defining them (§10, lines 380–406). A local anchor alone does not say which keys/authorizations are revoked or how the current signed state is obtained.

**Required change:** Restore a complete signed revocation contract: exact schema and canonical signed bytes, trusted revocation authority/key lifecycle, explicit expiry, descriptor-relative no-follow fresh reads, durable monotonic anchor update ordering, restart/rollback behavior, and fail-closed handling. Include revocation of trial, Director, and Operator keys and authorization IDs, plus tests for stale, expired, rollback, missing, malformed, and rotated-key cases.

### AUTH-SIGNER-R4-04 — HIGH: Gate 6 sequence contradicts the new operator-signature and dispatcher design

The R4 consent protocol explicitly rejects TTY/PTY input and requires an `OperatorEd25519` signature (§3), and the dispatcher is the only process that owns Resolve IPC (§2). But the future gate flow still says Gate 6 “Signer verifies, captures `/dev/tty` consent, executes single action” (§9, line 369). This reintroduces the rejected TTY mechanism and assigns the live dispatch to the signer, which must have zero Resolve access.

**Required change:** Make the Gate 6 sequence consistently say that the Signer verifies signed approval and signed operator consent, returns the HMAC authorization, and the separate Dispatcher verifies through the defined oracle and performs the sole dispatch. Remove all `/dev/tty` consent and signer-executes-dispatch wording from R4.

## Review conclusion

R4 resolves several important R3 findings, but the operator signature does not cryptographically cover the full mutation the human reviews, the verification RPC is inaccessible under its stated socket permissions and lacks a response contract, and the revocation specification has regressed. The gate sequence also contradicts the new architecture. The plan is therefore **`CHANGES_REQUIRED`** and is not ready for signer/dispatcher implementation approval or a new live run.

Gate 6 remains `NOT ACCEPTED` and incident-only. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review authorizes no implementation, key provisioning/access, Resolve operation, dispatch, retry, undo, or cleanup. No source or test files were changed.
