"""
EdgeDockWindow: Luxury Akitt-Pro-Style Floating Edge Dock for ChunDVC.
Features:
- 6-State machine: Hidden, Rail 44px, Peek 56px, Open (320-420px), Pinned, Wide 760px.
- Anchored against DaVinci Resolve right edge (ResolveWindowAnchor) with boundary clamping.
- Strict Focus Policy: Window never steals keyboard focus from Resolve (Space / JKL work instantly).
- Header 44px, Category Nav, AssetGridView (virtualized 16:9), Sticky ActionBar 56px, StatusBar 22px.
- Sheet Inspector: Responsive overlay sliding from bottom (62%) or right (320px).
"""
import logging
from typing import Optional, Tuple, Dict, Any, List

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, Signal as pyqtSignal, QVariantAnimation,
    QEasingCurve, QTimer
)
from PySide6.QtGui import QKeyEvent, QScreen

from src.ui.lux.lux_effects import get_luxury_dark_stylesheet
from src.ui.lux.window_anchor import (
    calculate_dock_geometry, get_resolve_window_rect, ResolveWindowAnchor
)
from src.ui.lux.library.asset_model import AssetListModel
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.ui.lux.library.asset_grid import AssetGridView
from src.services.insert_service import InsertService
from src.services.resolve_state import ResolveStateService
from src.core.fast_paste import focus_davinci_resolve

from .dock_state import DockState, DockStateMachine
from .dock_rail import DockRailWidget
from .dock_header import DockHeaderWidget
from .dock_status import DockStatusBar
from ..library.category_nav import CategoryNavWidget
from ..library.action_bar import DockActionBar
from ..inspector.inspector_host import SheetInspector

logger = logging.getLogger("ResolveFlow.EdgeDock")


class EdgeDockWindow(QWidget):
    """
    Floating Luxury Edge Dock docking to DaVinci Resolve right edge.
    Supports 6 states: Hidden, Rail (44px), Peek (56px), Open (320-420px), Pinned, Wide (760px).
    """
    expanded_changed = pyqtSignal(bool)
    state_changed = pyqtSignal(str)
    asset_inserted = pyqtSignal(dict, dict)
    restore_standard_window_requested = pyqtSignal()
    palette_requested = pyqtSignal()
    director_requested = pyqtSignal()

    def __init__(
        self,
        insert_service: Optional[InsertService] = None,
        resolve_state: Optional[ResolveStateService] = None,
        edge_side: str = "right",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._edge_side = edge_side
        self._insert_service = insert_service
        self._resolve_state = resolve_state
        self._anchor_rect: Optional[Tuple[int, int, int, int]] = None
        self._selected_asset: Optional[Dict[str, Any]] = None

        # State machine
        self.fsm = DockStateMachine(self)
        self.fsm.state_changed.connect(self._on_fsm_state_changed)
        self.fsm.width_requested.connect(self._on_width_requested)

        # Non-blocking geometry animation
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim_geometry)
        self._anim.finished.connect(self._on_anim_finished)

        self._init_ui()
        self.setStyleSheet(get_luxury_dark_stylesheet())

        # Connect Resolve state signals if provided
        if self._resolve_state:
            self._resolve_state.state_changed.connect(self._on_resolve_state_changed)

        # Initial geometry
        initial_w = self.fsm.get_width_for_state(DockState.RAIL)
        self.resize(initial_w, 720)
        self._update_dock_position(target_w=initial_w, animated=False)

    @property
    def is_expanded(self) -> bool:
        return self.fsm.is_open

    @property
    def is_pinned(self) -> bool:
        return self.fsm.is_pinned

    @property
    def is_wide(self) -> bool:
        return self.fsm.is_wide

    @property
    def current_state(self) -> DockState:
        return self.fsm.state

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Background styled container frame
        self.frame = QFrame(self)
        self.frame.setObjectName("lux_drawer_panel")
        self.frame.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outer_layout.addWidget(self.frame)

        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        # Mode Stack: Page 0 = Rail/Peek (44px), Page 1 = Full Expanded Dock View
        self.stack = QStackedWidget(self.frame)
        self.stack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        frame_layout.addWidget(self.stack)

        # --- Page 0: Collapsed Rail Widget ---
        self.rail = DockRailWidget(self.stack)
        self.rail.open_dock_requested.connect(self.open_dock)
        self.rail.category_selected.connect(self._on_rail_category_selected)
        self.rail.palette_requested.connect(self.palette_requested.emit)
        self.rail.director_requested.connect(self.director_requested.emit)
        self.stack.addWidget(self.rail)

        # --- Page 1: Expanded View Container ---
        self.expanded_container = QWidget(self.stack)
        self.expanded_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        exp_layout = QVBoxLayout(self.expanded_container)
        exp_layout.setContentsMargins(0, 0, 0, 0)
        exp_layout.setSpacing(0)

        # 1. Header 44px
        self.header = DockHeaderWidget(self.expanded_container)
        self.header.search_changed.connect(self._on_search_changed)
        self.header.search_submitted.connect(self._on_search_submitted)
        self.header.escape_requested.connect(self._on_search_escape)
        self.header.wide_toggled.connect(self._on_toggle_wide)
        self.header.pin_toggled.connect(self._on_toggle_pin)
        self.header.collapse_clicked.connect(self.collapse_dock)
        exp_layout.addWidget(self.header)

        # 2. Horizontal Category Navigation
        self.category_nav = CategoryNavWidget(self.expanded_container)
        self.category_nav.category_changed.connect(self._on_category_changed)
        self.category_nav.sub_category_changed.connect(self._on_sub_category_changed)
        exp_layout.addWidget(self.category_nav)

        # 3. Virtualized Asset Grid View
        self.asset_model = AssetListModel(parent=self.expanded_container)
        self.proxy_model = AssetFilterProxyModel(parent=self.expanded_container)
        self.proxy_model.setSourceModel(self.asset_model)

        self.grid = AssetGridView(self.expanded_container)
        self.grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid.setModel(self.proxy_model)
        self.grid.asset_selected.connect(self._on_asset_clicked)
        self.grid.asset_activated.connect(self._on_asset_double_clicked)
        exp_layout.addWidget(self.grid, stretch=1)

        # 4. Sticky Action Bar 56px
        self.action_bar = DockActionBar(self.expanded_container)
        self.action_bar.primary_triggered.connect(self._on_primary_action)
        self.action_bar.tune_triggered.connect(self._on_tune_action)
        self.action_bar.paste_fusion_triggered.connect(self._on_paste_fusion)
        self.action_bar.copy_code_triggered.connect(self._on_copy_fusion_code)
        self.action_bar.install_fusion_triggered.connect(self._on_install_fusion)
        self.action_bar.new_track_triggered.connect(self._on_insert_new_track)
        self.action_bar.toggle_fav_triggered.connect(self._on_toggle_fav)
        self.action_bar.menu_visibility_changed.connect(self.fsm.set_menu_open)
        exp_layout.addWidget(self.action_bar)

        # 5. Status Bar 22px
        self.status_bar = DockStatusBar(self.expanded_container)
        exp_layout.addWidget(self.status_bar)

        self.stack.addWidget(self.expanded_container)
        self.stack.setCurrentIndex(0)

        # 6. Sheet Inspector Overlay
        self.sheet = SheetInspector(self)
        self.sheet.closed.connect(lambda: self.fsm.set_sheet_open(False))
        self.sheet.insert_requested.connect(self._on_sheet_insert_requested)

    # -------------------------------------------------------------------------
    # Public API & Data Binding
    # -------------------------------------------------------------------------
    def set_assets(self, assets: List[Any]):
        """Sets library assets in the list model."""
        self.asset_model.set_assets(assets)

    def set_search_index(self, indexer):
        """Sets SearchIndex for ranked search in proxy model."""
        self.proxy_model.set_search_index(indexer)

    def set_anchor_rect(self, rect: Optional[Tuple[int, int, int, int]]):
        self._anchor_rect = rect
        if rect:
            self.fsm.set_resolve_width(rect[2])
        self._update_dock_position(target_w=self.width(), animated=False)

    def open_dock(self, animated: bool = True):
        self.fsm.open_dock(animated=animated)

    def collapse_dock(self, animated: bool = True):
        self.fsm.collapse_dock(animated=animated)

    def toggle(self, animated: bool = True):
        self.fsm.toggle_dock(animated=animated)

    # -------------------------------------------------------------------------
    # Geometry & Boundary Clamping
    # -------------------------------------------------------------------------
    def _get_screen_rect(self) -> Tuple[int, int, int, int]:
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            return (geom.x(), geom.y(), geom.width(), geom.height())
        return (0, 0, 1920, 1080)

    def _calc_target_geometry(self, target_w: int) -> QRect:
        screen_rect = self._get_screen_rect()
        sx, sy, sw, sh = screen_rect

        if self._anchor_rect:
            ax, ay, aw, ah = self._anchor_rect
            # Clip anchor vertically
            y = max(sy, ay)
            h = min(sh, ah)

            if aw < 900:
                # Narrow Resolve: float outside right edge if space permits
                ideal_x = ax + aw
                if ideal_x + target_w <= sx + sw:
                    x = ideal_x
                elif ax - target_w >= sx:
                    # Float outside left edge
                    x = ax - target_w
                else:
                    # Fit flush inside right edge
                    x = ax + aw - target_w
            else:
                # Normal docking flush to right edge of Resolve
                x = ax + aw - target_w
        else:
            # Fallback to primary screen right edge
            x = sx + sw - target_w
            y = sy
            h = sh

        return QRect(int(x), int(y), int(target_w), int(h))

    def _update_dock_position(self, target_w: Optional[int] = None, duration: int = 0, animated: bool = True):
        w = target_w if target_w is not None else self.width()
        target_geom = self._calc_target_geometry(w)

        if not animated or duration <= 0 or not self.isVisible():
            self._anim.stop()
            self.setGeometry(target_geom)
            self._sync_child_layouts()
            return

        self._anim.stop()
        self._anim.setDuration(duration)
        easing = QEasingCurve.Type.OutCubic if target_geom.width() > self.width() else QEasingCurve.Type.InCubic
        self._anim.setEasingCurve(easing)
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target_geom)
        self._anim.start()

    def _on_anim_geometry(self, val):
        if isinstance(val, QRect):
            self.setGeometry(val)
            self._sync_child_layouts()

    def _on_anim_finished(self):
        self._sync_child_layouts()
        self.update()

    def _sync_child_layouts(self):
        # Update sheet overlay size if sheet is open
        if self.sheet.is_open:
            self.sheet.update_geometry_for_parent()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_child_layouts()

    # -------------------------------------------------------------------------
    # State Machine Slots
    # -------------------------------------------------------------------------
    def _on_fsm_state_changed(self, new_state: DockState):
        logger.debug("Dock state transitioned to: %s", new_state.value)
        self.state_changed.emit(new_state.value)

        is_open = self.fsm.is_open
        self.stack.setCurrentIndex(1 if is_open else 0)
        self.header.set_pinned(self.fsm.is_pinned)
        self.header.set_wide(self.fsm.is_wide)

        self.expanded_changed.emit(is_open)

    def _on_width_requested(self, target_w: int, duration_ms: int):
        self._update_dock_position(target_w=target_w, duration=duration_ms, animated=(duration_ms > 0))

    # -------------------------------------------------------------------------
    # Hover & Focus Management
    # -------------------------------------------------------------------------
    def enterEvent(self, event):
        super().enterEvent(event)
        if not self.fsm.is_open:
            self.fsm.on_mouse_enter_rail()
        else:
            self.fsm.on_mouse_enter_dock()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.fsm.on_mouse_leave_dock()

    def _on_search_changed(self, text: str):
        self.fsm.on_search_text_changed(text)
        self.proxy_model.set_search_query(text)

    def _on_search_submitted(self):
        # Insert first matched item if exists
        if self.proxy_model.rowCount() > 0:
            idx = self.proxy_model.index(0, 0)
            asset = idx.data(Qt.ItemDataRole.UserRole)
            if asset:
                self._do_insert(asset, {})
        self.header.txt_search.clearFocus()
        focus_davinci_resolve()

    def _on_search_escape(self):
        self.header.clear_search()
        self.fsm.collapse_dock()
        focus_davinci_resolve()

    def _on_toggle_wide(self):
        self.fsm.toggle_wide()

    def _on_toggle_pin(self):
        self.fsm.toggle_pin()

    # -------------------------------------------------------------------------
    # Library Interaction Slots
    # -------------------------------------------------------------------------
    def _on_rail_category_selected(self, cat: str):
        self.category_nav.select_category(cat)

    def _on_category_changed(self, cat: str):
        self.proxy_model.set_category(cat)

    def _on_sub_category_changed(self, sub: str):
        self.proxy_model.set_sub_category(sub)

    def _on_asset_clicked(self, asset: Dict[str, Any]):
        self._selected_asset = asset
        timecode = self.status_bar.lbl_timecode.text()
        self.action_bar.set_asset(asset, timecode=timecode)

        # CRITICAL: Always return focus to Resolve immediately on card click
        focus_davinci_resolve()

    def _on_asset_double_clicked(self, asset: Dict[str, Any]):
        self._selected_asset = asset
        self._do_insert(asset, {})
        focus_davinci_resolve()

    def _on_primary_action(self):
        if self._selected_asset:
            self._do_insert(self._selected_asset, {})
        focus_davinci_resolve()

    def _on_tune_action(self):
        if not self._selected_asset:
            return
        if self.sheet.is_open:
            self.sheet.close_sheet()
            self.fsm.set_sheet_open(False)
        else:
            self.fsm.set_sheet_open(True)
            self.sheet.open_sheet(self._selected_asset)

    def _on_sheet_insert_requested(self, asset: Dict[str, Any], params: Dict[str, Any]):
        self._do_insert(asset, params)
        self.fsm.set_sheet_open(False)
        focus_davinci_resolve()

    def _on_paste_fusion(self):
        if self._selected_asset and self._insert_service:
            res = self._insert_service.insert(self._selected_asset, mode="fusion_paste")
            self.asset_inserted.emit(self._selected_asset, {"mode": "fusion_paste", "result": res})
        focus_davinci_resolve()

    def _on_copy_fusion_code(self):
        if self._selected_asset:
            code = self._selected_asset.get("content", "") or self._selected_asset.get("path", "")
            app = QApplication.instance()
            if app:
                app.clipboard().setText(str(code))
        focus_davinci_resolve()

    def _on_install_fusion(self):
        logger.info("Install to Fusion templates requested for: %s", self._selected_asset)
        focus_davinci_resolve()

    def _on_insert_new_track(self):
        if self._selected_asset:
            self._do_insert(self._selected_asset, {"track_mode": "new"})
        focus_davinci_resolve()

    def _on_toggle_fav(self):
        if self._selected_asset:
            is_fav = not self._selected_asset.get("is_fav", False)
            self._selected_asset["is_fav"] = is_fav
            self.action_bar.set_asset(self._selected_asset, is_fav=is_fav)

    def _do_insert(self, asset: Dict[str, Any], params: Dict[str, Any]):
        if not asset:
            return
        logger.info("Inserting asset '%s' with params %s", asset.get("name"), params)
        if self._insert_service:
            mode = "timeline_track" if params.get("track_mode") != "new" else "new_track"
            res = self._insert_service.insert(asset, mode=mode, extra_params=params)
            self.asset_inserted.emit(asset, {"params": params, "result": res})
        else:
            self.asset_inserted.emit(asset, params)

    def _on_resolve_state_changed(self, connected: bool, page: str, timeline: str, playhead_tc: str):
        self.rail.set_connected(connected)
        self.status_bar.set_connected(connected)
        self.status_bar.set_timeline(timeline)
        self.status_bar.set_timecode(playhead_tc)
        self.header.set_page_label(page.capitalize())

    # -------------------------------------------------------------------------
    # Keyboard Handling (When Dock is open and search input is not active)
    # -------------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_primary_action()
            event.accept()
            return
        elif key == Qt.Key.Key_Tab:
            self._on_tune_action()
            event.accept()
            return
        elif key == Qt.Key.Key_Escape:
            if self.sheet.is_open:
                self.sheet.close_sheet()
                self.fsm.set_sheet_open(False)
            else:
                self.fsm.collapse_dock()
            focus_davinci_resolve()
            event.accept()
            return

        super().keyPressEvent(event)
