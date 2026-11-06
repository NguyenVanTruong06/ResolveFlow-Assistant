# G06-06C3-S1-R1 / S2 design review

2026-10-30. Report SHA256 `4201aad2c13b2567b3e049041eb215c74631a75d89b388dcb60879c748f67b59` verified. Codex fresh focused UI suite **17passed in1.24s, exit0**. Independent original9reprobes now9/9PASS. Lossless text/item roundtrip, scientific duration, page-level bad-load protection, missing legacy field and invalid-note Qt boundary are fixed. Retain these fixes.

Two remaining original checklist points reproduced at `.bridge/ux-evidence/G06-06C3-S1-R1-CODEX-REVIEW/panel-and-bulk-residuals.json`:
- Public panel.load_from_dict still str-coerces unvalidated data and mutates controls. Calling with free_brief=123 overwrites 'LAST GOOD' with '123'; subsequent page save=True persists it. Page-level validated loader is protected, but panel boundary requested in prior workorder remains unsafe. Normalize/prepare candidate before any UI write, no coerced refs/fields or dropped malformed rows.
- Bulk picker [unsupported.pdf, valid.png] ends with1accepted reference and no visible error: valid add clears previous rejection. Aggregate admission/rejection or atomically reject batch; file order must not hide dropped items. Existing cap/bulk test only tests canceled dialog after reaching64, not mixed selections.

## S2 direction accepted, binding corrections in implementation

Worker+formatter split before UI wiring is appropriate. Actual request intents are copy_video/run_ai/prepare_batches/copy_batch/validate_batch_draft/copy_project_overview, not run_ai_ollama/cloud. Actual controller method is run_ai_script, not run_ai_pipeline; request_prompt_preparation has existing supplier/lifecycle, no pretend context positional arg. Copy_batch reuses prepared rendered snapshots; it does NOT call generate_copilot_prompt. Batch prepare uses build_batch_prompt_wrapper. Correct these design traces without rewriting these paths now.

First S2a supports CreativeContext only for direct copy_video/run_ai preparation; nonempty context on other intents must be explicitly denied before source IO until their own formatter/snapshot bindings exist. Legacy None/canonical empty preserves current routes. No claim tri-mode UI parity or allbatch context support yet.

Use canonical escaped JSON for all creative data, not free-form Markdown/raw interpolation or ':g' numeric display (lossy precision). reference_urls are strings, not URL+note objects; only ReferenceItem has user_note. Existing helper signature/32KiB canonical limit preserved. Insert immutable creative block INSIDE every formatter budget candidate/minimum skeleton, never append after budget decisions, truncate directives or drop source ID rows.

Next **G06-06C3-S1-FINAL-THEN-S2a-I**, HIGH: A narrow panel/bulk finalization (one UI file), then B two backend consumer modules. At most3production files across gated phases. No coordinator/ReviewPage/new AI call, no disk persistence/approval/Resolve changes. Accepted metadata/geometry frozen.
