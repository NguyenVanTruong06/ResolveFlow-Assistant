# G06-06C3-S3-R2 — Codex review: CHANGES_REQUIRED

2026-11-01. Report SHA256 ff0739ce28b8e3b4523db73b76686cfa7d464a26366ce55bcab8973ce8402d66 matches. Independently ran the prescribed five files: **104 passed in 17.24s, exit 0**. No production edits or desktop/model/Resolve execution by Codex. S3 remains unaccepted; existing S1/S2 and other accepted contracts are not reopened.

Evidence: `.bridge/ux-evidence/G06-06C3-S3-R2-CODEX-REVIEW/` contains reproducible finite `probe_review.py`, `probes.json`, execution log and test observation. It uses a real offscreen ReviewPage with sanitized supplier inputs; supervisor probes use only fakes and an injected finite sentinel.

## Improvements independently observed

Unchanged current editorial approval succeeds; removal of one active source now denies. The empty-demand/foreign-sidecar and falsy-choice fixes are present, and the prescribed regressions pass. Untyped geometry receives a blocked badge. These improvements do not prove the complete R2 acceptance matrix.

## A — Current fingerprint is a lossy summary, not the required capture

At `review_page.py:_build_current_input_fingerprint` (6064+), a valid **Mapping[str, SourceFrameEvidence]** is reduced to `('MALFORMED_TABLE', 'dict')`. Only list/tuple fields are captured. The assembler actually requires Mapping, so the R2 in-place evidence test supplies a list which was blocked even before mutation; it lacks an initial successful geometry/approval control.

Independent positive control: a real mapping yields GEOMETRY_VERIFIED. Replacing clip_01 total_frames=240 with 1 makes a fresh assembler result GEOMETRY_BLOCKED, **yet the old event still creates approval**. The current signature has not changed.

Other independent current-input failures:

- Change loaded manifest clip_01.duration_sec from 10 to 1 after display: old-event approval succeeds although the primary cut ends at 4. Fingerprint omits durations and other source metadata; approve no longer rebuilds/verifies the current proposal.
- Change the supplier manifest_id to a different valid ID with unchanged paths: old-event approval succeeds; supplier ID is not captured/compared with the loaded manifest.
- Change candidate.origin_library_id from lib_main to different_library, same asset ID: old-event approval succeeds with the previously displayed origin. Candidate summaries capture only IDs, not authority/content.
- Explicit evidence=False is treated as absent and editorial approval succeeds. `if ev_table` masks malformed explicit input. Missing/invalid FPS paths still fabricate RationalFPS(24, 1), contrary to the prior workorder.
- Approve with valid evidence, then change evidence to blocked and explicitly rebuild: geometry becomes BLOCKED **but the previous page approval record remains**, because only proposal_revision was compared. Geometry/source-only changes must invalidate usable approval too.

Capture must own and validate complete bounded known fields, not stringify arbitrary inputs with json.dumps(default=str), not coerce IDs/paths using str().strip(), and not query the supplier independently twice during one rebuild. Compare current capture at approve and invalidate prior approval whenever displayed-input proof changes, even if the proposal revision is unchanged. Preserve user JSON/ledger and last-good read-only content.

## B — Typed geometry still bypasses validation/ownership

At `common_proposal_review_widget.py:715–722`, typed results are stored by reference after checking three attributes. Copy a real verified assembler result, mutate total_timeline_frames=True via object.__setattr__, then set_proposal: **green VERIFIED and approve enabled**. Existing PlanGeometryAssemblyResult.__post_init__ would reject it. Rehydrate all fields through the existing constructor, including nested validation/ownership; reject missing fields and invalid nested fields rather than trusting the frozen nominal type. No core schema changes are needed.

## C — Full demand graph validation is still incomplete

At `proposal_builder.py:428–437`, the comparison excludes raw_type, raw_query_hint and query_source and does not revalidate the supplied demand. Independent probes changing each of these fields are accepted, including raw_query_hint=object(). Mutating source_index=0 to False is also accepted because False==0. The imported validate_asset_demand_item is not called for supplied sidecar demands.

Revalidate the entire supplied sidecar/batch graph before comparing **all existing AssetDemandItem fields**; use strict validators before ordinary value equality. Validate complete expected_batch requests/options against current canonical requests as well as demands. Validate missing/deleted members into controlled ValueError. Do not repair malformed records, ignore unselected rows, or replace full graph evidence with a count/ID summary. Reuse the frozen validators and adapter; no contracts edits.

## D — Actual supervisor pending loop does not complete safely

At `task/desktop_verify_06c3_creative_ui.py:1042–1050`, the keep-alive loop checks only whether a retained list is nonempty, never workers' current isRunning state. A fake app sets the worker to physically stopped on event tick 1; the supervisor still loops until the injected tick-3 sentinel. No real threads were leaked.

With keep_event_loop_on_pending=True and an already-set stop_event, supervisor_main returns None with a live worker. Merely avoiding sys.exit is not a safe CLI policy: the real __main__ reaches EOF if main returns. When retaining the actual CLI, all pending branches must stay in an owned live supervisor until physical completion, re-query workers each tick, then perform owned cleanup and permitted exit. A bounded probe helper may return pending separately. Keep QApplication strongly owned across the run_harness-return boundary, not only a best-effort QApplication.instance lookup afterward.

## Decision

Issue **G06-06C3-S3-R3**, same three production modules plus existing harness/tests. Four grouped closures above are unfinished R2 requirements, not new features. Require positive starting-state assertions and exact negative observations; do not claim all cases passed from test names. Once complete, the next review can authorize S3 component desktop acceptance. No G07, new architecture stage, models, network, source I/O, publication changes or shutdown.
