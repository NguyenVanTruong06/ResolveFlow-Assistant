# G05-05B-I2-AUTO-UI — Codex review (2026-10-27)

ACCEPTED_WITH_RECORDED_LIMITS cho hành vi component có bằng chứng, chưa blanket desktop100%PASS. Codex đọc smoke_metrics.json, log, harness và xem ảnh case1_source_analysis_modal.png; không chạy lại app/pytest/model trong phiên review.

Được nhận: real Director/Source QTest và QThread, real sampling hai fixture; A SEEDED_FIXTURE_CACHE chứ không inference; B actual no_audio_track. Director instance mới cùng cache, Review scan route về Source, replay decoder_calls=0/transcriber_calls=0 theo spy/guard. B vẫn cần stream probe: không gọi cả hai file là audio-cachehit hoặc0IO. Cancel ở CONTROLLED_BOUNDARY đã dừng worker và registry trống sau release.

Các giới hạn cần đính chính:
- Ảnh mang tên modal dùng window1.grab nên chỉ chụp list, không có QMessageBox top-level. Log xác nhận text; visual proof modal cần targeted capture dialog thật.
- “FINALLY CLEANUP” chỉ là comment ở normal path. Nhiều return1 trước cleanup trong khi session được cấp phát lúc import; cần actual try/finally và một fault injection chứng minh early-failure cleanup.
- Trước release controlled gate chưa có logical terminal: bằng chứng này là cancel-active-lock, không logical-terminal-before-physical. Nhánh logical terminal đã có offscreen test. Không có desktop close-pending thực vì close được gọi sau worker dừng.
- Widget grab là WIDGET_RENDER, không root-screen screenshot. Không suy diễn physical screen visibility/native cancel latency/main.py E2E từ đó.

Residual harness/evidence hữu hạn giao Medium; không thêm production feature. I3 local-vision design kế tiếp cần High theo model policy, dừng trước Phần B cho đến user xác nhận đã đổi. Không shutdown. Gate G05 chưa đóng.

## Review Evidence R1 — 2026-10-27
CHANGES_REQUIRED chỉ harness/evidence. Đã đọc log/metrics/source và xem cả hai ảnh:
- case1_modal_dialog_screen_capture.png chụp vùng list/buttons, không có dialog: SCREEN_CAPTURE claim chưa đạt.
- case1_modal_dialog_widget_render.png có QMessageBox nhưng chữ trắng trên nền gần trắng rất khó đọc; log text đúng2clip nhưng ảnh chưa readable. Không sửa stylesheet production trong evidence task; nếu palette issue là production thì ghi residual riêng, harness capture không fake text.
- Fault probe dùng DummyControlledWorker và có fallback worker.terminate() trái ràng buộc. Probe không kiểm chứng chính cleanup đường run_targeted_case1_evidence: finally thực của targeted chỉ window.close()+pump0.2s rồi rmtree session, không đợi registry/QThreadfinished khi exception xảy ra lúc realworker đang chạy. Không được xóa media/temp trong khi worker còn dùng.
- Có actualfinally normalerror đã tiến bộ và labels đã sửa đúng. Không cần rerun toàn bộ3cases hoặc sửa src; cần bounded shared cleanup/failure probe cùng đường harness.
I3-D vẫn WAIT_MODEL_HIGH; user chưa xác nhận đổi, không tự bắt đầu.
