"""
Tab 1: Auto Cut & Rough Cut (Dựng thô & Cắt tự động)
Được tái cấu trúc theo 3 Tầng Kiến Trúc rõ ràng:
- TẦNG 1: 🧹 LÀM SẠCH BẢN THÔ (Cắt khoảng lặng vật lý + Lọc nói vấp/bad takes + Whisper STT + Scan Cache)
- TẦNG 2: 🧭 CẤU TRÚC KỊCH BẢN & HOOK (Story Arranger toàn video + Vlog Hook 3-4s mồi câu + Nhịp Pacing)
- TẦNG 3: 🎨 GIA VỊ BIÊN TẬP (Punch-in Zoom 1.15x + Reframe 9:16 + Gợi ý B-Roll Track 2 & SFX Track 2)
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
from src.ui.widgets.mini_timeline import MiniTimelineWidget
from src.core.story_planner import PACING_PRESETS, DEFAULT_PACING
from src.core.story_arranger import INTENTS
from src.core.edit_policy import VIDEO_TYPES, POLICIES


class TabAutoCut(QWidget):
    """
    Giao diện Tab 1: Dựng thô & Cắt tự động
    """
    settings_changed = pyqtSignal()
    run_autocut_requested = pyqtSignal()

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

        # -------------------------------------------------------------
        # 0. ONBOARDING & WORKFLOW PRESETS
        # -------------------------------------------------------------
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
        lbl_g = QLabel("Chọn <b>Mục tiêu dựng</b> bên dưới để AI tự động kích hoạt tổ hợp tính năng tối ưu nhất, hoặc mở <b>Tùy chỉnh nâng cao</b>.")
        lbl_g.setWordWrap(True)
        lbl_g.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        b_layout.addWidget(lbl_w)
        b_layout.addWidget(lbl_g)
        panel.addWidget(self.banner_onboarding)
        panel.addSpacing(4)

        # 0.1 WORKFLOW MODE
        self.group_wf = SectionCard("🎯 CHẾ ĐỘ DỰNG TỰ ĐỘNG (WORKFLOW PRESET)", accent_color=ThemeColors.PRIMARY)
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

        # 0.2 RECIPE SELECTOR & MANAGER
        self.group_recipe = SectionCard("📋 QUẢN LÝ RECIPE (CẤU HÌNH ĐÃ LƯU)", accent_color=ThemeColors.WARNING)
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

        # 0.3 MASTER INTENSITY SLIDER
        self.group_master = SectionCard("🎛️ BỘ ĐIỀU KHIỂN TỔNG (MASTER CONTROLS)", accent_color=ThemeColors.SUCCESS)
        self.group_master.setObjectName("group_master")
        form_master = QVBoxLayout()
        self.group_master.set_body_layout(form_master)
        desc_master = QLabel("Thanh trượt điều khiển mức độ cắt gọt tổng thể và luật biên tập áp dụng cho toàn bộ dự án.")
        desc_master.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        form_master.addWidget(desc_master)

        self.lbl_master_intensity = QLabel("Mức độ cắt vấp & im lặng: VỪA (Cân bằng)")
        self.lbl_master_intensity.setStyleSheet("color: #81C784; font-weight: bold;")
        self.slide_master_intensity = QSlider(Qt.Horizontal)
        self.slide_master_intensity.setRange(1, 3)
        self.slide_master_intensity.setValue(2)
        form_master.addWidget(self.lbl_master_intensity)
        form_master.addWidget(self.slide_master_intensity)

        lbl_type = QLabel("Kiểu video (quyết định cách cắt khoảng lặng và tua):")
        lbl_type.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px; margin-top: 4px;")
        self.combo_video_type = QComboBox()
        for key, label in VIDEO_TYPES.items():
            self.combo_video_type.addItem(label, key)
        self.combo_video_type.setToolTip(
            "Tự nhận diện: app đo lời thoại và chuyển động hình ảnh để chọn luật phù hợp.\n" +
            "\n".join(f"• {p.label}: {p.summary}" for p in POLICIES.values())
        )
        form_master.addWidget(lbl_type)
        form_master.addWidget(self.combo_video_type)

        lbl_pacing = QLabel("Nhịp dựng (độ dài shot, tần suất đổi khung hình):")
        lbl_pacing.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px; margin-top: 4px;")
        self.combo_pacing = QComboBox()
        for key, preset in PACING_PRESETS.items():
            self.combo_pacing.addItem(preset["label"], key)
        self.combo_pacing.setCurrentIndex(self.combo_pacing.findData(DEFAULT_PACING))
        self.combo_pacing.setToolTip(
            "Thong thả: giữ nhịp tự nhiên, ít zoom. Cân bằng: chuẩn YouTube. "
            "Nhanh: đổi khung hình thường xuyên kiểu TikTok/Shorts."
        )
        form_master.addWidget(lbl_pacing)
        form_master.addWidget(self.combo_pacing)
        panel.addWidget(self.group_master)

        # -------------------------------------------------------------
        # TẦNG 2 PREVIEW: STORY ARRANGER (SẮP XẾP CÓ Ý ĐỒ)
        # -------------------------------------------------------------
        self.group_story = SectionCard("🧭 TẦNG 2: MẠCH KỊCH BẢN & SẮP XẾP Ý ĐỒ (STORY ARRANGER)", accent_color=ThemeColors.WARNING)
        self.group_story.setObjectName("group_story")
        form_story = QFormLayout()
        self.group_story.set_body_layout(form_story)
        desc_story = QLabel(
            "Sắp xếp lại thứ tự của <b>TOÀN BỘ video</b> theo mạch cảm xúc kịch tính (AIDA, PAS, Cao trào dần) hoặc cô đọng thành Shorts. "
            "Timeline gốc vẫn được giữ nguyên; bản sắp xếp được lưu ở timeline riêng (<i>_SapXep</i>) để bạn dễ so sánh."
        )
        desc_story.setWordWrap(True)
        desc_story.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        form_story.addRow(desc_story)

        self.combo_story_intent = QComboBox()
        for key, label in INTENTS.items():
            self.combo_story_intent.addItem(label, key)
        form_story.addRow("Ý đồ kịch bản toàn video:", self.combo_story_intent)

        self.combo_story_target = QComboBox()
        for secs in (30, 45, 60, 90):
            self.combo_story_target.addItem(f"{secs} giây", float(secs))
        self.combo_story_target.setCurrentIndex(2)
        self.combo_story_target.setToolTip("Chỉ dùng cho ý đồ Shorts: tổng thời lượng video ngắn mong muốn.")
        form_story.addRow("Thời lượng Shorts mục tiêu:", self.combo_story_target)

        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        self.txt_api_key.setPlaceholderText("Tùy chọn: API Key Gemini để AI hiểu nội dung sâu hơn")
        self.txt_api_key.setToolTip(
            "Nếu nhập, AI dùng Gemini để gán vai trò cho từng khối và chọn đoạn cho Shorts/Podcast Summary. "
            "Để trống: dùng thuật toán chấm điểm cục bộ (không gửi dữ liệu ra ngoài). Khóa không được lưu vào Recipe."
        )
        form_story.addRow("API Key (tùy chọn):", self.txt_api_key)
        panel.addWidget(self.group_story)

        # MINI TIMELINE PREVIEW
        self.group_timeline = SectionCard("⏱️ XEM TRƯỚC TIMELINE THU NHỎ (MINI TIMELINE PREVIEW)", accent_color=ThemeColors.PRIMARY_HOVER)
        self.group_timeline.setObjectName("group_timeline")
        vbox_timeline = QVBoxLayout()
        self.group_timeline.set_body_layout(vbox_timeline)
        desc_timeline = QLabel("Hiển thị trực quan các phân đoạn thoại, khoảng lặng cắt bỏ và mốc Teaser/Subtitles.")
        desc_timeline.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 2px;")
        vbox_timeline.addWidget(desc_timeline)

        self.mini_timeline = MiniTimelineWidget()
        vbox_timeline.addWidget(self.mini_timeline)
        panel.addWidget(self.group_timeline)

        # Toggle Advanced Button
        self.btn_toggle_advanced = QPushButton("⚙ Tùy chỉnh nâng cao (Chi tiết 3 Tầng Xử Lý) ▾")
        self.btn_toggle_advanced.setObjectName("btn_toggle_advanced")
        panel.addWidget(self.btn_toggle_advanced)

        # -------------------------------------------------------------
        # ADVANCED CONTAINER (CHI TIẾT 3 TẦNG XỬ LÝ)
        # -------------------------------------------------------------
        self.advanced_container = QWidget()
        adv_layout = QVBoxLayout(self.advanced_container)
        adv_layout.setContentsMargins(0, 0, 0, 0)

        # =============================================================
        # TẦNG 1: 🧹 LÀM SẠCH BẢN THÔ (CLEANUP & CUTTING)
        # =============================================================
        # 1.1 Cắt khoảng lặng vật lý & Bảo vệ cảnh quay
        self.group_cut = SectionCard("1.1 ✂️ CẮT KHOẢNG LẶNG VẬT LÝ & SPEED-RAMP", accent_color=ThemeColors.DANGER)
        form_cut = QFormLayout()
        self.group_cut.set_body_layout(form_cut)
        desc_cut = QLabel("Dọn dẹp vật lý sóng âm: tự động cắt bỏ hoặc tua nhanh các đoạn ngưng thở, im lặng không có tiếng nói.")
        desc_cut.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_cut.addRow(desc_cut)

        self.check_cut = QCheckBox("Kích hoạt xử lý khoảng lặng")
        self.check_cut.setChecked(True)
        form_cut.addRow(self.check_cut)

        self.check_scene_guard = QCheckBox("🎞️ Bảo vệ cảnh quay (Vlog-safe): không cắt khoảng lặng đang có chuyển động hình ảnh")
        self.check_scene_guard.setChecked(True)
        self.check_scene_guard.setToolTip(
            "Khoảng lặng mà hình ảnh vẫn chuyển động (đi bộ, lia máy, quay cảnh đẹp...) sẽ được GIỮ (ngắn) hoặc TUA NHANH (dài) "
            "thay vì cắt giật cục. Chỉ cắt khoảng lặng thật sự tĩnh."
        )
        form_cut.addRow(self.check_scene_guard)

        self.check_speedup = QCheckBox("⚡ Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp 8x)")
        self.check_speedup.setChecked(False)
        form_cut.addRow(self.check_speedup)

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

        # 1.2 Lọc câu nói vấp & Đạo diễn AI Semantic Clean
        self.group_director = SectionCard("1.2 🤖 LỌC NÓI VẤP & TINH LỌC THOẠI (AI SEMANTIC CLEAN)", accent_color=ThemeColors.SUCCESS_LIGHT)
        form_director = QFormLayout()
        self.group_director.set_body_layout(form_director)
        desc_dir = QLabel("Dọn dẹp ngữ nghĩa lời thoại: tự động phát hiện câu nói vấp, thử lại nhiều lần và loại bỏ câu thừa.")
        desc_dir.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_director.addRow(desc_dir)

        self.combo_ai_mode = QComboBox()
        self.combo_ai_mode.addItem("Lọc sạch nói vấp & từ đệm (Clean Talk)", "clean_talk")
        self.combo_ai_mode.addItem("Trích xuất Video Ngắn Viral (Shorts 60s)", "viral_shorts")
        self.combo_ai_mode.addItem("Tóm tắt Highlight (Podcast / Summary)", "podcast_summary")
        self.combo_ai_mode.addItem("Chỉ cắt im lặng (Classic Silence Cut)", "silence_only")
        form_director.addRow("Chế độ phân tích thoại:", self.combo_ai_mode)

        self.check_bad_takes = QCheckBox("🚫 Tự động phát hiện & xóa câu nói vấp (Bad Takes)")
        self.check_bad_takes.setChecked(True)
        form_director.addRow(self.check_bad_takes)

        self.check_repeats = QCheckBox("🔁 Loại câu nói lặp lại (chỉ giữ lần nói hoàn chỉnh cuối)")
        self.check_repeats.setChecked(True)
        self.check_repeats.setToolTip("Nếu bạn nói lại cùng một ý sau vài câu, chỉ giữ lần nói cuối. Câu bị loại hiện trong bảng duyệt để bạn đổi lại.")
        form_director.addRow(self.check_repeats)

        self.txt_confidence_threshold = QLineEdit("0.70")
        form_director.addRow("Tô vàng câu tin cậy dưới (0-1):", self.txt_confidence_threshold)

        self.check_hide_weak_subs = QCheckBox("Ẩn phụ đề khi Whisper không chắc chữ (vẫn giữ nguyên hình và tiếng)")
        self.check_hide_weak_subs.setChecked(True)
        self.check_hide_weak_subs.setToolTip(
            "Video ồn/nói lóng thường nhận dạng sai chữ. Câu có độ tin cậy trung bình dưới 0.35 sẽ không hiện phụ đề, "
            "nhưng đoạn hình và tiếng của câu đó KHÔNG bị cắt."
        )
        form_director.addRow(self.check_hide_weak_subs)
        adv_layout.addWidget(self.group_director)

        # 1.3 (Đã chuyển lên Bước 1: Quét Nguồn & Cache)
        # Giữ lại các widget ẩn để duy trì tương thích ngược 100%
        self.group_ai = QWidget()
        self.combo_model = QComboBox()
        self.combo_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.combo_model.setCurrentText("small")
        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["Auto", "Tiếng Việt", "Tiếng Anh"])
        self.check_cache = QCheckBox("⚡ Bật Scan Cache (Bỏ qua transcribe nếu file không đổi)")
        self.check_cache.setChecked(True)
        self.check_fill_gaps = QCheckBox("🔍 Quét bổ sung vùng Whisper bỏ sót (chậm hơn, cho video ồn/dài)")
        self.check_fill_gaps.setChecked(False)

        # =============================================================
        # TẦNG 2: 🧭 MỒI CÂU & TEASER (VLOG HOOK)
        # =============================================================
        self.group_vlog_hook = SectionCard("2.2 🔥 MỒI CÂU MỞ ĐẦU (VLOG HOOK / INTRO TEASER)", accent_color=ThemeColors.WARNING_HOVER)
        form_vlog_hook = QFormLayout()
        self.group_vlog_hook.set_body_layout(form_vlog_hook)
        desc_vlog = QLabel(
            "Trích xuất 3-4s khoảnh khắc đắt giá nhất (cao trào âm thanh + visual mạnh nhất) từ bất kỳ đâu trong toàn bộ video "
            "để đưa lên Frame 0:00 làm mồi nhử giữ chân người xem ngay lập tức."
        )
        desc_vlog.setWordWrap(True)
        desc_vlog.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_vlog_hook.addRow(desc_vlog)

        self.check_vlog_hook = QCheckBox("Tự động tạo Teaser: đọc toàn clip, ghép nhiều khoảnh khắc hay nhất")
        self.check_vlog_hook.setChecked(False)
        form_vlog_hook.addRow(self.check_vlog_hook)

        self.combo_hook_dur = QComboBox()
        self.combo_hook_dur.addItem("1.5s / clip (Cắt nhanh giật gân)", 1.5)
        self.combo_hook_dur.addItem("2.0s / clip (Tiêu chuẩn cân đối)", 2.0)
        self.combo_hook_dur.addItem("2.5s / clip (Đủ trọn vẹn câu thoại)", 2.5)
        self.combo_hook_dur.addItem("3.0s / clip (Trích đoạn mở rộng)", 3.0)
        self.combo_hook_dur.setCurrentIndex(1)
        form_vlog_hook.addRow("Độ dài mỗi khoảnh khắc hook:", self.combo_hook_dur)

        self.combo_hook_total = QComboBox()
        for secs in (10, 15, 20, 30):
            self.combo_hook_total.addItem(f"{secs} giây", float(secs))
        self.combo_hook_total.setCurrentIndex(2)
        self.combo_hook_total.setToolTip(
            "Tổng thời lượng teaser. App quét lời thoại hook, cao trào âm thanh và chuyển động hình ảnh "
            "của toàn bộ clip (kể cả chỉ có 1 clip), rồi ghép các khoảnh khắc điểm cao nhất, trải đều nội dung."
        )
        form_vlog_hook.addRow("Tổng thời lượng Teaser:", self.combo_hook_total)
        adv_layout.addWidget(self.group_vlog_hook)

        # =============================================================
        # TẦNG 3: 🎨 GIA VỊ BIÊN TẬP & ĐA TẦNG MEDIA (EDITORIAL POLISH)
        # =============================================================
        self.group_v4 = SectionCard("3. 👑 GIA VỊ BIÊN TẬP & ĐA TẦNG MEDIA (TRACK 2)", accent_color=ThemeColors.PRIMARY_HOVER)
        form_v4 = QFormLayout()
        self.group_v4.set_body_layout(form_v4)
        desc_v4 = QLabel(
            "Tạo hiệu ứng chuyển động camera, tự động gợi ý cảnh B-Roll/Meme đè lên Track Video 2 "
            "và hiệu ứng âm thanh SFX lên Track Audio 2 để video sinh động, cuốn hút."
        )
        desc_v4.setWordWrap(True)
        desc_v4.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        form_v4.addRow(desc_v4)

        self.check_punch_in = QCheckBox("🔍 Hiệu ứng Auto Punch-in (Zoom 1.15x luân phiên chống nhàm chán khi thoại dài)")
        self.check_punch_in.setChecked(True)
        form_v4.addRow(self.check_punch_in)

        self.check_reframe = QCheckBox("📱 Auto Re-framing (Bám mặt sang video dọc 9:16 cho Shorts/TikTok)")
        self.check_reframe.setChecked(False)
        form_v4.addRow(self.check_reframe)

        self.check_broll = QCheckBox("🎬 Gợi ý & Chèn cảnh minh họa B-Roll / Meme (Track Video 2)")
        self.check_broll.setChecked(True)
        self.check_broll.setToolTip("Tại các câu nói có hình ảnh ẩn dụ hoặc cảm xúc mạnh, AI gợi ý vị trí chèn B-Roll hoặc Meme đè lên Track Video 2.")
        form_v4.addRow(self.check_broll)

        self.check_sfx = QCheckBox("🔊 Tự động chèn âm thanh hiệu ứng SFX (Track Audio 2)")
        self.check_sfx.setChecked(True)
        self.check_sfx.setToolTip("Tự động chèn tiếng Whoosh, Pop, Ding khi có chuyển cảnh hoặc điểm nhấn từ khóa lên Track Audio 2.")
        form_v4.addRow(self.check_sfx)
        adv_layout.addWidget(self.group_v4)

        self.btn_run_autocut = QPushButton("✂ 🚀 XUẤT TIMELINE CẮT THÔ (OFFLINE)")
        self.btn_run_autocut.setCursor(Qt.PointingHandCursor)
        self.btn_run_autocut.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.DANGER};
                color: white;
                font-weight: bold;
                font-size: 13px;
                padding: 12px;
                border-radius: 6px;
                margin-top: 8px;
            }}
            QPushButton:hover {{
                background-color: #DC2626;
            }}
        """)
        self.btn_run_autocut.clicked.connect(self.run_autocut_requested.emit)
        panel.addWidget(self.btn_run_autocut)

        # Tooltips mapping
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
