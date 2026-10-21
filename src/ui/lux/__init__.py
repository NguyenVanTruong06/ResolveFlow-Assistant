"""
ChunDVC Luxury Dark UI Subsystem (Learning from Akitt Pro 4.0).
Provides Floating Edge Drawer, Micro-Interactions, MacroCard Grid, and Resolve Window Anchor.
"""

from .lux_effects import RippleOverlay, SegmentIndicator, get_luxury_dark_stylesheet
from .macro_card import MacroCard, MacroGridWidget
from .window_anchor import ResolveWindowAnchor, get_resolve_window_rect, calculate_dock_geometry
from .shell import EdgeDockWindow, DockState, DockStateMachine, ChunDVCTrayIcon
from .shell.tray import USE_NEW_EDGE_DOCK, SHOW_FLOATING_BUBBLE
from .palette import CommandPaletteWindow, CommandRegistry, PaletteSearchProvider
from .feedback import ChunDVCToast
from src.ui.director import DirectorWindow, DirectorController, DirectorConfig

__all__ = [
    "RippleOverlay",
    "SegmentIndicator",
    "get_luxury_dark_stylesheet",
    "MacroCard",
    "MacroGridWidget",
    "ResolveWindowAnchor",
    "get_resolve_window_rect",
    "calculate_dock_geometry",
    "EdgeDockWindow",
    "DockState",
    "DockStateMachine",
    "ChunDVCTrayIcon",
    "USE_NEW_EDGE_DOCK",
    "SHOW_FLOATING_BUBBLE",
    "CommandPaletteWindow",
    "CommandRegistry",
    "PaletteSearchProvider",
    "ChunDVCToast",
    "DirectorWindow",
    "DirectorController",
    "DirectorConfig",
]

