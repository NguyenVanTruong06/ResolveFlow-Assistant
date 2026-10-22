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

    elif icon_type == "bolt":
        # Tia chớp ChunDVC
        path = QPainterPath()
        path.moveTo(13, 2.5)
        path.lineTo(6.5, 12)
        path.lineTo(11.5, 12)
        path.lineTo(10.5, 21.5)
        path.lineTo(17.5, 11)
        path.lineTo(12.5, 11)
        path.closeSubpath()
        painter.setBrush(QColor(color_hex))
        painter.drawPath(path)

    elif icon_type == "text":
        # Chữ T tiêu đề NLE
        painter.drawLine(QPointF(5, 5.5), QPointF(19, 5.5))
        painter.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
        painter.drawLine(QPointF(9, 18.5), QPointF(15, 18.5))

    elif icon_type == "trans":
        # Hai khung hình chuyển cảnh lồng nhau
        painter.drawRoundedRect(QRectF(3.5, 5.5, 11, 13), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(9.5, 5.5, 11, 13), 1.5, 1.5)

    elif icon_type == "meme":
        # Khung ảnh / B-Roll
        painter.drawRoundedRect(QRectF(4, 5, 16, 14), 2, 2)
        painter.drawEllipse(QRectF(7, 7.5, 3, 3))
        path = QPainterPath()
        path.moveTo(5, 17)
        path.lineTo(9.5, 12.5)
        path.lineTo(13, 15.5)
        path.lineTo(16, 13)
        path.lineTo(19, 16)
        painter.drawPath(path)

    elif icon_type == "overlay":
        # Hai lớp xếp chồng isometric
        path1 = QPainterPath()
        path1.moveTo(12, 4)
        path1.lineTo(19.5, 8)
        path1.lineTo(12, 12)
        path1.lineTo(4.5, 8)
        path1.closeSubpath()
        painter.drawPath(path1)
        path2 = QPainterPath()
        path2.moveTo(4.5, 12.5)
        path2.lineTo(12, 16.5)
        path2.lineTo(19.5, 12.5)
        painter.drawPath(path2)

    elif icon_type == "sfx":
        # Nốt nhạc đôi
        painter.drawEllipse(QRectF(5, 13, 4.5, 3.5))
        painter.drawEllipse(QRectF(13.5, 11, 4.5, 3.5))
        painter.drawLine(QPointF(9.5, 14.5), QPointF(9.5, 5.5))
        painter.drawLine(QPointF(18, 12.5), QPointF(18, 4))
        painter.drawLine(QPointF(9.5, 5.5), QPointF(18, 4))

    elif icon_type == "palette":
        # Kính lúp / Command HUD với cán cầm sắc nét
        painter.drawEllipse(QRectF(4.5, 4.5, 10.5, 10.5))
        painter.drawLine(QPointF(12.5, 12.5), QPointF(19.5, 19.5))

    elif icon_type == "pin":
        # Thumbtack / Ghim (Trạng thái mở / Unpinned)
        painter.drawLine(QPointF(8, 4.5), QPointF(16, 4.5))
        body = QPainterPath()
        body.moveTo(10, 4.5)
        body.lineTo(10, 7.5)
        body.lineTo(7, 12.5)
        body.lineTo(17, 12.5)
        body.lineTo(14, 7.5)
        body.lineTo(14, 4.5)
        painter.drawPath(body)
        painter.drawLine(QPointF(12, 12.5), QPointF(12, 20.5))

    elif icon_type in ("pinned", "pin_active"):
        # Thumbtack / Ghim (Trạng thái đã ghim - tô đặc màu nhấn)
        painter.drawLine(QPointF(8, 4.5), QPointF(16, 4.5))
        body = QPainterPath()
        body.moveTo(10, 4.5)
        body.lineTo(10, 7.5)
        body.lineTo(7, 12.5)
        body.lineTo(17, 12.5)
        body.lineTo(14, 7.5)
        body.lineTo(14, 4.5)
        body.closeSubpath()
        painter.setBrush(QColor(color_hex))
        painter.drawPath(body)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(12, 12.5), QPointF(12, 20.5))

    elif icon_type in ("settings", "gear", "cog"):
        # Bánh răng cài đặt cơ khí chuẩn xác (6 răng cân xứng)
        import math
        painter.drawEllipse(QRectF(9.5, 9.5, 5, 5))
        gear_path = QPainterPath()
        teeth = 6
        r_inner = 6.0
        r_outer = 9.2
        step = 2 * math.pi / teeth
        w_tooth = step * 0.28
        for i in range(teeth):
            base_a = i * step
            p0 = (12 + r_inner * math.cos(base_a), 12 + r_inner * math.sin(base_a))
            p1 = (12 + r_outer * math.cos(base_a + 0.08), 12 + r_outer * math.sin(base_a + 0.08))
            p2 = (12 + r_outer * math.cos(base_a + w_tooth), 12 + r_outer * math.sin(base_a + w_tooth))
            p3 = (12 + r_inner * math.cos(base_a + w_tooth + 0.08), 12 + r_inner * math.sin(base_a + w_tooth + 0.08))
            p4 = (12 + r_inner * math.cos(base_a + step), 12 + r_inner * math.sin(base_a + step))
            if i == 0:
                gear_path.moveTo(*p0)
            gear_path.lineTo(*p1)
            gear_path.lineTo(*p2)
            gear_path.lineTo(*p3)
            gear_path.lineTo(*p4)
        gear_path.closeSubpath()
        painter.drawPath(gear_path)

    elif icon_type in ("wide", "expand"):
        # Mở rộng (2 mũi tên chéo góc hướng ra ngoài)
        painter.drawLine(QPointF(14.5, 4.5), QPointF(19.5, 4.5))
        painter.drawLine(QPointF(19.5, 4.5), QPointF(19.5, 9.5))
        painter.drawLine(QPointF(13, 11), QPointF(19.5, 4.5))
        painter.drawLine(QPointF(9.5, 19.5), QPointF(4.5, 19.5))
        painter.drawLine(QPointF(4.5, 19.5), QPointF(4.5, 14.5))
        painter.drawLine(QPointF(11, 13), QPointF(4.5, 19.5))

    elif icon_type in ("shrink", "compress", "compact"):
        # Thu nhỏ (2 mũi tên chéo góc hướng vào trong)
        painter.drawLine(QPointF(14, 9), QPointF(19, 9))
        painter.drawLine(QPointF(14, 9), QPointF(14, 4))
        painter.drawLine(QPointF(19.5, 3.5), QPointF(14, 9))
        painter.drawLine(QPointF(10, 15), QPointF(5, 15))
        painter.drawLine(QPointF(10, 15), QPointF(10, 20))
        painter.drawLine(QPointF(4.5, 20.5), QPointF(10, 15))

    elif icon_type in ("collapse", "chevron_right"):
        # Thu gọn / Mũi tên góc phải kép (>>)
        painter.drawLine(QPointF(7.5, 6), QPointF(12.5, 12))
        painter.drawLine(QPointF(12.5, 12), QPointF(7.5, 18))
        painter.drawLine(QPointF(12.5, 6), QPointF(17.5, 12))
        painter.drawLine(QPointF(17.5, 12), QPointF(12.5, 18))

    elif icon_type in ("chevron_left", "expand_rail"):
        # Mở rộng rail / Mũi tên góc trái kép (<<)
        painter.drawLine(QPointF(16.5, 6), QPointF(11.5, 12))
        painter.drawLine(QPointF(11.5, 12), QPointF(16.5, 18))
        painter.drawLine(QPointF(11.5, 6), QPointF(6.5, 12))
        painter.drawLine(QPointF(6.5, 12), QPointF(11.5, 18))

    elif icon_type in ("chevron_down", "arrow_down"):
        # Mũi tên trỏ xuống
        painter.drawLine(QPointF(6, 9), QPointF(12, 15))
        painter.drawLine(QPointF(12, 15), QPointF(18, 9))

    elif icon_type in ("chevron_up", "arrow_up"):
        # Mũi tên trỏ lên
        painter.drawLine(QPointF(6, 15), QPointF(12, 9))
        painter.drawLine(QPointF(12, 9), QPointF(18, 15))

    elif icon_type in ("tune", "sliders"):
        # 3 Cần gạt tinh chỉnh thông số (Sliders)
        painter.drawLine(QPointF(4, 7), QPointF(20, 7))
        painter.drawLine(QPointF(8, 4.5), QPointF(8, 9.5))
        painter.drawLine(QPointF(4, 12), QPointF(20, 12))
        painter.drawLine(QPointF(16, 9.5), QPointF(16, 14.5))
        painter.drawLine(QPointF(4, 17), QPointF(20, 17))
        painter.drawLine(QPointF(11, 14.5), QPointF(11, 19.5))

    elif icon_type in ("more", "dots"):
        # 3 dấu chấm tùy chọn ngang
        painter.setBrush(QColor(color_hex))
        painter.drawEllipse(QRectF(4.5, 10.5, 3, 3))
        painter.drawEllipse(QRectF(10.5, 10.5, 3, 3))
        painter.drawEllipse(QRectF(16.5, 10.5, 3, 3))

    elif icon_type in ("star", "fav", "favorite"):
        # Ngôi sao yêu thích (viền nét)
        import math
        path = QPainterPath()
        r_out = 8.5
        r_in = 3.8
        for i in range(5):
            a_out = -math.pi / 2 + i * (2 * math.pi / 5)
            a_in = a_out + math.pi / 5
            pt_out = QPointF(12 + r_out * math.cos(a_out), 12 + r_out * math.sin(a_out))
            pt_in = QPointF(12 + r_in * math.cos(a_in), 12 + r_in * math.sin(a_in))
            if i == 0:
                path.moveTo(pt_out)
            else:
                path.lineTo(pt_out)
            path.lineTo(pt_in)
        path.closeSubpath()
        painter.drawPath(path)

    elif icon_type in ("star_filled", "fav_filled"):
        # Ngôi sao yêu thích (tô đặc)
        import math
        path = QPainterPath()
        r_out = 8.5
        r_in = 3.8
        for i in range(5):
            a_out = -math.pi / 2 + i * (2 * math.pi / 5)
            a_in = a_out + math.pi / 5
            pt_out = QPointF(12 + r_out * math.cos(a_out), 12 + r_out * math.sin(a_out))
            pt_in = QPointF(12 + r_in * math.cos(a_in), 12 + r_in * math.sin(a_in))
            if i == 0:
                path.moveTo(pt_out)
            else:
                path.lineTo(pt_out)
            path.lineTo(pt_in)
        path.closeSubpath()
        painter.setBrush(QColor(color_hex))
        painter.drawPath(path)

    elif icon_type in ("plus", "add"):
        # Dấu cộng
        painter.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
        painter.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))

    elif icon_type in ("copy", "clipboard"):
        # Sao chép / 2 trang giấy
        painter.drawRoundedRect(QRectF(8, 8, 11, 12), 1.5, 1.5)
        path = QPainterPath()
        path.moveTo(5, 15)
        path.lineTo(5, 5)
        path.lineTo(15, 5)
        painter.drawPath(path)

    elif icon_type in ("paste", "insert_playhead"):
        # Bảng kẹp dán
        painter.drawRoundedRect(QRectF(5, 6, 14, 14), 2, 2)
        painter.drawLine(QPointF(9, 4), QPointF(15, 4))
        painter.drawLine(QPointF(8, 11), QPointF(16, 11))
        painter.drawLine(QPointF(8, 15), QPointF(14, 15))

    elif icon_type in ("evidence", "badge"):
        # Huy hiệu Viral Pack
        path = QPainterPath()
        path.moveTo(5, 4.5)
        path.lineTo(19, 4.5)
        path.lineTo(19, 19.5)
        path.lineTo(12, 16)
        path.lineTo(5, 19.5)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(9, 10.5), QPointF(11.5, 13))
        painter.drawLine(QPointF(11.5, 13), QPointF(15.5, 9))

    elif icon_type in ("music", "bgm"):
        # Âm nhạc / BGM
        painter.drawEllipse(QRectF(5, 13, 4.5, 3.5))
        painter.drawEllipse(QRectF(13.5, 11, 4.5, 3.5))
        painter.drawLine(QPointF(9.5, 14.5), QPointF(9.5, 5.5))
        painter.drawLine(QPointF(18, 12.5), QPointF(18, 4))
        painter.drawLine(QPointF(9.5, 5.5), QPointF(18, 4))
    elif icon_type in ("sound_on", "audio_on", "volume_on"):
        # Loa bật âm thanh: thân loa + phễu + sóng âm
        from PySide6.QtGui import QPolygonF
        horn = QPolygonF([QPointF(4, 9), QPointF(8, 9), QPointF(12, 5), QPointF(12, 19), QPointF(8, 15), QPointF(4, 15)])
        painter.drawPolygon(horn)
        # Sóng âm thanh
        painter.drawArc(QRectF(13, 8, 4, 8), -60 * 16, 120 * 16)
        painter.drawArc(QRectF(15, 5, 6, 14), -60 * 16, 120 * 16)

    elif icon_type in ("sound_off", "audio_off", "volume_off", "mute"):
        # Loa tắt âm thanh: thân loa + dấu gạch chéo
        from PySide6.QtGui import QPolygonF
        horn = QPolygonF([QPointF(4, 9), QPointF(8, 9), QPointF(12, 5), QPointF(12, 19), QPointF(8, 15), QPointF(4, 15)])
        painter.drawPolygon(horn)
        # Gạch chéo
        painter.drawLine(QPointF(14, 8), QPointF(20, 16))
        painter.drawLine(QPointF(20, 8), QPointF(14, 16))

    else:
        # Fallback: Vòng tròn chấm tâm
        painter.drawEllipse(QRectF(5, 5, 14, 14))
        painter.drawPoint(QPointF(12, 12))

    painter.end()
    return pix


def get_vector_icon(icon_type: str, color_hex: str = ThemeColors.TEXT_PRIMARY, size: int = 20) -> QIcon:
    """Trả về đối tượng QIcon tiện lợi cho QPushButton hoặc QAction."""
    return QIcon(create_vector_icon(icon_type, color_hex, size))
