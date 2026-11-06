# Gate 6 DEP-04-PREP-R1 Revision R3 — Static Candidate Harness Review

**Workorder:** G07-07B7-GATE6-DEP04-PREP-R1 — Hash-Pinned Harness Preparation (Non-Executing)  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review only. The candidate was not executed, imported, compiled, or syntax-checked. No Resolve/Oracle connection, host operation, production state/key access, or live operation occurred.

## Artifact integrity

All submitted SHA-256 values match the workspace. Runtime and archived candidates are byte-identical:

- `.bridge/task.md`: `0dddb75ed63994b967c022845ae236b4275420b6aba23ae16b61d70a2017c60b`
- `/tmp/resolveflow-gate6/run_gate6.py`: `7a6dee00e1727b5ff6fcaac0dee24310a60f3c371f485e56c8e4a231841bdeef`
- `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`: `7a6dee00e1727b5ff6fcaac0dee24310a60f3c371f485e56c8e4a231841bdeef`
- `.bridge/report.md`: `c16fc9d0b54e8d594dfe1f5c9859a2ea2ddc47ad031acb1a9ece945baa375d47`

## Findings

### DEP04-PREP-R3-01 — Gate evidence remains untrusted, unbound, and caller-selectable — BLOCKER

The revision still does not establish authoritative evidence for the exact run:

- Gate 2 is still the static review document `07B7-AUTH-SIGNER-GATE2-code-review.md`, accepted by searching for text such as `Decision: ACCEPTED` and `INSERT_CLIP` (`run_gate6.py:126-129, 269-284`). A review disposition is not the user's run-specific Gate 2 authorization.
- `_validate_gate_evidence(auth=...)` never reads or binds `auth` to Gate 2, Gate 4, Gate 5, challenge nonce, execution ID, action digest, or operator identity. The Gate 4 signoff and operator attestation paths remain caller-supplied CLI arguments (`:509-514, 603-640`).
- Gate 4 freshness accepts a caller-supplied future `expires_utc` and a `ttl_status` string; it does not validate issuance time, maximum TTL, nonce equality to the signed authorization, authenticated signoff provenance, or `zero_dispatch_calls` evidence (`:330-344`). The default Gate 4 signoff path is historical R16-R3 (`:125`).
- The attestation JSON is accepted by target UID, free-text substrings, and caller-provided timestamp (`:364-393`). There is no authenticated provenance proving a human operator supplied it contemporaneously.
- Gate 5 is still a substring search in `.bridge/task.md` (`:346-362`), not verification of an active issued workorder bound to the reviewed harness hash, exact command, and action.

These checks can accept stale or fabricated evidence and do not satisfy DEP-01 §7.5. In addition, `.bridge/task.md` currently authorizes only non-executing preparation, so the candidate must remain non-executable until a distinct execution workorder is issued.

**Required remediation:** Consume authoritative, integrity-protected, run-specific records; bind all Gate evidence to the exact authorization/target/action/harness and a single fresh challenge; enforce an actual maximum TTL; verify operator attestation provenance rather than a string or unsigned JSON; and validate the active Gate 5 execution workorder and pinned harness hash. Remove caller-selected evidence paths. Do not synthesize these records during PREP-R1.

### DEP04-PREP-R3-02 — Known architecture dependency is detected only after production key/state access — BLOCKER

R3 reports that the legacy adapter cannot be safely composed because `RecoveryCoordinator` opens the Signer-owned keyring. However, `run_harness()` loads the pinned Oracle public key and opens `DurableDispatchJournal` on the production dispatcher state path before raising `ERR_DEP01_PRINCIPAL_BOUNDARY_DEPENDENCY` (`run_gate6.py:562-588`). The dependency is already known in the candidate; the harness therefore accesses production key/state paths before stopping. This contradicts the fail-closed dependency boundary and the preparation workorder's prohibition on production key/state access.

**Required remediation:** Move the dependency stop before all production key, socket, and state access; preferably keep PREP-R1 candidate permanently non-executing and report the dependency only in the handoff. Resolve the adapter/Coordinator separation in a dedicated upstream workorder. Do not instantiate `RecoveryCoordinator`, access the Signer keystore, or broaden DAC from the dispatcher.

### DEP04-PREP-R3-03 — Action identity mismatch check is a no-op — HIGH

After loading the plan, the code checks whether the action's idempotency hash or clip ID matches the authorization execution ID, but the mismatch branch executes only `pass` (`run_gate6.py:535-543`). The intended binding check therefore has no effect. Later digest comparisons are valuable but do not justify leaving a documented authorization-binding predicate as a no-op, especially as the action identity may use a separate canonical ID/hash contract.

**Required remediation:** Replace the no-op with a fail-closed check against the exact authoritative action identity/hash specified by the accepted authorization and plan contracts, and prove mismatch rejection before opening the Oracle socket or journal. If the binding is already completely enforced by an authoritative verifier, remove this misleading conditional and document the exact enforced binding.

## Decision and safety state

Revision R3 is **not accepted** for future execution review. The explicit signer-keystore dependency report is directionally correct, but it must stop before production path access; the authority evidence and action-binding gaps remain. No candidate command, import, compile, test, socket connection, or Resolve operation was performed during this review.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Do not issue a live execution workorder based on this candidate.
