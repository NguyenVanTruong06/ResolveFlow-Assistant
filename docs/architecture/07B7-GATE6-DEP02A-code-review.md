# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed artifacts:**
  - `deploy/systemd/resolveflow-signer.service` — `93ad2290966e2a40272739ee14fe32e773dadc219195127432356788de150302`
  - `deploy/systemd/resolveflow-dispatcher.service` — `22b1c1e13875d9750b21cccae61fcb3901bed9fdfe8879130aecbfb277b39af9`
  - `deploy/scripts/provision_resolveflow_principals.sh` — `f4a41dd59bc757bb8feadd45e618713ea44a86f24745cebf2dbce010f832814d`
  - `deploy/scripts/verify_resolveflow_dac.sh` — `f17398ab9131fbac74544a9bace4f1e3721751b3d1319b0fd8f3eaeb947462b3`
- **Evidence log:** `.bridge/ux-evidence/DEP02A/dac_verification.log` is absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `2930535e3a1b05065e448c0aeaccd142c8027b7f1f40f85574c69bbbd93b490d`
- **Review mode:** Static inspection only. No script, shell test, systemd tool, host mutation, Resolve import/IPC/UI, or key access was performed.

## Decision summary

The submission observes the file allowlist and correctly leaves the reserved host-verification log absent. The verification script reports missing provisioning instead of attempting repair. However, the root provisioning script has an exploitable path-validation gap, the units do not establish the socket group ownership required by the DAC matrix, and their service commands are not supported by the repository's current packaged runtime evidence. The artifacts are not ready for acceptance or privileged application.

## Findings

### DEP02A-01 — BLOCKER — Root provisioning follows pathnames after a racy and incomplete preflight

**Evidence:** `provision_resolveflow_principals.sh` checks a leaf for symlink and owner in `validate_path_conflict()` (lines 118–143), but does not validate its mode or contents. It explicitly accepts existing root-owned directories for signer/dispatcher paths (lines 136–142). Later, `ensure_directory()` uses path-based `mkdir -p`, `chmod`, and `chown` (lines 261–299), without rechecking the object or anchoring operations to verified directory descriptors.

**Impact:** With `--apply` as root, a pre-existing group/world-writable directory passes the owner check. A lower-privileged user can replace a descendant between validation and `chmod`/`chown`; `-d` follows a symlink to a directory, and the privileged operation can then modify an unintended target. Existing root-owned directories with unknown contents are also silently transferred to service ownership, contrary to the workorder's reject-unexpected-content requirement.

**Required correction:** Never mutate an existing directory tree merely because it is root-owned. For this bounded provisioning artifact, fail closed on any pre-existing signer/runtime subtree unless a separately reviewed migration rule explicitly authorizes it. For paths created by the script, walk from trusted root descriptors with no-follow directory-relative operations, create missing components exclusively, verify type/owner/mode immediately, and perform ownership/mode changes only on the pinned inode. Reject group/world-writable ancestors before any privileged operation; do not use path-following `mkdir -p`, `chmod`, or `chown` on untrusted descendants.

### DEP02A-02 — HIGH — Service units do not produce the socket GIDs required by the DAC matrix

**Evidence:** The signer unit sets only `Group=_resolveflow_signer` and the dispatcher unit only `Group=_resolveflow_dispatcher` (both units, `[Service]`). Neither unit declares `SupplementaryGroups=` nor socket activation with an explicit `SocketGroup=`. DEP-01 requires signer public and dispatcher client sockets to have GID `resolveflow_clients` (903), and the Oracle socket to have GID `_resolveflow_dispatcher` (902). `UMask=0117` determines permission bits; it does not assign these required groups.

**Impact:** A socket created by either daemon under its configured effective group will not match the expected group. The runner's group-based connect permission and the dispatcher's Oracle connect permission are therefore not established by these units; the verifier will reject the resulting socket ownership.

**Required correction:** Define an explicit, least-privilege mechanism that produces each required socket GID, such as narrowly scoped socket units or deliberately scoped supplementary groups with an analysis of resulting access. Keep signer/dispatcher socket-directory write ownership separate, preserve runner denial of the Oracle path, and prove the signer still has no Resolve IPC access. Update both units and verification expectations consistently.

### DEP02A-03 — HIGH — `ExecStart` targets are not established by the repository packaging/runtime contract

**Evidence:** The service units invoke `/usr/bin/python3 -m src.services.trial_authorization_signer_daemon` and `...live_canary_dispatch_service` from `/opt/resolveflow-assistant`. The repository's `package_deb.sh` builds/copies `dist/ResolveFlow-Assistant/*` into that directory and installs a standalone executable/launcher; it does not install the source `src` package or these future DEP-02B/DEP-03 modules. `run_resolveflow.sh`'s source-tree Python module invocation does not establish that the packaged service account has the source tree and dependencies. Neither daemon module exists yet in the reviewed tree.

**Impact:** The units cannot be considered runnable based on the cited repository definitions; they may fail at startup or require an unreviewed packaging change. The workorder specifically required recording a dependency instead of guessing when the executable/runtime path was not established.

**Required correction:** Record the packaging/runtime dependency and leave `ExecStart` incomplete until a bounded workorder establishes an installable daemon entrypoint and its dependencies. Alternatively, issue a separate workorder that adds the exact packaging artifacts and then pin their paths. Do not point the units at an assumed system Python/module layout.

## Required disposition

Submit a remediation revision addressing all three findings. Do not execute `provision_resolveflow_principals.sh`, apply host DAC changes, or install/start services. Any future host provisioning requires a separate reviewed workorder with explicit execution authorization.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- The verification log remains absent. No tests, scripts, systemd tools, host provisioning, Resolve activity, harness, or key access occurred during review.
