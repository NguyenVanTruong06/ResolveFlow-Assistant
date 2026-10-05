"""
Bong bóng nổi Desktop (Floating Desktop Bubble) cho ResolveFlow Assistant.
Thiết kế theo chuẩn hiện đại từ src/ui/resolveflow_ui.html:
- Quả cầu tròn 56px với gradient Tím - Cyan điện ảnh và bóng đổ mượt mà.
- Vòng tròn tiến độ Conic Gradient (QConicalGradient) và huy hiệu % phát sáng.
- Khay điều hướng (Tray Popup) mở rộng thông minh với độ trễ 120ms mở / 400ms đóng chống giật.
- Tự động nhận diện mép màn hình (Ghim trái / Ghim phải) và tự dính mép (Edge Snapping).
- 2 Nút hành động lớn: 🤖 AI Director (1-Click) và 🎨 Kho Đạo Cụ (Studio).
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMenu,
    QGraphicsDropShadowEffect, QApplication, QPushButton, QProgressBar, QFrame
)
from PySide6.QtCore import Qt, QPoint, Signal as pyqtSignal, QSize, QTimer, QRectF
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QLinearGradient, QConicalGradient,
    QFont, QCursor, QPainterPath
)
from src.ui.theme import ThemeColors, ThemeFonts


class BubbleTrayPopup(QWidget):
    """
    Khay điều hướng nổi (Tray Popup) xuất hiện mượt mà khi rê chuột vào Bong bóng.
    """
    open_auto_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    insert_timeline_requested = pyqtSignal()

    def __init__(self, bubble):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SubWindow)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.bubble = bubble

        self.setFixedWidth(240)
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
        c_layout.setContentsMargins(10, 10, 10, 10)
        c_layout.setSpacing(8)

        # 1. Khối trạng thái (Status Header)
        self.status_box = QWidget()
        s_box_layout = QVBoxLayout(self.status_box)
        s_box_layout.setContentsMargins(0, 0, 0, 4)
        s_box_layout.setSpacing(4)

        h_stat = QHBoxLayout()
        h_stat.setContentsMargins(0, 0, 0, 0)
        h_stat.setSpacing(6)

        self.lbl_dot = QLabel("●")
        self.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN}; font-size: 10px;")

        self.lbl_tray_status = QLabel("<b>Sẵn sàng</b>")
        self.lbl_tray_status.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 12px;")

        self.lbl_tray_pct = QLabel("")
        self.lbl_tray_pct.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-family: {ThemeFonts.FAMILY_MONO}; font-weight: bold; font-size: 11px;")

        h_stat.addWidget(self.lbl_dot)
        h_stat.addWidget(self.lbl_tray_status, stretch=1)
        h_stat.addWidget(self.lbl_tray_pct)
        s_box_layout.addLayout(h_stat)

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

        c_layout.addWidget(self.status_box)

        # Đường ngăn cách mỏng
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {ThemeColors.BORDER_DEFAULT};")
        c_layout.addWidget(sep)

        # 2. Cụm 2 nút hành động lớn
        self.btn_ai = QPushButton()
        self.btn_ai.setCursor(Qt.PointingHandCursor)
        self.btn_ai.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 6px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.BG_INPUT};
                border-color: {ThemeColors.BORDER_HOVER};
            }}
        """)
        ai_layout = QHBoxLayout(self.btn_ai)
        ai_layout.setContentsMargins(4, 4, 4, 4)
        ai_layout.setSpacing(10)
        
        lbl_ai_icon = QLabel("🤖")
        lbl_ai_icon.setFixedSize(32, 32)
        lbl_ai_icon.setAlignment(Qt.AlignCenter)
        lbl_ai_icon.setStyleSheet(f"background: {ThemeColors.VIOLET_LO}; color: #c4b5fd; font-size: 16px; border-radius: 8px;")
        
        ai_text_box = QVBoxLayout()
        ai_text_box.setSpacing(1)
        lbl_ai_t = QLabel("<b>AI Director</b>")
        lbl_ai_t.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 12.5px;")
        lbl_ai_sub = QLabel("Dựng tự động 1-click")
        lbl_ai_sub.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        ai_text_box.addWidget(lbl_ai_t)
        ai_text_box.addWidget(lbl_ai_sub)
        
        ai_layout.addWidget(lbl_ai_icon)
        ai_layout.addLayout(ai_text_box, stretch=1)
        self.btn_ai.clicked.connect(self.open_auto_requested.emit)
        c_layout.addWidget(self.btn_ai)

        self.btn_studio = QPushButton()
        self.btn_studio.setCursor(Qt.PointingHandCursor)
        self.btn_studio.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 10px;
                padding: 6px;
                text-align: left;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.BG_INPUT};
                border-color: {ThemeColors.BORDER_HOVER};
            }}
        """)
        st_layout = QHBoxLayout(self.btn_studio)
        st_layout.setContentsMargins(4, 4, 4, 4)
        st_layout.setSpacing(10)

        lbl_st_icon = QLabel("🎨")
        lbl_st_icon.setFixedSize(32, 32)
        lbl_st_icon.setAlignment(Qt.AlignCenter)
        lbl_st_icon.setStyleSheet(f"background: {ThemeColors.CYAN_LO}; color: #67e8f9; font-size: 16px; border-radius: 8px;")

        st_text_box = QVBoxLayout()
        st_text_box.setSpacing(1)
        lbl_st_t = QLabel("<b>Kho Đạo Cụ</b>")
        lbl_st_t.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 12.5px;")
        lbl_st_sub = QLabel("Chữ, màu, hiệu ứng, âm thanh")
        lbl_st_sub.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        st_text_box.addWidget(lbl_st_t)
        st_text_box.addWidget(lbl_st_sub)

        st_layout.addWidget(lbl_st_icon)
        st_layout.addLayout(st_text_box, stretch=1)
        self.btn_studio.clicked.connect(self.open_studio_requested.emit)
        c_layout.addWidget(self.btn_studio)

        # Footer Hint
        lbl_hint = QLabel("Kéo để di chuyển · Chuột phải để thoát")
        lbl_hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10px; padding-top: 2px;")
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
    """
    open_auto_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
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

        # Khởi tạo kích thước vùng vẽ bong bóng: 68x92 px (đủ cho 56px orb + 20px % badge)
        self.setFixedSize(QSize(68, 92))

        # Khởi tạo khay Popup mở rộng (Tray)
        self.tray = BubbleTrayPopup(self)
        self.tray.open_auto_requested.connect(self._on_open_auto)
        self.tray.open_studio_requested.connect(self._on_open_studio)

        # Timers hover mở (120ms) và đóng (400ms)
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self._show_tray)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._hide_tray)

        # Các thuộc tính tương thích ngược với unit tests & legacy controllers
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

    def update_progress(self, pct: int, status_text: str = None):
        """Cập nhật tiến trình % và trạng thái."""
        self.progress_pct = max(0, min(100, pct))
        if status_text:
            self.status_text = status_text.strip().replace("\n", " ")

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
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN};")
            self.tray.lbl_tray_status.setText("<b>Đã hoàn tất!</b>")
            self.tray.lbl_tray_pct.setText("Xong")
            self.tray.lbl_tray_pct.setStyleSheet(f"color: {ThemeColors.TEXT_SUCCESS};")
            self.tray.mini_bar.setVisible(False)
        elif self.is_processing:
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.CYAN_HI};")
            short_txt = self.status_text if len(self.status_text) <= 20 else self.status_text[:18] + ".."
            self.tray.lbl_tray_status.setText(f"<b>{short_txt}</b>")
            self.tray.lbl_tray_pct.setText(pct_str)
            self.tray.lbl_tray_pct.setStyleSheet(f"color: {ThemeColors.CYAN_HI};")
            self.tray.mini_bar.setVisible(True)
            self.tray.mini_bar.setValue(self.progress_pct)
        else:
            self.tray.lbl_dot.setStyleSheet(f"color: {ThemeColors.GREEN};")
            self.tray.lbl_tray_status.setText("<b>Sẵn sàng</b>")
            self.tray.lbl_tray_pct.setText("")
            self.tray.mini_bar.setVisible(False)

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Tâm của quả cầu 56px (đặt ở x=34, y=34)
        cx, cy = 34, 34
        radius = 28 # Bán kính 28px (đường kính 56px)

        # 1. Vẽ vòng tiến độ ngoài (Conic Progress Ring) khi đang chạy
        if self.is_processing and self.progress_pct > 0:
            ring_rect = QRectF(cx - radius - 4, cy - radius - 4, (radius + 4) * 2, (radius + 4) * 2)
            conic = QConicalGradient(cx, cy, -90) # Bắt đầu từ 12 giờ
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

        # 2. Vẽ bóng đổ của quả cầu (Orb Drop Shadow)
        shadow_path = QPainterPath()
        shadow_path.addEllipse(cx - radius, cy - radius + 2, radius * 2, radius * 2)
        painter.fillPath(shadow_path, QColor(0, 0, 0, 140))

        # 3. Vẽ quả cầu chính (Orb with Linear Gradient Tím -> Cyan)
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

        # 4. Vẽ Icon trung tâm (🎬 Cinema clapper / Play icon)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Segoe UI Emoji", 18))
        painter.drawText(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), Qt.AlignCenter, "🎬")

        # 5. Vẽ dấu tick xanh hoàn tất (Done Badge)
        if self.is_done:
            done_cx, done_cy = cx + 18, cy + 18
            done_r = 9
            painter.setBrush(QColor(ThemeColors.GREEN))
            painter.setPen(QPen(QColor(ThemeColors.BG_CANVAS), 2))
            painter.drawEllipse(done_cx - done_r, done_cy - done_r, done_r * 2, done_r * 2)

            painter.setPen(QPen(QColor("#052e16"), 2))
            painter.drawLine(done_cx - 4, done_cy, done_cx - 1, done_cy + 3)
            painter.drawLine(done_cx - 1, done_cy + 3, done_cx + 4, done_cy - 3)

        # 6. Vẽ Pill hiển thị % tiến độ ở dưới đáy
        if self.is_processing or self.is_done:
            pill_w = 48
            pill_h = 18
            pill_x = cx - pill_w / 2
            pill_y = cy + radius + 4
            pill_rect = QRectF(pill_x, pill_y, pill_w, pill_h)

            painter.setBrush(QColor(24, 24, 27, 240))
            pill_border = ThemeColors.BORDER_FOCUS if self.is_processing else ThemeColors.GREEN
            painter.setPen(QPen(QColor(pill_border), 1))
            painter.drawRoundedRect(pill_rect, 9, 9)

            pct_txt = "Xong" if self.is_done else f"{self.progress_pct}%"
            txt_color = ThemeColors.TEXT_SUCCESS if self.is_done else ThemeColors.CYAN_HI
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

        # Nếu bong bóng nằm ở nửa phải màn hình -> bung sang TRÁI
        if b_pos.x() > screen.width() / 2:
            tx = b_pos.x() - tray_w - 6
        else: # Bung sang PHẢI
            tx = b_pos.x() + self.width() + 6

        ty = max(10, min(screen.height() - tray_h - 20, b_pos.y() - 10))

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
