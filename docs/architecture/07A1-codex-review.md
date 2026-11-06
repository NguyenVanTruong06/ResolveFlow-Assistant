# G07-07A1-I — Codex review

2026-11-02. **CHANGES_REQUIRED within the same three-module scope.** Report SHA256 `e72c00da98e9bf9d41d0e8d06d1d475b9cc21ba265449275c3f07d3ece7458de` and submitted source hashes match. Codex fresh prescribed group: **42 passed in18.06s, exit0**. No full suite, models, network, desktop or Resolve execution by Codex.

## Main behavior verified

An independent fixture uses the real S3 proposal builder, actual approval action and the actual Director-supplied context, rather than manually installing a fingerprint for an unrelated proposal. The new getter returns a valid pair and the Director reaches read-only Step4. Source review confirms legacy phases0/2 are denied and approved Build does not dispatch the old worker. Keep those changes.

Evidence: `.bridge/ux-evidence/G07-07A1-CODEX-REVIEW/{probe_review.py,probes.json,probe_execution.log}`.

## Four finite closure groups

1. **Getter fail-safe boundary is incomplete.** `compute_proposal_revision(self._current_proposal)` and direct access to approval.proposal_revision occur outside the exception boundary. Starting from real successful S3 approval, mutate the typed proposal.video_plan to an invalid mapping: getter raises ValueError. Delete the required proposal_revision member from a copied approval: getter raises AttributeError. They must produce controlled None, clear unusable approval and preserve user data. Window callers also need a defensive exception boundary around the newly introduced getter dependency.
2. **Feedback is not visible to the user.** At approved Build, the current implementation writes only logger/_last_step4_gate_reason/_last_build_result. Independent actual-window component inspection finds no visible QLabel containing the unavailable reason and the primary tooltip is empty. Missing-approval denial has the same private-only feedback. A click therefore appears to do nothing while Step4 still says “Dựng timeline”. Provide a visible concise nonmodal reason within the existing window and test rendered text/visibility, not only private attributes. No fake progress or extra production UI module.
3. **Worker state-query errors escape.** With an owned controller.worker whose isRunning raises RuntimeError, a direct phase0 rejection raises instead of returning False. Treat uncertain state as busy for the new gate, preserve the handle and avoid false terminal signals. Add the promised positive phase1 mocked-worker test and construction/kwargs tripwires for rejected phases. The submitted six tests never start a phase1 worker, and the purity test does not actually install IO tripwires; do not repeat stronger claims than its assertions.
4. **Current missing-AI-worker branch reports fabricated success.** `DirectorController.run_ai_script` with LLMDirectorWorker=None emits `(True, 'Mô phỏng AI kịch bản thành công!', '{}')`. This was reproduced without a model or network. Restore a truthful failure/no JSON response in this small controller branch; no provider redesign or live inference. This concrete false-success path must not survive under a report claiming unchanged truthful provider behavior.

## Evidence improvements required in the same repair

The new test helper manually installs a proposal/fingerprint while its raw page plan uses `cuts`, not the S3 builder's timeline_segments, and its creative context is unrelated to that proposal. Keep low-level tests if useful, but use at least one real rebuild→approve→getter→Director transition positive control with the actual context supplier wired. Test B8 constructs a fake_json_approval variable and never submits it; that is not a parser-boundary test. Either exercise the real input route or report that case as inherited/untested. No broad parser/source-analysis work is needed.

Report timing also differs between prose15.61s and evidence.json15.91s; record actual outputs consistently next time. This discrepancy is not itself a code blocker; the fresh Codex result is18.06s.

At final publication, the product/design/test source hashes still match the submitted report, but prescribed_suite_output.txt and evidence.json no longer match their submitted checksums. The fresh Codex test observation and independent probe files are the verification basis here. Freeze evidence/logs as well as source after the next report; do not retroactively rewrite submitted artifacts to repair report prose.

Next task **G07-07A1-R1-THEN-07A2-D** groups these fixes and meaningful tests, followed by design-only preparation for the smallest off-thread source/catalog readiness consumer. G06 remains closed; no actual preflight/execution/Resolve/model work is authorized here.
