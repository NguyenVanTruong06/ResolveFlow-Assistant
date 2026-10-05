import os
import tempfile
import uuid
from typing import Optional, Callable, List, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox,
    QSizePolicy
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QMimeData, QUrl, QTimer
from PySide6.QtGui import QDrag, QPixmap, QCursor
from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS
from src.core.text_preset import TextStylePreset, BUILTIN_PRESETS, FusionSettingGenerator, TextPreviewRenderer
from src.core.transition_preset import TransitionStylePreset, BUILTIN_TRANSITIONS, TransitionMacroGenerator


class DraggableAssetLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.preset_getter: Optional[Callable[[], Any]] = None
        self.sample_text_getter: Optional[Callable[[], str]] = None
        self.drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("🖱️ Kéo thả vào DaVinci Resolve Timeline!")
        self.setAlignment(Qt.AlignCenter)
        
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

        temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{preset.id}_{uuid.uuid4().hex[:6]}.setting")
        
        if isinstance(preset, TextStylePreset):
            sample_txt = self.sample_text_getter() if callable(self.sample_text_getter) else preset.name
            if not sample_txt: sample_txt = preset.name
            FusionSettingGenerator.export_setting_file(preset, temp_setting, sample_text=sample_txt)
        elif isinstance(preset, TransitionStylePreset):
            TransitionMacroGenerator.export_setting_file(preset, temp_setting)

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
        drag.setMimeData(mime_data)
        
        self.setCursor(Qt.OpenHandCursor)
        drag.exec_(Qt.CopyAction)


class AssetCard(QFrame):
    selected_signal = pyqtSignal(str) # preset_id
    insert_playhead_signal = pyqtSignal(str)
    copy_node_signal = pyqtSignal(str)

    def __init__(self, preset: Any, sample_text_func=None, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.sample_text_func = sample_text_func
        self.is_selected = False
        self.setProperty("class", "asset_card")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        h_top = QHBoxLayout()
        lbl_icon = QLabel(self.preset.badge_icon or "✨")
        lbl_title = QLabel(f"<b>{self.preset.name}</b>")
        lbl_title.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 12px;")
        
        cat_str = self.preset.category.upper() if hasattr(self.preset, 'category') else "EFFECT"
        cat_badge = QLabel(cat_str)
        cat_badge.setStyleSheet("""
            background-color: #1E293B; border: 1px solid #334155;
            color: #38BDF8; font-size: 9px; font-weight: bold;
            padding: 2px 6px; border-radius: 4px;
        """)

        h_top.addWidget(lbl_icon)
        h_top.addWidget(lbl_title, stretch=1)
        h_top.addWidget(cat_badge)
        layout.addLayout(h_top)

        self.preview_box = DraggableAssetLabel(self)
        self.preview_box.preset_getter = lambda: self.preset
        self.preview_box.sample_text_getter = self.sample_text_func
        self.preview_box.setFixedHeight(60)
        self.preview_box.setStyleSheet("background-color: #0A0D14; border: 1.5px dashed #2C354D; border-radius: 6px;")
        
        if isinstance(self.preset, TextStylePreset):
            self.preview_box.setText("T: Text+ Macro\n(Kéo thả)")
        elif isinstance(self.preset, TransitionStylePreset):
            self.preview_box.setText("Tr: Transition Macro\n(Kéo thả)")
            
        layout.addWidget(self.preview_box)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        if selected:
            self.setStyleSheet("QFrame.asset_card { background-color: #1A2436; border: 2px solid #3B82F6; border-radius: 10px; }")
        else:
            self.setStyleSheet("QFrame.asset_card { background-color: #12151F; border: 1.5px solid #232A3B; border-radius: 10px; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected_signal.emit(self.preset.id)
        super().mousePressEvent(event)


class TabAssets(QWidget):
    insert_title_requested = pyqtSignal(str, str, float)
    install_presets_requested = pyqtSignal()
    copy_fusion_node_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_preset_callback = None
        self.all_presets = BUILTIN_PRESETS.copy()
        self.all_assets = BUILTIN_PRESETS + BUILTIN_TRANSITIONS
        self.current_category = "all"
        self.card_widgets = []
        self._init_ui()

    def set_preset_provider(self, provider_func: Callable[[], Optional[Any]]):
        self._current_preset_callback = provider_func
        self.preview_lbl.preset_getter = provider_func
        self.preview_lbl.sample_text_getter = lambda: self.txt_single_title.text().strip() or "ResolveFlow Title"

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- UI TƯƠNG THÍCH VỚI APP.PY ---
        # Tương thích nhóm Settings Phụ đề
        self.group_presets = QGroupBox("⚙️ TÙY BIẾN CHI TIẾT & CHÈN PHỤ ĐỀ HÀNG LOẠT")
        form_p = QFormLayout(self.group_presets)
        self.check_subtitle = QCheckBox("Kích hoạt tạo phụ đề toàn bộ video")
        self.check_subtitle.setChecked(True)
        form_p.addRow(self.check_subtitle)

        h_preset = QHBoxLayout()
        self.combo_text_preset = QComboBox()
        self.btn_preview_preset = QPushButton("👁 Xem Trước")
        self.btn_save_custom_preset = QPushButton("➕ Lưu Preset...")
        h_preset.addWidget(self.combo_text_preset)
        h_preset.addWidget(self.btn_preview_preset)
        h_preset.addWidget(self.btn_save_custom_preset)
        form_p.addRow("Preset Kiểu Chữ:", h_preset)

        self.preview_lbl = DraggableAssetLabel()
        self.preview_lbl.setFixedHeight(50)
        self.preview_lbl.setStyleSheet("background-color: #0B0E14; border: 1px dashed #2B354D; border-radius: 4px;")
        form_p.addRow("Master Kéo-Thả:", self.preview_lbl)
        
        self.btn_install_presets = QPushButton("📥 Cài Đặt Toàn Bộ Tài Nguyên (Library)")
        self.btn_copy_fusion = QPushButton("📋 Copy Fusion Node")
        h_ins = QHBoxLayout()
        h_ins.addWidget(self.btn_install_presets)
        h_ins.addWidget(self.btn_copy_fusion)
        form_p.addRow(h_ins)

        # Tương thích Format
        self.combo_split_mode = QComboBox()
        self.combo_split_mode.addItems(["characters", "words"])
        self.txt_split_limit = QLineEdit("42")
        self.txt_font = QLineEdit("Arial")
        self.txt_size = QLineEdit("48")
        self.txt_color = QLineEdit("#FFFFFF")
        
        # Tương thích Single Title
        self.txt_single_title = QLineEdit()
        self.slide_title_dur = QSlider(Qt.Horizontal)
        self.btn_insert_title_playhead = QPushButton("🚀 Chèn Playhead")
        # ---------------------------------
        
        # Bố cục Asset Hub Chính
        h_split = QHBoxLayout()
        sidebar = QFrame()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background-color: #0E1726; border-right: 1px solid #1E2638;")
        side_layout = QVBoxLayout(sidebar)
        lbl_nav = QLabel("📂 DANH MỤC")
        lbl_nav.setStyleSheet("color: #94A3B8; font-weight: bold; padding: 10px 0;")
        side_layout.addWidget(lbl_nav)
        
        self.nav_btns = {}
        cats = [("all", "Tất cả tài nguyên"), ("title", "Tiêu đề (Titles)"), ("transition", "Chuyển cảnh (Trans)")]
        for cid, cname in cats:
            btn = QPushButton(cname)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton { text-align: left; padding: 8px; border: none; color: #CBD5E1; }
                QPushButton:checked { background-color: #1E293B; color: #38BDF8; font-weight: bold; border-left: 3px solid #38BDF8; }
            """)
            btn.clicked.connect(lambda _, c=cid: self._filter_by_nav(c))
            side_layout.addWidget(btn)
            self.nav_btns[cid] = btn
        self.nav_btns["all"].setChecked(True)
        
        side_layout.addWidget(self.group_presets) # Đưa settings phụ đề vào sidebar dưới
        side_layout.addStretch()
        h_split.addWidget(sidebar)
        
        right_panel = QWidget()
        r_layout = QVBoxLayout(right_panel)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Tìm trong tất cả tài nguyên (Hormozi, Zoom, Glitch)...")
        self.txt_search.textChanged.connect(self._refresh_grid)
        r_layout.addWidget(self.txt_search)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        scroll.setWidget(self.grid_container)
        r_layout.addWidget(scroll)
        
        h_split.addWidget(right_panel, stretch=1)
        main_layout.addLayout(h_split)
        
        self._populate_cards()

    def _filter_by_nav(self, cid: str):
        self.current_category = cid
        for k, b in self.nav_btns.items():
            if k != cid: b.setChecked(False)
        self._refresh_grid()

    def _populate_cards(self):
        for c in self.card_widgets:
            c.deleteLater()
        self.card_widgets.clear()
        
        # Populate all presets for combo boxes if needed by app
        if hasattr(self, 'all_presets') and self.all_presets:
            self.combo_text_preset.clear()
            for p in self.all_presets:
                if isinstance(p, TextStylePreset):
                    self.combo_text_preset.addItem(p.name, p.id)
                    
        # Replace list logic
        for p in self.all_assets:
            card = AssetCard(preset=p, sample_text_func=lambda: self.txt_single_title.text().strip())
            card.selected_signal.connect(self._on_card_selected)
            self.card_widgets.append(card)
            
        self._refresh_grid()
        
    def _refresh_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            
        query = self.txt_search.text().lower()
        cols = 3
        visible = []
        
        for c in self.card_widgets:
            p = c.preset
            is_title = isinstance(p, TextStylePreset)
            is_trans = isinstance(p, TransitionStylePreset)
            
            if self.current_category == "title" and not is_title: continue
            if self.current_category == "transition" and not is_trans: continue
            
            cat = p.category.lower() if hasattr(p, 'category') else ""
            if query and query not in p.name.lower() and query not in cat:
                continue
                
            visible.append(c)
            
        for i, c in enumerate(visible):
            c.setVisible(True)
            self.grid.addWidget(c, i // cols, i % cols)

    def _on_card_selected(self, preset_id: str):
        idx = self.combo_text_preset.findData(preset_id)
        if idx >= 0:
            self.combo_text_preset.setCurrentIndex(idx)
        for c in self.card_widgets:
            c.set_selected(c.preset.id == preset_id)
