"""
Sheet Inspector Host Widget: Responsive overlay sliding up from bottom (62% height)
or sliding in from right edge (320px width) based on Dock breakpoint.
"""
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea
)
from PySide6.QtCore import (
    Qt, QRect, Signal as pyqtSignal, QVariantAnimation, QEasingCurve
)

from .editors import (
    TextPresetEditor, AudioEditor, LutEditor, MacroEditor, SafeTrackSelector
)
from src.ui.icons import get_vector_icon


class SheetInspector(QWidget):
    """
    Sliding Inspector Sheet.
    - Width < 560px: slides from bottom, covers 62% of parent height.
    - Width >= 560px: slides from right edge, width 320px.
    """
    closed = pyqtSignal()
    insert_requested = pyqtSignal(dict, dict)  # asset, params

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._is_open = False
        self._current_asset: Optional[Dict[str, Any]] = None
        self._editor_widget: Optional[QWidget] = None
        self._track_selector: Optional[SafeTrackSelector] = None

        # Slide animation
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_geometry)
        self._anim.finished.connect(self._on_anim_finished)

        self._init_ui()
        self.hide()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _init_ui(self):
        self.setObjectName("sheet_inspector")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet("""
            QWidget#sheet_inspector {
                background-color: #141417;
                border: 1px solid #27272a;
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(8)

        # 1. Header (Grab handle + Title + Close button)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        # Grab indicator
        self.lbl_grab = QLabel("━")
        self.lbl_grab.setStyleSheet("color: #3f3f46; font-size: 16px; font-weight: bold;")
        header_row.addWidget(self.lbl_grab)

        self.lbl_title = QLabel("Tùy chỉnh tài nguyên")
        self.lbl_title.setStyleSheet("color: #f4f4f5; font-size: 12.5px; font-weight: 600;")
        header_row.addWidget(self.lbl_title, stretch=1)

        self.btn_close = QPushButton()
        self.btn_close.setIcon(get_vector_icon("cross", "#a1a1aa", 12))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setToolTip("Đóng tùy chỉnh (Esc)")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #27272a;
            }
        """)
        self.btn_close.clicked.connect(self.close_sheet)
        header_row.addWidget(self.btn_close)
        layout.addLayout(header_row)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background-color: #27272a; max-height: 1px;")
        layout.addWidget(sep)

        # 2. Scrollable Body
        self.scroll_area = QScrollArea()
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setStyleSheet("background: transparent;")

        self.body_container = QWidget()
        self.body_layout = QVBoxLayout(self.body_container)
        self.body_layout.setContentsMargins(0, 4, 0, 4)
        self.body_layout.setSpacing(10)
        self.scroll_area.setWidget(self.body_container)

        layout.addWidget(self.scroll_area, stretch=1)

        # 3. Footer: Primary Insert Button
        self.btn_insert = QPushButton("Chèn tại playhead  ⏎")
        self.btn_insert.setIcon(get_vector_icon("paste", "#ffffff", 14))
        self.btn_insert.setFixedHeight(32)
        self.btn_insert.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_insert.setCursor(Qt.PointingHandCursor)
        self.btn_insert.setStyleSheet("""
            QPushButton {
                background: #d94f3d;
                color: #ffffff;
                font-size: 12.5px;
                font-weight: 600;
                border: 1px solid #e86352;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #e86352;
                border-color: #f07b6b;
            }
        """)
        self.btn_insert.clicked.connect(self._on_insert_clicked)
        layout.addWidget(self.btn_insert)

    def open_sheet(self, asset: Any):
        if asset is not None and not isinstance(asset, dict):
            if hasattr(asset, "model_dump") and callable(asset.model_dump):
                asset = asset.model_dump()
            elif hasattr(asset, "dict") and callable(asset.dict):
                asset = asset.dict()
            elif hasattr(asset, "__dict__"):
                asset = dict(asset.__dict__)
            else:
                asset = {"id": str(asset), "name": str(asset)}
        self._current_asset = asset
        self._build_editors(asset)
        self._is_open = True

        parent = self.parentWidget()
        if not parent:
            self.show()
            return

        target_geom = self._calc_open_geometry()
        start_geom = self._calc_closed_geometry(target_geom)

        self.setGeometry(start_geom)
        self.show()
        self.raise_()

        self._anim.stop()
        self._anim.setStartValue(start_geom)
        self._anim.setEndValue(target_geom)
        self._anim.start()

    def close_sheet(self, animated: bool = True):
        if not self._is_open:
            return
        self._is_open = False
        self.closed.emit()

        if not animated or not self.isVisible():
            self.hide()
            return

        current_geom = self.geometry()
        target_geom = self._calc_closed_geometry(current_geom)

        self._anim.stop()
        self._anim.setStartValue(current_geom)
        self._anim.setEndValue(target_geom)
        self._anim.start()

    def toggle_sheet(self, asset: Optional[Dict[str, Any]] = None):
        if self._is_open:
            self.close_sheet()
        else:
            if asset:
                self.open_sheet(asset)

    def update_geometry_for_parent(self):
        """Called when parent Dock resizes while sheet is open."""
        if not self._is_open:
            return
        self.setGeometry(self._calc_open_geometry())

    def _calc_open_geometry(self) -> QRect:
        parent = self.parentWidget()
        if not parent:
            return QRect(0, 0, 320, 400)

        pw = parent.width()
        ph = parent.height()

        if pw < 560:
            # Bottom sheet mode: 62% of dock height, full width
            h = int(ph * 0.62)
            y = ph - h
            return QRect(0, y, pw, h)
        else:
            # Right sheet mode: 320px width, full dock height
            w = 320
            x = pw - w
            return QRect(x, 0, w, ph)

    def _calc_closed_geometry(self, open_geom: QRect) -> QRect:
        parent = self.parentWidget()
        pw = parent.width() if parent else 420
        ph = parent.height() if parent else 720

        if pw < 560:
            # Slid down off the bottom
            return QRect(open_geom.x(), ph, open_geom.width(), open_geom.height())
        else:
            # Slid right off the edge
            return QRect(pw, open_geom.y(), open_geom.width(), open_geom.height())

    def _build_editors(self, asset: Dict[str, Any]):
        self.lbl_title.setText(asset.get("name", "Tài nguyên"))

        # Clear old editor widgets
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cat = asset.get("category", "text")
        is_audio = cat in ("sfx", "bgm")

        if cat == "text":
            self._editor_widget = TextPresetEditor(asset, self.body_container)
        elif is_audio:
            self._editor_widget = AudioEditor(asset, self.body_container)
        elif cat == "lut":
            self._editor_widget = LutEditor(asset, self.body_container)
        else:
            self._editor_widget = MacroEditor(asset, self.body_container)

        self.body_layout.addWidget(self._editor_widget)

        # Track selector
        self._track_selector = SafeTrackSelector(is_audio=is_audio, parent=self.body_container)
        self.body_layout.addWidget(self._track_selector)

        self.body_layout.addStretch(1)

    def _on_insert_clicked(self):
        if not self._current_asset:
            return
        params = {}
        if self._editor_widget and hasattr(self._editor_widget, "get_params"):
            params = self._editor_widget.get_params()
        if self._track_selector:
            params["track_mode"] = self._track_selector.get_track_mode()

        self.insert_requested.emit(self._current_asset, params)
        self.close_sheet()

    def _on_anim_geometry(self, val):
        if isinstance(val, QRect):
            self.setGeometry(val)

    def _on_anim_finished(self):
        if not self._is_open:
            self.hide()
