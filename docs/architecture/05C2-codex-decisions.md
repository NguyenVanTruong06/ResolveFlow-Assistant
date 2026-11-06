# G05-05C2-D — Quyết định binding của Codex
Ngày: 2026-10-27
**DESIGN_ACCEPTED_WITH_BINDING_CORRECTIONS**. Không yêu cầu vòngD sửa riêng; đồng bộ tài liệu cùng C2a-I.

## Căn cứ và bằng chứng
Đối chiếu report/design với build_clips_data, StoryCopilot, Review getters/copy, Director run slot, ScanCacheManager verified API và VisualIndex.
Không chạy pytest trong review thiết kế. Probe current formatter inventory rỗng + DI assets_summary='':
travel_vlog 5635chars/6861UTF8bytes; tiktok_short4771/5742; podcast_summary4655/5616.
Đây là tổng baseline prompt khôngassets, KHÔNG phải kích thước riêng skeleton. Không thể cấp ngân sách theo giả định3kchars.

## Binding corrections
1. Bounded mode toànprompt <=32768 UTF-8 bytes, không32000chars/~8Ktokensguarantee. Đo actualskeleton/inventory/warnings trướcallocateevidence; khôngfit thìexpliciterror, khôngtruncate JSON/schema hoặc bỏID âmthầm.
2. Path/model/lang matching không verified audio. Chỉ API get_cached_scan(...verify_full=True,cancelled_fn=...) + validatedv3 cho verified. Validatedv3raw_subtitles=[] hợp lệ dùkhôngstatusfield. Default/candidate khác verified, khôngnamefallback.
3. VisualIndex path-key/fingerprint size+mtime, optional sampledhash; khôngluônchỉhash. Fullhashnguồnhômnay khôngchứngminhcaptionlịch sử. Machine configstale khôngusablefacts; giữID/warning. Originuser giữliteral nhưngkhôngvượt source invalid.
4. Missing/mutated source khôngmấtID/renumber: futureexplicitexport abort trênsource pre/postmismatch, giữclipboard; cacheanalysispartial khácsourceinvalid. Unknown duration0+knownFalse, không60/120s giả.
5. Firstslice chỉ2coremodules clip_manifest.py/story_copilot.py. KhôngReviewPageedits hoặcclaimGUIIOđãhết; worker/UI/Director/clipboard C2b sau.
6. Keyword-only additive options; bounded opt-in trongC2a đểkhông uncaughtbudgeterror ởsyncGUIcallerchưađượcnângcấp. FutureworkerC2b bắtbuộc32768bytes/verifiedaudio khi dùngtranscript.
7. Sau này cancel+deferclose tới QThread.finished, khôngwait2000GUI; V2pending phảigate thật, khôngdemoexportnguồnthật.
8. Sanitize/quote data không bảođảmLLM miễn promptinjection. Khôngconfidence/matching/inference mới.

G05 chưa đóng; C2a-I coreconsumer/formatter trước, C2b wiring sau. NghỉsauG05,khôngshutdown.

---

## Bổ sung G05-05C2a-R1: Quyết định kỹ thuật giải quyết dứt điểm residual probes
Ngày: 2026-10-27

1. **Xác thực Schema v3 Strict trong `build_clips_data` (P1-A):**
   - Khi `verify_audio=True`: Bắt buộc gọi `src.core.cache_manager.validate_v3_payload` với expected_model, expected_lang, và full_content_sha256. Bất kỳ sai khác nào về schema version (khác 3), kiểu dữ liệu (start < 0, end <= start, boolean float coercion) hoặc thiếu payload đều dẫn tới `audio_authority = "unavailable"`, `failed` / `scan_error`, 0 usable transcript; tuyệt đối không fallback sang candidate.
   - Candidate mode (`verify_audio=False`): Phân biệt rõ missing/corrupt raw_subtitles (`scan_error`/`failed`) với `raw_subtitles == []` quét sạch im lặng (`completed_empty`).

2. **Cách ly Stale Visual Machine Observations & Bảo toàn Provenance (P1-B):**
   - Mismatch `visual_context` (model, endpoint, window_sec, frames_per_window, max_windows, max_refinements, enable flags) hoặc thiếu signature evidence dẫn tới loại bỏ 100% quan sát máy khỏi `visual_segments` usable, chuyển sang `quarantined_visual_segments` kèm đếm `visual_stale_excluded_count`.
   - Formatter tự bảo vệ: Loại bỏ mọi machine observation khi clip stale hoặc analysis status stale.
   - Bảo toàn literal ghi chú `origin == "user"` khi source khả dụng.
   - Tách biệt sampling coverage vs analyzed coverage trong cảnh báo.

3. **Bắt buộc Khung xương Tối thiểu chứa Cảnh báo & Omission Notices (P1-C):**
   - Khung xương bắt buộc tối thiểu gồm: Framework + JSON Output Schema + Eligible Clip IDs + Cảnh báo Chất lượng/Nguồn/Stale + Omission Notices.
   - Loại bỏ hoàn toàn cờ `include_details = False` làm mất warnings/omissions.
   - Triển khai phân cấp `asset_level` (2 -> 1 -> 0) để giải phóng dung lượng trước khi ném `ValueError` ngân sách.
   - Giữ nguyên thông báo lược bỏ chính xác trên từng clip và footer bắt buộc toàn cục.

---

## Bổ sung G05-05C2b-I: Thực thi Async Prompt V1, Bảo vệ Clipboard và Single Dispatch
Ngày: 2026-10-27

1. **Chuẩn bị Prompt V1 chạy hoàn toàn trên QThread nền (`PromptPreparationWorker`):**
   - Tách rời 100% I/O nặng (tính SHA-256, nạp cache v3, tạo manifest, build_clips_data) khỏi GUI thread.
   - Bắt buộc kiểm tra tính hợp lệ nguồn preflight và post-stat (`st_mtime_ns`, `st_size`), fail-closed với `source_changed`/`source_missing` nếu tệp biến động.
   - Phân lập ClipManifest theo đúng danh sách tệp yêu cầu của request/project; không tái sử dụng manifest toàn cục có danh sách tệp khác request.
   - Áp trần cứng 32768 UTF-8 bytes khi định dạng prompt qua `StoryCopilot.generate_copilot_prompt`.
   - Nếu không có dữ liệu âm thanh/thị giác khả dụng $\rightarrow$ fail-closed với mã lỗi `no_data`, không bịa đặt timeline hay demo text.

2. **Bảo vệ Clipboard & Phân quyền Single Dispatch:**
   - ReviewPage chỉ ghi clipboard khi `intent == 'copy_video'` và chuẩn bị thành công. Thất bại, hủy, hoặc lỗi ngân sách giữ nguyên 100% MIME clipboard hiện tại.
   - Tuyến `run_ai` không ghi clipboard mà phát tín hiệu `prompt_ready_for_run(request_id, prompt_text)`.
   - DirectorWindow thẩm định cửa sổ chưa đóng, prompt không rỗng và khớp snapshot trước khi phát đúng 1 lệnh duy nhất tới `controller.run_ai_script(prompt_text)`.
   - Getters (`get_video_prompt`, `get_active_prompt`) chỉ trả về in-memory snapshot khớp chữ ký ngữ cảnh, tuyệt đối 0 I/O.
   - Tuyến V2 (Kinetic Prompt) được gated an toàn (trả về chuỗi rỗng `""`, hiển thị tooltip tiếng Việt trung thực, triệt tiêu 100% legacy I/O và `DEMO_WORDS`).
   - Tích hợp đóng cửa sổ an toàn: `closeEvent` trì hoãn cho tới khi toàn bộ QThread kết thúc vật lý.

## Quyết định G05-05C2b-R1 (Full context, final source và exactly-once)
1. **Chữ ký ngữ cảnh bảo toàn thứ tự clip và 16 trường cấu hình:**
   - `ReviewPage._extract_context_signature` bao gồm đầy đủ `ordered_norm_paths` (không sort), `whisper_model`, `whisper_language`, `enable_visual_describe`, `vision_model`, `normalized_endpoint`, `window_sec`, `frames_per_window`, `max_windows`, `max_refinements`, `style_intent`, `hook_duration`, `target_duration`, `project_name`, `max_prompt_bytes`, `manifest_id`.
   - `DirectorWindow._get_current_director_context` đọc trực tiếp runtime attributes từ `page_source` (`_runtime_vision_endpoint`, `_runtime_window_sec`, v.v.).
   - Thẩm định primitives allowlist an toàn, reject fail-closed khi context lỗi hoặc supplier ném exception.
2. **Final source stat check và cách ly manifest:**
   - Kiểm tra tính toàn vẹn của tất cả các file nguồn (kể cả clip phụ) ngay sau khi StoryCopilot định dạng xong và trước khi phát terminal success. Bất kỳ biến động nào về size hay mtime_ns đều lập tức phát `source_changed`.
   - Reconstruct pure `ClipManifest` in-memory nếu request truyền `manifest_metadata` hợp lệ.
   - Tạo cache manifest cách ly theo request_id nếu không chỉ định cache_dir, không dùng chung `ChunDVC_Project.json`.
   - `_apply_json_data` từ chối kịch bản nếu `manifest_id` không khớp với `self._manifest.manifest_id`.
3. **Exactly-once và Busy Control vật lý:**
   - `ReviewPage` chỉ consume logical result của mỗi request đúng 1 lần.
   - `DirectorWindow` quản lý `_pending_run_ai_request_id` và `_dispatched_run_ai_request_ids`, consume ID trước khi gọi controller.
   - Nút copy prompt bị khóa tới khi QThread kết thúc vật lý; Job A kết thúc trễ không mở khóa hay reset timer của Job B.

## Quyết định G05-05C2b-R2 (Busy final-owner restore và finite context validation)
1. **Phục hồi Busy Control theo Aggregate Registry & Bảo toàn Nhãn:**
   - Handler `_on_prompt_worker_thread_finished` tính aggregate busy của toàn bộ registry `_prompt_workers_registry`.
   - Nếu registry vẫn còn worker: giữ nút bấm bị khóa (`setEnabled(False)`).
   - Khi registry hoàn toàn rỗng (all idle): mở lại nút bấm (`setEnabled(True)`) nếu không có `_review_close_pending`.
   - Bảo toàn nhãn active: Old A kết thúc sau cùng không được đè nhãn `"✓ Đã sao chép Prompt V1!"` của Active B. Chỉ khôi phục nhãn gốc `"📋 1. Prompt Dựng Video (V1)"` khi active request không có snapshot thành công.
   - Callback QTimer 2.5s được guard chặt chẽ: `target_req == self._active_prompt_request_id and not self._review_close_pending`.
2. **Xác thực Context Finite & Fail-Closed (Tránh Numeric Exceptions):**
   - Viết helper `_validate_int` và `_validate_float` trên `ReviewPage` chặn hoàn toàn `bool`, `Inf`, `NaN`, non-integer float (không truncate `1.5` thành `1`), và các giá trị ngoài bounds. Bắt `(ValueError, TypeError, OverflowError) -> None`.
   - Bảo toàn chuỗi đường dẫn (preserve path string, reject all-whitespace nhưng không trim path thực tế).
   - Từ chối ép kiểu đối tượng lạ thành tên model; yêu cầu kiểu `str` nguyên thủy.
   - `DirectorWindow._get_current_director_context`: bắt mọi exception khi đọc `page_source` hoặc khi phát hiện malformed numeric values -> trả về `{}` (context unavailable / fail-closed), 0 dispatch.
   - `ReviewPage.get_video_prompt()`: trả về chuỗi rỗng `""` khi context rỗng hoặc chữ ký chứa `None` do trường numeric lỗi.
3. **Fixture Isolation:**
   - Patch `os.path.expanduser` trong test scope về `tmp_path / "fake_home"`, không chạm vào cache người dùng `~/.resolveflow_cache`.

