"""
src/ui/lux/library/asset_filter.py
QSortFilterProxyModel filtering and ranking assets using SearchIndex (from Step 1).

Maintains exact search score order without freezing GUI.
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Set, Any
import unicodedata

from PySide6.QtCore import Qt, QSortFilterProxyModel, QModelIndex
from src.ui.lux.library.asset_model import (
    IdRole, CategoryRole, SubCategoryRole, IsFavRole, NameRole, PackNameRole, TagsRole
)
from src.core.collections_manager import CollectionsManager
from src.services.search_index import SearchIndex

logger = logging.getLogger("ChunDVC.AssetFilter")


def normalize_taxonomy(text: Any) -> str:
    """Canonical Unicode NFC lower normalization for category/tag matching."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", str(text)).strip().lower()


CANONICAL_CATEGORIES: Dict[str, List[str]] = {
    "text": ["text", "title", "chu", "chữ", "subtitle", "kinetic", "kinetics"],
    "trans": ["trans", "chuyen_canh", "chuyển cảnh", "transition", "transitions"],
    "meme": ["meme", "hai_huoc", "hài hước", "reaction"],
    "overlay": ["overlay", "lop_phu", "lớp phủ"],
    "sfx": ["sfx", "sound", "audio", "am_thanh", "âm thanh"],
    "bgm": ["bgm", "nhac_nen", "nhạc nền", "music", "nhac", "nhạc"],
    "lut": ["lut", "color", "mau", "màu"],
    "icon": ["icon", "sticker", "dao_cu", "đạo cụ"],
    "fx": ["fx", "fusion", "macro"],
    "broll": ["broll", "b-roll", "footage", "canh", "cảnh"],
}


_CANONICAL_LOOKUP: Dict[str, str] = {}
for _canon, _aliases in CANONICAL_CATEGORIES.items():
    _CANONICAL_LOOKUP[_canon] = _canon
    for _alias in _aliases:
        _CANONICAL_LOOKUP[_alias] = _canon


def get_canonical_category(cat: str) -> str:
    norm = normalize_taxonomy(cat)
    return _CANONICAL_LOOKUP.get(norm, norm)


class AssetFilterProxyModel(QSortFilterProxyModel):
    """
    Filtering and sorting proxy model for AssetListModel.
    Integrates SearchIndex scoring to keep high-relevance items on top.
    """
    def __init__(
        self,
        search_index: Optional[SearchIndex] = None,
        parent: Optional[Any] = None
    ):
        super().__init__(parent)
        self._search_index = search_index
        self._category: str = "all"
        self._sub_category: str = "all"
        self._tag: Optional[str] = None
        self._pack_filter: Optional[str] = None
        self._filter_mode: str = "all"  # "all", "fav", "recent"
        self._search_query: str = ""

        self._search_scores: Dict[str, float] = {}
        self._matched_ids: Set[str] = set()
        self._recent_ids: List[str] = []
        self._recent_ids_set: Set[str] = set()

        # Default to sorting by column 0
        self.setDynamicSortFilter(True)

    def set_search_index(self, indexer: SearchIndex):
        self._search_index = indexer
        if self._search_query:
            self._apply_search()

    def set_category(self, cat: str):
        cat = (cat or "all").lower().strip()
        if self._category != cat:
            self._category = cat
            self._sub_category = "all"
            self._tag = None  # Đổi category reset tag
            self._pack_filter = None
            self.invalidate()

    def set_sub_category(self, sub: str):
        sub = (sub or "all").strip()
        tag_changed = False
        if sub in ("all", "Tất cả", "fav:all"):
            if self._tag is not None:
                self._tag = None  # "Tất cả" xóa riêng tag
                tag_changed = True
        if self._sub_category != sub or tag_changed:
            self._sub_category = sub
            self.invalidate()

    def set_pack_filter(self, pack_name: Optional[str]):
        """Explicit filter by pack_name."""
        norm = normalize_taxonomy(pack_name) if pack_name and pack_name != "all" else None
        if self._pack_filter != norm:
            self._pack_filter = norm
            self.invalidate()

    def active_pack(self) -> Optional[str]:
        return self._pack_filter

    def set_tag(self, tag: Optional[str]):
        """Sets canonical tag filter. None, 'all', or 'Tất cả' clears active tag."""
        if not tag or tag in ("all", "Tất cả", "fav:all"):
            tag_norm = None
        else:
            tag_norm = normalize_taxonomy(tag)
        if self._tag != tag_norm:
            self._tag = tag_norm
            self.invalidate()

    def active_tag(self) -> Optional[str]:
        return self._tag

    def filter_subcategory(self) -> str:
        return self._sub_category

    def filter_category(self) -> str:
        return self._category

    def set_filter_mode(self, mode: str):
        """Sets mode: 'all', 'fav' (favorites), or 'recent'."""
        mode = (mode or "all").lower().strip()
        if self._filter_mode != mode:
            self._filter_mode = mode
            self.invalidate()

    def set_recent_ids(self, ids: List[str]):
        self._recent_ids = list(ids)
        self._recent_ids_set = set(self._recent_ids)
        if self._filter_mode == "recent":
            self.invalidate()

    def set_search_query(self, query: str):
        query = (query or "").strip()
        if self._search_query == query:
            return
        self._search_query = query
        self._apply_search()

    def set_filters_batch(
        self,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
        filter_mode: Optional[str] = None,
        recent_ids: Optional[List[str]] = None,
        search_query: Optional[str] = None,
    ) -> None:
        """Updates multiple filter parameters and invalidates the proxy model at most once."""
        changed = False
        query_changed = False

        if category is not None:
            cat = (category or "all").lower().strip()
            if self._category != cat:
                self._category = cat
                self._sub_category = "all"
                self._tag = None
                self._pack_filter = None
                changed = True

        if sub_category is not None:
            sub = (sub_category or "all").strip()
            if sub in ("all", "Tất cả", "fav:all") and self._tag is not None:
                self._tag = None
                changed = True
            if self._sub_category != sub:
                self._sub_category = sub
                changed = True

        if filter_mode is not None:
            mode = (filter_mode or "all").lower().strip()
            if self._filter_mode != mode:
                self._filter_mode = mode
                changed = True

        if recent_ids is not None:
            new_recent = list(recent_ids)
            if new_recent != self._recent_ids:
                self._recent_ids = new_recent
                self._recent_ids_set = set(new_recent)
                if self._filter_mode == "recent":
                    changed = True

        if search_query is not None:
            q = (search_query or "").strip()
            if self._search_query != q:
                self._search_query = q
                query_changed = True

        if query_changed:
            self._apply_search()
        elif changed:
            self.invalidate()

    def _apply_search(self):
        if not self._search_query:
            self._search_scores.clear()
            self._matched_ids.clear()
            self.invalidate()
            self.sort(-1)  # Restore natural order
            return

        if not self._search_index:
            # Fallback simple string match if search index not yet assigned
            self._search_scores.clear()
            self._matched_ids.clear()
            self.invalidate()
            return

        # Perform high-performance inverted index search (<1ms)
        results = self._search_index.search(self._search_query, limit=0)
        self._search_scores = {r["asset"]["id"]: r["score"] for r in results}
        self._matched_ids = set(self._search_scores.keys())

        self.invalidate()
        self.sort(0, Qt.SortOrder.DescendingOrder)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src_model = self.sourceModel()
        if not src_model:
            return True

        assets_list = getattr(src_model, "_assets", None)
        if assets_list is not None and 0 <= source_row < len(assets_list):
            item = assets_list[source_row]
            asset_id = item.get("id", "")
            cat = str(item.get("category") or "").lower()

            # 1. Category Filter (fast early exit for ~85% of rows)
            if self._category and self._category not in ("all", "fav", "recent"):
                if get_canonical_category(self._category) != get_canonical_category(cat):
                    return False

            # 2. Search Query Filter
            if self._search_query:
                if self._search_index:
                    if asset_id not in self._matched_ids:
                        return False
                else:
                    name_lower = item.get("_name_lower") or str(item.get("name") or "").lower()
                    if self._search_query.lower() not in name_lower:
                        return False

            # 3. Filter Mode (all / fav / recent) & Favorites/Custom Collections
            if self._category == "fav" or self._filter_mode == "fav":
                favs = getattr(src_model, "_favs", None)
                is_fav = (asset_id in favs) if favs is not None else bool(item.get("is_fav", False))
                if self._sub_category in ("coll:__ungrouped__", "__ungrouped__"):
                    if not is_fav or bool(CollectionsManager.get_asset_collections(asset_id)):
                        return False
                elif self._sub_category and self._sub_category.startswith("coll:"):
                    coll_name = self._sub_category[5:]
                    if asset_id not in CollectionsManager.get_collection_assets(coll_name):
                        return False
                else:
                    if not is_fav:
                        return False
            elif self._filter_mode == "recent":
                if asset_id not in self._recent_ids_set:
                    return False

            pack_norm = item.get("_pack_norm")
            if pack_norm is None:
                pack_norm = normalize_taxonomy(item.get("pack_name") or "")

            # 4. Explicit Pack Filter
            if self._pack_filter:
                if pack_norm != self._pack_filter:
                    return False

            sub_norm = item.get("_sub_norm")
            if sub_norm is None:
                sub_norm = normalize_taxonomy(item.get("sub_category") or "")
            tags_norm = item.get("_tags_norm")
            if tags_norm is None:
                tags_norm = [normalize_taxonomy(t) for t in (item.get("tags") or []) if t]

            # 5. Sub-category Filter (supports pack: prefix or subcategory/tag)
            if self._sub_category and self._sub_category not in ("all", "Tất cả", "fav:all"):
                if self._sub_category.startswith("pack:"):
                    target_pack = normalize_taxonomy(self._sub_category[5:])
                    if pack_norm != target_pack:
                        return False
                elif self._category != "fav" and not self._sub_category.startswith("coll:"):
                    target_sub = normalize_taxonomy(self._sub_category)
                    if sub_norm != target_sub and target_sub not in tags_norm:
                        return False

            # 6. Tag Filter (explicit tag matching only - pack is never treated as tag)
            if self._tag:
                if self._tag not in tags_norm and self._tag != sub_norm:
                    return False

            return True

        idx = src_model.index(source_row, 0, source_parent)
        asset_id = src_model.data(idx, IdRole)
        is_fav = bool(src_model.data(idx, IsFavRole))
        cat = str(src_model.data(idx, CategoryRole) or "").lower()
        sub = str(src_model.data(idx, SubCategoryRole) or "")
        pack = str(src_model.data(idx, PackNameRole) or "")
        tags_raw = src_model.data(idx, TagsRole) or []
        tags_norm = [normalize_taxonomy(t) for t in tags_raw if t]

        # 1. Search Query Filter
        if self._search_query:
            if self._search_index:
                if asset_id not in self._matched_ids:
                    return False
            else:
                # Fallback contains match on name
                name = str(src_model.data(idx, NameRole) or "").lower()
                if self._search_query.lower() not in name:
                    return False

        # 2. Filter Mode (all / fav / recent) & Favorites/Custom Collections
        if self._category == "fav" or self._filter_mode == "fav":
            if self._sub_category in ("coll:__ungrouped__", "__ungrouped__"):
                if not is_fav or bool(CollectionsManager.get_asset_collections(asset_id)):
                    return False
            elif self._sub_category and self._sub_category.startswith("coll:"):
                coll_name = self._sub_category[5:]
                coll_assets = set(CollectionsManager.get_collection_assets(coll_name))
                if asset_id not in coll_assets:
                    return False
            else:
                if not is_fav:
                    return False
        elif self._filter_mode == "recent":
            if asset_id not in self._recent_ids_set:
                return False

        # 3. Category Filter (canonical mapping with Unicode normalization - exact, no substring bleed)
        if self._category and self._category not in ("all", "fav", "recent"):
            target_canon = get_canonical_category(self._category)
            asset_canon = get_canonical_category(cat)
            if target_canon != asset_canon:
                return False

        # 4. Explicit Pack Filter
        if getattr(self, "_pack_filter", None):
            if normalize_taxonomy(pack) != self._pack_filter:
                return False

        # 5. Sub-category Filter (supports pack: prefix or subcategory/tag)
        if self._sub_category and self._sub_category not in ("all", "Tất cả", "fav:all"):
            if self._sub_category.startswith("pack:"):
                target_pack = normalize_taxonomy(self._sub_category[5:])
                if normalize_taxonomy(pack) != target_pack:
                    return False
            elif self._category != "fav" and not self._sub_category.startswith("coll:"):
                sub_norm = normalize_taxonomy(sub)
                target_sub = normalize_taxonomy(self._sub_category)
                if sub_norm != target_sub and target_sub not in tags_norm:
                    return False

        # 6. Tag Filter (explicit tag matching only - pack is never treated as tag)
        if self._tag:
            sub_norm = normalize_taxonomy(sub)
            if self._tag not in tags_norm and self._tag != sub_norm:
                return False

        return True

    def lessThan(self, source_left: QModelIndex, source_right: QModelIndex) -> bool:
        """Compares two items. Prioritizes search relevance scores if searching."""
        src_model = self.sourceModel()
        if not src_model:
            return super().lessThan(source_left, source_right)

        if self._search_query and self._search_scores:
            left_id = src_model.data(source_left, IdRole)
            right_id = src_model.data(source_right, IdRole)
            score_left = self._search_scores.get(left_id, 0.0)
            score_right = self._search_scores.get(right_id, 0.0)

            if score_left != score_right:
                return score_left < score_right

        # Default order by row number
        return source_left.row() < source_right.row()
