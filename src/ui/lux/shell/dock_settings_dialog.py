"""
src/ui/lux/shell/dock_settings_dialog.py
Hộp thoại Cài Đặt Thanh Dock (Dock Settings Dialog):
Cho phép người dùng tùy biến:
- Vị trí thanh Dock: Bên phải (mặc định) hoặc Bên trái màn hình.
- Tùy chọn tự động mở ChunDVC Director (Trạm AI) khi khởi chạy.
- Tùy chọn ghim thanh Dock mặc định.
- Xem danh sách phím tắt toàn cục.
Tự động lưu vào QSettings để duy trì trạng thái khi khởi động lại.
"""

from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QButtonGroup, QCheckBox,
    QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QSettings, QSize
from src.ui.icons import get_vector_icon
from src.ui.theme import ThemeColors


class DockSettingsDialog(QDialog):
    """
    Cửa sổ Cài Đặt Giao Diện & Vị Trí Thanh Dock.
    """
    dock_edge_changed = pyqtSignal(str)          # 'left' | 'right'
    auto_director_changed = pyqtSignal(bool)
    pin_default_changed = pyqtSignal(bool)

    def __init__(self, current_edge: str = "right", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Cài Đặt Thanh Dock")
        self.setFixedSize(480, 520)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.CustomizeWindowHint |
            Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self._current_edge = current_edge if current_edge in ("left", "right") else "right"
        self._settings = QSettings("ChunDVC", "ResolveFlow")

        self._init_ui()
        self._load_preferences()

    def _init_ui(self):
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {ThemeColors.BG_CANVAS};
                color: {ThemeColors.TEXT_PRIMARY};
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }}
            QLabel {{
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QFrame#card_frame {{
                background-color: {ThemeColors.BG_MAIN};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
            }}
            QRadioButton {{
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 13px;
                spacing: 8px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_HOVER};
                background: {ThemeColors.BG_INPUT};
            }}
            QRadioButton::indicator:checked {{
                border: 2px solid {ThemeColors.PRIMARY};
                background: {ThemeColors.PRIMARY};
            }}
            QCheckBox {{
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid {ThemeColors.BORDER_HOVER};
                background: {ThemeColors.BG_INPUT};
            }}
            QCheckBox::indicator:checked {{
                border: 1px solid {ThemeColors.PRIMARY};
                background: {ThemeColors.PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 1. Header Bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)

        lbl_icon = QLabel()
        lbl_icon.setPixmap(get_vector_icon("settings", ThemeColors.PRIMARY_HOVER, 24).pixmap(24, 24))
        header_layout.addWidget(lbl_icon)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        lbl_title = QLabel("Cài Đặt Thanh Công Cụ (Dock Settings)")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        title_vbox.addWidget(lbl_title)

        lbl_subtitle = QLabel("Tùy biến vị trí dán mép màn hình và hành vi hiển thị")
        lbl_subtitle.setStyleSheet(f"font-size: 11.5px; color: {ThemeColors.TEXT_MUTED};")
        title_vbox.addWidget(lbl_subtitle)
        header_layout.addLayout(title_vbox)
        header_layout.addStretch(1)

        layout.addLayout(header_layout)

        # 2. Section 1: Vị trí hiển thị trên màn hình
        sec1_frame = QFrame()
        sec1_frame.setObjectName("card_frame")
        sec1_layout = QVBoxLayout(sec1_frame)
        sec1_layout.setContentsMargins(14, 12, 14, 14)
        sec1_layout.setSpacing(10)

        lbl_sec1 = QLabel("📍 VỊ TRÍ THANH DOCK (SCREEN EDGE)")
        lbl_sec1.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec1_layout.addWidget(lbl_sec1)

        lbl_sec1_desc = QLabel("Chọn cạnh màn hình mà thanh công cụ sẽ neo và mở rộng ra:")
        lbl_sec1_desc.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_SECONDARY};")
        sec1_layout.addWidget(lbl_sec1_desc)

        edge_btn_layout = QHBoxLayout()
        edge_btn_layout.setSpacing(20)

        self.btn_group_edge = QButtonGroup(self)
        self.rad_right = QRadioButton("Bên phải (Right - Mặc định)")
        self.rad_right.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rad_left = QRadioButton("Bên trái (Left)")
        self.rad_left.setCursor(Qt.CursorShape.PointingHandCursor)

        self.btn_group_edge.addButton(self.rad_right, 1)
        self.btn_group_edge.addButton(self.rad_left, 2)

        if self._current_edge == "left":
            self.rad_left.setChecked(True)
        else:
            self.rad_right.setChecked(True)

        self.rad_right.toggled.connect(self._on_edge_radio_toggled)
        self.rad_left.toggled.connect(self._on_edge_radio_toggled)

        edge_btn_layout.addWidget(self.rad_right)
        edge_btn_layout.addWidget(self.rad_left)
        edge_btn_layout.addStretch(1)
        sec1_layout.addLayout(edge_btn_layout)

        layout.addWidget(sec1_frame)

        # 3. Section 2: Khởi động & Hành vi
        sec2_frame = QFrame()
        sec2_frame.setObjectName("card_frame")
        sec2_layout = QVBoxLayout(sec2_frame)
        sec2_layout.setContentsMargins(14, 12, 14, 14)
        sec2_layout.setSpacing(10)

        lbl_sec2 = QLabel("⚙️ HÀNH VI & KHỞI CHẠY (BEHAVIOR)")
        lbl_sec2.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec2_layout.addWidget(lbl_sec2)

        self.chk_auto_director = QCheckBox("Tự động mở ChunDVC Director (Trạm AI) khi khởi động")
        self.chk_auto_director.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_auto_director.toggled.connect(self._on_auto_director_toggled)
        sec2_layout.addWidget(self.chk_auto_director)

        self.chk_pin_default = QCheckBox("Ghim thanh Dock mặc định (Không tự thu gọn khi mất tiêu điểm)")
        self.chk_pin_default.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_pin_default.toggled.connect(self._on_pin_default_toggled)
        sec2_layout.addWidget(self.chk_pin_default)

        layout.addWidget(sec2_frame)

        # 4. Section 3: Phím tắt nhanh
        sec3_frame = QFrame()
        sec3_frame.setObjectName("card_frame")
        sec3_layout = QVBoxLayout(sec3_frame)
        sec3_layout.setContentsMargins(14, 12, 14, 14)
        sec3_layout.setSpacing(8)

        lbl_sec3 = QLabel("⌨️ PHÍM TẮT NHANH (GLOBAL HOTKEYS)")
        lbl_sec3.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {ThemeColors.TEXT_ACCENT}; letter-spacing: 0.5px;")
        sec3_layout.addWidget(lbl_sec3)

        grid = QGridLayout()
        grid.setSpacing(6)

        hotkeys = [
            ("Ctrl + Alt + Space", "Mở nhanh Command Palette HUD"),
            ("Ctrl + Alt + K", "Mở Palette ở chế độ gõ lệnh"),
            ("Esc", "Thu gọn thanh Dock về mép màn hình"),
            ("Tab", "Mở rộng Dock / Hiệu chỉnh thông số"),
        ]
        for row, (key, desc) in enumerate(hotkeys):
            lbl_k = QLabel(key)
            lbl_k.setStyleSheet("""
                background: #27272a;
                color: #c4b5fd;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-family: monospace;
                font-size: 11px;
                font-weight: bold;
            """)
            lbl_d = QLabel(desc)
            lbl_d.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11.5px;")
            grid.addWidget(lbl_k, row, 0)
            grid.addWidget(lbl_d, row, 1)

        sec3_layout.addLayout(grid)
        layout.addWidget(sec3_frame)

        layout.addStretch(1)

        # 5. Bottom Buttons (Reset Defaults, Close)
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        self.btn_reset = QPushButton("Khôi phục mặc định")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
        """)
        self.btn_reset.clicked.connect(self._on_reset_defaults)
        btn_box.addWidget(self.btn_reset)

        btn_box.addStretch(1)

        self.btn_close = QPushButton("Hoàn tất")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.PRIMARY};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
                font-size: 12.5px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)

        layout.addLayout(btn_box)

    def _load_preferences(self):
        edge = self._settings.value("dock_edge", self._current_edge)
        if edge == "left":
            self.rad_left.setChecked(True)
        else:
            self.rad_right.setChecked(True)

        auto_dir = self._settings.value("auto_open_director", True, type=bool)
        self.chk_auto_director.setChecked(auto_dir)

        pin_def = self._settings.value("pinned_by_default", False, type=bool)
        self.chk_pin_default.setChecked(pin_def)

    def _on_edge_radio_toggled(self):
        new_edge = "left" if self.rad_left.isChecked() else "right"
        if new_edge != self._current_edge:
            self._current_edge = new_edge
            self._settings.setValue("dock_edge", new_edge)
            self.dock_edge_changed.emit(new_edge)

    def _on_auto_director_toggled(self, checked: bool):
        self._settings.setValue("auto_open_director", checked)
        self.auto_director_changed.emit(checked)

    def _on_pin_default_toggled(self, checked: bool):
        self._settings.setValue("pinned_by_default", checked)
        self.pin_default_changed.emit(checked)

    def _on_reset_defaults(self):
        self.rad_right.setChecked(True)
        self.chk_auto_director.setChecked(True)
        self.chk_pin_default.setChecked(False)
