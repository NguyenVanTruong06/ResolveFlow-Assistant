"""
src/services/audio_router.py
Bộ định tuyến âm thanh thống nhất theo thiết bị mặc định của hệ điều hành (OS Default Audio Output).
Theo dõi QMediaDevices.audioOutputsChanged và tự động cập nhật QAudioOutput của các player
(HoverPreview, Modal/Inspector Preview, Suggestion Chips) sang thiết bị mặc định mới.
"""

from __future__ import annotations

import logging
import weakref
from typing import Optional

from PySide6.QtCore import QObject, Signal as pyqtSignal
from PySide6.QtMultimedia import QMediaDevices, QAudioOutput, QAudioDevice

logger = logging.getLogger("ChunDVC.AudioRouter")

_AUDIO_ROUTER_INSTANCE: Optional[AudioDeviceRouter] = None


class AudioDeviceRouter(QObject):
    """
    Quản lý tập trung thiết bị đầu ra âm thanh theo thiết bị mặc định OS.
    Giữ QMediaDevices sống suốt vòng đời ứng dụng.
    """
    device_changed = pyqtSignal(str)
    device_lost = pyqtSignal()
    device_restored = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._devices: Optional[QMediaDevices] = None
        self._registered_outputs: weakref.WeakSet[QAudioOutput] = weakref.WeakSet()
        self._user_mute_state: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._has_device: bool = True
        self._ensure_devices()

    @property
    def has_device(self) -> bool:
        return self._has_device

    def _ensure_devices(self) -> QMediaDevices:
        if self._devices is None:
            self._devices = QMediaDevices(self)
            self._devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
            if hasattr(self._devices, "defaultAudioOutputChanged"):
                try:
                    self._devices.defaultAudioOutputChanged.connect(self._on_audio_outputs_changed)
                except Exception:
                    pass
        return self._devices

    def get_default_device(self) -> QAudioDevice:
        """Đọc thiết bị âm thanh mặc định hiện tại của hệ điều hành."""
        devices = self._ensure_devices()
        return devices.defaultAudioOutput()

    def register(self, output: QAudioOutput) -> None:
        """Đăng ký QAudioOutput để tự động đồng bộ theo thiết bị mặc định OS."""
        if output is None:
            return
        self._registered_outputs.add(output)
        if output not in self._user_mute_state:
            try:
                self._user_mute_state[output] = output.isMuted()
            except Exception:
                self._user_mute_state[output] = False

        default_dev = self.get_default_device()
        try:
            if default_dev.isNull():
                self._has_device = False
                output.setMuted(True)
                return

            self._has_device = True
            curr_dev = output.device()
            if curr_dev.id() != default_dev.id():
                output.setDevice(default_dev)
        except Exception as e:
            logger.debug("Lỗi khi áp thiết bị âm thanh ban đầu: %s", e)

    def unregister(self, output: QAudioOutput) -> None:
        """Hủy đăng ký QAudioOutput (mặc dù WeakSet đã tự dọn dẹp)."""
        self._registered_outputs.discard(output)
        self._user_mute_state.pop(output, None)

    def _on_audio_outputs_changed(self) -> None:
        """Xử lý khi danh sách hoặc thiết bị âm thanh mặc định của OS thay đổi."""
        try:
            default_dev = self.get_default_device()
            if default_dev.isNull():
                if self._has_device:
                    self._has_device = False
                    logger.warning("Mất thiết bị âm thanh OS: không có thiết bị đầu ra khả dụng. Mute an toàn.")
                    for output in list(self._registered_outputs):
                        try:
                            # Lưu trạng thái mute gốc của người dùng trước khi ép mute
                            self._user_mute_state[output] = output.isMuted()
                            output.setMuted(True)
                        except (RuntimeError, AttributeError):
                            self._registered_outputs.discard(output)
                            self._user_mute_state.pop(output, None)
                    self.device_lost.emit()
                return

            was_lost = not self._has_device
            self._has_device = True
            dev_id = default_dev.id()
            dev_desc = getattr(default_dev, "description", lambda: "Unknown")()
            logger.info("Thiết bị âm thanh OS thay đổi: %s (id: %s)", dev_desc, dev_id)

            for output in list(self._registered_outputs):
                try:
                    curr_dev = output.device()
                    curr_id = getattr(curr_dev, "id", lambda: None)()
                    if curr_id != dev_id or was_lost:
                        vol = output.volume()
                        if was_lost:
                            target_mute = self._user_mute_state.get(output, False)
                        else:
                            target_mute = output.isMuted()
                            self._user_mute_state[output] = target_mute

                        output.setDevice(default_dev)
                        output.setVolume(vol)
                        output.setMuted(target_mute)
                        logger.debug("Đã chuyển QAudioOutput sang thiết bị: %s", dev_desc)
                except (RuntimeError, AttributeError):
                    self._registered_outputs.discard(output)
                    self._user_mute_state.pop(output, None)

            if was_lost:
                self.device_restored.emit(dev_desc)
            self.device_changed.emit(dev_desc)
        except Exception as exc:
            logger.warning("Lỗi cập nhật thiết bị âm thanh: %s", exc)


def get_audio_router() -> AudioDeviceRouter:
    """Singleton getter cho AudioDeviceRouter."""
    global _AUDIO_ROUTER_INSTANCE
    if _AUDIO_ROUTER_INSTANCE is None:
        _AUDIO_ROUTER_INSTANCE = AudioDeviceRouter()
    return _AUDIO_ROUTER_INSTANCE
