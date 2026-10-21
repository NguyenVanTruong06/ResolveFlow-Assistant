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
from typing import Optional, Any, Callable

from PySide6.QtCore import Qt, QSize, QPoint, Signal, Slot, QMimeData, QUrl
from PySide6.QtWidgets import (
    QListView, QMenu, QApplication, QWidget
)
from PySide6.QtGui import QDrag, QPixmap, QPainter, QCursor, QDesktopServices

from src.ui.lux.library.asset_model import (
    IdRole, NameRole, CategoryRole, AssetObjRole, FilePathRole, IsFavRole
)
from src.ui.lux.library.asset_delegate import AssetCardDelegate
from src.ui.lux.library.hover_preview import SharedHoverPreview

logger = logging.getLogger("ResolveFlow.AssetGrid")


def create_asset_mime_data(target: Any, sample_text: str = "") -> QMimeData:
    """
    Creates standard QMimeData from an asset for drag & drop into DaVinci Resolve.
    Matches the exact logic of ChunDVC TabAssets._start_drag.
    """
    mime_data = QMimeData()
    temp_dir = tempfile.gettempdir()

    file_path = getattr(target, "file_path", "") if not isinstance(target, dict) else target.get("file_path", "")
    tab_type = getattr(target, "tab", "") if not isinstance(target, dict) else target.get("category", "")
    asset_id = getattr(target, "id", "") if not isinstance(target, dict) else target.get("id", "asset")
    name = getattr(target, "name", "") if not isinstance(target, dict) else target.get("name", "Asset")

    # 1. Existing physical file on disk (wav, mp4, cube, setting, png, etc.)
    if file_path and os.path.isfile(file_path):
        abs_p = os.path.abspath(file_path)
        mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
        mime_data.setText(abs_p)
        return mime_data

    # 2. Text / Title Preset -> Generate .setting file
    if tab_type in ("text", "title"):
        try:
            from src.core.text_preset import TextStylePreset, FusionSettingGenerator
            temp_setting = os.path.join(temp_dir, f"ChunDVC_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            st = sample_text or name or "ChunDVC Title"

            if isinstance(target, TextStylePreset):
                FusionSettingGenerator.export_setting_file(target, temp_setting, sample_text=st)
            else:
                native = getattr(target, "native_preset", None)
                if isinstance(native, TextStylePreset):
                    FusionSettingGenerator.export_setting_file(native, temp_setting, sample_text=st)
                else:
                    preset = TextStylePreset(id=asset_id, name=name)
                    content = FusionSettingGenerator.generate_setting_content(preset, sample_text=st)
                    with open(temp_setting, "w", encoding="utf-8") as f:
                        f.write(content)

            abs_p = os.path.abspath(temp_setting)
            mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
            mime_data.setText(abs_p)
            return mime_data
        except Exception as e:
            logger.warning("Error generating text mime setting: %s", e)

    # 3. Transition Preset -> Generate transition macro .setting
    if tab_type in ("trans", "transition"):
        try:
            from src.core.transition_preset import TransitionStylePreset, TransitionMacroGenerator
            temp_setting = os.path.join(temp_dir, f"ChunDVC_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            if isinstance(target, TransitionStylePreset):
                TransitionMacroGenerator.export_setting_file(target, temp_setting)
            else:
                native = getattr(target, "native_preset", None)
                if isinstance(native, TransitionStylePreset):
                    TransitionMacroGenerator.export_setting_file(native, temp_setting)
                else:
                    TransitionMacroGenerator.export_setting_file(TransitionStylePreset(id=asset_id, name=name), temp_setting)

            abs_p = os.path.abspath(temp_setting)
            mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
            mime_data.setText(abs_p)
            return mime_data
        except Exception as e:
            logger.warning("Error generating transition mime setting: %s", e)

    # Fallback to plain text id
    mime_data.setText(asset_id)
    return mime_data


class AssetGridView(QListView):
    """
    Virtual ListView displaying asset cards with 60fps virtualization.
    """
    asset_selected = Signal(object)       # Emits asset info/object on click
    asset_activated = Signal(object)      # Emits on double-click or Enter
    favorite_toggled = Signal(str)        # Emits asset_id
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
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)
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

        # Drag state
        self._drag_start_pos: Optional[QPoint] = None
        self._sample_text_getter: Optional[Callable[[], str]] = None
        self._insert_service: Optional[Any] = None

        # Signals
        self.clicked.connect(self._on_clicked)
        self.doubleClicked.connect(self._on_double_clicked)

    def set_insert_service(self, service: Any):
        self._insert_service = service

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

        # Fusion paste action
        act_fusion = menu.addAction("📋 Dán vào Fusion (Ctrl+V)")

        # Open containing folder
        act_open_dir = None
        if file_path and os.path.exists(file_path):
            act_open_dir = menu.addAction("📂 Mở thư mục chứa")

        action = menu.exec(self.mapToGlobal(pos))
        if action == act_fav:
            self.favorite_toggled.emit(asset_id)
        elif action == act_fusion:
            self.fusion_paste_requested.emit(asset)
        elif act_open_dir and action == act_open_dir:
            folder = os.path.dirname(file_path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            self.open_folder_requested.emit(folder)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self._drag_start_pos:
            super().mouseMoveEvent(event)
            return

        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        index = self.indexAt(self._drag_start_pos)
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
        drag.exec(Qt.DropAction.CopyAction)
