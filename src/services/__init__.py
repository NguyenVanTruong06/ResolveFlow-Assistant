"""
Package src.services - Core backend services for ResolveFlow / ChunDVC.
Follows Clean Architecture: Strictly NO QtWidgets / QtGui imports allowed.
"""

from src.services.resolve_gateway import ResolveGateway
from src.services.resolve_state import ResolveStateService
from src.services.insert_service import InsertService, InsertResult
from src.services.search_index import SearchIndex
from src.services.global_hotkey import GlobalHotkeyService
from src.services.multi_screen_service import MultiScreenService, get_multi_screen_service

__all__ = [
    "ResolveGateway",
    "ResolveStateService",
    "InsertService",
    "InsertResult",
    "SearchIndex",
    "GlobalHotkeyService",
    "MultiScreenService",
    "get_multi_screen_service",
]
