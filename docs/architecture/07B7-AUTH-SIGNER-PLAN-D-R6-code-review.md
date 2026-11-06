# Static Plan Review — G07-AUTH-SIGNER-PLAN-D Revision R6

**Decision: `CHANGES_REQUIRED`**  
**Scope:** Static review of Revision R6 against the active design-only workorder, R5 review findings, and frozen 07B5 authorization contracts. No tests, prototypes, Resolve, IPC, network, or key contents were accessed.

## Artifact integrity

Submitted hashes match the files on disk at review time:

| Artifact | Submitted SHA-256 | Result |
|---|---|---|
| `.bridge/task.md` | `fc00f10ae1ef0df394697a3a24b9314a5a13b6e2962b3c3d8a73e0e8299ca8c5` | Match |
| `.bridge/report.md` | `bbcc238c2d2cfe5a35373a1de5d320ddc463bfefd6090294f8006dc9ae619aae` | Match |
| `docs/architecture/07B7-independent-authorization-signing-workflow-plan.md` | `c52be9c116f7f7981b127b0448a19af6e20ec7f84522e6601712fb5db142be34` | Match |
| Prior R5 review | `4dc77a7fe1ff86938df9a6a8b627413863bb237c2e94feb58828901e4819bbe9` | Match |

R6 substantially addresses the R5 findings: it enumerates consent checks, defines a 11-point oracle response gate, sets parent-directory and socket permissions, and binds the revocation anchor to a manifest digest. The following issues remain.

## Findings

### AUTH-SIGNER-R6-01 — BLOCKER: Revocation signature contract omits an ambiguous manifest claim

The manifest schema in §6.1 has nine fields other than `manifest_signature`: `manifest_version`, `epoch`, `issued_at_iso`, `valid_until_iso`, four revocation lists, and `issuer_id` (§6.1, lines 364–382). The next line says canonical signed bytes cover “exactly the 8 manifest fields excluding `manifest_signature`” (§6.1, line 385). The plan therefore does not define whether one of those nine claims—most critically `issuer_id`, which selects the trusted Security Officer authority—is included in the signature. This leaves issuer identity and signature verification semantically ambiguous at the authority boundary. The sample `manifest_signature` is also shown as a SHA-256-looking value with an ellipsis rather than a valid-format Ed25519 signature, without labeling it as a non-parseable placeholder.

**Required change:** Enumerate the exact canonical claim set and include all nine non-signature fields, including `issuer_id`; specify the pinned issuer/key-ID lookup and signature encoding/length. Replace the sample signature with an explicitly marked placeholder that cannot be mistaken for a valid signature. Keep the epoch anchor digest over a precisely defined byte representation and test unknown/missing/duplicate fields and issuer substitution.

### AUTH-SIGNER-R6-02 — HIGH: A challenge can be accepted after its expiry

The consent gate verifies that the signed timestamp lies between `challenge.issued_at_iso` and `challenge.expires_at_iso`, and separately permits a timestamp up to 120 seconds old (§3.2, lines 220–229). It does not require the current verification time to be at or before `challenge.expires_at_iso`. A consent signed immediately before expiry can therefore be submitted after the challenge has expired and still pass the 120-second freshness check, if the expired entry remains in `active_challenges`. This weakens the challenge TTL and permits delayed use of a valid operator gesture outside its intended challenge window.

**Required change:** Require the challenge to remain unexpired at verification time (`now_utc <= challenge.expires_at_utc`) in addition to the signed-time and bounded-freshness checks. Specify atomic challenge consumption on successful authorization issuance and ensure expired challenges cannot remain usable through cache retention or restart recovery. Add a boundary case for submission just after expiry.

## Conclusion

Revision R6 closes the major R5 gaps, but the revocation signature’s signed claim set is internally inconsistent and the consent verifier does not enforce challenge expiry at the moment of use. The plan is **`CHANGES_REQUIRED`** and is not approved for implementation or a new live run.

Gate 6 remains `NOT ACCEPTED` and incident-only. `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. This review authorizes no implementation, key provisioning/access, Resolve operation, dispatch, retry, undo, or cleanup. No `src/` or `tests/` files were changed.
