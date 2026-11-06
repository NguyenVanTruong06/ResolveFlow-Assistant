# CHUNDVC — KHẢO SÁT HIỆN TRẠNG & BẢN ĐỒ KIẾN TRÚC (G00-D)

- **Mã nhiệm vụ:** `G00-D` (Baseline Audit & Scope Finalization)
- **Tech Lead:** Codex
- **Người thực hiện:** Antigravity (Worker)
- **Thời gian lập:** 2026-10-26
- **Trạng thái:** `DESIGN_READY` (Khảo sát hiện trạng hoàn tất, dừng theo quy ước giai đoạn Thiết kế D)

---

## 1. ĐỐI CHIẾU CÁC KẾT QUẢ CHƯA KIỂM CHỨNG TRONG TÀI LIỆU LỊCH SỬ

Qua đối chiếu 4 tài liệu cơ sở (`docs/architecture/ROADMAP.md`, `.bridge/storage-report.md`, `docs/multi-tier-storage.md`, `docs/local-vision-runtime.md`) với mã nguồn thực tế:

| Khẳng định trong tài liệu lịch sử | Trạng thái mã nguồn hiện tại | Phân loại | Bằng chứng mã nguồn / Ghi chú |
|---|---|---|---|
| **Kho đa tầng không fallback media về ổ máy** | `StorageOfflineError` được ném khi mất marker `library.json` | `VERIFIED` | `src/core/storage_manager.py:73` (`StorageOfflineError`), `src/core/pack_ingest.py:57` (`storage.require_online()`). |
| **Nhập kho có kiểm duyệt qua Ingest Wizard** | Đã triển khai Wizard, bảng IngestReviewModel, category delegate | `VERIFIED` | `src/ui/widgets/pack_ingest_wizard.py:35+`, `src/core/pack_ingest.py:53+`. |
| **Toàn bộ luồng kéo thả/tải đã qua kho mới** | Còn tồn tại nhiều luồng cũ ghi thẳng vào `./assets/` và `data/installed_packs.json` | `CODE_ONLY` (Lệch thiết kế) | `src/ui/lux/shell/edge_dock.py:1319` (kéo thả .rfpack vào Dock gọi `PackManager.install_pack` ghi vào `./assets/`), `src/ui/tabs/tab_assets.py:7525` (`shutil.copy2` vào `./assets/broll_memes/`). |
| **Đồng bộ kho Studio sang Dock và SearchIndex** | Signal `library_assets_changed` kết nối làm mới `set_assets` và `set_search_index` | `VERIFIED` | `src/main.py:90-94`, `src/ui/studio/studio_window.py:189`, `src/ui/tabs/tab_assets.py:6074`. |
| **Dock poll trạng thái ổ mỗi 2,5 giây** | Timer 2500ms kiểm tra `mtime_ns` của `registry.json` và nạp lại khi online | `VERIFIED` | `src/main.py:142-152` (`QTimer.singleShot(2500, refresh_storage)`). |
| **Local Vision chạy thật trên GPU với Qwen3-VL** | Đã có pipeline gọi Ollama `qwen3-vl:2b-instruct` qua REST API | `VERIFIED` | `src/core/visual_describer.py:45+`, `src/ui/director/pages/source_page.py:287+`. |
| **Mô tả hành động phức tạp / 3 giờ footage** | Chưa benchmark quy mô lớn; mới chỉ thử nghiệm 2 clip ngắn | `UNVERIFIED` | `docs/local-vision-runtime.md:66-68` thừa nhận chỉ mới thử nghiệm clip ngắn, chưa có dataset đối chiếu thao tác đồ vật chuẩn. |
| **Nghiệm thu thực tế trên máy người dùng** | Mới chạy offscreen và kiểm thử tự động, chưa kiểm chứng desktop vật lý và Resolve thật | `UNVERIFIED` | Ghi nhận trong `.bridge/storage-report.md:29-31` và `docs/architecture/ROADMAP.md:31-35`. |

---

## 2. BỐN CALL GRAPH CHI TIẾT (FILE:HÀM:DÒNG)

### Call Graph (a): Khởi động app → Nạp tài nguyên → Model/View → Tìm kiếm → Chọn / Xem trước / Chèn DaVinci

```
[1] Mở ứng dụng (Entrypoint)
    └── src/main.py:start_app() (L110-L137)
        ├── src/services/resolve_gateway.py:ResolveGateway.__init__() (L44)
        ├── src/services/insert_service.py:InsertService.__init__(gateway) (L100)
        ├── src/services/resolve_state.py:ResolveStateService.__init__(gateway) (L27)
        └── src/main.py:load_all_library_assets() (L39-L76)
            ├── src/core/text_preset.py:BUILTIN_PRESETS (L16)
            ├── src/core/transition_preset.py:BUILTIN_TRANSITIONS (L15)
            ├── src/core/asset_indexer.py:AssetIndexer.scan_*() (L50-L180)
            └── src/core/storage_manager.py:StorageManager.ui_assets() (L265-L290)

[2] Khởi tạo Surface chính (EdgeDockWindow & SearchIndex)
    └── src/main.py:start_app() (L129-L137)
        ├── src/services/search_index.py:SearchIndex.__init__(assets=all_assets) (L25)
        ├── src/ui/lux/shell/edge_dock.py:EdgeDockWindow.__init__() (L80-L191)
        ├── src/ui/lux/shell/edge_dock.py:EdgeDockWindow.set_assets(all_assets) (L420-L435)
        │   ├── src/ui/lux/library/asset_model.py:AssetListModel.set_assets(items) (L219-L225)
        │   │   └── src/ui/lux/library/asset_model.py:extract_asset_info(item) (L36-L74)
        │   └── src/ui/lux/palette/command_palette.py:CommandPaletteWindow.set_assets() (L120)
        └── src/ui/lux/shell/edge_dock.py:EdgeDockWindow.set_search_index(search_index) (L481-L485)
            └── src/ui/lux/library/asset_filter.py:AssetFilterProxyModel.set_search_index() (L45)

[3] Mở Kho Đạo Cụ Studio & Đồng bộ ngược về Dock
    └── src/main.py:create_studio_manager() -> open_studio() (L79-L107)
        ├── src/ui/studio/studio_window.py:StudioWindow.__init__() (L175-L200)
        │   └── src/ui/tabs/tab_assets.py:TabAssets.__init__() (L3485-L3520)
        └── [Khi quét lại kho trong Studio]:
            └── src/ui/tabs/tab_assets.py:TabAssets._rescan_assets() (L6010-L6076)
                ├── src/ui/tabs/tab_assets.py:TabAssets._merge_storage_assets() (L5997-L6009)
                └── Signal: library_assets_changed.emit(all_assets) (L6074)
                    └── src/ui/studio/studio_window.py:assets_updated.emit (L189)
                        └── src/main.py:on_studio_assets_updated(new_assets) (L90-L94)
                            ├── dock.set_assets(new_assets) (L91)
                            ├── search_index.set_assets(new_assets) (L92)
                            └── dock.set_search_index(search_index) (L93)

[4] Lọc danh mục, tìm kiếm và tương tác trên Lưới (Grid)
    ├── Lọc danh mục: src/ui/lux/library/category_nav.py:CategoryNavWidget.category_selected -> src/ui/lux/shell/edge_dock.py:_on_category_selected() (L845)
    │   └── src/ui/lux/library/asset_filter.py:AssetFilterProxyModel.set_category_filter() (L60)
    ├── Tìm kiếm: src/ui/lux/shell/dock_header.py:txt_search.textChanged -> src/ui/lux/shell/edge_dock.py:_on_search_changed() (L888)
    │   └── src/ui/lux/library/asset_filter.py:AssetFilterProxyModel.set_search_query() (L75)
    ├── Chọn thẻ: src/ui/lux/library/asset_grid.py:AssetGridView.clicked -> src/ui/lux/shell/edge_dock.py:_on_card_clicked() (L860-L870)
    │   ├── src/ui/lux/library/action_bar.py:ActionBarWidget.set_asset() (L120)
    │   └── src/ui/lux/inspector/inspector_host.py:SheetInspector.open_sheet() (L60)
    └── Xem trước: src/ui/lux/library/hover_preview.py:HoverPreviewController.preview_asset() (L40)
        └── src/services/audio_router.py:AudioRouter.set_audio_output() (L30)

[5] Thực thi Chèn vào Timeline DaVinci Resolve
    └── src/ui/lux/shell/edge_dock.py:_on_action_bar_insert() (L1180-L1215)
        └── src/services/insert_service.py:InsertService.insert(asset_dict, mode, ...) (L117-L165)
            └── src/services/insert_service.py:InsertService.insert_async() (L170-L244)
                ├── Chuyển cảnh: src/services/insert_service.py:_insert_transition() (L245-L280)
                │   └── src/core/resolve_bridge.py:prepare_native_transition() (L1450)
                ├── Âm thanh: src/services/insert_service.py:_insert_audio() (L228)
                │   └── src/services/resolve_gateway.py:ResolveGateway.submit() (L85)
                ├── Video / B-Roll / Meme: src/services/insert_service.py:_insert_video() (L238)
                │   └── src/services/resolve_gateway.py:ResolveGateway.submit() (L85)
                └── Title / Macro: src/services/insert_service.py:_insert_title_worker() (L242)
                    └── QProcess: src/services/title_worker.py (hoặc Carrier sequence)
```

---

### Call Graph (b): Storage Root → Download / Staging → Kiểm duyệt → Commit / Registry / Snapshot → Làm mới UI & Các đường dẫn Legacy

```
[1] Cấu hình và Kiểm duyệt Gói Tài Nguyên (Smart Ingestion)
    └── src/ui/widgets/pack_ingest_wizard.py:PackIngestWizard (L35+)
        ├── Chọn Storage Root: src/core/storage_manager.py:StorageManager.set_root() (L110-L120)
        │   └── Kiểm tra .chundvc_meta/library.json UUID marker (L80-L90)
        └── Chuẩn bị Staging: src/core/pack_ingest.py:PackIngestService.prepare(source) (L56-L84)
            ├── src/core/storage_manager.py:StorageManager.new_staging() (L93) -> .chundvc_meta/staging/<uuid>/payload
            ├── Giải nén an toàn: src/core/pack_ingest.py:_extract() (L85-L114)
            │   ├── Chống Path Traversal (safe_relative): src/core/storage_manager.py:safe_relative() (L47-L53)
            │   └── Guard dung lượng (ensure_space): src/core/storage_manager.py:ensure_space() (L145)
            ├── Duyệt cây thư mục: src/core/pack_ingest.py:inspect_tree() (L33-L50)
            ├── Đoán danh mục: src/core/pack_ingest.py:guess() (L19-L30)
            └── Đọc manifest nhúng: src/core/pack_ingest.py:inspect_job() (L115-L148)

[2] Duyệt & Chỉnh sửa trên UI (Review Table)
    └── src/ui/widgets/pack_ingest_wizard.py:IngestReviewModel (L120-L210)
        └── Người dùng gán category, subcategory, tags, toggle exclude

[3] Commit Atomic & Cập nhật Metadata
    └── src/core/pack_ingest.py:PackIngestService.commit(job) (L149-L196)
        ├── Kiểm tra taxonomy: src/core/storage_manager.py:StorageManager.taxonomy() (L100)
        ├── Khóa tiến trình: with storage.lock(): src/core/storage_manager.py:lock() (L125)
        ├── Ghi pack_manifest.json: src/core/storage_manager.py:atomic_json() (L33-L45)
        ├── Loại bỏ file excluded khỏi payload: payload.unlink() (L185-L186)
        ├── Đổi tên payload thành thư mục pack chính thức (os.rename payload -> 02_cloud_downloads/packs/...) (L187)
        ├── Cập nhật registry.json atomic: src/core/storage_manager.py:atomic_json() (L188)
        └── Ghi snapshot cục bộ (tối đa 16MB): src/core/storage_manager.py:snapshot() (L135-L142)

[4] Cập nhật UI & Polling
    ├── Wizard phát tín hiệu: ingest_completed.emit()
    ├── Studio Window cập nhật: src/ui/tabs/tab_assets.py:TabAssets._merge_storage_assets() (L5997)
    └── Polling nền 2.5s: src/main.py:refresh_storage() (L142-L152)

[5] CÁC ĐƯỜNG DẪN LEGACY CÒN CẠNH TRANH (GHI SAI VÀ BỎ QUA STORAGE ROOT):
    ├── ĐƯỜNG 1 (Dock Drag-and-Drop):
    │   └── src/ui/lux/shell/edge_dock.py:EdgeDockWindow.dropEvent() (L1319-L1323)
    │       └── from src.core.pack_manager import PackManager; PackManager.install_pack(fn)
    │           └── Giải nén trực tiếp vào thư mục "./assets/" và ghi vào "data/installed_packs.json" (src/core/pack_manager.py:61, L209-L224)
    ├── ĐƯỜNG 2 (Cloud Pack Worker cũ):
    │   └── src/ui/tabs/cloud_pack_worker.py:CloudPackInstallWorker.run() (L20-L55)
    │       └── Gọi PackManager.install_pack() -> ghi đè vào "./assets/"
    ├── ĐƯỜNG 3 (Asset Hub cũ):
    │   └── src/ui/tabs/tab_asset_hub.py:AssetHubWidget._on_download_pack() / _on_install_pack() (L279-L330)
    │       └── Chạy worker tải về thư mục tạm rồi gọi PackManager.install_pack()
    └── ĐƯỜNG 4 (Import File/Folder trong TabAssets):
        └── src/ui/tabs/tab_assets.py:TabAssets._on_import_files_or_folders() (L7523-L7528)
            └── shutil.copy2(fp, target_dir) vào thẳng "./assets/broll_memes/..." và gọi _rescan_assets()
```

---

### Call Graph (c): Source Import → Whisper / Visual Sampling / Model → Cache / Manifest → StoryCopilot Prompt / Edit-Plan

```
[1] Thêm Footage Nguồn (Source Page)
    └── src/ui/director/pages/source_page.py:SourcePage.add_paths() (L806-L841)
        ├── Quét đệ quy thư mục & lọc phần mở rộng video: SourceScanWorker.run() (L200-L246)
        ├── Sắp xếp theo trình tự thời gian quay: src/core/chronological_sorter.py (L43)
        ├── Trích xuất thông số & kiểm tra cache: probe_video_file() (L55-L95)
        │   └── Lưu memo RAM: _PROBE_MEMO (L25), _SORT_MEMO (L27)
        └── Phát tín hiệu theo lô: batch_loaded.emit() (L241) -> hiển thị SourceClipRow (L962)

[2] Lấy mẫu thị giác (Visual Sampling)
    └── src/ui/director/pages/source_page.py:SourcePage._start_visual_scan() (L1117-L1133)
        └── src/ui/director/pages/source_page.py:VisualScanWorker.run() (L264-L285)
            └── src/core/visual_index.py:VisualIndex.scan(path) (L80-L150)
                ├── Cắt frame đại diện qua PyAV (không chạy ffprobe phụ thuộc seek giả)
                ├── Lưu ảnh contact sheet tại ~/.resolveflow_cache/visual/frames/
                └── Ghi metadata vào visual_index.json (schema v3)

[3] Nhận diện Lời thoại (Whisper) & Mô tả Thị giác (Ollama Vision)
    ├── Whisper Speech-to-Text:
    │   └── src/ui/director/pages/source_page.py:SourcePage.rough_scan_requested -> DirectorController
    │       └── src/core/transcriber.py (Whisper Worker) -> Lưu cache JSON transcript
    └── Ollama Local Vision:
        └── src/ui/director/pages/source_page.py:SourcePage._start_visual_description() (L1038-L1058)
            └── src/ui/director/pages/source_page.py:VisualDescribeWorker.run() (L308-L343)
                └── src/core/visual_describer.py:describe_with_ollama() (L45-L120)
                    ├── Chia cửa sổ thời gian (mặc định 8s/cửa sổ, 4 ảnh/cửa sổ)
                    ├── Gửi HTTP POST tới http://127.0.0.1:11434/api/chat (qwen3-vl:2b-instruct)
                    └── Chuẩn hóa JSON schema đầu ra & ghi cache nguyên tử

[4] Tập hợp Manifest & Sinh Kế Hoạch Dựng (StoryCopilot)
    └── src/core/story_copilot.py:StoryCopilot (L65+)
        ├── src/core/clip_manifest.py:ClipManifestManager.build_project_manifest() (L40-L110)
        ├── Sinh Prompt Đạo diễn: StoryCopilot.build_prompt() (L180-L310)
        │   ├── Đính kèm tóm tắt kho đạo cụ: StoryCopilot.get_available_assets_summary() (L130-L175)
        │   ├── Đính kèm mốc nhịp âm nhạc (Music Beats): (L242-L250)
        │   └── Đính kèm transcript lời thoại và mô tả visual_segments theo timestamp (L258-L290)
        └── Bóc tách kết quả từ LLM (Ollama hoặc Cloud API):
            └── StoryCopilot.parse_director_plan(raw_json) (L350-L420)
                └── Trả về đối tượng Pydantic: CopilotDirectorPlan (L51-L63)
```

---

### Call Graph (d): Kế Hoạch (Plan) → Gateway / Bridge → DaVinci Resolve & Vòng đời Worker (Cancel / Close / Quit)

```
[1] Thực thi Kế hoạch Dựng lên DaVinci Resolve
    └── src/ui/director/director_controller.py:DirectorController.build_pipeline_kwargs() (L38-L105)
        └── src/ui/auto/auto_window.py:PipelineWorker.run() (L1450-L1545)
            ├── Bước 1: Ghép nối phân đoạn Timeline (Story Arranger / FCPXML / FCP7 XML / EDL)
            │   ├── src/core/fcpxml.py:FCPXMLGenerator.generate_timeline_fcpxml() (L1515)
            │   └── src/core/fcpxml.py:FCPXMLGenerator.generate_fcp7_xml() (L1520)
            ├── Bước 2: Nạp Timeline vào DaVinci Resolve
            │   └── src/core/resolve_api.py:ResolveAutomation.import_edl_to_timeline() (L1533)
            │       └── src/services/resolve_gateway.py:ResolveGateway.submit()
            └── Bước 3: Chèn B-Roll, SFX, Text Subtitles theo Timeline Markers
                └── src/services/insert_service.py:InsertService.insert() (L117)
                    └── src/core/resolve_bridge.py:AppendToTimeline / ImportFusionComp (L8-L22)

[2] Quản lý Vòng Đời & Hủy An Toàn (Cancel / Close / Shutdown)
    ├── Khi người dùng ấn nút Hủy trên UI:
    │   └── src/ui/director/pages/source_page.py:SourcePage.clear_all_paths() / shutdown_workers() (L922-L945)
    │       ├── Tăng _current_job_id (vô hiệu hóa các batch đang bay về GUI) (L923)
    │       └── Duyệt _workers_registry gọi worker.cancel() (L928, L943)
    ├── Khi đóng cửa sổ SourcePage / DirectorWindow:
    │   └── src/ui/director/pages/source_page.py:SourcePage.closeEvent(event) (L946-L953)
    │       └── if not self.shutdown_workers(): self._close_pending = True; event.ignore() (L948-L950)
    │           └── Chờ tín hiệu worker.finished -> _cleanup_worker() -> workers_idle.emit() -> QTimer close() (L865-L869)
    └── Khi thoát ứng dụng toàn cục (Quit Application):
        └── src/main.py:start_app() (L113-L114, L167-L174)
            ├── src/services/source_shutdown.py:SourceShutdownGuard (chặn thoát đột ngột nếu còn LibraryJob)
            ├── resolve_state.pause_polling() (L169)
            └── gateway.shutdown() (L170)
```

---

## 3. BẢNG CÁC NGUỒN DỮ LIỆU ĐANG CẠNH TRANH

| Nhóm dữ liệu | Bản sao / Định dạng | Ai Đọc | Ai Ghi | Nguồn chân lý hiện tại (Source of Truth) |
|---|---|---|---|---|
| **Cấu trúc dữ liệu Asset** | 1. `dict` chuẩn<br>2. `StudioAsset` (dataclass)<br>3. `TextStylePreset` / `TransitionStylePreset` (Pydantic)<br>4. `PackManifest` (Pydantic) | `main.py`, `EdgeDockWindow`, `AssetListModel`, `InsertService`, `SearchIndex`<br>`TabAssets`, `StudioWindow`<br>`text_preset.py`, `transition_preset.py`<br>`pack_manager.py`, `tab_asset_hub.py` | `StorageManager.ui_assets()`, `AssetIndexer`<br>`TabAssets._rescan_assets()`<br>Hardcoded constants<br>`pack_manager.py:inspect_pack()` | **Không có một nguồn duy nhất**. `AssetListModel.extract_asset_info` phải liên tục duck-type để chuyển đổi giữa `dict` và `StudioAsset`. |
| **Phân loại Category & Bí danh (Aliases)** | 1. Canonical Storage: `broll, sfx, bgm, overlays, memes, luts, templates`<br>2. UI Tabs: `text, trans, lut, meme, sfx, bgm, overlay, broll`<br>3. Legacy Pack: `titles, transitions, luts, memes, sfx, overlays`<br>4. Custom Category: ID chữ thường do user tạo | `StorageManager`, `PackIngestService`<br>`CategoryNavWidget`, `TabAssets`<br>`PackManager`, `CloudPackWorker`<br>`PackIngestWizard`, `taxonomy.json` | `storage_manager.py:CATEGORIES`<br>`category_nav.py:DEFAULT_CATEGORIES`<br>`pack_manager.py:CATEGORY_FOLDERS`<br>`taxonomy.json` | **Cạnh tranh trực tiếp**. `StorageManager` dùng danh từ số nhiều (`memes`, `luts`), UI dùng số ít (`meme`, `lut`), legacy dùng `transitions`/`titles`. |
| **Kho & Registry lưu trữ** | 1. Storage Root (`.chundvc_meta/registry.json`, `02_cloud_downloads/packs/`)<br>2. Local snapshot (`registry_snapshot.json` 16MB)<br>3. Legacy `data/installed_packs.json` + `./assets/`<br>4. Local repo `./assets/` | `StorageManager`, `main.py`<br>`StorageManager` khi offline<br>`PackManager.get_installed_packs()`<br>`AssetIndexer.scan_*()` | `PackIngestService.commit()`<br>`StorageManager.snapshot()`<br>`PackManager.record_installed_pack()`<br>`TabAssets._on_import_files_or_folders()`, `EdgeDock.dropEvent()` | **Cạnh tranh nghiêm trọng**. Thao tác kéo thả vào EdgeDock hoặc Import trong TabAssets vẫn ghi đè vào thư mục `./assets/` và `data/installed_packs.json`, hoàn toàn bỏ qua Storage Root. |
| **Cấu hình QSettings** | 1. Domain `"ChunDVC", "ResolveFlow"`<br>2. Domain `"ResolveFlow", "ResolveFlowAssistant"` | `edge_dock.py`, `main.py`, `pack_ingest_wizard.py`<br>`source_page.py` | `edge_dock.py` (`dock_edge`, `bubble_y`)<br>`source_page.py` (`whisper_model`, `whisper_lang`) | **Phân mảnh**. Cấu hình bị tách làm 2 tổ chức (ChunDVC vs ResolveFlow), gây nguy cơ mất đồng bộ cài đặt người dùng. |
| **Thư mục Cache** | 1. Visual Cache: `~/.resolveflow_cache/visual/frames/`<br>2. Whisper Cache: `~/.resolveflow_cache/` hoặc `~/.cache/`<br>3. Staging Ingestion: `<root>/.chundvc_meta/staging/` | `VisualIndex`, `SourcePage`<br>`Transcriber`<br>`PackIngestService` | `visual_index.py`<br>`transcriber.py`<br>`pack_ingest.py` | Tách biệt nhưng Visual Cache mặc định ghi vào ổ `~` (Home), có thể làm đầy ổ hệ thống khi xử lý video dung lượng lớn. |
| **Trạng thái Trực tuyến / Ngoại tuyến** | 1. Storage Root Online (`library.json` + UUID)<br>2. Resolve Connection (`ResolveStateService`) | `StorageManager.online`, `main.py`<br>`EdgeDockWindow`, `FloatingBubbleWidget` | `storage_manager.py:check_marker()`<br>`resolve_state.py:poll()` | `StorageManager` và `ResolveStateService` quản lý độc lập, hoạt động ổn định. |
| **Đơn vị Thời gian & Mốc cắt** | 1. Giây thực (Float seconds `0.0s`)<br>2. Frame nguyên (Integer frames theo FPS)<br>3. Mtime file (Nanoseconds `st_mtime_ns` vs Float `st_mtime`) | `StoryCopilot`, `AutoCut`, `VisualSampling`<br>`ResolveBridge`, `FCPXMLGenerator`<br>`source_page.py`, `main.py` | `PyAV`, `cv2`, `SilenceDetector`<br>`ResolveBridge.seconds_to_frames()`<br>`os.stat()` | Chuẩn hóa tương đối tốt: Pipeline dùng float seconds, cầu nối Resolve quy đổi sang frame qua FPS. |

---

## 4. BẢNG TỔNG HỢP KIỂM THỬ LỊCH SỬ

*Lưu ý theo ràng buộc G00-D: Không chạy lại test trong pha này; dưới đây là tổng hợp số liệu đã ghi nhận từ các báo cáo kiểm thử trước đó.*

| Nhóm kiểm thử | Số test ghi nhận | Thời gian chạy | Trạng thái | Nguồn báo cáo |
|---|---|---|---|---|
| **Kho đa tầng & Smart Ingestion (Tập A)** | 102 passed | 25.01s | PASS | `.bridge/storage-report.md:13-14` |
| **Kho đa tầng (Tập B - sau guard tags)** | 23 passed | 4.69s | PASS | `.bridge/storage-report.md:16-18` |
| **Local Vision & Async Source UI** | 77 passed | 19.38s | PASS | `docs/local-vision-runtime.md:62` |
| **Edge Dock & Studio Window Focused Suite** | 46 passed | 17.36s | PASS | `.bridge/report.md:53-69` |

*(Ghi chú: Các tập test trên là các tập con có giao nhau giữa các module, không cộng dồn cơ học).*

---

## 5. ĐÍNH CHÍNH & CẬP NHẬT THEO CODEX REVIEW (G00-D & G00-H1-I)

Theo văn bản review của Tech Lead Codex (`docs/architecture/00-codex-review.md`) và kết quả thực hiện hotfix `G00-H1-I`:

1. **Phân loại bằng chứng kiểm thử (Evidence Classification):**
   - Các số liệu kiểm thử ở Bảng 4 là dữ liệu **`HISTORICAL`** được ghi nhận từ các chu kỳ trước, không phải số liệu nghiệm thu runtime mới nhất.
   - Kết quả khảo sát G00 chỉ chứng minh ở mức **`CODE_VERIFIED`** / **`CODE_ONLY`**, không đại diện cho việc chạy GPU vật lý hay Resolve thật trên môi trường người dùng.

2. **Đính chính chi tiết kỹ thuật:**
   - `StudioAsset` là class Python thông thường (`src/ui/tabs/tab_assets.py:33`), không phải `dataclass`.
   - Cơ chế timer làm mới kho trong `src/main.py:154` sử dụng `storage_timer.start(2500)`, không phải `QTimer.singleShot(2500, ...)`.
   - Vấn đề phân loại danh mục (storage số nhiều `memes`, `luts` vs UI số ít `meme`, `lut`): Hiện đã có adapter chuyển đổi tại `StorageManager.ui_assets()`. Chuẩn hóa là mục tiêu kiến trúc dài hạn cho G01, không phải bug lọc đang gây sập ứng dụng.
   - Vấn đề phân mảnh domain QSettings (`ChunDVC` vs `ResolveFlow`): Cần kiểm kê keys (inventory) và bổ sung fallback đọc tương thích trong G01, không được thay đổi domain hàng loạt làm mất cấu hình người dùng.

3. **Cập nhật trạng thái sau Hotfix G00-H1-I:**
   - **`EdgeDockWindow.dropEvent`**: Đã loại bỏ hoàn toàn việc gọi `PackManager.install_pack(fn)`. Toàn bộ luồng kéo thả file `.zip` / `.rfpack` đã được chuyển sang `PackIngestWizard(parent=self, source=fn, auto_prepare=True)` kèm cơ chế hàng đợi an toàn, khôi phục `_is_dragging`, và chỉ cập nhật kho khi commit thành công.
   - **`TabAssets._import_files_or_folders`**: Đã loại bỏ hoàn toàn các lệnh `shutil.copy2` sao chép trực tiếp vào `./assets/` và thư mục DaVinci Resolve. Toàn bộ generic import (kéo thả file/thư mục hoặc bấm nút ➕ Nhập) đã được chuyển sang `PackIngestWizard` kiểm duyệt, hỗ trợ batch/multi-select không bị ghi đè cùng tên file và dừng hàng đợi khi người dùng hủy.
