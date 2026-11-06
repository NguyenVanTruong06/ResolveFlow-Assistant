# Gate 6 DEP-04-PREP-R1 Revision R2 — Static Candidate Harness Review

**Workorder:** G07-07B7-GATE6-DEP04-PREP-R1 — Hash-Pinned Harness Preparation (Non-Executing)  
**Disposition:** `CHANGES_REQUIRED`  
**Review mode:** Static source and artifact-integrity review only. The candidate was not executed, imported, compiled, or syntax-checked. No Resolve/Oracle connection, host operation, production state/key access, or live operation occurred.

## Artifact integrity

All submitted SHA-256 values match the workspace. Runtime and archived candidates are byte-identical:

- `.bridge/task.md`: `0dddb75ed63994b967c022845ae236b4275420b6aba23ae16b61d70a2017c60b`
- `/tmp/resolveflow-gate6/run_gate6.py`: `6aa8c62ef4d13b33e8b3fe8548449d9ae73833f371b05fe09d3481945c9c2498`
- `.bridge/ux-evidence/G07-07B7-GATE6/run_gate6.py`: `6aa8c62ef4d13b33e8b3fe8548449d9ae73833f371b05fe09d3481945c9c2498`
- `.bridge/report.md`: `30e70ecc41a308f5a521765709fa6f28f8e4f2b4965e4faa0e4100737ed6ab78`

## Findings

### DEP04-PREP-R2-01 — Gate evidence is caller-selectable and not authoritative or fresh — BLOCKER

`_validate_gate_evidence()` treats Gate 2 as a text file containing `ACCEPTED` or `GRANTED` and `INSERT_CLIP` (`run_gate6.py:165-180`); it does not verify an authenticated operator authorization bound to this exact authorization, action, target, and run. Gate 5 is accepted if a caller-selected file contains either of two marker strings (`:221-234`). Gate 4 checks a few values/counts but no signoff status, challenge identity, observation time, TTL/freshness, independent audit state, or attestation binding (`:182-219`). Its default is the historical R16-R3 signoff (`:125`), not evidence shown to be current for a new Gate 6 run. The operator attestation is another caller-provided CLI string, accepted on two substring checks (`:236-245, :573-577`); the code cannot establish who supplied it or that the operator is physically present at that time.

All evidence paths are CLI-overridable (`:549-577`), so the caller can point validation at arbitrary files. These checks do not establish the mandatory current Gate 2/4/5 authority or the contemporaneous direct operator attestation required by DEP-01 §7.5. The execution gate would therefore accept fabricated or stale evidence.

**Required remediation:** Validate canonical, integrity-protected evidence against an authoritative, fixed source. Bind Gate 2 authorization to the exact action, target, and execution; Gate 4 signoff to the current target and a fresh challenge/TTL; Gate 5 to the separately issued and hash-pinned execution workorder; and operator attestation to a trustworthy contemporaneous source and exact target. Reject arbitrary caller-selected evidence paths and string-only attestations. This preparation revision must not mint or fabricate any such evidence.

### DEP04-PREP-R2-02 — Dispatcher composition opens the Signer-owned keystore — BLOCKER

`_create_live_adapter()` passes `DEFAULT_SIGNER_KEYSTORE_DIR` (`/var/lib/resolveflow/signer/keystore`) to `RecoveryCoordinator` (`run_gate6.py:381-408, 510-514`). `RecoveryCoordinator.__init__()` constructs `TrialKeyringAuthority(keystore_path)` (`src/services/durable_trial_target_ledger.py:921-960`), which opens and enumerates the keystore and reads key files. The dispatcher therefore requires access to Signer-owned key material, violating the DEP-01 OS-principal boundary and the preparation workorder's prohibition on production key access. Under the designed DAC, dispatcher access should fail; granting it would weaken the accepted privilege separation.

**Required remediation:** Do not pass or open the Signer keystore from the dispatcher. Use an accepted dispatcher-side verification design that consumes Oracle decisions and dispatcher-owned state without loading Signer keys. If `LiveResolveCanaryAdapter`/`RecoveryCoordinator` cannot operate under that boundary, stop and report the dependency for a separately reviewed upstream workorder; do not widen DAC or copy keys.

### DEP04-PREP-R2-03 — Caller can replace the pinned Oracle trust endpoint and key — HIGH

The CLI exposes caller-overridable `--oracle-socket`, `--oracle-pubkey`, `--state-dir`, and `--keystore-dir` paths (`run_gate6.py:580-601`), and forwards them into `run_harness()` and service construction (`:443-454, :493-520, :606-616`). In particular, an invocation can select an alternate AF_UNIX server and a public key controlled by the same caller, allowing a substituted Oracle response to validate under the substituted key. This defeats the configured pinned Oracle trust boundary. The candidate workorder also forbids flags/fallbacks that widen scope.

**Required remediation:** Remove trust-boundary path overrides from the executable interface and bind the approved Oracle socket, pinned public key, and journal root to reviewed configuration/OS policy. The future execution workorder must pin these values. Do not test against production paths in PREP-R1.

### DEP04-PREP-R2-04 — Reconstructed plan omits authoritative bindings — HIGH

`_build_canonical_plan()` synthesizes a new plan from authorization claims (`run_gate6.py:327-378`) rather than loading the exact approved, bound plan. It invents `plan_id`, `clip_id`, and timestamps, hardcodes resolution, and sets `track_reservations=()` and `source_digests=()`. It then hashes this synthetic object and compares it with the digest signed into the authorization (`:473-490`). This does not prove the plan/action objects match the signer-approved proposal and its source facts; it either fails the binding or attempts to reconstruct critical context with guessed values. The accepted DEP-03 dispatcher requires exact nominal plan/action types and recomputes the canonical digest against the authorization before journal reservation.

**Required remediation:** Carry forward the exact authoritative, fully bound plan/action produced by the approved Gate 2/3 flow, including source digest and target reservation evidence, and validate that object/digest before opening production state. Do not synthesize omitted fields or weaken the digest comparison.

## Decision and safety state

Revision R2 is **not accepted** for future execution review. The interface/import fixes from R1 are present, but the evidence authority, privilege boundary, pinned Oracle trust path, and canonical plan binding remain unsatisfied. No candidate command, import, compile, test, socket connection, or Resolve operation was performed during this review.

Maintain `ready_to_execute = False`; Gate 6 remains `NOT ACCEPTED` and incident-only; Gate G07 and Milestones M1/M2/M3 remain `OPEN`. Do not issue a live execution workorder based on this candidate.
