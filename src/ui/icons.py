"""
Module Tạo Biểu Tượng Vector Sắc Nét (Vector Icon Generator) cho ResolveFlow Assistant.
Vẽ trực tiếp bằng QPainter (Antialiased) không phụ thuộc vào bộ font emoji của hệ điều hành,
đảm bảo hiển thị hoàn hảo và đồng nhất trên Windows, macOS và Linux.
"""

from typing import Optional
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QPixmap, QIcon, QPainterPath
)
from src.ui.theme import ThemeColors


def create_vector_icon(icon_type: str, color_hex: str = ThemeColors.TEXT_PRIMARY, size: int = 20) -> QPixmap:
    """
    Tạo biểu tượng vector độ nét cao (Hi-DPI) dạng QPixmap với kích thước và màu sắc tùy chỉnh.
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    # Quy chuẩn tọa độ về hệ quy chiếu chuẩn 24x24
    scale = size / 24.0
    painter.scale(scale, scale)

    pen = QPen(QColor(color_hex), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    if icon_type == "ai":
        # Đầu AI Director / Chip thông minh
        painter.drawRoundedRect(QRectF(4, 7, 16, 12), 3, 3)
        painter.drawLine(QPointF(12, 7), QPointF(12, 4))
        painter.drawLine(QPointF(9, 12), QPointF(9, 13))
        painter.drawLine(QPointF(15, 12), QPointF(15, 13))
        painter.drawLine(QPointF(9.5, 16), QPointF(14.5, 16))

    elif icon_type in ("grid", "studio"):
        # Kho Đạo Cụ 2x2 grid
        painter.drawRoundedRect(QRectF(4, 4, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(13, 4, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(4, 13, 7, 7), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(13, 13, 7, 7), 1.5, 1.5)

    elif icon_type == "resolve":
        # Biểu tượng bánh xe DaVinci Resolve pinwheel
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawEllipse(QRectF(8.8, 8.8, 6.4, 6.4))
        painter.drawLine(QPointF(12, 3), QPointF(12, 8.8))
        painter.drawLine(QPointF(19.8, 16.5), QPointF(14.8, 13.6))
        painter.drawLine(QPointF(4.2, 16.5), QPointF(9.2, 13.6))

    elif icon_type == "download":
        # Mũi tên tải về
        painter.drawLine(QPointF(12, 4.5), QPointF(12, 14.5))
        painter.drawLine(QPointF(7.5, 10.5), QPointF(12, 14.5))
        painter.drawLine(QPointF(16.5, 10.5), QPointF(12, 14.5))
        painter.drawLine(QPointF(5, 18.5), QPointF(19, 18.5))

    elif icon_type == "folder":
        # Thư mục
        path = QPainterPath()
        path.moveTo(4, 6)
        path.lineTo(9, 6)
        path.lineTo(11, 8.5)
        path.lineTo(20, 8.5)
        path.quadTo(21, 8.5, 21, 9.5)
        path.lineTo(21, 18)
        path.quadTo(21, 19, 20, 19)
        path.lineTo(4, 19)
        path.quadTo(3, 19, 3, 18)
        path.lineTo(3, 7)
        path.quadTo(3, 6, 4, 6)
        painter.drawPath(path)

    elif icon_type == "video":
        # Thước phim / File video
        painter.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
        # Các vạch răng cưa phim bên trên & bên dưới
        painter.drawLine(QPointF(7, 5), QPointF(7, 8))
        painter.drawLine(QPointF(12, 5), QPointF(12, 8))
        painter.drawLine(QPointF(17, 5), QPointF(17, 8))
        painter.drawLine(QPointF(7, 16), QPointF(7, 19))
        painter.drawLine(QPointF(12, 16), QPointF(12, 19))
        painter.drawLine(QPointF(17, 16), QPointF(17, 19))
        # Tam giác play nhỏ ở giữa
        play_path = QPainterPath()
        play_path.moveTo(10.5, 9.5)
        play_path.lineTo(15, 12)
        play_path.lineTo(10.5, 14.5)
        play_path.closeSubpath()
        painter.setBrush(QColor(color_hex))
        painter.drawPath(play_path)

    elif icon_type == "cloud":
        # Đám mây lưu trữ
        path = QPainterPath()
        path.moveTo(6.5, 18)
        path.lineTo(18, 18)
        path.quadTo(20.5, 18, 20.5, 15.5)
        path.quadTo(20.5, 13.5, 18.5, 13)
        path.quadTo(18.5, 8.5, 14, 8.5)
        path.quadTo(11.5, 8.5, 10, 10)
        path.quadTo(8.5, 9.5, 7, 11)
        path.quadTo(4, 11.5, 4, 14.5)
        path.quadTo(4, 18, 6.5, 18)
        painter.drawPath(path)

    elif icon_type in ("check", "done"):
        # Dấu tích hoàn thành
        painter.drawLine(QPointF(5, 12.5), QPointF(10, 17))
        painter.drawLine(QPointF(10, 17), QPointF(19.5, 7.5))

    elif icon_type in ("cross", "error"):
        # Dấu X báo lỗi
        painter.drawLine(QPointF(6, 6), QPointF(18, 18))
        painter.drawLine(QPointF(18, 6), QPointF(6, 18))

    elif icon_type in ("clock", "pending"):
        # Đồng hồ chờ / hẹn giờ
        painter.drawEllipse(QRectF(4, 4, 16, 16))
        painter.drawLine(QPointF(12, 8), QPointF(12, 12.5))
        painter.drawLine(QPointF(12, 12.5), QPointF(15.5, 12.5))

    elif icon_type in ("refresh", "running"):
        # Vòng xoay tiến trình
        path = QPainterPath()
        path.arcMoveTo(QRectF(4, 4, 16, 16), 45)
        path.arcTo(QRectF(4, 4, 16, 16), 45, 270)
        painter.drawPath(path)
        # Mũi tên ở đầu vòng
        painter.drawLine(QPointF(16, 8.5), QPointF(19, 8.5))
        painter.drawLine(QPointF(19, 8.5), QPointF(19, 5.5))

    elif icon_type == "play":
        # Nút Play
        play_path = QPainterPath()
        play_path.moveTo(8, 5)
        play_path.lineTo(18, 12)
        play_path.lineTo(8, 19)
        play_path.closeSubpath()
        painter.setBrush(QColor(color_hex))
        painter.drawPath(play_path)

    elif icon_type == "stop":
        # Nút Stop hình vuông bo tròn nhẹ
        painter.setBrush(QColor(color_hex))
        painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)

    elif icon_type == "arrow_right":
        # Mũi tên sang phải
        painter.drawLine(QPointF(6, 12), QPointF(18, 12))
        painter.drawLine(QPointF(13, 7), QPointF(18, 12))
        painter.drawLine(QPointF(13, 17), QPointF(18, 12))

    elif icon_type == "sparkles":
        # Ngôi sao 4 cánh / AI
        path = QPainterPath()
        path.moveTo(12, 3)
        path.quadTo(12, 12, 21, 12)
        path.quadTo(12, 12, 12, 21)
        path.quadTo(12, 12, 3, 12)
        path.quadTo(12, 12, 12, 3)
        painter.setBrush(QColor(color_hex))
        painter.drawPath(path)

    else:
        # Fallback: Vòng tròn chấm tâm
        painter.drawEllipse(QRectF(5, 5, 14, 14))
        painter.drawPoint(QPointF(12, 12))

    painter.end()
    return pix


def get_vector_icon(icon_type: str, color_hex: str = ThemeColors.TEXT_PRIMARY, size: int = 20) -> QIcon:
    """Trả về đối tượng QIcon tiện lợi cho QPushButton hoặc QAction."""
    return QIcon(create_vector_icon(icon_type, color_hex, size))
