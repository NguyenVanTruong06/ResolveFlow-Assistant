"""
ChunDVC Luxury Dark UI Subsystem (Learning from Akitt Pro 4.0).
Provides Floating Edge Drawer, Micro-Interactions, MacroCard Grid, and Resolve Window Anchor.
"""

from .lux_effects import RippleOverlay, SegmentIndicator, get_luxury_dark_stylesheet
from .macro_card import MacroCard, MacroGridWidget
from .window_anchor import ResolveWindowAnchor, get_resolve_window_rect, calculate_dock_geometry
from .panel_edge_rail import EdgeDrawerWindow

__all__ = [
    "RippleOverlay",
    "SegmentIndicator",
    "get_luxury_dark_stylesheet",
    "MacroCard",
    "MacroGridWidget",
    "ResolveWindowAnchor",
    "get_resolve_window_rect",
    "calculate_dock_geometry",
    "EdgeDrawerWindow",
]
