# Gate 6 Phase 2 Gate 4 Authority Design R7 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R7)
- **Submitted SHA-256:** `3d03f7d6c57b902ef41ce3ad49b20cc61106c179605a7ee4a4edb305fba65922` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Findings

### GATE4-AUTH-DESIGN-R7-01 — HIGH: Dispatcher socket endpoint and group conflict with accepted DAC contract

Section 7.3 specifies `/run/resolveflow/dispatcher.sock` owned by `_resolveflow_dispatcher:resolveflow`. The accepted DEP-01 R4 / DEP-02A layout defines the endpoint as `/run/resolveflow/dispatcher_client/dispatcher.sock`, owned by `_resolveflow_dispatcher:resolveflow_clients` (GID 903), under the dedicated `02750` `dispatcher_client` directory. The proposed root-level path and `resolveflow` group do not match that accepted directory tree or group name. As written, the runner’s existing DAC access to the accepted client socket does not apply to the proposed endpoint, and the path/group cannot be provisioned under the approved exact-child layout.

**Required correction:** Use the accepted path, owner/group, directory mode, socket mode, and existing runner access policy exactly, or explicitly reopen the host DAC design through a separately reviewed and accepted workorder. Keep the IPC contract non-operational until the endpoint is reconciled.

### GATE4-AUTH-DESIGN-R7-02 — HIGH: Dispatcher response-signing authority and verifier trust are undefined

Section 7.3 requires `DispatcherAdmissionResponse` to carry an Ed25519 signature from the Dispatcher, but §2.4 defines pairwise separation for only six key roles and does not include a Dispatcher signing key. The document does not define a distinct Dispatcher key ID/material, custody, public-key trust distribution to the interactive runner, rotation/revocation, or how the response signature is bound to that trust root. Reusing the Oracle or Tech Lead key would violate the document’s own separation rule; adding a new key silently would create a new authority and provisioning dependency.

**Required correction:** Either remove the response signature and define the authenticated AF_UNIX/SO_PEERCRED response-integrity boundary, or specify a distinct Dispatcher response-signing role with pairwise key separation, custody, trust bootstrap, lifecycle, and exact response signing claims as an explicit unresolved governance prerequisite. Do not imply that a signature can be verified absent a trusted public key.

## Safety disposition

R7 resolves the R6 findings in principle: Gate 5 checks precede preflight consumption, and the Dispatcher IPC boundary is explicitly marked unresolved and non-operational pending a separate workorder. The endpoint/DAC and signing-authority inconsistencies above remain before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
