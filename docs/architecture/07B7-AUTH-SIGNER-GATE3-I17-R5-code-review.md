# Gate 3-I17 Revision R5 — Static Code Review

**Decision: CHANGES_REQUIRED**

## Scope and evidence

Reviewed the I17 workorder, R5 implementation and tests, report, and submitted pytest log. The submitted SHA-256 values for `.bridge/task.md`, `.bridge/report.md`, `src/services/trusted_canary_dispatch_service.py`, `tests/services/test_trusted_canary_dispatch_service.py`, and `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I17/pytest.log` match the handoff values at review time.

The submitted log records **413 passed, 0 failed, 2 warnings**, exit code 0. Tests were not rerun during this static review.

The R5 implementation now calls the I11 loader or the I8 anchor checker and handles rollback/tampering errors. One anchor-presence check is still outside the descriptor-anchored boundary.

## Finding

### I17-R5-01 — BLOCKER: Path-based anchor precheck violates the trust boundary and has a missing-anchor race

In `LocalTestOracleVerificationClient.verify_authorization()` (`src/services/trusted_canary_dispatch_service.py:413–419`, with analogous checks in the raw-manifest and pre-verified-evidence branches), the code evaluates:

```python
anchor_file = Path(self._registry_directory) / "revocation_epoch.anchor"
if not anchor_file.is_file():
    ...
```

This performs a path-based filesystem query before the I11 loader has applied its lexical temporary-root policy and descriptor-relative traversal. A caller-configured path outside the permitted temporary root can therefore be probed before the secure loader rejects it.

It also does not make anchor presence atomic with the subsequent descriptor-based check. An anchor can exist for `is_file()`, then be removed or renamed before I11/I8 opens it. In the accepted `check_and_advance_revocation_epoch_anchor_for_local_test(..., advance=False)` implementation, a missing anchor returns successfully without creating one. The check-then-open race can therefore turn the “anchor required” gate into acceptance of unanchored evidence, contrary to the R5 fail-closed claim.

**Required correction:** remove every path-based `Path.is_file()` precheck on the registry/anchor paths. Enforce required anchor presence inside the descriptor-anchored I11/I8 operation while the verified directory descriptor and the appropriate lock are held; the read-only `advance=False` path must distinguish “anchor absent” as an error for this Oracle admission use. If the accepted API cannot express that requirement atomically, stop and report the dependency rather than approximating it with a path check. Add tests proving that disallowed paths trigger no filesystem query before lexical rejection, and a deterministic race that removes/replaces the anchor between preflight and descriptor open cannot yield `VERIFIED_VALID`.

## Safety and disposition

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this review. The declared state remains `ready_to_execute=False`; Gate G07 and M1/M2/M3 remain `OPEN`; Gate 6 remains `NOT ACCEPTED` and incident-only.

I17 R5 is **not accepted** pending correction of this finding and submission of updated source, tests, and authorized test evidence.
