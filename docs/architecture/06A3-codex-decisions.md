# G06-06A3-D — Codex binding decisions, 2026-10-28

**DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS.** Cho phép G06-06A3-I đúng ba production modules đã đề xuất, không thêm vòng D.

## Ba quyết định

1. Duyệt additive section dưới controls V1/Transcript/V2: Prepare, chọn lô, xem prompt/metadata, Copy. Không thay V1/V2, không tạo một QWidget cho mỗi batch.
2. Duyệt prefix/suffix trực tiếp trong BatchBudgetConfig, render_batch_prompt là toàn bộ chuỗi copy. Bác cam kết wrapper<=6000bytes hoặc evidence>=26000bytes: chưa đo, input động có thể lớn. Không giới hạn byte/token của AI web được suy từ cap ứng dụng. Đo UTF8 thật, equality hợp lệ, oversize explicit error, không cắt text.
3. Duyệt immutable in-memory snapshot và context invalidation. Tab switch không tự scan. Copy là hành động explicit: kiểm tra lại source identities trên worker trước khi ghi clipboard, không chạy lại build_clips_data/model/planner. Chuẩn bị xong không tự copy. File có thể đổi sau prepare dù context UI chưa đổi, nên signature UI đơn thuần không đủ làm file check.

## Corrections cùng task I

- get_available_assets_summary(asset_level=1) vẫn os.listdir repo assets và liệt kê sample media, không phải top5 matching kho thật (code hiện dùng limit8). Batch first slice dùng **source-only**: không gọi helper đó, không inject demo assets/filename mẫu hay claim đã match. Assets ngoài nguồn để empty, 06B mới làm matching. V1 cũ chưa sửa trong scope này.
- Không copy nguyên JSON sample V1 với clip_index=1, fake filenames hay khẳng định kết quả tự dựng Resolve ngay. Wrapper batch dùng global manifest_id/batch_id/clip_id và source bounds trong envelope, giữ unknown/partial/provenance, chỉ đề xuất draft local-batch; không global story/hook/full coverage claim. Import/merge/validated EditPlan còn 06C/G07.
- prefix/suffix phải cố định cho một request và không thêm header/label/delimiter ngoài render sau đo. Batch index lấy từ envelope, tổng số lô nằm UI, không chèn N sau partition rồi làm drift bytes.
- Intent là SSOT: thêm prepare_batches/copy_batch vào request intent hiện hành, không tạo cặp batch_mode/intent mâu thuẫn. Existing copy_video/run_ai giữ default và behavior. Batch payload tách khỏi prompt_text V1; _on_prompt_preparation_finished phải phân nhánh batch trước guard prompt_text rỗng.
- prompt_copied hiện là Signal(str), không emit('batch',batch_index=...). Nếu cần notification, emit('batch') một tham số; không thêm run-ai dispatch cho batch.
- Không nâng planned_items thành số clip: UI phân biệt distinct planned clip IDs, số partitions/items, unaccounted, adapter issues và source/evidence coverage.
- Worker hiện có no_data trước formatter: batch phải account known-duration unscanned/short-absent thay vì bị V1 no_data chặn. All-unaccounted/empty không fake ready-to-copy; vẫn có lý do/summary.
- Không hứa triple-stat luôn chứng minh hiện trạng 100% hay zero race sau check. Worker giữ checks thật trước publish, copy worker rechecks baseline stat identities; sampled hashes không full-file proof, race sau check được ghi giới hạn.
- Xóa hoặc gắn UNMEASURED các byte figures 5600/9810/24000–31500 và số lô4 khi không có fixture hoàn chỉnh chạy/đo. Không ghi counts/benchmark dự kiến như evidence.

## Phạm vi / kiểm tra

StoryCopilot helper source-only + PromptPreparationWorker branching + ReviewPage controls, tests tập trung core/worker/UI theo workorder. A1/A2/G05 producer không sửa. Không runtime model/Resolve trong implementation verification. Giữ request ownership, exactly-once logical terminal, physical busy đến thread.finished; current-context + selected batch recheck trước explicit copy. No ledger/matching/semantic summarizer/shutdown.
