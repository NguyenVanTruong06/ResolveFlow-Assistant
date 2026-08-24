# ResolveFlow Assistant

ResolveFlow Assistant là một trợ lý thông minh giúp tự động hóa quy trình hậu kỳ trong DaVinci Resolve.
Dự án sử dụng thư viện `faster-whisper` để thực hiện chuyển đổi giọng nói thành văn bản nhanh chóng, kết hợp với `ffmpeg` để xử lý và đồng bộ âm thanh/hình ảnh hiệu quả.

## Cấu trúc thư mục dự án

```text
ResolveFlow-Assistant/
├── assets/             # Chứa tài nguyên dự án (hình ảnh, icon, logo, mẫu)
├── docs/               # Tài liệu hướng dẫn sử dụng và đặc tả kỹ thuật
├── src/                # Mã nguồn chính của ứng dụng
│   ├── core/           # Logic xử lý nghiệp vụ chính (xử lý audio, gọi API DaVinci Resolve)
│   └── ui/             # Giao diện người dùng đồ họa (GUI)
└── tests/              # Các kịch bản kiểm thử (unit tests và integration tests)
```

## Yêu cầu hệ thống
- Windows 10 / 11
- Python 3.8 trở lên
- [FFmpeg](https://ffmpeg.org/) (được thêm vào cấu hình biến môi trường PATH)

## Cài đặt & Hướng dẫn nhanh

1. **Kích hoạt Virtual Environment (Môi trường ảo):**
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

2. **Cài đặt các thư viện cần thiết:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Chạy hoặc phát triển ứng dụng:**
   Viết mã nguồn xử lý trong thư mục `src/` và chạy các file tương ứng.
   ```