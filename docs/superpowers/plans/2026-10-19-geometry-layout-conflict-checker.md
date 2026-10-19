# Geometry & Layout Conflict Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng module `geometry_checker.py` cung cấp thuật toán hình học 2D AABB kiểm tra và tự động giải quyết các xung đột bố cục (phụ đề đè mặt người, PiP che khuất chủ thể, lẹm vào vùng giao diện TikTok/Shorts/Reels) cho ResolveFlow Assistant.

**Architecture:** 
Xây dựng lớp `BoundingBox` chuẩn hóa $[0.0, 1.0]$ với các phép toán hình học `intersects`, `intersection_area`, `iou`, `clamp`. Cung cấp preset `SafeZonePreset` cho TikTok, Shorts, Reels, và Broadcast 16:9. Xây dựng lớp `GeometryConflictChecker` thực hiện 3 chiến lược: Nâng cao độ phụ đề khi mặt người sát đáy, Đảo góc PiP sang góc đối diện trống, và Kẹp biên Safe-zone. Tích hợp trực tiếp vào `ShotbookOrchestrator` và chuẩn bị dữ liệu xuất cho `FCPXMLGenerator`.

**Tech Stack:** Python 3.12, Pydantic, Toán học hình học 2D AABB, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-geometry-layout-conflict-checker.md`

## Global Constraints
- Tất cả code Python phải tương thích cả Linux và Windows (`sys.platform`).
- Thuật toán hình học phải chạy hoàn toàn trên CPU trong $< 0.01$ giây, không phụ thuộc vào GPU hay network.
- 100% tương thích ngược với `ShotbookPlan` và `FCPXMLGenerator`.
- Toàn bộ 343 bài test hiện có phải tiếp tục PASS 100%.

---

## Danh Sách Các Tác Vụ (Tasks)

### Task 1: Xây Dựng `BoundingBox` & `SafeZonePreset`
**Files:**
- Create: `src/core/geometry_checker.py`
- Create: `tests/test_geometry_checker.py`

- [x] **Step 1.1:** Viết bài kiểm thử thất bại (`tests/test_geometry_checker.py`):
  - Test `BoundingBox` khởi tạo với tọa độ hợp lệ $[0.0, 1.0]$.
  - Test `intersects(other)` phát hiện đúng 2 hộp chồng lấn hoặc không chồng lấn.
  - Test `intersection_area(other)` và `iou(other)`.
  - Test `clamp(container)` kẹp hộp con nằm trọn trong vùng an toàn.
  - Test các preset `SafeZonePreset` (TikTok, Shorts, Reels, Broadcast 16:9).
- [x] **Step 1.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```
- [x] **Step 1.3:** Cài đặt `BoundingBox` và `SafeZonePreset` trong `src/core/geometry_checker.py`.
- [x] **Step 1.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```

---

### Task 2: Xây Dựng Logic Khử Xung Đột (`check_subtitle_face_collision` & `resolve_pip_placement`)
**Files:**
- Modify: `src/core/geometry_checker.py`
- Modify: `tests/test_geometry_checker.py`

- [x] **Step 2.1:** Viết bài kiểm thử thất bại (`tests/test_geometry_checker.py`):
  - Test `check_subtitle_face_collision`:
    - Khi mặt người ở đáy ($y_{max} > 0.70$) chồng lên phụ đề đáy $\rightarrow$ Phụ đề tự động được nâng lên vị trí an toàn ($y_{min} < 0.65$).
    - Khi mặt người ở cao $\rightarrow$ Không có xung đột, phụ đề giữ nguyên vị trí đáy.
  - Test `resolve_pip_placement`:
    - Mặt người lệch phải $\rightarrow$ PiP đặt ở góc dưới trái (`bottom_left`, `pan_x < 0`).
    - Mặt người lệch trái $\rightarrow$ PiP đặt ở góc dưới phải (`bottom_right`, `pan_x > 0`).
    - Mặt người ở chính giữa sát đáy $\rightarrow$ PiP đặt ở góc trên (`top_right` hoặc `top_left`).
- [x] **Step 2.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```
- [x] **Step 2.3:** Cài đặt `GeometryConflictChecker` với các phương thức kiểm tra và khử xung đột trong `src/core/geometry_checker.py`.
- [x] **Step 2.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```

---

### Task 3: Tích Hợp `audit_shotbook_plan` & Cầu Nối `ShotbookOrchestrator`
**Files:**
- Modify: `src/core/geometry_checker.py`
- Modify: `src/core/shotbook_orchestrator.py`
- Modify: `tests/test_geometry_checker.py`

- [x] **Step 3.1:** Viết bài kiểm thử thất bại:
  - Test `audit_shotbook_plan` nhận diện danh sách `face_detections` theo thời gian.
  - Test tự động cập nhật `pan_x`, `pan_y` trong các `ShotbookItem` kiểu `PIP_CORNER` để né mặt người nói.
  - Test xuất danh sách báo cáo xung đột `ConflictReport`.
- [x] **Step 3.2:** Chạy pytest để xác nhận test thất bại:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```
- [x] **Step 3.3:** Cài đặt `audit_shotbook_plan` và bổ sung phương thức hỗ trợ trong `ShotbookOrchestrator`.
- [x] **Step 3.4:** Chạy lại pytest và xác nhận test pass:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```

---

### Task 4: Hồi Quy Toàn Bộ Test Suite
**Files:**
- Verification: Toàn bộ thư mục `tests/`

- [x] **Step 4.1:** Chạy test suite mới:
  ```bash
  python3 -m pytest tests/test_geometry_checker.py
  ```
- [x] **Step 4.2:** Chạy toàn bộ bài test trong repository:
  ```bash
  python3 -m pytest tests/
  ```
- [x] **Step 4.3:** Đảm bảo 100% test PASS (351 passed, 2 skipped, 0 failed).
