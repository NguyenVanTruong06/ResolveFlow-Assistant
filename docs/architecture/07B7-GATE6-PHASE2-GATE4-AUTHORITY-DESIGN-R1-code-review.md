# Gate 6 Phase 2 Gate 4 Authority Design R1 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R1)
- **Submitted SHA-256:** `2fa97528d894c7a3dd0d18bec135ae483a041690a9b716ce95aefcebf5bd6a85` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R1-01 — HIGH: Signature is said to prove signing time

Section 3.4 says the Ed25519 signature proves the claims “were generated within” `verified_at_iso` to `valid_until_iso`. The accepted contract signs these timestamp fields and checks only that the interval is positive and at most 1800 seconds (`src/core/contracts/signed_preflight_contracts.py:152–175`); it has no trusted timestamp or trusted-clock attestation. A valid signature proves that the key signed those timestamp claims, not when signing actually occurred. Section 6's verifier clock comparison can reject stale claims, but cannot establish that `verified_at_iso` is truthful.

**Required correction:** Describe timestamps as signed claims and distinguish current-time freshness validation from proof of signing time. If the design needs trusted time provenance, specify a separate trusted mechanism and keep it an unresolved dependency until accepted.

### GATE4-AUTH-DESIGN-R1-02 — HIGH: Key-separation invariant does not enforce pairwise distinct keys

Section 2.4 uses a chained inequality (`verifier_key_id != operator_key_id != tech_lead_key_id != oracle_key_id != director_key_id`). This does not assert all pairs are distinct; non-adjacent identifiers may be equal. Further, different key IDs do not prove different underlying public keys, and `trial_signer_key_id` is omitted despite §2.1 listing its HMAC key.

**Required correction:** Define pairwise uniqueness over the complete role-to-key mapping, including the HMAC signing key, and bind key IDs to canonical public-key fingerprints or equivalent immutable key identity. Keep any enforcement mechanism as a future contract/implementation requirement, not an existing guarantee.

### GATE4-AUTH-DESIGN-R1-03 — HIGH: Predicate failure directs an unauthorized timeline mutation

The §7 Predicate Failure row says the disposition “requires operator manual timeline reset.” This design and the accepted post-incident runbook authorize no Resolve state change, and an operator reset is a live mutation that could destroy incident evidence or alter the pinned target. A failed empty-timeline predicate must stop; this workorder does not authorize repairing the target.

**Required correction:** Replace the reset instruction with a hard stop and escalation. Any timeline change or fresh-target setup requires a separate explicit workorder and authorization.

### GATE4-AUTH-DESIGN-R1-04 — HIGH: Replay risk is overstated relative to one-shot target exclusion

Section 6.2 calls reuse of a signed preflight envelope a vulnerability that could permit a subsequent execution with a modified timeline. The envelope is pinned to the single target, and accepted `DurableDispatchJournal.reserve_target()` rejects a target whose ledger is `EXHAUSTED` (`src/services/durable_dispatch_journal.py:656–665`); both successful and aborted terminal paths set that state. Under intact accepted journal state, replayed evidence cannot admit a second dispatch for this target. Missing/corrupt state is separately a hard stop under the accepted R5 runbook.

**Required correction:** Distinguish replay of evidence from successful replay of a mutation. State the actual current protections and identify any residual threat only under explicit conditions (such as state reset/loss or future multi-run targets). Justify a preflight-consumption ledger as a scoped future defense-in-depth requirement rather than asserting that its absence alone currently permits a second mutation.

### GATE4-AUTH-DESIGN-R1-05 — MEDIUM: Custody options conflict with the fixed three-principal boundary

Sections 2.2 and 4.4 prohibit a fourth OS principal but list an “unprivileged local verifier account” / verifier service under a “restricted UID” as a possible private-key custodian. That is a new principal unless it is explicitly the existing `interactive_user`; the document does not resolve the conflict. Also, role-separation language does not explain how verifier signing authority remains independent if the same UID can access both verifier and operator keys.

**Required correction:** Remove the new-UID option or label it unavailable without a separately accepted architecture change. Specify how custody and access separation work within the existing three principals, or leave independence as an unresolved blocker without describing the roles as operationally independent.

### GATE4-AUTH-DESIGN-R1-06 — MEDIUM: Static-only evidence claims and source hashes are inconsistent

Section 4.4 asserts the proposed trust-root file “does not currently exist on the host filesystem,” although this workorder prohibits host inspection. State only that its existence/provisioning is not established by accepted evidence. In addition, the source hashes in §10 do not match the files in the reviewed workspace: `signed_preflight_contracts.py` is `b71b34fc…f04ab4`, `signed_operator_consent_envelope.py` is `a6cad18a…09fb36`, and `signed_execution_workorder_contracts.py` is `5779d398…e736a62d`, not the hashes recorded in the design. This prevents the traceability section from identifying the exact source versions reviewed.

**Required correction:** Replace the host-absence assertion with an evidence-scoped statement and update the source hashes from the exact reviewed files (or identify and provide the intended immutable source revision without accessing host paths).

## Safety disposition

The design correctly keeps Gate 4 authority unresolved, rejects reuse of the Oracle key, and preserves the non-execution boundary. The findings above must be resolved before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
