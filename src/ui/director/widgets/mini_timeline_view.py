"""
src/ui/director/widgets/mini_timeline_view.py
Hiển thị dải màu trực quan mô phỏng timeline phân đoạn:
- Hook (Tím: #8b5cf6)
- Giữ lại (Xanh lá: #22c55e)
- Cắt bỏ (Đỏ: #ef4444)
- Tua nhanh 8x (Vàng hổ phách: #f59e0b)
"""

from typing import List, Tuple
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QBrush
from PySide6.QtCore import Qt, QRectF


class TimelineSegmentBar(QWidget):
    """Thanh dải màu vẽ các phân đoạn timeline với tỉ lệ linh hoạt."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        # (Label, weight, color_hex)
        self._segments: List[Tuple[str, float, str]] = [
            ("Hook", 6.0, "#8b5cf6"),
            ("Intro", 10.0, "#22c55e"),
            ("Cắt", 3.0, "#ef4444"),
            ("Main Talk", 26.0, "#22c55e"),
            ("8x", 4.0, "#f59e0b"),
            ("Outro", 11.0, "#22c55e")
        ]

    def set_segments(self, segments: List[Tuple[str, float, str]]):
        if segments:
            self._segments = segments
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        total_weight = sum(s[1] for s in self._segments) or 1.0

        w = rect.width()
        h = rect.height()

        x_cursor = 0.0
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

        for i, (label, weight, color_hex) in enumerate(self._segments):
            seg_w = (weight / total_weight) * w
            seg_rect = QRectF(x_cursor, 0, seg_w, h)

            # Vẽ màu khối
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.drawRect(seg_rect)

            # Viền ngăn cách nhỏ
            painter.setPen(QPen(QColor("#131316"), 1))
            painter.drawLine(int(x_cursor), 0, int(x_cursor), int(h))

            # Chữ nhãn bên trong nếu đủ rộng
            if seg_w > 32:
                painter.setPen(QColor("#ffffff" if color_hex != "#f59e0b" else "#18181b"))
                painter.drawText(seg_rect, Qt.AlignmentFlag.AlignCenter, label)

            x_cursor += seg_w

        # Viền ngoài bo góc nhẹ
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#33333a"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)


class MiniTimelineWidget(QWidget):
    """Widget trọn gói gồm thanh dải màu + chú giải (Legend)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        self.bar = TimelineSegmentBar(self)
        layout.addWidget(self.bar)

        # Hàng chú giải (Legend)
        h_legend = QHBoxLayout()
        h_legend.setContentsMargins(4, 0, 4, 0)
        h_legend.setSpacing(16)

        legends = [
            ("Giữ", "#22c55e"),
            ("Cắt", "#ef4444"),
            ("Tua 8x", "#f59e0b"),
            ("Hook", "#8b5cf6")
        ]

        for label, color in legends:
            item = QWidget()
            h_item = QHBoxLayout(item)
            h_item.setContentsMargins(0, 0, 0, 0)
            h_item.setSpacing(5)

            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background-color: {color}; border-radius: 4px;")
            h_item.addWidget(dot)

            lbl = QLabel(label)
            lbl.setStyleSheet("color: #a1a1aa; font-size: 11px;")
            h_item.addWidget(lbl)

            h_legend.addWidget(item)

        h_legend.addStretch(1)
        layout.addLayout(h_legend)

    def set_segments(self, segments: List[Tuple[str, float, str]]):
        self.bar.set_segments(segments)
