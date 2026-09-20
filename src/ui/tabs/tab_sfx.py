"""
Tab 3: Sound Effects (SFX) & Soundboard Studio
Bao gồm:
- Lưới SFX Soundboard Pad (Whoosh, Pop, Ding, Click, Camera, Glitch, Riser, Sub Drop...)
- Nghe thử âm thanh (Real-time Audio Preview) độ trễ cực thấp
- Thanh trượt Volume Offset tự động (Mặc định -12dB)
- Chèn nhanh âm thanh vào vị trí Playhead trên Audio Track 2/3 trong DaVinci Resolve
- Điều khiển động cơ AI Auto-SFX
"""

import os
import sys
from typing import Optional, Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QFileDialog
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QUrl
from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS

class SFXPadButton(QPushButton):
    """Nút bấm Pad hiệu ứng âm thanh trực quan."""
    def __init__(self, sfx_id: str, label: str, icon_symbol: str, parent=None):
        super().__init__(f"{icon_symbol}\n{label}", parent)
        self.sfx_id = sfx_id
        self.label_text = label
        self.setProperty("class", "sfx_pad_btn")
        self.setObjectName(f"sfx_pad_{sfx_id}")
        self.setFixedHeight(68)
        self.setCursor(Qt.PointingHandCursor)


class TabSFX(QWidget):
    """
    Giao diện Tab 3: Soundboard SFX & Audio Enhancer
    """
    insert_sfx_requested = pyqtSignal(str, int, float)  # sfx_path, target_track, volume_offset_db
    preview_played = pyqtSignal(str) # sfx_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sound_effects_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "sfx"))
        self._sound_effect_player = None
        self.selected_sfx_id = "whoosh"
        self._init_sound_player()
        self._init_ui()

    def _init_sound_player(self):
        """Khởi tạo player âm thanh ngoại tuyến không làm lag GUI."""
        try:
            from PySide6.QtMultimedia import QSoundEffect
            self._sound_effect_player = QSoundEffect(self)
        except Exception:
            self._sound_effect_player = None

    def _play_sfx_file(self, wav_path: str):
        """Phát âm thanh xem trước (Preview audio)."""
        if not os.path.exists(wav_path):
            return

        try:
            if self._sound_effect_player:
                self._sound_effect_player.setSource(QUrl.fromLocalFile(wav_path))
                self._sound_effect_player.play()
                return
        except Exception:
            pass

        # Fallback Windows Standard Library winsound
        try:
            import winsound
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

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

        # Header Banner
        self.banner = QFrame()
        self.banner.setStyleSheet(f"""
            QFrame {{
                background-color: #121A26;
                border: 1px solid #1E3A5F;
                border-left: 4px solid #F57C00;
                border-radius: 6px;
                padding: 6px 8px;
            }}
        """)
        b_lay = QVBoxLayout(self.banner)
        b_lay.setContentsMargins(8, 6, 8, 6)
        lbl_t = QLabel("🔊 <b>SFX Soundboard Studio & Audio Enhancer:</b>")
        lbl_t.setStyleSheet(f"color: {ThemeColors.TEXT_ACCENT}; font-size: 12px;")
        lbl_d = QLabel("Bấm nghe thử hiệu ứng tức thì và chèn chuẩn xác vào con trỏ Playhead trên Audio Track 2/3.")
        lbl_d.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; font-size: 11px;")
        b_lay.addWidget(lbl_t)
        b_lay.addWidget(lbl_d)
        panel.addWidget(self.banner)
        panel.addSpacing(4)

        # 1. SFX SOUNDBOARD GRID (8 PADS)
        self.group_pad = QGroupBox("🎹 BẢNG LƯỚI SFX SOUNDBOARD (CLICK ĐỂ NGHE THỬ)")
        grid_pad = QGridLayout(self.group_pad)
        grid_pad.setSpacing(8)

        self.sfx_items = [
            ("whoosh", "Whoosh Lướt", "🌬", "whoosh.wav"),
            ("pop", "Pop Nảy Chữ", "💥", "pop.wav"),
            ("ding", "Ding Điểm Nhấn", "🔔", "ding.wav"),
            ("click", "Click Chuột", "🖱", "click.wav"),
            ("camera_shutter", "Camera Shutter", "📸", "camera_shutter.wav"),
            ("glitch", "Glitch Điện Tử", "⚡", "glitch.wav"),
            ("riser", "Riser Kịch Tính", "📈", "riser.wav"),
            ("swoosh_sub", "Deep Sub Bass", "🔊", "swoosh_sub.wav"),
        ]

        self.pad_buttons: Dict[str, SFXPadButton] = {}
        for idx, (sfx_id, label, icon, fname) in enumerate(self.sfx_items):
            row = idx // 4
            col = idx % 4
            btn = SFXPadButton(sfx_id, label, icon)
            btn.clicked.connect(lambda checked=False, sid=sfx_id, fn=fname: self._on_pad_clicked(sid, fn))
            grid_pad.addWidget(btn, row, col)
            self.pad_buttons[sfx_id] = btn

        panel.addWidget(self.group_pad)

        # 2. SFX CONFIGURATION & VOLUME OFFSET
        self.group_cfg = QGroupBox("🎚 THIẾT LẬP ÂM LƯỢNG & TRACK CHÈN")
        form_cfg = QFormLayout(self.group_cfg)

        self.lbl_selected_sfx = QLabel("Hiệu ứng đang chọn: <b>Whoosh Lướt (whoosh.wav)</b>")
        self.lbl_selected_sfx.setStyleSheet("color: #64B5F6; font-size: 12px;")
        form_cfg.addRow(self.lbl_selected_sfx)

        self.combo_target_track = QComboBox()
        self.combo_target_track.addItem("Audio Track 2 (SFX Nhẹ)", 2)
        self.combo_target_track.addItem("Audio Track 3 (SFX Nhấn Mạnh)", 3)
        self.combo_target_track.addItem("Audio Track 4 (BGM / Ambient)", 4)
        form_cfg.addRow("Track Audio đích:", self.combo_target_track)

        self.slide_volume_offset = QSlider(Qt.Horizontal)
        self.slide_volume_offset.setRange(-30, 0)
        self.slide_volume_offset.setValue(-12)
        self.lbl_volume_offset = QLabel("-12 dB (Khuyên dùng cho video mạng xã hội)")
        self.lbl_volume_offset.setStyleSheet("color: #81C784; font-weight: bold;")
        self.slide_volume_offset.valueChanged.connect(lambda v: self.lbl_volume_offset.setText(f"{v} dB"))
        h_vol = QHBoxLayout()
        h_vol.addWidget(self.slide_volume_offset)
        h_vol.addWidget(self.lbl_volume_offset)
        form_cfg.addRow("Auto Volume Offset:", h_vol)

        self.check_auto_sfx = QCheckBox("Bật AI Auto-SFX (Tự động phân bổ Whoosh/Pop/Ding vào Timeline)")
        self.check_auto_sfx.setChecked(True)
        form_cfg.addRow(self.check_auto_sfx)

        panel.addWidget(self.group_cfg)

        # 3. ACTION CONTROLS
        self.group_actions = QGroupBox("⚡ THAO TÁC TRỰC TIẾP VỚI TIMELINE")
        vbox_act = QVBoxLayout(self.group_actions)

        self.btn_insert_playhead = QPushButton("🚀 Chèn SFX Vào Vị Trí Playhead (Audio Track)")
        self.btn_insert_playhead.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.WARNING};
                color: #FFFFFF;
                font-weight: bold;
                font-size: 13px;
                padding: 10px 14px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.WARNING_HOVER};
            }}
        """)
        self.btn_insert_playhead.clicked.connect(self._on_insert_playhead_clicked)
        vbox_act.addWidget(self.btn_insert_playhead)

        h_extra = QHBoxLayout()
        btn_open_folder = QPushButton("📂 Mở Thư Mục SFX...")
        btn_open_folder.clicked.connect(self._open_sfx_folder)
        h_extra.addWidget(btn_open_folder)
        vbox_act.addLayout(h_extra)

        panel.addWidget(self.group_actions)

    def _on_pad_clicked(self, sfx_id: str, fname: str):
        """Khi bấm vào một ô SFX Pad."""
        self.selected_sfx_id = sfx_id
        wav_path = os.path.normpath(os.path.join(self.sound_effects_dir, fname))
        self.lbl_selected_sfx.setText(f"Hiệu ứng đang chọn: <b>{sfx_id.upper()} ({fname})</b>")
        self._play_sfx_file(wav_path)
        self.preview_played.emit(sfx_id)

    def _on_insert_playhead_clicked(self):
        """Bấm nút chèn SFX vào Playhead."""
        target_track = self.combo_target_track.currentData() or 2
        vol_db = float(self.slide_volume_offset.value())
        
        # Tìm file WAV
        fname = f"{self.selected_sfx_id}.wav"
        for sid, lbl, icon, fn in self.sfx_items:
            if sid == self.selected_sfx_id:
                fname = fn
                break
        wav_path = os.path.normpath(os.path.join(self.sound_effects_dir, fname))
        self.insert_sfx_requested.emit(wav_path, target_track, vol_db)

    def _open_sfx_folder(self):
        """Mở thư mục SFX trên File Explorer."""
        if os.path.exists(self.sound_effects_dir):
            os.startfile(self.sound_effects_dir)
