#!/usr/bin/env python3
"""
tools/generate_brand_icons.py
Sinh biểu tượng thương hiệu chính thức cho ChunDVC (App Icon, Desktop Icon, Tray Icon, Windows ICO).
Thiết kế theo ngôn ngữ DaVinci Resolve Matte Graphite & Studio Coral / Cyber Violet.
"""

import os
import sys
from pathlib import Path
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QImage, QPainter, QColor, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath
)


def draw_chundvc_icon(size: int = 1024) -> QImage:
    """Vẽ biểu tượng ChunDVC vector độ nét siêu cao 1024x1024."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    scale = size / 1024.0
    p.scale(scale, scale)

    # -------------------------------------------------------------
    # 1. BÓNG ĐỔ SQUIRCLE NGOÀI (SOFT DROP SHADOW)
    # -------------------------------------------------------------
    for i in range(12, 0, -2):
        alpha = int(18 * (1.0 - i / 12.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, alpha))
        p.drawRoundedRect(QRectF(56 - i, 68 + i, 912 + i * 2, 912), 210, 210)

    # -------------------------------------------------------------
    # 2. KHUNG SQUIRCLE CHÍNH (OBSIDIAN DARK BACKGROUND)
    # -------------------------------------------------------------
    squircle_rect = QRectF(64, 64, 896, 896)
    radius = 200

    bg_grad = QLinearGradient(QPointF(200, 64), QPointF(824, 960))
    bg_grad.setColorAt(0.0, QColor("#181926"))  # Deep midnight violet graphite
    bg_grad.setColorAt(0.4, QColor("#12131d"))
    bg_grad.setColorAt(1.0, QColor("#08090e"))  # Pure matte obsidian

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg_grad))
    p.drawRoundedRect(squircle_rect, radius, radius)

    # -------------------------------------------------------------
    # 3. QUẦNG SÁNG NEON NỀN (AMBIENT COLOR BLOOM GLOW)
    # -------------------------------------------------------------
    glow_grad = QRadialGradient(QPointF(512, 512), 420)
    glow_grad.setColorAt(0.0, QColor(124, 58, 237, 85))   # Electric violet center
    glow_grad.setColorAt(0.45, QColor(244, 63, 94, 45))  # Studio coral middle
    glow_grad.setColorAt(0.75, QColor(6, 182, 212, 20))  # Cyan fringe
    glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))

    p.setBrush(QBrush(glow_grad))
    p.drawRoundedRect(squircle_rect, radius, radius)

    # -------------------------------------------------------------
    # 4. VIỀN KIM LOẠI SẮC NÉT (METALLIC INSET RIM)
    # -------------------------------------------------------------
    rim_grad = QLinearGradient(QPointF(64, 64), QPointF(960, 960))
    rim_grad.setColorAt(0.0, QColor("#818cf8"))   # Indigo highlight top-left
    rim_grad.setColorAt(0.3, QColor("#4f46e5"))
    rim_grad.setColorAt(0.7, QColor("#27273a"))
    rim_grad.setColorAt(1.0, QColor("#1e1b4b"))   # Deep rim bottom-right

    rim_pen = QPen(QBrush(rim_grad), 5.0)
    p.setPen(rim_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(66.5, 66.5, 891, 891), radius - 2, radius - 2)

    # -------------------------------------------------------------
    # 5. BIỂU TƯỢNG TRUNG TÂM: CHỮ "C" CÁCH ĐIỆU (CHUN)
    # -------------------------------------------------------------
    # Vẽ chữ C mềm mại, hiện đại với đường cắt góc 45 độ
    c_path = QPainterPath()
    # Outer arc
    c_rect_outer = QRectF(212, 212, 600, 600)
    # Inner arc
    c_rect_inner = QRectF(332, 332, 360, 360)

    # Bắt đầu từ góc 48 độ (trên bên phải)
    c_path.arcMoveTo(c_rect_outer, 44)
    c_path.arcTo(c_rect_outer, 44, 272)    # Quét ngược chiều kim đồng hồ quanh 272 độ
    # Đi vào mép trong dưới
    c_path.lineTo(QPointF(600, 680))
    c_path.arcTo(c_rect_inner, 318, -276)
    c_path.lineTo(QPointF(680, 310))
    c_path.closeSubpath()

    c_grad = QLinearGradient(QPointF(240, 200), QPointF(780, 820))
    c_grad.setColorAt(0.0, QColor("#ff4757"))  # Studio Coral neon
    c_grad.setColorAt(0.35, QColor("#ff6b81")) # Vibrant pink
    c_grad.setColorAt(0.7, QColor("#8a2be2"))  # Electric violet
    c_grad.setColorAt(1.0, QColor("#4338ca"))  # Deep indigo

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c_grad))
    p.drawPath(c_path)

    # -------------------------------------------------------------
    # 6. TIA CHỚP AI / LƯỠI DAO ROUGH CUT (LIGHTNING BOLT & BLADE)
    # -------------------------------------------------------------
    # Tia chớp nằm giữa chữ C tượng trưng cho AI Tốc độ cao & Cắt ghép
    bolt_path = QPainterPath()
    bolt_pts = [
        QPointF(535, 250),   # Đỉnh tia chớp
        QPointF(395, 520),   # Gập trong trái
        QPointF(495, 520),   # Ngấn gập ngang
        QPointF(440, 775),   # Mũi nhọn đáy
        QPointF(640, 465),   # Mũi nhọn phải
        QPointF(540, 465),   # Ngấn gập ngang trên
    ]
    bolt_path.moveTo(bolt_pts[0])
    for pt in bolt_pts[1:]:
        bolt_path.lineTo(pt)
    bolt_path.closeSubpath()

    bolt_grad = QLinearGradient(QPointF(400, 250), QPointF(640, 780))
    bolt_grad.setColorAt(0.0, QColor("#ffffff"))  # Ánh chớp trắng rực rỡ
    bolt_grad.setColorAt(0.2, QColor("#fed7aa"))  # Hổ phách sáng
    bolt_grad.setColorAt(0.55, QColor("#fb7185")) # Coral sáng
    bolt_grad.setColorAt(1.0, QColor("#a855f7"))  # Tím điện quang

    # Viền tia chớp tương phản
    p.setPen(QPen(QColor("#ffffff"), 3.0))
    p.setBrush(QBrush(bolt_grad))
    p.drawPath(bolt_path)

    # -------------------------------------------------------------
    # 7. BA CHẤM MÀU ĐẶC TRƯNG DAVINCI RESOLVE (COLOR WHEEL DOTS)
    # -------------------------------------------------------------
    # 3 chấm màu ở góc trên bên phải: Red (Lift), Green (Gamma), Blue (Gain)
    dot_y = 175
    dots = [
        (665, dot_y, QColor("#ef4444")),  # DaVinci Red
        (710, dot_y, QColor("#10b981")),  # DaVinci Green
        (755, dot_y, QColor("#06b6d4")),  # DaVinci Blue
    ]
    for dx, dy, dcolor in dots:
        p.setPen(Qt.PenStyle.NoPen)
        # Glow
        p.setBrush(QColor(dcolor.red(), dcolor.green(), dcolor.blue(), 60))
        p.drawEllipse(QPointF(dx, dy), 16, 16)
        # Dot core
        p.setBrush(QBrush(dcolor))
        p.drawEllipse(QPointF(dx, dy), 10, 10)
        # Specular glint
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(QPointF(dx - 3, dy - 3), 3.5, 3.5)

    # -------------------------------------------------------------
    # 8. NGÔI SAO SÁNG AI (AI SPARKLE / GLINT)
    # -------------------------------------------------------------
    sparkle_center = QPointF(535, 250)
    sparkle_path = QPainterPath()
    sc_x, sc_y = sparkle_center.x(), sparkle_center.y()
    sw, sh = 28, 5
    # Ngang
    sparkle_path.addEllipse(QRectF(sc_x - sw, sc_y - sh / 2, sw * 2, sh))
    # Dọc
    sparkle_path.addEllipse(QRectF(sc_x - sh / 2, sc_y - sw, sh, sw * 2))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 230))
    p.drawPath(sparkle_path)
    p.drawEllipse(sparkle_center, 6, 6)

    # -------------------------------------------------------------
    # 9. BÓNG KÍNH PHẢN CHIẾU ĐỈNH (SPECULAR GLASS REFLECTION)
    # -------------------------------------------------------------
    glass_path = QPainterPath()
    glass_path.moveTo(64 + radius, 64)
    glass_path.lineTo(960 - radius, 64)
    glass_path.quadTo(960, 64, 960, 64 + radius)
    glass_path.lineTo(960, 360)
    glass_path.cubicTo(QPointF(700, 420), QPointF(320, 240), QPointF(64, 460))
    glass_path.lineTo(64, 64 + radius)
    glass_path.quadTo(64, 64, 64 + radius, 64)
    glass_path.closeSubpath()

    glass_grad = QLinearGradient(QPointF(512, 64), QPointF(512, 460))
    glass_grad.setColorAt(0.0, QColor(255, 255, 255, 38))
    glass_grad.setColorAt(1.0, QColor(255, 255, 255, 0))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glass_grad))
    p.drawPath(glass_path)

    p.end()
    return img


def generate_all_icons():
    output_dir = PROJECT_ROOT / "assets" / "branding"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("🎨 Đang sinh biểu tượng ChunDVC Master (1024x1024)...")
    master_1024 = draw_chundvc_icon(1024)

    sizes = [512, 256, 128, 64, 48, 32, 16]
    pil_images = []

    for sz in sizes:
        scaled = master_1024.scaled(
            sz, sz,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        png_path = output_dir / f"chundvc_{sz}.png"
        scaled.save(str(png_path), "PNG")
        print(f"  ✓ Đã xuất: {png_path.name} ({sz}x{sz})")

        # Đọc vào Pillow để chuẩn bị cho .ico
        pil_img = Image.open(str(png_path))
        pil_images.append(pil_img)

    # Tạo alias chính: assets/icon.png và assets/chundvc.png
    (output_dir / "chundvc_512.png").replace(output_dir / "chundvc.png")
    master_1024.scaled(512, 512, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).save(str(output_dir / "chundvc_512.png"), "PNG")

    # Copy ra assets/icon.png để pyinstaller và app dùng
    shutil_copy = master_1024.scaled(512, 512, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    shutil_copy.save(str(PROJECT_ROOT / "assets" / "icon.png"), "PNG")
    print(f"  ✓ Đã đồng bộ: assets/icon.png & assets/branding/chundvc.png")

    # Tạo file Windows ICO đa phân giải (256, 128, 64, 48, 32, 16)
    ico_path = output_dir / "chundvc.ico"
    # Sắp xếp các size theo chuẩn ICO (từ lớn tới nhỏ)
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    master_pil = Image.open(str(output_dir / "chundvc_512.png"))
    master_pil.save(
        str(ico_path),
        format="ICO",
        sizes=ico_sizes
    )
    # Copy ico ra assets/icon.ico
    master_pil.save(
        str(PROJECT_ROOT / "assets" / "icon.ico"),
        format="ICO",
        sizes=ico_sizes
    )
    print(f"  ✓ Đã tạo Windows Icon: {ico_path.name} (chứa 6 độ phân giải)")
    print(f"\n🎉 Hoàn tất sinh bộ icon thương hiệu ChunDVC!")


if __name__ == "__main__":
    generate_all_icons()
