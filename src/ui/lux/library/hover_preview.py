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
from collections.abc import Mapping
from typing import Optional, Any, Dict, List, Tuple

from PySide6.QtCore import Qt, QObject, QTimer, QUrl, QRect, QPoint, QEvent, QSettings, Signal
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QListView
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from src.ui.lux.library.asset_model import PreviewPathRole, CategoryRole, IdRole, FilePathRole, AssetObjRole

logger = logging.getLogger("ChunDVC.HoverPreview")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a"}


def _normalize_preview_payload(
    item: Any,
    preview_path_hint: str = "",
    file_path_hint: str = "",
) -> Dict[str, Any]:
    """
    Chuẩn hóa asset payload cho ActionGuard preview mà không làm mất provenance,
    không deepcopy và không import asset_grid (tránh circular dependency).
    """
    if isinstance(item, Mapping):
        asset = dict(item)
        if preview_path_hint and not asset.get("preview_path"):
            asset["preview_path"] = preview_path_hint
        if file_path_hint and not asset.get("file_path"):
            asset["file_path"] = file_path_hint
        if "_raw_obj" not in asset:
            asset["_raw_obj"] = item
        return asset

    # Domain object (TextStylePreset, TransitionStylePreset, StudioAsset, duck-typed)
    return {
        "id": getattr(item, "id", None) or getattr(item, "name", ""),
        "name": getattr(item, "name", ""),
        "category": getattr(item, "category", None) or getattr(item, "tab", ""),
        "tab": getattr(item, "tab", None) or getattr(item, "category", ""),
        "template_type": getattr(item, "template_type", None),
        "source_kind": getattr(item, "source_kind", None),
        "origin_library_id": getattr(item, "origin_library_id", None),
        "origin_root": getattr(item, "origin_root", None),
        "storage_managed": getattr(item, "storage_managed", None),
        "library_path": getattr(item, "library_path", None),
        "relative_path": getattr(item, "relative_path", None),
        "external_path": getattr(item, "external_path", None),
        "file_path": getattr(item, "file_path", None) or getattr(item, "path", None) or file_path_hint,
        "path": getattr(item, "path", None) or getattr(item, "file_path", None) or file_path_hint,
        "preview_path": getattr(item, "preview_path", None) or preview_path_hint,
        "availability": getattr(item, "availability", None),
        "native_preset": getattr(item, "native_preset", None),
        "_raw_obj": item,
    }


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
    Supports video playback with audio for memes and SFX.
    """
    audio_preview_starting = Signal()

    def __init__(self, view: QListView, parent: Optional[QObject] = None):
        super().__init__(parent if parent is not None else (view if isinstance(view, QObject) else None))
        self._view = view
        self._overlay: Optional[HoverPreviewOverlay] = None

        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None

        self._settings = QSettings("ChunDVC", "ResolveFlow")
        # Default to True so hovering memes/videos/SFX plays audio immediately
        self._enable_audio_preview: bool = self._settings.value("library/hover_audio_enabled", True, type=bool)
        self._volume: float = float(self._settings.value("library/hover_audio_volume", 0.75))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._on_hover_timeout)

        self._hovered_row: int = -1
        self._current_source_file: str = ""
        self._bound_model: Optional[Any] = None
        self._connected_signals: List[Tuple[Any, Any]] = []
        self._is_active_playback: bool = False

        # Install event filter on view and viewport
        if self._view:
            self._view.installEventFilter(self)
            if hasattr(self._view, "viewport") and self._view.viewport():
                self._view.viewport().installEventFilter(self)
            if hasattr(self._view, "verticalScrollBar") and self._view.verticalScrollBar():
                self._view.verticalScrollBar().valueChanged.connect(self.stop)
            if hasattr(self._view, "model") and self._view.model() is not None:
                self.bind_model(self._view.model())

        self._ensure_player_lazy = True

    def bind_model(self, model: Optional[Any]) -> None:
        """Binds the active model (source or proxy) and subscribes to invalidation signals."""
        if model is self._bound_model:
            return

        self._disconnect_bound_model()
        self.stop()

        if model is None:
            self._bound_model = None
            return

        self._bound_model = model

        # Connect model lifecycle signals
        signals = [
            "modelAboutToBeReset",
            "modelReset",
            "rowsAboutToBeRemoved",
            "rowsRemoved",
            "layoutAboutToBeChanged",
            "layoutChanged",
        ]
        for sig_name in signals:
            sig = getattr(model, sig_name, None)
            if sig is not None:
                try:
                    sig.connect(self._on_model_invalidated)
                    self._connected_signals.append((sig, self._on_model_invalidated))
                except (AttributeError, RuntimeError, TypeError) as e:
                    logger.debug("Failed to connect signal %s: %s", sig_name, e)

        # Handle model destruction safely
        destroyed_sig = getattr(model, "destroyed", None)
        if destroyed_sig is not None:
            try:
                destroyed_sig.connect(self._on_model_destroyed)
                self._connected_signals.append((destroyed_sig, self._on_model_destroyed))
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _disconnect_bound_model(self) -> None:
        if self._bound_model is not None:
            for sig, slot in self._connected_signals:
                try:
                    sig.disconnect(slot)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            self._connected_signals.clear()
            self._bound_model = None

    def _on_model_invalidated(self, *args: Any, **kwargs: Any) -> None:
        """Slot safely handling model invalidation signals with arbitrary arguments."""
        self.stop(*args, **kwargs)

    def _on_model_destroyed(self, *args: Any, **kwargs: Any) -> None:
        """Safely clean up references when bound model QObject is destroyed."""
        self._connected_signals.clear()
        self._bound_model = None
        self.stop(*args, **kwargs)

    def _ensure_overlay(self) -> HoverPreviewOverlay:
        if self._overlay is None:
            self._overlay = HoverPreviewOverlay(self._view.viewport())
        return self._overlay

    def _ensure_player(self) -> Optional[QMediaPlayer]:
        if self._player is None:
            try:
                self._player = QMediaPlayer(self)
                self._audio_output = QAudioOutput(self)
                from src.services.audio_router import get_audio_router
                get_audio_router().register(self._audio_output)
                self._player.setAudioOutput(self._audio_output)
                vol = self._volume if self._enable_audio_preview else 0.0
                self._audio_output.setVolume(vol)
                self._player.mediaStatusChanged.connect(self._on_media_status_changed)
            except Exception as e:
                logger.warning("Failed to initialize QMediaPlayer: %s", e)
                self._player = None
        return self._player

    def set_audio_preview_enabled(self, enabled: bool):
        self._enable_audio_preview = bool(enabled)
        self._settings.setValue("library/hover_audio_enabled", self._enable_audio_preview)
        if self._audio_output:
            self._audio_output.setVolume(self._volume if self._enable_audio_preview else 0.0)

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))
        self._settings.setValue("library/hover_audio_volume", self._volume)
        if self._audio_output and self._enable_audio_preview:
            self._audio_output.setVolume(self._volume)

    @property
    def is_audio_preview_enabled(self) -> bool:
        return self._enable_audio_preview

    @property
    def volume(self) -> float:
        return self._volume

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            if not self._view:
                return False
            if watched == self._view:
                if event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
                    self.stop()
            elif hasattr(self._view, "viewport") and self._view.viewport() and watched == self._view.viewport():
                if event.type() == QEvent.Type.MouseMove:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    self._handle_mouse_move(pos)
                elif event.type() == QEvent.Type.Leave:
                    self.stop()
                elif event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
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

        raw = idx.data(AssetObjRole)
        preview_p = str(idx.data(PreviewPathRole) or "").strip()
        file_p = str(idx.data(FilePathRole) or "").strip()

        payload = _normalize_preview_payload(raw, preview_path_hint=preview_p, file_path_hint=file_p)

        from src.core.action_guard import check_action_availability
        guard_res = check_action_availability(payload, intent="preview")
        if not guard_res.allowed:
            self.stop()
            return

        if guard_res.result_code == "OK_PROCEDURAL_PREVIEW_CANVAS":
            self.stop()
            return

        target_file = guard_res.resolved_path
        if not target_file:
            self.stop()
            return

        ext = os.path.splitext(target_file)[1].lower()

        # Audio files (SFX, Sound, BGM)
        if ext in AUDIO_EXTS:
            if self._enable_audio_preview:
                self._play_audio(target_file)
            else:
                self.stop()
            return

        # Video preview files (Memes, Overlays, Transitions, Videos)
        if ext in VIDEO_EXTS:
            self._play_video(idx, target_file)
        else:
            self.stop()

    def _is_audible_output(self) -> bool:
        if not self._enable_audio_preview or self._volume <= 0.0:
            return False
        if not self._audio_output:
            return False
        try:
            if hasattr(self._audio_output, "isMuted") and self._audio_output.isMuted():
                return False
        except Exception:
            pass
        return True

    def _play_video(self, index: Any, video_path: str):
        if not self._view:
            return
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
        self._is_active_playback = True
        player.setSource(QUrl.fromLocalFile(os.path.abspath(video_path)))
        if self._audio_output:
            # Play meme and video audio at configured volume when audio preview is enabled
            vol = self._volume if self._enable_audio_preview else 0.0
            self._audio_output.setVolume(vol)
            if self._is_audible_output():
                self.audio_preview_starting.emit()
        player.play()

    def preview_asset(self, asset: Any):
        """Public API to preview an asset (audio or video), respecting audio settings and volume."""
        if not asset:
            self.stop()
            return

        payload = _normalize_preview_payload(asset)
        from src.core.action_guard import check_action_availability
        guard_res = check_action_availability(payload, intent="preview")
        if not guard_res.allowed:
            self.stop()
            return

        if guard_res.result_code == "OK_PROCEDURAL_PREVIEW_CANVAS":
            self.stop()
            return

        target_file = guard_res.resolved_path
        if not target_file:
            self.stop()
            return

        ext = os.path.splitext(target_file)[1].lower()
        if ext in AUDIO_EXTS:
            if self._enable_audio_preview:
                self._play_audio(target_file)
            else:
                self.stop()
        elif ext in VIDEO_EXTS:
            player = self._ensure_player()
            if player:
                self._current_source_file = target_file
                self._is_active_playback = True
                player.setSource(QUrl.fromLocalFile(os.path.abspath(target_file)))
                if self._audio_output:
                    vol = self._volume if self._enable_audio_preview else 0.0
                    self._audio_output.setVolume(vol)
                    if self._is_audible_output():
                        self.audio_preview_starting.emit()
                player.play()
        else:
            self.stop()

    def _play_audio(self, audio_path: str):
        player = self._ensure_player()
        if not player or not self._audio_output:
            return
        vol = self._volume if self._enable_audio_preview else 0.0
        if vol <= 0.0:
            return
        self._audio_output.setVolume(vol)
        if self._is_audible_output():
            self.audio_preview_starting.emit()
        self._current_source_file = audio_path
        self._is_active_playback = True
        player.setSource(QUrl.fromLocalFile(os.path.abspath(audio_path)))
        player.play()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus):
        # Auto-loop video playback during active hover only
        if not self._is_active_playback:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._player:
            self._player.setPosition(0)
            self._player.play()

    def stop(self, *args: Any, **kwargs: Any):
        """Immediately stops playback, silences audio, and hides overlay. Idempotent."""
        self._is_active_playback = False
        self._timer.stop()
        self._hovered_row = -1
        self._current_source_file = ""
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
        if self._overlay is not None:
            try:
                self._overlay.hide()
            except Exception:
                pass
