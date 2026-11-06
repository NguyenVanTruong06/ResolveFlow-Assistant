# Gate 6 Phase 2 Gate 5 Tech Lead Release Authority Design

**Document ID:** `07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1`  
**Revision:** `R4`  
**Status:** `DESIGN_DOCUMENT_ONLY`  
**Parent Workorder:** `G07-07B7-GATE6-PHASE2-GATE5-AUTHORITY-DESIGN-R1`  
**Date:** 2026-11-05  
**Governing Standard:** Gate 4 Authority Design R10 (`07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md`), Gate 4 Custody Policy Design R1 Revision R4, Gate 4 Custody Decision Record R3, DEP-01 R4 Amended V3, DEP-02A/B/C, DEP-03 R5, DEP-04 UPSTREAM R1-R4, Dispatcher Admission Contracts R1 R2  
**Operating Mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY; NO_IMPLEMENTATION; NO_TESTS; NO_KEY_GENERATION_OR_ACCESS; NO_HOST_OR_STATE_ACCESS; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Executive Summary & Incident Recovery Context

### 1.1 Architectural Purpose & Gate 5 Role in Plan 07B7
Under Plan 07B7, the ResolveFlow subsystem is undergoing comprehensive post-incident hardening following an unauthorized live mutation incident. Gate 6 strictly remains `NOT ACCEPTED` and incident-only. Gate G07 and Milestones M1, M2, and M3 strictly remain `OPEN`. Node `WO5 (Live Canary Execution Directive)` in the post-incident dependency Directed Acyclic Graph (DAG) is completely disconnected with strictly zero incoming edges.

In the hardened four-gate authorization architecture:
1. **Gate 2 (Human & Creative Governance):** Dual human consent consisting of `SignedApprovalEnvelope` (Creative Director) and `SignedOperatorConsentEnvelope` (Human Operator).
2. **Gate 4 (Independent Technical Preflight):** Automated technical verification of target state captured in `SignedPreflightEvidenceEnvelope`.
3. **Gate 5 (Engineering Release Clearance):** Technical Lead signoff captured in `SignedExecutionWorkorderRecord`.
4. **Signer Oracle Mutation Capability Clearance:** Single-use cryptographic execution clearance over authenticated AF_UNIX IPC.

Gate 5 represents the **engineering release authority**. It provides cryptographic non-repudiation and authorization from the Technical Lead over the release artifacts: certifying the exact candidate code digest (`reviewed_candidate_sha256`), execution plan digest (`plan_digest_sha256`), action payload digest (`action_payload_digest_sha256`), permitted invocation command (`permitted_command`), and target project/timeline UIDs within a bounded validity window, conditioned upon signature verification by a trusted verifier against a root-anchored public key.

**Scope of Authority vs. Process Runtime Boundary:**
Gate 5 guarantees that the Technical Lead reviewed and cryptographically committed to the specific release parameters and artifact digests. Gate 5 does **not** assert kernel-level execution control or in-memory process attestation over the operating system process connecting to `_resolveflow_dispatcher`. As established in §3.5 and §3.7, runtime process binding is a separate system boundary governed by host access controls, runner isolation, or future supervised execution models.

### 1.2 The Release Authority Mandate: Clearance vs. Consent vs. Verification
It is critical to distinguish the orthogonal responsibilities of the three gate authorities:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           THE TRIAD OF AUTHORITY                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. GATE 2 (Human Governance):                                               │
│    - Creative Director: "This project and timeline are approved for trial." │
│    - Human Operator: "I am physically present and visually confirm empty    │
│      target on DISPLAY=:1."                                                 │
│                                                                             │
│ 2. GATE 4 (Technical Preflight):                                            │
│    - Preflight Verifier: "I have programmatically probed the live Resolve   │
│      state and verified the 5 technical predicates at time T."              │
│                                                                             │
│ 3. GATE 5 (Engineering Release):                                            │
│    - Technical Lead: "I have statically audited the candidate code matching │
│      hash H, reviewed plan P and action A, and authorize command C within   │
│      validity window W."                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

Gate 5 does not inspect live DaVinci Resolve state (Gate 4's duty), nor does it provide human artistic or visual presence consent (Gate 2's duty). Gate 5 provides **cryptographic code and execution clearance**.

### 1.3 Strict Relationship to Blockers A, C, and D in the Post-Incident DAG
In Gate 4 Authority Design R10 §8.1, the post-incident hardening architecture identified four independent blockers:
- **Blocker A (Gate 4 Preflight Verifier Authority & Key Custody):** Addressed by Custody Design R4 and Custody Decision Record R3. Formally recorded as `NO_DECISION_RECORDED_PENDING_FULL_QUORUM`; model selection remains `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`.
- **Blocker B (Gate 5 Release Authority Design):** **The subject of this workorder.** Formally specifies the release authority boundary, `SignedExecutionWorkorderRecord` verification, candidate hash-pinning, trust bootstrap, and lifecycle requirements.
- **Blocker C (Signer Oracle Orphan Governance):** Governs crash/restart recovery, orphan ledger reconciliation, and durable cleanup in `_resolveflow_signer`.
- **Blocker D (Host DAC Provisioning):** Governs host accounts, groups, file permissions, and directory provisioning for `/var/lib/resolveflow` and `/run/resolveflow`.

### 1.4 Independence from Gate 4 Custody Full-Quorum Approval
Blocker B is architecturally independent of Blocker A:
- Gate 5 release authority does **not** depend on whether Gate 4 selects a hardware token (Proposal 1) or an offline station (Proposal 2).
- Gate 5 design does **not** wait for, nor does it substitute for, the full-quorum governance approval required for Gate 4 verifier custody.
- Gate 4 custody remains strictly `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`, and this document grants no authority over Gate 4 keys, ceremony, or provisioning.

---

## 2. Authority Scope & Cryptographic Role Separation

### 2.1 The Technical Lead Governance Role & Authority Perimeter
The Technical Lead (Codex / Engineering Authority) holds exclusive authority over:
1. **Candidate Runner Hash-Pinning:** Inspecting and approving the byte-identical SHA-256 hash of the candidate execution harness (e.g. `/tmp/resolveflow-gate6/run_gate6.py`).
2. **Workorder Scoping & Revision:** Assigning the canonical workorder identifier matching `^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$` and defining the exact `permitted_command`.
3. **Execution Plan & Action Authorization:** Auditing the exact execution plan digest (`plan_digest_sha256`) and single-action canary payload digest (`action_payload_digest_sha256`).
4. **Target Identity Pinning:** Reaffirming that execution is authorized solely on pinned project `4fd4272d-f0bc-445c-9490-5723cd6831c2` and timeline `ba6a6775-8ec4-41a4-bf2f-704de902e68d`.

### 2.2 Strict Reconciliation with the Three-Principal OS Model
Plan 07B7 and DEP-01 §2.1 establish exactly three operating system execution principals. No fourth operating system principal exists, and introducing any new POSIX account or restricted UID is strictly prohibited:

| OS Principal | POSIX UID / GID | Authorized Functions & State Custody | Gate 5 Role & Boundaries |
| :--- | :--- | :--- | :--- |
| `_resolveflow_signer` | UID `901` / GID `901` | Holds Oracle private key (`oracle_private.key`), HMAC signing key (`trial_auth.key`), and nonce ledger. | **Zero Gate 5 access.** Strictly prohibited from holding Tech Lead keys or validating workorder records. |
| `_resolveflow_dispatcher` | UID `902` / GID `902` | Custody of execution journal (`execution_journal.wal`), target ledgers, and admission listener. | **Consumer & Verifier.** Evaluates Gate 5 signature and bindings against Class A Tech Lead trust anchor. Sole custodian of execution replay tracking. |
| `interactive_user` | UID `1000` / GID `1000` (`vanntruongg`) | Runs DaVinci Resolve on `DISPLAY=:1`. Runs candidate runner harness. | **Submitter.** Presents `SignedExecutionWorkorderRecord` to Dispatcher via `DispatcherAdmissionRequest`. Holds zero Tech Lead signing keys. |

**Out-of-Band Authority:** The Technical Lead does not execute as an on-host system daemon. The Tech Lead issues `SignedExecutionWorkorderRecord` out-of-band as an engineering signoff artifact. The Dispatcher daemon (`_resolveflow_dispatcher:902`) verifies this record using a root-anchored public key.

### 2.3 Six-Role Pairwise Key Separation Invariant
The architecture defines six distinct cryptographic roles:
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

Under no circumstances may the Tech Lead release key share key identifiers, raw key bytes, public key fingerprints, or private key material with any of the other five roles.

### 2.4 Prevention of Role Overlap and Collusion
- **No Self-Attestation by Operator:** The Human Operator (`interactive_user:1000`) cannot issue Gate 5 release workorders.
- **No Self-Clearing by Dispatcher:** The Dispatcher daemon (`_resolveflow_dispatcher:902`) cannot issue Gate 5 release workorders.
- **No Domain Blurring with Oracle:** The Signer Oracle key (`oracle_key_id`) signs wire clearance payloads; it must never be used to sign release workorders.

---

## 3. Technical Grounding & Contract-Enforced Claims (`SignedExecutionWorkorderRecord`)

### 3.1 The 14-Field Contract Schema
The Gate 5 contract checked in at `src/core/contracts/signed_execution_workorder_contracts.py` defines the frozen dataclass `SignedExecutionWorkorderRecord` with exactly 14 fields:

```python
@dataclass(frozen=True)
class SignedExecutionWorkorderRecord:
    schema_version: int                   # Strictly int 1 (rejecting bool-as-int)
    workorder_id: str                     # Grammar: ^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$
    tech_lead_id: str                     # Non-empty str, no whitespace padding
    tech_lead_key_id: str                 # Non-empty str, no whitespace padding
    issued_at_iso: str                    # Strict UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)
    valid_until_iso: str                  # Strict UTC ISO-8601; interval <= 3600.0s
    target_project_uid: str               # Pinned: "4fd4272d-f0bc-445c-9490-5723cd6831c2"
    target_timeline_uid: str              # Pinned: "ba6a6775-8ec4-41a4-bf2f-704de902e68d"
    reviewed_candidate_sha256: str        # 64-char lowercase hex
    permitted_command: str                # Non-empty str, no whitespace padding
    plan_digest_sha256: str               # 64-char lowercase hex
    action_payload_digest_sha256: str     # 64-char lowercase hex
    execution_id: str                     # Non-empty str, no whitespace padding
    tech_lead_signature: str              # 128-char lowercase hex (Ed25519)
```

### 3.2 RFC 8785 JSON Canonicalization Scheme (JCS) & Signing Payload
The cryptographic signature is computed over exactly 13 fields, excluding `tech_lead_signature`. The payload is serialized using RFC 8785 JSON Canonicalization Scheme (JCS) via `canonical_json_bytes()`:
- Keys are sorted strictly lexicographically by UTF-16 code units.
- Whitespace is eliminated.
- Numbers conform to IEEE 754 without formatting anomalies.

The signing payload mapping is deterministic:
$$\text{SigningPayload} = \text{JCS}(\text{Record} \setminus \{ \text{"tech\_lead\_signature"} \})$$

### 3.3 PureEd25519 Cryptographic Verification Equation
The signature verification equation enforces RFC 8032 PureEd25519 directly over the canonical JCS byte sequence:
$$\text{Ed25519Verify}(\text{PubKey}_{\text{tech\_lead\_key\_id}}, \text{SigningPayload}, \text{Bytes}(\text{tech\_lead\_signature})) \equiv \text{True}$$

### 3.4 Pinned Target Bindings
The contract hardcodes and validates:
- `target_project_uid`: `4fd4272d-f0bc-445c-9490-5723cd6831c2`
- `target_timeline_uid`: `ba6a6775-8ec4-41a4-bf2f-704de902e68d`

Any attempt to construct or deserialize a record with divergent project or timeline UIDs fails closed with `ERR_TARGET_PROJECT_UID_MISMATCH` or `ERR_TARGET_TIMELINE_UID_MISMATCH`.

### 3.5 Candidate Hash Binding (`reviewed_candidate_sha256`) & Process Execution Boundary
The field `reviewed_candidate_sha256` binds the Tech Lead's cryptographic signature to the exact SHA-256 digest of the reviewed candidate execution harness.

**Architectural Analysis of the Runtime Admission Boundary (GATE5-AUTH-R1-01 / GATE5-AUTH-R2-01 / GATE5-AUTH-R3-01 / GATE5-AUTH-R3-02):**
1. **DTO Schema and Canonical Payload Framing (GATE5-AUTH-R3-02):** The `SignedExecutionWorkorderRecord` DTO binds `reviewed_candidate_sha256` as a 64-char lowercase hex string into the 13-field canonical JCS signing payload. However, the DTO itself performs solely syntactic validation and deterministic byte serialization; it does **not** verify the signature against any public key, nor does it inspect any file on disk. Mathematical verification and hash comparison are external, future verifier requirements (§3.8) evaluated by a trusted consumer against an anchored public key.
2. **Wire Contract Reality:** The accepted `DispatcherAdmissionRequest` DTO (`src/core/contracts/dispatcher_admission_contracts.py:50`) transports only six fields:
   `schema_version`, `request_id`, `approval_envelope`, `operator_consent_envelope`, `preflight_evidence_envelope`, and `execution_workorder_record`.
   The wire contract does **not** transport the candidate script source code, executable bytes, file path, or runtime process memory attestation.
3. **IPC Socket Boundary:** On the AF_UNIX admission socket, `SO_PEERCRED` authenticates only caller process credentials: PID, UID, and GID (confirming `interactive_user:1000`). `SO_PEERCRED` does **not** authenticate what executable image or script is loaded into the peer's address space. In Python execution environments, `/proc/<peer_pid>/exe` points simply to the shared `/usr/bin/python3` interpreter binary.
4. **Disk-Hashing Vulnerability & TOCTOU:** Verifying the hash of a script file on disk (e.g. in `/tmp` or user workspace) does not prove that the connecting peer process is executing those exact bytes, nor does it prevent race conditions (Time-of-Check to Time-of-Use) or unauthorized sibling processes under UID 1000 from presenting valid envelopes over the socket.
5. **Scoping of Gate 5 Claims:**
   - Gate 5 strictly provides **artifact and parameter release authorization**. It certifies that the Tech Lead approved the artifact digests and parameters.
   - Gate 5 does **not** assert kernel-level execution control or in-memory process attestation over the operating system process connecting to `_resolveflow_dispatcher`.
6. **Technical Runtime Process Binding Analysis (GATE5-AUTH-R3-01):**
   - **Supervised Execution (Dispatcher-Supervised / Isolated Launcher):** This is the **only technical mechanism** capable of directly binding admission to running process code. In this model, instead of an unconfined external process submitting envelopes and invoking Resolve directly, `_resolveflow_dispatcher` (or an authorized isolated launcher daemon) itself reads the candidate script, verifies `SHA256(script) == reviewed_candidate_sha256`, and executes the script within a controlled execution sandbox.
   - **Staged Artifact Storage Integrity Control (Sealed Staging Path):** Staging the candidate runner in a root-owned, immutable directory (e.g. `/opt/resolveflow/bin/` with permissions `0555 root:root`) prevents the script file itself from being overwritten or modified by UID 1000 on disk after Tech Lead review. However, as recognized in Finding `GATE5-AUTH-R3-01`, root-owned file permissions alone do **not** bind the connecting socket peer process to that file; any process running under UID 1000 can still execute arbitrary code in memory and connect to the admission socket. Therefore, sealed staging is an artifact storage integrity control, not a runtime process binding.
7. **Residual Risk Characterization (Baseline Unconfined Runner):**
   In the absence of a supervised execution launcher, the baseline architecture does **not** achieve cryptographic runtime process binding. Submitting envelopes from an unconfined client process under `interactive_user:1000` represents an operational residual risk posture: the system relies on cooperative runner adherence and continuous, direct physical human operator supervision (Gate 2), while explicitly recognizing that intra-UID process isolation and in-memory execution integrity are not cryptographically guaranteed by Gate 5 or AF_UNIX admission.

### 3.6 Plan & Action Digest Bindings
- `plan_digest_sha256`: Binds the canonical execution plan.
- `action_payload_digest_sha256`: Binds the single authorized `INSERT_CLIP` action (video track 1, start frame 86400, 120 frames at 24/1 fps).

### 3.7 Workorder Grammar & Permitted Command Binding
- `workorder_id`: Enforces strict regex `^G07-07B7-GATE6-EXEC-R[1-9][0-9]*$`. Rejects revision 0, negative revisions, lowercase letters, or missing prefixes.
- `permitted_command`: Non-empty string naming the authorized execution entrypoint (e.g. `execute_canary_trial_g07`).

**Clarification of Permitted Command Verification Boundary (GATE5-AUTH-R2-01):**
- `permitted_command` commits the Tech Lead's approval to a specific entrypoint identifier in the release record.
- In the accepted `DispatcherAdmissionRequest` wire contract, there is **no independent wire parameter or caller command field**. The Dispatcher receives only the envelopes.
- The Dispatcher validates that `record.permitted_command` is non-empty and well-formed according to contract schema. However, **the Dispatcher daemon has no caller wire field, OS capability, or out-of-band channel to observe what Python function or command the connecting client process actually executes.**
- Therefore, `ERR_GATE5_COMMAND_MISMATCH` is strictly an **internal client-side preflight check within the candidate runner**: the runner verifies its own target invocation against `record.permitted_command` before attempting admission, halting if they diverge. It is **not** an independent gate enforced by the Dispatcher daemon under the accepted V1 wire contract.

### 3.8 Strict Separation: Contract-Enforced Invariants vs. Future Verifier Requirements

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CONTRACT-ENFORCED INVARIANTS (DTO Layer)                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Schema version strictly int 1 (rejects bool-as-int).                     │
│ 2. Workorder ID grammar validation.                                         │
│ 3. Pinned project and timeline UIDs.                                        │
│ 4. Hex string formatting (64-char lowercase hex for digests; 128-char hex   │
│    for signature).                                                          │
│ 5. Timestamp parsing and ordering: issued_at_iso < valid_until_iso.          │
│ 6. Validity window interval bounded to <= 3600.0s (1 hour max).             │
│ 7. Frozen immutability and fresh dictionary copying.                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 FUTURE VERIFIER / DISPATCHER REQUIREMENTS                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. Ed25519 Mathematical Verification: Verifying signature against pinned    │
│    Tech Lead public key (unimplemented in contract).                        │
│ 2. Trust Anchor Loading: Locating and descriptor-checking the Tech Lead     │
│    public key PEM on disk (unimplemented in contract).                      │
│ 3. Candidate Staging Verification: Pre-execution hash verification of the   │
│    candidate runner artifact against reviewed_candidate_sha256 (client     │
│    preflight or future supervised launcher).                                │
│ 4. Plan/Action Recomputation: Recomputing plan and action digests and       │
│    comparing against record fields (unimplemented).                         │
│ 5. Wall-Clock Freshness: Comparing current host time tau_dispatcher against  │
│    issued_at and valid_until with asymmetric skew tolerance.                │
│ 6. Replay & Single-Use Ledger: Tracking and atomically committing consumed  │
│    workorder_id / execution_id in durable journal (unimplemented).          │
│ 7. Revocation Checking: Checking tech_lead_key_id against tombstone ledger   │
│    (unimplemented).                                                         │
│ 8. Permitted Command Check: Client-side runner preflight verification       │
│    comparing invocation target to permitted_command (cooperative runner     │
│    adherence; not observable by Dispatcher under V1 wire contract).         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Trust-Anchor Bootstrap, Path & DAC Policy

### 4.1 Proposed Trust Anchor Path
In alignment with the system trust root architecture established in Gate 4 Authority Design R10, the proposed Class A system trust anchor for the Technical Lead is:
$$\mathbf{Proposed \ Path: \quad /etc/resolveflow/tech\_lead\_public\_key.pem}$$

### 4.2 Current Unresolved Status: Absence of Host Provisioning & Packaging Decisions
**Critical Governance Reality:**
1. The host path `/etc/resolveflow/tech_lead_public_key.pem` is **NOT established or provisioned by any accepted host manifest, DAC configuration artifact, or system deployment**.
2. Creating, reading, writing, or inspecting this path on the host is **strictly prohibited** under the current workorder mode (`NO_HOST_OR_STATE_ACCESS`).
3. Gate 5 trust-anchor provisioning remains an **UNRESOLVED GOVERNANCE & PACKAGING DEPENDENCY** that must be resolved via a dedicated follow-on workorder (`WO-GATE5-PROVISIONING-SPEC-R1`) and reconciled with Blocker D (Host DAC Provisioning).

### 4.3 Descriptor-Anchored Trust Root Validation Protocol
When the future Dispatcher admission daemon (`_resolveflow_dispatcher:902`) loads the Tech Lead trust anchor, it must enforce descriptor-anchored system calls:
1. Open path with flags `O_RDONLY | O_NOFOLLOW | O_CLOEXEC`.
2. Perform `fstat()` on the file descriptor.
3. Validate:
   - `stat.st_uid == 0` and `stat.st_gid == 0` (strictly owned by `root:root`).
   - `(stat.st_mode & 0777) == 0644` (world-readable, root-writable only).
   - `stat.st_nlink == 1` (reject hardlinks).
   - `S_ISREG(stat.st_mode)` (regular file only; reject symlinks, FIFOs, sockets).
4. Parse exactly 32 raw public key bytes from SubjectPublicKeyInfo (SPKI) PEM.

### 4.4 Prohibition on Repurposing Oracle or Gate 4 Trust Roots
- The Tech Lead trust anchor must **never** reuse or point to `/etc/resolveflow/preflight_verifier_public_key.pem` (Gate 4 trust root).
- The Tech Lead trust anchor must **never** reuse or point to the Signer Oracle public key or keystore.
- Reusing trust anchors across distinct gate roles violates the Six-Role Pairwise Key Separation Invariant and collapses independent authorization boundaries.

### 4.5 Prohibition on New OS Principals
No new operating system user (e.g. `_resolveflow_techlead`) may be proposed. The Tech Lead operates as an out-of-band authority, and trust anchor validation is performed by `_resolveflow_dispatcher:902` within the established Three-Principal model.

---

## 5. Key Lifecycle, Custody & Cryptographic Governance (Future Requirements)

The following policies represent **future target requirements** and do not authorize immediate key generation or deployment:

### 5.1 Tech Lead Private Key Custody
The Tech Lead private key (`tech_lead_key_id`) authorizes live code execution. Therefore, its custody requires maximum assurance:
- **Recommended Custody:** Dedicated hardware security token (e.g. YubiKey 5 Series PIV or OpenPGP smartcard) or offline air-gapped development machine held by the Tech Lead.
- **Non-Exportability:** Key material must never exist in plaintext on the general-purpose Resolve editing workstation filesystem.
- **PIN & Touch:** Physical capacitive touch and PIN entry must be required per release workorder signature.

### 5.2 Witnessed Key Generation Ceremony Specifications
Key generation must take place under a formal, audited ceremony:
1. **Algorithm:** Pure Ed25519 (RFC 8032).
2. **Entropy:** Secure element hardware RNG or kernel CSPRNG (`/dev/urandom`).
3. **Identifier Grammar:** Canonical grammar `^[a-z0-9_-]{8,64}$`, structured as:
   $$\text{tech\_lead\_key\_id} = \text{"g5-techlead-" } + \text{epoch} + \text{"-" } + \text{fingerprint\_prefix}$$
4. **Ceremony Manifest:** Documentation recording date, device serial number, public key SHA-256 fingerprint, and witness signatures.

### 5.3 Public Key Pinning, Rotation & Epoch Management
- **Maximum Lifetime:** 90 days per release key epoch.
- **Rotation Procedure:** New keypair generated via ceremony; host trust anchor `/etc/resolveflow/tech_lead_public_key.pem` updated atomically by root via `replace_file_atomically`.

### 5.4 Revocation Mechanics & Tombstone Ledger
If a Tech Lead release key is suspected compromised or retired prematurely:
1. **Host Trust Root Removal:** Host administrator removes `/etc/resolveflow/tech_lead_public_key.pem`, instantly causing all subsequent admission attempts to fail closed with `ERR_GATE5_TRUST_ANCHOR_MISSING`.
2. **Tombstone Ledger:** The revoked `tech_lead_key_id` is appended to `/etc/resolveflow/revoked_keys.jsonl`. Dispatcher verifier checks this ledger and rejects revoked keys with `ERR_GATE5_KEY_REVOKED`.

---

## 6. Freshness, Clock Skew & Replay Prevention Governance

### 6.1 Validity Interval Dynamics
`SignedExecutionWorkorderRecord` enforces:
$$\Delta t = t_{\text{valid\_until}} - t_{\text{issued\_at}} \le \text{MAX\_WORKORDER\_VALIDITY\_SECONDS} = 3600.0\text{ s (1 hour)}$$
The 1-hour window accommodates code staging, preflight verification, and Dispatcher admission without permitting long-lived, standing authorization tokens.

### 6.2 Host Clock Evaluation & Asymmetric Skew Tolerance Policy (GATE5-AUTH-R1-02)
When evaluated by `_resolveflow_dispatcher:902`:
- Current host time $\tau_{\text{dispatcher}}$ is evaluated against the verifier's trusted local system clock.
- **Strict Expiration Policy (No Grace Period):** Expiration is an absolute deadline. Zero clock skew tolerance is permitted past `valid_until_iso`:
  $$\tau_{\text{dispatcher}} \le t_{\text{valid\_until}}$$
  If $\tau_{\text{dispatcher}} > t_{\text{valid\_until}}$, admission fails closed immediately with `ERR_GATE5_WORKORDER_EXPIRED`.
- **Clock Pre-dating Tolerance (Issuance Skew Only):** To accommodate minor clock variance where the Tech Lead signing station clock is slightly ahead of the Dispatcher host clock at issuance, an asymmetric past skew tolerance $\epsilon_{\text{skew}} = 30.0\text{ seconds}$ is permitted:
  $$\tau_{\text{dispatcher}} \ge t_{\text{issued\_at}} - \epsilon_{\text{skew}}$$
  If $\tau_{\text{dispatcher}} < t_{\text{issued\_at}} - \epsilon_{\text{skew}}$, admission fails closed with `ERR_GATE5_WORKORDER_PREDATES_CLOCK`.

**Harmonized Freshness Equation:**
$$t_{\text{issued\_at}} - 30.0\text{s} \le \tau_{\text{dispatcher}} \le t_{\text{valid\_until}}$$

### 6.3 Replay Mitigation via Dispatcher Atomic Execution Ledger (Non-Operational Prerequisite)
Like Gate 4, timestamp bounding alone is insufficient to prevent replay attacks (e.g. submitting the same release workorder twice to execute multiple clip insertions).

**Prerequisite Status:**
1. Replay prevention is **currently unimplemented and non-operational** because the Dispatcher admission daemon and durable ledgers do not yet exist.
2. Under future implementation workorders, `_resolveflow_dispatcher:902` will maintain `/var/lib/resolveflow/dispatcher/consumed_execution_workorders.jsonl`.
3. In the admission transaction, `workorder_id` and `execution_id` will be checked and atomically committed before mutation clearance. Any duplicate submission fails closed with `ERR_GATE5_WORKORDER_ALREADY_CONSUMED`.

---

## 7. Comprehensive Failure Matrix & Error Taxonomy

The following failure matrix specifies the exact fail-closed behavior for all Gate 5 verification scenarios:

| Failure Scenario | Root Cause | Target Error Code | Architectural Disposition |
| :--- | :--- | :--- | :--- |
| **Trust Anchor Missing** | `/etc/resolveflow/tech_lead_public_key.pem` not present on host. | `ERR_GATE5_TRUST_ANCHOR_MISSING` | Immediate abort; zero mutation; fail closed. |
| **Trust Anchor Unsafe** | Inode not root:root, symlink, mode != 0644, or link count > 1. | `ERR_GATE5_TRUST_ANCHOR_UNSAFE` | Immediate abort; security alert; fail closed. |
| **Key ID Mismatch** | `record.tech_lead_key_id` != key ID in trusted public key. | `ERR_GATE5_KEY_ID_MISMATCH` | Immediate abort; fail closed. |
| **Key Revoked** | `tech_lead_key_id` appears in `/etc/resolveflow/revoked_keys.jsonl`. | `ERR_GATE5_KEY_REVOKED` | Immediate abort; security alert; fail closed. |
| **Signature Invalid** | Ed25519 signature does not verify over 13-field canonical JCS payload. | `ERR_GATE5_SIGNATURE_INVALID` | Immediate abort; tampering alert; fail closed. |
| **Candidate Hash Mismatch** | SHA-256 of candidate runner artifact != `reviewed_candidate_sha256`. | `ERR_GATE5_CANDIDATE_HASH_MISMATCH` | Immediate abort; unreviewed code rejected; fail closed. *(Note: Evaluated on staged disk artifact before execution; does not authenticate peer kernel memory).* |
| **Plan Digest Mismatch** | SHA-256 of execution plan != `plan_digest_sha256`. | `ERR_GATE5_PLAN_DIGEST_MISMATCH` | Immediate abort; plan divergence rejected; fail closed. |
| **Action Digest Mismatch** | SHA-256 of canary action != `action_payload_digest_sha256`. | `ERR_GATE5_ACTION_DIGEST_MISMATCH` | Immediate abort; action tampering rejected; fail closed. |
| **Command Mismatch** | Target entrypoint identifier != `permitted_command`. | `ERR_GATE5_COMMAND_MISMATCH` | Client-side abort in runner prior to socket connect; fail closed. *(Note: Cooperative runner client-side check; NOT an independent Dispatcher gate under V1 contract).* |
| **Target Project Mismatch** | `target_project_uid` != `4fd4272d-f0bc-445c-9490-5723cd6831c2`. | `ERR_GATE5_TARGET_PROJECT_MISMATCH` | Immediate abort; target mismatch rejected; fail closed. |
| **Target Timeline Mismatch** | `target_timeline_uid` != `ba6a6775-8ec4-41a4-bf2f-704de902e68d`. | `ERR_GATE5_TARGET_TIMELINE_MISMATCH` | Immediate abort; target mismatch rejected; fail closed. |
| **Workorder Expired** | Host UTC time $\tau_{\text{dispatcher}} > t_{\text{valid\_until}}$ (strict deadline; zero skew tolerance past expiration). | `ERR_GATE5_WORKORDER_EXPIRED` | Immediate abort; fresh workorder required; fail closed. |
| **Workorder Predates Clock** | Host UTC time $\tau_{\text{dispatcher}} < (t_{\text{issued\_at}} - 30.0\text{s})$ (clock anomaly exceeding allowed skew tolerance). | `ERR_GATE5_WORKORDER_PREDATES_CLOCK` | Immediate abort; clock anomaly flagged; fail closed. |
| **Workorder Already Consumed**| `workorder_id` or `execution_id` exists in consumed ledger. | `ERR_GATE5_WORKORDER_ALREADY_CONSUMED` | Immediate abort; replay rejected; fail closed. |
| **Dispatcher Offline** | Dispatcher admission socket unreachable or times out. | `ERR_GATE5_DISPATCHER_UNAVAILABLE` | Candidate runner aborts; zero mutation; fail closed. |

---

## 8. Dependency Map & Sequential Follow-On Workorders

### 8.1 Post-Incident Hardening Dependency DAG
The positioning of this Gate 5 design within the Plan 07B7 Phase 2 DAG is illustrated below:

```
[Phase 2 Boundary Design R8 (ACCEPTED)]
       │
       ├───► [Receipt/WAL Binding Design R3 (ACCEPTED)]
       │            │
       │            └───► [Post-Incident Runbook R5 (ACCEPTED)]
       │                         │
       │                         ├───► [Blocker A: Gate 4 Preflight Authority Design R10 (ACCEPTED)]
       │                         │            │
       │                         │            ├───► [Gate 4 Custody Options R4 (ACCEPTED)]
       │                         │            │
       │                         │            └───► [Gate 4 Custody Decision R3 (NO DECISION / UNRESOLVED)]
       │                         │
       │                         ├───► [Blocker B: Gate 5 Release Authority Design R4 (THIS DESIGN)]
       │                         │            │
       │                         │            └───► (DESIGN ACCEPTANCE DOES NOT CLOSE BLOCKER B)
       │                         │
       │                         ├───► [Blocker C: Signer Oracle Orphan Governance] (STRICTLY UNRESOLVED)
       │                         │
       │                         └───► [Blocker D: Host DAC Provisioning Review] (STRICTLY UNRESOLVED)
       │
       ▼
[ALL BLOCKERS UNRESOLVED]
       │
       X  <─── [DISCONNECTED / NO EDGES]
       │
       ▼
[WO5: Live Canary Execution Directive]
```

### 8.2 Inter-Blocker Independence
- **Blocker B vs. Blocker A:** Blocker B specifies Tech Lead release authority. It operates in parallel with Blocker A (preflight verifier authority). Neither blocker can substitute for the other. Both must be cleared before Dispatcher admission can admit a trial.
- **Blocker B vs. Blocker C:** Blocker C resolves Signer Oracle orphan transitions. Independent of Gate 5 design.
- **Blocker B vs. Blocker D:** Blocker D provisions the actual host principals and directories (`_resolveflow_signer`, `_resolveflow_dispatcher`, `/etc/resolveflow`). Blocker B defines the trust anchor specification that Blocker D must package.

### 8.3 Sequenced Follow-On Workorders for Gate 5
Before Gate 5 can become operational, the following sequential workorders must be issued and reviewed:
1. **`WO-GATE5-CUSTODY-POLICY-R1` (Governance Workorder):**
   - Formal specification of Tech Lead private key custody mechanism (hardware token vs. offline workstation).
2. **`WO-GATE5-KEYGEN-CEREMONY-R1` (Ceremony Workorder):**
   - Execution and documentation of Tech Lead Ed25519 key generation ceremony.
3. **`WO-GATE5-PROVISIONING-SPEC-R1` (Host Packaging Workorder):**
   - Packaging and deployment specification for `/etc/resolveflow/tech_lead_public_key.pem` (`0644`, `root:root`).
4. **`WO-GATE5-HARNESS-VERIFIER-R1` (Implementation & Unit Test Workorder):**
   - Implementation of descriptor-anchored trust root loading and `SignedExecutionWorkorderRecord` verification inside candidate runner and Dispatcher admission service.

---

## 9. Governance Recommendation & Formal Non-Authorization

### 9.1 Summary Recommendation to Technical Lead & Governing Quorum
It is recommended that Technical Lead Codex:
1. **Accept this document as `ACCEPTED_FOR_DESIGN_ONLY`:** Formally approve Revision R4 as the definitive architectural design for Gate 5 Tech Lead release authority, contract bindings, failure matrix, and trust bootstrap.
2. **Maintain Strict Block on Operational Release:** Reaffirm that design acceptance does **not** close Blocker B, does not generate keys, does not deploy trust roots, and does not authorize signing live release workorders.
3. **Preserve Independence of Blocker A:** Note that Gate 4 custody remains `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`.

### 9.2 Strict Declaration of Non-Authorization
- **This document alone does NOT authorize any release, execution, key generation, or host change.**
- **No live candidate execution workorder (`G07-07B7-GATE6-EXEC-R*`) is issued, signed, or authorized by this document.**
- **Blockers A, B, C, and D remain completely unresolved.**

### 9.3 Preserved Incident Safety Invariants
- `ready_to_execute = False` is strictly maintained.
- Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
- Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
- Node `WO5 (Live Canary Execution Directive)` in decision DAG remains completely disconnected with strictly zero incoming edges.
- Target project `RF_TRIAL_AEC8F0A7A365D5F5` (UID `4fd4272d-f0bc-445c-9490-5723cd6831c2`) and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` (UID `ba6a6775-8ec4-41a4-bf2f-704de902e68d`) on `DISPLAY=:1` remain untouched and empty.
- Zero private key generation, zero host provisioning, zero systemd changes, zero socket creation, zero network traffic, zero Resolve API/UI interactions.
