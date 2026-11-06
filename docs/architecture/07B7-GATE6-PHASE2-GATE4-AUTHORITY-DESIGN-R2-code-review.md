# Gate 6 Phase 2 Gate 4 Authority Design R2 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R2)
- **Submitted SHA-256:** `8c4e7c51c48029606080c1931b2d739a7c7b00f0910cfd5e573c1d06ad87a263` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R2-01 — MEDIUM: One-shot journal code example does not match accepted source

Section 6.2 labels its snippet as accepted `reserve_target()` behavior, but the snippet uses `target_record` and raises `DispatchTargetExhaustedError("ERR_TARGET_EXHAUSTED...")`. Accepted source checks `self._target_state` (`src/services/durable_dispatch_journal.py:661–665`) and raises `DispatchJournalSecurityError` with `ERR_TARGET_NOT_AVAILABLE: Target status is EXHAUSTED`. The conceptual conclusion is supported, but the presented code and error are not source-exact.

**Required correction:** Replace the snippet with the exact accepted condition/error or label it clearly as pseudocode; preserve the source line reference and distinguish the implementation error from any proposed normalized error.

### GATE4-AUTH-DESIGN-R2-02 — HIGH: Optional source-pinned fallback can bypass trust-root revocation or rollback policy

Section 4.2 rejects repository source pinning as a sole mechanism but says it is acceptable as a “secondary pinned fallback or hash-check.” The document does not define precedence, key identity matching, rollback prevention, or how the fallback participates in the revocation policy in §5.4. If the host anchor is absent/revoked and the verifier silently falls back to an embedded key, stale or revoked authority could still validate envelopes, defeating the fail-closed missing-anchor/revocation behavior.

**Required correction:** Remove the fallback option and require a single explicit trust path that fails closed on missing/revoked keys, or specify a separately versioned multi-anchor policy with anti-downgrade, key-ID/fingerprint binding, and revocation checks applied identically to every trust source. Keep that policy a proposal until separately accepted.

### GATE4-AUTH-DESIGN-R2-03 — MEDIUM: Failure matrix combines evidence-consumer failures with live acquisition failures

Section 7 says every failure occurs before any Resolve API call, yet its `Verifier Unavailable` row covers a verifier that “fails to acquire Resolve state.” Acquiring that state necessarily entails attempting read-only Resolve access; the failure can occur after those calls. Other rows such as a missing trust anchor concern the consumer/admission stage and can stop before Resolve access. The current global statement conflates these distinct actors and phases.

**Required correction:** Split preflight acquisition failures from envelope-consumption/admission failures. State that acquisition may make bounded read-only Resolve calls but can never dispatch or mutate; signature/trust/admission failures must stop before the Dispatcher connects to sockets, constructs production journals, or crosses any mutation boundary. Do not claim all failures occur before acquisition calls.

## Safety disposition

R2 resolves all six R1 findings reviewed previously: timestamp claims are no longer treated as proof of signing time; key separation is stated pairwise; failed predicates stop without timeline repair; replay analysis accounts for `EXHAUSTED`; the three-principal custody limitation is explicit; and source hashes match the reviewed workspace. The remaining findings above must be resolved before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
