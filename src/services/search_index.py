"""
src/services/search_index.py
SearchIndex: High-performance memory inverted search index (<16ms for 600+ assets).
Implements 7-signal scoring table (UX_REDESIGN.md section 5.5).
Supports category prefixes (c:, t:, s:, m:, l:, f:), alias normalization, and frecency tracking.

Strict Clean Architecture: NO QtWidgets or QtGui allowed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("ResolveFlow.SearchIndex")

# Normalized alias table for motion types
# "mo dan" is strictly assigned to fade_in only; fade_out uses "toi dan", "bien mat".
ALIAS_MAP: Dict[str, List[str]] = {
    "shake": [
        "shake", "camera shake", "vibrate", "rung", "giat", "rung giat", "rung giật",
        "chan dong", "chấn động", "nhap nho"
    ],
    "zoom_in": [
        "zoom", "zoom_in", "scale", "scale_up", "phong to", "phóng to", "zoom vao", "zoom vào",
        "tien lai", "tiến lại"
    ],
    "zoom_out": [
        "zoom_out", "scale_down", "shrink", "thu nho", "thu nhỏ", "zoom ra", "lui lai", "lùi lại"
    ],
    "slide_up": [
        "slide_up", "rise", "truot len", "trượt lên", "bay len", "bay lên", "noi len", "nổi lên"
    ],
    "slide_down": [
        "slide_down", "drop", "truot xuong", "trượt xuống", "roi xuong", "rơi xuống", "ha xuong"
    ],
    "slide_left": [
        "slide_left", "pan_left", "truot trai", "trượt trái", "qua trai", "qua trái", "sang trai"
    ],
    "slide_right": [
        "slide_right", "pan_right", "truot phai", "trượt phải", "qua phai", "qua phải", "sang phai"
    ],
    "fade_in": [
        "fade", "fade_in", "dissolve", "appear", "mo dan", "mờ dần", "hien dan", "hiện dần",
        "sang dan", "sáng dần"
    ],
    "fade_out": [
        "fade_out", "disappear", "toi dan", "tối dần", "bien mat", "biến mất"
    ],
    "bounce": [
        "bounce", "pop", "nay", "nẩy", "bat len", "bật lên", "tung tang"
    ],
    "glitch": [
        "glitch", "rgb", "cyber", "nhieu", "nhiễu", "loi", "hỏng"
    ],
    "typewriter": [
        "typewriter", "go chu", "gõ chữ", "danh may", "đánh máy", "con tro", "cursor"
    ],
    "glow": [
        "glow", "neon", "phat sang", "phát sáng", "da quang", "dạ quang", "den neon"
    ],
    "rapid_cuts": [
        "rapid", "chop", "chớp", "nhanh", "chuyen canh", "chuyển cảnh", "transition"
    ],
}

MOTION_VI: Dict[str, str] = {
    "shake": "Rung giật",
    "zoom_in": "Phóng to",
    "zoom_out": "Thu nhỏ",
    "slide_up": "Trượt lên",
    "slide_down": "Trượt xuống",
    "slide_left": "Trượt trái",
    "slide_right": "Trượt phải",
    "fade_in": "Mờ hiện",
    "fade_out": "Tối dần",
    "bounce": "Nảy bật",
    "glitch": "Nhiễu số",
    "typewriter": "Gõ chữ",
    "glow": "Phát sáng",
    "rapid_cuts": "Chớp nhanh",
}

# Prefix shortcuts mapping
PREFIX_MAP: Dict[str, Set[str]] = {
    "c": {"titles", "title", "text"},
    "t": {"transitions", "transition", "trans"},
    "s": {"sfx", "audio", "sound"},
    "m": {"memes", "meme", "broll"},
    "l": {"luts", "lut", "color"},
    "f": {"overlays", "overlay", "generators", "generator", "icons", "stickers", "fx"},
}


def strip_accents(text: str) -> str:
    """Removes Vietnamese tone marks, returns lowercased ASCII-friendly string."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.replace("đ", "d").replace("Đ", "D").lower()


def is_subsequence(query: str, target: str) -> bool:
    """Returns True if query characters appear sequentially inside target."""
    if not query:
        return True
    i = 0
    q_len = len(query)
    for ch in target:
        if ch == query[i]:
            i += 1
            if i == q_len:
                return True
    return False


class SearchIndex:
    """
    Inverted search index for ChunDVC / ResolveFlow assets.
    """
    def __init__(
        self,
        assets: Optional[List[Dict[str, Any]]] = None,
        frecency_path: Optional[str] = None,
    ):
        self._assets: List[Dict[str, Any]] = []
        self._frecency_path = frecency_path or os.path.expanduser("~/.cache/chundvc/frecency.json")
        self._frecency: Dict[str, int] = {}
        self._page_boost_cats: Set[str] = set()

        self._load_frecency()
        if assets:
            self.set_assets(assets)

    def _load_frecency(self):
        try:
            if os.path.exists(self._frecency_path):
                with open(self._frecency_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._frecency = {str(k): int(v) for k, v in data.get("use_counts", data).items() if isinstance(v, (int, float))}
        except Exception as e:
            logger.debug("Could not load frecency cache: %s", e)
            self._frecency = {}

    def _save_frecency(self):
        try:
            os.makedirs(os.path.dirname(self._frecency_path), exist_ok=True)
            temp = self._frecency_path + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump({"use_counts": self._frecency}, f, ensure_ascii=False)
            os.replace(temp, self._frecency_path)
        except Exception as e:
            logger.debug("Could not save frecency cache: %s", e)

    def record_use(self, asset_id: str):
        """Records an asset usage, boosting its frecency score."""
        if not asset_id:
            return
        self._frecency[asset_id] = self._frecency.get(asset_id, 0) + 1
        self._save_frecency()

    def set_page_boost(self, categories: Iterable[str]):
        """Sets the categories of the active DaVinci page to receive +25 score boost."""
        self._page_boost_cats = {str(c).lower() for c in categories}

    def set_assets(self, assets: List[Dict[str, Any]]):
        """Pre-processes assets for ultra-fast indexing."""
        self._assets = []
        for a in assets:
            if isinstance(a, dict):
                item = dict(a)
            else:
                type_name = type(a).__name__
                if type_name == "TextStylePreset":
                    a_cat = "text"
                    a_sub = getattr(a, "category", "") or "Kinetic"
                elif type_name == "TransitionStylePreset":
                    a_cat = "trans"
                    a_sub = getattr(a, "category", "") or "Transition"
                elif getattr(a, "tab", ""):
                    a_cat = getattr(a, "tab", "")
                    a_sub = getattr(a, "category", "") or getattr(a, "sub", "")
                else:
                    a_cat = getattr(a, "category", "")
                    a_sub = getattr(a, "sub", "") or getattr(a, "sub_category", "")

                item = {
                    "id": getattr(a, "id", "") or getattr(a, "name", ""),
                    "name": getattr(a, "name", ""),
                    "category": a_cat,
                    "sub": a_sub,
                    "duration": getattr(a, "duration", 0.0) or getattr(a, "dur", 0.0),
                    "tags": getattr(a, "tags", []) or [],
                    "motion": getattr(a, "motion", "") or getattr(a, "animation", ""),
                    "file_path": getattr(a, "file_path", ""),
                    "obj": a,
                }
            name = str(item.get("name") or item.get("id") or "")
            cat = str(item.get("category") or item.get("cat") or item.get("tab") or "").lower()
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]

            clean_name = strip_accents(name)
            clean_tags = [strip_accents(str(t)) for t in tags]

            # Tokenize for prefix matching
            all_tokens = re.split(r"[\s_\-·&/]+", f"{clean_name} {' '.join(clean_tags)}")
            tokens = [t for t in all_tokens if t]

            # Detect motion type
            motion = str(item.get("motion") or item.get("motion_type") or "").lower()
            conf = float(item.get("confidence") or item.get("conf") or 0.8)

            # Pre-compute clean aliases
            clean_aliases = []
            if motion and motion in ALIAS_MAP:
                clean_aliases = [strip_accents(al) for al in ALIAS_MAP[motion]]

            # Also check name for implicit motion
            if not clean_aliases:
                for m_key, m_list in ALIAS_MAP.items():
                    for al in m_list:
                        cal = strip_accents(al)
                        if cal in clean_name:
                            motion = m_key
                            clean_aliases = [strip_accents(x) for x in m_list]
                            break
                    if clean_aliases:
                        break

            item["_clean_name"] = clean_name
            item["_tokens"] = tokens
            item["_motion"] = motion
            item["_conf"] = conf
            item["_clean_aliases"] = clean_aliases
            item["_cat_norm"] = cat
            self._assets.append(item)

    def search(
        self,
        query: str,
        limit_categories: Optional[Iterable[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Executes query search and returns items sorted by score descending.
        Each item in result contains: item dict, 'score', and 'why'.
        """
        q_raw = query.strip()
        prefix_filter: Optional[Set[str]] = None

        # 1. Parse category prefix (e.g. "s: whoosh", "c: title")
        m_prefix = re.match(r"^([ctsmlf]):\s*", q_raw, flags=re.IGNORECASE)
        if m_prefix:
            prefix_char = m_prefix.group(1).lower()
            prefix_filter = PREFIX_MAP.get(prefix_char)
            q_raw = q_raw[m_prefix.end():].strip()

        clean_q = strip_accents(q_raw)
        q_tokens = [t for t in clean_q.split() if t]

        limit_cats_set = {str(c).lower() for c in limit_categories} if limit_categories else None

        results = []
        for a in self._assets:
            cat = a["_cat_norm"]

            # Filter by prefix or limit_categories
            if prefix_filter and cat not in prefix_filter:
                continue
            if limit_cats_set and cat not in limit_cats_set:
                continue

            # Empty query: return items ranked by page boost + frecency
            if not clean_q:
                base_score = 0.0
                if cat in self._page_boost_cats:
                    base_score += 25.0
                use_cnt = self._frecency.get(str(a.get("id")), 0)
                base_score += min(30.0, use_cnt * 10.0)
                results.append({"asset": a, "score": base_score, "why": ""})
                continue

            score, why = self._score_item(clean_q, q_tokens, a)
            if score > 0:
                results.append({"asset": a, "score": score, "why": why})

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def _score_item(
        self,
        clean_q: str,
        q_tokens: List[str],
        a: Dict[str, Any],
    ) -> Tuple[float, str]:
        """Calculates 7-signal score according to section 5.5."""
        s = 0.0
        motion_hit = False
        why = ""

        clean_name = a["_clean_name"]
        clean_aliases = a["_clean_aliases"]
        tokens = a["_tokens"]

        # 1. Exact alias match ("rung giat" == alias of shake) -> +100
        if clean_aliases and clean_q in clean_aliases:
            s = 100.0
            motion_hit = True
        elif clean_aliases and len(clean_q) >= 3 and any(al.startswith(clean_q) for al in clean_aliases):
            # Prefix of alias (len >= 3) -> +50
            s = 50.0
            motion_hit = True

        # 2. Prefix of item name -> +80
        if clean_name.startswith(clean_q):
            s = max(s, 80.0)

        # 3. Prefix of tokens in name or tags -> +60
        if q_tokens and all(any(tok.startswith(qt) for tok in tokens) for qt in q_tokens):
            s = max(s, 60.0)

        # 4. Fuzzy subsequence match (len >= 3) -> +30
        if s == 0 and len(clean_q) >= 3:
            q_spaceless = clean_q.replace(" ", "")
            name_spaceless = clean_name.replace(" ", "")
            if is_subsequence(q_spaceless, name_spaceless):
                s = 30.0

        # If no signal matched, return 0
        if s <= 0:
            return 0.0, ""

        # 5. Motion confidence score: conf * 20 (only when query matched motion alias)
        if motion_hit and a["_motion"]:
            conf = a["_conf"]
            s += conf * 20.0
            m_label = MOTION_VI.get(a["_motion"], a["_motion"].capitalize())
            why = f"{m_label} {int(conf * 100)}%"

        # 6. Page boost: +25 if in active page's categories
        if a["_cat_norm"] in self._page_boost_cats:
            s += 25.0

        # 7. Frecency boost: 0 to +30
        use_cnt = self._frecency.get(str(a.get("id")), 0)
        s += min(30.0, use_cnt * 10.0)

        return s, why
