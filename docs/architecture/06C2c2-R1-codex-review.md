# G06-06C2c2-R1 — Prior probes verified; narrow residual checklist

2026-10-30. Report SHA256 `f9e227fb8f573792ed5f37d812630021a0381c80b6ab499deb35aa248c37d664` verified. Fresh finite suite **62 passed in41.02s, exit0**, command: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/director/test_prompt_preparation_metadata_slice.py tests/ui/test_prompt_preparation_routing.py -q`.

Original6 malformed provider reprobes now **6/6PASS**. Timeout path and cleanup-failure precedence are covered by the passing updated test. Actual positive batch handoff is now executed. Do not reopen those closed points or accepted acquisition/parser code.

## Remaining A — Required DTO attributes vs optional values

`_validate_and_reconstruct_header_result` uses getattr(res,'format_duration_sec',None): an object with the required attribute DELETED becomes valid unknown. It also dereferences nested header/freshness fields outside protected constructor calls, so deleting stream_index/r_frame_rate/avg_frame_rate/time_base/file_size_bytes leaks AttributeError from standalone core create.

Fresh required-member matrix over the 4 LocalHeaderAcquisitionResult +6 VideoStreamHeader +5 SourceFreshnessStat fields: **9/15 controlled rejections;6 residual failures**. Evidence `.bridge/ux-evidence/G06-06C2c2-R1-CODEX-REVIEW/required-member-probes.json`. None as an optional VALUE is valid; a missing required dataclass attribute is not. Read all required members with an explicit missing sentinel/error before dereference or reconstruct using required fields with controlled error translation. Don't silently map structural damage to unknown metadata.

## Remaining B — Final-guard assertions are still at the wrong stage

Tests case6_3/case6_4 mutate mtime INSIDE fake acquire, not after formatter. They can pass via earlier source checks; neither executes its claimed final cancellation case. Add post-render mutation and cancel for both intents with formatter-reached evidence. Use actual V1 StoryCopilot.generate_copilot_prompt or batch_planner.render_batch_prompt wrapper and preserve descriptors; don't mutate terminal emit itself or stub final checks.

## Remaining C — Test ownership/isolation limits

Bounded wait is an improvement; on failed wait pytest.fail alone does not explicitly retain a still-running QThread owner. Retain refs before failing, no deletion/terminate. New feature test file still lacks explicit home/visual-cache isolation/chronology fake. Add bounded fixture dependencies and avoid real legacy ffprobe in these feature assertions; don't claim whole legacy GUI is zero-probe. No source/runtime behavior changes needed for this test correction.

Next G06-06C2c2-R2 closes ONLY A/B/C; production expected only core normalizer, worker guard change only if a precise post-format test proves a real regression. Preserve all accepted implementation choices/default/replay/10-field schema. No new design or live task in this repair.
