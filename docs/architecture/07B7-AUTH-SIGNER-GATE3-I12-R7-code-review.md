# Gate 3-I12 Revision R7 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Review scope:** Challenge provenance signing and verification, trust-anchor selection, and workorder scope.  
**Review mode:** Static source/artifact review only. No tests, Resolve, IPC, or live operations were run.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `10ace39ffa9fd7e4143ed3b08ea30b825505d408afeeb78c7bc31bd7c020d6a1`
- Completion report: `.bridge/report.md` — SHA-256 `92a7179893ca192ab11948e417ac4ba42172384cbff74675dbad2651701a56f9`
- Source: `src/services/trial_authorization_signer_service.py` — SHA-256 `819530f70175e63a9bdd2f8fefa3a011e0416bbbef6a46fad1ed228211bc722a`
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `f56e9fe906c26ed56d54dd00128eca0e665ddc633dbe1688a5762cd6270ae997`
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `7ee32fb176c9cf6d2f3de37cbcf1758a42f62d05b1f90192872dd06976903492`; report states 236 passed, 2 warnings. Not rerun during this review.

## Findings

### I12-R7-01 — BLOCKER: The caller selects both the challenge signing key and the verification key

`issue_operator_challenge()` accepts `signer_private_key` directly from its caller and signs the challenge claims with that key (`src/services/trial_authorization_signer_service.py:1049-1068, 1114-1123`). The admission API separately accepts `signer_public_key` directly from its caller and only normalizes its cryptographic type (`:3393-3407, 3483-3488`); it does not compare that key with a pinned signer identity or another trusted source. `verify_challenge_provenance()` then verifies the challenge using exactly that caller-provided key (`:1136-1172`).

A caller can therefore generate its own Ed25519 key pair, issue a challenge using its private key, and pass the matching public key to admission. The provenance check will verify a self-created signature; it does not establish that an independently trusted Signer issued the challenge. The rogue-key regression does not cover this case: it creates a rogue signature but checks it against the fixture’s different `signer_pub` (`tests/services/test_trial_authorization_signer_service.py:5758-5787`). The positive fixture creates an ephemeral key pair locally and supplies the public key to admission (`:4010-4021, 5697-5711`), confirming that trust is selected by the caller.

**Required correction:** Establish the signer public key from a trusted, caller-independent authority (or a narrowly scoped signer capability that cannot be supplied by admission callers). The private key must remain owned by that signer boundary; callers must not pass arbitrary signing keys to a public helper and then nominate the corresponding verification key. Add a regression that creates a rogue pair, signs a challenge, supplies the rogue public key to the full admission path, and proves admission rejects it because the key is not the pinned trusted signer key.

### I12-R7-02 — HIGH: R7 adds challenge signing outside the verification-only workorder scope

The active workorder defines I12 as verification-only and prohibits signing, private-key provisioning/loading, authorization issuance, and related service wiring (`.bridge/task.md`, §6, especially the prohibition on “signing”). R7 adds a challenge issuance method that accepts a private key and performs `signer_private_key.sign(...)` (`src/services/trial_authorization_signer_service.py:1049-1058, 1122-1123`). This is challenge signing rather than `TrialExecutionAuthorization` signing, but it still performs a signing operation that the workorder forbids and introduces a minting API without the approved independent signer boundary.

**Required correction:** Keep I12 within its authorized verification-only scope. If challenge issuance/signing is required, report the dependency and obtain a separate bounded workorder that defines the trusted signer-key owner and caller boundary before implementing it.

## R6 remediation status

- The module-level HMAC secret was removed and replaced by Ed25519 challenge signatures. This avoids embedding a reusable secret, but the signing and verification keys are both caller-selected, so the trust-boundary blocker remains unresolved.
- The signer now generates the preallocated authorization and execution IDs internally, and context-to-challenge equality checks remain present. These changes do not resolve the lack of an independently trusted signer identity.

## Safety and scope

The submitted artifacts state `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, Gate 6 `NOT ACCEPTED` and incident-only, and zero live operations. This review authorizes no key provisioning, authorization issuance, Resolve access, Gate 4–7 activity, or live operations. Submitted test results are recorded as evidence only and were not independently reproduced.
