# KẾ HOẠCH TRIỂN KHAI BƯỚC 4: TỰ ĐỔI THEO TRANG DAVINCI (CONTEXT ROUTER & PROFILES)

> **Mục tiêu:** Xây dựng hệ thống Context Router và Context Profiles cho 7 trang của DaVinci Resolve (Media, Cut, Edit, Fusion, Color, Fairlight, Deliver). Khi người dùng chuyển trang trong Resolve, Dock tự động đổi danh mục tài nguyên, khối đặc biệt và hành động chính, đảm bảo cơ chế hoãn đổi thông minh (không nhảy giao diện khi người dùng đang thao tác trong Dock), lưu nhớ vị trí cuộn & danh mục của từng trang, và tăng điểm tìm kiếm theo trang hiện tại.
> **Quy tắc:** Tuân thủ quy trình 2 lượt — Lượt 1 chỉ lập kế hoạch, không sửa bất kỳ dòng code nào.

---

## 1. HIỂU YÊU CẦU

Bước 4 xây dựng bộ định tuyến ngữ cảnh **Context Router** và hệ thống hồ sơ **Context Profiles** cho 7 trang của DaVinci Resolve (Media, Cut, Edit, Fusion, Color, Fairlight, Deliver). Khi người dùng chuyển trang trong DaVinci Resolve, Dock tự động đổi danh mục tài nguyên, khối đặc biệt (gợi ý thoại, 12 pad âm thanh, hút preset Fusion, chuẩn hóa loudness, preset xuất bản) và hành động chính mà không đổi kích thước dock. Trọng tâm cốt lõi là **cơ chế hoãn đổi thông minh**: tuyệt đối không đổi giao diện khi người dùng đang thao tác bên trong Dock (rê chuột, gõ tìm kiếm, mở sheet, kéo thả); lưu nhớ vị trí cuộn & danh mục của từng trang; và tăng điểm tìm kiếm (`set_page_boost`) theo trang hiện tại.

---

## 2. HIỆN TRẠNG CODE

1. [`src/services/resolve_state.py:28-115`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/services/resolve_state.py#L28-L115):
   - `ResolveStateService` định kỳ thăm dò `GetCurrentPage()` mỗi 700ms trên background thread và phát tín hiệu `page_changed(str)`.
2. [`src/services/search_index.py:168-171`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/services/search_index.py#L168-L171):
   - Hàm `set_page_boost(categories)` đã sẵn sàng để cộng +25 điểm cho các tài nguyên khớp danh mục của trang đang kích hoạt.
3. [`src/core/contextual_suggester.py:21-120`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/contextual_suggester.py#L21-L120):
   - `ContextualSuggester` phân tích transcript tại playhead và đề xuất danh sách hiệu ứng/âm thanh phù hợp cho khối gợi ý Edit.
4. [`src/core/preset_capture.py:178-325`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/preset_capture.py#L178-L325):
   - Hàm `capture_playhead_text_preset()` hút style node Text+/Fusion từ playhead thành preset lưu vĩnh viễn.
5. [`src/core/resolve_api.py:1132-1210`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/resolve_api.py#L1132-L1210):
   - Cấu hình preset xuất bản và gửi render job vào Render Queue của Resolve (`AddRenderJob()`).
6. [`src/ui/lux/shell/edge_dock.py:1-480`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/shell/edge_dock.py#L1-L480):
   - `EdgeDockWindow` đã xây dựng hoàn chỉnh ở Bước 3, hiện đang dùng hồ sơ tĩnh Edit. Cần kết nối với `ContextRouter` để biến hình theo trang Resolve.
7. [`src/ui/lux/shell/dock_header.py:50-95`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/shell/dock_header.py#L50-L95):
   - `btn_page_chip` đã có sẵn, cần bổ sung chip hoãn pending (ví dụ `Fusion ›`) và menu khóa hồ sơ.

---

## 3. PHƯƠNG ÁN ĐỀ XUẤT

### 3.1. Danh sách file tạo mới & chỉnh sửa

```text
src/ui/lux/
├── context/
│   ├── __init__.py               # Khởi tạo package context
│   ├── context_profiles.py       # Dataclass ContextProfile & từ điển PROFILES cho 7 trang
│   ├── special_blocks.py         # Khối đặc biệt cho Media, Cut, Edit, Fusion, Color, Fairlight, Deliver
│   └── context_router.py         # Bộ định tuyến nhận page_changed, hoãn đổi, memory, profile lock
├── shell/
│   ├── dock_header.py            # Bổ sung pending chip và menu khóa hồ sơ
│   └── edge_dock.py              # Tích hợp ContextRouter, vạch màu 2px, special block container
tests/ui/
└── test_context_router.py        # Kiểm thử 7 hồ sơ, cơ chế hoãn đổi, bộ nhớ cuộn và khóa hồ sơ
```

### 3.2. Thứ tự triển khai chi tiết

1. **Giai đoạn 1: Context Profiles (`context_profiles.py`)**
   - Định nghĩa dataclass `ContextProfile`:
     - `page`: "media", "cut", "edit", "fusion", "color", "fairlight", "deliver"
     - `label`: "Media", "Cut", "Edit", "Fusion", "Color", "Fairlight", "Deliver"
     - `color`: Màu nhận diện (Edit `#7c3aed`, Fusion `#06b6d4`, Fairlight `#ec4899`, Color `#f59e0b`, Cut/Deliver/Media `#a8a8b3`)
     - `categories`: Danh mục hiển thị trên thanh chip ngang
     - `primary_action`: Tên hành động chính trên Action Bar
     - `special_block`: Tên khối đặc biệt tương ứng
   - Xây dựng từ điển `PROFILES` chuẩn xác theo bảng 4.1 trong UX_REDESIGN.md.

2. **Giai đoạn 2: Khối đặc biệt theo từng trang (`special_blocks.py`)**
   - `EditSuggestBlock`: Gợi ý ngữ cảnh thoại tại playhead (`ContextualSuggester`), các chip nút đề xuất, nút ẩn `✕`.
   - `CutPadsBlock`: Hàng 12 pad âm thanh ghim ở đầu, phím tắt 1–9, 0, -, = khi Dock mở.
   - `FusionCaptureBlock`: Tóm tắt node đang chọn + nút "📥 Hút thành preset" (`preset_capture`).
   - `ColorSpecialBlock`: Thanh trượt cường độ LUT, phạm vi áp dụng (Clip / Timeline), bỏ qua clip có LUT.
   - `FairlightBlock`: Chuẩn hóa loudness (-14 YouTube / -16 Podcast / -23 LUFS EBU R128) + 12 pad âm thanh.
   - `DeliverBlock`: 4 preset xuất bản (TikTok 9:16, YouTube 1080p, YouTube 4K, Podcast) + danh sách tiến độ job render.
   - `MediaBlock`: Số clip đang chọn trong Media Pool + nút "Phân tích & dựng thô" (mở Director) + tạo proxy.

3. **Giai đoạn 3: Bộ định tuyến Context Router (`context_router.py`)**
   - Kết nối với `ResolveStateService.page_changed`.
   - **Quy tắc hoãn đổi (Deferral Rule):**
     - Nếu `is_in_dock`, `has_search_text`, `is_sheet_open`, hoặc `is_dragging` là True:
       - Lưu `pending_page = new_page`.
       - Phát tín hiệu hiện chip chờ trên header: ví dụ `[ Fusion › ]`.
       - Không chuyển đổi giao diện!
     - Khi chuột rời Dock hoặc người dùng bấm chip chờ: áp dụng hồ sơ mới ngay lập tức.
   - **Chuyển tiếp thị giác (Morphing):**
     - Crossfade nội dung trong 140ms bằng `QGraphicsOpacityEffect`.
     - Giữ nguyên chiều rộng Dock, không trượt co giãn.
   - **Bộ nhớ hồ sơ (`ProfileMemory`):**
     - Khi rời trang: lưu lại `(cat, sub, scroll_y)`.
     - Khi quay lại trang: phục hồi chính xác vị trí đã lưu.
   - **Khóa hồ sơ (Profile Lock):**
     - Người dùng bấm vào chip trang trên header để mở menu:
       * `Theo DaVinci (tự động) [ Trang hiện tại ]`
       * `Khóa: Edit` / `Khóa: Fusion` / `Khóa: Color` / `Khóa: Fairlight`...
     - Khi khóa, bỏ qua các thay đổi trang tự động từ DaVinci.
   - **Mất kết nối:** Giữ hồ sơ cuối, chấm đèn xám, nút chính đổi thành "Kết nối lại".
   - **Tăng điểm tìm kiếm:** Gọi `search_index.set_page_boost(profile.categories)` mỗi khi đổi hồ sơ.

4. **Giai đoạn 4: Ghép nối vào `DockHeaderWidget` và `EdgeDockWindow`**
   - Thêm `btn_pending_chip` vào header (bấm để chuyển ngay sang trang đang chờ).
   - Thêm vạch viền 2px (`_accent_line`) bên mép trái Dock đổi màu theo trang.
   - Đặt `special_container` phía trên lưới tài nguyên, tự động hiển thị khối đặc biệt phù hợp.
   - Bắt phím 1–9, 0, -, = kích hoạt 12 pad âm thanh khi ở trang Cut hoặc Fairlight.

5. **Giai đoạn 5: Kiểm thử tự động (`tests/ui/test_context_router.py`)**
   - Viết các test case cho 7 hồ sơ, cơ chế hoãn đổi, phục hồi bộ nhớ cuộn, khóa hồ sơ và cập nhật điểm SearchIndex.

---

## 4. PHẢN BIỆN THIẾT KẾ (Chỗ sai, thừa, khó làm trong PySide6)

1. **Trang Media & Deliver không có lưới tài nguyên (Mục 4.1):**
   - *Thiết kế quy định:* Media và Deliver có `categories: []` (không có lưới).
   - *Phản biện giao diện:* Trong `EdgeDockWindow`, nếu ẩn toàn bộ lưới `AssetGridView` và `CategoryNavWidget` mà không có vùng nội dung thay thế đủ lớn thì chiều cao Dock (720px) sẽ bị trống trải một mảng đen lớn ở giữa.
   - *Giải pháp tối ưu:* Khi ở trang Media hoặc Deliver, khối đặc biệt (`MediaBlock` hoặc `DeliverBlock`) tự động co giãn (`stretch=1`) chiếm trọn khoảng không gian thân Dock, hiển thị bảng thông tin dự án, danh sách footage, hoặc hàng đợi render trực quan, tạo cảm giác một ứng dụng hoàn thiện và chuyên nghiệp.
2. **Khối 12 Pad âm thanh ở trang Cut và Fairlight (Mục 4.1 & Mockup):**
   - *Thiết kế:* 12 Pad gán phím `1–9, 0, -, =`.
   - *Phản biện kỹ thuật:* Nếu người dùng đang gõ tìm kiếm trong ô `dock_search` mà gõ số (ví dụ tìm "1 triệu đô" hoặc "90s"), các phím số không được kích hoạt pad âm thanh!
   - *Giải pháp:* Phím tắt 12 Pad chỉ kích hoạt khi Dock đang mở VÀ ô tìm kiếm `txt_search` không có focus.
3. **Hiệu ứng Crossfade 140ms trong PySide6:**
   - *Phản biện kỹ thuật:* Trong Qt, việc áp dụng `QGraphicsOpacityEffect` lên toàn bộ container chứa nhiều widget con phức tạp đôi khi làm mất tính sắc nét của font chữ (subpixel font antialiasing bị tắt khi opacity < 1.0) trên Linux X11.
   - *Giải pháp:* Áp dụng `QGraphicsOpacityEffect` chỉ trong khoảng thời gian 140ms chuyển tiếp; khi animation kết thúc (finished), gỡ bỏ effect (`setGraphicsEffect(None)`) để khôi phục 100% chất lượng render font chữ native sắc nét.
4. **Trang Fusion "Hút thành preset" (Mục 4.1 & Prompt điểm 4):**
   - *Thực tế API DaVinci:* Hàm `capture_playhead_text_preset()` trong `src/core/preset_capture.py` yêu cầu clip tại playhead phải có FusionComp (`GetFusionCompByIndex(1)`). Nếu người dùng đang đứng ở track trống hoặc clip video thô chưa tạo Fusion, API sẽ trả về lỗi.
   - *Giải pháp:* Khối `FusionCaptureBlock` kiểm tra comp hiện tại, hiển thị thông báo rõ ràng "Chưa chọn clip Text+/Fusion tại playhead" nếu không tìm thấy comp, và nút hút chỉ kích hoạt khi có comp hợp lệ.

---

## 5. PHƯƠNG ÁN THAY THẾ (Kèm ưu/nhược)

1. **Giải pháp hiển thị khối đặc biệt toàn màn hình cho Media / Deliver:**
   - *Phương án A (Theo mockup):* Ẩn grid, để special block nhỏ ở trên, dưới để trống. (Nhược điểm: khoảng trống vô nghĩa).
   - *Phương án B (Đề xuất):* Cho `special_container` mở rộng `stretch=1` khi trang hiện tại không có danh mục tài nguyên. (Ưu điểm: tận dụng tối đa không gian 420px x 720px hiển thị danh sách render và footage cực kỳ đẹp mắt).
2. **Giải pháp Crossfade 140ms phục hồi antialiasing:**
   - *Phương án A:* Giữ `QGraphicsOpacityEffect` vĩnh viễn trên widget và chỉ chỉnh `setOpacity`. (Nhược điểm: chữ bị mờ viền font trên Linux).
   - *Phương án B:* Gán opacity effect khi bắt đầu fade, chạy `QPropertyAnimation` 140ms, xong gọi `widget.setGraphicsEffect(None)`. (Ưu điểm: chuyển cảnh mượt mà, chữ luôn sắc nét 100%).

---

## 6. RỦI RO & CÁCH TRÁNH

- **Rủi ro 1: Lỗi nhảy giao diện khi người dùng đang thao tác trong Dock:**
  - *Hiện tượng:* Người dùng đang click chọn mẫu ở Edit thì Resolve nhảy sang Color, nếu đổi đột ngột sẽ làm click nhầm vào LUT.
  - *Cách tránh:* Tuân thủ triệt để cơ chế Hoãn đổi: Khi `is_in_dock`, `has_search_text`, `is_sheet_open`, hoặc `is_dragging` là True, mọi tín hiệu đổi trang từ Resolve đều được lưu vào `pending_page` và hiển thị chip chờ `[ Color › ]`. Chỉ chuyển đổi khi chuột hoàn toàn rời khỏi Dock hoặc người dùng chủ động bấm vào chip chờ.
- **Rủi ro 2: Mất trạng thái cuộn của người dùng khi quay lại trang cũ:**
  - *Hiện tượng:* Người dùng đang cuộn tìm chữ ở trang Edit, sang Fusion rồi quay lại Edit bị nhảy lên đầu danh sách.
  - *Cách tránh:* Lớp `ProfileMemory` lưu trữ bộ ba `(category, subcategory, vertical_scroll_value)` của từng trang vào RAM. Khi quay lại trang, khôi phục chính xác danh mục, nhóm con và gọi `scroll_bar.setValue(saved_scroll)` sau khi proxy model hoàn tất lọc.

---

## 7. ƯỚC LƯỢNG

- **Số file mới:** 4 file:
  - `src/ui/lux/context/context_profiles.py`
  - `src/ui/lux/context/special_blocks.py`
  - `src/ui/lux/context/context_router.py`
  - `tests/ui/test_context_router.py`
- **Số file chỉnh sửa:** 3 file:
  - `src/ui/lux/context/__init__.py`
  - `src/ui/lux/shell/dock_header.py`
  - `src/ui/lux/shell/edge_dock.py`
- **Độ khó:** 3.5/5.
- **Phần cần thử trên DaVinci thật:**
  - Chuyển giữa các trang Edit, Fusion, Color, Deliver trong Resolve xem Dock có biến hình đúng hồ sơ tương ứng không.
  - Kiểm tra xem khi rê chuột trong Dock mà Resolve đổi trang thì có hiện chip chờ `<Trang> ›` không.
  - Kiểm tra phím nóng 12 Pad âm thanh ở trang Cut/Fairlight.

---

## 8. CÂU HỎI CHO CHỦ DỰ ÁN

1. **Về thao tác áp dụng LUT ở trang Color:**
   - Khi ở trang Color, bạn muốn double-click vào thẻ LUT áp dụng ngay cho clip tại playhead, hay hiển thị menu hỏi áp dụng cho clip hay cả timeline?
   - *Đề xuất:* Double-click áp dụng ngay cho clip tại playhead; menu "⋯" và Sheet Inspector cung cấp tùy chọn "Áp dụng cho cả timeline".
2. **Về nút chính ở trang Deliver:**
   - Bạn muốn bấm nút chính là "Thêm vào hàng đợi & bắt đầu kết xuất" ngay trên DaVinci Resolve, hay chỉ thêm vào hàng đợi để người dùng bấm Render All trên Resolve?
   - *Đề xuất:* Nút chính "Bắt đầu kết xuất" sẽ thêm job vào hàng đợi và kích hoạt render luôn qua API `AddRenderJob()` và `StartRendering()`.
