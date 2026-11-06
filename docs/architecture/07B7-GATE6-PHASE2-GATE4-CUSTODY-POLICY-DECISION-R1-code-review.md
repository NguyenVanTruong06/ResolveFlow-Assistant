# Static Review — Gate 4 Custody Policy Decision Record R1

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DECISION-R1`  
**Decision:** `CHANGES_REQUIRED`  
**Reviewed artifact SHA-256:** `6142b9a389846bfb610a9ab0c9e34847bb62a31fe0064d6d49b810777b663c9e`  
**Review mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY`

## Scope and evidence

Verified the submitted SHA-256 values for the decision record, `.bridge/task.md`, and `.bridge/report.md`; all matched the workspace. Reviewed the decision disposition, quorum accounting, technical limitations, and non-authorization statements against the issued decision workorder and accepted custody design R4. No tests or runtime operations were performed.

## Finding

### CUSTODY-DECISION-R1-01 — MEDIUM: Proposed controls are labeled “Verified Strengths” without implementation or device evidence

Section 3 labels Proposal 1 and Proposal 2 properties as “Verified Strengths,” including an on-chip non-exportable key, configured `PINPolicy=Always`/`TouchPolicy=Always`, and “Absolute key custody isolation” on an encrypted offline workstation. The decision record and accepted custody design both state that no model is selected, procured, provisioned, or activated, and the required device/ceremony verification has not occurred. In particular, the specific hardware, firmware, PKCS#11 path, and configured key policies have not been verified; nor is an offline auditor station established in repository evidence. This wording can be read as attesting to controls that are only conditional properties of future implementations.

**Required correction:** Relabel these as expected or conditional proposal properties, explicitly contingent on procurement, configuration, and evidence from the later ceremony/workorders. Replace “absolute” with the narrower boundary actually supported (key isolated from the Resolve workstation if an independent offline station is implemented as specified). Preserve the no-decision disposition and do not assert that the controls currently exist.

## Decision

The record correctly reports `NO_DECISION_RECORDED_PENDING_FULL_QUORUM`, keeps the custody selection unresolved, and grants no downstream authorization. Correct the evidence-scoping language in §3 and submit a new revision. No custody model is accepted by this review.

## Safety disposition

Static review only. No tests, key access or generation, host changes, sockets, network, Resolve import/connection, or live operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
