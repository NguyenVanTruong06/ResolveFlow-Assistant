"""
src/ui/lux/library/hover_preview.py
Shared hover video/audio preview overlay (learning from Akitt Pro 4.0).

Reuses a single QMediaPlayer and QVideoWidget instance across the entire grid.
Activates after 150ms debounce and stops immediately on mouse leave or viewport scroll.
"""
from __future__ import annotations

import os
import math
import logging
from typing import Optional, Any

from PySide6.QtCore import Qt, QObject, QTimer, QUrl, QRect, QPoint, QEvent
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QListView
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from src.ui.lux.library.asset_model import PreviewPathRole, CategoryRole, IdRole

logger = logging.getLogger("ResolveFlow.HoverPreview")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a"}


class HoverPreviewOverlay(QFrame):
    """Floating borderless frame hosting the single QVideoWidget."""
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("""
            QFrame {
                background-color: #000000;
                border: 1.5px solid #8b5cf6;
                border-radius: 6px;
            }
        """)
        self._video_widget: Optional[QVideoWidget] = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.hide()

    @property
    def video_widget(self) -> Optional[QVideoWidget]:
        return self._video_widget

    def ensure_video_widget(self) -> QVideoWidget:
        if self._video_widget is None:
            self._video_widget = QVideoWidget(self)
            self._video_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._layout.addWidget(self._video_widget)
        return self._video_widget


class SharedHoverPreview(QObject):
    """
    Coordinates hover preview over an AssetGridView.
    Only ONE player exists, saving memory and GPU decoders.
    """
    def __init__(self, view: QListView, parent: Optional[QObject] = None):
        super().__init__(parent or view)
        self._view = view
        self._overlay: Optional[HoverPreviewOverlay] = None

        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._enable_audio_preview: bool = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._on_hover_timeout)

        self._hovered_row: int = -1
        self._current_source_file: str = ""

        # Install event filter on view viewport
        self._view.viewport().installEventFilter(self)
        self._view.verticalScrollBar().valueChanged.connect(self.stop)

        self._ensure_player_lazy = True

    def _ensure_overlay(self) -> HoverPreviewOverlay:
        if self._overlay is None:
            self._overlay = HoverPreviewOverlay(self._view.viewport())
        return self._overlay

    def _ensure_player(self) -> Optional[QMediaPlayer]:
        if self._player is None:
            try:
                self._player = QMediaPlayer(self)
                self._audio_output = QAudioOutput(self)
                self._player.setAudioOutput(self._audio_output)
                self._audio_output.setVolume(0.7 if self._enable_audio_preview else 0.0)
                self._player.mediaStatusChanged.connect(self._on_media_status_changed)
            except Exception as e:
                logger.warning("Failed to initialize QMediaPlayer: %s", e)
                self._player = None
        return self._player

    def set_audio_preview_enabled(self, enabled: bool):
        self._enable_audio_preview = enabled
        if self._audio_output:
            self._audio_output.setVolume(0.7 if enabled else 0.0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            if self._view and self._view.viewport() and watched == self._view.viewport():
                if event.type() == QEvent.Type.MouseMove:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    self._handle_mouse_move(pos)
                elif event.type() == QEvent.Type.Leave:
                    self.stop()
        except (RuntimeError, AttributeError):
            return False
        return super().eventFilter(watched, event)

    def _handle_mouse_move(self, pos: QPoint):
        idx = self._view.indexAt(pos)
        if not idx.isValid():
            self.stop()
            return

        row = idx.row()
        if row != self._hovered_row:
            self.stop()
            self._hovered_row = row
            self._timer.start()

    def _on_hover_timeout(self):
        if self._hovered_row < 0:
            return

        model = self._view.model()
        if not model:
            return

        idx = model.index(self._hovered_row, 0)
        if not idx.isValid():
            return

        preview_p = str(idx.data(PreviewPathRole) or "")
        category = str(idx.data(CategoryRole) or "").lower()

        if not preview_p or not os.path.isfile(preview_p):
            return

        ext = os.path.splitext(preview_p)[1].lower()

        # Audio files
        if ext in AUDIO_EXTS:
            if self._enable_audio_preview:
                self._play_audio(preview_p)
            return

        # Video preview files
        if ext in VIDEO_EXTS:
            self._play_video(idx, preview_p)

    def _play_video(self, index: Any, video_path: str):
        player = self._ensure_player()
        if not player:
            return

        # Position overlay directly over the 16:9 preview part of the card
        visual_rect = self._view.visualRect(index)
        if visual_rect.isEmpty():
            return

        overlay = self._ensure_overlay()
        vw = overlay.ensure_video_widget()
        player.setVideoOutput(vw)

        pad = 4
        pw = visual_rect.width() - (pad * 2)
        ph = round(pw * 9 / 16)
        preview_rect = QRect(visual_rect.x() + pad, visual_rect.y() + pad, pw, ph)

        overlay.setGeometry(preview_rect)
        overlay.show()
        overlay.raise_()

        self._current_source_file = video_path
        player.setSource(QUrl.fromLocalFile(os.path.abspath(video_path)))
        if self._audio_output:
            self._audio_output.setVolume(0.0)  # Video previews hover muted
        player.play()

    def _play_audio(self, audio_path: str):
        player = self._ensure_player()
        if not player or not self._audio_output:
            return
        self._audio_output.setVolume(0.7)
        player.setSource(QUrl.fromLocalFile(os.path.abspath(audio_path)))
        player.play()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus):
        # Auto-loop video playback during hover
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._player:
            self._player.setPosition(0)
            self._player.play()

    def stop(self):
        """Immediately stops playback and hides overlay."""
        self._timer.stop()
        self._hovered_row = -1
        if self._player:
            try:
                self._player.stop()
            except Exception:
                pass
        if self._overlay:
            self._overlay.hide()
