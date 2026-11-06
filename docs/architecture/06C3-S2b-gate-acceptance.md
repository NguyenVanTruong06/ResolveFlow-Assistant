# G06-06C3-S2b — Backend batch gate review

2026-10-30. Status: BACKEND_ACCEPTED_WITH_RECORDED_LIMITS. This accepts the implemented backend runtime slice; it does not certify every submitted evidence claim or current UI use. G06 remains open.

Reviewed report SHA256: a3f0b6f55a8357eab1a8c7819d754b9cfcb261f1c8b5d33b8e23d18ba922099d.
Codex fresh finite group: tests/core/test_batch_prompt_wrapper.py, tests/core/test_story_copilot_creative_binding.py, tests/ui/test_prompt_worker_creative_binding.py, tests/ui/test_prompt_worker_batch_creative_binding.py, tests/ui/test_review_page_batch_prompt.py: 143 passed in 13.91s, exit0. Evidence .bridge/ux-evidence/G06-06C3-S2b-CODEX-REVIEW/verification.json and pytest_observed.log.

Source review: full creative JSON enters batch prefix before existing partition/budget functions; direct formatter uses shared canonical helper. Request and worker both permit prepare_batches/copy_batch. Snapshot optional creative digest is strictly normalized, hashed separately, preserved in serialization and on successful early-return branches. Copy mismatch denies before source checks. Existing UI/evidence signature domains and six expected copy bindings remain intact. No runtime defect was reproduced in this bounded review.

Independent Codex probes (6 grouped outcomes): legacy geometry ID matches an independent old-domain hash oracle; geometry and nongeometry snapshots both reject typed digest tampering/removal and preserve UI-signature replacement; three context mismatch combinations each yield exactly one invalid_snapshot with observed zero source stat/isfile/exists calls. These run synchronously without starting a worker thread, separate from fresh threaded tests. No provider or desktop verification is inferred.

## Recorded evidence gaps — correct together, no runtime rewrite
- Submitted C10 test only contains actual post-render deletion and mutation; no batch cancel subcase, despite report claiming it. Add the reached-render cancellation test.
- C09 uses queued started_signal callback but does not block the worker at a deterministic after-start barrier. Use an explicit bounded synchronization point to prove mutation happened before continuation.
- C07 has no call spy proving zero formatting/analysis during copy; C08 has no actual pre-IO tripwire in submitted tests. Codex independently observed pre-IO denial, but add regression assertions to those tests.
- C01 wrapper baseline calls the same current function; no independent frozen wrapper oracle. Only nongeometry ID has independent legacy oracle in submitted C01. Codex independently checked geometry; preserve that regression too.
- C04 searches banned names without installing its claimed IO/asset-summary tripwires. Strengthen actual observations; scoped reference-only guards must allow owned source/cache reads.
- C12 checks a fake isRunning response and strong retention, not an actual cooperative-join failure. Some started workers lack finally cleanup. Add shared bounded cleanup/retention used by ALL started workers in the two new creative worker test files; do not leave real running threads at process teardown. Isolate RESOLVEFLOW_VISUAL_CACHE_DIR explicitly as well as HOME/XDG.
- No changes to accepted production required for these evidence gaps. Keep direct/legacy/geometry behavior frozen.

## Remaining design blocker
The S2b document still has a contradictory flow assigning compute_creative_context_signature to ui_context_signature (section1.2), even though section4 correctly separates them. Correct to two independent pending proofs. Preset-only context serializes canonical default fields too, not literally one key. UI remains deferred until validate_batch_draft and copy_project_overview have an explicit bound creative policy; current worker intentionally rejects nonempty context for those intents. Close that design in one focused document, not a rewrite of all prior architecture.

Next task: G06-06C3-S2b-EVIDENCE-THEN-S2c-D. Part A tests/doc evidence only; Part B focused backend draft/overview design, no production implementation or desktop work. Earlier intake, creative contracts, geometry, planner and metadata gates stay accepted.


## Evidence follow-up 2026-10-30
Report f4825f69f7447f80050a188f5ce97ae5ebeabde546b962c37e546da346f9293c reviewed: fresh28tests/10.37s/exit0; accepted source SHA256 unchanged. Submitted test assertions strengthened (frozen wrapper/geometry oracle, actual spies, deterministic wrapper barrier, cancel path, explicit visual cache isolation). Evidence accepted with ordering/unit-retention limits recorded in 06C3-S2c-codex-decisions.md. No production reopening. Next G06-06C3-S2c-BACKEND-DRAFT-OVERVIEW-SLICE.
