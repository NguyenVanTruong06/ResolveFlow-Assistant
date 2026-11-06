# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R5

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R5
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `148f8e1ebe9c7464e1a84005ce6146fc0d0c6e2626d104b536318e972c3c40de`
  - `deploy/systemd/resolveflow-dispatcher.service`: `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `5ae27934061b370ca8fc09ca0f43900b5597c2ace4095d0c1e2c2eeb235c3804`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `915aa4283f131b9897abf1f73a9af4d0ad1f0a05c1f8a1d7e84378b71c970c35`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `e18e18f5d0922317fbbc3a6f360d14383f7a8c0ec42fb08552750c45ad68c69a`.
- **Review mode:** Static inspection only. No scripts, systemd tools, tests, host mutation, Resolve activity, or key access were performed.

## Decision summary

R5 addresses DEP02A-R4-01: the provisioner and verifier now enforce exact direct-child sets through pinned directory descriptors. The descriptor-bound verifier and daemon supplementary-group checks from R4 are present, as are the approved `0750` modes and inactive `/bin/false` unit entrypoints. One workorder conflict remains: the verifier requires runtime socket directories to be empty, despite §3.3 requiring it to validate socket ownership/mode when sockets exist.

## Finding

### DEP02A-R5-01 — MEDIUM — DAC verifier rejects valid present socket nodes

**Evidence:** `verify_resolveflow_dac.sh` invokes `verify_node()` with an empty expected-child set for each socket directory (`deploy/scripts/verify_resolveflow_dac.sh:243-249`). `verify_node()` records every entry outside that set as a mismatch (`:190-198`). The script later contains `verify_socket_if_present()` checks for the three socket paths, but a valid socket already causes the containing directory check to fail first. Thus the later socket metadata check cannot produce an overall `STATUS=VERIFIED_OK` for a correctly running service layout.

**Contract:** Workorder §3.3 explicitly requires checking socket node ownership/mode where sockets exist. DEP-01 R3 §2.2 also defines the expected socket entries and their DAC. The provisioning script's initial-precondition rule that runtime directories be empty before daemon setup is compatible with this; the read-only verifier must also verify the later valid runtime state.

**Impact:** Once either daemon creates its expected socket, the verifier reports a mismatch even if the socket is correctly owned and mode `0660`. This prevents the authorized read-only DAC verifier from accepting the designed runtime state and makes its status inconsistent with the workorder.

**Required correction:** Keep provisioning preflight/apply fail-closed on pre-existing socket entries if that remains the DEP-02A setup policy. In the read-only verifier, allow each socket directory to contain either no entry or exactly its one expected socket basename. For a present entry, validate it as a socket with the exact expected UID/GID/mode using descriptor-relative no-follow operations on the same pinned parent descriptor; reject missing/extra/wrong-type entries and keep all other parent child-set checks exact. Add static/local fake-filesystem regression coverage only if a follow-on workorder authorizes tests; do not run host verification here.

## Remediations confirmed

- The exact direct-child sets for `/var/lib/resolveflow`, the signer state parent, and `/run/resolveflow` are checked by the provisioner and verifier using pinned descriptors.
- Leaf state directories remain required empty before daemon implementation; the provisioner applies the same policy in read-only preflight and apply.
- Daemon supplementary-group membership is checked against primary GID only.
- Socket directory mode is exactly `0750`; neither unit has `SupplementaryGroups=` and both retain `ExecStart=/bin/false` pending packaging/daemon work.

## Required disposition

Submit a bounded verifier remediation for the runtime socket-state policy. Do not execute the provisioner or verifier, provision host identities/directories, or install/start units. The reserved DAC verification log remains absent until separately authorized.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
