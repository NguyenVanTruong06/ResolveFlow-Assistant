# Static Review — Gate 6 R2 Live Boundary Readiness Assessment

**Review date:** 2026-11-04  
**Decision:** `CHANGES_REQUIRED`  
**Review mode:** Read-only review of the task, readiness assessment, report, and cited dispatcher source. No tests, harness, Resolve SDK, IPC, or UI were run.

## Evidence reviewed

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `.bridge/task.md` | `b0d1282815d09a6fde55fa14ed075cd66c5dc5bfbdd95d25e5f91f4eb51c93dd` | Matches issued GATE6-R2 workorder |
| `docs/architecture/07B7-GATE6-R2-live-boundary-readiness.md` | `e3b8b03feab9e6c54a916f381b34b0a550510f496b9aaf08527437886616f4e2` | Matches submission |
| `.bridge/report.md` | `6051408a0601b19fb3cf0972d61a0850bf52d0845befd815c39fe6d25246bf01` | Matches submission |
| `src/services/trusted_canary_dispatch_service.py` | `ca3c28a1b7cf5d9ea37c13b4c38330f5bb0ed4e56fbff83638606132a340a658` | Source supports the mock-only / no-live conclusion |

The reported absence of `/tmp/resolveflow-gate6-r2` and of the archive harness was confirmed. The assessment correctly stops at `NO_ACCEPTED_LIVE_BOUNDARY` and does not create a candidate harness.

## Findings

### G6-R2-01 — MEDIUM: I16 signer issuance capability is misclassified as production-viable

The component inventory describes `TrialAuthorizationSignerService` as a production-viable core providing durable nonce ledger and HMAC issuance. The I16 issuance entrypoint and signing capability are explicitly documented in `trial_authorization_signer_service.py` as local-test-only, use ephemeral test keys, and provide no production key custody or daemon. The same assessment correctly says elsewhere that only local-test key/signing primitives exist, so these statements conflict.

**Required:** Correct the inventory to separate reusable pure verification logic from the local-test-only I16 issuance implementation. Do not describe the current issuance path as production-capable absent a separately accepted production key boundary.

### G6-R2-02 — LOW: Live-adapter rejection error code is stated inaccurately

The assessment says the constructor raises `ERR_LIVE_ADAPTER_PROHIBITED` when given `LiveResolveCanaryAdapter`. The constructor first checks `type(adapter) is IsolatedResolveTrialAdapter` and raises `ERR_INVALID_ADAPTER_TYPE` for the separate `LiveResolveCanaryAdapter` class before the later explicit live-adapter guard is reached. The conclusion that the live adapter is rejected is correct, but the stated observed error is not.

**Required:** Describe the exact-type guard as the first rejection for the current live adapter, and note that the later live-adapter guard is a separate defense (or correct the exception description to match the actual path).

## Disposition

The principal readiness finding is supported: no accepted live signer/oracle/dispatcher boundary is present, and suppressing the candidate harness was the correct action. The two documentation corrections above are required before accepting the assessment as a reliable implementation baseline.

No challenge was minted, no harness or test was run, and no Resolve operation occurred. `ready_to_execute = False`; G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. Any future workorder must not authorize live execution based on this assessment until the findings are corrected and the live-boundary dependency is addressed in its own reviewed scope.

---

## Remediation R2 Re-review Addendum

**Review timestamp:** 2026-11-04T19:46:55Z (UTC)  
**Decision:** `ACCEPTED_FOR_READINESS_ASSESSMENT_ONLY`

### Artifact recheck

| Artifact | SHA-256 observed | Result |
|---|---|---|
| `docs/architecture/07B7-GATE6-R2-live-boundary-readiness.md` | `5ad97b77b14a5b2ee3f78a68955d58fd04d8af53accc965599163afe3f7a7974` | Matches R2 remediation submission |
| `.bridge/report.md` | `52aa252daafb564302e34be855e07cc2b88b6f28fa06d898a7a778636b886f60` | Matches R2 remediation submission |
| `.bridge/task.md` | `b0d1282815d09a6fde55fa14ed075cd66c5dc5bfbdd95d25e5f91f4eb51c93dd` | Workorder unchanged |

### Findings disposition

- **G6-R2-01 — Resolved.** The inventory now separates reusable verification logic from I16's local-test-only HMAC issuance and explicitly records the absence of production key custody/daemon.
- **G6-R2-02 — Resolved.** Sections 1 and 4.1 now accurately state that the exact-type guard rejects the live adapter and its subclasses first. The following `isinstance` guard is documented as redundant/unreachable under the current condition order, not as an effective defense-in-depth layer.

The assessment's determination remains `NO_ACCEPTED_LIVE_BOUNDARY`. No candidate harness or `/tmp/resolveflow-gate6-r2` directory exists. This accepts the static readiness assessment and its stop decision only; it does **not** approve a live boundary, Gate 6 execution, or Gate 6 acceptance. `ready_to_execute = False`; G07/M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, challenge mint, Resolve import, IPC, or live operation was performed during this review.

---

## Remediation R1 Re-review Addendum

**Review timestamp:** 2026-11-04T19:44:56Z (UTC)  
**Decision:** `CHANGES_REQUIRED`

### Disposition of prior findings

- **G6-R2-01 — Resolved.** The inventory now distinguishes reusable verification algorithms from the I16 HMAC issuance path, explicitly classifying I16 issuance as local-test-only with ephemeral keys and no production key custody/daemon.
- **G6-R2-02 — Partially resolved; remains open.** The assessment now correctly says the `LiveResolveCanaryAdapter` first fails the exact-type guard with `ERR_INVALID_ADAPTER_TYPE`. However, it still characterizes the subsequent `isinstance(adapter, LiveResolveCanaryAdapter)` check as an effective defense-in-depth guard against subclassing. With the current condition order, `type(adapter) is not IsolatedResolveTrialAdapter` rejects every different class (including every `LiveResolveCanaryAdapter` subclass) before the second check. Since the two concrete classes are unrelated, the second check is unreachable under the present type gate; it is not an effective additional defense for this input.

**Required:** In the readiness assessment, describe Tier 2 as a redundant/unreachable guard under the current exact-type gate, not as a functioning defense-in-depth layer. No source change is authorized by the static readiness workorder; if the code is to be changed, obtain a separate bounded implementation workorder and review.

The main determination `NO_ACCEPTED_LIVE_BOUNDARY` remains supported. No harness or `/tmp` directory was created, and there was no test run, challenge mint, Resolve import, IPC, or live operation. Preserve `ready_to_execute = False`, G07/M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED` / incident-only.
