# G07-07A2b — Binding implementation decisions

Ngày2026-11-02. Backend07A2a accepted trong limits tại07A2a-gate-acceptance.md. Triển khai ngay, không cần vòng design-only nữa.

## Phạm vi và API

Đúng3production files: src/ui/director/director_window.py (coordinator), src/ui/director/pages/build_page.py (readonly metadata feedback), src/services/source_shutdown.py (app Quit retention). Core readiness/PreflightWorker/Controller/ReviewPage đã đạt giữ frozen. Không timeline execution, không models/network.

- Click explicit Step4 primary trở thành `Kiểm tra nguồn & tài nguyên`; vào Step4 không tự scan. `_start_build_timeline` giữ tên nhưng chỉ gọi readiness handler, không dispatch phase0/2. No-auto-build sau availability pass.
- BuildPage cung cấp `set_readiness_busy(busy)`, `show_readiness_result(result)`, `clear_readiness_result(message='')`, signal `readiness_cancel_requested`; bảng/labels readonly, max200 displayed rows và count X/total minh bạch. Không preview/thumbnail/file lookup tại GUI. Bỏ nhãn sẵn sàng dựng giả trong phạm vi trang4; source-only/no-library vẫn dùng được.
- DirectorWindow thêm `_start_readiness_preflight`, `_on_readiness_terminal`, registry giữ worker đến native finished và signal `analysis_and_preflight_idle`; public `shutdown_analysis_and_preflight_workers()->bool`, `is_analysis_and_preflight_idle()->bool` gộp3families SourcePage, ReviewPage analysis/prompt/kinetic/matching, và preflight. Không tuyên bố protocol bao phủ mọi legacy/controller job ngoài3families này.

## Binding/lifecycle

Start: sync UI config in-memory hiện hành → getter approved pair → normalize ReadinessRequest trước thay generation/UI → constructor PreflightWorker0IO → đăng ký strong reference và callbacks closure exact worker/request → start. Callback terminal bind request_id,generation,proposal_id,revision,approval_id và fresh approved pair. Getter lỗi/stale/reapproval khác ID không cập nhật success. Không reapprove/restore UI từ request.

Logical terminal cập nhật readonly result, không release physical lock. Chỉ native finished pop exact worker, deleteLater và phục hồi aggregate controls; source/pipeline callbacks không được unlock khi preflight còn sống. Start failure nếu thread đã chạy vẫn retain/cancel; nếu query lỗi thì giữ như busy, không drop. Registry membership là thẩm quyền physical ownership; không chỉ isRunning để tin terminal đã xong.

Các nút/stepper/drop đầu vào cần khóa nhất quán khi preflight chạy, riêng Cancel usable. Thay đổi inputs qua signal/programmatic invalidation làm stale/cancel; terminal luôn hỏi getter hiện hành trước publish. Sau kết quả thành công chỉ ghi nguồn/catalog hiện có, còn thiếu content/frame/execution; không green Ready/Build tự bật.

Close và Quit phải hoạt động cả sau logical terminal nhưng trước physical finish. Director close pending cancel/source+review+preflight rồi ignore event cho tới aggregate idle, retry close bằng timer; không terminate/wait deadline/drop. Global SourceShutdownGuard giữ DirectorWindow strong ref và nghe aggregate signal, bên cạnh non-Director SourcePage và LibraryJob hiện có; discover/connect toàn bộ owners trước yêu cầu cancellation, tránh synchronous idle signal làm quit sớm. Không gọi app.quit tới khi tất cả owners/jobs đã physical idle, kể cả window ẩn. Kết nối1lần, repeated Quit idempotent, không duplicate signals/quit.

## Tương thích và xác minh

Sửa assertions của tests07A1 tạm thời kỳ vọng CONTROLLED_UNAVAILABLE/nhãn Build thành readonly preflight behavior; vẫn giữ approval denials và spies0legacydispatch/0Resolve. Bổ sung12case tests UI/lifecycle theo workorder, không đụng code core đã accepted. Không mở rộng shutdown mọi job toàn app hoặc refactor cơ sở hạ tầng; recording remaining legacy limits trung thực. Desktop smoke sau code review là slice tiếp, chưa claim desktop từ offscreen.
