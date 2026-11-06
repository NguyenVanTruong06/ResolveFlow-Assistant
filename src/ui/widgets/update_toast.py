"""
ChunDVC / ResolveFlow Assistant - Floating Update Toast Notification ("như Orca").
Hiển thị thông báo cập nhật nổi mượt mà ở góc dưới bên phải màn hình khi có bản mới.
Hỗ trợ Cập nhật 1-Click tự động tải và cài đặt trong nền, không cần cài lại thủ công.
"""

import sys
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QSettings
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QGraphicsDropShadowEffect, QGraphicsOpacityEffect
)

from src.ui.theme import ThemeColors
from src.services.update_service import UpdateDownloadAndInstallWorker, restart_app


class UpdateToastWidget(QWidget):
    """
    Cửa sổ Toast thông báo cập nhật không viền, nổi trên cùng góc dưới phải màn hình.
    """
    def __init__(self, update_info: dict, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.latest_version = update_info.get("latest_version", "")
        self._worker: Optional[UpdateDownloadAndInstallWorker] = None
        self._countdown = 5
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        self._init_window()
        self._init_ui()

    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(380)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)

        # Card container chính
        self.card = QFrame()
        self.card.setObjectName("toast_card")
        self.card.setStyleSheet(f"""
            QFrame#toast_card {{
                background-color: #16171a;
                border: 1.5px solid {ThemeColors.PRIMARY};
                border-radius: 12px;
            }}
        """)

        # Hiệu ứng bóng đổ sang trọng
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 8)
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)

        # 1. Header: Icon + Tiêu đề + Huy hiệu Version + Nút Đóng
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(8)

        lbl_icon = QLabel("⚡")
        lbl_icon.setStyleSheet("font-size: 16px; color: #ff4757;")
        hdr_layout.addWidget(lbl_icon)

        lbl_title = QLabel("ChunDVC Update")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #ffffff; letter-spacing: 0.3px;")
        hdr_layout.addWidget(lbl_title)

        lbl_badge = QLabel(f"v{self.latest_version}")
        lbl_badge.setStyleSheet("""
            background: #ff4757;
            color: #ffffff;
            font-size: 10.5px;
            font-weight: 700;
            padding: 1px 7px;
            border-radius: 4px;
        """)
        hdr_layout.addWidget(lbl_badge)
        hdr_layout.addStretch(1)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #71717a;
                border: none;
                font-size: 13px;
                font-weight: bold;
                border-radius: 11px;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.2);
                color: #ef4444;
            }
        """)
        btn_close.clicked.connect(self.close_animated)
        hdr_layout.addWidget(btn_close)
        card_layout.addLayout(hdr_layout)

        # 2. Body Text
        body_layout = QVBoxLayout()
        body_layout.setSpacing(3)

        self.lbl_headline = QLabel(f"Đã có phiên bản mới: <b>v{self.latest_version}</b>")
        self.lbl_headline.setStyleSheet("color: #ededf0; font-size: 12.5px;")
        body_layout.addWidget(self.lbl_headline)

        self.lbl_desc = QLabel("Nâng cấp 1-click tự động mượt mà không cần cài lại.")
        self.lbl_desc.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        self.lbl_desc.setWordWrap(True)
        body_layout.addWidget(self.lbl_desc)

        card_layout.addLayout(body_layout)

        # 3. Tiến độ tải & trạng thái cài đặt (ẩn ban đầu)
        self.prog_container = QWidget()
        prog_layout = QVBoxLayout(self.prog_container)
        prog_layout.setContentsMargins(0, 4, 0, 0)
        prog_layout.setSpacing(6)

        self.lbl_status = QLabel("Đang chuẩn bị tải...")
        self.lbl_status.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: 500;")
        prog_layout.addWidget(self.lbl_status)

        self.prog_bar = QProgressBar()
        self.prog_bar.setFixedHeight(6)
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setStyleSheet("""
            QProgressBar {
                background: #27272a;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff4757, stop:1 #f59e0b);
                border-radius: 3px;
            }
        """)
        prog_layout.addWidget(self.prog_bar)
        self.prog_container.hide()
        card_layout.addWidget(self.prog_container)

        # 4. Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        self.btn_later = QPushButton("Để sau")
        self.btn_later.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_later.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {ThemeColors.TEXT_MUTED};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
        """)
        self.btn_later.clicked.connect(self._on_later_clicked)
        btn_row.addWidget(self.btn_later)

        self.btn_update = QPushButton("🚀 Cập Nhật Ngay")
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ThemeColors.PRIMARY}, stop:1 #e0283b);
                color: #ffffff;
                border: 1px solid #ff6b81;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {ThemeColors.PRIMARY_HOVER};
            }}
            QPushButton:disabled {{
                background: {ThemeColors.BG_INPUT};
                color: {ThemeColors.TEXT_MUTED};
                border-color: {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        self.btn_update.clicked.connect(self._on_update_clicked)
        btn_row.addWidget(self.btn_update)

        card_layout.addLayout(btn_row)
        root_layout.addWidget(self.card)

    def show_animated(self):
        """Tính toán vị trí góc dưới bên phải màn hình chính và trượt vào mượt mà."""
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if not screen:
            self.show()
            return

        geo = screen.availableGeometry()
        margin = 24
        target_x = geo.right() - self.width() - margin
        target_y = geo.bottom() - self.height() - margin

        # Vị trí xuất phát thấp hơn 40px để tạo hiệu ứng trượt lên
        start_pos = QPoint(target_x, target_y + 40)
        target_pos = QPoint(target_x, target_y)

        self.move(start_pos)
        self.show()

        # Hiệu ứng trượt
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(350)
        self.anim.setStartValue(start_pos)
        self.anim.setEndValue(target_pos)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.start()

    def close_animated(self):
        """Trượt xuống và đóng."""
        cur_pos = self.pos()
        target_pos = QPoint(cur_pos.x(), cur_pos.y() + 40)

        self.anim_close = QPropertyAnimation(self, b"pos")
        self.anim_close.setDuration(250)
        self.anim_close.setStartValue(cur_pos)
        self.anim_close.setEndValue(target_pos)
        self.anim_close.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim_close.finished.connect(self.close)
        self.anim_close.start()

    def _on_later_clicked(self):
        settings = QSettings("ChunDVC", "ResolveFlow")
        settings.setValue("updates/ignored_version", self.latest_version)
        self.close_animated()

    def _on_update_clicked(self):
        if getattr(self, "_ready_to_restart", False):
            restart_app()
            return

        # Bắt đầu luồng cập nhật 1-Click
        self.btn_later.hide()
        self.btn_update.setEnabled(False)
        self.btn_update.setText("⏳ Đang cập nhật...")
        self.prog_container.show()
        self.lbl_status.setText("Đang chuẩn bị tải gói cập nhật...")
        self.adjustSize()

        self._worker = UpdateDownloadAndInstallWorker(self.update_info, self)
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.install_finished.connect(self._on_install_finished)
        self._worker.start()

    def _on_progress_changed(self, percent: float, msg: str):
        self.prog_bar.setValue(int(percent))
        self.lbl_status.setText(msg)

    def _on_status_changed(self, msg: str):
        self.lbl_status.setText(msg)

    def _on_install_finished(self, success: bool, msg: str):
        if success:
            self._ready_to_restart = True
            self.prog_bar.setValue(100)
            self.prog_bar.setStyleSheet("""
                QProgressBar {
                    background: #27272a;
                    border: none;
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background: #10b981;
                    border-radius: 3px;
                }
            """)
            self.lbl_status.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 600;")
            self.lbl_status.setText("✔ Cập nhật thành công! Sẵn sàng khởi động lại.")
            self.btn_update.setText(f"🔄 Khởi Động Lại Ngay ({self._countdown}s)")
            self.btn_update.setEnabled(True)
            self.btn_update.setStyleSheet("""
                QPushButton {
                    background: #10b981;
                    color: #ffffff;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 12px;
                    font-weight: 700;
                }
                QPushButton:hover {
                    background: #059669;
                }
            """)
            self._countdown_timer.start()
        else:
            self.lbl_status.setStyleSheet("color: #f87171; font-size: 11px; font-weight: 500;")
            self.lbl_status.setText(f"✕ {msg}")
            self.btn_update.setText("Thử Lại")
            self.btn_update.setEnabled(True)
            self.btn_later.show()

    def _on_countdown_tick(self):
        self._countdown -= 1
        if self._countdown > 0:
            self.btn_update.setText(f"🔄 Khởi Động Lại Ngay ({self._countdown}s)")
        else:
            self._countdown_timer.stop()
            restart_app()
