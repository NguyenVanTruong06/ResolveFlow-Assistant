# Gate 6 Phase 2 Gate 4 Authority Design R3 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R3)
- **Submitted SHA-256:** `39986dbdf4c03c26a2053f22944d5422425d743cb42bba0d1bea02f3c48793e4` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R3-01 — MEDIUM: Phase 2 boundary overstates production-path isolation

Section 7 says all Phase 2 failures stop “before opening production paths.” However, Phase 2 must read and validate the proposed host trust anchor `/etc/resolveflow/preflight_verifier_public_key.pem` (Sections 4.2–4.3) to detect missing, insecure, revoked, or malformed trust-anchor states. That is a host path read and cannot occur before opening every production path. The intended boundary appears to be that admission failures stop before opening mutable production state, connecting to sockets, accessing Resolve, or mutating journals.

**Required correction:** Define the path classes explicitly. Permit only the bounded, read-only trust-anchor access required for envelope verification; state that failures stop before opening mutable production state/keystore paths, connecting to AF_UNIX sockets, calling Resolve APIs, or mutating journals. Keep missing-anchor handling fail-closed and do not imply that its detection occurs without reading the configured trust-anchor location.

## Safety disposition

R3 resolves the three R2 findings: the `reserve_target()` example now matches the accepted source, repository-embedded key fallback is explicitly rejected in favor of one trust path, and acquisition is separated from evidence-consumer/admission failures. The remaining boundary wording above should be corrected before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
