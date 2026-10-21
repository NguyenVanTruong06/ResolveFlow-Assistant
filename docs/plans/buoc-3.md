# KẾ HOẠCH TRIỂN KHAI BƯỚC 3: DOCK MỚI (EDGE RAIL + DRAWER)

> **Mục tiêu:** Xây dựng hệ thống Edge Dock mới (`EdgeDockWindow`) đạt tiêu chuẩn Akitt Pro với máy trạng thái 6 cấp, neo chuẩn theo cửa sổ DaVinci Resolve (`ResolveWindowAnchor`), kiểm soát focus thông minh (không cướp phím của Resolve), tích hợp thanh hành động dính đáy, điều hướng danh mục chip ngang, Sheet Inspector tùy chỉnh thích ứng và khay hệ thống (`QSystemTrayIcon`).
> **Quy tắc:** Tuân thủ quy trình 2 lượt — Lượt 1 chỉ lập kế hoạch, không sửa bất kỳ dòng code nào.

---

## 1. HIỂU YÊU CẦU

Bước 3 thay thế thanh trượt thô sơ `EdgeDrawerWindow` cũ bằng **Edge Dock mới** (`EdgeDockWindow`) đạt chuẩn Akitt Pro với máy trạng thái 6 cấp (Ẩn / Rail 44px / Peek 56px / Mở 320–420px / Ghim / Rộng 760px), bám theo khung cửa sổ Resolve (`ResolveWindowAnchor`). Trọng tâm sống còn là **không bao giờ chiếm đoạt bàn phím của DaVinci Resolve**: click/chèn trả ngay focus cho Resolve để phím Space/JKL dùng được tức thì, chỉ nhận focus khi tương tác ô nhập (`QLineEdit`). Giao diện tích hợp Header 44px, chip danh mục ngang cuộn được, Lưới `AssetGridView` (từ Bước 2), thanh hành động dính đáy 56px kèm menu "⋯", thanh trạng thái 22px kết nối `ResolveStateService`, Sheet Inspector tùy chỉnh trượt mượt mà (đáy/phải theo breakpoint), và `QSystemTrayIcon` thay thế `FloatingBubble` (chuyển quả cầu thành tùy chọn tắt mặc định).

---

## 2. HIỆN TRẠNG CODE

1. [`src/ui/lux/panel_edge_rail.py:24-283`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/panel_edge_rail.py#L24-L283):
   - `EdgeDrawerWindow` hiện tại là một cửa sổ đơn giản chỉ có 2 trạng thái cố định (`COLLAPSED_WIDTH = 40`, `EXPANDED_WIDTH = 440`) và peek thủ công bằng cách dịch tọa độ `PEEK_OFFSET = 12`.
   - Cửa sổ dùng `QStackedWidget` nhúng `MacroGridWidget`, không có máy trạng thái đầy đủ, không có thanh hành động dính đáy, không có Sheet Inspector, không có thanh trạng thái playhead timecode.
2. [`src/ui/lux/window_anchor.py:39-191`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/window_anchor.py#L39-L191):
   - `get_resolve_window_rect()` dò tìm tọa độ Resolve bằng `wmctrl` / `xdotool` (Linux) hoặc `EnumWindows` (Windows).
   - `ResolveWindowAnchor` giám sát thay đổi vị trí Resolve và phát tín hiệu `anchor_updated(rect)`.
3. [`src/ui/bubble/floating_bubble.py:34-714`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/bubble/floating_bubble.py#L34-L714):
   - Quả cầu nổi `FloatingBubbleWidget` chiếm một cửa sổ riêng trên màn hình với cờ `WindowStaysOnTopHint`.
   - Hiện đang là launcher chính trong `main.py:19-63`. Cần chuyển đổi sang `QSystemTrayIcon` và biến Bubble thành tùy chọn tắt mặc định.
4. [`src/main.py:14-70`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/main.py#L14-L70):
   - Khởi động trực tiếp `FloatingBubbleWidget` và kết nối nó tới `AutoWindow` và `StudioWindow`.
5. [`src/core/fast_paste.py:16-100`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/core/fast_paste.py#L16-L100):
   - Hàm `focus_davinci_resolve()` đã có sẵn logic đưa Resolve ra foreground đa nền tảng (`wmctrl` / `xdotool` trên Linux, `user32.SetForegroundWindow` trên Windows).
6. [`src/ui/lux/library/asset_grid.py:28-260`](file:///media/vanntruongg/Documents/Development/Python/d%E1%BB%B1%20%C3%A1n%20c%C3%A1%20nh%C3%A2n/ResolveFlow-Assistant/src/ui/lux/library/asset_grid.py#L28-L260):
   - `AssetGridView` đã hoàn thiện ở Bước 2, hỗ trợ IconMode, tính cột động, double-click và drag & drop chèn tài nguyên.

---

## 3. PHƯƠNG ÁN ĐỀ XUẤT

Tuân thủ nguyên tắc không xóa code cũ, tách module mạch lạc theo cấu trúc thiết kế:

### 3.1. Danh sách file tạo mới & chỉnh sửa

```text
src/ui/lux/
├── shell/
│   ├── __init__.py               # Khởi tạo package shell
│   ├── dock_state.py             # Enum DockState, timers, logic chuyển trạng thái
│   ├── dock_rail.py              # Thanh Rail 44px (Logo, icon danh mục nhanh, dot trạng thái)
│   ├── dock_header.py            # Header 44px (Chip trang, ô tìm kiếm, ghim, rộng, thu)
│   ├── dock_status.py            # Thanh trạng thái 22px đáy (kết nối, tên timeline, playhead)
│   ├── edge_dock.py              # EdgeDockWindow chính: máy trạng thái 6 cấp, focus, phím tắt
│   └── tray.py                   # ChunDVCTrayIcon: QSystemTrayIcon thay thế FloatingBubble
├── library/
│   ├── category_nav.py           # Chip danh mục ngang cuộn được + nhóm con
│   └── action_bar.py             # Thanh hành động dính đáy 56px: Info + Primary + Sheet + Menu ⋯
├── inspector/
│   ├── __init__.py               # Khởi tạo package inspector
│   ├── inspector_host.py         # SheetInspector: Overlay trượt đáy 62% hoặc trượt phải 320px
│   └── editors.py                # TextPresetEditor, AudioEditor, LutEditor, MacroEditor
tests/ui/
└── test_edge_dock.py             # Bộ test unit máy trạng thái, focus, geometry, sheet inspector
```

### 3.2. Thứ tự triển khai chi tiết

1. **Giai đoạn 1: Máy trạng thái và Cấu trúc hình học Dock (`dock_state.py`, `dock_rail.py`, `dock_header.py`, `dock_status.py`)**
   - Định nghĩa 6 trạng thái trong `DockState`: `HIDDEN` (0/2px), `RAIL` (44px), `PEEK` (56px), `OPEN` (320..420px), `PINNED` (420px cố định), `WIDE` (760px).
   - Bộ đếm thời gian:
     - Rê vào rail > 80ms → `PEEK`; > 450ms → `OPEN`.
     - Rời chuột > 300ms → thoát `PEEK`; > 600ms → thu về `RAIL` hoặc `HIDDEN`, TRỪ KHI: ghim (`is_pinned`), ô tìm có chữ (`has_search_text`), sheet đang mở (`is_sheet_open`), đang kéo thả (`is_dragging`), hoặc menu ngữ cảnh đang mở (`is_menu_open`).
   - Animation: `QVariantAnimation` thời gian mở 200ms `OutCubic`, thời gian thu 160ms `InCubic`. Chỉ cập nhật geometry, tuyệt đối không dựng lại (re-render) widget.
   - Sizing: `clamp(320, 30% width Resolve, 420px)`. Khi Resolve < 900px: Dock nổi sang mép phải ngoài khung Resolve (có kiểm tra biên màn hình).

2. **Giai đoạn 2: Điều hướng danh mục & Thanh hành động (`category_nav.py`, `action_bar.py`)**
   - `category_nav.py`: Hàng chip danh mục chính dạng ngang cuộn mượt (`QScrollArea` ẩn scrollbar): `(Chữ)` `Chuyển cảnh` `Meme` `Lớp phủ` `Âm thanh` `›`. Hàng dưới là chip nhóm con nhỏ (`Tất cả`, `Kinetic`, `Bút dạ`, `Neon`...). Bỏ hoàn toàn rail dọc 78px có chữ bị cắt.
   - `action_bar.py`:
     - Dòng 1: Thông tin tóm tắt thẻ đang chọn (`Alex Hormozi Pop · 4.0s · V2 tự động`).
     - Dòng 2: Nút chính lớn (`Chèn tại playhead ⏎`), Nút `Tùy chỉnh (Tab)`, Nút menu `⋯`.
     - Menu `⋯`: Dán vào Fusion (⇧⏎), Sao chép code Fusion, Cài vào thư mục Fusion, Chèn trên track mới, Thêm/Bỏ Yêu thích (Ctrl+⏎), Mở thư mục chứa.

3. **Giai đoạn 3: Sheet Inspector (`inspector_host.py`, `editors.py`)**
   - `SheetInspector` đóng vai trò là một container trượt overlay phủ lên phần nội dung:
     - Chiều rộng Dock < 560px: Trượt từ đáy lên, chiếm 62% chiều cao Dock.
     - Chiều rộng Dock 560–1099px: Trượt từ cạnh phải sang với độ rộng cố định 320px.
   - Nội dung bao gồm:
     - Header: Thanh grab tay nắm, tiêu đề mẫu, nút đóng (✕).
     - Live 16:9 preview widget ở trên cùng.
     - Editors chuyên biệt theo category:
       * `TextPresetEditor`: Nội dung `QLineEdit`, thanh trượt thời lượng (1-10s), các chip màu (Chữ, Nhấn, Viền, Hộp), segmented buttons chuyển động (`Pop`, `Bounce`, `Gõ chữ`) và đường cong (`Spring`, `Ease`, `Linear`).
       * `AudioEditor`: Âm lượng bù (-30dB đến 0dB), Fade in/out slider (0 - 2.0s), nghe thử khi rê.
       * `LutEditor`: Cường độ (0 - 100%), Phạm vi (Clip hiện tại / Cả timeline), bỏ qua clip đã có LUT.
       * `MacroEditor`: Thời lượng và độ mạnh hiệu ứng.
     - Bộ chọn track an toàn: Tự động (track an toàn trống) / Track cụ thể / Track mới.
     - Nút chèn nhanh tại đáy sheet: `Chèn tại playhead ⏎`.

4. **Giai đoạn 4: Ghép nối `EdgeDockWindow`, Quản lý Focus & Bàn phím (`edge_dock.py`)**
   - Đặt cờ cửa sổ: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, `WA_TranslucentBackground = True`, `WA_ShowWithoutActivating = True`.
   - Phân cấp Focus nghiêm ngặt:
     * Tất cả các nút bấm, lưới thẻ, scroll area: `setFocusPolicy(Qt.FocusPolicy.NoFocus)`.
     * Khi click thẻ hoặc bấm nút chèn: lập tức gọi `fast_paste.focus_davinci_resolve()`.
     * Chỉ riêng `dock_header.search_input` và các ô nhập văn bản trong Sheet Inspector nhận `StrongFocus`.
   - Phím tắt bàn phím khi Dock đang mở (không trong ô gõ chữ):
     * `Enter`: Thực thi nút hành động chính (`do_primary`).
     * `Tab`: Đóng/Mở Sheet Inspector.
     * `Esc`: Đóng Sheet Inspector (nếu đang mở) hoặc thu gọn Dock và gọi `focus_davinci_resolve()`.

5. **Giai đoạn 5: Tương tác Kéo thả & Khay hệ thống (`tray.py`)**
   - Kéo thả: Hiển thị pixmap / tooltip nổi gắn theo con trỏ chuột. Khi rê trên Dock: `"Thả trên DaVinci để chèn"`, khi thả trên DaVinci: `"Thả để chèn tại playhead <Timecode>"`.
   - `tray.py`: `ChunDVCTrayIcon` (`QSystemTrayIcon`) với menu: Mở Dock, Mở Director, Mở Kho Đạo Cụ, Cài đặt, Thoát.
   - Cung cấp cờ cấu hình `USE_NEW_EDGE_DOCK = True` và `SHOW_FLOATING_BUBBLE = False` (mặc định tắt quả cầu nổi).

---

## 4. PHẢN BIỆN THIẾT KẾ (Chỗ sai, thừa, khó làm trong PySide6)

1. **Xung đột cờ `Qt.WindowDoesNotAcceptFocus` và ô nhập `QLineEdit` (Mục 3.5 & Prompt điểm 3):**
   - *Thiết kế yêu cầu:* Đặt cờ `Qt.WindowDoesNotAcceptFocus` lên Dock để không cướp focus của Resolve; khi click ô search hoặc sheet thì mới cho nhận focus.
   - *Phản biện kỹ thuật:* Trong PySide6 / X11 / Win32, `WindowDoesNotAcceptFocus` là cờ ở cấp cửa sổ gốc của OS. Khi cờ này bật, hệ điều hành **chặn hoàn toàn mọi sự kiện bàn phím gửi đến toàn bộ cửa sổ và các widget con**. `QLineEdit` sẽ không bao giờ nhận được `QFocusEvent` và không thể gõ được bất kỳ ký tự nào từ bàn phím vật lý (đã kiểm chứng qua thực nghiệm code PySide6). Hơn nữa, việc bật/tắt cờ này lúc runtime bằng `setWindowFlags()` sẽ hủy native window handle và tạo lại cửa sổ, gây giật lag và chớp đen (flicker).
2. **Kẹt tọa độ ngoài màn hình khi Resolve < 900px (Mục 3.2):**
   - *Thiết kế yêu cầu:* "Cửa sổ Resolve hẹp hơn 900px → Dock nổi ra ngoài mép phải khung Resolve thay vì đè lên."
   - *Phản biện kỹ thuật:* Nếu Resolve đang nằm sát mép phải màn hình (hoặc ở màn hình đơn độ phân giải 1366x768 / 1920x1080), việc đẩy Dock ra ngoài mép phải của Resolve (`ax + aw`) sẽ làm Dock bị văng ra **ngoài màn hình vật lý**, người dùng không thể nhìn thấy hoặc thao tác được.
3. **Thanh trượt Sheet Inspector đáy 62% vs trượt phải 320px (Mục 3.1 & 3.3):**
   - *Thiết kế yêu cầu:* Đáy trượt 62% khi < 560px, phải trượt 320px khi 560–1099px.
   - *Phản biện:* Khi người dùng đang mở sheet ở chế độ 420px (sheet đáy), rồi bấm nút Rộng `⤢` (760px), nếu không có cơ chế chuyển đổi hướng trượt (re-anchor) linh hoạt thì giao diện sheet sẽ bị kẹt giữa 2 layout hình học, gây méo mó hoặc tràn viền.
4. **Nhãn kéo thả trên OS (`#dragLabel` theo con trỏ) (Mục 3.6):**
   - *Thiết kế yêu cầu:* Nhãn nổi theo con trỏ khi kéo thả: "Thả để chèn tại playhead...".
   - *Phản biện kỹ thuật:* Trong Qt, khi `QDrag.exec()` đang chạy, sự kiện chuột bị Qt modal drag loop giữ chặt. Một widget con thông thường không thể tự cập nhật vị trí di chuột trên toàn màn hình desktop ngoài phạm vi cửa sổ của nó trừ khi dùng native mouse hook hoặc custom pixmap trên `QDrag`.

---

## 5. PHƯƠNG ÁN THAY THẾ (Kèm ưu/nhược điểm)

1. **Giải pháp Focus tối ưu (Thay cho cờ `WindowDoesNotAcceptFocus`):**
   - *Cách làm:* Giữ cờ `Qt.WidgetAttribute.WA_ShowWithoutActivating = True` trên `EdgeDockWindow`. Không dùng `WindowDoesNotAcceptFocus`.
   - Đặt `setFocusPolicy(Qt.FocusPolicy.NoFocus)` trên tất cả các widget thụ động (card grid, button, rail, scroll area, status bar).
   - Sau bất kỳ thao tác click thẻ, double-click, chọn danh mục, hoặc chèn tài nguyên nào, gọi ngay `fast_paste.focus_davinci_resolve()`. Như vậy, ngay khi người dùng click thẻ, DaVinci Resolve vẫn lập tức giữ focus bàn phím (Space, J/K/L hoạt động ngay lập tức).
   - Chỉ riêng `QLineEdit` trong header và các ô nhập văn bản trong Sheet Inspector là giữ `Qt.FocusPolicy.StrongFocus`. Khi người dùng click vào ô này, cửa sổ nhận focus để gõ chữ. Khi bấm `Esc`, `Enter` hoặc rê chuột ra ngoài, gọi `focus_davinci_resolve()` trả lại focus cho Resolve.
   - *Ưu điểm:* 100% gõ chữ được bình thường, không flicker tạo lại cửa sổ, Resolve luôn giữ được quyền điều khiển phím nóng.
2. **Giải pháp Neo mép an toàn (Boundary Clamping):**
   - Khi Resolve < 900px, tính vị trí Dock theo công thức:
     `ideal_x = ax + aw`
     Nếu `ideal_x + dock_w > screen_x + screen_w` (tràn màn hình bên phải):
     Kiểm tra nếu phía bên trái còn chỗ (`ax - dock_w >= screen_x`), đẩy Dock sang mép trái của Resolve. Nếu cả 2 bên đều hẹp, neo vào mép phải bên trong của Resolve với kích thước thu gọn tối thiểu `320px`.
   - *Ưu điểm:* Đảm bảo Dock luôn nằm trọn vẹn trong vùng hiển thị của màn hình vật lý, không bao giờ bị biến mất ra ngoài biên.
3. **Cơ chế Sheet Inspector thích ứng hình học động:**
   - Xây dựng `SheetInspector` với hai chế độ chuyển động:
     - `Mode.BOTTOM`: Dịch chuyển `pos_y` từ `height()` lên `height() * 0.38`.
     - `Mode.RIGHT`: Dịch chuyển `pos_x` từ `width()` sang `width() - 320`.
   - Kết nối với sự kiện `resizeEvent` của Dock: khi chiều rộng vượt ngưỡng 560px (hoặc thu nhỏ lại), tự động điều chỉnh geometry đích và animate mượt mà.
4. **Giải pháp Nhãn kéo thả (Drag Cursor Pixmap & Tooltip):**
   - Khi gọi `QDrag.exec()`, tạo một `QPixmap` vẽ huy hiệu bán trong suốt dạng viên thuốc (pill) với text: `"🎞️ Chèn tại playhead <TC>"`. Đặt làm hình đại diện kéo của `QDrag.setPixmap()`.
   - Trên sự kiện `dragMoveEvent` của `EdgeDockWindow`, nếu con trỏ vẫn nằm trong Dock, chuyển `dropAction` thành `IgnoreAction` và hiển thị tooltip hoặc cập nhật con trỏ cấm thả; khi chuột ra ngoài Resolve, cho phép drop.

---

## 6. RỦI RO & CÁCH TRÁNH

- **Rủi ro 1: Xung đột với `FloatingBubble` trong `main.py`:**
  - *Hiện tượng:* Cả Bubble và Dock cùng chạy, đè lên nhau gây rối mắt.
  - *Cách tránh:* Trong Bước 3, đưa Dock mới và `ChunDVCTrayIcon` vào hoạt động song song. Đặt cờ `SHOW_FLOATING_BUBBLE = False` làm mặc định, Bubble chỉ hiện khi người dùng bật lại trong cài đặt.
- **Rủi ro 2: Khựng/lag giao diện khi animate trượt Dock:**
  - *Hiện tượng:* Nếu layout phức tạp hoặc có widget con bị dựng lại (re-render) khi thay đổi geometry, animation 200ms sẽ bị giật khung hình.
  - *Cách tránh:* Tuân thủ nghiêm ngặt quy tắc của thiết kế: Chỉ thay đổi `geometry` thông qua `QVariantAnimation`, không gọi `clear()` hay tạo mới widget trong quá trình trượt. Lưới `AssetGridView` dùng chế độ `Batched` IconMode ảo hóa của Bước 2 nên việc đổi kích thước rất mượt mà.
- **Rủi ro 3: Mất phản hồi bàn phím trên DaVinci Resolve:**
  - *Hiện tượng:* Sau khi click vào Dock, người dùng bấm Space để play video nhưng Resolve không nhận tín hiệu.
  - *Cách tránh:* Mọi click trên card và nút chèn đều phát tín hiệu và kích hoạt `fast_paste.focus_davinci_resolve()`. Thêm unit test và kiểm tra thực tế trên Linux X11 và Windows.

---

## 7. ƯỚC LƯỢNG

- **Số file mới:** 10 file:
  - `src/ui/lux/shell/dock_state.py`
  - `src/ui/lux/shell/dock_rail.py`
  - `src/ui/lux/shell/dock_header.py`
  - `src/ui/lux/shell/dock_status.py`
  - `src/ui/lux/shell/edge_dock.py`
  - `src/ui/lux/shell/tray.py`
  - `src/ui/lux/library/category_nav.py`
  - `src/ui/lux/library/action_bar.py`
  - `src/ui/lux/inspector/inspector_host.py`
  - `src/ui/lux/inspector/editors.py`
  - `tests/ui/test_edge_dock.py`
- **Số file chỉnh sửa:** 2 file (`src/ui/lux/__init__.py`, `src/ui/lux/library/__init__.py`).
- **Độ khó:** 4/5 (Cần quản lý trơn tru máy trạng thái chuyển động trễ 80ms/450ms/600ms, cờ focus OS không làm kẹt Resolve, và Sheet Inspector co giãn 2 chiều).
- **Phần cần thử trên DaVinci thật:**
  - Mở Dock, click vào thẻ card, bấm phím Space xem DaVinci Resolve có chạy/dừng playback ngay lập tức không.
  - Double-click thẻ để chèn, kiểm tra focus có trở về Resolve để bấm phím J/K/L được ngay không.
  - Kiểm tra neo cửa sổ theo Resolve (`ResolveWindowAnchor`) khi thay đổi kích thước và di chuyển cửa sổ DaVinci Resolve.

---

## 8. CÂU HỎI CHO CHỦ DỰ ÁN

1. **Về phím tắt Tab khi ô tìm kiếm đang có con trỏ:**
   - Trong giao diện chuẩn, phím `Tab` thường chuyển focus sang widget tiếp theo. Khi ô tìm kiếm `dock_search` đang focus, bạn muốn phím `Tab` mở Sheet Inspector luôn, hay chỉ khi con trỏ KHÔNG nằm trong ô gõ chữ thì `Tab` mới mở Sheet Inspector?
   - *Đề xuất:* Khi ô tìm kiếm đang focus, `Tab` chuyển focus xuống danh sách kết quả; khi đang duyệt lưới thẻ hoặc không gõ chữ, `Tab` dùng để mở/đóng Sheet Inspector.
2. **Về hành vi click khay hệ thống (Tray Icon):**
   - Khi click chuột trái vào biểu tượng khay hệ thống, bạn muốn mở trực tiếp **Edge Dock**, hay mở menu ngữ cảnh (Dock / Director / Studio)?
   - *Đề xuất:* Click chuột trái = Bật/Tắt Edge Dock nhanh; Click chuột phải = Hiện menu điều hướng đầy đủ.
