# Static Review — Gate 4 Custody Policy Decision Record R2

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DECISION-R1`  
**Revision reviewed:** `R2`  
**Decision:** `CHANGES_REQUIRED`  
**Reviewed artifact SHA-256:** `01f517dfa13bb629152bc8b477f1624f8e1a305e6b641bd953d0748a1ddeffa8`  
**Review mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY`

## Scope and evidence

Verified the submitted SHA-256 values for the decision record, `.bridge/task.md`, and `.bridge/report.md`; all matched the workspace. Reviewed the R1 remediation, full-quorum disposition, proposal-property qualifiers, and non-authorization statements against the issued workorder and accepted custody design R4. No tests or runtime operations were performed.

## Remediation verification

`CUSTODY-DECISION-R1-01` is substantially remediated: §3.1 and §3.2 now label device/station security properties as expected and conditional, and Proposal 2's isolation claim is properly limited to the Resolve workstation filesystem if the proposed station is established.

## Finding

### CUSTODY-DECISION-R2-01 — MEDIUM: Procurement absence is asserted beyond repository evidence

Section 3.1 says “no device has been procured, configured, or benchmarked.” The record otherwise scopes its factual quorum accounting to repository evidence, but this sentence asserts the real-world nonexistence of procurement/configuration/benchmark activity. The workorder and available repository evidence establish that no device is evidenced, verified, or provisioned for this workflow; they do not establish that no device has been procured outside the repository. This is the same evidence-scope boundary the workorder requires the record to preserve.

**Required correction:** Replace the categorical claim with an evidence-scoped statement, such as: “No repository evidence establishes that a device has been procured, configured, or benchmarked for this workflow; those properties remain unverified.” Do not infer physical inventory or off-repository activity.

## Decision

The record correctly remains `NO_DECISION_RECORDED_PENDING_FULL_QUORUM`, and custody remains unresolved with no downstream authorization. Correct the evidence-scope statement and submit a new revision. No custody model is accepted by this review.

## Safety disposition

Static review only. No tests, key access or generation, host changes, sockets, network, Resolve import/connection, or live operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
