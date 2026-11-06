# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R7

- **Review date:** 2026-11-05
- **Decision:** `ACCEPTED_FOR_ARTIFACTS_ONLY`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R7
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `148f8e1ebe9c7464e1a84005ce6146fc0d0c6e2626d104b536318e972c3c40de`
  - `deploy/systemd/resolveflow-dispatcher.service`: `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `a9a1262bd89408e3091a9acabc67698abf41ce772874b6dd8e51fee730178fee`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `a9930bf9b87fe1dcd45272f545ac84381e4c32bdced14c751cbf2950af967c42`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `f48b1256c87e92b264308aefc48c3a49fd8b294f2d57b18c25bd18c8b4fdb82e`.
- **Review mode:** Static inspection only. No scripts, systemd tools, tests, host mutation, Resolve activity, or key access were performed.

## Decision

R7 resolves `DEP02A-R6-01`. The verifier now uses `os.stat(expected_socket_name, dir_fd=dir_fd, follow_symlinks=False)` for the expected socket entry, then checks symlink status, socket type, UID, GID, and exact mode. It allows either an empty pre-daemon socket directory or exactly the expected socket basename. The previous descriptor-anchored parent/leaf content checks, exact `0750` directory contract, daemon supplementary-group checks, inert `/bin/false` unit entrypoints, and provisioning's initial empty-socket-directory policy remain present.

The submitted artifact hashes match the handoff, and the reserved DAC verification log remains absent as required. No unresolved finding was identified within the artifact-only scope of DEP-02A.

## Scope limitation

This decision accepts the configuration and script artifacts only. It does **not** authorize execution of either script, host provisioning, account/group changes, directory creation, systemd installation or lifecycle operations, Resolve access, or Gate 6 execution. Host DAC verification and application require a separate bounded workorder.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- DAC verification log remains absent. No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
