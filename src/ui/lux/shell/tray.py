"""
ChunDVC System Tray Icon.
Provides a clean, persistent OS tray icon replacing the floating bubble by default.
"""
from typing import Optional
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication, QMessageBox
from PySide6.QtCore import QObject, Signal as pyqtSignal
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush, QPen
from src.core.branding import TRAY_TOOLTIP, BRAND_BADGE_TEXT


# Global settings defaults
SHOW_FLOATING_BUBBLE = False
USE_NEW_EDGE_DOCK = True


def create_tray_pixmap() -> QPixmap:
    """Generates a high-DPI 32x32 ChunDVC bolt icon for the tray."""
    pm = QPixmap(32, 32)
    pm.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Purple circular background
    painter.setBrush(QBrush(QColor("#7c3aed")))
    painter.setPen(QPen(QColor("#a78bfa"), 1))
    painter.drawRoundedRect(2, 2, 28, 28, 6, 6)

    # White lightning bolt symbol
    painter.setPen(QPen(QColor("#ffffff"), 2))
    painter.setBrush(QBrush(QColor("#ffffff")))
    points = [
        (17, 6), (10, 16), (15, 16),
        (13, 26), (22, 14), (17, 14)
    ]
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF
    poly = QPolygonF([QPointF(x, y) for x, y in points])
    painter.drawPolygon(poly)

    painter.end()
    return pm


class ChunDVCTrayIcon(QSystemTrayIcon):
    """
    Application Tray Icon:
    - Left click: Toggles Edge Dock
    - Right click: Context menu with full shortcuts & Quit
    """
    open_dock_requested = pyqtSignal()
    open_director_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None):
        icon = QIcon(create_tray_pixmap())
        super().__init__(icon, parent)
        self.setToolTip(TRAY_TOOLTIP)
        self._init_menu()
        self.activated.connect(self._on_activated)

    def _show_about_dialog(self):
        QMessageBox.about(
            None,
            "Giới thiệu ChunDVC Studio",
            f"<h3><b>{BRAND_BADGE_TEXT}</b></h3>"
            "<p>Hệ thống trợ lý AI Director & Kho Đạo Cụ chuyên nghiệp cho DaVinci Resolve.</p>"
            "<p>Phát triển bởi <b>Trương</b> với công nghệ AI thị giác local và đồng bộ hóa DaVinci API.</p>"
        )

    def _init_menu(self):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #27272a;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #27272a;
                margin: 4px 6px;
            }
        """)

        act_dock = menu.addAction("⚡ Mở Edge Dock")
        act_dock.triggered.connect(self.open_dock_requested.emit)

        act_director = menu.addAction("🤖 Mở AI Director")
        act_director.triggered.connect(self.open_director_requested.emit)

        act_studio = menu.addAction("🎨 Mở Kho Đạo Cụ (Studio)")
        act_studio.triggered.connect(self.open_studio_requested.emit)

        menu.addSeparator()

        act_settings = menu.addAction("⚙️ Cài đặt...")
        act_settings.triggered.connect(self.settings_requested.emit)

        act_about = menu.addAction("ℹ️ Giới thiệu ChunDVC...")
        act_about.triggered.connect(self._show_about_dialog)

        menu.addSeparator()

        act_quit = menu.addAction("❌ Thoát ChunDVC")
        act_quit.triggered.connect(self._on_quit_triggered)

        self.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_dock_requested.emit()

    def _on_quit_triggered(self):
        self.quit_requested.emit()
