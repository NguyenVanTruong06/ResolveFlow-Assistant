# Static Review — Gate 4 Custody Policy Design R1

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1`  
**Decision:** `CHANGES_REQUIRED`  
**Reviewed artifact SHA-256:** `f955621bdd037868bfc98c2f97421bbc87c7f125bf151c0dc6e674fdde68064f`  
**Review mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY`

## Scope and evidence

Verified the submitted SHA-256 values for the design document, `.bridge/task.md`, and `.bridge/report.md`; all matched the submitted values. Reviewed the custody proposals against the accepted Gate 4 envelope contract, the Gate 4 authority design, and the checked-in Dispatcher admission contract/source inventory. No tests or runtime operations were performed.

## Findings

### CUSTODY-R1-01 — BLOCKER: Proposal 1 does not specify a signing mechanism compatible with the frozen Ed25519 contract

Sections 4.1.3–4.1.4 (lines 231–243) mix FIDO2/WebAuthn, PIV/PKCS#11, and OpenPGP as if they provide one interchangeable signing operation. The approved envelope contract verifies Ed25519 directly over the 20-field canonical JCS payload (`SignedPreflightEvidenceEnvelope` and `canonical_json_bytes()`); it does not define a WebAuthn assertion format, pre-hash protocol, or alternate signature algorithm. The proposal instead says the client computes a SHA-512/SHA-256 digest and asks the token to sign “the digest or payload.” Signing a digest as the Ed25519 message will not verify as a signature over the contract’s JCS bytes. WebAuthn assertions also sign authenticator/client-data structures rather than the raw JCS message, so they cannot be substituted without a contract change.

The accompanying guarantees are also device-policy-dependent: PIN caching, touch policy, Ed25519 support, and per-operation user presence differ by authenticator, applet, firmware, and configuration. The document's generic device examples do not establish that one device simultaneously provides the exact Ed25519 operation and the stated per-signature PIN/touch behavior. Consequently §9's recommendation that Proposal 1 “completely solves” the same-UID deficit is not established by this design.

**Required correction:** Select and specify one concrete supported device/app/API and exact mechanism that signs the contract's canonical JCS bytes with Ed25519; remove the digest alternative unless the frozen contract is deliberately revised and separately reviewed. State the verified PIN/touch policy and its limits, and narrow the same-UID claim to what that mechanism actually guarantees. Keep the model unselected pending those facts and governance approval.

### CUSTODY-R1-02 — HIGH: Proposal 2 authenticates the evidence bundle's signer, not the origin or truth of the Resolve observations

Sections 4.2.2–4.2.3 (lines 266–281) have the Resolve-host tool, running as the same `interactive_user:1000` principal whose processes the design treats as untrusted, generate JSON dumps, logs, screenshots, and the proposed payload. The offline station recomputes hashes and inspects those supplied files, then signs. Hashes establish that bundle members are internally consistent; they do not establish that the files came from Resolve, were captured by an uncompromised probe, or faithfully represent the live project/timeline. A same-UID process can construct a self-consistent false bundle before transfer. Thus the claim that the workflow independently validates technical predicates and entirely eliminates the same-UID evidence-integrity deficit is unsupported.

**Required correction:** Separate key custody from evidence provenance. Either define a trusted, independently verifiable acquisition/attestation channel and bind it into the signed payload, or classify the offline station as protecting key custody only and explicitly state that it cannot independently attest live Resolve state from an unauthenticated bundle. Do not present the workflow as a Gate 4 truth source until this dependency is resolved.

### CUSTODY-R1-03 — MEDIUM: Replay defense is described as deployed despite being an unimplemented prerequisite

Section 5.3 (lines 355–364) says replay prevention “is enforced” by the Dispatcher daemon and describes an atomic ledger check-and-append as existing behavior. The accepted Gate 4 authority design §7.3 explicitly says the Dispatcher admission listener logic does not currently exist and the boundary is non-operational; its §8.3 lists replay-ledger implementation as a future workorder. Acceptance of the Dispatcher Admission DTOs is contract-artifact-only and does not supply that listener or ledger implementation. The current text therefore turns a target requirement into an operational assurance and overstates replay protection.

**Required correction:** Recast §5.3 as a required future control, mark it unimplemented/non-operational until the listener and durable ledger are implemented and separately accepted, and align the error code and sequencing claims with those accepted artifacts.

## Decision

The custody decision correctly remains `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`, and this document does not itself authorize keys, provisioning, or execution. However, the technical basis for the recommended hardware option and the offline alternative's evidence assurance is not yet sound, and replay protection is overstated. Remediate the findings and submit a new revision for review. No custody model is accepted by this review.

## Safety disposition

Static review only. No tests, key access or generation, host changes, sockets, network, Resolve import/connection, or live operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
