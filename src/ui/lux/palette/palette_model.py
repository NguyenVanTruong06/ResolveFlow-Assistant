"""
Palette Data Model & Search Provider for Command Palette.
(UX_REDESIGN.md Section 5.3 & 5.5)
"""
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Set
import logging

from src.services.search_index import SearchIndex
from .command_registry import CommandRegistry, CommandItem
from ..context.context_profiles import PROFILES

logger = logging.getLogger("ChunDVC.PaletteModel")


@dataclass
class PaletteItem:
    kind: str                   # "asset" or "command"
    data: Any                   # dict (asset) or CommandItem
    title: str                  # Display title
    meta: str                   # Category, duration, or command description
    score: int = 0
    match_reason: str = ""      # e.g. "chuyển động · rung 92%"
    shortcut_hint: str = ""     # e.g. "⏎", "⌥1", "⌥2"...
    is_fav: bool = False
    category: str = ""
    duration: float = 0.0


class PaletteSearchProvider:
    """
    Search provider for Command Palette:
    - Empty query: 3 groups (Recent, Favorites, Page-suggested assets)
    - Query starts with '>': Commands from CommandRegistry
    - Text query: Ranked search from SearchIndex with shortcut hints
    """
    def __init__(
        self,
        search_index: Optional[SearchIndex] = None,
        command_registry: Optional[CommandRegistry] = None,
    ):
        self._search_index = search_index
        self._command_registry = command_registry or CommandRegistry()
        self._all_assets: List[Dict[str, Any]] = []
        self._asset_map: Dict[str, Dict[str, Any]] = {}
        self._recent_ids: List[str] = []
        self._fav_ids: Set[str] = set()

    def set_search_index(self, index: SearchIndex):
        self._search_index = index

    def set_assets(self, assets: List[Any]):
        from ..library.asset_model import extract_asset_info
        normalized = []
        for a in assets:
            if isinstance(a, dict):
                normalized.append(a)
            else:
                normalized.append(extract_asset_info(a))
        self._all_assets = normalized
        self._asset_map = {a.get("id", ""): a for a in normalized if a.get("id")}

    def set_recent_ids(self, ids: List[str]):
        self._recent_ids = ids

    def add_recent(self, asset_id: str):
        if asset_id in self._recent_ids:
            self._recent_ids.remove(asset_id)
        self._recent_ids.insert(0, asset_id)
        self._recent_ids = self._recent_ids[:20]

    def set_fav_ids(self, ids: Set[str]):
        self._fav_ids = set(ids)

    def toggle_fav(self, asset_id: str) -> bool:
        if asset_id in self._fav_ids:
            self._fav_ids.remove(asset_id)
            return False
        else:
            self._fav_ids.add(asset_id)
            return True

    def search(self, query: str, current_page: str = "edit", limit: int = 7) -> List[PaletteItem]:
        q = (query or "").strip()

        # 1. Command Mode (starts with '>')
        if q.startswith(">"):
            cmds = self._command_registry.search(q, limit=limit)
            items = []
            for i, cmd in enumerate(cmds):
                hint = "⏎" if i == 0 else (f"⌥{i+1}" if i < 9 else "")
                items.append(PaletteItem(
                    kind="command",
                    data=cmd,
                    title=cmd.name,
                    meta=cmd.meta,
                    shortcut_hint=hint,
                ))
            return items

        # 2. Empty Query: Gần đây + Yêu thích + Gợi ý theo trang
        if not q:
            return self._build_empty_query_results(current_page, limit=limit)

        # 3. Text Query: SearchIndex ranking
        if self._search_index:
            scored_results = self._search_index.search(q, limit=limit)
            items = []
            for i, scored in enumerate(scored_results):
                aid = scored.asset_id
                asset = self._asset_map.get(aid, {"id": aid, "name": scored.name, "category": scored.category})
                hint = "⏎" if i == 0 else (f"⌥{i+1}" if i < 9 else "")

                # Format match reason
                reason = ""
                if scored.matched_alias:
                    reason = f"{scored.matched_alias} {min(100, scored.score)}%"
                elif scored.score > 0:
                    reason = f"{min(100, scored.score)}%"

                dur = asset.get("duration", 0.0) or 0.0
                cat = asset.get("category", "") or scored.category

                meta_str = cat.capitalize() if cat else ""
                if dur > 0:
                    meta_str += f" · {dur:.1f}s" if dur < 10 else f" · {int(dur)}s"
                if reason:
                    meta_str += f" · {reason}"

                items.append(PaletteItem(
                    kind="asset",
                    data=asset,
                    title=asset.get("name", scored.name),
                    meta=meta_str,
                    score=scored.score,
                    match_reason=reason,
                    shortcut_hint=hint,
                    is_fav=(aid in self._fav_ids),
                    category=cat,
                    duration=dur,
                ))
            return items

        # Fallback if no SearchIndex: simple substring match on assets
        q_lower = q.lower()
        matched = []
        for a in self._all_assets:
            if q_lower in a.get("name", "").lower() or q_lower in a.get("category", "").lower():
                matched.append(a)
                if len(matched) >= limit:
                    break

        items = []
        for i, a in enumerate(matched):
            hint = "⏎" if i == 0 else (f"⌥{i+1}" if i < 9 else "")
            items.append(PaletteItem(
                kind="asset",
                data=a,
                title=a.get("name", ""),
                meta=a.get("category", "").capitalize(),
                shortcut_hint=hint,
                is_fav=(a.get("id") in self._fav_ids),
                category=a.get("category", ""),
            ))
        return items

    def _build_empty_query_results(self, current_page: str, limit: int = 7) -> List[PaletteItem]:
        items: List[PaletteItem] = []
        seen_ids = set()

        # Group 1: Recent assets (up to 3)
        for rid in self._recent_ids:
            if len(items) >= limit:
                break
            if rid in self._asset_map and rid not in seen_ids:
                a = self._asset_map[rid]
                seen_ids.add(rid)
                items.append(self._to_palette_item(a, group_label="Gần đây"))

        # Group 2: Favorites (up to 3)
        for fid in self._fav_ids:
            if len(items) >= limit:
                break
            if fid in self._asset_map and fid not in seen_ids:
                a = self._asset_map[fid]
                seen_ids.add(fid)
                items.append(self._to_palette_item(a, group_label="Yêu thích"))

        # Group 3: Page suggestions (from active profile categories)
        profile = PROFILES.get(current_page)
        allowed_cats = profile.categories if profile else []
        for a in self._all_assets:
            if len(items) >= limit:
                break
            aid = a.get("id", "")
            if aid and aid not in seen_ids:
                if not allowed_cats or a.get("category") in allowed_cats:
                    seen_ids.add(aid)
                    items.append(self._to_palette_item(a, group_label=f"Gợi ý {profile.label if profile else ''}"))

        # Add shortcut hints
        for i, it in enumerate(items):
            it.shortcut_hint = "⏎" if i == 0 else (f"⌥{i+1}" if i < 9 else "")

        return items

    def _to_palette_item(self, asset: Dict[str, Any], group_label: str = "") -> PaletteItem:
        aid = asset.get("id", "")
        cat = asset.get("category", "")
        dur = asset.get("duration", 0.0) or 0.0
        meta_str = f"{group_label} · {cat.capitalize()}" if group_label else cat.capitalize()
        if dur > 0:
            meta_str += f" · {dur:.1f}s" if dur < 10 else f" · {int(dur)}s"

        return PaletteItem(
            kind="asset",
            data=asset,
            title=asset.get("name", "Asset"),
            meta=meta_str,
            is_fav=(aid in self._fav_ids),
            category=cat,
            duration=dur,
        )
