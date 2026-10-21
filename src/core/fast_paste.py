"""
Fast-Paste into Fusion Engine (Learning from Akitt Pro 4.0 FAST-FUSION).
Copies Fusion Lua/Macro text to system clipboard, brings DaVinci Resolve into focus,
and simulates Ctrl+V to instantiate nodes directly on the Fusion graph.
"""
import os
import sys
import time
import platform
import subprocess
from typing import Dict, Any, Optional

from PySide6.QtWidgets import QApplication


def focus_davinci_resolve() -> bool:
    """Brings the DaVinci Resolve window to the foreground."""
    system = platform.system()

    if system == "Linux":
        # Try wmctrl first
        try:
            res = subprocess.run(
                ["wmctrl", "-a", "DaVinci Resolve"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

        # Try xdotool
        try:
            res = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", "DaVinci Resolve", "windowactivate"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    elif system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found_hwnd = None

            # Quick check with FindWindowW
            if hasattr(user32, "FindWindowW"):
                try:
                    hwnd = user32.FindWindowW(None, "DaVinci Resolve")
                    if hwnd:
                        user32.SetForegroundWindow(hwnd)
                        return True
                except Exception:
                    pass

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def enum_cb(hwnd, lparam):
                nonlocal found_hwnd
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "DaVinci Resolve" in buf.value:
                    found_hwnd = hwnd
                    return False
                return True

            user32.EnumWindows(enum_cb, 0)
            if found_hwnd:
                user32.SetForegroundWindow(found_hwnd)
                return True
        except Exception:
            pass

    elif system == "Darwin":
        try:
            res = subprocess.run(
                ["osascript", "-e", 'tell application "DaVinci Resolve" to activate'],
                capture_output=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    return False


def simulate_paste_hotkey() -> bool:
    """Simulates Ctrl+V (or Cmd+V on macOS) to paste clipboard contents."""
    system = platform.system()

    if system == "Linux":
        try:
            res = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # Try pyautogui as cross-platform fallback
    try:
        import pyautogui
        if system == "Darwin":
            pyautogui.hotkey("command", "v")
        else:
            pyautogui.hotkey("ctrl", "v")
        return True
    except Exception:
        pass

    if system == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # VK_CONTROL = 0x11, 'V' = 0x56
            user32.keybd_event(0x11, 0, 0, 0)
            user32.keybd_event(0x56, 0, 0, 0)
            user32.keybd_event(0x56, 0, 2, 0)
            user32.keybd_event(0x11, 0, 2, 0)
            return True
        except Exception:
            pass

    return False


def fast_paste_to_fusion(
    macro_code: str,
    resolve_app: Optional[Any] = None,
    log_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Executes the Akitt Pro FAST-FUSION pipeline:
    1. Copies the Macro/Tool Lua code into the system clipboard.
    2. Switches DaVinci Resolve to the Fusion page (if available).
    3. Focuses the DaVinci Resolve window.
    4. Simulates Ctrl+V to paste the nodes directly into the Fusion node graph.
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    if not macro_code or not macro_code.strip():
        return {"ok": False, "error": "empty_code", "message": "Nội dung Macro code trống."}

    # Step 1: Set clipboard
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        clipboard = QApplication.clipboard()
        clipboard.setText(macro_code)
    except Exception as e:
        return {"ok": False, "error": "clipboard_error", "message": f"Lỗi nạp clipboard: {e}"}

    # Step 2: Ensure Fusion page
    if resolve_app is None:
        try:
            from src.core.resolve_bridge import load_resolve_api
            resolve_app, _ = load_resolve_api()
        except Exception:
            pass

    if resolve_app:
        try:
            cur_page = str(resolve_app.GetCurrentPage() or "").lower()
            if cur_page != "fusion":
                log(" 🔄 Đang chuyển DaVinci Resolve sang trang Fusion...")
                resolve_app.OpenPage("fusion")
                time.sleep(0.1)
        except Exception:
            pass

    # Step 3: Focus DaVinci Resolve
    log(" 🎯 Đang chuyển tiêu điểm sang cửa sổ DaVinci Resolve...")
    focused = focus_davinci_resolve()
    time.sleep(0.06)

    # Step 4: Simulate Ctrl+V
    log(" ⚡ Đang dán tự động chùm node Fusion (Ctrl + V)...")
    pasted = simulate_paste_hotkey()

    return {
        "ok": True,
        "mode": "fusion",
        "focused": focused,
        "pasted": pasted,
        "message": "Đã copy và dán chùm node vào trang Fusion! (Nếu chưa thấy, bạn có thể ấn Ctrl+V trên Resolve)",
    }
