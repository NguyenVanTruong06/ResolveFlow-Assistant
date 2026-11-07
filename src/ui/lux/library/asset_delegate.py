"""
src/ui/lux/library/asset_delegate.py
QStyledItemDelegate rendering modern 16:9 cards with rounded corners, badges, waveforms, and selection states.

Bans internal codes (kinetic, glitch_cyber...) and plus buttons.
Renders procedural waveforms for audio (SFX / BGM).
"""
from __future__ import annotations

import math
from typing import Any, Optional

from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPointF
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QPainterPath,
    QLinearGradient, QFontMetrics, QPixmap
)

from src.ui.lux.library.asset_model import (
    IdRole, NameRole, KindRole, DurationRole, ThumbnailRole,
    IsFavRole, CategoryRole, MotionRole, AssetObjRole
)

_COLOR_ACCENT = QColor("#d94f3d")
_COLOR_BORDER_SELECTED = QColor("#e86352")


class AssetCardDelegate(QStyledItemDelegate):
    """
    High-performance custom delegate for rendering 16:9 asset cards.
    Designed for 60fps virtualization without widget bloat.
    """
    def __init__(self, parent: Optional[Any] = None):
        super().__init__(parent)
        self._font_title = QFont("Segoe UI", 9)
        self._font_title.setWeight(QFont.Weight.Medium)
        self._font_badge = QFont("Segoe UI", 8)
        self._font_badge.setWeight(QFont.Weight.DemiBold)

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:
        w = option.rect.width() if option.rect.width() > 50 else 180
        preview_h = round(w * 9 / 16)
        label_h = 24
        return QSize(w, preview_h + label_h + 8)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Any):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = option.rect
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)

        asset_id = str(index.data(IdRole) or "")
        name = str(index.data(NameRole) or "")
        kind = str(index.data(KindRole) or "Chữ")
        duration = float(index.data(DurationRole) or 0.0)
        category = str(index.data(CategoryRole) or "").lower()
        is_fav = bool(index.data(IsFavRole))
        pixmap = index.data(ThumbnailRole)

        # -------------------------------------------------------------
        # 1. Card Outer Container & Selection Border
        # -------------------------------------------------------------
        card_margin = 2
        card_rect = QRectF(
            rect.x() + card_margin,
            rect.y() + card_margin,
            rect.width() - (card_margin * 2),
            rect.height() - (card_margin * 2)
        )
        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 8.0, 8.0)

        # Fill background
        if is_selected:
            bg_color = QColor("#2a1816")  # Subtle Studio Coral tint
            border_color = _COLOR_BORDER_SELECTED  # Studio Coral 2px
            border_w = 2.0
        elif is_hover:
            bg_color = QColor("#222228")
            border_color = QColor("#52525b")
            border_w = 1.0
        else:
            bg_color = QColor("#141416")  # Dark carbon
            border_color = QColor("#27272a")
            border_w = 1.0

        painter.fillPath(card_path, QBrush(bg_color))
        painter.setPen(QPen(border_color, border_w))
        painter.drawPath(card_path)

        # -------------------------------------------------------------
        # 2. Preview 16:9 Inner Viewport
        # -------------------------------------------------------------
        pad = 4.0
        pw = card_rect.width() - (pad * 2)
        ph = round(pw * 9 / 16)
        preview_rect = QRectF(card_rect.x() + pad, card_rect.y() + pad, pw, ph)

        preview_path = QPainterPath()
        preview_path.addRoundedRect(preview_rect, 6.0, 6.0)

        painter.save()
        painter.setClipPath(preview_path)

        # Background of preview area
        painter.fillRect(preview_rect, QColor("#09090b"))

        is_audio = category in ("sfx", "audio", "sound", "bgm", "nhac_nen")
        is_text = category in ("text", "title", "chu", "subtitle")
        asset_obj = index.data(AssetObjRole)

        if is_audio:
            # Render Procedural Waveform
            self._draw_procedural_waveform(painter, preview_rect, asset_id, category)
        elif is_text:
            # Render Rich Text Preset Typography Preview
            from src.ui.lux.library.text_preview_generator import draw_text_preset_preview
            try:
                draw_text_preset_preview(painter, preview_rect, asset_obj if asset_obj is not None else asset_id, name)
            except TypeError:
                draw_text_preset_preview(painter, preview_rect, asset_obj if asset_obj is not None else asset_id)
        elif pixmap and isinstance(pixmap, QPixmap) and not pixmap.isNull():
            # Draw real loaded thumbnail (scaled to cover 16:9 without stretching)
            scaled = pixmap.scaled(
                int(preview_rect.width()), int(preview_rect.height()),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            sx = int(preview_rect.x() + (preview_rect.width() - scaled.width()) / 2)
            sy = int(preview_rect.y() + (preview_rect.height() - scaled.height()) / 2)
            painter.drawPixmap(sx, sy, scaled)
        else:
            # Fallback procedural canvas
            self._draw_procedural_canvas(painter, preview_rect, name, category, asset_id, asset_obj=asset_obj)

        painter.restore()

        # -------------------------------------------------------------
        # 3. Badges: Kind (Top-Left), Duration (Bottom-Right), Fav (Top-Right)
        # -------------------------------------------------------------
        painter.setFont(self._font_badge)

        # Kind Badge (Top-Left)
        badge_pad_x, badge_pad_y = 6, 2
        fm_badge = QFontMetrics(self._font_badge)
        kw = fm_badge.horizontalAdvance(kind) + (badge_pad_x * 2)
        kh = fm_badge.height() + (badge_pad_y * 2)
        k_rect = QRectF(preview_rect.x() + 6, preview_rect.y() + 6, kw, kh)

        k_path = QPainterPath()
        k_path.addRoundedRect(k_rect, 4.0, 4.0)
        painter.fillPath(k_path, QColor(0, 0, 0, 180))
        painter.setPen(QColor("#f4f4f5"))
        painter.drawText(k_rect, Qt.AlignmentFlag.AlignCenter, kind)

        # Duration Badge (Bottom-Right)
        if duration > 0.0:
            dur_str = f"{duration:.1f}s" if duration < 10.0 else f"{int(round(duration))}s"
            dw = fm_badge.horizontalAdvance(dur_str) + 10
            dh = fm_badge.height() + 4
            d_rect = QRectF(preview_rect.right() - dw - 6, preview_rect.bottom() - dh - 6, dw, dh)

            d_path = QPainterPath()
            d_path.addRoundedRect(d_rect, 4.0, 4.0)
            painter.fillPath(d_path, QColor(0, 0, 0, 190))
            painter.setPen(QColor("#e4e4e7"))
            painter.drawText(d_rect, Qt.AlignmentFlag.AlignCenter, dur_str)

        # Favorite Heart (Top-Right)
        if is_fav:
            fav_rect = QRectF(preview_rect.right() - 20, preview_rect.y() + 6, 16, 16)
            painter.setPen(QColor("#ef4444"))
            painter.drawText(fav_rect, Qt.AlignmentFlag.AlignCenter, "♥")

        # -------------------------------------------------------------
        # 4. Single-line Elided Asset Name
        # -------------------------------------------------------------
        painter.setFont(self._font_title)
        fm_title = QFontMetrics(self._font_title)
        label_rect = QRectF(
            card_rect.x() + 6,
            preview_rect.bottom() + 4,
            card_rect.width() - 12,
            card_rect.bottom() - preview_rect.bottom() - 6
        )

        elided_name = fm_title.elidedText(name, Qt.TextElideMode.ElideRight, int(label_rect.width()))
        painter.setPen(QColor("#ffffff" if is_selected else "#e4e4e7"))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided_name)

        painter.restore()

    def _draw_procedural_waveform(self, painter: QPainter, rect: QRectF, asset_id: str, category: str):
        """Draws a crisp audio waveform using pseudo-random seed from asset id."""
        seed = sum(ord(c) for c in asset_id) if asset_id else 42
        color = QColor("#22d3ee") if category in ("sfx", "audio", "sound") else QColor("#c084fc")

        num_bars = 28
        bar_w = (rect.width() - 16) / num_bars
        center_y = rect.y() + (rect.height() / 2)
        max_h = (rect.height() - 16) / 2

        x = seed * 9301 % 233280
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))

        for i in range(num_bars):
            x = (x * 9301 + 49297) % 233280
            val = 0.2 + (x / 233280.0) * 0.8
            bh = max_h * val
            bx = rect.x() + 8 + (i * bar_w)
            by = center_y - bh
            painter.drawRoundedRect(QRectF(bx, by, max(2.0, bar_w - 2.0), bh * 2), 1.0, 1.0)

    def _draw_procedural_canvas(self, painter: QPainter, rect: QRectF, name: str, category: str, asset_id: str = "", asset_obj: Any = None):
        """Draws procedural placeholder canvas for templates without thumbnail."""
        if category in ("text", "title", "chu", "subtitle"):
            from src.ui.lux.library.text_preview_generator import draw_text_preset_preview
            try:
                draw_text_preset_preview(painter, rect, asset_obj if asset_obj is not None else asset_id, name)
            except TypeError:
                draw_text_preset_preview(painter, rect, asset_obj if asset_obj is not None else asset_id)
            return

        if category in ("trans", "transition", "transitions"):
            try:
                from src.ui.lux.library.transition_preview_generator import draw_transition_frame
                draw_transition_frame(
                    painter,
                    rect,
                    asset_obj if asset_obj is not None else name,
                    progress=0.5,
                    show_hud=True,
                )
                return
            except Exception:
                pass

        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if category in ("trans", "transition", "transitions"):
            grad.setColorAt(0.0, QColor("#2a1816"))
            grad.setColorAt(1.0, QColor("#111827"))
        elif category in ("lut", "color"):
            grad.setColorAt(0.0, QColor("#064e3b"))
            grad.setColorAt(1.0, QColor("#022c22"))
        else:
            grad.setColorAt(0.0, QColor("#1c1917"))
            grad.setColorAt(1.0, QColor("#09090b"))

        painter.fillRect(rect, grad)

        if category in ("trans", "transition", "transitions"):
            painter.setFont(QFont("Segoe UI Emoji", 26))
            painter.setPen(QColor(232, 99, 82, 180))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "🎬")
        else:
            # Center typography hint
            painter.setPen(QColor(255, 255, 255, 120))
            f = QFont("Segoe UI", 10)
            f.setBold(True)
            painter.setFont(f)
            fm = QFontMetrics(f)
            disp_txt = fm.elidedText(name, Qt.TextElideMode.ElideRight, int(rect.width() - 20))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, disp_txt)
