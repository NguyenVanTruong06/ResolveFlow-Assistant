"""
src/ui/lux/library/text_preview_generator.py
Bộ máy render trực quan các phong cách chữ nghệ thuật (Text Preset Typography Previews).
Tái hiện trung thực bản sắc nghệ thuật riêng của từng Preset:
- Alex Hormozi (Bold Uppercase + Yellow Pop)
- Karaoke Pop (White text + Lime Green highlight)
- Box Highlight (Red badge pill)
- Glow Neon & Neon Pulse (Cyan/Pink neon glow)
- VHS Retro (Green PLAY SP + Yellow retro monospace)
- Paper Cutout (Scrapbook paper tiles)
- RGB Glitch (Chromatic aberration separation)
- Gradient Fill (Sunset orange-rose-purple gradient)
- MrBeast Impact (Bold gold impact text)
- Apple Keynote (San Francisco minimal clean)
- True Crime (Serif noir blood red)
- Comic Manga (Action burst pill)
- Luxury Gold (Gold metallic serif)
- SChannel / Khoai Lang Thang / VTV Phim / Showbiz TikTok...
"""

from typing import Dict, Optional
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath,
    QLinearGradient, QFontMetrics, QPixmap
)

# Cache lưu trữ các Pixmap xem trước đã render
_PRESET_PIXMAP_CACHE: Dict[str, QPixmap] = {}


def draw_text_preset_preview(
    painter: QPainter,
    rect: QRectF,
    preset_id: str,
    name: str = "",
    custom_text: Optional[str] = None
):
    """
    Vẽ phong cách chữ nghệ thuật trực tiếp lên canvas QPainter.
    Tự động co giãn theo kích thước khung rect (16:9).
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    pid = (preset_id or "").lower()
    rw = rect.width()
    rh = rect.height()
    cx = rect.x() + (rw / 2.0)
    cy = rect.y() + (rh / 2.0)

    # -------------------------------------------------------------
    # 1. HÌNH ẢNH & MÀU NỀN THEO PHONG CÁCH
    # -------------------------------------------------------------
    if "paper" in pid:
        # Nền giấy Scrapbook màu be thủ công
        painter.fillRect(rect, QColor("#cbb48a"))
        painter.setPen(QPen(QColor("#a98d5f"), 1))
        painter.drawRect(rect)
    elif "neon" in pid or "glow" in pid:
        # Nền xanh thẫm dạ quang neon
        grad_bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad_bg.setColorAt(0.0, QColor("#051622"))
        grad_bg.setColorAt(1.0, QColor("#082338"))
        painter.fillRect(rect, grad_bg)
        painter.setPen(QPen(QColor("#0891b2"), 1))
        painter.drawRect(rect)
    elif "vhs" in pid or "retro" in pid or "vtv" in pid:
        # Nền màn hình đen hoài cổ
        painter.fillRect(rect, QColor("#0c0a09"))
        painter.setPen(QPen(QColor("#292524"), 1))
        painter.drawRect(rect)
    elif "gold" in pid or "luxury" in pid:
        # Nền đen hoàng gia vân vàng
        grad_bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad_bg.setColorAt(0.0, QColor("#1c1308"))
        grad_bg.setColorAt(1.0, QColor("#0c0804"))
        painter.fillRect(rect, grad_bg)
        painter.setPen(QPen(QColor("#d97706"), 1))
        painter.drawRect(rect)
    elif "gradient" in pid:
        # Nền tím hoàng hôn
        grad_bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad_bg.setColorAt(0.0, QColor("#180d2a"))
        grad_bg.setColorAt(1.0, QColor("#0d0914"))
        painter.fillRect(rect, grad_bg)
        painter.setPen(QPen(QColor("#7c3aed"), 1))
        painter.drawRect(rect)
    elif "crime" in pid:
        # Nền hồ sơ vụ án đen đỏ
        painter.fillRect(rect, QColor("#09090b"))
        painter.setPen(QPen(QColor("#7f1d1d"), 1))
        painter.drawRect(rect)
    elif "showbiz" in pid or "drama" in pid:
        # Nền drama căng đét mận chín
        painter.fillRect(rect, QColor("#240717"))
        painter.setPen(QPen(QColor("#f43f5e"), 1))
        painter.drawRect(rect)
    elif "khoai_lang" in pid:
        # Nền ấm áp chữa lành
        painter.fillRect(rect, QColor("#221b14"))
        painter.setPen(QPen(QColor("#78350f"), 1))
        painter.drawRect(rect)
    else:
        # Nền đen carbon sang trọng tiêu chuẩn
        painter.fillRect(rect, QColor("#09090b"))
        painter.setPen(QPen(QColor("#1f1f23"), 1))
        painter.drawRect(rect)

    # -------------------------------------------------------------
    # 2. VẼ TYPOGRAPHY ĐẶC TRƯNG TỪNG PRESET
    # -------------------------------------------------------------
    base_font_sz = max(8, int(rh * 0.16))

    if "hormozi" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)
        fm = QFontMetrics(f)
        lh = fm.height()
        cap_h = fm.capHeight()

        if custom_text and len(custom_text) > 12:
            # Tự động co chữ cho nội dung tùy biến dài
            words = custom_text.split()
            mid = len(words) // 2
            t1 = " ".join(words[:mid]) if mid > 0 else custom_text
            t2 = " ".join(words[mid:]) if mid > 0 else ""
        else:
            t1 = "TIỀN ĐẾN"
            t2 = custom_text or "TỪ ĐÂU?"

        if t2:
            w1 = fm.horizontalAdvance(t1)
            y1 = cy - (lh * 0.45) + cap_h
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QPointF(cx - (w1 / 2.0), y1), t1)

            # Dòng 2
            if "TỪ " in t2:
                parts = t2.split("TỪ ")
                w_tu = fm.horizontalAdvance("TỪ ")
                w_rest = fm.horizontalAdvance(parts[1])
                start_x2 = cx - ((w_tu + w_rest) / 2.0)
                y2 = cy + (lh * 0.55) + (cap_h / 2.0)

                painter.setPen(QColor("#ffffff"))
                painter.drawText(QPointF(start_x2, y2), "TỪ ")
                painter.setPen(QColor("#facc15"))
                painter.drawText(QPointF(start_x2 + w_tu, y2), parts[1])
            else:
                w2 = fm.horizontalAdvance(t2)
                y2 = cy + (lh * 0.55) + (cap_h / 2.0)
                painter.setPen(QColor("#facc15"))
                painter.drawText(QPointF(cx - (w2 / 2.0), y2), t2)
        else:
            w1 = fm.horizontalAdvance(t1)
            painter.setPen(QColor("#facc15"))
            painter.drawText(QPointF(cx - (w1 / 2.0), cy + (cap_h / 2.0)), t1)

    elif "karaoke" in pid:
        f = QFont("Arial", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "dính "
        t_hl = custom_text or "mưa"
        t2 = " rồi"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        whl = fm.horizontalAdvance(t_hl)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + whl + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        # Từ highlight xanh neon karaoke
        painter.setPen(QColor("#a3e635"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t_hl)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(start_x + w1 + whl, baseline_y), t2)

    elif "box_highlight" in pid or "box" in pid:
        f = QFont("Arial", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "100% "
        t_box = custom_text or "sức khỏe"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        wb = fm.horizontalAdvance(t_box) + 12
        hb = fm.height() + 4
        total_w = w1 + wb
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        # Hộp đỏ viền bo góc
        box_rect = QRectF(start_x + w1, cy - (hb / 2.0), wb, hb)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#ef4444")))
        painter.drawRoundedRect(box_rect, 4.0, 4.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(box_rect, Qt.AlignmentFlag.AlignCenter, t_box)

    elif "highlighter" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        txt = custom_text or "DẠ QUANG"
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(txt)
        th = fm.height()

        # Dải màu vàng dạ quang quét nền
        hw_rect = QRectF(cx - (tw / 2.0) - 8, cy - (th / 4.0), tw + 16, th * 0.7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(234, 179, 8, 220)))
        painter.drawRoundedRect(hw_rect, 3.0, 3.0)

        # Chữ đen nổi trên dải vàng
        painter.setPen(QColor("#000000"))
        painter.drawText(QRectF(cx - tw, cy - (th / 2.0), tw * 2, th), Qt.AlignmentFlag.AlignCenter, txt)

    elif "glow_neon" in pid or "neon" in pid:
        f = QFont("Segoe UI", base_font_sz + 1)
        f.setBold(True)
        painter.setFont(f)

        txt = custom_text or "NEON GLOW"
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(txt) + 20
        th = fm.height() + 8
        n_rect = QRectF(cx - (tw / 2.0), cy - (th / 2.0), tw, th)

        # Viền neon phát sáng
        painter.setPen(QPen(QColor(6, 182, 212, 100), 4))
        painter.setBrush(QBrush(QColor(6, 182, 212, 40)))
        painter.drawRoundedRect(n_rect, 6.0, 6.0)

        painter.setPen(QPen(QColor("#06b6d4"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(n_rect, 6.0, 6.0)

        painter.setPen(QColor("#ecfeff"))
        painter.drawText(n_rect, Qt.AlignmentFlag.AlignCenter, txt)

    elif "vhs" in pid or "retro" in pid:
        # Header play nhỏ
        f_sub = QFont("Consolas", max(7, int(base_font_sz * 0.65)))
        painter.setFont(f_sub)
        painter.setPen(QColor("#22c55e"))
        painter.drawText(QRectF(rect.x() + 8, rect.y() + 6, rw, 14), Qt.AlignmentFlag.AlignLeft, "▶ PLAY SP 0:12")

        # Chữ vàng retro
        f = QFont("Consolas", base_font_sz + 2)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#fff59d"))
        txt = custom_text or "HÈ 1999"
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)

    elif "paper" in pid:
        letters = list((custom_text or "VLOG")[:6])
        tile_size = int(rh * 0.38)
        spacing = 4
        total_w = len(letters) * tile_size + (len(letters) - 1) * spacing
        start_x = cx - (total_w / 2.0)
        tile_y = cy - (tile_size / 2.0)

        colors = ["#ffffff", "#fef3c7", "#fecaca", "#dbeafe", "#e0e7ff", "#f3e8ff"]
        f = QFont("Georgia", int(tile_size * 0.65))
        f.setBold(True)
        painter.setFont(f)

        for i, char in enumerate(letters):
            tx = start_x + (i * (tile_size + spacing))
            t_rect = QRectF(tx, tile_y, tile_size, tile_size)

            # Đổ bóng nhẹ
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 90)))
            painter.drawRoundedRect(QRectF(tx + 2, tile_y + 2, tile_size, tile_size), 3.0, 3.0)

            # Giấy cắt
            painter.setBrush(QBrush(QColor(colors[i % len(colors)])))
            painter.drawRoundedRect(t_rect, 3.0, 3.0)

            # Chữ đen
            painter.setPen(QColor("#111111"))
            painter.drawText(t_rect, Qt.AlignmentFlag.AlignCenter, char)

    elif "glitch" in pid:
        f = QFont("Arial Black", base_font_sz + 1)
        f.setBold(True)
        painter.setFont(f)
        txt = custom_text or "GLITCH"

        # Đỏ tách âm (-2px)
        painter.setPen(QColor("#ff0055"))
        painter.drawText(QRectF(rect.x() - 2, rect.y(), rw, rh), Qt.AlignmentFlag.AlignCenter, txt)

        # Xanh tách dương (+2px)
        painter.setPen(QColor("#00e5ff"))
        painter.drawText(QRectF(rect.x() + 2, rect.y(), rw, rh), Qt.AlignmentFlag.AlignCenter, txt)

        # Trắng trung tâm
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)

    elif "gradient" in pid:
        f = QFont("Arial Black", base_font_sz + 1)
        f.setBold(True)
        painter.setFont(f)
        txt = custom_text or "HOÀNG HÔN"

        grad = QLinearGradient(QPointF(cx - 60, cy), QPointF(cx + 60, cy))
        grad.setColorAt(0.0, QColor("#fb923c"))
        grad.setColorAt(0.5, QColor("#f43f5e"))
        grad.setColorAt(1.0, QColor("#a855f7"))

        painter.setPen(QPen(QBrush(grad), 1))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)

    elif "mrbeast" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "1 TRIỆU "
        t2 = custom_text or "ĐÔ LA!"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        painter.setPen(QColor("#ffe600"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t2)

    elif "apple" in pid or "keynote" in pid:
        f = QFont("Segoe UI", base_font_sz)
        f.setWeight(QFont.Weight.DemiBold)
        painter.setFont(f)
        painter.setPen(QColor("#f8fafc"))
        painter.drawText(QRectF(rect.x(), cy - 16, rw, 20), Qt.AlignmentFlag.AlignCenter, custom_text or "Think Different")

        f_sub = QFont("Segoe UI", max(7, int(base_font_sz * 0.65)))
        painter.setFont(f_sub)
        painter.setPen(QColor("#38bdf8"))
        painter.drawText(QRectF(rect.x(), cy + 4, rw, 16), Qt.AlignmentFlag.AlignCenter, "Apple Keynote")

    elif "crime" in pid:
        f = QFont("Georgia", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "HỒ SƠ "
        t2 = custom_text or "VỤ ÁN"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0) - 6

        painter.setPen(QColor("#f8fafc"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        painter.setPen(QColor("#ef4444"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t2)

        f_sub = QFont("Segoe UI", max(7, int(base_font_sz * 0.6)))
        painter.setFont(f_sub)
        painter.setPen(QColor("#71717a"))
        painter.drawText(QRectF(rect.x(), cy + 6, rw, 14), Qt.AlignmentFlag.AlignCenter, "01:23:45 · BẰNG CHỨNG")

    elif "comic" in pid or "manga" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t_boom = "BOOM!"
        t_act = custom_text or "ACTION"
        fm = QFontMetrics(f)
        wb = fm.horizontalAdvance(t_boom) + 10
        wa = fm.horizontalAdvance(t_act)
        hb = fm.height() + 2
        total_w = wb + wa + 6
        start_x = cx - (total_w / 2.0)

        # Nền đen cho BOOM
        b_rect = QRectF(start_x, cy - (hb / 2.0), wb, hb)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#000000")))
        painter.drawRoundedRect(b_rect, 3.0, 3.0)

        painter.setPen(QColor("#fff176"))
        painter.drawText(b_rect, Qt.AlignmentFlag.AlignCenter, t_boom)

        # Chữ hồng ACTION
        painter.setPen(QColor("#ff0055"))
        painter.drawText(QPointF(start_x + wb + 6, cy + (fm.capHeight() / 2.0)), t_act)

    elif "gold" in pid or "luxury" in pid:
        f = QFont("Georgia", base_font_sz)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#fef3c7"))
        painter.drawText(QRectF(rect.x(), cy - 14, rw, 18), Qt.AlignmentFlag.AlignCenter, custom_text or "LUXURY GOLD")

        f_sub = QFont("Segoe UI", max(7, int(base_font_sz * 0.55)))
        f_sub.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
        painter.setFont(f_sub)
        painter.setPen(QColor("#d97706"))
        painter.drawText(QRectF(rect.x(), cy + 4, rw, 14), Qt.AlignmentFlag.AlignCenter, "COLLECTION")

    elif "schannel" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "ĐẬP HỘP "
        t2 = custom_text or "SIÊU PHẨM"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        painter.setPen(QColor("#facc15"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t2)

    elif "khoai_lang" in pid:
        f = QFont("Arial", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "Hành Trình "
        t2 = custom_text or "Chữa Lành"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0) - 6

        painter.setPen(QColor("#fef3c7"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        painter.setPen(QColor("#f59e0b"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t2)

        f_sub = QFont("Segoe UI", max(7, int(base_font_sz * 0.6)))
        painter.setFont(f_sub)
        painter.setPen(QColor("#d4d4d8"))
        painter.drawText(QRectF(rect.x(), cy + 6, rw, 14), Qt.AlignmentFlag.AlignCenter, "Bình Yên Mộc Mạc")

    elif "vtv" in pid:
        f = QFont("Arial", base_font_sz + 1)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor("#ffeb3b"))
        painter.drawText(QRectF(rect.x(), cy - 14, rw, 18), Qt.AlignmentFlag.AlignCenter, "VTV PHIM TRUYỆN")

        f_sub = QFont("Arial", max(7, int(base_font_sz * 0.6)))
        f_sub.setBold(True)
        painter.setFont(f_sub)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(rect.x(), cy + 4, rw, 14), Qt.AlignmentFlag.AlignCenter, custom_text or "HÀ NỘI MÙA THU")

    elif "showbiz" in pid:
        f = QFont("Arial Black", base_font_sz)
        f.setBold(True)
        painter.setFont(f)

        t1 = "DRAMA "
        t2 = custom_text or "CĂNG ĐÉT!"
        fm = QFontMetrics(f)
        w1 = fm.horizontalAdvance(t1)
        w2 = fm.horizontalAdvance(t2)
        total_w = w1 + w2
        start_x = cx - (total_w / 2.0)
        baseline_y = cy + (fm.capHeight() / 2.0)

        painter.setPen(QColor("#ff0055"))
        painter.drawText(QPointF(start_x, baseline_y), t1)

        painter.setPen(QColor("#00ffff"))
        painter.drawText(QPointF(start_x + w1, baseline_y), t2)

    elif "podcast" in pid or "pill" in pid:
        f = QFont("Arial", base_font_sz)
        f.setBold(True)
        painter.setFont(f)
        txt = custom_text or "The Podcast Talk"
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(txt) + 16
        th = fm.height() + 6
        p_rect = QRectF(cx - (tw / 2.0), cy - (th / 2.0), tw, th)

        painter.setPen(QPen(QColor("#334155"), 1))
        painter.setBrush(QBrush(QColor("#0f172a")))
        painter.drawRoundedRect(p_rect, th / 2.0, th / 2.0)

        painter.setPen(QColor("#ffffff"))
        painter.drawText(p_rect, Qt.AlignmentFlag.AlignCenter, txt)

    else:
        # Fallback hiển thị tên đẹp với gradient
        f = QFont("Segoe UI", base_font_sz)
        f.setBold(True)
        painter.setFont(f)
        fm = QFontMetrics(f)
        disp_txt = custom_text or name.split("(")[0].strip() or "Text Preset"
        disp_txt = fm.elidedText(disp_txt, Qt.TextElideMode.ElideRight, int(rw - 20))

        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, disp_txt)

    painter.restore()


def get_text_preset_pixmap(
    preset_id: str,
    name: str = "",
    custom_text: Optional[str] = None,
    width: int = 320,
    height: int = 180
) -> QPixmap:
    """
    Tạo hoặc lấy từ cache QPixmap xem trước sắc nét tỉ lệ 16:9 của Preset.
    """
    cache_key = f"{preset_id}_{custom_text or ''}_{width}_{height}"
    if cache_key in _PRESET_PIXMAP_CACHE:
        return _PRESET_PIXMAP_CACHE[cache_key]

    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    rect = QRectF(0, 0, width, height)
    draw_text_preset_preview(painter, rect, preset_id, name, custom_text)
    painter.end()

    _PRESET_PIXMAP_CACHE[cache_key] = pixmap
    return pixmap
