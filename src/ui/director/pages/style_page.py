"""
src/ui/director/pages/style_page.py
Bước 2: Chọn Kiểu Dựng & Thiết Lập Nhanh.
Bao gồm:
- 4 Thẻ phong cách (Podcast, Shorts 9:16, Vlog có Hook, Tự chỉnh).
- Segmented controls: Loại footage & Nhịp dựng.
- Slider Mức cắt vấp & lặng (Nhẹ / Vừa / Mạnh).
- Cụm chip tính năng bật/tắt (Hook, Punch-in, B-roll, SFX, Reframe, Sub, v.v.).
- Nút "Tùy chỉnh nâng cao ›" trượt mở bảng bên phải 280px.
"""

from typing import Dict, List, Set
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QFrame, QScrollArea
)
from PySide6.QtCore import Qt, Signal
from src.ui.director.director_state import (
    DirectorConfig, STYLE_PODCAST, STYLE_SHORTS, STYLE_VLOG, STYLE_CUSTOM,
    ALL_FEATURE_CHIPS
)


class StyleCard(QFrame):
    """Một thẻ lớn đại diện cho 1 phong cách dựng video."""
    clicked = Signal(str)

    def __init__(self, style_id: str, title: str, desc: str, icon: str, parent=None):
        super().__init__(parent)
        self.style_id = style_id
        self.title = title
        self.desc = desc
        self.icon = icon
        self.is_selected = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(84)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        h_title = QHBoxLayout()
        lbl_icon = QLabel(self.icon)
        lbl_icon.setStyleSheet("font-size: 16px;")
        h_title.addWidget(lbl_icon)

        self.lbl_title = QLabel(self.title)
        self.lbl_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        h_title.addWidget(self.lbl_title, stretch=1)
        layout.addLayout(h_title)

        self.lbl_desc = QLabel(self.desc)
        self.lbl_desc.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

        self._update_style()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._update_style()

    def _update_style(self):
        if self.is_selected:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(124, 58, 237, 0.16);
                    border: 1.5px solid #8b5cf6;
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #18181b;
                    border: 1px solid #27272a;
                    border-radius: 8px;
                }
                QFrame:hover {
                    border-color: #3f3f46;
                    background-color: #202024;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.style_id)


class SegmentedControl(QFrame):
    """Thanh segmented control chọn 1 giá trị trong danh sách."""
    valueChanged = Signal(str)

    def __init__(self, options: List[str], current: str, parent=None):
        super().__init__(parent)
        self.options = options
        self.current = current
        self._buttons: Dict[str, QPushButton] = {}
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 2px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        for opt in self.options:
            btn = QPushButton(opt, self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked=False, val=opt: self._on_btn_clicked(val))
            layout.addWidget(btn)
            self._buttons[opt] = btn

        self._refresh()

    def _on_btn_clicked(self, val: str):
        self.current = val
        self._refresh()
        self.valueChanged.emit(val)

    def set_value(self, val: str):
        if val in self.options:
            self.current = val
            self._refresh()

    def _refresh(self):
        for opt, btn in self._buttons.items():
            if opt == self.current:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #7c3aed;
                        color: #ffffff;
                        border: none;
                        border-radius: 4px;
                        padding: 0 10px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: transparent;
                        color: #a1a1aa;
                        border: none;
                        border-radius: 4px;
                        padding: 0 10px;
                        font-size: 11px;
                    }
                    QPushButton:hover {
                        color: #ffffff;
                        background: rgba(255, 255, 255, 0.05);
                    }
                """)


class FeatureChipButton(QPushButton):
    """Một chip tính năng bật/tắt."""
    toggled_chip = Signal(str, bool)

    def __init__(self, name: str, is_active: bool = False, parent=None):
        super().__init__(name, parent)
        self.name = name
        self.is_active = is_active
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.clicked.connect(self._toggle)
        self._update_appearance()

    def _toggle(self):
        self.is_active = not self.is_active
        self._update_appearance()
        self.toggled_chip.emit(self.name, self.is_active)

    def set_active(self, active: bool):
        self.is_active = active
        self._update_appearance()

    def _update_appearance(self):
        if self.is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(124, 58, 237, 0.2);
                    color: #c4b5fd;
                    border: 1px solid #7c3aed;
                    border-radius: 14px;
                    padding: 0 12px;
                    font-size: 11px;
                    font-weight: 600;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #18181b;
                    color: #71717a;
                    border: 1px solid #27272a;
                    border-radius: 14px;
                    padding: 0 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    color: #a1a1aa;
                    border-color: #3f3f46;
                }
            """)


class StylePage(QWidget):
    """Trang Bước 2: Kiểu dựng."""
    style_changed = Signal(str)
    advanced_toggle_requested = Signal()
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_style = STYLE_VLOG
        self.current_footage = "Tự nhận diện"
        self.current_pacing = "Cân bằng"
        self.current_intensity = 2
        self.enabled_features: Set[str] = set()

        self._cards: Dict[str, StyleCard] = {}
        self._chips: Dict[str, FeatureChipButton] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # 1. 4 Thẻ Style
        lbl_h3 = QLabel("Bạn muốn dựng video kiểu gì?")
        lbl_h3.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl_h3)

        grid_cards = QHBoxLayout()
        grid_cards.setSpacing(10)

        styles_def = [
            (STYLE_PODCAST, "Podcast, phỏng vấn", "Cắt khoảng lặng, lọc câu vấp, phụ đề", "🎙️"),
            (STYLE_SHORTS, "Shorts, TikTok 9:16", "Cắt viral 60s, reframe dọc, karaoke sub", "📱"),
            (STYLE_VLOG, "Vlog có Hook", "Teaser mở đầu, punch-in, B-roll, tua lặng", "🎬"),
            (STYLE_CUSTOM, "Tự chỉnh", "Mở toàn bộ thông số nâng cao", "⚙️")
        ]

        for sid, title, desc, icon in styles_def:
            card = StyleCard(sid, title, desc, icon, self)
            card.clicked.connect(self._on_style_selected)
            grid_cards.addWidget(card)
            self._cards[sid] = card

        layout.addLayout(grid_cards)

        # 2. Thiết lập nhanh
        lbl_quick = QLabel("Thiết lập nhanh")
        lbl_quick.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_quick)

        # Hàng 1: Loại footage
        h_row1 = QHBoxLayout()
        h_row1.addWidget(QLabel("Loại footage:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.seg_footage = SegmentedControl(
            ["Tự nhận diện", "Nói liên tục", "Hỗn hợp", "Du lịch"],
            self.current_footage, self
        )
        self.seg_footage.valueChanged.connect(self._on_footage_changed)
        h_row1.addWidget(self.seg_footage)
        h_row1.addStretch(1)
        layout.addLayout(h_row1)

        # Hàng 2: Nhịp dựng
        h_row2 = QHBoxLayout()
        h_row2.addWidget(QLabel("Nhịp dựng:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.seg_pacing = SegmentedControl(
            ["Thong thả", "Cân bằng", "Nhanh"],
            self.current_pacing, self
        )
        self.seg_pacing.valueChanged.connect(self._on_pacing_changed)
        h_row2.addWidget(self.seg_pacing)
        h_row2.addStretch(1)
        layout.addLayout(h_row2)

        # Hàng 3: Mức cắt vấp & lặng
        h_row3 = QHBoxLayout()
        h_row3.addWidget(QLabel("Mức cắt vấp & lặng:", styleSheet="color: #a1a1aa; font-size: 11.5px; min-width: 130px;"))
        self.slider_intensity = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_intensity.setRange(1, 3)
        self.slider_intensity.setValue(self.current_intensity)
        self.slider_intensity.setFixedWidth(240)
        self.slider_intensity.valueChanged.connect(self._on_intensity_changed)
        h_row3.addWidget(self.slider_intensity)

        self.lbl_intensity = QLabel("Vừa")
        self.lbl_intensity.setStyleSheet("color: #8b5cf6; font-size: 11.5px; font-weight: bold; min-width: 50px;")
        h_row3.addWidget(self.lbl_intensity)
        h_row3.addStretch(1)
        layout.addLayout(h_row3)

        # 3. Tính năng bật
        lbl_feats = QLabel("Tính năng bật")
        lbl_feats.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_feats)

        h_chips = QHBoxLayout()
        h_chips.setSpacing(6)
        for feat in ALL_FEATURE_CHIPS:
            chip = FeatureChipButton(feat, False, self)
            chip.toggled_chip.connect(self._on_chip_toggled)
            h_chips.addWidget(chip)
            self._chips[feat] = chip
        h_chips.addStretch(1)
        layout.addLayout(h_chips)

        # Nút mở bảng nâng cao
        layout.addSpacing(6)
        self.btn_adv = QPushButton("Tùy chỉnh nâng cao ›", self)
        self.btn_adv.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_adv.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a78bfa;
                border: none;
                font-size: 12px;
                font-weight: 600;
                text-align: left;
                padding: 4px 0;
            }
            QPushButton:hover {
                color: #c4b5fd;
            }
        """)
        self.btn_adv.clicked.connect(self.advanced_toggle_requested.emit)
        layout.addWidget(self.btn_adv)

        layout.addStretch(1)
        self._select_style(self.current_style)

    def _on_style_selected(self, style_id: str):
        self._select_style(style_id)
        if style_id == STYLE_CUSTOM:
            self.advanced_toggle_requested.emit()
        self.style_changed.emit(style_id)
        self.config_changed.emit()

    def _select_style(self, style_id: str):
        self.current_style = style_id
        for sid, card in self._cards.items():
            card.set_selected(sid == style_id)

    def _on_footage_changed(self, val: str):
        self.current_footage = val
        self.config_changed.emit()

    def _on_pacing_changed(self, val: str):
        self.current_pacing = val
        self.config_changed.emit()

    def _on_intensity_changed(self, val: int):
        self.current_intensity = val
        labels = {1: "Nhẹ", 2: "Vừa", 3: "Mạnh"}
        self.lbl_intensity.setText(labels.get(val, "Vừa"))
        self.config_changed.emit()

    def _on_chip_toggled(self, name: str, active: bool):
        if active:
            self.enabled_features.add(name)
        else:
            self.enabled_features.discard(name)
        self.config_changed.emit()

    def load_from_config(self, config: DirectorConfig):
        self._select_style(config.style)
        self.current_footage = config.footage_type
        self.seg_footage.set_value(config.footage_type)
        self.current_pacing = config.pacing
        self.seg_pacing.set_value(config.pacing)
        self.current_intensity = config.intensity
        self.slider_intensity.setValue(config.intensity)
        self._on_intensity_changed(config.intensity)

        self.enabled_features = set(config.enabled_features)
        for name, chip in self._chips.items():
            chip.set_active(name in self.enabled_features)

    def save_to_config(self, config: DirectorConfig):
        config.style = self.current_style
        config.footage_type = self.current_footage
        config.pacing = self.current_pacing
        config.intensity = self.current_intensity
        config.enabled_features = set(self.enabled_features)
