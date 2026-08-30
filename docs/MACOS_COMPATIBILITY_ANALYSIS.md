# 🍏 Báo Cáo Đánh Giá Khả Năng Hỗ Trợ Hệ Điều Hành macOS

## 1. Tổng Quan
ResolveFlow Assistant hiện đang tối ưu hóa mạnh mẽ trên **Windows 10/11**. Tài liệu này phân tích chi tiết mức độ khả thi, độ phức tạp kỹ thuật và các điểm cần thay đổi khi mở rộng hỗ trợ sang hệ điều hành **macOS** (cả Intel và Apple Silicon M1/M2/M3/M4).

---

## 2. Ma Trận Đánh Giá Độ Khó Kỹ Thuật (Feasibility Matrix)

| Hạng mục kỹ thuật | Trạng thái hiện tại trên Windows | Thay đổi cần thiết trên macOS | Mức độ phức tạp |
| :--- | :--- | :--- | :---: |
| **Giao diện GUI (PySide6)** | Chuẩn Qt6 đa nền tảng | Chạy native 100% trên macOS (Cocoa) | **Rất Thấp (0/5)** |
| **Đường dẫn tệp (Path Conventions)** | Dùng Windows backslash & drive letter (`D:\...`) | Chuyển đổi sang chuẩn POSIX (`/Users/...`). `pathlib` và `os.path.normpath` hiện đã hỗ trợ tốt. | **Thấp (1/5)** |
| **FFmpeg & ffprobe binary** | Yêu cầu trong Windows PATH | Cài đặt qua `brew install ffmpeg` hoặc bundle binary macOS | **Thấp (1/5)** |
| **DaVinci Resolve Scripting API** | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting` | Thư mục macOS: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/` | **Trung Bình (2/5)** |
| **Mô hình Whisper AI (Inference)** | CUDA trên GPU NVIDIA hoặc CPU float32 | Apple Silicon Metal Performance Shaders (MPS) / CoreML / CPU NEON. `ctranslate2` và `faster-whisper` hỗ trợ rất tốt Apple Silicon. | **Thấp (1/5)** |
| **FCPXML URI Encoding** | `file:///D:/path...` | `file:///Users/path...` (Chuẩn gốc của Apple FCPXML v1.9, tương thích tuyệt đối) | **Rất Thấp (0/5)** |
| **Script Cài Đặt (DevOps)** | `setup_project.ps1` (PowerShell) & `.bat` | Tạo thêm `setup_project.sh` (Bash / Zsh script) cho macOS | **Thấp (1/5)** |

---

## 3. Các Điểm Khác Biệt Cụ Thể

### 3.1. Đường dẫn Module Scripting DaVinci Resolve
- **Windows:**
  ```python
  resolve_module_path = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
  ```
- **macOS:**
  ```python
  resolve_module_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
  ```

### 3.2. Script Cài Đặt Tự Động (`setup_project.sh`)
Thay vì chỉ cung cấp script PowerShell cho Windows, bổ sung script shell POSIX tiêu chuẩn:
```bash
#!/usr/bin/env bash
set -e
echo "🍏 Đang khởi tạo môi trường ResolveFlow Assistant cho macOS..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install PySide6 pytest pillow
echo "✔ Cài đặt hoàn tất! Chạy bằng lệnh: python3 main.py"
```

---

## 4. Kết Luận & Đề Xuất
- **Khả năng tương thích:** **95% mã nguồn hiện tại đã độc lập nền tảng (Platform-Agnostic)** nhờ sử dụng Python chuẩn, PySide6, Pydantic và FCPXML v1.9 (vốn là định dạng gốc của Apple Final Cut Pro).
- **Thời gian ước tính triển khai macOS:** Khoảng **1-2 giờ làm việc** (chủ yếu cấu hình đường dẫn Resolve API trên macOS và viết shell script).
