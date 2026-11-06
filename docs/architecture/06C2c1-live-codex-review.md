# G06-06C2c1-LIVE — Header evidence reviewed; harness changes required

2026-10-30. Report SHA256 verified: `3819f5cf9e7f10cf05995a25be0ff780df41be54e07a733370a0876ecf9ded94`.

Normal/fault metrics and logs were read, together with harness assertions. The actual ffprobe fixture readback is consistent: 60 reported frames, r/avg30000/1001, duration1001/500, read-only hash checks; actual audio/corrupt-file denials and controlled shim timeout/cancel are separately labelled. Acquisition production SHA256 matches the previously reviewed code. Codex did not repeat successful live runs or rerun pytest when production/tests were unchanged.

## Required harness corrections (not production regressions)

1. **Preparation outside ownership:** run_fault_injection_mode creates/instruments the session, generates fixture, creates shim and starts worker BEFORE try/finally. A controlled generation OSError leaves the session and global subprocess.Popen wrapper installed. All setup after resource allocation must be under the same cleanup owner. Normal instrumentation and version probes also need ownership/finite timeout.
2. **False clean while worker alive:** cleanup counts reader threads containing 'ffprobe', not its `fault-test-worker`. It deletes the session if child PIDs are gone regardless of workers/readers. Independent controlled probe held the owned acquisition worker past the bounded join: worker still alive, session deleted, harness returned2/CLEANUP_SUCCESS. Require every owned worker/reader and child idle before deletion and clean verdict; retain session on unresolved ownership.
3. **Fault protocol false pass:** fault mode returns2 whenever cleanup succeeds, even when the intended injection never happened. A controlled ready-marker timeout produced fault_injected=False yet exit2. Require intended fault point/name reached and the expected active-job cancellation/termination evidence plus clean ownership; otherwise failure exit, even if teardown succeeded.

Fresh independent probe artifact `.bridge/ux-evidence/G06-06C2c1-LIVE-CODEX-REVIEW/harness-probes.json` reproduces all three with no actual child process. Probe-only instrumentation was restored, controlled worker released/joined, own temp dirs removed. Main C2c1 code acceptance remains valid; full LIVE gate is **PENDING_HARNESS_EVIDENCE_FIX**, not globally accepted.

## Evidence quality corrections in same patch

Persist full bounded child table (pid, argv, role, final returncode/pipe status), source before/after hash/stat values and owned worker/reader counts, not only summary booleans. Current spawn_labels are not exported. Preserve old artifacts, produce repaired normal/fault evidence in a new task directory. A normal-case exception must still write failure/cleanup metrics rather than bypass return-to-main logging.

Next `G06-06C2c1-EVIDENCE-R1-THEN-06C2c2-D`: harness + focused harness-only regression tests, one normal and one fault rerun after fixes; then design one async metadata consumer from actual source. No production/parser/IO-helper changes or synchronous GUI/ClipManifest probe permitted.
