"""
Tab 2: Visual Techniques & Text Presets Library (Thư Viện Kỹ Xảo Chữ & Motion)
Lấy cảm hứng từ Eyecandy (eyecannndy.com), Mixkit (mixkit.co) và Shotdeck (shotdeck.com):
- Thư viện Kỹ Xảo Chữ Động (Kinetic Typography, Highlighter Dạ Quang, Paper Cutout Stop-motion, RGB Glitch, Neon Glow, 90s VHS, Clean Minimal)
- Kéo-Thả Trực Tiếp (Drag & Drop .setting Fusion Macro) vào DaVinci Resolve Timeline (Free & Studio 100% khả thi)
- Bộ Lọc Phân Loại (Category Filter Pills) & Tìm Kiếm Thời Gian Thực (Live Search)
- 1-Click Cài Đặt Tất Cả Presets vào Thư Viện Effects Library của DaVinci Resolve
- Sao chép nhanh Fusion Node (Ctrl+V) & Chèn tiêu đề tức thì tại Playhead
- Tùy biến phông chữ, cỡ chữ, màu sắc và ngắt dòng thông minh
"""

import os
import tempfile
import uuid
from typing import Optional, Callable, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QMimeData, QUrl
from PySide6.QtGui import QDrag, QPixmap, QCursor
from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS, MODULE_DESCRIPTIONS
from src.core.text_preset import TextStylePreset, BUILTIN_PRESETS, FusionSettingGenerator, TextPreviewRenderer


class DraggablePreviewLabel(QLabel):
    """
    Khung xem trước hỗ trợ Kéo-Thả (Drag & Drop) trực tiếp sang DaVinci Resolve.
    Khi người dùng nhấn giữ và kéo khung này thả vào Timeline hoặc Media Pool của DaVinci Resolve,
    hệ thống tự sinh file .setting Fusion Text+ và Resolve nhận diện tự động!
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.preset_getter: Optional[Callable[[], Optional[TextStylePreset]]] = None
        self.sample_text_getter: Optional[Callable[[], str]] = None
        self.drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("🖱️ NHẤN GIỮ VÀ KÉO THẢ sang DaVinci Resolve Timeline để chèn kiểu chữ này ngay lập tức!")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < 8:
            return

        preset = self.preset_getter() if callable(self.preset_getter) else None
        if not preset:
            return

        sample_txt = self.sample_text_getter() if callable(self.sample_text_getter) else preset.name
        if not sample_txt:
            sample_txt = preset.name

        temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{preset.id}_{uuid.uuid4().hex[:6]}.setting")
        FusionSettingGenerator.export_setting_file(preset, temp_setting, sample_text=sample_txt)

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
        drag.setMimeData(mime_data)
        if self.pixmap():
            drag.setPixmap(self.pixmap().scaled(180, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        
        self.setCursor(Qt.OpenHandCursor)
        drag.exec_(Qt.CopyAction)


class VisualTechniqueCard(QFrame):
    """
    Thẻ Kỹ Xảo Thị Giác (Eyecandy / Mixkit Style Visual Technique Card).
    Bao gồm:
    - Tiêu đề kỹ xảo, Badge Icon & Category Chip
    - Mô tả kỹ thuật hoạt ảnh & Anatomy of Effect
    - Thẻ Tags (#Viral, #Shorts, #StopMotion,...)
    - Vùng Kéo-Thả Draggable Preview
    - Các nút thao tác nhanh: [🚀 Chèn Playhead], [📋 Copy Node], [🖱️ Kéo thả]
    """
    selected_signal = pyqtSignal(str) # preset_id
    insert_playhead_signal = pyqtSignal(str) # preset_id
    copy_node_signal = pyqtSignal(str) # preset_id

    def __init__(self, preset: TextStylePreset, sample_text_func: Optional[Callable[[], str]] = None, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.sample_text_func = sample_text_func
        self.is_selected = False
        self.setProperty("class", "technique_card")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # 1. Header: Icon + Title + Category Tag
        h_top = QHBoxLayout()
        lbl_icon = QLabel(self.preset.badge_icon or "✨")
        lbl_icon.setStyleSheet("font-size: 16px;")
        
        lbl_title = QLabel(f"<b>{self.preset.name}</b>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 12px;")
        
        cat_badge = QLabel(self.preset.category.upper().replace("_", " & "))
        cat_badge.setStyleSheet("""
            background-color: #1E293B;
            border: 1px solid #334155;
            color: #38BDF8;
            font-size: 9px;
            font-weight: bold;
            padding: 2px 6px;
            border-radius: 4px;
        """)

        h_top.addWidget(lbl_icon)
        h_top.addWidget(lbl_title, stretch=1)
        h_top.addWidget(cat_badge)
        layout.addLayout(h_top)

        # 2. Description
        if self.preset.description:
            lbl_desc = QLabel(self.preset.description)
            lbl_desc.setWordWrap(True)
            lbl_desc.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10.5px; line-height: 1.2;")
            layout.addWidget(lbl_desc)

        # 3. Tags Chips
        if self.preset.tags:
            h_tags = QHBoxLayout()
            h_tags.setSpacing(4)
            for t in self.preset.tags[:4]:
                lbl_tag = QLabel(f"#{t}")
                lbl_tag.setProperty("class", "tag_chip")
                lbl_tag.setStyleSheet("""
                    background-color: #161B26;
                    border: 1px solid #283348;
                    color: #94A3B8;
                    font-size: 9.5px;
                    padding: 1px 5px;
                    border-radius: 3px;
                """)
                h_tags.addWidget(lbl_tag)
            h_tags.addStretch(1)
            layout.addLayout(h_tags)

        # 4. Draggable Preview Box
        self.preview_box = DraggablePreviewLabel(self)
        self.preview_box.preset_getter = lambda: self.preset
        self.preview_box.sample_text_getter = self.sample_text_func or (lambda: self.preset.name)
        self.preview_box.setFixedHeight(75)
        self.preview_box.setAlignment(Qt.AlignCenter)
        self.preview_box.setStyleSheet("""
            background-color: #0A0D14;
            border: 1.5px dashed #2C354D;
            border-radius: 6px;
            padding: 2px;
        """)
        self._refresh_preview_pixmap()
        layout.addWidget(self.preview_box)

        # 5. Quick Actions Bar
        h_act = QHBoxLayout()
        h_act.setSpacing(6)

        self.btn_insert = QPushButton("🚀 Chèn Playhead")
        self.btn_insert.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.PRIMARY};
                border: 1px solid #1E40AF;
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 10px;
                font-weight: bold;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_insert.clicked.connect(lambda: self.insert_playhead_signal.emit(self.preset.id))

        self.btn_copy = QPushButton("📋 Copy Node")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 4px;
                color: #94A3B8;
                font-size: 10px;
                padding: 4px 6px;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #F8FAFC;
            }
        """)
        self.btn_copy.clicked.connect(lambda: self.copy_node_signal.emit(self.preset.id))

        lbl_drag_hint = QLabel("🖱️ Kéo vào Resolve ➔")
        lbl_drag_hint.setStyleSheet("color: #60A5FA; font-size: 10px; font-style: italic;")

        h_act.addWidget(self.btn_insert)
        h_act.addWidget(self.btn_copy)
        h_act.addStretch(1)
        h_act.addWidget(lbl_drag_hint)
        layout.addLayout(h_act)

    def _refresh_preview_pixmap(self):
        """Sinh ảnh xem trước cho Card."""
        try:
            temp_img = os.path.join(tempfile.gettempdir(), f"rf_card_{self.preset.id}.png")
            TextPreviewRenderer.render_preview_to_file(
                preset=self.preset,
                output_image_path=temp_img,
                sample_words=[self.preset.name.split()[0], "Motion", "FX"],
                active_index=0,
                aspect_ratio="16:9",
                width=320,
                height=120
            )
            pix = QPixmap(temp_img)
            self.preview_box.setPixmap(pix.scaled(280, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.preview_box.setText(f"🎨 {self.preset.name}")

    def set_selected(self, selected: bool):
        self.is_selected = selected
        if selected:
            self.setStyleSheet("""
                QFrame.technique_card {
                    background-color: #1A2436;
                    border: 2px solid #3B82F6;
                    border-radius: 10px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame.technique_card {
                    background-color: #12151F;
                    border: 1.5px solid #232A3B;
                    border-radius: 10px;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected_signal.emit(self.preset.id)
        super().mousePressEvent(event)


class TabTitles(QWidget):
    """
    Giao diện Tab 2: Visual Techniques & Text Presets Library (Phong cách Eyecandy / Mixkit)
    """
    insert_title_requested = pyqtSignal(str, str, float)  # text, preset_id, duration
    install_presets_requested = pyqtSignal()
    copy_fusion_node_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_preset_callback = None
        self.all_presets: List[TextStylePreset] = BUILTIN_PRESETS.copy()
        self.current_category = "all"
        self.card_widgets: List[VisualTechniqueCard] = []
        self._init_ui()

    def set_preset_provider(self, provider_func: Callable[[], Optional[TextStylePreset]]):
        """Thiết lập hàm cung cấp đối tượng Preset hiện tại cho Draggable Label."""
        self._current_preset_callback = provider_func
        self.preview_lbl.preset_getter = provider_func
        self.preview_lbl.sample_text_getter = lambda: self.txt_single_title.text().strip() or "ResolveFlow Title"

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content_widget = QWidget()
        panel = QVBoxLayout(content_widget)
        panel.setContentsMargins(6, 6, 6, 6)
        panel.setSpacing(8)
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)

        # 1. Header Guide Banner (Eyecandy / Mixkit inspiration)
        self.banner = QFrame()
        self.banner.setStyleSheet(f"""
            QFrame {{
                background-color: #0E1726;
                border: 1px solid #1E3A5F;
                border-left: 4px solid #3B82F6;
                border-radius: 8px;
                padding: 6px 10px;
            }}
        """)
        b_lay = QVBoxLayout(self.banner)
        b_lay.setContentsMargins(8, 6, 8, 6)
        lbl_t = QLabel("✨ <b>THƯ VIỆN KỸ XẢO THỊ GIÁC & CHỮ ĐỘNG (VISUAL TECHNIQUE LIBRARY)</b>")
        lbl_t.setStyleSheet("color: #60A5FA; font-size: 12.5px; font-weight: bold;")
        lbl_d = QLabel("Lấy cảm hứng từ <b>Eyecandy</b> & <b>Mixkit</b>: Kéo thẻ kỹ xảo thả thẳng vào clip trên Timeline để biến đổi phong cách Text tức thì (100% chạy trên bản Thường & Studio)!")
        lbl_d.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        b_lay.addWidget(lbl_t)
        b_lay.addWidget(lbl_d)
        panel.addWidget(self.banner)

        # 2. CATEGORY FILTER BAR & SEARCH (Eyecandy / Shotdeck taxonomy)
        filter_box = QFrame()
        filter_box.setStyleSheet("background-color: #10141E; border: 1px solid #1E2638; border-radius: 8px; padding: 6px;")
        f_lay = QVBoxLayout(filter_box)
        f_lay.setContentsMargins(4, 4, 4, 4)
        f_lay.setSpacing(6)

        # Search line edit
        h_search = QHBoxLayout()
        lbl_s_icon = QLabel("🔍")
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Tìm kỹ xảo (ví dụ: Hormozi, Dạ Quang, Xé Giấy, Glitch, Neon, VHS, Minimal)...")
        self.txt_search.textChanged.connect(self._filter_cards)
        h_search.addWidget(lbl_s_icon)
        h_search.addWidget(self.txt_search)
        f_lay.addLayout(h_search)

        # Category Pills
        self.h_pills = QHBoxLayout()
        self.h_pills.setSpacing(6)

        self.pill_buttons = {}
        categories = [
            ("all", "🌐 Tất cả"),
            ("kinetic", "💥 Kinetic & TikTok"),
            ("highlighter_paper", "🖍️ Dạ Quang & Xé Giấy"),
            ("glitch_cyber", "⚡ Glitch & Cyberpunk"),
            ("retro_film", "📺 Retro VHS & Film"),
            ("clean_minimal", "🧊 Clean & Minimal")
        ]

        for cat_id, cat_name in categories:
            btn_pill = QPushButton(cat_name)
            btn_pill.setProperty("class", "category_pill")
            btn_pill.setCursor(Qt.PointingHandCursor)
            if cat_id == "all":
                btn_pill.setProperty("active", "true")
            btn_pill.clicked.connect(lambda _, c=cat_id: self._on_category_pill_clicked(c))
            self.pill_buttons[cat_id] = btn_pill
            self.h_pills.addWidget(btn_pill)

        self.h_pills.addStretch(1)
        f_lay.addLayout(self.h_pills)
        panel.addWidget(filter_box)

        # 3. VISUAL TECHNIQUE CARDS GRID
        self.group_cards = QGroupBox("🃏 DANH SÁCH THẺ KỸ XẢO THỊ GIÁC (CLICK HOẶC KÉO THẢ VÀO TIMELINE)")
        cards_layout = QVBoxLayout(self.group_cards)
        cards_layout.setContentsMargins(6, 12, 6, 6)

        self.cards_container = QWidget()
        self.grid_cards = QGridLayout(self.cards_container)
        self.grid_cards.setContentsMargins(0, 0, 0, 0)
        self.grid_cards.setSpacing(8)

        cards_layout.addWidget(self.cards_container)
        panel.addWidget(self.group_cards)

        # Populate cards
        self._populate_cards()

        # 4. FAST CONTROLS & BATCH SUBTITLE SETTINGS
        self.group_presets = QGroupBox("⚙️ TÙY BIẾN CHI TIẾT & CHÈN PHỤ ĐỀ HÀNG LOẠT")
        form_p = QFormLayout(self.group_presets)

        self.check_subtitle = QCheckBox("Kích hoạt tạo phụ đề toàn bộ video (Auto Subtitles từ Whisper)")
        self.check_subtitle.setChecked(True)
        form_p.addRow(self.check_subtitle)

        h_preset = QHBoxLayout()
        self.combo_text_preset = QComboBox()
        self.btn_preview_preset = QPushButton("👁 Xem Trước Lớn")
        self.btn_save_custom_preset = QPushButton("➕ Lưu Preset Tùy Chỉnh...")
        h_preset.addWidget(self.combo_text_preset, stretch=3)
        h_preset.addWidget(self.btn_preview_preset, stretch=2)
        h_preset.addWidget(self.btn_save_custom_preset, stretch=2)
        form_p.addRow("Preset Kiểu Chữ Đang Chọn:", h_preset)

        # Master Draggable Real-time Live Preview
        self.preview_lbl = DraggablePreviewLabel()
        self.preview_lbl.setAlignment(Qt.AlignCenter)
        self.preview_lbl.setFixedHeight(100)
        self.preview_lbl.setStyleSheet("background-color: #0B0E14; border: 1.5px dashed #2B354D; border-radius: 6px; padding: 2px;")
        form_p.addRow("Master Kéo-Thả Trực Tiếp:", self.preview_lbl)

        # Nút cài đặt Preset vào DaVinci Resolve Library & Copy Node
        h_install = QHBoxLayout()
        self.btn_install_presets = QPushButton("📥 Cài Đặt Toàn Bộ Presets Vào DaVinci Resolve (Effects Library)")
        self.btn_install_presets.setStyleSheet(f"""
            QPushButton {{
                background-color: #1A233A;
                border: 1px solid #2B3A5A;
                color: #90CAF9;
                font-weight: bold;
                padding: 7px 10px;
            }}
            QPushButton:hover {{
                background-color: #263554;
                border-color: #42A5F5;
            }}
        """)
        self.btn_install_presets.clicked.connect(self._on_install_presets_clicked)

        self.btn_copy_fusion = QPushButton("📋 Copy Fusion Node (Ctrl+V)")
        self.btn_copy_fusion.clicked.connect(self._on_copy_fusion_clicked)

        h_install.addWidget(self.btn_install_presets, stretch=3)
        h_install.addWidget(self.btn_copy_fusion, stretch=2)
        form_p.addRow(h_install)

        panel.addWidget(self.group_presets)

        # 5. TYPOGRAPHY & SPLIT SETTINGS
        self.group_format = QGroupBox("📐 TÙY CHỈNH PHÔNG CHỮ & NGẮT DÒNG")
        form_f = QFormLayout(self.group_format)

        self.combo_split_mode = QComboBox()
        self.combo_split_mode.addItem("Số ký tự tối đa (Max Characters)", "characters")
        self.combo_split_mode.addItem("Số từ tối đa (Max Words - Kiểu Shorts)", "words")
        form_f.addRow("Chế độ ngắt câu:", self.combo_split_mode)

        self.txt_split_limit = QLineEdit("42")
        form_f.addRow("Giới hạn ngắt dòng:", self.txt_split_limit)

        self.txt_font = QLineEdit("Arial")
        form_f.addRow("Phông chữ (Font):", self.txt_font)

        self.txt_size = QLineEdit("48")
        form_f.addRow("Cỡ chữ (Size):", self.txt_size)

        self.txt_color = QLineEdit("#FFFFFF")
        form_f.addRow("Màu chữ chính (Hex):", self.txt_color)

        panel.addWidget(self.group_format)

        # 6. INTERACTIVE SINGLE TITLE INSERT AT PLAYHEAD
        self.group_single = QGroupBox("⚡ CHÈN TIÊU ĐỀ TẠI CON TRỎ (INSERT AT PLAYHEAD)")
        form_s = QFormLayout(self.group_single)
        desc_s = QLabel("Nhập câu tiêu đề hoặc từ nhấn để chèn ngay 1 Text+ Clip vào đúng vị trí Playhead trên Timeline Resolve.")
        desc_s.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_s.addRow(desc_s)

        self.txt_single_title = QLineEdit()
        self.txt_single_title.setPlaceholderText("Nhập nội dung tiêu đề (Ví dụ: Bí Quyết Đạt 1 Triệu View)...")
        form_s.addRow("Nội dung Text:", self.txt_single_title)

        h_dur = QHBoxLayout()
        self.slide_title_dur = QSlider(Qt.Horizontal)
        self.slide_title_dur.setRange(10, 100) # 1.0s - 10.0s
        self.slide_title_dur.setValue(30) # 3.0s
        self.lbl_title_dur = QLabel("3.0 s")
        self.slide_title_dur.valueChanged.connect(lambda v: self.lbl_title_dur.setText(f"{v/10:.1f} s"))
        h_dur.addWidget(self.slide_title_dur)
        h_dur.addWidget(self.lbl_title_dur)
        form_s.addRow("Thời lượng xuất hiện:", h_dur)

        self.btn_insert_title_playhead = QPushButton("🚀 Chèn Tiêu Đề Vào Playhead (Video Track 2)")
        self.btn_insert_title_playhead.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.PRIMARY};
                font-weight: bold;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_insert_title_playhead.clicked.connect(self._on_insert_title_clicked)
        form_s.addRow(self.btn_insert_title_playhead)

        panel.addWidget(self.group_single)

        # Connect toggling
        self.check_subtitle.toggled.connect(self.combo_text_preset.setEnabled)
        self.check_subtitle.toggled.connect(self.combo_split_mode.setEnabled)
        self.check_subtitle.toggled.connect(self.txt_split_limit.setEnabled)
        self.check_subtitle.toggled.connect(self.txt_font.setEnabled)
        self.check_subtitle.toggled.connect(self.txt_size.setEnabled)
        self.check_subtitle.toggled.connect(self.txt_color.setEnabled)

        # Connect combo change to highlight card
        self.combo_text_preset.currentIndexChanged.connect(self._on_combo_preset_changed)

        # Tooltips
        self.combo_text_preset.setToolTip(TOOLTIPS["text_preset"])
        self.combo_split_mode.setToolTip(TOOLTIPS["split_mode"])
        self.txt_split_limit.setToolTip(TOOLTIPS["split_limit"])
        self.txt_font.setToolTip(TOOLTIPS["font_name"])
        self.txt_size.setToolTip(TOOLTIPS["font_size"])
        self.txt_color.setToolTip(TOOLTIPS["font_color"])

    def _populate_cards(self):
        """Khởi tạo danh sách các Card kỹ xảo vào grid."""
        # Clear existing
        for c in self.card_widgets:
            c.setParent(None)
            c.deleteLater()
        self.card_widgets.clear()

        # Clear grid layout items
        while self.grid_cards.count():
            item = self.grid_cards.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # Create cards
        for idx, preset in enumerate(self.all_presets):
            card = VisualTechniqueCard(
                preset=preset,
                sample_text_func=lambda: self.txt_single_title.text().strip() or "ResolveFlow Title",
                parent=self.cards_container
            )
            card.selected_signal.connect(self._on_card_selected)
            card.insert_playhead_signal.connect(self._on_card_insert_playhead)
            card.copy_node_signal.connect(self._on_card_copy_node)
            self.card_widgets.append(card)

        self._render_grid_layout(self.card_widgets)

    def _render_grid_layout(self, visible_cards: List[VisualTechniqueCard]):
        """Sắp xếp các card hiển thị vào 2 cột responsive."""
        while self.grid_cards.count():
            item = self.grid_cards.takeAt(0)

        cols = 2
        for idx, card in enumerate(visible_cards):
            card.setVisible(True)
            r = idx // cols
            c = idx % cols
            self.grid_cards.addWidget(card, r, c)

    def _on_category_pill_clicked(self, category_id: str):
        self.current_category = category_id
        for cat_id, btn in self.pill_buttons.items():
            if cat_id == category_id:
                btn.setProperty("active", "true")
                btn.setStyleSheet("background-color: #2563EB; border-color: #60A5FA; color: #FFFFFF;")
            else:
                btn.setProperty("active", "false")
                btn.setStyleSheet("background-color: #161A26; border: 1px solid #283144; color: #94A3B8;")
        self._filter_cards()

    def _filter_cards(self):
        query = self.txt_search.text().strip().lower()
        visible_cards = []

        for card in self.card_widgets:
            p = card.preset
            match_cat = (self.current_category == "all" or p.category == self.current_category)
            
            match_search = True
            if query:
                searchable_text = f"{p.name} {p.category} {p.description} {' '.join(p.tags)}".lower()
                match_search = query in searchable_text

            if match_cat and match_search:
                visible_cards.append(card)
            else:
                card.setVisible(False)

        self._render_grid_layout(visible_cards)

    def _on_card_selected(self, preset_id: str):
        # Update combo
        idx = self.combo_text_preset.findData(preset_id)
        if idx >= 0:
            self.combo_text_preset.setCurrentIndex(idx)
        self._highlight_selected_card(preset_id)

    def _on_combo_preset_changed(self):
        preset_id = self.combo_text_preset.currentData()
        if preset_id:
            self._highlight_selected_card(preset_id)

    def _highlight_selected_card(self, preset_id: str):
        for card in self.card_widgets:
            if card.preset.id == preset_id:
                card.set_selected(True)
            else:
                card.set_selected(False)

    def _on_card_insert_playhead(self, preset_id: str):
        text = self.txt_single_title.text().strip()
        if not text:
            p = next((x for x in self.all_presets if x.id == preset_id), None)
            text = p.name if p else "ResolveFlow Title"
        dur = self.slide_title_dur.value() / 10.0
        self.insert_title_requested.emit(text, preset_id, dur)

    def _on_card_copy_node(self, preset_id: str):
        p = next((x for x in self.all_presets if x.id == preset_id), None)
        if not p:
            return
        sample_txt = self.txt_single_title.text().strip() or p.name
        content = FusionSettingGenerator.generate_setting_content(p, sample_text=sample_txt)
        QApplication.clipboard().setText(content)
        self.copy_fusion_node_requested.emit(p.id)
        QMessageBox.information(
            self,
            "Đã Copy Fusion Node!",
            f"Đã sao chép cấu hình kỹ xảo '{p.name}' vào Clipboard!\n\n"
            "👉 Hãy chuyển sang DaVinci Resolve và bấm phím tắt Ctrl + V (hoặc paste vào Fusion Page) để chèn!"
        )

    def _on_insert_title_clicked(self):
        text = self.txt_single_title.text().strip()
        if not text:
            preset_id = self.combo_text_preset.currentData() or "kinetic_hormozi"
            p = next((x for x in self.all_presets if x.id == preset_id), None)
            text = p.name if p else "ResolveFlow Title"
        preset_id = self.combo_text_preset.currentData() or "kinetic_hormozi"
        dur = self.slide_title_dur.value() / 10.0
        self.insert_title_requested.emit(text, preset_id, dur)

    def _on_install_presets_clicked(self):
        """Cài đặt presets vào thư mục Effects Library của DaVinci Resolve."""
        try:
            count, path = FusionSettingGenerator.install_presets_to_davinci_resolve(self.all_presets)
            self.install_presets_requested.emit()
            QMessageBox.information(
                self,
                "Cài Đặt Thành Công!",
                f"Đã tự động cài đặt {count} Visual Technique Presets vào DaVinci Resolve!\n\n"
                f"📂 Đường dẫn:\n{path}\n\n"
                "👉 Trong DaVinci Resolve: Mở 'Effects' -> 'Titles' -> 'ResolveFlow' để kéo thả trực tiếp!"
            )
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cài Đặt", f"Không thể cài đặt presets: {str(e)}")

    def _on_copy_fusion_clicked(self):
        """Sao chép cấu hình Fusion Text+ Node vào Clipboard."""
        preset = self._current_preset_callback() if callable(self._current_preset_callback) else None
        if not preset:
            return
        sample_txt = self.txt_single_title.text().strip() or preset.name
        content = FusionSettingGenerator.generate_setting_content(preset, sample_text=sample_txt)
        QApplication.clipboard().setText(content)
        self.copy_fusion_node_requested.emit(preset.id)
        QMessageBox.information(
            self,
            "Đã Copy Fusion Node!",
            f"Đã sao chép cấu hình kiểu chữ '{preset.name}' vào Clipboard!\n\n"
            "👉 Hãy chuyển sang DaVinci Resolve và bấm phím tắt Ctrl + V (hoặc paste vào Fusion Page) để chèn!"
        )
