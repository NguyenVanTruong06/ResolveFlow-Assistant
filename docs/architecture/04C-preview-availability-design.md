# THIẾT KẾ KIẾN TRÚC G04-04C-D: PREVIEW LIFECYCLE, TÍNH KHẢ DỤNG (AVAILABILITY) VÀ BẢO VỆ OFFLINE

- **Mã định danh:** `G04-04C-D`
- **Trạng thái:** `IMPLEMENTED_PENDING_CODEX_REVIEW` (Lát cắt G04-04C1-I đã triển khai và đạt 36/36 tests PASS; dynamic guards deferred)
- **Cấp độ Worker:** `HIGH`
- **Tác giả:** Antigravity (Worker)
- **Tech Lead thẩm định:** Codex Tech Lead
- **Ngày lập:** 2026-10-26
- **Mục tiêu:** Thiết kế toàn diện vòng đời preview (Audio/Video), chuẩn hóa cơ chế kiểm tra tính khả dụng (online/offline/missing), định nghĩa chính sách tương tác thụ động khi click Suggestion Chip, và thiết lập cơ chế bảo vệ an toàn khi kho media bị ngắt kết nối.

---

## 1. KHẢO SÁT VÀ CALLGRAPH ĐỐI CHIẾU MÃ NGUỒN THỰC TẾ (FILE:HÀM:DÒNG)

Tất cả các dòng mã được đối chiếu trực tiếp từ mã nguồn thực tế tại thời điểm hiện tại:

```
[CODE_VERIFIED]  Mã nguồn hiện có đang chạy trong dự án
[PROPOSED]       Đặc tả thay đổi/bổ sung được đề xuất trong thiết kế
[UNVERIFIED]     Logic phán đoán chưa qua đo kiểm thực tế
```

### 1.1. Tầng Lưới Ảo Hóa & Kéo Thả (AssetGridView & Drag & Drop)
- `AssetGridView` [`src/ui/lux/library/asset_grid.py:112–320`][CODE_VERIFIED]:
  - Dòng 126–127: Dùng `AssetCardDelegate` vẽ bằng QPainter (thuần Virtual Model/View, không dùng Card QWidget).
  - Dòng 154: Khởi tạo duy nhất một `SharedHoverPreview(self)`.
  - Dòng 203–216 (`_on_double_clicked`): Nhận double click $\to$ gọi `self._insert_service.insert(asset, mode="auto")`.
  - Dòng 269–320 (`mouseMoveEvent` & `startDrag`): Khi kéo chuột quá ngưỡng `startDragDistance()`:
    - Gọi `create_asset_mime_data(asset, sample_text=...)` [`dòng 295`].
    - Tạo `QDrag`, gán `mime_data`, vẽ pixmap ghost chữ nhật bo góc bán trong suốt [`dòng 301–312`].
    - Phát tín hiệu `drag_started`, thực thi `drag.exec(Qt.DropAction.CopyAction)`, phát `drag_ended`.
- `create_asset_mime_data(target, sample_text)` [`src/ui/lux/library/asset_grid.py:36–110`][CODE_VERIFIED]:
  - Dòng 42–43: Kiểm tra `target.get("availability") == "offline" \to` trả về `QMimeData` rỗng (hủy drag an toàn).
  - Dòng 47–48: Kiểm tra `managed and (not file_path or not os.path.isfile(file_path)) \to` trả về `QMimeData` rỗng.
  - Dòng 54–58: Nếu file tồn tại trên đĩa (wav, mp4, cube, setting...) $\to$ gán `setUrls([QUrl.fromLocalFile(...)])` và `setText(abs_p)`.
  - Dòng 61–82: Tab text/title $\to$ sinh file `.setting` tạm thời qua `FusionSettingGenerator.export_setting_file` trong thư mục tmp.
  - Dòng 87–106: Tab trans $\to$ sinh macro `.setting` tạm thời qua `TransitionMacroGenerator.export_setting_file`.

### 1.2. Tầng Preview Rê Chuột (SharedHoverPreview & Preview Asset)
- `HoverPreviewOverlay` [`src/ui/lux/library/hover_preview.py:28–57`][CODE_VERIFIED]:
  - QFrame nổi không viền viền tím `#8b5cf6`, `WA_TransparentForMouseEvents = True`.
  - Giữ duy nhất một instance `QVideoWidget` (`_video_widget`).
- `SharedHoverPreview` [`src/ui/lux/library/hover_preview.py:58–272`][CODE_VERIFIED]:
  - Tái sử dụng **duy nhất 1** `QMediaPlayer` (`_player`) và **1** `QAudioOutput` (`_audio_output`) cho toàn bộ lưới virtual grid.
  - Dòng 96–110 (`_ensure_player`): Khởi tạo lười (lazy initialization). Tự động đăng ký `QAudioOutput` với `get_audio_router().register(self._audio_output)` để quản lý định tuyến âm thanh và hotplug.
  - Dòng 77–80: Bộ đếm thời gian debounce 150ms (`_timer`). Dừng ngay lập tức khi cuộn trang (`verticalScrollBar().valueChanged`) hoặc khi rời chuột (`eventFilter` nhận `Leave`).
  - Dòng 157–194 (`_on_hover_timeout`):
    - Đọc `availability`: nếu `"offline"` $\to$ thoát ngay không preview.
    - Tìm đường dẫn preview: ưu tiên `PreviewPathRole`, fallback sang `FilePathRole`. Kiểm tra `os.path.isfile(preview_p)`.
    - Định dạng âm thanh (`.wav`, `.mp3`...): gọi `_play_audio(preview_p)` nếu `_enable_audio_preview == True`.
    - Định dạng video (`.mp4`, `.mov`...): gọi `_play_video(idx, preview_p)`.
  - Dòng 226–246 (`preview_asset(self, asset)`): **Public API** nhận dict asset. Kiểm tra `availability != "offline"` và `os.path.isfile(preview_p)` trước khi phát audio/video.
  - Dòng 262–272 (`stop()`): Dừng player, dừng timer, ẩn overlay video, reset `_hovered_row = -1`.

### 1.3. Tầng Quản Lý Định Tuyến Âm Thanh (AudioRouter)
- `AudioDeviceRouter` [`src/services/audio_router.py:22–146`][CODE_VERIFIED]:
  - Quản lý tập trung toàn bộ các đối tượng `QAudioOutput` trong ứng dụng bằng `weakref.WeakSet`.
  - Theo dõi sự kiện `QMediaDevices.audioOutputsChanged`.
  - **Cơ chế Hotplug Guard [CODE_VERIFIED]:**
    - Khi mất thiết bị âm thanh hệ thống (`default_dev.isNull()`): chuyển `_has_device = False`, lưu trạng thái mute gốc và ép mute an toàn toàn bộ các player để ngăn chặn crash tầng C++/ALSA/PulseAudio.
    - Khi kết nối lại thiết bị (cắm tai nghe/loa ngoài): khôi phục thiết bị mặc định mới và trả lại trạng thái mute/volume cũ của người dùng.

### 1.4. Tầng Thanh Thao Tác Nổi & Thanh Dock (Dock Shell & Action Bar)
- `DockActionBar` [`src/ui/lux/library/action_bar.py:13–265`][CODE_VERIFIED]:
  - Dòng 164–200 (`set_asset(asset, is_fav, timecode)`):
    - Hiển thị tên, thời lượng, gợi ý track mục tiêu: `track video trống` (video/text), `track audio trống` (sfx/bgm), `clip tại playhead` (lut), `kéo thả vào điểm cắt` (trans).
    - **Điểm khuyết thiếu hiện tại [CODE_VERIFIED GAP]:** Hiện tại `set_asset` luôn gọi `btn_primary.setEnabled(True)` và `btn_tune.setEnabled(True)` mà **chưa kiểm tra** cờ `availability == "offline"`. Nếu asset bị offline, nút Chèn vẫn sáng.
- `SheetInspector` [`src/ui/lux/inspector/inspector_host.py:19–283`][CODE_VERIFIED]:
  - Dòng 143–173 (`open_sheet(asset)`): Dựng giao diện chỉnh sửa thông số qua `_build_editors(asset)` và chạy animation trượt mở.
  - Dòng 236–264 (`_build_editors`): Nạp `TextPresetEditor`, `AudioEditor`, `LutEditor` hoặc `MacroEditor`.
  - **Khảo sát an toàn:** Trong toàn bộ `SheetInspector` và `AudioEditor`, **KHÔNG** có bất kỳ lệnh gọi phát media tự động nào khi mở sheet.
- `EdgeDockWindow._on_suggested_asset(asset_id)` [`src/ui/lux/shell/edge_dock.py:1227–1296`][CODE_VERIFIED]:
  - Dòng 1230–1236: Tra cứu `source_idx = self.asset_model.index_of_asset_id(asset_id)`. Nếu không tồn tại $\to$ cảnh báo Toast `Tài nguyên '{asset_id}' không còn tồn tại trong kho` và dừng.
  - Dòng 1248–1269: Chuyển danh mục (`select_category`), dọn dẹp search header (`clear_search` - lưu ý `header.search_text` là `@property str` [CODE_VERIFIED]), reset sub/tag/pack/filter_mode về trạng thái hiển thị đầy đủ.
  - Dòng 1271–1283: Ánh xạ `proxy_idx = self.proxy_model.mapFromSource(source_idx)`. Nếu hợp lệ $\to$ chọn và cuộn tới ô trong lưới (`setCurrentIndex`, `scrollTo`), mở `SheetInspector` và cập nhật `DockActionBar`.
  - **ĐIỂM LỆCH KỸ THUẬT RÕ RÀNG (ROGUE AUTO-PLAY) [CODE_VERIFIED DEFECT]:**
    - Dòng 1284–1288:
      ```python
      # 5. Trigger audio preview if applicable
      cat_lower = (ad.get("category") or "").lower()
      if cat_lower in ("sfx", "audio", "bgm", "sound") or (ad.get("preview_path") and str(ad["preview_path"]).endswith((".mp3", ".wav", ".aac", ".m4a"))):
          if hasattr(self, "grid") and hasattr(self.grid, "hover_controller") and self.grid.hover_controller:
              self.grid.hover_controller.preview_asset(ad)
      ```
    - Khối code này tự động phát âm thanh khi người dùng bấm suggestion chip, vi phạm triết lý giao diện thụ động.
- `EdgeDockWindow._do_insert(asset, params)` [`src/ui/lux/shell/edge_dock.py:1425–1469`][CODE_VERIFIED]:
  - Gọi `self._insert_service.insert()`. Nhận `InsertResult` và cập nhật thông báo kết quả lên `action_bar.lbl_info`.

### 1.5. Tầng Studio Window & Kho Đạo Cụ (`TabAssets`)
- `TabAssets._insert_at_playhead` [`src/ui/tabs/tab_assets.py:7078–7150`][CODE_VERIFIED]:
  - Dòng 7084–7086: Kiểm tra `asset_d.get("availability") == "offline" \to` hiển thị toast `Kho Offline — kết nối lại ổ đĩa trước khi chèn.` và hủy thao tác.
  - Dòng 7103–7142 (Video): Kiểm tra `os.path.isfile(real_file_path)`. Nếu file không còn trên đĩa $\to$ báo toast `❌ Không tìm thấy tệp video của “{asset_name}” trên đĩa.`
  - Dòng 7143–7150 (Chuyển cảnh native): Copy tên vào clipboard qua `QApplication.clipboard().setText()`, gọi `prepare_native_transition()` hiển thị hướng dẫn kéo thả vào điểm cắt (Cut), toast tồn tại 15 giây. Tuyệt đối không chèn tệp mp4/carrier giả vào timeline.

### 1.6. Tầng Dịch Vụ Chèn Resolve (`InsertService`)
- `InsertService.insert_async` [`src/services/insert_service.py:167–244`][CODE_VERIFIED]:
  - Dòng 181–183:
    ```python
    if asset_dict.get("availability") == "offline":
        if callback: callback(InsertResult(ok=False, error="Kho Offline — hãy kết nối lại ổ đĩa trước khi chèn."))
        return
    ```
  - Dòng 222–225: Chuyển cảnh `trans` $\to$ điều hướng sang `_insert_transition`.
  - Dòng 228–230: Âm thanh `sfx` / `bgm` $\to$ điều hướng sang `_insert_audio` (sử dụng `_find_free_audio_track` bảo toàn track A1 cho thoại).
  - Dòng 233–235: LUT `lut` $\to$ điều hướng sang `_insert_lut` (gọi `ResolveAutomation.apply_look_lut` áp lên clip tại playhead).
  - Dòng 238–240: Video / Meme / Overlay $\to$ điều hướng sang `_insert_video`.
  - Dòng 243: Text / Template $\to$ điều hướng sang `_insert_title_worker`.

### 1.7. Tầng Quản Lý Lưu Trữ (`StorageManager`)
- `StorageManager` [`src/core/storage_manager.py`][CODE_VERIFIED]:
  - Dòng 91–95 (`root`): Đọc đường dẫn từ file cấu hình `storage.json`. Nếu chưa cấu hình $\to$ ném `StorageOfflineError`.
  - Dòng 100–104 (`online`): Đọc và đối chiếu `library_id` trong marker `.chundvc_meta/library.json` với `storage.json`. Nếu ổ đĩa bị ngắt kết nối hoặc marker không khớp $\to$ trả về `False`.
  - Dòng 373–375 (`ui_assets`): Chuyển đổi danh sách asset từ storage sang DTO UI thông qua adapter `to_ui_asset(asset)`.

---

## 2. CHỐT CƠ CHẾ KHẢ DỤNG (AVAILABILITY SPECIFICATION)

### 2.1. Ma Trận Trạng Thái Tài Nguyên (Availability Matrix)
Theo chuẩn hợp đồng dữ liệu hiện hành (`01-data-contracts.md`), trường `availability` gồm đúng 4 trạng thái chuẩn:

| Trạng Thái | Điều Kiện Kỹ Thuật | Duyệt Metadata & Lọc | Hành Vi Hover Preview | Hành Vi Chèn (Insert) & Menu Action Bar |
|---|---|:---:|:---:|:---:|
| **`online`** | Kho Online VÀ file media tồn tại | ✅ Hiển thị đầy đủ | ✅ Phát video/audio bình thường | ✅ Nút Chèn sáng, menu thao tác đầy đủ |
| **`offline`** | Kho Offline (mất kết nối ổ đĩa hoặc `library_id` không khớp) | ✅ **Luôn duyệt được** (metadata, tags, dur, category) | ❌ Chặn phát, không tạo overlay | ❌ Khóa nút Chèn (`btn_primary.setEnabled(False)`), khóa mutation menu (newtrack, paste, install). Drag MIME rỗng. |
| **`missing`** | Kho Online nhưng file media không tồn tại trên đường dẫn lưu trữ | ✅ Hiển thị bình thường | ❌ Thoát êm (silent return), không overlay | ❌ Khóa nút Chèn, khóa mutation menu. Thông báo `File nguồn không tồn tại trên đĩa`. |
| **`staging`** | Đang tải về hoặc đang nhập gói, chưa hoàn tất ghi registry | ✅ Hiển thị bình thường | ❌ Chặn phát | ❌ Khóa nút Chèn, khóa mutation menu. Thông báo `Đang đồng bộ / nạp tài nguyên — chưa sẵn sàng chèn`. |
| **`legacy` (thiếu cờ)** | Asset cũ hoặc preset built-in không có thuộc tính `availability` | ✅ Hiển thị bình thường | ✅ Hoạt động bình thường | ✅ Mặc định coi là `online` (tương thích ngược hoàn toàn) |

*Ghi chú quan trọng:* `preview_only` và `builtin_preset` là **đặc tính khả năng (capabilities)** hoặc loại nguồn gốc tài nguyên trực giao, tuyệt đối không tạo thành các giá trị enum mới trong trường `availability`.

### 2.2. Cơ Chế Guard Giao Diện Trên Cached Metadata (UI Guard Scope)
- Trong phạm vi lát cắt **`G04-04C1-I`**, `DockActionBar` và `EdgeDockWindow` áp dụng kiểm tra an toàn trực tiếp dựa trên metadata `availability` đã nạp vào model.
- Tuyệt đối **không thực hiện `os.stat` / `StorageManager` / Disk I/O / Network IO** bên trong `set_asset` hoặc khi mở menu để bảo đảm hiệu năng giao diện phản hồi tức thì.
- Cơ chế kiểm tra động lại tính hợp lệ của file/root trước thời điểm bấm chèn thực tế được định vị là **khoảng trống kiến trúc (gap) [PROPOSED]** và được tách riêng cho lát cắt kiểm tra động tiếp theo.

### 2.3. Hợp Đồng Chèn Theo Danh Mục Nghiệp Vụ (Category Insert Contracts)
1. **Chuyển cảnh Native (`trans`):**
   - Không tạo và không chèn file video mp4/carrier giả vào timeline.
   - Hành vi chuẩn: Tự động sao chép tên chuyển cảnh vào Clipboard (`QApplication.clipboard().setText(name)`), tìm điểm cắt (cut point) gần playhead nhất, và hiển thị thông báo hướng dẫn người dùng kéo thả hiệu ứng từ Effects Library của DaVinci Resolve.
2. **Bảng màu Điện ảnh (`lut`):**
   - Áp trực tiếp file `.cube` lên clip đang được chọn hoặc clip nằm dưới đầu đọc (playhead) trên Timeline thông qua `ResolveAutomation.apply_lut_to_current_clip`.
3. **Hiệu ứng Âm thanh (`sfx` / `bgm`):**
   - Tìm kiếm track âm thanh trống khả dụng gần nhất (bỏ qua A1 dành riêng cho Voice/Dialogue) qua `_find_free_audio_track`. Tự động tạo thêm track mới nếu toàn bộ các track đang bị chiếm chỗ.

---

## 3. CHÍNH SÁCH TƯƠNG TÁC THỤ ĐỘNG KHI CLICK SUGGESTION CHIP

### 3.1. Nguyên Tắc Cốt Lõi: Hoàn Toàn Thụ Động (Passive UI)
Bấm vào Suggestion Chip trên thanh Dock hoặc trong cửa sổ Studio là hành động mang tính chất **định hướng và định vị (Locate & Inspect)**, không phải là lệnh thực thi chèn hay phát phương tiện:
- **Được phép thực hiện:**
  1. Chuyển đổi danh mục (Category Nav) sang đúng tab của asset.
  2. Xóa các bộ lọc xung đột (search query, subcategory, pack, tags) để đảm bảo asset xuất hiện trên lưới.
  3. Cuộn lưới ảo hóa (`scrollTo`) và chọn ô của asset (`setCurrentIndex`).
  4. Mở bảng thông số chi tiết (`SheetInspector.open_sheet`) và cập nhật thanh thao tác nổi (`DockActionBar.set_asset`).
- **TUYỆT ĐỐI BỊ CẤM:**
  1. **Không tự động phát âm thanh hoặc video:** Loại bỏ hoàn toàn khối lệnh `hover_controller.preview_asset(ad)` tại dòng 1284–1288 của `src/ui/lux/shell/edge_dock.py`.
  2. **Không tự động chèn vào timeline DaVinci Resolve.**
  3. Người dùng chỉ nghe/xem preview khi:
     - Chủ động rê chuột (hover) vào card trên lưới sau 150ms debounce.
     - Hoặc chủ động bấm nút Nghe thử / Play trong bảng Tùy chỉnh (Inspector).

### 3.2. Rà Soát Toàn Bộ Các Điểm Kích Hoạt Preview Ngầm (Stealth Playback Audit)
Đã rà soát toàn bộ các hàm hiển thị và dựng giao diện để đảm bảo không còn đường phát ngầm nào khác:
1. `SheetInspector.open_sheet(asset)`: Chỉ dựng các editor widget (`TextPresetEditor`, `AudioEditor`, `LutEditor`, `MacroEditor`) và gán giá trị slider. **Không phát media ngầm** [CODE_VERIFIED].
2. `DockActionBar.set_asset(asset)`: Chỉ cập nhật nhãn text và bật/tắt nút bấm. **Không phát media ngầm** [CODE_VERIFIED].
3. `CategoryNavWidget`: Chỉ đổi danh mục và phát tín hiệu `category_changed`. **Không can thiệp player** [CODE_VERIFIED].

---

## 4. VÒNG ĐỜI QUẢN LÝ PREVIEW, SỞ HỮU VÀ BẢO VỆ TÀI NGUYÊN (PREVIEW LIFECYCLE & OWNERSHIP)

### 4.1. Kiến Trúc Khởi Tạo Lười (Lazy Initialization & Single Player) [CODE_VERIFIED]
- Không bao giờ tạo `QMediaPlayer` hay `QVideoWidget` cho từng item hoặc từng card trong danh sách ảo hóa.
- Lớp `SharedHoverPreview` đóng vai trò là Controller tập trung duy nhất cho mỗi View:
  - Chỉ khởi tạo `_player` và `_audio_output` ở lần đầu tiên người dùng thực sự hover vào một item hợp lệ (`_ensure_player`).
  - Toàn bộ các card trên lưới chia sẻ chung một instance `HoverPreviewOverlay` nổi trên viewport.

### 4.2. Vòng Đời Tự Động Dọn Dẹp (Stop & Cleanup Lifecycle) [CODE_VERIFIED - G04-04C3-I]
- **Tín hiệu tương tác chuột & Viewport:**
  - Rời chuột khỏi card (`QEvent.Type.Leave` hoặc chuột di chuyển ra ngoài item) $\to$ gọi `stop()`.
  - Cuộn trang (`verticalScrollBar().valueChanged`) $\to$ gọi `stop()`.
  - View ẩn hoặc đóng (`QEvent.Type.Hide`, `QEvent.Type.Close`, `hideEvent`, `closeEvent`) $\to$ gọi `stop()`.
- **Tín hiệu vòng đời Model (Model Lifecycle Binding):**
  - Khi gán hoặc đổi model (`AssetGridView.setModel`), view tự động kết nối qua `SharedHoverPreview.bind_model(model)`.
  - Lắng nghe các tín hiệu vô hiệu hóa: `modelAboutToBeReset`, `modelReset`, `rowsAboutToBeRemoved`, `rowsRemoved`, `layoutAboutToBeChanged`, `layoutChanged`, và `destroyed`.
  - Slot `_on_model_invalidated(*args, **kwargs)` và `stop(*args, **kwargs)` nhận tham số an toàn với mọi Qt signal signature.
  - Ngắt kết nối toàn bộ handler từ model cũ khi đổi sang model mới hoặc gán `None`; gán trùng model không tạo kết nối lặp.
  - Proxy model filtering (như `AssetFilterProxyModel.invalidate()`) phát `layoutAboutToBeChanged` / `layoutChanged`, dừng preview ngay lập tức khi asset đang xem bị lọc mất.
- **Cơ chế chốt chặn playback (Active Playback Gate):**
  - Biến cờ `_is_active_playback` và `_hovered_row` được dọn sạch (`False`, `-1`) đồng bộ trong `stop()`.
  - Callback `_on_media_status_changed` chỉ tự lặp video khi `_is_active_playback == True` và `_hovered_row >= 0`. Sự kiện trễ `EndOfMedia` phát ra sau `stop()` bị hủy bỏ tức thì, không phát lại file cũ.
  - Bảo toàn tính lười (lazy): `stop()` và `bind_model()` tuyệt đối không tự ý gọi `_ensure_player()` hoặc `_ensure_overlay()`.

### 4.3. Tích Hợp Quản Lý Thiết Bị Âm Thanh (AudioRouter Integration)
- Đối tượng `QAudioOutput` của `SharedHoverPreview` được đăng ký vào `AudioDeviceRouter` qua `weakref.WeakSet` [CODE_VERIFIED].
- Code xử lý sự kiện `audioOutputsChanged` để mute an toàn khi `default_dev.isNull()` [CODE_VERIFIED].
- *Lưu ý kỹ thuật:* Chưa kiểm thử hotplug vật lý (physical unplug) trong môi trường CI/offscreen để khẳng định tuyệt đối không freeze ở tầng C++/driver hệ thống.

---

## 5. LÁT CẮT TRIỂN KHAI ĐÃ HOÀN TẤT: G04-04C1-I

### **Lát cắt: `G04-04C1-I — Suggestion thụ động và Action Bar unavailable`**
- **Phạm vi mã nguồn production:** Bó hẹp chính xác trong **đúng 2 modules**:
  1. [`src/ui/lux/shell/edge_dock.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/ui/lux/shell/edge_dock.py):
     - Hàm `_on_suggested_asset`:
       - Khi `asset_id` không tồn tại: hiển thị toast cảnh báo VÀ dọn sạch `_selected_asset = None`, `action_bar.set_asset(None)`, `sheet.close_sheet()`.
       - Xóa bỏ hoàn toàn khối tự kích hoạt audio preview `hover_controller.preview_asset(ad)`.
       - Giữ nguyên toàn bộ logic điều hướng danh mục, xóa filter xung đột, cuộn lưới ảo hóa, mở SheetInspector và cập nhật Action Bar.
  2. [`src/ui/lux/library/action_bar.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/ui/lux/library/action_bar.py):
     - Hàm `set_asset`:
       - Hỗ trợ cả dict và object asset (Pydantic/model).
       - Nhận diện 3 trạng thái unavailable: `"offline"`, `"missing"`, `"staging"`:
         - Vô hiệu hóa `self.btn_primary.setEnabled(False)` kèm tooltip tương ứng.
         - Cập nhật `lbl_info` với cảnh báo tiếng Việt rõ ràng.
         - Nút `btn_tune` vẫn mở được để xem cấu hình preset.
       - Khi asset khả dụng (`"online"` hoặc legacy không có cờ): `btn_primary.setEnabled(True)` và reset tooltip (không sticky disabled).
     - Hàm `_build_more_menu()` & `_show_more_menu()`:
       - Vô hiệu hóa các tác vụ thay đổi (mutation actions): `act_newtrack`, `act_paste`, `act_install` khi asset unavailable.
       - Giữ các tác vụ phi-mutation (`act_copy`, `act_fav`, `act_folder`) mở để người dùng tương tác phù hợp.

### **Bằng chứng kiểm thử TDD (100% Green)**
- Test file: [`tests/ui/test_dock_passive_suggestions.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/tests/ui/test_dock_passive_suggestions.py) (2 tests).
- Test file: [`tests/ui/test_action_bar_availability.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/tests/ui/test_action_bar_availability.py) (10 tests).
- **Lệnh kiểm thử quy định:**
  ```bash
  QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_dock_passive_suggestions.py tests/ui/test_action_bar_availability.py tests/ui/test_edge_dock.py tests/test_lux_integration.py tests/ui/test_pack_surface_feedback.py -q
  ```
  **Kết quả:** `36 passed in 3.86s` (Exit code 0).

---

## 6. LÁT CẮT TRIỂN KHAI ĐÃ HOÀN TẤT: G04-04C3-I

### **Lát cắt: `G04-04C3-I — Hover preview dừng theo model lifecycle`**
- **Phạm vi mã nguồn production:** Bó hẹp chính xác trong **đúng 2 modules**:
  1. [`src/ui/lux/library/hover_preview.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/ui/lux/library/hover_preview.py):
     - `bind_model(model)`: Gán model đang hiển thị (source hoặc proxy), hủy đăng ký model cũ, kết nối an toàn với các signal `modelAboutToBeReset`, `modelReset`, `rowsAboutToBeRemoved`, `rowsRemoved`, `layoutAboutToBeChanged`, `layoutChanged`, `destroyed`. Bỏ qua không gán lặp nếu trùng model.
     - `stop(*args, **kwargs)`: Chấp nhận tham số tùy biến từ Qt signals, reset `_is_active_playback = False`, dừng timer, reset `_hovered_row = -1`, dừng player và ẩn overlay (idempotent, không tạo đối tượng lười).
     - `_on_media_status_changed(status)`: Gate bằng `_is_active_playback` ngăn chặn dứt điểm hiện tượng callback `EndOfMedia` phát lại media sau khi preview đã dừng.
     - `eventFilter`: Lắng nghe thêm `QEvent.Type.Hide` và `QEvent.Type.Close` trên cả `_view` và `viewport`.
  2. [`src/ui/lux/library/asset_grid.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/src/ui/lux/library/asset_grid.py):
     - Override `setModel(model)`: Gọi `super().setModel(model)` và điều phối `self._hover_controller.bind_model(model)` với guard khởi tạo an toàn.
     - Override `hideEvent` và `closeEvent`: Dừng hover preview chủ động khi view bị ẩn hoặc đóng.

### **Bằng chứng kiểm thử TDD (100% Green)**
- Test file mới: [`tests/ui/test_hover_preview_lifecycle.py`](file:///media/vanntruongg/Documents/Development/Python/dự án cá nhân/ResolveFlow-Assistant/tests/ui/test_hover_preview_lifecycle.py) (10 tests kiểm thử trọn vẹn 10 kịch bản vòng đời).
- **Lệnh kiểm thử quy định:**
  ```bash
  QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/ui/test_hover_preview_lifecycle.py tests/ui/test_dock_passive_suggestions.py tests/ui/test_edge_dock.py tests/test_lux_integration.py tests/services/test_audio_router.py -q
  ```
  **Kết quả:** `34 passed in 3.79s` (Exit code 0).

### **Giới hạn và Các hạng mục tiếp tục TRÌ HOÃN (DEFERRED)**
- **Giới hạn hiện tại (Limits):** Lát cắt này tập trung thuần túy vào **vòng đời Model & View (Lifecycle stop & invalidation)**. Kiểm tra tính khả dụng của tài nguyên khi hover hiện dựa trên cached metadata (`availability != 'offline'`), chưa có dynamic storage recheck.
- **Tiếp tục TRÌ HOÃN (DEFERRED):**
  - Direct Studio preview runtime guard (`TabAssets` preview paths).
  - Fusion paste runtime guard.
  - Drag & Drop runtime guard (hiện tại drag check cờ `availability == "offline"` cached).
  - Dynamic filesystem/storage recheck trong thời gian thực khi hover.
  - Tối ưu hóa vòng lặp selection $O(N)$ trong `edge_dock.py` và `tab_assets.py`.

---

## 7. QUYẾT ĐỊNH CHÍNH SÁCH ĐÃ CHỐT VỚI TECH LEAD CODEX

1. **Hiển thị nút Chèn trên Action Bar khi Asset Unavailable:**
   - Vô hiệu hóa nút (`self.btn_primary.setEnabled(False)`) kèm tooltip và nhãn cảnh báo tiếng Việt rõ ràng.
   - Vô hiệu hóa đồng bộ các action mutation trong menu More (`new_track`, `paste_fusion`, `install_fusion`).
   - Phục hồi bình thường khi chuyển sang asset online (không sticky disabled).
2. **Xử lý khi Hover Preview gặp Asset bị mất file trên đĩa (Missing File):**
   - Thoát êm (silent return), không hiển thị khung video overlay và không phát âm thanh; ghi nhận log debug.
   - Chi tiết cảnh báo thiếu file sẽ do các hành động chủ động (chọn, bấm chèn) đảm nhận.
3. **Quản lý vòng đời Preview theo Model & View:**
   - Khi có bất kỳ biến động cấu trúc dữ liệu nào (`modelReset`, `layoutChanged`, `rowsRemoved`, proxy filter), preview dừng ngay lập tức và hủy bỏ trạng thái active playback.
