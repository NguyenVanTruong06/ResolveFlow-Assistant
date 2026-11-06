# CHUNDVC — BACKLOG ƯU TIÊN & LỘ TRÌNH TRIỂN KHAI (G00-D)

- **Mã nhiệm vụ:** `G00-D` (Baseline Audit & Scope Finalization)
- **Tech Lead:** Codex
- **Người thực hiện:** Antigravity (Worker)
- **Thời gian lập:** 2026-10-26
- **Trạng thái:** `DESIGN_READY`

---

## 1. DANH SÁCH 10 VẤN ĐỀ ĐÁNG SỬA NHẤT (XẾP THEO P0 / P1 / P2)

Ưu tiên được xếp theo nguyên tắc: **Chống mất dữ liệu > Không ghi sai ổ > Không báo thành công sai > Bỏ qua service/kiến trúc > Vấn đề thẩm mỹ**.

| ID | Mức | Tên vấn đề | Bằng chứng mã nguồn (`file:hàm:dòng`) | Ca tái hiện dự kiến | Tác động & Rủi ro |
|---|---|---|---|---|---|
| **ISSUE-01** | **P0** | Kéo thả gói vào Thanh Dock bỏ qua Storage Root | `src/ui/lux/shell/edge_dock.py:1319-1323`<br>`src/core/pack_manager.py:209-224` | Kéo thả tệp `.rfpack` hoặc `.zip` vào cửa sổ EdgeDock. Hàm `dropEvent()` gọi trực tiếp `PackManager.install_pack(fn)`. | Giải nén thẳng vào `./assets/` (ổ dự án/hệ thống) và ghi `data/installed_packs.json`. Hoàn toàn bỏ qua Storage Root đã chọn, bỏ qua kiểm tra dung lượng dự phòng 256MB và bỏ qua atomic registry. |
| **ISSUE-02** | **P0** | Nút Import tệp trong TabAssets sao chép thẳng vào workspace | `src/ui/tabs/tab_assets.py:7523-7528` | Người dùng bấm nút Thêm tệp trong Kho Đạo Cụ Studio. Hàm `_on_import_files_or_folders` dùng `shutil.copy2` copy vào `./assets/broll_memes/...`. | Làm phình to thư mục mã nguồn git với các file media nhị phân nặng, làm sai lệch kho lưu trữ ngoài và không kiểm soát dung lượng đĩa. |
| **ISSUE-03** | **P0** | Đường dẫn tương đối nguy hiểm `data/installed_packs.json` | `src/core/pack_manager.py:61` | Khởi chạy app từ thư mục làm việc khác (ví dụ: `cd /tmp && python3 /path/to/main.py`). | `Path("data") / "installed_packs.json"` sẽ tạo hoặc tìm kiếm tại `/tmp/data/installed_packs.json` hoặc gây crash do quyền ghi (Permission Denied). |
| **ISSUE-04** | **P1** | Phân mảnh domain cài đặt người dùng trong QSettings | `src/ui/director/pages/source_page.py:517`<br>so với `src/ui/lux/shell/edge_dock.py:127` & `src/main.py:157` | `source_page.py` đọc/ghi `QSettings("ResolveFlow", "ResolveFlowAssistant")` cho Whisper, trong khi Dock và Main dùng `QSettings("ChunDVC", "ResolveFlow")`. | Cài đặt người dùng bị xé lẻ thành hai tệp cấu hình độc lập trên hệ điều hành, làm mất cấu hình hoặc không áp dụng được thiết lập chung. |
| **ISSUE-05** | **P1** | Xung đột biểu diễn dữ liệu Asset (`dict` vs `StudioAsset`) | `src/main.py:39-76`<br>`src/ui/tabs/tab_assets.py:6015`<br>`src/ui/lux/library/asset_model.py:36-74` | `main.py` và `StorageManager` trả về `dict`, trong khi `TabAssets` tạo `StudioAsset` (dataclass) rồi emit qua signal `library_assets_changed`. | `AssetListModel.extract_asset_info()` phải liên tục dùng duck-typing để ép kiểu. Nguy cơ lỗi lệch thuộc tính (`tab` vs `category`, `sub` vs `subcategory`, `tags` kiểu chuỗi vs list). |
| **ISSUE-06** | **P1** | Lệch chuẩn tên danh mục giữa Kho lưu trữ và Giao diện | `src/core/storage_manager.py:14-17`<br>`src/ui/lux/library/category_nav.py:18-28`<br>`src/core/pack_manager.py:35-50` | Storage Root dùng danh từ số nhiều (`memes`, `luts`, `overlays`, `templates`), UI dùng số ít (`meme`, `lut`, `overlay`, `text`/`trans`), PackManager dùng `transitions`, `titles`. | `InsertService` và `AssetFilterProxyModel` phải dùng hàng loạt lệnh kiểm tra tuple dài (`cat in ("trans", "transitions", "transition")`), dễ gây lọc sót hoặc chèn nhầm video preview của transition thành footage B-roll. |
| **ISSUE-07** | **P1** | Rút ổ đột ngột giữa chừng commit để lại thư mục mồ côi | `src/core/pack_ingest.py:187-193` | Ổ cứng ngoài bị rút hoặc app bị tắt cưỡng bức đúng lúc `os.rename(payload, folder)` vừa xong nhưng chưa kịp ghi `atomic_json(registry.json)`. | Pack đã nằm trên ổ nhưng không có trong registry; lần khởi động sau không có cơ chế tự quét phục hồi (rebuild registry from pack_manifest.json). |
| **ISSUE-08** | **P2** | Bộ nhớ đệm Thumbnail `_thumb_cache` không có cơ chế giải phóng | `src/ui/lux/library/asset_model.py:208, 232-235` | Nạp kho tài nguyên lớn (5.000–10.000 video/broll) và cuộn xem toàn bộ danh sách trên Grid. | `self._thumb_cache: Dict[str, QPixmap]` lưu giữ vĩnh viễn toàn bộ QPixmap trong RAM mà không có giới hạn số lượng (LRU Eviction), gây tốn hàng trăm MB RAM. |
| **ISSUE-09** | **P2** | Bộ đệm Visual Cache mặc định đặt tại thư mục Home `~` | `src/core/visual_index.py:35-45`<br>`docs/local-vision-runtime.md:44` | Người dùng phân tích 3 giờ video độ phân giải cao trên máy có phân vùng root `/` nhỏ. | Hàng nghìn ảnh contact sheet lưu tại `~/.resolveflow_cache/visual/frames/` có thể làm đầy phân vùng hệ thống Linux nếu không đặt biến môi trường `RESOLVEFLOW_VISUAL_CACHE_DIR`. |
| **ISSUE-10** | **P2** | Fallback nạp dữ liệu mẫu giả lập (Mockup Assets) khi quét trống | `src/ui/tabs/tab_assets.py:6028, 6030, 6032, 6034` | Khi thư mục tài nguyên cục bộ trống (`scanned_memes == []`), mã nguồn tự động gán `self.all_memes = MOCKUP_MEMES.copy()`. | Giao diện hiển thị các đạo cụ mẫu không có file thực trên đĩa; người dùng bấm xem trước hoặc chèn sẽ gặp lỗi file không tồn tại. |

---

## 2. ĐỀ XUẤT THỨ TỰ CÁC GIAI ĐOẠN G01–G09

```
┌────────────────────────────────────────────────────────────────────────┐
│ G00: Baseline Audit & Scope Finalization (Hiện tại - Đã xong)           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G01: Data Contracts & Taxonomy Adapter (Đơn vị sửa độc lập đầu tiên)    │
│ - Chuẩn hóa Hợp đồng dữ liệu Asset (Unified Asset Record)              │
│ - Thống nhất taxonomy & bộ chuyển đổi 2 chiều (Canonical <-> UI)       │
│ - Đồng nhất domain cấu hình QSettings("ChunDVC", "ResolveFlow")        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G02: Storage Root, Transactions & Ghost Path Elimination               │
│ - Xóa bỏ vĩnh viễn path data/installed_packs.json                      │
│ - Khóa chặt các luồng ghi vào ./assets/; chuyển sang Storage Root      │
│ - Cơ chế tự phục hồi registry từ pack_manifest.json sau sự cố rút ổ    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G03: Unified Ingestion Pipeline & Drag-Drop Consolidation             │
│ - Thống nhất toàn bộ luồng kéo thả vào Dock & Studio qua Ingest Wizard │
│ - Đóng lại hoàn toàn API legacy PackManager.install_pack trực tiếp    │
│ - Quản lý dung lượng đệm staging và dọn dẹp khi hủy/thoát              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G04: Unified Library UI, Search & Memory-Bounded Virtualization       │
│ - Thống nhất Model/View giữa StudioWindow và EdgeDockWindow            │
│ - Thêm giới hạn LRU Cache (tối đa 200 pixmaps) cho AssetListModel      │
│ - Loại bỏ các dữ liệu mẫu giả lập (MOCKUP_MEMES, MOCKUP_SFX)           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G05: Source Analysis Pipeline & Configurable Visual Cache              │
│ - Cho phép cấu hình Visual Cache nằm cùng ổ với Storage Root           │
│ - Đồng bộ vòng đời quét Whisper và Visual Sampling có kiểm soát tải    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G06: Story Planning & Matching Engine                                 │
│ - Chuẩn hóa CopilotDirectorPlan theo Asset IDs và Source Clip IDs thật │
│ - Prompt phân cấp có giới hạn token và bảo toàn mốc thời gian          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G07: DaVinci Resolve Execution & Resilient Bridge                     │
│ - Dựng thử trên Timeline cô lập [ResolveFlow_Test]                     │
│ - Xử lý thông báo kết quả thật, phục hồi lỗi kết nối không crash       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G08: Shell UX, Floating Bubble & Multi-surface Lifecycle               │
│ - Đảm bảo dọn dẹp tiến trình ngầm (SourceShutdownGuard)                │
│ - Đồng bộ trạng thái kết nối Resolve trên Bong bóng nổi và Thanh Dock  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ G09: Acceptance & Release Readiness                                   │
│ - Kiểm thử tích hợp trọn vẹn trên footage thật                         │
│ - Đo benchmark hiệu năng và đóng gói bản phát hành                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. PHẦN ĐẦU TIÊN CÓ THỂ SỬA ĐỘC LẬP: G01 — DATA CONTRACTS & TAXONOMY

### 1. Mục tiêu và Lý do chọn làm đầu tiên:
Hiện tại sự phân mảnh giữa `dict` vs `StudioAsset`, giữa category số nhiều (`luts`, `memes`) vs số ít (`lut`, `meme`), và sự phân mảnh trong `QSettings` (`ChunDVC` vs `ResolveFlow`) là nguyên nhân gốc rễ gây ra sự lộn xộn trong tất cả các module tiếp theo (từ Ingest, UI Model, Lọc tìm kiếm đến Chèn timeline). Sửa G01 trước sẽ tạo ra nền móng chuẩn hóa kiểu dữ liệu cho toàn bộ dự án mà **không làm xáo trộn cấu trúc lưu trữ hay can thiệp vào mã ngoại vi Resolve**.

### 2. Phạm vi tệp (File Scope):
- `src/core/schema.py`: Bổ sung định nghĩa `AssetRecord` (TypedDict hoặc dataclass chuẩn hóa với đầy đủ các trường: `id`, `name`, `category`, `subcategory`, `tags`, `file_path`, `thumbnail_path`, `preview_path`, `duration`, `availability`, `storage_managed`, `template_type`, `pack_name`).
- `src/core/storage_manager.py`: Thống nhất bảng danh mục `CATEGORIES` và cung cấp hàm adapter 2 chiều chuẩn mực:
  - `canonical_to_ui_category(cat: str) -> str`
  - `ui_to_canonical_category(cat: str) -> str`
- `src/ui/lux/library/asset_model.py`: Cập nhật `extract_asset_info()` dùng trực tiếp adapter trên; loại bỏ mã duck-typing rải rác.
- `src/ui/director/pages/source_page.py:517`: Thống nhất `QSettings("ChunDVC", "ResolveFlow")`.

### 3. API giữ tương thích ngược (Backward-Compatible API):
- Hàm `to_asset_dict(asset)` trong `src/services/insert_service.py` vẫn giữ nguyên chữ ký và trả về dict chứa cả `category` và `tab` để các caller hiện tại không bị ảnh hưởng.
- `StorageManager.ui_assets()` vẫn trả về `List[Dict[str, Any]]` tương thích hoàn toàn với `EdgeDockWindow.set_assets()` và `SearchIndex`.

### 4. Kiểm thử có sẵn cần dùng để xác minh sau khi sửa:
```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/pytest \
  tests/test_storage_ingest.py \
  tests/test_asset_filter_packs_and_collections.py \
  tests/test_tab_assets_suggestions.py \
  tests/ui/test_edge_dock.py \
  -v
```

### 5. Điều kiện kết thúc (Exit Criteria):
- Tất cả các trường của Asset nạp từ `StorageManager`, `AssetIndexer`, `TabAssets`, và `main.py` đều qua một schema kiểm chuẩn duy nhất.
- Không còn bất kỳ cảnh báo hoặc lỗi nào về lệch category số ít / số nhiều trong bộ lọc và chèn tài nguyên.
- Cấu hình QSettings toàn ứng dụng đọc/ghi thống nhất trên domain `"ChunDVC", "ResolveFlow"`.
- Toàn bộ nhóm test hồi quy trên passed 100%.

---

## 4. CẬP NHẬT TRẠNG THÁI SAU CODEX REVIEW & HOTFIX G00-H1-I

Theo văn bản kết luận của Tech Lead Codex (`docs/architecture/00-codex-review.md`) và triển khai hotfix `G00-H1-I`:

1. **Điều chỉnh phân loại và mức ưu tiên:**
   - **ISSUE-01 (P0)** & **ISSUE-02 (P0)**: Đã được giải quyết dứt điểm qua hotfix `G00-H1-I`. Cả hai đường nhập UI (kéo thả EdgeDock và import TabAssets) đã được chuyển hoàn toàn qua `PackIngestWizard` kiểm duyệt vào Storage Root, không còn ghi đè vào `./assets/` hay gọi thẳng `PackManager.install_pack`.
   - **ISSUE-03 (P0 -> P1)**: `INSTALLED_PACKS_FILE` phụ thuộc cwd được xếp vào nợ kỹ thuật tương thích (P1) thay vì P0 vì chưa có bằng chứng mất dữ liệu người dùng. Không xóa file hoặc bỏ API legacy trong hotfix; G02/G03 sẽ xử lý tương thích sau.
   - **QSettings domain (ISSUE-04)**: Cần kiểm kê keys (inventory) kèm cơ chế đọc fallback tương thích trong G01, không đổi domain hàng loạt.
   - **Data types (ISSUE-05)**: `StudioAsset` là class thông thường, không phải dataclass. Cần xác định vị trí thực sự vi phạm contract trước khi chọn schema mới.

2. **Tiến trình triển khai:**
   - Hoàn thành `G00-H1-I` (Hotfix đóng hai đường nhập UI ghi sai nơi).
   - Đang chờ Tech Lead Codex nghiệm thu hotfix để giao việc thiết kế `G01-D`.
