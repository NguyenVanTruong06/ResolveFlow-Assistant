# G06-06C2c2-I — REQUEST_CHANGES: validate provider evidence and complete tests

2026-10-30. Report SHA256 verified: `ee78f15d677178d01960c322ee131dee16b2d43fe65cb0654ad0442180ae1d9d`.

Fresh Codex finite suite completed: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/director/test_prompt_preparation_metadata_slice.py tests/ui/test_prompt_preparation_routing.py -q` → **58 passed in 39.40s, exit0**. Additive API, persistFalse worker creation, background acquisition and replay/default preservation implemented. However the workorder's malformed provider result and test/lifecycle checklist is not fully met.

## A. Provider result revalidation is missing

Core `_probe_and_create_clip_entry` only isinstance-checks LocalHeaderAcquisitionResult, normalizes its raw file_path and coerces fields. Worker dereferences the raw result before any independent validation. Six fresh probes all wrongly succeed: duration string'9.0' becomes9.0; NaN becomes unknown0; boolean header index accepted; fake FPS object with as_float becomes123; relative result path becomes absolute and passes; freshness.file_size_bytes=False ignored by core.

These are not valid optional unknown fields. Reconstruct/own/revalidate the entire provider result via the existing DTO BEFORE path/number/freshness use in BOTH core and worker; no duck-typed as_float acceptance. Only valid None or valid positive values overflowing legacy float representation map0.0. Evidence `.bridge/ux-evidence/G06-06C2c2-CODEX-REVIEW/provider-probes.json`.

## B. Terminal diagnostics / cleanup failure priority

Fresh direct-run isolated worker probes (not physical-thread evidence) found timeout source_error payload has clip_path=None, despite the provider knowing the requested source. Also if acquisition requests worker.cancel then raises ERR_PROBE_CLEANUP_FAILED, the generic is_cancelled branch emits cancelled with empty payload, losing the cleanup error. Keep known current path on all fresh-provider denials; cleanup failure must remain source_error with its code/path even when cancellation was requested. Ordinary InterruptedError remains cancelled. Evidence terminal-probes.json. No real subprocess/model started by probes.

## C. Tests have copied requirements not actually exercised

- New worker tests use unbounded while worker.isRunning loops and lack finally cancel/wait, despite docstrings promising those safeguards. Add one bounded event-processing helper/fixture with actual ownership teardown and isolated home/cache/visual dependencies.
- Case6's title/docstring mentions prepare_batches but only constructs copy_video. Add actual positive batch handoff and assert terminal/result; replay2a/2b must assert successful terminal, not only zero probe calls. Add final source mutation/cancel after formatting tests for both relevant branches, plus missing-tool/no-video/cleanup failures with0formatter/0save.
- Control legacy chronology probes explicitly in the new feature tests. Do not assert whole-app GUI0subprocess; earlier chronology limitation remains unchanged.
- Correct Codex workorder typo: actual ClipEntry has **10 fields**, not11. Preserve the actual schema, fix test/docstring wording; never add a field to match the typo.

Next G06-06C2c2-R1 stays in the same two production modules and current slice test file, with all A/B/C addressed in one report. C2a/C2b/C2c1 acceptance remains unchanged. No new design, acquisition/parser/UI/chronology implementation or live execution in this repair.
