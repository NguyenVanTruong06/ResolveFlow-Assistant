# G06-06C2b — ACCEPTED_WITH_RECORDED_LIMITS

2026-10-30. Report SHA256 `88f08c1804052e0a7ac7c0e619356dde7dbffec8e497db909be7788db99f0cf4` verified.

Codex fresh finite command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_source_frame_evidence_parser.py tests/core/geometry/test_local_metadata_acquisition.py -q` → **143 passed in 0.47s, exit 0**, comprising 94 parser cases and 49 new acquisition cases. This is NOT acceptance of the acquisition module. Parser final BadEq reprobe now rejects with ERR_INVALID_VIDEO_METADATA and equality_calls=0; prior null/scope/diagnostic groups remain covered by the passing parser suite.

Accepted C2b only: owned six-field VideoStreamHeader, bounded deterministic stream selection, attached-picture filtering, strict unknown/number/string handling and safe diagnostics. Header values remain reported metadata; no CFR/verified frame count/permission inference or geometry adapter. No subprocess/live media evidence belongs to this parser gate.

C2c1 IO implementation requires changes documented in `06C2c1-codex-review.md`. C2a remains accepted; G06 is not complete.
