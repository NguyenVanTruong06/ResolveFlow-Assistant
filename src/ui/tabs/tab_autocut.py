"""
Tab 1: Auto Cut & Rough Cut (Dựng thô & Cắt tự động)
Bao gồm:
- Workflow Mode Selector & Recipe Manager
- Master Intensity Slider
- Nhận diện giọng nói Whisper & Scan Cache
- Đạo diễn AI (AI Director) & Lọc nói vấp (Bad Takes)
- Vlog Hook / Intro Teaser
- Cắt khoảng lặng & Auto Speed-Ramp 8x
- Thị giác AI Reframe 9:16 & B-Roll / SFX Cues
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea
)
from PySide6.QtCore import Qt, Signal as pyqtSignal
from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS, MODULE_DESCRIPTIONS
from src.ui.widgets.section_card import SectionCard

class TabAutoCut(QWidget):
    """
    Giao diện Tab 1: Dựng thô & Cắt tự động
    """
    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
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
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)

        # Onboarding Banner
        self.banner_onboarding = QFrame()
        self.banner_onboarding.setObjectName("banner_onboarding")
        self.banner_onboarding.setStyleSheet(f"""
            QFrame#banner_onboarding {{
                background-color: #121A26;
                border: 1px solid #1E3A5F;
                border-left: 4px solid {ThemeColors.PRIMARY};
                border-radius: 6px;
                padding: 6px 8px;
            }}
        """)
        b_layout = QVBoxLayout(self.banner_onboarding)
        b_layout.setContentsMargins(8, 6, 8, 6)
        lbl_w = QLabel("👋 <b>Bắt đầu nhanh (Quick Start):</b>")
        lbl_w.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 12px;")
        lbl_g = QLabel("Chọn mục tiêu dựng bên dưới để AI tối ưu toàn bộ quy trình, hoặc mở <b>Tùy chỉnh nâng cao</b>.")
        lbl_g.setWordWrap(True)
        lbl_g.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        b_layout.addWidget(lbl_w)
        b_layout.addWidget(lbl_g)
        panel.addWidget(self.banner_onboarding)
        panel.addSpacing(4)

        # 1. WORKFLOW MODE
        self.group_wf = SectionCard("🎯 CHẾ ĐỘ DỰNG TỰ ĐỘNG (WORKFLOW MODE)", accent_color=ThemeColors.PRIMARY)
        self.group_wf.setObjectName("group_wf")
        form_wf = QFormLayout()
        self.group_wf.set_body_layout(form_wf)
        desc_wf = QLabel("Chọn mục tiêu dựng để app tự động kích hoạt tổ hợp tính năng tối ưu nhất.")
        desc_wf.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        form_wf.addRow(desc_wf)

        self.combo_workflow = QComboBox()
        self.combo_workflow.addItem("🎙 Dựng Podcast / Phỏng vấn dài (Silence Cut + Clean Talk + Sub)", "podcast")
        self.combo_workflow.addItem("📱 Làm Shorts / TikTok 9:16 (Viral Cut + Reframe + Karaoke Sub + SFX)", "shorts")
        self.combo_workflow.addItem("🎬 Vlog có Hook / Intro (Intro Teaser + Punch-in + B-Roll + Speed-Ramp)", "vlog")
        self.combo_workflow.addItem("⚙ Tùy chỉnh nâng cao (Advanced Mode)", "advanced")
        form_wf.addRow("Mục tiêu:", self.combo_workflow)
        panel.addWidget(self.group_wf)

        # 2. RECIPE SELECTOR & MANAGER
        self.group_recipe = SectionCard("📋 HỆ THỐNG RECIPE (TỔ HỢP CẤU HÌNH ĐÃ LƯU)", accent_color=ThemeColors.WARNING)
        self.group_recipe.setObjectName("group_recipe")
        vbox_recipe = QVBoxLayout()
        self.group_recipe.set_body_layout(vbox_recipe)
        desc_recipe = QLabel("Lưu và tái sử dụng nhanh toàn bộ cấu hình riêng của bạn cho các dự án sau.")
        desc_recipe.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        vbox_recipe.addWidget(desc_recipe)

        form_recipe = QHBoxLayout()
        self.combo_recipes = QComboBox()
        self.btn_save_recipe = QPushButton("💾 Lưu Recipe...")
        self.btn_delete_recipe = QPushButton("🗑 Xóa")
        form_recipe.addWidget(self.combo_recipes, stretch=3)
        form_recipe.addWidget(self.btn_save_recipe, stretch=2)
        form_recipe.addWidget(self.btn_delete_recipe, stretch=1)
        vbox_recipe.addLayout(form_recipe)
        panel.addWidget(self.group_recipe)

        # 3. MASTER INTENSITY SLIDER
        self.group_master = SectionCard("🎛 CƯỜNG ĐỘ CẮT LỌC TỔNG (BASIC LAYER)", accent_color=ThemeColors.SUCCESS)
        self.group_master.setObjectName("group_master")
        form_master = QVBoxLayout()
        self.group_master.set_body_layout(form_master)
        desc_master = QLabel("Thanh trượt điều khiển tổng thể mức độ cắt gọt và độ nhạy của AI.")
        desc_master.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        form_master.addWidget(desc_master)

        self.lbl_master_intensity = QLabel("Mức độ cắt vấp & im lặng: VỪA (Cân bằng)")
        self.lbl_master_intensity.setStyleSheet("color: #81C784; font-weight: bold;")
        self.slide_master_intensity = QSlider(Qt.Horizontal)
        self.slide_master_intensity.setRange(1, 3)
        self.slide_master_intensity.setValue(2)
        form_master.addWidget(self.lbl_master_intensity)
        form_master.addWidget(self.slide_master_intensity)
        panel.addWidget(self.group_master)

        # Toggle Advanced Button
        self.btn_toggle_advanced = QPushButton("⚙ Tùy chỉnh nâng cao (Chi tiết Module) ▾")
        self.btn_toggle_advanced.setObjectName("btn_toggle_advanced")
        panel.addWidget(self.btn_toggle_advanced)

        # Advanced Container
        self.advanced_container = QWidget()
        adv_layout = QVBoxLayout(self.advanced_container)
        adv_layout.setContentsMargins(0, 0, 0, 0)

        # Group 1: Whisper & Cache
        self.group_ai = SectionCard("1. 🤖 NHẬN DIỆN GIỌNG NÓI & CACHE", accent_color=ThemeColors.TEXT_ACCENT)
        form_ai = QFormLayout()
        self.group_ai.set_body_layout(form_ai)
        desc_ai = QLabel(MODULE_DESCRIPTIONS["whisper"])
        desc_ai.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_ai.addRow(desc_ai)

        self.combo_model = QComboBox()
        self.combo_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.combo_model.setCurrentText("small")
        form_ai.addRow("Kích thước Model:", self.combo_model)

        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["Auto", "Tiếng Việt", "Tiếng Anh"])
        form_ai.addRow("Ngôn ngữ đầu vào:", self.combo_lang)

        self.check_cache = QCheckBox("⚡ Bật Scan Cache (Bỏ qua transcribe nếu file không đổi)")
        self.check_cache.setChecked(True)
        form_ai.addRow(self.check_cache)
        adv_layout.addWidget(self.group_ai)

        # Group 2: AI Director & Semantic Cutting
        self.group_director = SectionCard("2. 🎬 ĐẠO DIỄN AI (AI DIRECTOR)", accent_color=ThemeColors.SUCCESS_LIGHT)
        form_director = QFormLayout()
        self.group_director.set_body_layout(form_director)
        desc_dir = QLabel(MODULE_DESCRIPTIONS["director"])
        desc_dir.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_director.addRow(desc_dir)

        self.combo_ai_mode = QComboBox()
        self.combo_ai_mode.addItem("Lọc sạch nói vấp & từ đệm (Clean Talk)", "clean_talk")
        self.combo_ai_mode.addItem("Trích xuất Video Ngắn Viral (Shorts 60s)", "viral_shorts")
        self.combo_ai_mode.addItem("Tóm tắt Highlight (Podcast / Summary)", "podcast_summary")
        self.combo_ai_mode.addItem("Chỉ cắt im lặng (Classic Silence Cut)", "silence_only")
        form_director.addRow("Chế độ kịch bản:", self.combo_ai_mode)

        self.check_bad_takes = QCheckBox("Tự động phát hiện & xóa câu nói vấp")
        self.check_bad_takes.setChecked(True)
        form_director.addRow(self.check_bad_takes)

        self.check_punch_in = QCheckBox("Hiệu ứng Auto Punch-in (Zoom luân phiên)")
        self.check_punch_in.setChecked(True)
        form_director.addRow(self.check_punch_in)

        self.txt_confidence_threshold = QLineEdit("0.70")
        form_director.addRow("Ngưỡng tin cậy AI (0-1):", self.txt_confidence_threshold)
        adv_layout.addWidget(self.group_director)

        # Group 3: Vlog Hook
        self.group_vlog_hook = SectionCard("3. 🔥 VLOG HOOK / INTRO TEASER", accent_color=ThemeColors.WARNING_HOVER)
        form_vlog_hook = QFormLayout()
        self.group_vlog_hook.set_body_layout(form_vlog_hook)
        desc_vlog = QLabel(MODULE_DESCRIPTIONS["vlog_hook"])
        desc_vlog.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_vlog_hook.addRow(desc_vlog)

        self.check_vlog_hook = QCheckBox("Tự động tạo Teaser/Hook mở đầu (10-30s)")
        self.check_vlog_hook.setChecked(False)
        form_vlog_hook.addRow(self.check_vlog_hook)

        self.combo_hook_dur = QComboBox()
        self.combo_hook_dur.addItem("1.5s / clip (Cắt nhanh giật gân)", 1.5)
        self.combo_hook_dur.addItem("2.0s / clip (Tiêu chuẩn cân đối)", 2.0)
        self.combo_hook_dur.addItem("2.5s / clip (Đủ trọn vẹn câu thoại)", 2.5)
        self.combo_hook_dur.addItem("3.0s / clip (Trích đoạn mở rộng)", 3.0)
        self.combo_hook_dur.setCurrentIndex(1)
        form_vlog_hook.addRow("Thời lượng mỗi clip:", self.combo_hook_dur)
        adv_layout.addWidget(self.group_vlog_hook)

        # Group 4: Visual Reframing & Multi-Track Media
        self.group_v4 = SectionCard("4. 👑 THỊ GIÁC & ĐA TẦNG MEDIA", accent_color=ThemeColors.PRIMARY_HOVER)
        form_v4 = QFormLayout()
        self.group_v4.set_body_layout(form_v4)
        desc_v4 = QLabel(MODULE_DESCRIPTIONS["visual_audio"])
        desc_v4.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_v4.addRow(desc_v4)

        self.check_reframe = QCheckBox("Auto Re-framing (Bám mặt sang video dọc 9:16)")
        self.check_reframe.setChecked(False)
        form_v4.addRow(self.check_reframe)

        self.check_broll = QCheckBox("Tự động gợi ý cảnh minh họa B-Roll (Track Video 2)")
        self.check_broll.setChecked(True)
        form_v4.addRow(self.check_broll)

        self.check_sfx = QCheckBox("Tự động chèn âm thanh hiệu ứng SFX (Track Audio 2)")
        self.check_sfx.setChecked(True)
        form_v4.addRow(self.check_sfx)
        adv_layout.addWidget(self.group_v4)

        # Group 5: Silence Cut & Speed-Ramp
        self.group_cut = SectionCard("5. ✂ CẮT KHOẢNG LẶNG & SPEED-RAMP", accent_color=ThemeColors.DANGER)
        form_cut = QFormLayout()
        self.group_cut.set_body_layout(form_cut)
        desc_cut = QLabel(MODULE_DESCRIPTIONS["silence_cut"])
        desc_cut.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_cut.addRow(desc_cut)

        self.check_cut = QCheckBox("Kích hoạt xử lý khoảng lặng")
        self.check_cut.setChecked(True)
        form_cut.addRow(self.check_cut)

        self.check_speedup = QCheckBox("⚡ Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp 8x)")
        self.check_speedup.setChecked(False)
        form_cut.addRow(self.check_speedup)

        # Audio Track Analysis Selector
        self.combo_audio_track = QComboBox()
        self.combo_audio_track.addItem("Track Audio 1 (Mặc định)", 1)
        self.combo_audio_track.addItem("Track Audio 2", 2)
        self.combo_audio_track.addItem("Tất cả Track gộp chung", 0)
        form_cut.addRow("Track Audio phân tích:", self.combo_audio_track)

        self.slide_db = QSlider(Qt.Horizontal)
        self.slide_db.setRange(-60, -10)
        self.slide_db.setValue(-35)
        self.lbl_db = QLabel("-35 dB")
        self.slide_db.valueChanged.connect(lambda v: self.lbl_db.setText(f"{v} dB"))
        h_db = QHBoxLayout()
        h_db.addWidget(self.slide_db)
        h_db.addWidget(self.lbl_db)
        form_cut.addRow("Ngưỡng im lặng (dB):", h_db)

        self.slide_dur = QSlider(Qt.Horizontal)
        self.slide_dur.setRange(2, 50)
        self.slide_dur.setValue(5)
        self.lbl_dur = QLabel("0.5 s")
        self.slide_dur.valueChanged.connect(lambda v: self.lbl_dur.setText(f"{v/10:.1f} s"))
        h_dur = QHBoxLayout()
        h_dur.addWidget(self.slide_dur)
        h_dur.addWidget(self.lbl_dur)
        form_cut.addRow("Thời lượng tối thiểu:", h_dur)

        adv_layout.addWidget(self.group_cut)
        panel.addWidget(self.advanced_container)

        # Tooltips
        self.combo_workflow.setToolTip(TOOLTIPS["workflow_mode"])
        self.combo_recipes.setToolTip(TOOLTIPS["recipe"])
        self.slide_master_intensity.setToolTip(TOOLTIPS["master_intensity"])
        self.combo_model.setToolTip(TOOLTIPS["whisper_model"])
        self.combo_lang.setToolTip(TOOLTIPS["language"])
        self.check_cache.setToolTip(TOOLTIPS["scan_cache"])
        self.combo_ai_mode.setToolTip(TOOLTIPS["ai_mode"])
        self.check_bad_takes.setToolTip(TOOLTIPS["bad_takes"])
        self.check_punch_in.setToolTip(TOOLTIPS["punch_in"])
        self.txt_confidence_threshold.setToolTip(TOOLTIPS["confidence_threshold"])
        self.check_vlog_hook.setToolTip(TOOLTIPS["vlog_hook"])
        self.combo_hook_dur.setToolTip(TOOLTIPS["hook_duration"])
        self.check_reframe.setToolTip(TOOLTIPS["reframe"])
        self.check_broll.setToolTip(TOOLTIPS["broll"])
        self.check_sfx.setToolTip(TOOLTIPS["sfx"])
        self.check_cut.setToolTip(TOOLTIPS["silence_cut"])
        self.check_speedup.setToolTip(TOOLTIPS["speedup_silence"])
        self.slide_db.setToolTip(TOOLTIPS["silence_db"])
        self.slide_dur.setToolTip(TOOLTIPS["min_duration"])
