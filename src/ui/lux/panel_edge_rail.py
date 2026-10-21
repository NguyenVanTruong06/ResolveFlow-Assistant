"""
Floating Edge Panel Drawer (Learning from Akitt Pro 4.0).
Provides a sleek, non-blocking frameless edge drawer that docks against DaVinci Resolve
or screen edges, supporting smooth slide-in / slide-out animations and peek reveal.
"""
from typing import Optional, Tuple

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QApplication
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, Signal as pyqtSignal, QVariantAnimation,
    QEasingCurve, QTimer
)
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QPainterPath, QCursor
)

from .lux_effects import get_luxury_dark_stylesheet, SegmentIndicator, RippleOverlay
from .window_anchor import calculate_dock_geometry, get_resolve_window_rect, ResolveWindowAnchor


class EdgeDrawerWindow(QWidget):
    """
    Floating Drawer Window docking to the screen or DaVinci Resolve edge.
    Supports collapsed rail (~40px) and full expanded panel (~440px) modes.
    """
    expanded_changed = pyqtSignal(bool)
    restore_standard_window_requested = pyqtSignal()

    COLLAPSED_WIDTH = 40
    EXPANDED_WIDTH = 440
    PEEK_OFFSET = 12

    def __init__(self, parent: Optional[QWidget] = None, edge_side: str = "right"):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._edge_side = edge_side
        self._is_expanded = False
        self._is_peeking = False
        self._central_widget: Optional[QWidget] = None
        self._anchor_rect: Optional[Tuple[int, int, int, int]] = None

        # Non-blocking geometry animation
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_geometry)
        self._anim.finished.connect(self._on_anim_finished)

        self._init_ui()
        self.setStyleSheet(get_luxury_dark_stylesheet())
        self.resize(self.COLLAPSED_WIDTH, 720)

    @property
    def is_expanded(self) -> bool:
        return self._is_expanded

    @property
    def central_widget(self) -> Optional[QWidget]:
        return self._central_widget

    def _init_ui(self):
        # Outer layout
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Background styled container frame
        self.frame = QFrame(self)
        self.frame.setObjectName("lux_drawer_panel")
        outer_layout.addWidget(self.frame)

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(4, 4, 4, 4)
        frame_layout.setSpacing(0)

        # Mode Stack: Page 0 = Collapsed Rail, Page 1 = Expanded Full View
        self.stack = QStackedWidget(self.frame)
        frame_layout.addWidget(self.stack)

        # --- Page 0: Collapsed Rail ---
        self.rail_widget = QWidget()
        rail_layout = QVBoxLayout(self.rail_widget)
        rail_layout.setContentsMargins(2, 6, 2, 6)
        rail_layout.setSpacing(10)
        rail_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # Mini Logo Badge
        self.btn_rail_logo = QPushButton("⚡")
        self.btn_rail_logo.setFixedSize(30, 30)
        self.btn_rail_logo.setToolTip("ChunDVC Luxury Dock")
        self.btn_rail_logo.clicked.connect(self.expand)
        rail_layout.addWidget(self.btn_rail_logo)

        # Chevron Expand Button
        self.btn_rail_expand = QPushButton("«" if self._edge_side == "right" else "»")
        self.btn_rail_expand.setFixedSize(30, 30)
        self.btn_rail_expand.setToolTip("Mở rộng bảng điều khiển")
        self.btn_rail_expand.clicked.connect(self.expand)
        rail_layout.addWidget(self.btn_rail_expand)

        rail_layout.addStretch(1)

        # Quick Peek tab icons
        self.btn_peek_copilot = QPushButton("🧠")
        self.btn_peek_copilot.setFixedSize(30, 30)
        self.btn_peek_copilot.setToolTip("AI Copilot")
        self.btn_peek_copilot.clicked.connect(self.expand)
        rail_layout.addWidget(self.btn_peek_copilot)

        self.btn_peek_cut = QPushButton("✂")
        self.btn_peek_cut.setFixedSize(30, 30)
        self.btn_peek_cut.setToolTip("Auto Cut")
        self.btn_peek_cut.clicked.connect(self.expand)
        rail_layout.addWidget(self.btn_peek_cut)

        self.btn_peek_titles = QPushButton("🗂️")
        self.btn_peek_titles.setFixedSize(30, 30)
        self.btn_peek_titles.setToolTip("Text+ Presets")
        self.btn_peek_titles.clicked.connect(self.expand)
        rail_layout.addWidget(self.btn_peek_titles)

        rail_layout.addStretch(1)

        # Status Dot
        self.lbl_status_dot = QLabel("●")
        self.lbl_status_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status_dot.setStyleSheet("color: #10B981; font-size: 14px;")
        self.lbl_status_dot.setToolTip("Sẵn sàng kết nối")
        rail_layout.addWidget(self.lbl_status_dot)

        self.stack.addWidget(self.rail_widget)

        # --- Page 1: Expanded Full View ---
        self.expanded_widget = QWidget()
        exp_layout = QVBoxLayout(self.expanded_widget)
        exp_layout.setContentsMargins(6, 6, 6, 6)
        exp_layout.setSpacing(6)

        # Header
        header_row = QHBoxLayout()
        header_row.setContentsMargins(4, 2, 4, 4)

        lbl_brand = QLabel("CHUNDVC <b>DOCK</b>")
        lbl_brand.setStyleSheet("color: #3dcee1; font-size: 13px; letter-spacing: 1px;")
        header_row.addWidget(lbl_brand)

        badge = QLabel("PRO 4.0")
        badge.setStyleSheet(
            "color: #10B981; font-size: 9px; font-weight: bold; "
            "background-color: rgba(16, 185, 129, 0.15); "
            "border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 3px; "
            "padding: 1px 4px;"
        )
        header_row.addWidget(badge)
        header_row.addStretch(1)

        # Standard window restore button
        btn_restore = QPushButton("🪟 Chuẩn")
        btn_restore.setToolTip("Quay lại giao diện cửa sổ tiêu chuẩn")
        btn_restore.setStyleSheet("font-size: 11px; padding: 3px 8px;")
        btn_restore.clicked.connect(self.restore_standard_window_requested.emit)
        header_row.addWidget(btn_restore)

        # Collapse chevron
        btn_collapse = QPushButton("»" if self._edge_side == "right" else "«")
        btn_collapse.setToolTip("Thu nhỏ vào mép màn hình")
        btn_collapse.setFixedSize(28, 26)
        btn_collapse.clicked.connect(self.collapse)
        header_row.addWidget(btn_collapse)

        exp_layout.addLayout(header_row)

        # Container for central hosted tab widget
        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        exp_layout.addWidget(self.content_container, stretch=1)

        self.stack.addWidget(self.expanded_widget)
        self.stack.setCurrentIndex(0)

    def set_central_widget(self, widget: QWidget):
        """Hosts a central widget (such as TabWidget or MacroGridWidget) inside the expanded drawer."""
        if self._central_widget:
            self.content_layout.removeWidget(self._central_widget)
        self._central_widget = widget
        if widget:
            self.content_layout.addWidget(widget)

    def set_anchor_rect(self, rect: Optional[Tuple[int, int, int, int]]):
        self._anchor_rect = rect
        self._update_dock_position(animated=False)

    def _get_screen_rect(self) -> Tuple[int, int, int, int]:
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            return (geom.x(), geom.y(), geom.width(), geom.height())
        return (0, 0, 1920, 1080)

    def _update_dock_position(self, animated: bool = True):
        w = self.EXPANDED_WIDTH if self._is_expanded else self.COLLAPSED_WIDTH
        screen_rect = self._get_screen_rect()
        x, y, w, h = calculate_dock_geometry(self._anchor_rect, screen_rect, w, self._edge_side)
        target_geom = QRect(x, y, w, h)

        if not animated or not self.isVisible():
            self.setGeometry(target_geom)
            return

        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target_geom)
        self._anim.start()

    def expand(self, animated: bool = True):
        """Expands the drawer to reveal full workspace."""
        if self._is_expanded:
            return
        self._is_expanded = True
        self._is_peeking = False
        self.stack.setCurrentIndex(1)
        self._update_dock_position(animated=animated)
        self.expanded_changed.emit(True)

    def collapse(self, animated: bool = True):
        """Collapses the drawer to a thin edge rail."""
        if not self._is_expanded:
            return
        self._is_expanded = False
        self._is_peeking = False
        self.stack.setCurrentIndex(0)
        self._update_dock_position(animated=animated)
        self.expanded_changed.emit(False)

    def toggle(self, animated: bool = True):
        if self._is_expanded:
            self.collapse(animated=animated)
        else:
            self.expand(animated=animated)

    def _peek_reveal(self, enable: bool):
        """Subtle 12px peek slide out when mouse approaches collapsed rail."""
        if self._is_expanded:
            return
        if enable and not self._is_peeking:
            self._is_peeking = True
            geom = self.geometry()
            dx = -self.PEEK_OFFSET if self._edge_side == "right" else self.PEEK_OFFSET
            self.setGeometry(geom.x() + dx, geom.y(), geom.width(), geom.height())
        elif not enable and self._is_peeking:
            self._is_peeking = False
            geom = self.geometry()
            dx = self.PEEK_OFFSET if self._edge_side == "right" else -self.PEEK_OFFSET
            self.setGeometry(geom.x() + dx, geom.y(), geom.width(), geom.height())

    def enterEvent(self, event):
        super().enterEvent(event)
        if not self._is_expanded:
            self._peek_reveal(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if not self._is_expanded:
            self._peek_reveal(False)

    def _on_anim_geometry(self, val):
        if isinstance(val, QRect):
            self.setGeometry(val)

    def _on_anim_finished(self):
        # Refresh container layout
        self.update()
