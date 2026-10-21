import os
import sys
import logging

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Cấu hình chuẩn cho Qt6 trên Linux (XCB display server & FFmpeg media engine)
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PySide6.QtWidgets import QApplication

from src.services.resolve_gateway import ResolveGateway
from src.services.insert_service import InsertService
from src.services.resolve_state import ResolveStateService
from src.services.search_index import SearchIndex

from src.core.text_preset import BUILTIN_PRESETS
from src.core.transition_preset import BUILTIN_TRANSITIONS
from src.core.asset_indexer import AssetIndexer

from src.ui.lux.shell.edge_dock import EdgeDockWindow
from src.ui.lux.shell.tray import ChunDVCTrayIcon
from src.ui.bubble.floating_bubble import FloatingBubbleWidget
from src.ui.studio.studio_window import StudioWindow

logger = logging.getLogger("ResolveFlow.Main")


def load_all_library_assets():
    """Tải và đồng bộ toàn bộ tài nguyên (Text Presets, Transitions, SFX, Memes, LUTs, Overlays)."""
    assets = []
    assets.extend(BUILTIN_PRESETS)
    assets.extend(BUILTIN_TRANSITIONS)

    try:
        indexer = AssetIndexer()
        for m in indexer.scan_memes():
            item = dict(m)
            item["tab"] = "meme"
            assets.append(item)
        for s in indexer.scan_sfx():
            item = dict(s)
            item["tab"] = "sfx"
            assets.append(item)
        for l in indexer.scan_luts():
            item = dict(l)
            item["tab"] = "lut"
            assets.append(item)
        for o in indexer.scan_overlays():
            item = dict(o)
            item["tab"] = "overlay"
            assets.append(item)
        for t in indexer.scan_titles():
            item = dict(t)
            item["tab"] = "text"
            assets.append(item)
        for tr in indexer.scan_transitions():
            item = dict(tr)
            item["tab"] = "trans"
            assets.append(item)
    except Exception as exc:
        logger.warning("Could not scan asset directories: %s", exc)

    return assets


def start_app():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 1. Khởi tạo Gateway, Insert Service và Resolve State Service
    gateway = ResolveGateway()
    insert_service = InsertService(gateway=gateway)
    resolve_state = ResolveStateService(gateway=gateway, poll_interval_ms=1000)

    # 2. Quét & nạp toàn bộ kho đạo cụ (600+ assets) và bộ chỉ mục tìm kiếm
    all_assets = load_all_library_assets()
    search_index = SearchIndex(assets=all_assets)

    # 3. Khởi tạo EdgeDockWindow làm surface chính
    dock = EdgeDockWindow(
        insert_service=insert_service,
        resolve_state=resolve_state,
    )
    dock.set_assets(all_assets)
    dock.set_search_index(search_index)
    dock.show()

    # 4. Quản lý cửa sổ Kho Đạo Cụ Studio (TabAssets 4 cột đầy đủ)
    studio_window = None

    def open_studio():
        nonlocal studio_window
        if studio_window is None:
            studio_window = StudioWindow()
        studio_window.show()
        studio_window.raise_()
        studio_window.activateWindow()

    dock.restore_standard_window_requested.connect(open_studio)

    # 5. Khởi tạo Tray Icon trong khay hệ thống
    tray = ChunDVCTrayIcon()
    tray.open_dock_requested.connect(dock.toggle)
    tray.open_director_requested.connect(dock.open_director)
    tray.open_studio_requested.connect(open_studio)
    tray.settings_requested.connect(dock.open_settings)
    tray.quit_requested.connect(app.quit)
    tray.show()

    # 6. Hỗ trợ Bubble nổi dạng tùy chọn qua cờ --bubble
    bubble = None
    if "--bubble" in sys.argv:
        bubble = FloatingBubbleWidget()
        bubble.open_auto_requested.connect(dock.open_director)
        bubble.open_studio_requested.connect(dock.open_dock)
        bubble.show()
        bubble.raise_()
        bubble.activateWindow()

    # 7. Khởi chạy giao diện chính:
    from PySide6.QtCore import QSettings
    settings = QSettings("ChunDVC", "ResolveFlow")
    auto_open = settings.value("auto_open_director", True, type=bool)

    if "--studio" in sys.argv:
        open_studio()
    elif "--dock-only" in sys.argv:
        pass  # Chỉ hiển thị EdgeDock
    elif auto_open or "--director" in sys.argv or "--auto" in sys.argv:
        dock.open_director()

    def cleanup():
        try:
            resolve_state.pause_polling()
            gateway.stop()
        except Exception:
            pass

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    start_app()
