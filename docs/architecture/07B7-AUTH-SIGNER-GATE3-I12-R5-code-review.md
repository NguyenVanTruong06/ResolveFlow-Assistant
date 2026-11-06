# Gate 3-I12 Revision R5 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Review scope:** Signer challenge issuance/provenance, immutable challenge-to-context binding, and admission provenance checks.  
**Review mode:** Static source and artifact review only. No tests, Resolve, IPC, or live operations were run.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `950d80a909927d28932775953b4e60fdbedfe4aa5ec6836250fab636dd2cccc3`
- Completion report: `.bridge/report.md` — SHA-256 `e44b36b5e2946779ce7b22c947a3cc9d09cb59b6656947c26fcbc40664c030f5`
- Source: `src/services/trial_authorization_signer_service.py` — SHA-256 `752489eeb7619061d0f97c49b2683037bdfdd00555b09abb1e01ae22d32f8831`
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `bf1cdc42de5f59c8f92ce864bdb6e00f91d7ef81815dadfc2d1998fcadfbe26b`
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `bd66c9904901f2f448db5a165150b64a9f635af8f63a44c8436f0e8388af5c5b`; report states 234 passed, 2 warnings. Not rerun during this review.

## Findings

### I12-R5-01 — BLOCKER: Challenge provenance HMAC uses a public, source-embedded constant

`_SIGNER_CHALLENGE_PROVENANCE_SECRET` is defined as a literal byte string in the source (`src/services/trial_authorization_signer_service.py:447`). `_compute_challenge_provenance_seal()` uses that value as the default signing key (`:450-473`), and `verify_challenge_provenance()` accepts seals derived from it (`:1164-1190`). Anyone who can inspect or import this module can reproduce the HMAC for arbitrary challenge claims; the seal therefore does not prove that a trusted signer issued the challenge. Calling the helper directly also exposes a ready-made minting path. This contradicts the report’s “unforgeable” provenance claim and leaves the R4 trust-boundary finding open.

**Required correction:** Do not embed a reusable HMAC secret in source. Establish the provenance key behind a signer-owned boundary unavailable to admission callers (for example, a separately protected signer capability/service); if that dependency is outside this workorder’s allowlist, stop and report it. Ensure verification does not accept a caller-selected secret or a seal that any module consumer can mint. Add a negative test demonstrating that possession of the public module/API surface cannot produce a challenge accepted as signer-issued.

### I12-R5-02 — HIGH: Challenge issuance accepts caller-selected authorization and execution identities

`TrialAuthorizationSignerService.issue_operator_challenge()` is a public class method and accepts optional `preallocated_authorization_id` and `preallocated_execution_id` values (`src/services/trial_authorization_signer_service.py:1074-1084`). When supplied, these values are used directly (`:1113-1124`) and then sealed as if they originated from the signer (`:1144-1162`). A caller can therefore choose both identity claims and ask this method to certify them; the method validates format and aliasing, but does not establish their source or uniqueness. The positive fixtures exercise this caller-selected path (`tests/services/test_trial_authorization_signer_service.py:4104-4110`).

This does not meet the stated remediation that the signer generates the preallocated IDs. It also undermines the purpose of using the challenge as the authoritative source for the canonical action digest: the seal attests only that the method processed the supplied values, not that the signer independently allocated them.

**Required correction:** Have the signer generate the authorization ID and execution ID internally, with strict formats and uniqueness appropriate to their contracts; remove the caller override parameters or bind them to an independently authorized, signer-owned allocation record. Add tests proving callers cannot select/reuse these identities and that the generated values flow unchanged through consent verification and the eventual authorization DTO/dispatch assembler.

## R4 findings status

- Context now rejects explicit expected IDs that conflict with challenge IDs, and admission checks the challenge seal and identity equality. These checks improve consistency but depend on trustworthy seal issuance, which I12-R5-01 and I12-R5-02 do not establish.
- The R4 read-only anchor synchronization change remains present: the directory descriptor is flocked shared/exclusive before anchor access, and the report describes a multiprocess regression test. No new issue was identified in that path during this static review.

## Safety and scope

The submitted workorder/report retain the verification-only boundary, `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED`/incident-only. This review authorizes no authorization issuance, key provisioning, Resolve access, Gate 4–7 activity, or live operations. Submitted test results are recorded as evidence only and were not independently reproduced.
