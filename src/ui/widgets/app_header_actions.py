"""
src/ui/widgets/app_header_actions.py
Cụm nút điều hướng góc trên bên phải dùng chung cho các cửa sổ (StudioWindow, DirectorWindow):
- Luôn hiển thị phiên bản ứng dụng APP_VERSION nhập từ src.core.branding (nguồn duy nhất).
- Nút bánh răng 'Cài đặt' (⚙) cố định, không bị khuất hoặc ẩn khi chuyển tab / bước làm việc.
- Cung cấp menu truy cập nhanh 'Cài đặt ứng dụng' và 'Thiết lập AI (Local AI)'.
- Phát các tín hiệu settings_requested và ai_setup_requested đồng bộ tới bộ điều phối.
"""

from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QMenu, QApplication
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QCursor

from src.core.branding import APP_VERSION


class AppHeaderActions(QWidget):
    """Cụm widget tiêu đề hiển thị Phiên bản và Nút Cài đặt góc trên bên phải."""

    settings_requested = Signal()
    ai_setup_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, show_version: bool = True):
        super().__init__(parent)
        self._show_version = show_version
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Nhãn phiên bản chuẩn (APP_VERSION)
        if self._show_version:
            self.lbl_version = QLabel(str(APP_VERSION))
            self.lbl_version.setObjectName("lbl_app_version")
            self.lbl_version.setStyleSheet("""
                QLabel#lbl_app_version {
                    background-color: rgba(255, 255, 255, 0.06);
                    color: #a1a1aa;
                    border: 1px solid rgba(255, 255, 255, 0.12);
                    border-radius: 4px;
                    padding: 2px 7px;
                    font-size: 11px;
                    font-weight: 600;
                }
            """)
            self.lbl_version.setToolTip(f"Phiên bản ứng dụng: {APP_VERSION}")
            layout.addWidget(self.lbl_version)
        else:
            self.lbl_version = None

        # 2. Nút Cài đặt (⚙)
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("btn_header_settings")
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setAccessibleName("Cài đặt")
        self.btn_settings.setToolTip("Cài đặt ứng dụng & Thiết lập AI (Click để chọn)")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet("""
            QPushButton#btn_header_settings {
                background-color: #23252a;
                color: #d4d4d8;
                border: 1px solid #353942;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton#btn_header_settings:hover {
                background-color: #2c2f36;
                border-color: #d94f3d;
                color: #ffffff;
            }
        """)
        self.btn_settings.clicked.connect(self._on_settings_button_clicked)
        layout.addWidget(self.btn_settings)

    def _on_settings_button_clicked(self):
        """Mở menu truy cập nhanh: Cài đặt ứng dụng hoặc Thiết lập AI."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1b1f;
                color: #ededf0;
                border: 1px solid #2d3039;
                border-radius: 8px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #d94f3d;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #2d3039;
                margin: 4px 0;
            }
        """)

        act_ai = menu.addAction("🤖 Thiết lập AI (Whisper, Ollama)...")
        act_ai.triggered.connect(self.ai_setup_requested.emit)

        menu.addSeparator()

        act_settings = menu.addAction("⚙️ Cài đặt ứng dụng...")
        act_settings.triggered.connect(self.settings_requested.emit)

        # Tính toán vị trí hiển thị menu ngay dưới nút bấm
        pos = self.btn_settings.mapToGlobal(self.btn_settings.rect().bottomLeft())
        menu.exec(pos)

    def trigger_settings(self):
        """Kích hoạt tín hiệu settings trực tiếp phục vụ kiểm thử."""
        self.settings_requested.emit()

    def trigger_ai_setup(self):
        """Kích hoạt tín hiệu AI setup trực tiếp phục vụ kiểm thử."""
        self.ai_setup_requested.emit()
