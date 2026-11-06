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
import os
from typing import Optional, Tuple, Dict, Any, List

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QStackedWidget, QApplication,
    QSizePolicy, QLabel, QPushButton
)
from PySide6.QtCore import (
    Qt, QRect, QPoint, QSize, Signal as pyqtSignal, QVariantAnimation,
    QEasingCurve, QTimer, QSettings
)
from PySide6.QtGui import QKeyEvent, QScreen, QKeySequence, QShortcut

from src.ui.lux.lux_effects import get_luxury_dark_stylesheet
from src.ui.lux.window_anchor import (
    calculate_dock_geometry, get_resolve_window_rect, ResolveWindowAnchor
)
from src.ui.lux.library.asset_model import AssetListModel
from src.ui.lux.library.asset_filter import AssetFilterProxyModel
from src.ui.lux.library.asset_grid import AssetGridView
from src.services.insert_service import InsertService, to_asset_dict
from src.services.resolve_state import ResolveStateService
from src.core.fast_paste import focus_davinci_resolve

from .dock_state import DockState, DockStateMachine
from .dock_rail import DockRailWidget
from .dock_header import DockHeaderWidget
from .dock_status import DockStatusBar
from ..library.category_nav import CategoryNavWidget, DEFAULT_CATEGORIES
from src.core.contracts.asset_dto import (
    CANONICAL_STORAGE_CATEGORIES,
    UI_CATEGORIES,
    RESERVED_CATEGORY_IDS,
    is_valid_custom_category_id,
)
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
from src.core.branding import DOCK_WINDOW_TITLE

logger = logging.getLogger("ChunDVC.EdgeDock")


class AdaptiveStackedWidget(QStackedWidget):
    """
    QStackedWidget có sizeHint và minimumSizeHint thích ứng động theo
    widget con hiện đang hiển thị (currentWidget), thay vì lấy max của toàn bộ các widget.
    Tránh tình trạng các trang nhỏ (Edit, Cut, Fusion, Color) bị phình to bởi trang lớn (Deliver 233px).
    """
    def sizeHint(self) -> QSize:
        curr = self.currentWidget()
        if curr:
            return curr.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        curr = self.currentWidget()
        if curr:
            return curr.minimumSizeHint()
        return super().minimumSizeHint()


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
    storage_assets_changed = pyqtSignal(list, dict)

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
        self.setWindowTitle(DOCK_WINDOW_TITLE)

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
        self._all_assets: List[Any] = []
        self.search_index = None
        self._active_ingest_wizard = None
        self._ingest_in_progress = False

        # Global Hotkey Service
        self.hotkey_service = GlobalHotkeyService(self)
        self.hotkey_service.palette_requested.connect(lambda: self.open_palette(command_mode=False))
        self.hotkey_service.command_mode_requested.connect(lambda: self.open_palette(command_mode=True))
        self.hotkey_service.dock_toggle_requested.connect(self.toggle)

        # In-app fallback shortcut when window is focused
        self._dock_shortcut = QShortcut(QKeySequence("Ctrl+Alt+D"), self)
        self._dock_shortcut.activated.connect(self.toggle)

        # Non-blocking geometry animation
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_anim_geometry)
        self._anim.finished.connect(self._on_anim_finished)

        # Read saved preferences
        settings = QSettings("ChunDVC", "ResolveFlow")
        self.dock_edge = str(settings.value("dock_edge", "right"))
        if self.dock_edge not in ("left", "right"):
            self.dock_edge = "right"
        self._auto_collapse_on_leave = bool(settings.value("auto_collapse_on_leave", False, type=bool))

        # Floating Bubble quả cầu nổi 56px thay thế cho thanh strip khi thu gọn dock
        from src.ui.bubble.floating_bubble import FloatingBubbleWidget
        self.bubble = FloatingBubbleWidget(parent=None)
        self.bubble.open_dock_requested.connect(lambda: self.open_dock(animated=True))
        self.bubble.open_auto_requested.connect(self.open_director)
        self.bubble.open_studio_requested.connect(self.restore_standard_window_requested.emit)

        self._init_ui()
        self.setAcceptDrops(True)
        self.setStyleSheet(get_luxury_dark_stylesheet())
        self.header.set_dock_edge(self.dock_edge)
        if hasattr(self, "rail") and self.rail:
            self.rail.set_dock_edge(self.dock_edge)

        # Initialize context profile appearance
        self._on_profile_changed(self.router.active_profile)

        # Connect Resolve state signals if provided
        if self._resolve_state:
            self._resolve_state.state_changed.connect(self._on_resolve_state_changed)
            self._resolve_state.state_changed.connect(
                lambda s: self.bubble.set_davinci_connected(bool(s.get("connected")))
            )

        # Resolve Window Anchor (Dynamic tracking)
        self.window_anchor = ResolveWindowAnchor(poll_interval_ms=1200, parent=self)
        self.window_anchor.anchor_updated.connect(self.set_anchor_rect)
        self.window_anchor.start()

        # Initial geometry: Khởi động thẳng ở trạng thái OPEN (420px to đầy đủ, không hiển thị thanh strip hẹp)
        initial_w = self.fsm.get_width_for_state(DockState.OPEN)
        self.resize(initial_w, 720)
        self._update_dock_position(target_w=initial_w, animated=False)
        self.fsm.open_dock(animated=False)

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
        self.setMinimumWidth(0)
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

        # 2px Accent Line along inner edge (NLE Studio Coral indicator)
        self.accent_line = QFrame(self.frame)
        self.accent_line.setFixedWidth(2)
        self.accent_line.setStyleSheet(
            "background-color: #d94f3d;"
        )

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
        self.rail.instant_autocut_clicked.connect(self.run_instant_autocut)
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
        self.header.instant_autocut_clicked.connect(self.run_instant_autocut)
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
        self.special_container = AdaptiveStackedWidget(self.expanded_container)
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
        self.proxy_model.set_category("text")

        self.grid = AssetGridView(self.expanded_container)
        self.grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grid.setModel(self.proxy_model)
        self.grid.asset_selected.connect(self._on_asset_clicked)
        self.grid.asset_activated.connect(self._on_asset_double_clicked)
        self.grid.drag_started.connect(lambda: self.fsm.set_dragging(True))
        self.grid.drag_ended.connect(lambda: self.fsm.set_dragging(False))
        exp_layout.addWidget(self.grid, stretch=1)

        # Empty State Widget when filtering yields 0 items
        self.empty_state_widget = QFrame(self.expanded_container)
        self.empty_state_widget.setStyleSheet("background: transparent;")
        empty_layout = QVBoxLayout(self.empty_state_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(8)

        lbl_empty_icon = QLabel("🔍")
        lbl_empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_icon.setStyleSheet("font-size: 28px; background: transparent;")
        empty_layout.addWidget(lbl_empty_icon)

        lbl_empty_msg = QLabel("Không tìm thấy tài nguyên phù hợp")
        lbl_empty_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_msg.setStyleSheet("color: #a1a1aa; font-size: 12px; font-weight: bold; background: transparent;")
        empty_layout.addWidget(lbl_empty_msg)

        lbl_empty_sub = QLabel("Thử đổi danh mục hoặc xóa bộ lọc tìm kiếm")
        lbl_empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_sub.setStyleSheet("color: #71717a; font-size: 11px; background: transparent;")
        empty_layout.addWidget(lbl_empty_sub)

        btn_clear_filters = QPushButton("✕ Bỏ bộ lọc")
        btn_clear_filters.setCursor(Qt.PointingHandCursor)
        btn_clear_filters.setFixedHeight(26)
        btn_clear_filters.setStyleSheet("""
            QPushButton {
                background: #27272a;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                color: #e4e4e7;
                font-size: 11px;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #3f3f46;
                color: #ffffff;
                border-color: #d94f3d;
            }
        """)
        btn_clear_filters.clicked.connect(self.clear_all_filters)
        empty_layout.addWidget(btn_clear_filters, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_state_widget.hide()
        exp_layout.addWidget(self.empty_state_widget, stretch=1)

        self.proxy_model.layoutChanged.connect(self._on_model_filter_changed)
        self.proxy_model.modelReset.connect(self._on_model_filter_changed)
        self.proxy_model.rowsInserted.connect(self._on_model_filter_changed)
        self.proxy_model.rowsRemoved.connect(self._on_model_filter_changed)

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
    def _build_effective_categories(self, profile: Optional[Any] = None) -> List[Dict[str, Any]]:
        """
        Hợp nhất category definitions dùng chung cho set_assets và _on_profile_changed.
        - Giữ nguyên thứ tự các category IDs theo profile có thư viện (Edit, Cut, Fusion, Color, Fairlight).
        - Profile không có thư viện (Media, Deliver) trả về danh sách rỗng.
        - Giữ B-roll khi dữ liệu source có thật.
        - Nối các custom categories hợp lệ từ StorageManager.taxonomy() ở cuối (kể cả khi chưa có asset).
        - Giữ nhãn tiếng Việt nguyên bản, loại trùng theo ID.
        - Dùng validators/alias contracts: không biến canonical storage categories (templates, overlays...) thành chip trùng.
        - Tổng hợp subs và tags từ assets (storage_managed và custom) mà không làm mất dữ liệu.
        - Đọc taxonomy đúng một lần cho mỗi lần rebuild nav; khi offline tự động dùng snapshot.
        """
        if profile is None:
            profile = getattr(self, "_current_profile", None) or getattr(getattr(self, "router", None), "active_profile", None)

        # Profile categories rỗng (như Media, Deliver) -> không có thư viện
        if profile is not None and getattr(profile, "categories", None) == []:
            return []

        default_cat_map = {
            c["id"]: {"id": c["id"], "label": c["label"], "subs": list(c.get("subs", ["Tất cả"]))}
            for c in DEFAULT_CATEGORIES
        }

        cats: List[Dict[str, Any]] = []
        existing_ids: set[str] = set()

        # 1. Base category list từ profile.categories
        profile_cat_ids = getattr(profile, "categories", None) if profile else None
        if profile_cat_ids:
            for cid in profile_cat_ids:
                if cid not in existing_ids:
                    if cid in default_cat_map:
                        cats.append({
                            "id": cid,
                            "label": default_cat_map[cid]["label"],
                            "subs": list(default_cat_map[cid]["subs"]),
                        })
                    else:
                        cats.append({"id": cid, "label": cid.capitalize(), "subs": ["Tất cả"]})
                    existing_ids.add(cid)
        else:
            cats = [
                {"id": c["id"], "label": c["label"], "subs": list(c.get("subs", ["Tất cả"]))}
                for c in DEFAULT_CATEGORIES
            ]
            existing_ids = {c["id"] for c in cats}

        # 2. Kiểm tra nhu cầu "Cảnh" (B-roll): chỉ thêm chip B-roll khi source category có thật
        has_broll = False
        all_assets = getattr(self, "_all_assets", None) or []
        for a in all_assets:
            if isinstance(a, dict):
                raw_cat = str(a.get("category") or a.get("tab") or "").lower()
            else:
                raw_cat = str(getattr(a, "category", "") or getattr(a, "tab", "")).lower()
            if raw_cat in ("broll", "b-roll", "canh", "cảnh"):
                has_broll = True
                break

        if has_broll and "broll" not in existing_ids:
            cats.append({"id": "broll", "label": "Cảnh (B-Roll)", "subs": ["Tất cả"]})
            existing_ids.add("broll")

        # 3. Đọc taxonomy một lần duy nhất từ StorageManager (online hoặc snapshot offline)
        taxonomy: Dict[str, Any] = {}
        try:
            from src.core.storage_manager import StorageManager
            sm_taxonomy = StorageManager().taxonomy()
            if isinstance(sm_taxonomy, dict):
                taxonomy = sm_taxonomy
        except Exception as exc:
            logger.debug("Không thể đọc taxonomy từ StorageManager: %s", exc)
            taxonomy = {}

        # Nối các custom category hợp lệ từ taxonomy ở cuối (kể cả chưa có asset nào trong kho)
        for cid, clabel in taxonomy.items():
            cid_str = str(cid).strip().lower()
            if cid_str in existing_ids:
                continue
            if (
                cid_str in CANONICAL_STORAGE_CATEGORIES
                or cid_str in RESERVED_CATEGORY_IDS
                or cid_str in UI_CATEGORIES
            ):
                continue
            if not is_valid_custom_category_id(cid_str):
                continue
            label_str = str(clabel).strip() if clabel else cid_str
            cats.append({"id": cid_str, "label": label_str, "subs": ["Tất cả"]})
            existing_ids.add(cid_str)

        # 4. Quét assets để giữ các tab legacy/custom tương thích chưa có trong taxonomy
        for asset in all_assets:
            read = asset.get if isinstance(asset, dict) else lambda key, default=None: getattr(asset, key, default)
            tab = str(read("tab", "") or read("category", "") or "").strip().lower()
            if tab and tab not in existing_ids:
                if (
                    tab not in CANONICAL_STORAGE_CATEGORIES
                    and tab not in RESERVED_CATEGORY_IDS
                    and tab not in UI_CATEGORIES
                    and is_valid_custom_category_id(tab)
                ):
                    label_str = taxonomy.get(tab, tab)
                    cats.append({"id": tab, "label": str(label_str), "subs": ["Tất cả"]})
                    existing_ids.add(tab)

        # 5. Tổng hợp subs và tags managed/custom mà không làm mất dữ liệu
        for definition in cats:
            known = set(definition["subs"])
            for asset in all_assets:
                read = asset.get if isinstance(asset, dict) else lambda key, default=None: getattr(asset, key, default)
                asset_tab = str(read("tab", "") or read("category", "") or "").strip().lower()
                if asset_tab == definition["id"]:
                    sub_val = read("sub", "") or read("subcategory", "")
                    if sub_val and isinstance(sub_val, str) and sub_val not in known and sub_val not in ("all", "Tất cả"):
                        definition["subs"].append(sub_val)
                        known.add(sub_val)
                    tags_val = read("tags", [])
                    if isinstance(tags_val, (list, tuple, set)):
                        for tag in tags_val:
                            if tag and isinstance(tag, str) and tag not in known and tag not in ("all", "Tất cả"):
                                definition["subs"].append(tag)
                                known.add(tag)

        return cats

    def set_assets(self, assets: List[Any]):
        """Sets library assets in the list model and palette."""
        self._all_assets = list(assets)
        self.asset_model.set_assets(assets)
        self.palette.set_assets(assets)
        if getattr(self, "search_index", None) is not None:
            self.search_index.set_assets(assets)

        if hasattr(self, "category_nav"):
            profile = getattr(self, "_current_profile", None) or getattr(getattr(self, "router", None), "active_profile", None)
            if profile and getattr(profile, "categories", None) == []:
                # Profile không có thư viện -> giữ nav/grid ẩn
                return
            cats = self._build_effective_categories(profile)
            self.category_nav.set_categories(cats)

            # Đồng bộ category và subcategory với proxy_model
            effective_ids = [c["id"] for c in cats]
            cur_cat = self.category_nav.current_category
            proxy_cat = self.proxy_model.filter_category() if hasattr(self, "proxy_model") and self.proxy_model else None

            if proxy_cat == "all":
                # Chế độ tìm kiếm / lọc toàn cục "all": bảo toàn proxy category="all" và query
                # Không ép về nav text hoặc xóa query của proxy_model
                if cur_cat in effective_ids:
                    cat_def = next((c for c in cats if c["id"] == cur_cat), None)
                    valid_subs = set(cat_def.get("subs", ["Tất cả"])) if cat_def else {"Tất cả"}
                    cur_sub = self.category_nav.current_subcategory
                    if cur_sub and cur_sub in valid_subs:
                        self.category_nav.select_subcategory(cur_sub)
                    else:
                        self.category_nav.select_subcategory("Tất cả")
                elif effective_ids:
                    self.category_nav.select_category(effective_ids[0])
                    self.category_nav.select_subcategory("Tất cả")

                if hasattr(self, "proxy_model") and self.proxy_model:
                    cur_proxy_sub = self.proxy_model.filter_subcategory()
                    if cur_proxy_sub and cur_proxy_sub not in ("all", "Tất cả"):
                        all_valid_subs = {s for c in cats for s in c.get("subs", [])}
                        if not (cur_proxy_sub in all_valid_subs or cur_proxy_sub.startswith("pack:")):
                            self.proxy_model.set_sub_category("all")
                    if self.proxy_model._search_query:
                        self.proxy_model._apply_search()
            elif cur_cat in effective_ids:
                if hasattr(self, "proxy_model") and self.proxy_model:
                    if self.proxy_model.filter_category() != cur_cat:
                        self.proxy_model.set_category(cur_cat)

                # Kiểm tra subcategory hiện tại còn hợp lệ trong category này không
                cat_def = next((c for c in cats if c["id"] == cur_cat), None)
                valid_subs = set(cat_def.get("subs", ["Tất cả"])) if cat_def else {"Tất cả"}
                cur_sub = self.category_nav.current_subcategory
                if cur_sub and cur_sub in valid_subs:
                    if cur_sub not in ("all", "Tất cả"):
                        self.category_nav.select_subcategory(cur_sub)
                        if hasattr(self, "proxy_model") and self.proxy_model:
                            if self.proxy_model.filter_subcategory() != cur_sub:
                                self.proxy_model.set_sub_category(cur_sub)
                    else:
                        self.category_nav.select_subcategory("Tất cả")
                        if hasattr(self, "proxy_model") and self.proxy_model:
                            if self.proxy_model.filter_subcategory() not in ("all", "Tất cả"):
                                self.proxy_model.set_sub_category("all")
                else:
                    # Sub không còn tồn tại -> reset nav và proxy về conventions Tất cả/all
                    self.category_nav.select_subcategory("Tất cả")
                    if hasattr(self, "proxy_model") and self.proxy_model:
                        self.proxy_model.set_sub_category("all")
                    # Cập nhật router memory để profile roundtrip không khôi phục sub cũ đã mất
                    if getattr(self, "_current_profile", None) and hasattr(getattr(self, "router", None), "_memory"):
                        scroll_y = self.grid.verticalScrollBar().value() if hasattr(self, "grid") else 0
                        self.router._memory.save(self._current_profile.page, cur_cat, "Tất cả", scroll_y)
                if hasattr(self, "proxy_model") and self.proxy_model and self.proxy_model._search_query:
                    self.proxy_model._apply_search()
            elif effective_ids:
                fallback_cat = effective_ids[0]
                self.category_nav.select_category(fallback_cat)
                if hasattr(self, "proxy_model") and self.proxy_model:
                    self.proxy_model.set_category(fallback_cat)
                self.category_nav.select_subcategory("Tất cả")
                if hasattr(self, "proxy_model") and self.proxy_model:
                    self.proxy_model.set_sub_category("all")
                if getattr(self, "_current_profile", None) and hasattr(getattr(self, "router", None), "_memory"):
                    scroll_y = self.grid.verticalScrollBar().value() if hasattr(self, "grid") else 0
                    self.router._memory.save(self._current_profile.page, fallback_cat, "Tất cả", scroll_y)
                if hasattr(self, "proxy_model") and self.proxy_model and self.proxy_model._search_query:
                    self.proxy_model._apply_search()

    def _on_model_filter_changed(self):
        """Cập nhật empty state và đồng bộ deselect tài nguyên khi bị lọc mất."""
        count = self.proxy_model.rowCount()
        is_empty = (count == 0)
        if hasattr(self, "empty_state_widget"):
            self.empty_state_widget.setVisible(is_empty)
        if hasattr(self, "grid"):
            self.grid.setVisible(not is_empty)

        # Kiểm tra nếu selected_asset bị lọc mất thì deselect khỏi Inspector và Action Bar
        if self._selected_asset:
            sel_dict = to_asset_dict(self._selected_asset)
            sel_id = sel_dict.get("id")
            from src.ui.lux.library.asset_model import IdRole
            still_visible = any(self.proxy_model.index(r, 0).data(IdRole) == sel_id for r in range(count))
            if not still_visible:
                self._selected_asset = None
                if hasattr(self, "action_bar"):
                    self.action_bar.set_asset(None)
                if hasattr(self, "sheet") and self.sheet.is_open:
                    self.sheet.close_sheet()

    def clear_all_filters(self):
        """Xóa toàn bộ bộ lọc đang áp dụng và khôi phục hiển thị đầy đủ."""
        if hasattr(self, "header") and self.header:
            self.header.clear_search()
        if hasattr(self, "category_nav") and self.category_nav:
            self.category_nav.select_category("all")
            self.category_nav.select_subcategory("all")
        if hasattr(self, "proxy_model") and self.proxy_model:
            self.proxy_model.set_category("all")
            self.proxy_model.set_sub_category("all")
            self.proxy_model.set_tag(None)
            self.proxy_model.set_pack_filter(None)
            self.proxy_model.set_filter_mode("all")
            self.proxy_model.set_search_query("")

    def set_search_index(self, indexer):
        """Sets SearchIndex for ranked search in proxy model and palette."""
        self.search_index = indexer
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
        if hasattr(self, "bubble") and self.bubble:
            self.bubble.hide()
        self.show()
        self.raise_()
        self.fsm.open_dock(animated=animated)
        # Khôi phục trạng thái trước đó (category, search, scroll, selected asset)
        if getattr(self, "_saved_category", None) and hasattr(self, "category_nav") and self.category_nav:
            self.category_nav.select_category(self._saved_category)
        if getattr(self, "_saved_subcategory", None) and hasattr(self, "category_nav") and self.category_nav:
            self.category_nav.select_subcategory(self._saved_subcategory)
        if hasattr(self, "_saved_search") and self._saved_search is not None and hasattr(self, "header") and self.header:
            self.header.txt_search.setText(self._saved_search)
            self.proxy_model.set_search_query(self._saved_search)
        if getattr(self, "_saved_selected_asset", None):
            # Chỉ khôi phục asset nếu asset còn tồn tại và hiển thị hợp lệ trên proxy model
            saved = to_asset_dict(self._saved_selected_asset) if self._saved_selected_asset else None
            sel_id = saved.get("id") if saved else None
            src_idx = self.asset_model.index_of_asset_id(sel_id) if (sel_id and hasattr(self, "asset_model")) else None
            proxy_idx = self.proxy_model.mapFromSource(src_idx) if (src_idx and src_idx.isValid()) else None
            if proxy_idx and proxy_idx.isValid() and proxy_idx.row() >= 0:
                # Lấy dữ liệu mới nhất từ model rồi chuẩn hóa sang dict chuẩn (giữ contract dict cho UI & _raw_obj cho InsertService)
                fresh_raw = proxy_idx.data(Qt.ItemDataRole.UserRole)
                fresh_dict = to_asset_dict(fresh_raw) if fresh_raw is not None else None
                self._selected_asset = fresh_dict
                if hasattr(self, "action_bar") and self.action_bar:
                    self.action_bar.set_asset(self._selected_asset)
                if hasattr(self, "grid") and self.grid:
                    self.grid.setCurrentIndex(proxy_idx)
            else:
                self._selected_asset = None
                if hasattr(self, "action_bar") and self.action_bar:
                    self.action_bar.set_asset(None)
                if hasattr(self, "grid") and self.grid:
                    self.grid.clearSelection()
        else:
            self._selected_asset = None
            if hasattr(self, "action_bar") and self.action_bar:
                self.action_bar.set_asset(None)
            if hasattr(self, "grid") and self.grid:
                self.grid.clearSelection()
        if hasattr(self, "_saved_scroll") and self._saved_scroll is not None and hasattr(self, "grid") and self.grid:
            val = self._saved_scroll
            QTimer.singleShot(60, lambda: self.grid.verticalScrollBar().setValue(val))

    def collapse_dock(self, to_strip: bool = True, animated: bool = True):
        # Lưu trạng thái trước khi thu gọn nếu chưa bị thu gọn
        if self.fsm.is_open:
            if hasattr(self, "category_nav") and self.category_nav:
                self._saved_category = self.category_nav.current_category
                self._saved_subcategory = self.category_nav.current_subcategory
            if hasattr(self, "header") and self.header:
                self._saved_search = self.header.search_text
            if hasattr(self, "grid") and self.grid:
                self._saved_scroll = self.grid.verticalScrollBar().value()
            self._saved_selected_asset = to_asset_dict(self._selected_asset) if self._selected_asset else None

        # Dừng animation và ẩn hoàn toàn cửa sổ Dock (xóa bỏ hoàn toàn thanh strip dọc 28px)
        self._anim.stop()
        self.hide()
        self.fsm.collapse_dock(to_strip=to_strip, animated=False)

        # Hiển thị Quả cầu nổi (Bong bóng tròn 56px) tại mép màn hình
        self._show_bubble_at_edge()

    def _show_bubble_at_edge(self):
        """Hiển thị quả cầu nổi 56px tại mép màn hình khi EdgeDock thu nhỏ."""
        if not hasattr(self, "bubble") or not self.bubble:
            return

        sx, sy, sw, sh = self._get_screen_rect()
        b_w = self.bubble.width()
        b_h = self.bubble.height()

        if getattr(self, "dock_edge", "right") == "left":
            bx = sx + 8
        else:
            bx = sx + sw - b_w - 8

        settings = QSettings("ChunDVC", "ResolveFlow")
        saved_y = settings.value("bubble_y", None)
        if saved_y is not None:
            try:
                by = int(saved_y)
                by = max(sy + 16, min(sy + sh - b_h - 16, by))
            except (ValueError, TypeError):
                by = sy + max(16, (sh - b_h) // 2)
        else:
            by = sy + max(16, (sh - b_h) // 2)

        self.bubble.move(bx, by)
        self.bubble.show()
        self.bubble.raise_()

    def toggle(self, animated: bool = True):
        if self.fsm.is_open:
            self.collapse_dock(to_strip=True, animated=animated)
        else:
            self.open_dock(animated=animated)

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
        elif cmd_id == "cmd_instant_autocut":
            self.run_instant_autocut()
        elif cmd_id == "cmd_pin_dock":
            self.header.btn_pin.click()
        elif cmd_id == "cmd_settings":
            self.open_settings()

    def _get_toast(self):
        if not hasattr(self, "_toast") or self._toast is None:
            from src.ui.lux.feedback.toast import ChunDVCToast
            self._toast = ChunDVCToast(self)
        if self._anchor_rect:
            self._toast.set_anchor_rect(self._anchor_rect)
        return self._toast

    def run_instant_autocut(self):
        """
        Kích hoạt 1-Click Instant AutoCut: Nhận diện clip trên Timeline đang mở,
        phân tích khoảng lặng và co sát các đoạn thoại lại với nhau trên DaVinci Resolve.
        """
        if hasattr(self, "_autocut_worker") and self._autocut_worker and self._autocut_worker.isRunning():
            toast = self._get_toast()
            toast.show_message("⚡ Đang trong tiến trình AutoCut, vui lòng đợi...", icon="⏳", level="warning")
            return

        from src.services.autocut_worker import AutoCutWorker
        from src.core.autocut import AudioCutConfig
        from src.core.resolve_api import ResolveAutomation

        automation = None
        if self._insert_service and hasattr(self._insert_service, "_gateway") and self._insert_service._gateway:
            worker = getattr(self._insert_service._gateway, "_worker", None)
            if worker and hasattr(worker, "get_automation"):
                automation = worker.get_automation()
        if not automation:
            automation = ResolveAutomation()

        toast = self._get_toast()
        toast.show_message("⚡ Đang đọc Timeline & phân tích khoảng lặng...", icon="⚡", level="info", duration_ms=6000)
        if hasattr(self, "status_bar") and self.status_bar:
            self.status_bar.set_timeline("⚡ Đang AutoCut...")

        self._autocut_worker = AutoCutWorker(
            resolve_automation=automation,
            config=AudioCutConfig(
                silence_threshold_db=-38.0,
                min_silent_duration=0.6,
                padding_seconds=0.08,
            ),
            parent=self,
        )
        self._autocut_worker.progress_changed.connect(self._on_autocut_progress)
        self._autocut_worker.finished_result.connect(self._on_autocut_finished)
        self._autocut_worker.start()

    def _on_autocut_progress(self, pct: int, msg: str):
        if hasattr(self, "status_bar") and self.status_bar:
            self.status_bar.set_timeline(f"⚡ AutoCut: {msg} ({pct}%)")

    def _on_autocut_finished(self, result):
        toast = self._get_toast()
        if result.ok:
            msg = f"🎉 {result.message}"
            toast.show_message(msg, icon="🎉", level="success", duration_ms=5000)
            if hasattr(self, "status_bar") and self.status_bar:
                self.status_bar.set_timeline(result.new_timeline_name)
        else:
            toast.show_message(f"AutoCut: {result.message}", icon="⚠", level="warning", duration_ms=5000)
            if hasattr(self, "status_bar") and self.status_bar:
                self.status_bar.set_timeline("AutoCut dừng lại")

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
        if hasattr(self, "rail") and self.rail:
            self.rail.set_dock_edge(self.dock_edge)

        if hasattr(self, "bubble") and self.bubble and self.bubble.isVisible():
            self._show_bubble_at_edge()

        self._update_dock_position(target_w=self.width(), animated=False)

    def open_settings(self):
        """Mở hộp thoại Cài đặt thanh Dock."""
        from .dock_settings_dialog import DockSettingsDialog
        dlg = DockSettingsDialog(current_edge=self.dock_edge, parent=self)
        dlg.dock_edge_changed.connect(self.set_dock_edge)
        dlg.auto_collapse_changed.connect(self.set_auto_collapse_on_leave)
        dlg.hover_audio_changed.connect(self._on_hover_audio_changed)
        dlg.hover_volume_changed.connect(self.grid.hover_controller.set_volume)
        if hasattr(dlg, "dock_screen_changed"):
            dlg.dock_screen_changed.connect(self.set_dock_screen_index)
        if hasattr(dlg, "library_changed"):
            dlg.library_changed.connect(self.refresh_storage_assets)
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
        return QRect(*calculate_dock_geometry(
            self._anchor_rect, self._get_screen_rect(), target_w,
            edge=getattr(self, "dock_edge", "right"),
        ))

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
            self.rail.set_strip_mode(False)
            self.accent_line.show()
        else:
            self.expanded_container.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            self.rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            if new_state == DockState.STRIP:
                self.rail.set_strip_mode(True)
                self.accent_line.hide()
                self.frame.setStyleSheet("#lux_drawer_panel { border: none; border-radius: 4px; }")
            else:
                self.rail.set_strip_mode(False)
                self.accent_line.show()
                self.frame.setStyleSheet("")
        self.updateGeometry()

        self.stack.setCurrentIndex(1 if is_open else 0)
        self.header.set_pinned(self.fsm.is_pinned)
        self.header.set_wide(self.fsm.is_wide)

        self.expanded_changed.emit(is_open)

    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                if getattr(self, "_auto_collapse_on_leave", False) and self.fsm.is_open and not self.fsm.is_pinned:
                    if (
                        self.fsm.can_auto_collapse()
                        and not QApplication.activeModalWidget()
                        and not QApplication.activePopupWidget()
                        and (not hasattr(self, "sheet") or not self.sheet.is_open)
                    ):
                        self.collapse_dock(to_strip=True, animated=True)

    def set_auto_collapse_on_leave(self, enabled: bool):
        self._auto_collapse_on_leave = enabled
        settings = QSettings("ChunDVC", "ResolveFlow")
        settings.setValue("auto_collapse_on_leave", enabled)

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
        self.collapse_dock(to_strip=True)
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
        if self._current_profile and self._current_profile.categories and hasattr(self, "category_nav") and self.category_nav:
            cat = self.category_nav.current_category
            sub = self.category_nav.current_subcategory
            scroll_y = self.grid.verticalScrollBar().value()
            if hasattr(self.router, "_memory"):
                self.router._memory.save(self._current_profile.page, cat, sub, scroll_y)
            else:
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
            effective_cats = self._build_effective_categories(profile)
            effective_ids = [c["id"] for c in effective_cats]
            self.category_nav.show()
            self.category_nav.set_categories(effective_cats)
            self.grid.show()
            self.special_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            if exp_layout:
                exp_layout.setStretchFactor(self.special_container, 0)
                exp_layout.setStretchFactor(self.grid, 1)
        else:
            effective_cats = []
            effective_ids = []
            self.category_nav.hide()
            self.grid.hide()
            self.special_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
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
        self.special_container.updateGeometry()

        # 7. Restore saved state from memory using effective IDs
        if profile.categories and effective_ids:
            saved = self.router.get_saved_page_state(profile.page)
            candidate_cat = saved.get("category") if saved else None
            candidate_sub = saved.get("subcategory") if saved else None
            scroll_y = saved.get("scroll_y", 0) if saved else 0

            if candidate_cat and candidate_cat in effective_ids:
                target_cat = candidate_cat
                cat_def = next((c for c in effective_cats if c["id"] == target_cat), None)
                valid_subs = set(cat_def.get("subs", ["Tất cả"])) if cat_def else {"Tất cả"}
                target_sub = candidate_sub if (candidate_sub and candidate_sub in valid_subs) else "Tất cả"
            else:
                # Category không còn tồn tại trong effective IDs -> fallback có chủ ý và reset sub/filter
                target_cat = effective_ids[0]
                target_sub = "Tất cả"
                if hasattr(self, "proxy_model") and self.proxy_model:
                    if self.proxy_model.active_tag():
                        self.proxy_model.set_tag(None)
                    if getattr(self.proxy_model, "_pack_filter", None):
                        self.proxy_model.set_pack_filter(None)
                    if getattr(self.proxy_model, "_filter_mode", "all") != "all":
                        self.proxy_model.set_filter_mode("all")

            # Đồng bộ tuyệt đối giữa category_nav và proxy_model
            self.category_nav.select_category(target_cat)
            if hasattr(self, "proxy_model") and self.proxy_model:
                if self.proxy_model.filter_category() != target_cat:
                    self.proxy_model.set_category(target_cat)

            if target_sub and target_sub not in ("all", "Tất cả"):
                self.category_nav.select_subcategory(target_sub)
                if hasattr(self, "proxy_model") and self.proxy_model:
                    self.proxy_model.set_sub_category(target_sub)
            else:
                self.category_nav.select_subcategory("Tất cả")
                if hasattr(self, "proxy_model") and self.proxy_model:
                    self.proxy_model.set_sub_category("all")

            if saved and scroll_y > 0:
                QTimer.singleShot(15, lambda: self.grid.verticalScrollBar().setValue(scroll_y))

    def _on_pending_changed(self, pending_profile: Optional[ContextProfile]):
        self.header.set_pending_page(pending_profile)

    def _on_profile_locked_changed(self, locked: bool, page_id: str):
        profile = self.router.active_profile
        self.header.set_page_label(profile.label, locked=locked, color=profile.color)

    def _on_search_boost_requested(self, categories: List[str]):
        logger.debug("Context profile categories for search boost: %s", categories)

    def _on_suggested_asset(self, asset_id: str):
        logger.info("Suggested asset selected: %s", asset_id)
        # 1. Locate asset in source model
        source_idx = self.asset_model.index_of_asset_id(asset_id)
        if not source_idx.isValid():
            logger.warning("Suggested asset not found in source model: %s", asset_id)
            self._selected_asset = None
            if hasattr(self, "action_bar") and self.action_bar:
                self.action_bar.set_asset(None)
            if hasattr(self, "sheet") and self.sheet and self.sheet.is_open:
                self.sheet.close_sheet(animated=False)
            toast = self._get_toast()
            if toast:
                toast.show_message(f"Tài nguyên '{asset_id}' không còn tồn tại trong kho", icon="⚠", level="warning")
            return

        from src.ui.lux.library.asset_model import AssetObjRole
        raw_asset = source_idx.data(AssetObjRole)
        ad = to_asset_dict(raw_asset)
        if not ad and 0 <= source_idx.row() < len(self.asset_model._assets):
            ad = self.asset_model._assets[source_idx.row()]

        if not ad:
            return

        # 2. Switch category and clear conflicting filters
        cat = ad.get("category", "") or ad.get("tab", "")
        if cat and hasattr(self, "category_nav") and self.category_nav:
            self.category_nav.select_category(cat)

        if hasattr(self, "header") and self.header:
            q = self.header.search_text.strip().lower()
            if q and q not in (ad.get("name", "")).lower() and q not in asset_id.lower():
                self.header.clear_search()

        if hasattr(self, "category_nav") and self.category_nav:
            sub = ad.get("sub", "") or ad.get("subcategory", "")
            if self.category_nav.current_subcategory not in ("all", "Tất cả") and sub != self.category_nav.current_subcategory:
                self.category_nav.select_subcategory("all")

        if hasattr(self, "proxy_model") and self.proxy_model:
            if self.proxy_model.active_tag():
                self.proxy_model.set_tag(None)
            if getattr(self.proxy_model, "_pack_filter", None):
                self.proxy_model.set_pack_filter(None)
            if self.proxy_model._filter_mode != "all":
                self.proxy_model.set_filter_mode("all")

        # 3. Map to proxy index, select and scroll to in grid ONLY if valid
        proxy_idx = self.proxy_model.mapFromSource(source_idx)
        if proxy_idx.isValid() and proxy_idx.row() >= 0:
            self._selected_asset = ad
            if hasattr(self, "grid") and self.grid:
                self.grid.setCurrentIndex(proxy_idx)
                self.grid.scrollTo(proxy_idx)

            # 4. Update Inspector & Action Bar (passive UI: do NOT auto-insert and do NOT auto-play audio)
            if hasattr(self, "sheet") and hasattr(self.sheet, "open_sheet"):
                self.sheet.open_sheet(ad)
            if hasattr(self, "action_bar") and self.action_bar:
                self.action_bar.set_asset(ad)
        else:
            # Target is still hidden / not visible in proxy model: deselect safely
            self._selected_asset = None
            if hasattr(self, "action_bar") and self.action_bar:
                self.action_bar.set_asset(None)
            if hasattr(self, "sheet") and self.sheet and self.sheet.is_open:
                self.sheet.close_sheet(animated=False)

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
            if hasattr(self.router, "_memory"):
                self.router._memory.save(self._current_profile.page, cat, sub, scroll_y)
            else:
                self.router.save_page_state(cat, sub, scroll_y)

    def _on_sub_category_changed(self, sub: str):
        self.proxy_model.set_sub_category(sub)
        if self._current_profile:
            cat = self.category_nav.current_category
            scroll_y = self.grid.verticalScrollBar().value()
            if hasattr(self.router, "_memory"):
                self.router._memory.save(self._current_profile.page, cat, sub, scroll_y)
            else:
                self.router.save_page_state(cat, sub, scroll_y)

    def _on_asset_clicked(self, asset: Any):
        asset_dict = to_asset_dict(asset)
        self._selected_asset = asset_dict
        timecode = self.status_bar.lbl_timecode.text()
        self.action_bar.set_asset(asset_dict, timecode=timecode)
        cat = str(asset_dict.get("category") or asset_dict.get("tab") or "").lower()
        if cat in ("trans", "transitions", "transition"):
            name = asset_dict.get("name", "Chuyển cảnh")
            self.action_bar.lbl_info.setText(f"{name} · Kéo từ Effects > Video Transitions > ResolveFlow vào điểm cắt")

    def _on_asset_double_clicked(self, asset: Any):
        asset_dict = to_asset_dict(asset)
        self._selected_asset = asset_dict
        if self.router.active_profile.page == "fusion":
            self._on_paste_fusion()
        else:
            self._do_insert(asset_dict, {})
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
            asset_dict = to_asset_dict(self._selected_asset)
            self._do_insert(asset_dict, {})
        focus_davinci_resolve()

    def _on_tune_action(self):
        if not self._selected_asset:
            return
        asset_dict = to_asset_dict(self._selected_asset)
        self._selected_asset = asset_dict
        if self.sheet.is_open:
            self.sheet.close_sheet()
            self.fsm.set_sheet_open(False)
            self.router.is_sheet_open = False
        else:
            self.fsm.set_sheet_open(True)
            self.router.is_sheet_open = True
            self.sheet.open_sheet(asset_dict)

    def _on_sheet_insert_requested(self, asset: Any, params: Dict[str, Any]):
        asset_dict = to_asset_dict(asset)
        self._do_insert(asset_dict, params)
        self.fsm.set_sheet_open(False)
        self.router.is_sheet_open = False
        focus_davinci_resolve()

    def _on_paste_fusion(self):
        if self._selected_asset and self._insert_service:
            asset_dict = to_asset_dict(self._selected_asset)
            res = self._insert_service.insert(asset_dict, mode="fusion_paste")
            self.asset_inserted.emit(asset_dict, {"mode": "fusion_paste", "result": res})
        focus_davinci_resolve()

    def _on_copy_fusion_code(self):
        if self._selected_asset:
            asset_dict = to_asset_dict(self._selected_asset)
            code = asset_dict.get("content", "") or asset_dict.get("path", "")
            app = QApplication.instance()
            if app:
                app.clipboard().setText(str(code))
        focus_davinci_resolve()

    def _on_install_fusion(self):
        logger.info("Install to Fusion templates requested for: %s", self._selected_asset)
        focus_davinci_resolve()

    def _on_insert_new_track(self):
        if self._selected_asset:
            asset_dict = to_asset_dict(self._selected_asset)
            self._do_insert(asset_dict, {"track_mode": "new"})
        focus_davinci_resolve()

    def _on_toggle_fav(self):
        if self._selected_asset:
            asset_dict = to_asset_dict(self._selected_asset)
            is_fav = not asset_dict.get("is_fav", False)
            asset_dict["is_fav"] = is_fav
            self._selected_asset = asset_dict
            self.action_bar.set_asset(asset_dict, is_fav=is_fav)

    def _do_insert(self, asset: Any, params: Dict[str, Any]):
        if not asset:
            return
        asset_dict = to_asset_dict(asset)
        name = asset_dict.get("name") or asset_dict.get("id") or "Tài nguyên"
        cat = str(asset_dict.get("category") or asset_dict.get("tab") or "").lower()
        logger.info("Inserting asset '%s' with params %s", name, params)
        if self._insert_service:
            track_mode = str(params.get("track_mode") or "").strip()
            preferred_track = params.get("preferred_track")

            if track_mode and track_mode.lower() not in ("auto", "new", "timeline_track"):
                import re
                digits = re.findall(r"\d+", track_mode)
                if digits:
                    try:
                        preferred_track = int(digits[0])
                    except (ValueError, TypeError):
                        pass

            if track_mode == "new":
                mode = "new_track"
            elif preferred_track is not None:
                mode = "timeline_track"
            else:
                mode = "auto"

            res = self._insert_service.insert(
                asset_dict,
                mode=mode,
                preferred_track=preferred_track,
                extra_params=params,
            )
            if res and res.ok:
                if cat in ("trans", "transitions", "transition") and res.track == "Transitions":
                    self.action_bar.lbl_info.setText(res.message)
                    self.action_bar.lbl_info.setToolTip(res.message)
                else:
                    self.action_bar.lbl_info.setText(f"✔ Đã chèn “{name}” lên {res.track or 'Timeline'}")
            elif res:
                self.action_bar.lbl_info.setText(f"❌ Chèn thất bại: {res.error or 'Lỗi không xác định'}")
            self.asset_inserted.emit(asset_dict, {"params": params, "result": res})
        else:
            self.asset_inserted.emit(asset_dict, params)

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
                self.collapse_dock(to_strip=True)
            focus_davinci_resolve()
            event.accept()
            return

        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        self.fsm.set_dragging(True)
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                fn = url.toLocalFile().lower()
                if fn.endswith(".rfpack") or fn.endswith(".zip"):
                    event.acceptProposedAction()
                    return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.fsm.set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.fsm.set_dragging(False)
        try:
            urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
            pack_files = [u.toLocalFile() for u in urls if u.toLocalFile().lower().endswith((".rfpack", ".zip"))]
            if pack_files:
                event.acceptProposedAction()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._handle_dropped_packs(pack_files))
                return
        finally:
            self.fsm.set_dragging(False)
        super().dropEvent(event)

    def _handle_dropped_packs(self, pack_files: List[str]):
        if getattr(self, "_ingest_in_progress", False):
            return
        self._ingest_in_progress = True
        try:
            from PySide6.QtWidgets import QDialog
            from src.ui.widgets.pack_ingest_wizard import PackIngestWizard
            from src.ui.lux.feedback.toast import ChunDVCToast

            committed_packs = []
            committed_payloads = []
            error_info = None
            canceled = False

            for fn in pack_files:
                if not os.path.exists(fn):
                    continue
                try:
                    dialog = PackIngestWizard(parent=self, source=fn, auto_prepare=True)
                    self._active_ingest_wizard = dialog
                    current_payload = []

                    def _on_committed(meta):
                        current_payload.append(meta if isinstance(meta, dict) else {})

                    dialog.pack_committed.connect(_on_committed)
                    res = dialog.exec()
                    self._active_ingest_wizard = None

                    is_committed = (bool(current_payload) or dialog.is_pack_committed()) and (
                        res == QDialog.DialogCode.Accepted or res == 1
                    )
                    if is_committed:
                        committed_packs.append(fn)
                        meta_dict = current_payload[0] if current_payload else {}
                        committed_payloads.append(meta_dict)
                    else:
                        canceled = True
                        break
                except Exception as exc:
                    self._active_ingest_wizard = None
                    pack_name = os.path.basename(fn)
                    error_info = (pack_name, str(exc))
                    break

            if committed_packs:
                self.refresh_storage_assets()
                if not hasattr(self, "_toast") or self._toast is None:
                    self._toast = ChunDVCToast(self)
                unsynced = sum(1 for m in committed_payloads if m.get("snapshot_synced") is False)
                unsynced_note = f" (⚠️ {unsynced} gói chưa đồng bộ bộ đệm offline)" if unsynced > 0 else ""

                if error_info:
                    err_pack, err_msg = error_info
                    self._toast.show_message(
                        message=f"Đã nhập {len(committed_packs)}/{len(pack_files)} gói{unsynced_note} (gói '{err_pack}' lỗi: {err_msg}; đã dừng hàng đợi).",
                        icon="ℹ",
                        level="info",
                    )
                elif canceled or len(committed_packs) < len(pack_files):
                    self._toast.show_message(
                        message=f"Đã nhập {len(committed_packs)}/{len(pack_files)} gói{unsynced_note} (đã dừng hàng đợi).",
                        icon="ℹ",
                        level="info",
                    )
                else:
                    self._toast.show_message(
                        message=f"Đã nhập {len(committed_packs)} gói vào kho!{unsynced_note}",
                        icon="✓",
                        level="success",
                    )
            elif error_info:
                if not hasattr(self, "_toast") or self._toast is None:
                    self._toast = ChunDVCToast(self)
                err_pack, err_msg = error_info
                self._toast.show_error(f"Gói '{err_pack}' gặp lỗi: {err_msg}")
        except Exception as e:
            if not hasattr(self, "_toast") or self._toast is None:
                self._toast = ChunDVCToast(self)
            self._toast.show_error(f"Lỗi nhập gói: {e}")
        finally:
            self._ingest_in_progress = False
            self._active_ingest_wizard = None

    def refresh_storage_assets(self):
        from src.core.storage_manager import StorageManager
        sm = StorageManager()
        def managed(asset):
            return asset.get("storage_managed", False) if isinstance(asset, dict) else getattr(asset, "storage_managed", False)
        managed_records = sm.ui_assets()
        taxonomy_dict = sm.taxonomy()
        assets = [a for a in getattr(self, "_all_assets", []) if not managed(a)] + managed_records
        if getattr(self, "search_index", None) is not None:
            self.search_index.set_assets(assets)
        self.set_assets(assets)
        if getattr(self, "search_index", None) is not None:
            self.proxy_model.set_search_index(self.search_index)
            self.palette.set_search_index(self.search_index)
        self.storage_assets_changed.emit(managed_records, taxonomy_dict)

    def closeEvent(self, event):
        self.fsm.set_dragging(False)
        if hasattr(self, "_active_ingest_wizard") and self._active_ingest_wizard is not None:
            try:
                self._active_ingest_wizard.reject()
            except Exception:
                pass
        if hasattr(self, "bubble") and self.bubble:
            try:
                self.bubble.close()
            except Exception:
                pass
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
