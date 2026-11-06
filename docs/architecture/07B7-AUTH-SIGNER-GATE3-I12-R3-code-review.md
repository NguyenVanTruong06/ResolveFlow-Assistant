# Gate 3-I12 Revision R3 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Review scope:** `verify_trial_authorization_admission_for_local_test()` and the read-only revocation-anchor path added/used by I12 R3.  
**Review mode:** Static source and artifact review only. No tests, Resolve, IPC, or live operations were run.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `dc867ff37bc8afb80410316ab7df48ad691965029664379d89ce904bff025f65`
- Completion report: `.bridge/report.md` — SHA-256 `c148e242684766677d60be7cabb62cd459301903d351429e0079cd9b7476f517`
- Source: `src/services/trial_authorization_signer_service.py` — SHA-256 `97b6fae193da58a729868a0424bc75642d3af80cff8a10efc96923cd3fda8b4d`
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `bba2e3b630ae5f948f80f74ca89f3a4e16288e3689dfab134fb522d825a412c4`
- Submitted focused-test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `d332edfbf4bca4b9d2eae57148de749d464a69ec015515900043db227377b4d2`. The report states 232 passed; this review did not rerun them.

## Findings

### I12-R3-01 — BLOCKER: Admission digest substitutes approval and consent identities for authorization and execution identities

`assemble_canonical_action_payload_for_admission()` constructs the 16-key payload with `authorization_id = approval_envelope.approval_record.approval_id` and `execution_id = operator_consent_envelope.client_nonce` (`src/services/trial_authorization_signer_service.py:3078-3083, 3106-3112`). The admission verifier then treats a digest recomputed from that payload as the authoritative `action_payload_digest_sha256` (`:3416-3452`).

This does not match the accepted 07B5 canonical assembler. `assemble_canonical_action_payload(action, auth, execution_id)` serializes `auth.authorization_id` and the supplied execution ID, and enforces `execution_id == auth.execution_id == auth.token_nonce` (`src/core/contracts/trial_authorization_contracts.py:995-1003, 1041-1047`). 07B7 separately defines `authorization_id` as Signer-generated and `approval_record_id` as the Director approval identity, and defines `execution_id`/`token_nonce` as the single-use execution nonce (`docs/architecture/07B7-independent-authorization-signing-workflow-plan.md:471-475, 499-502`). 07B5 expressly prohibits equating `approval_id` and `authorization_id` (`docs/architecture/07B5-isolated-resolve-acceptance-gate-design.md:747-751`) and requires the single shared assembler and byte-for-byte digest agreement across admission and dispatch (`:1027-1035`).

Consequently, the R3 admission digest describes a different identity envelope from the eventual authorization-bound dispatch payload. It can reject the correct dispatch digest or accept a digest that is not the digest of the payload later authorized for dispatch. The added test fixture repeats the same substitutions (`tests/services/test_trial_authorization_signer_service.py:4007-4026`), so it does not detect this contract drift.

**Required correction:** Do not alias these identifiers. Establish the authoritative future `authorization_id` and execution nonce before calculating/signing the consent-bound action digest, then call the accepted canonical assembler (or its exact shared contract) with those identities. If the existing challenge/consent lifecycle cannot know those values at that point, stop and report the contract/lifecycle dependency for an approved design change; do not invent a local identity mapping. Add a regression that uses distinct approval ID vs authorization ID and client nonce vs execution ID and compares the admission digest byte-for-byte with the accepted assembler output.

### I12-R3-02 — HIGH: Read-only anchor verification is unsynchronized across processes when the lock file is absent

In the `advance=False` branch, failure to open `revocation_epoch.lock` with `ENOENT` sets `lock_fd = None` (`src/services/trial_authorization_signer_service.py:2177-2194`). The code then skips `flock()` entirely (`:2196-2213`) but proceeds to read `revocation_epoch.anchor` and can return success when it is absent (`:2215-2230, 2322-2326`). `_REVOCATION_EPOCH_ANCHOR_LOCK` only coordinates threads in this process; it does not exclude another process using the `LOCK_EX` path to create the lock and advance the anchor.

A reader can therefore observe “no anchor” and accept a manifest while another process concurrently creates the lock and commits a newer epoch/revocation state. The no-mutation guarantee is met, but the admission decision is not serialized against cross-process anchor advancement, leaving a stale-read window in the security check.

**Required correction:** Define a non-mutating interprocess synchronization contract. For example, require the lock file to be provisioned before read-only admission and fail closed if it is absent, or introduce an already-existing stable lock object/authority that both reader and writer must lock. Do not create a lockfile in the read-only path. Add a multiprocess race test proving admission cannot succeed from a stale anchor snapshot while an exclusive advance is in progress.

## Safety and scope

The submitted report and workorder retain the stated local-test-only boundary, `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, and Gate 6 `NOT ACCEPTED`/incident-only. No evidence in this review authorizes authorization issuance, Gate 4–7 work, Resolve access, or live operations. The reported test result is recorded as submitted evidence, not independently reproduced.
