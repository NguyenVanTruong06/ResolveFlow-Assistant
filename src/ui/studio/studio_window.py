import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QStackedWidget
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon

from src.ui.theme import ThemeColors, get_application_stylesheet
from src.ui.tabs.tab_assets import TabAssets
from src.ui.tabs.tab_sfx import TabSFX

class StudioWindow(QMainWindow):
    """
    Cửa sổ Hậu kỳ (Studio / CapCut Mode).
    Bao gồm:
    - Kho Tài nguyên (Chữ, Chuyển cảnh, Ảnh/Video, LUTs)
    - Kho Âm thanh (SFX)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ChunDVC Studio - Hậu Kỳ & Tài Nguyên")
        self.resize(1000, 700)
        self.setStyleSheet(get_application_stylesheet())
        
        # Bật cờ để nhớ vị trí/kích thước sau này
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        
        self.tab_assets = TabAssets(self)
        self.tab_sfx = TabSFX(self)
        
        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Header (Top bar)
        header = QWidget()
        header.setFixedHeight(50)
        header.setStyleSheet(f"background-color: {ThemeColors.BG_CARD_ACTIVE}; border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 0, 16, 0)
        
        lbl_title = QLabel("🎨 Kho Đạo Cụ (Studio Mode)")
        lbl_title.setStyleSheet(f"color: {ThemeColors.PRIMARY}; font-size: 16px; font-weight: bold;")
        h_layout.addWidget(lbl_title)
        
        self.btn_pin = QPushButton("📌 Ghim trên cùng")
        self.btn_pin.setCheckable(True)
        self.btn_pin.clicked.connect(self._toggle_always_on_top)
        self.btn_pin.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {ThemeColors.TEXT_MUTED}; border: 1px solid {ThemeColors.BORDER_DEFAULT}; padding: 4px 12px; border-radius: 4px; }}
            QPushButton:checked {{ color: {ThemeColors.PRIMARY}; border: 1px solid {ThemeColors.PRIMARY}; }}
        """)
        h_layout.addStretch()
        h_layout.addWidget(self.btn_pin)
        
        main_layout.addWidget(header)
        
        # 4-Column Studio Layout (TabAssets)
        main_layout.addWidget(self.tab_assets, stretch=1)
        
    def _toggle_always_on_top(self):
        if self.btn_pin.isChecked():
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()
