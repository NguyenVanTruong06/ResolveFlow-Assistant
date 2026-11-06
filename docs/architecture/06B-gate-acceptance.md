# G06-06B — ACCEPTED_WITH_RECORDED_LIMITS

Ngày 2026-10-29. Phạm vi: matching offline advisory, strict storage reader/worker, Review drawer và layout integration. Đây không phải nghiệm thu toàn G06 hoặc đường dựng Resolve mới.

## Bằng chứng kiểm tra

- Báo cáo cuối G06-06B-UI-CLIP-R1 có SHA256 `e037860b800843f2664acb641bad56d35aa4c55f58db00c882326ffb05a234a6`, Codex đã đối chiếu đúng.
- Codex đọc đúng hai UI modules, tests mới, harness/metrics và đính chính thiết kế. Fresh focused command: `QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_review_advisory_layout.py tests/ui/test_review_page_advisory_wiring.py -q` → **41 passed in 6.41s, exit 0**.
- Đã xem cả hai SCREEN_CAPTURE happy1200x750/1280x800 và ảnh lỗi. Hai hàng B-roll/SFX hiện thật, không còn mất hàng SFX do parent clipping dọc. Scroll container giữ Web page ở index0; Cloud/Ollama không bị đổi index. Drawer co/mở và last-good được giữ trong tests/evidence nêu trên.
- Kế thừa các cổng pure matcher/adapter/service, strict assets, worker authority/cancel, pending binding và physical ownership đã review. Evidence273tests/54.48s từ code gate trước và desktop6cases/fault probe là evidence lịch sử, không cộng vào41 như tổng tests độc lập và không nói Codex vừa chạy lại chúng.

## Giới hạn và hiệu đính báo cáo

- Không nhận claim mọi thứ "unclipped100%": metrics case lỗi ghi `banner_geometry.unclipped=false` (503/1327px ngang còn trong viewport), trái câu báo cáo nói true. Tests lỗi chỉ assert visible height>0. Desktop ảnh happy ở hai kích thước vẫn cần cuộn ngang để xem các cột bên phải/điểm và một số control; đây là UI có scroll, không phải mọi cột cùng lúc trên màn hình.
- Chấp thuận fix mất hàng theo chiều dọc và chức năng advisory trong giới hạn; ghi usability residual về wrap/compact summary/error details vào shell UX G08. Không mở thêm vòng polish trong G06 chỉ vì text dài; trước release cần xác nhận luồng cuộn/đọc nội dung dài.
- Fixture `source_kind='cloud_download'` trong layout tests chỉ là DTO display giả, không chứng minh domain source contract mới. Core source kinds vẫn managed/linked/builtin/generated.
- Dummy header MP4/WAV và GUI manifest fixture chỉ phục vụ metadata/layout; không chứng minh decode, source validation, chất lượng semantic hoặc real Resolve execution. Điểm NN/100 là lexical score, không xác suất phù hợp.
- UI chỉ đọc; chưa có assignment/proposal approval/executable handoff mới. Không auto-pick, clipboard write, tải internet, AI inference hay mutation timeline từ matching.
- Strict storage context là observed snapshot, không transaction/hotplug guarantee đến lúc thi công. G07 preflight/action checks vẫn cần độc lập.

## Điểm tiếp tục

06B đóng trong phạm vi và giới hạn trên. 06C thiết kế đã cải thiện nhưng chưa cấp implementation/schema mới: các quyết định user/approval và quantization còn cần chốt. D01 được user duyệt: AI đề xuất trọn bản dựng và tài nguyên, user duyệt/sửa một lượt trước khi dựng. D02 thiếu B-roll/SFX và D03 brief/reference đang trao đổi theo `06C-product-decisions.md`.

Worker chờ chỉ đạo mới, không tự mở06C/G07, không chạy thêm app/test/model, không shutdown. G06 tổng thể chưa hoàn tất.
