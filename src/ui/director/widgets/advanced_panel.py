"""
src/ui/director/widgets/advanced_panel.py
Bảng trượt tùy chỉnh nâng cao 280px bên phải của ChunDVC Director.
Lưu giữ đầy đủ 100% các tham số kỹ thuật từ TabAutoCut cũ mà không bỏ sót bất kỳ tùy chọn nào.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QSlider, QLineEdit, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal
from src.ui.director.director_state import DirectorConfig


class AdvancedSidePanel(QFrame):
    """Bảng trượt 280px chứa toàn bộ tùy chọn nâng cao của TabAutoCut."""
    config_changed = Signal()
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(280)
        self.setStyleSheet("""
            QFrame#advanced_side_panel {
                background-color: #18181b;
                border-left: 1px solid #27272a;
            }
        """)
        self.setObjectName("advanced_side_panel")
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # Header: Tiêu đề + Nút đóng ✕
        h_header = QHBoxLayout()
        lbl_title = QLabel("Tùy chỉnh nâng cao")
        lbl_title.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        h_header.addWidget(lbl_title)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
            }
        """)
        btn_close.clicked.connect(self.close_requested.emit)
        h_header.addWidget(btn_close)
        main_layout.addLayout(h_header)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: #27272a;")
        main_layout.addWidget(divider)

        # Scroll area chứa form cài đặt
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 4, 4)
        layout.setSpacing(12)

        # --- Nhóm 1: Cắt khoảng lặng vật lý ---
        lbl_grp1 = QLabel("1. CẮT KHOẢNG LẶNG & SPEED-RAMP")
        lbl_grp1.setStyleSheet("color: #ef4444; font-size: 10.5px; font-weight: bold;")
        layout.addWidget(lbl_grp1)

        self.chk_cut = QCheckBox("Kích hoạt xử lý khoảng lặng")
        self.chk_cut.setChecked(True)
        layout.addWidget(self.chk_cut)

        self.chk_scene_guard = QCheckBox("Bảo vệ cảnh có chuyển động (Vlog)")
        self.chk_scene_guard.setChecked(True)
        layout.addWidget(self.chk_scene_guard)

        self.chk_speedup = QCheckBox("Tua nhanh 8x thay vì cắt")
        layout.addWidget(self.chk_speedup)

        layout.addWidget(QLabel("Track Audio phân tích:", styleSheet="color: #a1a1aa; font-size: 11px;"))
        self.combo_audio_track = QComboBox()
        self.combo_audio_track.addItem("Track 1 (Mặc định)", 1)
        self.combo_audio_track.addItem("Track 2", 2)
        self.combo_audio_track.addItem("Tất cả Track gộp chung", 0)
        layout.addWidget(self.combo_audio_track)

        # Slider Ngưỡng im lặng (dB)
        h_db_lbl = QHBoxLayout()
        h_db_lbl.addWidget(QLabel("Ngưỡng im lặng:", styleSheet="color: #a1a1aa; font-size: 11px;"))
        self.lbl_db_val = QLabel("-35 dB")
        self.lbl_db_val.setStyleSheet("color: #c4b5fd; font-weight: bold; font-size: 11px;")
        h_db_lbl.addWidget(self.lbl_db_val)
        layout.addLayout(h_db_lbl)

        self.slider_db = QSlider(Qt.Orientation.Horizontal)
        self.slider_db.setRange(-60, -10)
        self.slider_db.setValue(-35)
        self.slider_db.valueChanged.connect(lambda v: self.lbl_db_val.setText(f"{v} dB"))
        layout.addWidget(self.slider_db)

        # Slider Thời lượng im lặng tối thiểu
        h_dur_lbl = QHBoxLayout()
        h_dur_lbl.addWidget(QLabel("Lặng tối thiểu:", styleSheet="color: #a1a1aa; font-size: 11px;"))
        self.lbl_dur_val = QLabel("0.5s")
        self.lbl_dur_val.setStyleSheet("color: #c4b5fd; font-weight: bold; font-size: 11px;")
        h_dur_lbl.addWidget(self.lbl_dur_val)
        layout.addLayout(h_dur_lbl)

        self.slider_dur = QSlider(Qt.Orientation.Horizontal)
        self.slider_dur.setRange(2, 50)  # 0.2s - 5.0s
        self.slider_dur.setValue(5)
        self.slider_dur.valueChanged.connect(lambda v: self.lbl_dur_val.setText(f"{v / 10:.1f}s"))
        layout.addWidget(self.slider_dur)

        # --- Nhóm 2: Lọc nói vấp & Ngữ nghĩa ---
        lbl_grp2 = QLabel("2. LỌC NÓI VẤP & NGỮ NGHĨA")
        lbl_grp2.setStyleSheet("color: #22c55e; font-size: 10.5px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_grp2)

        self.chk_bad_takes = QCheckBox("Xóa câu nói vấp (Bad Takes)")
        self.chk_bad_takes.setChecked(True)
        layout.addWidget(self.chk_bad_takes)

        self.chk_repeats = QCheckBox("Loại câu nói lặp lại (giữ lần cuối)")
        self.chk_repeats.setChecked(True)
        layout.addWidget(self.chk_repeats)

        self.chk_hide_weak = QCheckBox("Ẩn phụ đề khi chữ không rõ")
        self.chk_hide_weak.setChecked(True)
        layout.addWidget(self.chk_hide_weak)

        self.chk_fill_gaps = QCheckBox("Quét bổ sung vùng Whisper sót")
        layout.addWidget(self.chk_fill_gaps)

        # --- Nhóm 3: Vlog Hook & Teaser ---
        lbl_grp3 = QLabel("3. MỒI CÂU (VLOG HOOK / TEASER)")
        lbl_grp3.setStyleSheet("color: #f59e0b; font-size: 10.5px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_grp3)

        self.chk_hook = QCheckBox("Tự động tạo Teaser đầu video")
        layout.addWidget(self.chk_hook)

        h_hook = QHBoxLayout()
        h_hook.addWidget(QLabel("Độ dài clip:", styleSheet="color: #a1a1aa; font-size: 11px;"))
        self.combo_hook_dur = QComboBox()
        self.combo_hook_dur.addItem("1.5s", 1.5)
        self.combo_hook_dur.addItem("2.0s", 2.0)
        self.combo_hook_dur.addItem("2.5s", 2.5)
        self.combo_hook_dur.setCurrentIndex(1)
        h_hook.addWidget(self.combo_hook_dur)
        layout.addLayout(h_hook)

        # --- Nhóm 4: Gia vị biên tập Track 2 ---
        lbl_grp4 = QLabel("4. GIA VỊ BIÊN TẬP (TRACK 2)")
        lbl_grp4.setStyleSheet("color: #38bdf8; font-size: 10.5px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_grp4)

        self.chk_punch_in = QCheckBox("Auto Punch-in (Zoom 1.15x)")
        self.chk_punch_in.setChecked(True)
        layout.addWidget(self.chk_punch_in)

        self.chk_reframe = QCheckBox("Auto Reframe dọc (9:16)")
        layout.addWidget(self.chk_reframe)

        self.chk_broll = QCheckBox("Gợi ý B-roll / Meme (Track V2)")
        self.chk_broll.setChecked(True)
        layout.addWidget(self.chk_broll)

        self.chk_sfx = QCheckBox("Tự động chèn SFX (Track A2)")
        self.chk_sfx.setChecked(True)
        layout.addWidget(self.chk_sfx)

        self.chk_evidence = QCheckBox("Evidence Hunter (Ảnh Web)")
        self.chk_evidence.setChecked(True)
        layout.addWidget(self.chk_evidence)

        self.chk_safe_zone = QCheckBox("Khử đè mặt & Safe Zone")
        self.chk_safe_zone.setChecked(True)
        layout.addWidget(self.chk_safe_zone)

        layout.addStretch(1)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def load_from_config(self, config: DirectorConfig):
        """Nạp giá trị từ DirectorConfig vào các controls."""
        self.chk_cut.setChecked(config.silence_cut_enabled)
        self.chk_scene_guard.setChecked(config.scene_guard_enabled)
        self.chk_speedup.setChecked(config.speedup_silence)
        self.slider_db.setValue(config.silence_db)
        self.lbl_db_val.setText(f"{config.silence_db} dB")
        self.slider_dur.setValue(int(config.min_silence_dur * 10))
        self.lbl_dur_val.setText(f"{config.min_silence_dur:.1f}s")

        self.chk_bad_takes.setChecked(config.remove_bad_takes)
        self.chk_repeats.setChecked(config.remove_repeats)
        self.chk_hide_weak.setChecked(config.hide_weak_subs)
        self.chk_fill_gaps.setChecked(config.fill_gaps)

        self.chk_hook.setChecked("Hook mở đầu" in config.enabled_features)
        self.chk_punch_in.setChecked("Punch-in" in config.enabled_features)
        self.chk_reframe.setChecked("Reframe 9:16" in config.enabled_features)
        self.chk_broll.setChecked("Gợi ý B-roll" in config.enabled_features)
        self.chk_sfx.setChecked("Tự chèn SFX" in config.enabled_features)
        self.chk_evidence.setChecked("Săn B-roll web" in config.enabled_features)
        self.chk_safe_zone.setChecked("Khử đè mặt" in config.enabled_features)

    def save_to_config(self, config: DirectorConfig):
        """Lưu giá trị từ panel vào DirectorConfig."""
        config.silence_cut_enabled = self.chk_cut.isChecked()
        config.scene_guard_enabled = self.chk_scene_guard.isChecked()
        config.speedup_silence = self.chk_speedup.isChecked()
        config.silence_db = self.slider_db.value()
        config.min_silence_dur = self.slider_dur.value() / 10.0

        config.remove_bad_takes = self.chk_bad_takes.isChecked()
        config.remove_repeats = self.chk_repeats.isChecked()
        config.hide_weak_subs = self.chk_hide_weak.isChecked()
        config.fill_gaps = self.chk_fill_gaps.isChecked()

        # Cập nhật set features
        features = set()
        if self.chk_hook.isChecked():
            features.add("Hook mở đầu")
        if self.chk_punch_in.isChecked():
            features.add("Punch-in")
        if self.chk_reframe.isChecked():
            features.add("Reframe 9:16")
        if self.chk_broll.isChecked():
            features.add("Gợi ý B-roll")
        if self.chk_sfx.isChecked():
            features.add("Tự chèn SFX")
        if self.chk_evidence.isChecked():
            features.add("Săn B-roll web")
        if self.chk_safe_zone.isChecked():
            features.add("Khử đè mặt")
        config.enabled_features = features
