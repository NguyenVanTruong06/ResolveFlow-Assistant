import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu,
    QGraphicsDropShadowEffect, QApplication, QPushButton
)
from PySide6.QtCore import Qt, QPoint, Signal as pyqtSignal, QSize
from PySide6.QtGui import QColor, QPainter, QBrush, QPen
from src.ui.theme import ThemeColors

class FloatingBubbleWidget(QWidget):
    """
    TRUNG TÂM ĐIỀU HƯỚNG CỦA ỨNG DỤNG.
    Chỉ hiển thị 2 nút mở 2 cửa sổ độc lập. Không chứa UI con.
    """
    open_auto_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        
        self.drag_position = QPoint()
        self.progress_pct = 0
        self.status_text = "Sẵn sàng"
        self.is_processing = False
        
        self.setFixedSize(QSize(320, 64))
        self._init_ui()

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(10, 8, 12, 10)
        self.main_layout.setSpacing(10)

        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)

        self.lbl_icon = QLabel("🎬")
        self.lbl_icon.setAlignment(Qt.AlignCenter)
        self.lbl_icon.setFixedSize(38, 38)
        self.lbl_icon.setStyleSheet(f"""
            background: {ThemeColors.PRIMARY};
            color: {ThemeColors.PRIMARY_TEXT};
            font-size: 18px;
            border-radius: 19px;
            border: 2px solid {ThemeColors.BORDER_ACTIVE};
        """)
        header_layout.addWidget(self.lbl_icon)

        # Trạng thái tiến trình (Chỉ hiện khi luồng AUTO đang xử lý AI)
        self.info_layout = QWidget()
        i_layout = QVBoxLayout(self.info_layout)
        i_layout.setSpacing(2)
        i_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_title = QLabel("<b>Đang chạy AI Director...</b>")
        self.lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 11px;")
        self.lbl_status = QLabel("Processing...")
        self.lbl_status.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 10px;")
        self.lbl_progress = QLabel("0%")
        self.lbl_progress.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 10px; font-weight: bold;")
        h_status = QHBoxLayout()
        h_status.setSpacing(4)
        h_status.addWidget(self.lbl_status, stretch=1)
        h_status.addWidget(self.lbl_progress)
        i_layout.addWidget(self.lbl_title)
        i_layout.addLayout(h_status)
        self.info_layout.setVisible(False)
        header_layout.addWidget(self.info_layout, stretch=1)

        # Cụm 2 Nút (Launcher Mode)
        self.launcher_layout = QWidget()
        l_layout = QHBoxLayout(self.launcher_layout)
        l_layout.setContentsMargins(0,0,0,0)
        l_layout.setSpacing(6)
        
        self.btn_ai_mode = QPushButton("🤖 AI Director")
        self.btn_ai_mode.setCursor(Qt.PointingHandCursor)
        self.btn_ai_mode.setStyleSheet(f"""
            QPushButton {{ background-color: {ThemeColors.BG_INPUT}; border: 1px solid {ThemeColors.BORDER_DEFAULT}; border-radius: 6px; color: {ThemeColors.TEXT_PRIMARY}; padding: 8px; font-weight: bold; font-size: 11px; }}
            QPushButton:hover {{ background-color: {ThemeColors.BORDER_DEFAULT}; border: 1px solid {ThemeColors.PRIMARY}; }}
        """)
        self.btn_ai_mode.clicked.connect(self.open_auto_requested.emit)
        
        self.btn_capcut_mode = QPushButton("🎨 Kho Hiệu ứng")
        self.btn_capcut_mode.setCursor(Qt.PointingHandCursor)
        self.btn_capcut_mode.setStyleSheet(f"""
            QPushButton {{ background-color: {ThemeColors.BG_INPUT}; border: 1px solid {ThemeColors.BORDER_DEFAULT}; border-radius: 6px; color: {ThemeColors.TEXT_PRIMARY}; padding: 8px; font-weight: bold; font-size: 11px; }}
            QPushButton:hover {{ background-color: {ThemeColors.BORDER_DEFAULT}; border: 1px solid {ThemeColors.PRIMARY}; }}
        """)
        self.btn_capcut_mode.clicked.connect(self.open_studio_requested.emit)
        
        l_layout.addWidget(self.btn_ai_mode, stretch=1)
        l_layout.addWidget(self.btn_capcut_mode, stretch=1)
        header_layout.addWidget(self.launcher_layout, stretch=1)

        self.main_layout.addWidget(header_widget)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        brush = QBrush(QColor(2, 20, 22, 245))
        border_color = QColor(ThemeColors.BORDER_ACTIVE) if self.is_processing else QColor(ThemeColors.BORDER_DEFAULT)
        pen = QPen(border_color, 1.5)

        painter.setBrush(brush)
        painter.setPen(pen)
        
        radius = 32
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), radius, radius)

        if self.is_processing and self.progress_pct > 0:
            prog_pen = QPen(QColor(ThemeColors.PRIMARY), 3)
            painter.setPen(prog_pen)
            w = self.width() - 40
            y = self.height() - 6
            prog_w = int((w * self.progress_pct) / 100)
            painter.drawLine(20, y, 20 + prog_w, y)

    def update_progress(self, pct: int, status_text: str = None):
        self.progress_pct = max(0, min(100, pct))
        self.lbl_progress.setText(f"{self.progress_pct}%")
        if status_text:
            clean_text = status_text.strip().replace("\n", " ")
            if len(clean_text) > 18:
                clean_text = clean_text[:16] + ".."
            self.lbl_status.setText(clean_text)
        
        self.is_processing = (self.progress_pct > 0 and self.progress_pct < 100)
        
        if self.is_processing:
            self.launcher_layout.setVisible(False)
            self.info_layout.setVisible(True)
        else:
            self.launcher_layout.setVisible(True)
            self.info_layout.setVisible(False)
            
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and not self.drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color: {ThemeColors.BG_MAIN}; color: {ThemeColors.TEXT_PRIMARY}; border: 1px solid {ThemeColors.BORDER_DEFAULT}; border-radius: 6px; padding: 4px; }}
            QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {ThemeColors.BG_INPUT}; color: {ThemeColors.TEXT_ACCENT}; }}
        """)
        action_quit = menu.addAction("❌ Thoát ChunDVC")
        action_quit.triggered.connect(QApplication.instance().quit)
        menu.exec(event.globalPos())
