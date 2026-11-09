"""
src/ui/lux/library/asset_grid.py
High-performance virtualized QListView with dynamic column calculation and full Akitt Pro drag & drop.

Supports:
- Responsive columns: cols = max(2, floor((w + 8) / (min_card_w + 8)))
- Single click = select (updates Inspector)
- Double click = InsertService.insert (auto safe track)
- Right-click = Context menu (Favorite, Fusion Paste, Open Folder)
- Drag = System QMimeData for dropping into DaVinci Resolve
"""
from __future__ import annotations

import os
import math
import uuid
import tempfile
import logging
from collections.abc import Mapping
from typing import Optional, Any, Callable

from PySide6.QtCore import Qt, QSize, QPoint, Signal, Slot, QMimeData, QUrl
from PySide6.QtWidgets import (
    QListView, QMenu, QApplication, QWidget
)
from PySide6.QtGui import QDrag, QPixmap, QPainter, QCursor, QDesktopServices, QColor

from src.ui.lux.library.asset_model import (
    IdRole, NameRole, CategoryRole, AssetObjRole, FilePathRole, IsFavRole
)
from src.ui.lux.library.asset_delegate import AssetCardDelegate
from src.ui.lux.library.hover_preview import SharedHoverPreview

logger = logging.getLogger("ChunDVC.AssetGrid")


def _normalize_asset_payload(target: Any) -> dict:
    """
    Chuẩn hóa shallow payload từ Mapping, domain object, hoặc StudioAsset mà không làm mất provenance.
    Bảo toàn runtime refs và native_preset thực. Không deepcopy runtime objects.
    """
    if isinstance(target, Mapping):
        d = dict(target)
    elif hasattr(target, "model_dump") and callable(target.model_dump):
        d = dict(target.model_dump())
        d["_raw_obj"] = target
    elif hasattr(target, "dict") and callable(target.dict):
        d = dict(target.dict())
        d["_raw_obj"] = target
    elif hasattr(target, "__dict__"):
        d = dict(target.__dict__)
        d["_raw_obj"] = target
    else:
        d = {"id": str(target), "name": str(target), "_raw_obj": target}

    # Trích xuất các trường provenance và runtime refs nếu có trên target object
    fields_to_sync = (
        "id", "name", "category", "tab", "template_type", "file_path", "path",
        "source_kind", "storage_managed", "library_path", "external_path",
        "relative_path", "origin_library_id", "origin_root", "availability",
        "native_preset", "_raw_obj"
    )
    for field in fields_to_sync:
        if field not in d or d[field] is None or d[field] == "":
            val = getattr(target, field, None)
            if val is not None and val != "":
                d[field] = val

    if "_raw_obj" not in d or d["_raw_obj"] is None:
        if hasattr(target, "native_preset") and getattr(target, "native_preset") is not None:
            d["_raw_obj"] = getattr(target, "native_preset")
        else:
            d["_raw_obj"] = target

    return d


def create_asset_mime_data(target: Any, sample_text: str = "", custom_preset: Optional[Any] = None) -> QMimeData:
    """
    Creates standard QMimeData from an asset for drag & drop into DaVinci Resolve.
    Thẩm định toàn diện qua ActionGuard(intent='drag') trước khi xuất MIME URL.
    Hỗ trợ tham số tùy chọn custom_preset (TextStylePreset) cho Inspector drag (G04-04C7-I).
    """
    mime_data = QMimeData()
    if target is None:
        return mime_data

    from src.core.action_guard import check_action_availability
    payload = _normalize_asset_payload(target)
    guard_res = check_action_availability(payload, intent="drag")

    if not guard_res.allowed:
        return mime_data

    # 1. File-backed media: Đường dẫn vật lý đã được giải mã và kiểm tra trên đĩa
    if guard_res.resolved_path:
        abs_p = os.path.abspath(guard_res.resolved_path)
        mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
        mime_data.setText(abs_p)
        return mime_data

    # 2. Procedural Text Preset: Chỉ xuất khi TextStylePreset thực được xác nhận
    if guard_res.result_code == "OK_PROCEDURAL_DRAG_SETTING":
        from src.core.text_preset import TextStylePreset, FusionSettingGenerator

        preset: Optional[TextStylePreset] = None
        if isinstance(target, TextStylePreset):
            preset = target
        elif isinstance(getattr(target, "native_preset", None), TextStylePreset):
            preset = target.native_preset
        elif isinstance(payload.get("_raw_obj"), TextStylePreset):
            preset = payload["_raw_obj"]
        elif isinstance(payload.get("native_preset"), TextStylePreset):
            preset = payload["native_preset"]

        if preset is None:
            return mime_data

        # Chỉ sử dụng custom_preset khi nó là instance TextStylePreset hợp lệ
        preset_to_export = preset
        if custom_preset is not None and isinstance(custom_preset, TextStylePreset):
            preset_to_export = custom_preset

        st = sample_text or getattr(preset_to_export, "name", "") or getattr(preset, "name", "") or str(payload.get("name") or "ChunDVC Title")

        temp_setting = ""
        try:
            # Dùng tempfile.NamedTemporaryFile để cấp phát file độc quyền, an toàn tuyệt đối
            # Tuyệt đối không ghép asset_id hay bất kỳ untrusted string nào vào đường dẫn file
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix="ChunDVC_Drag_",
                suffix=".setting",
                delete=False,
                encoding="utf-8",
            ) as f:
                temp_setting = f.name

            FusionSettingGenerator.export_setting_file(preset_to_export, temp_setting, sample_text=st)
            if os.path.isfile(temp_setting) and os.path.getsize(temp_setting) > 0:
                abs_p = os.path.abspath(temp_setting)
                mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
                mime_data.setText(abs_p)
                return mime_data
            else:
                if temp_setting and os.path.exists(temp_setting):
                    try:
                        os.remove(temp_setting)
                    except OSError:
                        pass
                return QMimeData()
        except Exception as e:
            logger.warning("Error exporting procedural text mime setting: %s", e)
            if temp_setting and os.path.exists(temp_setting):
                try:
                    os.remove(temp_setting)
                except OSError:
                    pass
            return QMimeData()

    return mime_data


class AssetGridView(QListView):
    """
    Virtual ListView displaying asset cards with 60fps virtualization.
    """
    asset_selected = Signal(object)       # Emits asset info/object on click
    asset_activated = Signal(object)      # Emits on double-click or Enter
    drag_started = Signal()
    drag_ended = Signal()
    favorite_toggled = Signal((str, bool), (str,))  # Emits (asset_id, is_fav) or asset_id
    move_category_requested = Signal(list)          # Emits list of selected assets to move
    fusion_paste_requested = Signal(object) # Emits asset for Fusion paste
    open_folder_requested = Signal(str)   # Emits directory path

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._delegate = AssetCardDelegate(self)
        self.setItemDelegate(self._delegate)

        # Performance flags
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(40)
        self.setSpacing(8)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMouseTracking(True)

        self.setStyleSheet("""
            QListView {
                background: transparent;
                border: none;
                outline: none;
            }
            QListView::item {
                border: none;
                background: transparent;
            }
        """)

        # Hover Preview Controller
        self._hover_controller = SharedHoverPreview(self)
        if self.model() is not None:
            self._hover_controller.bind_model(self.model())

        # Drag state
        self._drag_start_pos: Optional[QPoint] = None
        self._sample_text_getter: Optional[Callable[[], str]] = None
        self._insert_service: Optional[Any] = None

        # Signals
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    def setModel(self, model: Optional[Any]):
        super().setModel(model)
        if hasattr(self, "_hover_controller") and self._hover_controller is not None:
            self._hover_controller.bind_model(model)

    def hideEvent(self, event):
        if hasattr(self, "_hover_controller") and self._hover_controller is not None:
            self._hover_controller.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "_hover_controller") and self._hover_controller is not None:
            self._hover_controller.stop()
        super().closeEvent(event)

    def set_insert_service(self, service: Any):
        self._insert_service = service

    @property
    def hover_controller(self) -> SharedHoverPreview:
        return self._hover_controller

    def set_sample_text_getter(self, getter: Callable[[], str]):
        self._sample_text_getter = getter

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalculate_grid_geometry()

    def _recalculate_grid_geometry(self):
        w = self.viewport().width()
        if w <= 0:
            return

        min_card_w = 140 if w < 360 else 180
        gap = 8
        cols = max(2, math.floor((w + gap) / (min_card_w + gap)))
        card_w = math.floor((w - ((cols - 1) * gap)) / cols)
        preview_h = round(card_w * 9 / 16)
        card_h = preview_h + 28

        self.setGridSize(QSize(card_w, card_h))

    def _get_asset_at_index(self, index: Any) -> Optional[Any]:
        if not index.isValid():
            return None
        return index.data(AssetObjRole)

    def _on_clicked(self, index: Any):
        asset = self._get_asset_at_index(index)
        if asset:
            self.asset_selected.emit(asset)

    def _on_double_clicked(self, index: Any):
        asset = self._get_asset_at_index(index)
        if not asset:
            return

        self.asset_activated.emit(asset)

        # If insert service is attached, execute insertion
        if self._insert_service and hasattr(self._insert_service, "insert"):
            try:
                self._insert_service.insert(asset, mode="auto")
            except Exception as e:
                logger.error("InsertService error on double click: %s", e)

    def contextMenuEvent(self, event):
        pos = event.pos()
        index = self.indexAt(pos)
        if not index.isValid():
            return

        # Handle multi-selection: if clicked index not in selected, make it the sole selection
        sel_model = self.selectionModel()
        selected_indexes = sel_model.selectedIndexes() if sel_model else []
        if index not in selected_indexes:
            if sel_model:
                from PySide6.QtCore import QItemSelectionModel
                sel_model.select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            selected_indexes = [index]

        selected_assets = [self._get_asset_at_index(idx) for idx in selected_indexes if self._get_asset_at_index(idx) is not None]
        num_sel = len(selected_assets)

        asset = self._get_asset_at_index(index)
        asset_id = str(index.data(IdRole) or "")
        is_fav = bool(index.data(IsFavRole))
        file_path = str(index.data(FilePathRole) or "")

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 4px;
                color: #f4f4f5;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #27272a;
                color: #a78bfa;
            }
        """)

        # Favorite action
        fav_label = "☆ Bỏ yêu thích" if is_fav else "★ Thêm vào Yêu thích"
        act_fav = menu.addAction(fav_label)

        # Move Category action (supports moving one or multiple selected assets)
        move_label = f"📁 Chuyển danh mục ({num_sel} đạo cụ)..." if num_sel > 1 else "📁 Chuyển danh mục..."
        act_move_cat = menu.addAction(move_label)

        # Fusion paste action
        act_fusion = menu.addAction("📋 Dán vào Fusion (Ctrl+V)")

        # Open containing folder
        act_open_dir = None
        if file_path and os.path.exists(file_path):
            act_open_dir = menu.addAction("📂 Mở thư mục chứa")

        action = menu.exec(self.mapToGlobal(pos))
        if action == act_fav:
            self.favorite_toggled.emit(asset_id, not is_fav)
        elif action == act_move_cat:
            self.move_category_requested.emit(selected_assets)
        elif action == act_fusion:
            self.fusion_paste_requested.emit(asset)
        elif act_open_dir and action == act_open_dir:
            folder = os.path.dirname(file_path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            self.open_folder_requested.emit(folder)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self._drag_start_pos:
            super().mouseMoveEvent(event)
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (pos - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        start_pos = self._drag_start_pos
        self._drag_start_pos = None

        index = self.indexAt(start_pos)
        if not index.isValid():
            super().mouseMoveEvent(event)
            return

        asset = self._get_asset_at_index(index)
        if not asset:
            super().mouseMoveEvent(event)
            return

        # Execute Drag
        sample_txt = self._sample_text_getter() if callable(self._sample_text_getter) else ""
        mime_data = create_asset_mime_data(asset, sample_text=sample_txt)

        # Kiểm tra MIME có local file URLs hợp lệ trước khi tạo QDrag/ghost/emitting drag_started
        if not mime_data or not mime_data.hasUrls():
            return
        local_urls = [u for u in mime_data.urls() if u.isLocalFile()]
        if not local_urls:
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)

        # Generate subtle ghost drag pixmap
        ghost = QPixmap(180, 101)
        ghost.fill(Qt.GlobalColor.transparent)
        p = QPainter(ghost)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(0.85)
        p.setBrush(QColor("#8b5cf6"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 180, 101, 8, 8)
        p.setPen(QColor("#ffffff"))
        name = str(index.data(NameRole) or "Asset")
        p.drawText(ghost.rect(), Qt.AlignmentFlag.AlignCenter, name)
        p.end()

        drag.setPixmap(ghost)
        drag.setHotSpot(QPoint(90, 50))
        self.drag_started.emit()
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.drag_ended.emit()
