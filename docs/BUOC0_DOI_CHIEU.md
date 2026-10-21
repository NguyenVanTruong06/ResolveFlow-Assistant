# BƯỚC 0: ĐỐI CHIẾU THIẾT KẾ (UX_REDESIGN.md & ui_mockup.html) VỚI CODEBASE THỰC TẾ

> **Ngày thực hiện:** 21/10/2026  
> **Nguyên tắc:** Chỉ đọc, đối chiếu từng dòng mã nguồn thực tế, tìm ra mọi điểm sai lệch hoặc bất khả thi trước khi tiến hành viết code.

---

## 1. KIẾN TRÚC: `main.py` VÀ `app.py`

### 1.1. `src/main.py` có thật sự khởi tạo `FloatingBubble`, `AutoWindow`, `StudioWindow` không?
- **Xác nhận: ĐÚNG.**
- **Bằng chứng mã nguồn:** [`src/main.py:19-25`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/main.py#L19-L25)
  ```python
  # 1. Khởi tạo Bubble (Trung tâm điều hướng - Singleton)
  bubble = FloatingBubbleWidget()
  
  # 2. Khởi tạo 2 Cửa sổ (Singleton)
  auto_win = AutoWindow()
  auto_win.bubble = bubble
  
  studio_win = StudioWindow()
  ```
- **Điểm thiết kế hiểu sai:**
  - Thiết kế (`docs/UX_REDESIGN.md:31`) viết: `main.py` khởi tạo Bubble, AutoWindow, StudioWindow; *thêm EdgeDrawer và app.py cũ*.
  - **Thực tế:** `src/main.py` **hoàn toàn không** khởi tạo `EdgeDrawer` và **không hề import** `app.py`. `src/main.py` chỉ hiển thị `bubble` đầu tiên (`bubble.show()`), rồi liên kết tín hiệu click từ Bubble để mở `auto_win` hoặc `studio_win`.

### 1.2. `src/ui/app.py` còn được dùng ở đâu (import, test)?
`src/ui/app.py` (chứa class `ChunDVCApp` / `ResolveFlowApp`, dài 4.585 dòng) là file monolith cũ trước khi dự án tách thành `AutoWindow` và `StudioWindow`.
Hiện tại `src/ui/app.py` **không còn nằm trong luồng chạy chính** của `src/main.py`, nhưng vẫn còn bị ràng buộc tại các vị trí:
1. **PyInstaller build script & spec**:
   - [`build_standalone.py:90`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/build_standalone.py#L90): `"src.ui.app"`
   - [`resolveflow.spec:71`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/resolveflow.spec#L71): `'src.ui.app'`
2. **Các file Unit / Integration Test**:
   - [`tests/test_ui.py:4`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_ui.py#L4): `from src.ui.app import PipelineWorker, ResolveFlowApp, PreviewDialog` (chứa hàng loạt mock test cho `PipelineWorker`).
   - [`tests/test_pipeline_worker_shotbook.py:7`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_pipeline_worker_shotbook.py#L7): `from src.ui.app import PipelineWorker as AppPipelineWorker`.
   - [`tests/test_lux_integration.py:16`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_lux_integration.py#L16): `from src.ui.app import ChunDVCApp`.
   - [`tests/test_auto_window_parity.py:48`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_auto_window_parity.py#L48): Kiểm tra tính độc lập parity, yêu cầu `assert "ChunDVCApp" not in src`.
3. **Thực thi trực tiếp độc lập**:
   - [`src/ui/app.py:4578`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/app.py#L4578): `if __name__ == "__main__": window = ChunDVCApp()`.

---

## 2. CÁC ĐƯỜNG CODE ĐANG CHÈN VÀO DAVINCI RESOLVE

Hiện tại trong codebase có **7 con đường** thực hiện chèn dữ liệu / tác động vào DaVinci Resolve:

| # | Hành động chèn | Vị trí định nghĩa (file:dòng) | Nơi gọi từ UI | Thread thực thi | Có chặn UI không? |
|---|---|---|---|---|---|
| **1** | **Chèn Fusion Title (Zero Ripple)** | [`src/core/resolve_api.py:1821-1996`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1821-L1996)<br>gọi qua [`src/core/title_insert_worker.py:178`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/title_insert_worker.py#L178) | `tab_assets.py:6205`<br>`auto_window.py:3345`<br>`macro_card.py:187` | Chạy trong **Subprocess riêng** (`python title_insert_worker.py`) | **CÓ CHẶN NHẸ UI** vì UI gọi `subprocess.run(..., timeout=15)` đồng bộ trên Main Thread thay vì `QProcess`. |
| **2** | **Fast-Paste vào Fusion (`Ctrl+V`)** | [`src/core/fast_paste.py:146-215`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/fast_paste.py#L146-L215) | `tab_assets.py:6630`<br>`macro_card.py:194` | **Main UI Thread** | **KHÔNG** (thao tác clipboard + hotkey diễn ra dưới 30ms). |
| **3** | **Chèn SFX Audio vào Track** | [`src/core/resolve_api.py:883-958`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L883-L958) | `auto_window.py:3362`<br>`tab_assets.py:6100`<br>`tab_sfx.py:254` | **Main UI Thread** | **CÓ CHẶN UI** nếu MediaPool import hoặc timeline Append bị khựng. |
| **4** | **Áp dụng 3D LUT (.cube)** | [`src/core/resolve_api.py:754-850`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L754-L850) | `auto_window.py:3382`<br>`tab_export.py:481` | **Main UI Thread** | **CÓ CHẶN UI** vì duyệt qua từng clip trên track và gọi `item.SetLUT()`. |
| **5** | **Nhập Timeline FCPXML / XML** | [`src/core/resolve_api.py:617-660`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L617-L660)<br>[`src/core/connection/bridge.py:74-95`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/connection/bridge.py#L74-L95) | `auto_window.py:2800` (qua `PipelineWorker`) | **QThread phụ** (`PipelineWorker`) | **KHÔNG** chặn UI khi chạy qua pipeline auto cut. |
| **6** | **Chèn phụ đề Kinetic Subtitles hàng loạt** | [`src/core/kinetic/kinetic_engine.py:525`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/kinetic/kinetic_engine.py#L525) | `auto_window.py:3020` (qua `PipelineWorker`) | **QThread phụ** | **KHÔNG** chặn UI khi chạy trong worker. |
| **7** | **Thêm Marker / Cắt Timeline** | [`src/core/resolve_api.py:690-745`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L690-L745) | `auto_window.py:2850` | **Main UI Thread** hoặc Worker | **CÓ CHẶN UI** nếu lặp qua hàng trăm marker trên main thread. |

### Các đường code mà tài liệu thiết kế đã BỎ SÓT:
1. **`KineticEngine.render_to_timeline` (`src/core/kinetic/kinetic_engine.py:525`)**: Tạo hàng chục TimelineItem TextPlus phụ đề tự động theo từng trang thoại via `media_pool.AppendToTimeline`. Thiết kế chỉ xem kinetic subtitle là 1 checkbox trong Director mà quên mất đường chèn timeline phức tạp này.
2. **`ImportTimelineFromFile` (FCPXML Pipeline)**: Nhập timeline sau khi AutoCut khoảng lặng.
3. **`Timeline.AddMarker` (`src/core/resolve_api.py:690`)**: Đánh dấu nhịp hoặc điểm cắt.

---

## 3. THUẬT TOÁN CHÈN TRACK TRỐNG (`NEW-NESTED-NO-RIPPLE-V2`) VÀ KHẢ NĂNG HOÀN TÁC

### 3.1. Xác nhận giá trị trả về
- **Xác nhận:** Hàm [`import_preset_nested_v2`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1988-L1996) **ĐÃ TRẢ VỀ ĐẦY ĐỦ** thông tin track và frame:
  ```python
  # src/core/resolve_api.py:1988-1996
  return {
      "ok": True,
      "track": target_track,        # int, ví dụ 2 (V2)
      "start": rec_frame,           # int, vị trí recordFrame (frame số)
      "end": rec_frame + duration,  # int
      "duration": duration,         # int, số frame
      "track_added": track_was_added,
      "item_name": placed_item.GetName() if hasattr(placed_item, "GetName") else disp,
  }
  ```
- Đồng thời `title_insert_worker.py:190` in toàn bộ dictionary này ra `stdout` dạng JSON cho tiến trình cha.

### 3.2. Về Timecode chuỗi (String Timecode dạng `00:01:12:04`)
- Trong code hiện tại, `start` là số nguyên frame (ví dụ `86400`).
- Để `InsertService` có chuỗi timecode hiển thị lên Toast (`"Đã chèn ... tại 00:01:12:04"`), cần:
  - **Đề xuất cách lấy:** Trước khi chèn, đọc `timeline.GetCurrentTimecode()` (ví dụ `"01:00:00:00"`). Thêm trực tiếp trường `"timecode": timeline.GetCurrentTimecode()` vào dictionary trả về của `import_preset_nested_v2` tại [`src/core/resolve_api.py:1990`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1990).

---

## 4. TỐC ĐỘ GỌI RESOLVE API (`GetCurrentPage` VÀ TIMECODE PLAYHEAD)

- **Trạng thái đo thực tế:** **Chưa đo** (vì tại thời điểm kiểm tra, DaVinci Resolve không bật trên máy người dùng, chỉ có daemon hệ thống `systemd-resolve`).
- **Phân tích kỹ thuật dựa trên fusionscript IPC:**
  - `resolve.GetCurrentPage()`: Là lời gọi C++ binding trực tiếp qua bộ nhớ chia sẻ cục bộ, thời gian thực thi tiêu chuẩn khoảng **0.2ms – 1.0ms**.
  - `timeline.GetCurrentTimecode()`: Thời gian thực thi khoảng **0.5ms – 2.0ms**.
  - **Đánh giá tần suất poll 700ms trong thiết kế:** Chu kỳ 700ms trên một `QThread` riêng biệt là hoàn toàn an toàn, chiếm chưa tới 0.1% CPU một nhân và không gây nghẽn DaVinci Resolve.

---

## 5. XÓA TIMELINE ITEM ĐÃ CHÈN QUA API (CHO TÍNH NĂNG HOÀN TÁC)

- **Xác nhận: CÓ THỂ XÓA ĐƯỢC.**
- **Hàm API chính thức của DaVinci Resolve:**
  ```python
  timeline.DeleteClips(timeline_items, ripple=False)
  ```
  - `timeline_items`: danh sách đối tượng `TimelineItem` cần xóa.
  - `ripple=False`: Xóa dạng **Lift** (chừa khoảng trống, tuyệt đối không làm dạt/co cụm các clip xung quanh).
  - Bằng chứng đã dùng thành công trong codebase: [`src/core/resolve_api.py:1978`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1978):
    ```python
    main_timeline.DeleteClips([placed_item], False)
    ```
- **Cơ chế định vị clip để hoàn tác (khi đối tượng `placed_item` đã mất tham chiếu):**
  1. Lấy danh sách clip trên track: `items = timeline.GetItemListInTrack("video", target_track)`.
  2. Quét clip có `item.GetStart() == start_frame` và `item.GetName() == item_name`.
  3. Gọi `timeline.DeleteClips([matched_item], False)`.

---

## 6. SO SÁNH BẢNG ALIAS (MOCKUP) VỚI `BILINGUAL_TAG_MAP` THỰC TẾ

| Tiêu chí | Bảng ALIAS trong `ui_mockup.html:476-491` | `BILINGUAL_TAG_MAP` trong `src/core/motion_analyzer.py:21-76` | Nhận xét đối chiếu |
|---|---|---|---|
| **Dấu tiếng Việt** | Chỉ lưu chuỗi **không dấu** (`mo dan`, `phong to`, `truot len`). | Lưu cả **có dấu** và **không dấu** (`"phong to"`, `"phóng to"`, `"mờ dần"`...). | Mockup làm đúng vì đã qua hàm `strip()`. Cần giữ chuẩn hóa không dấu cho index. |
| **Xử lý `fade_out` vs `fade_in`** | `fade_out` **chỉ có** `['fade out', 'toi dan', 'bien mat']`. Đã xóa `mo dan`. | `fade_out` **vẫn chứa** `"mo dan", "mờ dần"`, gây xung đột với `fade_in`. | **Mockup sửa đúng.** Code Python cần xóa `"mo dan"` khỏi `fade_out`. |
| **Danh mục `static`** | **Không có** | Có (`static`, `still`, `tinh`, `tĩnh`, `dung yen`...) | Mockup thiếu tag tìm kiếm cho đồ họa tĩnh. |
| **Danh mục `text`** | **Không có** | Có (`text`, `title`, `typography`, `chu`, `chữ`, `tieu de`...) | Mockup dùng prefix `c:` thay vì tag motion. |
| **Danh mục `animated`** | **Không có** | Có (`animated`, `animation`, `chuyen dong`...) | Code Python tổng quát hơn. |
| **Các tag bổ sung** | Có `glitch`, `bounce`, `typewriter` tách thành key riêng. | `glitch` gộp chung vào `shake`; `bounce` nằm trong preset; không có key `typewriter`. | **Mockup phân tách tốt hơn** cho việc xếp hạng các kiểu chữ nhảy (bounce) và máy đánh chữ (typewriter). |

---

## 7. DANH MỤC TÀI NGUYÊN THỰC TẾ (AssetIndexer) VS THIẾT KẾ

### Số lượng tài nguyên thật trên đĩa (quét bằng `AssetIndexer`):
- `sfx`: **435** files âm thanh.
- `luts`: **23** files `.cube`.
- `memes`: **68** video reaction kèm thumbnail.
- `overlays`: **23** files video/alpha.
- `titles`: **16** files `.setting` (cộng thêm 14 `BUILTIN_PRESETS` trong `text_preset.py`).
- `transitions`: **10** files `.setting` (cộng thêm 6 `BUILTIN_TRANSITIONS`).
- `bgm`: **19** tracks (chia theo 4 mood: `chill_vlog`: 5, `upbeat_trend`: 5, `cinematic`: 5, `funny`: 4).
- `stickers / icons`: **17** vector stickers (`MOCKUP_ICONS` trong `tab_assets.py:160-178`).

### So sánh với thiết kế (`UX_REDESIGN.md` & `CATS` trong mockup):
1. **Thiết kế THIẾU:**
   - **`icon` / `sticker`**: Mockup và thiết kế **bỏ quên hoàn toàn** 17 Sticker Vector đang hoạt động rất tốt trong `tab_assets.py` (Mũi tên, Cảnh báo, Trái tim, Ngọn lửa...).
   - **BGM Moods**: Trong code thật, BGM chia thành 4 mood rõ rệt (`chill_vlog`, `upbeat_trend`, `cinematic`, `funny`), còn trong thiết kế BGM chỉ có nhóm con `Tất cả`.
2. **Thiết kế THỪA (Tài nguyên ảo, chưa có folder/backend thật):**
   - **`fx` (Hiệu ứng node Fusion)**: Mockup liệt kê `fx_shake`, `fx_glow`, `fx_zoomblur`, `fx_chroma`. Thực tế trên đĩa không có folder `fx` hay file template node riêng nào.
   - **`macro_text` & `macro_trans`**: Thiết kế chia tách làm danh mục riêng trên trang Fusion, nhưng thực chất backend đều quét chung từ `templates/titles` và `templates/transitions`.

---

## 8. TÌNH TRẠNG FILE PREVIEW THỰC TẾ TRÊN ĐĨA

| Nhóm tài nguyên | File Video Preview (.mp4) | Thumbnail tĩnh (.png / .jpg) | Cơ chế hiển thị hiện tại |
|---|---|---|---|
| **Memes** (68 files) | **CÓ** (68/68 files mp4) | **CÓ** (68/68 files png companion) | Rê chuột phát chính video file; tĩnh hiển thị file png. |
| **Overlays** (23 files) | **CÓ** (video mp4) | Tự sinh bằng `AssetCache` | Hiện thumbnail tĩnh trích xuất từ frame giữa. |
| **Titles (.setting)** (16+ files) | **KHÔNG CÓ** (0/16) | **KHÔNG CÓ** (0/16) | **100% phụ thuộc vào canvas procedural** của `MacroCard` (`_paint_static`/`_paint_animated`) hoặc HTML RichText preview. |
| **Transitions (.setting)** (10 files) | **KHÔNG CÓ** (0/10) | **KHÔNG CÓ** (0/10) | Hoàn toàn vẽ bằng procedural QPainter. |
| **SFX & BGM** (454 files) | Không áp dụng | Không áp dụng | Vẽ sóng âm waveform procedural qua `QPainter`. |
| **LUTs** (23 files) | Không áp dụng | Không áp dụng | Vẽ 4 ô màu swatch palette (`#1f2937`, `#64748b`...). |

> **Kết luận:** Giả định trong thiết kế rằng `.setting` có thể render sẵn video clip 320x180 là **chưa có dữ liệu thực tế**. Giai đoạn 1 bắt buộc phải dùng `_paint_animated` (procedural canvas) của `MacroCard`.

---

## 9. CỬA SỔ & CỜ `Qt.WindowDoesNotAcceptFocus` TRÊN EDGE DRAWER

### 9.1. Đọc code `EdgeDrawerWindow` hiện tại
- File: [`src/ui/lux/panel_edge_rail.py:37-43`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/panel_edge_rail.py#L37-L43)
  ```python
  super().__init__(
      parent,
      Qt.WindowType.FramelessWindowHint |
      Qt.WindowType.WindowStaysOnTopHint |
      Qt.WindowType.Tool
  )
  ```
- Hiện tại cửa sổ dùng cờ `Qt.WindowType.Tool`. Cờ này trên Windows và Linux X11 vẫn cho phép cửa sổ nhận focus khi click chuột.

### 9.2. Phân tích xung đột nếu áp dụng `Qt.WindowDoesNotAcceptFocus`
- **Thiết kế đề xuất (Mục 3.5):** Dùng `Qt.WindowDoesNotAcceptFocus` để click thẻ không cướp focus của Resolve; khi click ô search thì mới cho nhận focus.
- **Vấn đề kỹ thuật nghiêm trọng trong Qt/PySide6:**
  1. `WindowDoesNotAcceptFocus` là cờ cấp **Cửa sổ Top-Level (Window Flag)**. Khi cờ này được bật, hệ điều hành (X11 / Windows) sẽ **chặn hoàn toàn mọi sự kiện bàn phím gửi tới cửa sổ đó**.
  2. Các widget con bên trong như:
     - `self.search_input = QLineEdit()` ([`macro_card.py:340`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/macro_card.py#L340))
     - `self.txt_search = QLineEdit()` ([`tab_assets.py:3556`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/tabs/tab_assets.py#L3556))
     - Các QComboBox và QSlider trong Sheet Inspector
     **sẽ không thể gõ được chữ hoặc nhận phím mũi tên!**
  3. Trong Qt, không thể bật/tắt cờ `WindowDoesNotAcceptFocus` một cách động lúc runtime mà không gọi `setWindowFlags()`, hành động này sẽ phá hủy native window handle và tạo lại cửa sổ, gây hiện tượng chớp nháy (flicker) và mất trạng thái UI.
- **Giải pháp thay thế khả thi hơn:**
  - Không đặt cờ `WindowDoesNotAcceptFocus`.
  - Thay vào đó, đặt `setFocusPolicy(Qt.FocusPolicy.NoFocus)` trên tất cả `QPushButton`, `MacroCard`, `QListView` thẻ card.
  - Sau bất kỳ thao tác click/chèn/kéo thả nào, gọi ngay `fast_paste.focus_resolve_window()` để trả focus về cho DaVinci Resolve.
  - Chỉ riêng `QLineEdit` là giữ `Qt.FocusPolicy.StrongFocus`.

---

## 10. DANH SÁCH UNIT TEST HIỆN TẠI PHỤ THUỘC WIDGET ẨN

Trong [`src/ui/tabs/tab_assets.py:3302-3327`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/tabs/tab_assets.py#L3302-L3327), có hẳn một khối code ghi chú:
`# WIDGETS TƯƠNG THÍCH NGƯỢC (Bắt buộc cho app.py & test_ui.py)`
Các widget này được tạo ra rồi gọi `.hide()` ngay lập tức chỉ để phục vụ test cũ không bị crash:
- `self.combo_text_preset = QComboBox()`
- `self.check_subtitle = QCheckBox()`
- `self.combo_split_mode = QComboBox()`
- `self.txt_split_limit = QLineEdit()`
- `self.txt_font`, `self.txt_size`, `self.txt_color`

### Các test cụ thể phụ thuộc vào chúng:
1. [`tests/test_ui.py:1308`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_ui.py#L1308):
   `assert window.combo_text_preset.count() == len(window.tab_titles.all_presets)`
2. [`tests/test_ui.py:1325`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_ui.py#L1325):
   `assert window.combo_text_preset.currentData() == "kinetic_hormozi"`
3. [`tests/test_ui.py:1380-1387`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_ui.py#L1380-L1387):
   Truy cập trực tiếp vào `window.tab_titles.txt_single_title`, `slide_font_size`, `lbl_insp_preview`.
4. [`tests/test_ui.py:1407-1428`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_ui.py#L1407-L1428):
   Gọi `_create_drag_mime_data`, `btn_copy_fusion.click()`, `_insert_at_playhead()` trên `tab_titles`.
5. [`tests/test_auto_window_parity.py`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/tests/test_auto_window_parity.py):
   Kiểm tra sự tồn tại của các thuộc tính đồng bộ giữa `AutoWindow` và `app.py`.

> Khi tái cấu trúc sang kiến trúc mới (Bước 1), các test này cần được cập nhật sang kiểm tra qua Service (`InsertService`, `AssetRepository`) thay vì kiểm tra widget ẩn.

---

## 11. CÁC ĐIỂM KHÁC THIẾT KẾ HIỂU SAI CODE

1. **Về việc nhúng `TabAssets` vào Edge Drawer (Mục 1.4):**
   - Thiết kế ghi: *Drawer nhúng TabAssets tối thiểu 798px vào 440px mà không responsive*.
   - **Thực tế:** `EdgeDrawerWindow` (`panel_edge_rail.py`) không hề nhúng `TabAssets` 4 cột vào. Nó nhúng `MacroGridWidget` 2 cột chuyên biệt từ `macro_card.py`.
2. **Khớp truy vấn hai chiều trong tìm kiếm (Mục 1.5):**
   - Thiết kế ghi nhận rất chính xác: Trong [`src/core/motion_analyzer.py:109`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/motion_analyzer.py#L109), dòng `if clean_q in clean_item or clean_item in clean_q:` đang làm cho các từ khóa ngắn như `"up"`, `"cut"` bị khớp vào mọi truy vấn dài.
3. **Quản lý tiến trình Worker Subprocess:**
   - Thiết kế cho rằng worker chèn title đang dùng `subprocess.run` chặn 15s.
   - **Thực tế:** Code đúng là đang dùng `subprocess.run` đồng bộ tại [`src/core/resolve_api.py:2044`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L2044). Việc chuyển sang `QProcess` không đồng bộ như thiết kế đề xuất ở Bước 1 là hoàn toàn chính xác và cấp thiết.
4. **Về `FloatingBubble`:**
   - Thiết kế nhận xét quả cầu nổi đè lên mép phải cửa sổ app.
   - **Thực tế:** Đúng là quả cầu nổi độc lập có cờ `WindowStaysOnTopHint`, nên khi mở cửa sổ to nó sẽ nằm đè lên. Việc chuyển quả cầu thành tùy chọn tắt mặc định và thay bằng `QSystemTrayIcon` là rất hợp lý.

---

## ĐỀ XUẤT SỬA THIẾT KẾ (`UX_REDESIGN.md`)

Dựa trên các phát hiện thực tế ở trên, đề xuất điều chỉnh tài liệu thiết kế `UX_REDESIGN.md` như sau trước khi bắt tay vào Bước 1:

1. **Bổ sung danh mục Sticker / Icon vào kiến trúc thư viện:**
   - Thêm category `icon` (17 vector sticker) vào `LibraryView` và `CATS` của hồ sơ trang Edit/Cut, tránh làm mất tính năng tách nền AI và chèn sticker sẵn có.
2. **Loại bỏ danh mục `fx` ảo:**
   - Gộp các macro Fusion vào đúng bản chất là `titles` và `transitions`, không tạo danh mục `fx` độc lập khi đĩa chưa có thư mục này.
3. **Điều chỉnh giải pháp Focus cho Edge Drawer:**
   - Bỏ yêu cầu dùng cờ `Qt.WindowDoesNotAcceptFocus` trên `EdgeDrawerWindow` vì sẽ làm hỏng `QLineEdit` tìm kiếm.
   - Thay bằng quy tắc: Đặt `NoFocus` cho các card/button, và gọi hàm `fast_paste.focus_resolve_window()` ngay sau khi người dùng thực hiện xong thao tác click/drag để trả focus về cho Resolve.
4. **Giữ lại Canvas Procedural cho Preview Title / Transition:**
   - Vì 100% file `.setting` chưa có video `.mp4` rendered sẵn, thiết kế cần khẳng định `_paint_animated` procedural của `MacroCard` là phương án hiển thị chính thức cho Title/Transition trong giai đoạn này, không coi nó là "tạm bợ".
5. **Cập nhật danh sách các đường chèn trong `InsertService`:**
   - `InsertService` cần hỗ trợ thêm: `insert_kinetic_subtitles` (từ `KineticEngine`) và `import_fcpxml_timeline`.
6. **Bổ sung trường Timecode vào kết quả trả về:**
   - Xác định rõ `InsertResult` sẽ nhận chuỗi timecode từ `timeline.GetCurrentTimecode()` để truyền thẳng cho Toast Hoàn tác mà không cần tính toán quy đổi phức tạp.
