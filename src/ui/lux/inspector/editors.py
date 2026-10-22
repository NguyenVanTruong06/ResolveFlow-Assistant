"""
Modular Property Editors for Sheet Inspector.
Tailored for Text+, SFX/Audio, LUT, and Macro assets.
"""
from typing import Dict, Any, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSlider, QPushButton, QComboBox, QFrame
)
from PySide6.QtCore import Qt, QRectF, Signal as pyqtSignal
from PySide6.QtGui import QPainter


class SegmentedControl(QWidget):
    """Segmented toggle buttons [ Option 1 | Option 2 | Option 3 ]"""
    value_changed = pyqtSignal(str)

    def __init__(self, options: list[str], default_idx: int = 0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._options = options
        self._current = options[default_idx] if options else ""
        self._buttons = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        container = QFrame()
        container.setStyleSheet("""
            QFrame {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 2px;
            }
        """)
        c_layout = QHBoxLayout(container)
        c_layout.setContentsMargins(2, 2, 2, 2)
        c_layout.setSpacing(2)

        for opt in options:
            btn = QPushButton(opt)
            btn.setFixedHeight(22)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            is_active = (opt == self._current)
            btn.setStyleSheet(self._btn_style(is_active))
            btn.clicked.connect(lambda checked=False, o=opt: self._set_value(o))
            c_layout.addWidget(btn)
            self._buttons.append(btn)

        layout.addWidget(container)

    def _set_value(self, val: str):
        if self._current == val:
            return
        self._current = val
        for btn in self._buttons:
            btn.setStyleSheet(self._btn_style(btn.text() == self._current))
        self.value_changed.emit(self._current)

    def get_value(self) -> str:
        return self._current

    def _btn_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: #27272a;
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    padding: 0 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
            """
        return """
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """


class SafeTrackSelector(QWidget):
    """Dropdown for safe track destination."""
    def __init__(self, is_audio: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        lbl = QLabel("Track")
        lbl.setFixedWidth(70)
        lbl.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        layout.addWidget(lbl)

        self.combo = QComboBox()
        self.combo.setFixedHeight(26)
        self.combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.combo.setStyleSheet("""
            QComboBox {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11.5px;
            }
            QComboBox QAbstractItemView {
                background: #18181b;
                color: #f4f4f5;
                selection-background-color: #7c3aed;
            }
        """)

        target_kind = "audio" if is_audio else "video"
        self.combo.addItem(f"Tự động ({target_kind} trống an toàn)", "auto")
        tracks = ["A1", "A2", "A3", "A4"] if is_audio else ["V2", "V3", "V4", "V5"]
        for t in tracks:
            self.combo.addItem(f"Track {t}", t)
        self.combo.addItem("➕ Tạo track mới", "new")

        layout.addWidget(self.combo, stretch=1)

    def get_track_mode(self) -> str:
        return self.combo.currentData()


class TextPresetPreviewCanvas(QFrame):
    """Khung xem trước phong cách chữ nghệ thuật trong Inspector."""
    def __init__(self, preset_id: str, name: str, initial_text: str = "ResolveFlow Studio", parent=None):
        super().__init__(parent)
        self.preset_id = preset_id
        self.name = name
        self.custom_text = initial_text
        self.setFixedHeight(88)
        self.setStyleSheet("""
            QFrame {
                background-color: #09090b;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
        """)

    def set_text(self, text: str):
        self.custom_text = text.strip() if text.strip() else "ResolveFlow Studio"
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        from src.ui.lux.library.text_preview_generator import draw_text_preset_preview
        draw_text_preset_preview(painter, rect, self.preset_id, self.name, self.custom_text)


class TextPresetEditor(QWidget):
    """Parameter editor for Text+ and Title presets."""
    def __init__(self, asset: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 0. Live visual style preview box
        asset_id = asset.get("id", "")
        asset_name = asset.get("name", "")
        self.preview_canvas = TextPresetPreviewCanvas(
            preset_id=asset_id,
            name=asset_name,
            initial_text="ResolveFlow Studio",
            parent=self
        )
        layout.addWidget(self.preview_canvas)

        # 1. Content field
        row_txt = QHBoxLayout()
        lbl_txt = QLabel("Nội dung")
        lbl_txt.setFixedWidth(70)
        lbl_txt.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_txt.addWidget(lbl_txt)

        self.txt_content = QLineEdit("ResolveFlow Studio")
        self.txt_content.setFixedHeight(26)
        self.txt_content.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.txt_content.setStyleSheet("""
            QLineEdit {
                background: #18181b;
                color: #ffffff;
                border: 1px solid #27272a;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 11.5px;
            }
            QLineEdit:focus {
                border-color: #7c3aed;
            }
        """)
        self.txt_content.textChanged.connect(self.preview_canvas.set_text)
        row_txt.addWidget(self.txt_content, stretch=1)
        layout.addLayout(row_txt)

        # 1b. Font size slider (Cỡ chữ)
        row_sz = QHBoxLayout()
        lbl_sz = QLabel("Cỡ chữ")
        lbl_sz.setFixedWidth(70)
        lbl_sz.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_sz.addWidget(lbl_sz)

        default_sz = int(asset.get("size", 54) or 54)
        self.slider_sz = QSlider(Qt.Orientation.Horizontal)
        self.slider_sz.setRange(24, 140)
        self.slider_sz.setValue(default_sz)
        self.slider_sz.setFixedHeight(20)
        self.slider_sz.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_sz_val = QLabel(f"{default_sz} px")
        self.lbl_sz_val.setFixedWidth(44)
        self.lbl_sz_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_sz_val.setStyleSheet("color: #06b6d4; font-family: monospace; font-size: 11px; font-weight: bold;")
        self.slider_sz.valueChanged.connect(lambda v: self.lbl_sz_val.setText(f"{v} px"))

        row_sz.addWidget(self.slider_sz, stretch=1)
        row_sz.addWidget(self.lbl_sz_val)
        layout.addLayout(row_sz)

        # 2. Duration slider
        row_dur = QHBoxLayout()
        lbl_dur = QLabel("Thời lượng")
        lbl_dur.setFixedWidth(70)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_dur.addWidget(lbl_dur)

        default_dur = asset.get("duration", 3.0) or 3.0
        self.slider_dur = QSlider(Qt.Orientation.Horizontal)
        self.slider_dur.setRange(10, 100)  # 1.0s to 10.0s
        self.slider_dur.setValue(int(default_dur * 10))
        self.slider_dur.setFixedHeight(20)
        self.slider_dur.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_dur_val = QLabel(f"{default_dur:.1f}s")
        self.lbl_dur_val.setFixedWidth(36)
        self.lbl_dur_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_dur_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")

        self.slider_dur.valueChanged.connect(lambda v: self.lbl_dur_val.setText(f"{v / 10.0:.1f}s"))

        row_dur.addWidget(self.slider_dur, stretch=1)
        row_dur.addWidget(self.lbl_dur_val)
        layout.addLayout(row_dur)

        # 3. Motion style
        row_mot = QHBoxLayout()
        lbl_mot = QLabel("Chuyển động")
        lbl_mot.setFixedWidth(70)
        lbl_mot.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_mot.addWidget(lbl_mot)
        self.seg_motion = SegmentedControl(["Pop", "Bounce", "Gõ chữ"], default_idx=0)
        row_mot.addWidget(self.seg_motion, stretch=1)
        layout.addLayout(row_mot)

        # 4. Easing curve
        row_curve = QHBoxLayout()
        lbl_curve = QLabel("Đường cong")
        lbl_curve.setFixedWidth(70)
        lbl_curve.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_curve.addWidget(lbl_curve)
        self.seg_curve = SegmentedControl(["Spring", "Ease", "Linear"], default_idx=0)
        row_curve.addWidget(self.seg_curve, stretch=1)
        layout.addLayout(row_curve)

    def get_params(self) -> Dict[str, Any]:
        return {
            "text": self.txt_content.text(),
            "size": self.slider_sz.value(),
            "duration": self.slider_dur.value() / 10.0,
            "motion": self.seg_motion.get_value(),
            "curve": self.seg_curve.get_value(),
        }


class AudioEditor(QWidget):
    """Parameter editor for SFX and BGM assets."""
    def __init__(self, asset: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Volume slider
        row_vol = QHBoxLayout()
        lbl_vol = QLabel("Âm lượng bù")
        lbl_vol.setFixedWidth(70)
        lbl_vol.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_vol.addWidget(lbl_vol)

        self.slider_vol = QSlider(Qt.Orientation.Horizontal)
        self.slider_vol.setRange(-30, 0)
        self.slider_vol.setValue(-12)
        self.slider_vol.setFixedHeight(20)
        self.slider_vol.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_vol_val = QLabel("-12 dB")
        self.lbl_vol_val.setFixedWidth(40)
        self.lbl_vol_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_vol_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_vol.valueChanged.connect(lambda v: self.lbl_vol_val.setText(f"{v} dB"))

        row_vol.addWidget(self.slider_vol, stretch=1)
        row_vol.addWidget(self.lbl_vol_val)
        layout.addLayout(row_vol)

        # 2. Fade slider
        row_fade = QHBoxLayout()
        lbl_fade = QLabel("Fade in/out")
        lbl_fade.setFixedWidth(70)
        lbl_fade.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_fade.addWidget(lbl_fade)

        self.slider_fade = QSlider(Qt.Orientation.Horizontal)
        self.slider_fade.setRange(0, 20)  # 0.0s to 2.0s
        self.slider_fade.setValue(1)
        self.slider_fade.setFixedHeight(20)
        self.slider_fade.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_fade_val = QLabel("0.1s")
        self.lbl_fade_val.setFixedWidth(40)
        self.lbl_fade_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_fade_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_fade.valueChanged.connect(lambda v: self.lbl_fade_val.setText(f"{v / 10.0:.1f}s"))

        row_fade.addWidget(self.slider_fade, stretch=1)
        row_fade.addWidget(self.lbl_fade_val)
        layout.addLayout(row_fade)

        # 3. Hover audio preview toggle
        row_hov = QHBoxLayout()
        lbl_hov = QLabel("Nghe khi rê")
        lbl_hov.setFixedWidth(70)
        lbl_hov.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_hov.addWidget(lbl_hov)
        self.seg_hover = SegmentedControl(["Bật", "Tắt"], default_idx=0)
        row_hov.addWidget(self.seg_hover, stretch=1)
        layout.addLayout(row_hov)

    def get_params(self) -> Dict[str, Any]:
        return {
            "volume_db": self.slider_vol.value(),
            "fade_seconds": self.slider_fade.value() / 10.0,
            "hover_preview": self.seg_hover.get_value() == "Bật",
        }


class LutEditor(QWidget):
    """Parameter editor for LUT and Color assets."""
    def __init__(self, asset: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. Strength slider
        row_str = QHBoxLayout()
        lbl_str = QLabel("Cường độ")
        lbl_str.setFixedWidth(70)
        lbl_str.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_str.addWidget(lbl_str)

        self.slider_str = QSlider(Qt.Orientation.Horizontal)
        self.slider_str.setRange(0, 100)
        self.slider_str.setValue(100)
        self.slider_str.setFixedHeight(20)
        self.slider_str.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_str_val = QLabel("100%")
        self.lbl_str_val.setFixedWidth(36)
        self.lbl_str_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_str_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_str.valueChanged.connect(lambda v: self.lbl_str_val.setText(f"{v}%"))

        row_str.addWidget(self.slider_str, stretch=1)
        row_str.addWidget(self.lbl_str_val)
        layout.addLayout(row_str)

        # 2. Scope
        row_scope = QHBoxLayout()
        lbl_scope = QLabel("Phạm vi")
        lbl_scope.setFixedWidth(70)
        lbl_scope.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_scope.addWidget(lbl_scope)
        self.seg_scope = SegmentedControl(["Clip hiện tại", "Cả timeline"], default_idx=0)
        row_scope.addWidget(self.seg_scope, stretch=1)
        layout.addLayout(row_scope)

        # 3. Skip existing
        row_skip = QHBoxLayout()
        lbl_skip = QLabel("Bỏ qua clip có LUT")
        lbl_skip.setFixedWidth(110)
        lbl_skip.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_skip.addWidget(lbl_skip)
        self.seg_skip = SegmentedControl(["Có", "Không"], default_idx=0)
        row_skip.addWidget(self.seg_skip, stretch=1)
        layout.addLayout(row_skip)

    def get_params(self) -> Dict[str, Any]:
        return {
            "strength": self.slider_str.value() / 100.0,
            "scope": self.seg_scope.get_value(),
            "skip_existing": self.seg_skip.get_value() == "Có",
        }


class MacroEditor(QWidget):
    """Parameter editor for transitions, memes, overlays, and Fusion macros."""
    def __init__(self, asset: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Duration
        row_dur = QHBoxLayout()
        lbl_dur = QLabel("Thời lượng")
        lbl_dur.setFixedWidth(70)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_dur.addWidget(lbl_dur)

        default_dur = asset.get("duration", 1.0) or 1.0
        self.slider_dur = QSlider(Qt.Orientation.Horizontal)
        self.slider_dur.setRange(2, 50)  # 0.2s to 5.0s
        self.slider_dur.setValue(int(default_dur * 10))
        self.slider_dur.setFixedHeight(20)
        self.slider_dur.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_dur_val = QLabel(f"{default_dur:.1f}s")
        self.lbl_dur_val.setFixedWidth(36)
        self.lbl_dur_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_dur_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_dur.valueChanged.connect(lambda v: self.lbl_dur_val.setText(f"{v / 10.0:.1f}s"))

        row_dur.addWidget(self.slider_dur, stretch=1)
        row_dur.addWidget(self.lbl_dur_val)
        layout.addLayout(row_dur)

        # Intensity
        row_int = QHBoxLayout()
        lbl_int = QLabel("Cường độ")
        lbl_int.setFixedWidth(70)
        lbl_int.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_int.addWidget(lbl_int)

        self.slider_int = QSlider(Qt.Orientation.Horizontal)
        self.slider_int.setRange(0, 100)
        self.slider_int.setValue(70)
        self.slider_int.setFixedHeight(20)
        self.slider_int.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_int_val = QLabel("70%")
        self.lbl_int_val.setFixedWidth(36)
        self.lbl_int_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_int_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_int.valueChanged.connect(lambda v: self.lbl_int_val.setText(f"{v}%"))

        row_int.addWidget(self.slider_int, stretch=1)
        row_int.addWidget(self.lbl_int_val)
        layout.addLayout(row_int)

    def get_params(self) -> Dict[str, Any]:
        return {
            "duration": self.slider_dur.value() / 10.0,
            "intensity": self.slider_int.value() / 100.0,
        }
