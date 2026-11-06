# G07-AUTH-SIGNER-GATE3-I4 Revision R1 — Static Code Review

**Decision: ACCEPTED**  
**Scope:** Director Approval Verification Core only  
**Reviewed:** 2026-11-03

## Reviewed artifacts

- Workorder: `.bridge/task.md` — SHA-256 `50043410be8611a5bc26b28a1799115834fcaa80fa391a5fd0a6085d0d74e7f6`.
- Implementation: `src/services/trial_authorization_signer_service.py` — SHA-256 `a6f3b0212c4db806892ff6756d7d3530518e2125585c30c6ac88b2b5a20cd6e7`.
- Tests: `tests/services/test_trial_authorization_signer_service.py` — SHA-256 `c6fd341bd03317ac7d7a6637f53b8fcfbc37343581a61c5178ae24478d55374d`.
- Submitted test log: `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I4/pytest.log` — SHA-256 `3eab3bfb1a13d49ed58fc4112a8f8ce863e646b733b3319d637d23f2bb104e35`.

All submitted hashes match the workspace. The log records the submitted focused/regression command ending in `104 passed in 0.65s`; tests were not rerun during this review.

## Findings

No blocking findings were identified in the submitted slice.

1. **Input boundaries and fail-closed checks:** The public function requires exact envelope, proposal, and plan DTO types and a PyCA Ed25519 public-key object. It validates the expected Director key ID and checks it against the envelope before proceeding (`trial_authorization_signer_service.py:107-140`). The embedded approval record is also required to be the exact `AppOwnedApprovalRecord` type (`:143-148`).
2. **Approval and plan binding:** The implementation calls the source-owned `verify_proposal_approval_binding()` and independently checks approval ID, proposal ID, and revision alignment (`:150-181`).
3. **Digest recomputation:** Proposal and plan digests are recomputed through `compute_proposal_revision()` and `compute_canonical_plan_digest()` and compared with `hmac.compare_digest` (`:183-211`). No competing serializer is introduced.
4. **Signature verification:** Ed25519 verification uses `envelope.canonical_signing_payload_bytes()`, the accepted envelope contract that excludes `director_signature` (`:213-229`). Invalid signatures fail closed.
5. **Result and operational boundary:** The immutable result contains identifiers and verified digests only; the service exposes no authorization issuance or dispatch capability (`:60-77`, `:242-272`). The module has no import-time I/O, persistent key loading, network, socket, subprocess, or Resolve integration.
6. **Test evidence:** The submitted log records 104 passing tests and zero failures. Static inspection confirms focused negative cases for key ID, wrong public key, altered plan/proposal, stale binding, digest mismatch, malformed nominal types, and signature tampering. Evidence is limited to this submitted local suite and is not live or deployment validation.

## Decision boundary

ACCEPTED applies only to the bounded in-memory verification core authorized by Gate 3-I4. The supplied public key remains a candidate input for this isolated helper and is not an established trust anchor. This acceptance does not authorize trusted-key resolution, persistent keystore access, operator-consent verification, nonce consumption, `TrialExecutionAuthorization` issuance, service/RPC/Dispatcher wiring, Gate 4/5/6/7 work, or live operations.

Safety state remains `ready_to_execute=False`; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Gate 6 remains `NOT ACCEPTED` and incident-only. No tests, Resolve, IPC, key access, or network operations were performed during this review.
