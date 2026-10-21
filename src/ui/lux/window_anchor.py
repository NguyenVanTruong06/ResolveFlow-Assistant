"""
DaVinci Resolve Window Anchor (Learning from Akitt Pro 4.0).
Detects the DaVinci Resolve main window position across platforms (Linux & Windows)
and calculates docked coordinates for the edge drawer.
"""
import os
import sys
import platform
import subprocess
import re
from typing import Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal as pyqtSignal


def _parse_wmctrl_output(output: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Parses `wmctrl -l -G` output for DaVinci Resolve.
    Format: <id> <desktop> <x> <y> <w> <h> <hostname> <title...>
    """
    if not output:
        return None

    for line in output.strip().splitlines():
        if "davinci resolve" in line.lower():
            parts = line.split(None, 7)
            if len(parts) >= 7:
                try:
                    x = int(parts[2])
                    y = int(parts[3])
                    w = int(parts[4])
                    h = int(parts[5])
                    return (x, y, w, h)
                except (ValueError, IndexError):
                    continue
    return None


def get_resolve_window_rect() -> Optional[Tuple[int, int, int, int]]:
    """
    Returns (x, y, width, height) of the active DaVinci Resolve window,
    or None if not found / running headlessly.
    """
    system = platform.system()

    if system == "Linux":
        # Try wmctrl
        try:
            res = subprocess.run(
                ["wmctrl", "-l", "-G"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                rect = _parse_wmctrl_output(res.stdout)
                if rect:
                    return rect
        except Exception:
            pass

        # Try xdotool
        try:
            res = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "DaVinci Resolve"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                wid = res.stdout.strip().splitlines()[0]
                geom = subprocess.run(
                    ["xdotool", "getwindowgeometry", "--shell", wid],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if geom.returncode == 0:
                    data = {}
                    for gline in geom.stdout.splitlines():
                        if "=" in gline:
                            k, v = gline.split("=", 1)
                            data[k.strip()] = int(v.strip())
                    if "X" in data and "Y" in data and "WIDTH" in data and "HEIGHT" in data:
                        return (data["X"], data["Y"], data["WIDTH"], data["HEIGHT"])
        except Exception:
            pass

    elif system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found_rect = None

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_windows_callback(hwnd, lparam):
                nonlocal found_rect
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value
                if "DaVinci Resolve" in title:
                    r = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                        found_rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
                        return False
                return True

            user32.EnumWindows(enum_windows_callback, 0)
            if found_rect:
                return found_rect
        except Exception:
            pass

    return None


def calculate_dock_geometry(
    anchor_rect: Optional[Tuple[int, int, int, int]],
    screen_rect: Tuple[int, int, int, int],
    drawer_width: int,
    edge: str = "right",
) -> Tuple[int, int, int, int]:
    """
    Computes (x, y, width, height) to dock the drawer flush against the anchor.
    Falls back gracefully to screen bounds if anchor is None.
    """
    sx, sy, sw, sh = screen_rect

    if anchor_rect:
        ax, ay, aw, ah = anchor_rect
        # Clip anchor to screen bounds
        ay = max(sy, ay)
        ah = min(sh, ah)
        if edge == "right":
            x = ax + aw - drawer_width
        else:
            x = ax
        y = ay
        h = ah
    else:
        # Fallback to screen edge
        if edge == "right":
            x = sx + sw - drawer_width
        else:
            x = sx
        y = sy
        h = sh

    return (int(x), int(y), int(drawer_width), int(h))


class ResolveWindowAnchor(QObject):
    """
    Background tracker that monitors DaVinci Resolve's window coordinates
    and emits `anchor_updated` whenever position or size shifts.
    """
    anchor_updated = pyqtSignal(tuple)

    def __init__(self, poll_interval_ms: int = 1500, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.poll_interval_ms = poll_interval_ms
        self._last_rect: Optional[Tuple[int, int, int, int]] = None

        self._timer = QTimer(self)
        self._timer.setInterval(self.poll_interval_ms)
        self._timer.timeout.connect(self._check_anchor)

    def get_rect(self) -> Optional[Tuple[int, int, int, int]]:
        return get_resolve_window_rect()

    def start(self):
        self._check_anchor()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _check_anchor(self):
        current = self.get_rect()
        if current != self._last_rect:
            self._last_rect = current
            if current is not None:
                self.anchor_updated.emit(current)
