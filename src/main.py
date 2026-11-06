import os
import sys
import logging
from typing import Optional

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

if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
logger = logging.getLogger("ChunDVC.Main")


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

    from src.core.storage_manager import StorageManager
    assets.extend(StorageManager().ui_assets())
    return assets


def _compute_storage_poll_signature(sm) -> tuple:
    """
    Tính toán signature trạng thái kho media thuần túy qua stat filesystem (zero IO nặng).
    Dùng để phát hiện các thay đổi offline/reconnect/root/taxonomy-only mà không parse media hay scan bundled.
    Lưu ý: Stat polling là cơ chế phát hiện nhẹ nhàng (advisory), không phải transaction authority.
    """
    root_val = sm._config.get("root")
    expected_id = sm._config.get("library_id")
    is_online = sm.online

    def _file_stat_sig(path) -> tuple:
        if not path:
            return (0, 0)
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    marker_sig = (0, 0)
    reg_sig = (0, 0)
    tax_sig = (0, 0)

    try:
        if root_val:
            meta_dir = sm.meta
            marker_sig = _file_stat_sig(meta_dir / "library.json")
            reg_sig = _file_stat_sig(meta_dir / "registry.json")
            tax_sig = _file_stat_sig(meta_dir / "taxonomy.json")
    except Exception:
        pass

    snap_sig = (0, 0)
    try:
        snap_path = getattr(sm, "_snapshot_path", lambda: None)()
        if snap_path:
            snap_sig = _file_stat_sig(snap_path)
    except Exception:
        pass

    return (root_val, expected_id, is_online, marker_sig, reg_sig, tax_sig, snap_sig)


class _StudioSyncCoordinator:
    """Điều phối đồng bộ, xử lý lỗi tại slot boundary và retry cho StudioWindow."""
    def __init__(self, dock, insert_service, search_index):
        from PySide6.QtCore import QTimer
        self.dock = dock
        self.insert_service = insert_service
        self.search_index = search_index
        self.studio_window = None
        self.pending_snapshot = None  # (managed_assets, taxonomy)

        self.retry_timer = QTimer(dock)
        self.retry_timer.setInterval(1000)
        self.retry_timer.timeout.connect(self._on_retry_tick)

        dock.storage_assets_changed.connect(self._on_dock_storage_assets_changed)

    def _apply_to_studio(self, managed_assets, taxonomy) -> bool:
        if self.studio_window is None:
            return True
        try:
            self.studio_window.apply_storage_snapshot(
                managed_assets=managed_assets,
                taxonomy=taxonomy,
            )
            return True
        except Exception as exc:
            logger.warning("Không thể áp dụng snapshot sang StudioWindow: %s", exc)
            return False

    def _on_retry_tick(self):
        if self.studio_window is None or self.pending_snapshot is None:
            self.retry_timer.stop()
            return
        managed_assets, taxonomy = self.pending_snapshot
        if self._apply_to_studio(managed_assets, taxonomy):
            self.pending_snapshot = None
            self.retry_timer.stop()

    def _on_dock_storage_assets_changed(self, managed_assets, taxonomy):
        if self.studio_window is not None:
            # Snapshot mới đến luôn thay thế pending snapshot cũ (newer supersedes older)
            if not self._apply_to_studio(managed_assets, taxonomy):
                self.pending_snapshot = (managed_assets, taxonomy)
                if not self.retry_timer.isActive():
                    self.retry_timer.start()
            else:
                self.pending_snapshot = None
                if self.retry_timer.isActive():
                    self.retry_timer.stop()
        else:
            self.pending_snapshot = None
            if self.retry_timer.isActive():
                self.retry_timer.stop()

    def _on_return_to_dock(self):
        if self.studio_window:
            self.studio_window.hide()
        self.dock.show()
        self.dock.fsm.open_dock(animated=False)

    def _on_studio_assets_updated(self, new_assets):
        self.dock.set_assets(new_assets)
        self.search_index.set_assets(new_assets)
        self.dock.set_search_index(self.search_index)

    def open_studio(self):
        if self.studio_window is None:
            self.studio_window = StudioWindow(insert_service=self.insert_service)
            self.studio_window.return_to_dock_requested.connect(self._on_return_to_dock)
            self.studio_window.assets_updated.connect(self._on_studio_assets_updated)
            self.studio_window.open_auto_requested.connect(self.dock.open_director)

            # Lần đầu mở Studio: chủ động nạp snapshot mới nhất từ StorageManager
            try:
                from src.core.storage_manager import StorageManager
                sm = StorageManager()
                managed = sm.ui_assets()
                tax = sm.taxonomy()
                if not self._apply_to_studio(managed, tax):
                    self.pending_snapshot = (managed, tax)
                    if not self.retry_timer.isActive():
                        self.retry_timer.start()
            except Exception as exc:
                logger.warning("Không thể nạp snapshot ban đầu cho StudioWindow: %s", exc)
                if self.pending_snapshot is not None and not self.retry_timer.isActive():
                    self.retry_timer.start()

        self.studio_window.show()
        self.studio_window.raise_()
        self.studio_window.activateWindow()
        return self.studio_window


def create_studio_manager(dock, insert_service, search_index):
    """Quản lý vòng đời và wiring cho cửa sổ Kho Đạo Cụ StudioWindow."""
    coordinator = _StudioSyncCoordinator(dock, insert_service, search_index)
    return coordinator.open_studio


def poll_storage_status(sm=None, dock=None, storage_signature: Optional[list] = None) -> bool:
    """
    Thực hiện 1 chu kỳ kiểm tra (poll tick) trạng thái kho lưu trữ.
    - Dùng sm cấu hình hiện tại (nếu sm=None, khởi tạo fresh StorageManager() để đọc config mới nhất ở mỗi tick).
    - So sánh chữ ký stat filesystem.
    - Khi có thay đổi: gọi dock.refresh_storage_assets().
    - CHỈ cập nhật storage_signature[0] sau khi refresh thành công.
    - Trả về True nếu có cập nhật thành công, False nếu không đổi hoặc lỗi.
    """
    if sm is None:
        from src.core.storage_manager import StorageManager
        sm = StorageManager()

    if storage_signature is None:
        storage_signature = [None]

    try:
        current_sig = _compute_storage_poll_signature(sm)
    except Exception as exc:
        logger.warning("Lỗi tính toán signature kho lưu trữ: %s", exc)
        return False

    if current_sig == storage_signature[0]:
        return False

    try:
        if dock is not None:
            dock.refresh_storage_assets()
        storage_signature[0] = current_sig
        return True
    except Exception as exc:
        logger.error("Lỗi khi đồng bộ tài nguyên lưu trữ nền: %s", exc)
        # Không cập nhật storage_signature[0] để tick sau có thể retry
        return False


def start_app():
    app = QApplication(sys.argv)
    app.setApplicationName("ChunDVC")
    app.setApplicationDisplayName("ChunDVC")
    app.setDesktopFileName("chundvc.desktop")
    app.setQuitOnLastWindowClosed(False)

    from src.ui.icons import get_app_icon
    app_icon = get_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    from src.services.source_shutdown import SourceShutdownGuard
    source_shutdown = SourceShutdownGuard(app)

    # 1. Khởi tạo Gateway, Insert Service và Resolve State Service
    gateway = ResolveGateway()
    insert_service = InsertService(gateway=gateway)
    resolve_state = ResolveStateService(gateway=gateway, poll_interval_ms=1000)

    # 2. Quét & nạp toàn bộ kho đạo cụ (600+ assets) và bộ chỉ mục tìm kiếm
    try:
        from src.core.transition_preset import TransitionMacroGenerator
        TransitionMacroGenerator.install_transitions_to_davinci_resolve()
    except Exception as e:
        logger.warning("Auto-install transitions to DaVinci Resolve error: %s", e)

    all_assets = load_all_library_assets()
    search_index = SearchIndex(assets=all_assets)

    # 3. Khởi tạo EdgeDockWindow làm surface chính
    dock = EdgeDockWindow(
        insert_service=insert_service,
        resolve_state=resolve_state,
    )
    dock.set_assets(all_assets)
    dock.set_search_index(search_index)

    # 4. Quản lý cửa sổ Kho Đạo Cụ Studio (TabAssets 4 cột đầy đủ)
    # Đặt wiring StudioManager trước khi khởi động timer để đảm bảo lắng nghe đầy đủ
    open_studio = create_studio_manager(dock, insert_service, search_index)
    dock.restore_standard_window_requested.connect(open_studio)

    # 5. Timer polling trạng thái kho (stat polling nhẹ nhàng, không parse media hay scan bundled)
    from PySide6.QtCore import QTimer
    storage_signature = [None]

    storage_timer = QTimer(dock)
    storage_timer.timeout.connect(lambda: poll_storage_status(None, dock, storage_signature))
    storage_timer.start(2500)
    dock.show()

    # 5. Khởi tạo Tray Icon trong khay hệ thống
    tray = ChunDVCTrayIcon()
    tray.open_dock_requested.connect(dock.toggle)
    tray.open_director_requested.connect(dock.open_director)
    tray.open_studio_requested.connect(open_studio)
    tray.settings_requested.connect(dock.open_settings)
    tray.quit_requested.connect(source_shutdown.request_quit)
    tray.show()

    # 6. Hỗ trợ khởi động thẳng ở trạng thái Quả cầu nổi qua cờ --bubble
    if "--bubble" in sys.argv:
        dock.collapse_dock(to_strip=True, animated=False)

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

    # 8. Tự động kiểm tra bản cập nhật mới nhất từ GitHub trong chế độ nền (Silent Auto-Update Toast "như Orca")
    def _trigger_background_update_check():
        try:
            from src.services.update_service import AutoUpdateController
            update_ctrl = AutoUpdateController(parent=dock)
            dock._update_controller = update_ctrl
            update_ctrl.start_silent_check()
        except Exception as e:
            logger.debug("Auto update silent check error: %s", e)

    QTimer.singleShot(3500, _trigger_background_update_check)

    def cleanup():
        try:
            resolve_state.pause_polling()
            gateway.shutdown()
        except Exception:
            pass

    app.aboutToQuit.connect(cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    start_app()
