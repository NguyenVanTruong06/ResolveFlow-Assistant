# G06-06A1-R2 — Codex nghiệm thu contract cơ sở, 2026-10-28

**G06-06A1 CODE_ACCEPTED_WITH_RECORDED_LIMITS.** G06 chưa đóng.

Fresh command: PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_batch_planner.py -q -> **39 passed in0.46s, process exit0**. Đọc helper parse, validation trước skip, raw metadata và strict range/point checks. Không full suite/app/model/Resolve.

Probe lại đúng các residual: Mapping window bigint, unknownduration+invalidstatus, source_missing+badhasaudio, project/fileNone, overrun và pointatend đều controlledValueError; raw manifest_id=' M ' và window_id=' W ' được giữ; validpositive end==duration nhận đúng. Evidence .bridge/ux-evidence/G06-06A1-R2-CODEX-REVIEW/.

Nhận toàn bộ scope cơ sở đã giao sau I/R1/R2: immutable pure DTO/partition, global source IDs/ranges/accounting, exact owned envelope bytes, hard/time limits, complete content/config signature và malformed guards. Không đồng nghĩa full V1 prompt luôn vừa hoặc input đã được xác minh bằng file hash; planner không đọc nguồn.

## Giới hạn giữ nguyên

- Chưa có producer adapter từ G05 vào normalized analysis mapping, chưa tích hợp app/UI.
- Chưa có hierarchical semantic summaries, queue/ledger/restart resume, matcher hoặc edit-plan validation/execution.
- Default32768 chỉ bảo vệ render_batch_prompt envelope/prefix/suffix mà module sở hữu, không token budget hay full StoryCopilot prompt về sau.
- Những DTOs/provenance fields được caller cấp không tự chứng minh machine analysis complete; sampled content_hash khác fullSHA.

## Bước tiếp

Giao G06-06A2-D, designonly hẹp: adapter từ verified build_clips_data G05 sang input A1, và chốt boundary với hierarchy/batchprompt consumer kế tiếp. Một producer/caller được chọn, không khảo sát lại toàn G06. Cần field matrix/fixtures/API để không tự suy ra has_audio=False từ transcript rỗng hoặc bỏ source/quarantine/provenance. Không code/test/inference hoặc sửaA1accepted ở giai đoạnD này.
