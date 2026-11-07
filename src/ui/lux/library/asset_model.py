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

logger = logging.getLogger("ChunDVC.AssetModel")

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
        dur = float(item.get("duration") or item.get("dur") or 0.0)
        file_path = str(item.get("file_path") or item.get("path") or "")
        thumbnail_path = str(item.get("thumbnail_path") or item.get("thumb") or "")
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
            sub = str(getattr(item, "sub", "") or getattr(item, "category", "") or "Tất cả")
        else:
            cat = str(getattr(item, "category", "text")).lower()
            sub = str(getattr(item, "sub", "") or getattr(item, "sub_category", "Tất cả"))

        dur = float(getattr(item, "duration", 0.0) or getattr(item, "dur", 0.0))
        file_path = str(getattr(item, "file_path", "") or getattr(item, "path", ""))
        thumbnail_path = str(getattr(item, "thumbnail_path", "") or getattr(item, "thumb", "") or "")
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
        # Map category to display kind label
        "lut": "LUT",
        "luts": "LUT",
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
        "thumbnail_path": thumbnail_path,
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

        if role in (Qt.ItemDataRole.UserRole, AssetObjRole):
            return info["obj"]

        if role == IsFavRole:
            return asset_id in self._favs

        if role == ThumbnailRole:
            return self._load_or_generate_thumbnail(info)

        return None

    def _load_or_generate_thumbnail(self, info: Dict[str, Any]) -> Optional[QPixmap]:
        """Loads or generates a rich 16:9 thumbnail for the given asset info dict."""
        asset_id = info["id"]
        if asset_id in self._thumb_cache:
            return self._thumb_cache[asset_id]

        cat = str(info.get("category") or "").lower()
        asset_obj = info.get("obj") or info

        if cat in ("text", "title", "chu", "subtitle"):
            try:
                from src.ui.lux.library.text_preview_generator import generate_text_preset_thumbnail
                pix = generate_text_preset_thumbnail(asset_obj, 256, 144)
                if pix and not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix
            except Exception:
                pass
            try:
                from src.ui.lux.library.text_preview_generator import get_text_preset_pixmap
                pix = get_text_preset_pixmap(asset_id, info["name"])
                if pix and not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix
            except Exception:
                pass

        if cat in ("lut", "luts", "color"):
            thumb_lut = info.get("thumbnail_path")
            if thumb_lut and os.path.isfile(thumb_lut):
                pix = QPixmap(thumb_lut)
                if not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix
            try:
                from src.ui.lux.library.lut_preview_generator import generate_lut_thumbnail
                pix = generate_lut_thumbnail(asset_obj, 256, 144)
                if pix and not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix
            except Exception:
                pass

        # 1. Nếu có thumbnail_path trực tiếp là ảnh thật trên đĩa (vd: PNG từ gói DRFX), nạp và cache ngay
        thumb_p = info.get("thumbnail_path")
        if not thumb_p and cat in ("trans", "transition", "transitions"):
            try:
                from src.ui.lux.library.transition_preview_generator import resolve_drfx_thumbnail
                resolved_thumb = resolve_drfx_thumbnail(asset_obj)
                if resolved_thumb and os.path.isfile(resolved_thumb):
                    thumb_p = resolved_thumb
                    info["thumbnail_path"] = resolved_thumb
            except Exception:
                pass

        if thumb_p and os.path.isfile(thumb_p):
            pix = QPixmap(thumb_p)
            if not pix.isNull():
                self._thumb_cache[asset_id] = pix
                return pix

        # 2. Với Chuyển cảnh & Media có video thật (.mp4/.mov/.webm), trích xuất khung hình video thật
        cand_p = info.get("preview_path") or info.get("file_path")
        if cat in ("trans", "transition", "transitions"):
            _vid_exts = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")
            if not (cand_p and str(cand_p).lower().endswith(_vid_exts) and os.path.isfile(str(cand_p))):
                try:
                    from src.ui.lux.library.transition_preview_generator import resolve_transition_preview_video
                    resolved_vid = resolve_transition_preview_video(asset_obj)
                    if resolved_vid and os.path.isfile(resolved_vid):
                        cand_p = resolved_vid
                        info["preview_path"] = resolved_vid
                except Exception:
                    pass

            if cand_p and str(cand_p).lower().endswith(_vid_exts) and os.path.isfile(str(cand_p)):
                # Nếu khung hình video đã có sẵn trong đĩa cache AssetCache thì nạp tức thì
                try:
                    from src.core.asset_cache import get_file_hash_key, get_thumb_cache_dir
                    cached_jpg = os.path.join(get_thumb_cache_dir(), f"{get_file_hash_key(cand_p)}.jpg")
                    if os.path.isfile(cached_jpg):
                        pix = QPixmap(cached_jpg)
                        if not pix.isNull():
                            self._thumb_cache[asset_id] = pix
                            return pix
                except Exception:
                    pass
                if cand_p not in self._pending_thumb_requests:
                    self._request_async_thumbnail(asset_id, cand_p)
                return None

            # 3. CHỈ fallback sang vẽ mô phỏng khi chuyển cảnh hoàn toàn KHÔNG có ảnh hay video thật trên đĩa
            try:
                from src.ui.lux.library.transition_preview_generator import generate_transition_thumbnail
                pix = generate_transition_thumbnail(asset_obj, 256, 144)
                if pix and not pix.isNull():
                    self._thumb_cache[asset_id] = pix
                    return pix
            except Exception:
                pass
            return None

        # Thử nạp bất đồng bộ từ preview_path hoặc file_path cho các loại media khác (Meme, Overlay...)
        if cand_p and os.path.isfile(str(cand_p)) and cand_p not in self._pending_thumb_requests:
            self._request_async_thumbnail(asset_id, cand_p)
        return None

    def set_assets(self, items: List[Any]):
        """Replace the dataset and notify views."""
        self.beginResetModel()
        self._assets = [extract_asset_info(it) for it in items]
        self._id_to_row = {info["id"]: idx for idx, info in enumerate(self._assets)}
        self.endResetModel()

    def index_of_asset_id(self, asset_id: str) -> QModelIndex:
        """Returns source QModelIndex for given asset_id, or invalid index if not found."""
        if asset_id in self._id_to_row:
            return self.index(self._id_to_row[asset_id], 0)
        return QModelIndex()

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
