# 🚀 ResolveFlow Assistant v4.1 - AI Visual, Director, Text+ Presets & Smart Performance Suite

**ResolveFlow Assistant v4.1** là bộ công cụ trợ lý AI toàn năng tự động hóa quy trình hậu kỳ video 100% cục bộ (Local / On-Premise) dành cho **DaVinci Resolve** (Hỗ trợ cả bản **Free** và bản **Studio**).

Hệ thống tích hợp công nghệ AI nhận dạng giọng nói ngoại tuyến (Whisper AI), bộ não **Đạo Diễn AI (AI Director)** tự động phân tích kịch bản lời thoại, lọc sạch nói vấp (Bad Takes), thị giác máy tính **Auto Dynamic Re-framing 9:16 (Bám mặt chuyển video dọc)**, **AI B-Roll Inserter (Tự động gợi ý cảnh minh họa Track Video 2)**, **Auto SFX Engine (Hiệu ứng âm thanh Track Audio 2)**, **Auto Speed-Ramp 8x (Tua nhanh khoảng lặng thành cú chuyển cảnh Timelapse)**, hiệu ứng **Auto Punch-in (Zoom luân phiên 1.15x)**, **Hệ thống Text Style Presets đa phong cách (Karaoke Pop, Bounce Word, Box Highlight, Glow/Neon, Clean Outline, Gradient Fill, Slide-in)**, **Bộ chọn Workflow Mode 1-click & Hệ thống Recipe**, cùng cơ chế **Scan Cache siêu tốc (<0.05s)** và **Video Proxy 480p**.

---

## 📑 Bảng So Sánh Các Phiên Bản

| Tính năng | Bản v1.0 - v3.0 | Bản v4.0 | Bản v4.1 (Hiện tại - Nâng cấp Toàn diện) |
| :--- | :---: | :---: | :---: |
| **Mục tiêu sử dụng** | Cắt thô & Lọc nói vấp | Đa tầng thị giác & Speed-Ramp | **Workflow Mode 1-click (Podcast, Shorts, Vlog, Advanced)** |
| **Text Style Presets** | Phụ đề cơ bản | Karaoke chữ nhảy đơn giản | **7 Preset CapCut-like Text+ (Pop, Bounce, Box, Glow, Outline...) + Quick Preview** |
| **UX & Cấu hình** | 1 Lớp cài đặt | Giao diện cơ bản | **2 Lớp (Cơ bản + Master Slider / Nâng cao ▾) & Hệ thống Recipe 1-click** |
| **Tốc độ Quét (Scanning)** | Chạy trực tiếp file gốc | Quét tuần tự | **Scan Cache theo Checksum (<0.05s) + Proxy 480p & Pipeline song song** |
| **Gợi ý Whisper Model** | Người dùng tự chọn | Người dùng tự chọn | **Tự động gợi ý model tối ưu theo thời lượng video** |
| **Cắt khoảng lặng & Speed-Ramp** | Cắt thô | Speed-Ramp 8x | **Speed-Ramp 8x kết hợp Master Intensity Slider** |
| **Lọc nói vấp & Đạo diễn AI** | Cơ bản | Gợi ý 2 Pha duyệt cắt | **Quy trình 2 Pha (Phase 1 AI Review / Phase 2 Xuất bản)** |
| **Auto Re-framing (16:9 ➔ 9:16)** | ❌ | Bám mặt chuyển dọc | **Tự động bám mặt & căn chỉnh vị trí Text+ theo tỷ lệ** |
| **Gợi ý B-Roll & SFX Audio 2** | ❌ | Xuất Markers & Cues | **Tự động chèn Markers & Cues B-Roll, SFX, Timelapse** |
| **Tương thích DaVinci Resolve** | Free & Studio | Free & Studio | **100% Resolve Free & Studio (FCPXML v1.9 + EDL CMX3600)** |

---

## 🛠️ Yêu Cầu Hệ Thống & Cài Đặt

### 1. Chuẩn bị môi trường
* **Hệ điều hành:** Windows 10/11 (64-bit).
* **Python:** Phiên bản `3.10` trở lên.
* **GPU (Khuyên dùng):** NVIDIA (hỗ trợ CUDA) để tăng tốc độ nhận diện Whisper AI.
* **Công cụ bắt buộc:** [FFmpeg](https://ffmpeg.org/) (đã thêm vào biến môi trường `PATH`).

### 2. Cài đặt mã nguồn
```powershell
# 1. Clone hoặc tải mã nguồn về máy
git clone https://github.com/NguyenVanTruong06/ResolveFlow-Assistant.git
cd ResolveFlow-Assistant

# 2. Khởi tạo môi trường ảo
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Cài đặt các thư viện cần thiết
pip install -r requirements.txt
pip install PySide6 pytest pillow
```

---

## 📖 Hướng Dẫn Sử Dụng Chi Tiết (Bản v4.1)

Chạy ứng dụng bằng lệnh:
```powershell
python main.py
```

---

### 🟢 1. Bộ Chọn Chế Độ Dựng Nhanh (Workflow Mode)
* **🎙 Dựng Podcast / Phỏng vấn dài:** Tự động bật Silence Cut + Lọc sạch nói vấp + Clean Talk + Sub chuẩn viền nét thanh lịch (`clean_outline`).
* **📱 Làm Shorts / TikTok 9:16:** Tự động bật Viral Shorts + Auto Reframe 9:16 + Sub Karaoke Pop nhảy chữ (giới hạn 4-6 từ) + Auto SFX + Auto Punch-in.
* **🎬 Vlog có Hook / Intro:** Tự động trích xuất Teaser/Hook mở đầu giật gân (10-30s) + Punch-in + B-Roll + Speed-Ramp tua nhanh Timelapse.
* **⚙ Tùy chỉnh nâng cao (Advanced):** Mở toàn bộ 6 nhóm chức năng để người dùng tùy biến đè (override) bất kỳ lúc nào.

---

### 🟢 2. Hệ Thống Text Style Preset & Quick Preview
* **Preset có sẵn:** Karaoke Pop (Highlight phóng to), Bounce Word (Chữ nảy Spring), Box Highlight (Khung nền hộp), Glow/Neon (Chữ phát sáng), Clean Outline (Viền nét Podcast), Gradient Fill (Chuyển sắc), Slide-in (Trượt từ).
* **👁 Xem trước (Quick Preview):** Bấm nút xem trước để render ngay ảnh phụ đề 16:9 hoặc 9:16 mà không cần mở Resolve.
* **Tạo Preset riêng:** Bấm **"➕ Lưu Preset..."** để lưu vào thư mục `presets/text_styles/custom/`.

---

### 🟢 3. Hệ Thống Recipe (Cấu Hình 1-Click)
* Cho phép lưu toàn bộ trạng thái cài đặt vào `recipes/<tên_recipe>.json`.
* Nạp lại bằng 1 click chọn trong dropdown Recipe thay vì phải cấu hình lại từ đầu.

---

### 🟢 4. Tối Ưu Hiệu Năng & Scan Cache
* **Scan Cache theo Checksum:** Nạp lại kết quả nhận diện giọng nói và cắt lọc trong **0.05s** nếu chạy lại cùng file video gốc.
* **Gợi ý Model Whisper:** Tự động tính thời lượng video để gợi ý `large-v3` (< 3 phút) hoặc `small`/`medium` (> 30 phút, tiết kiệm 65-75% thời gian).

---

## 🧪 Chạy Kiểm Thử Tự Động (Unit Tests)
```powershell
.\venv\Scripts\pytest -v
```
Toàn bộ **68/68 kịch bản kiểm thử** đạt kết quả **100% PASSED**.