"""
src/services/autocut_worker.py
QThread Worker chạy InstantAutoCutService trong luồng phụ không gây đơ giao diện.
"""
from __future__ import annotations

from typing import Optional
from PySide6.QtCore import QThread, Signal
from src.services.autocut_service import InstantAutoCutService, AutoCutResult
from src.core.autocut import AudioCutConfig
from src.core.resolve_api import ResolveAutomation


class AutoCutWorker(QThread):
    progress_changed = Signal(int, str)  # percent (0..100), status message
    finished_result = Signal(object)     # AutoCutResult

    def __init__(
        self,
        resolve_automation: Optional[ResolveAutomation] = None,
        config: Optional[AudioCutConfig] = None,
        parent=None
    ):
        super().__init__(parent)
        self.service = InstantAutoCutService(
            resolve_automation=resolve_automation,
            config=config
        )

    def run(self):
        result = self.service.run_instant_autocut(
            progress_callback=lambda pct, msg: self.progress_changed.emit(pct, msg)
        )
        self.finished_result.emit(result)
