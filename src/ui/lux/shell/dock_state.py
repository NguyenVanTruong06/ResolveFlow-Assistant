"""
Dock State Machine managing transitions, debounce timers, and geometry rules.
"""
from enum import Enum
from typing import Optional
from PySide6.QtCore import QObject, QTimer, Signal as pyqtSignal


class DockState(str, Enum):
    HIDDEN = "hidden"
    STRIP = "strip"
    RAIL = "rail"
    PEEK = "peek"
    OPEN = "open"
    PINNED = "pinned"
    WIDE = "wide"


class DockStateMachine(QObject):
    """
    State machine for EdgeDock:
    - Hidden: 0 (or 2px indicator)
    - Strip: 28px mini strip
    - Rail: 44px
    - Peek: 56px (44px + 12px)
    - Open: clamp(320, 30% resolve width, 420px)
    - Pinned: 420px fixed
    - Wide: 760px
    """
    state_changed = pyqtSignal(DockState)
    width_requested = pyqtSignal(int, int)  # target_width, duration_ms

    STRIP_WIDTH = 28
    RAIL_WIDTH = 44
    PEEK_WIDTH = 56
    DEFAULT_OPEN_WIDTH = 420
    MIN_OPEN_WIDTH = 320
    WIDE_WIDTH = 760

    # Timing constants (ms) - Tối ưu chống giật/nhạy quá mức
    PEEK_HOVER_DELAY = 180
    OPEN_HOVER_DELAY = 450
    PEEK_LEAVE_DELAY = 350
    OPEN_LEAVE_DELAY = 900
    OPEN_ANIM_DURATION = 200
    CLOSE_ANIM_DURATION = 160

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._state = DockState.RAIL
        self._is_pinned = False
        self._is_wide = False
        self._has_search_text = False
        self._is_sheet_open = False
        self._is_dragging = False
        self._is_menu_open = False
        self._is_in_dock = False
        self._resolve_width = 1920

        # Debounce timers
        self._peek_timer = QTimer(self)
        self._peek_timer.setSingleShot(True)
        self._peek_timer.timeout.connect(self._on_peek_timeout)

        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(self._on_open_timeout)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._on_leave_timeout)

    @property
    def state(self) -> DockState:
        return self._state

    @property
    def is_pinned(self) -> bool:
        return self._is_pinned

    @property
    def is_wide(self) -> bool:
        return self._is_wide

    @property
    def is_open(self) -> bool:
        return self._state in (DockState.OPEN, DockState.PINNED, DockState.WIDE)

    def set_resolve_width(self, width: int):
        self._resolve_width = max(640, width)
        if self.is_open and not self._is_wide:
            target_w = self.calculate_open_width()
            self.width_requested.emit(target_w, 0)

    def calculate_open_width(self) -> int:
        if self._is_wide:
            return self.WIDE_WIDTH
        target = int(self._resolve_width * 0.30)
        return max(self.MIN_OPEN_WIDTH, min(target, self.DEFAULT_OPEN_WIDTH))

    def get_width_for_state(self, state: DockState) -> int:
        if state == DockState.HIDDEN:
            return 2
        elif state == DockState.STRIP:
            return self.STRIP_WIDTH
        elif state == DockState.RAIL:
            return self.RAIL_WIDTH
        elif state == DockState.PEEK:
            return self.PEEK_WIDTH
        elif state == DockState.WIDE:
            return self.WIDE_WIDTH
        else:  # OPEN or PINNED
            return self.calculate_open_width()

    # Guards
    def can_auto_collapse(self) -> bool:
        if self._is_pinned:
            return False
        if self._has_search_text:
            return False
        if self._is_sheet_open:
            return False
        if self._is_dragging:
            return False
        if self._is_menu_open:
            return False
        if self._is_in_dock:
            return False
        return True

    # Events from UI
    def on_mouse_enter_rail(self):
        self._is_in_dock = True
        self._leave_timer.stop()
        if not self.is_open:
            self._peek_timer.start(self.PEEK_HOVER_DELAY)
            self._open_timer.start(self.OPEN_HOVER_DELAY)

    def on_mouse_enter_dock(self):
        self._is_in_dock = True
        self._leave_timer.stop()
        self._peek_timer.stop()

    def on_mouse_leave_dock(self):
        self._is_in_dock = False
        self._peek_timer.stop()
        self._open_timer.stop()

        if self._state == DockState.PEEK:
            self._leave_timer.start(self.PEEK_LEAVE_DELAY)
        elif self.is_open:
            self._leave_timer.start(self.OPEN_LEAVE_DELAY)

    def on_search_text_changed(self, text: str):
        self._has_search_text = bool(text.strip())

    def set_sheet_open(self, open_state: bool):
        self._is_sheet_open = open_state

    def set_dragging(self, dragging: bool):
        self._is_dragging = dragging

    def set_menu_open(self, menu_open: bool):
        self._is_menu_open = menu_open

    # Actions
    def open_dock(self, animated: bool = True):
        self._peek_timer.stop()
        self._open_timer.stop()
        self._leave_timer.stop()

        new_state = DockState.WIDE if self._is_wide else (DockState.PINNED if self._is_pinned else DockState.OPEN)
        self._transition_to(new_state, animated=animated)

    def collapse_dock(self, to_hidden: bool = False, to_strip: bool = False, animated: bool = True):
        self._peek_timer.stop()
        self._open_timer.stop()
        self._leave_timer.stop()
        if to_hidden:
            target_state = DockState.HIDDEN
        elif to_strip:
            target_state = DockState.STRIP
        else:
            target_state = DockState.RAIL
        self._transition_to(target_state, animated=animated)

    def toggle_dock(self, animated: bool = True):
        if self.is_open:
            self.collapse_dock(to_strip=True, animated=animated)
        else:
            self.open_dock(animated=animated)

    def toggle_pin(self) -> bool:
        self._is_pinned = not self._is_pinned
        if self._is_pinned:
            if not self.is_open:
                self.open_dock()
            else:
                self._state = DockState.PINNED
                self.state_changed.emit(self._state)
        else:
            if self._state == DockState.PINNED:
                self._state = DockState.OPEN
                self.state_changed.emit(self._state)
        return self._is_pinned

    def toggle_wide(self) -> bool:
        self._is_wide = not self._is_wide
        if self._is_wide:
            self._transition_to(DockState.WIDE, animated=True)
        else:
            new_state = DockState.PINNED if self._is_pinned else DockState.OPEN
            self._transition_to(new_state, animated=True)
        return self._is_wide

    def _on_peek_timeout(self):
        if not self.is_open and self._is_in_dock:
            self._transition_to(DockState.PEEK, animated=True)

    def _on_open_timeout(self):
        if not self.is_open and self._is_in_dock:
            self.open_dock(animated=True)

    def _on_leave_timeout(self):
        if not self.can_auto_collapse():
            return
        self.collapse_dock(to_strip=True, animated=True)

    def _transition_to(self, new_state: DockState, animated: bool = True):
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        target_width = self.get_width_for_state(new_state)

        duration = 0
        if animated:
            # Opening or expanding: 200ms OutCubic; Collapsing: 160ms InCubic
            is_expanding = target_width > self.get_width_for_state(old_state)
            duration = self.OPEN_ANIM_DURATION if is_expanding else self.CLOSE_ANIM_DURATION

        self.state_changed.emit(self._state)
        self.width_requested.emit(target_width, duration)
