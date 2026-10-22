"""
CommandPaletteWindow: Luxury Raycast-style HUD for ChunDVC.
Features:
- Fixed 640px width, dynamic height (56px to 420px max).
- Centered horizontally, top at 22% of DaVinci Resolve window height.
- Appearance: #131316 background at 94% opacity, 12px border radius, #33333a border.
- 120ms fade-in and 8px slide-down animation, 80ms exit animation.
- Instant search (< 16ms per keystroke).
- Keyboard: ↑↓ navigation, Enter to insert, Shift+Enter for new track/fusion paste,
  Alt+1..9 instant insert, Tab to customize in Dock sheet, Ctrl+Enter toggle favorite, Esc to close.
- Seamless DaVinci Resolve focus return after every action.
- Toast feedback integration with 1-click Undo.
(UX_REDESIGN.md Section 5)
"""
from typing import Optional, List, Dict, Any, Tuple
import logging

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QApplication, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, Signal as pyqtSignal, QTimer, QPropertyAnimation,
    QEasingCurve
)
from PySide6.QtGui import QKeyEvent, QPixmap, QPainter, QColor

from src.services.insert_service import InsertService, to_asset_dict
from src.services.search_index import SearchIndex
from src.core.fast_paste import focus_davinci_resolve
from src.ui.lux.window_anchor import get_resolve_window_rect
from src.ui.lux.feedback.toast import ChunDVCToast
from ..context.context_profiles import PROFILES, ContextProfile
from .command_registry import CommandRegistry, CommandItem
from .palette_model import PaletteSearchProvider, PaletteItem

logger = logging.getLogger("ResolveFlow.CommandPalette")


class PaletteSearchInput(QLineEdit):
    """
    Search QLineEdit capturing navigation keys for the parent palette.
    """
    key_nav_requested = pyqtSignal(str)          # "up", "down", "tab", "esc"
    alt_digit_pressed = pyqtSignal(int)          # 1..9
    enter_pressed = pyqtSignal(bool, bool)       # is_shift, is_ctrl

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()
        is_shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        is_ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        is_alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

        # Alt+1..9 shortcut handling
        if is_alt and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            digit = key - Qt.Key.Key_0
            self.alt_digit_pressed.emit(digit)
            event.accept()
            return

        if key == Qt.Key.Key_Down:
            self.key_nav_requested.emit("down")
            event.accept()
            return
        elif key == Qt.Key.Key_Up:
            self.key_nav_requested.emit("up")
            event.accept()
            return
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter_pressed.emit(is_shift, is_ctrl)
            event.accept()
            return
        elif key == Qt.Key.Key_Tab:
            self.key_nav_requested.emit("tab")
            event.accept()
            return
        elif key == Qt.Key.Key_Escape:
            self.key_nav_requested.emit("esc")
            event.accept()
            return

        super().keyPressEvent(event)


class PaletteRowWidget(QFrame):
    """
    48px single row widget displaying a PaletteItem:
    [ 64x36 Thumb ]  Title (bold) / Meta & Match Reason  [ Shortcut Hint ]
    """
    clicked = pyqtSignal(int)

    def __init__(self, index: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.index = index
        self._is_selected = False
        self._item: Optional[PaletteItem] = None

        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 12, 6)
        layout.setSpacing(10)

        # 1. Thumbnail Container (64x36)
        self.thumb_box = QLabel(self)
        self.thumb_box.setFixedSize(64, 36)
        self.thumb_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_box.setStyleSheet("""
            QLabel {
                background: #27272a;
                color: #a1a1aa;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.thumb_box)

        # 2. Text information (Title + Meta)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.lbl_title = QLabel(self)
        self.lbl_title.setStyleSheet("color: #f4f4f5; font-size: 12.5px; font-weight: 600;")
        text_layout.addWidget(self.lbl_title)

        self.lbl_meta = QLabel(self)
        self.lbl_meta.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        text_layout.addWidget(self.lbl_meta)

        layout.addLayout(text_layout, stretch=1)

        # 3. Shortcut hint pill
        self.lbl_hint = QLabel(self)
        self.lbl_hint.setFixedHeight(22)
        self.lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_hint.setStyleSheet("""
            QLabel {
                background: rgba(255, 255, 255, 0.08);
                color: #d4d4d8;
                border-radius: 4px;
                padding: 0 6px;
                font-size: 11px;
                font-family: monospace;
            }
        """)
        layout.addWidget(self.lbl_hint)

        self.update_style()

    def set_data(self, item: PaletteItem, is_selected: bool):
        self._item = item
        self._is_selected = is_selected

        self.lbl_title.setText(item.title)
        self.lbl_meta.setText(item.meta)
        self.lbl_hint.setText(item.shortcut_hint)
        self.lbl_hint.setVisible(bool(item.shortcut_hint))

        # Thumbnail / Kind icon
        if item.kind == "command":
            self.thumb_box.setText(">_")
            self.thumb_box.setStyleSheet("background: rgba(124, 58, 237, 0.25); color: #c4b5fd; border-radius: 4px; font-size: 14px; font-family: monospace;")
        else:
            cat = item.category.lower() if item.category else ""
            if "sfx" in cat or "bgm" in cat:
                self.thumb_box.setText("♪")
                self.thumb_box.setStyleSheet("background: #18181b; color: #ec4899; border-radius: 4px; font-size: 15px;")
            elif "lut" in cat:
                self.thumb_box.setText("LUT")
                self.thumb_box.setStyleSheet("background: #18181b; color: #fbbf24; border-radius: 4px; font-size: 11px;")
            else:
                self.thumb_box.setText(cat[:4].upper() if cat else "FX")
                self.thumb_box.setStyleSheet("background: #202024; color: #38bdf8; border-radius: 4px; font-size: 10.5px;")

        self.update_style()

    def set_selected(self, selected: bool, hint: str = ""):
        self._is_selected = selected
        if hint:
            self.lbl_hint.setText(hint)
            self.lbl_hint.show()
        self.update_style()

    def update_style(self):
        if self._is_selected:
            self.setStyleSheet("""
                PaletteRowWidget {
                    background: rgba(124, 58, 237, 0.22);
                    border: 1px solid rgba(124, 58, 237, 0.5);
                    border-radius: 8px;
                }
            """)
            self.lbl_hint.setStyleSheet("""
                QLabel {
                    background: #7c3aed;
                    color: #ffffff;
                    border-radius: 4px;
                    padding: 0 8px;
                    font-size: 11.5px;
                    font-weight: bold;
                }
            """)
        else:
            self.setStyleSheet("""
                PaletteRowWidget {
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 8px;
                }
                PaletteRowWidget:hover {
                    background: rgba(255, 255, 255, 0.05);
                }
            """)
            self.lbl_hint.setStyleSheet("""
                QLabel {
                    background: rgba(255, 255, 255, 0.06);
                    color: #a1a1aa;
                    border-radius: 4px;
                    padding: 0 6px;
                    font-size: 10.5px;
                    font-family: monospace;
                }
            """)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit(self.index)


class CommandPaletteWindow(QWidget):
    """
    Quick Command Palette (640px) Raycast HUD.
    """
    asset_inserted = pyqtSignal(dict, dict)
    command_executed = pyqtSignal(str)
    customize_requested = pyqtSignal(dict)
    palette_closed = pyqtSignal()

    def __init__(
        self,
        insert_service: Optional[InsertService] = None,
        search_index: Optional[SearchIndex] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._insert_service = insert_service
        self._search_provider = PaletteSearchProvider(search_index=search_index)
        self._anchor_rect: Optional[Tuple[int, int, int, int]] = None
        self._current_page: str = "edit"
        self._current_timecode: str = "00:00:00:00"

        self._items: List[PaletteItem] = []
        self._selected_index: int = 0
        self._row_widgets: List[PaletteRowWidget] = []

        # Shared Toast for instant feedback with Undo
        self.toast = ChunDVCToast(parent=None)

        # Slide and fade animation
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._is_closing: bool = False
        self._fade_anim.finished.connect(self._on_fade_finished)

        self._init_ui()

    def _init_ui(self):
        self.setFixedWidth(640)

        # Outer layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Background main card
        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: rgba(19, 19, 22, 0.95);
                border: 1px solid #33333a;
                border-radius: 12px;
            }
        """)
        main_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(10, 10, 10, 8)
        card_layout.setSpacing(6)

        # 1. Header (56px)
        header_widget = QWidget(self.card)
        header_widget.setFixedHeight(44)
        h_layout = QHBoxLayout(header_widget)
        h_layout.setContentsMargins(6, 0, 6, 0)
        h_layout.setSpacing(10)

        self.lbl_icon = QLabel("⌕")
        self.lbl_icon.setStyleSheet("color: #8b5cf6; font-size: 18px; font-weight: bold;")
        h_layout.addWidget(self.lbl_icon)

        self.txt_search = PaletteSearchInput(header_widget)
        self.txt_search.setPlaceholderText("Tìm asset (gõ) hoặc lệnh (>)...")
        self.txt_search.setStyleSheet("""
            QLineEdit {
                background: transparent;
                color: #ffffff;
                border: none;
                font-size: 14px;
                font-weight: 500;
                selection-background-color: #7c3aed;
            }
        """)
        self.txt_search.textChanged.connect(self._on_search_text_changed)
        self.txt_search.key_nav_requested.connect(self._on_key_nav)
        self.txt_search.alt_digit_pressed.connect(self._on_alt_digit)
        self.txt_search.enter_pressed.connect(self._on_enter_pressed)
        h_layout.addWidget(self.txt_search, stretch=1)

        # Context badge (e.g. Edit · 00:01:12:04)
        self.lbl_context = QLabel("● Edit · 00:00:00:00")
        self.lbl_context.setStyleSheet("""
            QLabel {
                color: #c4b5fd;
                background: rgba(124, 58, 237, 0.15);
                border: 1px solid rgba(124, 58, 237, 0.35);
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
        """)
        h_layout.addWidget(self.lbl_context)

        card_layout.addWidget(header_widget)

        # Divider line
        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet("background: #27272a;")
        card_layout.addWidget(self.divider)

        # 2. Results Container (up to 7 rows)
        self.results_container = QWidget(self.card)
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 4, 0, 4)
        self.results_layout.setSpacing(4)
        card_layout.addWidget(self.results_container)

        # Pre-create 7 reusable row widgets
        for i in range(7):
            row = PaletteRowWidget(i, self.results_container)
            row.clicked.connect(self._on_row_clicked)
            row.hide()
            self.results_layout.addWidget(row)
            self._row_widgets.append(row)

        # Empty state label
        self.lbl_empty = QLabel("Không tìm thấy kết quả phù hợp.", self.results_container)
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setFixedHeight(60)
        self.lbl_empty.setStyleSheet("color: #71717a; font-size: 12px;")
        self.lbl_empty.hide()
        self.results_layout.addWidget(self.lbl_empty)

        # Divider line
        self.footer_divider = QFrame()
        self.footer_divider.setFixedHeight(1)
        self.footer_divider.setStyleSheet("background: #27272a;")
        card_layout.addWidget(self.footer_divider)

        # 3. Footer Cheat Sheet (28px)
        self.footer = QLabel("⏎ Chèn   ⇧⏎ Dán Fusion / Track mới   Tab Tùy chỉnh   Ctrl⏎ Yêu thích   Esc Đóng")
        self.footer.setFixedHeight(24)
        self.footer.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.footer.setStyleSheet("color: #71717a; font-size: 10.5px; padding-left: 6px;")
        card_layout.addWidget(self.footer)

    # -------------------------------------------------------------------------
    # Public API & State Binding
    # -------------------------------------------------------------------------
    def set_assets(self, assets: List[Dict[str, Any]]):
        self._search_provider.set_assets(assets)

    def set_search_index(self, index: SearchIndex):
        self._search_provider.set_search_index(index)

    def set_anchor_rect(self, rect: Optional[Tuple[int, int, int, int]]):
        self._anchor_rect = rect
        self.toast.set_anchor_rect(rect)

    def set_resolve_context(self, page: str, timecode: str):
        self._current_page = page.lower().strip()
        self._current_timecode = timecode
        profile = PROFILES.get(self._current_page)
        color = profile.color if profile else "#7c3aed"
        label = profile.label if profile else page.capitalize()

        self.lbl_context.setText(f"● {label} · {timecode}")
        self.lbl_context.setStyleSheet(f"""
            QLabel {{
                color: #f4f4f5;
                background: {color}22;
                border: 1px solid {color}55;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }}
        """)

    def open_palette(self, command_mode: bool = False, query_prefix: str = ""):
        """Opens palette HUD with smooth 120ms fade & slide down animation."""
        prefix = ">" if command_mode else query_prefix
        self.txt_search.setText(prefix)
        self.txt_search.selectAll()

        self._update_results()
        self._update_geometry(animated=True)

        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.txt_search.setFocus()

        # 120ms fade in
        self._is_closing = False
        self._fade_anim.stop()
        self._fade_anim.setDuration(120)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.start()

    def close_palette(self, animated: bool = False):
        """Closes palette HUD and returns focus to Resolve."""
        if not self.isVisible():
            return

        self._fade_anim.stop()
        self._anim.stop()

        if not animated:
            self._is_closing = False
            self.hide()
            self.palette_closed.emit()
            focus_davinci_resolve()
            return

        self._is_closing = True
        self._fade_anim.setDuration(80)
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.start()

    def _on_fade_finished(self):
        if self._is_closing:
            self._is_closing = False
            self.hide()
            self.palette_closed.emit()
            focus_davinci_resolve()

    # -------------------------------------------------------------------------
    # Search & Results Rendering
    # -------------------------------------------------------------------------
    def _on_search_text_changed(self, text: str):
        is_cmd = text.strip().startswith(">")
        self.lbl_icon.setText(">_" if is_cmd else "⌕")
        self.lbl_icon.setStyleSheet(
            "color: #06b6d4; font-size: 15px; font-weight: bold; font-family: monospace;"
            if is_cmd else "color: #8b5cf6; font-size: 18px; font-weight: bold;"
        )
        self.footer.setText(
            "⏎ Chạy lệnh &nbsp; Esc Đóng"
            if is_cmd else
            "⏎ Chèn   ⇧⏎ Dán Fusion / Track mới   Tab Tùy chỉnh   Ctrl⏎ Yêu thích   Esc Đóng"
        )
        self._selected_index = 0
        self._update_results()
        self._update_geometry(animated=False)

    def _update_results(self):
        query = self.txt_search.text()
        self._items = self._search_provider.search(query, current_page=self._current_page, limit=7)

        count = len(self._items)
        if count == 0 and query.strip():
            self.lbl_empty.setText(f"Không tìm thấy “{query.strip()}”. Thử “rung”, “zoom”, “swoosh” hoặc >lệnh")
            self.lbl_empty.show()
        else:
            self.lbl_empty.hide()

        for i, row in enumerate(self._row_widgets):
            if i < count:
                item = self._items[i]
                is_sel = (i == self._selected_index)
                hint = "⏎" if is_sel else (f"⌥{i+1}" if i < 9 else "")
                item.shortcut_hint = hint
                row.set_data(item, is_selected=is_sel)
                row.show()
            else:
                row.hide()

    def _update_selection(self):
        count = len(self._items)
        if count == 0:
            return
        self._selected_index = max(0, min(self._selected_index, count - 1))
        for i, row in enumerate(self._row_widgets):
            if i < count:
                is_sel = (i == self._selected_index)
                hint = "⏎" if is_sel else (f"⌥{i+1}" if i < 9 else "")
                row.set_selected(is_sel, hint=hint)

    # -------------------------------------------------------------------------
    # Geometry & Layout Positioning
    # -------------------------------------------------------------------------
    def _calc_target_rect(self) -> QRect:
        w = 640
        # Dynamic height calculation:
        # Header (56px) + Divider (1px) + Results (n * 48px + 8px margins + spacing) + Divider (1px) + Footer (28px)
        items_count = len(self._items)
        if items_count == 0 and self.txt_search.text().strip():
            list_h = 60
        else:
            list_h = items_count * 48 + (max(0, items_count - 1) * 4) + 8 if items_count > 0 else 0

        h = 56 + 1 + list_h + 1 + 28 + 20
        h = max(100, min(420, h))

        anchor = self._anchor_rect or get_resolve_window_rect()
        if anchor:
            ax, ay, aw, ah = anchor
            x = ax + (aw - w) // 2
            y = ay + int(ah * 0.22)
        else:
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                x = geom.x() + (geom.width() - w) // 2
                y = geom.y() + int(geom.height() * 0.22)
            else:
                x, y = 200, 200

        return QRect(int(x), int(y), int(w), int(h))

    def _update_geometry(self, animated: bool = False):
        target = self._calc_target_rect()
        if not animated:
            self.setGeometry(target)
            return

        # Slide down 8px while opening
        start = QRect(target.x(), target.y() - 8, target.width(), target.height())
        self.setGeometry(start)

        self._anim.stop()
        self._anim.setDuration(120)
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    # -------------------------------------------------------------------------
    # Actions & Execution
    # -------------------------------------------------------------------------
    def _on_key_nav(self, action: str):
        count = len(self._items)
        if action == "down" and count > 0:
            self._selected_index = (self._selected_index + 1) % count
            self._update_selection()
        elif action == "up" and count > 0:
            self._selected_index = (self._selected_index - 1 + count) % count
            self._update_selection()
        elif action == "tab":
            self.customize_current()
        elif action == "esc":
            self.close_palette()

    def _on_alt_digit(self, digit: int):
        idx = digit - 1
        if 0 <= idx < len(self._items):
            self._selected_index = idx
            self.execute_current(mode="auto")

    def _on_enter_pressed(self, is_shift: bool, is_ctrl: bool):
        if is_ctrl:
            self.toggle_fav_current()
        elif is_shift:
            self.execute_current(mode="shift")
        else:
            self.execute_current(mode="auto")

    def _on_row_clicked(self, index: int):
        if 0 <= index < len(self._items):
            self._selected_index = index
            self.execute_current(mode="auto")

    def execute_current(self, mode: str = "auto"):
        if not self._items or self._selected_index >= len(self._items):
            return

        item = self._items[self._selected_index]
        self.close_palette()

        # Command Execution
        if item.kind == "command":
            cmd: CommandItem = item.data
            logger.info("Executing palette command: %s (%s)", cmd.name, cmd.id)
            if cmd.callback:
                cmd.callback()
            self.command_executed.emit(cmd.id)
            return

        # Asset Insertion
        raw_asset = item.data
        asset = to_asset_dict(raw_asset)
        insert_mode = mode
        if mode == "shift":
            insert_mode = "fusion_paste" if self._current_page == "fusion" else "new_track"
        elif self._current_page == "fusion":
            insert_mode = "fusion_paste"

        logger.info("Palette inserting asset '%s' in mode '%s'", asset.get("name"), insert_mode)
        self._search_provider.add_recent(asset.get("id", ""))

        if self._insert_service:
            res = self._insert_service.insert(asset, mode=insert_mode)
            self.asset_inserted.emit(asset, {"mode": insert_mode, "result": res})
            # Show Toast feedback
            is_ok = getattr(res, "ok", False) or getattr(res, "success", False)
            if is_ok:
                track_lbl = getattr(res, "track", "") or (f"Track {res.track_index}" if getattr(res, "track_index", None) else "")
                tc_lbl = res.start_tc if getattr(res, "start_tc", None) else self._current_timecode
                self.toast.show_success(
                    asset_name=asset.get("name", "Asset"),
                    track_name=track_lbl,
                    timecode=tc_lbl,
                    result=res,
                    undo_callback=lambda r: self._insert_service.undo(r),
                )
            else:
                err_msg = getattr(res, "error", "") or getattr(res, "error_message", "Chèn thất bại")
                self.toast.show_error(reason=err_msg)
        else:
            self.asset_inserted.emit(asset, {"mode": insert_mode})
            self.toast.show_success(
                asset_name=asset.get("name", "Asset"),
                timecode=self._current_timecode,
            )

        focus_davinci_resolve()

    def customize_current(self):
        if not self._items or self._selected_index >= len(self._items):
            return
        item = self._items[self._selected_index]
        if item.kind == "asset":
            self.close_palette()
            self.customize_requested.emit(item.data)

    def toggle_fav_current(self):
        if not self._items or self._selected_index >= len(self._items):
            return
        item = self._items[self._selected_index]
        if item.kind == "asset":
            aid = item.data.get("id", "")
            is_fav = self._search_provider.toggle_fav(aid)
            item.is_fav = is_fav
            msg = f"Đã thêm “{item.title}” vào Yêu thích" if is_fav else f"Đã bỏ yêu thích “{item.title}”"
            self.toast.show_success(asset_name=item.title, track_name="Yêu thích")
            self._update_results()
