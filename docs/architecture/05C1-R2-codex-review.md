# G05-05C1-R2 — Kết quả review Codex, dừng chờ người dùng
Ngày: 2026-10-27
Trạng thái: **REVIEWED_NEEDS_FIX_WAIT_USER_CONTINUE**
Không nghiệm thu C1 toàn phần. Không có workorder mới; không thực thi sửa code.

## Kiểm tra đã chạy
Codex đọc .bridge/report.md, hai modules R2, regression mới và pause-after-report.md. Fresh command:
```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_review_page_visual_consumption.py tests/test_review_state.py tests/ui/test_director.py tests/ui/test_source_analysis_routing.py tests/ui/test_source_analysis_visual_describe.py -q
```
**63 passed in 5.78s, exit 0.** Không full suite, không Resolve/VLM/desktop acceptance.
Probes bổ sung dùng tmpdir, explicit DI và QSettings namespace tạm; không sửa production/tests.

## Các thay đổi đã đạt trong phạm vi review
- Post-stat không bỏ qua khi file biến mất; nguồn đổi/mất thì rows/observations được xóa, receiver bỏ invalid-source payload.
- Null/string/dict subtitles không tự là completed-empty; [] chỉ silent theo completed evidence trong cache branch.
- Provider exception từng clip không làm mất clip sau; outer terminal fail-safe giữ nguyên.
- Request giữ runtime nested copy, clip path/duration primitives; max_refinements=0 được giữ.
- Close-pending/generation chặn callback, cleanup pending và owner theo physical finished; good partial payload được giữ theo tests.
Đây là nhận các sửa cụ thể, không xác nhận toàn bộ các claim “triệt để” hoặc execution authority.

## Điểm còn sót đã tái hiện
### 1. P1 — Validate snapshot sau khi đã xóa UI
ReviewPage.load_analysis_data chỉ validate outer clip_infos. Sau đó đổi inventory/generation và set_review_rows([]), rồi mới float/int snapshot và deepcopy runtime.
Probe với UI có VALID OLD ROW và snapshot.max_windows='bad':
```text
INVALID_CONFIG ValueError rows_after=[] generation_changed=True
```
Input không hợp lệ gây exception và mất state UI trước khi reject, trái gate R2 “validate trước mutate”. Test hiện chỉ truyền clip_infos=None nên chưa đi qua cấu hình sai. Chưa sửa trong review này.

### 2. P2 — Terminal/counters nguồn không hợp lệ vẫn completed
Pre-stat missing branch tăng loaded nhưng không cập nhật failure/invalid count; source_changed/source_missing sau read tương tự. Final chỉ xét failed_clips từ exception branch.
Probe request chỉ có một nguồn không tồn tại:
```text
success=True, message='completed'
total_clips=1, loaded_clips=1
speech_status_counts={'source_missing':1}
visual_status_counts={'source_missing':1}
```
UI có thể hiển thị “Không có quan sát” thay vì thông báo nguồn không khả dụng; không còn stale data nhưng summary chưa trung thực về kết quả có thể dùng. Inventory loaded khác analysis usable; không được trộn hai khái niệm.

## Ranh giới GUI I/O còn chưa đạt (đối chiếu code)
ReviewPage.load_analysis_data gọi sort_clips_chronologically trực tiếp trên GUI trước khi tạo worker. Chronological sorter có thể gọi get_clip_shot_time -> subprocess.run(ffprobe, timeout=5) cho file thật không có timestamp tên.
Đây là đường gọi đọc metadata trên GUI, chưa đạt yêu cầu loader nền. Chỉ xác nhận callgraph, chưa đo benchmark hoặc chạy nguồn lớn. Không tuyên bố có freeze đã đo. Cần giữ trong review lúc tiếp tục, không mở task tự động.

## Giới hạn giữ nguyên
- Candidate stat metadata khác verified execution; core source runtime chưa có historical fingerprint; không bịa full source authority.
- Prompt/export C2 và Whisper real/integration acceptance chưa hoàn tất. Không đóng G05 hoặc ALL_DONE.
- Không sửa code trong lượt review. Không mở C1-R3/C2/G06 hoặc giao việc khác.
- Theo .bridge/pause-after-report.md: **dừng tại đây, chỉ tiếp tục khi người dùng nhắn rõ “tiếp tục”; không shutdown**.

