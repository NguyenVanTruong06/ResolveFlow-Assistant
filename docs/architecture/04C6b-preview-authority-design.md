# KIẾN TRÚC VÀ ĐẶC TẢ THẨM QUYỀN PREVIEW: G04-04C6b-D
# (Preview Proxy Cache Authority & Hover Playback Boundary)

- **TASK_ID:** `G04-04C6b-D / G04-04C6b-I / G04-04C6b-R1 — Chốt quyền preview và nguồn đường dẫn phát`
- **TRẠNG THÁI:** `IMPLEMENTED_PENDING_REVIEW` (Hoàn thành triển khai và nghiệm thu 188 tests exit 0, đóng kín 4 boundary probes theo quyết định Codex Tech Lead).
- **WORKER_LEVEL:** `HIGH`
- **Người thiết kế:** Antigravity (Worker)
- **Tech Lead:** Codex
- **Ngày lập:** 2026-10-27
- **Tài liệu nền tảng:**
  - [`docs/architecture/04C6-drag-preview-guard-design.md`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/docs/architecture/04C6-drag-preview-guard-design.md)
  - [`docs/architecture/04C6a-codex-review.md`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/docs/architecture/04C6a-codex-review.md)

---

## 1. CALLGRAPH VÀ TRUY VẾT NGUỒN ĐƯỜNG DẪN PREVIEW (CALLGRAPH & PROVENANCE TRACE)

### 1.1. Callgraph Thực Tế (File:Hàm:Dòng)

```
[User Hover trên QListView]
      │ (150ms debounce)
      ▼
src/ui/lux/library/hover_preview.py:SharedHoverPreview._on_hover_timeout (dòng 233)
      │
      ├─► idx.data(AssetObjRole) ──► src/ui/lux/library/asset_model.py:AssetListModel.data (dòng 186)
      │                                └─► Trả về info["obj"] (reference gốc dict/object)
      ├─► idx.data(PreviewPathRole) ─► src/ui/lux/library/asset_model.py:AssetListModel.data (dòng 177)
      │                                └─► Trả về info["preview_path"]
      ├─► idx.data(FilePathRole) ────► src/ui/lux/library/asset_model.py:AssetListModel.data (dòng 174)
      │                                └─► Trả về info["file_path"]
      │
      ▼ (LỖ HỔNG HIỆN TẠI: Thiếu ActionGuard, chỉ check thô sơ availability == "offline" và os.path.isfile)
      ├─► [ext in AUDIO_EXTS] ───────► SharedHoverPreview._play_audio(preview_p) (dòng 327)
      │                                  └─► QMediaPlayer.setSource(QUrl.fromLocalFile(preview_p))
      │
      └─► [ext in VIDEO_EXTS] ───────► SharedHoverPreview._play_video(idx, preview_p) (dòng 272)
                                         ├─► SharedHoverPreview._ensure_overlay() & QVideoWidget
                                         └─► QMediaPlayer.setSource(QUrl.fromLocalFile(preview_p))

[External / Inspector Direct Trigger]
      │
      ▼
src/ui/lux/library/hover_preview.py:SharedHoverPreview.preview_asset(asset) (dòng 304)
      └─► Lấy asset.get("preview_path") or asset.get("file_path") ──► QMediaPlayer.setSource(...)
```

### 1.2. Các Nhà Sản Xuất (Producers) Của `preview_path` Trên Mã Nguồn Thực Tế

| STT | Producer | Vị trí File:Dòng | Kiểu nguồn | Đường dẫn thực tế ghi nhận |
|:---:|:---|:---|:---|:---|
| 1 | `AssetIndexer.scan_transitions` | `src/core/asset_indexer.py:517–538` | `[CODE_VERIFIED]` Bundled Native Demo | `assets/templates/transitions/previews/{pack}/{stem}.mp4` hoặc `assets/templates/transitions/previews/{stem}.mp4`. File gốc setting là `file_path`. |
| 2 | `asset_cache.scan_folder_cached` | `src/core/asset_cache.py:320–345` | `[CODE_VERIFIED]` Companion File (cùng folder) | Tìm file cùng stem `{base_name}.mp4`, `.mov`, `.gif` nằm cùng thư mục cha với `file_path`. |
| 3 | `AssetListModel.extract_asset_info` | `src/ui/lux/library/asset_model.py:47, 71` | `[CODE_VERIFIED]` Model Fallback | `preview_path = str(item.get("preview_path") or file_path)`. Nếu item không có `preview_path` riêng, tự động gán bằng `file_path`. |
| 4 | `TabAssets._dict_to_studio_asset` | `src/ui/tabs/tab_assets.py:6061` | `[CODE_VERIFIED]` UI Legacy Adapter | `preview_path = str(record.get("preview_path") or file_path)`. |
| 5 | `StorageManager.assets` | `src/core/storage_manager.py:388–410` | `[CODE_VERIFIED]` Managed Records | Snapshot kho chỉ lưu `library_path`, `external_path`, `file_path`. `StorageManager` không tự sinh proxy cache độc lập ngoài các trường có sẵn trong asset record. |
| 6 | *External Proxy Cache Registry* | *Chưa tồn tại* | `[PROPOSED / UNVERIFIED]` Cache ngoài root | Đề xuất lưu cache proxy tại `~/.cache/...` hiện chưa có cơ chế ánh xạ thẩm quyền và quyền sở hữu (ownership mapping) trong codebase. |

### 1.3. Phân Biệt Dữ Liệu Thực Tế và Giả Định Rủi Ro
- `[CODE_VERIFIED]` Nguồn transition demo MP4 là file bundled đi kèm bộ cài đặt ChunDVC trong thư mục `assets/templates/transitions/previews/`.
- `[CODE_VERIFIED]` Nguồn companion preview là file nằm cùng thư mục đĩa với file master (cùng parent và cùng stem).
- `[CODE_VERIFIED]` Nguồn do kho quản lý (`source_kind == "managed"`): Khi không có proxy riêng trong kho, `preview_path` mặc định bằng `file_path` (master file trong kho).
- `[UNVERIFIED - KHOẢNG TRỐNG THẨM QUYỀN]`: Bất kỳ giả định nào cho rằng có một thư mục proxy cache ngoài root được phép phát tự do chỉ bằng phép thử `os.path.isfile(preview_p)` là một khoảng trống thẩm quyền (authority gap), dẫn đến rủi ro phát file không đúng quyền sở hữu hoặc không kiểm soát được nguồn phát.

---

## 2. CHÍNH SÁCH QUYỀN PREVIEW ĐỘC LẬP (SEPARATE PREVIEW AUTHORITY POLICY)

### 2.1. Định Đề Phân Tách Thẩm Quyền Bất Biến
$$\text{Quyền Xem Trước (Preview Authority)} \neq \text{Quyền Chèn/Kéo (Insert/Drag Authority)}$$

1. **Không Dùng Quyền Chèn/Kéo Làm Bằng Chứng Cho Preview:**
   - Việc một tài nguyên vượt qua `intent == "insert"` hay `intent == "drag"` không đồng nghĩa với việc đường dẫn `preview_path` của nó đã được thẩm định an toàn (vì preview path có thể trỏ tới file proxy khác hoặc đường dẫn không an toàn).
2. **Không Dùng Quyền Preview Làm Bằng Chứng Cho Chèn/Kéo:**
   - Việc `preview_path` hợp lệ để xem trước **TUYỆT ĐỐI KHÔNG CẤP QUYỀN** cho thao tác kéo (`drag`) hoặc chèn (`insert`) file preview đó vào DaVinci Resolve timeline!
   - Kéo thả và chèn luôn bắt buộc thẩm định `file_path` master gốc (Master Authority).
3. **Không Biến Đổi Metadata Để Vượt Rào:**
   - Tuyệt đối không thay đổi `category`, `source_kind`, hay `origin_*` của asset nhằm lách qua các quy tắc guard.

### 2.2. Quy Tắc Đối Với Các Dạng Tài Nguyên Đặc Thù
1. **Hiệu Ứng Chuyển Cảnh Native (Native Transitions):**
   - Nhận diện: `category/tab in ("trans", "transition", "transitions")` hoặc `TransitionStylePreset`.
   - Với `intent == "drag"`: Bị từ chối (`ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED`), cấm kéo file demo MP4 vào timeline.
   - Với `intent == "insert"`: Hướng dẫn thủ công (`OK_NATIVE_TRANSITION_GUIDANCE`).
   - Với `intent == "preview"`: **ĐƯỢC PHÉP PHÁT VIDEO DEMO** (`OK_BUILTIN_PREVIEW_VERIFIED` hoặc `OK_PREVIEW_VERIFIED`) nếu `preview_path` là file video demo hợp lệ nằm trong thư mục bundled của ứng dụng hoặc trong root kho được ủy quyền.
2. **Preset Sinh Chữ Nghệ Thuật (Procedural Text Presets):**
   - Nhận diện: `isinstance(raw_obj, TextStylePreset)` hoặc `category == "text"` không có file media.
   - Với `intent == "preview"`: Trả về `OK_PROCEDURAL_PREVIEW_CANVAS` (`resolved_path=""`). Tuyệt đối không sinh file video MP4 giả; việc xem trước thuộc trách nhiệm của `TextPresetPreviewCanvas` / `draw_text_preset_preview` trên delegate. `QMediaPlayer` không phát file rỗng.

---

## 3. MA TRẬN QUYẾT ĐỊNH HỮU HẠN CHO INTENT PREVIEW (FINITE DECISION MATRIX)

| # | Trường Hợp / Nguồn | Điều Kiện Thẩm Quyền | Kết Quả Guard | Result Code | Đường Dẫn Phát (`resolved_path`) |
|:---:|:---|:---|:---:|:---|:---|
| 1 | `managed` (kho Online) | `preview_path` nằm trong cùng `active_root` hợp lệ, là regular file | `allowed=True` | `OK_PREVIEW_VERIFIED` | `resolved_preview_path` |
| 2 | `managed` (kho Online) | `preview_path` rỗng/không tồn tại, nhưng `file_path` master hợp lệ trong kho | `allowed=True` | `OK_PREVIEW_VERIFIED` | `resolved_master_path` (fallback an toàn) |
| 3 | `managed` (Master mất) | `file_path` master mất trên đĩa, nhưng `preview_path` còn trong kho và hợp lệ | `allowed=True` | `OK_PREVIEW_VERIFIED` | `resolved_preview_path` (chỉ xem trước, không cấp insert) |
| 4 | `managed` (Kho Offline) | Storage offline, khác library_id hoặc root mismatch (`ERR_FOREIGN_ROOT`) | `allowed=False` | `ERR_STORAGE_OFFLINE` / `ERR_FOREIGN_ROOT` | `""` (Cấm phát dù file proxy cũ còn trên đĩa) |
| 5 | `linked` (Liên kết ngoài) | `external_path` (hoặc companion preview cùng folder) hợp lệ trên đĩa | `allowed=True` | `OK_LINKED_PREVIEW_VERIFIED` | `resolved_external_path` (độc lập root kho) |
| 6 | `builtin` / Bundled Demo | Nằm trong thư mục bundled assets của ứng dụng (`assets/...` bao gồm templates, sfx, bgm, memes, overlays) | `allowed=True` | `OK_BUILTIN_PREVIEW_VERIFIED` | `resolved_bundled_demo_path` |
| 7 | `generated` (File-backed) | File tạm/sinh mã tồn tại hợp lệ trên đĩa | `allowed=True` | `OK_GENERATED_PREVIEW_VERIFIED` | `resolved_generated_path` |
| 8 | `legacy` (Không metadata) | Không có origin, `file_path` hoặc companion file tồn tại trên đĩa | `allowed=True` | `OK_LEGACY_PREVIEW` | `resolved_legacy_path` |
| 9 | `staging` (Chưa commit) | `availability == "staging"` | `allowed=False` | `ERR_STAGING_UNPUBLISHED` | `""` |
| 10 | `origin-only` (Mất liên kết) | Có `origin_*` nhưng thiếu `source_kind`, không có bằng chứng `linked` | `allowed=False` | `ERR_ORIGIN_UNVERIFIED` | `""` (Fail-closed nhất quán với drag) |
| 11 | Traversal / Symlink Escape | Đường dẫn chứa `..` hoặc symlink trỏ ra ngoài root kho | `allowed=False` | `ERR_INVALID_PATH_TRAVERSAL` / `ERR_SYMLINK_ESCAPE` | `""` |
| 12 | Lỗi File System / Access | Không tìm thấy cả preview lẫn master, hoặc lỗi phân quyền OS | `allowed=False` | `ERR_FILE_NOT_FOUND` / `ERR_FILE_ACCESS_DENIED` | `""` |

---

## 4. QUY TẮC AN TOÀN VỀ PROXY CACHE NGOÀI ROOT VÀ FALLBACK

### 4.1. Khoảng Trống Thẩm Quyền Proxy Cache Ngoài Root (Out-of-Root Proxy Authority Gap)
- Nếu cho phép `SharedHoverPreview` phát bất kỳ file nào nằm ngoài root chỉ bằng điều kiện `os.path.isfile(preview_p)`:
  - Dẫn đến khoảng trống thẩm quyền (authority gap), cho phép phát các file ngoài quyền sở hữu của asset.
  - Xung đột vòng đời: Cache có thể bị dọn dẹp hoặc sửa đổi mà không qua kiểm soát của kho.
  - Hiện tại, hệ thống chưa có cơ chế ánh xạ thẩm quyền và quyền sở hữu (ownership mapping) cho proxy ngoài root.
- **Quy Tắc Bảo Thủ (Conservative Policy):**
  - **BẢO LƯU HOÀN TOÀN (DEFERRED):** Việc tin cậy một file proxy cache nằm ngoài root của kho được bảo lưu cho một task chuyên biệt sau khi thiết kế xong cơ chế ownership mapping. Không đồng nhất “ngoài StorageRoot” với cấm toàn bộ: linked external/companion và file-backed generated có thẩm quyền riêng theo quy tắc xác thực nguồn tương ứng.
  - Trong phạm vi lát cắt này, một đường dẫn `preview_path` **CHỈ ĐƯỢC COI LÀ CÓ THẨM QUYỀN** khi thỏa mãn một trong các ranh giới:
    1. Nằm trong cùng `norm_active_root` của kho quản lý (managed) và không vi phạm symlink escape;
    2. Nằm trong thư mục bundled assets thực của ứng dụng (`assets/...`);
    3. Cùng thư mục đĩa với `external_path` của linked asset và có cùng stem (`.mp4/.mov/.gif/.webm/.wav/.mp3/.aac`);
    4. Fallback chính là file master (`file_path` / `external_path`) đã được verify đầy đủ thẩm quyền sau metadata/identity gates.

### 4.2. Quy Tắc Fallback Sang Master File
- Nếu `preview_path` rỗng, không tồn tại hoặc bị từ chối: Guard tự động thẩm định sang `file_path` master.
- Nếu `file_path` thỏa mãn đầy đủ điều kiện thẩm quyền (tùy theo `source_kind` là managed, linked, builtin hay legacy) $\to$ Trả về `OK_PREVIEW_VERIFIED` với `resolved_path = resolved_master_file`.
- Tuyệt đối không fallback sang repository alias hoặc file ngẫu nhiên không thuộc quyền sở hữu của asset.

---

## 5. LÁT CẮT TRIỂN KHAI DUY NHẤT (RECOMMENDED SINGLE SLICE: 2 MODULES)

Chúng tôi đề xuất triển khai gói gọn trong đúng **2 production modules**:

### 5.1. Module 1: `src/core/action_guard.py`
- Mở rộng hàm `check_action_availability` chấp nhận thêm `intent == "preview"`.
- Signature và Enums giữ nguyên chuẩn kiến trúc ActionGuard:
  ```python
  ActionIntent = Literal["insert", "inspect", "drag", "preview"]

  def check_action_availability(
      asset: Any,
      intent: ActionIntent = "insert",
      storage_provider: Optional[Callable[[], Any]] = None,
      stat_fn: Optional[Callable[[str], Any]] = None,
      realpath_fn: Optional[Callable[[str], str]] = None,
      bundled_root: Optional[Union[str, Path]] = None,
  ) -> ActionGuardResult:
  ```
- **Xử lý `intent == "preview"` trong Core Guard:**
  1. Kiểm tra staging: Nếu `availability == "staging"` $\to$ Deny `ERR_STAGING_UNPUBLISHED`.
  2. Kiểm tra origin-only: Nếu có origin nhưng không có bằng chứng linked/managed $\to$ Deny `ERR_ORIGIN_UNVERIFIED`.
  3. Kiểm tra procedural text: Nếu là `TextStylePreset` $\to$ Allow `OK_PROCEDURAL_PREVIEW_CANVAS` (`resolved_path=""`).
  4. Xác định ứng viên đường dẫn:
     - Ứng viên 1: `preview_path` (nếu có).
     - Ứng viên 2: `file_path` / `path` (fallback).
  5. Thẩm định ứng viên theo `effective_source_kind`:
     - Nếu `managed`: Bắt buộc kiểm tra `active_root`, `library_id`, `safe_relative` và symlink escape.
     - Nếu `linked`: Kiểm tra `external_path` hoặc companion path tồn tại và không traversal.
     - Nếu `builtin`: Kiểm tra file tồn tại trong bundled assets.
     - Nếu `legacy`: Kiểm tra file tồn tại trên đĩa.
  6. Trả về `ActionGuardResult` chứa `allowed=True/False`, `status`, `result_code` và `resolved_path`.
- **Ranh giới:** Giữ `action_guard.py` là pure Python logic độc lập với Qt, không import QMediaPlayer, không chạy IO per-card trong data().

### 5.2. Module 2: `src/ui/lux/library/hover_preview.py`
- Tích hợp `check_action_availability(raw, intent="preview")` vào `_on_hover_timeout`:
  ```python
  raw = idx.data(AssetObjRole)
  guard_res = check_action_availability(raw, intent="preview")
  if not guard_res.allowed:
      self.stop()
      return

  if guard_res.result_code == "OK_PROCEDURAL_PREVIEW_CANVAS":
      # Delegate tự vẽ trên card, không phát qua QMediaPlayer
      return

  target_file = guard_res.resolved_path
  if not target_file:
      return

  # Phát target_file đã được Guard thẩm định
  ext = os.path.splitext(target_file)[1].lower()
  if ext in AUDIO_EXTS:
      self._play_audio(target_file)
  elif ext in VIDEO_EXTS:
      self._play_video(idx, target_file)
  ```
- Tương tự trong hàm `preview_asset(self, asset: Dict[str, Any])`:
  Thẩm định qua guard trước khi gọi `player.setSource()`.

---

## 6. KẾ HOẠCH KIỂM THỬ HỮU HẠN (FINITE ACCEPTANCE TESTS)

### 6.1. Unit Tests (`tests/core/test_action_guard_drag_preview.py`)
1. `test_preview_managed_asset_within_active_root_allowed`: Preview file trong cùng root kho hợp lệ $\to$ `OK_PREVIEW_VERIFIED`.
2. `test_preview_managed_fallback_to_master_when_preview_missing`: Preview rỗng nhưng master tồn tại trong kho $\to$ Fallback master `OK_PREVIEW_VERIFIED`.
3. `test_preview_managed_storage_offline_denied`: Kho offline $\to$ Deny `ERR_STORAGE_OFFLINE` (cấm phát dù file proxy cũ còn trên đĩa).
4. `test_preview_managed_foreign_root_mismatch_denied`: Kho đổi root $\to$ Deny `ERR_FOREIGN_ROOT`.
5. `test_preview_managed_traversal_and_symlink_escape_denied`: Đường dẫn chứa `..` hoặc symlink trỏ ra ngoài root $\to$ Deny `ERR_INVALID_PATH_TRAVERSAL` / `ERR_SYMLINK_ESCAPE`.
6. `test_preview_linked_external_path_allowed_independent_of_storage`: Nguồn linked có file ngoài hợp lệ $\to$ `OK_LINKED_PREVIEW_VERIFIED` bất kể kho online hay offline.
7. `test_preview_native_transition_demo_allowed`: Category `trans` có file demo MP4 bundled $\to$ `OK_BUILTIN_PREVIEW_VERIFIED`, nhưng `intent == "drag"` trên cùng asset vẫn bị từ chối `ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED`.
8. `test_preview_origin_only_denied`: Asset mang origin cũ nhưng thiếu `source_kind` $\to$ Deny `ERR_ORIGIN_UNVERIFIED`.
9. `test_preview_staging_asset_denied`: Asset staging $\to$ Deny `ERR_STAGING_UNPUBLISHED`.
10. `test_preview_procedural_text_returns_canvas_guidance`: `TextStylePreset` $\to$ `OK_PROCEDURAL_PREVIEW_CANVAS` với `resolved_path=""`.
11. `test_preview_input_immutability`: Gọi guard với `intent="preview"` không làm thay đổi từ điển asset đầu vào.

### 6.2. Component Tests Cho Hover Preview (`tests/ui/test_hover_preview_lifecycle.py`)
1. `test_hover_preview_denied_guard_never_inits_player_or_shows_overlay`: Khi guard trả về `allowed=False`, player không bao giờ gọi `setSource` hay `play`, overlay không bao giờ hiện.
2. `test_hover_preview_plays_verified_resolved_path`: Player nhận chính xác `res.resolved_path` từ guard, không lấy đường dẫn giả mạo.
3. `test_hover_preview_stops_immediately_when_moving_to_denied_card`: Đang phát card A, di chuột sang card B bị từ chối $\to$ Stop ngay lập tức, tắt âm thanh và ẩn overlay.
4. `test_hover_preview_native_transition_plays_demo_without_drag_authority`: Hover vào transition phát được video demo, nhưng kiểm tra drag trên cùng item đảm bảo không thể kéo.

---

## 7. CÁC CONSUMER NGOÀI LÁT CẮT VÀ 3 CÂU HỎI QUYẾT ĐỊNH

### 7.1. Các Consumer Nằm Ngoài Lát Cắt (Residuals Outside Slice)
1. `TabAssets.icon_meme_widget` (`src/ui/tabs/tab_assets.py:6739`): Trình xem trước media trong inspector tab cũ tiếp tục dùng đường dẫn trực tiếp.
2. `TabAssets.sfx_play_btn` (`src/ui/tabs/tab_assets.py:6721`): Nút nghe thử âm thanh thủ công trong inspector.
3. `MacroCard._start_live_preview` (`src/ui/lux/macro_card.py:150`): Component card tĩnh tự vẽ animation ticker riêng.

### 7.2. 3 Câu Hỏi Quyết Định Và Phê Duyệt Của Codex Tech Lead

#### Câu hỏi 1: Có cho phép QMediaPlayer phát file Master dung lượng lớn khi fallback không?
- *Bối cảnh:* Khi asset không có file proxy nén nhẹ (`preview_path`), fallback sẽ lấy trực tiếp `file_path` master.
- *Quyết định của Codex Tech Lead:* **Chấp thuận A**. Cho phép fallback master cho audio/video được hỗ trợ sau xác thực; không áp ngưỡng MB tùy tiện. Debounce 150ms và một player chỉ giảm số lượt khởi động đồng thời, không bảo đảm RAM/GPU/decoder performance của master lớn. Hiệu năng chưa đo giữ UNVERIFIED trong slice này.

#### Câu hỏi 2: Có chấp nhận đường dẫn proxy cache nằm ngoài root trong lát cắt này không?
- *Bối cảnh:* Proxy cache ngoài root (ví dụ `~/.cache/...`).
- *Quyết định của Codex Tech Lead:* **Chấp thuận B (DEFERRED)**. Proxy ngoài authority hiện có DEFERRED. Không bắt buộc SHA-256 hoặc database mới để giải quyết ngay; mapping ownership cần thiết kế riêng. Linked source/companion và file-backed generated có quyền riêng, không đồng nhất “ngoài StorageRoot” với cấm toàn bộ.

#### Câu hỏi 3: Fallback từ `preview_path` sang `file_path` master nên do Core Guard xử lý hay do UI xử lý?
- *Bối cảnh:* Cơ chế fallback đường dẫn phát khi preview rỗng hoặc thiếu.
- *Quyết định của Codex Tech Lead:* **Chấp thuận A**. Core guard chọn candidate/fallback, UI chỉ phát `resolved_path`. Chỉ fallback sau khi identity/metadata gates thành công; root/ID mismatch, staging, origin-only hay provider failure không được bị nuốt bằng fallback builtin/master.

---

## 8. KẾT QUẢ TRIỂN KHAI VÀ NGHIỆM THU (IMPLEMENTATION RESOLUTION & VERIFICATION)

### 8.1. Các Module Sản Xuất Đã Chỉnh Sửa (Đúng 2 Modules)

1. **`src/core/action_guard.py`:**
   - Hỗ trợ `intent == "preview"` trong `check_action_availability`, bổ sung tham số `bundled_root` (mặc định lấy `assets/` của ứng dụng qua path anchor của module).
   - **Xử lý bundled_root fail-closed (Probe 1):** Chỉ resolve `bundled_root` khi thực sự cần authority cho `builtin` hoặc transition demo MP4. Nếu `realpath_fn` gặp lỗi phân quyền (PermissionError/EACCES) hoặc lỗi I/O, guard lập tức trả về lỗi có cấu trúc (`ERR_FILESYSTEM_IO_OR_PERMISSION`), không bao giờ bỏ qua containment hay phát file ngoài root.
   - **Xác thực anchor linked (Probe 2):** Đối với tài nguyên `linked`, bắt buộc xác thực `external_path` (master anchor) là regular file trên đĩa qua `stat_fn` trước khi cấp quyền companion proxy. Nếu anchor mất, là thư mục hoặc lỗi I/O $\to$ từ chối ngay (`ERR_FILE_NOT_FOUND`), không để companion proxy che lấp lỗi anchor.
   - **Phân biệt candidate failure vs asset authority failure (Probe 4):** Trong `managed`, candidate `preview_path` bị lỗi ngoài root hoặc symlink escape sẽ bị từ chối; nhưng nếu các ranh giới danh tính kho (`active_root`, `library_id`, `safe_relative`) đã đạt và master file tồn tại hợp lệ trên đĩa $\to$ tự động fallback sang master file (`OK_PREVIEW_VERIFIED`). Nếu cả hai đều không hợp lệ $\to$ từ chối (bảo đảm an toàn).
   - Tách riêng nhánh preview cho native transitions: `drag` từ chối `ERR_NATIVE_TRANSITION_DRAG_UNSUPPORTED`, `insert` trả về hướng dẫn `OK_NATIVE_TRANSITION_GUIDANCE`, trong khi `preview` tiếp tục thẩm định video demo MP4 trong bundled assets.
   - Thẩm định `TextStylePreset` procedural: trả về `OK_PROCEDURAL_PREVIEW_CANVAS` với `resolved_path=""` (canvas vẽ trực tiếp trên view, không khởi tạo media player).
   - Kiểm tra `availability == "staging"` và `origin-only` fail-closed nhất quán cho cả `drag` và `preview`.

2. **`src/ui/lux/library/hover_preview.py`:**
   - **Hỗ trợ `collections.abc.Mapping` toàn diện (Probe 3):** Hàm `_normalize_preview_payload` nhận diện mọi `Mapping` (kể cả `MappingProxyType`), shallow copy đầy đủ các trường `source_kind`, `origin_library_id`, `origin_root`, `storage_managed`, `library_path`, `external_path`, runtime refs `_raw_obj` mà không làm mất provenance và không import `asset_grid`. Nhánh domain object giữ đầy đủ `relative_path`, `native_preset`.
   - Cập nhật cả `_on_hover_timeout` và `preview_asset` thẩm định quyền phát trước khi khởi tạo player hay overlay:
     ```python
     guard_res = check_action_availability(payload, intent="preview")
     if not guard_res.allowed or not guard_res.resolved_path:
         self.stop()
         return
     ```
   - Đảm bảo nếu chuyển từ card được phép sang card bị từ chối $\to$ `self.stop()` dừng phát ngay, ẩn overlay và không kích hoạt lại player.

### 8.2. Kết Quả Kiểm Thử Tập Trung

- **Bộ kiểm thử đã tạo & cập nhật:**
  - `tests/core/test_action_guard_preview.py`: 22 unit tests bao quát toàn bộ ma trận thẩm quyền preview (managed, linked, builtin, procedural canvas, staging, origin-only, traversal escape, immutability, và 3 regression probes).
  - `tests/ui/test_hover_preview_authority.py`: 7 integration tests kiểm thử `SharedHoverPreview` với mock/fake player/overlay, cô lập thumbnail scheduling và dọn dẹp Qt widget sạch sẽ (bao gồm probe MappingProxyType provenance).
  - `tests/core/test_action_guard_drag_preview.py`: Cập nhật test unsupported preview cũ thành `test_unsupported_intent_denied_in_action_guard`.
- **Lệnh thực thi duy nhất:**
  ```bash
  QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/core/test_action_guard.py tests/core/test_action_guard_drag_preview.py tests/core/test_action_guard_preview.py tests/ui/test_hover_preview_authority.py tests/ui/test_hover_preview_lifecycle.py tests/ui/test_asset_grid_drag_guard.py tests/services/test_insert_service.py -q
  ```
- **Kết quả:** **188 passed, 5 warnings in 3.05s, EXIT_STATUS: 0** (ghi nhận tại `.bridge/ux-evidence/G04-04C6b-R1/focused-tests.log`).

### 8.3. Các Phần Còn Lại Nằm Ngoài Lát Cắt (Explicit Residuals)
1. **Inspector / Legacy Preview:** Các widget preview media cũ trên `TabAssets` (`icon_meme_widget`, `sfx_play_btn`) chưa tích hợp `ActionGuard`.
2. **Proxy Cache Ngoài Root:** Proxy cache bên ngoài storage root hoặc thư mục bundled ứng dụng (ví dụ `~/.cache/...`) tiếp tục được bảo lưu (DEFERRED) do chưa có cơ chế ánh xạ ownership / digest SHA-256.
3. Không mở rộng tuyên bố rằng toàn bộ ứng dụng đã được bảo vệ hoàn toàn; lát cắt tập trung chính xác vào Action Guard core và SharedHoverPreview.

