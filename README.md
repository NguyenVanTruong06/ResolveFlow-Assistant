# ResolveFlow Assistant (AI Video Automation Suite for DaVinci Resolve)

ResolveFlow Assistant là trợ lý AI tự động hóa quy trình hậu kỳ video 100% cục bộ (Local / On-Premise) dành cho DaVinci Resolve. 
Hệ thống tích hợp nhận dạng giọng nói tự động (Speech-to-Text) và công nghệ cắt khoảng lặng thông minh (Smart Cut) bằng thuật toán xử lý âm thanh thô để giúp tăng tốc độ edit video lên gấp nhiều lần.

---

## ✨ Tính năng nổi bật
1. **AI Speech-to-Text Ngoại tuyến**: Tải mô hình Whisper cục bộ lên GPU CUDA (hoặc CPU) để dịch giọng nói tiếng Việt và tiếng Anh chính xác tuyệt đối mà không cần gửi dữ liệu lên đám mây, hoàn toàn bảo mật và miễn phí token.
2. **Cắt khoảng lặng thông minh (Smart Cut)**: Tự động phân tích sóng âm thô PCM (dB RMS), cô lập các vùng im lặng kéo dài và chuẩn bị sẵn sơ đồ cắt thô video (Ripple Cut).
3. **Đồng bộ tự động vào DaVinci Resolve**: Tự động kết nối và chèn trực tiếp phụ đề đồng bộ chuẩn xác thời gian dưới dạng một Subtitle Track độc lập trên Timeline của bạn.
4. **Giao diện Modern Dark Mode Premium**: Bảng điều khiển trực quan bằng PySide6 hỗ trợ tùy chỉnh tham số model Whisper, kiểu dáng phụ đề và biên độ khoảng im lặng chỉ bằng vài cú click chuột.

---

## 📂 Cấu trúc thư mục dự án
```text
ResolveFlow-Assistant/
├── assets/             # Tài nguyên đồ họa, sơ đồ kiến trúc hệ thống
├── docs/               # Tài liệu SRS và System Design bằng Word (.docx) & Markdown (.md)
├── src/                # Mã nguồn chính của ứng dụng
│   ├── core/           # Xử lý lõi (audio.py, transcriber.py, resolve_api.py, autocut.py)
│   └── ui/             # Giao diện người dùng đồ họa (app.py)
├── tests/              # Kịch bản kiểm thử tự động (pytest)
├── main.py             # File chạy khởi động ứng dụng chính (Entrypoint)
└── requirements.txt    # Danh sách các thư viện Python cần thiết
```

---

## 💻 Yêu cầu hệ thống & Chuẩn bị
* **Hệ điều hành**: Windows 10/11
* **Phần cứng khuyên dùng**: GPU NVIDIA (hỗ trợ CUDA) để tăng tốc độ nhận diện giọng nói bằng AI.
* **Môi trường**: Python 3.10+
* **Công cụ bổ trợ bắt buộc**:
  - [FFmpeg](https://ffmpeg.org/): Tải về và cấu hình đường dẫn thư mục `bin` vào biến môi trường **PATH** của hệ thống (để có thể gọi lệnh `ffmpeg` và `ffprobe` từ Terminal).

---

## 🛠️ Hướng dẫn cài đặt

1. **Khởi tạo và kích hoạt môi trường ảo (Virtual Environment):**
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. **Cài đặt các gói thư viện phụ thuộc:**
   ```powershell
   pip install -r requirements.txt
   pip install PySide6 pytest
   ```

---

## 🚀 Hướng dẫn vận hành

### 1. Khởi động giao diện Dashboard
Đảm bảo bạn đã mở ứng dụng DaVinci Resolve và có một dự án (Project) cùng Timeline đang hoạt động.
Sau đó chạy lệnh:
```powershell
python main.py
```

### 2. Các bước thao tác trên giao diện:
1. Nhấn nút **Chọn Video** ở góc phải để nạp file video nguồn của bạn.
2. Tùy chỉnh thông số:
   - **Whisper AI**: Chọn kích thước model (ví dụ: `small` cân bằng tốt giữa tốc độ và độ chính xác) và chọn Ngôn ngữ đầu vào.
   - **Kiểu dáng phụ đề**: Thiết lập tên Font, cỡ chữ và màu sắc Hex.
   - **Cắt khoảng lặng**: Kích hoạt/Tắt tính năng Smart Cut, tinh chỉnh thanh trượt ngưỡng im lặng (dB) và thời gian ngắt im lặng tối thiểu.
3. Nhấp nút **KHỞI CHẠY TIẾN TRÌNH TỰ ĐỘNG HÓA**. 
4. Theo dõi hộp thoại Console log màu xanh hiển thị tiến độ thời gian thực. Sau khi hoàn tất, phụ đề sẽ được chèn trực tiếp vào Timeline của DaVinci Resolve!

---

## 🧪 Chạy Kiểm thử tự động (Test Suite)
Dự án được bao phủ bởi các kịch bản Unit Test toàn diện. Bạn có thể chạy kiểm thử bất kỳ lúc nào để đảm bảo hệ thống hoạt động ổn định:
```powershell
pytest -v
```
Toàn bộ các thư viện ngoài và API DaVinci Resolve đều được mock chi tiết để kiểm thử có thể chạy độc lập ngoại tuyến.