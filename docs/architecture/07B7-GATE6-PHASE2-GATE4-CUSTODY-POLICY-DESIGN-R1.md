# Gate 6 Phase 2 Gate 4 Verifier Key Custody Governance Options Analysis

**Document ID:** `07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1`  
**Revision:** `R4`  
**Status:** `DECISION_READY_GOVERNANCE_OPTIONS_ANALYSIS`  
**Parent Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1`  
**Date:** 2026-11-05  
**Governing Standard:** Gate 4 Authority Design R10 (`07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md`), DEP-01 R4 Amended V3, DEP-02A/B/C, DEP-03 R5, DEP-04 UPSTREAM R1-R4, Dispatcher Admission Contracts R1 R2  
**Operating Mode:** `DOCUMENTATION_AND_GOVERNANCE_OPTIONS_ANALYSIS_ONLY; NO_IMPLEMENTATION; NO_TESTS; NO_KEY_GENERATION_OR_ACCESS; NO_HOST_OR_STATE_ACCESS; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Architectural Context & The Authority Triad (§3.1)

### 1.1 Background & Incident Recovery Context
Under Plan 07B7, the ResolveFlow subsystem is undergoing structured post-incident hardening following an unauthorized live mutation incident. Gate 6 remains strictly `NOT ACCEPTED` and incident-only. Gate G07 and Milestones M1, M2, and M3 strictly remain `OPEN`. Node `WO5 (Live Canary Execution Directive)` in the post-incident dependency Directed Acyclic Graph (DAG) is completely disconnected with strictly zero incoming edges.

A central requirement of the hardened architecture is the total decoupling of authorization, verification, and execution. Prior to any state mutation, four independent gates must be cleared:
1. **Gate 2 (Human & Creative Governance):** Dual human consent consisting of `SignedApprovalEnvelope` (Creative Director) and `SignedOperatorConsentEnvelope` (Human Operator).
2. **Gate 4 (Independent Technical Preflight):** Automated and audited technical verification captured in `SignedPreflightEvidenceEnvelope`.
3. **Gate 5 (Engineering Release Clearance):** Technical Lead signoff captured in `SignedExecutionWorkorderRecord`.
4. **Signer Oracle Mutation Capability Clearance:** Kernel-enforced single-use execution clearance over authenticated AF_UNIX IPC.

Workorder `G07-07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1` Revision R10 established the frozen contract and verification semantics for Gate 4. However, §4.4 and §8.2 of that document explicitly identified that **the custody of the Gate 4 preflight verifier private key (`verifier_key_id`) remains an unresolved governance blocker (Blocker A)**. This document provides the formal, decision-ready options analysis required to evaluate viable key custody models.

### 1.2 The Gate 4 Preflight Authority Mandate
Gate 4 represents the **technical truth** of the target environment immediately prior to execution. Its authority must be established independently of both the human operator seeking to perform work and the dispatcher executing the workflow.

The core mandate of Gate 4 is to cryptographically attest that five immutable preflight predicates hold true simultaneously:
1. `project_and_timeline_active`: Target project and timeline are currently active in DaVinci Resolve.
2. `empty_timeline_verified`: The active timeline contains exactly zero video clips, zero audio clips, and zero subtitle elements.
3. `topology_verified`: Video track 1 exists; track topology conforms strictly to the canonical 1-action canary configuration.
4. `incident_denylist_verified`: The target timeline does not match any entry in the immutable incident blacklist.
5. `media_pool_source_verified`: The source media asset is present in the media pool and its content matches the canonical digest.

### 1.3 Strict Reconciliation with the Three-Principal OS Model
Plan 07B7 and DEP-01 §2.1 establish exactly three operating system execution principals. No fourth operating system principal exists, and introducing any new POSIX account or restricted UID is strictly prohibited:

| OS Principal | POSIX UID / GID | Authorized Functions & State Custody | Prohibited Operations & Boundaries |
| :--- | :--- | :--- | :--- |
| `_resolveflow_signer` | UID `901` / GID `901` | Custody of `/var/lib/resolveflow/signer/` (mode `0700`). Holds Oracle private key (`oracle_private.key`), HMAC signing key (`trial_auth.key`), and durable execution nonces (`consumed_nonces.jsonl`). | **Strictly prohibited from holding preflight keys or signing preflight evidence.** Prohibited from accessing Resolve scripting APIs, Resolve GUI, display servers, or network sockets. |
| `_resolveflow_dispatcher` | UID `902` / GID `902` | Custody of `/var/lib/resolveflow/dispatcher/` (mode `0700`). Holds execution journal (`execution_journal.wal`), target ledgers (`target_ledger.v2.json`), receipts (`receipt.json`), and preflight replay ledger (`consumed_preflight_runs.jsonl`). Connects to Oracle AF_UNIX socket. Hosts Dispatcher admission listener. | **Strictly prohibited from signing preflight evidence (self-attestation prohibited).** Sole custodian of preflight replay tracking. Consumer and verifier of Gate 4 evidence. Prohibited from accessing Signer private keystore. |
| `interactive_user` | UID `1000` / GID `1000` (`vanntruongg`) | Runs DaVinci Resolve Studio GUI process on `DISPLAY=:1`. Executes interactive user sessions, visual inspection, and client commands. | Custody of operator consent keys. Runs candidate runner. Evaluates Class A public trust root. **Possesses zero direct read or write access to `/var/lib/resolveflow/dispatcher/` or `/var/lib/resolveflow/signer/`.** |

### 1.4 Prohibition on Principal Expansion & Oracle Co-location
Any custody proposal that violates the Three-Principal OS model is architecturally invalid:
- **No Fourth POSIX Principal:** Proposing a new user (e.g. `_resolveflow_verifier:904`) is prohibited because it requires system-level provisioning changes that are outside the accepted architecture and violates the settled DEP-01 security boundary.
- **Categorical Rejection of Signer Oracle Co-location:** Placing the preflight verifier private key inside `_resolveflow_signer:901` is strictly forbidden because:
  1. *Destruction of Independence:* The Signer Oracle is the authoritative gate for mutation clearance. Co-locating preflight signing within the Signer Oracle merges the technical inspection gate into the clearance authority, destroying independent technical evidence provenance.
  2. *Sandbox Breach:* `_resolveflow_signer` operates in a headless, network-isolated systemd sandbox (`ProtectSystem=strict`, `PrivateTmp=true`, no `DISPLAY`). Accessing DaVinci Resolve state requires display server access (`DISPLAY=:1`) or Resolve IPC, which would catastrophically breach the Signer sandbox.
  3. *Domain Separation Breach:* Cryptographic keys must not cross schema domains. The Oracle key (`oracle_key_id`) signs wire responses; it must never sign preflight envelopes.

### 1.5 Strict Six-Role Pairwise Key Separation Invariant
The architecture defines six distinct cryptographic roles across the complete lifecycle:
$$\mathcal{K} = \{ (\text{Role}_i, \text{KeyID}_i, \text{KeyMaterial}_i) \}_{i=1}^6$$
consisting of:
1. **Gate 4 Preflight Verifier:** $(\text{Verifier}, \text{verifier\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
2. **Gate 2 Human Operator:** $(\text{Operator}, \text{operator\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
3. **Gate 2 Creative Director:** $(\text{Director}, \text{director\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
4. **Gate 5 Tech Lead Release:** $(\text{TechLead}, \text{tech\_lead\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
5. **Dispatch Oracle Clearance:** $(\text{Oracle}, \text{oracle\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
6. **Mutation Capability Signer:** $(\text{Signer}, \text{trial\_signer\_key\_id}, \text{SecretKey}_{\text{HMAC-SHA256}})$

**Pairwise Distinctness Invariant:**
$$\forall i \ne j: \quad \text{KeyID}_i \ne \text{KeyID}_j \quad \text{and} \quad \text{KeyMaterial}_i \ne \text{KeyMaterial}_j$$

Under no circumstances may the preflight verifier key share key identifiers, raw key bytes, public key fingerprints, or private key material with any of the other five roles.

---

## 2. Technical Grounding & Envelope Bindings (§3.2)

### 2.1 The 21-Field `SignedPreflightEvidenceEnvelope` Schema
The Gate 4 contract checked in at `src/core/contracts/signed_preflight_contracts.py` defines the immutable frozen dataclass `SignedPreflightEvidenceEnvelope` with exactly 21 fields:

```python
@dataclass(frozen=True)
class SignedPreflightEvidenceEnvelope:
    schema_version: int                   # Strictly int 1 (rejecting bool-as-int)
    preflight_run_id: str                 # Canonical UUIDv4
    verifier_id: str                      # Non-empty str, no whitespace padding
    verifier_key_id: str                  # Non-empty str, no whitespace padding
    verified_at_iso: str                  # Strict UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)
    valid_until_iso: str                  # Strict UTC ISO-8601; interval <= 1800.0s
    target_project_uid: str               # Pinned: "4fd4272d-f0bc-445c-9490-5723cd6831c2"
    target_project_name: str              # Pinned: "RF_TRIAL_AEC8F0A7A365D5F5"
    target_timeline_uid: str              # Pinned: "ba6a6775-8ec4-41a4-bf2f-704de902e68d"
    target_timeline_name: str             # Pinned: "RF_TRIAL_TL_AEC8F0A7A365D5F5"
    target_timeline_fps_numerator: int    # Pinned: 24
    target_timeline_fps_denominator: int  # Pinned: 1
    observed_start_frame: int             # Pinned: 86400
    project_and_timeline_active: bool     # Must be True
    empty_timeline_verified: bool         # Must be True
    topology_verified: bool               # Must be True
    incident_denylist_verified: bool      # Must be True
    media_pool_source_verified: bool      # Must be True
    source_file_sha256: str               # 64-char lowercase hex
    audit_chain_head_sha256: str          # 64-char lowercase hex
    verifier_signature: str               # 128-char lowercase hex (Ed25519)
```

### 2.2 Canonical Serialization (RFC 8785) & Direct PureEd25519 Signing
The cryptographic signature is computed over exactly 20 fields, excluding `verifier_signature`. The payload is serialized using RFC 8785 JSON Canonicalization Scheme (JCS) via `canonical_json_bytes()`:
- Keys are sorted strictly lexicographically by UTF-16 code units.
- Whitespace is eliminated (no trailing spaces or line breaks).
- Number representations conform to IEEE 754 without exponent formatting anomalies.
- Booleans serialize strictly as `true` or `false`.

**Direct PureEd25519 Verification:**
The contract verification logic enforces RFC 8032 PureEd25519 directly over the raw JCS byte sequence:
$$\text{Payload} = \text{JCS}(\text{Envelope} \setminus \{ \text{"verifier\_signature"} \})$$
$$\text{Ed25519Verify}(\text{PubKey}_{\text{verifier\_key\_id}}, \text{Payload}, \text{Bytes}(\text{verifier\_signature})) \equiv \text{True}$$

**Cryptographic Invariant on Signature Operation:**
Under RFC 8032 PureEd25519, the signing algorithm internally computes $\text{SHA-512}(R \,||\, A \,||\, M)$ where $M$ is the complete canonical JCS message. Crucially:
1. **No External Pre-Hashing:** The contract does **not** implement Ed25519ph (pre-hashed Ed25519). If an external tool or device pre-hashes the message into a 32-byte SHA-256 or 64-byte SHA-512 digest and passes that digest as the message $M$ to an Ed25519 signer, the resulting signature signs the hash bytes rather than the JCS text, causing contract verification over $\text{Payload}$ to **fail unconditionally**.
2. **No Protocol Enveloping / WebAuthn Assertions:** WebAuthn / FIDO2 authentication assertions wrap signatures around an authenticator data structure ($\text{authenticatorData} \,||\, \text{clientDataHash}$) rather than raw application messages. A signature produced via standard WebAuthn assertion flows cannot verify against the frozen `SignedPreflightEvidenceEnvelope` contract without fundamentally altering the verified byte layout.

### 2.3 Strict Cryptographic Bindings
The preflight evidence envelope binds the following immutable trial constants:
1. **Target Project Identity:** UID `4fd4272d-f0bc-445c-9490-5723cd6831c2`, Name `RF_TRIAL_AEC8F0A7A365D5F5`.
2. **Target Timeline Identity:** UID `ba6a6775-8ec4-41a4-bf2f-704de902e68d`, Name `RF_TRIAL_TL_AEC8F0A7A365D5F5`.
3. **Timeline Frame Rate & Geometry:** Numerator `24`, Denominator `1` ($24.0\text{ fps}$), Start Frame `86400` ($01:00:00:00$).
4. **Source Clip Content Hash:** `source_file_sha256` matching the canonical media file (`7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916`).
5. **Audit Chain Head Binding:** `audit_chain_head_sha256` binding the preflight run cryptographically to the historical append-only audit trail.

### 2.4 The Five Mandatory Technical Predicates
The envelope explicitly incorporates five boolean predicates that must evaluate to `True`. The contract constructor rejects any envelope where any predicate is `False` or non-boolean, preventing ambiguous signoff states.

### 2.5 Scope of Cryptographic Proof: What the Ed25519 Signature Does and Does NOT Prove

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       WHAT THE SIGNATURE PROVES                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Integrity: The 20 canonical payload fields were not tampered with        │
│    subsequent to signing.                                                   │
│ 2. Authenticity: The signature was created by a holder of the private key   │
│    matching the public key trust anchor for verifier_key_id.                │
│ 3. Non-repudiation: The signer explicitly committed to the specific run ID, │
│    target UIDs, hashes, and boolean predicate assertions.                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    WHAT THE SIGNATURE DOES NOT PROVE                        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. NOT Proof of Physical Time: Timestamps (verified_at_iso, valid_until_iso)│
│    are self-asserted claims by the signer, not trusted third-party clock    │
│    attestations.                                                            │
│ 2. NOT Proof of Live State Truth: The signature proves the signer claimed   │
│    predicates were True; it does not physically guarantee that the probe    │
│    script observed authentic DaVinci Resolve state unless the acquisition   │
│    channel itself is authenticated.                                         │
│ 3. NOT Proof of Semantic Verification: A hardware touch proves physical    │
│    presence at the device, but does not prove the human verified payload    │
│    claims.                                                                  │
│ 4. NOT Proof of State Persistence: State verified at T0 may be altered at   │
│    T1 before execution. (Mitigated by validity window <= 1800s and planned  │
│    single-use consumption in Dispatcher journal).                           │
│ 5. NOT Authorization to Mutate: Gate 4 is technical signoff only; mutation  │
│    requires Gates 2, 4, 5, and Signer Oracle clearance.                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. The Same-UID Process Isolation Deficit on POSIX Systems (§3.3)

### 3.1 The Graphical Execution Dilemma
DaVinci Resolve Studio on Linux runs exclusively within an interactive graphical desktop session bound to X11/Wayland display server (`DISPLAY=:1`). The Resolve scripting API (via Python `fusionscript` / IPC) connects directly to the running application process.

Under the Three-Principal OS Model:
- `_resolveflow_signer:901` is headless, network-isolated, and has no display access.
- `_resolveflow_dispatcher:902` is a background daemon with no display access.
- Only `interactive_user:1000` (`vanntruongg`) possesses access to `DISPLAY=:1` and the active DaVinci Resolve session.

Therefore, any technical probe that inspects DaVinci Resolve state (querying project status, timeline tracks, clip counts) must execute as `interactive_user:1000`.

### 3.2 Lack of Intra-UID Process Boundaries in Linux DAC
Standard Linux Discretionary Access Control (POSIX DAC) enforces security boundaries between distinct UIDs. It provides **zero process-level isolation between processes executing under the same UID**:
- Any process running as UID 1000 can read any file owned by UID 1000 (mode `0600` or `0700`).
- Any process running as UID 1000 can attach to another UID 1000 process using `ptrace` (unless restricted by `yama.ptrace_scope`), read its memory via `/proc/$PID/mem`, or inspect its environment via `/proc/$PID/environ`.
- File system permissions cannot prevent Process A (e.g. an operator tool) from reading private key files created by Process B (e.g. a verifier tool) if both run as UID 1000.

### 3.3 Attack Vectors of Filesystem-Stored Private Keys under UID 1000
If `verifier_private_key.pem` is stored on the filesystem accessible to UID 1000 (e.g. `~/.config/resolveflow/keys/verifier_private_key.pem`):
1. **Collusion & Role Collapse:** The human operator running as UID 1000 can easily script the execution of both Gate 2 (`SignedOperatorConsentEnvelope`) and Gate 4 (`SignedPreflightEvidenceEnvelope`), completely eliminating the independence of the technical verification gate.
2. **Accidental Self-Attestation:** A candidate runner script executing under UID 1000 could automatically sign its own preflight checks using the local key file, bypassing genuine preflight verification.
3. **Malware / Compromise Escalation:** Any arbitrary script, build tool, or dependency executing in the user's workspace could extract the verifier private key from disk and forge preflight envelopes arbitrarily.

### 3.4 Security Objective: Enforcing True Technical Separation
The fundamental security objective of Gate 4 key custody is to **prevent the interactive user or any arbitrary process under UID 1000 from signing Gate 4 preflight evidence without passing through an independent, physically verified, or externally controlled custody boundary**.

---

## 4. Comparative Evaluation of Key Custody Proposals (§3.4)

Three distinct custody proposals are evaluated as **proposals only**:

```
                       ┌────────────────────────────────────────┐
                       │  CUSTODY MODEL PROPOSALS FOR GATE 4   │
                       └────────────────────────────────────────┘
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
┌──────────────────┐             ┌──────────────────┐             ┌──────────────────┐
│    PROPOSAL 1    │             │    PROPOSAL 2    │             │    PROPOSAL 3    │
│ Hardware Security│             │ Offline Auditor  │             │ Remote Signing   │
│ Device (PIV/     │             │ Station (Air-Gap │             │ Service (Cloud / │
│ PKCS#11 Pure     │             │ Key Custody Only)│             │ KMS Over Net)    │
│ Ed25519)         │             │                  │             │                  │
├──────────────────┤             ├──────────────────┤             ├──────────────────┤
│ Non-exportable   │             │ Physically air-  │             │ UNAVAILABLE &    │
│ on-chip key. PIN │             │ gapped machine.  │             │ UNAPPROVED.      │
│ + touch required │             │ Protects key, not│             │ Violates zero-   │
│ per signature.   │             │ live observation.│             │ network safety.  │
└──────────────────┘             └──────────────────┘             └──────────────────┘
```

---

### 4.1 Proposal 1: Hardware-Backed Signing Device with Independently Verified User Presence / PIN
In Proposal 1, the preflight verifier private key material is generated directly on and permanently confined within a dedicated physical hardware security device.

#### 4.1.1 Concrete Signing Mechanism & Contract Compatibility
To satisfy the frozen `SignedPreflightEvidenceEnvelope` contract, the hardware device must support **RFC 8032 PureEd25519 raw message signing over arbitrary-length byte buffers**:
- **Protocol Disqualification (WebAuthn / FIDO2 Assertions):** Standard FIDO2/WebAuthn assertions cannot be used. WebAuthn constructs a signature over $\text{authenticatorData} \,||\, \text{clientDataHash}$, where $\text{clientDataHash} = \text{SHA-256}(\text{clientDataJSON})$. This signature structure will not verify under the contract's `Ed25519PublicKey.verify(signature, canonical_json_bytes)`.
- **Pre-Hashing Disqualification:** Tokens or APIs that require the host to pre-hash the message into a digest prior to signing cannot be used, because PureEd25519 takes the complete message $M$ directly.
- **Concrete Viable Mechanism:** A dedicated hardware cryptographic token accessed via **PKCS#11 with PureEd25519 support** (e.g., YubiKey 5 Series with firmware 5.7+ running the PIV applet with Curve25519/Ed25519 support, or a dedicated smartcard/HSM supporting the `CKM_EDDSA` mechanism with `CKF_SIGN` over raw message buffers).
  - The host probing tool running as UID 1000 connects to the local PKCS#11 module (e.g. `libykcs11.so` or `opensc-pkcs11.so`).
  - The client provides the exact 20-field canonical JCS byte sequence directly to `C_SignInit` / `C_Sign`.
  - The hardware token performs the complete RFC 8032 Ed25519 signature computation entirely on-chip and returns the 64-byte raw signature (encoded as 128 lowercase hex characters).

#### 4.1.2 Hardware PIN & Touch Policy Specifications
The hardware token must be provisioned during the key generation ceremony with strict security attributes:
1. **`PINPolicy=Always`:** The device enforces that the user PIN (6–8 digits) must be entered immediately prior to every single signing transaction. The PIN is never cached across signing operations.
2. **`TouchPolicy=Always`:** The device requires physical capacitive contact on the token's sensor for every single private-key operation. Touch policies such as `Cached` (which permits reusing a prior touch within a 15-second cache window) or `Never` (which disables touch entirely) are strictly prohibited.
3. **Touch Timeout Semantics:** Under `TouchPolicy=Always`, touch is required for each operation; the device itself does not impose a fixed 15-second cache window. Any timeout before aborting an unfulfilled touch prompt is governed by the host PKCS#11 module/caller configuration. If touch is not provided before the host-configured timeout elapses, the operation aborts fail-closed. The exact timeout value and driver behavior must be formally established and verified for the specific hardware model, firmware, PIV slot, and PKCS#11 library during procurement and ceremony testing.
4. **Hardware Retry Counter:** The device permanently locks after 3 consecutive incorrect PIN attempts, requiring administrative PUK recovery.

#### 4.1.3 Precise Boundary of Same-UID Protection and Its Limits
It is vital to establish what Proposal 1 **does** and **does NOT** guarantee against same-UID threats:
- **What It Guarantees (Protection Against Unattended Forgery):** A background process, cron job, malicious package dependency, or automated script running under `interactive_user:1000` **cannot** forge a Gate 4 signature. Even if the process has full read access to the filesystem and environment, it cannot extract the private key from the hardware enclave, nor can it sign without physical human capacitive touch.
- **What It Does NOT Guarantee (The Human Deception Deficit):** If a malicious process under UID 1000 initiates a signing request while the user is actively working at the workstation, the hardware token will flash requesting touch. If the user touches the token without inspecting the specific payload being signed (e.g., assuming it is an expected operation), the token will emit a valid signature. Therefore, **Proposal 1 enforces physical contemporaneous human presence, but does not provide a secure, isolated display for the human to verify the semantic payload claims**.

#### 4.1.4 Pairwise Separation & Role Preservation
- A dedicated, physically distinct hardware token is assigned solely to the Gate 4 Preflight Verifier role.
- Its keypair is generated independently from the Human Operator's token (if any), the Director's token, the Tech Lead's token, and the Signer Oracle's key.
- Pairwise key separation ($\text{KeyID}_i \ne \text{KeyID}_j$ and $\text{KeyMaterial}_i \ne \text{KeyMaterial}_j$) is strictly satisfied.

#### 4.1.5 Clock and Timestamp Provenance
- Standard hardware tokens do not contain a battery-backed real-time clock (RTC).
- Therefore, the timestamp fields (`verified_at_iso`, `valid_until_iso`) originate from the host system clock and are included in the signed payload.
- The hardware token asserts *that someone pressed the button to sign these claims*, but does **not** generate an independent cryptographic timestamp token.

---

### 4.2 Proposal 2: Offline Independent Auditor Signing Station (Air-Gapped Key Custody)
In Proposal 2, the preflight verification key is held on a completely separate, physically air-gapped machine operated by an independent compliance officer or technical auditor.

#### 4.2.1 Key Custody Protection
Proposal 2 provides maximum **key custody isolation**:
- The private key exists exclusively on an encrypted, non-networked workstation.
- It is physically impossible for any software, network attacker, or process on the Resolve workstation (including root or UID 1000) to extract or compromise the private key.

#### 4.2.2 The Evidence Provenance Deficit: Separating Key Custody from Live State Truth
While Proposal 2 completely protects key custody, **it does not solve the problem of authenticating the live DaVinci Resolve state**:
1. **Unauthenticated Evidence Acquisition:** The evidence bundle (JSON metadata dumps, clip manifests, track topology logs, and UI screenshots) is gathered entirely by a script executing as `interactive_user:1000` on the Resolve host.
2. **Vulnerability to Synthetic Bundles:** If processes under UID 1000 are compromised or malicious, a script can construct an internally consistent, synthetic evidence bundle containing fake metadata, forged logs, and pre-recorded screenshots showing an empty timeline, while the live Resolve timeline is actually dirty.
3. **The Limitation of Bundle Hashing:** When the offline auditor's station verifies SHA-256 hashes of the files inside `evidence_bundle_<preflight_run_id>.tar.gz`, the hashes prove only that the files were not corrupted or modified between bundle packaging and audit inspection. **Hashing does NOT prove that the data originated from an authentic DaVinci Resolve process or that the observations represent physical truth**.
4. **Architectural Reality:** Unless an independent, hardware-rooted or kernel-attested measurement of the live Resolve process is designed and accepted, the offline auditor is merely signing unauthenticated host telemetry. Therefore, **Proposal 2 protects key custody only, and cannot be treated as an independent Gate 4 truth source**.

#### 4.2.3 Latency & Validity Window Exhaustion
- The contract enforces a maximum validity window:
  $$\Delta t = t_{\text{valid\_until}} - t_{\text{verified\_at}} \le 1800.0\text{ seconds (30 minutes)}$$
- The manual air-gap procedure (exporting bundle to USB, physical transport, auditor login, manual inspection, signing, transfer back to host, submission to Dispatcher) realistically requires 15 to 25 minutes.
- This creates severe operational latency, leaving an unacceptably narrow margin for Gate 5 release signoff and Dispatcher admission before `ERR_GATE4_PREFLIGHT_EXPIRED` triggers fail-closed.

---

### 4.3 Proposal 3: Remote / Networked Signing Service (Cloud KMS / HSM-as-a-Service)
In Proposal 3, the preflight verifier key is managed by a centralized remote signing service (e.g. AWS KMS, Google Cloud KMS, HashiCorp Vault, or a dedicated corporate HSM appliance accessible over TLS).

#### 4.3.1 Explicit Governance Status: UNAVAILABLE & UNAPPROVED
**This proposal is explicitly classified as UNAVAILABLE and UNAPPROVED under current governance and architectural constraints.**
1. **Direct Violation of Safety Invariants:** The project operating mode and post-incident invariants strictly specify `NO_SOCKET_OR_NETWORK`. No network interface configuration, DNS resolution, internet access, or remote API connection is permitted.
2. **Attack Surface Expansion:** Introducing a remote network endpoint introduces transport layer risks, DNS poisoning, external denial-of-service, TLS certificate validation dependencies, and credential exfiltration vectors.
3. **Absence of Host Infrastructure:** The target test environment has zero configured or authorized remote KMS integrations.
4. **Zero Physical Presence:** Network APIs sign programmatically upon presentation of valid bearer tokens or mTLS client certificates, enabling trivial automated forging by any script under UID 1000 that can read the API token.

---

### 4.4 Comprehensive Comparative Matrix across Critical Dimensions

| Evaluation Dimension | Proposal 1: Hardware Security Device (PIV / PKCS#11 Pure Ed25519) | Proposal 2: Offline Auditor Station (Air-Gapped Key Custody) | Proposal 3: Remote Signing Service (Cloud KMS / Vault) |
| :--- | :--- | :--- | :--- |
| **Operational Status** | **Proposal Only (Requires Hardware Procurement & Firmware 5.7+ Verification)** | **Proposal Only (High Latency; Fails Evidence Provenance)** | **UNAVAILABLE / UNAPPROVED (Zero Network Invariant)** |
| **Contract Compatibility** | **Compatible:** PureEd25519 over raw JCS bytes via PKCS#11 `CKM_EDDSA`. (WebAuthn disqualified). | **Compatible:** Offline script invokes pure Ed25519 signing library directly over JCS bytes. | Incompatible without network stack and cloud SDK integration. |
| **Key Non-Exportability** | **Hardware-Enforced:** On-chip generation; scalar cannot be read by host OS or root. | **Air-Gap Enforced:** Stored on separate non-networked computer; encrypted disk. | **Cloud HSM Enforced:** Non-exportable HSM enclave. |
| **Request Authorization** | Hardware PIN + Physical Capacitive Touch per signature (`TouchPolicy=Always`). | Human auditor manual inspection + passphrase entry. | Bearer token / mTLS API call (purely programmatic). |
| **Physical Presence Proof** | **Enforced:** Physical touch required at moment of signing. (Does not prove payload review). | **Enforced:** Human auditor physically present at station. | **None:** Readily automated by scripts holding network tokens. |
| **Same-UID Deficit Resolution** | **Substantial:** Prevents unattended/background script forgery under UID 1000. | **Key Custody Only:** Host key exfiltration eliminated; evidence provenance unresolved. | **Fails:** Any UID 1000 process can use the local network API token. |
| **Evidence Provenance** | Host probe telemetry (unauthenticated kernel channel). | Host probe telemetry (unauthenticated host bundle). | Host probe telemetry. |
| **Pairwise Role Separation** | **Strict:** Distinct physical token and keypair from all 5 other roles. | **Strict:** Distinct offline keypair from all 5 other roles. | **Strict:** Distinct KMS key ARN / ID. |
| **Three-Principal Model** | **Preserved:** No new POSIX accounts; client runs under UID 1000. | **Preserved:** Probing tool runs under UID 1000; signing is external. | **Violated:** Introduces external network identity management. |
| **Validity Window Impact ($\le 1800\text{s}$)** | **Low Operational Latency:** Local signing completes promptly upon operator touch, preserving the vast majority of the 30-minute validity window. (Latency bounds depend on operator reaction and host driver timeout). | **Critical Risk:** Manual transfer takes 15–25 min; severe risk of expiration. | **Minimal:** Network call takes milliseconds (if network existed). |
| **Network Invariant Adherence** | **Strictly Adheres:** Zero network sockets or interfaces required. | **Strictly Adheres:** Fully air-gapped; zero network traffic. | **Violates:** Requires internet / network socket access. |

---

## 5. Timestamp Provenance & Future Replay Protection Governance (§3.5)

### 5.1 Signed Timestamp Claims vs. Cryptographic Time Proofs
The fields `verified_at_iso` and `valid_until_iso` represent **signed assertions** made by the verifier entity. They do **not** constitute an RFC 3161 cryptographically verified Time-Stamp Token (TST) issued by an independent Time Stamping Authority (TSA).

The architectural significance of this distinction:
$$\text{Signature} = \text{Sign}_{K_{\text{verifier}}}(\dots, \text{verified\_at\_iso}, \text{valid\_until\_iso}, \dots)$$
The signature proves that the entity in possession of $K_{\text{verifier}}$ attested to those specific timestamp values. It does **not** prove that the physical wall-clock time was actually `verified_at_iso` when the cryptographic operation took place.

### 5.2 Validity Window Dynamics & Constraints
`SignedPreflightEvidenceEnvelope` enforces:
1. Strict Ordering: $\text{verified\_at\_iso} < \text{valid\_until\_iso}$.
2. Maximum Bounded Interval:
   $$\Delta t = t_{\text{valid\_until}} - t_{\text{verified\_at}} \le \text{MAX\_PREFLIGHT\_VALIDITY\_SECONDS} = 1800.0\text{ s (30 minutes)}$$

When the envelope is submitted to `_resolveflow_dispatcher` via `DispatcherAdmissionRequest`:
- The future Dispatcher admission logic will evaluate current host time $\tau_{\text{dispatcher}}$ against the target tolerance:
  $$t_{\text{verified\_at}} - \epsilon_{\text{skew}} \le \tau_{\text{dispatcher}} \le t_{\text{valid\_until}} + \epsilon_{\text{skew}}$$
  where $\epsilon_{\text{skew}} = 30.0\text{ seconds}$.
- If $\tau_{\text{dispatcher}} > t_{\text{valid\_until}}$, admission must be rejected fail-closed with `ERR_GATE4_PREFLIGHT_EXPIRED`.
- If $\tau_{\text{dispatcher}} < t_{\text{verified\_at}} - \epsilon_{\text{skew}}$, admission must be rejected fail-closed with `ERR_GATE4_PREFLIGHT_PREDATES_CLOCK`.

### 5.3 Future Replay Mitigation via Dispatcher Atomic Replay Ledger (Non-Operational Prerequisite)
Because timestamps are bounded but span up to 30 minutes, timestamp validity alone cannot prevent replay attacks (submitting an already-used preflight envelope to authorize multiple dispatches).

**Critical Architecture Reality & Prerequisite Status:**
1. **Currently Unimplemented and Strictly Non-Operational:**
   - As documented in Gate 4 Authority Design R10 §7.3 and §8.2, the Dispatcher admission listener daemon (`resolveflow-dispatcher.service`) **does not currently exist**, and the IPC boundary is **strictly non-operational**.
   - The replay ledger (`consumed_preflight_runs.jsonl`) is **not implemented** in the active codebase. Acceptance of Workorder `G07-07B7-GATE6-PHASE2-DISPATCHER-ADMISSION-CONTRACTS-R1` (Revision R2) was strictly `ACCEPTED_FOR_CONTRACT_ARTIFACTS_ONLY`, establishing only pure in-memory DTOs and framing helpers.
   - Therefore, **replay protection cannot currently be claimed as an active operational assurance**.
2. **Target Design Requirement for Future Implementation:**
   - Under future follow-on workorders (`WO-GATE4-HARNESS-IMPLEMENTATION-R1` and Dispatcher Daemon Implementation), replay protection must be implemented inside `_resolveflow_dispatcher:902`:
     - Exclusive custody of `/var/lib/resolveflow/dispatcher/consumed_preflight_runs.jsonl`.
     - Descriptor-anchored atomic check-and-append of `preflight_run_id` (UUIDv4) within the single-threaded admission loop prior to state reservation.
     - Any duplicate `preflight_run_id` will fail closed with target error code:
       ```python
       ERR_GATE4_PREFLIGHT_RUN_ALREADY_CONSUMED: "Preflight run ID has already been admitted and consumed"
       ```

---

## 6. Key Lifecycle, Ceremony, Distribution & Revocation Policy (§3.6)

### 6.1 Key Generation Ceremony Requirements
If Proposal 1 (Hardware Security Device) is approved by governance, key generation must occur under a formal, witnessed ceremony:
1. **Algorithm:** Pure Ed25519 (RFC 8032 / Edwards-curve Digital Signature Algorithm).
2. **Entropy Source:** Hardware cryptographic RNG on the secure element.
3. **Role Independence:** Key generation for `verifier_key_id` must occur in a dedicated session on a designated hardware token.
4. **Key Identifier Standard:** Canonical grammar `^[a-z0-9_-]{8,64}$`, structured as:
   $$\text{verifier\_key\_id} = \text{"g4-verifier-" } + \text{epoch} + \text{"-" } + \text{fingerprint\_prefix}$$
   *(Example: `g4-verifier-2026e1-a1b2c3d4`)*.
5. **Witnessing & Documentation:** The ceremony must produce an auditable manifest recording device serial number, firmware revision, witness signatures, public key SPKI SHA-256 fingerprint, and confirm that zero private key material exists on the host filesystem.

### 6.2 Class A Trust Anchor Provisioning Policy
The public key must be deployed as a Class A system trust anchor:
- **Canonical Host Path:** `/etc/resolveflow/preflight_verifier_public_key.pem`
- **Ownership:** `root:root` (UID 0 / GID 0)
- **Permissions:** `0644` (world-readable, root-writable only)
- **Parent Directory:** `/etc/resolveflow/` (`0755`, `root:root`)
- **Format:** Standard PEM-encoded SubjectPublicKeyInfo (SPKI) for Ed25519.

### 6.3 Descriptor-Anchored Trust Root Loading Policy
When `_resolveflow_dispatcher` or any verification component loads the trust anchor, it must use descriptor-anchored system calls to prevent race conditions and symlink attacks:
1. Open path with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`.
2. Execute `fstat()` on the resulting file descriptor.
3. Validate:
   - `stat.st_uid == 0` and `stat.st_gid == 0` (owned by root).
   - `(stat.st_mode & 0777) == 0644` (exact mode).
   - `stat.st_nlink == 1` (no hardlink multiplexing).
   - `S_ISREG(stat.st_mode)` (must be regular file; no sockets, FIFOs, or device nodes).
4. Read raw public key bytes (exactly 32 bytes from SPKI) and cache in memory.

### 6.4 Key Rotation & Revocation Policy
- **Maximum Cryptographic Lifetime:** 90 days from ceremony date.
- **Scheduled Rotation:** A new keypair is generated under a new ceremony; the host PEM file is updated atomically via root-owned `replace_file_atomically` (`mkstemp` + `rename`).
- **Emergency Revocation (Compromise):**
  If a hardware token is lost, stolen, or suspected compromised:
  1. *Host Trust Anchor Removal:* Host root administrator immediately removes `/etc/resolveflow/preflight_verifier_public_key.pem` or truncates it to 0 bytes.
  2. *Immediate Fail-Closed:* All subsequent admission attempts fail closed with:
     ```python
     ERR_GATE4_TRUST_ANCHOR_MISSING: "Gate 4 preflight verifier public key trust anchor is not established on host"
     ```
  3. *Tombstone Ledger:* The compromised `verifier_key_id` is appended to `/etc/resolveflow/revoked_keys.jsonl`. The verifier loader checks this ledger and rejects revoked keys with:
     ```python
     ERR_GATE4_VERIFIER_KEY_REVOKED: "Verifier key ID has been explicitly revoked"
     ```

---

## 7. Fail-Closed Error Taxonomy & Handling (§3.7)

The target verifier and dispatcher admission layers must implement the following standardized error taxonomy:

| Error Code | Trigger Condition | Architectural Action |
| :--- | :--- | :--- |
| `ERR_GATE4_TRUST_ANCHOR_MISSING` | `/etc/resolveflow/preflight_verifier_public_key.pem` does not exist or cannot be opened. | Abort admission; log diagnostic; zero mutation. |
| `ERR_GATE4_TRUST_ANCHOR_UNSAFE` | File descriptor check fails on trust anchor (not root, symlink, unsafe mode, link count $>1$). | Abort admission; log security alert; zero mutation. |
| `ERR_GATE4_VERIFIER_KEY_REVOKED` | `verifier_key_id` appears in revocation ledger or is tombstoned. | Abort admission; log security alert; zero mutation. |
| `ERR_GATE4_KEY_ID_MISMATCH` | `envelope.verifier_key_id` does not match the key ID bound to the trusted public key. | Abort admission; fail closed; zero mutation. |
| `ERR_GATE4_SIGNATURE_INVALID` | Ed25519 signature verification fails over the 20 canonical payload bytes. | Abort admission; log tampering alert; zero mutation. |
| `ERR_GATE4_PREFLIGHT_EXPIRED` | Current UTC time exceeds `valid_until_iso` (plus skew tolerance). | Abort admission; require fresh preflight run; zero mutation. |
| `ERR_GATE4_PREFLIGHT_PREDATES_CLOCK` | Current UTC time is earlier than `verified_at_iso` minus skew tolerance. | Abort admission; flag clock anomaly; zero mutation. |
| `ERR_GATE4_PREFLIGHT_RUN_ALREADY_CONSUMED` | Target future error when `preflight_run_id` already exists in durable ledger. | Abort admission; flag replay attempt; zero mutation. |
| `ERR_GATE4_TARGET_MISMATCH` | Pinned project/timeline UIDs or names do not match contract constants. | Abort admission; flag configuration error; zero mutation. |
| `ERR_GATE4_PREDICATE_FAILED` | Any of the 5 technical predicates is `False` or non-boolean. | Abort admission; flag invalid preflight inspection; zero mutation. |
| `ERR_GATE4_DEVICE_TOUCH_TIMEOUT` | Host PKCS#11 caller/driver timeout elapsed waiting for hardware capacitive touch. | Preflight tool aborts fail-closed; no envelope emitted. |
| `ERR_GATE4_DEVICE_PIN_LOCKED` | Hardware token locked due to repeated incorrect PIN attempts. | Preflight tool aborts; physical security recovery required. |

---

## 8. Governance Decision Record (§4)

### 8.1 Formal Custody Model Status
$$\mathbf{selected\_custody\_model: \quad UNRESOLVED\_PENDING\_GOVERNANCE\_APPROVAL}$$

**Formal Declaration:**  
No key custody model is approved, activated, or provisioned by this document. The final determination of which custody model to deploy remains strictly **UNRESOLVED** pending formal, joint governance review and authorized signature by the established decision authorities.

### 8.2 Recognized Decision Authorities
The custody decision must be made exclusively by the recognized governing roles of the ResolveFlow project. No new roles, committees, or operating system principals are recognized or created:
1. **Human Operator (`interactive_user:1000` / `vanntruongg`):** Authority over desktop interactive operations and physical token custody on the editing workstation.
2. **Creative Director:** Authority over project boundaries, timeline targets, and artistic signoff.
3. **Technical Lead (Codex / Engineering Authority):** Authority over architectural invariants, code review, cryptographic policies, and execution workorders.
4. **Host System Administrator (`root:0`):** Authority over OS principal management, systemd units, `/etc/resolveflow/` Class A trust root deployment, and host DAC enforcement.

### 8.3 Required Approvals & Evidence for Model Activation
To transition any custody proposal from `PROPOSAL` to `ACTIVATED_POLICY`, the following explicit evidence must be produced and accepted:
1. **Formal Joint Signoff:** Written concurrence from all four recognized decision authorities (Technical Lead, Creative Director, Human Operator, and Host System Administrator) formally adopting the specific custody architecture.
2. **Physical Token Manifest (if Proposal 1 selected):** Model, serial number, firmware revision (verifying firmware 5.7+ for Ed25519 PIV PKCS#11 support), FIPS certification documentation, and physical custody assignment.
3. **Audited Ceremony Log:** SHA-256 fingerprint of the generated Ed25519 public key, registered key ID, witness signatures, and formal confirmation that no private key material exists on any general-purpose filesystem.
4. **Host DAC Provisioning Evidence:** Cryptographic verification that `/etc/resolveflow/preflight_verifier_public_key.pem` has been deployed with mode `0644` owned by `root:root` with link count 1.

### 8.4 Sequenced Implementation Workorders
Following governance approval of the custody model, the implementation must proceed in strictly bounded, sequential workorders:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      GATE 4 REMAINING WORKORDER SEQUENCE                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. WO-GATE4-CUSTODY-POLICY-DECISION-R1                                      │
│    Formal governance resolution selecting Proposal 1 or Proposal 2.         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. WO-GATE4-KEYGEN-CEREMONY-R1                                              │
│    Execution and documentation of witnessed key generation ceremony.        │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. WO-GATE4-PROVISIONING-SPEC-R1                                            │
│    Packaging and host deployment of /etc/resolveflow trust anchor.          │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. WO-GATE4-HARNESS-IMPLEMENTATION-R1                                       │
│    Implementation of descriptor-anchored loader & replay ledger.            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. WO-GATE4-HOST-VERIFICATION-R1                                            │
│    Non-executing static verification of deployed host trust anchor.         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.5 Explicit Statement of Non-Authorization & Safety Invariants
- **This document alone does NOT authorize any of the above actions.**
- **No key generation, key handling, file creation, socket binding, or host provisioning is authorized.**
- **Blockers B (Gate 5 Release Authority), C (Signer Oracle Orphan Governance), and D (Host DAC Provisioning) remain completely unresolved.**
- Target DaVinci Resolve project `RF_TRIAL_AEC8F0A7A365D5F5` and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` on `DISPLAY=:1` must remain untouched and empty.
- `ready_to_execute = False` is strictly maintained. Gate 6 strictly remains `NOT ACCEPTED` / incident-only. Gate G07 and Milestones M1, M2, and M3 strictly remain `OPEN`. Node `WO5 (Live Canary Execution Directive)` remains completely disconnected with strictly zero incoming edges.

---

## 9. Architectural Recommendation

While the decision remains formally `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`, this analysis provides the following **technical recommendation** to the governing authorities:

1. **Recommend Proposal 1 (Hardware Security Device with Pure Ed25519 PKCS#11):**
   - *Rationale:* Proposal 1 provides the strongest technical protection against automated background process forgery under UID 1000 by requiring hardware non-exportability, per-signature PIN entry, and contemporaneous physical capacitive touch. Local signing avoids manual air-gap sneaker-net delays, readily fitting within the 30-minute envelope validity window, and strictly adheres to the zero-network invariant.
   - *Prerequisite for Activation:* Prior to adoption, governance must verify that the specific hardware device firmware (e.g. YubiKey 5 Series firmware $\ge 5.7$) supports RFC 8032 PureEd25519 raw message signing via PKCS#11 without pre-hashing, enforce `PINPolicy=Always` and `TouchPolicy=Always`, and formally establish the host driver/PKCS#11 touch timeout configuration.
2. **Classify Proposal 2 (Offline Auditor Station) as Unviable for Live Preflight:**
   - *Rationale:* Proposal 2 protects key custody but fails to establish live evidence provenance (auditing unauthenticated host telemetry), and introduces 15–25 minute sneaker-net latency that severely risks expiring the 30-minute validity window.
3. **Categorically Disqualify Proposal 3 (Remote Signing Service):**
   - *Rationale:* Directly violates the zero-network invariant, expands the attack surface, and lacks physical presence verification.
4. **Next Governance Action:**
   - Submit this options analysis for formal joint governance review by the complete decision authority quorum (Technical Lead, Creative Director, Human Operator, and Host System Administrator) to consider adopting Proposal 1.
   - Key generation, ceremony execution, and trust-anchor provisioning workorders remain blocked until that joint approval is formally recorded and accepted.
