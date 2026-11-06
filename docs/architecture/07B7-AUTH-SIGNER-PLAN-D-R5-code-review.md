# Static Plan Review — G07-AUTH-SIGNER-PLAN-D Revision R5

**Decision: `CHANGES_REQUIRED`**  
**Scope:** Static review of Revision R5 against the active design-only workorder, R4 review findings, and the frozen 07B5 authorization/action contracts. No tests, prototypes, Resolve, IPC, network, or key contents were accessed.

## Artifact integrity

Submitted hashes match the files on disk at review time:

| Artifact | Submitted SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `93836977e8c550a4bce8d6af848305027b722287c932d055166d11d25fb2b3d8` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `bf14ff62332faf56bacf1d863ba9ebbf3854f04ce3226dd3dd06b9dfafd7d478` | Match |
| Prior R4 review | `3250718c08d31b18c95687a21ddec9ab8a821ad1daa3894ff8c965d4622bbe8d` | Match |

R5 addresses the four R4 findings in part: it adds the consent envelope, changes the socket mode to `0660`, restores a revocation manifest outline, and corrects Gate 6's signer/dispatcher wording. The following gaps remain.

## Findings

### AUTH-SIGNER-R5-01 — BLOCKER: Consent claims are not all checked against the pending mutation, and timestamp freshness accepts future times

The envelope now contains the approval and plan digests, action digest, project/timeline names and UIDs, source path/hash, action/track and geometry (§3.1, lines 179–196). However, the Signer’s purported “every single claim” comparison enumerates only eight checks (§3.2, lines 210–218): it does not compare the target names, `source_file_path`, `action_type`, `track_type`, `track_index`, `operator_id`, `operator_key_id`, `challenge_id`, `client_nonce`, confirmation phrase, or freshness attestation to authoritative pending/challenge data. A signed envelope can therefore contain a human-visible source path or action description that differs from the mutation actually authorized while the listed digest/hash checks still pass. Also, `now - consent_timestamp_iso <= 120s` accepts timestamps in the future and does not bind consent to the challenge’s own issue/expiry interval.

**Required change:** Define exact schema/type gates and compare every security-relevant envelope field to the signer-owned pending request and active challenge, including challenge/client nonce, operator identity/key, names, source path, action, track and geometry. Require `0 <= now - consent_time <= 120s` (with any explicit bounded clock-skew rule) and enforce the challenge’s own expiry before consuming its nonce or issuing authorization. Add negative cases for each omitted-field mismatch, future timestamp, expired challenge and replay.

### AUTH-SIGNER-R5-02 — BLOCKER: Dispatcher is not required to accept only a fully matching successful oracle response

The response DTO includes `verification_result`, `is_valid`, request ID, authorization digest, action digest and both target UIDs (§2.2, lines 137–153). But the Dispatcher procedure only says to verify the oracle signature, compare `request_nonce`, check response expiry, and then proceed (§2.2, line 156). It does not require `is_valid is True`, `verification_result == "VERIFIED_VALID"`, `reason_code == "OK"`, or equality of the response request ID, authorization digest, recomputed action digest, project UID and timeline UID with the outstanding request and current action. The Signer’s evaluation list also checks the requested project UID but omits `req.target_timeline_uid == auth.target_timeline_uid` (§2.2, lines 130–136). Thus the stated contract permits dispatch after a signed negative or differently bound response, and does not enforce the timeline binding at the oracle boundary.

**Required change:** Make the Dispatcher fail closed unless the signed response is successful and every response/request/auth/action field matches exactly, including request ID and both target UIDs. Require bounded UTC response freshness and one-time request nonce handling. The Signer must validate both target UIDs, the complete authorization digest and the recomputed action digest before signing a success response; failure responses must never authorize dispatch.

### AUTH-SIGNER-R5-03 — HIGH: Socket file mode does not establish access through its parent directory

R5 fixes the socket inode to `0660` and assigns its group to `_resolveflow_dispatcher` (§2.2, lines 106–115), but does not specify ownership, mode/ACL, creation protocol or symlink protection for `/run/resolveflow`. UNIX pathname socket access also requires directory traversal permission. If the parent is signer-only (for example `0700`), the dispatcher still cannot reach the socket despite the `0660` socket mode; an unsafe writable parent could also permit path replacement.

**Required change:** Specify a signer-owned runtime directory with narrowly scoped dispatcher traversal (for example a dedicated group/ACL with no untrusted write permission), safe descriptor-anchored creation, socket inode verification and cleanup/lifecycle behavior. State and verify the effective DAC/ACL on every path component, not only the socket inode.

### AUTH-SIGNER-R5-04 — HIGH: Revocation manifest signature and same-epoch rollback protection remain underspecified

The manifest lists revocation fields and an issuer, but the plan does not define the exact signed-byte construction, signature algorithm/key ID and rotation/trust-root procedure, exact-key schema validation, or duplicate-key rejection (§6.1, lines 306–332). The local anchor stores only an epoch (§6.2, lines 334–343). When a newly read manifest has the same epoch as the anchor, the plan accepts it without binding that epoch to a manifest digest; a different still-valid signed manifest at the same epoch could therefore replace the revocation set without triggering the stated `< anchored_epoch` rollback check. Bootstrap/missing-anchor and interrupted-update recovery semantics are also not explicit.

**Required change:** Specify canonical signed bytes and an exact manifest schema, including issuer/key identity and rotation/revocation rules. Atomically anchor both epoch and manifest digest (or require a strictly increasing epoch for any content change), define bootstrap and crash recovery, and fail closed on same-epoch content changes, missing/corrupt anchor, expired or malformed manifests, and untrusted signer keys.

## Conclusion

Revision R5 makes substantive progress, but the consent-verification gap and oracle-response acceptance gap still cross the core authorization boundary. The runtime socket path and revocation state also lack necessary security invariants. The plan is **`CHANGES_REQUIRED`** and is not approved for implementation or a new live run.

Gate 6 remains `NOT ACCEPTED` and incident-only. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review authorizes no implementation, key provisioning/access, Resolve operation, dispatch, retry, undo or cleanup. No `src/` or `tests/` files were changed.
