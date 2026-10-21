"""
Micro-Interactions System (Learning from Akitt Pro 4.0).
Provides RippleOverlay, SegmentIndicator, and Luxury Dark Glassmorphism Stylesheet.
"""
from typing import Optional
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import (
    Qt, QPoint, QRect, QRectF, QVariantAnimation, QEasingCurve, Property, Signal as pyqtSignal
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient, QPainterPath
)


class RippleOverlay(QWidget):
    """
    Transparent overlay widget that renders smooth radial expanding ripples on click.
    Uses non-blocking QVariantAnimation with QEasingCurve.Type.OutCubic.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._center = QPoint(0, 0)
        self._color = QColor("#3dcee1")
        self._max_radius = 120.0
        self._current_radius = 0.0
        self._current_opacity = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_step)
        self._anim.finished.connect(self._on_anim_finished)

        if parent:
            self.resize(parent.size())

    @property
    def current_radius(self) -> float:
        return self._current_radius

    @property
    def current_opacity(self) -> float:
        return self._current_opacity

    def is_animating(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def trigger_ripple(
        self,
        pos: QPoint,
        color: Optional[QColor] = None,
        max_radius: float = 120.0,
        duration: int = 300,
    ):
        """Triggers a ripple wave starting at `pos`."""
        if self.parent():
            self.setGeometry(0, 0, self.parent().width(), self.parent().height())
            self.raise_()

        self._center = pos
        self._color = color or QColor("#3dcee1")
        self._max_radius = max_radius
        self._current_radius = 6.0
        self._current_opacity = 0.35

        self._anim.stop()
        self._anim.setDuration(duration)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self.show()
        self._anim.start()

    def stop(self):
        """Immediately stops the ripple animation."""
        if self.is_animating():
            self._anim.stop()
        self._current_opacity = 0.0
        self._current_radius = 0.0
        self.update()

    def _on_anim_step(self, val: float):
        progress = float(val)
        self._current_radius = 6.0 + (self._max_radius - 6.0) * progress
        self._current_opacity = max(0.0, 0.35 * (1.0 - progress))
        self.update()

    def _on_anim_finished(self):
        self._current_opacity = 0.0
        self.update()

    def paintEvent(self, event):
        if self._current_opacity <= 0.001 or self._current_radius <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        c = QColor(self._color)
        c.setAlpha(int(self._current_opacity * 255))
        painter.setBrush(QBrush(c))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(self._center, self._current_radius, self._current_radius)


class SegmentIndicator(QWidget):
    """
    Physical sliding indicator for tab and segmented bar switching.
    Smoothly animates its horizontal position using QEasingCurve.Type.OutCubic.
    """
    def __init__(self, segment_count: int = 1, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._segment_count = max(1, segment_count)
        self._current_index = 0
        self._target_index = 0
        self._current_x = 0.0

        self.animation = QVariantAnimation(self)
        self.animation.setDuration(240)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._on_anim_step)
        self.animation.finished.connect(self._on_anim_finished)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def target_index(self) -> int:
        return self._target_index

    def set_segment_count(self, count: int):
        self._segment_count = max(1, count)
        self.update()

    def get_indicator_rect(self) -> QRect:
        seg_w = self.width() / self._segment_count if self._segment_count > 0 else self.width()
        return QRect(int(self._current_x), 0, int(seg_w), self.height())

    def set_index(self, index: int, animated: bool = True, duration: int = 240):
        """Sets the active segment index."""
        idx = max(0, min(self._segment_count - 1, index))
        self._target_index = idx
        seg_w = self.width() / self._segment_count if self._segment_count > 0 else self.width()
        target_x = float(idx * seg_w)

        if not animated:
            self.animation.stop()
            self._current_x = target_x
            self._current_index = idx
            self.update()
            return

        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(float(self._current_x))
        self.animation.setEndValue(target_x)
        self.animation.start()

    def _on_anim_step(self, val: float):
        self._current_x = float(val)
        self.update()

    def _on_anim_finished(self):
        self._current_index = self._target_index
        seg_w = self.width() / self._segment_count if self._segment_count > 0 else self.width()
        self._current_x = float(self._current_index * seg_w)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        seg_w = self.width() / self._segment_count if self._segment_count > 0 else self.width()
        self._current_x = float(self._current_index * seg_w)
        self.update()

    def paintEvent(self, event):
        if self._segment_count <= 0 or self.width() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.get_indicator_rect()
        pad_x = 4
        h = max(2, self.height())

        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        gradient.setColorAt(0.0, QColor("#3C9A8C"))
        gradient.setColorAt(1.0, QColor("#3dcee1"))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        # Draw pill shaped indicator
        pill_rect = QRectF(rect.x() + pad_x, 0, max(4, rect.width() - pad_x * 2), h)
        painter.drawRoundedRect(pill_rect, h / 2.0, h / 2.0)


def get_luxury_dark_stylesheet() -> str:
    """
    Returns the comprehensive Luxury Dark Glassmorphism stylesheet for ChunDVC.
    Palette:
      - Deep background: rgba(14, 17, 22, 0.95)
      - Surface card: rgba(22, 27, 34, 0.85)
      - Border: 1px solid rgba(255, 255, 255, 0.08)
      - Primary Accent: #3C9A8C / #3dcee1
      - Text: #E6EDF3 (Primary), #8B949E (Muted)
    """
    return """
    /* Luxury Dark Glassmorphism Theme for ChunDVC */
    QWidget {
        font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
        color: #E6EDF3;
        font-size: 12px;
    }

    /* Container Frames */
    QFrame#lux_drawer_panel, QFrame.lux_card {
        background-color: rgba(14, 17, 22, 0.95);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
    }

    QFrame.lux_surface {
        background-color: rgba(22, 27, 34, 0.75);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px;
    }

    /* PushButtons */
    QPushButton {
        background-color: rgba(26, 32, 42, 0.9);
        color: #E6EDF3;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 7px 14px;
        font-weight: 600;
        min-height: 18px;
    }

    QPushButton:hover {
        background-color: rgba(38, 48, 62, 0.95);
        border: 1px solid rgba(61, 206, 225, 0.35);
        color: #FFFFFF;
    }

    QPushButton:pressed {
        background-color: rgba(18, 22, 28, 0.95);
        border-color: #3C9A8C;
    }

    QPushButton.lux_primary_btn {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1E645A, stop:1 #278577);
        color: #FFFFFF;
        border: 1px solid rgba(61, 206, 225, 0.4);
    }

    QPushButton.lux_primary_btn:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #278577, stop:1 #3C9A8C);
        border: 1px solid #3dcee1;
    }

    /* Input & Combos */
    QLineEdit, QPlainTextEdit, QTextEdit {
        background-color: rgba(10, 13, 17, 0.85);
        color: #F0F6FC;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: #3C9A8C;
    }

    QLineEdit:focus, QPlainTextEdit:focus {
        border: 1px solid #3dcee1;
        background-color: rgba(13, 17, 23, 0.95);
    }

    QComboBox {
        background-color: rgba(18, 22, 28, 0.9);
        color: #E6EDF3;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 6px;
        padding: 5px 10px;
    }

    QComboBox:hover {
        border-color: rgba(61, 206, 225, 0.35);
    }

    QComboBox QAbstractItemView {
        background-color: #0E1116;
        color: #E6EDF3;
        selection-background-color: #1E645A;
        border: 1px solid rgba(255, 255, 255, 0.08);
        outline: none;
    }

    /* Scrollbars */
    QScrollBar:vertical {
        background: transparent;
        width: 6px;
        margin: 0;
    }

    QScrollBar::handle:vertical {
        background: rgba(255, 255, 255, 0.16);
        border-radius: 3px;
        min-height: 24px;
    }

    QScrollBar::handle:vertical:hover {
        background: rgba(61, 206, 225, 0.5);
    }

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }

    /* Tab Widget Luxury Dark */
    QTabWidget::pane {
        border: 1px solid rgba(255, 255, 255, 0.06);
        background-color: rgba(14, 17, 22, 0.95);
        border-radius: 8px;
    }

    QTabBar::tab {
        background-color: transparent;
        color: #8B949E;
        padding: 8px 16px;
        font-weight: 600;
        border-bottom: 2px solid transparent;
    }

    QTabBar::tab:selected {
        color: #3dcee1;
    }

    QTabBar::tab:hover {
        color: #E6EDF3;
    }
    """
