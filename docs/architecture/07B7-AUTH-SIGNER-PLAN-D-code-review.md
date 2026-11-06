# Static Plan Review — G07-AUTH-SIGNER-PLAN-D

**Decision: `CHANGES_REQUIRED`**  
**Review scope:** Static review of the independent authorization signing workflow plan, its submission metadata, the accepted 07B5 contract surfaces, and the Gate 6/7 incident reviews. No tests, prototypes, Resolve operations, IPC, network, or key contents were accessed.

## Artifact integrity

The submitted hashes match the files present at review time:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `e114adcc40f7ee9200f5ead741546d02fc68f5ae5a2a0cb4b4529b08701994e7` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `c759ee28f5e883bc18214464a44c8c208144d6a0fcabdbfd464027dfab7166f6` | Match |

## Findings

### AUTH-SIGNER-01 — BLOCKER: HMAC verifier key remains available to the runner trust domain

The plan preserves symmetric HMAC-SHA256 (§4.3) while directing the adapter to verify with a “read-only keyring” (§5.1, steps 5 and 6). Read-only access is still signing authority: any process that can obtain the HMAC key can compute a valid authorization HMAC. The existing implementation exposes the coordinator's keyring (`durable_trial_target_ledger.py`, `keyring` property) and `bind_signed_authorization()` obtains raw key bytes through `get_key()` before comparing the HMAC. The proposed diagram places the runner and adapter in the same principal (§2.1), so this design does not establish that runner-controlled code cannot reach the verifier key.

**Required change:** Choose and specify a concrete deployment boundary that preserves the accepted contract. If HMAC remains, put both key use and the verification/dispatch decision behind a trusted process boundary that the runner cannot inspect, replace, or bypass; define which process owns Resolve IPC and ensure the runner cannot call Resolve directly. Alternatively, obtain an explicitly approved contract change to an asymmetric signature model. Do not claim that a read-only symmetric key or a separate Python process under the same user makes self-minting impossible.

### AUTH-SIGNER-02 — BLOCKER: Approval and consent DTOs do not prove their claimed provenance

The sequence sends `approval_record` and `gesture` from the runner to the signer (§5.1, steps 3–4). The listed `verify_proposal_approval_binding()` verifies structural/content consistency, but the current `AppOwnedApprovalRecord` is a plain frozen DTO without an issuer signature or trusted-store lookup (`edit_plan_approval_contracts.py`, lines 144–190 and 246–293). Its factory accepts caller-provided `approval_id` and `approved_at_iso` (lines 197–225). Similarly, `OperatorConsentGesture.compute_gesture_digest()` is a hash of caller-provided fields, not an authenticated operator signature or trusted capture (`trial_authorization_contracts.py`, lines 656–684). TTL, a fixed phrase, and a nonce do not establish who supplied those values.

**Required change:** The signer must obtain approval from an authoritative Director-owned store or verify an approval-authority signature over the approval and exact proposal revision. It must capture consent through a signer-controlled, authenticated operator channel; issue the challenge itself, bind the exact run/target/action to the prompt, and consume the challenge once. Do not treat runner-supplied DTO instances or their digests as provenance.

### AUTH-SIGNER-03 — BLOCKER: The selected process/IPC model does not enforce caller authorization

Section 2.3 leaves two materially different deployments as alternatives (“AF_UNIX daemon” or CLI) and describes the same-user socket as a low-risk boundary (§2.2–2.3). `SO_PEERCRED` establishes peer UID/GID/PID, but a socket mode of `0600` owned by the same user does not distinguish a trusted operator from runner-controlled code running as that user. The protocol also lets the runner submit all signing inputs. This permits an unauthorized caller to invoke the signer even if it cannot read the key file.

**Required change:** Select one deployment and define its OS identity, socket ownership/ACL, caller authentication and authorization, service startup/ownership, request framing/versioning, and shutdown behavior. Bind requests to an independently authenticated approval and operator action rather than trusting same-UID peer identity. State the threat model explicitly, including what same-user malware or a compromised runner can still do.

### AUTH-SIGNER-04 — HIGH: Single-use authorization/challenge consumption is not durable at the signer

The plan calls `execution_id` and `token_nonce` single-use (§4.1, §7), but its only concrete replay gate is the target ledger's post-action state (`NONE` to `EXHAUSTED`/`LOCKED`, §7). It does not specify atomic, durable consumption of the signer challenge, client nonce, approval revision, or signing request before an authorization is returned. Repeated or concurrent requests can therefore produce multiple valid authorizations from the same consent event; target-side blocking after dispatch does not prevent issuance or pre-dispatch replay.

**Required change:** Specify a signer-owned, durable consume-once record keyed to the challenge/consent and bound to run ID, approval digest/revision, target UIDs, action digest, and execution nonce. Define atomic publication/fsync, concurrent request behavior, crash recovery, and whether a retry returns the exact prior artifact or fails closed. Ensure this state is independent of the target ledger and cannot be reset by the runner.

### AUTH-SIGNER-05 — HIGH: Revocation authority and freshness are underspecified

The plan calls `revocations.json` authoritative and says signer and adapter check it (§6.2), but does not define its authenticated writer, signature/MAC, ownership and descriptor checks, atomic update protocol, version/epoch monotonicity, reload/cache rules, or behavior when signer and adapter observe different versions. A mutable JSON file is not an authority boundary, and an owner-writable file does not protect against code running under that same owner.

**Required change:** Define who may revoke, how the revocation state is authenticated and durably updated, how each trusted verifier obtains a fresh consistent version, and how failures or rollback/stale state fail closed. Keep raw key bytes out of all evidence and logs.

### AUTH-SIGNER-06 — HIGH: Incident wording still implies §2.6 compliance

The executive summary says the prior action's physical readback and durable tuple transitions were “verified 100% compliant with 07B5 §2.6” (§1.1, line 17). The cited Gate 6 review states the run was not accepted because authorization was self-minted and the cleanup boundary was violated; Gate 7 R1 accepts only the incident record. The later sentences correctly say Gate 6 is `NOT ACCEPTED`, but the absolute compliance wording conflicts with that disposition and can be read as partial Gate 6 acceptance.

**Required change:** Describe only the observed physical insertion/readback and matching durable artifacts, then state that the run did not satisfy the complete 07B5/Gate 6 acceptance contract because its authority and cleanup controls failed. Preserve the incident-only classification and do not imply that this plan retroactively cures it.

## Review conclusion

The plan correctly preserves the previous run as incident-only and keeps the new work design-only. It also identifies the key trust-boundary failure and proposes useful provenance checks. However, its present architecture still allows symmetric-key verification to collapse into signing capability, accepts provenance DTOs whose current contracts are forgeable by construction, and does not select/enforce a process boundary or durable signer-side one-time consumption. These are core acceptance criteria, so the plan is **`CHANGES_REQUIRED`** and is not ready to authorize signer implementation or another live run.

`ready_to_execute=False` remains in force. Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED`; the prior Gate 7 record remains accepted for incident-record purposes only. This review authorizes no implementation, Resolve operation, dispatch, retry, undo, key access, or cleanup.
