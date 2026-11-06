# G05-05C1-R3 — Codex nghiệm thu code
Ngày: 2026-10-27
Trạng thái: **CODE_ACCEPTED_WITH_RECORDED_LIMITS**

## Bằng chứng mới
Codex đọc report, hai production modules, các nhánh prepare/commit, counters/terminal và receiver. Chạy đúng finite command:
```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_review_page_visual_consumption.py tests/test_review_state.py tests/ui/test_director.py tests/ui/test_source_analysis_routing.py tests/ui/test_source_analysis_visual_describe.py -q
```
**82 passed in 6.88s, exit 0.** Không fullsuite, không suy desktop/real-model từ offscreen.

## Chấp nhận ba residual R3
1. Prepare config/path/runtime hoàn tất trước mutation state, cancel worker cũ và clear UI. Các probe input lỗi trong finite suite giữ rows/generation/owner.
2. Loaded inventory tách usable/source-invalid/provider-error, whole missing/changed trả source_unavailable; mixed trả partial; online cache miss và zero có nhãn no_data. Good rows không mất, invalid-source rows không publish.
3. Load Review không gọi chronological sorter/ffprobe/hash/stat trên GUI. Raw path giữ caller order; metadata shot_time số sẵn có được stable sort in-memory. Worker request/inventory/payload cùng thứ tự.

C1 code được đóng theo phạm vi các probe đã giao. Không tiếp tục sinh C1 task nếu chưa có bug tái hiện ảnh hưởng contract.

## Giới hạn bắt buộc giữ nguyên
- Candidate metadata/stat không phải full source execution verification.
- Numeric shot_time sort không chứng minh diễn tiến quay thực tế nếu input thiếu metadata; không tự probe media để có chronology.
- Chỉ đường load_analysis_data đã bỏ GUI I/O. get_video_prompt/get_final_cut_words/_extract_words_from_rows còn gọi manifest/cache/sorter trên GUI, thuộc C2; không claim toàn ReviewPage zero blocking.
- build_clips_data vẫn tự duration=60 nếu chưa biết, default dict cache là no_dialogue, lấy coverage chưa phân biệt mọi tầng; đây là vấn đề consumer cần chốt C2, không giả đã đóng.
- G05 chưa nghiệm thu: còn Prompt/provenance/export routing và speech real/integration acceptance; không chứng minh nguồn3giờ hoặc phần cứng.
- Worker report được giữ nguyên. Người dùng đã resume; nghỉ sau nghiệm thu G05 vẫn giữ, không shutdown.

## Bước tiếp
G05-05C2-D: chốt đường chuẩn bị/copy/run Prompt có thẩm quyền trên worker, giữ synchronous getter compatibility rõ ràng. Thiết kế hữu hạn dựa code hiện có, không sửa production/tests trong bước D.
