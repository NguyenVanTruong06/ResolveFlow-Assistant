# G06-06C2c1 — CODE_ACCEPTED_WITH_RECORDED_LIMITS / LIVE_PENDING

2026-10-30. Worker report SHA256 verified: `812655c4e056f52a1f911b937c7b5029da956e13110188d3de564d09059618ae`.

Fresh Codex finite suite: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_source_frame_evidence_parser.py tests/core/geometry/test_local_metadata_acquisition.py -q` → **150 passed in 0.52s, exit 0** (94 parser +56 acquisition cases). Independent **15/15** lifecycle/boundary reprobes PASS at `.bridge/ux-evidence/G06-06C2c1-R1-CODEX-REVIEW/probes.json`.

Accepted code scope: child ownership covers reader initialization/start failures; late reader errors/unclean EOF cannot produce success; final drainage is supervised; strict cancellation returns and duration lexical gate; explicit format null denied; results own/revalidate header/freshness; real ns stat fields required. The existing cleanup branch tests and source review support this bounded worker-oriented acquisition contract.

Verification limits: all current acquisition child tests are fakes/mocked; Codex spawned no real child or decoder for this review, owned temp probe directory removed and reader count returned to zero. Windows flags are mocked, not live Windows acceptance. Source snapshot equality is best-effort freshness, not content/CFR or race-proof identity. No GUI/manifest/worker integration, disk cache, full digest, exact frame/CFR verifier or Resolve execution is implied.

Next task `G06-06C2c1-LIVE` verifies actual local binaries with tiny owned fixtures and controlled owned-child cancellation/timeout. It must keep production/tests unchanged and label actual ffprobe vs controlled shim evidence separately. Missing legacy get_video_metadata import/async manifest ownership remain deferred. G06 is not complete.
