# Gate 6 DEP-02A Privileged Host & DAC Provisioning Artifacts Review — R2

- **Review date:** 2026-11-05
- **Decision:** `CHANGES_REQUIRED`
- **Workorder:** `G07-07B7-GATE6-DEP02A — Privileged Host & DAC Provisioning Artifacts`
- **Workorder SHA-256:** `67b3bc80bd66354aaaef304a0dafe18bde5315daf2330546c8a7b616656dad47`
- **Reviewed revision:** R2
- **Artifact SHA-256:**
  - `deploy/systemd/resolveflow-signer.service`: `d2832ecd452cd95154e97b7c12231be16ccd17e908f3f345121680fd7b36e5a2`
  - `deploy/systemd/resolveflow-dispatcher.service`: `338756d23f97583e590a525e84a1b9d9911180dfc138d0286776753fc039dd8d`
  - `deploy/scripts/provision_resolveflow_principals.sh`: `2b21ac60cc7d5e35d8ceaf98c48d063ada765cd0a1f6cf790fa61de653d36b8e`
  - `deploy/scripts/verify_resolveflow_dac.sh`: `a5de6d3fe3757f1252c6e2679b57466046468394843196fdff23e94ba8cf734c`
- **DAC verification log:** absent as required (`NOT_RUN`).
- **Report SHA-256 at review:** `b5b78edf2cec3322e2214fb190393f3992a34467b443941cabe33c9226aae496`
- **Review mode:** Static inspection only. No scripts, systemd tools, tests, host mutation, Resolve activity, or key access were performed.

## Decision summary

R2 addresses the prior pathname race by using descriptor-relative creation and pinned-descriptor mode/owner updates. It also records the unavailable daemon/package entrypoint and sets `ExecStart=/bin/false`. The submission is not yet acceptable: it adopts pre-existing directory trees without checking contents; performs some host identity mutations before checking filesystem preconditions; changes the approved directory modes from `0750` to `2750` while the verifier accepts both; and grants supplementary group memberships that conflict with the stated least-privilege matrix.

## Findings

### DEP02A-R2-01 — HIGH — Existing signer state is adopted without validating directory contents

**Evidence:** `provision_node()` accepts an existing directory when its type, UID, GID, and mode match (provision script lines 267–290), then returns its descriptor. It never enumerates or validates entries in `keystore/`, `nonces/`, or `anchors/`. The verifier likewise checks directory metadata and socket nodes, not unexpected files in those state directories (verify script lines 190–200 and 241–243).

**Impact:** The provisioning script can report success while an unreviewed key, ledger, anchor, or other file already exists in the signer state tree. The later daemon could consume inherited state that was not created or reviewed under this architecture. This contradicts workorder §3.1's requirement to reject unexpected existing contents.

**Required correction:** Define the permitted contents at this provisioning stage and verify them descriptor-relatively. Before DEP-02B, require the state directories to be empty (or explicitly enumerate any allowed entries and validate type, ownership, mode, link count, and schema as applicable). Any extra entry must fail closed without modification. Make the read-only verifier enforce the same content policy.

### DEP02A-R2-02 — MEDIUM — Filesystem preconditions are checked only after account/group changes

**Evidence:** In `--apply` mode, the script creates groups, creates users, and adds the runner to `resolveflow_clients` (lines 181–222) before running the descriptor-anchored ancestor/path checks in the embedded Python block (starting line 229). If an unsafe ancestor or conflicting pre-existing directory is discovered there, the script exits after partially changing host identity databases.

**Impact:** A fail-closed filesystem check can leave a partially provisioned host, with service identities and runner membership present despite the boundary directories being rejected. Re-running may continue from this partial state without a single all-preconditions decision.

**Required correction:** Complete all read-only identity, ancestor, and existing-tree validation before the first user/group/membership mutation. Then apply changes in a documented order and report any partial failure without claiming provisioning success. Do not add rollback that deletes pre-existing identities.

### DEP02A-R2-03 — HIGH — Setgid mode diverges from the approved contract and verifier accepts the non-inheriting mode

**Evidence:** DEP-01 R3 and this workorder specify socket directory mode `0750` (design §2.2; workorder §3.1, lines 37–39). R2 instead provisions `02750` (provision script lines 156–158 and 342–349). The verifier explicitly accepts either `2750` or `750` (verify script lines 197–200).

**Impact:** The change to `02750` is outside the exact accepted design/workorder. Further, a host with mode `0750` passes verification even though the implementation relies on setgid inheritance to produce socket GIDs 902/903; without setgid, the socket's GID is not guaranteed by this design. The verifier can therefore report acceptable directory state without the property R2 claims to enforce.

**Required correction:** Do not silently change the accepted mode contract. Either retain `0750` and specify a reviewed socket-group mechanism that guarantees the socket GID, or submit a bounded DEP-01 design/workorder revision approving `02750`; then make the verifier require the single approved mode exactly. Do not accept both modes while relying on only one for correctness.

### DEP02A-R2-04 — MEDIUM — Daemon supplementary groups grant unnecessary cross-service DAC access

**Evidence:** The signer unit adds `_resolveflow_dispatcher` and `resolveflow_clients` (signer unit line 22); the dispatcher unit adds `resolveflow_clients` (dispatcher unit line 23). The accepted design gives these group memberships to the runner for client socket access, while R2 itself relies on setgid directories to assign socket GIDs. With these memberships, the signer has group traversal/connect rights to `dispatcher_client/dispatcher.sock`, and the dispatcher has group traversal/connect rights to `signer_client/signer_public.sock` under ordinary DAC. `InaccessiblePaths` masks paths in these particular service mount namespaces but does not remove the underlying cross-principal group grants.

**Impact:** The units widen each daemon's filesystem/socket authority beyond its stated role, contrary to the per-principal access matrix. In particular, signer membership in the dispatcher group also creates unreviewed access to any group-902 resources not covered by the listed namespace masks.

**Required correction:** Remove unnecessary daemon supplementary groups and use the approved socket-directory GID inheritance mechanism or a narrowly scoped socket activation design. Keep `resolveflow_clients` membership limited to the intended runner. If any daemon supplementary membership is unavoidable, enumerate every resource accessible through that group and prove it does not grant cross-service, Resolve IPC, or signer-state access; update the design/workorder before applying it.

## Required disposition

Submit a remediation revision addressing all findings. Do not execute the scripts, provision host identities/directories, or install/start units. The R2 mode change requires the accepted design/workorder to be updated before it can be treated as approved configuration.

## Safety state

- `ready_to_execute = False`.
- Gate G07 and Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- DAC verification log remains absent. No scripts, systemd tools, tests, host mutation, Resolve activity, harness, or key access occurred during review.
