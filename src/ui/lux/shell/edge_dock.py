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
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication,
    QSizePolicy
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, Signal as pyqtSignal, QVariantAnimation,
    QEasingCurve, QTimer, QSettings
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
from ..context.context_profiles import ContextProfile, PROFILES
from ..context.context_router import ContextRouter
from ..context.special_blocks import (
    EditSuggestBlock, CutPadsBlock, FusionCaptureBlock,
    ColorSpecialBlock, FairlightBlock, DeliverBlock, MediaBlock
)
from ..palette.command_palette import CommandPaletteWindow
from src.services.global_hotkey import GlobalHotkeyService

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

        # Context Router
        self.router = ContextRouter(initial_page="edit", parent=self)
        self.router.profile_changed.connect(self._on_profile_changed)
        self.router.pending_changed.connect(self._on_pending_changed)
        self.router.profile_locked_changed.connect(self._on_profile_locked_changed)
        self.router.search_boost_requested.connect(self._on_search_boost_requested)
        self._current_profile: Optional[ContextProfile] = None

        # State machine
        self.fsm = DockStateMachine(self)
        self.fsm.state_changed.connect(self._on_fsm_state_changed)
        self.fsm.width_requested.connect(self._on_width_requested)

        # Quick Command Palette HUD
        self.palette = CommandPaletteWindow(
            insert_service=self._insert_service,
            parent=None,
        )
        self.palette.asset_inserted.connect(self.asset_inserted.emit)
        self.palette.customize_requested.connect(self._on_palette_customize_requested)
        self.palette.command_executed.connect(self._on_palette_command_executed)
        self.palette_requested.connect(lambda: self.open_palette(command_mode=False))
        self.director_requested.connect(self.open_director)
        self._director_window = None

        # Global Hotkey Service
        self.hotkey_service = GlobalHotkeyService(self)
        self.hotkey_service.palette_requested.connect(lambda: self.open_palette(command_mode=False))
        self.hotkey_service.command_mode_requested.connect(lambda: self.open_palette(command_mode=True))

        # Non-blocking geometry animation
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim_geometry)
        self._anim.finished.connect(self._on_anim_finished)

        # Read saved dock edge preference (default: 'right')
        settings = QSettings("ChunDVC", "ResolveFlow")
        self.dock_edge = str(settings.value("dock_edge", "right"))
        if self.dock_edge not in ("left", "right"):
            self.dock_edge = "right"

        self._init_ui()
        self.setStyleSheet(get_luxury_dark_stylesheet())
        self.header.set_dock_edge(self.dock_edge)

        # Initialize context profile appearance
        self._on_profile_changed(self.router.active_profile)

        # Connect Resolve state signals if provided
        if self._resolve_state:
            self._resolve_state.state_changed.connect(self._on_resolve_state_changed)

        # Resolve Window Anchor (Dynamic tracking)
        self.window_anchor = ResolveWindowAnchor(poll_interval_ms=1200, parent=self)
        self.window_anchor.anchor_updated.connect(self.set_anchor_rect)
        self.window_anchor.start()

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

    def minimumSizeHint(self) -> QSize:
        w = self.fsm.get_width_for_state(self.fsm.state)
        return QSize(w, 200)

    def sizeHint(self) -> QSize:
        w = self.fsm.get_width_for_state(self.fsm.state)
        return QSize(w, 720)

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Background styled container frame
        self.frame = QFrame(self)
        self.frame.setObjectName("lux_drawer_panel")
        self.frame.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outer_layout.addWidget(self.frame)

        self.frame_layout = QHBoxLayout(self.frame)
        self.frame_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_layout.setSpacing(0)

        # 2px Accent Line along inner edge
        self.accent_line = QFrame(self.frame)
        self.accent_line.setFixedWidth(2)
        self.accent_line.setStyleSheet("background: #7c3aed;")

        # Mode Stack: Page 0 = Rail/Peek (44px), Page 1 = Full Expanded Dock View
        self.stack = QStackedWidget(self.frame)
        self.stack.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        if self.dock_edge == "left":
            self.frame_layout.addWidget(self.stack, stretch=1)
            self.frame_layout.addWidget(self.accent_line)
        else:
            self.frame_layout.addWidget(self.accent_line)
            self.frame_layout.addWidget(self.stack, stretch=1)

        # --- Page 0: Collapsed Rail Widget ---
        self.rail = DockRailWidget(self.stack)
        self.rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.rail.open_dock_requested.connect(self.open_dock)
        self.rail.category_selected.connect(self._on_rail_category_selected)
        self.rail.palette_requested.connect(self.palette_requested.emit)
        self.rail.director_requested.connect(self.director_requested.emit)
        self.rail.settings_requested.connect(self.open_settings)
        self.stack.addWidget(self.rail)

        # --- Page 1: Expanded View Container ---
        self.expanded_container = QWidget(self.stack)
        self.expanded_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
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
        self.header.restore_standard_window_clicked.connect(self.restore_standard_window_requested.emit)
        self.header.director_clicked.connect(self.director_requested.emit)
        self.header.settings_clicked.connect(self.open_settings)
        self.header.audio_preview_toggled.connect(self._on_hover_audio_changed)
        self.header.pending_chip_clicked.connect(self.router.apply_pending)
        self.header.profile_lock_selected.connect(self.router.lock_profile)
        exp_layout.addWidget(self.header)

        # 2. Horizontal Category Navigation
        self.category_nav = CategoryNavWidget(self.expanded_container)
        self.category_nav.category_changed.connect(self._on_category_changed)
        self.category_nav.sub_category_changed.connect(self._on_sub_category_changed)
        exp_layout.addWidget(self.category_nav)

        # 3. Special Context Blocks Stack (Edit, Cut, Fusion, Color, Fairlight, Deliver, Media)
        self.special_container = QStackedWidget(self.expanded_container)
        self.special_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.block_edit = EditSuggestBlock(self.special_container)
        self.block_edit.asset_suggested.connect(self._on_suggested_asset)

        self.block_cut = CutPadsBlock(self.special_container)
        self.block_cut.pad_triggered.connect(self._on_pad_triggered)

        self.block_fusion = FusionCaptureBlock(self.special_container)
        self.block_fusion.capture_requested.connect(self._on_fusion_captured)

        self.block_color = ColorSpecialBlock(self.special_container)

        self.block_fairlight = FairlightBlock(self.special_container)
        self.block_fairlight.pad_triggered.connect(self._on_pad_triggered)

        self.block_deliver = DeliverBlock(self.special_container)
        self.block_deliver.start_render_requested.connect(self._on_start_render)

        self.block_media = MediaBlock(self.special_container)
        self.block_media.open_director_requested.connect(self.director_requested.emit)

        self._special_blocks_map = {
            "edit": self.block_edit,
            "cut": self.block_cut,
            "fusion": self.block_fusion,
            "color": self.block_color,
            "fairlight": self.block_fairlight,
            "deliver": self.block_deliver,
            "media": self.block_media,
        }
        for block in self._special_blocks_map.values():
            self.special_container.addWidget(block)

        exp_layout.addWidget(self.special_container)

        # 4. Virtualized Asset Grid View
        self.asset_model = AssetListModel(parent=self.expanded_container)
        self.proxy_model = AssetFilterProxyModel(parent=self.expanded_container)
        self.proxy_model.setSourceModel(self.asset_model)

        self.grid = AssetGridView(self.expanded_container)
        self.grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid.setModel(self.proxy_model)
        self.grid.asset_selected.connect(self._on_asset_clicked)
        self.grid.asset_activated.connect(self._on_asset_double_clicked)
        exp_layout.addWidget(self.grid, stretch=1)

        # 5. Sticky Action Bar 56px
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

        # 6. Status Bar 22px
        self.status_bar = DockStatusBar(self.expanded_container)
        exp_layout.addWidget(self.status_bar)

        self.stack.addWidget(self.expanded_container)
        self.stack.setCurrentIndex(0)

        # 7. Sheet Inspector Overlay
        self.sheet = SheetInspector(self)
        self.sheet.closed.connect(self._on_sheet_closed)
        self.sheet.insert_requested.connect(self._on_sheet_insert_requested)

    # -------------------------------------------------------------------------
    # Public API & Data Binding
    # -------------------------------------------------------------------------
    def set_assets(self, assets: List[Any]):
        """Sets library assets in the list model and palette."""
        self.asset_model.set_assets(assets)
        self.palette.set_assets(assets)

    def set_search_index(self, indexer):
        """Sets SearchIndex for ranked search in proxy model and palette."""
        self.proxy_model.set_search_index(indexer)
        self.palette.set_search_index(indexer)

    def set_anchor_rect(self, rect: Optional[Tuple[int, int, int, int]]):
        self._anchor_rect = rect
        if rect:
            self.fsm.set_resolve_width(rect[2])
        self.palette.set_anchor_rect(rect)
        self._update_dock_position(target_w=self.width(), animated=False)

    def open_palette(self, command_mode: bool = False):
        """Opens Quick Command Palette HUD."""
        self.palette.open_palette(command_mode=command_mode)

    def _on_palette_customize_requested(self, asset: Dict[str, Any]):
        """Triggered when Tab is pressed in Palette: opens Dock Sheet Inspector."""
        self.open_dock(animated=True)
        self._selected_asset = asset
        self.fsm.set_sheet_open(True)
        self.sheet.open_sheet(asset)

    def open_dock(self, animated: bool = True):
        self.fsm.open_dock(animated=animated)

    def collapse_dock(self, animated: bool = True):
        self.fsm.collapse_dock(animated=animated)

    def toggle(self, animated: bool = True):
        self.fsm.toggle_dock(animated=animated)

    def open_director(self):
        """Opens ChunDVC Director window."""
        if not hasattr(self, "_director_window") or self._director_window is None:
            from src.ui.director import DirectorWindow
            self._director_window = DirectorWindow()
        self._director_window.show()
        self._director_window.raise_()
        self._director_window.activateWindow()

    def _on_palette_command_executed(self, cmd_id: str):
        if cmd_id in ("cmd_director", "cmd_autocut"):
            self.open_director()
        elif cmd_id == "cmd_pin_dock":
            self.header.btn_pin.click()
        elif cmd_id == "cmd_settings":
            self.open_settings()

    def set_dock_edge(self, edge: str):
        """
        Đặt vị trí neo của thanh Dock ('left' hoặc 'right').
        Tự động cập nhật layout, hướng mũi tên thu gọn và lưu vào QSettings.
        """
        if edge not in ("left", "right"):
            return
        self.dock_edge = edge

        # Lưu cấu hình vào QSettings
        settings = QSettings("ChunDVC", "ResolveFlow")
        settings.setValue("dock_edge", self.dock_edge)

        # Cập nhật thứ tự frame_layout (đường accent line quay về phía timeline DaVinci)
        if hasattr(self, "frame_layout") and hasattr(self, "accent_line") and hasattr(self, "stack"):
            self.frame_layout.removeWidget(self.accent_line)
            self.frame_layout.removeWidget(self.stack)
            if self.dock_edge == "left":
                self.frame_layout.addWidget(self.stack, stretch=1)
                self.frame_layout.addWidget(self.accent_line)
            else:
                self.frame_layout.addWidget(self.accent_line)
                self.frame_layout.addWidget(self.stack, stretch=1)

        if hasattr(self, "header") and self.header:
            self.header.set_dock_edge(self.dock_edge)

        self._update_dock_position(target_w=self.width(), animated=False)

    def open_settings(self):
        """Mở hộp thoại Cài đặt thanh Dock."""
        from .dock_settings_dialog import DockSettingsDialog
        dlg = DockSettingsDialog(current_edge=self.dock_edge, parent=self)
        dlg.dock_edge_changed.connect(self.set_dock_edge)
        dlg.hover_audio_changed.connect(self._on_hover_audio_changed)
        dlg.hover_volume_changed.connect(self.grid.hover_controller.set_volume)
        if hasattr(dlg, "dock_screen_changed"):
            dlg.dock_screen_changed.connect(self.set_dock_screen_index)
        dlg.exec()

    def set_dock_screen_index(self, index: int):
        from src.services.multi_screen_service import get_multi_screen_service
        svc = get_multi_screen_service()
        svc.set_preferred_dock_screen(index)
        self._update_dock_position(animated=False)

    def _on_hover_audio_changed(self, enabled: bool):
        self.grid.hover_controller.set_audio_preview_enabled(enabled)
        self.header.set_audio_preview_enabled(enabled)

    # -------------------------------------------------------------------------
    # Geometry & Boundary Clamping
    # -------------------------------------------------------------------------
    def _get_screen_rect(self) -> Tuple[int, int, int, int]:
        app = QApplication.instance()
        target_screen = None
        if app:
            # 1. Nếu có tọa độ Resolve window anchor, lấy màn hình chứa Resolve
            if self._anchor_rect:
                ax, ay, aw, ah = self._anchor_rect
                center_point = QPoint(ax + max(10, aw // 2), ay + max(10, ah // 2))
                target_screen = app.screenAt(center_point)

            # 2. Nếu không có anchor, dùng màn hình được người dùng chỉ định (mặc định Màn 1)
            # Tuyệt đối KHÔNG dùng app.screenAt(self.geometry().center()) vì thanh dock dán sát
            # mép biên giới giữa 2 màn hình, khi mở rộng kích thước sẽ khiến tâm cửa sổ tràn
            # sang màn hình bên cạnh và gây lỗi tự động nhảy màn hình khi lướt chuột!
            if not target_screen:
                from src.services.multi_screen_service import get_multi_screen_service
                svc = get_multi_screen_service()
                pref_idx = svc.get_preferred_dock_screen()
                target_screen = svc.get_screen_by_index(pref_idx)

            # 3. Fallback sang màn hình chính
            if not target_screen:
                target_screen = QApplication.primaryScreen()

        if target_screen:
            geom = target_screen.availableGeometry()
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

            if getattr(self, "dock_edge", "right") == "left":
                # Neo vào cạnh trái của DaVinci Resolve
                x = ax
            else:
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
            if getattr(self, "dock_edge", "right") == "left":
                # Neo vào cạnh trái màn hình
                x = sx
            else:
                # Neo vào cạnh phải màn hình
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
        if is_open:
            self.expanded_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self.rail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        else:
            self.expanded_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            self.rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.updateGeometry()

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
        self.router.is_in_dock = True
        if not self.fsm.is_open:
            self.fsm.on_mouse_enter_rail()
        else:
            self.fsm.on_mouse_enter_dock()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.fsm.on_mouse_leave_dock()
        self.router.is_in_dock = False
        self.router.on_user_left_dock()

    def _on_search_changed(self, text: str):
        self.fsm.on_search_text_changed(text)
        self.proxy_model.set_search_query(text)
        self.router.has_search_text = bool(text.strip())

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
        self.router.has_search_text = False
        self.fsm.collapse_dock()
        focus_davinci_resolve()

    def _on_toggle_wide(self):
        self.fsm.toggle_wide()

    def _on_toggle_pin(self):
        self.fsm.toggle_pin()

    # -------------------------------------------------------------------------
    # Context Profile Slots
    # -------------------------------------------------------------------------
    def _on_profile_changed(self, profile: ContextProfile):
        # 1. Save state of previous profile if applicable
        if self._current_profile and self._current_profile.categories:
            cat = self.category_nav.current_category
            sub = self.category_nav.current_subcategory
            scroll_y = self.grid.verticalScrollBar().value()
            self.router.save_page_state(cat, sub, scroll_y)
        self._current_profile = profile

        # 2. Update Accent Line color
        self.accent_line.setStyleSheet(f"background: {profile.color};")

        # 3. Update Header Page Chip
        self.header.set_page_label(profile.label, locked=self.router.is_locked, color=profile.color)

        # 4. Update Action Bar Primary Button Label
        self.action_bar.btn_primary.setText(f"{profile.primary_action}  ⏎")

        # 5. Show/Hide Category Nav and Grid vs Special Container
        exp_layout = self.expanded_container.layout()
        if profile.categories:
            self.category_nav.show()
            self.category_nav.set_categories(profile.categories)
            self.grid.show()
            if exp_layout:
                exp_layout.setStretchFactor(self.special_container, 0)
                exp_layout.setStretchFactor(self.grid, 1)
        else:
            self.category_nav.hide()
            self.grid.hide()
            if exp_layout:
                exp_layout.setStretchFactor(self.special_container, 1)
                exp_layout.setStretchFactor(self.grid, 0)

        # 6. Switch Special Block
        block = self._special_blocks_map.get(profile.page)
        if block:
            self.special_container.setCurrentWidget(block)
            self.special_container.show()
        else:
            self.special_container.hide()

        # 7. Restore saved state from memory
        saved = self.router.get_saved_page_state(profile.page)
        if saved and profile.categories:
            cat = saved.get("category")
            if cat and cat in profile.categories:
                self.category_nav.select_category(cat)
            elif profile.categories:
                self.category_nav.select_category(profile.categories[0])
            sub = saved.get("subcategory")
            if sub:
                self.category_nav.select_sub_category(sub)
            scroll_y = saved.get("scroll_y", 0)
            QTimer.singleShot(15, lambda: self.grid.verticalScrollBar().setValue(scroll_y))
        elif profile.categories:
            self.category_nav.select_category(profile.categories[0])

    def _on_pending_changed(self, pending_profile: Optional[ContextProfile]):
        self.header.set_pending_page(pending_profile)

    def _on_profile_locked_changed(self, locked: bool, page_id: str):
        profile = self.router.active_profile
        self.header.set_page_label(profile.label, locked=locked, color=profile.color)

    def _on_search_boost_requested(self, categories: List[str]):
        logger.debug("Context profile categories for search boost: %s", categories)

    def _on_suggested_asset(self, asset_id: str):
        logger.info("Suggested asset selected: %s", asset_id)
        # Find asset in proxy model and insert
        for i in range(self.proxy_model.rowCount()):
            idx = self.proxy_model.index(i, 0)
            asset = idx.data(Qt.ItemDataRole.UserRole)
            if asset and (asset.get("id") == asset_id or asset.get("name") == asset_id):
                self._selected_asset = asset
                self._do_insert(asset, {})
                break

    def _on_pad_triggered(self, pad_id: str):
        logger.info("Sound pad triggered: %s", pad_id)
        pad_asset = {"id": pad_id, "name": pad_id, "category": "sfx", "type": "sfx"}
        self._do_insert(pad_asset, {})

    def _on_start_render(self, preset_id: str):
        logger.info("Deliver render triggered for preset: %s", preset_id)

    def _on_fusion_captured(self):
        logger.info("Fusion node captured into preset")

    def _on_sheet_closed(self):
        self.fsm.set_sheet_open(False)
        self.router.is_sheet_open = False

    # -------------------------------------------------------------------------
    # Library Interaction Slots
    # -------------------------------------------------------------------------
    def _on_rail_category_selected(self, cat: str):
        self.category_nav.select_category(cat)

    def _on_category_changed(self, cat: str):
        self.proxy_model.set_category(cat)
        if self._current_profile:
            sub = self.category_nav.current_subcategory
            scroll_y = self.grid.verticalScrollBar().value()
            self.router.save_page_state(cat, sub, scroll_y)

    def _on_sub_category_changed(self, sub: str):
        self.proxy_model.set_sub_category(sub)
        if self._current_profile:
            cat = self.category_nav.current_category
            scroll_y = self.grid.verticalScrollBar().value()
            self.router.save_page_state(cat, sub, scroll_y)

    def _on_asset_clicked(self, asset: Dict[str, Any]):
        self._selected_asset = asset
        timecode = self.status_bar.lbl_timecode.text()
        self.action_bar.set_asset(asset, timecode=timecode)

        # CRITICAL: Always return focus to Resolve immediately on card click
        focus_davinci_resolve()

    def _on_asset_double_clicked(self, asset: Dict[str, Any]):
        self._selected_asset = asset
        if self.router.active_profile.page == "fusion":
            self._on_paste_fusion()
        else:
            self._do_insert(asset, {})
        focus_davinci_resolve()

    def _on_primary_action(self):
        curr_page = self.router.active_profile.page
        if curr_page == "fusion":
            self._on_paste_fusion()
        elif curr_page == "deliver":
            self.block_deliver._on_render_clicked()
        elif curr_page == "media":
            self.director_requested.emit()
        elif self._selected_asset:
            self._do_insert(self._selected_asset, {})
        focus_davinci_resolve()

    def _on_tune_action(self):
        if not self._selected_asset:
            return
        if self.sheet.is_open:
            self.sheet.close_sheet()
            self.fsm.set_sheet_open(False)
            self.router.is_sheet_open = False
        else:
            self.fsm.set_sheet_open(True)
            self.router.is_sheet_open = True
            self.sheet.open_sheet(self._selected_asset)

    def _on_sheet_insert_requested(self, asset: Dict[str, Any], params: Dict[str, Any]):
        self._do_insert(asset, params)
        self.fsm.set_sheet_open(False)
        self.router.is_sheet_open = False
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
        self.palette.set_resolve_context(page, playhead_tc)
        if connected:
            self.router.on_resolve_page_changed(page)
        else:
            self.status_bar.set_connected(False)
            self.header.set_page_label("Mất kết nối", color="#71717a")
            self.action_bar.btn_primary.setText("Kết nối lại  ⏎")

    # -------------------------------------------------------------------------
    # Keyboard Handling (When Dock is open and search input is not active)
    # -------------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        # Cut and Fairlight sound pad trigger keys: 1-9, 0, -, =
        if (
            self.router.active_profile.page in ("cut", "fairlight")
            and not self.header.txt_search.hasFocus()
        ):
            text = event.text()
            if text in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]:
                triggered = False
                if self.router.active_profile.page == "cut":
                    triggered = self.block_cut.trigger_by_key(text)
                else:
                    triggered = self.block_fairlight.trigger_by_key(text)
                if triggered:
                    event.accept()
                    return

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
                self.router.is_sheet_open = False
            else:
                self.fsm.collapse_dock()
            focus_davinci_resolve()
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "window_anchor") and self.window_anchor:
            try:
                self.window_anchor.stop()
            except Exception:
                pass
        if hasattr(self, "hotkey_service") and self.hotkey_service:
            try:
                self.hotkey_service.stop()
            except Exception:
                pass
        super().closeEvent(event)
