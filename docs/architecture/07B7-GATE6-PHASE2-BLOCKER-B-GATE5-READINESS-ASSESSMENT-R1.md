# Plan 07B7 Phase 2 Blocker B: Gate 5 Trust Anchor & Runtime Process Binding Readiness Assessment (Revision R1)

**Document ID:** `07B7-GATE6-PHASE2-BLOCKER-B-GATE5-READINESS-ASSESSMENT-R1`  
**Workorder:** `G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-READINESS-ASSESSMENT-R1`  
**Date:** 2026-11-05  
**Parent:** Plan 07B7 Phase 2 Hardening, Blocker B (Gate 5 Release Authority). Follows accepted Gate 5 Release Authority Design R1 (`07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md`, Revision R4), accepted Blocker-D packaging realignment artifacts, and accepted Blocker Status Checkpoint R2.  
**Mode:** `REPOSITORY_STATIC_ASSESSMENT_ONLY; NO_KEY_OR_TRUST_ROOT_GENERATION_OR_ACCESS; NO_HOST_OR_SYSTEMD; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_TESTS_OR_IMPORTS; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`.

---

## 1. Executive Summary & Assessment Objective

Under Plan 07B7 Phase 2 post-incident hardening, Blocker B governs Gate 5 Release Authority—the architectural and cryptographic control requiring explicit, two-party signed authorization by the Technical Lead before the Live Canary execution engine can admit a candidate execution workorder.

This assessment performs a static, repository-grounded readiness review of Gate 5 specifications across:
1. **Trust-Anchor Distribution:** The exact filesystem placement, packaging declarations, and DAC verification rules for the Technical Lead release public key.
2. **Admission IPC & Contract Schemas:** The wire format, DTO envelope, and runtime signature/replay verification mechanics for `DispatcherAdmissionRequest`.
3. **Runtime Process Binding:** The technical mechanisms that bind an authorized candidate workorder to an active running execution process, distinguishing between weak operational assumptions and kernel/daemon-enforced guarantees.

### Summary Assessment Finding
Blocker B remains **OPEN / UNRESOLVED**. While pure contract DTOs (`SignedExecutionWorkorderRecord`, `DispatcherAdmissionRequest`) and conceptual architecture designs exist in the repository, **critical operational and architectural prerequisites remain entirely absent**:
- Host state was not inspected under this workorder; repository evidence establishes that packaging and provisioning scripts explicitly do not distribute key bytes, and no repository evidence establishes a key ceremony or installation.
- Zero runtime dispatcher admission service implements envelope verification, signature validation, or revocation checks.
- A critical path divergence exists between the accepted design (`/etc/resolveflow/tech_lead_public_key.pem`) and candidate runner harness (`/var/lib/resolveflow/signer/trusted_roots/gate5_release_pubkey.pem`).
- Process-binding guarantees are currently absent: no supervised launcher, kernel memfd seal, or `/proc/<pid>/exe` cryptographic verification exists.

---

## 2. Gate 5 Trust-Anchor Distribution Analysis

### 2.1 Canonical Path vs. Legacy Proposed Paths
- **Accepted Design Specification:** In `docs/architecture/07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md` (§3.1, §4.1, §7.1), the canonical Class A host trust anchor for Gate 5 is explicitly defined as:
  ```
  /etc/resolveflow/tech_lead_public_key.pem
  ```
  *Standard Policy:* Owner `root:root` (0:0), Mode `0644`, regular file (`stat.S_ISREG`), non-symlink (`stat.S_ISLNK == False`), link count strictly 1 (`nlink == 1`).
- **Path Divergence in Candidate Harness:** In `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py` (lines 167–169), the runner specifies:
  ```python
  PINNED_GATE5_RELEASE_KEY_PATH = Path(
      "/var/lib/resolveflow/signer/trusted_roots/gate5_release_pubkey.pem"
  )
  ```
  *Divergence Analysis:* This path references a legacy pre-DEP-01 directory hierarchy. Under accepted DEP-01 and DEP-02A packaging realignment, direct children of `/var/lib/resolveflow/signer` are strictly constrained to the set `{'keystore', 'nonces'}`. Any directory named `trusted_roots` or `anchors` under signer state is prohibited and rejected by DAC verifiers fail-closed. Furthermore, the Dispatcher daemon's systemd unit (`resolveflow-dispatcher.service`) explicitly enforces `InaccessiblePaths=/var/lib/resolveflow/signer`, meaning the Dispatcher is structurally blocked from accessing any path under `/var/lib/resolveflow/signer`.
  *Conclusion:* The candidate harness path is obsolete, invalid, and directly contradicts accepted architecture and packaging contracts.

### 2.2 Host Packaging Artifact Declarations
1. **Administrative Provisioning Script (`deploy/scripts/provision_resolveflow_principals.sh`):**
   - Declares `KNOWN_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}` (lines 258, 511).
   - Provisions directory `/etc/resolveflow` (Owner `0:0`, Mode `0755`).
   - **Explicit Non-Installation Policy:** The script explicitly contains the architectural guarantee:
     ```bash
     # 2. Provision /etc/resolveflow trust-root directory (Owner 0:0, Mode 0755)
     # Does NOT generate, copy, install, replace, remove, parse, or read any key contents.
     ```
   - Zero key bytes, trust anchor certificates, or public key files are deployed or generated by this script.
2. **Read-Only DAC Verifier Script (`deploy/scripts/verify_resolveflow_dac.sh` Revision R8):**
   - Declares `KNOWN_TRUST_ANCHORS = {"oracle_public_key.pem", "preflight_verifier_public_key.pem", "tech_lead_public_key.pem"}` (lines 446–450).
   - If present, verifies metadata descriptor-relatively (`S_ISREG`, `nlink == 1`, `0:0`, `0644`).
   - If absent, reports: `Trust anchor '/etc/resolveflow/tech_lead_public_key.pem' is absent (unprovisioned)` (line 514).
   - Explicitly rejects `/etc/resolveflow/revoked_keys.jsonl` as an unresolved packaging prerequisite fail-closed (line 480).
3. **Artifact Fact vs. Host State:**
   - The presence of `tech_lead_public_key.pem` in provisioning/verifier allowlists is strictly an artifact declaration. Host state was not inspected; repository evidence does not establish that a key ceremony was conducted or that a trust anchor file was installed, and the provisioning scripts explicitly do not write key contents.

---

## 3. Runtime Process-Binding Contracts & Wire Schema Analysis

### 3.1 Dispatcher Admission Contracts
- **DTO Specification:** `src/core/contracts/dispatcher_admission_contracts.py` defines:
  ```python
  class DispatcherAdmissionRequest:
      schema_version: int  # strictly 1
      request_id: str      # canonical UUIDv4
      approval_envelope: SignedApprovalEnvelope
      operator_consent_envelope: SignedOperatorConsentEnvelope
      preflight_evidence_envelope: SignedPreflightEvidenceEnvelope
      execution_workorder_record: SignedExecutionWorkorderRecord
  ```
- **Release Authority Envelope:** `src/core/contracts/signed_execution_workorder_contracts.py` defines `SignedExecutionWorkorderRecord`, which encapsulates:
  - `workorder_id`: Grammar `^G07-07B7-GATE6-EXEC-R[0-9]+$`
  - `tech_lead_id`, `tech_lead_key_id`: Non-empty strings
  - `issued_at_iso`, `valid_until_iso`: Canonical UTC timestamps
  - `target_project_uid`, `target_timeline_uid`: Canonical UUIDv4
  - `reviewed_candidate_sha256`: 64-hex SHA-256 digest
  - `plan_digest_sha256`, `action_payload_digest_sha256`: 64-hex SHA-256 digests
  - `execution_id`: Unique non-empty identifier
  - `tech_lead_signature`: 128-hex Ed25519 signature

### 3.2 Critical Implementation & Verification Gaps
1. **Zero Runtime Consumer or Server Implementation:**
   - Static search across `src/` confirms that `DispatcherAdmissionRequest` is imported and referenced **only** within its own unit test and definition file (`src/core/contracts/dispatcher_admission_contracts.py`).
   - No daemon, server socket listener, or admission controller implements reception, deserialization, or validation of `DispatcherAdmissionRequest` over any socket.
2. **Zero Cryptographic Signature Verification:**
   - Neither `DispatcherAdmissionRequest` nor `SignedExecutionWorkorderRecord` is verified by any active service against `/etc/resolveflow/tech_lead_public_key.pem`.
   - The DTO `__post_init__` and `from_dict` methods perform schema, type, bounds, and regex validation only. **DTO validation does not evaluate cryptographic signatures.**
3. **Zero Revocation or Replay Defense:**
   - The Gate 5 design specifies an append-only revocation ledger (`/etc/resolveflow/revoked_keys.jsonl`) and execution nonce tracking. No runtime component inspects or maintains this ledger.
4. **Candidate Runner Entrypoint & Helper Reachability:**
   - In `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`, an internal validation helper `verify_signed_execution_workorder_record()` is present in downstream code. However, this helper is unreachable through the candidate entrypoint: `run_harness()` unconditionally raises `Gate6HarnessSecurityError("ERR_GATE6_PREPARATION_ONLY: ...")` fail-closed as its first executable statement (lines 637–644) before any validation helper is invoked or can run.
   - Even in downstream code, the helper's configured trust-root path remains divergent and obsolete (`/var/lib/resolveflow/signer/trusted_roots/gate5_release_pubkey.pem`, as analyzed in §2.1), conflicting with accepted signer packaging rules and blocked by Dispatcher daemon isolation. The helper does not execute through the candidate entrypoint, nor does it operate across a privilege-separated admission boundary.

---

## 4. Evaluation of Process-Binding Claims vs. Actual Enforcement

To establish technical rigor, claims regarding process binding must be disaggregated into distinct architectural layers:

| Binding Layer | Mechanism & Enforcement Scope | Current Repository Reality | Residual Risk / Security Failure Mode |
| :--- | :--- | :--- | :--- |
| **Layer 1: Socket Peer Identity** | UNIX domain socket `SO_PEERCRED` retrieves kernel-attested `{pid, uid, gid}`. Design specification (Gate 4 Design R10 §7.3): Dispatcher server checks connecting runner peer as UID/GID 1000; runner checks Dispatcher server peer as UID/GID 902. | Specified in architecture design; **neither direction is currently implemented** in any active admission server. | Peer credential check verifies only numeric OS identity at connect time. **Zero attestation of code identity, binary hash, or memory contents.** |
| **Layer 2: Candidate File Hash** | Static hashing of candidate script on filesystem (`reviewed_candidate_sha256`). | Defined in `SignedExecutionWorkorderRecord` DTO schema. | File-integrity race concern: static on-disk hash check does not guarantee that the file remains unchanged between check and execution unless underlying storage is sealed. |
| **Layer 3: Immutable / Sealed File Storage** | Systemd `ProtectSystem=strict` in `resolveflow-dispatcher.service`. | Service unit specifies `ProtectSystem=strict`. | Protects only against modifications *by the daemon process itself*. **Does not protect against modifications by workspace owners or unprivileged local users.** |
| **Layer 4: In-Flight Process-Image Binding** | Cryptographic/kernel binding between running memory image and signed workorder. | **COMPLETELY ABSENT.** No `memfd_create(F_SEAL_WRITE)`, no supervised launcher, no `/proc/<pid>/exe` hash verification. | Unresolved process-integrity requirement: absent kernel sealing or supervised process image tracking, in-flight process execution is not cryptographically bound to the reviewed candidate digest. |

### Technical Rigor Rule
A static file hash check combined with `SO_PEERCRED` does **not** constitute a process-binding guarantee. Conflating Layer 1 or Layer 2 with Layer 4 is an architectural error. Process-image binding remains an open, unmitigated residual risk.

---

## 5. Remaining Prerequisites & Recommended Follow-on Workorders

To transition Blocker B towards operational readiness without violating governance or safety invariants, the following sequential prerequisites must be addressed:

1. **Prerequisite B-1: Gate 5 Trust Anchor Policy & Ceremony Specification:**
   - Formal specification of key generation ceremony, format (Ed25519 PEM), metadata policy (`0644`, `0:0`), and atomic installation workflow.
   - Reconciliation of candidate harness `PINNED_GATE5_RELEASE_KEY_PATH` with canonical `/etc/resolveflow/tech_lead_public_key.pem`.
2. **Prerequisite B-2: Supervised Launcher & Process-Binding Architecture Design:**
   - Architecture specification defining how candidate execution is bound to `reviewed_candidate_sha256` at runtime (e.g., supervised launcher daemon, pinned descriptor execution via `fexecve`, or immutable runtime sealing).
3. **Prerequisite B-3: Dispatcher Admission Server Daemon & Signature Engine:**
   - Implementation of the AF_UNIX admission socket listener in `src/services/dispatcher_admission_service.py`, consuming `DispatcherAdmissionRequest` and verifying Ed25519 signatures, timestamp validity, target project/timeline UIDs, and candidate hashes.

---

## 6. DAG Alignment, Governance & Safety Invariants

- **DAG Independence:** Blocker B is an independent architectural prerequisite under Plan 07B7 Phase 2. This readiness assessment does not depend on, resolve, or alter Blocker A (Gate 4 preflight custody), Blocker C (orphan governance), or Blocker D (host DAC).
- **Gate 4 Independence:** Gate 4 custody remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and `STRICTLY OPEN`. This assessment does not substitute for or infer Gate 4 quorum concurrence.
- **Blocker C & D Status:** Prerequisite C-1 remains `STRICTLY OPEN`; C-2 remains non-operational artifacts only; D-1 remains `OPEN / UNVERIFIED`.
- **Node WO5 Isolation:** Node `WO5 (Live Canary Execution Directive)` remains **COMPLETELY DISCONNECTED** with strictly zero incoming and zero outgoing edges.
- **Safety Invariants:** `ready_to_execute = False` is strictly maintained. Gate 6 is `NOT ACCEPTED` / incident-only. Gate G07 and Milestones M1, M2, and M3 remain `OPEN`.
