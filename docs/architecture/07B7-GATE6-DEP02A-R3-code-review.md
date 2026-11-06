# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R3

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R3
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `148f8e1ebe9c7464e1a84005ce6146fc0d0c6e2626d104b536318e972c3c40de`
  - `deploy/systemd/resolveflow-dispatcher.service`: `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `0e210f4213c8321a4bf9d10170ae36f1e082086ba2f9e3c5c6218ba8601102f4`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `b86cb02c702d7522a8152fe27648aaf9731aacae8763e1236e80b50f30eb94bb`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `408f1f3c64a1ffac97600953896b1717c003ee9f4d89c0b134506218538d14b3`
- **Review mode:** Static inspection only. No scripts, systemd tools, tests, host mutation, Resolve activity, or key access were performed.

## Decision summary

R3 resolves the four R2 findings in the provisioner and unit files: it checks designated state/runtime directories for emptiness, runs read-only preflight before account changes, restores the approved exact `0750` socket-directory mode, and removes daemon `SupplementaryGroups=` settings. Two verification gaps remain: the read-only verifier performs its empty-directory checks by pathname rather than through the descriptor whose identity and DAC it checked, and it does not verify supplementary-group memberships for the signer and dispatcher accounts.

## Findings

### DEP02A-R3-01 — MEDIUM — Empty-directory verification reopens checked paths by pathname

**Evidence:** `verify_directory_and_contents()` first checks a path using shell predicates and `stat` (`deploy/scripts/verify_resolveflow_dac.sh:170-198`), then checks contents with `python3 -c "...os.scandir('$p')..."` (`:200-210`). The directory opened for enumeration is not the inode whose owner, mode, type, and symlink status were checked. The R3 provisioner correctly uses `os.open(..., dir_fd=..., O_NOFOLLOW | O_DIRECTORY)` and `os.scandir(fd)` for its corresponding checks (`deploy/scripts/provision_resolveflow_principals.sh:165-196`).

**Impact:** If a path component can be replaced between the verifier's metadata checks and enumeration, the verifier can inspect a different directory and report the empty-content predicate for an inode other than the one it validated. This weakens the verifier's claim that the designated state/socket directories are empty and DAC-correct at the same checked object.

**Required correction:** Make the read-only verifier open each directory component descriptor-relatively with no-follow semantics, validate the leaf with `fstat`, and enumerate that same pinned descriptor. Avoid shell path predicates/stat for security decisions on those paths. Keep the verifier read-only and preserve structured `NOT_PROVISIONED`/mismatch outcomes.

### DEP02A-R3-02 — MEDIUM — Daemon supplementary-group memberships are not verified

**Evidence:** The unit files no longer declare `SupplementaryGroups=`. However, the provisioner only validates an existing daemon user's UID and primary GID (`deploy/scripts/provision_resolveflow_principals.sh:99-120`) and the verifier checks supplementary groups only for `RUNNER_USER` (`deploy/scripts/verify_resolveflow_dac.sh:76-106`). It does not reject or report extra supplementary groups for `_resolveflow_signer` or `_resolveflow_dispatcher`.

**Impact:** A pre-existing service account with additional group memberships can retain group-based access beyond the reviewed least-privilege matrix. Removing explicit unit group additions does not establish that the existing account identities have no unapproved memberships; cross-service socket or other group-owned resource access could therefore remain unreviewed.

**Required correction:** Define the allowed supplementary-group set for both daemon principals in the approved access matrix, then have both provisioning preflight and the read-only verifier enforce it against the host account/group database. Reject unexpected memberships before any mutation; do not remove memberships automatically from pre-existing accounts.

## Remediations confirmed

- Existing keystore, nonces, anchors, and socket directories are checked for emptiness through pinned descriptors in the provisioner; the verifier applies the same empty-content policy.
- Read-only identity/filesystem preflight now precedes account/group/membership mutations.
- Socket directories are provisioned and verified at exactly `0750`, matching DEP-01 R3 and the workorder.
- Neither systemd unit specifies supplementary groups; the runner's `resolveflow_clients` membership remains distinct.
- Both unit files continue to use `ExecStart=/bin/false` pending the documented daemon/packaging dependency.

## Required disposition

Submit a bounded remediation revision for both findings. Do not execute either script, provision host identities/directories, or install/start units. The reserved DAC verification log must remain absent until a separate workorder authorizes host verification.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- DAC verification log remains absent. No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
