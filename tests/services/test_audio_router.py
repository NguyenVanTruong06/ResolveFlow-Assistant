"""
Unit tests for AudioDeviceRouter (Section B: OS Default Audio Routing).
Deterministic tests covering device switching, null device safety, volume preservation,
and stale C++ object cleanup.
"""
import gc
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtMultimedia import QAudioOutput, QAudioDevice
from src.services.audio_router import AudioDeviceRouter


class FakeAudioOutput:
    """Mock audio output that satisfies AudioDeviceRouter interface and weakref."""
    def __init__(self, dev_id=b"dev_a", vol=0.65, muted=False):
        self._dev = MagicMock()
        self._dev.id.return_value = dev_id
        self._dev.isNull.return_value = False
        self._dev.description.return_value = "Speaker A"
        self._vol = vol
        self._muted = muted
        self.set_device_calls = []

    def device(self):
        return self._dev

    def setDevice(self, d):
        self._dev = d
        self.set_device_calls.append(d)

    def volume(self):
        return self._vol

    def setVolume(self, v):
        self._vol = v

    def isMuted(self):
        return self._muted

    def setMuted(self, m):
        self._muted = m


def test_audio_router_registration_and_deterministic_switch_a_to_b(qapp):
    router = AudioDeviceRouter()
    fake_out = FakeAudioOutput(dev_id=b"dev_a", vol=0.65, muted=False)

    dev_a = MagicMock()
    dev_a.id.return_value = b"dev_a"
    dev_a.isNull.return_value = False
    dev_a.description.return_value = "Speaker A"

    with patch.object(router, "get_default_device", return_value=dev_a):
        router.register(fake_out)

    assert fake_out in router._registered_outputs
    assert fake_out.volume() == 0.65
    assert fake_out.isMuted() is False

    # Simulate OS switching default device from A to B
    dev_b = MagicMock()
    dev_b.id.return_value = b"dev_b"
    dev_b.isNull.return_value = False
    dev_b.description.return_value = "Headphones B"

    changed_events = []
    router.device_changed.connect(lambda desc: changed_events.append(desc))

    # Reset calls from initial register
    fake_out.set_device_calls.clear()

    with patch.object(router, "get_default_device", return_value=dev_b):
        router._on_audio_outputs_changed()

    assert fake_out.device() == dev_b
    assert fake_out.volume() == pytest.approx(0.65)
    assert fake_out.isMuted() is False
    assert len(fake_out.set_device_calls) == 1
    assert changed_events == ["Headphones B"]

    # Test unchanged ID: should not call setDevice again
    fake_out.set_device_calls.clear()
    with patch.object(router, "get_default_device", return_value=dev_b):
        router._on_audio_outputs_changed()
    assert len(fake_out.set_device_calls) == 0

    # Test unregister
    router.unregister(fake_out)
    assert fake_out not in router._registered_outputs


def test_audio_router_null_device_safety_and_restoration(qapp):
    router = AudioDeviceRouter()
    fake_out = FakeAudioOutput(dev_id=b"dev_a", vol=0.8, muted=False)

    dev_a = MagicMock()
    dev_a.id.return_value = b"dev_a"
    dev_a.isNull.return_value = False
    dev_a.description.return_value = "Speaker A"

    with patch.object(router, "get_default_device", return_value=dev_a):
        router.register(fake_out)

    lost_events = []
    restored_events = []
    router.device_lost.connect(lambda: lost_events.append(True))
    router.device_restored.connect(lambda desc: restored_events.append(desc))

    # 1. Device lost (null device)
    null_dev = MagicMock()
    null_dev.isNull.return_value = True

    with patch.object(router, "get_default_device", return_value=null_dev):
        router._on_audio_outputs_changed()

    assert router.has_device is False
    assert fake_out.isMuted() is True
    assert len(lost_events) == 1

    # 2. Device restored (B appears)
    dev_b = MagicMock()
    dev_b.id.return_value = b"dev_b"
    dev_b.isNull.return_value = False
    dev_b.description.return_value = "USB DAC"

    with patch.object(router, "get_default_device", return_value=dev_b):
        router._on_audio_outputs_changed()

    assert router.has_device is True
    assert fake_out.device() == dev_b
    assert fake_out.isMuted() is False
    assert fake_out.volume() == pytest.approx(0.8)
    assert restored_events == ["USB DAC"]


def test_audio_router_mute_preservation_and_independence(qapp):
    """Kiểm tra: ban đầu output1 muted=True, output2 muted=False.
    Khi mất thiết bị (null) cả 2 bị ép mute, khi cắm lại output1 vẫn muted, output2 unmuted.
    Repeated null không ghi đè trạng thái mute gốc.
    """
    router = AudioDeviceRouter()
    out1_muted = FakeAudioOutput(dev_id=b"dev_a", vol=0.7, muted=True)
    out2_unmuted = FakeAudioOutput(dev_id=b"dev_a", vol=0.5, muted=False)

    dev_a = MagicMock()
    dev_a.id.return_value = b"dev_a"
    dev_a.isNull.return_value = False
    dev_a.description.return_value = "Speaker A"

    with patch.object(router, "get_default_device", return_value=dev_a):
        router.register(out1_muted)
        router.register(out2_unmuted)

    assert out1_muted.isMuted() is True
    assert out2_unmuted.isMuted() is False

    lost_events = []
    restored_events = []
    router.device_lost.connect(lambda: lost_events.append(True))
    router.device_restored.connect(lambda desc: restored_events.append(desc))

    null_dev = MagicMock()
    null_dev.isNull.return_value = True

    # Lần 1: Mất thiết bị
    with patch.object(router, "get_default_device", return_value=null_dev):
        router._on_audio_outputs_changed()

    assert router.has_device is False
    assert out1_muted.isMuted() is True
    assert out2_unmuted.isMuted() is True
    assert len(lost_events) == 1

    # Lần 2: Repeated null không ghi đè _user_mute_state và không lặp signal
    with patch.object(router, "get_default_device", return_value=null_dev):
        router._on_audio_outputs_changed()

    assert len(lost_events) == 1  # Không phát lại signal
    assert router._user_mute_state[out1_muted] is True
    assert router._user_mute_state[out2_unmuted] is False

    # Lần 3: Đăng ký thêm output3 trong lúc mất thiết bị (initial muted=False)
    out3_unmuted = FakeAudioOutput(dev_id=b"dev_a", vol=0.9, muted=False)
    with patch.object(router, "get_default_device", return_value=null_dev):
        router.register(out3_unmuted)
    assert out3_unmuted.isMuted() is True  # Bị ép mute an toàn
    assert router._user_mute_state[out3_unmuted] is False  # Nhớ ý định unmuted

    # Lần 4: Kết nối thiết bị B
    dev_b = MagicMock()
    dev_b.id.return_value = b"dev_b"
    dev_b.isNull.return_value = False
    dev_b.description.return_value = "Headphones B"

    with patch.object(router, "get_default_device", return_value=dev_b):
        router._on_audio_outputs_changed()

    assert router.has_device is True
    assert len(restored_events) == 1
    # Kiểm tra bảo toàn ý định người dùng độc lập:
    assert out1_muted.isMuted() is True, "Output 1 phải giữ nguyên mute do người dùng chủ động mute"
    assert out2_unmuted.isMuted() is False, "Output 2 phải giữ nguyên unmuted"
    assert out3_unmuted.isMuted() is False, "Output 3 phải giữ nguyên unmuted sau khôi phục"
    assert out1_muted.volume() == pytest.approx(0.7)
    assert out2_unmuted.volume() == pytest.approx(0.5)
    assert out3_unmuted.volume() == pytest.approx(0.9)


def test_audio_router_initial_no_device_startup(qapp):
    """Kiểm tra khởi động máy tính không có bất kỳ audio device nào."""
    router = AudioDeviceRouter()
    null_dev = MagicMock()
    null_dev.isNull.return_value = True

    out = FakeAudioOutput(dev_id=b"", vol=0.6, muted=False)
    with patch.object(router, "get_default_device", return_value=null_dev):
        router.register(out)

    assert router.has_device is False
    assert out.isMuted() is True
    assert router._user_mute_state[out] is False

    # Cắm tai nghe vào
    dev_b = MagicMock()
    dev_b.id.return_value = b"dev_b"
    dev_b.isNull.return_value = False
    dev_b.description.return_value = "Headphones"

    with patch.object(router, "get_default_device", return_value=dev_b):
        router._on_audio_outputs_changed()

    assert router.has_device is True
    assert out.isMuted() is False


def test_audio_router_real_qaudiooutput_null_safety(qapp):
    router = AudioDeviceRouter()
    output = QAudioOutput()
    output.setVolume(0.8)
    router.register(output)

    null_dev = QAudioDevice()
    with patch.object(router, "get_default_device", return_value=null_dev):
        router._on_audio_outputs_changed()

    assert router.has_device is False
    assert output.isMuted() is True


def test_audio_router_deleted_cpp_object_handled(qapp):
    router = AudioDeviceRouter()
    deleted_out = MagicMock()
    deleted_out.device.side_effect = RuntimeError("Internal C++ object (QAudioOutput) already deleted.")

    router.register(deleted_out)
    assert deleted_out in router._registered_outputs

    dev_b = MagicMock()
    dev_b.id.return_value = b"dev_b"
    dev_b.isNull.return_value = False
    dev_b.description.return_value = "Headphones"

    with patch.object(router, "get_default_device", return_value=dev_b):
        # Should not raise RuntimeError
        router._on_audio_outputs_changed()

    assert deleted_out not in router._registered_outputs


def test_audio_router_weakset_cleanup(qapp):
    router = AudioDeviceRouter()

    def _scope_create():
        temp_out = QAudioOutput()
        router.register(temp_out)
        assert len(router._registered_outputs) == 1

    _scope_create()
    gc.collect()
    assert len(router._registered_outputs) == 0


def test_audio_router_inspector_registration(qapp):
    from src.ui.tabs.tab_assets import IconMemePreviewWidget
    from src.services.audio_router import get_audio_router

    preview_widget = IconMemePreviewWidget()
    router = get_audio_router()
    preview_widget._ensure_player()

    assert preview_widget._audio_output is not None
    assert preview_widget._audio_output in router._registered_outputs
