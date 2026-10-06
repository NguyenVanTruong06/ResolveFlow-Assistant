"""
Bong bóng nổi Desktop (Floating Desktop Bubble) cho ResolveFlow Assistant.
Thiết kế theo chuẩn hiện đại từ src/ui/resolveflow_ui.html:
- Quả cầu tròn 56px với gradient Tím - Cyan điện ảnh và bóng đổ mượt mà.
- Biểu tượng logo ResolveFlow vector sắc nét (Khung màn hình bo góc + Tam giác Play) thay cho emoji thô.
- Vòng tròn tiến độ Conic Gradient (QConicalGradient) và huy hiệu % phát sáng bên dưới.
- Khay điều hướng (Tray Popup) mở rộng thông minh với độ trễ 120ms mở / 400ms đóng chống giật.
- 3 trạng thái Status Header:
  + Chờ (Idle): ● Sẵn sàng | DaVinci đang mở / DaVinci chưa mở
  + Đang chạy (Run): ● Tên bước đang chạy | % tiến độ | Thanh mini progress bar cyan
  + Hoàn tất (Done): ● Đã xuất timeline | Nút chính nổi bật '🎞️ Nạp vào DaVinci'
- Tự động nhận diện mép màn hình (Ghim trái / Ghim phải) và tự dính mép (Edge Snapping).
- 2 Nút hành động lớn với biểu tượng vector sắc nét:
  + 🤖 AI Director (Dựng tự động 1-click)
  + 🎨 Kho Đạo Cụ (Chữ, màu, hiệu ứng, sticker, âm thanh)
"""

import os
import time
import subprocess
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu,
    QGraphicsDropShadowEffect, QApplication, QPushButton, QProgressBar, QFrame
)
from PySide6.QtCore import Qt, QPoint, Signal as pyqtSignal, QSize, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QLinearGradient, QConicalGradient,
    QFont, QCursor, QPainterPath, QPixmap
)
from src.ui.theme import ThemeColors, ThemeFonts


def create_vector_icon(icon_type: str, color_hex: str, size: int = 20) -> QPixmap:
    """Tạo biểu tượng vector sắc nét không phụ thuộc vào font emoji của hệ điều hành."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    scale = size / 24.0
    painter.scale(scale, scale)

    pen = QPen(QColor(color_hex), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if icon_type == "ai":
        # Biểu tượng AI Director (#i-ai trong thiết kế UI)
        painter.drawRoundedRect(QRectF(4, 7, 16, 12), 3, 3)
        painter.drawLine(QPointF(12, 7), QPointF(12, 4))
        painter.drawLine(QPointF(9, 12), QPointF(9, 13))
        painter.drawLine(QPointF(15, 12), QPointF(15, 13))
        painter.drawLine(QPointF(9.5, 16), QPointF(14.5, 16))
    elif icon_type == "grid":
        # Biểu tượng Kho Đạo Cụ 2x2 grid (#i-grid trong thiết kế UI)
        painter.drawRoundedRect(QRectF(4, 4, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(13, 4, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(4, 13, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(13, 13, 7, 7), 1.5, 1.5)
    elif icon_type == "resolve":
        # Biểu tượng DaVinci Resolve pinwheel (#i-resolve trong thiết kế UI)
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawEllipse(QRectF(8.8, 8.8, 6.4, 6.4))
        painter.drawLine(QPointF(12, 3), QPointF(12, 8.8))
        painter.drawLine(QPointF(19.8, 16.5), QPointF(14.8, 13.6))
        painter.drawLine(QPointF(4.2, 16.5), QPointF(9.2, 13.6))

    painter.end()
    return pix


class BubbleTrayPopup(QWidget):
    """
    Khay điều hướng nổi (Tray Popup) xuất hiện mượt mà khi rê chuột vào Bong bóng.
    Tương ứng với .b-tray trong resolveflow_ui.html.
    """
    open_auto_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    insert_timeline_requested = pyqtSignal()

    def __init__(self, bubble):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.bubble = bubble

        self.setFixedWidth(276)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("tray_container")
        self.container.setStyleSheet(f"""
            QFrame#tray_container {{
                background-color: rgba(26, 26, 30, 0.97);
                border: 1px solid {ThemeColors.BORDER_HOVER};
                border-radius: 14px;
            }}
        """)

        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(8, 8, 8, 8)
        c_layout.setSpacing(4)

        # =========================================================================
        # 1. Khối trạng thái (Status Header: .t-status)
        # =========================================================================
        self.status_box = QWidget()
        s_box_layout = QVBoxLayout(self.status_box)
        s_box_layout.setContentsMargins(2, 2, 2, 4)
        s_box_layout.setSpacing(6)

        # Hàng trạng thái chính (.r)
        h_stat = QHBoxLayout()
        h_stat.setContentsMargins(0, 0, 0, 0)
        h_stat.setSpacing(6)

        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN}; font-size: 11px;")

        self.lbl_tray_status = QLabel("<b>Sẵn sàng</b>")
        self.lbl_tray_status.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 12px;")

        self.lbl_tray_right = QLabel("DaVinci đang mở")
        self.lbl_tray_right.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")

        # Tương thích ngược với mã kiểm thử
        self.lbl_tray_pct = self.lbl_tray_right

        h_stat.addWidget(self.lbl_dot)
        h_stat.addWidget(self.lbl_tray_status, stretch=1)
        h_stat.addWidget(self.lbl_tray_right)
        s_box_layout.addLayout(h_stat)

        # Thanh tiến độ mini (.bar) khi đang chạy
        self.mini_bar = QProgressBar()
        self.mini_bar.setFixedHeight(4)
        self.mini_bar.setTextVisible(False)
        self.mini_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {ThemeColors.CYAN_HI};
                border-radius: 2px;
            }}
        """)
        self.mini_bar.setVisible(False)
        s_box_layout.addWidget(self.mini_bar)

        # Nút hành động nổi bật khi hoàn tất: "🎞️ Nạp vào DaVinci"
        self.btn_insert_timeline = QPushButton("🎞️ Nạp vào DaVinci")
        self.btn_insert_timeline.setCursor(Qt.PointingHandCursor)
        self.btn_insert_timeline.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:1 #7c3aed);
                color: #ffffff;
                font-size: 12px;
                font-weight: 600;
                border-radius: 8px;
                padding: 6px 12px;
                border: none;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #8b5cf6);
            }}
            QPushButton:pressed {{
                background-color: #6d28d9;
            }}
        """)
        self.btn_insert_timeline.clicked.connect(self.insert_timeline_requested.emit)
        self.btn_insert_timeline.setVisible(False)
        s_box_layout.addWidget(self.btn_insert_timeline)

        c_layout.addWidget(self.status_box)

        # Đường ngăn cách mỏng (.line)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {ThemeColors.BORDER_DEFAULT};")
        c_layout.addWidget(sep)

        # =========================================================================
        # 2. Cụm 2 nút hành động lớn (.t-btn ai & .t-btn kit)
        # =========================================================================
        # Nút 1: AI Director
        self.btn_ai = QPushButton()
        self.btn_ai.setCursor(Qt.PointingHandCursor)
        self.btn_ai.setFixedHeight(54)
        self.btn_ai.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 0px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: rgba(39, 39, 42, 0.75);
                border-color: {ThemeColors.BORDER_HOVER};
            }}
            QPushButton:pressed {{
                background-color: rgba(39, 39, 42, 0.95);
            }}
        """)
        ai_layout = QHBoxLayout(self.btn_ai)
        ai_layout.setContentsMargins(8, 6, 8, 6)
        ai_layout.setSpacing(10)

        lbl_ai_icon = QLabel()
        lbl_ai_icon.setFixedSize(34, 34)
        lbl_ai_icon.setAlignment(Qt.AlignCenter)
        lbl_ai_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_ai_icon.setStyleSheet(f"background: {ThemeColors.VIOLET_LO}; border-radius: 9px;")
        lbl_ai_icon.setPixmap(create_vector_icon("ai", "#c4b5fd", 20))

        ai_text_box = QVBoxLayout()
        ai_text_box.setContentsMargins(0, 0, 0, 0)
        ai_text_box.setSpacing(1)
        lbl_ai_t = QLabel("<b>AI Director</b>")
        lbl_ai_t.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_ai_t.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 13px; line-height: 1.2;")
        lbl_ai_sub = QLabel("Dựng tự động 1-click")
        lbl_ai_sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_ai_sub.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        ai_text_box.addWidget(lbl_ai_t)
        ai_text_box.addWidget(lbl_ai_sub)

        ai_layout.addWidget(lbl_ai_icon)
        ai_layout.addLayout(ai_text_box, stretch=1)
        self.btn_ai.clicked.connect(self.open_auto_requested.emit)
        c_layout.addWidget(self.btn_ai)

        # Nút 2: Kho Đạo Cụ
        self.btn_studio = QPushButton()
        self.btn_studio.setCursor(Qt.PointingHandCursor)
        self.btn_studio.setFixedHeight(54)
        self.btn_studio.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 0px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: rgba(39, 39, 42, 0.75);
                border-color: {ThemeColors.BORDER_HOVER};
            }}
            QPushButton:pressed {{
                background-color: rgba(39, 39, 42, 0.95);
            }}
        """)
        st_layout = QHBoxLayout(self.btn_studio)
        st_layout.setContentsMargins(8, 6, 8, 6)
        st_layout.setSpacing(10)

        lbl_st_icon = QLabel()
        lbl_st_icon.setFixedSize(34, 34)
        lbl_st_icon.setAlignment(Qt.AlignCenter)
        lbl_st_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_st_icon.setStyleSheet(f"background: {ThemeColors.CYAN_LO}; border-radius: 9px;")
        lbl_st_icon.setPixmap(create_vector_icon("grid", "#67e8f9", 20))

        st_text_box = QVBoxLayout()
        st_text_box.setContentsMargins(0, 0, 0, 0)
        st_text_box.setSpacing(1)
        lbl_st_t = QLabel("<b>Kho Đạo Cụ</b>")
        lbl_st_t.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_st_t.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 13px; line-height: 1.2;")
        lbl_st_sub = QLabel("Chữ, màu, hiệu ứng, sticker, âm thanh")
        lbl_st_sub.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl_st_sub.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        st_text_box.addWidget(lbl_st_t)
        st_text_box.addWidget(lbl_st_sub)

        st_layout.addWidget(lbl_st_icon)
        st_layout.addLayout(st_text_box, stretch=1)
        self.btn_studio.clicked.connect(self.open_studio_requested.emit)
        c_layout.addWidget(self.btn_studio)

        # =========================================================================
        # 3. Chân khay hướng dẫn (.t-foot)
        # =========================================================================
        lbl_hint = QLabel("Kéo để di chuyển · Chuột phải để thoát")
        lbl_hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10.5px; padding-top: 2px;")
        lbl_hint.setAlignment(Qt.AlignCenter)
        c_layout.addWidget(lbl_hint)

        layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 200))
        shadow.setOffset(0, 8)
        self.container.setGraphicsEffect(shadow)

    def enterEvent(self, event):
        self.bubble._on_tray_entered()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.bubble._on_tray_left()
        super().leaveEvent(event)


class FloatingBubbleWidget(QWidget):
    """
    TRUNG TÂM ĐIỀU HƯỚNG NỔI TRÊN DESKTOP (ResolveFlow Bubble).
    Khắc phục hoàn toàn các nhược điểm cũ:
    - Biểu tượng logo thương hiệu ResolveFlow vector chuẩn mực.
    - Vòng tròn Conic Ring mượt mà khi đang xử lý.
    - Huy hiệu Done Badge xanh lá và % pill thông minh.
    - Khay mở rộng linh hoạt theo vị trí mép màn hình.
    """
    open_auto_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    insert_timeline_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.drag_position = QPoint()
        self.is_dragging = False
        self.progress_pct = 0
        self.status_text = "Sẵn sàng"
        self.is_processing = False
        self.is_done = False
        self.last_duration = ""
        self.is_davinci_connected = True

        # Khởi tạo kích thước vùng vẽ bong bóng: 68x96 px (56px orb + conic ring + 20px % pill)
        self.setFixedSize(QSize(68, 96))

        # Khởi tạo khay Popup mở rộng (Tray)
        self.tray = BubbleTrayPopup(self)
        self.tray.open_auto_requested.connect(self._on_open_auto)
        self.tray.open_studio_requested.connect(self._on_open_studio)
        self.tray.insert_timeline_requested.connect(self._on_insert_timeline)

        # Timers hover mở (120ms) và đóng (400ms) chống giật theo đặc tả thiết kế
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self._show_tray)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_tray)

        # Các thuộc tính tương thích ngược với unit tests & controllers cũ
        self.lbl_title = QLabel("AI Director", self)
        self.lbl_title.setVisible(False)
        self.lbl_status = QLabel("Sẵn sàng", self)
        self.lbl_status.setVisible(False)
        self.lbl_progress = QLabel("0%", self)
        self.lbl_progress.setVisible(False)
        self.lbl_icon = QLabel("🎬", self)
        self.lbl_icon.setVisible(False)

        # Alias 2 nút để tương thích 100% với mã gọi ngoài
        self.btn_ai_mode = self.tray.btn_ai
        self.btn_capcut_mode = self.tray.btn_studio

    def _on_open_auto(self):
        self._hide_tray_immediate()
        self.open_auto_requested.emit()

    def _on_open_studio(self):
        self._hide_tray_immediate()
        self.open_studio_requested.emit()

    def _on_insert_timeline(self):
        self._hide_tray_immediate()
        self.insert_timeline_requested.emit()

    def set_davinci_connected(self, connected: bool):
        """Cập nhật trạng thái kết nối tới DaVinci Resolve."""
        self.is_davinci_connected = connected
        if not self.is_processing and not self.is_done:
            dv_text = "DaVinci đang mở" if self.is_davinci_connected else "DaVinci chưa mở"
            self.tray.lbl_tray_right.setText(dv_text)

    def update_progress(self, pct: int, status_text: str = None, duration: str = None):
        """
        Cập nhật tiến trình % và trạng thái theo đặc tả UI mockup:
        - pct == 0: Chờ (Idle)
        - 0 < pct < 100: Đang chạy (Run)
        - pct >= 100: Hoàn tất (Done)
        """
        self.progress_pct = max(0, min(100, pct))
        if status_text:
            self.status_text = status_text.strip().replace("\n", " ")
        if duration:
            self.last_duration = duration

        self.is_processing = (0 < self.progress_pct < 100)
        self.is_done = (self.progress_pct >= 100)

        # Cập nhật các label tương thích ngược
        pct_str = f"{self.progress_pct}%"
        self.lbl_progress.setText(pct_str)
        self.lbl_status.setText(self.status_text)
        if "AI Director" not in self.lbl_title.text():
            self.lbl_title.setText("AI Director")

        # Cập nhật nội dung trong khay Tray Popup
        if self.is_done:
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN}; font-size: 11px;")
            self.tray.lbl_tray_status.setText("<b>Đã xuất timeline</b>")
            time_display = self.last_duration if self.last_duration else "Xong"
            self.tray.lbl_tray_right.setText(time_display)
            self.tray.lbl_tray_right.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
            self.tray.mini_bar.setVisible(False)
            self.tray.btn_insert_timeline.setVisible(True)
        elif self.is_processing:
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-size: 11px;")
            short_txt = self.status_text if len(self.status_text) <= 36 else self.status_text[:34] + ".."
            self.tray.lbl_tray_status.setText(f"<b>{short_txt}</b>")
            self.tray.lbl_tray_right.setText(pct_str)
            self.tray.lbl_tray_right.setStyleSheet(
                f"color: {ThemeColors.CYAN_HI}; font-family: {ThemeFonts.FAMILY_MONO}; font-weight: bold; font-size: 11px;"
            )
            self.tray.mini_bar.setVisible(True)
            self.tray.mini_bar.setValue(self.progress_pct)
            self.tray.btn_insert_timeline.setVisible(False)
        else:
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN}; font-size: 11px;")
            self.tray.lbl_tray_status.setText("<b>Sẵn sàng</b>")
            dv_text = "DaVinci đang mở" if self.is_davinci_connected else "DaVinci chưa mở"
            self.tray.lbl_tray_right.setText(dv_text)
            self.tray.lbl_tray_right.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
            self.tray.mini_bar.setVisible(False)
            self.tray.btn_insert_timeline.setVisible(False)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Tâm của quả cầu 56px (tâm tại x=34, y=34)
        cx, cy = 34, 34
        radius = 28  # Bán kính 28px (đường kính 56px)

        # =========================================================================
        # 1. Vẽ vòng tiến độ ngoài (Conic Progress Ring: .b-ring) khi đang xử lý
        # =========================================================================
        if self.is_processing and self.progress_pct > 0:
            ring_radius = radius + 4
            ring_rect = QRectF(cx - ring_radius, cy - ring_radius, ring_radius * 2, ring_radius * 2)
            conic = QConicalGradient(cx, cy, -90)  # Bắt đầu từ 12 giờ
            conic.setColorAt(0.0, QColor(ThemeColors.CYAN_HI))
            pct_norm = max(0.01, min(1.0, self.progress_pct / 100.0))
            conic.setColorAt(pct_norm, QColor(ThemeColors.CYAN_HI))
            conic.setColorAt(min(1.0, pct_norm + 0.001), QColor(255, 255, 255, 25))
            conic.setColorAt(1.0, QColor(255, 255, 255, 25))

            ring_pen = QPen(QBrush(conic), 3)
            ring_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(ring_rect)

        # =========================================================================
        # 2. Vẽ bóng đổ của quả cầu (Orb Drop Shadow)
        # =========================================================================
        shadow_path = QPainterPath()
        shadow_path.addEllipse(cx - radius, cy - radius + 2, radius * 2, radius * 2)
        painter.fillPath(shadow_path, QColor(0, 0, 0, 140))

        # =========================================================================
        # 3. Vẽ quả cầu chính (Orb: .b-orb) với Gradient Tím -> Cyan điện ảnh
        # =========================================================================
        orb_path = QPainterPath()
        orb_path.addEllipse(cx - radius, cy - radius, radius * 2, radius * 2)

        grad = QLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius)
        grad.setColorAt(0.0, QColor("#9466ff"))
        grad.setColorAt(0.48, QColor("#6d28d9"))
        grad.setColorAt(1.0, QColor("#0e7490"))

        painter.setBrush(QBrush(grad))
        pen_border = QColor(ThemeColors.BORDER_FOCUS) if self.is_processing else QColor(255, 255, 255, 60)
        painter.setPen(QPen(pen_border, 1.2))
        painter.drawPath(orb_path)

        # =========================================================================
        # 4. Vẽ Logo trung tâm ResolveFlow vector (#logo: Khung màn hình bo góc + Tam giác Play)
        # =========================================================================
        painter.save()
        painter.translate(cx, cy)
        scale_logo = 1.05
        painter.scale(scale_logo, scale_logo)

        # Khung viền bo góc màn hình (viewBox 24x24: M4 7.5A3.5 3.5...)
        frame_rect = QRectF(-8.0, -8.0, 16.0, 16.0)
        frame_pen = QPen(QColor("#FFFFFF"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(frame_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(frame_rect, 3.2, 3.2)

        # Tam giác Play ở giữa khung (d="M10 8.9v6.2l5.2-3.1z")
        play_path = QPainterPath()
        play_path.moveTo(-2.2, -4.5)
        play_path.lineTo(-2.2, 4.5)
        play_path.lineTo(4.4, 0.0)
        play_path.closeSubpath()
        painter.fillPath(play_path, QColor("#FFFFFF"))
        painter.restore()

        # =========================================================================
        # 5. Vẽ dấu tick xanh hoàn tất (.b-done)
        # =========================================================================
        if self.is_done:
            done_cx, done_cy = cx + 18, cy + 18
            done_r = 9.5
            painter.setBrush(QColor(ThemeColors.GREEN))
            painter.setPen(QPen(QColor("#0d0d11"), 2))
            painter.drawEllipse(QRectF(done_cx - done_r, done_cy - done_r, done_r * 2, done_r * 2))

            pen_check = QPen(QColor("#052e16"), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen_check)
            painter.drawLine(QPointF(done_cx - 4.2, done_cy), QPointF(done_cx - 1.0, done_cy + 3.2))
            painter.drawLine(QPointF(done_cx - 1.0, done_cy + 3.2), QPointF(done_cx + 4.5, done_cy - 3.2))

        # =========================================================================
        # 6. Vẽ Pill hiển thị % tiến độ ở dưới đáy (.b-pct)
        # =========================================================================
        if self.is_processing or self.is_done:
            pill_w = 48
            pill_h = 20
            pill_x = cx - pill_w / 2
            pill_y = cy + radius + 5
            pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)

            painter.setBrush(QColor(24, 24, 27, 240))
            pill_border = ThemeColors.BORDER_HOVER if self.is_processing else QColor("#166534")
            painter.setPen(QPen(QColor(pill_border), 1))
            painter.drawRoundedRect(pill_rect, 10, 10)

            pct_txt = "Xong" if self.is_done else f"{self.progress_pct}%"
            txt_color = "#86efac" if self.is_done else ThemeColors.CYAN_HI
            painter.setPen(QColor(txt_color))
            f_mono = QFont("Cascadia Mono", 9)
            f_mono.setBold(True)
            painter.setFont(f_mono)
            painter.drawText(pill_rect, Qt.AlignCenter, pct_txt)

    def _show_tray(self):
        """Hiển thị khay popup cạnh bong bóng, tự phát hiện phía còn trống."""
        if self.is_dragging:
            return

        screen = QApplication.primaryScreen().geometry()
        tray_w = self.tray.width()
        tray_h = self.tray.sizeHint().height()

        b_pos = self.mapToGlobal(QPoint(0, 0))

        # Nếu bong bóng nằm ở nửa phải màn hình -> bung sang TRÁI (right: calc(100% + 12px))
        if b_pos.x() > screen.width() / 2:
            tx = b_pos.x() - tray_w - 10
        else:  # Bung sang PHẢI (left: calc(100% + 12px))
            tx = b_pos.x() + self.width() + 10

        ty = max(10, min(screen.height() - tray_h - 20, b_pos.y() - 8))

        self.tray.move(tx, ty)
        self.tray.show()
        self.tray.raise_()

    def _hide_tray(self):
        """Ẩn khay popup."""
        self.tray.hide()

    def _hide_tray_immediate(self):
        self.show_timer.stop()
        self.hide_timer.stop()
        self.tray.hide()

    def _on_tray_entered(self):
        self.hide_timer.stop()

    def _on_tray_left(self):
        self.hide_timer.start(400)

    def enterEvent(self, event):
        self.hide_timer.stop()
        self.show_timer.start(120)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.show_timer.stop()
        self.hide_timer.start(400)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._hide_tray_immediate()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.is_dragging:
            new_pos = event.globalPosition().toPoint() - self.drag_position
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            # Edge Snapping: Hút dính mép màn hình nếu kéo gần biên (< 50px)
            screen = QApplication.primaryScreen().geometry()
            cur_x = self.x()
            cur_y = max(16, min(screen.height() - self.height() - 40, self.y()))

            if cur_x < 50:
                self.move(16, cur_y)
            elif cur_x > screen.width() - self.width() - 50:
                self.move(screen.width() - self.width() - 16, cur_y)
            else:
                self.move(cur_x, cur_y)

            event.accept()

    def contextMenuEvent(self, event):
        self._hide_tray_immediate()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {ThemeColors.BG_CARD};
                color: {ThemeColors.TEXT_PRIMARY};
                border: 1px solid {ThemeColors.BORDER_HOVER};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {ThemeColors.PRIMARY};
                color: #FFFFFF;
            }}
        """)
        action_auto = menu.addAction("🤖 Mở AI Director")
        action_auto.triggered.connect(self._on_open_auto)

        action_studio = menu.addAction("🎨 Mở Kho Đạo Cụ")
        action_studio.triggered.connect(self._on_open_studio)

        menu.addSeparator()

        action_quit = menu.addAction("❌ Thoát ResolveFlow")
        action_quit.triggered.connect(QApplication.instance().quit)
        menu.exec(event.globalPos())

    def closeEvent(self, event):
        self.tray.close()
        super().closeEvent(event)
