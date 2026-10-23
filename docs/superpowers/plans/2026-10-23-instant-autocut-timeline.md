# Instant In-Place AutoCut Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng tính năng AutoCut 1-Click trực tiếp từ DaVinci Resolve Timeline (Phương pháp 2: API Instant Assembly), tự động nhận diện video trên Timeline đang mở, phân tích khoảng lặng và tạo ngay Timeline đã cắt gọt & co sát clip (hút clip lại) trên màn hình DaVinci chỉ trong 1-2 giây mà vẫn bảo toàn Timeline gốc.

**Architecture:** Tạo service độc lập `InstantAutoCutService` (`src/services/autocut_service.py`) kết nối với `ResolveAutomation` để phát hiện clip trên Timeline đang mở, trích xuất âm thanh, chạy `SilenceDetector` từ `src/core/autocut.py`, sinh FCPXML/EDL cắt gọt chuẩn xác từng frame, gọi `media_pool.ImportTimelineFromFile()` và `project.SetCurrentTimeline()` để kích hoạt ngay Timeline mới trong DaVinci. Tích hợp nút kích hoạt 1-Click trên EdgeDock, Command Palette (`>cắt khoảng lặng`) và Studio Mode.

**Tech Stack:** Python 3.12, PySide6, DaVinci Resolve Python API, FFmpeg / wave / numpy, FCPXML 1.9 / EDL CMX3600.

**Spec:** Yêu cầu từ người dùng: người dùng có video trên DaVinci Timeline, bấm AutoCut trên tool là ngay lập tức thấy các khoảng lặng bị cắt bỏ và các đoạn thoại co sát lại gần nhau trong dự án DaVinci Resolve đang mở.

## Global Constraints
- Không phá hủy Timeline gốc: Luôn tạo Timeline phái sinh với tên tiền tố `[AutoCut] {tên_timeline_gốc}` và đặt làm active timeline.
- Tốc độ xử lý: Dưới 3 giây cho video 10-15 phút.
- Xử lý mượt mà khi DaVinci chưa mở hoặc chưa có Timeline: Báo toast thông báo thân thiện, không crash ứng dụng.
- Đa nền tảng: Chạy tốt trên cả Linux và Windows.

## Review Focus
1. Trường hợp không có Timeline nào đang mở trong DaVinci Resolve -> Trả về lỗi rõ ràng và thông báo người dùng mở Timeline.
2. Trường hợp Timeline có clip nhưng không tìm thấy file video nguồn thực tế -> Báo lỗi đường dẫn và hướng dẫn.
3. Trường hợp video không có khoảng lặng nào (người nói liên tục không ngừng) -> Thông báo video đã chuẩn, không cần cắt.
4. Trường hợp video toàn bộ là im lặng -> Báo cảnh báo không tìm thấy đoạn thoại hợp lệ.
5. Trường hợp người dùng hủy bỏ giữa chừng (cancellation) -> Dọn dẹp file tạm sạch sẽ.

---

## Task Structure

### Task 1: Xây Dựng Core Service `InstantAutoCutService`
**Files:**
- Create: `src/services/autocut_service.py`
- Test: `tests/test_instant_autocut_service.py`

**Steps:**
- [ ] Viết test `tests/test_instant_autocut_service.py` mô phỏng các kịch bản:
  - DaVinci chưa kết nối
  - Timeline trống
  - Phân tích và sinh Timeline cắt gọt thành công
- [ ] Triển khai `InstantAutoCutService` trong `src/services/autocut_service.py`:
  - `detect_active_timeline_media()`: Lấy đường dẫn file từ `ResolveAutomation.get_active_timeline()`.
  - `extract_audio_for_analysis(video_path)`: Xuất file WAV 16kHz mono phục vụ phân tích.
  - `execute_autocut()`: Chạy `SilenceDetector`, sinh FCPXML / EDL, gọi `media_pool.ImportTimelineFromFile()` và `project.SetCurrentTimeline()`.
  - Trả về đối tượng `AutoCutResult` chứa: `ok`, `original_timeline_name`, `new_timeline_name`, `cuts_count`, `duration_saved_sec`, `message`.
- [ ] Chạy test `pytest tests/test_instant_autocut_service.py -v` và kiểm tra 100% pass.
- [ ] Commit git.

---

### Task 2: Tích Hợp Worker Luồng Phụ & Tín Hiệu Giao Diện (`AutoCutWorker`)
**Files:**
- Create / Modify: `src/services/autocut_worker.py`
- Test: `tests/test_autocut_worker.py`

**Steps:**
- [ ] Viết `AutoCutWorker(QThread)` để chạy `InstantAutoCutService` trong luồng ngầm không gây đơ GUI.
  - Tín hiệu: `started`, `progress(int, str)`, `finished(AutoCutResult)`.
- [ ] Viết unit test cho worker.
- [ ] Chạy test và commit.

---

### Task 3: Tích Hợp Giao Diện Kích Hoạt 1-Click
**Files:**
- Modify: `src/ui/lux/shell/dock_header.py` hoặc `src/ui/lux/shell/edge_dock.py`
- Modify: `src/ui/lux/palette/command_palette.py` & `command_registry.py`
- Modify: `src/ui/tabs/tab_assets.py` (nút chèn nhanh trong Studio Mode)

**Steps:**
- [ ] Trong EdgeDock: Thêm nút hành động nhanh `⚡ AutoCut` trên thanh công cụ hoặc menu popup.
- [ ] Trong Command Palette: Lệnh `>cắt khoảng lặng` mở dialog xác nhận nhanh hoặc chạy ngay 1-Click AutoCut với thanh tiến độ mini.
- [ ] Hiển thị thông báo Toast đẹp mắt: "🎉 Đã AutoCut thành công! 54 khoảng lặng đã được lọc, rút ngắn 2p18s."
- [ ] Test tương tác UI và đảm bảo không phá vỡ các chức năng cũ.

---

### Task 4: Kiểm Thử Toàn Diện & Chụp Ảnh Nghiệm Thu
**Files:**
- Create: `scripts/verify_instant_autocut.py`
- Artifact: Báo cáo nghiệm thu & ảnh chụp minh chứng

**Steps:**
- [ ] Viết kịch bản kiểm thử mô phỏng timeline DaVinci có clip video thô.
- [ ] Chạy `verify_instant_autocut.py` tạo ra timeline mới `[AutoCut] Timeline_Goc` với các clip đã được co sát lại.
- [ ] Chụp ảnh màn hình lưu vào artifacts.
- [ ] Chạy lại toàn bộ regression test suite (`pytest tests/`).
- [ ] Commit và báo cáo cho người dùng.
