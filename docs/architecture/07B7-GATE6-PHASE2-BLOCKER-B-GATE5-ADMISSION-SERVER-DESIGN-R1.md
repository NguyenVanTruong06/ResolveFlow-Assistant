# Gate 6 Phase 2 Blocker B: Dispatcher Admission Server Daemon & Signature Engine Architecture Design (Revision R5)

**Document ID:** `07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-SERVER-DESIGN-R1`  
**Revision:** `R5`  
**Workorder:** `G07-07B7-GATE6-PHASE2-BLOCKER-B-GATE5-ADMISSION-SERVER-DESIGN-R1`  
**Date:** 2026-11-05  
**Parent:** Plan 07B7 Phase 2 Hardening, Blocker B, prerequisite B-3. Follows accepted Gate 5 Release Authority Design R4 (`07B7-GATE6-PHASE2-GATE5-RELEASE-AUTHORITY-DESIGN-R1.md`), accepted Gate 5 Readiness Assessment R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-READINESS-ASSESSMENT-R1.md`), accepted Gate 5 Trust Anchor Policy Design R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-TRUST-ANCHOR-POLICY-DESIGN-R1.md`), accepted Gate 5 Process-Binding Architecture Design R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-PROCESS-BINDING-DESIGN-R1.md`), accepted Gate 4 Authority Design R10 (`07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md`), accepted Gate 4 Custody Policy Design R4 (`07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1.md`), accepted Phase 2 Boundary Design R8, and accepted Phase 2 Blocker Status Checkpoint R2.  
**Mode:** `REPOSITORY_ONLY_STATIC_ARCHITECTURE_DESIGN; READ_ONLY_INPUTS; NO_SOURCE_OR_CONFIG_EDITS; NO_TESTS_OR_IMPORTS_OR_COMPILATION; NO_HOST_OR_PRODUCTION_STATE_ACCESS; NO_KEY_OR_TRUST_ROOT_CONTENT_ACCESS; NO_SYSTEMD_LIFECYCLE; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`.

---

## 1. Executive Summary & Problem Formulation

Under Plan 07B7 post-incident hardening, Blocker B governs Gate 5 Technical Lead Release Authority. Gate 5 ensures that no candidate execution workflow can mutate the DaVinci Resolve canary target without cryptographic authorization from the Technical Lead, coordinated with director approval (Gate 2), contemporaneous physical operator presence (Gate 2), and clean preflight verification (Gate 4).

### 1.1 Objective and Scope of Prerequisite B-3
This document specifies the static architecture design for a future **Dispatcher Admission Server Daemon and Signature-Verification Engine** executing within the `_resolveflow_dispatcher:902` service boundary. Specifically, it establishes:
1. **AF_UNIX Transport & Peer Security Boundary:** Wire framing, descriptor lifecycle, and mutual `SO_PEERCRED` verification between the client runner (`interactive_user:1000`) and the Dispatcher daemon (`_resolveflow_dispatcher:902`).
2. **Four-Envelope Verification & Cryptographic Engine:** Parsing, canonical RFC 8785 byte extraction, Ed25519 signature validation against provisioned public trust roots, and strict cross-envelope semantic field binding across all four required authorization envelopes.
3. **Admission Transaction Ordering & Durability:** The precise sequential verification pipeline reconciling Gate 4 Design R10: performing all cryptographic, semantic, and validity checks *prior* to the atomic, durable consumption of single-use replay identifiers (`preflight_run_id` and `execution_id`).
4. **Boundary to Authoritative Mutation Pipeline:** The strict structural separation between admission authorization (granting intent) and subsequent DEP-03 journaled execution (`LiveCanaryDispatchService`), preserving one-shot `EXHAUSTED` target semantics and fail-closed crash recovery.
5. **Readiness and Governance Limits:** Explicit documentation that accepting this static design specification does **not** constitute an operational admission service, host trust-anchor installation, key ceremony completion, process-binding attestation, contract extension, host modification, or Gate 6 execution authority.

### 1.2 Strict Statement Classification Taxonomy
In adherence to Workorder Section 3, all statements throughout this specification are classified as:
- **[ACCEPTED-DESIGN]:** Formally accepted requirements in accepted architecture documents (e.g. Gate 5 Design R4, Gate 4 Design R10, DEP-01 R4, DEP-03 R5, Phase 2 Boundary Design R8).
- **[IMPLEMENTED-CONTRACT]:** Cryptographic and schema behaviors verified in checked-in repository source code (e.g. `src/core/contracts/dispatcher_admission_contracts.py`).
- **[PACKAGING-DECLARATION]:** Declarations in host setup or verification scripts (e.g. `deploy/systemd/resolveflow-dispatcher.service`, `deploy/scripts/verify_resolveflow_dac.sh`); facts about configuration code, not proof of host state.
- **[PROPOSAL]:** Architectural policies, mechanism models, and procedural designs proposed in this document.
- **[UNRESOLVED-PREREQUISITE]:** Governance, host-state, or implementation gaps that remain strictly open and require future authorized workorders.

---

## 2. AF_UNIX Transport, Socket Layout, and Peer Security Boundary

### 2.1 Endpoint Path, Directory Hierarchy & DAC Declarations
- **[ACCEPTED-DESIGN]:** The admission interface between client runners and the Dispatcher daemon is specified as a local POSIX AF_UNIX stream socket.
- **[PACKAGING-DECLARATION]:** In accordance with repository systemd and setup scripts (`deploy/systemd/resolveflow-dispatcher.service`, `deploy/scripts/provision_resolveflow_principals.sh`, `deploy/scripts/verify_resolveflow_dac.sh`):
  - **Socket Endpoint Path:** `/run/resolveflow/dispatcher_client/dispatcher.sock`
  - **Parent Directory:** `/run/resolveflow/dispatcher_client/` (mode `02750`, setgid enabled, owner `_resolveflow_dispatcher:resolveflow_clients`, UID `902`, GID `903`).
  - **Socket Inode Attributes:** Mode `0660`, owner `_resolveflow_dispatcher:resolveflow_clients` (UID `902`, GID `903`).
  - **Singleton Lock:** `/run/resolveflow/dispatcher_client/dispatcher.lock` (mode `0600`, owner `_resolveflow_dispatcher:resolveflow_clients`, non-blocking exclusive `flock` held by daemon).
  - **Client Group Access:** The interactive user running candidate harnesses (`interactive_user:1000`) is declared as a member of supplementary group `resolveflow_clients:903`, granting traversal (`r-x`) through `dispatcher_client/` and connect/read/write (`rw-`) access to `dispatcher.sock`.
  - **Isolation from Oracle:** Client processes under `interactive_user:1000` possess zero DAC permissions on `/run/resolveflow/oracle/` (mode `02750`, group `_resolveflow_dispatcher:902`, returning immediate `EACCES` to `resolveflow_clients`).
- **[UNRESOLVED-PREREQUISITE]:** Host provisioning, directory creation, group membership, and runtime DAC verification on the active host remain unverified (Prerequisite D-1 remains `OPEN / UNVERIFIED`). The above descriptions are configuration declarations, not assertions of current host filesystem reality.

### 2.2 Bidirectional Mutual Peer Authentication (`SO_PEERCRED`)
- **[ACCEPTED-DESIGN]:** Mutual peer identity verification is anchored in the Linux kernel via socket option `SO_PEERCRED` (`struct ucred`), which returns caller PID, UID, and GID at connection establishment.
- **[PROPOSAL]:** The admission server and client runner enforce bidirectional verification before exchanging any payload bytes:
  1. **Server-Side Verification (Dispatcher Daemon):**
     - Upon `accept()`, the Dispatcher calls `getsockopt(client_fd, SOL_SOCKET, SO_PEERCRED, ...)`.
     - The daemon strictly verifies that `cred.uid == 1000` (`interactive_user`) and `cred.gid == 1000`.
     - *Failure Action:* If peer UID or GID does not match `1000`, the Dispatcher immediately terminates the connection (`close()`) without transmitting any response and logs security event `ERR_GATE5_PEER_REJECTED`.
  2. **Client-Side Verification (Candidate Runner):**
     - Immediately upon `connect()`, the runner calls `getsockopt(sock_fd, SOL_SOCKET, SO_PEERCRED, ...)`.
     - The runner strictly verifies that `cred.uid == 902` (`_resolveflow_dispatcher`) and process primary `cred.gid == 902` (`_resolveflow_dispatcher`), matching `resolveflow-dispatcher.service`.
     - *Failure Action:* If peer process credentials do not match UID 902 / GID 902, the runner immediately terminates the socket and aborts fail-closed with `ERR_DISPATCHER_PEER_UNTRUSTED` before transmitting any request bytes and before interpreting any response bytes as admission.
  3. **Critical Distinction: Process Credentials vs. Filesystem Inode DAC:**
     - The filesystem inode of `/run/resolveflow/dispatcher_client/dispatcher.sock` is owned by `_resolveflow_dispatcher:resolveflow_clients` (UID 902, GID 903) via setgid directory inheritance to grant group traversal and connect access to `interactive_user:1000`.
     - In contrast, `SO_PEERCRED` returns the effective process credentials (`struct ucred`) of the communicating process at connection time. The Dispatcher daemon executes under service unit `resolveflow-dispatcher.service` with user `_resolveflow_dispatcher` (UID 902) and primary group `_resolveflow_dispatcher` (GID 902). Therefore, `SO_PEERCRED` strictly reports process UID 902 and process GID 902, not the socket inode GID 903. Any mismatch triggers immediate fail-closed termination.

### 2.3 Wire Framing, Frame Bounds & Transport Lifecycle
- **[IMPLEMENTED-CONTRACT]:** Wire framing is formalized in `src/core/contracts/dispatcher_admission_contracts.py`:
  - **Header:** 4-byte big-endian unsigned integer (`>I`) encoding exact payload byte length.
  - **Bounds:** Minimum payload length $\ge 1$ byte (`MIN_PAYLOAD_SIZE`); maximum payload length $\le 65536$ bytes (`MAX_PAYLOAD_SIZE` = 64 KiB).
  - **Payload Encoding:** RFC 8785 Canonical JSON UTF-8 bytes without trailing whitespace or garbage.
- **[PROPOSAL]:** The connection lifecycle follows strict bounded framing rules:
  1. **Frame Ingestion:** The receiver reads exactly 4 bytes to determine payload length $N$. If $N < 1$, connection aborts with `ERR_EMPTY_FRAME`. If $N > 65536$, connection aborts with `ERR_FRAME_TOO_LARGE`.
  2. **Bounded Payload Read:** The receiver reads exactly $N$ bytes within a fixed socket timeout (default 5.0 seconds). If connection closes prematurely, receiver aborts with `ERR_INVALID_FRAME` (truncated payload). If extra bytes follow the single expected frame, receiver aborts with `ERR_INVALID_FRAME` (trailing bytes).
  3. **Single Transaction Concurrency:** The admission server is architected as a serialized single-transaction processor. To eliminate concurrent race conditions on Class B replay ledgers and dispatch journals, the daemon accepts exactly one active admission connection at a time. Concurrent connection attempts are queued by kernel backlog or rejected if non-blocking lock cannot be acquired.
  4. **Strict JSON Parsing:** The JSON payload is parsed using duplicate-key rejecting decoder (`parse_strict_json()`). Any duplicate key, malformed syntax, or non-object root raises `DispatcherAdmissionContractError` fail-closed.

### 2.4 Response Delivery, Kernel Transport Guarantees & Scope Limitations (BLOCKER-B-ADMISSION-SERVER-R1-02)
- **[IMPLEMENTED-CONTRACT]:** `DispatcherAdmissionResponse` contains:
  ```python
  class DispatcherAdmissionResponse:
      schema_version: int          # Strictly 1
      request_id: str              # Canonical UUIDv4 matching request
      status: str                  # "ADMITTED" or "REJECTED"
      error_code: Optional[str]    # Grammar-validated code if REJECTED, None if ADMITTED
      execution_id: str            # Canonical UUIDv4
      preflight_run_id: str        # Canonical UUIDv4 confirming consumption
      admitted_at_iso: str         # Strict UTC ISO-8601 timestamp
  ```
- **[ACCEPTED-DESIGN]:** In adherence to Gate 4 Authority Design R10 (§7.3), `DispatcherAdmissionResponse` contains **no unanchored asymmetric signature**:
  1. *Pairwise Key Separation Invariant:* Reusing an existing signing key (Signer Oracle, Tech Lead, Director, Operator, or Verifier) would violate the strict pairwise role-key separation invariant.
  2. *Prohibition of Seventh Cryptographic Role:* Inventing an asymmetric signing key for the Dispatcher would introduce a 7th cryptographic role without established custody, provisioning, ceremony, or distribution mechanisms.
  3. *Kernel AF_UNIX Stream Guarantees & Scoped Boundaries (BLOCKER-B-ADMISSION-SERVER-R1-02):*
     - **Inter-UID Channel Isolation:** The kernel guarantees that bytes written to the AF_UNIX socket stream by the Dispatcher (`_resolveflow_dispatcher:902`) cannot be read, injected, or altered in transit by third-party processes lacking credentials to either endpoint (such as other system UIDs or unprivileged services).
     - **Explicit Non-Defense Against Same-UID Sibling Processes:** In strict alignment with accepted Process-Binding Design R3 and §5.1 below, the kernel AF_UNIX socket transport does **NOT** protect the client endpoint against concurrent sibling processes running under the same user identity (`interactive_user:1000`). Sibling processes under UID 1000 share identical numeric credentials, can attach via `ptrace` (unless restricted by Yama), inspect or duplicate file descriptors via `/proc/<pid>/fd/`, or manipulate client process memory directly.
     - **Unresolved Prerequisite:** Enforcing client endpoint isolation, descriptor sealing, and process provenance against same-UID adversaries remains an **explicitly unresolved prerequisite** (Prerequisite B-2). Kernel stream transport guarantees apply strictly between the endpoints as authenticated by `SO_PEERCRED`, but do not attest to the integrity of the client process memory image.

---

## 3. Four-Envelope Verification & Cryptographic Engine Architecture

### 3.1 Inventory of the Four Authorization Envelopes
The admission server evaluates exactly four independent signed envelopes transmitted in `DispatcherAdmissionRequest`.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        DISPATCHER ADMISSION REQUEST PAYLOAD                            │
├────────────────────────────────┬───────────────────────────────┬───────────────────────┤
│ Envelope Contract              │ Governing Authority & Role    │ Signing Mechanism     │
├────────────────────────────────┼───────────────────────────────┼───────────────────────┤
│ 1. SignedApprovalEnvelope      │ Gate 2: Director Approval     │ Ed25519 Pure (RFC8032)│
│ 2. SignedOperatorConsentEnvelope│ Gate 2: Human Operator Presence│ Ed25519 Pure (RFC8032)│
│ 3. SignedPreflightEvidenceEnv  │ Gate 4: Technical Verifier    │ Ed25519 Pure (RFC8032)│
│ 4. SignedExecutionWorkorderRec │ Gate 5: Technical Lead Release│ Ed25519 Pure (RFC8032)│
└────────────────────────────────┴───────────────────────────────┴───────────────────────┘
```

#### Envelope 1: Gate 2 Director Approval (`SignedApprovalEnvelope`)
- **[IMPLEMENTED-CONTRACT]:** Defined in `src/core/contracts/signed_approval_envelope.py` (Note: candidate path `signed_approval_contracts.py` is absent from repository; canonical implemented file is `signed_approval_envelope.py`).
- **Signing Payload Dict:** `canonical_signing_payload_dict()` contains exactly: `schema_version` (1), `approval_record` (AppOwnedApprovalRecord dict), `plan_digest_sha256`, `proposal_content_digest_sha256`, `director_key_id`, `signed_at_iso`.
- **Signing Bytes:** Deterministic RFC 8785 canonical JSON bytes via `canonical_json_bytes()` (`canonical_signing_payload_bytes()`). Signature `director_signature` is strictly excluded.
- **Signature Algorithm:** PureEd25519, 128-char lowercase hex string.
- **Signer Role & Key ID:** Director role (`director_key_id`).
- **Trust Source:** Director public key trust anchor. (Status: `[UNRESOLVED-PREREQUISITE]`).
- **Freshness & Revocation:** Evaluated against proposal lifecycle and revocation ledger. (Status: `[UNRESOLVED-PREREQUISITE]`).

#### Envelope 2: Gate 2 Operator Consent (`SignedOperatorConsentEnvelope`)
- **[IMPLEMENTED-CONTRACT]:** Defined in `src/core/contracts/signed_operator_consent_envelope.py` (Note: candidate path `signed_operator_consent_contracts.py` is absent; canonical file is `signed_operator_consent_envelope.py`).
- **Signing Payload Dict:** `canonical_signing_payload_dict()` contains 22 fields: `action_payload_digest_sha256`, `action_type` (`INSERT_CLIP`), `approval_envelope_digest_sha256`, `challenge_id`, `client_nonce`, `confirmation_phrase` (`CONFIRM_CANARY_TRIAL_EXECUTION`), `consent_timestamp_iso`, `duration_frames` (120), `freshness_attestation` (`OPERATOR_VISUAL_ATTESTATION_EMPTY_TIMELINE_VERIFIED`), `operator_id`, `operator_key_id`, `plan_digest_sha256`, `record_frame` ($\ge 0$), `schema_version` (1), `source_file_path`, `source_file_sha256`, `target_project_name`, `target_project_uid`, `target_timeline_name`, `target_timeline_uid`, `track_index` (1), `track_type` (`video`).
- **Signing Bytes:** Deterministic RFC 8785 canonical JSON bytes via `canonical_signing_payload_bytes()`. Signature `operator_signature` is strictly excluded.
- **Signature Algorithm:** PureEd25519, 128-char lowercase hex string.
- **Signer Role & Key ID:** Human Operator role (`operator_key_id`).
- **Trust Source & Challenge Lifecycle Gaps (BLOCKER-B-ADMISSION-SERVER-R1-04):**
  - While `SignedOperatorConsentEnvelope` defines `challenge_id` and `client_nonce`, repository source currently contains **zero challenge issuance mechanism, zero active session lookup, zero nonce replay ledger, and zero provisioned trust anchor or key-ID mapping for `operator_key_id`**.
  - *Status:* **UNIMPLEMENTED / UNRESOLVED PREREQUISITE (BLOCKER-B-ADMISSION-SERVER-R1-04).** Establishing contemporaneous operator presence and proving replay defense requires a future challenge-response protocol and session contract (`WO-OPERATOR-CHALLENGE-PROTOCOL-R1`). The admission server cannot claim operational replay defense for operator consent until this protocol is authorized and implemented.

#### Envelope 3: Gate 4 Preflight Evidence (`SignedPreflightEvidenceEnvelope`)
- **[IMPLEMENTED-CONTRACT]:** Defined in `src/core/contracts/signed_preflight_contracts.py`.
- **Signing Payload Dict:** `canonical_signing_payload_dict()` contains 20 fields: `audit_chain_head_sha256`, `empty_timeline_verified` (`True`), `incident_denylist_verified` (`True`), `media_pool_source_verified` (`True`), `observed_start_frame` (86400), `preflight_run_id` (UUIDv4), `project_and_timeline_active` (`True`), `schema_version` (1), `source_file_sha256`, `target_project_name`, `target_project_uid`, `target_timeline_fps_denominator` (1), `target_timeline_fps_numerator` (24), `target_timeline_name`, `target_timeline_uid`, `topology_verified` (`True`), `valid_until_iso`, `verified_at_iso`, `verifier_id`, `verifier_key_id`.
- **Signing Bytes:** Deterministic RFC 8785 canonical JSON bytes via `canonical_signing_payload_bytes()`. Signature `verifier_signature` is strictly excluded.
- **Signature Algorithm:** PureEd25519, 128-char lowercase hex string.
- **Signer Role & Key ID:** Gate 4 Technical Verifier role (`verifier_key_id`).
- **Trust Source:** Provisioned public trust anchor `/etc/resolveflow/preflight_verifier_public_key.pem`. (Status: `[UNRESOLVED-PREREQUISITE]`; Gate 4 custody remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and open).
- **Freshness & Revocation:** Enforces maximum validity window $\le 1800.0$ seconds (`MAX_PREFLIGHT_VALIDITY_SECONDS`). Replay prevented via durable preflight consumption ledger (`consumed_preflight_runs.jsonl`).

#### Envelope 4: Gate 5 Technical Lead Workorder (`SignedExecutionWorkorderRecord`)
- **[IMPLEMENTED-CONTRACT]:** Defined in `src/core/contracts/signed_execution_workorder_contracts.py`.
- **Signing Payload Dict:** `canonical_signing_payload_dict()` contains 13 fields: `action_payload_digest_sha256`, `execution_id` (UUIDv4), `issued_at_iso`, `permitted_command` (`canary-trial-execute`), `plan_digest_sha256`, `reviewed_candidate_sha256`, `schema_version` (1), `target_project_uid`, `target_timeline_uid`, `tech_lead_id`, `tech_lead_key_id`, `valid_until_iso`, `workorder_id` (`G07-07B7-GATE6-EXEC-R...`).
- **Signing Bytes:** Deterministic RFC 8785 canonical JSON bytes via `canonical_signing_payload_bytes()`. Signature `tech_lead_signature` is strictly excluded.
- **Signature Algorithm:** PureEd25519, 128-char lowercase hex string.
- **Signer Role & Key ID:** Gate 5 Technical Lead Release Authority role (`tech_lead_key_id`).
- **Trust Source:** Provisioned public trust anchor `/etc/resolveflow/tech_lead_public_key.pem` (mode `0644`, owner `root:root`, SPKI PEM). (Status: `[ACCEPTED-DESIGN]` in Gate 5 Design R4 and Trust Anchor Policy R3; host provisioning remains `[UNRESOLVED-PREREQUISITE]`).
- **Freshness & Revocation:** Enforces validity window $\le 3600.0$ seconds (`MAX_WORKORDER_VALIDITY_SECONDS`). Replay prevented via durable workorder consumption tracking.

### 3.2 Pairwise Key Separation Invariant & Custody Non-Transitivity
- **[ACCEPTED-DESIGN]:** All cryptographic keys across the workflow are pairwise distinct. Let the six system key identifiers be defined as:
  $$\mathcal{K} = \{K_{\text{oracle}}, K_{\text{trial\_signer}}, K_{\text{director}}, K_{\text{operator}}, K_{\text{verifier}}, K_{\text{tech\_lead}}\}$$
  The invariant requires:
  $$\forall K_i, K_j \in \mathcal{K}, \quad i \neq j \implies K_i \neq K_j$$
- **[ACCEPTED-DESIGN]:** **Gate 4 / Gate 5 Custody Non-Transitivity:**
  - The Technical Lead key ($K_{\text{tech\_lead}}$) cannot sign preflight evidence.
  - The Preflight Verifier key ($K_{\text{verifier}}$) cannot sign execution workorders.
  - The Signer Oracle key ($K_{\text{oracle}}$) cannot sign admission requests, preflight evidence, or workorders.
  - Any overlap or substitution triggers immediate `ERR_GATE5_KEY_SEPARATION_VIOLATION`.

### 3.3 Comprehensive Cross-Envelope Semantic Binding Matrix
- **[PROPOSAL]:** The admission server validates cross-envelope semantic coherence prior to granting admission. The table below specifies every bound property, the enforcing contracts, and the exact verifier assertion:

| Semantic Parameter | Enforcing Source Contracts | Admission Verifier Integrity Assertion | Failure Code on Mismatch |
| :--- | :--- | :--- | :--- |
| **Target Project UID** | Envelopes 2, 3, 4 | `env2.target_project_uid == env3.target_project_uid == env4.target_project_uid == CANARY_TARGET_PROJECT_UID` (`4fd4272d-f0bc-445c-9490-5723cd6831c2`) | `ERR_GATE5_TARGET_MISMATCH` |
| **Target Timeline UID**| Envelopes 2, 3, 4 | `env2.target_timeline_uid == env3.target_timeline_uid == env4.target_timeline_uid == CANARY_TARGET_TIMELINE_UID` (`ba6a6775-8ec4-41a4-bf2f-704de902e68d`) | `ERR_GATE5_TARGET_MISMATCH` |
| **Target Project Name**| Envelopes 2, 3 | `env2.target_project_name == env3.target_project_name == "RF_TRIAL_AEC8F0A7A365D5F5"` | `ERR_GATE5_TARGET_MISMATCH` |
| **Target Timeline Name**| Envelopes 2, 3 | `env2.target_timeline_name == env3.target_timeline_name == "RF_TRIAL_TL_AEC8F0A7A365D5F5"` | `ERR_GATE5_TARGET_MISMATCH` |
| **Plan Digest** | Envelopes 1, 2, 4 | `env1.plan_digest_sha256 == env2.plan_digest_sha256 == env4.plan_digest_sha256` | `ERR_GATE5_PLAN_DIGEST_MISMATCH` |
| **Action Payload Digest**| Envelopes 2, 4 | `env2.action_payload_digest_sha256 == env4.action_payload_digest_sha256` | `ERR_GATE5_ACTION_DIGEST_MISMATCH` |
| **Approval Envelope Binding**| Envelopes 1, 2 | `SHA256(env1.canonical_envelope_bytes()) == env2.approval_envelope_digest_sha256` | `ERR_GATE5_APPROVAL_BINDING_MISMATCH` |
| **Candidate Script Hash**| Envelope 4 | `env4.reviewed_candidate_sha256 == SHA256(open_fd(run_gate6.py))` (Entrypoint-source integrity only) | `ERR_GATE5_CANDIDATE_HASH_MISMATCH` |
| **Permitted Command** | Envelope 4 | `env4.permitted_command == "canary-trial-execute"` | `ERR_GATE5_COMMAND_UNAUTHORIZED` |
| **Execution Run ID** | Envelope 4, Response | `env4.execution_id == response.execution_id` (Echoed in journal and receipt) | `ERR_GATE5_EXECUTION_ID_INVALID` |
| **Preflight Run ID** | Envelope 3, Response | `env3.preflight_run_id == response.preflight_run_id` (Checked against replay ledger) | `ERR_GATE4_EVIDENCE_REPLAYED` |
| **Operator Challenge ID**| Envelope 2 | **UNRESOLVED / FUTURE CONTRACT REQUIREMENT (BLOCKER-B-ADMISSION-SERVER-R1-04):** `env2.challenge_id` requires active session registry lookup to verify freshness. Currently unimplemented. | `ERR_OPERATOR_CHALLENGE_UNRESOLVED` |
| **Operator Client Nonce**| Envelope 2 | **UNRESOLVED / FUTURE CONTRACT REQUIREMENT (BLOCKER-B-ADMISSION-SERVER-R1-04):** `env2.client_nonce` requires durable nonce replay check. Currently unimplemented. | `ERR_OPERATOR_NONCE_UNRESOLVED` |
| **Validity Window (Gate 4)**| Envelope 3 | `parse_iso(env3.verified_at_iso) - 5.0 <= now <= parse_iso(env3.valid_until_iso)` and `valid_until - verified_at <= 1800.0` | `ERR_GATE4_EVIDENCE_EXPIRED` |
| **Validity Window (Gate 5)**| Envelope 4 | **Gate 5 R4 §6.2 Alignment (BLOCKER-B-ADMISSION-SERVER-R1-05):** `parse_iso(env4.issued_at_iso) - 30.0 <= tau_dispatcher <= parse_iso(env4.valid_until_iso)` and `valid_until - issued_at <= 3600.0` (zero grace after expiry). | `ERR_GATE5_WORKORDER_EXPIRED` / `ERR_GATE5_WORKORDER_FUTURE` |
| **Technical Predicates**| Envelope 3 | `all([env3.project_and_timeline_active, env3.empty_timeline_verified, env3.topology_verified, env3.incident_denylist_verified, env3.media_pool_source_verified]) is True` | `ERR_GATE4_PREDICATE_UNSATISFIED` |

---

## 4. Admission Transaction Lifecycle, Durable Ordering & DEP-03 Boundary

### 4.1 End-to-End Admission & Verification Sequence Diagram
- **[PROPOSAL]:** The lifecycle spans deterministic phases from connection accept to dispatch handoff, strictly reflecting source-verified DEP-03 ordering (BLOCKER-B-ADMISSION-SERVER-R1-03):

```
Candidate Runner (UID 1000)                        Dispatcher Daemon (UID 902)
       │                                                      │
       ├─────────── 1. connect() AF_UNIX socket ─────────────►│
       │                                                      ├─► Verify SO_PEERCRED == {pid, 1000, 1000}
       │◄────────── 2. Mutual SO_PEERCRED == {pid, 902, 902} ─┤
       │                                                      │
       ├─────────── 3. Transmit Request Frame (4B len + JSON)►│
       │                                                      ├─► Frame bound checks [1, 65536]
       │                                                      ├─► parse_strict_json() (reject duplicate keys)
       │                                                      ├─► DispatcherAdmissionRequest.from_dict()
       │                                                      │
       │                                                      ├─► 4. Verify Gate 5 Ed25519 signature (Tech Lead)
       │                                                      ├─► 5. Verify Gate 4 Ed25519 signature (Verifier)
       │                                                      ├─► 6. Verify Gate 2 Ed25519 signatures (Dir/Op)
       │                                                      ├─► 7. Evaluate Semantic Cross-Binding Matrix
       │                                                      ├─► 8. Evaluate Time Bounds (Gate 5 30s skew; Gate 4 5s)
       │                                                      │
       │                                                      ├─► 9. Replay Consumption Protocol (BLOCKER-B-ADMISSION-SERVER-R1-01)
       │                                                      │      (Single atomic entry in consumed_admissions.jsonl)
       │                                                      │
       │◄────────── 10. Transmit Response Frame (ADMITTED) ───┤
       │                                                      ▼
[Proceeds to wait]                             [Handoff to DEP-03 Execution Pipeline (Source-Verified)]
                                               11. Query Signer Oracle (AF_UNIX) & Verify Response (DEP03-R2-02/03)
                                               12. DurableDispatchJournal.write_audit_intent() -> DISPATCH_ATTEMPT_INTENT
                                               13. DurableDispatchJournal.reserve_target() -> Status: RESERVED
                                               14. DurableDispatchJournal.write_action_pending() -> Status: ACTIVE
                                               15. DurableDispatchJournal.acquire_target_lock() -> .locks/.lock (flock)
                                               16. LiveResolveCanaryAdapter.execute_live_canary_action()
                                               17. DurableDispatchJournal.commit_terminal() -> Status: EXHAUSTED
                                               18. Send Oracle Terminal Notification (COMPLETED / ABORTED)
```

### 4.2 Reconciliation of Gate 4 Design R10 Durable Ordering & Unified Admission Ledger (BLOCKER-B-ADMISSION-SERVER-R1-01, BLOCKER-B-ADMISSION-SERVER-R2-03)
- **[ACCEPTED-DESIGN]:** In adherence to Gate 4 Authority Design R10 (§7.2, Finding `GATE4-AUTH-DESIGN-R6-01`):
  1. **Pre-Validation Priority:** The Dispatcher **MUST** validate all Gate 5 authorization claims, candidate script hashes, plan/action digests, and semantic cross-bindings **BEFORE** opening or mutating any Class B replay ledgers.
  2. **No Inadvertent Evidence Burning:** If a request presents an invalid Tech Lead signature, an expired workorder, a target mismatch, or an unreviewed candidate hash, the Dispatcher rejects admission immediately (`ERR_GATE5_...`). The valid Gate 4 preflight evidence is **NOT consumed or burned**, allowing the operator to rectify administrative workorder issues without repeating the preflight inspection cycle.
- **[PROPOSAL]:** **Replay Consumption via Unified Compound Admission Ledger (`consumed_admissions.jsonl`) (BLOCKER-B-ADMISSION-SERVER-R2-03):**
  - To eliminate the split-state failure mode inherent in dual-file appends (where `workorder_id` and `preflight_run_id` would reside in separate files without cross-file atomicity), the architecture specifies a single, authoritative Class B ledger:
    $$\text{Ledger Path: } \texttt{/var/lib/resolveflow/dispatcher/consumed\_admissions.jsonl}$$
  - **Single Compound Record Format:** Upon passing all pre-validation gates, the Dispatcher commits both identifiers in a single compound RFC 8785 JSON line:
    ```json
    {"admission_id": "<canonical_uuidv4>", "execution_id": "<env4.execution_id>", "preflight_run_id": "<env3.preflight_run_id>", "workorder_id": "<env4.workorder_id>", "admitted_at_iso": "<now_utc>"}\n
    ```
  - **Append Protocol & Fail-Closed Torn-Tail Handling:**
    - Appending to a regular file followed by `os.fsync(fd)` guarantees persistence to durable storage, but does not make a multi-byte filesystem write an atomic hardware primitive.
    - If a crash or power loss occurs mid-write, leaving an incomplete or malformed trailing line in `consumed_admissions.jsonl`, the admission parser enforces strict fail-closed torn-tail handling:
      - Any syntax error, partial JSON fragment, or duplicate key detected during ledger ingestion raises `ERR_ADMISSION_LEDGER_CORRUPT` immediately;
      - The admission daemon halts all subsequent processing until manual administrative inspection;
      - Zero corrupted records are silently skipped, truncated, or repaired.
  - **Replay Verification (BLOCKER-B-ADMISSION-SERVER-R4-02):** In strict adherence to Gate 5 Release Authority Design R4 (§6.3, §7) and Gate 4 Authority Design R10 (§7.2), all single-use release and environment tokens are guarded against replay. A request is rejected fail-closed if *any* of `workorder_id`, `execution_id`, or `preflight_run_id` has already been recorded in any valid entry within `consumed_admissions.jsonl`:
    - If `workorder_id` was previously recorded: rejected immediately with `ERR_GATE5_WORKORDER_ALREADY_CONSUMED` (Gate 5 R4 §6.3, §7);
    - If `execution_id` was previously recorded: rejected immediately with `ERR_GATE5_WORKORDER_REPLAYED`;
    - If `preflight_run_id` was previously recorded: rejected immediately with `ERR_GATE4_EVIDENCE_REPLAYED` (Gate 4 R10 §7.2).
  - **Source Implementation Status:** Current repository source does **NOT** implement `consumed_admissions.jsonl`, the replay consumption logic, or the AF_UNIX admission listener daemon. These exist exclusively as architectural specifications.

### 4.3 The Boundary Between Admission Authorization and DEP-03 Execution (BLOCKER-B-ADMISSION-SERVER-R1-03)
- **[ACCEPTED-DESIGN]:** A fundamental architectural boundary separates Dispatcher Admission from DaVinci Resolve target mutation:
  1. **Admission Authorization ($\neq$ Target Mutation):**
     - Transmitting `DispatcherAdmissionResponse(status="ADMITTED")` confirms strictly that authorization envelopes are verified and replay tokens are consumed.
     - **Dispatcher admission itself performs ZERO target mutation and makes ZERO calls to DaVinci Resolve APIs.**
  2. **The Source-Verified DEP-03 Execution Protocol (`LiveCanaryDispatchService`):**
     - Following admission, execution transitions to `LiveCanaryDispatchService.dispatch_live_canary()` (`src/services/live_canary_dispatch_service.py`, handoff hash `bbcd3652...`). In strict alignment with checked-in source code, the exact sequence of operations is:
       - **Step 1 (Geometry & Invariants):** Validates single-action geometry and recomputes canonical plan and action digests. Confirms `is_execution_id_committed()` is `False` and `is_target_locked_or_in_doubt()` is `False`.
       - **Step 2 (Signer Oracle Query & Verification):** Transmits `OracleVerificationRequest` to Signer Oracle over `/run/resolveflow/oracle/signer_oracle.sock`; verifies `OracleVerificationResponse` against 11 acceptance invariants (DEP03-R2-02/03). *Oracle clearance occurs BEFORE any journal intent or target reservation.*
       - **Step 3 (Write-Ahead Audit Intent):** Invokes `self._journal.write_audit_intent(...)` -> logs `DISPATCH_ATTEMPT_INTENT` with `fsync()`.
       - **Step 4 (Target Ledger Reservation):** Invokes `self._journal.reserve_target(...)` -> target state in `target_ledger.v2.json` transitions to **`RESERVED`** with `fsync()`.
       - **Step 5 (Durable WAL Reservation):** Invokes `self._journal.write_action_pending(...)` -> logs `ACTION_PENDING` and target state in `target_ledger.v2.json` transitions to **`ACTIVE`** with `fsync()`.
       - **Step 6 (Ephemeral Exclusion Lock):** Invokes `self._journal.acquire_target_lock(...)` -> acquires non-blocking exclusive file lock on `.locks/.lock`.
       - **Step 7 (Adapter Boundary):** Calls `self._adapter.execute_live_canary_action()` -> crosses process boundary into DaVinci Resolve Scripting API.
       - **Step 8 (Terminal Commit & EXHAUSTED State):** Invokes `self._journal.commit_terminal(execution_id, "COMPLETED")` -> target state in `target_ledger.v2.json` transitions from `ACTIVE` to **`EXHAUSTED`** with `fsync()`.
       - **Step 9 (Oracle Notification):** Transmits `OracleTerminalNotificationRequest(terminal_state="COMPLETED")` to Signer Oracle.
  3. **One-Shot Target Semantics & Ephemeral Lock Distinction:**
     - In `durable_dispatch_journal.py` (handoff hash `0d08297e...`), target lifecycle states are strictly: `INITIAL` -> `RESERVED` -> `ACTIVE` -> `EXHAUSTED` (or `IN_DOUBT_LOCKED`).
     - Once a target enters any non-initial state (`RESERVED`, `ACTIVE`, `EXHAUSTED`, or `IN_DOUBT_LOCKED`), `reserve_target()` rejects all subsequent attempts via `DispatchJournalSecurityError(f"ERR_TARGET_NOT_AVAILABLE: Target status is {prev_status}")` (`durable_dispatch_journal.py` lines 661–666).
     - Ephemeral lock release (releasing `.locks/.lock` on socket disconnect or clean abort) does **NOT** clear the durable target reservation in `target_ledger.v2.json`.
     - Strictly **no automatic redispatch, retry, rollback, orphan cleanup, ambiguous-notification replay, or administrative unlock** may be introduced.

### 4.4 Failure and Crash Outcomes Across Durable Boundaries (BLOCKER-B-ADMISSION-SERVER-R1-03, BLOCKER-B-ADMISSION-SERVER-R3-01)
- **[PROPOSAL]:** System outcomes are categorized strictly by the durable commit boundary at which failure or crash occurs:

```
Boundary A: Pre-Consumption         Boundary B: Post-Consumption        Boundary C: Post-Oracle Intent / Active
(Validation Checks)                 (Admission Ledger Committed)        (DEP-03 Mutation Pipeline)
         │                                   │                                   │
         ▼                                   ▼                                   ▼
[Failure / Crash]                   [Failure / Crash]                   [Failure / Crash]
- Zero durable state changes.       - preflight_run_id burned.          - Oracle clearance already granted.
- Replay tokens unconsumed.         - execution_id burned.              - C.1 (Intent-only crash): in execution.log;
- Target ledger untouched.          - Zero target mutation.               journal recovery ignores; zero mutation.
- Client may safely retry.          - Target ledger untouched.          - C.2 (RESERVED crash): target locked RESERVED;
                                    - Response lost / client timeout.     ERR_TARGET_NOT_AVAILABLE; zero mutation.
                                    - Client MUST NOT re-submit.        - C.3 (Post-ACTION_PENDING): IN_DOUBT_LOCKED;
                                                                          permanent local lockdown; zero Oracle notify.
```

1. **Failure / Crash Before Boundary A (Pre-Consumption):**
   - *Scenario:* DTO decode error, invalid signature, expired workorder, target mismatch, or daemon crash during validation.
   - *Outcome:* Socket closed immediately; zero state written to disk; zero replay tokens consumed; target remains completely untouched.
2. **Failure / Crash Between Boundary A and Boundary B (Consumption to Response):**
   - *Scenario:* Admission ledger append succeeds, but daemon crashes before or during `DispatcherAdmissionResponse` transmission over the socket.
   - *Outcome:* `workorder_id`, `execution_id`, and `preflight_run_id` are durably consumed on disk in `consumed_admissions.jsonl` (Boundary B). The client observes socket EOF / connection reset.
   - *Fail-Closed Invariant:* **A lost or ambiguous response must not silently restore consumed evidence or imply the client may safely submit a fresh execution.** The client harness must treat an interrupted admission as an ambiguous outcome and abort without redispatching.
3. **Failure / Crash in Mutation Window (Boundary C) (BLOCKER-B-ADMISSION-SERVER-R2-01, BLOCKER-B-ADMISSION-SERVER-R2-02, BLOCKER-B-ADMISSION-SERVER-R3-01, BLOCKER-B-ADMISSION-SERVER-R4-01):**
   - *Scenario:* Daemon crashes after Oracle verification, during WAL intent (`DISPATCH_ATTEMPT_INTENT`), target reservation (`RESERVED`), WAL active record (`ACTION_PENDING`), or during `execute_live_canary_action()`.
   - *Deterministic Crash & Recovery Rules across Discrete Durable Sub-Boundaries:*
     - **Sub-boundary C.1 — Crash After `DISPATCH_ATTEMPT_INTENT` but Before `reserve_target()` (Intent-Only State) (BLOCKER-B-ADMISSION-SERVER-R3-01, BLOCKER-B-ADMISSION-SERVER-R4-01):**
       - `write_audit_intent()` appends `DISPATCH_ATTEMPT_INTENT` to `execution.log` with `fsync()`.
       - *Source Recovery Limitation:* In accepted `durable_dispatch_journal.py` source (hash `0d08297e...`, lines 498–590), `DurableDispatchJournal._recover_from_files()` reads only the `locks/` directory, `target_ledger.v2.json`, and `execution_journal.wal`; **it does NOT read or reconcile `execution.log`**.
       - *Journal Recovery State:* On restart, journal recovery does not observe this intent-only record and does NOT construct a target reservation in `_target_state` or `target_ledger.v2.json`. The target is **NOT recovered as `RESERVED` by this journal**.
       - *Admission & Availability Policy:*
         - At the Admission Server layer, `workorder_id`, `execution_id`, and `preflight_run_id` were ALREADY durably appended to `consumed_admissions.jsonl` (Boundary B) prior to dispatch. Any attempt to resubmit with the same `workorder_id`, `execution_id`, or `preflight_run_id` is rejected fail-closed at admission with `ERR_GATE5_WORKORDER_ALREADY_CONSUMED`, `ERR_GATE5_WORKORDER_REPLAYED`, or `ERR_GATE4_EVIDENCE_REPLAYED`.
         - At the Journal layer: because `_recover_from_files()` does not inspect `execution.log`, any automatic target locking based purely on an intent-only record is absent in checked-in source. Under post-incident safety policy, any detected presence of an uncommitted `DISPATCH_ATTEMPT_INTENT` in `execution.log` during audit indicates an interrupted dispatch pipeline and mandates administrative investigation before issuing fresh workorders against the target.
         - Zero timeline mutation occurred because execution never reached the adapter boundary.
     - **Sub-boundary C.2 — Crash After `reserve_target()` but Before `ACTION_PENDING` is Durable (BLOCKER-B-ADMISSION-SERVER-R3-01):**
       - `reserve_target()` atomically publishes `target_ledger.v2.json` with status `RESERVED` and populates `_committed_executions`.
       - *Journal Recovery State:* On restart, `_recover_from_files()` reads `target_ledger.v2.json` and reconstructs `_target_state` with status `RESERVED`.
       - *Availability & Error Behavior:* The target remains durably in status `RESERVED`. In strict alignment with checked-in source (`durable_dispatch_journal.py` lines 661–666), any subsequent invocation of `reserve_target()` against this target is rejected fail-closed via `DispatchJournalSecurityError(f"ERR_TARGET_NOT_AVAILABLE: Target status is RESERVED")`.
       - Zero timeline mutation occurred because execution never reached `ACTION_PENDING` or the adapter boundary.
     - **Sub-boundary C.3 — Crash After `ACTION_PENDING` is Durable (BLOCKER-B-ADMISSION-SERVER-R2-01):**
       - Once an `ACTION_PENDING` record is durably written to `execution_journal.wal`, ANY subsequent crash or restart where `commit_terminal()` is missing forces the target into **`IN_DOUBT_LOCKED`** and writes the persistent marker `.in_doubt` (`durable_dispatch_journal.py` lines 552–589).
       - *Crucial Recovery Boundary:* Recovery logic executing in a new process cannot establish from volatile process memory whether the adapter call had begun or completed. Therefore, **every unmatched durable `ACTION_PENDING` record is treated strictly as `IN_DOUBT_LOCKED`, regardless of volatile adapter state.**
     - **Local Recovery Boundary & Absence of Automatic Oracle Notification (BLOCKER-B-ADMISSION-SERVER-R2-02):**
       - Local journal recovery (`DurableDispatchJournal._recover_from_files()`) strictly reconstructs local state and writes the `.in_doubt` marker on disk.
       - **Local recovery alone does NOT emit an Oracle terminal notification and does NOT alter the Oracle's durable state.**
       - Unresolved outcomes remain permanently fail-closed pending manual post-incident administrative inspection. Any future reconciliation or out-of-band notification to the Oracle requires a separate, unimplemented, and separately authorized operational runbook/workorder.

---

## 5. Process Provenance, Trust Roots & System Readiness Limits

### 5.1 Incorporation of Accepted Process-Binding Architecture (Prerequisite B-2 R3)
- **[ACCEPTED-DESIGN]:** In strict conformance with accepted Process-Binding Architecture Design R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-PROCESS-BINDING-DESIGN-R1.md`):
  1. **Entrypoint-Source Integrity Only:** Verifying `reviewed_candidate_sha256` in `SignedExecutionWorkorderRecord` binds strictly the entrypoint source file bytes on disk (`run_gate6.py`). It does **not** bind the complete execution closure (Python interpreter binary `/usr/bin/python3`, standard libraries, dynamic site-packages `.venv`, native libraries `fusionscript.so`, or in-flight RAM).
  2. **Deficiency Against Same-UID Adversary:** The session-confined supervised launcher running under `interactive_user:1000` does **NOT** satisfy the sibling-process threat model under UID 1000:
     - A launcher signing key stored in user space has zero DAC isolation from concurrent sibling processes under UID 1000.
     - Dispatcher trust in a launcher public key does not protect the private key from same-UID compromise.
     - The launcher remains an **explicitly hypothetical and unresolved operational proposal** until an independently enforceable identity, protected attestation key/channel, and exact child binding are specified under separately authorized future workorders.
  3. **Non-Attestation of Process Memory:** Neither `SO_PEERCRED`, caller PID, script file hash, sealed `memfd`, cgroup membership, nor the accepted envelope-only `DispatcherAdmissionRequest` independently proves the executable process image loaded in memory.

### 5.2 Trust-Anchor Resolution & Non-Provisioned Status
- **[ACCEPTED-DESIGN]:** In accordance with Gate 5 Trust Anchor Policy Design R3 (`07B7-GATE6-PHASE2-BLOCKER-B-GATE5-TRUST-ANCHOR-POLICY-DESIGN-R1.md`):
  - **Canonical Path:** `/etc/resolveflow/tech_lead_public_key.pem`
  - **Metadata:** Regular file, mode `0644`, owner `root:root`, SPKI Ed25519 PEM format.
  - **Single Anchor Rule:** Exactly one trust anchor exists on the host. Key rotation immediately invalidates all unconsumed workorders signed by the prior key.
  - **Key-ID Binding Gap:** Standard SPKI PEM contains no key identifier field. Matching `record.tech_lead_key_id` to public key content requires a future mapping manifest or convention.
- **[UNRESOLVED-PREREQUISITE]:** Repository evidence does not establish installation of `/etc/resolveflow/tech_lead_public_key.pem` on the host. Host state is unknown and remains an uninspected, unverified prerequisite. No key ceremony has occurred.

### 5.3 System Readiness & Post-Incident Boundary Limits
- **Prerequisite D-1 (Host DAC Provisioning):** Remains `OPEN / UNVERIFIED`. Socket paths, directory modes, service units, and user accounts on the host have not been verified.
- **Prerequisite C-1 (Orphan State Governance):** Remains `STRICLY OPEN`.
- **Blocker A (Gate 4 Preflight Custody):** Remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and open. No verifier signing key custodian or trust-anchor provisioning exists.
- **Admission Daemon Implementation:** No admission socket listener daemon or replay ledger currently exists in repository source code.

---

## 6. Comprehensive Fail-Closed Failure Matrix

The table below specifies the exhaustive fail-closed response for all possible admission failure modes:

| Failure Mode & Trigger Condition | Technical Detection Point | Precise Error Code | Daemon & Target Disposition | Safety Invariant Impact |
| :--- | :--- | :--- | :--- | :--- |
| **Peer Credential Mismatch** | `SO_PEERCRED` UID != 1000 or GID != 1000. | `ERR_GATE5_PEER_REJECTED` | Immediate socket close; zero bytes read; zero response. | Prevents unauthorized local callers. |
| **Empty Frame** | Frame length header specifies 0 bytes. | `ERR_EMPTY_FRAME` | Socket closed; admission rejected. | Zero mutation; zero state change. |
| **Oversized Frame** | Frame length header exceeds 65536 bytes. | `ERR_FRAME_TOO_LARGE` | Socket closed; admission rejected. | Prevents buffer exhaustion DoS. |
| **Truncated / Extra Bytes** | Incomplete payload or extra bytes received. | `ERR_INVALID_FRAME` | Socket closed; admission rejected. | Prevents framing bypass attacks. |
| **Malformed JSON / Syntax Error** | JSON decode fails in `parse_strict_json()`. | `ERR_INVALID_FRAME` | Socket closed; admission rejected. | Rejects corrupted requests. |
| **Duplicate JSON Keys** | JSON payload contains duplicated key names. | `ERR_DUPLICATE_KEY` | Socket closed; admission rejected. | Prevents parser differential attacks. |
| **Schema Version Invalid** | `schema_version` != 1 or bool-as-int. | `ERR_SCHEMA_VERSION_INVALID` | Request rejected; status `REJECTED`. | Enforces strict contract versioning. |
| **Missing Required Field** | Any of 6 request fields missing from mapping. | `ERR_MISSING_FIELD` | Request rejected; status `REJECTED`. | Prevents incomplete envelope submission. |
| **Unexpected Field** | Request dict contains undeclared field. | `ERR_UNEXPECTED_FIELD` | Request rejected; status `REJECTED`. | Enforces closed-world schema. |
| **Nested Envelope Parse Failure** | Any of 4 nested envelopes fails `from_dict()`. | `ERR_INVALID_NESTED_ENVELOPE` | Request rejected; status `REJECTED`. | Fails closed on inner schema error. |
| **Missing Tech Lead Trust Anchor** | Public key file `/etc/resolveflow/tech_lead_public_key.pem` missing. | `ERR_GATE5_TRUST_ANCHOR_MISSING` | Rejection; stops before replay ledger append. | Zero state change; target untouched. |
| **Insecure Trust Anchor Mode** | Trust anchor writable by non-root or symlink. | `ERR_GATE5_TRUST_ANCHOR_PERMISSIONS_INSECURE` | Rejection; stops before replay ledger append. | Prevents privilege escalation bypass. |
| **Unreadable Trust Anchor** | Trust anchor file cannot be read (`EACCES`). | `ERR_GATE5_TRUST_ANCHOR_UNREADABLE` | Rejection; stops before replay ledger append. | Fails closed on I/O error. |
| **Invalid Tech Lead Signature** | PureEd25519 verification fails on Envelope 4. | `ERR_GATE5_SIGNATURE_INVALID` | Rejection; status `REJECTED`; preflight not burned. | Prevents unauthorized workorder execution. |
| **Invalid Verifier Signature** | PureEd25519 verification fails on Envelope 3. | `ERR_GATE4_SIGNATURE_INVALID` | Rejection; status `REJECTED`; evidence rejected. | Prevents fraudulent preflight assertion. |
| **Invalid Director/Op Signature**| PureEd25519 verification fails on Envelope 1/2. | `ERR_GATE2_SIGNATURE_INVALID` | Rejection; status `REJECTED`. | Prevents unapproved plan execution. |
| **Target Project/Timeline Mismatch**| Envelopes disagree on project/timeline UID. | `ERR_GATE5_TARGET_MISMATCH` | Rejection; status `REJECTED`. | Enforces canary isolation. |
| **Plan / Action Digest Mismatch**| Envelopes disagree on plan or action digest. | `ERR_GATE5_PLAN_DIGEST_MISMATCH` | Rejection; status `REJECTED`. | Prevents execution divergence. |
| **Approval Binding Mismatch** | Envelope 2 digest does not match Envelope 1. | `ERR_GATE5_APPROVAL_BINDING_MISMATCH` | Rejection; status `REJECTED`. | Enforces director approval chain. |
| **Candidate Script Hash Mismatch**| Script hash != `reviewed_candidate_sha256`. | `ERR_GATE5_CANDIDATE_HASH_MISMATCH` | Rejection; status `REJECTED`. | Enforces audited code binding. |
| **Permitted Command Unauthorized**| `permitted_command` != `"canary-trial-execute"`. | `ERR_GATE5_COMMAND_UNAUTHORIZED` | Rejection; status `REJECTED`. | Restricts execution capability. |
| **Operator Challenge Unresolved** | Session challenge registry missing / unverified. | `ERR_OPERATOR_CHALLENGE_UNRESOLVED` | Rejection; status `REJECTED`. | Prevents unanchored operator claims. |
| **Operator Nonce Unresolved** | Operator client nonce replay ledger unverified. | `ERR_OPERATOR_NONCE_UNRESOLVED` | Rejection; status `REJECTED`. | Prevents unanchored nonce reuse. |
| **Expired Preflight Evidence** | $\tau_{\text{now}} > \text{valid\_until\_iso}$ ($> 1800\text{s}$). | `ERR_GATE4_EVIDENCE_EXPIRED` | Rejection; fresh preflight required. | Prevents stale environment execution. |
| **Future-Dated Preflight** | $\tau_{\text{now}} < \text{verified\_at\_iso} - 5.0\text{s}$. | `ERR_GATE4_EVIDENCE_FUTURE` | Rejection; clock sync required. | Rejects clock skew anomalies. |
| **Future-Dated Workorder** | $\tau_{\text{dispatcher}} < \text{issued\_at\_iso} - 30.0\text{s}$ (Gate 5 R4 §6.2). | `ERR_GATE5_WORKORDER_FUTURE` | Rejection; clock sync required. | Rejects clock skew anomalies beyond 30s. |
| **Expired Workorder** | $\tau_{\text{dispatcher}} > \text{valid\_until\_iso}$ ($> 3600\text{s}$, zero grace). | `ERR_GATE5_WORKORDER_EXPIRED` | Rejection; reissuance required. | Enforces strict workorder validity window. |
| **Technical Predicate False** | Any of 5 Gate 4 predicates is `False`. | `ERR_GATE4_PREDICATE_UNSATISFIED` | Rejection; status `REJECTED`. | Preserves non-empty timelines untouched. |
| **Replayed Workorder ID** | `workorder_id` already recorded in admission ledger. | `ERR_GATE5_WORKORDER_ALREADY_CONSUMED` | Rejection; workorder re-use prohibited (Gate 5 R4 §6.3, §7). | Enforces single-use release workorder (BLOCKER-B-ADMISSION-SERVER-R4-02). |
| **Replayed Execution ID** | `execution_id` already recorded in admission ledger. | `ERR_GATE5_WORKORDER_REPLAYED` | Rejection; execution ID re-use prohibited. | Enforces single-use execution nonce. |
| **Replayed Preflight Evidence** | `preflight_run_id` already in admission ledger. | `ERR_GATE4_EVIDENCE_REPLAYED` | Rejection; preflight re-use prohibited (Gate 4 R10 §7.2). | Eliminates preflight replay attacks. |
| **Torn / Corrupted Ledger Tail** | Ledger append interrupted leaving malformed line. | `ERR_ADMISSION_LEDGER_CORRUPT` | Fail-closed stop; admission halted until manual audit. | Rejects corrupted trailing writes fail-closed. |
| **Replay Ledger Lock Contention**| Cannot acquire `flock` on admission ledger. | `ERR_ADMISSION_LEDGER_LOCKED` | Rejection; concurrent request aborted. | Serializes admission transactions. |
| **Consumption Persistence Error**| Disk full / `fsync` fails on admission append. | `ERR_ADMISSION_LEDGER_PERSISTENCE_FAILED` | Fail-closed stop; admission rejected; target untouched. | Guarantees durable consumption before response. |
| **Client Clean Abort Pre-Mutation**| Clean abort signaled before crossing adapter boundary. | `ERR_GATE5_CLIENT_ABORTED` | Dispatcher commits `ABORTED`; target ledger records status `EXHAUSTED` with terminal_result `ABORTED`; subsequent `reserve_target()` fails with `ERR_TARGET_NOT_AVAILABLE: Target status is EXHAUSTED`. | Target excluded; zero mutation. |
| **Daemon Crash: Intent-Only** | Crash after `write_audit_intent()` before `reserve_target()`. | (Intent Recovery) | `execution.log` contains intent, but `_recover_from_files()` does not inspect it; target remains unreserved in journal; `execution_id` burned in admission ledger; zero mutation. | Administrative audit mandatory before any new workorders (BLOCKER-B-ADMISSION-SERVER-R3-01). |
| **Daemon Crash: Target Reserved** | Crash after `reserve_target()` before `ACTION_PENDING`. | (Reservation Recovery) | Journal recovery restores status `RESERVED` from `target_ledger.v2.json`; subsequent `reserve_target()` fails with `ERR_TARGET_NOT_AVAILABLE: Target status is RESERVED`; zero mutation. | Target locked `RESERVED`; zero mutation (BLOCKER-B-ADMISSION-SERVER-R3-01). |
| **Crash In Mutation Window** | Process dies after `ACTION_PENDING` committed. | (In-Doubt Lockdown) | Once `ACTION_PENDING` is durable, journal recovery forces `IN_DOUBT_LOCKED` and writes `.in_doubt` locally; zero automatic Oracle notification; target permanently locked. | Conservative fail-closed containment (BLOCKER-B-ADMISSION-SERVER-R2-01, R2-02). |
| **Oracle Rejection** | Signer Oracle returns status != `ACCEPTED`. | `ERR_GATE6_ORACLE_REJECTED` | Dispatcher fails closed; records failure; target locked. | Prevents un-cleared mutation. |

---

## 7. Implemented vs. Proposed Capability Matrix & Decision DAG

### 7.1 Implemented vs. Proposed Capability Matrix
The table below explicitly categorizes every admission server capability by its actual implementation and governance status:

| Capability / Component | Implemented in Repository Source | Accepted Architectural Design | Proposal / Unresolved Prerequisite |
| :--- | :--- | :--- | :--- |
| **Contract DTOs & Framing** (`DispatcherAdmissionRequest`/`Response`) | **YES**: `src/core/contracts/dispatcher_admission_contracts.py` (DTOs, framing, RFC 8785 serialization). | **ACCEPTED**: Gate 4 R10, Phase 2 Boundary R8. | Complete contract layer; non-operational. |
| **Socket Path & DAC Layout** (`dispatcher.sock`, `02750` dir) | **NO** (Packaging declaration only in scripts/units). | **ACCEPTED**: DEP-01 R4, DEP-02A R4, Gate 4 R10. | **UNRESOLVED-PREREQUISITE** (D-1 unverified on host). |
| **AF_UNIX Socket Listener Daemon** | **NO**: `resolveflow-dispatcher.service` has `ExecStart=/bin/false`. | **PROPOSAL**: Specified in this document (§2.3). | **UNRESOLVED-PREREQUISITE** (Requires daemon implementation). |
| **Bidirectional Mutual `SO_PEERCRED`** | **NO**: Not implemented in daemon. | **ACCEPTED**: Gate 4 Design R10. | **PROPOSAL**: Specified in this document (§2.2). |
| **Tech Lead Signature Verification** | **NO**: Verifier engine not implemented in service. | **ACCEPTED**: Gate 5 Release Authority Design R4. | **PROPOSAL**: Specified in this document (§3.1). |
| **Tech Lead Trust Anchor on Host** | **NO**: Repository contains no key or installation record. | **ACCEPTED**: Gate 5 Trust Anchor Policy Design R3. | **UNRESOLVED-PREREQUISITE** (Key ceremony & Blocker D). |
| **Gate 4 Preflight Verifier Authority**| **NO**: No signing custodian or key exists. | **ACCEPTED-DESIGN-ONLY**: Gate 4 Authority Design R10. | **STRICTLY OPEN** (Blocker A custody unresolved). |
| **Preflight Replay Consumption Ledger**| **NO**: No ledger exists in `src/services/`. | **ACCEPTED**: Gate 4 Authority Design R10. | **UNRESOLVED-PREREQUISITE** (Requires ledger implementation). |
| **Cross-Envelope Semantic Verifier** | **NO**: DTOs validate nominal schema only. | **PROPOSAL**: Specified in this document (§3.3). | **UNRESOLVED-PREREQUISITE** (Requires verifier implementation). |
| **Session-Confined Supervised Launcher**| **NO**: No launcher daemon exists. | **ACCEPTED-DESIGN-ONLY**: Process-Binding Design R3. | **UNRESOLVED-PREREQUISITE** (B-2 same-UID attestation gap). |
| **Durable Dispatch Journal & Oracle IPC**| **YES**: `durable_dispatch_journal.py`, `live_canary_dispatch_service.py`. | **ACCEPTED**: DEP-01, DEP-02B, DEP-03. | Functional core; disconnected from admission. |

### 7.2 Decision DAG & Follow-On Workorders
Acceptance of this static architecture design completes **Prerequisite B-3**. However, it does **not** close Blocker B. To transition from static architecture to operational capability, the following sequential workorders must be authorized:

```
[Prerequisite B-1: Gate 5 Trust Anchor Policy R3 (ACCEPTED)]
       │
       ▼
[Prerequisite B-2: Supervised Launcher Process Binding R3 (ACCEPTED)]
       │
       ▼
[Prerequisite B-3: Dispatcher Admission Server Design R1 (THIS WORKORDER)]
       │
       ├───► [WO-GATE5-ADMISSION-SERVER-IMPL-R1: Admission Listener & Signature Engine Implementation]
       │
       ├───► [WO-GATE4-GATE5-REPLAY-LEDGER-IMPL-R1: Durable Replay Consumption Ledgers]
       │
       ├───► [WO-GATE5-PROVISIONING-SPEC-R1: Trust Anchor Packaging & DAC Provisioning (with Blocker D)]
       │
       └───► [WO-GATE5-PROCESS-ATTESTATION-CONTRACTS-R1: Intra-UID Attestation Protocol (with B-2)]
```

---

## 8. Preserved Safety Invariants & Governance Limits

- **Strict Non-Operational Status:** This document is an architectural design specification only. It grants zero operational, execution, host mutation, key generation, launcher execution, or custody authority.
- **Safety Invariants Unconditionally Preserved:**
  - `ready_to_execute = False` is strictly maintained across all components.
  - Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
  - Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
  - Blocker A / Gate 4 custody remains strictly `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` and `STRICTLY OPEN`.
  - Blocker B remains `OPEN / UNRESOLVED`.
  - Prerequisite C-1 remains `STRICTLY OPEN`; C-2 remains non-operational implementation artifacts only.
  - Prerequisite D-1 remains `OPEN / UNVERIFIED`.
  - Node `WO5 (Live Canary Execution Directive)` remains **COMPLETELY DISCONNECTED** in the execution DAG, with strictly zero incoming and zero outgoing edges.
  - Target project `RF_TRIAL_AEC8F0A7A365D5F5` and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` on `DISPLAY=:1` were outside the scope of this workorder and were not inspected, contacted, or modified.
  - Zero host filesystem mutations, zero key generation, zero credentials, zero network, zero socket IPC, zero daemon execution, and zero Resolve API calls occurred.
