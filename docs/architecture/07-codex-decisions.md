# G07 — Binding decisions after design review

2026-11-02. G06 is accepted within `06-gate-acceptance.md`. The direction of `07-resolve-execution-design.md` is **ACCEPTED_WITH_BINDING_CORRECTIONS**; its proposed APIs and unverified claims are not existing code contracts.

## Corrections mandatory before implementation

1. `AppOwnedApprovalRecord` has `approval_scope`, not `scope`. `EditPlanProposal` has no `proposal_revision` field: compute it with `compute_proposal_revision(proposal)` and compare with the record. Reuse `verify_proposal_approval_binding` and exact ordered omission IDs; do not add fields to either frozen DTO.
2. `ReviewPage.get_approved_proposal_and_record` is not implemented. A first slice using that receiver must include ReviewPage in scope and implement a current-input proof, rather than inventing a caller or reading private cached fields as execution authority.
3. `ClipManifest` has project_name, manifest_id, clips, created_at and updated_at; no timeline_name. Source-project labels are not Resolve target identity. Capture actual Resolve project/timeline UIDs on the Gateway when that execution context exists; use exact strong IDs, not “UID or name”. Creating a new owned timeline and writing to an existing one are distinct operations and need explicit target policy before mutation.
4. `InsertService` exposes `insert`/`insert_async`/`undo`, not `insert_asset`. Its Undo is not a general transactional rollback API for arbitrary rough-cut assembly. Preserve its ten-field InsertResult and current mutation contract; keep future per-operation execution states clearly PROPOSED.
5. `check_action_availability` is a function accepting asset metadata with injected/default IO providers. It does not retrieve the catalog or resolve asset_id by itself and is not always zero-IO. Catalog-to-ID resolution and origin evidence are the caller's responsibility, off the GUI thread in a later slice. Do not claim current guard enforces nonzero size/read permission/drive semantics beyond verified code; add such readiness checks explicitly when implemented.
6. Formula B is source-frame-first, not a generic float `round(seconds*fps)` recipe. Reuse `quantize_cfr_video_span`/`assemble_plan_frame_geometry`: source native half-even frame bounds, rational span/speed, then timeline-duration quantization. No duplicated rounding or epsilon/clamping. Existing source evidence enum includes `HEADER_REPORTED_ONLY`; distinguish it from verified CFR and verified VFR.
7. Absent/header-only evidence means execution is not ready. Do not promise a “safe fallback” that guesses CFR/frames/FPS. Any future verification or conversion must be explicit, source-read-only, bounded/cancellable, and bound to current source identity. G06 runtime evidence tables used by the desktop harness are fixtures, not an already installed production scanner.
8. Keep source/asset IO outside GUI. Future Resolve calls use the existing services/resolve_gateway.py queue. `submit_sync` runs a local event loop; do not put it on the GUI readiness path or call nested Gateway work in a way that deadlocks its own worker. A new execution QThread must not independently call Resolve concurrently.
9. D02 does not permit adding omissions after approval. Asset loss/drift requires a revised proposal and current whole-proposal approval. Manual Dock/Studio actions remain independent of Director approval. Reference metadata and preview paths do not become importable masters.

## First slice selected by Codex — G07-07A1-I

Exactly three existing production modules: ReviewPage, DirectorWindow, DirectorController. Implement a current **editorial approval gate**, allow a valid approved proposal to reach the build-summary page, and prevent Director legacy phase0/phase2 PipelineWorker construction. No new core module/DTO/executor or IO in this slice. Build remains truthfully unavailable until an actual G07 preflight/executor is implemented; do not emit fake execution success.

This replaces the design's premature three-module proposal_preflight/Window/Controller proposal, which omitted the nonexistent ReviewPage getter and mixed pure validation with IO. Later preflight and execution use the same current approved payload; no worker can obtain authority from AI JSON or a boolean flag.

API to implement (PROPOSED, not current code):

`ReviewPage.get_approved_proposal_and_record() -> Optional[Tuple[EditPlanProposal, AppOwnedApprovalRecord]]`

Return owned revalidated copies only when current captured input, proposal revision, real stored approval, exact omission binding and current stale/blocked state match. Use existing pure contracts and current-proof helpers; one supplier read, no reapproval/clock/UUID/IO/Resolve. None means denied/unavailable. This is editorial handoff, not `PREFLIGHT_PASSED` or execution readiness.

DirectorWindow must guard primary navigation, direct/stepper entry to step4 and direct Build calls. DirectorController must independently reject legacy build phases even when called directly. Source-analysis phase1 and manual insert/provider prompt routes remain unchanged in scope. Rejecting a request must not falsely finish or unlock an already active job.

## Next stage boundary

After this real UI gate is accepted, propose the smallest off-thread current-source/catalog/geometry readiness slice with a reachable consumer. Do not create a chain of unused pure schemas or call the old full pipeline with an “approved” flag. Real timeline creation/insertion and recovery require a separate authorized implementation/isolated Resolve acceptance task.
