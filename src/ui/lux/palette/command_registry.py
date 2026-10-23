"""
Command Registry for Quick Palette system commands (prefixed with '>').
(UX_REDESIGN.md Section 5.3)
"""
from dataclasses import dataclass
from typing import Callable, Optional, List, Dict
import logging

logger = logging.getLogger("ResolveFlow.CommandRegistry")


@dataclass
class CommandItem:
    id: str
    name: str                   # e.g. ">cắt khoảng lặng", ">render tiktok"
    meta: str                   # e.g. "Mở Director AutoCut", "Thêm vào queue và render"
    callback: Optional[Callable] = None
    page_target: Optional[str] = None


class CommandRegistry:
    """
    Registry for Quick Palette system commands.
    Allows external modules (Director, Render, ContextRouter, Dock) to register dynamic commands.
    """
    def __init__(self):
        self._commands: Dict[str, CommandItem] = {}
        self._register_default_commands()

    def register(self, command: CommandItem):
        self._commands[command.id] = command

    def unregister(self, cmd_id: str):
        self._commands.pop(cmd_id, None)

    def get(self, cmd_id: str) -> Optional[CommandItem]:
        return self._commands.get(cmd_id)

    def all_commands(self) -> List[CommandItem]:
        return list(self._commands.values())

    def search(self, query: str, limit: int = 7) -> List[CommandItem]:
        """
        Searches commands matching query.
        Handles search strings with or without leading '>'.
        """
        q = (query or "").lower().strip()
        if q.startswith(">"):
            q = q[1:].strip()

        if not q:
            return list(self._commands.values())[:limit]

        # Simple rank: starts with > contains
        matched = []
        for cmd in self._commands.values():
            cmd_clean = cmd.name.lower().replace(">", "").strip()
            meta_clean = cmd.meta.lower()
            if q in cmd_clean or q in meta_clean:
                score = 100 if cmd_clean.startswith(q) else 50
                matched.append((score, cmd))

        matched.sort(key=lambda x: x[0], reverse=True)
        return [cmd for _, cmd in matched][:limit]

    def _register_default_commands(self):
        defaults = [
            CommandItem(
                id="cmd_instant_autocut",
                name=">cắt khoảng lặng 1-click",
                meta="⚡ AutoCut tức thì: Cắt khoảng lặng trên Timeline đang mở & co sát clip (0.3s)",
            ),
            CommandItem(
                id="cmd_autocut",
                name=">cắt khoảng lặng (director)",
                meta="Mở Director: tách thoại Whisper & cắt khoảng lặng (AutoCut)",
            ),
            CommandItem(
                id="cmd_director",
                name=">mở director",
                meta="Mở trung tâm ChunDVC Director (AutoCut + AI Copilot)",
            ),
            CommandItem(
                id="cmd_render_tt",
                name=">render tiktok",
                meta="Thêm vào hàng đợi & kết xuất TikTok 9:16 (1080×1920 60fps)",
                page_target="deliver",
            ),
            CommandItem(
                id="cmd_render_yt",
                name=">render youtube",
                meta="Thêm vào hàng đợi & kết xuất YouTube 1080p (1920×1080 60fps)",
                page_target="deliver",
            ),
            CommandItem(
                id="cmd_loudness",
                name=">chuẩn hóa loudness",
                meta="Fairlight: Chuẩn hóa loudness YouTube −14 LUFS (Track A1)",
                page_target="fairlight",
            ),
            CommandItem(
                id="cmd_pin_dock",
                name=">ghim / bỏ ghim dock",
                meta="Edge Dock: Bật/tắt ghim dock cố định trên màn hình",
            ),
            CommandItem(
                id="cmd_settings",
                name=">cài đặt thanh dock",
                meta="Cài đặt: Đổi vị trí neo bên trái / phải, phím tắt & hành vi",
            ),
            # DaVinci resolve page navigation
            CommandItem(id="nav_edit", name=">đi tới trang edit", meta="DaVinci Resolve: Chuyển sang trang Edit", page_target="edit"),
            CommandItem(id="nav_fusion", name=">đi tới trang fusion", meta="DaVinci Resolve: Chuyển sang trang Fusion", page_target="fusion"),
            CommandItem(id="nav_color", name=">đi tới trang color", meta="DaVinci Resolve: Chuyển sang trang Color", page_target="color"),
            CommandItem(id="nav_fairlight", name=">đi tới trang fairlight", meta="DaVinci Resolve: Chuyển sang trang Fairlight", page_target="fairlight"),
            CommandItem(id="nav_cut", name=">đi tới trang cut", meta="DaVinci Resolve: Chuyển sang trang Cut", page_target="cut"),
            CommandItem(id="nav_deliver", name=">đi tới trang deliver", meta="DaVinci Resolve: Chuyển sang trang Deliver", page_target="deliver"),
            CommandItem(id="nav_media", name=">đi tới trang media", meta="DaVinci Resolve: Chuyển sang trang Media", page_target="media"),
        ]
        for cmd in defaults:
            self.register(cmd)
