# Thiết Kế Kỹ Thuật: Tích Hợp Nhạc Nền Hot & Auto Beat-Sync Vào AI Story Copilot & DaVinci Resolve

**Ngày:** 07-10-2026  
**Trạng thái:** Bản thiết kế đã duyệt (Draft / Ready for Planning)  
**Tác giả:** Antigravity AI Pair Programmer & Project Owner  

---

## 1. Mục Tiêu & Yêu Cầu Tổng Quan

### 1.1. Bối cảnh
Người dùng muốn tích hợp tính năng tự động chèn nhạc nền (Background Music - BGM) và các bản nhạc hot/trending trực tiếp vào quy trình làm việc của **AI Story Copilot** và xuất sang **DaVinci Resolve**.

### 1.2. Mục tiêu chính
1. **Kho Nhạc Nền & Quản lý Mood (`assets/music/`):**
   - Hỗ trợ kho bài hát chia theo phân loại Mood/Style (`chill_vlog`, `upbeat_trend`, `cinematic`, `funny`).
   - Tự động quét (Auto-Indexer) file nhạc `.mp3`, `.wav`, `.m4a`, `.aac` và tích hợp vào **Kho Đạo Cụ (TabAssets)**.
   - Hỗ trợ nghe thử trực tiếp (Play / Pause preview) và kéo thả vào DaVinci Resolve.
2. **Bộ tách nhịp Audio Beat Detector (`src/core/audio_beat.py`):**
   - Nhận diện các mốc Beat Drop của bài nhạc bằng thuật toán phân tích năng lượng sóng (RMS / Energy Peak / Onset envelope) sử dụng `numpy` + `ffmpeg` siêu tốc (dưới 0.5s/bài), hoạt động offline 100% không phụ thuộc thư viện nặng.
3. **Tích hợp vào Đạo Diễn AI (TabCopilot & StoryCopilot):**
   - Thêm bộ chọn nhạc nền trong Tab Đạo Diễn AI (Tự động theo Mood kịch bản, chọn bài cụ thể trong kho, hoặc tải bài riêng).
   - Truyền danh sách các mốc thời gian Beat Drop (`music_beats`) vào Prompt AI để AI ưu tiên cắt B-Roll, chuyển cảnh và Hook đúng nhịp giật.
4. **Thi công Timeline DaVinci Resolve:**
   - Đưa bài nhạc vào Audio Track chuyên biệt (Track Audio A4/A5 stereo) trong `FCP7 XML` và `FCPXML`.
   - Tạo các **Marker nhịp (Beat Markers)** màu Xanh Dương (`Cyan`/`Blue`) trên timeline DaVinci Resolve để người dựng nhìn thấy rõ nhịp bài nhạc.
   - Sao chép / link file nhạc vào thư mục `_TIMELINE_IMPORT`.

---

## 2. Kiến Trúc Hệ Thống & Các Thành Phần

```
[Kho Nhạc Nền: assets/music/*]
          │
          ▼
   [AssetIndexer] ────► [TabAssets (Kho Đạo Cụ: Danh mục BGM & Preview Audio)]
          │
          ▼
 [AudioBeatDetector] (FFmpeg + Numpy Onset Peaks)
          │ (Trích xuất các mốc Beat Drop: [1.2s, 2.4s, 4.8s...])
          ▼
 [StoryCopilot.generate_copilot_prompt]
          │ (Đưa Beat Timestamps vào Prompt AI để căn nhịp)
          ▼
 [AI Phản Hồi Kịch Bản Plan]
          │
          ▼
 [FCPXMLGenerator.generate_fcp7_xml / generate_timeline_fcpxml]
          │
          ▼
 [DaVinci Resolve Timeline Import: Track V1/V2, Audio A1/A2 Thoại, A3 SFX, A4/A5 BGM + Beat Markers]
```

---

## 3. Chi Tiết Kỹ Thuật Từng Thành Phần

### 3.1. Kho Nhạc & Asset Indexer (`src/core/asset_indexer.py`)
- Mở rộng hàm `scan_all_assets()` để quét thêm thư mục `assets/music/`.
- Hỗ trợ định dạng: `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`.
- Trả về danh sách cấu trúc:
  ```python
  {
      "id": f"bgm_{mood}_{idx}",
      "name": file_title,
      "category": "bgm",
      "mood": mood_folder,  # chill_vlog, upbeat_trend, cinematic, funny
      "file_path": abs_path,
      "duration": dur_sec,
      "bpm": bpm_estimate
  }
  ```
- Cập nhật `TabAssets` trong `src/ui/tabs/tab_assets.py`:
  - Thêm Category chip/button: `🎵 Nhạc Nền (BGM)`.
  - Hiển thị danh sách card nhạc, thời lượng, thể loại.
  - Tích hợp phát thanh audio preview trong Inspector bằng `QMediaPlayer` sẵn có.

### 3.2. Cải tiến Bộ Tách Nhịp `AudioBeatDetector` (`src/core/audio_beat.py`)
- Hiện tại file `src/core/audio_beat.py` gọi `import librosa` (gặp lỗi khi môi trường chưa cài `librosa`).
- Cải tiến:
  - Nếu có `librosa`, sử dụng `librosa.beat.beat_track`.
  - Nếu không có `librosa` (Fallback tiêu chuẩn):
    - Dùng `ffmpeg` giải mã audio sang raw PCM float32 (16kHz hoặc 22.05kHz mono).
    - Dùng `numpy` tính toán Short-Time Energy hoặc Spectral Flux (onset strength).
    - Tìm các đỉnh năng lượng (peak detection với ngưỡng động) để lấy các giây beat drop (`List[float]`).
    - Thời gian phân tích: < 0.5 giây cho bài 3 phút.

### 3.3. Tích hợp AI Story Copilot (`src/core/story_copilot.py`)
- Cập nhật hàm `generate_copilot_prompt`:
  - Thêm tham số `bgm_name: Optional[str] = None` và `music_beats: Optional[List[float]] = None`.
  - Tạo section trong prompt:
    ```markdown
    ## 🎵 NHẠC NỀN & CÁC MỐC NHỊP BEAT DROP:
    - Bài nhạc: {bgm_name}
    - Các mốc nhịp rơi (Beat drops): [1.4s, 2.8s, 4.2s, 5.6s, 8.4s, 11.2s...]
    👉 YÊU CẦU ĐẠO DIỄN: Hãy ưu tiên đặt `timeline_sec` của các đoạn cắt, B-Roll inserts và Teaser Hook trùng hoặc sát với các mốc nhịp này để tạo hiệu ứng Beat-Sync giật theo nhạc.
    ```

### 3.4. Dựng Timeline Nhạc Nền Trên DaVinci (`src/core/fcpxml_generator.py`)
- Mở rộng `generate_fcp7_xml`:
  - Thêm tham số `bgm_file: Optional[str] = None`, `music_beats: Optional[List[float]] = None`.
  - Nếu có `bgm_file`:
    - Tạo Audio Track riêng: `a_track_bgm1` và `a_track_bgm2` (Audio Track A4 và A5 Stereo).
    - Tạo `clipitem` cho nhạc nền trải dài từ `0.0s` đến tổng thời lượng của timeline (`total_timeline_sec`).
    - Nếu bài nhạc ngắn hơn video: tự động lặp lại (loop) hoặc kéo dài đến hết video.
    - Thêm các Beat Markers vào danh sách markers:
      ```python
      for b_sec in music_beats:
          markers.append({
              "time": b_sec,
              "duration": 1.0 / fps,
              "name": "🎵 Beat Drop",
              "color": "Cyan",
              "note": "Mốc nhịp nhạc nền"
          })
      ```
- Mở rộng `generate_timeline_fcpxml` tương tự cho chuẩn Apple FCPXML.

### 3.5. Giao Diện Người Dùng (`TabCopilot` & `AutoWindow`)
- Thêm SectionCard **"🎵 3. CHỌN NHẠC NỀN & BEAT-SYNC"** trong `TabCopilot`:
  - `QComboBox` chọn nguồn nhạc:
    - *"✨ Tự động theo Mood (AI gợi ý từ kho)"*
    - *"🎧 Danh sách bài trong Kho Nhạc..."*
    - *"📁 Tải tệp MP3/WAV riêng..."*
    - *"🚫 Không chèn nhạc nền"*
  - Nút nghe thử bài nhạc đã chọn (Play / Pause).
  - Checkbox *"⚡ Tự động Beat-Sync (Cắt cảnh & B-Roll giật theo nhịp nhạc)"*.
- Kết nối vào `_quick_copy_copilot_prompt`, `_run_local_ollama_pipeline`, `_run_cloud_api_pipeline`, và `_on_copilot_plan_applied`.

---

## 4. Kế Hoạch Kiểm Thử (Testing Strategy)

1. **Test `AudioBeatDetector`:**
   - Test phân tích file audio mẫu bằng thuật toán numpy fallback, đảm bảo trả về danh sách các float timestamps hợp lệ trong thời gian < 1 giây.
2. **Test `AssetIndexer` với Music:**
   - Test nhận diện đúng các file âm thanh trong `assets/music/` và phân nhóm theo thư mục con.
3. **Test `FCPXMLGenerator` với BGM Track & Beat Markers:**
   - Test sinh FCP7 XML và FCPXML có chứa Track Audio A4/A5 và các Beat Markers màu Cyan.
4. **Test Toàn Bộ Test Suite:**
   - Chạy `pytest tests/test_ui.py tests/test_tab_assets.py` để đảm bảo 100% test hiện tại vẫn pass không có bất kỳ regression nào.

---

## 5. Ràng Buộc & Cam Kết (Constraints)
- **Zero Git Leaks:** Không commit file âm thanh dung lượng lớn hoặc bản quyền vào git. Thư mục `assets/music/` có file `.gitkeep` và file test âm lượng nhỏ (hoặc mock tone).
- **Tương thích DaVinci Resolve Free:** Hoạt động 100% mượt mà qua FCP7 XML và FCPXML kéo thả.
- **Tốc độ:** Phân tích beat không block giao diện người dùng (chạy trong worker hoặc thuật toán vector hóa numpy nhanh dưới 0.5s).
