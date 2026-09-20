"""
Tab 4: Polish, Color Grading Suite & Export (Shotdeck & DaVinci Resolve Master)
Bao gồm:
- Thư viện Color Looks & 3D LUTs chuẩn điện ảnh (Shotdeck / Film Mood Palette)
- 10 Phong cách màu sắc: Clean Rec.709, Warm Vlog, Korean Pastel, Cinematic Teal & Orange,
  Cyberpunk Neon, Moody Dark, Golden Hour, Anime Ghibli, Vintage Kodak 2383, Noir B&W
- Kéo-Thả Trực Tiếp file .cube (Drag & Drop 3D LUT) vào DaVinci Resolve Timeline / Color Page
- Bảng Palette Swatches trực quan 4 màu
- Cấu hình xuất bản nhanh 1-Click Export Presets (TikTok 9:16, YouTube 1080p, YouTube 4K, Podcast)
- Điều khiển trực tiếp Render Queue của DaVinci Resolve Deliver Page
"""

import os
from typing import Optional, List, Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QMimeData, QUrl
from PySide6.QtGui import QDrag, QPixmap
from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS
from src.core.lut_generator import ColorLookPreset, BUILTIN_COLOR_LOOKS, LUT3DGenerator


class DraggableLUTCard(QFrame):
    """
    Thẻ màu sắc (Color Look Card - Shotdeck Style).
    Hỗ trợ Kéo-Thả (Drag & Drop) file .cube trực tiếp vào DaVinci Resolve Color Page.
    """
    selected_signal = pyqtSignal(str) # look_id
    apply_signal = pyqtSignal(str)    # look_id

    def __init__(self, look: ColorLookPreset, luts_dir: str, parent=None):
        super().__init__(parent)
        self.look = look
        self.luts_dir = luts_dir
        self.drag_start_pos = None
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
        lbl_icon = QLabel(self.look.badge_icon or "🎨")
        lbl_icon.setStyleSheet("font-size: 16px;")

        lbl_title = QLabel(f"<b>{self.look.name}</b>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 12px;")

        cat_badge = QLabel(self.look.category.upper())
        cat_badge.setStyleSheet("""
            background-color: #1E293B;
            border: 1px solid #334155;
            color: #A855F7;
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
        if self.look.description:
            lbl_desc = QLabel(self.look.description)
            lbl_desc.setWordWrap(True)
            lbl_desc.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10.5px; line-height: 1.2;")
            layout.addWidget(lbl_desc)

        # 3. 4-Color Palette Swatch Bar
        if self.look.palette_hex:
            h_pal = QHBoxLayout()
            h_pal.setSpacing(4)
            for hex_col in self.look.palette_hex:
                color_bar = QFrame()
                color_bar.setFixedHeight(12)
                color_bar.setStyleSheet(f"background-color: {hex_col}; border-radius: 3px; border: 1px solid rgba(255,255,255,0.15);")
                h_pal.addWidget(color_bar)
            layout.addLayout(h_pal)

        # 4. Tags
        if self.look.tags:
            h_tags = QHBoxLayout()
            h_tags.setSpacing(4)
            for t in self.look.tags[:4]:
                lbl_tag = QLabel(f"#{t}")
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

        # 5. Actions Bar
        h_act = QHBoxLayout()
        self.btn_apply = QPushButton("✨ Áp Dụng Màu")
        self.btn_apply.setStyleSheet(f"""
            QPushButton {{
                background-color: #7C3AED;
                border: 1px solid #6D28D9;
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 10.5px;
                font-weight: bold;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: #8B5CF6;
            }}
        """)
        self.btn_apply.clicked.connect(lambda: self.apply_signal.emit(self.look.id))

        lbl_drag_hint = QLabel("🖱️ Kéo .cube vào Resolve ➔")
        lbl_drag_hint.setStyleSheet("color: #C084FC; font-size: 10px; font-style: italic;")

        h_act.addWidget(self.btn_apply)
        h_act.addStretch(1)
        h_act.addWidget(lbl_drag_hint)
        layout.addLayout(h_act)

    def set_selected(self, selected: bool):
        if selected:
            self.setStyleSheet("""
                QFrame.technique_card {
                    background-color: #211832;
                    border: 2px solid #A855F7;
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
            self.drag_start_pos = event.pos()
            self.selected_signal.emit(self.look.id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < 8:
            return

        lut_file_path = os.path.join(self.luts_dir, self.look.file_name)
        if not os.path.exists(lut_file_path):
            LUT3DGenerator.ensure_all_luts_exist(self.luts_dir)

        if os.path.exists(lut_file_path):
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setUrls([QUrl.fromLocalFile(lut_file_path)])
            drag.setMimeData(mime_data)
            drag.exec_(Qt.CopyAction)


class TabExport(QWidget):
    """
    Giao diện Tab 4: Hoàn thiện màu sắc & Xuất bản (Polish & Export)
    """
    render_requested = pyqtSignal(str, str)  # preset_name, custom_name
    apply_lut_requested = pyqtSignal(str)    # lut_name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.luts_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "luts"))
        os.makedirs(self.luts_dir, exist_ok=True)
        LUT3DGenerator.ensure_all_luts_exist(self.luts_dir)
        
        self.all_looks: List[ColorLookPreset] = BUILTIN_COLOR_LOOKS.copy()
        self.current_category = "all"
        self.card_widgets: List[DraggableLUTCard] = []
        self._init_ui()

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

        # 1. Header Banner
        self.banner = QFrame()
        self.banner.setStyleSheet(f"""
            QFrame {{
                background-color: #171026;
                border: 1px solid #3B1D5F;
                border-left: 4px solid #A855F7;
                border-radius: 8px;
                padding: 6px 10px;
            }}
        """)
        b_lay = QVBoxLayout(self.banner)
        b_lay.setContentsMargins(8, 6, 8, 6)
        lbl_t = QLabel("🎨 <b>THƯ VIỆN PHONG CÁCH MÀU SẮC & 1-CLICK EXPORT SUITE</b>")
        lbl_t.setStyleSheet("color: #C084FC; font-size: 12.5px; font-weight: bold;")
        lbl_d = QLabel("Áp dụng các tone màu chuẩn điện ảnh (3D .cube LUTs) hoặc kéo thả trực tiếp sang DaVinci Resolve Color Page.")
        lbl_d.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        b_lay.addWidget(lbl_t)
        b_lay.addWidget(lbl_d)
        panel.addWidget(self.banner)

        # 2. COLOR CATEGORY FILTER BAR & SEARCH (Shotdeck style)
        filter_box = QFrame()
        filter_box.setStyleSheet("background-color: #10141E; border: 1px solid #1E2638; border-radius: 8px; padding: 6px;")
        f_lay = QVBoxLayout(filter_box)
        f_lay.setContentsMargins(4, 4, 4, 4)
        f_lay.setSpacing(6)

        # Search line edit
        h_search = QHBoxLayout()
        lbl_s_icon = QLabel("🔍")
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Tìm phong cách màu (ví dụ: Teal & Orange, Pastel Hàn Quốc, Cyberpunk, Moody, Kodak)...")
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
            ("cinematic", "🎬 Điện Ảnh (Teal/Moody)"),
            ("vlog", "🌸 Pastel & Vlog"),
            ("cyber", "⚡ Cyberpunk & Anime"),
            ("vintage", "🎞 Vintage Film"),
            ("bw", "🖤 Đen Trắng Noir")
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

        # 3. COLOR LOOKS GRID
        self.group_color = QGroupBox("🎨 THƯ VIỆN BỘ MÀU CHUẨN (CLICK CHỌN HOẶC KÉO THẢ .CUBE)")
        color_layout = QVBoxLayout(self.group_color)
        color_layout.setContentsMargins(6, 12, 6, 6)

        self.cards_container = QWidget()
        self.grid_cards = QGridLayout(self.cards_container)
        self.grid_cards.setContentsMargins(0, 0, 0, 0)
        self.grid_cards.setSpacing(8)
        color_layout.addWidget(self.cards_container)

        panel.addWidget(self.group_color)
        self._populate_cards()

        # 4. COLOR LOOK SELECTOR DROPDOWN (Compatibility)
        self.group_lut_ctrl = QGroupBox("🎚 ĐIỀU KHIỂN CHUẨN MÀU NHANH")
        form_c = QFormLayout(self.group_lut_ctrl)

        self.combo_lut = QComboBox()
        for look in self.all_looks:
            self.combo_lut.addItem(f"{look.badge_icon} {look.name}", look.id)
        self.combo_lut.currentIndexChanged.connect(self._on_combo_lut_changed)
        form_c.addRow("Bộ màu đang chọn:", self.combo_lut)

        h_lut_btns = QHBoxLayout()
        self.btn_apply_lut = QPushButton("✨ Áp Dụng Bộ Màu Này Vào Timeline DaVinci Resolve")
        self.btn_apply_lut.setStyleSheet(f"""
            QPushButton {{
                background-color: #7C3AED;
                border: 1px solid #6D28D9;
                color: #FFFFFF;
                font-weight: bold;
                padding: 8px 12px;
            }}
            QPushButton:hover {{
                background-color: #8B5CF6;
            }}
        """)
        self.btn_apply_lut.clicked.connect(self._on_apply_lut_clicked)

        btn_open_luts = QPushButton("📂 Mở Thư Mục 10 LUTs (.cube)...")
        btn_open_luts.clicked.connect(self._open_luts_folder)

        h_lut_btns.addWidget(self.btn_apply_lut, stretch=3)
        h_lut_btns.addWidget(btn_open_luts, stretch=2)
        form_c.addRow(h_lut_btns)

        panel.addWidget(self.group_lut_ctrl)

        # 5. 1-CLICK RENDER PRESETS
        self.group_render = QGroupBox("🚀 CẤU HÌNH XUẤT BẢN NHANH (1-CLICK EXPORT)")
        form_r = QFormLayout(self.group_render)

        desc_r = QLabel("Tự động thiết lập định dạng, tỷ lệ và bitrate tối ưu cho thuật toán của từng nền tảng.")
        desc_r.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_r.addRow(desc_r)

        self.combo_render_preset = QComboBox()
        self.combo_render_preset.addItem("📱 TikTok / Reels / Shorts (9:16 - 1080x1920 60fps - 15Mbps)", "tiktok_916")
        self.combo_render_preset.addItem("📺 YouTube Full HD (16:9 - 1920x1080 60fps - 25Mbps)", "youtube_1080p")
        self.combo_render_preset.addItem("💎 YouTube 4K Ultra HD (16:9 - 3840x2160 60fps - H.265 Master)", "youtube_4k")
        self.combo_render_preset.addItem("🎙 Podcast Audio Only (WAV 48kHz Master / 320kbps)", "podcast_audio")
        form_r.addRow("Mục tiêu xuất bản:", self.combo_render_preset)

        self.txt_custom_render_name = QLineEdit()
        self.txt_custom_render_name.setPlaceholderText("Để trống sẽ tự động đặt tên theo Project...")
        form_r.addRow("Tên tệp xuất bản:", self.txt_custom_render_name)

        panel.addWidget(self.group_render)

        # 6. ACTION BUTTON: ADD TO RENDER QUEUE & START
        self.group_render_act = QGroupBox("⚡ KẾT XUẤT DAVINCI RESOLVE")
        vbox_ra = QVBoxLayout(self.group_render_act)

        self.btn_start_render = QPushButton("🚀 Thêm Vào Render Queue & Bắt Đầu Render")
        self.btn_start_render.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.SUCCESS};
                color: #FFFFFF;
                font-weight: bold;
                font-size: 13px;
                padding: 10px 14px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.SUCCESS_HOVER};
            }}
        """)
        self.btn_start_render.clicked.connect(self._on_start_render_clicked)
        vbox_ra.addWidget(self.btn_start_render)

        self.lbl_render_info = QLabel("Resolve API: Sẵn sàng gửi lệnh kết xuất sang DaVinci Resolve Deliver Page.")
        self.lbl_render_info.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        vbox_ra.addWidget(self.lbl_render_info)

        panel.addWidget(self.group_render_act)

    def _populate_cards(self):
        for c in self.card_widgets:
            c.setParent(None)
            c.deleteLater()
        self.card_widgets.clear()

        while self.grid_cards.count():
            item = self.grid_cards.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        for idx, look in enumerate(self.all_looks):
            card = DraggableLUTCard(look=look, luts_dir=self.luts_dir, parent=self.cards_container)
            card.selected_signal.connect(self._on_card_selected)
            card.apply_signal.connect(self._on_card_apply)
            self.card_widgets.append(card)

        self._render_grid_layout(self.card_widgets)

    def _render_grid_layout(self, visible_cards: List[DraggableLUTCard]):
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
                btn.setStyleSheet("background-color: #7C3AED; border-color: #A855F7; color: #FFFFFF;")
            else:
                btn.setProperty("active", "false")
                btn.setStyleSheet("background-color: #161A26; border: 1px solid #283144; color: #94A3B8;")
        self._filter_cards()

    def _filter_cards(self):
        query = self.txt_search.text().strip().lower()
        visible_cards = []

        for card in self.card_widgets:
            look = card.look
            match_cat = (self.current_category == "all" or look.category == self.current_category)
            
            match_search = True
            if query:
                searchable_text = f"{look.name} {look.category} {look.description} {' '.join(look.tags)}".lower()
                match_search = query in searchable_text

            if match_cat and match_search:
                visible_cards.append(card)
            else:
                card.setVisible(False)

        self._render_grid_layout(visible_cards)

    def _on_card_selected(self, look_id: str):
        idx = self.combo_lut.findData(look_id)
        if idx >= 0:
            self.combo_lut.setCurrentIndex(idx)
        self._highlight_selected_card(look_id)

    def _on_combo_lut_changed(self):
        look_id = self.combo_lut.currentData()
        if look_id:
            self._highlight_selected_card(look_id)

    def _highlight_selected_card(self, look_id: str):
        for card in self.card_widgets:
            if card.look.id == look_id:
                card.set_selected(True)
            else:
                card.set_selected(False)

    def _on_card_apply(self, look_id: str):
        self.apply_lut_requested.emit(look_id)

    def _on_apply_lut_clicked(self):
        lut_id = self.combo_lut.currentData() or "clean_rec709"
        self.apply_lut_requested.emit(lut_id)

    def _on_start_render_clicked(self):
        preset_id = self.combo_render_preset.currentData() or "tiktok_916"
        cname = self.txt_custom_render_name.text().strip()
        self.render_requested.emit(preset_id, cname)

    def _open_luts_folder(self):
        if os.path.exists(self.luts_dir):
            os.startfile(self.luts_dir)
