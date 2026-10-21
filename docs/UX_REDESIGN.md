# ChunDVC — UX/UI Audit & Tái thiết kế

Ngày 21/10/2026

## Tóm tắt điều hành

Đề xuất: chia ChunDVC theo **tần suất thao tác** chứ không theo loại tài nguyên. Thao tác vi mô, lặp lại hàng trăm lần mỗi buổi dựng (chèn chữ, shake, swoosh) đi vào **Palette** và **Dock**. Thao tác vĩ mô, chạy vài lần mỗi dự án (cắt thô, lên kịch bản, xuất bản) đi vào **Director**.

Hiện tại sản phẩm có 5 bề mặt chồng chéo: `app.py` (tab cũ), `AutoWindow`, `StudioWindow`, `FloatingBubble` và `EdgeDrawer`. Brief mô tả "5 tab", nhưng người dùng thực tế gặp 2 cửa sổ lớn cộng một quả cầu cộng một ngăn kéo. Đây là gốc của cả 5 nỗi đau.

Mục tiêu đo được sau tái thiết kế:

| Chỉ số | Hiện tại (ước tính từ luồng) | Mục tiêu |
| --- | --- | --- |
| Chèn 1 hiệu ứng quen thuộc | 5–7 thao tác chuột, ~6s | 1 phím tắt + 3–5 ký tự + Enter, dưới 2s |
| Diện tích che Resolve khi nghỉ | 1280×880 hoặc 1140×740 | 0px (ẩn) hoặc 6px (vạch sáng) |
| Mở Dock | không đo | dưới 120ms tới frame đầu |
| Cuộn lưới 600 tài nguyên | tạo 600 QFrame | 60fps, chỉ vẽ ô đang thấy |
| Số cửa sổ cấp cao | 5 | 3 (Palette, Dock, Director) |

Ba thay đổi cấu trúc quan trọng nhất: (1) gộp `TabSFX` và thư viện LUT của `TabExport` vào một **Library** duy nhất; (2) gộp `TabCopilot` và `TabAutoCut` thành một pipeline **Director** 4 bước; (3) bỏ `FloatingBubble` và `app.py`, Edge Rail trở thành sự hiện diện thường trực duy nhất (FloatingBubble giữ làm tùy chọn, mặc định tắt — xem mục 7).

## 1. Audit

Vấn đề gốc không nằm ở từng tab mà ở chỗ có quá nhiều điểm vào và cùng một việc được làm ở 2–3 nơi. Dưới đây là phát hiện theo mức độ nghiêm trọng, mỗi cái kèm bằng chứng.

### 1.1 Kiến trúc và điểm vào

| # | Phát hiện | Bằng chứng | Mức |
| --- | --- | --- | --- |
| A1 | 5 bề mặt cấp cao, không có mô hình tư duy rõ | `main.py` khởi tạo Bubble, AutoWindow, StudioWindow; thêm EdgeDrawer và `app.py` cũ | Nghiêm trọng |
| A2 | `app.py` và `AutoWindow` lặp ~80% logic | Báo cáo mục K; 4.585 + 6.368 dòng | Nghiêm trọng |
| A3 | Quả cầu nổi chồng lên chính cửa sổ app | Ảnh 1 và 2: nút tròn tím đè mép phải AutoWindow | Cao |
| A4 | SFX tồn tại 2 nơi, LUT tồn tại 2 nơi, BGM tồn tại 2 nơi | TabSFX vs rail `sfx`; TabExport vs rail `lut`; Copilot Card 3 vs rail `bgm` | Cao |
| A5 | Tab tên "Export" nhưng 70% là thư viện màu | Widget tree TabExport | Trung bình |
| A6 | Không biết Resolve đang ở trang nào | `GetCurrentPage()` chỉ gọi on-demand | Cao |

### 1.2 AI Director (ảnh 1 và 2)

- **Ba nút "chạy" cạnh tranh nhau**: "Bắt đầu dựng" (footer), "Dựng Theo Kịch Bản Này" (trong accordion), "1-CLICK: Tự Phủ Chữ Kinetic". Người dùng không biết nút nào là con đường chính.
- **Bốn màu nút chính cùng lúc**: tím, xanh cyan, gradient tím-cyan, xanh dương. Màu nhấn mất nghĩa khi mọi nút đều nhấn.
- **Cột phải lãng phí khi rảnh**: "0%", 6 bước xám và một khung Nhật ký trống chiếm ~25% chiều rộng trong suốt giai đoạn cấu hình.
- **Cột trái lộ chi tiết kỹ thuật**: ô "small" và "vi" (model Whisper, ngôn ngữ) không có nhãn rõ, đặt ngang hàng với chọn nguồn.
- **Cấu hình AI nằm giữa luồng dựng**: chọn Web Prompt / Cloud / Local là cài đặt một lần, nhưng đang chiếm cả một accordion trong màn hình mỗi lần dựng.
- **Ô dán JSON là thao tác chính** của chế độ miễn phí. Đây là luồng của lập trình viên, không phải editor.
- Điểm tốt nên giữ: 4 thẻ "Kiểu dựng" (Podcast, Shorts, Vlog có Hook, Tự chỉnh), segmented control cho Loại footage và Nhịp dựng, Timeline Preview màu ở đáy.

### 1.3 Kho Đạo Cụ (ảnh 3)

- **Nhãn rail bị cắt**: "uyển cả", "ớp phủ", "m than", "hạc nề", "ẫu của t". Rail 78px không chứa nổi emoji + chữ tiếng Việt.
- **Nút Inspector bị cắt**: "éo Da'", "Dán Fu", "opy Fu", "Cài Đ", "Hút Pr". 5 nút phụ dàn ngang trong 280px.
- **Lộ mã nội bộ**: dòng phụ trên thẻ là `kinetic`, `glitch_cyber`, `highlighter_paper`, `retro_film`. Đây là id, không phải nhãn cho người dùng.
- **Tên lặp hai lần**: preview ghi "RGB Split Glitch", nhãn dưới cũng ghi "RGB Split Glitch".
- **Preview không đọc được**: thẻ "Highlighter Marker" gần như vô hình trên nền tối.
- **Telemetry lặp và vô dụng**: FPS N/A, LAT N/A ở góc; CPU/GPU/RAM xuất hiện hai lần ở hai dòng header.
- **Điều khiển sai ngữ cảnh**: "Rê chuột nghe thử" hiển thị khi đang xem danh mục Chữ.
- **Thanh gợi ý AI chiếm ~100px chiều cao** cố định, kể cả khi không dùng.
- **Ba cách chỉnh cỡ lưới** cùng lúc: nút Nhỏ/Vừa/Lớn, slider, nhãn "3 cột".
- **Nút "+" trên thẻ** không rõ nghĩa: thêm vào yêu thích, chèn, hay chọn?

### 1.4 Edge Drawer

- Nhúng TabAssets (tối thiểu 798px) vào 440px mà không có responsive. Báo cáo xác nhận không có listener tự ẩn cột.
- 3 nút Quick Peek (Copilot, Cut, Titles) đều chỉ gọi `self.expand`, không mở đúng nội dung tương ứng.
- **Cướp focus**: cửa sổ `Tool` nhận focus khi click, Resolve mất phím tắt cho tới khi người dùng click lại vào Resolve. Với editor dùng 90% bàn phím, đây là lỗi nặng nhất của drawer.

### 1.5 Kéo thả và tìm kiếm

- **Kỳ vọng sai về vị trí thả**: code chỉ kiểm tra chuột nằm trong cửa sổ Resolve, rồi chèn tại playhead. Người dùng thả ở giây 20 sẽ nghĩ clip nằm ở giây 20. Cần nói rõ trong UI rằng thả = chèn tại playhead.
- **Tìm kiếm khớp hai chiều gây nhiễu**: `clean_item in clean_q` khiến tag ngắn như `up`, `cut`, `zoom` khớp với mọi truy vấn chứa chúng. Không có xếp hạng nên kết quả đúng có thể nằm cuối.
- **Alias mơ hồ**: "mờ dần" nằm trong cả `fade_in` và `fade_out`.

### 1.6 Kỹ thuật ảnh hưởng UX

- Lưới tạo 580+ `QFrame` thật; lọc bằng `setVisible` và `addWidget` lại. Sẽ giật khi kho tăng lên vài nghìn.
- Không có hover video preview trên lưới; chỉ Inspector có `QVideoWidget`.
- Render không có thanh tiến độ.
- Không có global hotkey, không có tray icon.

## 2. Kiến trúc thông tin mới

Ba bề mặt, một lõi dịch vụ chung. Mọi bề mặt gọi cùng một `InsertService` và cùng một `SearchIndex`, nên chèn từ Palette, từ Dock hay kéo thả đều cho kết quả giống hệt nhau.

```text
   Palette (HUD)            Dock (Rail + Drawer)          Director
   tìm và chèn thứ          duyệt, tinh chỉnh             cắt thô, kịch bản,
   đã biết tên              theo trang Resolve            duyệt thoại
        │                          │                          │
        └──────────────┬───────────┴──────────────┬───────────┘
                       ▼                          ▼
   ┌──────────────── Lõi dịch vụ (src/services, không import QtWidgets) ───────────────┐
   │  InsertService            SearchIndex                 ResolveStateService          │
   │  một đường chèn cho       alias, motion tag,          trang, playhead, kết nối     │
   │  mọi nơi, trả kết quả     frecency, chấm điểm         poll 700ms, phát khi đổi     │
   │  để hoàn tác              dưới 16ms                                                │
   └───────────────────────────────────────────────────────────────────────────────────┘
                       │                                  │
                       ▼                                  ▼
          ResolveGateway                         Title insert worker
          một QThread, mọi lệnh API              process riêng, gọi qua QProcess
          chạy tuần tự
                       │                                  │
                       └────────────────┬─────────────────┘
                                        ▼
                          DaVinci Resolve Studio 18/19
```

Palette, Dock và Director không gọi Resolve trực tiếp; mọi lệnh đi qua lõi dịch vụ rồi qua một thread duy nhất.

| Bề mặt | Việc của nó | Tần suất | Kích thước |
| --- | --- | --- | --- |
| Palette (HUD) | Tìm và chèn một thứ đã biết tên | Hàng trăm lần mỗi buổi | 640×(56–420) px, nổi giữa Resolve |
| Dock (Edge Rail + Drawer) | Duyệt, xem trước, tinh chỉnh tài nguyên theo trang Resolve | Hàng chục lần | 6px vạch / 44px rail / 420px drawer / 760px rộng |
| Director (cửa sổ đầy đủ) | Cắt thô, lên kịch bản AI, duyệt thoại, dựng timeline mới | Vài lần mỗi dự án | tối thiểu 1024×640 px, vừa laptop 1366×768 |

### Bảng sáp nhập

| Hiện tại | Đi đâu | Ghi chú |
| --- | --- | --- |
| TabSFX (12 pad) | Library › Âm thanh, hàng "Pad" ghim ở đầu | Pad = 12 mục yêu thích, gán phím 1–9, 0, -, = khi Dock mở |
| TabSFX › Chuẩn hóa loudness | Dock ngữ cảnh Fairlight | Là thao tác trên timeline, không phải tài nguyên |
| TabExport › Thư viện LUT | Library › Màu | Một lưới LUT duy nhất |
| TabExport › Render | Dock ngữ cảnh Deliver + bước 4 Director | Thêm thanh tiến độ |
| TabAssets (4 cột) | Library, một view co giãn 3 breakpoint | Xem mục 3 |
| TabCopilot | Director › bước 3 "Kịch bản" | Chọn engine chuyển vào Cài đặt |
| TabCopilot › Card 3 BGM | Library › Nhạc nền, nút "Dùng làm nhạc nền" trong Director | Bỏ trình duyệt BGM riêng |
| TabAutoCut | Director › bước 2 "Kiểu dựng" và bước 3 "Duyệt" | 20+ checkbox thành Recipe + bảng nâng cao trượt bên |
| FloatingBubble | Tùy chọn trong Cài đặt, mặc định tắt. Mặc định dùng Edge Rail + tray icon | Tray giữ Thoát, Cài đặt, mở Director |
| `app.py` | Xóa sau khi Director thay thế xong | Gỡ widget ẩn chỉ để chống lỗi test |

### Director: 4 bước thay cho 2 tab

1. **Nguồn**: thả thư mục, chọn từ Resolve, Drive. Model Whisper và ngôn ngữ chuyển vào Cài đặt, hiển thị dạng chip nhỏ "Whisper small · vi" bấm để đổi.
2. **Kiểu dựng**: giữ 4 thẻ hiện tại + segmented control. Tính năng bật là danh sách chip; "Tùy chỉnh nâng cao" mở bảng trượt từ phải, không đẩy nội dung xuống.
3. **Duyệt và kịch bản**: bảng câu thoại Giữ/Cắt + mini timeline. Nút "Lên kịch bản AI" dùng engine mặc định. Chế độ Web miễn phí là luồng phụ: "Sao chép prompt" → dán kết quả vào ô nhận diện tự động (dán thẳng vào cửa sổ, không cần tìm ô JSON).
4. **Dựng**: một nút chính duy nhất "Dựng timeline". Kinetic subtitle là một tùy chọn bật/tắt của bước này, không phải nút riêng.

Cột tiến độ bên phải chỉ xuất hiện khi pipeline đang chạy; khi rảnh, nội dung chính chiếm toàn bộ chiều rộng.

## 3. Shell mới: Dock và Studio là một view co giãn

Không xây hai giao diện. `LibraryView` là một widget duy nhất, đổi bố cục theo chiều rộng nó được cấp. Dock 420px và Studio toàn cửa sổ chỉ là hai container khác nhau chứa cùng widget đó.

### 3.1 Breakpoint

| Chiều rộng | Tên | Điều hướng danh mục | Lưới | Inspector |
| --- | --- | --- | --- | --- |
| < 560px | Compact (Dock) | Chip ngang cuộn được | 2 cột | Sheet trượt từ đáy, phủ 62% chiều cao |
| 560–1099px | Medium | Rail icon 52px + tooltip | 3–4 cột | Sheet trượt từ phải 320px |
| ≥ 1100px | Wide (Studio) | Rail có nhãn 184px | 4–6 cột tự tính | Cột cố định 320px |

Số cột tính từ chiều rộng: `cols = max(2, floor((w + gap) / (min_card_w + gap)))`, `min_card_w = 180px`. Bỏ cả slider lẫn 3 nút Nhỏ/Vừa/Lớn; thay bằng một nút mật độ "Gọn / Thoáng" (min_card_w 150 / 220).

### 3.2 Trạng thái Dock

| Trạng thái | Rộng | Vào khi | Ra khi |
| --- | --- | --- | --- |
| Ẩn | 0 (vạch 2px) | Cài đặt "tự ẩn", hoặc Resolve không phải cửa sổ trước | Chuột chạm hành lang 6px ở mép |
| Rail | 44px | Mặc định | — |
| Hé (peek) | 44 + 12px | Chuột vào rail > 80ms | Chuột rời > 300ms |
| Mở | 420px | Click rail, hover rail > 450ms, phím tắt Dock | Chuột rời drawer > 600ms và không ghim, không đang kéo, không có menu mở, ô tìm kiếm trống |
| Ghim | 420px | Nút ghim | Bỏ ghim |
| Rộng | 760px | Kéo mép trái drawer | Kéo về |

Thời gian chuyển: mở 200ms `OutCubic`, đóng 160ms `InCubic`. Nội dung không render lại khi trượt; chỉ đổi geometry.

**Màn hình nhỏ và Resolve thu nhỏ.** Dock bám theo khung cửa sổ Resolve, không bám màn hình. Chiều rộng drawer = `clamp(320, 30% chiều rộng cửa sổ Resolve, 420)`: 420px trên 1920px, khoảng 400px trên laptop 1366px, 320px khi Resolve thu nhỏ. Dưới 360px lưới vẫn 2 cột nhưng ô nhỏ lại (`min_card_w` 140px). Nếu cửa sổ Resolve hẹp hơn 900px, Dock nổi ra ngoài mép phải khung Resolve thay vì đè lên. Palette co về 90% chiều rộng cửa sổ Resolve khi cửa sổ hẹp hơn 720px.

### 3.3 Wireframe Dock 420px (ngữ cảnh Edit)

```text
┌──────────────────────────────────────────── 420 ─┐
│ ● Edit   [ Tìm: chữ, shake, swoosh…   Ctrl+K ] 📌 » │  44  Header
├──────────────────────────────────────────────────┤
│ ( Chữ ) Chuyển cảnh  Meme  Lớp phủ  Âm thanh  ›  │  36  Chip danh mục (cuộn ngang)
│  Tất cả · Kinetic · Bút dạ · Neon · Retro    ⋯   │  28  Nhóm con (chip nhỏ)
├──────────────────────────────────────────────────┤
│ Gợi ý tại 00:01:12 — "thật không thể tin nổi"  × │  32  Gợi ý ngữ cảnh (ẩn được)
├──────────────────────────────────────────────────┤
│ ┌──────────────────┐  ┌──────────────────┐       │
│ │                  │  │                  │       │
│ │   16:9 preview   │  │   16:9 preview   │  108  │
│ │                  │  │                  │       │
│ └──────────────────┘  └──────────────────┘       │
│  Alex Hormozi Pop ♥    Highlighter            22  │
│ ┌──────────────────┐  ┌──────────────────┐       │
│ │      (chọn)      │  │                  │       │  Lưới 2 cột, ô 194×130
│ └──────────────────┘  └──────────────────┘       │  QListView ảo hóa
│  …                                               │
├──────────────────────────────────────────────────┤
│ Alex Hormozi Pop · 4.0s · V2 tự động             │  56  Thanh hành động
│ [ Chèn tại playhead  ⏎ ]  [ Tùy chỉnh  Tab ] [⋯] │      (dính đáy)
├──────────────────────────────────────────────────┤
│ ● Resolve 19 · Timeline_01 · 00:01:12:04         │  22  Trạng thái
└──────────────────────────────────────────────────┘

Rail 44px (thu gọn):        Sheet Tùy chỉnh (Tab), phủ từ đáy:
┌────┐                      ┌───────────────────────────────┐
│ ⚡ │ logo, click = mở     │ ━━━  Alex Hormozi Pop      ✕  │
│────│                      │ [ live preview 16:9  376×211 ]│
│ ✎  │ Edit: Chữ             │ Nội dung  [ResolveFlow Studio]│
│ ⇄  │ Chuyển cảnh           │ Thời lượng ━━━●━━━━  4.0s     │
│ ◉  │ Meme                  │ Màu  ■Chữ ■Nhấn ■Viền ■Hộp    │
│ ♪  │ Âm thanh              │ Chuyển động  Pop Bounce Type  │
│    │                      │ Track  Tự động ▾              │
│ ⌘  │ Palette               │ [ Chèn tại playhead ⏎ ]       │
│ ●  │ trạng thái Resolve    └───────────────────────────────┘
└────┘
```

Icon rail ở Compact đổi theo trang Resolve (mục 4), mỗi icon mở thẳng danh mục tương ứng, không chỉ gọi `expand`.

### 3.4 Thẻ 16:9

- Ô 194×130: preview 194×109 + nhãn 21px. Một dòng tên, cắt bằng dấu ba chấm, tooltip đầy đủ. Bỏ dòng mã nội bộ (`kinetic`, `glitch_cyber`).
- Huy hiệu loại ở góc trên trái (icon 14px): chữ, video, âm thanh, macro Fusion. Thời lượng ở góc dưới phải.
- Hover > 150ms: phát preview lặp trong ô (video, sprite hoặc canvas procedural). Âm thanh chỉ phát khi bật "Nghe khi rê" và chỉ trong danh mục Âm thanh và Nhạc nền.
- Thẻ âm thanh: preview là waveform + tên, không phải thumbnail trống.
- Không có nút "+" trên thẻ. Click = chọn, double-click = chèn, kéo = chèn tại playhead, chuột phải = menu (Yêu thích, Dán vào Fusion, Mở thư mục).
- Preview text preset phải có độ tương phản tối thiểu 4.5:1 với nền thẻ; preset trong suốt như Highlighter dùng nền mẫu là frame video thật.

### 3.5 Focus: không bao giờ giữ bàn phím của Resolve

- Rail và Dock mở dùng `Qt.WindowDoesNotAcceptFocus` và `WA_ShowWithoutActivating`. Click thẻ, double-click, kéo thả đều không lấy focus.
- Chỉ khi người dùng click vào ô tìm kiếm hoặc một trường nhập của Sheet, Dock mới nhận focus.
- Sau mọi thao tác chèn, gọi lại hàm focus Resolve có sẵn trong `fast_paste.py`. Esc trong Dock = trả focus về Resolve.

### 3.6 Kéo thả

Khi kéo ra khỏi Dock, con trỏ mang theo nhãn "Thả để chèn tại playhead 00:01:12:04". Nếu chuột không nằm trên Resolve, nhãn đổi thành "Thả trên DaVinci để chèn" và thả = hủy. Không giả vờ rằng vị trí thả là vị trí thời gian.

### 3.7 Studio (≥ 1100px)

Cùng `LibraryView` ở breakpoint Wide: rail 184px có nhãn đầy đủ, lưới tự tính cột, Inspector cột cố định 320px. Thanh hành động của Inspector gồm một nút chính "Chèn tại playhead" và một menu ⋯ chứa Kéo, Dán Fusion, Sao chép code, Cài vào Fusion, Hút preset. Header gộp còn một dòng: tìm kiếm, trạng thái Resolve, ghim. Bỏ FPS, LAT và chỉ số phần cứng khỏi header; chuyển vào trang Chẩn đoán trong Cài đặt.

## 4. Context-aware theo trang Resolve

Dock đổi danh mục, thứ tự và hành động chính theo trang Resolve, nhưng không bao giờ đổi khi người dùng đang thao tác bên trong nó.

### 4.1 Hồ sơ ngữ cảnh

| Trang | Danh mục hiển thị (thứ tự) | Hành động chính | Khối đặc biệt | Palette ưu tiên |
| --- | --- | --- | --- | --- |
| Media | Không có lưới | "Phân tích footage đã chọn" → mở Director bước 1 | Trạng thái cache, proxy | Lệnh Director |
| Cut | Âm thanh (Pad), Meme, Chữ nhanh | Chèn tại playhead | Hàng 12 Pad cố định ở đầu, thẻ cỡ Gọn | Âm thanh, Meme |
| Edit | Chữ, Chuyển cảnh, Meme/B-roll, Lớp phủ, Âm thanh | Chèn tại playhead (track trống an toàn) | Gợi ý theo câu thoại tại playhead | Chữ, Chuyển cảnh |
| Fusion | Macro Chữ, Macro Chuyển cảnh, Hiệu ứng node, Mẫu của tôi | Dán vào Fusion (Ctrl+V) | Nút "Hút preset từ node đang chọn" | Macro |
| Color | Màu (LUT) | Áp dụng cho clip / cho timeline | Preview LUT trên frame hiện tại (nếu lấy được still) | LUT |
| Fairlight | Âm thanh, Nhạc nền | Chèn vào track audio | Chuẩn hóa loudness (-14 / -16 / -23 LUFS), offset âm lượng | Âm thanh |
| Deliver | Không có lưới | Thêm vào hàng đợi và render | 4 thẻ preset, thanh tiến độ job | Lệnh render |

Người dùng có thể bấm chip trang trong header để khóa một hồ sơ (ví dụ đang ở Edit nhưng muốn xem Macro Fusion). Khóa hết khi bấm lại hoặc khi đổi trang hai lần.

### 4.2 Quy tắc biến hình

1. `ResolveStateService` hỏi `GetCurrentPage()` mỗi 700ms trên một thread riêng; chỉ phát tín hiệu khi giá trị đổi.
2. Nếu chuột đang trong Dock, ô tìm kiếm có chữ, Sheet đang mở hoặc đang kéo: **hoãn** biến hình. Hiện chip nhỏ "Fusion ›" trong header; áp dụng khi chuột rời Dock hoặc khi bấm chip.
3. Biến hình: crossfade nội dung 140ms, chip trang đổi màu. Không trượt, không đổi chiều rộng.
4. Mỗi hồ sơ nhớ danh mục và vị trí cuộn cuối cùng. Quay lại Edit thì thấy đúng chỗ đã rời.
5. Nếu mất kết nối Resolve: giữ hồ sơ cuối, chấm trạng thái chuyển xám, hành động chính đổi thành "Kết nối lại".

### 4.3 Màu chip trang

Dùng một màu cho mỗi trang để người dùng nhận ra ngay bằng mắt, nhưng chỉ tô ở chip và vạch 2px bên trái rail, không tô nút: Edit tím `#7c3aed`, Fusion cyan `#06b6d4`, Fairlight hồng `#ec4899`, Color hổ phách `#f59e0b`, Cut và Deliver xám `#a8a8b3`. Nút hành động chính vẫn chỉ một màu tím.

## 5. Quick Palette (HUD kiểu Raycast)

Palette là đường chính để chèn: một phím tắt, vài ký tự, Enter, focus quay về Resolve. Mục tiêu dưới 2 giây từ lúc bấm phím tới lúc clip nằm trên timeline.

### 5.1 Phím tắt

- Mặc định **Ctrl+Alt+Space**, đổi được trong Cài đặt. Không dùng `Ctrl+Space` vì xung đột bộ gõ (IBus, bộ chuyển ngôn ngữ Windows). `Alt+D` cần kiểm tra với bảng phím tắt Resolve của người dùng trước khi đặt làm mặc định.
- Chỉ kích hoạt khi cửa sổ trước là DaVinci Resolve hoặc chính ChunDVC. Ở ứng dụng khác, phím tắt được trả lại cho hệ thống.
- Phím tắt thứ hai: **Ctrl+Alt+Shift+Space** mở Palette ở chế độ lệnh (`>`).

### 5.2 Kích thước và vị trí

```text
┌──────────────────────────────── 640 ──────────────────────────┐
│ ⌕  rung giat█                               Edit · 00:01:12:04 │  56
├────────────────────────────────────────────────────────────────┤
│ ▶[64×36]  Camera Shake Mạnh     Chuyển động · rung   92%  ⏎    │  48  (đang chọn: preview chạy)
│  [64×36]  Shake Nhẹ Handheld    Chuyển động · rung   81%  ⌥1   │  48
│  [64×36]  RGB Split Glitch      Chữ · glitch              ⌥2   │  48
│  [ wave ]  Impact Rung Bass      Âm thanh · 0.8s           ⌥3   │  48
├────────────────────────────────────────────────────────────────┤
│ ⏎ Chèn   ⇧⏎ Dán Fusion / track mới   Tab Tùy chỉnh   Esc Đóng   │  28
└────────────────────────────────────────────────────────────────┘
```

- Rộng 640px, cao co giãn 56px (trống) tới tối đa 7 kết quả (56 + 7×48 + 28 = 420px).
- Vị trí: căn giữa ngang theo cửa sổ Resolve (dùng `ResolveWindowAnchor`), mép trên ở 22% chiều cao cửa sổ. Không đặt chính giữa để tránh che playhead của Viewer.
- Nền `#131316` độ mờ 94%, viền `#33333a`, bo 12px. Không blur nền (Qt trên X11 không làm blur nhất quán).
- Xuất hiện: fade + dịch 8px từ trên xuống, 120ms. Biến mất: 80ms. Ô nhập có con trỏ ngay khi hiện.

### 5.3 Tương tác

| Phím | Hành động |
| --- | --- |
| Gõ | Lọc tức thì, mỗi phím dưới 16ms (chỉ index trong RAM) |
| ↑ ↓ | Chọn; mục đang chọn phát preview trong ô thumbnail sau 150ms |
| Enter | Chèn tại playhead qua `InsertService`, đóng Palette, trả focus Resolve |
| Shift+Enter | Ở Fusion: dán vào Fusion. Ở Edit: ép tạo track mới |
| Alt+1…9 | Chèn ngay kết quả thứ n |
| Tab | Mở mục đang chọn trong Dock với Sheet Tùy chỉnh |
| Ctrl+Enter | Thêm vào Yêu thích |
| Esc | Đóng, trả focus Resolve |
| Ô trống | Hiển thị 5 mục gần đây + Pad yêu thích theo trang hiện tại |

Tiền tố lọc: `c:` chữ, `t:` chuyển cảnh, `s:` âm thanh, `m:` meme, `l:` LUT, `f:` macro Fusion. Tiền tố `>` là lệnh: `>cắt khoảng lặng`, `>render tiktok`, `>mở director`, `>đi tới fusion`.

### 5.4 Phản hồi sau khi chèn

Toast nhỏ ở góc dưới phải cửa sổ Resolve trong 4 giây: "Đã chèn Camera Shake · V3 · 00:01:12:04  [Hoàn tác]". Hoàn tác xóa đúng item vừa chèn, dựa trên track và vị trí mà `InsertService` trả về, không dựa vào Ctrl+Z của Resolve. Nếu lỗi (không có track trống, Resolve không phản hồi): toast đỏ với một câu lý do và một nút sửa ("Tạo track mới").

### 5.5 Xếp hạng kết quả

Thay `matches_query` khớp hai chiều bằng điểm số. Index được dựng một lần khi khởi động từ tên, tag, alias và output của `motion_analyzer`; chuẩn hóa không dấu như hiện tại.

| Tín hiệu | Điểm |
| --- | --- |
| Trùng khớp nguyên alias ("rung giat" = alias của `shake`) | +100 |
| Tiền tố của tên | +80 |
| Tiền tố của một token trong tên hoặc tag | +60 |
| Khớp dãy con mờ ("rgg" → "rung giat") | +30 |
| Độ tin cậy chuyển động × 20 (chỉ khi truy vấn khớp alias chuyển động) | 0 tới +20 |
| Đúng danh mục ưu tiên của trang hiện tại | +25 |
| Frecency (số lần dùng, giảm dần theo thời gian) | 0 tới +30 |

Bỏ chiều khớp `clean_item in clean_q`. Tách alias "mờ dần" khỏi `fade_out`, dùng "tối dần", "biến mất" cho `fade_out`. Thêm alias Telex gõ thiếu phổ biến khi dữ liệu dùng thật cho thấy cần.

## 6. Kế hoạch triển khai PySide6

Nguyên tắc: tách dịch vụ trước, giao diện sau. Ba dịch vụ lõi (`ResolveStateService`, `InsertService`, `SearchIndex`) phải xong trước khi viết Palette hay Dock mới, vì cả hai cùng phụ thuộc vào chúng.

### 6.1 Cấu trúc thư mục đích

```text
src/
├── services/                     # không import QtWidgets
│   ├── resolve_gateway.py        # ResolveGateway: 1 thread, hàng đợi lệnh tuần tự
│   ├── resolve_state.py          # ResolveStateService(QObject): page, timeline, playhead, kết nối
│   ├── insert_service.py         # InsertService: insert(asset, mode) -> InsertResult, undo(result)
│   ├── search_index.py           # SearchIndex: index đảo, chấm điểm, frecency
│   ├── asset_repository.py       # bọc AssetIndexer, phát assetsChanged
│   ├── preview_cache.py          # mở rộng asset_cache: clip preview 320×180 lặp
│   └── global_hotkey.py          # X11 (python-xlib) / Windows (RegisterHotKey)
└── ui/lux/
    ├── shell/
    │   ├── edge_dock.py          # thay EdgeDrawerWindow: máy trạng thái 6 trạng thái
    │   ├── dock_header.py        # chip trang, ô tìm, ghim
    │   └── tray.py               # QSystemTrayIcon thay FloatingBubble
    ├── context/
    │   ├── context_profiles.py   # dataclass ContextProfile cho 7 trang
    │   └── context_router.py     # nhận pageChanged, áp quy tắc hoãn
    ├── library/
    │   ├── asset_model.py        # QAbstractListModel, role: thumb, kind, duration, fav
    │   ├── asset_filter.py       # QSortFilterProxyModel dùng SearchIndex
    │   ├── asset_delegate.py     # QStyledItemDelegate vẽ thẻ 16:9
    │   ├── asset_grid.py         # QListView IconMode, uniformItemSizes
    │   ├── hover_preview.py      # 1 overlay dùng chung: QMediaPlayer + QVideoSink
    │   ├── category_nav.py       # chip ngang / rail icon / rail nhãn theo breakpoint
    │   ├── action_bar.py         # thanh hành động dính đáy
    │   └── library_view.py       # ghép tất cả, xử lý breakpoint
    ├── inspector/
    │   ├── inspector_host.py     # Sheet (đáy/phải) hoặc cột, cùng nội dung
    │   └── editors/              # TextStyleEditor, SfxEditor, LutEditor, MacroEditor
    ├── palette/
    │   ├── command_palette.py    # cửa sổ HUD
    │   ├── palette_model.py      # kết quả + lệnh
    │   └── command_registry.py   # lệnh '>' đăng ký từ Director, Export
    └── feedback/
        └── toast.py              # toast có Hoàn tác
```

### 6.2 Quy tắc luồng

- **Mọi lời gọi Resolve API đi qua một thread duy nhất** (`ResolveGateway`). API scripting của Resolve không được thiết kế để gọi đồng thời từ nhiều thread; hiện tại `connect()`, `fast_paste` và worker đang gọi từ nhiều nơi.
- `ResolveGateway` là `QObject` sống trên `QThread` riêng, nhận lệnh qua `submit(fn) -> Future`, trả kết quả bằng signal. UI không bao giờ chờ đồng bộ.
- Worker subprocess chèn title giữ nguyên, nhưng được gọi qua `QProcess` (bất đồng bộ) thay cho `subprocess.run` chặn với timeout 15s.
- Thumbnail và preview: giữ `ThreadPoolExecutor`, nhưng trả kết quả qua `asset_model.dataChanged(index)` thay vì callback gán `QPixmap` vào từng `QLabel`.

```python
class ResolveStateService(QObject):
    pageChanged = Signal(str)
    playheadChanged = Signal(str)        # timecode
    connectionChanged = Signal(bool)

    def __init__(self, gateway: ResolveGateway):
        super().__init__()
        self._gw, self._page = gateway, None
        self._timer = QTimer(self, interval=700)
        self._timer.timeout.connect(self._poll)

    def _poll(self):
        self._gw.submit(lambda r: (r.GetCurrentPage(), _tc(r))).then(self._on_state)

    def _on_state(self, state):
        page, tc = state
        if page != self._page:
            self._page = page
            self.pageChanged.emit(page)
        self.playheadChanged.emit(tc)
```

### 6.3 Global hotkey

- **Linux X11** (app đã ép `QT_QPA_PLATFORM=xcb`): `python-xlib` gọi `XGrabKey` trên root window, đọc sự kiện trong một `QThread`, phát signal về main thread. Grab cả các tổ hợp NumLock/CapsLock. Thêm `python-xlib` vào requirements.
- **Windows**: `RegisterHotKey` qua `ctypes`, nhận `WM_HOTKEY` bằng `QAbstractNativeEventFilter`. Không cần thread phụ.
- **Wayland**: không hỗ trợ global grab. Ghi rõ trong Cài đặt; người dùng có thể gán phím trong môi trường desktop để chạy `chundvc --palette` (lệnh CLI gửi tín hiệu qua `QLocalSocket` tới instance đang chạy).
- Kiểm tra cửa sổ trước là Resolve trước khi hiện Palette (đã có `ResolveWindowAnchor`).
- Bỏ phụ thuộc `pynput` / `keyboard` để tránh hook toàn bộ bàn phím.

### 6.4 Hiệu năng

| Hạng mục | Cách làm | Ngưỡng chấp nhận |
| --- | --- | --- |
| Lưới | `QListView` + `setUniformItemSizes(True)` + `setLayoutMode(Batched)`, delegate tự vẽ | 2.000 mục, cuộn 60fps |
| Lọc | Proxy model gọi `SearchIndex`, không tạo widget | < 16ms mỗi phím |
| Hover preview | Một `QMediaPlayer` dùng chung, đặt lên ô đang hover | Hiện frame đầu < 150ms sau delay |
| Preview .setting | Render trước clip 320×180 vào cache; chưa có thì dùng canvas procedural của `MacroCard` | Không chặn UI |
| Mở Dock | Widget dựng sẵn khi khởi động, chỉ đổi geometry | < 120ms |
| Dò cửa sổ Resolve | `wmctrl`/`xdotool` mỗi 1s khi Dock hiện, dừng khi ẩn | < 2% CPU một nhân |

### 6.5 Lộ trình

Thứ tự thực thi chi tiết và prompt từng bước nằm trong `docs/PROMPTS.md` (Bước 0 → 7). Tóm tắt:

1. **Dọn dẹp (1 tuần)**: xóa widget ẩn chống test trong `tab_assets.py`, viết lại test theo dịch vụ; khóa `app.py` không phát triển thêm.
2. **Dịch vụ lõi (2 tuần)**: `ResolveGateway`, `ResolveStateService`, `InsertService` (gom 3 đường chèn: title worker, fast paste, SFX), `SearchIndex` + test xếp hạng.
3. **Library model/view (2 tuần)**: model, delegate, grid, hover preview, action bar. Thay lưới trong TabAssets trước, giữ Inspector cũ.
4. **Dock mới (1–2 tuần)**: máy trạng thái, xử lý focus, breakpoint Compact, Sheet Inspector, tray thay Bubble.
5. **Context router (1 tuần)**: 7 hồ sơ, quy tắc hoãn, Fairlight loudness, Deliver có tiến độ render (`GetRenderJobStatus` mỗi 1s).
6. **Palette + hotkey (1–2 tuần)**: HUD, lệnh `>`, toast Hoàn tác.
7. **Director (2–3 tuần)**: tách `auto_window.py` thành 4 trang + controller; gộp Copilot và AutoCut; xóa `app.py`, TabSFX, phần LUT của TabExport.

Thứ tự này cho phép phát hành sau bước 4 mà người dùng đã thấy khác biệt lớn nhất (Dock không cướp focus, không vỡ ở 420px), trước khi động vào Director.

## 7. Rủi ro và quyết định cần chốt

| Rủi ro | Hệ quả | Giảm thiểu |
| --- | --- | --- |
| Poll `GetCurrentPage()` mỗi 700ms làm Resolve chậm | Editor thấy lag khi phát lại | Đo trên máy thật; nếu tốn, tăng lên 1.2s và dừng poll khi Resolve không phải cửa sổ trước |
| Wayland | Không có hotkey toàn cục, không định vị được cửa sổ | Giữ ép XCB; với Wayland gốc dùng lệnh CLI + phím tắt của desktop |
| `WindowDoesNotAcceptFocus` hoạt động khác nhau giữa các window manager | Dock vẫn cướp focus trên một số máy | Luôn gọi lại focus Resolve sau thao tác, kể cả khi cờ có hiệu lực |
| Preview clip cho `.setting` cần render qua Resolve | Thư viện chữ ban đầu chỉ có canvas procedural | Chấp nhận canvas ở bản đầu; thêm lệnh "Tạo preview" chạy nền |
| Hoàn tác tự viết có thể xóa nhầm nếu người dùng đã sửa timeline | Mất dữ liệu | Chỉ cho Hoàn tác trong 30 giây và khi item tại vị trí đó vẫn khớp tên |

Quyết định đã chốt:

- [x] Phím tắt Palette mặc định là Ctrl+Alt+Space, đổi được trong Cài đặt.
- [x] Dock mặc định ở trạng thái Rail 44px.
- [x] Giữ chế độ Web Prompt miễn phí trong Director bản đầu, làm luồng phụ bên cạnh Local và Cloud (mục 2, bước 3).
- [x] FloatingBubble được giữ làm tùy chọn trong Cài đặt cho người dùng cũ, mặc định tắt.
- [x] Màn hình mục tiêu gồm cả 1920×1080 và laptop 1366×768, kể cả khi cửa sổ Resolve được thu nhỏ (mục 3.2).
