# Gate 6 Phase 2: Gate 4 Preflight Authority & Trust Bootstrap Architecture Design (Revision R7)

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1` (Remediation Revision R7)  
**Document Status:** `DESIGN_SPECIFICATION_ONLY; NO_IMPLEMENTATION; UNRESOLVED_PREREQUISITE`  
**Classification:** Security Architecture, Cryptographic Provenance & Trust Bootstrap Specification  
**Governing Standard:** DEP-01 R4, DEP-02A/B/C, DEP-03 R5, DEP-04 UPSTREAM R1-R4, DEP-04 CONTRACTS R2, Phase 2 Boundary Design R1 R8, Post-Incident Runbook R5  
**Execution Context:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY; NO_IMPLEMENTATION; NO_TESTS; NO_HOST_OR_STATE_ACCESS; NO_KEY_GENERATION_OR_ACCESS; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Executive Summary & Problem Formulation

### 1.1 Context and Background
Under Plan 07B7 and the Gate 6 post-incident hardening framework, the execution of the live canary mutation requires five sequential, mutually independent verification gates (Gate 1 through Gate 5) before dispatch admission. 

In the Phase 2 boundary design review (`docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` Revision R8), four critical post-incident blockers were formally isolated:
- **Blocker A:** Gate 4 Preflight Evidence Authority & Trust Bootstrap.
- **Blocker B:** Gate 5 Release Authority & Candidate Pinned Binding.
- **Blocker C:** Signer Oracle Orphan Governance & Verification Timeout/Expiry.
- **Blocker D:** Host DAC & Systemd Service Provisioning Verification.

Workorder `G07-07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1` specifically and exclusively addresses **Blocker A**: establishing the source-grounded design for authenticated Gate 4 preflight evidence authority and its trust-bootstrap boundary under the accepted three-principal OS model.

### 1.2 The Scope of Blocker A
Gate 4 requires technical verification that the target DaVinci Resolve environment is in an exact, uncorrupted, empty state prior to any canary execution. The contract layer for this verification has been established in `src/core/contracts/signed_preflight_contracts.py` as `SignedPreflightEvidenceEnvelope` (accepted under `G07-07B7-GATE6-DEP04-CONTRACTS-R1` / `R2`).

However, the architecture currently possesses:
1. **No Established Verifier Signing Role:** The repository defines the data contract, but specifies no authoritative operational entity or custodian for the preflight private key (`verifier_key_id`).
2. **No Provisioned Trust Anchor:** There is no provisioned public key file, trust anchor path, or distribution mechanism for the verifier public key on the host. The existing `/etc/resolveflow/oracle_public_key.pem` is strictly and exclusively the Signer Oracle public key and cannot be repurposed.
3. **No Key Lifecycle Policy:** There are no formal rules for verifier key generation, storage, rotation, revocation, or compromise handling.
4. **Replay & Freshness Verification Gaps:** While the contract enforces a 1800-second maximum validity window, accepted source code lacks a durable preflight consumption ledger to prevent envelope replay within that window.

### 1.3 Invariant Preservation & Non-Authorization
This document is strictly an **architectural design specification**. It does not establish a signing authority, create or access cryptographic keys, provision host files, execute verification commands, connect to DaVinci Resolve, or authorize Gate 6 execution. 

Blockers B, C, and D remain completely unresolved. Safety invariants remain intact:
- `ready_to_execute = False` is strictly maintained.
- Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
- Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
- Node `WO5 (Live Canary Execution Directive)` remains completely disconnected with strictly zero incoming edges.

### 1.4 Revision History
- **Revision R2:** Resolved initial six findings (`GATE4-AUTH-DESIGN-R1-01` through `R1-06`) regarding timestamp claim vs. signing time distinction, pairwise role key separation, removal of timeline reset instructions, baseline replay grounding in `EXHAUSTED` state, three-principal DAC confinement, and workspace source hash matching.
- **Revision R3:** Resolved three findings (`GATE4-AUTH-DESIGN-R2-01` through `R2-03`) regarding exact `reserve_target()` source code matching, total rejection of embedded key fallback, and two-phase separation of acquisition vs. admission failures.
- **Revision R4:** Resolved finding `GATE4-AUTH-DESIGN-R3-01` by distinguishing permitted read-only trust-anchor access from prohibited mutable state paths.
- **Revision R5:** Classified paths into Classes A, B, and C and introduced sequential 6-step ordering.
- **Revision R6:** Resolved `GATE4-AUTH-DESIGN-R5-01` and `R5-02` by specifying atomic `flock` compare-and-consume and restricting Class B ledger access exclusively to the Dispatcher daemon.
- **Revision R7:** Resolved findings regarding Gate 5 pre-validation before Class B consumption (`GATE4-AUTH-DESIGN-R6-01`) and specification of the Dispatcher Admission IPC Boundary requirements (`GATE4-AUTH-DESIGN-R6-02`).
- **Revision R8:** Reconciled the Dispatcher Admission socket endpoint path and DAC permissions with DEP-01 R4 / DEP-02A (`GATE4-AUTH-DESIGN-R7-01`) and removed the unanchored Dispatcher response signature in favor of kernel AF_UNIX stream integrity (`GATE4-AUTH-DESIGN-R7-02`).
- **Revision R9:** Specified bidirectional mutual `SO_PEERCRED` verification across Dispatcher IPC boundary (`GATE4-AUTH-DESIGN-R8-01`).
- **Revision R10 (Current):** Resolves finding `GATE4-AUTH-DESIGN-R9-01` from `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R9-code-review.md`:
  - **`GATE4-AUTH-DESIGN-R9-01` (MEDIUM):** Reconciled client-side `SO_PEERCRED` peer process validation with Dispatcher service daemon credentials:
    1. Corrected expected server peer process credentials from socket inode group GID 903 to daemon process primary GID 902 (`cred.uid == 902` and `cred.gid == 902`), matching `resolveflow-dispatcher.service`.
    2. Explicitly distinguished `SO_PEERCRED` process-level credentials (`struct ucred`) from filesystem inode DAC ownership (`_resolveflow_dispatcher:resolveflow_clients`, UID 902, GID 903).
    3. Preserved fail-closed abort with `ERR_DISPATCHER_PEER_UNTRUSTED` on any mismatch.

---

## 2. Authority Separation & The Three-Principal Model (§3.1)

### 2.1 The Four Governance & Operational Roles
To guarantee end-to-end integrity and prevent unauthorized canary execution, the architecture strictly segregates four distinct roles:

1. **Gate 4 Technical System Verifier (Evidence Acquisition & Attestation):**
   - **Responsibility:** Software components that inspect the live DaVinci Resolve application state (active project UID, active timeline UID, video track topology, clip counts, media pool contents) and issue the signed technical assertion (`SignedPreflightEvidenceEnvelope`).
   - **Key:** `verifier_key_id` (Ed25519).
   - **Boundary:** Asserts that a technical inspection took place and observed nominal target conditions corresponding to the signed claims.

2. **Human Operator (Visual Observation & Consent):**
   - **Responsibility:** A physically present human operator supervising the workstation display (`DISPLAY=:1`), visually verifying that the project and timeline on screen are empty, and providing contemporaneous attestation (`SignedOperatorConsentEnvelope`).
   - **Key:** `operator_key_id` (Ed25519).
   - **Boundary:** Proves human awareness, direct physical supervision, and fresh visual confirmation; binds to the active challenge nonce and plan digests.

3. **Technical Lead / Release Authority (Governance & Code Authorization):**
   - **Responsibility:** The project technical authority who reviews audited code, candidate hashes, and operational parameters, issuing a signed execution workorder (`SignedExecutionWorkorderRecord`).
   - **Key:** `tech_lead_key_id` (Ed25519).
   - **Boundary:** Authorizes the exact candidate script hash (`reviewed_candidate_sha256`), permitted CLI command, and bound execution plan for a single execution cycle.

4. **Audit & Journal Custody (Durable Record Keeping & Dispatch Admission):**
   - **Responsibility:** The system components (`DurableDispatchJournal`, `TrialAuthorizationSignerDaemon`, `DurableNonceLedger`) that record WAL transitions, lock targets, enforce single-use execution nonces, and issue cryptographically cleared dispatch tokens.
   - **Key:** `oracle_key_id` (Ed25519) and `trial_signer_key_id` (HMAC-SHA256).
   - **Boundary:** Authoritative for mutation clearance and local crash-consistent terminal recording.

### 2.2 Strict Reconciliation with the Three-Principal OS Model
DEP-01 §2.1 and Plan 07B7 establish exactly three operating system execution principals. No fourth operating system principal exists, and introducing any new POSIX account or restricted UID is strictly prohibited:

| OS Principal | POSIX UID / GID | Authorized Functions & State Custody | Prohibited Operations & Boundaries |
| :--- | :--- | :--- | :--- |
| `_resolveflow_signer` | UID `901` / GID `901` | Custody of `/var/lib/resolveflow/signer/` (mode `0700`). Holds Oracle private key (`oracle_private.key`), HMAC signing key (`trial_auth.key`), and durable execution nonces (`consumed_nonces.jsonl`). | **Strictly prohibited from holding preflight keys or signing preflight evidence.** Prohibited from accessing Resolve scripting APIs, Resolve GUI, display servers, or network sockets. |
| `_resolveflow_dispatcher` | UID `902` / GID `902` | Custody of `/var/lib/resolveflow/dispatcher/` (mode `0700`). Holds execution journal (`execution_journal.wal`), target ledgers (`target_ledger.v2.json`), receipts (`receipt.json`), and preflight replay ledger (`consumed_preflight_runs.jsonl`). Connects to Oracle AF_UNIX socket. Hosts Dispatcher admission listener. | **Strictly prohibited from signing preflight evidence (self-attestation prohibited).** Sole custodian of preflight replay tracking. Consumer and verifier of Gate 4 evidence. Prohibited from accessing Signer private keystore. |
| `interactive_user` | UID `1000` / GID `1000` (`vanntruongg`) | Runs DaVinci Resolve Studio GUI process on `DISPLAY=:1`. Executes interactive user sessions, visual inspection, and client commands. | Custody of operator consent keys. Runs candidate runner. Evaluates Class A public trust root. **Possesses zero direct read or write access to `/var/lib/resolveflow/dispatcher/` or `/var/lib/resolveflow/signer/`.** |

### 2.3 Rejection of Merging Preflight Signing into the Signer Oracle
An architectural proposal to co-locate preflight verifier signing inside `_resolveflow_signer` (or the Signer Oracle daemon) is **categorically rejected** for the following structural reasons:
1. **Separation of Verification from Authorization:** The Signer Oracle is the authoritative clearinghouse that issues cryptographic mutation clearance. If the Oracle were to sign its own preflight evidence, the independent technical gate (Gate 4) collapses into an internal self-affirmation, destroying independent evidence provenance.
2. **Environment & Sandbox Isolation:** The Signer Oracle operates as a headless, network-isolated daemon in a locked-down systemd sandbox (`ProtectSystem=strict`, `PrivateTmp=true`, no `DISPLAY` access). DaVinci Resolve requires access to the display server (`DISPLAY=:1`) and scripting IPC. Giving the Signer daemon access to `DISPLAY=:1` or Resolve IPC breaches its least-privilege security perimeter.
3. **Key Isolation:** The Signer Oracle private key (`oracle_key_id`) must remain solely dedicated to signing `OracleVerificationResponse` and `OracleTerminalNotificationResponse` wire payloads. Sharing keys across distinct contract schemas violates cryptographic domain separation.

### 2.4 Cryptographic Role & Pairwise Key Separation Invariant
All cryptographic keys across the workflow must be pairwise distinct. Specifically, let the system key mapping be defined as:
$$\mathcal{K} = \{ (\text{Role}_i, \text{KeyID}_i, \text{KeyMaterial}_i) \}_{i=1}^6$$
encompassing:
1. Gate 4 Preflight Verifier: $(\text{Verifier}, \text{verifier\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
2. Gate 2 Human Operator: $(\text{Operator}, \text{operator\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
3. Gate 2 Creative Director: $(\text{Director}, \text{director\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
4. Gate 5 Tech Lead Release: $(\text{TechLead}, \text{tech\_lead\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
5. Dispatch Oracle Clearance: $(\text{Oracle}, \text{oracle\_key\_id}, \text{PubKey}_{\text{Ed25519}})$
6. Mutation Capability Signer: $(\text{Signer}, \text{trial\_signer\_key\_id}, \text{SecretKey}_{\text{HMAC-SHA256}})$

**Pairwise Distinctness Invariant:**
$$\forall i \ne j: \quad \text{KeyID}_i \ne \text{KeyID}_j \quad \text{and} \quad \text{KeyMaterial}_i \ne \text{KeyMaterial}_j$$

- Different key identifiers alone do not prove cryptographic separation; each role must be backed by an independently generated, cryptographically unique keypair or secret.
- For asymmetric roles (1 through 5), the canonical 32-byte public key bytes (and their SHA-256 fingerprints) must be pairwise distinct.
- For the symmetric role (6), the HMAC-SHA256 secret key must be distinct from any private key scalar.
- Enforcing and validating this pairwise distinctness across all six roles is an explicit **future contract and implementation requirement** that must be incorporated into admission gates.

---

## 3. Envelope-to-Evidence Cryptographic Binding (§3.2)

### 3.1 Accepted Contract Structure (`SignedPreflightEvidenceEnvelope`)
The Gate 4 contract defined in `src/core/contracts/signed_preflight_contracts.py` establishes a frozen, schema-versioned data structure with 21 mandatory fields:

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
    project_and_timeline_active: bool     # Mandatory: True
    empty_timeline_verified: bool         # Mandatory: True
    topology_verified: bool               # Mandatory: True
    incident_denylist_verified: bool      # Mandatory: True
    media_pool_source_verified: bool      # Mandatory: True
    source_file_sha256: str               # Pinned clip SHA-256 (64 hex lowercase)
    audit_chain_head_sha256: str          # Current audit log head (64 hex lowercase)
    verifier_signature: str               # 128 lowercase hex (Ed25519)
```

### 3.2 Canonical Signing Payload Boundary
The canonical signing payload is generated via `canonical_signing_payload_bytes()`, which applies RFC 8785 JSON Canonicalization Scheme (JCS) via `canonical_json_bytes()` to exactly 20 fields (all envelope fields except `verifier_signature`):
$$\text{Payload} = \text{JCS}(\text{Envelope} \setminus \{\text{verifier\_signature}\})$$

The signature `verifier_signature` is a 64-byte Ed25519 signature encoded as 128 lowercase hex characters:
$$\text{verifier\_signature} = \text{Sign}_{\text{verifier\_private\_key}}(\text{Payload})$$

### 3.3 Target Geometry & Predicate Bindings
The envelope establishes rigid cryptographic bindings to the physical target geometry and environment predicates:
1. **Pinned Project & Timeline UIDs:** Strictly matches target project `4fd4272d-f0bc-445c-9490-5723cd6831c2` and timeline `ba6a6775-8ec4-41a4-bf2f-704de902e68d`.
2. **Pinned Frame Rate & Start Frame:** Timeline frame rate must be exactly `24/1` FPS and start frame must be exactly `86400` (01:00:00:00 timecode standard).
3. **Five Mandatory Technical Predicates:** All five boolean predicates must be explicitly `True` (booleans-as-integers are strictly rejected):
   - `project_and_timeline_active`: Target project and timeline are currently open and active in the Resolve session.
   - `empty_timeline_verified`: Target timeline contains zero clips across all tracks.
   - `topology_verified`: Video Track 1 exists and matches target parameters.
   - `incident_denylist_verified`: Target does not collide with the immutable incident blacklist.
   - `media_pool_source_verified`: Source canary clip is imported in the Media Pool and its digest matches `source_file_sha256`.
4. **Source Clip Digest:** `source_file_sha256` must match the exact SHA-256 digest of the canary source media (`7c83154437b2e05e01f6a795d6295e9827e04907c4479c7b0435e3a4ac18d916`).
5. **Audit Chain Head:** `audit_chain_head_sha256` binds the preflight evidence to the tip of the immutable append-only audit trail at the moment of inspection.

### 3.4 What the Cryptographic Signature Proves vs. Does Not Prove
Rigorous boundary specification requires distinguishing cryptographic claims from physical and temporal realities:

- **What the Signature Proves:**
  1. **Claim Integrity & Non-Repudiation:** The specific private key corresponding to `verifier_key_id` signed the exact 20 canonical fields. The canonical payload has not suffered bit-level modification since signing.
  2. **Assertion of Claimed Timestamps:** The signer asserted the specific strings `verified_at_iso` and `valid_until_iso` as part of the signed payload.

- **What the Signature Does NOT Prove:**
  1. **Actual Physical Time of Signing:** The signature **does not prove when the signing physically occurred**. In the accepted contract, `verified_at_iso` and `valid_until_iso` are signer-supplied claims; there is no trusted third-party timestamp authority (RFC 3161 TSA) or hardware secure-clock attestation. A rogue or compromised signer could backdate or forward-date the timestamp claims within the validity limits.
  2. **Physical Truth of Observation:** The signature does not prove that the Resolve environment was truthfully observed unless the acquisition pipeline itself is authenticated, uncompromised, and executed in an un-spoofed environment.
  3. **Intervening State Changes:** The signature does not prove that the timeline remained empty *after* `verified_at_iso`. An operator or external script could add clips to the timeline during the 1800-second window.
  4. **Absence of UI Collisions:** The signature does not prove that modal dialogs, license prompts, or popups have not appeared on `DISPLAY=:1`.
  5. **Authorization to Mutate:** A valid preflight envelope provides evidence of nominal state; it carries zero authority to clear or execute a mutation without Gate 2, Gate 3, Gate 5, and Oracle clearance.

---

## 4. Trust Bootstrap Decision & Custody Model (§3.3)

### 4.1 Prohibition on Reusing the Oracle Public Key
The existing file `/etc/resolveflow/oracle_public_key.pem` is strictly the Signer Oracle Ed25519 public key. It is deployed under DAC `root:root` mode `0644` to enable Dispatcher verification of `OracleVerificationResponse` over AF_UNIX IPC. 
- Silently repurposing `/etc/resolveflow/oracle_public_key.pem` to verify Gate 4 preflight evidence is **strictly forbidden**.
- Verifying preflight envelopes against the Oracle public key would allow an actor holding Oracle keys to fabricate preflight evidence, or would require the Oracle to sign preflight payloads, collapsing authority separation.

### 4.2 Trust Root Location & Single-Trust-Path Policy
To verify `SignedPreflightEvidenceEnvelope`, the candidate runner and Dispatcher require access to an unambiguous, authoritative preflight public key trust anchor. Three candidate options were evaluated:

| Architecture Option | Trust Anchor Path & DAC Policy | Key Custody Model | Security & Operational Trade-offs | Architectural Disposition |
| :--- | :--- | :--- | :--- | :--- |
| **Option 1: Dedicated Host Trust Root (Approved Baseline)** | `/etc/resolveflow/preflight_verifier_public_key.pem`<br>Mode `0644`, Owner `root:root` | Private key held by an independent audit token or offline key; public key provisioned by system administrator via root DAC. | - Clean separation from Oracle public key.<br>- Readable by both `interactive_user` (UID 1000) and `_resolveflow_dispatcher` (UID 902).<br>- Tamper-proof against unprivileged users.<br>- Single explicit trust path. | **APPROVED BASELINE DESIGN.** Consistent with existing host DAC pattern for `/etc/resolveflow/oracle_public_key.pem`. |
| **Option 2: Dispatcher-Owned Trust Keystore** | `/var/lib/resolveflow/dispatcher/trust_anchors/preflight.pub`<br>Mode `0644`, Owner `_resolveflow_dispatcher:902` | Managed within Dispatcher application tree. | - Fails least-privilege: `interactive_user` (UID 1000) cannot read inside `/var/lib/resolveflow/dispatcher/` (directory mode is `0700`).<br>- Candidate runner executing as UID 1000 would suffer `EACCES`. | **REJECTED.** Violates DAC boundary for candidate runner. |
| **Option 3: Repository Source Pinning (Hardcoded Key)** | `src/core/contracts/pinned_verifier_key.py`<br>Version-controlled constant in repository | Static public key embedded in codebase; private key maintained offline. | - If used as a "fallback", it allows an attacker or stale environment to bypass host trust-root revocation or removal, violating fail-closed security.<br>- Lacks dynamic key rotation or emergency revocation capabilities. | **CATEGORICALLY REJECTED.** No fallback to code-embedded keys is permitted. Verification must use a single explicit trust path. |

**Single Explicit Trust Path Requirement:**
The system must enforce a **single explicit trust path**: `/etc/resolveflow/preflight_verifier_public_key.pem`. 
- There is **strictly zero fallback** to secondary, embedded, or hardcoded repository keys.
- If the host trust root does not exist, has invalid permissions, fails SPKI parsing, or matches an emergency revocation tombstone, verification **must fail closed immediately**. Falling back to an alternative key source would create an anti-downgrade and revocation bypass vulnerability.

### 4.3 Proposed Trust Root File Format & Descriptor-Anchored Validation
Under the approved baseline (Option 1):
- **Path:** `/etc/resolveflow/preflight_verifier_public_key.pem`.
- **Format:** Standard SubjectPublicKeyInfo (SPKI) PEM encoding of an Ed25519 public key.
- **DAC Requirements:**
  - File owner: `0` (`root`), file group: `0` (`root`).
  - Permissions: strictly `0644` (or `0444`), rejecting world-writable, group-writable, or non-regular files.
  - Parent directory: `/etc/resolveflow` owned by `root:root` with mode `0755`.
- **Descriptor-Anchored Validation:** When loading the trust anchor, the verifier must open with `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`, perform `fstat()` on the file descriptor to verify ownership, mode, link count ($=1$), and regular file status, preventing symlink substitution attacks.

### 4.4 Unresolved Key Custody Governance Decisions & DAC Limits
The architecture identifies the following specific decisions that **remain unresolved governance dependencies**:
1. **Host Provisioning Fact:** The existence and provisioning of `/etc/resolveflow/preflight_verifier_public_key.pem` is **not established by any accepted repository evidence, DAC configuration artifact, or provisioning manifest**. Creating or inspecting this host path is prohibited under this workorder.
2. **Custody of the Preflight Private Key within Three Principals:**
   - Because DaVinci Resolve requires access to the graphical desktop session on `DISPLAY=:1`, any host process acquiring live Resolve state must run as `interactive_user` (UID 1000).
   - However, the human operator issuing visual consent (`SignedOperatorConsentEnvelope`) also operates as `interactive_user` (UID 1000).
   - Standard Linux POSIX DAC **cannot enforce process-level memory or file isolation between two processes running under the same UID (1000)**. If `verifier_private_key.pem` is stored on the filesystem accessible to UID 1000, the operator process can read the verifier key, collapsing the technical vs. human separation.
   - **Architectural Resolution:** True operational independence within the three-principal model requires either:
     - Holding `verifier_private_key` in a physical hardware token (e.g. FIDO2 / YubiKey / HSM) with PIN/presence requirements.
     - Acquiring preflight data via a verified script and transmitting it to an independent external auditor machine for signing.
   - The choice of private key custody mechanism is an **UNRESOLVED GOVERNANCE BLOCKER**.
3. **Formal Stop Code:** In the absence of an established, valid trust root, any evaluation must fail closed immediately:
   ```python
   ERR_GATE4_TRUST_ANCHOR_MISSING: "Gate 4 preflight verifier public key trust anchor is not established on host"
   ```

---

## 5. Key Lifecycle & Cryptographic Policy (§3.4)

### 5.1 Key Generation Policy
Any future authorization for preflight key generation must enforce:
1. **Algorithm:** Ed25519 (Edwards-curve Digital Signature Algorithm over Curve25519 conforming to RFC 8032).
2. **Entropy Source:** Cryptographically secure pseudorandom number generator (`os.urandom()` via trusted kernel CSPRNG).
3. **No Import-Time Generation:** Generating keys on module import or test execution is strictly prohibited (enforced by AST tripwires established in DEP-04-ADAPTER-DECOUPLE-R2).
4. **Key Identifier Grammar:** `verifier_key_id` must follow strict grammar: `^[a-z0-9_-]{8,64}$`, uniquely identifying the key version and custody epoch.

### 5.2 Key Custody & Storage Policy
1. **Private Key Protection:** Private key material must never be stored in world-readable locations, version-controlled repositories, temporary directories, or memory-dumpable core segments.
2. **Memory Cleansing:** In languages/runtimes where supported, private key buffers must be overwritten with zeros immediately after signing.
3. **Zero Secret Leakage:** Private key bytes must never appear in logs, error traces, telemetry, receipts, or serialized JSON contracts.

### 5.3 Public Key Pinning & Verification
1. **Key Normalization:** Raw public key bytes (32 bytes) must be read directly from SPKI PEM, rejecting custom subclasses or malleable encodings.
2. **Verification Logic:**
   $$\text{Verify}_{\text{public\_key}}(\text{verifier\_signature}, \text{Payload}) \equiv \text{True}$$
3. **Key ID Matching:** `envelope.verifier_key_id` must match the key ID bound to the trusted public key.

### 5.4 Key Rotation & Revocation Policy
1. **Scheduled Rotation:** Verifier keys must have a defined maximum cryptographic lifetime (e.g. 90 days).
2. **Revocation Mechanism:** A revocation list or tombstone ledger must be checked before validating any preflight envelope.
3. **Emergency Revocation:** If a verifier private key is compromised:
   - The public key file `/etc/resolveflow/preflight_verifier_public_key.pem` is immediately removed or replaced with a tombstone by host administration.
   - All preflight envelopes signed with that `verifier_key_id` are rejected fail-closed with:
     ```python
     ERR_GATE4_VERIFIER_KEY_REVOKED: "Verifier key ID has been revoked or tombstoned"
     ```

---

## 6. Freshness, Replay Prevention & Nonce Mechanics (§3.5)

### 6.1 Validity Interval & Clock Skew Policy
`SignedPreflightEvidenceEnvelope` enforces a strict validity interval:
- `verified_at_iso < valid_until_iso`.
- $(\text{valid\_until} - \text{verified\_at}) \le \text{MAX\_PREFLIGHT\_VALIDITY\_SECONDS}$ ($1800.0\text{ seconds}$ / 30 minutes).

When evaluating freshness against current time $\tau_{\text{now}}$:
$$\text{verified\_at} - \Delta_{\text{skew}} \le \tau_{\text{now}} \le \text{valid\_until}$$
- Allowed future clock skew $\Delta_{\text{skew}}$ is strictly bounded: $\Delta_{\text{skew}} \le 5.0\text{ seconds}$.
- If $\tau_{\text{now}} > \text{valid\_until}$, evaluation fails with `ERR_GATE4_EVIDENCE_EXPIRED`.
- If $\tau_{\text{now}} < \text{verified\_at} - \Delta_{\text{skew}}$, evaluation fails with `ERR_GATE4_EVIDENCE_FUTURE`.
- **Note on Time Provenance:** These checks establish only that the signed claims are currently valid according to the evaluating machine's trusted local clock; they do not establish that the signer's clock was truthful at the time the claims were generated.

### 6.2 Replay Analysis: One-Shot Target Exclusion vs. Replay Vulnerabilities
To assess replay risks accurately, we must analyze the interaction between preflight evidence and Dispatcher state:

1. **Current Protection via One-Shot Target Exclusion:**
   - In accepted source, `DurableDispatchJournal.reserve_target()` (`src/services/durable_dispatch_journal.py:660–666`) enforces strict one-shot target exclusion:
     ```python
     if self._target_state:
         prev_status = self._target_state.get("status")
         if prev_status in ("RESERVED", "ACTIVE", "EXHAUSTED", "IN_DOUBT_LOCKED"):
             raise DispatchJournalSecurityError(
                 f"ERR_TARGET_NOT_AVAILABLE: Target status is {prev_status}"
             )
     ```
   - In `commit_terminal()`, both successful execution (`COMPLETED`) and pre-mutation abort (`ABORTED`) atomically transition the target ledger status to `EXHAUSTED` (`:780–818`).
   - Therefore, **under intact accepted journal state, replaying a signed preflight envelope cannot cause a second mutation on the pinned target**, because `reserve_target()` fails closed with `DispatchJournalSecurityError("ERR_TARGET_NOT_AVAILABLE: Target status is EXHAUSTED")` before any adapter execution.
2. **Residual Threats Warranting Preflight Replay Defense:**
   - **Threat 1: State Reset or Ledger Loss:** If host state is cleared or corrupted, or if an administrator improperly resets the target ledger without resetting Resolve, replayed evidence could clear Gate 4.
   - **Threat 2: Concurrent Dispatch Races:** If two dispatch processes are initiated simultaneously before either reaches `commit_terminal()`, both might consume the same preflight envelope.
   - **Threat 3: Future Multi-Run or Multi-Target Scopes:** If the architecture evolves to support sequential canaries or multiple timelines, a preflight envelope pinned only to timeline UID could be reused across runs.

### 6.3 Defense-in-Depth Preflight Replay Defense
To address these residual threats, the architecture specifies two scoped defense-in-depth requirements to be implemented in follow-on work:

- **Layer 1: Binding Preflight Evidence to the Active Challenge Nonce:**
  - The Gate 4 preflight acquisition cycle must be initiated after the active challenge nonce is minted.
  - The preflight envelope or its admission gate must cryptographically bind to the active `challenge_id` / `client_nonce` of the current execution cycle.
  - Alternatively, the `SignedOperatorConsentEnvelope` must explicitly include the digest of the preflight evidence:
    $$\text{preflight\_envelope\_digest\_sha256} = \text{SHA-256}(\text{canonical\_envelope\_bytes})$$
  - Since operator consent is single-use and bound to the Signer Oracle challenge nonce, binding preflight evidence to operator consent transitively prevents preflight replay across distinct challenges.

- **Layer 2: Durable Dispatcher Preflight Consumption Ledger:**
  - `DurableDispatchJournal` must maintain an append-only, descriptor-anchored record of consumed preflight runs (`consumed_preflight_runs.jsonl`).
  - Before admitting a preflight envelope, the journal must verify that `preflight_run_id` has never been recorded.
  - Upon dispatch admission, `preflight_run_id` is durably appended and fsynced.

---

## 7. Failure, Path Boundary & Recovery Matrix (§3.6)

### 7.1 Path Classifications & Principal Access Boundaries
To reconcile state isolation with the requirements of trust validation and replay defense under the three-principal model, the architecture explicitly defines three path classes:

1. **Class A: Permitted Read-Only Host Configuration Path:**
   - Path: `/etc/resolveflow/preflight_verifier_public_key.pem` (mode `0644`, owner `root:root`).
   - Access: Both the candidate runner (`interactive_user:1000`) and the Dispatcher daemon (`_resolveflow_dispatcher:902`) are permitted bounded, descriptor-anchored read-only access strictly for public key extraction and SPKI validation.
2. **Class B: Dispatcher-Owned Bounded Preflight Replay Ledger:**
   - Path: `/var/lib/resolveflow/dispatcher/consumed_preflight_runs.jsonl` (mode `0600`, owner `_resolveflow_dispatcher:902`).
   - Access: **Exclusively accessible to `_resolveflow_dispatcher:902`.** The candidate runner (`interactive_user:1000`) has **strictly zero access** to this path or its parent directory (`/var/lib/resolveflow/dispatcher/`, mode `0700`). Replay lookup and consumption are performed solely inside the Dispatcher daemon process during dispatch admission.
3. **Class C: Prohibited Mutable State, Keystores, Sockets, and Target Ledgers:**
   - Paths: All other production files and sockets, including the Signer private keystore (`/var/lib/resolveflow/signer/`), Oracle socket (`/run/resolveflow/oracle/signer_oracle.sock`), execution WAL (`execution_journal.wal`), target ledger (`target_ledger.v2.json`), and receipts (`receipt.json`).
   - Access: Strictly prohibited prior to full gate clearance. Any admission failure stops immediately before touching Class C paths, before connecting to AF_UNIX sockets, before making DaVinci Resolve API calls, and before mutating journal state.

### 7.2 Strict Sequential Verification Ordering & Replay Protection

#### 7.2.1 Prevention of Preflight Burning by Unapproved Requests
A critical vulnerability identified in Revision R6 was the premature consumption of `preflight_run_id` before verifying subsequent non-mutating gates (such as Gate 5 Tech Lead release authorization). If `preflight_run_id` were consumed prior to Gate 5 validation, an unapproved request (e.g. invalid Tech Lead signature or mismatched candidate hash) would permanently burn the preflight evidence, causing denial-of-service for the subsequent authorized trial.

**Inviolable Invariant:**
No irreversible state mutation or consumption of preflight evidence (`preflight_run_id`) may occur until **every non-mutating authorization requirement across all gates (Gate 1, Gate 2, Gate 4, and Gate 5) has been completely verified**.

#### 7.2.2 End-to-End Admission Workflow
```
[Candidate Runner: interactive_user:1000]
       │
       ▼
[Step 1: In-Memory Envelope Syntax & Target Checks] ────► (Failure: ERR_GATE4_SCHEMA_INVALID / TARGET_MISMATCH / PREDICATE_UNSATISFIED)
       │ (Zero I/O)
       ▼
[Step 2: Read Class A Trust Anchor] ────────────────────► (Failure: ERR_GATE4_TRUST_ANCHOR_MISSING / PERMISSIONS_INSECURE)
       │ (Stops before touching Class B or Class C)
       ▼
[Step 3: Signature & Freshness Validation] ─────────────► (Failure: ERR_GATE4_SIGNATURE_INVALID / EVIDENCE_EXPIRED / EVIDENCE_FUTURE)
       │ (Stops before touching Class B or Class C)
       ▼
[Submit Dispatch Admission Request over Dispatcher IPC via /run/resolveflow/dispatcher_client/dispatcher.sock]
       │
       ▼
[Dispatcher Daemon: _resolveflow_dispatcher:902]
       │
       ▼
[Step 4: Non-Mutating Comprehensive Pre-Admission Gate]
       │ 1. Re-verify Gate 4 envelope signature against Class A trust anchor
       │ 2. Verify Gate 2 user authorization & operator consent envelopes
       │ 3. Verify Gate 5 Tech Lead execution workorder record (SignedExecutionWorkorderRecord)
       │ 4. Recompute & verify cross-bindings (target project/timeline, candidate SHA-256, plan/action digests)
       │    ───► If ANY pre-admission check fails: REJECT IMMEDIATELY (Zero Class B / Class C mutation)
       │
       ▼ (All Non-Mutating Authorizations Strictly Validated)
       │
[Step 5: Atomic Compare-and-Consume on Class B Ledger]
       │ 1. Open consumed_preflight_runs.jsonl with O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC (mode 0600)
       │ 2. Acquire exclusive descriptor lock: flock(LOCK_EX | LOCK_NB)
       │ 3. Scan ledger lines: if preflight_run_id exists, release lock and REJECT:
       │    ───► Failure: ERR_GATE4_EVIDENCE_REPLAYED
       │ 4. If absent: append {"preflight_run_id": "<id>", "consumed_at_iso": "<now>"}\n
       │ 5. fsync(ledger_fd)
       │ 6. Release lock: flock(LOCK_UN)
       │
       ▼
[Step 6: Gate 4 Clearance Formally Committed]
       │
       ▼
[Proceed to Signer Oracle AF_UNIX IPC Clearance, Class C Journal Target Reservation, and Canary Dispatch]
```

#### Detailed Step Specifications:
1. **Step 1 (In-Memory Validation - Runner & Dispatcher):** Validate schema syntax, version (`1`), pinned target names and UIDs, frame rates, and the 5 boolean predicates in memory. Zero I/O is performed.
2. **Step 2 (Trust Anchor Loading - Runner & Dispatcher):** Perform bounded descriptor-anchored read of Class A path `/etc/resolveflow/preflight_verifier_public_key.pem`. If missing, unreadable, or insecure permissions, fail closed immediately. **Stops strictly before opening Class B or Class C paths.**
3. **Step 3 (Cryptographic Signature & Freshness Check - Runner & Dispatcher):** Verify Ed25519 signature over the canonical 20-field JCS payload; verify freshness against local trusted clock. If signature is invalid, expired, future-dated, or key revoked, fail closed immediately. **Stops strictly before opening Class B or Class C paths.**
4. **Step 4 (Comprehensive Non-Mutating Admission Gate - Dispatcher Only):**
   - The Dispatcher daemon, executing as `_resolveflow_dispatcher:902`, re-evaluates Gate 4 claims.
   - Crucially, the Dispatcher verifies Gate 2 approval and Gate 5 execution workorder (`SignedExecutionWorkorderRecord`) against the Tech Lead trust anchor, confirming that the permitted command, candidate hash, and plan/action digests match.
   - **Preservation of Preflight Evidence:** If Gate 5 release authorization fails (or if Blocker B remains unresolved), dispatch is rejected immediately *without accessing or mutating the Class B replay ledger*. Valid preflight evidence is preserved intact.
5. **Step 5 (Atomic Compare-and-Consume on Class B Ledger - Dispatcher Only):**
   - Performed if and only if Step 4 passes all non-mutating authorization gates.
   - The Dispatcher opens `/var/lib/resolveflow/dispatcher/consumed_preflight_runs.jsonl` with `O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC` (mode `0600`).
   - Immediately acquires an exclusive descriptor-anchored kernel lock: `flock(fd, LOCK_EX | LOCK_NB)`. If lock contention occurs, fails closed with `ERR_PREFLIGHT_LEDGER_LOCKED`.
   - **Continuous Lock Invariant:** The exclusive lock is held continuously across both the lookup scan and the append/fsync operations.
   - The Dispatcher parses the ledger with strict duplicate-key rejection. If `preflight_run_id` is present, releases lock and rejects dispatch with `ERR_GATE4_EVIDENCE_REPLAYED`.
   - If absent, writes `{"preflight_run_id": "<id>", "consumed_at_iso": "<now>"}\n`, executes `fsync(fd)`, and releases lock.
   - If writing or fsync fails, fails closed with `ERR_PREFLIGHT_LEDGER_PERSISTENCE_FAILED`.
6. **Step 6 (Full Gate 4 Clearance Committed):** Preflight consumption is now durably established. The Dispatcher proceeds to request clearance from the Signer Oracle over AF_UNIX socket IPC, reserve the target in Class C journal (`reserve_target()`), write intent, and dispatch.

### 7.3 Dispatcher Admission IPC Boundary Specification & Unresolved Status
To bridge the client runner (`interactive_user:1000`) and the Dispatcher daemon (`_resolveflow_dispatcher:902`), the architecture aligns strictly with the accepted DEP-01 R4 and DEP-02A socket layout, directory hierarchy, and DAC policies:

- **Endpoint Path:** `/run/resolveflow/dispatcher_client/dispatcher.sock` (AF_UNIX stream socket, mode `0660`, owner `_resolveflow_dispatcher:resolveflow_clients`, GID `903`).
- **Directory Hierarchy & Permissions:** Resides inside the dedicated directory `/run/resolveflow/dispatcher_client/` (mode `02750`, setgid enabled, owner `_resolveflow_dispatcher:resolveflow_clients`, GID `903`).
- **Singleton Lock:** Accompanied by `/run/resolveflow/dispatcher_client/dispatcher.lock` (mode `0600`, owner `_resolveflow_dispatcher:resolveflow_clients`, singleton non-blocking `flock` held exclusively by the daemon).
- **Client DAC & Traversal:** The client runner executes as `interactive_user:1000`. Under the accepted DEP-01/02A model, `interactive_user` is a member of supplementary group `resolveflow_clients` (GID `903`), which provides group traversal permission (`r-x`) on `dispatcher_client/` and group read/write permission (`rw-`) to connect to `dispatcher.sock`. The runner has strictly zero access to internal oracle sockets (`/run/resolveflow/oracle/`, mode `02750`, group `_resolveflow_dispatcher`, returning immediate `EACCES` to `resolveflow_clients`).
- **Bilateral Peer Authentication Policy (`SO_PEERCRED`):**
  1. *Server-Side Verification (Dispatcher Daemon):* Upon `accept()`, the Dispatcher daemon retrieves socket credentials via `getsockopt(client_fd, SOL_SOCKET, SO_PEERCRED, ...)`, strictly requiring client process credentials `cred.uid == 1000` (`interactive_user`) and `cred.gid == 1000`. Any other connecting UID/GID is rejected immediately with connection termination before receiving or parsing request bytes.
  2. *Client-Side Verification (Candidate Runner):* Immediately upon `connect()`, the candidate runner retrieves socket credentials via `getsockopt(sock_fd, SOL_SOCKET, SO_PEERCRED, ...)`, strictly verifying that the peer process credentials match `cred.uid == 902` (`_resolveflow_dispatcher`) and process primary `cred.gid == 902` (`_resolveflow_dispatcher`). If peer process credentials do not match, the runner immediately terminates the socket connection and fails closed with `ERR_DISPATCHER_PEER_UNTRUSTED` before transmitting any request bytes and before accepting or interpreting any response bytes as admission or consumption confirmation.
  3. *Distinction Between Process Credentials and Filesystem Inode DAC:*
     - The filesystem inode of `/run/resolveflow/dispatcher_client/dispatcher.sock` is owned by `_resolveflow_dispatcher:resolveflow_clients` (UID 902, GID 903) via setgid directory inheritance to grant group traversal and connect access to `interactive_user:1000`.
     - In contrast, `SO_PEERCRED` returns the effective process credentials (`struct ucred`) of the communicating process at connection time. The Dispatcher daemon executes under service unit `resolveflow-dispatcher.service` with user `_resolveflow_dispatcher` (UID 902) and primary group `_resolveflow_dispatcher` (GID 902). Therefore, `SO_PEERCRED` strictly reports process UID 902 and process GID 902, not the socket inode GID 903. Any mismatch triggers immediate fail-closed termination.
- **Wire Framing:** Standard 4-byte big-endian unsigned length prefix, payload capped at 65536 bytes.
- **Strict Request Contract (`DispatcherAdmissionRequest`):** Must carry the four bound envelopes:
  1. `SignedApprovalEnvelope` (Gate 2 Director approval)
  2. `SignedOperatorConsentEnvelope` (contemporaneous physical operator presence on `DISPLAY=:1`)
  3. `SignedPreflightEvidenceEnvelope` (Gate 4 preflight attestation)
  4. `SignedExecutionWorkorderRecord` (Gate 5 Tech Lead release workorder)
- **Strict Response Contract (`DispatcherAdmissionResponse`) & Authenticated AF_UNIX Integrity Boundary:**
  - **DTO Schema:**
    - `schema_version: int` (strictly `1`)
    - `request_id: str` (UUIDv4 matching the admission request)
    - `status: str` (`"ADMITTED"` or `"REJECTED"`)
    - `error_code: Optional[str]` (exact failure code if status is `"REJECTED"`)
    - `execution_id: str` (bound execution run UUID)
    - `preflight_run_id: str` (bound Gate 4 run UUID confirming consumption)
    - `admitted_at_iso: str` (UTC timestamp)
  - **Rejection of Asymmetric Response Signature:** Revision R7 proposed that `DispatcherAdmissionResponse` carry an Ed25519 signature from the Dispatcher. This requirement is **explicitly rejected and removed** in Revision R8:
    1. Reusing an existing role key (e.g. Oracle, Tech Lead, or Verifier) would violate the strict pairwise key separation invariant (§2.4).
    2. Introducing a new Dispatcher signing key would create a 7th key role without defined key custody, provisioning, trust bootstrap, or distribution to the candidate runner.
  - **Kernel-Enforced AF_UNIX Stream Integrity:** Instead of an asymmetric signature, response authenticity and integrity are anchored in Linux kernel-level stream socket guarantees:
    1. *Private Descriptor Pair:* The client connects directly to the listening daemon; the resulting socket pair descriptor is confined strictly to the two communicating processes (`interactive_user:1000` and `_resolveflow_dispatcher:902`).
    2. *In-Kernel Tamper Immunity:* No third-party unprivileged process can read, inject, or alter bytes transmitted over an established AF_UNIX stream socket.
    3. *True Kernel Mutual Authentication:* Both peers cryptographically bind the connection to expected kernel identities (`interactive_user:1000` on the client end, and `_resolveflow_dispatcher:902` on the server end) via `SO_PEERCRED` validation.
    4. *Tamper-Proof Response Delivery:* Bytes transmitted by the Dispatcher daemon over this authenticated descriptor arrive unmodified and in exact FIFO sequence at the candidate runner, guaranteeing response integrity without requiring an asymmetric cryptographic signature.
- **UNRESOLVED ARCHITECTURAL PREREQUISITE:** Although the endpoint path and DAC layout match the accepted DEP-01 R4 / DEP-02A standard, the concrete wire protocol DTOs and dispatcher admission listener logic do NOT currently exist in repository sources. Establishing this contract requires a dedicated, separately reviewed workorder (`WO-DISPATCHER-IPC-CONTRACTS-R1`). Until that workorder is accepted, **the Dispatcher admission boundary remains strictly non-operational**.

### 7.4 Complete Two-Phase Failure and Recovery Matrix

| Phase & Failure Mode | Root Cause & Detection Point (Step) | Precise Error Code | Immediate Operational Disposition | Safety Invariant Impact |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1: Verifier Unavailable / Crash** | Preflight verifier process crashes, encounters IPC timeout, or fails during read-only Resolve inspection. | `ERR_GATE4_VERIFIER_UNAVAILABLE` | Evidence production halts. No signed envelope is emitted. Execution runner aborts before Gate 5. | Zero timeline mutation; zero partial execution state created. |
| **Phase 1: Predicate Failure** | Any of the 5 boolean predicates is `False` during read-only inspection (e.g. non-empty timeline, missing Track 1). | `ERR_GATE4_PREDICATE_UNSATISFIED` | **Immediate fail-closed stop; no signed envelope emitted; runner aborts. Target state preserved untouched for incident diagnosis. No automated repair, operator timeline edit, or target mutation is authorized. Any target reconfiguration or timeline reset strictly requires a separate explicit workorder.** | Protects non-empty timelines and preserves incident evidence. |
| **Phase 2: Missing Trust Anchor** | Public key file `/etc/resolveflow/preflight_verifier_public_key.pem` does not exist (`ENOENT`) (Step 2). | `ERR_GATE4_TRUST_ANCHOR_MISSING` | Immediate process exit upon Class A read failure; stops before opening Class B/C paths, before socket IPC, before Resolve calls, before journal creation. | Target remains untouched and empty. |
| **Phase 2: Insecure Trust Anchor Permissions** | Trust anchor file is group/world-writable, symlink, or not owned by `root:root` (Step 2). | `ERR_GATE4_TRUST_ANCHOR_PERMISSIONS_INSECURE` | Immediate fail-closed abort upon descriptor validation; security alert raised; stops before Class B/C paths, sockets, or Resolve access. | Prevents local privilege escalation or key substitution. |
| **Phase 2: Invalid Signature** | Cryptographic verification fails against the canonical 20-field JCS payload (Step 3). | `ERR_GATE4_SIGNATURE_INVALID` | Immediate rejection; dispatch denied; candidate harness halts before Class B/C paths, sockets, or Resolve access. | Zero journal mutation; zero Resolve call. |
| **Phase 2: Expired Evidence** | Wall clock $\tau_{\text{now}} > \text{valid\_until\_iso}$ ($> 1800\text{s}$ window) (Step 3). | `ERR_GATE4_EVIDENCE_EXPIRED` | Rejection; requires fresh preflight inspection cycle; stops before Class B/C paths, sockets, or Resolve access. | Prevents stale attestation from clearing dispatch. |
| **Phase 2: Future-Dated Evidence** | Wall clock $\tau_{\text{now}} < \text{verified\_at\_iso} - 5.0\text{s}$ (clock skew anomaly) (Step 3). | `ERR_GATE4_EVIDENCE_FUTURE` | Rejection; system clock synchronization required; stops before Class B/C paths, sockets, or Resolve access. | Prevents premature token admission. |
| **Phase 2: Target Identity Mismatch** | `target_project_uid` or `target_timeline_uid` does not match pinned trial constants (Step 1). | `ERR_GATE4_TARGET_MISMATCH` | Immediate in-memory rejection; incident alert triggered; zero host path access. | Enforces strict canary target confinement. |
| **Phase 2: Dispatcher Peer Untrusted** | Runner inspects `SO_PEERCRED` upon connect; peer process UID is not `902` or process GID is not `902` (§7.3). | `ERR_DISPATCHER_PEER_UNTRUSTED` | Immediate socket close; fails closed before transmitting request or interpreting response; stops before Class B/C mutation. | Prevents rogue daemon from spoofing admission or falsely confirming evidence consumption. |
| **Phase 2: Gate 5 Validation Failure** | Gate 5 workorder missing, invalid signature, or Blocker B unresolved (Step 4). | `ERR_GATE5_EXECUTION_UNAUTHORIZED` | **Rejection in Dispatcher pre-admission gate; stops BEFORE Class B replay ledger access or append. Preflight evidence is NOT burned.** | Preserves valid preflight evidence for future authorized runs. |
| **Phase 2: Replay Ledger Lock Contention** | Dispatcher cannot acquire exclusive `flock(LOCK_EX | LOCK_NB)` on Class B ledger (Step 5). | `ERR_PREFLIGHT_LEDGER_LOCKED` | Immediate fail-closed abort; stops before append, before Class C paths, before sockets, before Resolve mutation. | Prevents concurrent race conditions. |
| **Phase 2: Replayed Evidence** | `preflight_run_id` matches previously recorded run in Class B ledger under lock (Step 5). | `ERR_GATE4_EVIDENCE_REPLAYED` | Rejection inside Dispatcher daemon; stops before Class B append, before opening Class C paths, before sockets, before Resolve mutation. | Eliminates replay vulnerabilities. |
| **Phase 2: Replay Ledger Corrupted** | Replay ledger file has invalid JSON, duplicate keys, or torn tail (Step 5). | `ERR_PREFLIGHT_LEDGER_CORRUPT` | Fail-closed abort; stops before Class B append, before Class C paths, before sockets, before Resolve mutation. | Prevents bypass via ledger corruption. |
| **Phase 2: Consumption Commit Failure** | Disk full or `fsync()` fails when appending `preflight_run_id` to Class B ledger (Step 5). | `ERR_PREFLIGHT_LEDGER_PERSISTENCE_FAILED` | Fail-closed abort; Gate 4 not cleared; stops before Class C paths, before sockets, before Resolve mutation. | Guarantees durable consumption before clearance. |
| **Phase 2: Verifier Key Compromise** | Verifier key ID present in revocation ledger or tombstone marker (Step 3). | `ERR_GATE4_VERIFIER_KEY_REVOKED` | Immediate halt; administrator must rotate keys and re-verify; stops before Class B/C paths, sockets, or Resolve access. | Quarantines compromised cryptographic authority. |

---

## 8. Decision DAG & Next Dependencies (§3.7)

### 8.1 Post-Incident Dependency DAG
The following directed acyclic graph illustrates the positioning of this design within the overall Gate 6 post-incident hardening framework:

```
[Phase 2 Boundary Design R8 (ACCEPTED)]
       │
       ├───► [Receipt/WAL Binding Design R3 (ACCEPTED)]
       │            │
       │            └───► [Post-Incident Runbook R5 (ACCEPTED)]
       │                         │
       │                         └───► [G07-07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1 (THIS WORKORDER)]
       │                                      │
       │                                      ▼ (DESIGN ACCEPTANCE DOES NOT CLOSE BLOCKER A)
       │                               [Blocker A: Gate 4 Authority DESIGN ONLY]
       │                                      │
       ├──────────────────────────────────────┼──────────────────────────────────────┐
       │                                      │                                      │
       ▼                                      ▼                                      ▼
[Blocker B: Gate 5 Authority]          [Blocker C: Oracle Orphans]            [Blocker D: Host DAC]
  (STRICTLY UNRESOLVED)                  (STRICTLY UNRESOLVED)                 (STRICTLY UNRESOLVED)
       │                                      │                                      │
       └──────────────────────────────────────┼──────────────────────────────────────┘
                                              │
                                              ▼
                              [ALL BLOCKERS UNRESOLVED]
                                              │
                                              X  <─── [DISCONNECTED / NO EDGES]
                                              │
                                              ▼
                             [WO5: Live Canary Execution Directive]
```

### 8.2 Architectural Reality of Blocker A
Acceptance of this design document **does NOT close Blocker A**. It establishes the design baseline, defines the missing custody choices, and specifies the verification contracts. Blocker A remains open until:
1. Verifier private key custody is formally decided and provisioned under a mechanism ensuring operational separation.
2. Verifier public key trust anchor is provisioned on the host.
3. Preflight runner and consumption verification logic are implemented and tested.
4. The Dispatcher Admission IPC contract is formally defined, implemented, and accepted.

### 8.3 Required Follow-On Workorders
Before Gate 4 preflight authority can become operational, the following discrete, sequentially bounded workorders must be issued and accepted:

1. **`WO-GATE4-CUSTODY-POLICY-R1` (Governance Workorder):**
   - Formally decide the private key custody model for `verifier_key_id`.
   - Specify whether the verifier uses a hardware token, offline ceremony, or remote signing service.

2. **`WO-DISPATCHER-IPC-CONTRACTS-R1` (Wire Contracts Workorder):**
   - Formally specify the `DispatcherAdmissionRequest` and `DispatcherAdmissionResponse` DTOs, endpoint path (`/run/resolveflow/dispatcher_client/dispatcher.sock`), length-prefixed framing, and `SO_PEERCRED` UID 1000 peer authentication.

3. **`WO-GATE4-PROVISIONING-SPEC-R1` (Host DAC & Packaging Specification):**
   - Specify the exact packaging and provisioning scripts for deploying `/etc/resolveflow/preflight_verifier_public_key.pem` with mode `0644` owned by `root:root`.
   - Reconcile with Blocker D (Host DAC provisioning).

4. **`WO-GATE4-HARNESS-IMPLEMENTATION-R1` (Source Implementation & Unit Tests):**
   - Implement `_load_and_validate_preflight_envelope()` in the candidate runner using descriptor-anchored trust root loading.
   - Implement atomic compare-and-consume replay handling in `DurableDispatchJournal` (`consumed_preflight_runs.jsonl`) under `_resolveflow_dispatcher:902`.
   - Add comprehensive unit tests in `tests/` with fake keys, concurrent processes, and temporary directories.

5. **`WO-GATE4-KEYGEN-PROCEDURE-R1` (Key Generation & Ceremony Workorder):**
   - Supervised, audited key generation procedure for the preflight verifier keypair.

---

## 9. Governance Recommendation (§3.8)

### 9.1 Summary Recommendation
It is recommended that Technical Lead Codex:
1. **Accept this document as `ACCEPTED_FOR_DESIGN_ONLY`:** Formally approve Revision R7 as the definitive architectural design for Gate 4 preflight evidence authority, trust bootstrap, and key lifecycle.
2. **Maintain Strict Block on Execution:** Reaffirm that acceptance of this design does NOT resolve Blocker A, does NOT resolve Blockers B, C, or D, does NOT authorize key generation, does NOT authorize host provisioning, and does NOT connect Node `WO5`.
3. **Preserve Incident Invariants:** Maintain `ready_to_execute = False`, keep Gate 6 strictly `NOT ACCEPTED` / incident-only, and keep Gate G07 and Milestones M1, M2, and M3 strictly `OPEN`.
4. **Sequencing Next Steps:** Await Tech Lead direction on whether to proceed next to Blocker B (Gate 5 Release Authority Design), Blocker C (Oracle Orphan Governance Design), or Blocker D (Host DAC Provisioning Review).

---

## 10. Audit & Traceability Artifacts

- **Exact Source Contracts Inspected (Current Workspace Hashes):**
  - `src/core/contracts/signed_preflight_contracts.py` (SHA-256: `b71b34fc12b71a98d0580a32ec98f367b47a02d52721598001adf421c5f04ab4`)
  - `src/core/contracts/signed_operator_consent_envelope.py` (SHA-256: `a6cad18a01c7d374e4b1e3ceec27670a912bee5de42c7e46de5da56a9209fb36`)
  - `src/core/contracts/signed_execution_workorder_contracts.py` (SHA-256: `5779d398726efa9361d1cd2198a4fa3c5a657e20ef126c7778128da9e736a62d`)
- **Core Dispatcher & Nonce Services Inspected:**
  - `src/services/durable_dispatch_journal.py` (SHA-256: `0d08297e36d100a2800c29f2f33c7f0ed629f1506fefafc2465153878284cf8a`)
  - `src/services/durable_nonce_ledger.py` (SHA-256: `bc389ae78db49acdeb53c2da2909463c43912d4000ce2898741944b421c0163d`)
  - `src/services/live_canary_dispatch_service.py` (SHA-256: `bbcd3652f4d54255a8c6dd4d24550543499344635dfc3a64c1600301b4295f0a`)
- **Governing Architecture References:**
  - `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md` (Revision R8)
  - `docs/architecture/07B7-GATE6-DEP04-UPSTREAM-R1-dependency-resolution-design.md` (Revision R4)
  - `docs/architecture/07B7-GATE6-POST-INCIDENT-RECONCILIATION-RUNBOOK-R1.md` (Revision R5)
