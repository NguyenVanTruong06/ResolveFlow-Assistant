"""
Context Router managing page transitions, deferral rules, profile memory, and locking.
(UX_REDESIGN.md section 4.2)
"""
import logging
from typing import Optional, Dict, Any, List
from PySide6.QtCore import QObject, Signal as pyqtSignal

from .context_profiles import ContextProfile, PROFILES, PAGES

logger = logging.getLogger("ChunDVC.ContextRouter")


class ProfileMemory:
    """Remembers active category, subcategory, and vertical scroll position per page."""
    def __init__(self):
        self._mem: Dict[str, Dict[str, Any]] = {}

    def save(self, page: str, category: Optional[str], subcategory: Optional[str], scroll_y: int):
        self._mem[page] = {
            "category": category,
            "subcategory": subcategory or "Tất cả",
            "scroll_y": scroll_y,
        }

    def get(self, page: str) -> Optional[Dict[str, Any]]:
        return self._mem.get(page)


class ContextRouter(QObject):
    """
    Routes DaVinci Resolve page changes to Dock context profiles.
    Rules:
    - If user is interacting inside Dock (mouse inside, search has text, sheet open, dragging):
      defer profile switch and show pending chip ('Fusion ›').
    - Apply pending profile on mouse leave or pending chip click.
    - Saves and restores scroll position and active categories per page.
    - Profile locking ('Khóa hồ sơ') to override auto page detection.
    """
    profile_changed = pyqtSignal(ContextProfile)
    pending_changed = pyqtSignal(object)              # ContextProfile or None
    profile_locked_changed = pyqtSignal(bool, str)     # is_locked, page_id
    search_boost_requested = pyqtSignal(list)         # categories

    def __init__(self, initial_page: str = "edit", parent: Optional[QObject] = None):
        super().__init__(parent)
        self._active_profile: ContextProfile = PROFILES.get(initial_page, PROFILES["edit"])
        self._pending_profile: Optional[ContextProfile] = None
        self._is_locked: bool = False
        self._memory = ProfileMemory()

        # Busy state indicators
        self._is_in_dock: bool = False
        self._has_search_text: bool = False
        self._is_sheet_open: bool = False
        self._is_dragging: bool = False

    @property
    def active_profile(self) -> ContextProfile:
        return self._active_profile

    @property
    def pending_profile(self) -> Optional[ContextProfile]:
        return self._pending_profile

    @property
    def is_locked(self) -> bool:
        return self._is_locked

    @property
    def is_in_dock(self) -> bool:
        return self._is_in_dock

    @is_in_dock.setter
    def is_in_dock(self, val: bool):
        self._is_in_dock = bool(val)
        if not self.is_busy and self._pending_profile:
            self.apply_pending()

    @property
    def has_search_text(self) -> bool:
        return self._has_search_text

    @has_search_text.setter
    def has_search_text(self, val: bool):
        self._has_search_text = bool(val)
        if not self.is_busy and self._pending_profile:
            self.apply_pending()

    @property
    def is_sheet_open(self) -> bool:
        return self._is_sheet_open

    @is_sheet_open.setter
    def is_sheet_open(self, val: bool):
        self._is_sheet_open = bool(val)
        if not self.is_busy and self._pending_profile:
            self.apply_pending()

    @property
    def is_dragging(self) -> bool:
        return self._is_dragging

    @is_dragging.setter
    def is_dragging(self, val: bool):
        self._is_dragging = bool(val)
        if not self.is_busy and self._pending_profile:
            self.apply_pending()

    @property
    def is_busy(self) -> bool:
        return bool(self._is_in_dock or self._has_search_text or self._is_sheet_open or self._is_dragging)

    def on_resolve_page_changed(self, page_str: str):
        """Called when ResolveStateService reports a page change."""
        page_norm = (page_str or "").lower().strip()
        if page_norm not in PROFILES:
            return

        if self._is_locked:
            logger.debug("Page change to '%s' ignored because profile is locked.", page_norm)
            return

        if page_norm == self._active_profile.page and self._pending_profile is None:
            return

        if self.is_busy:
            # Defer switch: user is actively interacting with the Dock
            self._pending_profile = PROFILES[page_norm]
            logger.info("Deferred page switch to '%s' (user busy). Showing pending chip.", page_norm)
            self.pending_changed.emit(self._pending_profile)
        else:
            self.apply_profile(page_norm)

    def on_user_left_dock(self):
        """Called when mouse leaves Dock: apply any pending profile."""
        self.is_in_dock = False

    def apply_pending(self):
        """Applies the pending deferred profile immediately."""
        if not self._pending_profile:
            return
        target = self._pending_profile
        self._pending_profile = None
        self.pending_changed.emit(None)
        self.apply_profile(target.page)

    def apply_profile(self, page_str: str):
        """Switches active profile, notifies views, and updates search boost."""
        page_norm = page_str.lower().strip()
        if page_norm not in PROFILES:
            return

        new_profile = PROFILES[page_norm]
        self._active_profile = new_profile
        self._pending_profile = None
        self.pending_changed.emit(None)

        logger.info("Applied context profile: %s (%s)", new_profile.label, new_profile.color)
        self.search_boost_requested.emit(new_profile.categories)
        self.profile_changed.emit(new_profile)

    def lock_profile(self, page_str: Optional[str]):
        """
        Locks to a specific profile or unlocks to 'auto' (follow Resolve).
        """
        if page_str is None or page_str.lower() in ("auto", "none"):
            self._is_locked = False
            self.profile_locked_changed.emit(False, self._active_profile.page)
            if self._pending_profile:
                self.apply_pending()
        else:
            p_norm = page_str.lower().strip()
            if p_norm in PROFILES:
                self._is_locked = True
                self.apply_profile(p_norm)
                self.profile_locked_changed.emit(True, p_norm)

    def save_page_state(self, category: Optional[str], subcategory: Optional[str], scroll_y: int):
        """Saves active category and scroll position of current page into memory."""
        self._memory.save(self._active_profile.page, category, subcategory, scroll_y)

    def get_saved_page_state(self, page_str: str) -> Optional[Dict[str, Any]]:
        return self._memory.get(page_str)
