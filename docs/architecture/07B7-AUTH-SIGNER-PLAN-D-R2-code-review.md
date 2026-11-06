# Static Plan Review — G07-AUTH-SIGNER-PLAN-D Revision R2

**Decision: `CHANGES_REQUIRED`**  
**Scope:** Static review of the R2 plan, submission report, current authorization contract/verifier, and R1 findings. No tests, prototypes, Resolve, IPC, network, or key contents were accessed.

## Artifact integrity

Submitted hashes match the files on disk:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `b515e49a3598e2db437affdb4f4d72cbe8ff423d1d8fa199e3a12a96778b8278` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `736549b9e746fc2a437794ccba43c72b647c0c785d127748ce831bc9acba945c` | Match |

## Disposition of R1 findings

R2 corrects the Gate 6 incident wording, rejects caller-provided approval and gesture DTOs in its proposed flow, and adds signer-side nonce and signed-revocation concepts. Those are useful design changes. The findings below remain because the selected mechanisms do not yet meet the stated trust boundaries or the frozen 07B5 contract.

## Findings

### AUTH-SIGNER-R2-01 — BLOCKER: Ed25519 encoding is incompatible with the frozen HMAC contract and current verifier

The plan makes Ed25519 the primary model and stores its 128-hex-character signature in `TrialExecutionAuthorization.authorization_hmac` (§2.2, lines 98–102). The accepted DTO and serializer define that field as the separate HMAC signature member, and the current coordinator verifier retrieves a symmetric key and recomputes HMAC-SHA256 (`trial_authorization_contracts.py`, lines 745–800 and 869–875; `durable_trial_target_ledger.py`, lines 1376–1408). The plan's “same name and type” argument does not make the algorithms interchangeable. The proposed implementation allowlist also omits changes to the DTO/verifier or a new verification protocol. Tier 2 then reintroduces HMAC without specifying a compatible signer-to-dispatcher key arrangement, leaving two conflicting architectures.

**Required change:** Select one contract-compatible design. Either keep the accepted HMAC contract and place all HMAC verification and dispatch decisions in a protected dispatcher boundary inaccessible to the runner, or submit a separate, explicit 07B5 contract-change proposal that updates the signature field semantics, canonical verification API, adapter, tests, migration, and acceptance gates. Do not label an Ed25519 signature as an HMAC or claim the current adapter can verify it.

### AUTH-SIGNER-R2-02 — BLOCKER: Same-UID file and Resolve access defeat the claimed process isolation

The signer runs under the operator's active login and stores its private key under that user's home with owner-only permissions (§2.3, lines 113–115). A compromised process running as the same UID can open files owned by that UID; `0700/0600` do not isolate one same-UID process from another. `PR_SET_DUMPABLE=0` limits ptrace, but does not prevent ordinary reads of an owner-readable key file (§2.4, lines 144–148). Separately, the plan acknowledges that the runner may load `fusionscript` when Resolve's socket is open and defers protection to a future “strict runtime sandbox / container” (§2.4, line 146), without specifying an enforceable sandbox or including a dispatcher implementation in the file allowlist (§9, lines 385–398).

**Required change:** Define the enforceable OS boundary before asserting zero key/Resolve access: for example, a distinct service UID with inaccessible private-key storage and a dispatcher-owned Resolve connection, or a non-exportable hardware signing key plus a dispatcher boundary that prevents runner-side direct IPC. Specify sandbox policy, socket access, service startup, and the minimal implementation/test allowlist. If the host cannot enforce this boundary, mark that environment unsupported and fail closed.

### AUTH-SIGNER-R2-03 — HIGH: Director approval issuer authenticity remains optional

The signer queries `IApprovalStore` and checks `director_session_id` (§3.1, lines 154–169), but the Director signature is explicitly optional when the UI is not out-of-process. The design does not specify authenticated store access, who may create or alter records, or how a `director_session_id` is validated. The current `AppOwnedApprovalRecord` is a frozen data object and `verify_proposal_approval_binding()` verifies consistency/revision, not the identity of the human approver or the authority that created the record (`edit_plan_approval_contracts.py`, lines 144–190 and 246–293). A record's presence and valid content digest alone do not prove approval.

**Required change:** Make the approval issuer verifiable in every deployment: require a Director signature over the exact approval ID, proposal revision/content digest, scope, and approval timestamp, or specify an equivalently authenticated, access-controlled approval store with a trusted writer identity and immutable/auditable records. Remove the optional-authentication branch from the security decision.

### AUTH-SIGNER-R2-04 — HIGH: Revocation rollback and atomic replacement are not safely detected

The anti-rollback anchor `last_observed_epoch` is kept only in process memory (§6.2, lines 313–320). A restarted verifier therefore has no trusted prior epoch against which to reject an older but correctly signed manifest. In addition, the design says to inspect an already-open file descriptor for `st_mtime_ns` changes while also publishing updates with atomic `os.replace` (§6.2, lines 316–324). After replacement, the old descriptor still refers to the old inode and its metadata need not change, so the verifier may continue accepting a revoked key. The schema also contains `issued_at_iso` but no expiry/max-age field despite requiring rejection of an “expired” manifest.

**Required change:** Anchor the highest accepted epoch durably outside the replaceable manifest and define rollback-safe recovery. On each decision, reopen the manifest relative to a trusted directory descriptor (or otherwise prove the descriptor refers to the current published inode), verify the complete signature and schema, and enforce an explicit freshness/expiry policy. Failure to establish current state must stop signing and dispatch.

### AUTH-SIGNER-R2-05 — HIGH: The nonce ledger is not fully specified for torn writes and creation durability

The plan calls a JSONL append under `flock` plus `fsync(file)` an atomic durable consume (§4.2, lines 221 onward), but does not specify short-write/EINTR handling, mandatory newline framing, torn-tail recovery, bounded parsing, descriptor anchoring/no-follow checks, or directory `fsync` when the ledger is first created. A crash during append can leave a partial record; without a specified fail-closed parser and repair rule, restart may miss a consumed nonce or strand the signer ambiguously.

**Required change:** Specify the ledger's exact file lifecycle and byte framing, safe descriptor open/ownership/mode checks, write-all and fsync ordering (including parent directory on creation), strict bounded restart scan, torn-tail behavior, and a cross-process lock lifecycle. Preserve fail-closed semantics when a write outcome is ambiguous; never truncate or silently skip malformed entries.

### AUTH-SIGNER-R2-06 — HIGH: Example prompt reuses the incident target and confuses target names with UIDs

The supposedly operator-facing prompt hardcodes the previous incident project/timeline names and frame (`RF_TRIAL_9A56C835B97208ED`, `RF_TRIAL_TL_9A56C835B97208ED`, frame 86400; §2.3, lines 119–132), despite §8 requiring an entirely fresh target and prohibiting reuse. The nonce-ledger example repeats those values in fields named `target_project_uid` and `target_timeline_uid` (§4.1, lines 203–215), although these strings are the prior project/timeline names, not the actual UIDs. Since the prompt is the human's last review point, a stale literal can direct approval toward the incident target or display a false identity.

**Required change:** Replace every incident-specific example value with clearly non-runnable placeholders. Show separate `project_name`/`timeline_name` and actual Resolve UID fields, and require the signer to retrieve and display the current fresh target identity from the trusted preflight/Resolve boundary immediately before consent. Add an explicit invariant rejecting any prior incident project/timeline UID or name.

## Review conclusion

R2 substantially improves the design and closes the inaccurate incident summary. It is still **`CHANGES_REQUIRED`**: the primary signature algorithm does not match the accepted verifier, the same-user key/Resolve boundary is not enforced, and revocation and nonce durability need precise restart-safe rules. The Gate 6 incident remains **`NOT ACCEPTED`** and incident-only.

`ready_to_execute=False` remains in force. Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review authorizes no implementation, key provisioning/access, Resolve operation, dispatch, retry, undo, or cleanup. No source or test files were changed.
