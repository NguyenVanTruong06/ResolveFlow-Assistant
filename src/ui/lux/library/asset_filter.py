"""
src/ui/lux/library/asset_filter.py
QSortFilterProxyModel filtering and ranking assets using SearchIndex (from Step 1).

Maintains exact search score order without freezing GUI.
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Set, Any

from PySide6.QtCore import Qt, QSortFilterProxyModel, QModelIndex
from src.ui.lux.library.asset_model import (
    IdRole, CategoryRole, SubCategoryRole, IsFavRole, NameRole, PackNameRole
)
from src.core.collections_manager import CollectionsManager
from src.services.search_index import SearchIndex

logger = logging.getLogger("ResolveFlow.AssetFilter")


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
        self._filter_mode: str = "all"  # "all", "fav", "recent"
        self._search_query: str = ""

        self._search_scores: Dict[str, float] = {}
        self._matched_ids: Set[str] = set()
        self._recent_ids: List[str] = []

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
            self.invalidate()

    def set_sub_category(self, sub: str):
        sub = (sub or "all").strip()
        if self._sub_category != sub:
            self._sub_category = sub
            self.invalidate()

    def set_filter_mode(self, mode: str):
        """Sets mode: 'all', 'fav' (favorites), or 'recent'."""
        mode = (mode or "all").lower().strip()
        if self._filter_mode != mode:
            self._filter_mode = mode
            self.invalidate()

    def set_recent_ids(self, ids: List[str]):
        self._recent_ids = list(ids)
        if self._filter_mode == "recent":
            self.invalidate()

    def set_search_query(self, query: str):
        query = (query or "").strip()
        if self._search_query == query:
            return
        self._search_query = query
        self._apply_search()

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
        results = self._search_index.search(self._search_query)
        self._search_scores = {r["asset"]["id"]: r["score"] for r in results}
        self._matched_ids = set(self._search_scores.keys())

        self.invalidate()
        self.sort(0, Qt.SortOrder.DescendingOrder)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src_model = self.sourceModel()
        if not src_model:
            return True

        idx = src_model.index(source_row, 0, source_parent)
        asset_id = src_model.data(idx, IdRole)
        is_fav = bool(src_model.data(idx, IsFavRole))
        cat = str(src_model.data(idx, CategoryRole) or "").lower()
        sub = str(src_model.data(idx, SubCategoryRole) or "")
        pack = str(src_model.data(idx, PackNameRole) or "")

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
            if self._sub_category and self._sub_category.startswith("coll:"):
                coll_name = self._sub_category[5:]
                coll_assets = set(CollectionsManager.get_collection_assets(coll_name))
                if asset_id not in coll_assets:
                    return False
            else:
                if not is_fav:
                    return False
        elif self._filter_mode == "recent":
            if asset_id not in self._recent_ids:
                return False

        # 3. Category Filter
        if self._category and self._category not in ("all", "fav", "recent"):
            cat_aliases = {
                "chuyen_canh": ["chuyen_canh", "trans", "transition"],
                "trans": ["trans", "chuyen_canh", "transition"],
                "text": ["text", "title", "chu"],
                "am_thanh": ["sfx", "sound", "audio", "am_thanh"],
                "sfx": ["sfx", "sound", "audio", "am_thanh"],
                "nhac_nen": ["bgm", "nhac_nen", "music"],
                "bgm": ["bgm", "nhac_nen", "music"],
                "lut": ["lut", "color", "mau"],
                "meme": ["meme", "hai_huoc"],
                "overlay": ["overlay", "lop_phu"],
                "icon": ["icon", "sticker"],
                "fx": ["fx", "fusion"],
            }
            allowed = cat_aliases.get(self._category, [self._category])
            if cat not in allowed and self._category not in cat:
                return False

        # 4. Sub-category & Pack Filter
        if self._sub_category and self._sub_category not in ("all", "Tất cả", "fav:all"):
            if self._sub_category.startswith("pack:"):
                target_pack = self._sub_category[5:]
                if pack != target_pack and sub != target_pack:
                    return False
            elif self._category != "fav":
                if sub != self._sub_category and pack != self._sub_category:
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
