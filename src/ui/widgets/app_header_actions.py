"""Shared version badge and quick navigation for the application windows."""

from typing import Optional
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QMenu
from PySide6.QtCore import Qt, Signal

from src.core.branding import APP_VERSION


class AppHeaderActions(QWidget):
    settings_requested = Signal()
    ai_setup_requested = Signal()
    studio_requested = Signal()
    director_requested = Signal()
    dock_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, show_version: bool = True):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.lbl_version = None
        if show_version:
            self.lbl_version = QLabel(str(APP_VERSION))
            self.lbl_version.setObjectName("lbl_app_version")
            self.lbl_version.setStyleSheet(
                "color: #a1a1aa; font-size: 11px; padding: 2px 4px;"
            )
            self.lbl_version.setToolTip(f"Phiên bản ứng dụng {APP_VERSION}")
            layout.addWidget(self.lbl_version)

        self.btn_launcher = QPushButton("☰")
        self.btn_launcher.setObjectName("btn_app_launcher")
        self.btn_launcher.setFixedSize(32, 32)
        self.btn_launcher.setAccessibleName("Mở nhanh · Kho, AI, Dock và Cài đặt")
        self.btn_launcher.setToolTip("Mở nhanh Kho, AI Director, Thanh Dock và Cài đặt")
        self.btn_launcher.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launcher.setStyleSheet("""
            QPushButton {
                background: #23252a; color: #ededf0;
                border: 1px solid #444850; border-radius: 16px;
                font-size: 16px; padding: 0;
            }
            QPushButton:hover, QPushButton:focus {
                border-color: #e86352; background: #303238;
            }
        """)
        # Kept for existing header callers.
        self.btn_settings = self.btn_launcher
        layout.addWidget(self.btn_launcher)

        self.menu = QMenu(self)
        self.menu.setStyleSheet("""
            QMenu {
                background: #1a1b1f; color: #ededf0;
                border: 1px solid #3a3d44; padding: 6px; font-size: 12px;
            }
            QMenu::item { padding: 9px 18px; }
            QMenu::item:selected { background: #353840; }
            QMenu::separator { height: 1px; background: #3a3d44; margin: 5px; }
        """)
        self.action_studio = self.menu.addAction("Kho đạo cụ")
        self.action_director = self.menu.addAction("AI Director")
        self.action_dock = self.menu.addAction("Thanh Dock")
        self.menu.addSeparator()
        self.action_ai_setup = self.menu.addAction("Thiết lập AI · Whisper, Ollama")
        self.action_settings = self.menu.addAction("Cài đặt ứng dụng")
        self.action_studio.triggered.connect(self.studio_requested.emit)
        self.action_director.triggered.connect(self.director_requested.emit)
        self.action_dock.triggered.connect(self.dock_requested.emit)
        self.action_ai_setup.triggered.connect(self.ai_setup_requested.emit)
        self.action_settings.triggered.connect(self.settings_requested.emit)
        self.btn_launcher.clicked.connect(self._on_settings_button_clicked)

    def _on_settings_button_clicked(self):
        self.menu.popup(self.btn_launcher.mapToGlobal(self.btn_launcher.rect().bottomRight()))

    def trigger_settings(self):
        self.settings_requested.emit()

    def trigger_ai_setup(self):
        self.ai_setup_requested.emit()
