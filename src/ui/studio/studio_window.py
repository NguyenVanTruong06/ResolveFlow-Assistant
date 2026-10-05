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
        
        lbl_title = QLabel("🎨 Kho Hiệu Ứng (Studio Mode)")
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
        
        # Content (Splitter)
        splitter = QSplitter(Qt.Horizontal)
        
        # Sidebar (danh mục) -> Chuyển hướng Tab
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(f"background-color: {ThemeColors.BG_MAIN}; border-right: 1px solid {ThemeColors.BORDER_DEFAULT};")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 20, 10, 20)
        side_layout.setSpacing(5)
        
        self.stack = QStackedWidget()
        
        # Dùng lại giao diện bên trong TabAssets
        self.stack.addWidget(self.tab_assets)
        self.stack.addWidget(self.tab_sfx)
        
        # Nút điều hướng
        btn_assets = QPushButton("📦 Kho Hình / Chữ / Màu")
        btn_sfx = QPushButton("🔊 Kho Âm Thanh (SFX)")
        
        for idx, btn in enumerate([btn_assets, btn_sfx]):
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{ text-align: left; padding: 12px; border: none; color: {ThemeColors.TEXT_PRIMARY}; border-radius: 6px; font-weight: bold; }}
                QPushButton:checked {{ background-color: {ThemeColors.BG_INPUT}; color: {ThemeColors.PRIMARY}; border-left: 3px solid {ThemeColors.PRIMARY}; }}
            """)
            btn.clicked.connect(lambda _, i=idx: self._switch_tab(i))
            side_layout.addWidget(btn)
            
        side_layout.addStretch()
        btn_assets.setChecked(True)
        
        splitter.addWidget(sidebar)
        splitter.addWidget(self.stack)
        
        main_layout.addWidget(splitter)
        
    def _switch_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.findChildren(QPushButton)):
            if btn.text() in ["📦 Kho Hình / Chữ / Màu", "🔊 Kho Âm Thanh (SFX)"]:
                btn.setChecked(i == index)
                
    def _toggle_always_on_top(self):
        if self.btn_pin.isChecked():
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()
