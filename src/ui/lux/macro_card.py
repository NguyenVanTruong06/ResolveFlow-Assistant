"""
Preset Card Grid with Live Hover Preview (Learning from Akitt Pro 4.0).
Provides MacroCard (16:9 aspect ratio, 150ms hover preview, drag & double-click)
and MacroGridWidget (2-column luxury dark grid with search & category filters).
"""
import os
import math
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QGridLayout, QPushButton, QApplication
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, QRectF, Signal as pyqtSignal, QMimeData
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QLinearGradient,
    QPainterPath, QDrag, QCursor, QPixmap
)


class MacroCard(QFrame):
    """
    Luxury Dark Preset Card with 16:9 thumbnail frame, category/duration badges,
    150ms debounce hover live preview, and timeline insertion via double-click or drag.
    """
    clicked = pyqtSignal(str)
    insert_requested = pyqtSignal(str)
    dropped_on_resolve = pyqtSignal(str)
    fast_paste_requested = pyqtSignal(str)

    def __init__(
        self,
        preset_id: str,
        name: str,
        category: str = "Text+",
        duration_text: str = "1.5s",
        style_dna: str = "pop",
        thumbnail_path: str = "",
        motion_tags: Optional[List[str]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.preset_id = preset_id
        self.name = name
        self.category = category
        self.duration_text = duration_text
        self.style_dna = (style_dna or "pop").lower()
        self.thumbnail_path = thumbnail_path or ""
        self.thumbnail_pixmap: Optional[QPixmap] = None
        self.motion_tags: List[str] = list(motion_tags or [])

        if self.thumbnail_path and os.path.isfile(self.thumbnail_path):
            self.thumbnail_pixmap = QPixmap(self.thumbnail_path)

        self._is_previewing = False
        self._preview_frame = 0
        self._drag_start_pos: Optional[QPoint] = None

        # Hover trigger debounce timer (150ms)
        self.hover_timer = QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(150)
        self.hover_timer.timeout.connect(self._start_live_preview)

        # Animation preview loop timer (~30 FPS)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(40)
        self.preview_timer.timeout.connect(self._on_preview_tick)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(160, 140)
        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(),
            self.sizePolicy().verticalPolicy()
        )
        self._init_ui()
        self._update_style(hovered=False)

    @property
    def is_previewing(self) -> bool:
        return self._is_previewing

    @property
    def preview_frame(self) -> int:
        return self._preview_frame

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 16:9 Thumbnail Canvas Area
        self.canvas = _CardCanvas(self)
        self.canvas.setFixedHeight(84)
        layout.addWidget(self.canvas)

        # Text Info Bar
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(4)

        self.lbl_title = QLabel(self.name)
        self.lbl_title.setStyleSheet("color: #E6EDF3; font-weight: 600; font-size: 11px;")
        info_row.addWidget(self.lbl_title, stretch=1)

        self.lbl_duration = QLabel(self.duration_text)
        self.lbl_duration.setStyleSheet(
            "background-color: rgba(255, 255, 255, 0.08); color: #8B949E; "
            "border-radius: 4px; padding: 1px 5px; font-size: 10px;"
        )
        info_row.addWidget(self.lbl_duration)
        layout.addLayout(info_row)

        # Sub info row: category badge
        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)

        cat_badge = QLabel(self.category.upper())
        cat_badge.setStyleSheet(
            "color: #3dcee1; font-size: 9px; font-weight: bold; "
            "background-color: rgba(61, 206, 225, 0.12); "
            "border: 1px solid rgba(61, 206, 225, 0.25); border-radius: 3px; "
            "padding: 1px 4px;"
        )
        sub_row.addWidget(cat_badge)
        sub_row.addStretch(1)
        layout.addLayout(sub_row)

    def _update_style(self, hovered: bool):
        if hovered:
            self.setStyleSheet("""
                MacroCard {
                    background-color: rgba(28, 35, 46, 0.95);
                    border: 1px solid rgba(61, 206, 225, 0.5);
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                MacroCard {
                    background-color: rgba(22, 27, 34, 0.75);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                }
            """)

    def _start_live_preview(self):
        self._is_previewing = True
        self._preview_frame = 0
        self.preview_timer.start()
        self.canvas.update()

    def _on_preview_tick(self):
        self._preview_frame = (self._preview_frame + 1) % 40
        self.canvas.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._update_style(hovered=True)
        self.hover_timer.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._update_style(hovered=False)
        self.hover_timer.stop()
        self.preview_timer.stop()
        self._is_previewing = False
        self._preview_frame = 0
        self.canvas.update()

    def mousePressEvent(self, event):
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self.clicked.emit(self.preset_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event is not None and self._drag_start_pos is not None:
            dist = (event.pos() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                # Initiate QDrag
                drag = QDrag(self)
                mime = QMimeData()
                mime.setText(self.preset_id)
                drag.setMimeData(mime)
                self._drag_start_pos = None
                drag.exec(Qt.DropAction.CopyAction)
                self._handle_drop_on_release()
                return
        super().mouseMoveEvent(event)

    def _handle_drop_on_release(self):
        try:
            from .drag_drop_tracker import is_mouse_over_resolve
            if is_mouse_over_resolve():
                self.dropped_on_resolve.emit(self.preset_id)
        except Exception:
            pass

    def request_fast_paste(self):
        self.fast_paste_requested.emit(self.preset_id)

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0E1116;
                color: #E6EDF3;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #1E645A;
                color: #FFFFFF;
                border-radius: 4px;
            }
        """)
        act_paste = menu.addAction("⚡ Dán nhanh vào Fusion (Ctrl+V)")
        act_insert = menu.addAction("📥 Chèn tại Playhead (Timeline)")

        action = menu.exec(event.globalPos() if hasattr(event, "globalPos") else QCursor.pos())
        if action == act_paste:
            self.request_fast_paste()
        elif action == act_insert:
            self.insert_requested.emit(self.preset_id)

    def set_thumbnail(self, thumb_path: str):
        if thumb_path and os.path.isfile(thumb_path):
            self.thumbnail_path = thumb_path
            self.thumbnail_pixmap = QPixmap(thumb_path)
            self.canvas.update()

    def set_motion_tags(self, tags: List[str]):
        self.motion_tags = list(tags)

    def mouseDoubleClickEvent(self, event):
        self.insert_requested.emit(self.preset_id)


class _CardCanvas(QWidget):
    """Inner 16:9 canvas rendering the static thumbnail or active live preview animation."""
    def __init__(self, card: MacroCard):
        super().__init__(card)
        self.card = card
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        r = 6.0

        # Clip rounded rect
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(0, 0, w, h), r, r)
        painter.setClipPath(clip_path)

        # Background
        bg_color = QColor(14, 17, 22) if not self.card.is_previewing else QColor(10, 14, 20)
        painter.fillRect(0, 0, w, h, bg_color)

        frame = self.card.preview_frame
        dna = self.card.style_dna

        if not self.card.is_previewing:
            # Static Preview
            self._paint_static(painter, w, h)
        else:
            # Live Animated Preview
            self._paint_animated(painter, w, h, dna, frame)

    def _paint_static(self, painter: QPainter, w: int, h: int):
        if self.card.thumbnail_pixmap and not self.card.thumbnail_pixmap.isNull():
            scaled = self.card.thumbnail_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            crop_x = max(0, (scaled.width() - w) // 2)
            crop_y = max(0, (scaled.height() - h) // 2)
            painter.drawPixmap(0, 0, scaled, crop_x, crop_y, w, h)

            # Subtle dark gradient at bottom for text contrast
            grad = QLinearGradient(0, h * 0.5, 0, h)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, 160))
            painter.fillRect(0, int(h * 0.5), w, int(h * 0.5), grad)
        else:
            # Subtle grid background
            painter.setPen(QPen(QColor(255, 255, 255, 10), 1))
            for x in range(0, w, 20):
                painter.drawLine(x, 0, x, h)
            for y in range(0, h, 20):
                painter.drawLine(0, y, w, y)

            # Title icon/text centered
            painter.setPen(QColor("#E6EDF3"))
            font = QFont("Segoe UI", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self.card.name[:14])

    def _paint_animated(self, painter: QPainter, w: int, h: int, dna: str, frame: int):
        cx = w / 2.0
        cy = h / 2.0
        progress = (frame % 30) / 30.0

        if "glow" in dna or "neon" in dna:
            glow_rad = 18.0 + math.sin(progress * math.pi * 2) * 8.0
            glow_col = QColor(61, 206, 225, int(120 + 80 * math.sin(progress * math.pi * 2)))
            painter.setBrush(QBrush(glow_col))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, cy), glow_rad, glow_rad)

        elif "glitch" in dna:
            offset_x = 4.0 if (frame % 4 < 2) else -4.0
            painter.setPen(QColor(255, 0, 70, 200))
            painter.drawText(QRectF(offset_x, 0, w, h), Qt.AlignmentFlag.AlignCenter, self.card.name[:14])
            painter.setPen(QColor(0, 240, 255, 200))
            painter.drawText(QRectF(-offset_x, 0, w, h), Qt.AlignmentFlag.AlignCenter, self.card.name[:14])

        elif "comic" in dna:
            scale = 1.0 + 0.12 * math.sin(progress * math.pi * 2)
            painter.setPen(QPen(QColor("#FFE600"), 2))
            painter.setBrush(QBrush(QColor(30, 25, 0)))
            bubble_w = 70 * scale
            bubble_h = 36 * scale
            painter.drawRoundedRect(QRectF(cx - bubble_w/2, cy - bubble_h/2, bubble_w, bubble_h), 12, 12)

        elif "box" in dna or "highlight" in dna:
            sweep_w = (w * 0.7) * min(1.0, progress * 1.5)
            painter.fillRect(QRectF(cx - (w * 0.35), cy - 12, sweep_w, 24), QColor(255, 230, 0, 160))

        else: # Pop / Bounce
            bounce_y = cy + math.sin(progress * math.pi * 2) * 5.0
            painter.setBrush(QBrush(QColor(60, 154, 140, 180)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(cx - 30, bounce_y - 10, 60, 20), 4, 4)

        # Draw primary text on top
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, self.card.name[:14])


class MacroGridWidget(QWidget):
    """
    2-column scrollable grid widget for displaying and filtering MacroCards.
    Includes a live search input and category filter tabs.
    """
    preset_selected = pyqtSignal(str)
    preset_inserted = pyqtSignal(str)
    preset_dropped_on_resolve = pyqtSignal(str)
    preset_fast_paste_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.cards: List[MacroCard] = []
        self._selected_category = "Tất cả"
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # Top search bar
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Tìm preset, style, âm thanh...")
        self.search_input.textChanged.connect(self._apply_filter)
        main_layout.addWidget(self.search_input)

        # Category Chips Row
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(4)
        self.categories = ["Tất cả", "Text+", "Transition", "SFX", "Meme"]
        self.chip_buttons: Dict[str, QPushButton] = {}

        for cat in self.categories:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            if cat == "Tất cả":
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, c=cat: self._on_category_clicked(c))
            chip_row.addWidget(btn)
            self.chip_buttons[cat] = btn

        main_layout.addLayout(chip_row)

        # Scroll Area for 2-column Grid
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(8)
        self.scroll.setWidget(self.grid_container)
        main_layout.addWidget(self.scroll, stretch=1)

    def _on_category_clicked(self, category: str):
        self._selected_category = category
        for c, btn in self.chip_buttons.items():
            btn.setChecked(c == category)
        self._apply_filter()

    def load_items(self, items: List[Dict[str, Any]]):
        """Populates the grid with items (dict with id, name, category, duration, style_dna, thumbnail_path, motion_tags)."""
        # Clear existing
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards.clear()

        for item in items:
            card = MacroCard(
                preset_id=item.get("id", ""),
                name=item.get("name", ""),
                category=item.get("category", "Text+"),
                duration_text=item.get("duration", "1.5s"),
                style_dna=item.get("style_dna", "pop"),
                thumbnail_path=item.get("thumbnail_path", ""),
                motion_tags=item.get("motion_tags", []),
                parent=self.grid_container,
            )
            card.clicked.connect(self.preset_selected.emit)
            card.insert_requested.connect(self.preset_inserted.emit)
            card.dropped_on_resolve.connect(self.preset_dropped_on_resolve.emit)
            card.fast_paste_requested.connect(self.preset_fast_paste_requested.emit)
            self.cards.append(card)

        self._relayout_grid()

    def _relayout_grid(self):
        # Clear layout references without detaching from parent container
        while self.grid_layout.count():
            self.grid_layout.takeAt(0)

        visible_cards = [c for c in self.cards if not c.isHidden()]
        for idx, card in enumerate(visible_cards):
            row = idx // 2
            col = idx % 2
            self.grid_layout.addWidget(card, row, col)

    def _apply_filter(self):
        query = self.search_input.text().strip()
        cat = self._selected_category

        from src.core.motion_analyzer import matches_query

        for card in self.cards:
            if not query:
                match_query = True
            else:
                match_name = query.lower() in card.name.lower() or query.lower() in card.preset_id.lower()
                match_tags = matches_query(query, card.motion_tags)
                match_query = match_name or match_tags

            match_cat = (cat == "Tất cả") or (cat.lower() in card.category.lower())
            card.setVisible(match_query and match_cat)

        self._relayout_grid()

