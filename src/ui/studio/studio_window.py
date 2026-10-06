"""
Cửa sổ Kho Đạo Cụ (ResolveFlow Studio Window).
Thiết kế theo chuẩn 4 cột từ src/ui/resolveflow_ui.html (#v-studio):
- Thanh tiêu đề phong cách chuyên nghiệp: Logo vector, Tên Kho Đạo Cụ, Thống kê tài nguyên, Playhead DaVinci, Nút Ghim trên cùng.
- Nhúng toàn bộ 4 cột của TabAssets:
  + Cột 1: Rail danh mục (Chữ, Màu, Chuyển cảnh, Sticker, Lớp phủ, Meme, SFX, Mẫu của tôi)
  + Cột 2: Phân loại nhóm con & nút nạp file cá nhân
  + Cột 3: Lưới thẻ mẫu trực quan (Text, LUT, Transition, Sticker, SFX...)
  + Cột 4: Inspector tinh chỉnh & nút chèn/kéo thả DaVinci Resolve
"""

import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QColor, QPen, QBrush, QPainterPath

from src.ui.theme import ThemeColors, ThemeFonts, get_application_stylesheet
from src.ui.tabs.tab_assets import TabAssets


class StudioWindow(QMainWindow):
    """
    Cửa sổ Hậu kỳ & Kho Đạo Cụ (Studio Mode).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ResolveFlow · Kho Đạo Cụ")
        self.resize(1140, 740)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(get_application_stylesheet())

        # Bật cờ để nhớ vị trí/kích thước sau này
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self.tab_assets = TabAssets(self)
        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # =====================================================================
        # HEADER (Titlebar) theo chuẩn resolveflow_ui.html
        # =====================================================================
        header = QFrame()
        header.setFixedHeight(46)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 0, 14, 0)
        h_layout.setSpacing(12)

        # 1. Logo Vector + Tiêu đề
        lbl_logo = QLabel()
        lbl_logo.setFixedSize(26, 26)
        lbl_logo.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:0.5 #7c3aed, stop:1 #0e7490);
            border-radius: 7px;
        """)
        # Vẽ biểu tượng camera play bên trong logo
        lbl_logo.setAlignment(Qt.AlignCenter)
        lbl_logo.setText("🎬")
        h_layout.addWidget(lbl_logo)

        lbl_title = QLabel("<b>Kho Đạo Cụ</b>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 14px;")
        h_layout.addWidget(lbl_title)

        h_layout.addStretch(1)

        # 2. Thống kê phần cứng (.stats)
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"""
            color: {ThemeColors.TEXT_MUTED};
            font-size: 11.5px;
        """)
        s_layout = QHBoxLayout(stats_frame)
        s_layout.setContentsMargins(0, 0, 0, 0)
        s_layout.setSpacing(12)

        lbl_cpu = QLabel("CPU <b style='color:#ededf0'>21%</b>")
        lbl_gpu = QLabel("GPU <b style='color:#ededf0'>8%</b>")
        lbl_ram = QLabel("RAM <b style='color:#ededf0'>3.4 GB</b>")
        s_layout.addWidget(lbl_cpu)
        s_layout.addWidget(lbl_gpu)
        s_layout.addWidget(lbl_ram)
        h_layout.addWidget(stats_frame)

        # 3. Trạng thái Playhead DaVinci Resolve (.pill)
        self.pill_playhead = QLabel("● Playhead V1 · 14:49")
        self.pill_playhead.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            color: {ThemeColors.GREEN};
            font-size: 11.5px;
            font-weight: 500;
            padding: 3px 10px;
        """)
        h_layout.addWidget(self.pill_playhead)

        # 4. Nút Ghim Luôn trên cùng (#pinBtn)
        self.btn_pin = QPushButton("📌 Luôn trên cùng")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.clicked.connect(self._toggle_always_on_top)
        self.btn_pin.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                padding: 4px 12px;
                border-radius: 6px;
                font-size: 11.5px;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QPushButton:checked {{
                color: #c4b5fd;
                border-color: {ThemeColors.PRIMARY};
                background-color: {ThemeColors.VIOLET_LO};
                font-weight: bold;
            }}
        """)
        h_layout.addWidget(self.btn_pin)

        main_layout.addWidget(header)

        # =====================================================================
        # GIAO DIỆN 4 CỘT KHO ĐẠO CỤ (TabAssets)
        # =====================================================================
        main_layout.addWidget(self.tab_assets, stretch=1)

    def _toggle_always_on_top(self):
        is_on_top = self.btn_pin.isChecked()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, is_on_top)
        self.show()
