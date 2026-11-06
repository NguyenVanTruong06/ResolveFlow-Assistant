# G06-06C3-S2a — Backend creative binding acceptance

Status: ACCEPTED_WITH_RECORDED_LIMITS, 2026-10-30.
Reviewed report: G06-06C3-S2a-R1-THEN-S2b-D, SHA256 75b34588a58ec95f39c6aebac3008d83e0ac6b2c4ff08741f8a8c26a3669c467.

Codex independently ran tests/core/test_story_copilot_creative_binding.py and tests/ui/test_prompt_worker_creative_binding.py: 22 passed in 6.67s, exit 0. Evidence: .bridge/ux-evidence/G06-06C3-S2a-R1-CODEX-REVIEW/verification.json and pytest_observed.log.
Three independent constructor probes (top-level field, nested reference note, mapping input) each show shares_request_object=false, captured signature unchanged and caller signature changed. They deliberately do not start a QThread; the focused worker suite separately exercises real threaded completion and an after-start boundary.

Accepted: canonical complete JSON in direct copy_video/run_ai formatter; exact byte budgeting, empty legacy parity; request and constructor-owned context with run-boundary validation; actual original formatter reached before controlled source deletion/mutation/cancel yields no successful publication; reference metadata remains unread. No intake/core geometry changes needed.

Limits: no current StylePage→ReviewPage creative wiring; no actual provider parity/inference, reference media/URL analysis, project disk persistence or common review/approval execution proven. Nonempty creative context remains rejected for batch/overview intents until explicitly implemented. The worker tests now have monotonic physical-finish checks/finally cleanup, but no strong retention fallback if cooperative join fails; strengthen that test-only residual in the next bounded test slice, without reopening accepted runtime ownership. Environment isolation should explicitly cover remaining XDG/visual roots there. Backend acceptance is not desktop acceptance or closure of G06.

Next: G06-06C3-S2b-BACKEND-BATCH-SLICE; binding corrections in 06C3-S2b-codex-decisions.md. S1 and earlier contracts remain frozen.
