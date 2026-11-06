# G06-06C3-S3-R3 — Codex review

2026-11-02. At review start, report `bd7328ac1753a6e7d0ef15f7fdaa8f34559f8083bc021cc120d4383ac06a3925` and all four reported source hashes matched the files. Fresh prescribed five-file group: **125 passed in 20.81s, exit 0**. CODE_TESTED, not desktop acceptance.

The independent R2 reproduction was copied to a new evidence directory before execution, preserving the old reproduction source. All original expected behaviors now hold, including real Mapping evidence, duration/supplier ID/candidate-origin invalidation, falsy evidence blocking, blocked rebuild approval clearance, typed geometry rehydration, twelve-field demand comparisons, and physical worker completion on the normal keep-loop path. These R3 corrections are retained; no core/widget redesign is requested.

Evidence: `.bridge/ux-evidence/G06-06C3-S3-R3-CODEX-REVIEW/`. The copied script was extended with finite current-binding and supervisor probes, using isolated real offscreen ReviewPage and fake-only supervisor inputs. It made no production edits, model calls, source I/O, desktop claims, or Resolve mutations.

## Remaining current-binding closure — review_page.py only

1. **Sidecar header omitted.** Start with a valid matched sidecar, display and approve successfully; mutate only sidecar.manifest_id to `foreign_manifest`; old event still approves. Fingerprint captures sidecar.plan_revision but not its manifest ID, and approval does not revalidate the current graph through the builder.
2. **Omission reason omitted.** Start with a valid unmatched result and displayed omission; change only result.unmatched_reason; old event still approves the old omission reason. Result capture contains four fields but omits unmatched_reason, suggested_action, warnings and rejected_record_count. Existing R3 requested a complete sidecar/result capture, not an ID summary.
3. **Valid changed geometry retains approval on Update.** Approve at 24fps with real verified fixture evidence, change supplier fps to 30, then rebuild: result is GEOMETRY_VERIFIED at30, but page retains the previous approval. Rebuild compares proposal_revision and blocked status, not previous versus new displayed-input fingerprint. Capture-only changes must require a new explicit editorial approval too.
4. **Approval reads supplier twice.** A controlled supplier returns24 for fingerprint and30 for fresh geometry. Approval succeeds, while displayed geometry remains24. Capture once per action and use that same owned normalized context for validation/build/geometry. This is an action-boundary consistency test, not a claim that a user clicked between two synchronous lines.

Close these with the existing helpers/builder: complete typed sidecar/result capture, full graph validation at action time from the same capture, clear approval when displayed-input proof changes before replacing the stored fingerprint, and one supplier read per operation. Preserve exact core schemas and prior fixes. Do not grow another architecture stage.

## Remaining harness safety closure

`supervisor_main` still uses `supervisor_metrics.get('pending_workers_count', 0)` as authority. Inject a helper returning `{}` while a fake physical worker remains alive: exit spy receives0 immediately, with0 event ticks. This violates the existing R3 no-missing-metrics fallback requirement. Physical state of owned workers must be the exit gate independently of diagnostics; missing, malformed, understated, or stale metrics must never permit cleanup/exit with live workers. State-query failure is unknown/pending, not proof of stopped.

Normal reaping is fixed; retain it. Test the actual CLI keep path with a fake worker true→false and missing/zero/malformed helper counters. No live hung-worker experiment is needed.

## Recorded limit and scope decision

The source still constructs some BLOCKED diagnostics with a placeholder RationalFPS(24,1), despite report prose saying no fabricated FPS. Do not repeat that claim. This review does not accept that field as measured/verified frame evidence. Because those paths block approval and execution, keep it as a recorded diagnostic-only limit rather than expanding another geometry/UI schema task. G07 must use valid current frame evidence and reject BLOCKED/unsupported geometry; a placeholder cannot grant execution readiness.

No fresh broad codec/provider/platform/source-analysis work is required. G10 remains queued after G09, English README and publication/backups stay untouched. After this small closure and finite regressions, the next workorder authorizes fixture-backed S3 component desktop checks in the same run. Full G06 acceptance still requires Codex review of those artifacts.

**Next task:** `G06-06C3-S3-FINAL-THEN-AUTO-UI`. Production scope narrowed to `src/ui/director/pages/review_page.py`; existing harness/tests and a new nonproduction S3 desktop harness allowed. Core builder, widget and contracts remain frozen absent an identified blocker reported to Codex.

## Concurrent-edit checkpoint

Before publishing the next task, source-hash verification detected further edits at 2026-11-02 01:17 local time: ReviewPage now hashes to `8144fb7db1ef797e5223c01fb74ca85829c54fc5c2d4b99ec6b4a20d70aa3da0`, harness to `978900de7231af2f273e2d6c10014f4b3d601edce208ecbc5bb3213941da042d`. These are not the submitted R3 hashes. The 125-test observation and residual probes above apply to the submitted version, not a fresh acceptance of these later edits. Do not revert or overwrite the in-progress work; finish the same final closure, verify all cases including understated/invalid supervisor metrics, and submit one stable report/source hash set for Codex review. No duplicate full test run by Codex while these files are changing.
