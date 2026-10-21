# Kế hoạch BƯỚC 7: Dọn dẹp và gỡ code cũ

> **Mục tiêu**: Loại bỏ triệt để các file UI cũ cồng kềnh đã được thay thế (giảm hơn 6.500 dòng code thừa), gỡ các widget ẩn chống lỗi test trong `tab_assets.py`, viết lại/cập nhật các unit test phụ thuộc vào UI cũ sang test qua Services hoặc UI mới, chuẩn hóa launcher `src/main.py` sang Edge Dock + Director, đảm bảo toàn bộ pytest trong repo pass 100% và cập nhật `README.md`.

---

## 1. Hiểu yêu cầu (3–5 dòng)
Bước 7 là bước dọn dẹp kỹ thuật cuối cùng trong lộ trình UI Redesign sau khi toàn bộ 6 bước trước đã hoàn thiện và chạy ổn định. Nhiệm vụ trọng tâm là xóa sạch các tệp legacy không còn sử dụng (`src/ui/app.py` 4.585 dòng, `tab_sfx.py`, `tab_copilot.py`, `tab_autocut.py`, `tab_export.py`, `panel_edge_rail.py`), dọn sạch các widget ẩn trong `tab_assets.py`, refactor các unit test cũ phụ thuộc vào widget ẩn sang kiểm thử qua kiến trúc mới, cập nhật launcher `src/main.py` và cập nhật tài liệu `README.md` theo diện mạo mới của ChunDVC.

---

## 2. Hiện trạng code (Current State Audit)

| File & Đường dẫn | Trạng thái hiện tại | Kế hoạch xử lý ở Bước 7 |
| :--- | :--- | :--- |
| `src/ui/app.py` (4.585 dòng) | Monolith app cũ, chứa logic 5 tab. Đã được thay thế hoàn toàn bởi `EdgeDockWindow` và `DirectorWindow`. | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/tabs/tab_sfx.py` (265 dòng) | Tab âm thanh cũ. Đã được thay thế bởi Library Âm thanh (Bước 2) và Pad 12 âm thanh trên Cut/Fairlight (Bước 4). | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/tabs/tab_copilot.py` (603 dòng) | Tab Copilot cũ. Đã sáp nhập vào Bước 3 của Director (Bước 6). | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/tabs/tab_autocut.py` (436 dòng) | Tab AutoCut cũ. Đã sáp nhập vào Bước 2 & Bảng nâng cao của Director (Bước 6). | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/tabs/tab_export.py` (492 dòng) | Tab Export cũ. LUT đã chuyển sang Library Color; Render Deliver đã chuyển sang `DeliverBlock` và lệnh `>render`. | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/lux/panel_edge_rail.py` (282 dòng) | `EdgeDrawerWindow` cũ. Đã được thay thế hoàn toàn bởi `EdgeDockWindow` (Bước 3). | **XÓA BỎ HOÀN TOÀN**. |
| `src/ui/tabs/tab_assets.py:3302-3329` | Khối `WIDGETS TƯƠNG THÍCH NGƯỢC` gồm 10 widget ẩn (`group_presets`, `check_subtitle`, `combo_text_preset`...). | **GỠ BỎ TOÀN BỘ KHỐI WIDGET ẨN**. |
| `src/main.py:1-74` | Đang import và khởi động `FloatingBubbleWidget`, `AutoWindow`, `StudioWindow`. | **CẬP NHẬT LAUNCHER**: Khởi chạy `EdgeDockWindow` + Tray icon làm surface chính, hỗ trợ mở Director qua Rail/Palette/CLI. |
| `tests/test_ui.py` (1.511 dòng) | Phụ thuộc vào `src.ui.app.ResolveFlowApp` và các widget ẩn. | **REFACTOR / CẬP NHẬT**: Chuyển các test thuật toán sang mock/services; loại bỏ assert vào widget ẩn. |
| `tests/test_pipeline_worker_shotbook.py` | Import `PipelineWorker` từ `src.ui.app`. | Đổi import sang `src.ui.auto.auto_window` hoặc `src.ui.director`. |
| `tests/test_ui_bgm.py` | Import `TabCopilot` từ `src.ui.tabs.tab_copilot`. | Đổi sang test qua `MusicIndexer` / `Library`. |
| `tests/test_lux_integration.py` | Import `EdgeDrawerWindow` và `ChunDVCApp`. | Đổi sang test `EdgeDockWindow`. |
| `README.md` | Tài liệu giao diện cũ. | Cập nhật hướng dẫn sử dụng Edge Dock, Palette HUD, và ChunDVC Director. |

---

## 3. Phương án đề xuất (Proposed Solution)

### 3.1 Thứ tự thực hiện

```text
Bước 7.1: Refactor các test phụ thuộc widget ẩn & app.py
   │
Bước 7.2: Gỡ bỏ widget ẩn trong tab_assets.py
   │
Bước 7.3: Xóa 6 file legacy (app.py, tab_sfx, tab_copilot, tab_autocut, tab_export, panel_edge_rail)
   │
Bước 7.4: Dọn dẹp export trong src/ui/tabs/__init__.py và src/ui/lux/__init__.py
   │
Bước 7.5: Cập nhật launcher src/main.py
   │
Bước 7.6: Grep toàn bộ repo để quét sạch mọi broken import
   │
Bước 7.7: Chạy full regression pytest (đạt 100% pass)
   │
Bước 7.8: Cập nhật README.md với diện mạo và phím tắt mới
```

### 3.2 Chi tiết xử lý từng phần

1. **Xử lý Widget Ẩn trong `src/ui/tabs/tab_assets.py`**:
   - Xóa bỏ các dòng khai báo và `.hide()` của: `group_presets`, `check_subtitle`, `combo_text_preset`, `btn_preview_preset`, `btn_save_custom_preset`, `combo_split_mode`, `txt_split_limit`, `txt_font`, `txt_size`, `txt_color`.
   - Giữ lại các thuộc tính cấu hình thực tế dạng dữ liệu thuần (properties hoặc dataclass) nếu có module nào cần đọc giá trị font/size mặc định.

2. **Xóa các file Legacy**:
   - `git rm src/ui/app.py`
   - `git rm src/ui/tabs/tab_sfx.py`
   - `git rm src/ui/tabs/tab_copilot.py`
   - `git rm src/ui/tabs/tab_autocut.py`
   - `git rm src/ui/tabs/tab_export.py`
   - `git rm src/ui/lux/panel_edge_rail.py`

3. **Cập nhật `src/main.py`**:
   - Thiết lập chuẩn khởi động:
     - Tạo `QApplication(sys.argv)`.
     - Khởi tạo `EdgeDockWindow` (tích hợp sẵn `ChunDVCTrayIcon`, `DockRailWidget`, `CommandPaletteWindow`, `GlobalHotkeyService`).
     - Quản lý cửa sổ `DirectorWindow` mở theo yêu cầu (khi click nút ▶ trên rail, click Tray menu, hoặc gọi qua phím tắt / CLI `--director`).
     - `FloatingBubbleWidget` được giữ lại dạng tùy chọn mở qua cờ CLI `--bubble` hoặc cài đặt.

4. **Refactor Unit Tests**:
   - `tests/test_pipeline_worker_shotbook.py`: Đổi `from src.ui.app import PipelineWorker as AppPipelineWorker` thành `from src.ui.auto.auto_window import PipelineWorker as AppPipelineWorker`.
   - `tests/test_lux_integration.py`: Chuyển test kiểm tra `EdgeDrawerWindow` sang kiểm tra `EdgeDockWindow`.
   - `tests/test_ui.py`:
     - Chuyển `app_window` fixture khởi tạo sang `AutoWindow` hoặc mock sạch.
     - Loại bỏ các test case chỉ kiểm tra sự tồn tại của widget ẩn (`window.combo_text_preset`, `txt_font`, v.v.).
     - Giữ nguyên toàn bộ các test kiểm tra logic thuật toán cắt thô, nhận diện im lặng, FCPXML sequence.
   - `tests/test_panel_edge_rail.py`: Chuyển sang kiểm thử `DockRailWidget` hoặc gỡ bỏ các test phụ thuộc class cũ.

5. **Cập nhật `README.md`**:
   - Viết lại phần Hướng dẫn sử dụng:
     - **Quick Palette (Raycast HUD)**: Phím tắt `Ctrl+Alt+Space` / `Ctrl+K`, tìm nhanh tài nguyên và lệnh hệ thống `>`.
     - **Edge Dock**: Vạch màu ngữ cảnh theo 7 trang DaVinci, Rail 44px, Drawer 420px, Sheet Inspector tùy biến.
     - **ChunDVC Director**: Wizard 4 bước (Nguồn → Kiểu dựng → Duyệt & Kịch bản → Dựng timeline).
     - **Toast Feedback & Hoàn tác**: Hoàn tác 1-click trong 30 giây.

---

## 4. Phản biện thiết kế (Design Critique)

1. **Về việc xóa `StudioWindow`**:
   - *Phân tích*: Thiết kế Bước 7 ghi "xóa StudioWindow cũ nếu đã được Library bản rộng thay thế". Trong thực tế, `src/ui/studio/studio_window.py` (324 dòng) đóng vai trò là cửa sổ mở rộng độc lập khi người dùng muốn duyệt kho tài nguyên trên toàn màn hình laptop hoặc màn hình thứ hai mà không dính chặt vào mép phải DaVinci Resolve.
   - *Đề xuất*: Giữ lại `StudioWindow` như một cửa sổ độc lập cho chế độ Studio bản rộng (≥1100px) theo đúng tinh thần mục 3.7 của `UX_REDESIGN.md`, hoặc tích hợp mở rộng từ Edge Dock. Không vội vàng xóa nếu creator vẫn cần một cửa sổ kho đạo cụ toàn màn hình.

2. **Về việc giữ lại `src/ui/auto/auto_window.py`**:
   - *Phân tích*: File `auto_window.py` (6.368 dòng) hiện chứa `PipelineWorker` 1.300 dòng, `LLMDirectorWorker`, và các thuật toán FCPXML phức tạp. Yêu cầu của Bước 7 trong `docs/PROMPTS.md` chỉ rõ các file cần xóa là `app.py`, `tab_sfx.py`, `tab_export.py`, `tab_copilot.py`, `tab_autocut.py`, `panel_edge_rail.py`.
   - *Đề xuất*: Tuân thủ đúng danh sách này, không xóa `auto_window.py` ở Bước 7 để tránh làm đứt gãy luồng xử lý nặng của `PipelineWorker`. `DirectorController` tiếp tục sử dụng worker này ổn định.

---

## 5. Phương án thay thế (Alternative Approaches)

- **Phương án xử lý `tests/test_ui.py`**:
  - *Cách 1*: Xóa toàn bộ `tests/test_ui.py` và chỉ dùng 53 test mới.
    - *Ưu điểm*: Nhanh gọn.
    - *Nhược điểm*: Mất đi các test case kiểm tra thuật toán cắt thô và FCPXML đã được kiểm chứng qua nhiều phiên bản.
  - *Cách 2 (Được chọn)*: Giữ lại toàn bộ test logic thuật toán, chỉ refactor fixture `app_window` thành `AutoWindow` hoặc mock component, và xóa bỏ các assertions vào widget ẩn.
    - *Ưu điểm*: Bảo toàn 100% độ tin cậy của core thuật toán, không tạo ra vùng mù kiểm thử.

---

## 6. Rủi ro & Biện pháp phòng ngừa (Risks & Mitigations)

1. **Rủi ro vỡ import ngầm (Hidden Import Breakage)**:
   - Một số test cũ có thể import `TabSFX` hoặc `app.py` trong sub-functions mà static analysis bỏ sót.
   - *Biện pháp*: Dùng `grep -rn` tìm kiếm triệt để tên các file đã xóa trong toàn bộ repo; chạy `pytest` toàn diện trước khi chốt nghiệm thu.
2. **Rủi ro người dùng quen với giao diện quả cầu cũ**:
   - *Biện pháp*: Giữ cờ `--bubble` trong `src/main.py` để người dùng vẫn có thể bật lại Desktop Bubble nếu muốn, trong khi mặc định sử dụng Edge Dock + Tray icon.

---

## 7. Ước lượng

- **Số dòng code dự kiến xóa**: ~6.650 dòng code cũ (`app.py`: 4.585, `tab_copilot`: 603, `tab_export`: 492, `tab_autocut`: 436, `tab_sfx`: 265, `panel_edge_rail`: 282).
- **Tổng số dòng trong `src/ui/`**: Giảm từ ~33.140 dòng xuống còn khoảng ~26.400 dòng (gọn gàng, sạch sẽ hơn 20%).
- **Kiểm thử**: Toàn bộ unit tests trong thư mục `tests/` phải pass không có lỗi.

---

## 8. Câu hỏi cho chủ dự án
- Không có câu hỏi nghẽn; mọi yêu cầu dọn dẹp đã được định nghĩa rất rõ ràng trong `docs/PROMPTS.md` và `docs/BUOC0_DOI_CHIEU.md`.
