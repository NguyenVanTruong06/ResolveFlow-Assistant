# Gate 6 DEP-02A-DAC-REALIGN Code Review

- **Review date:** 2026-11-05
- **Decision:** `ACCEPTED_FOR_ARTIFACTS_ONLY`
- **Workorder:** `G07-07B7-GATE6-DEP02A-DAC-REALIGN`
- **Workorder SHA-256:** `dc7135f84b1f530f92b06eb07167ff3349dd06628fa753fa12352a0866ba0d32`
- **Reviewed artifacts:**
  - `deploy/scripts/provision_resolveflow_principals.sh` — SHA-256 `80777c6789f9882b19de1465bc9896bfba516d100a63d8f09330666a20cbffbd`
  - `deploy/scripts/verify_resolveflow_dac.sh` — SHA-256 `dfe179e4a13f23e8bdba4c29885b727fd944a53efae7175a0f695fc665a603b9`
- **Report SHA-256 at review:** `e50cf19118548523f0a25ba5f689129d1dce5ab546191951c8669830e8bfc25d`
- **Review mode:** Static code review only. Neither script was executed; no tests or host operations were performed.

## Decision summary

The submitted artifacts meet the bounded DEP-02A-DAC-REALIGN requirements. The provisioning artifact declares and applies exact setgid mode `02750` to the three socket directories, preserves the initial empty-directory policy, and does not create lock files or sockets. The read-only verifier checks directory mode and the accepted exact-child states, and validates lock and socket metadata relative to the pinned directory descriptor.

## Review observations

- The endpoint mapping is consistent: oracle signer lock/socket UID 901 and GID 902; signer client UID 901 and GID 903; dispatcher client UID 902 and GID 903.
- Lock nodes are checked as regular files with exact mode `0600`, expected daemon UID and directory GID. Socket nodes are checked as UNIX sockets with exact mode `0660`, expected daemon UID and GID.
- `verify_socket_dir` accepts only `{}`, `{lock}`, or `{lock, socket}`. Unexpected names and invalid combinations are reported as mismatches.
- Node inspection uses `os.stat(..., dir_fd=..., follow_symlinks=False)` and the verifier does not create, open for locking, chmod, chown, unlink, repair, or otherwise mutate nodes.
- Provisioning continues to reject non-empty socket directories and does not provision runtime lock/socket nodes.
- No new blocking finding was identified within this workorder's allowlist and scope.

## Scope limitation

This is acceptance of the configuration artifacts only. The scripts were not executed, and no claim is made that the host users, groups, directories, modes, lock files, or sockets currently match the design. Host provisioning and runtime verification require separate authorization and a separate workorder. DEP-02B remains gated on its stated prerequisites.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- Zero script execution, host mutation, systemd operation, socket/key access, Resolve import/IPC/UI, and live operations during this review.
