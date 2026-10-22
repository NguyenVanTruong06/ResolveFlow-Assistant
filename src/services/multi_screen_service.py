"""
src/services/multi_screen_service.py
Dịch vụ Quản lý Đa Màn Hình (Multi-Monitor Service).

Chuyên trách:
- Nhận diện danh sách màn hình kết nối vào máy tính (QGuiApplication.screens).
- Phát hiện màn hình chính (Primary), màn hình phụ (Secondary), độ phân giải, tọa độ.
- Hỗ trợ di chuyển cửa sổ (StudioWindow, EdgeDockWindow) xuyên màn hình mượt mà.
- Lưu trữ cấu hình màn hình ưa thích của người dùng vào QSettings.
"""
from typing import List, Dict, Any, Optional
import logging

from PySide6.QtCore import QObject, QSettings, QPoint, QRect, Signal as pyqtSignal
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget, QApplication

logger = logging.getLogger("ResolveFlow.MultiScreen")


class MultiScreenService(QObject):
    """
    Dịch vụ phát hiện và điều phối cửa sổ trên môi trường đa màn hình (Dual/Multi Monitor).
    """
    screens_changed = pyqtSignal(int)  # Phát ra số lượng màn hình khi có cắm/rút màn hình

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._settings = QSettings("ChunDVC", "ResolveFlow")
        app = QGuiApplication.instance()
        if app:
            app.screenAdded.connect(self._on_screens_changed)
            app.screenRemoved.connect(self._on_screens_changed)

    def _on_screens_changed(self, screen: QScreen):
        count = self.get_screen_count()
        logger.info("Screen configuration changed. Current count: %d", count)
        self.screens_changed.emit(count)

    def get_screens(self) -> List[QScreen]:
        """Trả về danh sách đối tượng QScreen hiện có."""
        app = QGuiApplication.instance()
        if not app:
            return []
        return app.screens()

    def get_screen_count(self) -> int:
        """Số lượng màn hình hiện tại."""
        return len(self.get_screens())

    def is_multi_monitor(self) -> bool:
        """True nếu hệ thống có từ 2 màn hình trở lên."""
        return self.get_screen_count() >= 2

    def get_screens_info(self) -> List[Dict[str, Any]]:
        """
        Trả về thông tin chi tiết từng màn hình:
        [
            {
                "index": 0,
                "name": "HDMI-1-0",
                "is_primary": True,
                "x": 0, "y": 121, "width": 1920, "height": 1080,
                "dpr": 1.0
            },
            ...
        ]
        """
        screens = self.get_screens()
        primary = QGuiApplication.primaryScreen()
        results = []
        for idx, s in enumerate(screens):
            geom = s.geometry()
            avail = s.availableGeometry()
            is_prim = (s == primary)
            results.append({
                "index": idx,
                "name": s.name() or f"Màn hình {idx + 1}",
                "is_primary": is_prim,
                "x": geom.x(),
                "y": geom.y(),
                "width": geom.width(),
                "height": geom.height(),
                "avail_x": avail.x(),
                "avail_y": avail.y(),
                "avail_width": avail.width(),
                "avail_height": avail.height(),
                "dpr": s.devicePixelRatio(),
            })
        return results

    def get_screen_by_index(self, index: int) -> Optional[QScreen]:
        """Lấy QScreen theo chỉ số (0, 1,...), an toàn tránh index error."""
        screens = self.get_screens()
        if 0 <= index < len(screens):
            return screens[index]
        if screens:
            return screens[0]
        return None

    def get_screen_of_widget(self, widget: QWidget) -> Optional[QScreen]:
        """Xác định widget đang nằm chủ yếu trên màn hình nào."""
        if not widget:
            return None
        center = widget.geometry().center()
        app = QGuiApplication.instance()
        if app:
            screen = app.screenAt(center)
            if screen:
                return screen
        return QGuiApplication.primaryScreen()

    def get_screen_index_of_widget(self, widget: QWidget) -> int:
        """Trả về index của màn hình đang chứa widget (0, 1,...)."""
        widget_screen = self.get_screen_of_widget(widget)
        if not widget_screen:
            return 0
        screens = self.get_screens()
        for idx, s in enumerate(screens):
            if s == widget_screen or s.name() == widget_screen.name():
                return idx
        return 0

    def get_other_screen_index(self, current_index: int) -> int:
        """Nếu có >= 2 màn hình, trả về màn hình còn lại (0 -> 1, 1 -> 0)."""
        count = self.get_screen_count()
        if count <= 1:
            return 0
        return 0 if current_index >= 1 else 1

    def move_widget_to_screen(
        self,
        widget: QWidget,
        target_screen_index: int,
        fit_ratio: float = 0.85
    ) -> bool:
        """
        Di chuyển widget sang màn hình target_screen_index một cách mượt mà và canh giữa.
        """
        target_screen = self.get_screen_by_index(target_screen_index)
        if not target_screen or not widget:
            return False

        avail = target_screen.availableGeometry()
        curr_w = widget.width()
        curr_h = widget.height()

        # Nếu kích thước cửa sổ lớn hơn màn hình đích, co lại theo fit_ratio
        target_w = min(curr_w, int(avail.width() * fit_ratio))
        target_h = min(curr_h, int(avail.height() * fit_ratio))

        # Tọa độ canh giữa màn hình đích
        target_x = avail.x() + max(0, (avail.width() - target_w) // 2)
        target_y = avail.y() + max(0, (avail.height() - target_h) // 2)

        # Di chuyển widget
        widget.resize(target_w, target_h)
        widget.move(target_x, target_y)
        widget.raise_()
        widget.activateWindow()

        logger.info(
            "Moved widget '%s' to screen %d (%s) at (%d, %d, %d, %d)",
            widget.windowTitle(),
            target_screen_index,
            target_screen.name(),
            target_x, target_y, target_w, target_h
        )
        return True

    # -------------------------------------------------------------------------
    # Lưu trữ cấu hình người dùng (QSettings)
    # -------------------------------------------------------------------------
    def get_preferred_studio_screen(self) -> int:
        """Lấy index màn hình ưa thích cho Kho Đạo Cụ (Mặc định: màn 1 nếu có >= 2 màn hình, ngược lại màn 0)."""
        default_idx = 1 if self.is_multi_monitor() else 0
        return int(self._settings.value("display/studio_screen_index", default_idx))

    def set_preferred_studio_screen(self, screen_index: int):
        self._settings.setValue("display/studio_screen_index", int(screen_index))

    def get_preferred_dock_screen(self) -> int:
        """Lấy index màn hình cho Thanh Dock (Mặc định: màn 0 - nơi chứa Resolve)."""
        return int(self._settings.value("display/dock_screen_index", 0))

    def set_preferred_dock_screen(self, screen_index: int):
        self._settings.setValue("display/dock_screen_index", int(screen_index))

    def is_auto_open_studio_on_secondary(self) -> bool:
        """Có tự động mở Kho Đạo Cụ trên màn hình phụ hay không."""
        return self._settings.value("display/auto_open_studio_on_secondary", True, type=bool)

    def set_auto_open_studio_on_secondary(self, enabled: bool):
        self._settings.setValue("display/auto_open_studio_on_secondary", bool(enabled))


# Singleton instance tiện lợi
_instance: Optional[MultiScreenService] = None


def get_multi_screen_service() -> MultiScreenService:
    global _instance
    if _instance is None:
        _instance = MultiScreenService()
    return _instance
