"""
Drag & Drop Over Resolve Detection (Learning from Akitt Pro 4.0 Native Drop).
Determines whether a mouse release event occurred over the DaVinci Resolve workspace window.
"""
from typing import Optional, Tuple
from PySide6.QtCore import QPoint
from PySide6.QtGui import QCursor

from .window_anchor import get_resolve_window_rect


def is_point_inside_rect(point: QPoint, rect: Tuple[int, int, int, int]) -> bool:
    """Checks whether `point` lies inside the bounding box (x, y, width, height)."""
    rx, ry, rw, rh = rect
    px = point.x()
    py = point.y()
    return (rx <= px <= rx + rw) and (ry <= py <= ry + rh)


def is_mouse_over_resolve(global_pos: Optional[QPoint] = None) -> bool:
    """
    Returns True if the current global cursor position lies over DaVinci Resolve's main window.
    """
    pos = global_pos if global_pos is not None else QCursor.pos()
    rect = get_resolve_window_rect()
    if not rect:
        return False
    return is_point_inside_rect(pos, rect)
