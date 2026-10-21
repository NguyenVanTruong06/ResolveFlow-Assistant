"""
src/services/resolve_state.py
ResolveStateService: Periodically monitors Resolve's active page and playhead timecode.

Strict Clean Architecture: NO QtWidgets or QtGui allowed.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from src.services.resolve_gateway import ResolveGateway

logger = logging.getLogger("ResolveFlow.ResolveStateService")


def _query_resolve_state(resolve: Optional[Any]) -> tuple[str, str, str]:
    """
    Worker function executed on the ResolveGateway thread.
    Returns: (page_name, timecode, timeline_name)
    """
    if not resolve:
        return "", "", ""

    try:
        page = ""
        if hasattr(resolve, "GetCurrentPage"):
            page = str(resolve.GetCurrentPage() or "").lower()

        tc = "00:00:00:00"
        tl_name = ""
        if hasattr(resolve, "GetProjectManager"):
            pm = resolve.GetProjectManager()
            proj = pm.GetCurrentProject() if pm else None
            if proj and hasattr(proj, "GetCurrentTimeline"):
                tl = proj.GetCurrentTimeline()
                if tl:
                    tl_name = tl.GetName() if hasattr(tl, "GetName") else ""
                    if hasattr(tl, "GetCurrentTimecode"):
                        tc = str(tl.GetCurrentTimecode() or "00:00:00:00")

        return page, tc, tl_name
    except Exception as e:
        logger.debug("Error querying resolve state: %s", e)
        return "", "", ""


class ResolveStateService(QObject):
    """
    Service that polls DaVinci Resolve state every 700ms via ResolveGateway.
    Emits signals only on state transitions.
    """
    page_changed = Signal(str)           # Emits only when page changes
    playhead_changed = Signal(str)       # Emits when timecode changes
    timeline_changed = Signal(str)       # Emits when timeline name changes
    connection_changed = Signal(bool)
    state_changed = Signal(bool, str, str, str)  # connected, page, timeline, playhead_tc

    def __init__(self, gateway: ResolveGateway, parent: Optional[QObject] = None, poll_interval_ms: int = 700):
        super().__init__(parent)
        self._gateway = gateway
        self._poll_interval = poll_interval_ms

        self._current_page: str = ""
        self._current_playhead: str = ""
        self._current_timeline: str = ""
        self._is_connected: bool = gateway.is_connected
        self._paused: bool = False

        self._timer = QTimer(self)
        self._timer.setInterval(self._poll_interval)
        self._timer.timeout.connect(self._on_poll_tick)

        self._gateway.connection_changed.connect(self._on_connection_changed)

        if self._is_connected:
            self._timer.start()

    @property
    def current_page(self) -> str:
        return self._current_page

    @property
    def current_playhead(self) -> str:
        return self._current_playhead

    @property
    def current_timeline(self) -> str:
        return self._current_timeline

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def pause_polling(self):
        """Temporarily pause polling (e.g. while inserting via worker process)."""
        self._paused = True

    def resume_polling(self):
        """Resume polling."""
        self._paused = False

    @Slot(bool)
    def _on_connection_changed(self, connected: bool):
        self._is_connected = connected
        self.connection_changed.emit(connected)
        self.state_changed.emit(connected, self._current_page, self._current_timeline, self._current_playhead)
        if connected and not self._timer.isActive():
            self._timer.start()
        elif not connected and self._timer.isActive():
            self._timer.stop()
            if self._current_page != "":
                self._current_page = ""
                self.page_changed.emit("")

    @Slot()
    def _on_poll_tick(self):
        if not self._is_connected or self._paused:
            return

        self._gateway.submit(_query_resolve_state, callback=self._on_state_received)

    def _on_state_received(self, result: tuple[str, str, str]):
        if not result or not isinstance(result, tuple):
            return

        page, tc, tl_name = result

        if page and page != self._current_page:
            self._current_page = page
            self.page_changed.emit(page)

        if tc and tc != self._current_playhead:
            self._current_playhead = tc
            self.playhead_changed.emit(tc)

        if tl_name != self._current_timeline:
            self._current_timeline = tl_name
            self.timeline_changed.emit(tl_name)

        self.state_changed.emit(self._is_connected, self._current_page, self._current_timeline, self._current_playhead)
