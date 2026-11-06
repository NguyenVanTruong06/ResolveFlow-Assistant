# Static Review — Gate 4 Custody Policy Design R3

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1`  
**Revision reviewed:** `R3`  
**Decision:** `CHANGES_REQUIRED`  
**Reviewed artifact SHA-256:** `497853c09b389acc9104bed2af1cdb134bcaecc3d24a0fdc745283966e268579`  
**Review mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY`

## Scope and evidence

Verified the submitted SHA-256 values for the design document, `.bridge/task.md`, and `.bridge/report.md`; all matched the workspace. Reviewed the R2 remediation and compared the decision authority and approval requirements in §§8.2–8.3 with the next action in §9. No tests or runtime operations were performed.

## Remediation verification

`CUSTODY-R2-01` is remediated: `TouchPolicy=Always` is defined as requiring touch per private-key operation; the 15-second cached-touch interval is attributed only to `Cached`; timeout behavior is attributed to the selected host PKCS#11 caller/driver; and the unsupported unconditional sub-15-second bound has been removed. This matches Yubico's documented PIV policy semantics ([Yubico PIV touch policies](https://docs.yubico.com/yesdk/users-manual/application-piv/pin-touch-policies.html)).

The R1 remediations remain present: Proposal 1 distinguishes direct PureEd25519/JCS signing from WebAuthn and pre-hashing; Proposal 2 is limited to key custody and does not claim authenticated live evidence; replay protection remains a future, unimplemented control.

## Finding

### CUSTODY-R3-01 — MEDIUM: Governance approval quorum conflicts with the stated next action

Section 8.3, line 440, requires written concurrence from the Technical Lead, Creative Director, and Host System Administrator before a proposal transitions to `ACTIVATED_POLICY`. Section 9, lines 504–505, instead directs the Technical Lead and Human Operator to “formally adopt Proposal 1” and authorize the key-generation workorder. The latter can be read as a sufficient two-party approval, contradicting the explicit three-party approval condition and omitting two required authorities.

**Required correction:** Make §9's next action a convening/recommendation step only, explicitly subject to the full §8.3 approval quorum; alternatively, reconcile §8.3 if the intended authorized quorum is different. Do not imply adoption or authorize key generation until the governing decision is recorded with the required approvals. Preserve `selected_custody_model: UNRESOLVED_PENDING_GOVERNANCE_APPROVAL` meanwhile.

## Decision

The touch-policy timing finding is resolved, and the custody model remains appropriately unselected. The approval-path inconsistency prevents this decision-ready governance document from being accepted as written. Correct `CUSTODY-R3-01` and submit a new revision. This review grants no custody selection, key-generation, provisioning, or execution authority.

## Safety disposition

Static review only. No tests, key access or generation, host changes, sockets, network, Resolve import/connection, or live operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
