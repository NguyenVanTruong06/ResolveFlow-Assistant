# G06-06C2c2 — Fresh worker manifest provider, bounded implementation approved

2026-10-30. Design reviewed from report `6db40f899502315c18c71c4d31fab0c7db16dae6b49b019f3e22a0ede142e149`; DIRECTION_ACCEPTED_WITH_BINDING_CORRECTIONS. Implement one fresh-creation path, not a global manifest migration.

## Source corrections

- Actual GUI names at auto_window.py:4513/5113 are `_build_copilot_clips_data` and `_apply_json_data`; ReviewPage:5516 is `_apply_json_data`, not the invented names in design. PromptPreparationWorker.run begins near1180, selected fresh-create branch near2229. Fix docs in same implementation task.
- `_is_cancelled` is a bool, not a callback. Use existing callable `self.is_cancelled` with acquire_local_video_header; no bool passed as cancelled_fn. RationalFPS has `.as_float()`, not float(rate).
- Branch2a reconstructs request metadata; branch2b loads explicit cache dir; only2c creates fresh. Preserve reuse/IDs/order and zero NEW header probes on2a/2b; don't call reuse verified CFR or claim stale historical metadata fixed.
- Existing req.manifest_cache_dir is not guaranteed per-request; fresh code currently writes directly there when supplied. New worker path must not overwrite that shared/cache authority. Approve `persist=False` for fresh in-memory worker manifest; default `persist=True` keeps legacy API behavior. Request-owned result handoff remains existing manifest_metadata, no new persisted schema/global cache.
- Important omitted callgraph: chronological_sorter.get_clip_shot_time calls existing ffprobe for container creation_time; create sorts then _probe calls it again. Therefore GUI callers are NOT proven globally zero-subprocess/0.0s. This slice adds no new header probe on GUI/default path; chronology IO/locator side effects remain recorded legacy limits, not fixed here. Don't patch chronology or silently change chronological IDs/order to fake zero-IO evidence.

## Policies / Q1–Q3

1. Provider-enabled path only maps truly missing/unrepresentable optional duration/FPS to0.0, never30. Preserve default/providerNone legacy behavior, ClipEntry.from_dict and existing disk manifests. Don't delete the legacy import branch in this bounded change and then claim global repair.
2. No raw DTO/provenance added to ClipEntry or request schema. Typed provider result is locally owned/revalidated, file_path must bind requested source, worker freshness must match job preflight. Reported float mapping is not native source evidence. Keep six-field header and existing ClipEntry serialization unchanged.
3. Header timeout3.0s per clip accepted for this worker path, not a total job/GUI responsiveness guarantee. Remove unmeasured '<0.5s for20clips' claim. Acquisition failures, source mutation and cancellation propagate: no generic except returning unknown/default entries. Only valid successful headers with optional missing fields map0.0.
4. Cancellation strict callback checkpoints before/after provider and immediately before optional save. New worker uses persistFalse; do not save partial/shared manifests on failure. Existing final source-stat and request/generation terminal bindings remain unchanged, source_changed/source_error/cancelled fail closed.

Exact additive signatures, finite cases and module scope are in G06-06C2c2-I workorder. No new UI/acquisition/parser edits or whole timeline/CFR/approval execution work.
