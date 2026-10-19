# Web Evidence Hunter & Dynamic SHOTBOOK Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tích hợp bộ săn minh chứng web thật (Web Evidence Hunter) và bộ điều phối khung hình động (Dynamic SHOTBOOK Orchestrator) vào ResolveFlow Assistant, tự động chụp screenshot thật và chuyển đổi layout Punch-in / PiP trên timeline DaVinci Resolve.

**Architecture:** 
Xây dựng module `evidence_grabber.py` phát hiện thực thể web/URL từ transcript và chụp màn hình bằng Chrome headless không phụ thuộc thư viện nặng. Xây dựng module `shotbook_orchestrator.py` phân vai ngữ nghĩa từng câu thoại (Hook, Evidence, Punchline, Normal) để tính toán tỷ lệ phóng to (Punch-in 1.18x) hoặc thu nhỏ góc (PiP 0.35x), sau đó tích hợp vào `fcpxml_generator.py` để sinh thẻ `<adjust-transform>` và chèn clip bằng chứng lên Track V2.

**Tech Stack:** Python 3.12, Pydantic v1/v2, Native Headless Chrome/Chromium CLI, XML/FCPXML 1.8-1.11, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-web-evidence-shotbook-orchestrator.md`

## Global Constraints
- Tất cả code Python phải tương thích cả Linux và Windows (`sys.platform`).
- Headless browser phải có cơ chế timeout an toàn (tối đa 8s/trang) và có mock fallback khi offline hoặc trong môi trường test để test chạy dưới 1 giây.
- Không phá vỡ tương thích ngược với bất kỳ hàm nào trong `FCPXMLGenerator` và `EDLGenerator`.
- Toàn bộ 333 bài test hiện có phải tiếp tục PASS 100%.

## Review Focus
1. **Offline & Network Timeout:** Không để treo ứng dụng nếu trang web đích tải chậm hoặc mất kết nối mạng.
2. **URL & Entity Parsing:** Nhận diện chính xác URL trần (`https://...`), link domain (`github.com/user/repo`) và từ khóa công nghệ phổ biến.
3. **PiP Bounds & Safe Margins:** Tọa độ vị trí PiP (Scale 0.35, Pan X/Y) phải nằm an toàn trong khung nhìn 16:9 hoặc 9:16, không bị lẹm ra ngoài màn hình.
4. **FCPXML Structure Validity:** Thẻ `<adjust-transform>` phải đúng chuẩn DTD của Apple FCPXML để DaVinci Resolve import trơn tru không báo lỗi cú pháp.
5. **Zero Main-Thread Blocking:** Các lệnh chụp screenshot web chạy bất đồng bộ hoặc trong worker background.

---

## Danh Sách Các Tác Vụ (Tasks)

### Task 1: Xây Dựng `EvidenceKeywordDetector` & `WebScreenshotEngine`
**Files:**
- Create: `src/core/evidence_grabber.py`
- Create: `tests/test_evidence_grabber.py`

- [x] **Step 1.1:** Viết bài kiểm thử thất bại (`tests/test_evidence_grabber.py`):
  - Test `detect_evidence_targets(text)` nhận diện đúng URL, domain, tên thương hiệu công nghệ.
  - Test `capture_web_screenshot(url, out_path)` chạy lệnh headless hoặc mock fallback an toàn.
- [x] **Step 1.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_evidence_grabber.py
  ```
- [x] **Step 1.3:** Cài đặt `src/core/evidence_grabber.py`:
  - Lớp `EvidenceTarget` (Pydantic model: `keyword`, `target_url`, `confidence`, `timestamp_range`).
  - Lớp `EvidenceKeywordDetector` với danh mục mapping thực thể công nghệ và Regex URL.
  - Lớp `WebScreenshotEngine` phát hiện binary trình duyệt (`google-chrome`, `chromium`, `chrome.exe`, `msedge.exe`), gọi subprocess với cờ `--headless --disable-gpu --window-size=1920,1080 --screenshot=...`, có timeout 8s và fallback vẽ card giả lập khi offline.
- [x] **Step 1.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_evidence_grabber.py
  ```

---

### Task 2: Xây Dựng `ShotbookOrchestrator` (Phân Vai Kịch Bản & Tính Toán Layout)
**Files:**
- Create: `src/core/shotbook_orchestrator.py`
- Create: `tests/test_shotbook_orchestrator.py`

- [x] **Step 2.1:** Viết bài kiểm thử thất bại (`tests/test_shotbook_orchestrator.py`):
  - Test phân loại `ShotRole` (Hook, Evidence, Punchline, Normal).
  - Test tính toán `layout_mode`:
    - Hook -> `punch_in` (Scale ~1.18, Pan (0,0)).
    - Có Evidence -> `pip_corner` (Scale ~0.35, Pan X/Y ở góc dưới).
    - Normal -> `full_center` (Scale 1.0, Pan (0,0)).
  - Test xuất kế hoạch `ShotbookPlan`.
- [x] **Step 2.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_shotbook_orchestrator.py
  ```
- [x] **Step 2.3:** Cài đặt `src/core/shotbook_orchestrator.py`:
  - Enums & Models: `ShotRole`, `LayoutMode`, `ShotbookItem`, `ShotbookPlan`.
  - Bộ phân loại quy tắc ngữ nghĩa (Semantic Rules Classifier):
    - Kiểm tra thời gian đầu video (3-5s đầu) hoặc câu hỏi kích thích -> HOOK.
    - Kiểm tra câu có `evidence_target` -> EVIDENCE.
    - Kiểm tra từ khóa cao trào ("1 triệu đô", "bất ngờ", "kỷ lục", "nguy hiểm") -> PUNCHLINE.
  - Hàm tính toán vị trí `calculate_pip_transform(aspect_ratio, face_side="left")`: trả về `(scale, pan_x, pan_y)`.
- [x] **Step 2.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_shotbook_orchestrator.py
  ```

---

### Task 3: Tích Hợp Vào `FCPXMLGenerator` (Ghi Thẻ Transform & Chèn Track V2 Evidence)
**Files:**
- Modify: `src/core/fcpxml_generator.py`
- Create: `tests/test_fcpxml_shotbook.py`

- [x] **Step 3.1:** Viết bài kiểm thử thất bại (`tests/test_fcpxml_shotbook.py`):
  - Test xuất FCPXML khi có `shotbook_plan`:
    - Clip V1 có thẻ `<adjust-transform position="..." scale="..." />`.
    - Track V2 chứa các clip ảnh Evidence đúng start/duration.
    - Nếu không có `shotbook_plan`, FCPXML xuất ra vẫn giữ nguyên cấu trúc truyền thống (100% tương thích ngược).
- [x] **Step 3.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_fcpxml_shotbook.py
  ```
- [x] **Step 3.3:** Cập nhật `src/core/fcpxml_generator.py`:
  - Bổ sung tham số tùy chọn `shotbook_plan: Optional[ShotbookPlan] = None` vào `generate_timeline_fcpxml(...)`.
  - Khi render từng clip `<video>` / `<clip>` trên Track V1, tra cứu layout tương ứng trong shotbook và chèn thẻ XML `<adjust-transform>`.
  - Bổ sung các asset Evidence vào `<resources>` và render các clip tương ứng lên `<spine>` của Track V2.
- [x] **Step 3.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_fcpxml_shotbook.py
  ```

---

### Task 4: Hồi Quy Toàn Bộ Test Suite & Hoàn Thiện
**Files:**
- Verification: Toàn bộ thư mục `tests/`

- [x] **Step 4.1:** Chạy test suite mới:
  ```bash
  python3 -m pytest tests/test_evidence_grabber.py tests/test_shotbook_orchestrator.py tests/test_fcpxml_shotbook.py
  ```
- [x] **Step 4.2:** Chạy toàn bộ bài test hiện có trong repository:
  ```bash
  python3 -m pytest tests/
  ```
- [x] **Step 4.3:** Đảm bảo 100% test PASS (343 passed, 2 skipped, 0 failed).
