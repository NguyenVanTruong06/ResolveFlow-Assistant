# Unified Asset Studio Redesign Spec (Kho Đạo Cụ Chuẩn Thiết Kế)

## 1. Mục tiêu (Goals)
Nâng cấp toàn diện phân hệ **Kho Đạo Cụ** (Asset Manager) trong ứng dụng Python Desktop ([src/ui/tabs/tab_assets.py](file:///d:/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/tabs/tab_assets.py)) để đạt độ hoàn thiện cao cấp, sống động và trực quan, đồng bộ 100% với bản thiết kế nguyên mẫu Web ([src/ui/resolveflow_ui.html](file:///d:/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/resolveflow_ui.html)).

Đạo cụ không còn hiển thị đơn điệu dưới dạng icon/chữ tĩnh mà sở hữu bộ vẽ đồ họa trực quan (Custom Visual Renderers), hiệu ứng xem trước sống động theo thời gian thực (Live Preview) và tích hợp các thao tác kéo thả (Drag & Drop), chèn tại Playhead và sao chép mã Fusion Macro Node vào DaVinci Resolve.

---

## 2. Kiến trúc Bố cục 4 cột (Layout Architecture)

Kế thừa và chuẩn hóa cấu trúc 4 cột trong [`tab_assets.py`](file:///d:/Development/Python/dự%20án%20cá%20nhân/ResolveFlow-Assistant/src/ui/tabs/tab_assets.py):

```
+-----------+----------------------+------------------------------------+--------------------------+
|  Col 1    |        Col 2         |               Col 3                |          Col 4           |
| Nav Rail  |    Subcategories     |        Main Bento Grid View        |     Inspector Panel      |
|  (64px)   |       (200px)        |              (Flex 1)              |         (340px)          |
|           |                      |                                    |                          |
| 🔤 Chữ    | • Tất cả (14)        | [Search Bar] [16:9|9:16] [Count]   | [Live Preview Viewport]  |
| 🎨 Màu    | • Chuyển động (4)    | +--------------------------------+ |                          |
| 🎬 Hiệu ứng| • Highlight & Giấy(3)| | [Card] [Card] [Card]           | | [Typography Controls]    |
| 🏷️ Sticker| • Neon & Glitch (3)  | | [Card] [Card] [Card]           | | [Color Swatches]         |
| 🔊 SFX    | • Retro Film (1)     | | [Card] [Card] [Card]           | | [Motion & Curves]        |
| ❤️ Của tôi| • Tối giản (3)       | +--------------------------------+ | [Track: V2 | V3 | A2]    |
|           |                      |                                    |                          |
|           | [➕ Quét cá nhân]     | (Hỗ trợ cuộn mượt & Drag & Drop)   | [Chèn Playhead] [Kéo thả]|
|           |                      |                                    | [Copy Fusion] [Cài đặt]  |
+-----------+----------------------+------------------------------------+--------------------------+
```

---

## 3. Bộ hiển thị thẻ đạo cụ trực quan (Visual Card Renderers)

Mỗi lớp thẻ trong lưới bento kế thừa từ `AssetCard` với vùng vẽ thumbnail `thumb` chuyên biệt:

### 3.1. Thẻ Chữ (Text & Titles):
Hiển thị chính xác phong cách typographic thực tế:
* **Alex Hormozi Pop:** Chữ 2 tầng in hoa đậm viền đen 2px, từ khóa trọng tâm bôi vàng `#FACC15`.
* **Karaoke Pop:** Cụm từ có từ khóa active sáng rực màu xanh neon `#A3E635` bám sát nhịp thoại.
* **Bounce Word:** Các chữ cái nảy so le nghiêng nhẹ, đổ bóng viền tím `#7C3AED`.
* **Box Highlight:** Chữ trắng nằm gọn trong hộp nền đỏ bo góc `#EF4444`.
* **Highlighter Marker:** Nền giấy kraft, vệt dạ quang vàng chanh `#FDE047` gạch chân từ khóa.
* **Paper Cutout:** Chữ phong cách sticker xé giấy vintage trên nền tương phản.
* **Glow Neon Cyan & Pulse:** Hiệu ứng phát sáng rực rỡ (`QGraphicsDropShadowEffect` màu `#06B6D4` / `#D946EF`).
* **RGB Split Glitch:** Chữ tách kênh màu quang học đỏ/cyan lệch trục.
* **90s VHS Camcorder:** Phong cách máy quay băng từ cổ điển (Consolas font + date stamp hè 1999).
* **Gradient Sunset:** Chữ chuyển sắc gradient từ cam hoàng hôn sang tím hồng.

### 3.2. Thẻ Âm thanh (SFX Soundboard):
* Tích hợp **Interactive Waveform Canvas**: Vẽ 16-20 cột sóng hiển thị đúng hình dáng sóng (envelope) của âm thanh (`whoosh`, `pop`, `ding`, `riser`, `glitch`).
* Nút tròn **`▶ Nghe thử`** trên thẻ: Nghe thử audio qua `QAudioOutput`. Cột sóng đổi màu sang Cyan sáng khi đang phát.

### 3.3. Thẻ Màu (LUTs điện ảnh):
* Hiển thị **dải Palette 4 cột màu điện ảnh** đại diện (Teal & Orange, Moody Dark, Kodak 2383, Pastel, Cyberpunk...).
* Badge thể loại: *Tự nhiên*, *Hollywood*, *Phim nhựa*, *Pastel*, *Cyberpunk*.

### 3.4. Thẻ Chuyển cảnh (Transitions) & Overlays:
* Thẻ đồ họa với texture mô phỏng chuyển động (Whip pan vệt lia, Zoom blur tâm phóng đại, Glitch scanlines).
* Huy hiệu hiển thị số frame: `16 frame (0.53s)`, `24 frame (0.80s)`, `30 frame (1.0s)`.

### 3.5. Thẻ Sticker SVG & Meme:
* **Icon:** Vẽ vector SVG sắc nét trên nền ô vuông trong suốt (checkerboard).
* **Meme:** Thẻ phản ứng với emoji lớn (`😮`, `🤦`, `🐱`, `💸`, `🤯`...) và nhãn video MP4.

---

## 4. Bảng tinh chỉnh chi tiết (Inspector Panel & Live Preview)

Khi click chọn bất kỳ thẻ nào trên lưới, cột Inspector (Cột 4) cập nhật theo thời gian thực:

### 4.1. Khung xem trước lớn (Live Preview Viewport):
* **Chữ (Text):** Khung xem trước lớn với nút gạt chuyển đổi `16:9` (ngang) và `9:16` (dọc). Chữ cập nhật trực tiếp theo font/màu/kích thước đang chỉnh.
* **Màu (LUT):** Khung so sánh Split Screen 50/50 giữa ảnh **Gốc** và **Sau khi áp LUT** với con trượt kéo qua lại.
* **Chuyển cảnh (Transitions):** Nút phát lặp vòng 2 cảnh cắt nhau.
* **SFX:** Thanh sóng âm lớn (*Big Waveform*) với đồng hồ thời gian và nút phát/dừng.

### 4.2. Bộ điều khiển tham số (Parameter Tuning Controls):
* **Kiểu chữ:** Dropdown font chữ, thanh trượt cỡ chữ (24px - 140px), segmented chọn độ đậm (Thường / Đậm / Rất đậm), độ dày viền.
* **Màu sắc:** 4 ô màu Swatches (Màu chữ chính, Highlight, Viền nét, Hộp nền/Glow) mở color dialog.
* **Chuyển động (Motion):** Chips chọn animation (Pop, Bounce, Typewriter, Slide, Box Highlight, Glow, Static) và đường cong thời gian (Spring, Ease in-out, Tuyến tính).
* **Track đích:** Chọn track V2 / V3 (cho chữ, overlay, sticker) hoặc Audio Track 2 (cho SFX kèm bù âm lượng -12dB).

---

## 5. Tích hợp DaVinci Resolve (Actions & Integration)

Bốn nút thao tác ở đáy bảng Inspector:
1. **`📥 Chèn tại Playhead` (Primary Button):**  
   Gọi `ResolveAutomation` / DaVinci Scripting API để đưa clip/title vào đúng vị trí Playhead hiện tại.
2. **`🖐 Kéo vào DaVinci` (Native Drag & Drop):**  
   Hỗ trợ kéo chuột thả sang DaVinci Resolve. Đóng gói chuẩn MIME file `.setting` (Fusion Macro), `.cube` (LUT), `.wav` (SFX), `.mp4` (Meme).
3. **`📋 Copy Fusion` (Sao chép mã Node vào Clipboard):**  
   Sinh mã Fusion Macro Node hoàn chỉnh vào Clipboard Windows. Người dùng chỉ cần vào tab Fusion của DaVinci bấm `Ctrl + V` là dán được ngay.
4. **`⚡ Cài vào Fusion / Lưu mẫu`:**  
   Sao chép tệp template vào thư mục hệ thống DaVinci Resolve (`Fusion/Templates/Edit/Titles` hoặc `Transitions`).

---

## 6. Kiểm thử & Xác minh (Testing Strategy)
* **Unit Tests (`tests/test_tab_assets.py`):**
  * Kiểm tra khởi tạo 5 tab chính và tab Favorites.
  * Kiểm tra render đúng số lượng và thuộc tính visual của từng nhóm thẻ (`text`, `lut`, `trans`, `icon`, `meme`, `sfx`).
  * Kiểm tra chuyển đổi tỷ lệ khung nhìn (16:9 vs 9:16) trong Inspector.
  * Kiểm tra việc tạo mã Fusion Macro `.setting` và MIME data kéo thả.
  * Kiểm tra lưu và khôi phục trạng thái Yêu thích (`FavoritesManager`).
