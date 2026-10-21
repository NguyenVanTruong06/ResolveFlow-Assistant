"""
Horizontal Scrollable Category Navigation for ChunDVC Library.
Replaces the old cramped 78px vertical rail with truncated text.
"""
from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal as pyqtSignal


DEFAULT_CATEGORIES = [
    {"id": "text", "label": "Chữ", "subs": ["Tất cả", "Kinetic", "Bút dạ", "Neon", "Retro", "Tối giản"]},
    {"id": "trans", "label": "Chuyển cảnh", "subs": ["Tất cả", "Chuyển động", "Ánh sáng", "Glitch"]},
    {"id": "meme", "label": "Meme", "subs": ["Tất cả"]},
    {"id": "overlay", "label": "Lớp phủ", "subs": ["Tất cả", "Grain", "Light Leak", "VHS", "Dust"]},
    {"id": "sfx", "label": "Âm thanh", "subs": ["Tất cả", "Whoosh", "UI", "Glitch", "Impact"]},
    {"id": "bgm", "label": "Nhạc", "subs": ["Tất cả", "Vlog", "Cinematic", "Lofi"]},
    {"id": "lut", "label": "Màu (LUT)", "subs": ["Tất cả", "Điện ảnh", "Vintage", "Cyber", "Vlog"]},
    {"id": "mine", "label": "Của tôi", "subs": ["Tất cả", "Đã lưu", "Macro"]},
]


class CategoryNavWidget(QWidget):
    """
    Horizontal Category Navigation:
    Row 1: [ (Chữ) ] [ Chuyển cảnh ] [ Meme ] [ Lớp phủ ] [ Âm thanh ] [ Màu ]
    Row 2: [ Tất cả ] · [ Kinetic ] · [ Bút dạ ] · [ Neon ] · [ Retro ]
    """
    category_changed = pyqtSignal(str)
    sub_category_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._categories: List[Dict] = DEFAULT_CATEGORIES
        self._current_cat: str = "text"
        self._current_sub: str = "Tất cả"
        self._cat_buttons: Dict[str, QPushButton] = {}
        self._sub_buttons: List[QPushButton] = []

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 4, 6, 4)
        main_layout.setSpacing(4)

        # Row 1: Primary category chips
        self.cat_scroll = QScrollArea()
        self.cat_scroll.setFixedHeight(34)
        self.cat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cat_scroll.setWidgetResizable(True)
        self.cat_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cat_scroll.setStyleSheet("background: transparent;")

        self.cat_container = QWidget()
        self.cat_layout = QHBoxLayout(self.cat_container)
        self.cat_layout.setContentsMargins(0, 0, 0, 0)
        self.cat_layout.setSpacing(6)
        self.cat_scroll.setWidget(self.cat_container)
        main_layout.addWidget(self.cat_scroll)

        # Row 2: Sub-category chips
        self.sub_scroll = QScrollArea()
        self.sub_scroll.setFixedHeight(28)
        self.sub_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sub_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sub_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sub_scroll.setWidgetResizable(True)
        self.sub_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sub_scroll.setStyleSheet("background: transparent;")

        self.sub_container = QWidget()
        self.sub_layout = QHBoxLayout(self.sub_container)
        self.sub_layout.setContentsMargins(0, 0, 0, 0)
        self.sub_layout.setSpacing(4)
        self.sub_scroll.setWidget(self.sub_container)
        main_layout.addWidget(self.sub_scroll)

        self.render_categories()

    def set_categories(self, categories: List[Any]):
        if not categories:
            self._categories = []
        elif isinstance(categories[0], str):
            cat_map = {c["id"]: c for c in DEFAULT_CATEGORIES}
            self._categories = []
            for cid in categories:
                if cid in cat_map:
                    self._categories.append(cat_map[cid])
                else:
                    self._categories.append({"id": cid, "label": cid.capitalize(), "subs": ["Tất cả"]})
        else:
            self._categories = categories

        if self._categories and self._current_cat not in [c["id"] for c in self._categories]:
            self._current_cat = self._categories[0]["id"]
            self._current_sub = "Tất cả"
        self.render_categories()

    def select_sub_category(self, sub: str):
        self.select_subcategory(sub)

    def render_categories(self):
        # Clear existing buttons
        while self.cat_layout.count():
            item = self.cat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cat_buttons.clear()

        for cat in self._categories:
            cid = cat["id"]
            lbl = cat["label"]
            btn = QPushButton(lbl)
            btn.setFixedHeight(28)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            is_active = (cid == self._current_cat)
            btn.setStyleSheet(self._cat_style(is_active))
            btn.clicked.connect(lambda checked=False, c=cid: self.select_category(c))
            self.cat_layout.addWidget(btn)
            self._cat_buttons[cid] = btn

        self.cat_layout.addStretch(1)
        self.render_subcategories()

    def render_subcategories(self):
        while self.sub_layout.count():
            item = self.sub_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._sub_buttons.clear()

        cat_data = next((c for c in self._categories if c["id"] == self._current_cat), None)
        subs = cat_data.get("subs", ["Tất cả"]) if cat_data else ["Tất cả"]

        if len(subs) <= 1:
            self.sub_scroll.hide()
            return
        self.sub_scroll.show()

        for sub in subs:
            btn = QPushButton(sub)
            btn.setFixedHeight(22)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            is_active = (sub == self._current_sub)
            btn.setStyleSheet(self._sub_style(is_active))
            btn.clicked.connect(lambda checked=False, s=sub: self.select_subcategory(s))
            self.sub_layout.addWidget(btn)
            self._sub_buttons.append(btn)

        self.sub_layout.addStretch(1)

    def select_category(self, cat_id: str):
        if self._current_cat == cat_id:
            return
        self._current_cat = cat_id
        self._current_sub = "Tất cả"

        # Update styling
        for cid, btn in self._cat_buttons.items():
            btn.setStyleSheet(self._cat_style(cid == self._current_cat))

        self.render_subcategories()
        self.category_changed.emit(self._current_cat)
        self.sub_category_changed.emit(self._current_sub)

    def select_subcategory(self, sub: str):
        if self._current_sub == sub:
            return
        self._current_sub = sub
        for btn in self._sub_buttons:
            btn.setStyleSheet(self._sub_style(btn.text() == self._current_sub))
        self.sub_category_changed.emit(self._current_sub)

    @property
    def current_category(self) -> str:
        return self._current_cat

    @property
    def current_subcategory(self) -> str:
        return self._current_sub

    def _cat_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: #7c3aed;
                    color: #ffffff;
                    border: 1px solid #8b5cf6;
                    border-radius: 14px;
                    padding: 0 12px;
                    font-size: 11.5px;
                    font-weight: 600;
                }
            """
        return """
            QPushButton {
                background: #18181b;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 14px;
                padding: 0 12px;
                font-size: 11.5px;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
                border-color: #3f3f46;
            }
        """

    def _sub_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: rgba(124, 58, 237, 0.2);
                    color: #c4b5fd;
                    border: 1px solid rgba(124, 58, 237, 0.4);
                    border-radius: 11px;
                    padding: 0 8px;
                    font-size: 10.5px;
                    font-weight: 500;
                }
            """
        return """
            QPushButton {
                background: transparent;
                color: #71717a;
                border: 1px solid transparent;
                border-radius: 11px;
                padding: 0 8px;
                font-size: 10.5px;
            }
            QPushButton:hover {
                background: #18181b;
                color: #d4d4d8;
            }
        """
