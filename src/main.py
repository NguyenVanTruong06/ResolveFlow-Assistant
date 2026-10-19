import os
import sys

# Cấu hình chuẩn cho Qt6 trên Linux (XCB display server & FFmpeg media engine)
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PySide6.QtWidgets import QApplication
from src.ui.bubble.floating_bubble import FloatingBubbleWidget
from src.ui.auto.auto_window import AutoWindow
from src.ui.studio.studio_window import StudioWindow

def start_app():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False) # Đóng cửa sổ con không thoát app, chỉ thoát khi chuột phải vào bubble
    
    # 1. Khởi tạo Bubble (Trung tâm điều hướng - Singleton)
    bubble = FloatingBubbleWidget()
    
    # 2. Khởi tạo 2 Cửa sổ (Singleton)
    auto_win = AutoWindow()
    auto_win.bubble = bubble # Cho phép AutoWindow gọi update_progress trên Bubble
    
    studio_win = StudioWindow()
    
    # 3. Gắn tín hiệu điều hướng từ Bubble
    bubble.open_auto_requested.connect(auto_win.show)
    bubble.open_auto_requested.connect(auto_win.raise_)
    bubble.open_auto_requested.connect(auto_win.activateWindow)
    
    bubble.open_studio_requested.connect(studio_win.show)
    bubble.open_studio_requested.connect(studio_win.raise_)
    bubble.open_studio_requested.connect(studio_win.activateWindow)
    
    # 3.1. Điều hướng liên thông trực tiếp giữa AutoWindow <-> StudioWindow
    auto_win.open_studio_requested.connect(studio_win.show)
    auto_win.open_studio_requested.connect(studio_win.raise_)
    auto_win.open_studio_requested.connect(studio_win.activateWindow)

    studio_win.open_auto_requested.connect(auto_win.show)
    studio_win.open_auto_requested.connect(auto_win.raise_)
    studio_win.open_auto_requested.connect(auto_win.activateWindow)
    
    # 4. Gắn tín hiệu báo cáo tiến trình & điều khiển từ Bubble ngược lại AutoWindow
    bubble.stop_requested.connect(auto_win._stop_pipeline)
    if hasattr(auto_win, '_pause_pipeline'):
        bubble.pause_requested.connect(auto_win._pause_pipeline)

    def _on_insert_timeline_clicked():
        auto_win.show()
        auto_win.raise_()
        auto_win.activateWindow()
        auto_win._log_message("🎞️ [Bong bóng] Đang nạp timeline vào DaVinci Resolve...")

    bubble.insert_timeline_requested.connect(_on_insert_timeline_clicked)
    
    # Khởi động: Chỉ hiển thị Bong bóng nổi (Desktop Bubble) làm điểm chạm đầu tiên.
    # Người dùng click vào quả cầu để chọn mở 'AI Director' hoặc 'Kho Đạo Cụ'.
    bubble.show()
    bubble.raise_()
    bubble.activateWindow()

    # Hỗ trợ tùy chọn mở thẳng cửa sổ nếu truyền cờ CLI (--auto hoặc --studio)
    if "--auto" in sys.argv:
        auto_win.show()
    elif "--studio" in sys.argv:
        studio_win.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    start_app()
