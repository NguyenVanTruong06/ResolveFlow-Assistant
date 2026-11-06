# THIẾT KẾ KIẾN TRÚC G04-04A2-D: CHỐT RECEIVER STUDIO VÀ LUỒNG ĐỒNG BỘ THƯ VIỆN

- **Mã định danh:** `G04-04A2-D`
- **Trạng thái thẩm định:** `ACCEPTED_WITH_BINDING_CORRECTIONS` (Xem `docs/architecture/04A2-codex-decisions.md`)
- **Cấp độ Worker:** `HIGH`
- **Tác giả:** Antigravity Worker
- **Tech Lead thẩm định:** Codex Tech Lead
- **Ngày lập:** 2026-10-26 (Đã cập nhật theo quyết định Codex)

---

## 1. CALLGRAPH ĐỐI CHIẾU MÃ NGUỒN THỰC TẾ (FILE:HÀM:DÒNG)

Tất cả các dòng mã được đối chiếu trực tiếp từ mã nguồn thực tế tại thời điểm hiện tại:

```
[CODE_VERIFIED]  Mã nguồn hiện có đang chạy trong dự án
[PROPOSED]       Đặc tả thay đổi/bổ sung được đề xuất trong thiết kế
[UNVERIFIED]     Logic phán đoán chưa qua đo kiểm thực tế
```

### 1.1. Luồng nạp thư viện và khởi tạo cửa sổ tại `src/main.py`
- `load_all_library_assets()` [`src/main.py:39–76`][CODE_VERIFIED]:
  - Dòng 42–43: Ghép danh sách mẫu tĩnh `BUILTIN_PRESETS` (Text) và `BUILTIN_TRANSITIONS`.
  - Dòng 46–70: Quét tài nguyên local tĩnh qua `AssetIndexer()`: memes (L47), sfx (L51), luts (L55), overlays (L59), titles (L63), transitions (L67).
  - Dòng 74–76: Gọi `StorageManager().ui_assets()` để nạp tài nguyên lưu trữ ngoài đĩa vào danh sách phẳng.
  - Trả về danh sách `all_assets: List[Union[dict, TextStylePreset, TransitionStylePreset]]`.
- `create_studio_manager(dock, insert_service, search_index)` [`src/main.py:79–107`][CODE_VERIFIED]:
  - Dòng 81: Khởi tạo biến đóng `studio_window = None` (lazy init).
  - Dòng 90–93: Hàm nhận callback `on_studio_assets_updated(new_assets)`:
    - Gọi `dock.set_assets(new_assets)`
    - Gọi `search_index.set_assets(new_assets)`
    - Gọi `dock.set_search_index(search_index)`
  - Dòng 98–101: Khi `open_studio()` được gọi lần đầu:
    - Tạo `studio_window = StudioWindow(insert_service=insert_service)`
    - Kết nối `studio_window.assets_updated.connect(on_studio_assets_updated)` (CHỈ CÓ MỘT CHIỀU: Studio $\to$ Dock!)
  - **Hiện trạng:** Hoàn toàn KHÔNG có chiều ngược lại từ `dock` hay `storage_timer` báo sang `studio_window`.
- `storage_timer` & `refresh_storage()` [`src/main.py:142–154`][CODE_VERIFIED]:
  - Dòng 142–149: Đọc `StorageManager().meta / "registry.json"` kiểm tra `stamp` và `sm.online`, tạo `signature = (sm._config.get("root"), sm.online, stamp)`.
  - Dòng 150–153: Lọc `assets = [a for a in dock._all_assets if not managed(a)] + sm.ui_assets()`, cập nhật `dock.set_assets(assets)` và `search_index.set_assets(assets)`.
  - Dòng 154: `storage_timer.timeout.connect(refresh_storage); storage_timer.start(2500)`.
  - **Khiếm khuyết:** Vòng lặp timer này chỉ làm mới cho `dock`, `studio_window` (dù đang mở) hoàn toàn bị bỏ quên và rơi vào trạng thái cũ (stale).

### 1.2. Luồng nạp và làm mới tại `src/ui/lux/shell/edge_dock.py`
- `EdgeDockWindow.set_assets(assets)` [`src/ui/lux/shell/edge_dock.py:557–614`][CODE_VERIFIED]:
  - Cập nhật `self._all_assets = list(assets)` (L559), `self.asset_model.set_assets(assets)` (L560), `self.palette.set_assets(assets)` (L561).
  - Tái tạo danh mục qua `_build_effective_categories(profile)` (L568) bảo toàn custom taxonomy và labels tiếng Việt.
  - Đồng bộ `category_nav` và `proxy_model`, kiểm tra `valid_subs` để reset sub mất về `"Tất cả"` / `"all"` (L580–603).
- `EdgeDockWindow._on_model_filter_changed()` [`src/ui/lux/shell/edge_dock.py:615–636`][CODE_VERIFIED]:
  - Kiểm tra `self._selected_asset`: nếu asset đang chọn bị lọc mất khỏi danh sách hiển thị, gán `self._selected_asset = None`, xóa preview khỏi `action_bar`, đóng `sheet`. Không can thiệp nếu asset vẫn còn hiển thị.
- `EdgeDockWindow._run_ingest_wizard()` [`src/ui/lux/shell/edge_dock.py:1520–1585`][CODE_VERIFIED]:
  - Dòng 1568: Khi nhập pack thành công, gọi `self.refresh_storage_assets()`.
- `EdgeDockWindow.refresh_storage_assets()` [`src/ui/lux/shell/edge_dock.py:1606–1618`][CODE_VERIFIED]:
  - Lọc bỏ asset managed cũ trong `_all_assets`, nạp lại `sm.ui_assets()`, cập nhật `search_index`, gọi `self.set_assets(assets)`, cập nhật proxy và palette.
  - **Hiện trạng:** Không phát ra bất kỳ tín hiệu nào để báo cho Studio biết kho đã được cập nhật.

### 1.3. Luồng nạp và quản lý tài nguyên tại `src/ui/studio/studio_window.py`
- `StudioWindow.__init__()` [`src/ui/studio/studio_window.py:177–205`][CODE_VERIFIED]:
  - Dòng 175: Khai báo tín hiệu `assets_updated = pyqtSignal(list)`.
  - Dòng 188–189: Khởi tạo `self.tab_assets = TabAssets(self)`, nối `self.tab_assets.library_assets_changed.connect(self.assets_updated.emit)`.
- `StudioWindow._on_sync_dock_clicked()` [`src/ui/studio/studio_window.py:469–475`][CODE_VERIFIED]:
  - Dòng 470–471: Gọi `self.tab_assets._rescan_assets()`.
  - Dòng 472: Gọi `self._notify_assets_changed()` $\to$ phát `self.assets_updated.emit(assets)`.
  - **Hiện tượng phát lặp:** Do dòng 189 đã nối `library_assets_changed` sang `assets_updated.emit`, việc gọi `_notify_assets_changed()` khiến `assets_updated` bị phát 2 lần liên tiếp.

### 1.4. Chi tiết khởi tạo và nạp dữ liệu tại `src/ui/tabs/tab_assets.py`
- Khởi tạo danh sách tài nguyên [`src/ui/tabs/tab_assets.py:3230–3316`][CODE_VERIFIED]:
  - Dòng 3254–3295: Quét và giữ các danh sách tài nguyên tĩnh độc lập:
    - `self.all_presets`: Text presets built-in + quét từ đĩa
    - `self.all_transitions`: Transition built-in + quét từ đĩa
    - `self.all_luts`: Mockup LUTs + quét từ đĩa
    - `self.all_icons`: Mockup Stickers/Icons
    - `self.all_overlays`: Overlays quét từ đĩa + mockup
    - `self.all_memes`: Memes quét từ đĩa + mockup
    - `self.all_sfx`: SFX quét từ đĩa + mockup
    - `self.all_bgm`: Nhạc nền quét từ đĩa
    - `self.local_assets`: Tài nguyên người dùng nạp thủ công
  - Dòng 3299–3307: Ghép toàn bộ 8 danh sách trên vào `self.all_assets`.
  - Dòng 3308: Gọi `self._merge_storage_assets()`.
  - Dòng 3316: Khởi tạo `self.suggester = ContextualSuggester(assets=self.all_assets)`.
  - Dòng 3853–3857: Khởi tạo kiến trúc Model/View ảo hóa:
    - `self.asset_model = AssetListModel(self.all_assets, favs=self.favorites)`
    - `self.search_index = SearchIndex(self.all_assets)`
    - `self.asset_filter = AssetFilterProxyModel(self.search_index)`
    - `self.asset_filter.setSourceModel(self.asset_model)`
    - `self.asset_grid.setModel(self.asset_filter)`
- `_merge_storage_assets()` [`src/ui/tabs/tab_assets.py:6035–6046`][CODE_VERIFIED]:
  - Đọc `StorageManager().ui_assets()`, chuyển từng dict thành `StudioAsset` và append vào `self.all_assets`.
- `_rescan_assets()` [`src/ui/tabs/tab_assets.py:6048–6114`][CODE_VERIFIED]:
  - Dòng 6049: `AssetIndexer.clear_cache()`
  - Dòng 6050–6092: Quét lại toàn bộ đĩa cứng cho 8 loại tài nguyên bundled (tiêu tốn nhiều I/O).
  - Dòng 6093–6102: Khởi tạo lại `self.all_assets`, append lại 8 danh sách và gọi `self._merge_storage_assets()`.
  - Dòng 6103: Gọi `self._sync_storage_rail()`.
  - Dòng 6106: Gọi `self.suggester.set_assets(self.all_assets)`.
  - Dòng 6109: Gọi `self._populate_cards()` (cập nhật `asset_model` và `search_index`).
  - Dòng 6110: Gọi `self._rebuild_categories_column()`.
  - Dòng 6111: Gọi `self._refresh_grid()`.
  - Dòng 6112: Phát `self.library_assets_changed.emit(self.all_assets)`.

---

## 2. ĐẶC TẢ RECEIVER NHẸ TẠI STUDIO / TABASSETS

### 2.1. Chữ ký và cấu trúc đầu vào (Input Shape) [CODE_VERIFIED / IMPLEMENTED]

```python
def apply_storage_snapshot(
    self,
    managed_assets: Optional[List[Dict[str, Any]]] = None,
    taxonomy: Optional[Dict[str, str]] = None,
    generation_sig: Optional[Tuple[Any, ...]] = None
) -> None:
    """
    Cập nhật snapshot tài nguyên kho ngoài mà KHÔNG quét lại đĩa cứng (zero disk re-scan).
    
    Tham số:
    - managed_assets: Danh sách dict bản ghi đã qua to_ui_asset (từ StorageManager.ui_assets()).
                      Nếu None: tự động gọi StorageManager().ui_assets().
                      Nếu []: xóa toàn bộ managed assets mà không xóa bundled/local.
    - taxonomy: Bảng nhãn danh mục {id: label_vn} (từ StorageManager.taxonomy()).
                Nếu None: tự động gọi StorageManager().taxonomy().
    - generation_sig: Chữ ký phiên bản advisory (root, online, stamp).
    """
```

### 2.2. Quyền sở hữu và Bảo toàn dữ liệu (Ownership & Data Preservation)
1. **Bảo toàn 8 danh mục Bundled/Local:**
   - Giữ nguyên 100% các danh sách:
     `self.all_presets`, `self.all_transitions`, `self.all_luts`, `self.all_icons`,
     `self.all_overlays`, `self.all_memes`, `self.all_sfx`, `self.all_bgm`, `self.local_assets`.
2. **Tách và Hợp nhất `self.all_assets`:**
   - Trích xuất danh sách tài nguyên tĩnh cơ sở (không bị storage quản lý):
     ```python
     base_assets = [
         a for a in self.all_assets 
         if not getattr(a, "storage_managed", False) and not (isinstance(a, dict) and a.get("storage_managed", False))
     ]
     ```
   - Chuyển đổi toàn bộ `managed_assets` qua adapter `self._dict_to_studio_asset(record)`.
3. **Bảo toàn tham chiếu Runtime (Zero Deepcopy):**
   - Không gọi `copy.deepcopy()` lên các đối tượng chứa runtime context Resolve hoặc PySide6 handles.
   - Các thuộc tính `_raw_obj`, `native_preset` được gán trực tiếp theo tham chiếu danh tính (identity).

### 2.3. Adapter chuyển đổi: `_dict_to_studio_asset(record)` [CODE_VERIFIED / IMPLEMENTED]
Khắc phục triệt để lỗi mất dữ liệu metadata của `_merge_storage_assets()` cũ:

```python
def _dict_to_studio_asset(self, record: Dict[str, Any]) -> StudioAsset:
    """Chuyển đổi UI asset dict sang StudioAsset đầy đủ metadata hiển thị và xem trước."""
    tab = str(record.get("tab") or record.get("category") or "broll").strip().lower()
    category = str(record.get("category") or tab)
    
    # 1. Khôi phục sub chuẩn (ưu tiên subcategory -> sub -> 'Tất cả')
    raw_sub = record.get("subcategory")
    if not isinstance(raw_sub, str) or not raw_sub.strip():
        raw_sub = record.get("sub")
    if not isinstance(raw_sub, str) or not raw_sub.strip():
        raw_sub = "Tất cả"

    pack_name = str(record.get("pack_name") or "")

    # 2. Xử lý duration an toàn: không tạo 3.0s giả cho media chưa biết
    raw_dur = record.get("duration_sec", record.get("duration"))
    duration_sec = None
    display_dur = 0.0
    if raw_dur is not None and not isinstance(raw_dur, bool):
        try:
            dur_f = float(raw_dur)
            if math.isfinite(dur_f) and dur_f > 0.0:
                duration_sec = dur_f
                display_dur = dur_f
        except (ValueError, TypeError, OverflowError):
            pass

    file_path = str(record.get("file_path") or "")
    thumbnail_path = str(record.get("thumbnail_path") or "")
    preview_path = str(record.get("preview_path") or file_path)

    asset = StudioAsset(
        id=str(record.get("id") or ""),
        name=str(record.get("name") or record.get("id") or ""),
        tab=tab,
        category=category,
        sub=raw_sub,
        badge_icon=badge_icon,
        file_path=file_path,
        thumbnail_path=thumbnail_path,
        preview_path=preview_path,
        pack_name=pack_name,
        duration=display_dur,
    )
    
    # BẮT BUỘC: Khôi phục sub gốc vì StudioAsset.__init__ tự ý gán sub = pack_name nếu có pack!
    asset.sub = raw_sub
    asset.duration_sec = duration_sec

    # Tags độc lập, không chia sẻ mutable list
    raw_tags = record.get("tags")
    asset.tags = list(raw_tags) if isinstance(raw_tags, (list, tuple, set)) else []

    asset.availability = str(record.get("availability") or "online")
    asset.storage_managed = True
    asset.pack_id = record.get("pack_id")
    asset.source_kind = record.get("source_kind")
    asset.source_id = record.get("source_id")
    asset.template_type = record.get("template_type")
    asset.size_bytes = int(record.get("size_bytes") or 0) if isinstance(record.get("size_bytes"), int) else 0

    if "_raw_obj" in record:
        asset._raw_obj = record["_raw_obj"]
    if "native_preset" in record:
        asset.native_preset = record["native_preset"]
        
    return asset
```

---

## 3. LUỒNG ĐỒNG BỘ & SƠ ĐỒ SEQUENCE DIAGRAM

### 3.1. Lựa chọn kiến trúc: Direct Owner Snapshot vs Event Bus
- **Quyết định Codex:** Không xây dựng `library_bus.py` cồng kềnh. Hệ thống chỉ gồm hai bề mặt giao diện: `EdgeDockWindow` và `StudioWindow`.
- **Giải pháp:** Sử dụng mô hình **Notification/Invalidation qua Owner Coordinator** tại `create_studio_manager` trong `src/main.py`:
  - `main.py` sở hữu cả `dock` và `studio_window`.
  - Phân tách 2 giai đoạn:
    1. **G04-04A2a-I / R1 (Đã nghiệm thu):** Hoàn tất receiver nội bộ tại `TabAssets`, bảo toàn metadata, zero rescan, selection update/deselect, reject va chạm ID và bảo toàn Dock all-search.
    2. **G04-04A2b-I (Đã triển khai):** Nối dây tín hiệu hai chiều giữa Dock ↔ Studio và vòng lặp `storage_timer` polling.

### 3.2. Callgraph luồng đồng bộ hai chiều thực tế (Implemented)

```
[Luồng 1: Storage Poll hoặc Pack Ingest trên Dock]
Storage Timer / Ingest Wizard
   │
   ▼
EdgeDockWindow.refresh_storage_assets()
   │  ├── Đọc sm.ui_assets() và sm.taxonomy()
   │  ├── set_assets(assets) [Cập nhật list model, palette, category_nav]
   │  ├── proxy_model.set_search_index()
   │  └── storage_assets_changed.emit(managed_records, taxonomy_dict)
   │
   ▼
create_studio_manager.<closure>.on_dock_storage_assets_changed(managed_assets, taxonomy)
   │
   └── Nếu studio_window is not None:
          │
          ▼
       StudioWindow.apply_storage_snapshot(managed_assets, taxonomy)
          │
          ▼
       TabAssets.apply_storage_snapshot(managed_assets, taxonomy)
          [Zero disk re-scan, zero emission library_assets_changed -> Không echo!]

[Luồng 2: Người dùng bấm Đồng bộ trên StudioWindow]
StudioWindow._on_sync_dock_clicked()
   │
   ├── TabAssets._rescan_assets()
   │      └── library_assets_changed.emit(all_assets)
   │             │
   │             ▼
   ├── StudioWindow.assets_updated.emit(all_assets) [Phát đúng 1 lần!]
   │      │
   │      ▼
   └── create_studio_manager.<closure>.on_studio_assets_updated(new_assets)
          │
          ├── dock.set_assets(new_assets) [Thụ động, KHÔNG phát storage_assets_changed!]
          └── search_index.set_assets(new_assets)
```

---

## 4. CƠ CHẾ CHỐNG ECHO, REENTRANCY VÀ INVALIDATION

### 4.1. Chặn vòng lặp phản hồi (Echo Loop)
- Hàm receiver `apply_storage_snapshot()` của `TabAssets` **TUYỆT ĐỐI KHÔNG** phát tín hiệu `library_assets_changed`.
- `EdgeDockWindow.set_assets()` là hàm nạp thụ động, **TUYỆT ĐỐI KHÔNG** phát `storage_assets_changed` (chỉ hàm chủ động `refresh_storage_assets` mới phát).
- Trong `StudioWindow._on_sync_dock_clicked()`, đã loại bỏ lời gọi `_notify_assets_changed()` thừa, đảm bảo `assets_updated` chỉ phát đúng 1 lần duy nhất từ signal `library_assets_changed`.

### 4.2. Chữ ký thăm dò trạng thái kho (Stat Polling Signature)
- Hàm thuần `_compute_storage_poll_signature(sm)` tại `src/main.py` đọc các thông số filesystem qua stat (zero IO nặng):
  `(root, library_id, online, marker_stat, reg_stat, tax_stat, snapshot_stat)`.
- Phát hiện đầy đủ: offline/reconnect, root switch, thay đổi registry, và taxonomy-only update.
- **Giới hạn kỹ thuật:** Stat polling chỉ là cơ chế advisory phát hiện nhẹ nhàng nền, không phải là transaction authority hay crash-proof log.
- Nếu `refresh_storage_assets()` ném ngoại lệ, signature checkpoint không được advance để đảm bảo tick tiếp theo có thể retry.

### 4.3. Quy tắc Invalidation các thành phần phụ trợ
1. **`SearchIndex` & `AssetFilterProxyModel`:**
   - Khởi tạo lại `self.search_index = SearchIndex(self.all_assets)`.
   - Gán `self.asset_filter.set_search_index(self.search_index)`.
   - Giữ nguyên search query đang gõ trên `self.txt_search`.
2. **`ContextualSuggester`:**
   - Gọi `self.suggester.set_assets(self.all_assets)`.
3. **`AssetListModel`:**
   - Gọi `self.asset_model.set_assets(self.all_assets)`.
4. **Thanh phân loại bên trái (`self.rail` / `_sync_storage_rail`):**
   - Đọc taxonomy mới, không tạo trùng nút cho category đã có.
5. **Cột phân loại danh mục con (`_rebuild_categories_column`):**
   - Làm mới danh sách danh mục con.
   - Nếu `current_sub_cat` không còn trong tập sub hợp lệ của tab hiện tại, reset về `"all"`.
6. **Xử lý lựa chọn hiện tại (`selected_asset` & Inspector):**
   - Lưu `selected_id` trước khi reset model.
   - Tìm kiếm asset mới: nếu còn hiển thị trên proxy model, cập nhật tham chiếu đối tượng mới và cập nhật Inspector.
   - Nếu asset bị xóa khỏi kho hoặc bị filter che khuất: gán `self.selected_asset = None`, dọn sạch Inspector, **KHÔNG** tự ý fallback chọn clip đầu tiên.

---

## 5. BẢNG FINITE ACCEPTANCE TESTS (KIỂM THỬ HỮU HẠN ĐÃ ĐẠT)

Tất cả các bài kiểm thử quy định đã đạt 100% Green trên môi trường offscreen (`QT_QPA_PLATFORM=offscreen`):

| Nhóm Kiểm Thử | File Test | Số lượng Test | Trạng thái |
|---|---|---|---|
| Wiring hai chiều Dock ↔ Studio | `tests/ui/test_studio_dock_sync.py` | 12/12 | **PASSED** |
| Receiver managed snapshot | `tests/ui/test_studio_storage_snapshot.py` | 15/15 | **PASSED** |
| Feedback bề mặt gói | `tests/ui/test_pack_surface_feedback.py` | 10/10 | **PASSED** |
| Feedback kết quả gói | `tests/ui/test_pack_result_feedback.py` | 7/7 | **PASSED** |
| Ingest Wizard & Dock Search | `tests/ui/test_pack_ingest_wizard.py` | 14/14 | **PASSED** |
| Profile & Taxonomy Dock | `tests/ui/test_dock_profile_taxonomy.py` | 12/12 | **PASSED** |
| Edge Dock Shell | `tests/ui/test_edge_dock.py` | 14/14 | **PASSED** |

---

## 6. LÁT CẮT THỰC THI (IMPLEMENTATION SLICES)

- **Lát cắt G04-04A2a-I:** `Receiver managed snapshot tại TabAssets` (Hoàn thành trên `src/ui/tabs/tab_assets.py`).
- **Lát cắt G04-04A2a-R1:** `Dock all-search & receiver ID validation` (Hoàn thành trên `src/ui/lux/shell/edge_dock.py` và `src/ui/tabs/tab_assets.py`; 62/62 tests PASSED, đã được Codex nghiệm thu).
- **Lát cắt G04-04A2b-I:** `Wiring managed storage Dock ↔ Studio` (Hoàn thành trên `src/ui/lux/shell/edge_dock.py`, `src/ui/studio/studio_window.py`, và `src/main.py`; 40/40 tests nhóm quy định PASSED, 80/80 tests toàn diện PASSED, đã được Codex nghiệm thu).
- **Lát cắt G04-04A2b-R1:** `Studio retry tại Qt slot boundary` (Hoàn thành trên `src/main.py`; 43/43 tests PASSED, đã được Codex nghiệm thu).
- **Lát cắt G04-04A2b-R2:** `Poll theo Storage Root cấu hình hiện tại` (Hoàn thành trên `src/main.py` với `sm=None` instantiate fresh `StorageManager()` mỗi tick; 44/44 tests PASSED, exit 0).
- **Trạng thái:** Dừng chờ Tech Lead Codex nghiệm thu lát cắt G04-04A2b-R2. Chưa desktopverified. Không tuyên bố toàn bộ G04 đã accepted.

## Codex review R2
CODE ACCEPTED trong scope receiver/wiring/retry/fresh-config polling; Codex44focusedtestsPASS40.64s exit0. Desktop WAIT_USER_TEST tại04A-desktop-acceptance.md; chưa nghiệm thu toànG04 hoặc hardware/Resolve. Không còn yêu cầu sửa code đang active.
