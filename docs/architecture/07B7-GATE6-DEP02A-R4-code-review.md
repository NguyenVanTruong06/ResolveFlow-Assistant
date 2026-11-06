# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R4

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R4
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `148f8e1ebe9c7464e1a84005ce6146fc0d0c6e2626d104b536318e972c3c40de`
  - `deploy/systemd/resolveflow-dispatcher.service`: `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `5273627ac92694f760c6fc869ccbb0b1f4b36559abdeea184a7e49e61eab8786`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `d0510c17bf141472c4eb3ae63dfee5d544709f015920350a5c4c3f0e3701f021`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `64d6e3dc5abb42bacfb9c45f7a5a5b4f207bc7e0379e1b5a8574473bbdda3c6e`.
- **Review mode:** Static inspection only. No scripts, systemd tools, tests, host mutation, Resolve activity, or key access were performed.

## Decision summary

R4 resolves both R3 findings: directory metadata and empty-leaf checks use pinned descriptors in the verifier, and both scripts check daemon account supplementary-group membership. The approved `0750` contract, no-supplementary-groups unit configuration, prior preflight ordering, and `/bin/false` packaging dependency remain intact. One content-policy gap remains: parent directories are accepted with arbitrary entries, so the scripts can still accept unreviewed files or directories outside the checked empty leaves.

## Finding

### DEP02A-R4-01 — MEDIUM — Parent trees accept unreviewed entries

**Evidence:** The provisioner calls `inspect_node_if_exists(..., must_be_empty=False)` for `/var/lib/resolveflow`, `/var/lib/resolveflow/signer`, and `/run/resolveflow` (`deploy/scripts/provision_resolveflow_principals.sh:216-239`), and repeats the same policy during apply (`:407-426`). The verifier similarly calls `verify_node(..., must_be_empty=False)` for those parent directories (`deploy/scripts/verify_resolveflow_dac.sh:210-214` and `:225-239`). Only `keystore`, `nonces`, `anchors`, `oracle`, `signer_client`, and `dispatcher_client` are checked for emptiness.

**Impact:** The expected tree in DEP-01 R3 §2.2 lists the permitted children of these parent directories. An existing extra file or directory at `/var/lib/resolveflow/signer/` (including unreviewed key/state material) or an unexpected runtime entry can be silently accepted while provisioning/verifier reports the reviewed subtree as compliant. Checking only the designated leaf directories does not enforce the submitted claim that unexpected existing contents fail closed.

**Required correction:** For each parent directory, enumerate the same pinned descriptor and enforce its exact permitted direct-child set: `/var/lib/resolveflow` → `signer`; `signer` → `keystore`, `nonces`, `anchors`; `/run/resolveflow` → `oracle`, `signer_client`, `dispatcher_client`. Reject any extra or missing entry without modifying it. Apply this policy in both the read-only preflight/apply checks and verifier, with no pathname reopening.

## Remediations confirmed

- The verifier now opens and validates directory nodes descriptor-relatively and enumerates the same pinned FD (`deploy/scripts/verify_resolveflow_dac.sh:140-192`).
- Provisioner and verifier reject daemon accounts with supplementary groups beyond their primary GID (`deploy/scripts/provision_resolveflow_principals.sh:99-116`; `deploy/scripts/verify_resolveflow_dac.sh:67-94`).
- Socket directories remain exactly `0750`; unit files contain no `SupplementaryGroups=` directives and retain `ExecStart=/bin/false` pending packaging/daemon work.
- The DAC verification log remains absent as required.

## Required disposition

Submit a bounded remediation revision for the parent-directory content policy. Do not execute either script, provision host identities/directories, or install/start units. Keep `.bridge/ux-evidence/DEP02A/dac_verification.log` absent unless a separate workorder authorizes host verification.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
