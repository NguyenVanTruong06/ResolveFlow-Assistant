# Static Review — Gate 4 Custody Policy Design R2

**Workorder:** `G07-07B7-GATE6-PHASE2-GATE4-CUSTODY-POLICY-DESIGN-R1`  
**Revision reviewed:** `R2`  
**Decision:** `CHANGES_REQUIRED`  
**Reviewed artifact SHA-256:** `5e5bf05d23f6ddcc72f58a7a493116e18d824ccd2fd58c08f2063cdd0e62ea21`  
**Review mode:** `DOCUMENTATION_AND_STATIC_SOURCE_REVIEW_ONLY`

## Scope and evidence

Verified the submitted SHA-256 values for the design, task, and report; all matched the workspace. Reviewed R2 against the frozen SignedPreflightEvidenceEnvelope contract and Gate 4 Authority Design R10. Also checked Yubico's current PIV documentation for the stated touch-policy semantics. No tests or runtime operations were performed.

## Remediation verification

- `CUSTODY-R1-01`: The design now distinguishes WebAuthn assertion signing and pre-hashing from direct PureEd25519 over canonical JCS bytes, and acknowledges the lack of a secure payload display.
- `CUSTODY-R1-02`: Proposal 2 is correctly limited to key custody; its host-produced evidence bundle is explicitly unauthenticated and cannot establish live Resolve truth.
- `CUSTODY-R1-03`: Replay-ledger behavior is correctly labeled future, unimplemented, and non-operational.

## Finding

### CUSTODY-R2-01 — MEDIUM: `TouchPolicy=Always` does not establish the stated 15-second timeout or sub-15-second signing bound

Sections 4.1.2 and 9 (lines 236–240 and 496–498) say that `TouchPolicy=Always` waits for touch within a 15-second hardware timeout and that the flow executes in under 15 seconds. The cited Yubico PIV policy documentation defines `Always` as requiring touch for each key operation; the 15-second interval is documented for `Cached` touch, where a previous touch is reused within that window. It does not establish a universal 15-second timeout or a maximum end-to-end signing latency for `Always`. The design also does not pin a particular PKCS#11 module/driver and its timeout behavior.

This overstates an operational timing guarantee used to compare Proposal 1 with the 30-minute envelope lifetime. It also makes `ERR_GATE4_DEVICE_TOUCH_TIMEOUT` appear tied to a device-level timeout when the timeout may be imposed by the host API or caller.

**Required correction:** Describe `TouchPolicy=Always` only as requiring touch on every private-key operation. Attribute any timeout to the selected device/API and verify it for the exact model, firmware, PIV slot, and PKCS#11 module before making an SLA or latency comparison. Remove the unconditional `<15s` claim and define the fail-closed timeout source and value as a deployment-specific prerequisite.

## Decision

The prior three findings are remediated. The custody decision remains correctly `UNRESOLVED_PENDING_GOVERNANCE_APPROVAL`, and R2 still grants no operational authority. Correct the unsupported touch-timeout/latency claims and submit a new revision for review. No custody model is accepted by this review.

## Safety disposition

Static review only. No tests, key access or generation, host changes, sockets, network, Resolve import/connection, or live operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.

## Technical reference

- Yubico, [PIN, touch, and biometric policies](https://docs.yubico.com/yesdk/users-manual/application-piv/pin-touch-policies.html): `Always` requires touch for every key operation; `Cached` is the 15-second touch-cache policy.
- Yubico, [PIV touch policy extensions](https://developers.yubico.com/PIV/Introduction/Yubico_extensions.html): defines `Always` and `Cached`, including the 15-second cached interval.
