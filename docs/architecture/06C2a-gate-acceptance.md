# G06-06C2a — ACCEPTED_WITH_RECORDED_LIMITS

2026-10-30. Report SHA256 verified: `25136b0c6cc61d3661d0fbef3d437d40fccc4cf840a07f1a47bebc7512f82afc`.

Fresh Codex command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/geometry/test_frame_geometry_calculator.py tests/core/contracts/test_time_contracts.py -q` → **98 passed in 0.40s, exit 0**. Independent reprobes: **14/14 FPS/range + 14/14 Fraction = 28/28 PASS**, evidence `.bridge/ux-evidence/G06-06C2a-R2-CODEX-REVIEW/probes.json`.

Accepted: frozen owned six-field QuantizedCFRSpan; exact raw bounds, half-even native source frames, exact Fraction speed rescale to record duration using public pts_to_frame; strict nested FPS/range/Fraction validation before arithmetic. No clamp/epsilon/minimum-one-frame, no mutation of caller/proposal/approval. Scoped source review and tests support math contract only.

Limits: caller-supplied CFR/rate/framecount are not file verification. No source IO/supplier, whole timeline/cursor/timecode/hook, asset or word placement, creative input/approval UI, audio synchronization or Resolve execution. G06 remains open. C2b design has binding corrections in `06C2b-codex-decisions.md`; approval of C2a does not approve HEADER_HEURISTIC as CFR proof.
