import sys
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
    
    # 4. Gắn tín hiệu báo cáo tiến trình & điều khiển từ Bubble ngược lại AutoWindow
    bubble.stop_requested.connect(auto_win._stop_pipeline)
    if hasattr(auto_win, '_pause_pipeline'):
        bubble.pause_requested.connect(auto_win._pause_pipeline)
    
    # Hiển thị Bubble ngay khi khởi động
    bubble.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    start_app()
