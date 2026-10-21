# Kế hoạch BƯỚC 6: Director (Gộp AutoCut + Copilot thành Wizard 4 bước)

> **Mục tiêu**: Hợp nhất hai màn hình cũ cồng kềnh (`TabAutoCut` trong `auto_window.py` 6.368 dòng và `TabCopilot` 604 dòng) thành một giao diện Đạo diễn chuyên nghiệp duy nhất — **ChunDVC Director** (`src/ui/director/`). Giao diện được thiết kế theo mô hình Wizard 4 bước mạch lạc (Nguồn → Kiểu dựng → Duyệt & Kịch bản → Dựng timeline), bảng trượt nâng cao 280px, cột tiến độ 6 bước real-time 260px, tích hợp Web Prompt miễn phí và chạy song song an toàn với code cũ.

---

## 1. Hiểu yêu cầu (3–5 dòng)
Bước 6 tập trung giải quyết vấn đề phân mảnh trải nghiệm creator bằng cách gom quy trình dựng thô tự động và đạo diễn AI từ 2 tab rời rạc thành một chu trình liền mạch duy nhất (Wizard 4 bước: Nguồn → Kiểu dựng → Duyệt & Kịch bản → Dựng). Cửa sổ Director mới có kích thước tối thiểu 1024×640px, chân trang chỉ duy nhất một nút hành động chính (màu tím), hỗ trợ bảng thông số nâng cao trượt từ phải (280px) và cột tiến độ 6 bước (260px). Kiến trúc mới phân tách rõ ràng UI và Logic (`DirectorController` làm trung gian gọi `PipelineWorker` / `LLMDirectorWorker` có sẵn mà không viết lại pipeline), đồng thời bổ sung lệnh Palette (`>mở director`, `>cắt khoảng lặng`) và giữ nguyên code cũ chạy song song cho tới Bước 7.

---

## 2. Hiện trạng code (Current State Audit)

| File & Dòng | Vai trò hiện tại | Đánh giá & Hướng xử lý ở Bước 6 |
| :--- | :--- | :--- |
| `src/ui/auto/auto_window.py:172-1498` | `PipelineWorker(QThread)`: Chạy pipeline ngầm nặng (tách âm, Whisper STT, nhận diện khoảng lặng, cắt thô, B-roll, SFX, xuất FCPXML và nhập Resolve). | **Giữ nguyên 100% logic pipeline**; `DirectorController` sẽ khởi tạo và kết nối signals với `PipelineWorker`. Không viết lại pipeline. |
| `src/ui/auto/auto_window.py:122-170` | `LLMDirectorWorker(QThread)`: Gọi Ollama Local hoặc Cloud API (Gemini/DeepSeek) chạy ngầm để lấy kịch bản. | **Tái sử dụng nguyên vẹn**; kết nối vào Bước 3 (Kịch bản AI) của Director. |
| `src/ui/auto/auto_window.py:1499-1740` | `PipelineStepTrackerWidget(QFrame)`: Hiển thị 6 bước tiến trình chi tiết (Kiểm tra, Tải AI, Nhận diện, Đạo diễn AI, Biên tập, Xuất bản). | Đóng gói thành `ProgressSidePanel` (cột trượt 260px bên phải) xuất hiện khi bấm "Dựng timeline". |
| `src/ui/auto/auto_window.py:1747-1770` | `CacheCheckWorker(QRunnable)`: Quét checksum file video kiểm tra cache audio/transcript. | Sử dụng trong Bước 1 (Nguồn) để hiển thị badge cache (xanh: đã có cache; vàng: chưa phân tích). |
| `src/ui/auto/auto_window.py:1771-6368` | `AutoWindow(QMainWindow)`: Cửa sổ cũ 4.500 dòng pha trộn toàn bộ UI, quản lý tab và bảng duyệt thoại. | Vẫn giữ nguyên để chạy song song. Director mới sẽ kế thừa `ReviewState` và các presets từ nó. |
| `src/ui/tabs/tab_autocut.py:1-437` | `TabAutoCut(QWidget)`: Chứa hơn 20 checkbox và thanh trượt thuộc 3 tầng xử lý (Dọn dẹp, Ý đồ, Gia vị). | Toàn bộ 20+ tùy chọn được ánh xạ vào 4 thẻ phong cách + bảng trượt "Tùy chỉnh nâng cao" (280px). |
| `src/ui/tabs/tab_copilot.py:1-604` | `TabCopilot(QWidget)`: Chứa 3 Radio engines (Web Prompt, Local Ollama, Cloud API), ô dán JSON và bộ chọn BGM. | Chuyển luồng Web Prompt thành "Sao chép prompt" + vùng dán tự động nhận diện; cấu hình engine chuyển vào Cài đặt (giữ chip trạng thái). BGM kết nối với Library. |
| `src/core/review_state.py:10-97` | `ReviewState`: Quản lý danh sách `ProposedSegment`, hỗ trợ `approved` toggle, `undo()`, `redo()`, `keep_all()`, `cut_all()`. | Sử dụng trực tiếp trong Bước 3 của Director cho bảng câu thoại Giữ/Cắt và phím `Ctrl+Z`. |
| `src/ui/lux/palette/command_registry.py:35-45` | Lệnh Palette tiền tố `>`: `>director`, `>render`... | Bổ sung handler mở `DirectorWindow` và thêm alias `>cắt khoảng lặng`. |
| `src/ui/lux/shell/edge_dock.py:797-810` | Nút ▶ trên Edge Rail gọi `director_requested`. | Kết nối mở `DirectorWindow`. |

---

## 3. Phương án đề xuất (Proposed Solution)

### 3.1 Cấu trúc thư mục mới: `src/ui/director/`
```text
src/ui/director/
├── __init__.py                  # Export DirectorWindow, DirectorController
├── director_state.py            # Dataclass DirectorConfig, Recipe, StepEnum
├── director_controller.py       # Bộ điều khiển trung gian kết nối UI với Worker cũ
├── director_window.py          # Khung cửa sổ chính 1100×720px, Stepper, Footer
├── widgets/
│   ├── __init__.py
│   ├── stepper.py              # Stepper 4 bước ngang với badge số và trạng thái
│   ├── advanced_panel.py       # Bảng trượt 280px chứa toàn bộ tham số chi tiết
│   ├── progress_panel.py       # Cột tiến độ 260px (bọc PipelineStepTrackerWidget)
│   └── mini_timeline_view.py   # Preview dải màu timeline (Hook, Giữ, Cắt, 8x)
└── pages/
    ├── __init__.py
    ├── source_page.py          # Bước 1: Vùng thả, 4 nguồn, danh sách clip & cache
    ├── style_page.py           # Bước 2: 4 thẻ kiểu dựng, segmented controls, chips
    ├── review_page.py          # Bước 3: Bảng câu thoại Giữ/Cắt, Kịch bản AI, Web Prompt
    └── build_page.py           # Bước 4: Tóm tắt, Kinetic sub V2, Beat-sync, BGM
```

### 3.2 Đặc tả 4 bước trong Wizard

#### Bước 1: Nguồn Footage (`source_page.py`)
- **Vùng kéo thả (Drop Area)**: Kéo thả nhiều tệp video (`.mp4`, `.mov`, `.mkv`) hoặc cả thư mục.
- **4 nút nguồn**:
  1. `📁 Thư mục`: Chọn folder chứa video.
  2. `🎞️ Tệp video`: Chọn tệp lẻ qua `QFileDialog`.
  3. `⚡ Từ DaVinci`: Đọc timeline hiện tại của DaVinci qua `ResolveGateway.get_timeline_info()` và lấy danh sách clip.
  4. `☁️ Google Drive`: Placeholder / tích hợp tải từ link Drive.
- **Danh sách clip & trạng thái Cache**:
  - Tên tệp, thời lượng, độ phân giải.
  - Badge trạng thái: Xanh lá (`Đã có cache` - quét qua `CacheCheckWorker`), Vàng hổ phách (`Chưa phân tích`).
  - Nút `✕` để xóa clip khỏi hàng đợi.
- **Header Chip**: Chip nhỏ hiển thị cấu hình nhận diện: `Whisper small · vi` (click mở popup nhanh đổi model/ngôn ngữ hoặc trỏ tới Cài đặt).

#### Bước 2: Kiểu dựng & Thiết lập nhanh (`style_page.py`)
- **4 thẻ phong cách (Style Cards)**:
  1. `🎙️ Podcast, phỏng vấn`: Cắt khoảng lặng, lọc câu vấp, phụ đề chuẩn Clean Talk.
  2. `📱 Shorts, TikTok 9:16`: Cắt viral 60s, reframe dọc bám mặt, karaoke sub, SFX.
  3. `🎬 Vlog có Hook`: Teaser mở đầu (3-4s đắt giá), punch-in 1.15x, B-roll, tua nhanh 8x khoảng lặng có hình ảnh.
  4. `⚙️ Tự chỉnh`: Giữ nguyên thiết lập người dùng và tự động trượt mở bảng nâng cao.
- **Segmented Controls**:
  - *Loại footage*: `[Tự nhận diện | Nói liên tục | Hỗn hợp | Du lịch]`
  - *Nhịp dựng*: `[Thong thả | Cân bằng | Nhanh]`
- **Thanh trượt Mức cắt vấp & lặng**: 3 nấc `[Nhẹ | Vừa | Mạnh]` (tự tính ngưỡng dB và min_duration phù hợp).
- **Chip tính năng bật/tắt**:
  - `Hook mở đầu`, `Punch-in`, `Gợi ý B-roll`, `Tự chèn SFX`, `Reframe 9:16`, `Phụ đề Karaoke`, `Săn B-roll web`, `Khử đè mặt`.
- **Nút "Tùy chỉnh nâng cao ›"**: Kích hoạt bảng trượt bên phải 280px (`AdvancedSidePanel`).

#### Bước 3: Duyệt câu thoại & Kịch bản AI (`review_page.py`)
- **Bố cục chia 2 cột**:
  - **Cột trái (Duyệt câu thoại)**:
    - Bảng danh sách câu thoại hiển thị: Timecode, nội dung text, tag phân loại (`Hook`, `Vấp`, `Im lặng`).
    - Nút toggle cho từng câu: `[Cắt]` (đỏ) / `[Giữ lại]` (xanh).
    - Phím tắt: `Ctrl+Z` hoàn tác thay đổi qua `ReviewState`, `Space` nghe thử âm thanh câu đang chọn.
    - Mini Timeline trực quan phía dưới: dải màu phân đoạn (`Hook`: tím, `Giữ`: xanh lá, `Cắt`: đỏ, `Tua 8x`: vàng).
  - **Cột phải (Kịch bản AI)**:
    - Chip thông tin Engine hiện tại: `Engine: Gemini (Cloud) · đổi trong Cài đặt`.
    - Nút phụ "Lên kịch bản AI" kích hoạt `LLMDirectorWorker`.
    - Phần mở rộng **Web Prompt Miễn Phí (0đ)**:
      - Nút toggle: `Dùng Web Prompt miễn phí ▾`.
      - Nút `1. Sao chép prompt`: Tự động build prompt kèm transcript và copy vào Clipboard qua `QClipboard`.
      - Vùng `2. Dán kết quả từ ChatGPT/Claude`: Bấm vào vùng này rồi bấm `Ctrl+V` (hoặc chuột phải Dán). Hệ thống tự động phân tích JSON, nhận diện `Video Plan` (số cảnh, hook, thời lượng) hoặc `Kinetic Subtitle Plan` và hiển thị badge xanh xác nhận thành công mà không bắt người dùng nhìn ô JSON text thô.

#### Bước 4: Dựng timeline (`build_page.py`)
- **Khối tóm tắt lựa chọn (Summary Card)**:
  - *Footage*: Số clip, tổng thời lượng.
  - *Kiểu dựng*: Tên style, loại footage, nhịp dựng.
  - *Tính năng kích hoạt*: Danh sách chip đã bật.
  - *Câu thoại*: Số câu Giữ vs Số câu Cắt.
  - *Kịch bản*: Nguồn kịch bản (Web Prompt đã dán / Gemini Cloud / Thuật toán cục bộ).
  - *Định dạng xuất*: FCPXML 1.9 → Tạo timeline mới trong DaVinci Resolve.
- **Tùy chọn bổ trợ**:
  - `☑ Phủ phụ đề Kinetic lên Track V2` (chọn kiểu chữ).
  - `☑ Beat-sync với nhạc nền` (bắt nhịp chuyển cảnh).
  - `Nhạc nền`: Tên bài nhạc hiện tại kèm nút `[Đổi trong thư viện Nhạc nền]`.
- **Chân trang (Footer)**:
  - Nút phụ: `‹ Quay lại`, `💾 Lưu thành Recipe`.
  - **Nút chính duy nhất (Màu tím)**: `✂ 🚀 Dựng timeline`.
  - Khi bấm, nút chính chuyển trạng thái thành `Đang dựng... (Hủy)`, đồng thời **cột tiến độ 260px bên phải** (`ProgressSidePanel`) trượt mở mượt mà hiển thị 6 bước pipeline thời gian thực.

---

## 4. Bảng đối chiếu cũ → mới: Không bỏ sót bất kỳ tùy chọn nào của TabAutoCut

| Tùy chọn trong `TabAutoCut` cũ | Vị trí mới trong `ChunDVC Director` | Cơ chế hoạt động |
| :--- | :--- | :--- |
| `combo_workflow` (Podcast, Shorts, Vlog, Advanced) | 4 thẻ Style lớn ở Bước 2 | Chọn thẻ tự áp dụng bộ tham số mặc định |
| `combo_recipes`, `btn_save_recipe` | Nút `Lưu thành Recipe` ở chân trang & menu mở đầu | Lưu/tải cấu hình JSON |
| `slide_master_intensity` (1, 2, 3) | Slider Mức cắt vấp & lặng ở Bước 2 | Điều chỉnh ngưỡng cắt tổng thể |
| `combo_video_type` (Tự nhận diện, Nói liên tục, v.v.) | Segmented "Loại footage" ở Bước 2 | Áp dụng `edit_policy` |
| `combo_pacing` (Thong thả, Cân bằng, Nhanh) | Segmented "Nhịp dựng" ở Bước 2 | Quyết định độ dài shot & tần suất zoom |
| `check_cut` (Bật cắt khoảng lặng) | Bảng nâng cao 280px (Tầng 1) | Checkbox |
| `check_scene_guard` (Bảo vệ cảnh có chuyển động) | Bảng nâng cao 280px + Chip "Khử đè mặt" | Checkbox |
| `check_speedup` (Tua nhanh 8x) | Bảng nâng cao 280px | Checkbox |
| `combo_audio_track` (Track audio phân tích) | Bảng nâng cao 280px | Dropdown chọn track |
| `slide_db` (Ngưỡng im lặng dB) | Bảng nâng cao 280px | Slider dB (-60 đến -10 dB) |
| `slide_dur` (Thời lượng im lặng tối thiểu) | Bảng nâng cao 280px | Slider giây (0.2s đến 5.0s) |
| `combo_ai_mode` (Clean Talk, Shorts, Summary) | Tích hợp vào 4 thẻ Style + Bảng nâng cao | Chế độ phân tích thoại |
| `check_bad_takes` (Xóa nói vấp) | Bảng nâng cao 280px | Checkbox |
| `check_repeats` (Loại câu nói lặp) | Bảng nâng cao 280px | Checkbox |
| `txt_confidence_threshold` | Bảng nâng cao 280px | Ô nhập ngưỡng tin cậy |
| `check_hide_weak_subs` | Bảng nâng cao 280px | Checkbox |
| `combo_model` (Whisper model) | Chip tiêu đề + Cài đặt | `Whisper small · vi` |
| `combo_lang` (Ngôn ngữ STT) | Chip tiêu đề + Cài đặt | Ngôn ngữ nhận diện |
| `check_cache` (Scan Cache) | Tự động bật mặc định; badge trạng thái ở Bước 1 | Checkbox nâng cao |
| `check_fill_gaps` | Bảng nâng cao 280px | Checkbox quét bổ sung |
| `check_vlog_hook`, `combo_hook_dur`, `combo_hook_total` | Chip "Hook mở đầu" ở Bước 2 + Bảng nâng cao | Checkbox & dropdown thời lượng |
| `check_punch_in` (Auto Punch-in 1.15x) | Chip "Punch-in" ở Bước 2 + Bảng nâng cao | Checkbox |
| `check_reframe` (9:16 Shorts) | Chip "Reframe 9:16" ở Bước 2 | Checkbox |
| `check_broll` (Track Video 2) | Chip "Gợi ý B-roll" ở Bước 2 | Checkbox |
| `check_sfx` (Track Audio 2) | Chip "Tự chèn SFX" ở Bước 2 | Checkbox |
| `check_evidence` (Evidence Hunter) | Chip "Săn B-roll web" ở Bước 2 | Checkbox |
| `check_geometry` (Safe Zone) | Chip "Khử đè mặt" ở Bước 2 | Checkbox |
| `check_subtitles` & Kinetic Sub | Checkbox ở Bước 4 ("Phủ phụ đề Kinetic lên V2") | Checkbox |
| `combo_story_intent`, `combo_story_target` | Khối Kịch bản AI ở Bước 3 | Ý đồ kịch bản & thời lượng |

---

## 5. Phản biện thiết kế (Design Critique)

1. **Về hình thức cửa sổ: Window độc lập vs Modal che kín màn hình (`#director { position: absolute; inset: 0; }` trong HTML mockup)**:
   - *Vấn đề*: Trong mockup HTML, Director là một `div` overlay che toàn bộ ứng dụng. Trong môi trường desktop PySide6, quá trình xử lý STT, tạo proxy và dựng video có thể mất vài phút đến nửa tiếng. Nếu ép Director thành `QDialog` modal chặn màn hình (application modal), creator sẽ bị "giam lỏng", không thể kiểm tra DaVinci Resolve, không thể duyệt lại clip ở màn hình khác.
   - *Giải pháp*: Thiết kế `DirectorWindow` là một `QMainWindow` độc lập (frameless hoặc standard border), kích thước mặc định 1100×720px (min 1024×640px). Người dùng có thể kéo sang màn hình thứ hai (multi-monitor setup), thu nhỏ khi render ngầm, hoặc ghim phía trên nếu muốn.

2. **Về cơ chế bắt `Ctrl+V` tự động để nhận diện JSON (Web Prompt)**:
   - *Vấn đề*: Mockup ghi "bấm vào cửa sổ rồi Ctrl+V dán thẳng, không cần tìm ô JSON". Nếu cài event filter bắt `Ctrl+V` toàn bộ cửa sổ, khi người dùng đang nhập text trong ô tìm kiếm clip, sửa câu thoại ở bảng duyệt hoặc gõ API key, phím `Ctrl+V` sẽ bị chặn cướp mất để parse JSON, gây ức chế nặng nề.
   - *Giải pháp*: Tạo một widget `PasteZoneWidget` được style nổi bật dạng "Dropzone / Click to paste". Chỉ khi người dùng click vào vùng này hoặc khi focus không nằm trong bất kỳ `QLineEdit` / `QTextEdit` nào thì sự kiện `Ctrl+V` mới kích hoạt auto-parser. Thêm nút "Dán từ Clipboard" để người dùng click 1 chạm mà không cần bấm phím.

3. **Về việc không viết lại `PipelineWorker` 1.300 dòng**:
   - *Vấn đề*: `src/ui/auto/auto_window.py` có hàm `PipelineWorker` xử lý rất nhiều trường hợp đặc thù của Resolve (tránh ripple delete, gán track video/audio, xử lý cache SHA-256, FCPXML sequence). Việc "viết lại" pipeline sẽ dẫn đến rủi ro hồi quy (regression) cực kỳ nghiêm trọng.
   - *Giải pháp*: `DirectorController` đóng vai trò Adapter: thu thập state từ 4 bước của UI mới, tổng hợp thành bộ tham số chuẩn xác và chuyển thẳng vào `PipelineWorker` cũ. Mọi signal tiến độ (`step_signal`, `progress_signal`, `log_signal`) được chuyển tiếp mượt mà về `ProgressSidePanel`.

---

## 6. Phương án thay thế (Alternative Approaches)

### 6.1 Hiển thị Bảng nâng cao (Advanced Panel 280px)
- **Cách A (Chọn)**: Bảng nằm bên trong layout chính, ẩn/hiện bằng animation thay đổi `maximumWidth` từ 0px đến 280px (thời gian 200ms `OutCubic`).
  - *Ưu điểm*: Cực kỳ mượt mà, đúng chuẩn hiện đại của các app sáng tạo (Premiere, CapCut, Raycast). Không bị popup che khuất nội dung trang.
  - *Nhược điểm*: Phải quản lý layout co giãn cho nội dung chính.
- **Cách B**: Mở một dialog con hoặc popover trôi nổi (flyout).
  - *Ưu điểm*: Dễ code.
  - *Nhược điểm*: Trông rời rạc, che mất các thẻ style ở Bước 2.

### 6.2 Bảng duyệt câu thoại Bước 3
- **Cách A (Chọn)**: Dùng `QScrollArea` chứa danh sách card câu thoại tối ưu (hoặc `QTableWidget` tùy biến row height 36px) kết hợp trực tiếp với `ReviewState`.
  - *Ưu điểm*: Thao tác trực quan, hỗ trợ undo/redo `Ctrl+Z` chuẩn xác, tô màu cảnh báo vấp/tua nhanh.
- **Cách B**: Dùng `QPlainTextEdit` hiển thị văn bản thuần như một văn bản Word.
  - *Ưu điểm*: Giống đọc kịch bản.
  - *Nhược điểm*: Khó liên kết chính xác từng câu với timecode và timeline segments trong Resolve.

---

## 7. Rủi ro & Biện pháp phòng ngừa (Risks & Mitigations)

1. **Rủi ro rò rỉ hoặc thiếu tham số khi chạy Pipeline**:
   - *Biện pháp*: Viết unit test kiểm tra hàm chuyển đổi cấu hình `DirectorController.build_pipeline_kwargs()` đảm bảo sinh đầy đủ 100% các keyword arguments mà `PipelineWorker.__init__` yêu cầu.
2. **Rủi ro xung đột bộ nhớ giữa code cũ và mới**:
   - *Biện pháp*: `DirectorController` quản lý độc lập vòng đời của worker; khi đóng cửa sổ Director, nếu worker đang chạy sẽ hiển thị cảnh báo xác nhận hủy an toàn (`worker.requestInterruption()`).
3. **Rủi ro người dùng dán JSON sai định dạng**:
   - *Biện pháp*: Viết hàm `parse_web_prompt_json(text)` có khả năng tự bóc tách markdown code fence (````json ... ````), tự nhận diện cấu trúc Video Plan vs Kinetic Plan, và báo lỗi bằng tiếng Việt dễ hiểu kèm hướng dẫn nếu JSON bị hỏng.

---

## 8. Ước lượng & Kế hoạch kiểm thử

- **Ước lượng**:
  - Số file mới: ~9 file trong `src/ui/director/` và `tests/ui/test_director.py`.
  - Độ khó: 8/10 (giao diện Wizard phong phú, animation trượt bên, kết nối worker luồng ngầm, bắt phím bàn phím và paste clipboard).
- **Kế hoạch kiểm thử tự động (`pytest`)**:
  1. `test_director_state_and_recipe_mapping`: Đảm bảo 4 kiểu dựng ánh xạ chính xác toàn bộ tham số vào `PipelineWorker`.
  2. `test_director_stepper_navigation`: Kiểm tra di chuyển qua lại 4 bước và ràng buộc điều kiện nút chính.
  3. `test_web_prompt_paste_detection`: Test bóc tách và nhận diện tự động chuỗi JSON thật từ ChatGPT/Claude.
  4. `test_review_state_integration`: Test tương tác phím `Ctrl+Z` và toggle Giữ/Cắt trên danh sách câu thoại.
  5. `test_palette_and_rail_director_trigger`: Đảm bảo lệnh `>director`, `>cắt khoảng lặng` và nút ▶ trên Rail kích hoạt mở DirectorWindow.
- **Kiểm thử trên DaVinci thật**:
  - Mở Director từ Edge Rail hoặc phím tắt Palette.
  - Thêm 1 video clip nguồn, chọn kiểu "Shorts", dán JSON Web Prompt (hoặc chạy mặc định), bấm "Dựng timeline" và xác nhận timeline mới được tạo thành công trong DaVinci Resolve.

---

## 9. Câu hỏi cho chủ dự án
- Không có câu hỏi nghẽn; mọi yêu cầu thiết kế và kỹ thuật đã khớp hoàn toàn với kiến trúc hệ thống và tài liệu đặc tả.
