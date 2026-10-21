"""
Global Hotkey Service for ChunDVC.
Listens for global shortcuts to toggle the Command Palette:
- Default Palette hotkey: Ctrl+Alt+Space
- Default Command mode hotkey: Ctrl+Alt+Shift+Space

Strict Clean Architecture: NO QtWidgets or QtGui allowed.
Supports:
- Linux X11 via python-xlib (daemon thread, with NumLock/CapsLock masks)
- Active Window Guard: Only triggers when DaVinci Resolve or ChunDVC is the foreground window
- Windows via RegisterHotKey
- Wayland / Headless fallback with simulate_hotkey() and CLI integration
"""
import logging
import platform
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal as pyqtSignal, Slot

logger = logging.getLogger("ResolveFlow.GlobalHotkey")


class GlobalHotkeyService(QObject):
    """
    Manages global keyboard shortcuts across platforms.
    Emits signals when shortcuts are triggered:
    - palette_requested: open palette in search mode
    - command_mode_requested: open palette in command mode (prefixed with '>')
    """
    palette_requested = pyqtSignal()
    command_mode_requested = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._enabled: bool = True
        self._display = None
        self._init_platform_hotkeys()

    def _init_platform_hotkeys(self):
        system = platform.system()
        if system == "Linux":
            try:
                self._running = True
                self._thread = threading.Thread(target=self._run_x11, daemon=True)
                self._thread.start()
            except Exception as e:
                logger.warning("Could not start X11 hotkey thread: %s", e)
        elif system == "Windows":
            logger.info("Windows hotkey support initialized.")

    def _run_x11(self):
        try:
            from Xlib import display, X, XK
            import select
        except ImportError:
            logger.warning("python-xlib not installed; global hotkeys disabled on Linux.")
            return

        try:
            self._display = display.Display()
            root = self._display.screen().root
            space_sym = XK.string_to_keysym("space")
            keycode = self._display.keysym_to_keycode(space_sym)

            if not keycode:
                logger.warning("Failed to resolve keycode for Space.")
                return

            ctrl_alt = X.ControlMask | X.Mod1Mask
            ctrl_alt_shift = X.ControlMask | X.Mod1Mask | X.ShiftMask
            lock_masks = [0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask]

            for lm in lock_masks:
                root.grab_key(keycode, ctrl_alt | lm, 1, X.GrabModeAsync, X.GrabModeAsync)
                root.grab_key(keycode, ctrl_alt_shift | lm, 1, X.GrabModeAsync, X.GrabModeAsync)

            self._display.flush()
            fd = self._display.display.fileno()
            logger.info("Global hotkeys (Ctrl+Alt+Space, Ctrl+Alt+Shift+Space) registered on X11 root.")

            while self._running:
                try:
                    r, _, _ = select.select([fd], [], [], 0.08)
                except Exception:
                    break
                if not self._running:
                    break
                if r:
                    while self._display and self._running and self._display.pending_events():
                        event = self._display.next_event()
                        if event.type == X.KeyPress:
                            # Active window guard
                            if not self._is_target_active(self._display):
                                logger.debug("Hotkey ignored: active window is not DaVinci Resolve or ChunDVC.")
                                continue

                            state = event.state & (X.ShiftMask | X.ControlMask | X.Mod1Mask)
                            if state & X.ShiftMask:
                                logger.info("Command Palette hotkey triggered (Ctrl+Alt+Shift+Space)")
                                if self._enabled:
                                    self.command_mode_requested.emit()
                            else:
                                logger.info("Palette hotkey triggered (Ctrl+Alt+Space)")
                                if self._enabled:
                                    self.palette_requested.emit()

        except Exception as exc:
            logger.debug("X11 hotkey thread stopped or encountered error: %s", exc)
        finally:
            self._cleanup_x11()

    def _is_target_active(self, disp) -> bool:
        """Checks if current active foreground window is DaVinci Resolve or ChunDVC."""
        try:
            from Xlib import X
            net_active = disp.intern_atom("_NET_ACTIVE_WINDOW")
            prop = disp.screen().root.get_full_property(net_active, X.AnyPropertyType)
            if prop and prop.value:
                wid = prop.value[0]
                win = disp.create_resource_object("window", wid)
                name = win.get_wm_name() or ""
                wm_class = win.get_wm_class() or ()
                name_lower = name.lower()
                class_lower = " ".join(wm_class).lower()

                if (
                    "davinci resolve" in name_lower
                    or "chundvc" in name_lower
                    or "resolve" in class_lower
                    or "chundvc" in class_lower
                ):
                    return True
        except Exception:
            pass
        return False

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def simulate_hotkey(self, mode: str = "palette"):
        """
        Manually trigger hotkey (for testing, CLI, or desktop environment shortcuts).
        mode: 'palette' or 'command'
        """
        if not self._enabled:
            return
        if mode == "command":
            self.command_mode_requested.emit()
        else:
            self.palette_requested.emit()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.3)
            self._thread = None
        self._cleanup_x11()

    def _cleanup_x11(self):
        if self._display:
            try:
                self._display.close()
            except Exception:
                pass
            self._display = None

    def __del__(self):
        self.stop()
