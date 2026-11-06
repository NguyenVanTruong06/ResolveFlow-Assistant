# Gate 6 Phase 2 Gate 4 Authority Design R9 — Static Review

- **Decision:** `CHANGES_REQUIRED`
- **Reviewed artifact:** `docs/architecture/07B7-GATE6-PHASE2-GATE4-AUTHORITY-DESIGN-R1.md` (Revision R9)
- **Submitted SHA-256:** `05116621b971226b065a09cebfbf196f680fce131471eb2676983c19ce233e96` (matched workspace)
- **Mode:** Documentation and static source review only. No tests, key access, host access, sockets, or Resolve operations.

## Finding

### GATE4-AUTH-DESIGN-R9-01 — MEDIUM: Client expects socket inode GID instead of Dispatcher peer process GID

Section 7.3 requires the candidate runner to accept the Dispatcher peer only when `SO_PEERCRED` reports UID 902 and GID 903. Under the accepted DEP-01/DEP-02A model, `_resolveflow_dispatcher` runs with primary GID 902 and zero supplementary groups; GID 903 (`resolveflow_clients`) is inherited by the socket **inode** from the setgid directory so that clients can connect. `SO_PEERCRED` reports the peer process credentials in effect at connection time, not the socket inode's group. Therefore the legitimate Dispatcher peer is expected to report GID 902, not the socket node's GID 903, and the specified client check would reject the valid daemon.

**Required correction:** Keep the socket node DAC requirement at owner `_resolveflow_dispatcher:resolveflow_clients`, mode `0660`, but make the client authenticate the peer process against the accepted Dispatcher credentials (UID 902 and process GID 902, consistent with the service unit). Explicitly distinguish process credentials returned by `SO_PEERCRED` from the filesystem owner/group of the socket node. Preserve fail-closed behavior for any mismatch.

## Safety disposition

R9 resolves `GATE4-AUTH-DESIGN-R8-01` by specifying checks on both ends of the AF_UNIX connection. The client-side expected GID must be corrected as above before acceptance. No tests or runtime operations were performed. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; G07 and M1/M2/M3 remain `OPEN`; WO5 remains disconnected.
