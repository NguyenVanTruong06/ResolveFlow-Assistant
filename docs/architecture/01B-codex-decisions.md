# Quyết định Codex — G01-01A-R2 và G01-01B-D

## 01A: ACCEPTED trong phạm vi đã giao
Codex đọc bản vá và chạy:
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/core/contracts/test_asset_dto.py tests/test_storage_ingest.py -q
61 passed in0.73s, exit0.
Các nhánh optional field, template subtype bắt buộc và finitefloat duration đã sửa theo review. Consumer vẫn chỉui_assets; chưa coi toàn app thống nhất DTO hoặc persistence đã đi qua serializer mới.

## Trả lời ba câu hỏi 01B
1. Đồng ý tách status_contracts.py và time_contracts.py theo trách nhiệm. Chỉ time_contracts được giao triển khai trước; status cần semantics chính xác.
2. Property có thể giữ positional signature, nhưng CHƯA duyệt InsertResult.status. Không được suy skipped từ câu “đã có”/“bỏ qua”; cũng không suy mutation applied chỉ vì ok=True: native-transition guidance có thể thành công mà không sửa timeline.
3. Giữ nguyên LUT dictionary status="error" cho legacy caller. Canonical adapter về sau có thể xuất mutation_status="failed" riêng; không sửa giá trị key cũ rồi gọi đó là backward compatibility. Counter mixed applied+failed phải bảo toàn partial.

## Điều chỉnh thiết kế bắt buộc
- Functions thực: apply_lut_to_current_clip, insert_sfx_to_track, describe_with_ollama; không apply_lut_to_playhead_clip/append_audio_to_track/describe_video_windows nếu không có trong repo.
- Dòng mô tả GetEnd half-open chỉ là convention code hiện dùng, chưa phải bằng chứng runtime Resolve; không sửa boundary API từ giả định đó.
- 600s×23.976 và600s×24000/1001 chênh khoảng0.0144frame, cảhai round thành14386; ví dụ “10phút lệch1frame” sai. Vẫn dùng rational để giữ nhất quán, không tạo bằng chứng benchmark sai.
- Chốt half-even chính xác bằng Fraction ở boundary, không để “round hoặc floor+0.5”.
- Missing FPS không tự thành30 trong metadata nguồn mới. Nếu caller legacy có fallback30 thì giữ ở adapter compatibility riêng, không giấu trong strict parser.
- Coverage total0/không có evidence không được suy complete; chưa triển khai status/coverage tới khi policy invalid/missing/mixed rõ.

## Bước tiếp
G01-01B1-T-I: time_contracts thuần Python + test characterization với TimelineMapper hiện có; chưa sửa mapper/service/Resolve. Một bước độc lập để chốt arithmetic, sau review mới giao consumer integration. Không khẳng định helper mới đã sửa timeline runtime.
