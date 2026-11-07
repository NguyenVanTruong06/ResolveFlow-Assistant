"""
Modular Property Editors for Sheet Inspector.
Tailored for Text+, SFX/Audio, LUT, and Macro assets.
"""
import os
from typing import Dict, Any, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSlider, QPushButton, QComboBox, QFrame, QStackedWidget
)
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, QUrl, Signal as pyqtSignal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath


class SegmentedControl(QWidget):
    """Segmented toggle buttons [ Option 1 | Option 2 | Option 3 ]"""
    value_changed = pyqtSignal(str)
    selection_changed = pyqtSignal(str)

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
        self.selection_changed.emit(self._current)

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
                selection-background-color: #d94f3d;
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
    """Khung xem trước phong cách chữ nghệ thuật động (~30 FPS) trong Inspector."""
    def __init__(
        self,
        preset_id: str = "",
        name: str = "",
        initial_text: str = "ResolveFlow Studio",
        parent: Optional[QWidget] = None,
        asset: Any = None,
    ):
        super().__init__(parent)
        if isinstance(preset_id, dict) and asset is None:
            asset = preset_id
            preset_id = str(asset.get("id") or "")
            name = name or str(asset.get("name") or "")
        self.preset_id = preset_id
        self.name = name
        self.custom_text = initial_text
        self._asset: Any = asset if asset is not None else {"id": preset_id, "name": name}
        self._progress: float = 0.0
        self._overrides: Dict[str, Any] = {
            "text": self.custom_text,
            "id": self.preset_id,
        }
        self.setFixedHeight(88)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background-color: #09090b;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
        """)

        self._anim_timer = QTimer(self)
        self.anim_timer = self._anim_timer
        self._anim_timer.setInterval(33)  # ~30 FPS
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

    def _on_tick(self):
        self._progress += 0.025
        if self._progress > 1.35:
            self._progress = 0.0
        self.update()

    def showEvent(self, event):
        if hasattr(self, "_anim_timer") and not self._anim_timer.isActive():
            self._anim_timer.start()
        if event is not None:
            super().showEvent(event)

    def hideEvent(self, event):
        if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
            self._anim_timer.stop()
        if event is not None:
            super().hideEvent(event)

    def mousePressEvent(self, event):
        self._progress = 0.0
        self.update()
        if event is not None:
            super().mousePressEvent(event)

    def set_text(self, text: str):
        self.custom_text = text.strip() if text and text.strip() else "ResolveFlow Studio"
        self._overrides["text"] = self.custom_text
        self.update()

    def set_overrides(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None:
                self._overrides[k] = v
        if "text" in kwargs:
            txt = kwargs.get("text") or ""
            self.custom_text = txt.strip() if str(txt).strip() else "ResolveFlow Studio"
            self._overrides["text"] = self.custom_text
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if not painter.isActive():
            return
        rect = QRectF(4, 4, max(10, self.width() - 8), max(10, self.height() - 8))
        try:
            from src.ui.lux.library.text_preview_generator import draw_davinci_text_frame
            draw_davinci_text_frame(
                painter,
                rect,
                self._asset,
                progress=min(1.0, max(0.0, self._progress)),
                aspect_ratio="16:9",
                show_hud=True,
                overrides=self._overrides,
            )
            return
        except Exception:
            pass
        from src.ui.lux.library.text_preview_generator import draw_text_preset_preview
        try:
            draw_text_preset_preview(painter, rect, self.preset_id, self.name, self.custom_text)
        except TypeError:
            draw_text_preset_preview(painter, rect, self._asset)


class TextPresetEditor(QWidget):
    """Parameter editor for Text+ and Title presets."""
    def __init__(self, asset: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        if asset is None:
            self._asset = {"id": "kinetic_hormozi", "name": "ResolveFlow Studio"}
        elif isinstance(asset, dict):
            self._asset = asset
        else:
            self._asset = {
                "id": str(getattr(asset, "id", "") or asset),
                "name": str(getattr(asset, "name", "") or asset),
                "size": int(getattr(asset, "size", 54) or 54),
                "_raw_obj": asset,
            }
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 0. Live visual style preview box
        asset_id = self._asset.get("id", "")
        asset_name = self._asset.get("name", "")
        self.preview_canvas = TextPresetPreviewCanvas(
            preset_id=asset_id,
            name=asset_name,
            initial_text="ResolveFlow Studio",
            parent=self,
            asset=self._asset,
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
                border-color: #d94f3d;
            }
        """)
        self.txt_content.textChanged.connect(self.preview_canvas.set_text)
        self.txt_content.textChanged.connect(self._sync_live_preview)
        row_txt.addWidget(self.txt_content, stretch=1)
        layout.addLayout(row_txt)

        # 1b. Font size slider (Cỡ chữ)
        row_sz = QHBoxLayout()
        lbl_sz = QLabel("Cỡ chữ")
        lbl_sz.setFixedWidth(70)
        lbl_sz.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_sz.addWidget(lbl_sz)

        default_sz = int(self._asset.get("size", 54) or 54)
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
        self.slider_sz.valueChanged.connect(self._sync_live_preview)

        row_sz.addWidget(self.slider_sz, stretch=1)
        row_sz.addWidget(self.lbl_sz_val)
        layout.addLayout(row_sz)

        # 2. Duration slider
        row_dur = QHBoxLayout()
        lbl_dur = QLabel("Thời lượng")
        lbl_dur.setFixedWidth(70)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_dur.addWidget(lbl_dur)

        default_dur = self._asset.get("duration", 3.0) or 3.0
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
        self.slider_dur.valueChanged.connect(self._sync_live_preview)

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
        self.seg_motion.selection_changed.connect(self._on_motion_or_curve_changed)
        row_mot.addWidget(self.seg_motion, stretch=1)
        layout.addLayout(row_mot)

        # 4. Easing curve
        row_curve = QHBoxLayout()
        lbl_curve = QLabel("Đường cong")
        lbl_curve.setFixedWidth(70)
        lbl_curve.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_curve.addWidget(lbl_curve)
        self.seg_curve = SegmentedControl(["Spring", "Ease", "Linear"], default_idx=0)
        self.seg_curve.selection_changed.connect(self._on_motion_or_curve_changed)
        row_curve.addWidget(self.seg_curve, stretch=1)
        layout.addLayout(row_curve)

        self._sync_live_preview()

    def _on_motion_or_curve_changed(self, _val: str = ""):
        self.preview_canvas._progress = 0.0
        self._sync_live_preview()

    def _sync_live_preview(self, *args):
        mot_raw = self.seg_motion.get_value() if hasattr(self, "seg_motion") else "Pop"
        mot_map = {
            "pop": "pop",
            "bounce": "bounce",
            "gõ chữ": "typewriter",
            "typewriter": "typewriter",
        }
        anim = mot_map.get(mot_raw.lower(), mot_raw.lower())
        curve = (self.seg_curve.get_value() if hasattr(self, "seg_curve") else "Spring").lower()
        self.preview_canvas.set_overrides(
            text=self.txt_content.text(),
            font_size=self.slider_sz.value(),
            duration=self.slider_dur.value() / 10.0,
            animation=anim,
            timing_curve=curve,
        )

    def bind(self, asset: Any):
        if isinstance(asset, dict):
            self._asset = asset
        else:
            self._asset = {
                "id": str(getattr(asset, "id", "") or asset),
                "name": str(getattr(asset, "name", "") or asset),
                "size": int(getattr(asset, "size", 54) or 54),
                "_raw_obj": asset,
            }
        self.preview_canvas._asset = asset if asset is not None else self._asset
        self.preview_canvas.preset_id = self._asset.get("id", "")
        self.preview_canvas.name = self._asset.get("name", "")
        self._sync_live_preview()

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


class LutPreviewCanvas(QFrame):
    """Khung xem trước màu 3D .cube LUT Before / After Split Wipe chuẩn DaVinci Color Gallery."""
    def __init__(self, asset: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._asset: Any = asset if asset is not None else {}
        self.split_pct: float = 34.0
        self.strength: float = 1.0
        self.setFixedHeight(124)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("Kéo chuột trái/phải để so sánh màu GỐC | SAU khi áp 3D LUT")
        self.setStyleSheet("""
            QFrame {
                background-color: #09090b;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
        """)

    def set_asset(self, asset: Any) -> None:
        self._asset = asset if asset is not None else {}
        self.update()

    def set_split(self, pct: float) -> None:
        self.split_pct = max(2.0, min(98.0, float(pct)))
        self.update()

    def set_strength(self, strength: float) -> None:
        self.strength = max(0.0, min(1.0, float(strength)))
        self.update()

    def _scrub_from_event(self, event) -> None:
        w = max(1, self.width())
        pos_x = event.position().x() if hasattr(event, "position") else float(event.pos().x())
        self.set_split((pos_x / w) * 100.0)

    def mousePressEvent(self, event) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._scrub_from_event(event)
        if event is not None:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self._scrub_from_event(event)
        if event is not None:
            super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        if not painter.isActive():
            return
        rect = QRectF(3, 3, max(12, self.width() - 6), max(12, self.height() - 6))
        try:
            from src.ui.lux.library.lut_preview_generator import draw_lut_preview_frame
            draw_lut_preview_frame(
                painter,
                rect,
                self._asset,
                split_pct=self.split_pct,
                strength=self.strength,
                show_hud=True,
                show_swatches=True,
                corner_radius=5.0,
            )
        except Exception:
            painter.fillRect(rect, QColor("#18181b"))


class LutEditor(QWidget):
    """Parameter editor for LUT and Color assets with interactive 3D .cube Split Wipe."""
    def __init__(self, asset: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._asset = asset if asset is not None else {}
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 0. Interactive 3D .cube Before/After Split Wipe Canvas
        self.lut_preview_canvas = LutPreviewCanvas(self._asset, self)
        self.preview_canvas = self.lut_preview_canvas
        layout.addWidget(self.lut_preview_canvas)

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
        self.lbl_str_val.setStyleSheet("color: #06b6d4; font-family: monospace; font-size: 11px; font-weight: bold;")
        self.slider_str.valueChanged.connect(self._on_strength_changed)

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

    def _on_strength_changed(self, v: int) -> None:
        self.lbl_str_val.setText(f"{v}%")
        self.lut_preview_canvas.set_strength(v / 100.0)

    def bind(self, asset: Any) -> None:
        self._asset = asset if asset is not None else {}
        self.lut_preview_canvas.set_asset(self._asset)

    def get_params(self) -> Dict[str, Any]:
        return {
            "strength": self.slider_str.value() / 100.0,
            "scope": self.seg_scope.get_value(),
            "skip_existing": self.seg_skip.get_value() == "Có",
        }


class TransitionPreviewCanvas(QWidget):
    """Khung xem trước chuyển cảnh động (~30 FPS) trong SheetInspector."""
    def __init__(self, asset: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._asset = asset if asset is not None else {}
        if isinstance(self._asset, dict):
            self._frames = int(self._asset.get("frames") or 24)
            self._name = str(self._asset.get("name") or self._asset.get("id") or "Transition")
            self._kind = str(self._asset.get("sub") or self._asset.get("category") or "transform")
            self._thumb_path = str(self._asset.get("thumbnail_path") or "")
        else:
            self._frames = int(getattr(self._asset, "frames", 24) or 24)
            self._name = str(getattr(self._asset, "name", "") or getattr(self._asset, "id", "Transition"))
            self._kind = str(getattr(self._asset, "sub", "") or getattr(self._asset, "category", "transform"))
            self._thumb_path = str(getattr(self._asset, "thumbnail_path", "") or "")

        self._progress: float = 0.0
        self.setFixedHeight(128)
        self.setCursor(Qt.PointingHandCursor)

        self._anim_timer = QTimer(self)
        self.anim_timer = self._anim_timer
        self._anim_timer.setInterval(33)  # ~30 FPS
        self._anim_timer.timeout.connect(self._on_tick)
        self._anim_timer.start()

    def set_frames(self, frames: int):
        self._frames = max(1, int(frames))
        self.update()

    def _on_tick(self):
        self._progress += 0.03
        if self._progress > 1.3:
            self._progress = 0.0
        self.update()

    def showEvent(self, event):
        if hasattr(self, "_anim_timer") and not self._anim_timer.isActive():
            self._anim_timer.start()
        if event is not None:
            super().showEvent(event)

    def hideEvent(self, event):
        if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
            self._anim_timer.stop()
        if event is not None:
            super().hideEvent(event)

    def mousePressEvent(self, event):
        self._progress = 0.0
        self.update()
        if event is not None:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0, 0, self.width(), self.height())
        try:
            from src.ui.lux.library.transition_preview_generator import draw_transition_frame
            draw_transition_frame(
                painter,
                rect,
                self._asset,
                progress=min(1.0, max(0.0, self._progress)),
                frames=self._frames,
                kind=self._kind,
                thumb_path=self._thumb_path,
                show_hud=True,
            )
            return
        except Exception:
            pass

        w = self.width()
        h = self.height()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(0, 0, w, h, 8, 8)
        painter.setClipPath(clip_path)
        painter.fillRect(0, 0, w, h, QColor("#1c1917"))
        p = min(1.0, max(0.0, self._progress))
        bx = int(w * (1.0 - p))
        painter.fillRect(bx, 0, w - bx, h, QColor("#083344"))
        if 0.05 < p < 0.95:
            painter.setPen(QPen(QColor("#06b6d4"), 2))
            painter.drawLine(bx, 0, bx, h)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(QColor("#06b6d4"))
        painter.drawText(QRectF(8, 8, w - 16, 18), Qt.AlignmentFlag.AlignRight, f"{self._frames} frames")
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(10, h - 24, w - 20, 18), Qt.AlignmentFlag.AlignLeft, f"🔁 {self._name}")


class MacroEditor(QWidget):
    """Parameter editor for transitions, memes, overlays, and Fusion macros."""
    _VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")

    def __init__(self, asset: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._player: Any = None
        self._audio_output: Any = None
        self._video_widget: Optional[QWidget] = None
        self.resolved_video_path: str = ""
        self.is_video_preview: bool = False
        if asset is None:
            self._asset = {"id": "whip_pan_left", "name": "Whip Pan Left", "frames": 24}
        elif isinstance(asset, dict):
            self._asset = asset
        else:
            self._asset = {
                "id": str(getattr(asset, "id", "") or asset),
                "name": str(getattr(asset, "name", "") or asset),
                "frames": int(getattr(asset, "duration_frames", getattr(asset, "frames", 24)) or 24),
                "_raw_obj": asset,
            }
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._setup_ui()
        self._refresh_preview_source(asset if asset is not None else self._asset)

    def _resolve_verified_video(self, target_asset: Any) -> str:
        try:
            from src.core.action_guard import check_action_availability
            from src.ui.lux.library.transition_preview_generator import (
                resolve_transition_preview_video,
                build_verified_video_preview_payload,
            )
            cand_vid = resolve_transition_preview_video(target_asset)
            if cand_vid and os.path.isfile(cand_vid) and cand_vid.lower().endswith(self._VIDEO_EXTS):
                payload = build_verified_video_preview_payload(target_asset, cand_vid)
                guard = check_action_availability(payload, intent="preview")
                if guard.allowed and guard.resolved_path and guard.resolved_path.lower().endswith(self._VIDEO_EXTS):
                    return guard.resolved_path
        except Exception:
            pass
        return ""

    def _ensure_video_player(self) -> bool:
        if self._player is not None and self._video_widget is not None:
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
            if self._video_widget is None:
                self._video_widget = QVideoWidget(self.preview_stack)
                self.preview_stack.addWidget(self._video_widget)
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(0.0)
            self._player.setAudioOutput(self._audio_output)
            self._player.setVideoOutput(self._video_widget)
            self._player.mediaStatusChanged.connect(self._on_media_status_changed)
            return True
        except Exception:
            self._player = None
            return False

    def _on_media_status_changed(self, status: Any) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if self.is_video_preview and status == QMediaPlayer.MediaStatus.EndOfMedia and self._player is not None:
                self._player.setPosition(0)
                self._player.play()
        except Exception:
            pass

    def _refresh_preview_source(self, target_asset: Any) -> None:
        self.stop_preview()
        vid_path = self._resolve_verified_video(target_asset)
        if vid_path and self._ensure_video_player():
            self.resolved_video_path = vid_path
            self.is_video_preview = True
            self.trans_preview_canvas._anim_timer.stop()
            self.preview_stack.setCurrentWidget(self._video_widget)
            if self.isVisible():
                self.start_video_preview()
        else:
            self.resolved_video_path = ""
            self.is_video_preview = False
            self.preview_stack.setCurrentWidget(self.trans_preview_canvas)
            if self.isVisible() and not self.trans_preview_canvas._anim_timer.isActive():
                self.trans_preview_canvas._anim_timer.start()

    def start_video_preview(self) -> None:
        if self.is_video_preview and self.resolved_video_path and self._player is not None:
            try:
                self._player.setSource(QUrl.fromLocalFile(os.path.abspath(self.resolved_video_path)))
                self._player.play()
            except Exception:
                pass

    def stop_preview(self) -> None:
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass

    def showEvent(self, event) -> None:
        if self.is_video_preview:
            self.start_video_preview()
        if event is not None:
            super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.stop_preview()
        if event is not None:
            super().hideEvent(event)

    def bind(self, asset: Any):
        if isinstance(asset, dict):
            self._asset = asset
        else:
            self._asset = {
                "id": str(getattr(asset, "id", "") or asset),
                "name": str(getattr(asset, "name", "") or asset),
                "frames": int(getattr(asset, "duration_frames", getattr(asset, "frames", 24)) or 24),
                "_raw_obj": asset,
            }
        self.trans_preview_canvas._asset = asset if asset is not None else self._asset
        self.trans_preview_canvas._name = self._asset.get("name", "Transition")
        self.trans_preview_canvas.set_frames(int(self._asset.get("frames", 24) or 24))
        self._refresh_preview_source(asset if asset is not None else self._asset)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 0. Live Video / Fallback Transition Preview Stack at the top
        self.preview_stack = QStackedWidget(self)
        self.preview_stack.setFixedHeight(128)
        self.preview_stack.setStyleSheet("background-color: #09090b; border: 1px solid #27272a; border-radius: 6px;")

        self.trans_preview_canvas = TransitionPreviewCanvas(self._asset, self.preview_stack)
        self.preview_canvas = self.trans_preview_canvas
        self.preview_stack.addWidget(self.trans_preview_canvas)
        layout.addWidget(self.preview_stack)

        # 1. Frame Duration slider (Thời lượng: 24 frames)
        row_frames = QHBoxLayout()
        default_frames = int(self._asset.get("frames", 24) or 24)
        self.lbl_frames_title = QLabel(f"Thời lượng: {default_frames} frames")
        self.lbl_frames_title.setStyleSheet("color: #06b6d4; font-size: 11px; font-weight: 600;")
        row_frames.addWidget(self.lbl_frames_title)

        self.slider_frames = QSlider(Qt.Orientation.Horizontal)
        self.slider_frames.setRange(6, 60)
        self.slider_frames.setValue(default_frames)
        self.slider_frames.setFixedHeight(20)
        self.slider_frames.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider_frames.valueChanged.connect(self._on_frames_changed)
        row_frames.addWidget(self.slider_frames, stretch=1)
        layout.addLayout(row_frames)

        # 2. Duration (seconds)
        row_dur = QHBoxLayout()
        lbl_dur = QLabel("Thời lượng")
        lbl_dur.setFixedWidth(70)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        row_dur.addWidget(lbl_dur)

        default_dur = self._asset.get("duration", 1.0) or 1.0
        self.slider_dur = QSlider(Qt.Orientation.Horizontal)
        self.slider_dur.setRange(2, 50)  # 0.2s to 5.0s
        self.slider_dur.setValue(int(default_dur * 10))
        self.slider_dur.setFixedHeight(20)
        self.slider_dur.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.lbl_dur_val = QLabel(f"{default_dur:.1f}s")
        self.lbl_dur_val.setFixedWidth(36)
        self.lbl_dur_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_dur_val.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        self.slider_dur.valueChanged.connect(self._on_dur_changed)

        row_dur.addWidget(self.slider_dur, stretch=1)
        row_dur.addWidget(self.lbl_dur_val)
        layout.addLayout(row_dur)

        # 3. Intensity
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

    def _on_frames_changed(self, val: int):
        self.lbl_frames_title.setText(f"Thời lượng: {val} frames")
        self.trans_preview_canvas.set_frames(val)

    def _on_dur_changed(self, val: int):
        self.lbl_dur_val.setText(f"{val / 10.0:.1f}s")
        frames = max(6, min(60, int(round((val / 10.0) * 24))))
        self.slider_frames.blockSignals(True)
        self.slider_frames.setValue(frames)
        self.slider_frames.blockSignals(False)
        self.lbl_frames_title.setText(f"Thời lượng: {frames} frames")
        self.trans_preview_canvas.set_frames(frames)

    def get_params(self) -> Dict[str, Any]:
        return {
            "duration": self.slider_dur.value() / 10.0,
            "frames": self.slider_frames.value(),
            "intensity": self.slider_int.value() / 100.0,
        }
