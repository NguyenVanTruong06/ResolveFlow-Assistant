"""
src/ui/director/pages/build_page.py
Bước 4: Xác Nhận & Dựng Timeline DaVinci Resolve.
Bao gồm:
- Thẻ tóm tắt toàn bộ lựa chọn (Footage, Kiểu dựng, Tính năng, Câu thoại, Kịch bản, Định dạng FCPXML).
- Tùy chọn: Phủ phụ đề Kinetic lên Track V2, Beat-sync với nhạc nền.
- Trình chọn nhạc nền kèm nút mở Thư viện Nhạc nền.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QGridLayout
)
from PySide6.QtCore import Qt, Signal
from src.ui.director.director_state import DirectorConfig


class BuildSummaryCard(QFrame):
    """Thẻ tóm tắt toàn bộ lựa chọn trước khi xuất bản."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame#build_summary_card {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        self.setObjectName("build_summary_card")
        self._init_ui()

    def _init_ui(self):
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(14, 14, 14, 14)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(10)

        # Labels
        self.lbl_footage = QLabel("0 clip · 00:00")
        self.lbl_style = QLabel("Vlog có Hook")
        self.lbl_features = QLabel("Không")
        self.lbl_dialogue = QLabel("Giữ 0 · Cắt 0")
        self.lbl_script = QLabel("Gemini (Cloud)")
        self.lbl_export = QLabel("FCPXML 1.9 → Timeline mới trong DaVinci Resolve")

        fields = [
            ("Footage nguồn", self.lbl_footage),
            ("Kiểu dựng", self.lbl_style),
            ("Tính năng", self.lbl_features),
            ("Câu thoại", self.lbl_dialogue),
            ("Kịch bản", self.lbl_script),
            ("Định dạng xuất", self.lbl_export),
        ]

        for row, (name, val_widget) in enumerate(fields):
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet("color: #71717a; font-size: 11.5px; font-weight: 500;")
            self.grid.addWidget(lbl_name, row, 0)

            val_widget.setStyleSheet("color: #e4e4e7; font-size: 12px; font-weight: 600;")
            self.grid.addWidget(val_widget, row, 1)

    def update_summary(self, config: DirectorConfig):
        clip_count = len(config.video_paths)
        self.lbl_footage.setText(f"{clip_count} clip · sẵn sàng dựng" if clip_count > 0 else "Chưa chọn clip")

        style_map = {
            "podcast": "Podcast, phỏng vấn",
            "shorts": "Shorts, TikTok 9:16",
            "vlog": "Vlog có Hook",
            "custom": "Tự chỉnh"
        }
        style_name = style_map.get(config.style, config.style)
        self.lbl_style.setText(f"{style_name} · {config.footage_type} · {config.pacing}")

        feat_str = ", ".join(config.enabled_features) if config.enabled_features else "Mặc định"
        self.lbl_features.setText(feat_str)

        if config.web_prompt_pasted_data:
            self.lbl_script.setText(f"Web Prompt ({config.web_prompt_summary or 'Đã dán'})")
        elif config.ai_engine_mode == "ollama":
            self.lbl_script.setText("Local Ollama (Qwen 2.5)")
        else:
            self.lbl_script.setText(f"Cloud API ({config.cloud_provider.upper()})")


class BuildPage(QWidget):
    """Trang Bước 4: Dựng timeline."""
    bgm_browse_requested = Signal()
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._kinetic = True
        self._beat_sync = True
        self._bgm_name = "Chill Vlog Lofi"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        lbl_h3 = QLabel("Sẵn sàng dựng timeline mới trong DaVinci Resolve")
        lbl_h3.setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl_h3)

        # Card tóm tắt
        self.summary_card = BuildSummaryCard(self)
        layout.addWidget(self.summary_card)

        # Tùy chọn bổ trợ
        lbl_opt = QLabel("Tùy chọn bổ trợ:")
        lbl_opt.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: bold; margin-top: 6px;")
        layout.addWidget(lbl_opt)

        self.chk_kinetic = QCheckBox("Phủ phụ đề Kinetic Subtitle lên Track Video 2 (Style: Karaoke Pop)", self)
        self.chk_kinetic.setChecked(True)
        self.chk_kinetic.setStyleSheet("font-size: 12px; color: #e4e4e7;")
        self.chk_kinetic.toggled.connect(self._on_kinetic_toggled)
        layout.addWidget(self.chk_kinetic)

        self.chk_beatsync = QCheckBox("Beat-sync chuyển cảnh và punch-in theo nhịp nhạc nền", self)
        self.chk_beatsync.setChecked(True)
        self.chk_beatsync.setStyleSheet("font-size: 12px; color: #e4e4e7;")
        self.chk_beatsync.toggled.connect(self._on_beatsync_toggled)
        layout.addWidget(self.chk_beatsync)

        # Hàng nhạc nền
        h_bgm = QHBoxLayout()
        h_bgm.setSpacing(6)
        self.lbl_bgm = QLabel(f"Nhạc nền: {self._bgm_name} ·")
        self.lbl_bgm.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        h_bgm.addWidget(self.lbl_bgm)

        btn_pick_bgm = QPushButton("Đổi trong thư viện Nhạc nền", self)
        btn_pick_bgm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pick_bgm.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a78bfa;
                border: none;
                font-size: 11.5px;
                font-weight: 600;
                text-decoration: underline;
            }
            QPushButton:hover {
                color: #c4b5fd;
            }
        """)
        btn_pick_bgm.clicked.connect(self.bgm_browse_requested.emit)
        h_bgm.addWidget(btn_pick_bgm)
        h_bgm.addStretch(1)
        layout.addLayout(h_bgm)

        layout.addStretch(1)

    def _on_kinetic_toggled(self, checked: bool):
        self._kinetic = checked
        self.config_changed.emit()

    def _on_beatsync_toggled(self, checked: bool):
        self._beat_sync = checked
        self.config_changed.emit()

    def update_summary(self, config: DirectorConfig):
        self.summary_card.update_summary(config)

    def set_bgm(self, name: str):
        self._bgm_name = name
        self.lbl_bgm.setText(f"Nhạc nền: {name} ·")

    def load_from_config(self, config: DirectorConfig):
        self._kinetic = config.kinetic_subtitles_v2
        self.chk_kinetic.setChecked(config.kinetic_subtitles_v2)
        self._beat_sync = config.beat_sync
        self.chk_beatsync.setChecked(config.beat_sync)
        if config.bgm_name:
            self.set_bgm(config.bgm_name)
        self.summary_card.update_summary(config)

    def save_to_config(self, config: DirectorConfig):
        config.kinetic_subtitles_v2 = self._kinetic
        config.beat_sync = self._beat_sync
        config.bgm_name = self._bgm_name
