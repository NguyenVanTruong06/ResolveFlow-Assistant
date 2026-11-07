"""
ChunDVC Shell package: Edge Dock, Header, Rail, Status Bar, and System Tray.
"""
from .dock_state import DockState, DockStateMachine
from .dock_rail import DockRailWidget
from .dock_header import DockHeaderWidget
from .dock_status import DockStatusBar
from .edge_peek_handle import EdgePeekHandle
from .edge_dock import EdgeDockWindow
from .tray import ChunDVCTrayIcon

__all__ = [
    "DockState",
    "DockStateMachine",
    "DockRailWidget",
    "DockHeaderWidget",
    "DockStatusBar",
    "EdgePeekHandle",
    "EdgeDockWindow",
    "ChunDVCTrayIcon",
]
