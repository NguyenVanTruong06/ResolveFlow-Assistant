# G04-04C6-AUTO-UI — Codex evidence review (2026-10-27)

COMPONENT_DESKTOP_PARTIAL, không mở lại code accepted188tests. Đã đọc harness/log và xem trực tiếp case1_preview_positive_playing.png, case3_managed_mismatch_denied.png. Component harness không phải main.py E2E; hover gọi private handlers trực tiếp, drag fake exec; không native drop/physical inputdriver proof. Log positive assertions cho URI/PlayingState/overlay, filter stop và replay, identity/staging, MIME dispatch và deny được nhận trong đúng phạm vi đó.

Residual evidence: ảnh case1 testsrc màu lại đen hoàn toàn trong overlay. Harness chụp ngay khi PlayingState, không đợi video frame/sink/image hoặc position advance. Đây là hình ảnh chưa xác minh, chưa kết luận production playback lỗi. Cần một target rerun positive frame/stop/replay, không cả checklist.

Harness safety: SESSION_DIR cố định /tmp/chundvc_smoke_04c6 và rmtree nếu tồn tại ở import; không chứng minh ownership để xóa directory từ phiên khác. Script không có cleanup finally, log chỉ subset evidence_logs chứ không toàn stdout/exit; report dọn sạch là Worker claim chưa được chính harness bảo đảm. Không rerun nguyên harness cũ. Dùng TemporaryDirectory/mkdtemp và main/finally rõ, log riêng G04-04C6-AUTO-EVIDENCE-R1 để không trộn thư mục G04-04C-AUTO-UI cũ. Không xóa evidence cũ.

Kết thúc gate khi testsrc frame có dữ liệu và render rõ trên desktop, source đúng, stop/replay assertions kèm ảnh/log. Nếu frame được decode nhưng overlay vẫn đen, ghi FAIL/BLOCKED đúng mức và repro, không sửa production trong task evidence.

## Nghiệm thu evidence R1 — 2026-10-27
ACCEPTED / COMPONENT_DESKTOP_VERIFIED trong scope đã giao. Codex đọc frame_verification.log và harness mới, xem trực tiếp ảnh case1_preview_positive_frame_playing.png: test pattern màu thật hiện trong card. Log ghi source verified, position835ms/20frames, sink640x360 có màu; filterstop/overlayhide và replay15frames; finally owned session cleanup SUCCESS, FINAL_PROCESS_EXIT0. Không cần rerun desktop/suite.

Đóng gate 04C6 grid drag + shared preview trong phạm vi component desktop. Các ca native guidance, authority deny/fallback và drag dispatch trước được nhận theo assertions, không nâng thành physical-input/main.py E2E/native OS drop vào Resolve hoặc macro installation. Không sửa production vì ảnh đen trước là evidence chụp trước frame render, chưa phải lỗi player đã chứng minh. Không dùng nhãn 100% data integrity; nhận isolation design và cleanup log trong scope session.

Residuals chuyển 04C7-D: Studio Inspector/legacy preview/drag và Text/Fusion customization. Hardware/audio physical output, main.py smoke/native external drop giữ G08/G09; roadmap chưa ALL_DONE, không shutdown.
