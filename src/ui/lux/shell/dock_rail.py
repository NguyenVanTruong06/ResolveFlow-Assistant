"""
Collapsed 44px Rail Widget for Edge Dock.
"""
from typing import Optional
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, QSize, Signal as pyqtSignal

from src.ui.icons import get_vector_icon


class DockRailWidget(QWidget):
    """
    44px Rail bar shown when the dock is collapsed or peeking.
    Contains quick category buttons, shortcut buttons (Palette, Director), and connection status dot.
    """
    category_selected = pyqtSignal(str)
    open_dock_requested = pyqtSignal()
    instant_autocut_clicked = pyqtSignal()
    palette_requested = pyqtSignal()
    director_requested = pyqtSignal()
    settings_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedWidth(44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # 1. Logo button
        self.btn_logo = QPushButton()
        self.btn_logo.setIcon(get_vector_icon("bolt", "#ffffff", 20))
        self.btn_logo.setIconSize(QSize(20, 20))
        self.btn_logo.setFixedSize(34, 34)
        self.btn_logo.setToolTip("ChunDVC Dock (Click để mở)")
        self.btn_logo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_logo.setCursor(Qt.PointingHandCursor)
        self.btn_logo.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7c3aed, stop:1 #06b6d4);
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 8px;
            }
            QPushButton:hover {
                border-color: #ffffff;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:1 #22d3ee);
            }
        """)
        self.btn_logo.clicked.connect(self.open_dock_requested.emit)
        layout.addWidget(self.btn_logo)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #27272a; max-height: 1px;")
        layout.addWidget(sep)

        # 2. Category fast-jump buttons (Hi-DPI vector icons)
        categories = [
            ("text", "text", "Chữ & Tiêu đề"),
            ("trans", "trans", "Chuyển cảnh"),
            ("meme", "meme", "Meme & B-Roll"),
            ("overlay", "overlay", "Lớp phủ & Hiệu ứng"),
            ("sfx", "sfx", "Âm thanh & SFX"),
        ]
        self._cat_buttons = {}
        for cat_id, icon_type, label in categories:
            btn = QPushButton()
            btn.setIcon(get_vector_icon(icon_type, "#a1a1aa", 18))
            btn.setIconSize(QSize(18, 18))
            btn.setFixedSize(34, 34)
            btn.setToolTip(f"{label} (Mở danh mục)")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 7px;
                }
                QPushButton:hover {
                    background: #27272a;
                    border-color: #3f3f46;
                }
            """)
            btn.clicked.connect(lambda checked=False, c=cat_id: self._on_cat_clicked(c))
            layout.addWidget(btn)
            self._cat_buttons[cat_id] = btn

        layout.addStretch(1)

        # 2.8. AutoCut 1-Click button
        self.btn_instant_autocut = QPushButton()
        self.btn_instant_autocut.setIcon(get_vector_icon("autocut", "#f59e0b", 18))
        self.btn_instant_autocut.setIconSize(QSize(18, 18))
        self.btn_instant_autocut.setFixedSize(34, 34)
        self.btn_instant_autocut.setToolTip("⚡ AutoCut 1-Click: Cắt khoảng lặng trên Timeline đang mở & co sát clip (0.3s)")
        self.btn_instant_autocut.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_instant_autocut.setCursor(Qt.PointingHandCursor)
        self.btn_instant_autocut.setStyleSheet("""
            QPushButton {
                background: rgba(245, 158, 11, 0.12);
                border: 1px solid rgba(245, 158, 11, 0.35);
                border-radius: 7px;
            }
            QPushButton:hover {
                background: rgba(245, 158, 11, 0.25);
                border-color: #f59e0b;
            }
        """)
        self.btn_instant_autocut.clicked.connect(self.instant_autocut_clicked.emit)
        layout.addWidget(self.btn_instant_autocut)

        # 3. Palette HUD button
        self.btn_palette = QPushButton()
        self.btn_palette.setIcon(get_vector_icon("palette", "#c4b5fd", 16))
        self.btn_palette.setIconSize(QSize(16, 16))
        self.btn_palette.setFixedSize(34, 34)
        self.btn_palette.setToolTip("Quick Palette (Ctrl+Alt+Space)")
        self.btn_palette.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_palette.setCursor(Qt.PointingHandCursor)
        self.btn_palette.setStyleSheet("""
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 7px;
            }
            QPushButton:hover {
                background: #27272a;
                border-color: #7c3aed;
            }
        """)
        self.btn_palette.clicked.connect(self.palette_requested.emit)
        layout.addWidget(self.btn_palette)

        # 4. Director button
        self.btn_director = QPushButton()
        self.btn_director.setIcon(get_vector_icon("ai", "#06b6d4", 16))
        self.btn_director.setIconSize(QSize(16, 16))
        self.btn_director.setFixedSize(34, 34)
        self.btn_director.setToolTip("Mở ChunDVC Director (AI AutoCut, Phụ đề, Prompt JSON)")
        self.btn_director.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_director.setCursor(Qt.PointingHandCursor)
        self.btn_director.setStyleSheet("""
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 7px;
            }
            QPushButton:hover {
                background: #27272a;
                border-color: #06b6d4;
            }
        """)
        self.btn_director.clicked.connect(self.director_requested.emit)
        layout.addWidget(self.btn_director)

        # 4.5. Settings button
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(get_vector_icon("settings", "#a1a1aa", 16))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setFixedSize(34, 34)
        self.btn_settings.setToolTip("Cài đặt thanh Dock (Đổi bên trái/phải, phím tắt...)")
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 7px;
            }
            QPushButton:hover {
                background: #27272a;
                border-color: #7c3aed;
            }
        """)
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.btn_settings)

        # 5. Status indicator dot
        self.lbl_status_dot = QLabel("●")
        self.lbl_status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status_dot.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lbl_status_dot.setStyleSheet("color: #10b981; font-size: 12px;")
        self.lbl_status_dot.setToolTip("Đã kết nối DaVinci Resolve")
        layout.addWidget(self.lbl_status_dot)

    def set_connected(self, connected: bool):
        if connected:
            self.lbl_status_dot.setStyleSheet("color: #10b981; font-size: 12px;")
            self.lbl_status_dot.setToolTip("Đã kết nối DaVinci Resolve")
        else:
            self.lbl_status_dot.setStyleSheet("color: #71717a; font-size: 12px;")
            self.lbl_status_dot.setToolTip("Chưa kết nối DaVinci Resolve")

    def _on_cat_clicked(self, category: str):
        self.category_selected.emit(category)
        self.open_dock_requested.emit()
