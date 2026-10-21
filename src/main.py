import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Cấu hình chuẩn cho Qt6 trên Linux (XCB display server & FFmpeg media engine)
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PySide6.QtWidgets import QApplication
from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.shell.tray import ChunDVCTrayIcon
from src.ui.bubble.floating_bubble import FloatingBubbleWidget
from src.ui.studio.studio_window import StudioWindow


def start_app():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 1. Khởi tạo EdgeDockWindow làm surface chính (tích hợp Rail, Palette HUD, Global Hotkey)
    dock = EdgeDockWindow()
    dock.show()

    # 2. Khởi tạo Tray Icon trong khay hệ thống
    tray = ChunDVCTrayIcon()
    tray.open_dock_requested.connect(dock.toggle)
    tray.open_director_requested.connect(dock.open_director)
    tray.quit_requested.connect(app.quit)
    tray.show()

    # 3. Hỗ trợ Bubble nổi dạng tùy chọn qua cờ --bubble
    bubble = None
    if "--bubble" in sys.argv:
        bubble = FloatingBubbleWidget()
        bubble.open_auto_requested.connect(dock.open_director)
        bubble.open_studio_requested.connect(dock.open_dock)
        bubble.show()
        bubble.raise_()
        bubble.activateWindow()

    # 4. Hỗ trợ mở trực tiếp các cửa sổ qua cờ CLI
    if "--director" in sys.argv or "--auto" in sys.argv:
        dock.open_director()
    elif "--studio" in sys.argv:
        studio_win = StudioWindow()
        studio_win.show()
        studio_win.raise_()
        studio_win.activateWindow()

    sys.exit(app.exec())


if __name__ == "__main__":
    start_app()
