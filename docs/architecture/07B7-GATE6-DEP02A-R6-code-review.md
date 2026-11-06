# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R6

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R6
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `148f8e1ebe9c7464e1a84005ce6146fc0d0c6e2626d104b536318e972c3c40de`
  - `deploy/systemd/resolveflow-dispatcher.service`: `21f70df95eaad82119ebac63c46a94ebc28c6b16e8ede263d1f54fdcfe087792`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `c82f7370e48525ebc66ab459c9fac26619a65a934bb06e3aded013b94449ddad`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `44048df6792a3a31fe97ebd5f93f7f8019b7b44e1eb9691b82f2f394771cacb4`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `10a23d29f9d29f303338603f8ba9abede464556715d4c90f5bc46c4ebb73f327`.
- **Review mode:** Static inspection only; no deployment artifact was executed.

## Decision summary

R6 addresses DEP02A-R5-01 at the policy level: `verify_socket_dir()` accepts either an empty directory or the one expected socket entry, and checks parent/state directory contents through pinned descriptors. However, the present-socket branch calls `os.fstatat`, which is not part of Python's standard `os` API. The resulting `AttributeError` is caught by the generic exception handler and recorded as a mismatch, so a correctly present socket cannot verify successfully.

## Finding

### DEP02A-R6-01 — MEDIUM — Present socket verification calls a nonexistent Python API

**Evidence:** `verify_socket_dir()` calls `os.fstatat(dir_fd, expected_socket_name, follow_symlinks=False)` at `deploy/scripts/verify_resolveflow_dac.sh:241`. Python's standard `os` module does not expose `fstatat`; the descriptor-relative operation is available as `os.stat(path, dir_fd=dir_fd, follow_symlinks=False)`. The call is inside `except Exception` (`:263-266`), which closes the directory descriptor, appends an inspection error, and returns failure.

**Impact:** Whenever an expected socket entry exists, the verifier records an error instead of validating its type/UID/GID/mode. Consequently, it cannot return `STATUS=VERIFIED_OK` for the intended running daemon state, contrary to workorder §3.3 and the R5 remediation objective.

**Required correction:** Replace the unsupported call with Python's descriptor-relative `os.stat(expected_socket_name, dir_fd=dir_fd, follow_symlinks=False)`, preserving the pinned-parent and no-follow checks. Ensure missing/replaced entries fail closed and that a valid socket proceeds through the exact `S_ISSOCK`, owner, group, and mode checks. Do not execute the host verifier in this workorder.

## Remediations confirmed

- Socket directories permit either the empty pre-daemon state or exactly the expected socket basename.
- Parent directory exact-child sets and empty signer state leaves remain descriptor-anchored.
- Provisioner retains the pre-daemon empty socket-directory requirement.
- Daemon account supplementary groups, exact `0750` directory modes, and `/bin/false` packaging placeholders remain as previously reviewed.

## Required disposition

Submit a bounded code revision for the socket metadata lookup. Keep the provisioning and verification scripts unexecuted, the host untouched, and `.bridge/ux-evidence/DEP02A/dac_verification.log` absent.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
