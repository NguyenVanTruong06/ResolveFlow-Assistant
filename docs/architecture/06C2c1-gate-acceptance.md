# G06-06C2c1 — ACCEPTED_WITH_RECORDED_LIMITS

2026-10-30. Report SHA256 `6db40f899502315c18c71c4d31fab0c7db16dae6b49b019f3e22a0ede142e149` verified. Earlier code gate remains valid; acquisition source hash unchanged.

Codex fresh focused harness suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/task/test_verify_06c2c1_live_harness.py -q` → **7 passed in 2.33s, exit0**. Shared owner/source reviewed: preparation covered, running worker/reader retains session, hooks restored, intentional fault requires expected point/name/cancel/terminal evidence. Existing normal/fault metrics/logs were audited; successful live cases were not repeated unnecessarily.

Reviewed Worker live evidence at `.bridge/ux-evidence/G06-06C2c1-EVIDENCE-R1/`: normal exit0 with9terminal tracked children; fault exit2 with2terminal tracked children, intended fault reached while worker active, cancellation verified;0workers/readers/unreaped children and session deletion. Actual NTSC header60frames/30000/1001/1001÷500seconds, source hash and five stat fields unchanged. Real ffprobe/audio/corrupt denials and controlled-shim timeout/cancel distinguished. Codex's fresh artifact invariant checks saved under `.bridge/ux-evidence/G06-06C2c1-EVIDENCE-R1-CODEX-REVIEW/`.

Limits: Linux tiny generated fixtures and controlled shim, not Windows/GUI/Resolve/user-footage acceptance; HEADER_REPORTED, not decoded frame count/CFR/content identity proof. No async consumer or legacy metadata import repair is implied. G06 remains open. Next implementation targets only fresh manifest creation in PromptPreparationWorker with explicit provider and no new GUI subprocess path.
