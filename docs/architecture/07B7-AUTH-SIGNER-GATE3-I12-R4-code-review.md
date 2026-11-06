# Gate 3-I12 Revision R4 — Static Code Review

**Decision: CHANGES_REQUIRED**  
**Review scope:** I12 admission cross-binding, authoritative preallocated identity claims, and read-only revocation anchor synchronization.  
**Review mode:** Static source/artifact review. No tests, Resolve, IPC, or live operations were run.

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `a58d3f8c734f8c863392bd54700bb13ce1e32b994269b483f5667c70abcd1bc4`
- Completion report: `.bridge/report.md` — SHA-256 `924cf92f00b23a63a070c0a1de24355919e1088972c612f1b175d29e0ee2876b`
- Source: `src/services/trial_authorization_signer_service.py` — SHA-256 `93e3b78d19df3c505887a526de10680a0fe9cb043e12e72114682e8b4f30e12f`
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `ab46df3f4f8f438f93916249fcfad40a2a0bb599376a0c05e658a8d67b30b8ae`
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I12/pytest.log` — SHA-256 `8c4b51b2a5596bfa8c6c30eac5fbb427528d10f5c0bd173a7d55bd902c3880d3`; report states 234 passed and 2 warnings. Tests were not rerun for this review.

## Findings

### I12-R4-01 — BLOCKER: Preallocated authorization/execution IDs remain caller assertions, not authoritative challenge bindings

R4 correctly changes the admission payload assembler to accept separate `authorization_id` and `execution_id` values (`src/services/trial_authorization_signer_service.py:3144-3207`) and compares the resulting digest with the accepted canonical 16-key assembler in a dedicated test. It also rejects equality between authorization and approval IDs and between execution and client nonces (`:3518-3547`). Those changes address the literal aliasing in R3, but they do not establish who owns or issued the values used in the admission decision.

`ActiveChallengeContext` exposes `preallocated_authorization_id` and `preallocated_execution_id` as ordinary constructor fields (`:446-460`). `OperatorConsentVerificationContext` explicitly describes its expected fields as caller-prepared assertions until a later integrated signer resolves provenance (`:519-555`). Its `__post_init__` copies challenge IDs only when the corresponding `expected_*` value is `None` (`:582-586`); an explicitly supplied expected ID can differ from the value in the challenge, and no equality/provenance check rejects that conflict. Admission then computes its digest from `operator_context.expected_authorization_id` and `expected_execution_id` (`:3550-3556`). Thus a caller can select the identities that are treated as authoritative, independently of the challenge’s preallocated identities, and the local cross-binding result does not prove that the later authorization issuer will use those same values.

The regression test demonstrates parity for manually selected distinct strings (`tests/services/test_trial_authorization_signer_service.py:5370-5442`), but it does not establish a trusted issuer for them or exercise conflicting challenge-vs-context identities. This is also contrary to the workorder stop condition: if source-owned binding cannot be established without inventing caller-trusted claims, stop and report the dependency (`.bridge/task.md`, §6).

**Required correction:** Make the preallocated IDs originate from an authoritative signer-owned challenge/issuance component and bind them immutably to the consent verification context. At minimum, reject any explicit `expected_*` value that differs from its challenge’s preallocated value; however, equality alone is insufficient unless the challenge itself has trusted provenance. If that signer-owned source is outside I12’s approved scope, report the dependency and obtain an approved follow-up slice/design instead of treating caller-supplied fields as authoritative. Add negative coverage for conflicting challenge/context IDs and a positive path proving the same trusted IDs flow into the eventual authorization DTO and the canonical dispatch assembler.

## Findings addressed from R3

- **I12-R3-01:** The implementation no longer aliases `approval_id` to `authorization_id` or `client_nonce` to `execution_id`; the distinct-value assembler parity test checks all 16 fields and bytes. The remaining blocker is authoritative provenance of those preallocated identities, described above.
- **I12-R3-02:** The implementation now acquires `flock(dir_fd, LOCK_EX/LOCK_SH)` before anchor access and releases it in cleanup (`:2215-2229, 2519-2528`). The submitted multiprocess test exercises a reader against an exclusive directory lock, and the read-only fresh-directory case checks no files are created (`tests/services/test_trial_authorization_signer_service.py:5550-5650`). Static inspection finds this addresses the missing-lockfile race while preserving the no-mutation path.

## Safety and scope

The submitted workorder/report retain `ready_to_execute=False`, Gate G07 and Milestones M1/M2/M3 `OPEN`, Gate 6 `NOT ACCEPTED` and incident-only, and prohibit live operations. No authorization issuance, Resolve access, Gate 4–7 activity, or other runtime action is authorized by this review. Submitted test results are recorded as evidence only and were not independently reproduced.
