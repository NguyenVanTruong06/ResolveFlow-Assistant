# G06C3 S3 — Code acceptance and desktop evidence boundary

2026-11-02. **S3 ACCEPTED_WITH_RECORDED_LIMITS. G06 evidence closure is now accepted at [06-gate-acceptance.md](06-gate-acceptance.md).** The review details below preserve the historical evidence boundary; both finite harness corrections have since been verified.

Reviewed report `4a06f930bd28be86053b781487f020de59ef0d79e6645eef80fac6915d8d9260`; current source hashes match the report. Codex independently ran the prescribed five files: **131 passed in 20.12s, exit 0**. Fresh replay of the original and final residual probes gives the expected outcomes, including sidecar manifest/omission invalidation, valid-FPS rebuild clearing approval, exactly one supplier read, and physical supervisor completion with missing diagnostics. Evidence: `.bridge/ux-evidence/G06-06C3-S3-FINAL-CODEX-REVIEW/`.

## Accepted product scope

- Current source/creative/Slot1–2/matching/evidence binding; approval is invalidated when displayed authoritative input changes.
- Pure proposal demand accounting, candidate selection/omission/restore, exact proposal-revision approval binding, and truthful optional omissions.
- Revalidated geometry display and stale/error denial. None/empty evidence permits editorial review as unresolved; blocked geometry does not grant approval or execution readiness.
- Existing gateway/Resolve code is unchanged. This gate does not establish whole-plan execution, disk/source freshness at action time, measured CFR from headers, AI/provider quality, or project persistence.

The placeholder 24fps used in some BLOCKED diagnostic objects remains diagnostic-only, not verified frame evidence. G07 must not use it to authorize execution.

## Desktop evidence reviewed, with narrower claims

Worker normal run exits0 and fault run exits2, according to current logs/metrics. Fixture-backed component dispatch demonstrates proposal creation, exact approval, candidate change, omission/restore, stale denial, and geometry transitions. Fault metrics record one cooperative PromptPreparationWorker at the controlled barrier, then zero physical workers and owned-session cleanup. These runs were reviewed, not re-executed on the desktop by Codex.

Code and screenshots require two corrections before final G06 evidence closure:

1. **Viewport visibility claim is not supported.** Codex opened both actual Director screenshots (1200x750 and1280x800). They show the upper prompt area; common review and its approval button are below/outside the visible viewport. The isolated `common_review_widget.grab()` image is1058x422 and readable, but both sizes have the same widget-image hash. It proves widget rendering, not that a user can see/click those controls in the Director viewport. `isVisible()` and QTest.mouseClick on the target object do not prove a visible click location. Scroll to each tested target and verify its visible/hit-test coordinates; record horizontal scrolling as an honest layout limit rather than claiming simultaneous fit. No production layout refactor is authorized here; remaining polish belongs to G08.
2. **Setup is outside the actual cleanup boundary.** In `task/desktop_verify_06c3_common_review.py:run_harness`, mkdtemp, directory creation, environment replacement, QApplication/clipboard initialization, DirectorWindow construction/show and event pumping precede the try/finally. The observed mid-run fault is safely cleaned, but a constructor/setup exception can escape without restoring the environment or removing the owned session. Move setup into the actual outer cleanup boundary and pin early-failure behavior with finite fakes. No full application/test rerun is necessary for this harness-only repair.

The reported 6/6 is five fixture-backed interaction groups plus cleanup/fault coverage, not six independently verified production user journeys. Candidate ID in the executed harness is `asset_cat_alt`; the report's `asset_broll_alt` is a naming erratum. Preserve granular observations; correct prose instead of rerunning unrelated logic.

## Next bounded work

Task **G06-EVIDENCE-CLOSE-THEN-G07-D**: repair only the existing S3 desktop harness and its small tests, collect targeted visible interaction and cleanup evidence, then prepare source-grounded G07 design. No G06 production changes; no inference, network, or Resolve mutation. G07 implementation remains blocked until Codex closes the G06 evidence gate and approves the single implementation slice.

G07 scope is approved current proposal → nonmutating preflight → Resolve execution/recovery in a later implementation task. D01/D02/D03 remain binding. G10 remains queued after G09; README, GitHub publication and library backups are unchanged.


## Evidence closure accepted — 2026-11-02
Codex reviewed the corrected outerfinally/visible-hit-test harness, new actualwindow screenshots afterscroll, normal0/fault2/early1 metrics and fresh31tests2.77s/exit0. S3production remained frozen. Horizontal scrolling is documentedG08work, not a remaining G06code task. G06 closed with recordedlimits;G07-07A1-I is active per07-codex-decisions.md.
