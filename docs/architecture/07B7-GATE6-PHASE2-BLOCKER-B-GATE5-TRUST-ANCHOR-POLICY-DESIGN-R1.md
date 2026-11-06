# Gate 6 Phase 2 Blocker B: Gate 5 Trust Anchor Policy & Ceremony Specification (Revision R3)

**Document ID:** `07B7-GATE6-PHASE2-BLOCKER-B-GATE5-TRUST-ANCHOR-POLICY-DESIGN-R1`  
**Revision:** `R3`  
**Workorder:** `G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-TRUST-ANCHOR-POLICY-DESIGN-R1`  
**Date:** 2026-11-05  
**Parent:** Plan 07B7 Phase 2 Hardening, Blocker B (Gate 5 Release Authority). Follows accepted Gate 5 Release Authority Design R4 (`07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md`), accepted Gate 5 Readiness Assessment R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-READINESS-ASSESSMENT-R1.md`), accepted Gate 4 Authority Design R10, accepted Gate 4 Custody Policy Design R4, and accepted Phase 2 Blocker Status Checkpoint R2.  
**Mode:** `REPOSITORY_ONLY_STATIC_POLICY_DESIGN; READ_ONLY_INPUTS; NO_SOURCE_OR_CONFIG_EDITS; NO_TESTS_OR_IMPORTS_OR_COMPILATION; NO_KEY_GENERATION_OR_KEY_CONTENT_ACCESS; NO_TOKEN_OR_CREDENTIAL_INTERACTION; NO_HOST_OR_PRODUCTION_STATE_ACCESS; NO_SYSTEMD_LIFECYCLE; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`.

---

## 1. Executive Summary, Authority Triad & Scope Boundary

Under Plan 07B7 post-incident recovery, Blocker B governs the establishment of Gate 5 Technical Lead Release Authority. Gate 5 ensures that no candidate execution workflow can mutate the DaVinci Resolve target without explicit, cryptographic engineering authorization from the Technical Lead.

### 1.1 The Authority Triad & Gate 5 Clearance Mandate
The post-incident architecture enforces three orthogonal, independent authorization gates prior to Signer Oracle capability invocation:
1. **Gate 2 (Human & Creative Governance):** Dual human consent consisting of `SignedApprovalEnvelope` (Creative Director trial consent) and `SignedOperatorConsentEnvelope` (Human Operator physical presence and empty-target visual confirmation).
2. **Gate 4 (Independent Technical Preflight):** Automated preflight verification captured in `SignedPreflightEvidenceEnvelope`, attesting to the 5 live DaVinci Resolve state predicates at inspection time $\tau_{\text{preflight}}$.
3. **Gate 5 (Engineering Release Clearance):** Technical Lead cryptographic signoff captured in `SignedExecutionWorkorderRecord`, attesting to the exact candidate code digest (`reviewed_candidate_sha256`), execution plan digest (`plan_digest_sha256`), action payload digest (`action_payload_digest_sha256`), permitted invocation command (`permitted_command`), and target project/timeline UIDs within a strictly bounded validity window ($\le 3600.0\text{ s}$).

### 1.2 Strict Statement Classification Taxonomy
In adherence to Workorder Section 3, all statements and assertions throughout this specification are explicitly classified into one of five categories:
- **[ACCEPTED-DESIGN]:** Formally accepted requirements specified in accepted architecture documents (e.g. Gate 5 Design R4, Gate 4 Design R10, DEP-01 R4 Amended V3).
- **[IMPLEMENTED-CONTRACT]:** Cryptographic and schema behaviors verified in checked-in source code (e.g. `src/core/contracts/`).
- **[PACKAGING-DECLARATION]:** Declarations in host setup or verification scripts (e.g. `deploy/scripts/`); facts about configuration code, not proof of host state.
- **[PROPOSAL]:** Architectural policies and procedural designs proposed in this document to resolve remaining prerequisites.
- **[UNRESOLVED-PREREQUISITE]:** Governance, host-state, or implementation gaps that remain strictly open and require future authorized workorders.

### 1.3 Non-Transitivity of Gate Custody Governance
**[ACCEPTED-DESIGN]:** Governance decisions, approvals, and quorum rules across gates are strictly non-transitive:
- Gate 4 custody options analysis (`07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1.md`) and its governance quorum model govern **solely** the Gate 4 Preflight Verifier key.
- Approval or adoption of a Gate 4 custody model does **not** govern, select, or activate Gate 5 Technical Lead key custody.
- Gate 5 Technical Lead release authority operates out-of-band and possesses distinct cryptographic identity, custody boundaries, and verification targets.

### 1.4 Scope Limitations & Invariants
This document is a static policy and ceremony specification only:
- Zero private or public keys are generated, accessed, converted, or installed.
- Zero host state, credentials, hardware tokens, or filesystems are inspected or modified.
- Blocker B remains strictly **OPEN / UNRESOLVED**.
- Safety invariants remain fully intact: `ready_to_execute = False`, Gate 6 is `NOT ACCEPTED` / incident-only, Gate G07 and Milestones M1, M2, and M3 remain `OPEN`, and Node `WO5 (Live Canary Execution Directive)` remains completely isolated with zero edges.

---

## 2. Accepted Trust-Anchor Path vs. Packaging Declarations vs. Candidate Harness Divergence

### 2.1 Canonical Path & Metadata Policy
- **[ACCEPTED-DESIGN]:** Accepted Gate 5 Release Authority Design R4 (§3.1, §4.1) establishes the canonical Class A host trust anchor for Gate 5:
  ```
  /etc/resolveflow/tech_lead_public_key.pem
  ```
- **[ACCEPTED-DESIGN]:** The required filesystem metadata policy for this trust anchor is:
  - **Ownership:** Strictly `root:root` (POSIX UID `0`, GID `0`).
  - **Permissions:** Strictly mode `0644` (`-rw-r--r--`).
  - **File Type:** Strictly regular file (`stat.S_ISREG(st_mode) == True`).
  - **Link Count:** Strictly 1 (`st_nlink == 1`; hardlinks prohibited).
  - **Symlinks:** Strictly prohibited (`stat.S_ISLNK(st_mode) == False`; opened with `O_NOFOLLOW`).
- **[UNRESOLVED-PREREQUISITE]:** Host state was not inspected under this workorder; repository evidence does not establish installation; host state is unknown and remains an uninspected, unverified prerequisite (GATE5-TRUST-POLICY-R1-01).

### 2.2 Host Packaging Artifact Declarations
1. **Administrative Provisioning Script (`deploy/scripts/provision_resolveflow_principals.sh`):**
   - **[PACKAGING-DECLARATION]:** Declares `KNOWN_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}` (lines 258, 511).
   - **[PACKAGING-DECLARATION]:** Provisions parent directory `/etc/resolveflow` (Owner `0:0`, Mode `0755`).
   - **[PACKAGING-DECLARATION]:** Explicit non-installation policy (lines 508–509):
     ```bash
     # 2. Provision /etc/resolveflow trust-root directory (Owner 0:0, Mode 0755)
     # Does NOT generate, copy, install, replace, remove, parse, or read any key contents.
     ```
   - **[PACKAGING-DECLARATION]:** The script explicitly contains zero public key bytes, does not write files into `/etc/resolveflow`, and provides no key ceremony capability.
2. **Read-Only DAC Verifier Script (`deploy/scripts/verify_resolveflow_dac.sh` Revision R8):**
   - **[PACKAGING-DECLARATION]:** Declares `KNOWN_TRUST_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}` (lines 446–450).
   - **[PACKAGING-DECLARATION]:** Checks metadata descriptor-relatively if present (`0644`, `0:0`, `S_ISREG`, `nlink == 1`) without reading key bytes.
   - **[PACKAGING-DECLARATION]:** If absent, reports: `Trust anchor '/etc/resolveflow/tech_lead_public_key.pem' is absent (unprovisioned)` (line 514).
   - **[PACKAGING-DECLARATION]:** Explicitly fails closed if `/etc/resolveflow/revoked_keys.jsonl` exists (line 480).
3. **Artifact Fact vs. Host State:**
   - **[PACKAGING-DECLARATION]:** The scripts' allowlists represent repository configuration declarations only. They do not constitute evidence that the host filesystem contains the key.

### 2.3 Candidate Runner Path Divergence Analysis
1. **Observed Harness Path:**
   - **[IMPLEMENTED-CONTRACT]:** In `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py` (lines 167–169), the candidate harness defines:
     ```python
     PINNED_GATE5_RELEASE_KEY_PATH = Path(
         "/var/lib/resolveflow/signer/trusted_roots/gate5_release_pubkey.pem"
     )
     ```
2. **Structural & Security Conflicts:**
   - **[ACCEPTED-DESIGN]:** *Violation of Signer Packaging Policy:* Under DEP-01 §2.1 and DEP-02A R4, direct subdirectories of `/var/lib/resolveflow/signer` are strictly constrained to the set `{'keystore', 'nonces'}`. Any path containing `trusted_roots` under signer state violates accepted packaging invariants and is rejected fail-closed by DAC verifiers.
   - **[ACCEPTED-DESIGN]:** *Blocked by Daemon Sandboxing:* In `deploy/systemd/resolveflow-dispatcher.service`, systemd sandbox rules explicitly enforce:
     ```ini
     InaccessiblePaths=/var/lib/resolveflow/signer
     ```
     The Dispatcher daemon (`_resolveflow_dispatcher:902`) cannot read any file under `/var/lib/resolveflow/signer`.
   - **[ACCEPTED-DESIGN]:** *Security Role Separation Breach:* Co-locating Gate 5 release trust roots under the Signer state directory collapses the isolation boundary between the Signer Oracle (`901`) and the Dispatcher consumer (`902`).
3. **Helper Reachability in Candidate Entrypoint:**
   - **[IMPLEMENTED-CONTRACT]:** As established in Gate 5 Readiness Assessment R3 (§3.2 item 4), the validation helper `verify_signed_execution_workorder_record()` in `run_gate6.py` resides entirely within unreachable downstream code.
   - **[IMPLEMENTED-CONTRACT]:** The candidate entrypoint `run_harness()` unconditionally raises `Gate6HarnessSecurityError("ERR_GATE6_PREPARATION_ONLY: ...")` fail-closed as its very first statement (lines 637–644). The helper does not run and cannot execute through the candidate entrypoint.
4. **Realignment Requirement:**
   - **[PROPOSAL]:** A follow-on repository workorder must realign `run_gate6.py` to reference `/etc/resolveflow/tech_lead_public_key.pem` once the entrypoint and verifier architecture are authorized. No edits to `run_gate6.py` are authorized or made under this workorder.

---

## 3. Cryptographic Role Separation & The Five Core Distinctions

To prevent security vulnerabilities and architectural conflation, Gate 5 cryptographic management strictly distinguishes five orthogonal concepts:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 FIVE ORTHOGONAL CRYPTOGRAPHIC BOUNDARIES                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Key Identifier (tech_lead_key_id):                                       │
│    - Human-readable canonical string identifying the active key version.    │
│    - Binds record to key epoch; NOT secret, NOT proof of authority.        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2. Public-Key Fingerprint:                                                  │
│    - SHA-256 digest of RFC 5280 SubjectPublicKeyInfo (SPKI) DER bytes.     │
│    - Used in ceremony logs, audit records, and verifier cross-checking.    │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3. Private-Key Custody:                                                     │
│    - Physical/logical environment where Ed25519 seed/private key is held.   │
│    - Custody model unresolved (Hardware token vs Air-gapped station).       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4. Trust-Anchor Distribution:                                               │
│    - Controlled placement of public key PEM on host (/etc/resolveflow/).    │
│    - Root-owned, world-readable (0644), descriptor-checked by Dispatcher.   │
├─────────────────────────────────────────────────────────────────────────────┤
│ 5. Runtime Signature Verification:                                          │
│    - Mathematical RFC 8032 PureEd25519 verification over RFC 8785 JCS byte  │
│      sequence, executed by Dispatcher verifier using the trust anchor.     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Mathematical PureEd25519 Signature Verification
- **[IMPLEMENTED-CONTRACT]:** `src/core/contracts/signed_execution_workorder_contracts.py` defines `SignedExecutionWorkorderRecord` with 14 fields.
- **[IMPLEMENTED-CONTRACT]:** The signing payload is serialized over exactly 13 fields (excluding `tech_lead_signature`) using RFC 8785 JSON Canonicalization Scheme (JCS) via `canonical_json_bytes()`:
  $$\text{SigningPayload} = \text{JCS}(\text{Record} \setminus \{ \text{"tech\_lead\_signature"} \})$$
- **[ACCEPTED-DESIGN]:** The verification equation evaluated by the Dispatcher daemon is:
  $$\text{PureEd25519Verify}(\text{PubKey}_{\text{tech\_lead}}, \text{SigningPayload}, \text{Bytes}(\text{tech\_lead\_signature})) \equiv \text{True}$$
- **[ACCEPTED-DESIGN]:** The cryptographic signature authenticates the signed claims **if and only if** the verifying public key $\text{PubKey}_{\text{tech\_lead}}$ is loaded from an independently established, descriptor-verified Class A trust anchor (`/etc/resolveflow/tech_lead_public_key.pem`).
- **[UNRESOLVED-PREREQUISITE]:** *Key-ID-to-Public-Key Binding Limitation (GATE5-TRUST-POLICY-R1-04):* An RFC 5280 SubjectPublicKeyInfo (SPKI) PEM file contains only the cryptographic algorithm identifier (Ed25519) and the 32-byte raw public key; it contains **no key identifier field**. Evaluating whether `record.tech_lead_key_id` matches the public key cannot be performed from the PEM file alone. Establishing a key-ID-to-public-key binding mechanism (e.g. metadata sidecar, manifest mapping, strict fingerprint derivation convention, or certificate envelope) is an unresolved policy and verifier prerequisite.

### 3.2 Six-Role Pairwise Key Separation Invariant
- **[ACCEPTED-DESIGN]:** Gate 5 Release Authority Design R4 (§2.3) enforces absolute pairwise key distinctness across the six cryptographic roles in Plan 07B7:
  $$\forall i \ne j: \quad \text{KeyID}_i \ne \text{KeyID}_j \quad \land \quad \text{KeyMaterial}_i \ne \text{KeyMaterial}_j$$
- **[ACCEPTED-DESIGN]:** The Technical Lead release key must never share key identifiers, raw key bytes, public key fingerprints, or private key custody with:
  1. Gate 4 Preflight Verifier (`verifier_key_id`)
  2. Gate 2 Human Operator (`operator_key_id`)
  3. Gate 2 Creative Director (`director_key_id`)
  4. Signer Oracle Clearance (`oracle_key_id`)
  5. Mutation Capability Signer HMAC (`trial_signer_key_id`)

---

## 4. Custody-Neutral Key Ceremony Specification & Lifecycle Policy (Design Proposals)

The following procedures specify a formal, repeatable key lifecycle for the Technical Lead release key. **This is a specification of future controlled procedures; no key ceremony, key generation, or custody activation occurs under this workorder.**

### 4.1 Unresolved Custody Model & Custody-Neutral Ceremony Framework (GATE5-TRUST-POLICY-R1-02)
- **[UNRESOLVED-PREREQUISITE]:** The custody model for the Technical Lead private key is **unresolved and not selected by this document**. Under accepted Gate 5 Design R4 (§5.1), multiple viable custody architectures exist:
  1. *Hardware Token Custody (Model 1):* Dedicated cryptographic token (FIPS 140-2 Level 3 / 140-3, e.g. YubiKey 5 Series PIV or OpenPGP smartcard) requiring physical capacitive touch and PIN per signature.
  2. *Air-Gapped Workstation Custody (Model 2):* Dedicated, offline development machine held by the Technical Lead with isolated CSPRNG and encrypted storage.
- **[PROPOSAL]:** A custody-neutral ceremony framework establishes common invariants across both models:
  - Private key generation occurs strictly within the designated custody boundary; raw seed material is never exposed to general-purpose editing workstation filesystems or networks.
  - The ceremony generates an Ed25519 keypair specifically bound to the Gate 5 release authority role.
  - Ceremony execution requires an authorized ceremony workorder (`WO-GATE5-CEREMONY-EXECUTION-R1`) and prior governance approval of the selected custody model.

### 4.2 Illustrative Ceremony Roles & Procedure (Unapproved Proposals)
- **[PROPOSAL]:** The following multi-party ceremony procedure represents an **illustrative proposal** requiring separate governance authorization:
  - **Ceremony Administrator (Technical Lead):** Executes key generation on the approved custody device (e.g. hardware token or offline machine); validates non-exportability attributes.
  - **Audit Witness (Proposal - Unapproved Role):** Observes key generation out-of-band; attests that private key material was not exported, displayed, or logged; signs the ceremony audit manifest.
  - **Host System Administrator (Proposal - Unapproved Procedure):** Receives the exported public key SPKI PEM out-of-band; verifies its SHA-256 fingerprint against the ceremony manifest; performs atomic installation on the target host under an authorized provisioning workorder.

```
                  ┌────────────────────────────────────────────────────────┐
                  │   CUSTODY-NEUTRAL KEY GENERATION CEREMONY (PROPOSAL)   │
                  └────────────────────────────────────────────────────────┘
                                               │
                        ┌──────────────────────┴──────────────────────┐
                        ▼                                             ▼
            [Option A: Hardware Token]                    [Option B: Air-Gapped Machine]
            - Key generated in secure element             - Key generated in offline memory
            - PIN & capacitive touch required             - Passphrase-encrypted storage
            - Non-exportable (CKA_EXTRACTABLE=FALSE)      - Strict offline perimeter
                        └──────────────────────┬──────────────────────┘
                                               │
                                               ▼
                              Exports ONLY SubjectPublicKeyInfo PEM
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ Audit Witness & Manifest│ (Unapproved proposal)
                                  └─────────────────────────┘
                                               │
                                               ▼ Out-of-band delivery
                                  ┌─────────────────────────┐
                                  │ Host System Admin       │ (Unapproved procedure)
                                  └─────────────────────────┘
                                               │ Atomic install (0644, 0:0)
                                               ▼
                                  /etc/resolveflow/
                                  tech_lead_public_key.pem
```

### 4.3 Key Identifiers, Formats & Metadata
- **[PROPOSAL]:** *Key Identifier Grammar:* Must conform strictly to `src/core/contracts/signed_execution_workorder_contracts.py` grammar (`^[a-z0-9_-]{8,64}$`):
  $$\text{tech\_lead\_key\_id} = \text{"g5-techlead-" } + \text{YYYYMMDD} + \text{"-" } + \text{hex\_fingerprint}[0:8]$$
  *Example:* `g5-techlead-20261105-a1b2c3d4`
- **[PROPOSAL]:** *Public Key Encoding:* Strict RFC 5280 SubjectPublicKeyInfo (SPKI) in PEM format (`-----BEGIN PUBLIC KEY-----`), containing exactly 32 raw Ed25519 public key bytes.
- **[PROPOSAL]:** *Non-Exportability Invariant:* Private key material must never touch general-purpose workstation filesystems, temporary directories, or memory swap.

### 4.4 Rotation Mechanics, Single-Anchor Semantics & Cutover Policy (GATE5-TRUST-POLICY-R1-03)
- **[PROPOSAL]:** *Maximum Validity Period:* A Gate 5 release key has an active issuance lifetime not exceeding 90 calendar days from the ceremony date.
- **[ACCEPTED-DESIGN]:** *Single-Anchor & Immediate Invalidation Invariant:*
  - Gate 5 architecture specifies exactly one Class A host trust anchor (`/etc/resolveflow/tech_lead_public_key.pem`) and explicitly prohibits multi-key fallback chains or grace periods.
  - Consequently, replacing the public key trust anchor on the host **immediately invalidates any outstanding, unconsumed release workorders** signed by the rotated key.
- **[PROPOSAL]:** *Cutover & Reissuance Policy:*
  - Prior to key rotation, all active, unexpired release workorders signed under the retiring key must either be consumed to completion or explicitly abandoned.
  - Any workflow admitted after trust-anchor rotation requires a fresh `SignedExecutionWorkorderRecord` reissued by the Technical Lead under the newly installed key epoch.
- **[PROPOSAL]:** *Atomic Replacement Mechanism & Staging Prerequisites (GATE5-TRUST-POLICY-R2-01):*
  - To prevent partial reads or torn-state windows, trust-anchor replacement by host administration must use atomic POSIX replacement (`rename()` / `renameat()`) on the same filesystem.
  - *Distinction Between Directory and File Metadata:* If an administrative staging directory is used, it must possess valid traversal and search permissions (e.g. mode `0700` or `0755`, owner `root:root`), whereas the staged candidate regular file itself must possess mode `0644` (owner `root:root`). Mode `0644` cannot be applied to a directory, as the absence of execute/search bits (`+x`) prevents pathname traversal and access to child files.
  - *Unresolved Staging Specification:* The exact staging location (e.g. a same-directory temporary file within `/etc/resolveflow` versus a dedicated staging subdirectory), its directory permissions, and the replacement script implementation remain unapproved proposals. They are formally left **unresolved** for a future dedicated host packaging and provisioning workorder (`WO-GATE5-PROVISIONING-SPEC-R1` reconciled with Blocker D).
  - Flag `renameat2(..., RENAME_NOREPLACE)` must **not** be used for replacement, as it strictly refuses to overwrite existing destination files.

### 4.5 Emergency Compromise Response & Severing Principle (GATE5-TRUST-POLICY-R1-06)
- **[ACCEPTED-DESIGN]:** *Architectural Root-Anchor Severing Concept:*
  - In accepted Gate 5 Design R4 (§5.4), emergency revocation is architecturally defined by the immediate removal of the host trust anchor `/etc/resolveflow/tech_lead_public_key.pem` by host administration.
- **[PROPOSAL]:** *Future Target Verifier Behavior:*
  - When the Dispatcher admission verifier is implemented under future workorders, it will open the trust anchor descriptor-relatively with `O_RDONLY | O_NOFOLLOW` on each admission request.
  - In that future runtime state, removal of the trust anchor will cause all subsequent admission requests to fail closed with `ERR_GATE5_TRUST_ANCHOR_MISSING`.
  - *Boundary Limitation:* Because no operational Dispatcher admission daemon or verifier currently exists in the repository, per-request loading and instant rejection are future target design properties, not currently functioning behaviors.
  - Deletion commands are not authorized or executed under this documentation-only workorder.

---

## 5. Revocation Architecture & Unresolved Ledger Governance

### 5.1 Accepted Architecture Design Specification
- **[ACCEPTED-DESIGN]:** Gate 5 Release Authority Design R4 (§5.4, §7) describes a tombstone revocation ledger located at `/etc/resolveflow/revoked_keys.jsonl` to record compromised key IDs and prevent replay of unexpired workorders.

### 5.2 Current Packaging Rejection & Fail-Closed Behavior
- **[PACKAGING-DECLARATION]:** In checked-in provisioning and verifier scripts (`provision_resolveflow_principals.sh:263,514` and `verify_resolveflow_dac.sh:480`), the presence of `revoked_keys.jsonl` is explicitly handled as an unapproved, fail-closed condition:
  ```bash
  # Pre-existing '/etc/resolveflow/revoked_keys.jsonl' encountered
  # (unresolved packaging prerequisite; fails closed)
  ```
- **[PACKAGING-DECLARATION]:** The scripts halt immediately with an exit code 1 if `revoked_keys.jsonl` exists in `/etc/resolveflow`.

### 5.3 Explicit Unresolved Status
- **[UNRESOLVED-PREREQUISITE]:** `revoked_keys.jsonl` remains an **unresolved packaging and governance prerequisite**:
  1. **DAC Ownership & Permissions:** No POSIX owner, group, or mode (e.g. `root:root 0644` vs `root:_resolveflow_dispatcher 0640`) is approved in any accepted packaging manifest.
  2. **Consumption Semantics:** No Dispatcher admission daemon code exists to parse, validate, or query this ledger.
  3. **Append Authority:** No mechanism or authority is approved defining who (root, Tech Lead, Security Officer) may append tombstone records.
- **[PROPOSAL]:** Pending formal governance and packaging approval of `revoked_keys.jsonl`, trust-root removal (`/etc/resolveflow/tech_lead_public_key.pem`) serves as the primary, fail-closed emergency revocation concept in the design. This specification does **not** approve or create `revoked_keys.jsonl`.

---

## 6. Comprehensive Lifecycle & Verification Failure Matrix (GATE5-TRUST-POLICY-R1-05)

The following matrix specifies the architectural status, error taxonomy, and fail-closed dispositions for all Gate 5 verification scenarios. In all failure conditions, the target disposition is immediate abort with zero state mutation and zero fallback trust roots.

| Verification Scenario | Observed Condition & Technical Failure | Target Error Code | Governance Classification | Architectural & Safety Disposition |
| :--- | :--- | :--- | :--- | :--- |
| **Trust Anchor Missing** | File does not exist at `/etc/resolveflow/tech_lead_public_key.pem`. | `ERR_GATE5_TRUST_ANCHOR_MISSING` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; admission denied; zero mutation. Fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Trust Anchor Unsafe Inode** | Inode UID != 0, GID != 0, symlink, mode != 0644, or `st_nlink > 1`. | `ERR_GATE5_TRUST_ANCHOR_UNSAFE` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; security alert; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Trust Anchor PEM Invalid** | PEM format corrupt, unparseable, or invalid DER payload. | `ERR_GATE5_TRUST_ANCHOR_INVALID` | **[PROPOSAL]** (Sub-case of unsafe/unparseable anchor) | Immediate abort; fail closed. Target runtime verifier proposal; verifier currently unimplemented. |
| **Key Algorithm Divergence** | SPKI algorithm OID is not PureEd25519 (OID 1.3.101.112). | `ERR_GATE5_KEY_ALGORITHM_UNSUPPORTED`| **[PROPOSAL]** (Sub-case of invalid key encoding) | Immediate abort; fail closed. Target runtime verifier proposal; verifier currently unimplemented. |
| **Key ID Mismatch** | `record.tech_lead_key_id` != expected ID bound to trust anchor. | `ERR_GATE5_KEY_ID_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; fail closed. **Implementation unresolved:** SPKI PEM contains no key ID; check requires unapproved binding mechanism (R1-04). |
| **Key Revocation** | `tech_lead_key_id` listed in tombstone revocation ledger. | `ERR_GATE5_KEY_REVOKED` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; security alert; fail closed. **Implementation unresolved:** `revoked_keys.jsonl` unapproved in packaging; rejected by scripts. |
| **Signature Invalid** | PureEd25519 verification fails over 13-field canonical JCS payload. | `ERR_GATE5_SIGNATURE_INVALID` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; tampering alert; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Workorder Expired** | Verifier host time $\tau_{\text{dispatcher}} > t_{\text{valid\_until}}$. | `ERR_GATE5_WORKORDER_EXPIRED` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; fresh release required; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Clock Pre-dating** | Verifier host time $\tau_{\text{dispatcher}} < (t_{\text{issued\_at}} - 30.0\text{s})$. | `ERR_GATE5_WORKORDER_PREDATES_CLOCK` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; clock audit required; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Replay Detection** | `workorder_id` or `execution_id` exists in consumed journal. | `ERR_GATE5_WORKORDER_ALREADY_CONSUMED`| **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; duplicate rejected; fail closed. Target runtime verifier behavior; journal ledger currently unimplemented. |
| **Candidate Hash Mismatch** | SHA-256 of candidate runner artifact != `reviewed_candidate_sha256`. | `ERR_GATE5_CANDIDATE_HASH_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; unreviewed code rejected; fail closed. Pre-execution disk artifact check; does not attest in-flight process memory. |
| **Plan Digest Mismatch** | SHA-256 of execution plan != `plan_digest_sha256`. | `ERR_GATE5_PLAN_DIGEST_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; plan divergence rejected; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Action Digest Mismatch** | SHA-256 of canary action != `action_payload_digest_sha256`. | `ERR_GATE5_ACTION_DIGEST_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; action tampering rejected; fail closed. Target runtime verifier behavior; verifier currently unimplemented. |
| **Permitted Command Mismatch**| Invocation target command != `record.permitted_command`. | `ERR_GATE5_COMMAND_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Client-side runner preflight abort; fail closed. Cooperative runner adherence; not observable by Dispatcher under V1 wire contract. |
| **Target Project Mismatch** | `target_project_uid` != `4fd4272d-f0bc-445c-9490-5723cd6831c2`. | `ERR_GATE5_TARGET_PROJECT_MISMATCH` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; target mismatch rejected; fail closed. Target runtime verifier behavior; enforced at DTO boundary. |
| **Target Timeline Mismatch** | `target_timeline_uid` != `ba6a6775-8ec4-41a4-bf2f-704de902e68d`. | `ERR_GATE5_TARGET_TIMELINE_MISMATCH`| **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Immediate abort; target mismatch rejected; fail closed. Target runtime verifier behavior; enforced at DTO boundary. |
| **Dispatcher Offline** | Dispatcher admission AF_UNIX socket unreachable or times out. | `ERR_GATE5_DISPATCHER_UNAVAILABLE` | **[ACCEPTED-DESIGN]** (Gate 5 Design R4 §7) | Candidate runner aborts; zero mutation; fail closed. Daemon socket currently unimplemented. |

---

## 7. Remaining Unresolved Governance & Packaging Dependencies

To transition Blocker B towards operational resolution, the following specific items remain open and require future dedicated workorders:

1. **Governance Key Custody Model Decision:**
   - Formal selection and adoption of the Technical Lead key custody model (Hardware Security Token vs. Air-Gapped Offline Station).
   - Must be approved through affirmative governance records; cannot be selected unilaterally by this policy document.
2. **Key-ID-to-Public-Key Binding Specification (GATE5-TRUST-POLICY-R1-04):**
   - Specification of the technical mechanism binding `record.tech_lead_key_id` to the SPKI PEM trust anchor (e.g. metadata manifest sidecar, certificate envelope, or deterministic fingerprint derivation convention).
3. **Key Generation Ceremony Execution Workorder:**
   - Formal execution of the key generation ceremony under an authorized workorder (`WO-GATE5-CEREMONY-EXECUTION-R1`), producing the public key PEM and ceremony witness audit manifest.
4. **Packaging & DAC Provisioning Reconciliation (Reconciled with Blocker D):**
   - Updating packaging scripts (`provision_resolveflow_principals.sh`, `verify_resolveflow_dac.sh`) under an authorized administrative workorder to safely distribute the public key PEM into `/etc/resolveflow/tech_lead_public_key.pem`.
   - Resolving the DAC ownership, mode, and governance policy for `revoked_keys.jsonl`.
5. **Candidate Harness Path Realignment (`run_gate6.py`):**
   - Updating `PINNED_GATE5_RELEASE_KEY_PATH` in `run_gate6.py` to point to `/etc/resolveflow/tech_lead_public_key.pem`, resolving the obsolete legacy path `/var/lib/resolveflow/signer/trusted_roots/...`.
6. **Runtime Process-Binding Architecture & Supervised Launcher Design (Prerequisite B-2):**
   - Formal architecture specification for binding the admitted `reviewed_candidate_sha256` to the running process image at execution time (e.g. supervised launcher daemon, pinned file descriptor execution via `fexecve`, or immutable memory sealing).
7. **Dispatcher Admission Daemon Implementation (Prerequisite B-3):**
   - Implementation of the AF_UNIX admission listener in `_resolveflow_dispatcher:902`, consuming `DispatcherAdmissionRequest` and performing the verification protocol specified in Section 6.

---

## 8. DAG Alignment, Governance & Safety Invariants

- **Blocker Independence:** Blocker B is an independent architectural track under Plan 07B7 Phase 2. This policy design does not depend on, resolve, or modify:
  - **Blocker A (Gate 4 Preflight Custody):** Remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and `STRICTLY OPEN`.
  - **Blocker C (Orphan State Governance):** Prerequisite C-1 remains `STRICTLY OPEN`; C-2 remains non-operational implementation artifacts only.
  - **Blocker D (Host DAC Provisioning):** Prerequisite D-1 remains `OPEN / UNVERIFIED`.
- **Node WO5 Isolation:** Node `WO5 (Live Canary Execution Directive)` remains **COMPLETELY DISCONNECTED** in the execution DAG, with strictly zero incoming edges and strictly zero outgoing edges.
- **Safety Invariants Maintained:**
  - `ready_to_execute = False` is unconditionally preserved.
  - Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
  - Gate G07 and Milestones M1, M2, and M3 strictly remain `OPEN`.
  - Target project `RF_TRIAL_AEC8F0A7A365D5F5` and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` on `DISPLAY=:1` were outside the scope of this workorder and were not inspected or contacted.
  - Zero host modifications, zero key generation, zero credentials, zero network, zero socket IPC, and zero Resolve API calls occurred.
