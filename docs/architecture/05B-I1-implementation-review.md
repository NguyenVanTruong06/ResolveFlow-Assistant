# G05-05B-I1 — Codex implementation review (2026-10-27)

CHANGES_REQUIRED, phạm vi worker mới. Fresh finite6files:96passed9.36s exit0. Nhận additive local_files_only trong cả initial/CPU fallback constructors và seam STT-only không bỏ detector ở PipelineWorker theo test/code. Không yêu cầu sửa lại hai module đó khi chưa có regression mới.

## Các probe thật trên tmp + QtCore (không model/Resolve)
- Config enable_audio=False, enable_visual_sampling=False cho2files -> finished counters audio.cached=2, visual.cached=2. Không có analysis mà báo cachehit là false evidence. Phải skipped, không giả cached.
- Inject hash_fn ném OSError -> run() ném ra caller, terminal count0. mkdtemp còn nằm ngoài try. Khi QThread chạy thật UI có thể kẹt vì không có terminal. Bọc toàn job với terminal/finally ownership đúng; unexpected failure phải phát exactly1failed terminal.
- Hai clip, audio probe clip1 gọi cancel() rồi trảFalse -> terminalcancel nhưng audio.pending=1. Các vòng decrement riêng lẻ không phân hoạch được mọi boundary. Terminal phải không để pending ở stage được chọn; bảo toàn completed/cached và chỉ cancel pending stages.

## Các ranh giới cùng worker cần đóng
- config.local_files_only có thểFalse và worker truyền thẳng xuống loader, trái policy worker luôn offline. Không cho source-only bật download dù caller config sai; enforceTrue hoặc rejectFalse trước heavy work.
- Duration exception hiện biến clip_dur=0 nhưng vẫn đánh dấu audio.new và finalsuccess nếu visual xong. Duration không finite positive phải audiofailed không publish; save failure cần output persisted=False/warning rõ, không chỉ log rồi hứa cache đã có.
- counters/status signal đang shallow-copy nesteddict hoặc truyền mutable dict dùng tiếp. UI queued consumer phải nhận immutable snapshot theo event (deepcopy cấu trúc primitive), không quan sát trạng thái cuối cho mọi event.
- Mọi cancellation sau probe/load/scan phải được kiểm trước stage kế tiếp và final verdict; terminal stats cân bằng, no later callbacks ghi đè. Blocking API không có cancel thì ghi rõ chưa có latency guarantee; không QThread.terminate.

Giữ source-only/owned temp/unload. Không sửa UI/Visual/cache schema/roots. Không claim noRegression toàn repo từ96focusedtests. Worker level theo user hiện Medium, không phải yêu cầu HIGH cũ; không cần đổi cho residual cụ thể này.


## Quyết định G05-05B-I1-R1 — 2026-10-27
CODE_ACCEPTED_WITH_RECORDED_LIMITS cho backend I1. Fresh6files113passed8.47s exit0. Codex chạy lại3probe tmp/QtCore: cả2stage disabled ->nothing_selected/skipped2; hash dependencyOSError ->1terminal/pending0; cancel sau probe ->audio/visualpending0. Đọc code xác nhận per-clip state/deepcopy, terminal catch, offline enforce, duration/cache_saved flags. Hai seam transcriber/PipelineWorker đã nhận trong I1 review tiếp tục giữ.

Chưa có UI wiring/model inference/desktop/3giờbenchmark. Không khẳng định toàn bộ fault/physical/network đã kiểm chứng từ fake dependencies. Temp cleanup best-effort và native blocking cancel chỉ tại boundaries; stat không phải filelock. Worker logical finished_signal được emit trước finally/unload/thread.finished: UI I2 phải giữ ownership và khóa tới QThread.finished, không delete worker hoặc khởi job mới theo logical terminal đơn thuần. Chỉ mở lại backend nếu consumer expose bug có repro; không kéo dài I1 bằng enhancement. I2 kế tiếp chuyển cả Source và Review scan route; model Medium theo user.
