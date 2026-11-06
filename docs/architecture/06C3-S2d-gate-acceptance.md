# G06-06C3-S2d — UI code acceptance and harness limits

2026-10-30. UI CODE: ACCEPTED_WITH_RECORDED_LIMITS. Harness failure-path closure: CHANGES_REQUIRED. Report SHA256 76d6b45546050eeb97a4c522bcafe25a390ef6701a3e4502e7ab75e5de52942f.

Codex fresh prescribed7file suite:230 passed in76.66s/exit0. Independent original3UI probes all pass: user batch draft survives creative invalidation, missing pending member denied, uppercase result digest denied. Both valid legacy explicitNone and valid creative terminal controls still publish. Source review confirms exact membership/type/lowercase proof and preserved user text. DirectorWindow source hash matches prior frozen record. Evidence .bridge/ux-evidence/G06-06C3-S2d-R1-CODEX-REVIEW/probes.json and pytest_observed.log.

Accepted: current StylePage creative draft reaches owned source prompt requests via real supplier; all6source intents retain legacy and creative proof domains; stale creative context blocks reuse; user draft text remains. Provider dispatch is mocked, metadata refs remain unread, no semantic-compliance or timeline execution claims. Prior backend/core/UI decisions not reopened.

Desktop functional evidence: reviewed normal5case metrics and active-real-worker fault metrics (normal0/fault2 reported). Widget-render case5 screenshot reviewed: user batch input remains, stale state shown; narrow1100x720 panel horizontal clipping/readability observed, not accepted as final layout quality. These are WIDGET_RENDER/COMPONENT evidence, not OS-wide UI/provider/Resolve acceptance.

## Residual harness defects, safely reproduced with fakes only
1. cleanup_harness_resources calls director.close even when cancellation returns still_running. Fake stubborn worker -> close_calls1, expected0; session retained correctly but window/owner lifetime is not. run_harness also returns0 for normal mode regardless of cleanup result. Stop/destroy/process-exit decision must obey actual cleanup gate and retain owners if busy.
2. shutil.rmtree(ignore_errors=True) then unconditional session_status REMOVED: injected no-op removal leaves directory present while status says REMOVED. Metrics/exit must reflect postcondition and failure, not just attempted operation.
3. Current harness hash0d739f81a1a508526c12f985489989ed043b7deefc1bc6cdfe72d6d79801198f differs from hash quoted in submitted report. Capture exact tested file hash in final evidence and do not edit after finalization. Existing positive cooperative fault run does not prove these failure branches.

Next grouped task: harness-only corrections/tests + focused common proposal review/approval design. Zero src edits in next task. UI code accepted and frozen; no repetitive full230rerun. No shutdown; G06 remains open for S3/common review and final integration.
