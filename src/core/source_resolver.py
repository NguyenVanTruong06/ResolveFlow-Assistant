"""
Bộ kiểm tra & giải quyết nguồn tài nguyên (Source Resolver & Asset Matcher).
Tự động đối soát video gốc, meme B-roll và hiệu ứng âm thanh SFX khi nạp kịch bản JSON.
Tự động tìm kiếm nguồn thay thế tương đương trong kho khi phát hiện tệp thiếu.
"""

import os
import re
import copy
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from src.core.story_copilot import CopilotDirectorPlan, CopilotMediaInsert


@dataclass
class SourceAuditResult:
    """Kết quả đối soát tính sẵn sàng của toàn bộ nguồn kịch bản."""
    found_footage: List[Dict[str, Any]] = field(default_factory=list)
    missing_footage: List[Dict[str, Any]] = field(default_factory=list)
    found_memes: List[Dict[str, Any]] = field(default_factory=list)
    missing_memes: List[Dict[str, Any]] = field(default_factory=list)
    found_sfx: List[Dict[str, Any]] = field(default_factory=list)
    missing_sfx: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def footage_readiness(self) -> float:
        total = len(self.found_footage) + len(self.missing_footage)
        if total == 0:
            return 100.0
        return round(len(self.found_footage) / total * 100.0, 1)

    @property
    def asset_readiness(self) -> float:
        total = (len(self.found_memes) + len(self.missing_memes) + 
                 len(self.found_sfx) + len(self.missing_sfx))
        if total == 0:
            return 100.0
        found = len(self.found_memes) + len(self.found_sfx)
        return round(found / total * 100.0, 1)

    @property
    def total_readiness(self) -> float:
        total_items = (len(self.found_footage) + len(self.missing_footage) +
                       len(self.found_memes) + len(self.missing_memes) +
                       len(self.found_sfx) + len(self.missing_sfx))
        if total_items == 0:
            return 100.0
        found_items = len(self.found_footage) + len(self.found_memes) + len(self.found_sfx)
        return round(found_items / total_items * 100.0, 1)


class SourceResolver:
    """Bộ não đối soát nguồn và tự động giải quyết các tài nguyên thiếu."""

    def __init__(self, available_videos: Optional[List[str]] = None, assets_dir: Optional[str] = None):
        self.available_videos = available_videos or []
        if assets_dir:
            self.assets_dir = assets_dir
        else:
            self.assets_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "assets")
            )

    def _index_directory(self, folder_path: str) -> Dict[str, str]:
        """Tạo từ điển ánh xạ {lowercase_filename: full_path}."""
        mapping = {}
        if not os.path.exists(folder_path):
            return mapping
        for root, _, files in os.walk(folder_path):
            for f in files:
                mapping[f.lower()] = os.path.join(root, f)
        return mapping

    def audit_plan(self, plan: CopilotDirectorPlan) -> SourceAuditResult:
        """Kiểm tra xem toàn bộ clip, meme và SFX trong plan đã có trên đĩa chưa."""
        result = SourceAuditResult()

        # 1. Kiểm tra Video Gốc (Footage)
        video_map = {os.path.basename(v).lower(): v for v in self.available_videos}
        
        # Kiểm tra Global Hook nếu có
        if plan.global_hook and plan.global_hook.clip_name:
            h_name = plan.global_hook.clip_name
            if h_name.lower() in video_map:
                result.found_footage.append({
                    "role": "hook",
                    "clip_name": h_name,
                    "path": video_map[h_name.lower()]
                })
            else:
                result.missing_footage.append({
                    "role": "hook",
                    "clip_name": h_name
                })

        for seg in plan.timeline_segments:
            c_name = seg.clip_name
            if c_name.lower() in video_map:
                result.found_footage.append({
                    "clip_index": seg.clip_index,
                    "clip_name": c_name,
                    "path": video_map[c_name.lower()]
                })
            else:
                result.missing_footage.append({
                    "clip_index": seg.clip_index,
                    "clip_name": c_name
                })

        # 2. Kiểm tra Meme & B-Roll
        broll_dir = os.path.join(self.assets_dir, "broll_memes")
        meme_map = self._index_directory(broll_dir) if os.path.exists(broll_dir) else self._index_directory(self.assets_dir)

        for b in plan.broll_inserts:
            af = b.asset_file.strip()
            if af.lower() in meme_map:
                result.found_memes.append({
                    "timeline_sec": b.timeline_sec,
                    "asset_file": af,
                    "path": meme_map[af.lower()],
                    "description": b.description
                })
            else:
                result.missing_memes.append({
                    "timeline_sec": b.timeline_sec,
                    "asset_file": af,
                    "description": b.description
                })

        # 3. Kiểm tra SFX
        sfx_dir = os.path.join(self.assets_dir, "sfx")
        sfx_map = self._index_directory(sfx_dir) if os.path.exists(sfx_dir) else self._index_directory(self.assets_dir)

        for s in plan.sfx_inserts:
            af = s.asset_file.strip()
            if af.lower() in sfx_map:
                result.found_sfx.append({
                    "timeline_sec": s.timeline_sec,
                    "asset_file": af,
                    "path": sfx_map[af.lower()],
                    "description": s.description
                })
            else:
                result.missing_sfx.append({
                    "timeline_sec": s.timeline_sec,
                    "asset_file": af,
                    "description": s.description
                })

        return result

    def auto_resolve_missing_assets(self, plan: CopilotDirectorPlan) -> CopilotDirectorPlan:
        """
        Tự động giải quyết các asset còn thiếu bằng cách tìm file tương đương
        có sẵn trong kho dữ liệu cục bộ hoặc starter pack.
        """
        resolved_plan = copy.deepcopy(plan)

        sfx_dir = os.path.join(self.assets_dir, "sfx")
        broll_dir = os.path.join(self.assets_dir, "broll_memes")

        sfx_map = self._index_directory(sfx_dir) if os.path.exists(sfx_dir) else self._index_directory(self.assets_dir)
        meme_map = self._index_directory(broll_dir) if os.path.exists(broll_dir) else self._index_directory(self.assets_dir)

        # 1. Giải quyết SFX còn thiếu
        sfx_synonyms = {
            "whoosh": ["whoosh", "swish", "transition", "slide"],
            "braam": ["braam", "boom", "dramatic", "hit", "bass"],
            "boom": ["boom", "vine_boom", "bass", "thud"],
            "cuoi": ["cuoi", "laugh", "funny", "comedy", "haha"],
            "bell": ["bell", "ding", "pop", "bubble", "ping"],
            "ding": ["ding", "bell", "pop", "chime"],
            "scratch": ["scratch", "glitch", "error", "pop"],
            "buzzer": ["buzzer", "error", "wrong", "fail"],
            "fanfare": ["fanfare", "tada", "win", "bell", "success"],
            "airhorn": ["airhorn", "horn", "party", "boom"],
            "cash": ["cash", "ching", "coin", "ding", "money"],
            "coi": ["coi", "horn", "beep", "car"],
        }

        for s in resolved_plan.sfx_inserts:
            af = s.asset_file.strip().lower()
            if af not in sfx_map:
                # Tìm từ đồng nghĩa phù hợp
                matched_file = None
                search_pool = list(sfx_map.keys())

                # Thử tìm theo nhóm từ khóa
                for key_cat, syns in sfx_synonyms.items():
                    if any(syn in af or syn in s.description.lower() for syn in syns):
                        for candidate in search_pool:
                            if any(syn in candidate for syn in syns):
                                matched_file = candidate
                                break
                    if matched_file:
                        break

                # Nếu vẫn chưa tìm thấy, lấy file SFX đầu tiên trong kho làm fallback
                if not matched_file and search_pool:
                    matched_file = search_pool[0]

                if matched_file:
                    s.asset_file = os.path.basename(sfx_map[matched_file])

        # 2. Giải quyết Meme còn thiếu
        meme_synonyms = {
            "mixi": ["mixi", "do_mixi", "troll", "laugh", "funny"],
            "facepalm": ["facepalm", "toang", "thua", "sad"],
            "nit": ["nit", "dung_cai_nit", "tien_bip", "toang"],
            "balo": ["balo", "xach_balo", "di", "travel"],
            "wow": ["wow", "surprised", "shock"],
            "cat": ["cat", "meo", "vibing"],
        }

        for b in resolved_plan.broll_inserts:
            af = b.asset_file.strip().lower()
            if af not in meme_map:
                matched_file = None
                search_pool = list(meme_map.keys())

                for key_cat, syns in meme_synonyms.items():
                    if any(syn in af or syn in b.description.lower() for syn in syns):
                        for candidate in search_pool:
                            if any(syn in candidate for syn in syns):
                                matched_file = candidate
                                break
                    if matched_file:
                        break

                if not matched_file and search_pool:
                    matched_file = search_pool[0]

                if matched_file:
                    b.asset_file = os.path.basename(meme_map[matched_file])

        return resolved_plan
