# G06-06C2c2 — CODE_ACCEPTED_WITH_RECORDED_LIMITS / LIVE_PENDING

2026-10-30. Reviewed report SHA256 `444e91968a824413bc15037c1e0fbd473f6188247ad9344539b0f8c997d9cb1f` matches current file.

Codex fresh finite command: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/director/test_prompt_preparation_metadata_slice.py tests/ui/test_prompt_preparation_routing.py -q` → **67 passed in 40.33s, exit0**.

Independent direct boundary reprobes: **15/15 missing required members controlled; 6/6 original malformed providers denied**. Owned nested reconstruction and explicit optional None positive controls pass. Evidence: `.bridge/ux-evidence/G06-06C2c2-R2-CODEX-REVIEW/`.

## Closed production/test findings

- Required members now read through sentinel before use; absent duration is not silently interpreted as unknown. Missing header/freshness attributes yield controlled errors. Strict provider revalidation precedes coercion/freshness comparisons; accepted typed values are reconstructed as owned DTOs.
- Four tests now call the original V1/batch formatter, then mutate the owned source or cancel before returning. Terminal publication rejects post-format source mutation and cancellation. Batch worker adds the missing post-render-loop cancellation checkpoint. Both positive handoffs remain covered.
- Timeout retains known clip_path; cleanup failure takes precedence over cancellation. No new schema fields, provider APIs, persisted authority or CFR claim. Fresh worker branch2c uses persist=False; branches2a/2b and legacy defaults remain unchanged.
- Test owner now retains a strong reference before reporting an unresolved live QThread. HOME and visual cache are isolated; no unsafe termination introduced.

## One remaining evidence correction, not a production blocker

New feature fixture replaces `clip_manifest.sort_clips_chronologically` only. `_probe_and_create_clip_entry` still calls its independently imported `get_clip_shot_time` alias, which can invoke the old ffprobe locator/subprocess. Codex controlled probe recorded **one legacy subprocess dependency call**, with the actual launch replaced by a fake denied response (zero children spawned by this probe). Thus the report's blanket claim that feature tests perform no ffprobe work is not established. Correct both chronology helper aliases in the next evidence task and test that subprocess/locator dependency is not reached. Do not alter production chronology or reopen already accepted defaults.

## Acceptance boundary and next work

This is source-reviewed/focused-offscreen acceptance of the fresh metadata consumer, not real-binary worker integration or desktop/Windows/Resolve acceptance. Prior C2c1 live evidence proves the acquisition helper separately; the new worker path still needs a small real-ffprobe handoff check. Headers remain HEADER_REPORTED, not CFR/native decoded-frame proof; cached historical fps, GUI legacy chronology, complete geometry, group-review UI and G07 execution remain outside this gate.

Next **G06-06C2c2-FINAL-THEN-LIVE**, HIGH: test-only chronology isolation correction plus bounded real worker/ffprobe evidence. No production edits, no new design round, no full test run, no models/Resolve/network/user footage, no shutdown.
