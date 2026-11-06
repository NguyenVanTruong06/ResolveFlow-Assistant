# Gate 6 Phase 2 Governance Boundary Design R1 — Static Review

**Workorder:** `G07-07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1`  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Documentation/source cross-check only. No host, service, socket, key, or Resolve state was accessed.

## Artifact integrity

Submitted hashes match the workspace:

- `docs/architecture/07B7-GATE6-PHASE2-BOUNDARY-DESIGN-R1.md`: `72b18067f42b8690605886143665d36978845d5416d0e0de16818db904dd4ec2`
- `.bridge/task.md`: `ef1fb3582c93bbd84aa7a5f09dcb3bbf305feff8e6db370b7b033bcc44346655`
- `.bridge/report.md` before this review entry: `ec5b3e846f1a538fddc81096d8e5659c588ace30c00dd20efa2e482e45254b5d`

## Findings

### PHASE2-BOUNDARY-R1-01 — Unapproved fourth OS principal and unsupported private-key path — BLOCKER

Sections 2.2.1 and 4 introduce `_resolveflow_preflight` (owner of `/var/lib/resolveflow/preflight/keys/`) and describe a separate System Verifier process. DEP-01 §2.1 and the Phase 2 workorder basis explicitly define only `_resolveflow_signer`, `_resolveflow_dispatcher`, and `interactive_user`; this workorder also required documenting missing authority rather than assuming a new principal. The authority matrix itself assigns Gate 4 to `interactive_user`, contradicting the prose that requires an independent verifier principal.

The new private-key path and principal are not provisioned by accepted DEP-02A and have no approved ownership/custody contract. Remove the assumed principal/path and record the missing authority/custody decision as an unresolved dependency, or obtain a separate architecture decision before proposing it.

### PHASE2-BOUNDARY-R1-02 — Host DAC model conflicts with accepted DEP-01 / DEP-02A — BLOCKER

Section 4.1 and the D evidence checklist substitute a new `resolveflow_ipc` GID 900, a shared `/run/resolveflow/oracle` directory at `2770`, and peer checks that include the wrong UID. The accepted DEP-01 R4 design and DEP-02A-DAC-REALIGN instead specify:

- signer primary UID/GID `901`; dispatcher primary UID/GID `902`; both daemons have zero supplementary groups;
- client group `resolveflow_clients` GID `903`;
- three distinct socket directories (`oracle`, `signer_client`, `dispatcher_client`), each mode `02750`, with the accepted owner/group split;
- Oracle peer verification requires dispatcher UID `902`; socket nodes are mode `0660`.

The proposed shared group, directory mode, layout, and peer-UID statements are incompatible with that accepted least-privilege boundary. Reconcile the evidence checklist to the accepted contract and cite the applicable accepted review; do not introduce GID 900 or a shared socket directory without a separately reviewed architecture change.

### PHASE2-BOUNDARY-R1-03 — Public trust-root placement is unreadable by the stated verifier — HIGH

The design places Gate 4 and Gate 5 public keys under `/var/lib/resolveflow/signer/trusted_roots/`, while its own DAC model gives the Signer state tree owner-only `0700` access. It then assigns verification to the candidate runner / Dispatcher, which is not the Signer principal. Under the accepted DAC model those verifiers cannot traverse the Signer directory to read the public keys. No accepted transfer, read-only publication, or verification service boundary is specified.

Specify a trust-root distribution mechanism accessible to the verifier while preserving Signer private-state isolation, or record this as an unresolved dependency. Do not infer that a public key's contents make the parent-directory access control irrelevant.

## Verification boundary and decision

The document correctly retains WO5 as disconnected and declares that this design does not establish the blockers. Those constraints remain. However, the principal and DAC contradictions prevent acceptance of Blockers A and D and undermine the trust-root verification design for A/B.

R1 is **not accepted**. Remediate the findings within the documentation-only scope, preserving exact source-grounded architecture and the disconnected WO5 decision graph. No tests or operational checks were run. `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` / incident-only; Gate G07 and M1/M2/M3 remain `OPEN`.
