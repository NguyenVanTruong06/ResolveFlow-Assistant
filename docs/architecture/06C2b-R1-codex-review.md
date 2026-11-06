# G06-06C2b-R1 — 13 repairs verified; one type-gate residual

2026-10-30. Report SHA256 verified: `a22410fac0a630cd7c477aa7b6883433d81a44901c700dc01230cec230381fb5`.

Fresh Codex command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_source_frame_evidence_parser.py tests/core/geometry/test_frame_geometry_calculator.py -q` → **172 passed in 0.42s, exit 0**. All original **13/13** counterexamples independently re-run and PASS. Explicit null, non-video consumption scope and error diagnostic groups are closed; do not reopen them.

One previously specified requirement remains: `_parse_reported_nb_frames` tests `raw_val == 'N/A'` before its type gate. Passing a wrong-type runtime object with __eq__ raising produces RuntimeError and calls the object. Prior workorder explicitly required type gating before such sentinel comparisons. Repro `nb_frames=BadEq()` → RuntimeError('must type-gate before equality'), calls=1; expected ERR_INVALID_VIDEO_METADATA with calls=0. Evidence `.bridge/ux-evidence/G06-06C2b-R1-CODEX-REVIEW/probes.json` includes original13 plus this residual.

Fix by handling None, strict int branch, strict str branch with sentinel handling inside it, then controlled type rejection. No need for broad try/except or accepted dependency changes. Combine the tiny final patch with real bounded IO implementation below, not another standalone design cycle.

C2b remains pending this last guard; C2a remains accepted. Main parser is reported metadata only, no CFR promotion. C2c direction accepted with binding scope/corrections in `06C2c-codex-decisions.md`.
