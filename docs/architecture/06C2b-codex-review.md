# G06-06C2b-I — REQUEST_CHANGES (selection boundary and diagnostics)

2026-10-30. Report SHA256 verified: `7d9662d23923fd6625da76d80916b3aec0aba943b6c094af9629adabac2874b5`.

Fresh Codex finite suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_source_frame_evidence_parser.py tests/core/geometry/test_frame_geometry_calculator.py -q` → **152 passed in 0.39s, exit 0**. Main header parsing, exact rational values, lowest/explicit stream choice and no CFR promotion are implemented. C2a remains accepted; C2b not accepted yet.

## Consolidated remaining checklist

**A. Missing vs explicit null:** parser uses `.get()`/`is not None` for disposition and attached_pic. On a video entry, explicit `disposition: null` or `attached_pic: null` is accepted as absent, contrary to the strict present-field contract. Missing keys should retain documented defaults; present invalid values must reject.

**B. Consumption scope:** parser validates disposition on audio/other non-video entries too. A valid video plus audio entry with `disposition: 'bad'` wrongly rejects. Only index/codec_type structure is globally required; video-specific disposition is consumed only for video eligibility, just like unselected rate/count metadata is ignored. Keep duplicate/index/codec_type rejection on all streams.

**C. Error reporting must not become a second failure:** parser and DTO interpolate unchecked `!r`/large integers in error messages (including valid huge selected index not found / duplicate huge indexes). With `-(10**5000)`, Python's integer-to-string limit replaces the required ERR_* with an unrelated ValueError. With an invalid index whose __repr__ raises, RuntimeError escapes. Nested malformed FPS/time_base negative big integers show the same diagnostic problem. This was explicitly prohibited by the workorder; do not disable Python's digit limit or constrain valid positive native integers merely to simplify formatting. Header boundary owns its controlled error prefix, including when a reused accepted helper raises ValueError without the header prefix.

Evidence `.bridge/ux-evidence/G06-06C2b-CODEX-REVIEW/probes.json` records **13 counterexamples across these three groups**. These are boundary/diagnostic failures, not 13 independent media algorithms. Fix all groups in one R1 with symmetric tests and code-path audit; no per-field follow-up tasks.

## Next

G06-06C2b-R1-THEN-06C2c-D: patch only two current modules + focused tests; then design real bounded local header acquisition and its first actual producer/consumer path. No live probing/manifest repair in this task, no header-to-CFR promotion, no changes to accepted C2a contracts. Design scope must make a useful IO integration path visible rather than claiming the pure parser already reads user files.
