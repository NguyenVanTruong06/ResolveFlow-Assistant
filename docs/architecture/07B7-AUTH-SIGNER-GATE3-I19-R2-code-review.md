# Gate 3 I19 Revision R2 Review

**Review date:** 2026-11-04  
**Workorder:** `G07-AUTH-SIGNER-GATE3-I19-R2 — Strict Versioned Receipt & Ledger Schemas`  
**Decision:** `CHANGES_REQUIRED` — I19-R2 and Gate 3 closeout are not accepted.  
**Scope:** Static review of the allowlisted schema implementation, fixtures, closeout dossier, report, and submitted test evidence. Tests were not rerun.

## Evidence reviewed

| Artifact | SHA-256 observed |
|---|---|
| `.bridge/task.md` | `e0968f229367c956f949e26a68286ee8cc3dc7800ae5199b9476479f5bf1285e` |
| `.bridge/report.md` | `a8e1a979ca115bf5b3e86f3c80fd0a6011c6ff27a1bd0682969bdf57595dc954` |
| `docs/architecture/07B7-AUTH-SIGNER-GATE3-CLOSEOUT.md` | `412bc137b862e92ee95f917f86d44fc1ad5f5208d9ab9e430f996ccbe2ecf90a` |
| `.bridge/ux-evidence/G07-AUTH-SIGNER-G3-I19-R2/pytest.log` | `eae5d0a24d3e8bc3b3ace831f0782eecba83f8a38c4e31a50a78ccfb8df10215` |

The submitted log records **689 passed, 0 failed, 0 skipped, 0 xfailed, 2 warnings, exit code 0**. Both warnings are Python 3.12 `multiprocessing`/`fork()` deprecation warnings from the anchor concurrency test; the dossier identifies them. The artifact hashes listed in the report match the inspected workspace values.

## Review results

### I19-R1-01 — Resolved

The receipt and durable target ledger now use schema version 2. Receipt deserialization requires an explicit exact built-in integer version and rejects missing, malformed, legacy v1, and unsupported versions. Ledger parsing and construction enforce the v2 boundary and reject v1 before transitions or writes. The pre-mutation receipt producer and allowlisted fixtures use v2. The submitted tests cover v2 round trips, malformed versions, legacy rejection, and preservation of legacy ledger bytes. This resolves the prior schema ambiguity within the authorized scope.

### I19-R2-01 — HIGH: Closeout traceability still cites reviews that rejected the referenced slices

The closeout matrix at `docs/architecture/07B7-AUTH-SIGNER-GATE3-CLOSEOUT.md:32-47` still cites superseded review files for multiple accepted capabilities. For example:

- C-01 cites I1 base review instead of the accepted I1-R2 review.
- C-02 and C-04 cite I2 base review instead of accepted I2-R5.
- C-03 cites I3 base review instead of accepted I3-R2.
- C-09 through C-14 cite base reviews for I13, I14, I15, I16, and I17, although the accepted revisions are I13-R3, I14-R3, I15-R4, I16-R5, and I17-R7. Several cited base reviews record `CHANGES_REQUIRED`.
- C-16 still identifies I19-R1 / its review even though that review is `CHANGES_REQUIRED` and the current submission is I19-R2.

This leaves the closeout's central evidence map pointing at rejected or incomplete review decisions. Passing tests and the R2 schema fix do not establish the acceptance status of those earlier slices. The previous closeout review required every row to be reconciled to the actual accepted review artifact; that requirement remains unmet in the current dossier.

**Required:** Update the closeout matrix to name the final accepted review revision for every slice, including I1-R2, I2-R5, I3-R2, I4-R1, I5-R2, I6-R4, I9-R2, I13-R3, I14-R3, I15-R4, I16-R5, I17-R7, and I18-R10 as applicable. Cite I19-R2 as the current closeout submission and replace it with this final review artifact once the decision is recorded. Reconcile all rows against the actual decision text; do not infer acceptance from implementation reports or test results.

## Decision and lifecycle state

- I19-R2 schema implementation: the prior schema finding is resolved.
- I19-R2 closeout: `CHANGES_REQUIRED` pending correction of I19-R2-01.
- Gate 3 and Gate G07 remain `OPEN` / not accepted; Milestones M1/M2/M3 remain `OPEN`.
- Gate 6 remains `NOT ACCEPTED` and incident-only.
- `ready_to_execute = False`. This review authorizes no later gate, Resolve IPC, production key access, or live operation.

No tests, Resolve operations, IPC, network calls, or key-file access were performed during this static review.
