"""
src/ui/lux/library/asset_model.py
Qt Model for asset library virtualization (learning from Akitt Pro 4.0 & Apple Final Cut Pro).

Provides QAbstractListModel over AssetRepository / AssetIndexer items.
Supports async thumbnail loading with dataChanged notifications.
"""
from __future__ import annotations

import os
import logging
from typing import Optional, List, Dict, Any, Set

from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, Signal, Slot
from PySide6.QtGui import QPixmap

logger = logging.getLogger("ResolveFlow.AssetModel")

# Custom Item Data Roles
IdRole = Qt.ItemDataRole.UserRole + 1
NameRole = Qt.ItemDataRole.UserRole + 2
KindRole = Qt.ItemDataRole.UserRole + 3
DurationRole = Qt.ItemDataRole.UserRole + 4
ThumbnailRole = Qt.ItemDataRole.UserRole + 5
IsFavRole = Qt.ItemDataRole.UserRole + 6
PreviewPathRole = Qt.ItemDataRole.UserRole + 7
CategoryRole = Qt.ItemDataRole.UserRole + 8
SubCategoryRole = Qt.ItemDataRole.UserRole + 9
AssetObjRole = Qt.ItemDataRole.UserRole + 10
MotionRole = Qt.ItemDataRole.UserRole + 11
FilePathRole = Qt.ItemDataRole.UserRole + 12
TagsRole = Qt.ItemDataRole.UserRole + 13
PackNameRole = Qt.ItemDataRole.UserRole + 14


def extract_asset_info(item: Any) -> Dict[str, Any]:
    """Helper to extract unified dictionary metadata from any asset item."""
    if isinstance(item, dict):
        asset_id = str(item.get("id") or item.get("name") or "")
        name = str(item.get("name") or asset_id)
        cat = str(item.get("tab") or item.get("category") or "text").lower()
        pack_name = str(item.get("pack_name") or "")
        sub = str(item.get("sub") or item.get("sub_category") or (item.get("category") if item.get("tab") else "Tất cả"))
        if pack_name and pack_name != "Mặc định":
            sub = pack_name
        dur = float(item.get("duration") or item.get("dur") or 0.0)
        file_path = str(item.get("file_path") or item.get("path") or "")
        preview_path = str(item.get("preview_path") or file_path)
        motion = str(item.get("motion") or "")
        tags = item.get("tags") or []
    else:
        asset_id = str(getattr(item, "id", "") or getattr(item, "name", ""))
        name = str(getattr(item, "name", "") or asset_id)
        pack_name = str(getattr(item, "pack_name", "") or "")
        type_name = type(item).__name__
        if type_name == "TextStylePreset":
            cat = "text"
            sub = str(getattr(item, "category", "") or "Kinetic")
        elif type_name == "TransitionStylePreset":
            cat = "trans"
            sub = str(getattr(item, "category", "") or "Transition")
        elif getattr(item, "tab", ""):
            cat = str(getattr(item, "tab")).lower()
            sub = str(getattr(item, "category", "") or getattr(item, "sub", "") or "Tất cả")
        else:
            cat = str(getattr(item, "category", "text")).lower()
            sub = str(getattr(item, "sub", "") or getattr(item, "sub_category", "Tất cả"))
        if pack_name and pack_name != "Mặc định":
            sub = pack_name

        dur = float(getattr(item, "duration", 0.0) or getattr(item, "dur", 0.0))
        file_path = str(getattr(item, "file_path", "") or getattr(item, "path", ""))
        preview_path = str(getattr(item, "preview_path", "") or file_path)
        motion = str(getattr(item, "motion", "") or getattr(item, "animation", ""))
        tags = getattr(item, "tags", []) or []

    # Map category to display kind label
    kind_map = {
        "text": "Chữ",
        "trans": "Chuyển cảnh",
        "transition": "Chuyển cảnh",
        "meme": "Meme",
        "overlay": "Lớp phủ",
        "sfx": "SFX",
        "audio": "SFX",
        "sound": "SFX",
        "bgm": "Nhạc nền",
        "lut": "LUT",
        "color": "LUT",
        "fx": "Fusion Node",
        "icon": "Sticker",
        "sticker": "Sticker",
    }
    kind = kind_map.get(cat, cat.upper())

    return {
        "id": asset_id,
        "name": name,
        "category": cat,
        "sub": sub,
        "duration": dur,
        "file_path": file_path,
        "preview_path": preview_path,
        "motion": motion,
        "tags": tags,
        "kind": kind,
        "pack_name": pack_name,
        "obj": item
    }


class AssetListModel(QAbstractListModel):
    """
    Virtual model managing the complete asset library.
    Emits dataChanged when asynchronous thumbnails arrive.
    """
    favorite_changed = Signal(str, bool)

    def __init__(
        self,
        assets: Optional[List[Any]] = None,
        favs: Optional[Set[str]] = None,
        parent: Optional[Any] = None
    ):
        super().__init__(parent)
        self._assets: List[Dict[str, Any]] = []
        self._id_to_row: Dict[str, int] = {}
        self._favs: Set[str] = set(favs) if favs is not None else set()
        self._thumb_cache: Dict[str, QPixmap] = {}
        self._pending_thumb_requests: Set[str] = set()

        if assets:
            self.set_assets(assets)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._assets)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._assets)):
            return None

        info = self._assets[index.row()]
        asset_id = info["id"]

        if role == Qt.ItemDataRole.DisplayRole:
            return info["name"]

        if role == Qt.ItemDataRole.ToolTipRole:
            dur_str = f" · {info['duration']:.1f}s" if info["duration"] > 0 else ""
            return f"{info['name']} ({info['kind']}{dur_str})\nDanh mục: {info['category']} / {info['sub']}"

        if role == IdRole:
            return asset_id

        if role == NameRole:
            return info["name"]

        if role == KindRole:
            return info["kind"]

        if role == DurationRole:
            return info["duration"]

        if role == CategoryRole:
            return info["category"]

        if role == SubCategoryRole:
            return info["sub"]

        if role == MotionRole:
            return info["motion"]

        if role == FilePathRole:
            return info["file_path"]

        if role == PreviewPathRole:
            return info["preview_path"]

        if role == TagsRole:
            return info["tags"]

        if role == PackNameRole:
            return info.get("pack_name", "")

        if role == AssetObjRole:
            return info["obj"]

        if role == IsFavRole:
            return asset_id in self._favs

        if role == ThumbnailRole:
            if asset_id in self._thumb_cache:
                return self._thumb_cache[asset_id]

            if info["category"] in ("text", "title", "chu", "subtitle"):
                from src.ui.lux.library.text_preview_generator import get_text_preset_pixmap
                pix = get_text_preset_pixmap(asset_id, info["name"])
                if pix and not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix

            # Request async thumbnail if file path exists
            file_p = info["file_path"]
            if file_p and file_p not in self._pending_thumb_requests:
                self._request_async_thumbnail(asset_id, file_p)
            return None

        return None

    def set_assets(self, items: List[Any]):
        """Replace the dataset and notify views."""
        self.beginResetModel()
        self._assets = [extract_asset_info(it) for it in items]
        self._id_to_row = {info["id"]: idx for idx, info in enumerate(self._assets)}
        self.endResetModel()

    def update_thumbnail(self, asset_id: str, pixmap: QPixmap):
        """Thread-safe update of loaded thumbnail."""
        if not pixmap or pixmap.isNull():
            return
        self._thumb_cache[asset_id] = pixmap
        if asset_id in self._id_to_row:
            row = self._id_to_row[asset_id]
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [ThumbnailRole])

    def _request_async_thumbnail(self, asset_id: str, file_path: str):
        """Dispatches thumbnail extraction via AssetCache."""
        if not os.path.isfile(file_path):
            return
        self._pending_thumb_requests.add(file_path)

        try:
            from src.core.asset_cache import request_thumbnail_async

            def _on_loaded(thumb_path: str):
                self._pending_thumb_requests.discard(file_path)
                if thumb_path and os.path.isfile(thumb_path):
                    pix = QPixmap(thumb_path)
                    self.update_thumbnail(asset_id, pix)

            request_thumbnail_async(file_path, _on_loaded)
        except Exception as e:
            logger.debug("Failed to dispatch async thumbnail for %s: %s", asset_id, e)
            self._pending_thumb_requests.discard(file_path)

    def toggle_favorite(self, asset_id: str) -> bool:
        """Toggles favorite state for an asset, notifies views."""
        if asset_id in self._favs:
            self._favs.remove(asset_id)
            is_fav = False
        else:
            self._favs.add(asset_id)
            is_fav = True

        if asset_id in self._id_to_row:
            row = self._id_to_row[asset_id]
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [IsFavRole])

        self.favorite_changed.emit(asset_id, is_fav)
        return is_fav

    def get_asset(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self._assets):
            return self._assets[row]
        return None

    def get_asset_by_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        row = self._id_to_row.get(asset_id)
        if row is not None:
            return self._assets[row]
        return None
