# Gate 4 Verifier Key Custody Governance Decision Record

**Document ID:** `07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DECISION-R1`  
**Revision:** `R3`  
**Status:** `NO_DECISION_RECORDED_PENDING_FULL_QUORUM`  
**Parent Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DECISION-R1`  
**Date:** 2026-11-05  
**Governing Standard:** Gate 4 Authority Design R10, Gate 4 Custody Policy Design R1 Revision R4 (`07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1.md`), Static Review R4 (`07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R4-code-review.md`)  
**Operating Mode:** `GOVERNANCE_DECISION_RECORD_ONLY; NO_IMPLEMENTATION; NO_KEY_GENERATION_OR_ACCESS; NO_HOST_OR_STATE_ACCESS; NO_SOCKET_OR_NETWORK; NO_RESOLVE; NO_LIVE_OPERATIONS; READY_TO_EXECUTE_FALSE`

---

## 1. Governance Context & Reference Baseline

### 1.1 Options Analysis Acceptance Scope
Workorder `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1` Revision R4 established a decision-ready options analysis comparing candidate custody models for the Gate 4 Preflight Verifier private key (`verifier_key_id`).

In `docs/architecture/07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R4-code-review.md`, Technical Lead Codex formally accepted Revision R4 under the restricted status:
$$\mathbf{ACCEPTED\_FOR\_GOVERNANCE\_OPTIONS\_ONLY}$$
That acceptance strictly established that:
1. The comparative options analysis is technically sound and decision-ready.
2. Custody model selection was **not** approved or activated.
3. No key generation, ceremony execution, trust-anchor deployment, host provisioning, or live execution was authorized.

### 1.2 The Four-Authority Governance Quorum
As formally codified in Custody Design R4 §8.3 and confirmed in the R4 review, transitioning any custody proposal to an `ACTIVATED_POLICY` requires explicit, written concurrence from **all four** recognized project governance authorities:
1. **Technical Lead (Codex / Engineering Authority):** Authority over system architecture, code review, cryptographic invariants, and workorder issuance.
2. **Creative Director:** Authority over artistic boundaries, project/timeline target definitions, and creative approvals.
3. **Human Operator (`interactive_user:1000` / `vanntruongg`):** Authority over desktop interactive sessions and physical token custody on the editing workstation.
4. **Host System Administrator (`root:0`):** Authority over host operating system configuration, POSIX principal management, systemd services, `/etc/resolveflow/` Class A trust root deployment, and host DAC enforcement.

---

## 2. Decision Status & Factual Quorum Accounting

### 2.1 Formal Decision Disposition
$$\mathbf{Decision: \quad NO\_DECISION\_RECORDED\_PENDING\_FULL\_QUORUM}$$
$$\mathbf{selected\_custody\_model: \quad UNRESOLVED\_PENDING\_GOVERNANCE\_APPROVAL}$$

In strict accordance with Workorder `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DECISION-R1` §1, agreement cannot be inferred from silence, prior standing authorizations, review acceptances, or statements made by other entities. Because explicit written concurrences from all four decision authorities are not simultaneously present in verified repository evidence, **no custody model is selected, approved, or activated**.

### 2.2 Factual Status of Decision Authorities

| Decision Authority | Governance Role | Authority Status in Repository Evidence | Concurrence Disposition |
| :--- | :--- | :--- | :--- |
| **Technical Lead** | Architecture, Cryptographic Policy & Engineering Review | Documented technical preference for Proposal 1 (Hardware Security Device with Pure Ed25519 PKCS#11) in design options analysis and static review R4. Explicitly stated that governance adoption remains blocked pending full quorum concurrence. | **Pending Joint Quorum Review** (No unilateral adoption) |
| **Creative Director** | Project & Timeline Target Authority | Zero written concurrence recorded in repository records for any custody proposal. | **ABSENT** |
| **Human Operator** (`interactive_user:1000`) | Workstation Interactive Context & Token Custodian | Zero written concurrence recorded in repository records for any custody proposal. | **ABSENT** |
| **Host System Administrator** (`root:0`) | Host DAC & Class A Trust Anchor Authority | Zero written concurrence recorded in repository records for any custody proposal. | **ABSENT** |

---

## 3. Preservation of Technical Constraints & Known Residual Deficits

Any future governance evaluation must evaluate the candidate proposals against the immutable technical realities established in Custody Design R4:

### 3.1 Proposal 1 (Hardware Security Device — Pure Ed25519 PKCS#11)
- **Expected Conditional Properties (Contingent on Future Procurement & Configuration):** If procured with firmware supporting RFC 8032 PureEd25519 via PKCS#11 (e.g. YubiKey 5 Series $\ge 5.7$) and configured with `PINPolicy=Always` and `TouchPolicy=Always`, the device would provide on-chip non-exportability, require contemporaneous human presence per signature, stop unattended background script forgery under same-UID (`interactive_user:1000`), and operate without network sockets (`NO_SOCKET_OR_NETWORK`).
- **Verification Prerequisite:** No repository evidence establishes that a device has been procured, configured, or benchmarked for this workflow; those properties remain unverified.
- **Known Residual Deficit (No Trusted Display):** Standard hardware security tokens lack an isolated secure display. A human capacitive touch proves contemporaneous physical presence at the token, but does **not** prove that the human inspected or verified the semantic claims of the 20-field canonical JCS payload.
- **Timing & Driver Dependencies:** Any timeout before aborting an unfulfilled touch prompt is governed by the host PKCS#11 caller/driver, which must be formally benchmarked and verified during ceremony testing.

### 3.2 Proposal 2 (Offline Auditor Station — Air-Gapped Key Custody)
- **Expected Conditional Properties (Contingent on Future Station Implementation):** If an independent, encrypted, physically air-gapped workstation were established, private key material would be isolated from the Resolve workstation filesystem.
- **Verification Prerequisite:** No offline station, independent auditor role, or secure transfer media is currently established or verified in repository evidence.
- **Known Architectural Deficit (Unauthenticated Live State Truth):** The offline station receives evidence bundles produced entirely by `interactive_user:1000` scripts on the Resolve host. SHA-256 bundle hashes prove only transport integrity; they do **not** prove that the observations originated from a genuine, unmanipulated DaVinci Resolve process on `DISPLAY=:1`.
- **Operational Latency:** Manual sneaker-net transfer realistically requires 15–25 minutes, creating severe risk of expiring the contract's $\le 1800.0\text{s}$ (30-minute) validity window before Dispatcher admission.

### 3.3 Replay Defense Boundary Status
- Replay prevention via Dispatcher atomic preflight run consumption (`consumed_preflight_runs.jsonl`) is a **REQUIRED FUTURE CONTROL** that is **CURRENTLY UNIMPLEMENTED AND STRICTLY NON-OPERATIONAL**.
- The Dispatcher admission listener daemon does not exist, and Dispatcher Admission Contracts R1 R2 established only pure in-memory DTOs and framing helpers. Replay defense cannot be claimed as an active operational assurance.

---

## 4. Explicit Non-Authorization & Safety Invariants

1. **Zero Downstream Authorization:**  
   This decision record does **not** authorize key generation, key handling, ceremony execution, trust-anchor deployment, host DAC changes, systemd unit provisioning, socket creation, network access, or candidate execution.
2. **Blockers A, B, C, and D Remain Open:**
   - **Blocker A (Gate 4 Preflight Authority):** Unresolved pending full-quorum custody approval, ceremony execution, trust anchor provisioning, and harness implementation.
   - **Blocker B (Gate 5 Release Authority):** Unresolved.
   - **Blocker C (Signer Oracle Orphan Governance):** Unresolved.
   - **Blocker D (Host DAC Provisioning):** Unresolved.
3. **Preserved Incident Safety Invariants:**
   - `ready_to_execute = False` is strictly maintained.
   - Gate 6 strictly remains `NOT ACCEPTED` / incident-only.
   - Gate G07, Milestones M1, M2, and M3 strictly remain `OPEN`.
   - Node `WO5 (Live Canary Execution Directive)` in decision DAG remains completely disconnected with strictly zero incoming edges.
   - Target project `RF_TRIAL_AEC8F0A7A365D5F5` (UID `4fd4272d-f0bc-445c-9490-5723cd6831c2`) and timeline `RF_TRIAL_TL_AEC8F0A7A365D5F5` (UID `ba6a6775-8ec4-41a4-bf2f-704de902e68d`) on `DISPLAY=:1` remain untouched and empty.

---

## 5. Required Governance Inputs for Model Activation

To transition from `NO_DECISION_RECORDED_PENDING_FULL_QUORUM` to a definitive model selection, the following specific governance actions are required:
1. **Convening of the Full Governance Quorum:** Formal review session attended by Technical Lead, Creative Director, Human Operator, and Host System Administrator.
2. **Individual Written Concurrences:** Submission and recording of explicit written concurrences from each of the four authorities specifying:
   - Authority identity and role.
   - Date and cryptographic/audit context of concurrence.
   - Exact approved custody model (Proposal 1, Proposal 2, or formal rejection of all proposals).
   - Acknowledgment of technical limitations (e.g. absence of trusted display for Proposal 1).
3. **Issuance of Successor Workorder:** Issuance of an updated decision workorder to record the verified full-quorum concurrence before any ceremony or provisioning workorders may be authorized.
