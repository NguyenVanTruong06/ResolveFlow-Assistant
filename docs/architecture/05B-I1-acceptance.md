# G05-05B-I1 — Nghiệm thu backend


## Quyết định G05-05B-I1-R1 — 2026-10-27
CODE_ACCEPTED_WITH_RECORDED_LIMITS cho backend I1. Fresh6files113passed8.47s exit0. Codex chạy lại3probe tmp/QtCore: cả2stage disabled ->nothing_selected/skipped2; hash dependencyOSError ->1terminal/pending0; cancel sau probe ->audio/visualpending0. Đọc code xác nhận per-clip state/deepcopy, terminal catch, offline enforce, duration/cache_saved flags. Hai seam transcriber/PipelineWorker đã nhận trong I1 review tiếp tục giữ.

Chưa có UI wiring/model inference/desktop/3giờbenchmark. Không khẳng định toàn bộ fault/physical/network đã kiểm chứng từ fake dependencies. Temp cleanup best-effort và native blocking cancel chỉ tại boundaries; stat không phải filelock. Worker logical finished_signal được emit trước finally/unload/thread.finished: UI I2 phải giữ ownership và khóa tới QThread.finished, không delete worker hoặc khởi job mới theo logical terminal đơn thuần. Chỉ mở lại backend nếu consumer expose bug có repro; không kéo dài I1 bằng enhancement. I2 kế tiếp chuyển cả Source và Review scan route; model Medium theo user.
