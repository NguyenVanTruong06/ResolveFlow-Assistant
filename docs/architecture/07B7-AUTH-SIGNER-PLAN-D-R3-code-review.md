# Static Plan Review — G07-AUTH-SIGNER-PLAN-D Revision R3

**Decision: `CHANGES_REQUIRED`**  
**Scope:** Static review of the R3 plan and submission metadata, the R2 review findings, and relevant current approval/authorization contracts and incident signoff. No tests, prototypes, Resolve, IPC, network, or key contents were accessed.

## Artifact integrity

Submitted hashes match the files present at review time:

| Artifact | SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `7b3abf634299e87a86906d733034e9847f7a0d7216322b512418b68dce9dcd4f` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `8a9439f7e0720d2b70160392485647c9bd8a1b905ac069e5a2a9c08bb03a66be` | Match |

## R2 finding disposition

R3 restores the HMAC algorithm, adds a Director signature requirement, adds a durable epoch anchor and torn-tail handling, and separates target names from UID fields. The following gaps mean the trust-boundary and recovery requirements are not yet met.

## Findings

### AUTH-SIGNER-R3-01 — BLOCKER: Signer and dispatcher share an OS identity, so capability separation is not enforced

The plan puts both `TrialAuthorizationSignerService` and `TrustedCanaryDispatchService` under the same `_resolveflow_svc` UID (§2.3 and §10). The signer must possess the HMAC secret to issue authorizations, while the dispatcher must verify with that same symmetric secret (§2.2). A process under that UID can access the same owner-only key files and, absent a separate OS-enforced restriction, the same Resolve IPC endpoint. Thus the signer has the OS authority to dispatch and the dispatcher has the authority to sign; the claimed “signer cannot dispatch” boundary is only a code convention. The runner sandbox does not isolate the signer from the dispatcher.

**Required change:** Give signer and dispatcher distinct service identities and permissions. Define the narrow key-sharing mechanism required by HMAC without granting signer access to the Resolve endpoint or dispatcher access to a signing API; alternatively, place signing and dispatch behind one trusted broker and revise the threat model. Specify socket ownership/ACLs and prove the dispatcher is the only principal able to open the Resolve connection. If the Resolve integration cannot operate under a restricted dispatcher identity, mark that deployment unsupported rather than claiming the boundary is enforced.

### AUTH-SIGNER-R3-02 — BLOCKER: Passing a TTY descriptor from the client does not authenticate human consent

The signer socket accepts clients in `resolveflow-clients`, and the CLI passes `/dev/tty` or a TTY session token to the service (§2.3, lines 120–132; §3.2). `SO_PEERCRED` authenticates the connecting process's UID/GID, not the origin or human control of a passed file descriptor. A client in that group can create a pseudo-terminal it controls, pass its descriptor, and programmatically send the fixed confirmation phrase. The service then hashes that phrase as an operator gesture. This leaves the stated physical-human consent gate bypassable by a runner that can reach the signer socket.

**Required change:** Do not treat a client-supplied terminal descriptor or session token as proof of physical operator input. Specify a signer-owned trusted interaction channel that the untrusted runner cannot create, replace, or automate, and bind the confirmation to the exact fresh target, action, approval, and challenge. Otherwise downgrade the evidence claim and require a separate, independently authenticated operator authorization before signing.

### AUTH-SIGNER-R3-03 — HIGH: Mandatory Director signature cannot be represented by the current approval contract or planned file scope

The R3 signing payload requires `director_signature`, `plan_digest_sha256`, and `proposal_content_digest_sha256` (§3.1, lines 138–164). The current `AppOwnedApprovalRecord` contract has exactly six fields—approval ID, proposal ID, proposal revision, approval timestamp, omissions, and scope—and has no signature or those digest fields (`src/core/contracts/edit_plan_approval_contracts.py`, lines 144–160). Yet the implementation proposal only lists new signer, dispatcher, CLI, and their tests (§10, lines 465–484); it does not include the approval DTO/serializer, Director approval writer, authoritative store schema, or trusted-key provisioning needed to create and persist the signed record. As written, the signer cannot retrieve the record shape it requires without an unplanned contract change.

**Required change:** Specify the signed approval envelope and its canonical serializer as a deliberate contract change or a separate versioned envelope around the existing DTO. Include the Director-side signing producer, authoritative store/read API and migration, trusted Director public-key provisioning/rotation, and their tests in the future dependency plan and allowlist. Keep the authorization's accepted 32-claim/HMAC contract unchanged unless separately reviewed.

### AUTH-SIGNER-R3-04 — HIGH: Incident blacklist contains incorrect Resolve UIDs

The blacklist labels `9a56c835-b972-08ed-a1b2-c3d4e5f60718` and `d1590073-a215-4b7f-915a-7d00fa727278` as the incident project and timeline UIDs (§6.2, lines 341–360). The incident signoff and preflight record identify the actual project UID as `0ee71404-6405-47ca-89e1-aaeaac142398` and timeline UID as `e4e5a587-661d-4854-8f37-8a279d3f2ee1`; `d159...` is the inserted timeline item's UID, and `9a56...` is derived from the challenge/name, not the Resolve project UID. The blacklist would therefore fail to reject the original project/timeline if either were renamed, while potentially blacklisting an unrelated item UID.

**Required change:** Correct the blacklist from the authoritative incident records, map project UID and timeline UID to their respective fields, and add a static acceptance fixture proving each original UID is rejected even when its display name changes. Keep the incident's existing name blacklist as an additional check.

### AUTH-SIGNER-R3-05 — HIGH: Nonce ledger creation and cross-process duplicate checks are not atomic

The descriptor-open example computes `is_new` with `os.path.exists(path)` before opening the file relative to `dir_fd` (§4.1, lines 205–219). This path-based check is racy: the file can be created/replaced between the check and `openat`, causing a newly created ledger entry not to receive the required directory `fsync`. The append path then checks only `in_memory_index` while holding `flock` (§4.2, lines 229–251). With two signer processes that loaded the same index before either append, the second process can acquire the lock after the first, still see its stale index, and append the duplicate challenge. The plan does not require one signer instance or refresh the index from disk under the lock.

**Required change:** Determine creation using an atomic descriptor-relative open protocol (no `exists()` pre-check), fsync the directory whenever the entry is created, and validate the opened file with `fstat`. Under the inter-process lock, re-read/refresh the authoritative ledger state before every consume-once check, or enforce a single signer process with a durable OS singleton lock and recovery rule. Preserve fail-closed torn-tail handling.

## Review conclusion

R3 makes substantial progress and fixes the signature-algorithm mismatch from R2, but the same-UID signer/dispatcher arrangement and client-supplied TTY descriptor still leave direct paths around the intended authority boundary. The wrong incident UIDs also make the explicit no-reuse control ineffective. The plan is therefore **`CHANGES_REQUIRED`** and is not ready for signer/dispatcher implementation approval or a new live run.

Gate 6 remains `NOT ACCEPTED` and incident-only. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review authorizes no implementation, key provisioning/access, Resolve operation, dispatch, retry, undo, or cleanup. No source or test files were changed.
