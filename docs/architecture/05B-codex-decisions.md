# G05-05B-D — Codex decisions (2026-10-27)

DESIGN ACCEPTED_WITH_BINDING_CORRECTIONS. Chọn source-only worker; chưa nối UI trong I1 vì phải đóng cache compatibility và offline model loading trước. Không yêu cầu design tổng thể lại.

## Source-grounded corrections
- director_window._on_start_rough_scan thực sự nối cả SourcePage và ReviewPage tới start_pipeline(phase=1). Phase1 có nhánh tự phase2/ensure_resolve_running khi không có cut proposals. Khi nối UI ở I2 phải chuyển cả hai nút nguồn/review về source-only; không để review nút còn gọi pipeline cũ. Không sửa build timeline explicit route.
- SourcePage.btn_scan đang emit rough_scan_requested nhưng còn bị _refresh_clip_list thay nhãn/enable theo audio-only has_cache. I2 phải đóng seam này và giữ active-job lock, không chỉ sửa label constructor.
- Phantom APIs trong design: ScanCacheManager.save_scan_cache_v3 và AudioExtractor.detect_silence_intervals không tồn tại. Dùng save_scan_result và detector thật nếu cần; worker không gọi VisualIndex._write private. VisualIndex.scan tự cache-hit có kiểm config/files và cleanup; gọi public API này, không chỉ load()+frames exist.
- Audio cache directory thật là ScanCacheManager.cache_dir hiện hữu (~/.resolveflow_cache mặc định), không có /audio mặc định. Không di chuyển cache roots trong task.
- ResolveTranscriber.load_model hiện gọi WhisperModel(model_size,...) có thể tải model; is_model_on_disk chỉ check sơ bộ, không đủ guarantee offline. Cần additive local_files_only flag mặc địnhFalse cho callers cũ, propagate đúng vào cả initial và CPU fallback constructor; worker mới luônTrue, không env global.
- PipelineWorker khi cached_data truthy lấy silence_keep_intervals default fullclip rồi chỉ chạy detector khi not cached_data. Cache STT-only từ worker mới nếu public ở cùng namespace sẽ khiến AutoCut tưởng đã có analysis silence. I1 phải tách “có transcript verified” khỏi “có dữ liệu cut”: thiếu silence artifact phải tính cut từ transcript/audio theo settings hiện hành, KHÔNG chạy Whisper lại và KHÔNG chế fullclip keep giả. Không bịa visual_activity/energy/silence fields để đủ payload.

## Quyết định slice
I1 đúng3modules: worker mới + transcriber.py (offline-only additive) + PipelineWorker(auto_window.py, STT-only compatibility). UI source_page/director_window giữ nguyên cho I2, chưa claim nút đã an toàn. I2 ưu tiên chuyển cảSource/Review scan route ngay sau backend accepted; không để residual route bị quên. Ollama opt-in description I3; worker I1 chỉ audio+overview sampling, không hiểu nội dung ảnh.

Giữ SourceAnalysisWorker tại src/ui/director/workers/source_analysis_worker.py. Mọi source-only test phải không runtime-import PipelineWorker/ResolveAutomation/Director builders, không thư mục _TIMELINE_IMPORT hoặc source writes. DI factories cho model/cache/visual thuận tiện test là được, không plugin abstraction cồng kềnh. Temp dùng mkdtemp riêng job, finally cleanup chỉ session đó. Chỉ check cancel ở native blocking API boundaries nếu API không hỗ trợ intra-call; bỏ SLA<0.5s và bounded GUI wait. Kế thừa SourcePage shutdown_workers/registry/idle pattern ở I2, không destroy QThread còn chạy.

Counters phân hoạch riêng: cached+new+no_audio_track+failed+unavailable+cancelled+pending=total cho audio; no_speech là thuộc tính subset của completed audio (có thểcached hoặcnew), không cộng thêm để vượt total. Partial error không finalsuccess toànjob; visual vẫn được chạy khi audio failed/unavailable nếu nguồn còn stable. [] là completed-empty, không khẳng định B-roll. total0 reject. Không giả thời gian0ms hoặc100%cache/modelRAM.
