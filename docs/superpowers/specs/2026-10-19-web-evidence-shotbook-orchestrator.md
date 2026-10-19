# Spec: Web Evidence Hunter & Dynamic SHOTBOOK Orchestrator (Phase 1)

> **Mã đặc tả:** `SPEC-2026-10-19-SEECUT-PHASE1`  
> **Dự án:** ResolveFlow Assistant (tích hợp tư duy SeeCut)  
> **Trạng thái:** Chờ duyệt (Pending Review)

---

## 1. Mục Tiêu & Bối Cảnh (Context & Goal)

Học hỏi từ kiến trúc thành công của **SeeCut**, Phase 1 nâng cấp trí thông minh biên tập của ResolveFlow Assistant từ việc chỉ cắt ghép thoại (talking-head đơn điệu) thành video ngắn giàu nhịp điệu và hình ảnh minh chứng thực tế:
1. **Web Evidence Hunter (`src/core/evidence_grabber.py`):** Tự động phát hiện khi người nói nhắc đến công cụ, website, bài báo, tài liệu công nghệ hoặc URL trong transcript và dùng headless browser (Chrome/Edge/Chromium) chụp ngay screenshot thật làm B-roll chất lượng cao.
2. **Dynamic SHOTBOOK Orchestrator (`src/core/shotbook_orchestrator.py`):** Phân tích ngữ nghĩa câu thoại theo vai trò kịch bản (Hook, Evidence, Opinion, Punchline) để điều phối khung hình:
   - **Hook / Punchline:** Punch-in phóng to trung tâm (1.15x - 1.25x).
   - **Evidence (Có B-Roll/Screenshot):** Tự động co người nói thành ô tròn/bo góc Picture-in-Picture (PiP, Scale ~0.35x) đặt ở góc dưới, nhường toàn bộ màn hình cho hình ảnh bằng chứng.
   - **Normal Talk:** Trở lại khung hình chuẩn 1.0x.
3. **Cầu nối FCPXML & DaVinci Resolve:** Tự động ghi thẻ `<adjust-transform>` và đặt clip Evidence lên Track V2 khớp đúng timestamp thoại.

---

## 2. Kiến Trúc Module & Luồng Dữ Liệu (Architecture & Data Flow)

```
[ Whisper Transcript (seg_words.json / segments) ]
                     │
                     ▼
       ┌─────────────────────────────┐
       │   EvidenceKeywordDetector   │
       │ (Phát hiện URL, Brand, Tech)│
       └─────────────┬───────────────┘
                     │ [Found Web Targets]
                     ▼
       ┌─────────────────────────────┐
       │     WebScreenshotEngine     │
       │ (Chrome Headless / Sandbox) │
       └─────────────┬───────────────┘
                     │ [Saved 1080p PNGs in data/evidence/]
                     ▼
       ┌─────────────────────────────┐
       │    ShotbookOrchestrator     │
       │ (Phân loại Hook/Evidence/PiP│
       └─────────────┬───────────────┘
                     │ [ShotbookPlan]
                     ▼
       ┌─────────────────────────────┐
       │   Enhanced FCPXMLGenerator  │
       │  - Track V1: A-roll (PiP)   │
       │  - Track V2: Evidence B-roll│
       │  - Track V3: Text+ Subtitles│
       └─────────────┬───────────────┘
                     │
                     ▼
       [ DaVinci Resolve Timeline Import ]
```

---

## 3. Đặc Tả Chi Tiết Từng Thành Phần

### 3.1. `WebScreenshotEngine` & `EvidenceKeywordDetector` (`src/core/evidence_grabber.py`)
- **Phát hiện mục tiêu (Entity Detection):**
  - Biểu thức chính quy quét URL trực tiếp (`https?://[^\s]+`, `github\.com/[^\s]+`, etc.).
  - Từ điển các nền tảng phổ biến: *GitHub, ChatGPT, OpenAI, DaVinci Resolve, Notion, Google, Midjourney, Claude, HuggingFace, YouTube, VnExpress, v.v.*
  - Tự động map từ khóa thương hiệu thành URL trang chủ hoặc trang tìm kiếm nếu không có URL trực tiếp.
- **Chụp ảnh màn hình (Headless Browser Capture):**
  - Tìm kiếm binary trình duyệt có sẵn: `/usr/bin/google-chrome`, `/usr/bin/chromium-browser`, `/usr/bin/chromium` (Linux) hoặc `chrome.exe`, `msedge.exe` (Windows).
  - Lệnh chụp headless độc lập không phụ thuộc thư viện nặng:
    ```bash
    google-chrome --headless --disable-gpu --window-size=1920,1080 --screenshot=<out_path> "<url>"
    ```
  - Cơ chế Timeout (tối đa 8s/trang) và Fallback an toàn (nếu mất mạng hoặc không có Chrome, tự sinh thẻ đồ họa Placeholder/Card thanh lịch).
  - Tệp lưu trữ tại: `data/evidence/<sha256_hash>.png`.
- **Tích hợp `AssetIndexer`:**
  - Tự động nạp các ảnh chụp vào danh mục `evidence` / `overlays` để hiển thị trong `TabAssets`.

### 3.2. `ShotbookOrchestrator` (`src/core/shotbook_orchestrator.py`)
- **Schema Pydantic `ShotRole`:**
  ```python
  class ShotRole(str, Enum):
      HOOK = "hook"               # Câu mở đầu giật gân, câu hỏi kích thích
      PUNCHLINE = "punchline"     # Đoạn cao trào, con số bất ngờ
      EVIDENCE = "evidence"       # Đoạn nhắc tới website, tài liệu, số liệu
      OPINION = "opinion"         # Bày tỏ quan điểm cá nhân
      SUMMARY = "summary"         # Tóm tắt, kêu gọi hành động (CTA)
      NORMAL = "normal"           # Đoạn nói chuyện thông thường
  ```
- **Schema `ShotbookItem`:**
  ```python
  class ShotbookItem(BaseModel):
      segment_index: int
      start_time: float
      end_time: float
      text: str
      role: ShotRole
      layout_mode: Literal["full_center", "punch_in", "pip_corner"]
      zoom_scale: float = 1.0     # 1.0 (chuẩn), 1.18 (punch-in), 0.35 (PiP)
      pan_x: float = 0.0          # Độ dời X (-0.3 đến +0.3)
      pan_y: float = 0.0          # Độ dời Y
      evidence_path: Optional[str] = None
      sfx_tag: Optional[str] = None
  ```
- **Thuật toán điều phối (Orchestration Rules):**
  1. Nếu câu thoại ở 3-5 giây đầu tiên HOẶC chứa từ khóa nghi vấn ("Tại sao", "Làm thế nào", "Bí mật", "1 triệu đô"): Gán `role = HOOK`, `layout_mode = punch_in` (Scale 1.18).
  2. Nếu câu thoại có Evidence chụp được: Gán `role = EVIDENCE`, `layout_mode = pip_corner` (Scale 0.35, Pan sang góc dưới phải hoặc trái dựa theo vị trí mặt người nói từ `scene_activity`), clip Evidence được chèn vào V2.
  3. Nếu câu thoại kết thúc hoặc chuyển ý thông thường: Gán `layout_mode = full_center` (Scale 1.0).

### 3.3. Nâng Cấp `FCPXMLGenerator` (`src/core/fcpxml_generator.py`)
- Bổ sung hỗ trợ thẻ biến đổi FCPXML chuẩn Apple/DaVinci:
  ```xml
  <adjust-transform position="320.0 -240.0" scale="0.35 0.35" />
  ```
  áp dụng trên từng clip video A-roll tại Track V1.
- Tự động đặt các clip Evidence (`.png` hoặc `.mp4`) lên Track V2 với thời lượng tương ứng.

### 3.4. Giao Diện & Tùy Chọn Người Dùng
- Trong `AutoWindow` (`src/ui/auto/auto_window.py`) và Recipe Cấu hình:
  - Checkbox: `[x] Tự động săn B-Roll Web (Evidence Hunter)`
  - Checkbox: `[x] Bố cục khung hình động SHOTBOOK (Punch-in & PiP)`

---

## 4. Kế Hoạch Kiểm Thử (Verification Plan)
1. **Unit Test `test_evidence_grabber.py`:**
   - Kiểm tra bộ tách thực thể URL và tên thương hiệu từ transcript tiếng Việt và tiếng Anh.
   - Kiểm tra hàm chụp screenshot Chrome headless với URL mẫu (và fallback mock offline khi không có mạng).
2. **Unit Test `test_shotbook_orchestrator.py`:**
   - Kiểm tra phân loại câu thoại Hook vs Evidence vs Normal.
   - Kiểm tra sinh các thông số tọa độ Pan/Scale cho chế độ PiP và Punch-in.
3. **Unit Test `test_fcpxml_shotbook.py`:**
   - Kiểm tra sinh FCPXML chứa thẻ `<adjust-transform>` và track V2 Evidence.
4. **Hồi quy toàn bộ test suite:** Đảm bảo toàn bộ 333 tests hiện tại tiếp tục PASS 100%.
