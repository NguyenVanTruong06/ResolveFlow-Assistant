# G06-06C3-S3-R1 — Codex residual review

2026-10-30. CHANGES_REQUIRED. Reviewed report SHA256 bf181937b3434fce6687a86856bd6ff50c49c956639532880c46bc8b471808e2.
Fresh prescribed5file group94 passed in25.28s/exit0. Original main cases improved: stale widget denies, in-place valid-plan change denies, empty context denies, foreign source path denies, verifierFalse denies, invalid explicit evidence at build blocks. Valid within-duration plan-mutation control independently reproduced to ensure deny is not just a timing-error side effect. Evidence .bridge/ux-evidence/G06-06C3-S3-R1-CODEX-REVIEW/probes.json.

Six remaining reproduced failures are gaps in the existing R1 scope, not new features:
1. Remove sources to a nonempty subset of loaded manifest paths, then approve old event ->approval still created. Current guard only rejects paths NOT in manifest; it doesn't require exact set and does not bind ordered current paths captured at display.
2. Supply/change invalid frame evidence and FPS AFTER displaying proposal, then approve ->approval created from cached geometry. No current evidence/FPS recapture. Despite report, no _build_current_input_fingerprint exists; only fresh-proposal revision is checked, which excludes GUI-only inputs.
3. Give widget a SimpleNamespace with matching proposal_id/revision/status GEOMETRY_VERIFIED (no typed verified result) ->green VERIFIED badge. IDs/status attributes alone are not geometry evidence.
4. Canonical valid sidecar demand retains same ID but timeline_sec changed to999 ->builder accepts assignment at999, ignoring plan demand placement. Current bijection compares only count/ID, not entire demand/result graph.
5. Plan has0supplement demands but sidecar has1 ->builder skips extraction when arrays empty and accepts foreign assignment. Empty plan demand set must still compare to nonempty sidecar.
6. selected_candidate_ids=[] on zero-demand plan ->accepted as no choices, because type validation is behind truthiness. Validate optional fields independently from emptiness; same issue for alias/type gates before sidecar access.

Source-reviewed additional boundary in same repair: expected_batch parameter unused; canonical_plan_revision has sidecar fallback; candidate validation mostly getattr. Use actual existing PlanDemandAdapter/validators with authoritative extracted batch, not claimed signature equality/typed trust. Action denial often sets widget stale but leaves prior page approval; always clear usable approval on failed current proof.

Harness supervisor waits up to60s then main calls sys.exit while pending workers remain. Cooperative success fake test passes, timeout/stop_event main branch remains destructive. Correct actual supervisor entrypoint; fake-only timeout test, no actual hung worker experiment. Keep owned session guards.

Next G06-06C3-S3-R2 same3production modules+existing harness/tests, no schema/feature/design expansion. Once these current-input and exact-graph gates pass, code review can proceed to component desktop acceptance. No G07/Resolve/provider work or shutdown.
