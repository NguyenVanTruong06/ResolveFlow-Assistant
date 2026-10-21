"""
Context Profiles dataclass and configuration for the 7 DaVinci Resolve pages.
(UX_REDESIGN.md section 4.1)
"""
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class ContextProfile:
    page: str               # "media", "cut", "edit", "fusion", "color", "fairlight", "deliver"
    label: str              # "Media", "Cut", "Edit", "Fusion", "Color", "Fairlight", "Deliver"
    color: str              # Accent color: Edit #7c3aed, Fusion #06b6d4, Fairlight #ec4899, Color #f59e0b, Cut/Deliver/Media #a8a8b3
    categories: List[str]   # Categories shown in category nav
    primary_action: str     # Main action label on sticky action bar
    special_block: str      # "media", "pads", "suggest", "capture", "color", "fairlight", "deliver"


PAGES: List[str] = ["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"]

PROFILES: Dict[str, ContextProfile] = {
    "media": ContextProfile(
        page="media",
        label="Media",
        color="#a8a8b3",
        categories=[],
        primary_action="Phân tích footage đã chọn",
        special_block="media",
    ),
    "cut": ContextProfile(
        page="cut",
        label="Cut",
        color="#a8a8b3",
        categories=["sfx", "meme", "text"],
        primary_action="Chèn tại playhead",
        special_block="pads",
    ),
    "edit": ContextProfile(
        page="edit",
        label="Edit",
        color="#7c3aed",
        categories=["text", "trans", "meme", "overlay", "sfx"],
        primary_action="Chèn tại playhead",
        special_block="suggest",
    ),
    "fusion": ContextProfile(
        page="fusion",
        label="Fusion",
        color="#06b6d4",
        categories=["text", "trans", "overlay", "mine"],
        primary_action="Dán vào Fusion",
        special_block="capture",
    ),
    "color": ContextProfile(
        page="color",
        label="Color",
        color="#f59e0b",
        categories=["lut"],
        primary_action="Áp dụng cho clip",
        special_block="color",
    ),
    "fairlight": ContextProfile(
        page="fairlight",
        label="Fairlight",
        color="#ec4899",
        categories=["sfx", "bgm"],
        primary_action="Chèn vào track audio",
        special_block="fairlight",
    ),
    "deliver": ContextProfile(
        page="deliver",
        label="Deliver",
        color="#a8a8b3",
        categories=[],
        primary_action="Bắt đầu kết xuất",
        special_block="deliver",
    ),
}
