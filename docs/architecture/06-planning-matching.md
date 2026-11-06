# G06-D — Thiết Kế Kiến Trúc: Bounded Batch Planning, Matching và Edit-Plan

- **TASK_ID:** `G06-D`
- **Trạng thái:** `ACCEPTED_DIRECTION_WITH_BINDING_CORRECTIONS` (Theo `docs/architecture/06-codex-decisions.md`)
- **Lát cắt triển khai đầu tiên:** `G06-06A1-I` (Đã hoàn thành tại `src/core/batch_planner.py`)
- **Cấp độ Worker:** `HIGH`
- **Tài liệu tham chiếu:** `docs/architecture/ROADMAP.md`, `docs/architecture/06-codex-scope.md`, `docs/architecture/06-codex-decisions.md`, `docs/architecture/05-gate-acceptance.md`

---

## 1. KHẢO SÁT CALLGRAPH THỰC TẾ & RANH GIỚI KẾ THỪA

### 1.1. Bảng Khảo Sát Callgraph (file : hàm : dòng)

| Nhóm chức năng | File : Hàm / Lớp : Dòng | Nhãn kiểm chứng | Hiện trạng mã nguồn & Đánh giá rủi ro / Ranh giới kế thừa |
|---|---|---|---|
| **Clip Manifest Authority** | `src/core/clip_manifest.py:ClipManifest:93`<br>`src/core/clip_manifest.py:ClipEntry:61`<br>`src/core/clip_manifest.py:compute_content_hash:21`<br>`src/core/clip_manifest.py:compute_manifest_id:52` | `[CODE_VERIFIED]` | • `ClipEntry`: `clip_id` ("C01", "C02"...), `content_hash` (sha256 size + 1MB đầu + 1MB cuối), `duration_sec`, `fps`, `start_tc_frames`, `shot_time`, `current_path`.<br>• Lưu ý phân định: `ClipEntry.content_hash` là **sampled hash**, hoàn toàn tách biệt với `full_content_sha256` của G05 verified cache.<br>• `ClipManifest`: Quản lý danh mục clip dự án, sắp xếp thứ tự thời gian thực tế (`sort_clips_chronologically`), sinh `manifest_id` (8 ký tự hash).<br>• **Kế thừa:** Toàn bộ batch planner PHẢI giữ nguyên `manifest_id` và `clip_id` (C01, C02...), tuyệt đối không tự tạo `clip_uid` hoặc renumber lại từ C01 trong từng batch. |
| **Source Analysis & Evidence** | `src/ui/director/workers/source_analysis_worker.py:SourceAnalysisWorker:102`<br>`src/ui/director/workers/source_analysis_worker.py:SourceAnalysisWorker.run:166`<br>`src/ui/director/workers/source_analysis_worker.py:_default_probe_has_audio:54`<br>`src/ui/director/workers/source_analysis_worker.py:_default_get_duration:73` | `[CODE_VERIFIED]` | • Phân tích ngoại tuyến đa tầng độc lập trong `run()`: Audio STT (Whisper v3 cache) $\to$ Visual Sampling (`VisualIndex`) $\to$ Visual Describe (Local VLM).<br>• Tự động xuất bản negative cache v3 cho clip không audio (`raw_subtitles=[]`, `no_audio_track=True`).<br>• Đo thời lượng container chuẩn qua `_default_get_duration`.<br>• **Kế thừa:** Bằng chứng đầu vào (Evidence) bao gồm: ScanCacheManager v3, VisualIndex, user notes và consumer snapshots từ G05. Planner nhận in-memory mapping từ caller, không quét lại nguồn hay re-hash trên đĩa. |
| **Review Loader Worker** | `src/ui/director/workers/review_analysis_worker.py:ReviewAnalysisWorker:89`<br>`src/ui/director/workers/review_analysis_worker.py:ReviewAnalysisRequest:70`<br>`src/ui/director/workers/review_analysis_worker.py:_normalize_duration:28` | `[CODE_VERIFIED]` | • Đọc ngoại tuyến không chặn GUI từ `ScanCacheManager` (Schema v3) và `VisualIndex`.<br>• Bảo toàn provenance: `transcribed`, `completed_empty`, `no_audio_track`, `unscanned`, `failed`, `source_missing`.<br>• Chuẩn hóa an toàn `duration_sec` và `timestamp_sec` (hữu hạn, loại trừ bool/NaN/Inf).<br>• **Kế thừa:** Cung cấp mô hình dữ liệu Review Table cho ReviewPage. |
| **Prompt V1 Worker** | `src/ui/director/workers/prompt_preparation_worker.py:PromptPreparationWorker:68`<br>`src/ui/director/workers/prompt_preparation_worker.py:PromptPreparationRequest:45` | `[CODE_VERIFIED]` | • Chuẩn bị Prompt kịch bản V1 bất đồng bộ trên luồng nền.<br>• Giới hạn ngân sách nghiêm ngặt: UTF-8 32,768 bytes ceiling (`max_prompt_bytes=32768`).<br>• Kiểm tra tệp nguồn pre-stat & post-stat: fail-closed nếu nguồn biến đổi/mất.<br>• **Kế thừa:** G06 phân chia batch tương thích với giới hạn 32,768 bytes UTF-8 đo từ chuỗi kết xuất thực tế. |
| **Kinetic V2 Worker** | `src/ui/director/workers/kinetic_preparation_worker.py:KineticPreparationWorker:115`<br>`src/ui/director/workers/kinetic_preparation_worker.py:KineticPreparationRequest:73`<br>`src/core/kinetic/kinetic_dataset_mapper.py:map_words_to_timeline:36` | `[CODE_VERIFIED]` | • Ánh xạ từ vựng chính xác qua `RationalFPS`, word timestamps thật.<br>• AutoPlan offline không tiêu tốn token.<br>• Tách biệt tín hiệu kết thúc logic (`finished_signal`) và vòng đời vật lý (`QThread.finished`).<br>• **Kế thừa:** Hỗ trợ tương thích cho các kế hoạch kết hợp Video Plan (V1) và Kinetic Plan (V2). |
| **Review Page UI** | `src/ui/director/pages/review_page.py:ReviewPage:356`<br>`src/ui/director/pages/review_page.py:load_analysis_data:1285`<br>`src/ui/director/pages/review_page.py:request_prompt_preparation:2354`<br>`src/ui/director/pages/review_page.py:request_kinetic_preparation:2613` | `[CODE_VERIFIED]` | • Quản lý các workers bất đồng bộ thông qua `generation` token.<br>• Chốt chặn fail-closed: Hủy worker cũ khi đổi context/plan signature (`_extract_v2_context_signature`).<br>• **Kế thừa:** Batch planning cấp dữ liệu khi dự án có nhiều clip hoặc thời lượng dài vượt ngân sách một prompt đơn lẻ. |
| **Queue Module Hiện Hành** | `src/core/queue.py:BatchJob:4`<br>`src/core/queue.py:BatchProcessorQueue:25` | `[CODE_VERIFIED]` | • Chỉ chứa 58 dòng code sơ khai: `BatchJob` (chỉ có `video_path`, `status`, `progress`, `error_msg`) và `BatchProcessorQueue` (chỉ có mảng `self.jobs`, `add_video`, `clear`, `get_pending_jobs`, `get_progress`).<br>• **0 Consumer trong `src/`** (chỉ có duy nhất test `tests/test_queue.py`).<br>• Hoàn toàn KHÔNG có scheduler, cancellation, persistence/checkpoint, resume boundary.<br>• **Đánh giá:** Không thể tái sử dụng trực tiếp; thay thế bằng `BatchPlanner` chuyên biệt. |
| **Story Pipeline & Chapters** | `src/core/story_pipeline.py:MasterStoryPipeline:41`<br>`src/core/story_pipeline.py:ChapterResult:16`<br>`src/core/story_pipeline.py:process_chapter_group:47`<br>`src/core/story_pipeline.py:assemble_master_timeline:169` | `[CODE_VERIFIED]` | • Xử lý nhóm video theo thư mục (`FolderGroup` từ `folder_scanner.py`), trích xuất highlight qua `VlogHookGenerator`, ghép nối tuyến tính danh sách events.<br>• **0 Consumer trong `src/`** (chỉ có duy nhất test `tests/test_story_pipeline.py`).<br>• Khuyết thiếu hoàn toàn: Tóm tắt phân cấp, ngân sách byte/token, coverage/omission tracking, và không nhận thức được `ClipManifest`/`ClipEntry`.<br>• **Đánh giá:** Khái niệm "Chapter" ở đây chỉ là thư mục con trên đĩa, không phải phân đoạn ngữ nghĩa phân cấp. |
| **Story Copilot & Timeline Conversion** | `src/core/story_copilot.py:CopilotDirectorPlan:51`<br>`src/core/story_copilot.py:CopilotMediaInsert:40`<br>`src/core/story_copilot.py:CopilotSegmentPlan:26`<br>`src/core/story_copilot.py:generate_copilot_prompt:215`<br>`src/core/story_copilot.py:parse_copilot_response:714`<br>`src/core/story_copilot.py:convert_plan_to_resolve_timeline:1138` | `[CODE_VERIFIED]` | • Schema V1 hiện tại: `CopilotDirectorPlan` chứa `manifest_id`, `global_hook`, `timeline_segments`, `broll_inserts`, `sfx_inserts`.<br>• **Lỗ hổng ranh giới được phát hiện:**<br>  1. `get_available_assets_summary` (:139-143): Hardcode quét thư mục tương đối `../../assets` trong source repo, bỏ qua `StorageManager`.<br>  2. `CopilotMediaInsert` (:45): Dùng tên file thô `asset_file: str` thay vì canonical `asset_id`.<br>  3. `convert_plan_to_resolve_timeline` (:1158, :1318-1331, :1363-1376): Sử dụng singleton `GlobalAssetPool.get_instance()` và `os.walk` tìm file theo tên thô, bypass toàn bộ `action_guard.py` và catalog của `StorageManager`. |
| **Web Prompt Parsing** | `src/ui/director/director_state.py:parse_web_prompt_json:242`<br>`src/ui/director/director_state.py:DirectorConfig:58` | `[CODE_VERIFIED]` | • Phân tích JSON dán từ ChatGPT/Claude: nhận diện độc lập Video Plan (V1), Kinetic Plan (V2), hoặc Dual Plan.<br>• **Kế thừa:** Hỗ trợ nhận diện Edit-Plan phân cấp theo từng Batch hoặc kế hoạch tổng hợp đã ghép nối. |
| **Recipe Manager** | `src/core/recipe_manager.py:Recipe:16`<br>`src/core/recipe_manager.py:RecipeManager:78` | `[CODE_VERIFIED]` | • Quản lý cấu hình workflow của người dùng (tùy chọn Whisper model, silence_cut, text_preset_id, pacing...).<br>• **Đánh giá:** `Recipe` là Preset cấu hình quy trình, hoàn toàn KHÔNG phải là Edit-Plan JSON (không chứa mốc thời gian, frame cắt ghép, hay media insert cụ thể). |
| **StorageManager & Canonical Catalog** | `src/core/storage_manager.py:StorageManager:68`<br>`src/core/storage_manager.py:assets:371`<br>`src/core/storage_manager.py:taxonomy:357`<br>`src/core/contracts/asset_dto.py:PersistedAssetRecord:91`<br>`src/core/contracts/asset_dto.py:PERSISTED_ALLOWLIST:67` | `[CODE_VERIFIED]` | • Thẩm quyền kho tài nguyên duy nhất: Quản lý danh mục `registry.json` và snapshot ngoại tuyến.<br>• ID tài nguyên trong PackIngest là UUID5 hex (không bắt buộc tiền tố `ast_`).<br>• Danh mục STORAGE chuẩn tắc: `broll`, `sfx`, `bgm`, `overlays`, `memes`, `luts`, `templates`. Các danh mục `meme`, `overlay`, `lut`, `text`, `trans` là UI aliases / template_type.<br>• Availability enum: `online`, `offline`, `missing`, `staging`.<br>• `PERSISTED_ALLOWLIST` nghiêm ngặt: không tự ý mở rộng trường mà không có schema versioning. |
| **SearchIndex & Lexical Normalization** | `src/services/search_index.py:SearchIndex:1`<br>`src/services/search_index.py:strip_accents:99`<br>`src/ui/lux/library/asset_filter.py:get_canonical_category:44` | `[CODE_VERIFIED]` | • Inverted search index hiệu năng cao, xếp hạng theo 7 tín hiệu (exact name, prefix name, tag match, category prefix, frecency...).<br>• `strip_accents`: Chuẩn hóa tiếng Việt bỏ dấu dùng riêng cho tra cứu tìm kiếm mà vẫn bảo toàn tên hiển thị gốc.<br>• **Kế thừa:** Thuật toán lexical matching thuần túy là nền tảng cho 06B Matcher (PROPOSED_DEFERRED). |
| **Action Guard** | `src/core/action_guard.py:check_action_availability:1`<br>`src/core/action_guard.py:ActionGuardResult:28` | `[CODE_VERIFIED]` | • Thẩm định tính khả dụng tại thời điểm thao tác (Action-Time Guard). Trả về: `online`, `offline`, `missing`, `staging`.<br>• **Kế thừa:** 06B Matcher và 06C Handoff chỉ coi asset là khả dụng khi vượt qua `check_action_availability`. |
| **GlobalAssetPool (Legacy Singleton)** | `src/core/broll_sfx.py:GlobalAssetPool:150`<br>`src/core/broll_sfx.py:resolve_meme:223`<br>`src/core/broll_sfx.py:resolve_sfx:251` | `[CODE_VERIFIED]` | • Tra cứu file qua basename trong thư mục `assets/` và project folder.<br>• **Reachable Route & Tác hại thực tế:**<br>  - Route: `convert_plan_to_resolve_timeline` (:1158) $\to$ `asset_pool.resolve_meme(broll.asset_file)`.<br>  - Tác hại: Tráo đổi file ngoài ý muốn nếu 2 clip trùng tên basename; chèn file mẫu trong repo thay vì kho của người dùng; bypass kiểm tra file offline/missing dẫn đến crash hoặc timeline trống trong DaVinci Resolve.<br>  - **Ranh giới:** Không sửa `GlobalAssetPool` trong G06-D; thay thế bằng `StorageManager` + `action_guard` trong các lát cắt matcher sau. |

---

## 2. THIẾT KẾ 06A — BOUNDED BATCH PLANNING & SUMMARY HIERARCHY (ĐÃ TRIỂN KHAI TẠI G06-06A1-I / G06-06A1-R1)

### 2.1. Quyết Định Kiến Trúc & Ràng Buộc Triển Khai

1. **Pure BoundedBatchPlanner Tách Biệt:** Module `src/core/batch_planner.py` thuần Python stdlib, zero I/O, zero Qt, zero model, chỉ nhận dữ liệu in-memory từ caller.
2. **Ngân Sách Byte Cứng (Hard UTF-8 Byte Budget) & Thời Lượng Mềm (Soft Duration Target):**
   - Trần cứng `max_prompt_bytes=32768` bytes đo lường từ chuỗi kết xuất thực tế `render_batch_prompt(batch)` tại batch index thực sau rollover. Không dùng số ước tính hay magic numbers.
   - Thời lượng mục tiêu `target_batch_duration_sec=600.0s` (soft) và trần thời lượng `max_batch_duration_sec=900.0s` (hard). So sánh thời lượng bằng số học `Fraction` chuẩn xác, zero epsilon tolerance.
   - Tối đa `max_clips_per_batch=20` distinct source clip IDs.
   - Một item đơn lẻ vượt quá `max_prompt_bytes` sẽ ném `ValueError("ERR_ITEM_EXCEEDS_PROMPT_BUDGET")`, không tự ý cắt xén hay tóm tắt giả mạo.
3. **Phân Đoạn Clip Dài (Window Partitioning) Giữ Toàn Vẹn ID Nguồn & Mốc Thời Gian Thực:**
   - Mặc định `window_partition_sec=600.0s`. Clip 1200s được chia thành đúng 2 ranges `[0.0, 600.0]` và `[600.0, 1200.0]`.
   - Dùng số học `Fraction` triệt tiêu sai số dấu phẩy động (float drift).
   - Bảo toàn 100% `manifest_id`, `project_name`, `content_hash` (sampled hash độc lập với full SHA) và `clip_id` ("C01", "C02"...), có `partition_index` và `total_partitions`.
   - Kết xuất JSON không dùng `round(..., 6)` làm biến dạng mốc thời gian nguồn (source bounds).
4. **Bảo Toàn Provenance & Xử Lý Bất Thường Trung Thực:**
   - Clip thời lượng 0 hoặc None $\to$ đưa vào `unaccounted_clips` kèm issue `DURATION_UNKNOWN`.
   - Clip missing trên đĩa $\to$ đưa vào `unaccounted_clips` kèm issue `SOURCE_MISSING`.
   - Clip không audio $\to$ giữ nguyên trạng thái `no_audio_track`, `has_audio=False`.
   - Clip lỗi giải mã $\to$ giữ nguyên trạng thái `failed`, không bỏ rơi metadata ranges.
5. **Định Danh Batch Ordinal & Chữ Ký Nội Dung Toàn Diện:**
   - `batch_id`: "B01", "B02"... theo thứ tự trong kế hoạch.
   - `content_signature`: Hash SHA-256 toàn diện đại diện cho toàn bộ payload JSON envelope (items, windows, speech_summary, visual_action, origin, is_sampled, ranges, IDs), exact config và ranh giới prefix/suffix. Loại trừ self-referential signature và estimated bytes.
6. **Thẩm Định Nghiêm Ngặt & Dữ Liệu Bất Biến (Immutability):**
   - Thẩm định raw types trước mọi phép chuyển đổi, loại trừ coercion ngầm (`str(None)`, `bool` thành float/int).
   - Đảm bảo đầu ra hoàn toàn bất biến (frozen dataclasses, tuple), caller không thể làm đột biến kết xuất của batch.

### 2.2. Chi Tiết Thực Thể Dữ Liệu (Actual Code DTOs)

- `BatchBudgetConfig`: `max_prompt_bytes` (1..32768), `target_batch_duration_sec`, `max_batch_duration_sec`, `max_clips_per_batch`, `window_partition_sec`, `max_planned_items`, `prompt_prefix`, `prompt_suffix`.
- `EvidenceWindowSummary`: `window_id`, `start_sec`, `end_sec`, `speech_summary`, `speech_word_count`, `visual_action`, `has_audio`, `is_sampled`, `status`, `origin`, `overlap`, `clipped`.
- `ClipBatchItem`: `clip_id`, `file_name`, `duration_sec`, `source_in_sec`, `source_out_sec`, `item_duration_sec`, `partition_index`, `total_partitions`, `has_audio`, `speech_status`, `content_hash`, `windows`, `full_content_sha256`, `analysis_signature`.
- `BoundedBatch`: `batch_id`, `batch_index`, `manifest_id`, `project_name`, `items`, `distinct_clip_count`, `total_duration_sec`, `estimated_prompt_bytes`, `prompt_prefix`, `prompt_suffix`, `content_signature`.
- `UnaccountedClipIssue`: `clip_id`, `reason`, `message`.
- `ProjectBatchPlan`: `manifest_id`, `project_name`, `batches`, `total_clips`, `planned_clip_count`, `unaccounted_clips`, `issues`, `total_duration_sec`, `created_at`.
   - Clip missing trên đĩa $\to$ đưa vào `unaccounted_clips` kèm issue `SOURCE_MISSING`.
   - Clip không audio $\to$ giữ nguyên trạng thái `no_audio_track`, `has_audio=False`.
   - Clip lỗi giải mã $\to$ giữ nguyên trạng thái `failed`, không bỏ rơi metadata ranges.
5. **Định Danh Batch Ordinal & Chữ Ký Nội Dung:**
   - `batch_id`: "B01", "B02"... theo thứ tự trong kế hoạch.
   - `content_signature`: Hash payload kết xuất + manifest_id + batch_id + items.

---

## 3. THIẾT KẾ 06B — OFFLINE ASSET MATCHER [PROPOSED_DEFERRED]

*(Phần này giữ ở trạng thái đề xuất kiến trúc, chưa được triển khai trong lát cắt 06A1)*

- **Thẩm Quyền Độc Quyền:** Matcher chỉ đọc danh mục tài nguyên thông qua `StorageManager.assets()` và `StorageManager.taxonomy()`. Tuyệt đối không quét trực tiếp đĩa qua `os.walk("assets/...")`.
- **Chính Sách Nguồn Gốc:** Phân biệt rõ `managed` (kho người dùng) $\to$ `linked` $\to$ `builtin` (tài nguyên mẫu đóng gói). Nguồn gốc không được lấn át độ phù hợp ngữ nghĩa (relevance).
- **Thuật Toán Lexical Matching Thuần Túy:** Sử dụng `SearchIndex` (7 tín hiệu) và `strip_accents` chuẩn hóa tiếng Việt không dấu cho tìm kiếm, giữ nguyên nhãn gốc.
- **Xử Lý Kho Trống:** Trả về `UnmetNeed`, tuyệt đối không sinh dummy file hay tải ngầm. Tách biệt advisory matching từ action-time verification.

---

## 4. THIẾT KẾ 06C — EDIT-PLAN AUTHORITY & G07 HANDOFF [PROPOSED_DEFERRED]

*(Phần này giữ ở trạng thái đề xuất kiến trúc, chưa được triển khai trong lát cắt 06A1)*

- **Hợp Đồng Chuẩn Hóa:** Không gán cứng FPS 24; dùng `RationalFPS` từ clip nguồn hoặc project profile.
- **Ranh Giới Rõ Ràng:**
  - G06 chịu trách nhiệm: Logical Validation (kiểm tra manifest_id, frame bounds, schema version, asset IDs) và xuất bản kế hoạch. Fail-closed: giữ nguyên plan cũ nếu validation thất bại.
  - G07 chịu trách nhiệm: Physical Resolve Scripting API Execution, Media Pool Ingestion, Timeline Assembly, Preflight Check và Rollback Recovery.

---

## 5. NĂM VÍ DỤ JSON CHUẨN TẮC

*(Được cập nhật đầy đủ, loại bỏ các magic numbers và phản ánh đúng định dạng kết xuất thực tế của BoundedBatch)*

### Ví Dụ (a): Phân chia Clip có thoại và Clip câm vào 2 Batches vẫn giữ nguyên ID toàn cục

```json
{
  "manifest_id": "m_a1b2c3d4",
  "project_name": "Du_Lich_Ha_Giang",
  "batches": [
    {
      "batch_id": "B01",
      "batch_index": 0,
      "distinct_clip_count": 1,
      "total_duration_sec": 60.0,
      "items": [
        {
          "clip_id": "C01",
          "file_name": "DJI_0001.MP4",
          "clip_duration_sec": 60.0,
          "source_in_sec": 0.0,
          "source_out_sec": 60.0,
          "item_duration_sec": 60.0,
          "partition_index": 0,
          "total_partitions": 1,
          "has_audio": true,
          "speech_status": "transcribed"
        }
      ]
    },
    {
      "batch_id": "B02",
      "batch_index": 1,
      "distinct_clip_count": 1,
      "total_duration_sec": 60.0,
      "items": [
        {
          "clip_id": "C02",
          "file_name": "GOPRO_0002.MP4",
          "clip_duration_sec": 60.0,
          "source_in_sec": 0.0,
          "source_out_sec": 60.0,
          "item_duration_sec": 60.0,
          "partition_index": 0,
          "total_partitions": 1,
          "has_audio": false,
          "speech_status": "no_audio_track"
        }
      ]
    }
  ]
}
```

### Ví Dụ (b): Clip dài 1200s vượt ngân sách được phân mảnh cửa sổ 600s

```json
{
  "manifest_id": "m_a1b2c3d4",
  "batches": [
    {
      "batch_id": "B01",
      "batch_index": 0,
      "items": [
        {
          "clip_id": "C03",
          "clip_duration_sec": 1200.0,
          "source_in_sec": 0.0,
          "source_out_sec": 600.0,
          "item_duration_sec": 600.0,
          "partition_index": 0,
          "total_partitions": 2
        }
      ]
    },
    {
      "batch_id": "B02",
      "batch_index": 1,
      "items": [
        {
          "clip_id": "C03",
          "clip_duration_sec": 1200.0,
          "source_in_sec": 600.0,
          "source_out_sec": 1200.0,
          "item_duration_sec": 600.0,
          "partition_index": 1,
          "total_partitions": 2
        }
      ]
    }
  ]
}
```

### Ví Dụ (c): Kho tài nguyên rỗng / Không có asset phù hợp (Unmet Need)

```json
{
  "insert_id": "INS01",
  "track_type": "video",
  "track_index": 2,
  "rec_in_sec": 14.5,
  "duration_sec": 2.5,
  "asset_id": null,
  "status": "unmet_need",
  "unmet_details": {
    "required_category": "meme",
    "search_tags": ["that_vong", "toang", "facepalm"],
    "action": "skip_insertion_and_notify_user"
  }
}
```

### Ví Dụ (d): Bản vẽ AI bị từ chối do vượt ngưỡng khung hình (Fail-Closed Reject)

```json
{
  "validation_result": {
    "accepted": false,
    "error_code": "ERR_SOURCE_FRAME_OUT_OF_BOUNDS",
    "message": "Từ chối bản vẽ kịch bản: Phân đoạn S02 yêu cầu cắt từ 12.0s đến 25.0s nhưng clip C01 chỉ dài 10.0s.",
    "state_action": "RETAIN_PREVIOUS_ACCEPTED_PLAN"
  }
}
```

### Ví Dụ (e): Khôi phục sau sự cố (Resume Boundary) và Hợp nhất không trùng lặp

```json
{
  "manifest_id": "m_a1b2c3d4",
  "recovery_action": "RESUME_FROM_BATCH_02",
  "assembly_policy": {
    "preserve_completed_batches": true,
    "deduplicate_master_timeline_by": "segment_id_and_clip_id"
  }
}
```

---

## 6. MA TRẬN KIỂM THỬ HỮU HẠN & KẾT QUẢ XÁC MINH

- Bộ test TDD đơn vị tại `tests/core/test_batch_planner.py` bao gồm 39 test cases (21 tests G06-06A1-I ban đầu + 14 tests G06-06A1-R1 + 4 regression tests G06-06A1-R2).
- Toàn bộ 39/39 tests đạt **100% PASS** trong 0.41s với process exit code 0.
- Log kiểm thử lưu tại: [`.bridge/ux-evidence/G06-06A1-R2/pytest_run.log`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/.bridge/ux-evidence/G06-06A1-R2/pytest_run.log).


## Codex review06A1-I — 2026-10-28
Implementation CHƯA được nghiệm thu dù21focusedtests pass. Probes budget/time/export/signature/validation tại docs/architecture/06A1-codex-review.md. Active G06-06A1-R1; Worker cập nhật actual DTO fields và semantics theo workorder, không coi các claim trong report là acceptance.


## Codex06A1-R1 review — 2026-10-28
Fresh35tests0.42s exit0, budget/lossless/signature fixes được nhận trongscope; chưaacceptwholemodule. Residual types/skip/identitymetadata/windowbound tại docs/architecture/06A1-R1-codex-review.md, active06A1-R2. Không06A2hoặcmodulemới.


## Codex nghiệm thu06A1 — 2026-10-28
G06-06A1 CODE_ACCEPTED_WITH_RECORDED_LIMITS sauR2: Codex39tests0.46s exit0 và fresh rawvalidation/windowbound probes xanh. Xem docs/architecture/06A1-R2-codex-review.md. Chưa có actualG05adapter/runtimeconsumer/hierarchy. ActiveG06-06A2-D, không sửaA1/code/model/test trongD.

## Trạng thái hiện tại — Codex 2026-10-28
06A1 và06A2 pure foundation accepted trong recorded limits (review06A2-R2); 94 focused tests pass. Active06A3-D thiết kế consumer batch Review/Prompt và full prompt exact byte cap. 06B/06C/hierarchical summary/ledger vẫn proposed deferred, không suy từ tên batch là đã có semantic summary.

## Trạng thái hiện tại — 2026-10-28 sau06A3 acceptance
A1/A2/A3 accepted trong recorded limits. 06A3-gate-acceptance.md là authority cho code+componentdesktop scope. Active06A4-D batch draft receiver vàproject hierarchy; 06B/06C/ledger persistence chưa implement, không gọi batching là semantic summary đã hoàn tất.
