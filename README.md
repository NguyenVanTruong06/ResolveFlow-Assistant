# 🚀 ResolveFlow Assistant v4.0 - AI Visual, Audio & Director Automation Suite for DaVinci Resolve

**ResolveFlow Assistant v4.0** là bộ công cụ trợ lý AI toàn năng tự động hóa quy trình hậu kỳ video 100% cục bộ (Local / On-Premise) dành cho **DaVinci Resolve** (Hỗ trợ cả bản **Free** và bản **Studio**).

Hệ thống tích hợp công nghệ AI nhận dạng giọng nói ngoại tuyến (Whisper AI), bộ não **Đạo Diễn AI (AI Director)** tự động phân tích kịch bản lời thoại, lọc sạch nói vấp (Bad Takes), thị giác máy tính **Auto Dynamic Re-framing 9:16 (Bám mặt chuyển video dọc)**, **AI B-Roll Inserter (Tự động gợi ý cảnh minh họa Track Video 2)**, **Auto SFX Engine (Hiệu ứng âm thanh Track Audio 2)**, **Auto Speed-Ramp 8x (Tua nhanh khoảng lặng thành cú chuyển cảnh Timelapse)**, hiệu ứng **Auto Punch-in (Zoom luân phiên 1.15x)**, và phụ đề hiệu ứng **Karaoke Text+ FCPXML** chuyên nghiệp.

---

## 📑 Bảng So Sánh Các Phiên Bản

| Tính năng | Bản v1.0 | Bản v2.0 | Bản v3.0 | Bản v4.0 (Hiện tại - Toàn năng) |
| :--- | :---: | :---: | :---: | :---: |
| **Mục tiêu sử dụng** | Cắt thô 1 video đơn | Cắt thô đa clip & Sub Karaoke | Đạo diễn AI: Lọc nói vấp & kịch bản | **Hậu kỳ thị giác, âm thanh & Speed-Ramp toàn diện** |
| **Số lượng video/audio** | 1 Clip đơn lẻ | Hàng loạt clip (Batch) / Audio rời | Hàng loạt clip (Batch) / Audio rời | **Hàng loạt clip (Batch) / Audio rời** |
| **Cắt khoảng lặng (Smart Cut)** | Cắt thô cơ bản | Tự động ghép nối EDL đa clip | Cắt thông minh theo kịch bản | **Cắt thông minh kết hợp Ngữ nghĩa & Thị giác** |
| **Lọc nói vấp (Bad Takes)** | ❌ | ❌ | ✔ | **✔ Tự động gọt bỏ câu nói hỏng** |
| **Auto Speed-Ramp (Tua nhanh 8x)** | ❌ | ❌ | ❌ | **✔ Biến khoảng lặng thành chuyển cảnh Timelapse** |
| **Hiệu ứng Auto Punch-in** | ❌ | ❌ | ✔ | **✔ Tự động Zoom 1.15x luân phiên** |
| **Auto Re-framing (16:9 ➔ 9:16)** | ❌ | ❌ | ❌ | **✔ Tự động bám mặt chuyển sang video dọc** |
| **Gợi ý B-Roll (Track Video 2)** | ❌ | ❌ | ❌ | **✔ Tự trích xuất từ khóa & xuất danh sách B-roll** |
| **Âm thanh SFX (Track Audio 2)** | ❌ | ❌ | ❌ | **✔ Tự chèn Whoosh/Pop ở điểm chuyển cảnh** |
| **Timeline Markers màu** | ❌ | ❌ | ✔ | **✔ Markers đầy đủ: Bad Take, B-Roll, SFX, Timelapse** |
| **Phụ đề Karaoke Text+ FCPXML** | ❌ | ✔ | ✔ | **✔ Tương thích 100% khung hình 16:9 & 9:16** |
| **Tương thích DaVinci Resolve** | Studio (API) | **Resolve Free & Studio** | **Resolve Free & Studio** | **100% Resolve Free & Studio** |

---

## 🛠️ Yêu Cầu Hệ Thống & Cài Đặt

### 1. Chuẩn bị môi trường
* **Hệ điều hành:** Windows 10/11 (64-bit).
* **Python:** Phiên bản `3.10` trở lên.
* **GPU (Khuyên dùng):** NVIDIA (có hỗ trợ CUDA) để tăng tốc độ nhận diện AI.
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
pip install PySide6 pytest
```

---

## 📖 Hướng Dẫn Sử Dụng Chi Tiết (Bản v4.0)

Chạy ứng dụng bằng lệnh:
```powershell
python main.py
```

---

### 🟢 1. Cấu hình Tính Năng Nổi Bật v4.0
* **⚡ Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp 8x):** Biến các khoảng dừng chết (lúc suy nghĩ, thao tác tay, đi lại...) thành cú chuyển cảnh tua nhanh 800% cực nghệ thuật kèm hiệu ứng âm thanh Whoosh.
* **Auto Re-framing (Bám mặt sang video dọc 9:16):** Tự động bám theo người nói để chuyển đổi video ngang 16:9 sang video dọc 9:16.
* **Tự động gợi ý cảnh minh họa B-Roll (Track Video 2):** Quét từ khóa và xuất danh sách `*_broll_suggestions.txt` kèm Markers màu Magenta trên Timeline.
* **Tự động chèn âm thanh hiệu ứng SFX (Track Audio 2):** Bố trí các điểm âm thanh Whoosh, Pop ở các vết cắt và Punch-in trên Track Audio 2.

---

### 🟢 2. Dành cho DaVinci Resolve FREE (Bản Miễn Phí)
1. **Chọn video:** Bấm **"Chọn Video"** (giữ `Ctrl` hoặc `Shift` để chọn nhiều video hoặc file audio rời).
2. **Khởi chạy:** Bấm **"KHỞI CHẠY TIẾN TRÌNH TỰ ĐỘNG HÓA v4.0"**.
3. **Nạp vào Resolve:**
   * **Tạo Timeline đã gọt giũa + Markers:** Vào **File** > **Import** > **Timeline...** > Chọn file `.edl`.
   * **Nạp phụ đề Karaoke nảy chữ:** Vào **File** > **Import** > **Timeline...** > Chọn file `_karaoke.fcpxml`.

---

### 🔵 3. Dành cho DaVinci Resolve STUDIO (Bản Quyền)
1. Mở DaVinci Resolve Studio và mở sẵn Project/Timeline của bạn.
2. Trên ResolveFlow, bấm **"Tự lấy từ Resolve"** -> Bấm **"KHỞI CHẠY TIẾN TRÌNH TỰ ĐỘNG HÓA v4.0"** -> Hệ thống tự động tạo Timeline và phụ đề trực tiếp vào Resolve!

---

## 🧪 Chạy Kiểm Thử Tự Động (Unit Tests)
```powershell
.\venv\Scripts\pytest -v
```
Toàn bộ **32/32 kịch bản kiểm thử** độc lập đạt kết quả kiểm thử **100% PASSED**.