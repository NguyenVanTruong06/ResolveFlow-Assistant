# Kế hoạch BƯỚC 5: Quick Palette + Phím tắt toàn cục (HUD kiểu Raycast)

> **Mục tiêu**: Xây dựng Quick Palette (Command HUD nổi 640px) kích hoạt bằng phím tắt toàn cục (mặc định `Ctrl+Alt+Space` hoặc `Ctrl+K`), cho phép tìm kiếm và chèn tài nguyên vào DaVinci Resolve với thời gian dưới 2 giây, điều hướng bàn phím 100%, hỗ trợ lệnh hệ thống tiền tố `>`, và hiển thị Toast phản hồi có nút "Hoàn tác".

---

## 1. Mục tiêu bước & Tiêu chí nghiệm thu (Acceptance Criteria)

Theo yêu cầu tại `docs/PROMPTS.md` (Bước 5) và `docs/UX_REDESIGN.md` (Mục 5):

1. **Tốc độ chèn tài nguyên < 2 giây**: Từ lúc bấm phím tắt (`Ctrl+Alt+Space` hoặc click rail) tới lúc clip nằm trên timeline mất dưới 2 giây (đo trung bình nhiều lần).
2. **Tìm kiếm & Chèn "rung giat"**: Gõ "rung giat" + `Enter` → chèn hiệu ứng shake ngay tại playhead trên track trống an toàn; `Shift+Enter` ép tạo track mới hoặc dán vào Fusion.
3. **Bàn phím & Focus không gián đoạn**: Sau khi bấm `Enter` để chèn hoặc `Esc` để đóng, phím `Space` và `J/K/L` trong Resolve dùng được ngay lập tức (trả focus về Resolve).
4. **Toast phản hồi có Hoàn tác**: Toast 4s xuất hiện tại góc dưới cửa sổ Resolve: `"Đã chèn <tên> · <track> · <TC>  [Hoàn tác]"`. Bấm "Hoàn tác" gọi `InsertService.undo(result)` xóa đúng clip vừa chèn trong vòng 30s.
5. **Bộ lọc cửa sổ hoạt động (Active Window Guard)**: Phím tắt toàn cục chỉ kích hoạt mở Palette khi cửa sổ đang active trước đó là DaVinci Resolve hoặc ChunDVC; ở trình duyệt / ứng dụng khác, phím tắt được trả lại cho hệ điều hành.
6. **Lệnh hệ thống tiền tố `>`**: Gõ `>` mở danh sách lệnh (Director, Render Deliver, Đổi trang Resolve, Ghim/Bỏ ghim Dock).
7. **Phím tắt nhanh kết quả `Alt+1..9`**: Bấm `Alt+1` đến `Alt+9` để chèn ngay kết quả thứ 1 đến thứ 9 mà không cần mũi tên di chuyển.

---

## 2. Đối chiếu code hiện có (Current State Audit)

| Thành phần | Hiện trạng trong codebase | Đánh giá & Hướng xử lý |
| :--- | :--- | :--- |
| **Global Hotkey** | Chỉ có `simulate_paste_hotkey()` gửi phím qua `pyautogui` trong `src/core/fast_paste.py`. Chưa có bộ lắng nghe phím tắt toàn cục. | Tạo mới `src/services/global_hotkey.py`. Dùng `python-xlib` (X11) và `RegisterHotKey` (Windows) để bắt phím mà không hook bàn phím toàn phần. |
| **Command Palette HUD** | Chưa có (chỉ có mockup trong `docs/ui_mockup.html` đoạn `openPalette()`, `renderPalette()`, `runPal()`). | Tạo mới `src/ui/lux/palette/`: `command_palette.py`, `palette_model.py`, `command_registry.py`. |
| **Search Engine** | Đã có `SearchIndex` (Bước 1) với chấm điểm alias, motion tags, prefix, frecency cache và page boost. | `PaletteSearchProvider` kết nối trực tiếp với `SearchIndex` và `AssetRepository` trong RAM. |
| **Insert & Undo** | Đã có `InsertService` (Bước 1) hỗ trợ `insert()`, `undo(result)` trong 30s, mode `auto`, `new_track`, `fusion_paste`. | Tích hợp vào Palette và kết nối với Toast Hoàn tác. |
| **Toast Feedback** | Chưa có Toast hiện đại có nút Hoàn tác trong `src/ui/lux/`. | Tạo mới `src/ui/lux/feedback/toast.py` (`ChunDVCToast`). |
| **Window Anchor** | `src/ui/lux/window_anchor.py` đã có `get_resolve_window_rect()` cho Linux/Windows. | Dùng để căn giữa Palette theo chiều ngang và mép trên ở 22% chiều cao của Resolve. Thêm hàm kiểm tra active window qua `Xlib` / `GetForegroundWindow()`. |

---

## 3. Thiết kế chi tiết kiến trúc & Giao diện (Detailed Design)

```
┌──────────────────────────────────────────────────────────┐
│ GlobalHotkeyService (X11 XGrabKey / Win32 RegisterHotKey)│
│  - Active Window Guard: chỉ bắt khi Resolve/ChunDVC active
└──────────────────────────┬───────────────────────────────┘
                           │ palette_triggered
                           ▼
┌──────────────────────────────────────────────────────────┐
│ CommandPaletteWindow (640px, dynamic height 56px-420px)  │
│  ├── Header: [ ⌕ txt_search ] ── [ Edit · 00:01:12:04 ] │
│  ├── Results List: 7 rows (Thumbnail 64x36, Name, Score) │
│  └── Footer: ⏎ Chèn  ⇧⏎ Dán/Mới  Tab Sheet  Esc Đóng     │
└──────────────┬───────────────────────────┬───────────────┘
               │ Enter                     │ Tab
               ▼                           ▼
┌───────────────────────────────┐  ┌───────────────────────┐
│ InsertService.insert(...)     │  │ Mở Sheet trong Dock   │
│  ├── focus_davinci_resolve()  │  └───────────────────────┘
│  └── ChunDVCToast (Hoàn tác)  │
└───────────────────────────────┘
```

### 3.1 `src/services/global_hotkey.py` (Clean Architecture: Không import QtWidgets/QtGui)
- `GlobalHotkeyService(QObject)`:
  - Signals:
    - `palette_requested = Signal()`
    - `command_mode_requested = Signal()`
  - Thuộc tính:
    - `hotkey_palette`: mặc định `"Ctrl+Alt+Space"` (Linux/Windows)
    - `hotkey_command`: mặc định `"Ctrl+Alt+Shift+Space"`
  - X11 Backend (`python-xlib`):
    - Chạy trong `QThread` riêng (`HotkeyWorkerThread`).
    - Lấy Display X11, Root Window.
    - Đăng ký `XGrabKey` cho phím Space với tổ hợp phím biến thể: Mod1 (Alt), Control, kèm các mặt nạ CapsLock (LockMask), NumLock (Mod2Mask).
    - Vòng lặp `display.next_event()`: khi nhận sự kiện `KeyPress`, kiểm tra `is_target_active()` qua thuộc tính `_NET_ACTIVE_WINDOW`. Nếu là Resolve hoặc ChunDVC, phát signal về main thread; nếu không, bỏ qua.
  - Windows Backend (`ctypes`):
    - Đăng ký `user32.RegisterHotKey` qua `QAbstractNativeEventFilter` bắt sự kiện `WM_HOTKEY`.
  - Fallback / Wayland / Headless:
    - Không crash nếu X11 không có hoặc đang test headless; hỗ trợ phương thức thủ công `simulate_hotkey(mode)` để test.

### 3.2 `src/ui/lux/palette/command_registry.py`
- `CommandItem` dataclass: `id`, `name`, `meta`, `callback`, `icon`
- `CommandRegistry`:
  - Quản lý danh mục lệnh hệ thống tiền tố `>`:
    - `>cắt khoảng lặng`: Mở Director bước 1 (AutoCut)
    - `>mở director`: Kích hoạt cửa sổ Director
    - `>render tiktok`: Thêm vào queue render TikTok 9:16
    - `>render youtube`: Thêm vào queue render YouTube 1080p
    - `>chuẩn hóa loudness`: Chuẩn hóa audio track về -14 LUFS
    - `>ghim / bỏ ghim dock`: Đổi trạng thái ghim Dock
    - `>đi tới <trang>`: Chuyển trang Resolve tương ứng (Edit, Fusion, Color, Fairlight, Cut, Deliver, Media)
  - `search(query: str) -> List[CommandItem]` (fuzzy/contains).

### 3.3 `src/ui/lux/palette/palette_model.py`
- `PaletteItem`:
  - `kind`: `"asset"` hoặc `"command"`
  - `data`: Dict thông tin asset hoặc CommandItem
  - `title`: Tên hiển thị
  - `meta`: Loại danh mục + thời lượng hoặc lý do khớp (e.g. `chuyển động · rung 92%`)
  - `shortcut_hint`: `⏎`, `⌥1`..`⌥9`
- `PaletteSearchProvider`:
  - Nhận `query` từ ô tìm:
    - Nếu `query.startswith('>')`: tìm kiếm trong `CommandRegistry`.
    - Nếu `query == ""`: trả về 3 nhóm:
      1. Gần đây (Recent - lấy từ frecency)
      2. Yêu thích (Favorites)
      3. Gợi ý theo trang hiện tại (từ `ContextProfile.categories`)
    - Nếu `query` có chữ: gọi `SearchIndex.search(query, limit=7)`.

### 3.4 `src/ui/lux/palette/command_palette.py`
- `CommandPaletteWindow(QWidget)`:
  - Cờ cửa sổ: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`.
  - Thuộc tính: `WA_TranslucentBackground = True`.
  - Kích thước: Rộng 640px cố định. Chiều cao co giãn từ 56px (khi trống kết quả) tới tối đa 420px (7 dòng × 48px + 56px header + 28px footer).
  - Vị trí: Căn giữa ngang theo khung cửa sổ Resolve (hoặc màn hình chính), mép trên đặt ở 22% chiều cao của Resolve.
  - Giao diện & Màu sắc:
    - Nền `#131316` độ mờ 94% (`background: rgba(19, 19, 22, 0.95);`).
    - Viền bo góc 12px, border 1px `#33333a`.
  - Header (56px):
    - Biểu tượng `⌕` màu tím.
    - Ô nhập `txt_search`: font 14px, không border, placeholder "Tìm asset hoặc lệnh (>)...". Focus ngay khi cửa sổ hiện.
    - Chip ngữ cảnh bên phải: `lbl_context` hiển thị `● Edit · 00:01:12:04` với màu chấm theo profile.
  - Danh sách kết quả (tối đa 7 dòng, mỗi dòng 48px):
    - Thumbnail 64×36: bo góc 4px. Dòng đang chọn phát preview sau 150ms hover/active.
    - Nhãn tên asset (`#ffffff`, 12.5px font-weight: 600).
    - Nhãn phụ: Loại + lý do khớp (e.g. `Chuyển động · rung 92%`) màu `#c4b5fd`.
    - Phím tắt gợi ý bên phải: `⏎` cho dòng đang chọn, `⌥1`..`⌥9` cho các dòng tiếp theo.
  - Footer (28px):
    - Font 11px, màu `#71717a`:
      `⏎ Chèn   ⇧⏎ Dán Fusion / Track mới   Tab Tùy chỉnh   Ctrl⏎ Yêu thích   Esc Đóng`
  - Bàn phím:
    - `↑` / `↓`: Đổi vị trí chọn `selected_index`.
    - `Enter`: Chèn tại playhead qua `InsertService`, đóng Palette, trả focus Resolve, bật Toast.
    - `Shift+Enter`: Ở Fusion dán vào Fusion; ở Edit ép tạo track mới.
    - `Alt+1..9`: Chèn nhanh kết quả thứ n.
    - `Tab`: Mở Dock ở vị trí asset đang chọn kèm Sheet Inspector.
    - `Ctrl+Enter`: Bật/tắt trạng thái yêu thích.
    - `Esc`: Đóng Palette, trả focus Resolve.

### 3.5 `src/ui/lux/feedback/toast.py`
- `ChunDVCToast(QWidget)`:
  - Cửa sổ frameless nhỏ nổi ở góc dưới phải của Resolve (cách đáy 24px, cách phải 16px).
  - Tự động đóng sau 4 giây bằng `QTimer` và hiệu ứng mờ dần (200ms fade-out).
  - Thành công:
    - Nền `#18181b`, viền 1px `#7c3aed`, bo 8px.
    - Nhãn: `"Đã chèn <tên> · <track> · <TC>"`.
    - Nút bấm: `"Hoàn tác"`. Khi bấm:
      - Gọi `InsertService.undo(result)`.
      - Đổi text thành `"Đã hoàn tác chèn <tên>"`.
      - Tự đóng sau 1.5s.
  - Thất bại:
    - Nền `#241215`, viền 1px `#ef4444`.
    - Nhãn: `"Lỗi: <nguyên nhân>"` (ví dụ: "Không còn track trống").
    - Nút: `"Tạo track mới"` (gọi lại `insert(mode="new_track")`).

---

## 4. Kế hoạch tương thích ngược (Backward Compatibility)

1. Cấu trúc hiện có của `EdgeDockWindow`, `TabAssets`, `ResolveGateway`, `InsertService` không bị thay đổi giao diện công khai (public API).
2. `EdgeDockWindow` đã có signal `palette_requested`, nay được liên kết với `CommandPaletteWindow.show_palette()`.
3. Nếu hệ thống chạy trong môi trường Wayland hoặc headless không hỗ trợ XGrabKey, `GlobalHotkeyService` tự động chuyển sang chế độ dự phòng mà không gây crash ứng dụng.
4. Toast hoạt động độc lập và không can thiệp vào timeline ngoài việc gọi `InsertService.undo()`.

---

## 5. Kịch bản kiểm thử (Test Matrix)

### 5.1 Unit Tests (`tests/ui/test_command_palette.py`)
1. `test_command_registry_search`: Kiểm tra đăng ký lệnh và tìm kiếm với tiền tố `>`.
2. `test_palette_model_search_and_modes`: Kiểm tra hiển thị ô trống (Gần đây/Yêu thích/Gợi ý), lọc từ khóa, tính toán phím tắt `⌥1`..`⌥9`.
3. `test_command_palette_window_geometry_and_layout`: Căn giữa Resolve, mép trên 22%, chiều cao co giãn theo số lượng kết quả (56px → 420px).
4. `test_command_palette_keyboard_navigation`: Bấm `ArrowDown`/`ArrowUp`, `Enter` chèn, `Alt+1` chèn nhanh, `Esc` đóng.
5. `test_command_palette_shift_enter_modes`: Kiểm tra `Shift+Enter` tạo track mới trên Edit hoặc dán Fusion trên Fusion.
6. `test_toast_feedback_success_and_undo`: Hiển thị toast thành công, bấm nút Hoàn tác gọi `InsertService.undo` và cập nhật thông báo.
7. `test_toast_feedback_error_action`: Hiển thị toast lỗi với nút hành động sửa lỗi.
8. `test_global_hotkey_service_window_guard`: Kiểm tra hotkey chỉ phát tín hiệu khi Resolve/ChunDVC là cửa sổ active.

### 5.2 Integration Tests
- Chạy toàn bộ regression suite:
  - `tests/services/` (14 tests)
  - `tests/ui/test_asset_library_model_view.py` (7 tests)
  - `tests/ui/test_edge_dock.py` (6 tests)
  - `tests/ui/test_context_router.py` (7 tests)
  - `tests/ui/test_command_palette.py` (mới)

---

## 6. Rủi ro kỹ thuật & Giải pháp dự phòng

| Rủi ro | Giải pháp kỹ thuật |
| :--- | :--- |
| **XGrabKey chặn toàn bộ bàn phím nếu lỗi** | Chỉ grab duy nhất phím Space kèm modifier (Mod1 + Control), không bao giờ dùng `keyboard.hook()` hoặc `pynput` toàn cục. Đảm bảo giải phóng `XUngrabKey` khi thoát. |
| **NumLock / CapsLock làm sai lệch modifier** | Grab đồng thời các biến thể tổ hợp: `mask`, `mask | Mod2Mask`, `mask | LockMask`, `mask | Mod2Mask | LockMask`. |
| **Toast bị che bởi các cửa sổ khác** | Đặt cờ `Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool` cho Toast và cập nhật tọa độ bám theo góc dưới phải của Resolve. |
| **Tìm kiếm bị giật khi gõ nhanh** | `SearchIndex` đã được tối ưu chạy hoàn toàn trong RAM (< 16ms cho 600 tài nguyên). Không gọi disk/API trong luồng lọc của Palette. |

---

## 7. Trình tự thực hiện (Turn 2 Execution Plan)

1. **Commit 1**: Tạo `src/services/global_hotkey.py` (X11 & Windows hotkey listener, window guard).
2. **Commit 2**: Tạo `src/ui/lux/palette/command_registry.py` & `palette_model.py`.
3. **Commit 3**: Tạo `src/ui/lux/feedback/toast.py` (`ChunDVCToast` với nút Hoàn tác).
4. **Commit 4**: Tạo `src/ui/lux/palette/command_palette.py` (`CommandPaletteWindow`).
5. **Commit 5**: Tích hợp Palette & Toast vào `EdgeDockWindow` và viết test suite `tests/ui/test_command_palette.py`.
6. **Commit 6**: Chạy full test regression và xuất báo cáo Bước 5.

---

## 8. Định nghĩa "Xong" (Definition of Done)

- [ ] File `src/services/global_hotkey.py` tuân thủ Clean Architecture (không import `QtWidgets` hay `QtGui`).
- [ ] `CommandPaletteWindow` hiển thị chuẩn 640px, nền `#131316` 94%, viền 12px, căn 22% chiều cao Resolve.
- [ ] Gõ tức thì lọc < 16ms, hỗ trợ `↑↓`, `Enter`, `Shift+Enter`, `Alt+1..9`, `Tab`, `Ctrl+Enter`, `Esc`.
- [ ] Tiền tố `>` chạy các lệnh hệ thống.
- [ ] Toast 4s hiển thị sau khi chèn kèm nút Hoàn tác hoạt động chính xác.
- [ ] Toàn bộ test suite (unit + integration + regression) đạt 100% pass.
