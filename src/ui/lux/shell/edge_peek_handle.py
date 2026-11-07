"""
EdgePeekHandle: Nút mũi tên nhỏ nhô ra từ cạnh màn hình khi rê chuột lại gần.
Thiết kế chuyên dụng cho quy trình dựng phim trên DaVinci Resolve:
- Khi thu gọn Dock: ẩn hoàn toàn (0px trên màn hình, không vướng Inspector hay Timeline).
- Khi chuột di chuyển tới sát mép màn hình đã neo: mũi tên nhỏ nhô mượt ra từ cạnh màn hình.
- Khi bấm chuột trái vào mũi tên: mở toàn bộ thanh EdgeDock (420px).
- Khi chuột rời xa vùng cạnh màn hình: tự động thụt ẩn hoàn toàn vào mép.
- Tuyệt đối không chiếm tiêu điểm bàn phím (WA_ShowWithoutActivating + NoFocus).
"""
from typing import Optional

from PySide6.QtCore import (
    Qt, QPointF, QRectF, QVariantAnimation, QEasingCurve, Signal as pyqtSignal
)
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QLinearGradient, QMouseEvent
)
from PySide6.QtWidgets import QWidget, QMenu


class EdgePeekHandle(QWidget):
    """
    Nút mũi tên nhỏ gọn (26x64px) nhô ra sát mép màn hình khi chuột tiến lại gần.
    """
    HANDLE_WIDTH = 26
    HANDLE_HEIGHT = 64

    open_dock_requested = pyqtSignal()
    open_director_requested = pyqtSignal()
    open_studio_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    edge_switch_requested = pyqtSignal(str)

    def __init__(self, edge: str = "right", parent: Optional[QWidget] = None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.HANDLE_WIDTH, self.HANDLE_HEIGHT)

        self._edge = edge if edge in ("left", "right") else "right"
        self._hovered = False
        self._pressed = False
        self._slide_progress = 1.0  # 0.0 = ẩn sát mép, 1.0 = nhô ra hoàn toàn
        self._hiding = False
        self.is_menu_open = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_finished)

        self._update_tooltip()

    @property
    def dock_edge(self) -> str:
        return self._edge

    def set_dock_edge(self, edge: str):
        if edge not in ("left", "right"):
            return
        self._edge = edge
        self._update_tooltip()
        self.update()

    def _update_tooltip(self):
        side_txt = "trái" if self._edge == "left" else "phải"
        self.setToolTip(f"Bấm để mở Thanh Dock ChunDVC (Neo cạnh {side_txt} • Ctrl+Alt+D)")

    def show_at(self, x: int, y: int, animated: bool = True):
        """Hiển thị mũi tên tại tọa độ mép màn hình với hiệu ứng nhô ra mượt."""
        self.move(x, y)
        self._hiding = False
        self._anim.stop()

        if not animated:
            self._slide_progress = 1.0
            self.show()
            self.raise_()
            self.update()
            return

        if not self.isVisible():
            self._slide_progress = 0.0
            self.show()
            self.raise_()

        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setStartValue(float(self._slide_progress))
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_animated(self, animated: bool = True):
        """Thu mũi tên vào trong cạnh màn hình rồi ẩn hoàn toàn (0px)."""
        if not self.isVisible():
            self._hiding = False
            return

        self._hovered = False
        self._pressed = False
        self._anim.stop()

        if not animated:
            self._hiding = False
            self._slide_progress = 0.0
            self.hide()
            return

        self._hiding = True
        self._anim.setDuration(110)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.setStartValue(float(self._slide_progress))
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_anim_value(self, val):
        self._slide_progress = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_anim_finished(self):
        if self._hiding and self._slide_progress <= 0.02:
            self._hiding = False
            self.hide()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        if self._hiding:
            self._hiding = False
            self._anim.stop()
            self._slide_progress = 1.0
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self._pressed = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            was_pressed = self._pressed
            self._pressed = False
            self.update()
            if was_pressed and self.rect().contains(event.position().toPoint()):
                self.open_dock_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.is_menu_open = True
        try:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background: #18181b;
                    color: #f4f4f5;
                    border: 1px solid #2e3138;
                    border-radius: 8px;
                    padding: 4px;
                    font-size: 12px;
                }
                QMenu::item {
                    padding: 6px 14px;
                    border-radius: 4px;
                }
                QMenu::item:selected {
                    background: #d94f3d;
                    color: #ffffff;
                }
                QMenu::separator {
                    height: 1px;
                    background: #27272a;
                    margin: 4px 6px;
                }
            """)

            act_open = menu.addAction("◀ Mở Thanh Dock (Ctrl+Alt+D)")
            act_open.triggered.connect(self.open_dock_requested.emit)

            act_director = menu.addAction("🤖 Mở AI Director")
            act_director.triggered.connect(self.open_director_requested.emit)

            act_studio = menu.addAction("🎨 Mở Kho Đạo Cụ")
            act_studio.triggered.connect(self.open_studio_requested.emit)

            menu.addSeparator()

            target_edge = "left" if self._edge == "right" else "right"
            edge_lbl = "Chuyển sang cạnh Trái" if target_edge == "left" else "Chuyển sang cạnh Phải"
            act_edge = menu.addAction(f"⇄ {edge_lbl}")
            act_edge.triggered.connect(lambda: self.edge_switch_requested.emit(target_edge))

            act_settings = menu.addAction("⚙ Cài đặt Thanh Dock")
            act_settings.triggered.connect(self.settings_requested.emit)

            menu.exec(event.globalPos())
        finally:
            self.is_menu_open = False

    def _build_tab_path(self, rect: QRectF, radius: float) -> QPainterPath:
        """Tạo hình thẻ bo tròn hướng vào trong màn hình, phẳng tuyệt đối ở cạnh ngoài."""
        path = QPainterPath()
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        r = min(radius, w * 0.6, h * 0.35)

        if self._edge == "right":
            # Cạnh phải phẳng (x + w), 2 góc bên trái bo tròn
            path.moveTo(x + w, y)
            path.lineTo(x + r, y)
            path.quadTo(x, y, x, y + r)
            path.lineTo(x, y + h - r)
            path.quadTo(x, y + h, x + r, y + h)
            path.lineTo(x + w, y + h)
            path.closeSubpath()
        else:
            # Cạnh trái phẳng (x), 2 góc bên phải bo tròn
            path.moveTo(x, y)
            path.lineTo(x + w - r, y)
            path.quadTo(x + w, y, x + w, y + r)
            path.lineTo(x + w, y + h - r)
            path.quadTo(x + w, y + h, x + w - r, y + h)
            path.lineTo(x, y + h)
            path.closeSubpath()
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())

        # Dịch chuyển mượt từ ngoài mép màn hình nhô vào theo _slide_progress
        offset_px = (1.0 - self._slide_progress) * (w - 2.0)
        if self._edge == "right":
            painter.translate(offset_px, 0.0)
            body_rect = QRectF(1.5, 1.5, w - 1.5, h - 3.0)
        else:
            painter.translate(-offset_px, 0.0)
            body_rect = QRectF(0.0, 1.5, w - 1.5, h - 3.0)

        tab_path = self._build_tab_path(body_rect, radius=10.0)

        # Nền Studio Dark + Studio Coral subtle gradient khi hover
        grad = QLinearGradient(body_rect.topLeft(), body_rect.bottomRight())
        if self._pressed:
            grad.setColorAt(0.0, QColor(42, 26, 25, 248))
            grad.setColorAt(1.0, QColor(28, 28, 32, 248))
        elif self._hovered:
            grad.setColorAt(0.0, QColor(38, 33, 36, 246))
            grad.setColorAt(1.0, QColor(24, 24, 28, 246))
        else:
            grad.setColorAt(0.0, QColor(27, 28, 32, 236))
            grad.setColorAt(1.0, QColor(18, 19, 22, 236))

        painter.fillPath(tab_path, grad)

        # Viền mảnh Studio Coral (#d94f3d)
        border_color = QColor("#e05a47") if self._hovered else QColor(217, 79, 61, 155)
        border_pen = QPen(border_color, 1.4 if self._hovered else 1.1)
        painter.setPen(border_pen)
        painter.drawPath(tab_path)

        # Vạch nhấn dọc nhỏ bên mép trong
        accent_y1 = h * 0.30
        accent_y2 = h * 0.70
        accent_x = body_rect.left() + 2.2 if self._edge == "right" else body_rect.right() - 2.2
        accent_pen = QPen(QColor("#d94f3d" if not self._hovered else "#ff6b57"), 2.0)
        accent_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(accent_pen)
        painter.drawLine(QPointF(accent_x, accent_y1), QPointF(accent_x, accent_y2))

        # Mũi tên Chevron sắc nét ở tâm (‹ khi neo phải, › khi neo trái)
        cx = body_rect.center().x() + (1.5 if self._edge == "right" else -1.5)
        cy = body_rect.center().y()
        arm_x = 4.2
        arm_y = 6.2

        arrow_path = QPainterPath()
        if self._edge == "right":
            # Mũi tên trỏ sang trái ‹ (mở ra từ cạnh phải)
            arrow_path.moveTo(cx + arm_x * 0.55, cy - arm_y)
            arrow_path.lineTo(cx - arm_x * 0.65, cy)
            arrow_path.lineTo(cx + arm_x * 0.55, cy + arm_y)
        else:
            # Mũi tên trỏ sang phải › (mở ra từ cạnh trái)
            arrow_path.moveTo(cx - arm_x * 0.55, cy - arm_y)
            arrow_path.lineTo(cx + arm_x * 0.65, cy)
            arrow_path.lineTo(cx - arm_x * 0.55, cy + arm_y)

        arrow_color = QColor("#ffffff") if self._hovered else QColor("#e4e4e7")
        arrow_pen = QPen(arrow_color, 2.2)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        arrow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arrow_pen)
        painter.drawPath(arrow_path)
