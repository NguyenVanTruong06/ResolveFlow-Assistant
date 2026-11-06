"""
tests/ui/test_tab_assets_audio_inspector_guard.py
=============================================================================
Bộ kiểm thử TDD cho Task G04-04C9-I:
Audio Inspector thật và ưu tiên phát trong Studio.

Bao gồm:
1. Lazy init & no autoplay: nạp asset không khởi tạo player, không tự phát.
2. Selection không auto-audition: click thẻ SFX/BGM hoặc Suggestion không tự phát.
3. Positive managed audio playback: phát âm thanh thật, đăng ký AudioDeviceRouter.
4. Điều khiển Pause, Resume, Replay và tính idempotent: không nhân đôi connections.
5. Bảo vệ Session Epoch chống stale callbacks: A cũ không ghi đè B đang active.
6. Đổi asset: dừng phát ngay, xóa source QUrl(), reset position về 0, 0 autoplay.
7. Action-time guard denied: xóa file fixture trước khi Play -> fail-closed trung thực.
8. Staging & Offline storage denied: nút bị vô hiệu hóa với tooltip tiếng Việt.
9. Quản lý volume: đọc QSettings, clamp [0.0, 1.0], chống crash khi dữ liệu lỗi.
10. Dừng chéo: Inspector start gọi dừng hover controller của grid trước khi play.
11. Dừng chéo: Hover preview starting phát tín hiệu dừng Inspector đang phát.
12. Ẩn / đóng widget: hideEvent và closeEvent dừng phát sạch sẽ và clear source.
13. Đăng ký AudioDeviceRouter đúng 1 lần: đồng bộ OS default device an toàn.
14. Khả năng chống chịu tín hiệu đồng bộ từ setSource: không hạ cờ active sớm.
15. WaveformCanvas: vạch tiến trình playhead an toàn với duration=0/unknown.
=============================================================================
"""
import os
import math
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QUrl, QSettings, QRect
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtMultimedia import QMediaPlayer

from src.ui.tabs.tab_assets import (
    TabAssets,
    WaveformCanvas,
    StudioAsset,
)
from src.services.audio_router import get_audio_router, AudioDeviceRouter


class MockStorage:
    def __init__(self, root: str, library_id: str, online: bool = True):
        self._root = Path(root)
        self.library_id = library_id
        self._config = {"root": str(self._root), "library_id": library_id}
        self.online = online

    @property
    def root(self):
        return self._root

    def ui_assets(self):
        return []

    def taxonomy(self):
        return {}

    def links(self):
        return []


def make_studio_asset(**kwargs):
    kwargs.setdefault("category", kwargs.get("tab", "sfx"))
    if "file_path" in kwargs and kwargs.get("source_kind") == "managed" and "origin_root" in kwargs and "relative_path" not in kwargs:
        try:
            kwargs["relative_path"] = str(Path(kwargs["file_path"]).relative_to(Path(kwargs["origin_root"])))
        except ValueError:
            kwargs["relative_path"] = Path(kwargs["file_path"]).name
    init_keys = {"id", "name", "tab", "category", "sub", "badge_icon", "pal", "duration", "frames", "color_hex", "svg_path", "native_preset", "file_path", "thumbnail_path", "pack_name", "preview_path"}
    init_args = {k: v for k, v in kwargs.items() if k in init_keys}
    extra_attrs = {k: v for k, v in kwargs.items() if k not in init_keys}
    asset = StudioAsset(**init_args)
    for k, v in extra_attrs.items():
        setattr(asset, k, v)
    return asset


class FakeSignal:
    def __init__(self):
        self.cbs = []

    def connect(self, cb):
        self.cbs.append(cb)
        return cb

    def disconnect(self, cb):
        if cb in self.cbs:
            self.cbs.remove(cb)

    def emit(self, *args, **kwargs):
        for cb in list(self.cbs):
            cb(*args, **kwargs)


class FakePlayer:
    def __init__(self):
        self.positionChanged = FakeSignal()
        self.durationChanged = FakeSignal()
        self.playbackStateChanged = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.mediaStatusChanged = FakeSignal()
        self._source = QUrl()
        self._state = QMediaPlayer.StoppedState
        self._status = QMediaPlayer.NoMedia
        self._pos = 0
        self.stop_called = 0
        self.play_called = 0
        self.pause_called = 0
        self.set_source_calls = []

    def setSource(self, s):
        self._source = s
        self.set_source_calls.append(s)

    def source(self):
        return self._source

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def play(self):
        self.play_called += 1
        self._state = QMediaPlayer.PlayingState

    def pause(self):
        self.pause_called += 1
        self._state = QMediaPlayer.PausedState

    def stop(self):
        self.stop_called += 1
        self._state = QMediaPlayer.StoppedState

    def setPosition(self, p):
        self._pos = p

    def position(self):
        return self._pos

    def setVideoOutput(self, v):
        self._video_output = v

    def setAudioOutput(self, a):
        self._audio_output = a

    def disconnect(self, c):
        self.positionChanged.disconnect(c)
        self.durationChanged.disconnect(c)
        self.playbackStateChanged.disconnect(c)
        self.errorOccurred.disconnect(c)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolate_thumbnail_and_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        "src.core.asset_cache.request_thumbnail_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.core.asset_cache.extract_thumbnail",
        lambda *args, **kwargs: None,
    )


# ---------------------------------------------------------------------------
# 1. LAZY INIT VÀ NO AUTOPLAY
# ---------------------------------------------------------------------------
def test_audio_inspector_lazy_init_and_no_autoplay(qapp, tmp_path, monkeypatch):
    """Nạp asset âm thanh không eager initialize player, không autoplay, nút Play sẵn sàng."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    widget.show()

    asset = make_studio_asset(
        id="sfx_whoosh",
        name="Whoosh SFX",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=2.5,
    )

    widget.set_asset(asset)

    assert widget._player is None, "FAIL: Player was eagerly created on set_asset!"
    assert widget._audio_output is None, "FAIL: Audio output was eagerly created on set_asset!"
    assert widget._is_active_playback is False, "FAIL: Active playback flag set on selection!"
    assert widget.btn_play.isEnabled() is True, "FAIL: btn_play should be enabled for valid audio asset!"
    assert widget.btn_play.text() == "▶ Nghe thử"
    assert "00:02" in widget.lbl_time.text() or "02.5s" in widget.lbl_time.text() or "00:00" in widget.lbl_time.text()
    widget.close()


# ---------------------------------------------------------------------------
# 2. SELECTION KHÔNG AUTO-AUDITION
# ---------------------------------------------------------------------------
def test_audio_inspector_card_selected_no_auto_audition(qapp, tmp_path, monkeypatch):
    """Click chọn thẻ SFX/BGM trên TabAssets hoặc Suggestion Bar KHÔNG tự động phát âm thanh."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "sfx_pop.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    tab_assets = TabAssets(headless=True)

    asset = make_studio_asset(
        id="sfx_pop",
        name="Pop SFX",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=1.0,
    )
    tab_assets.all_assets.append(asset)

    # Khẳng định không có sfx_playback_timer chạy ngầm
    assert not hasattr(tab_assets, "sfx_playback_timer") or not tab_assets.sfx_playback_timer.isActive()

    # Kích hoạt lựa chọn thẻ
    tab_assets._on_card_selected("sfx_pop")

    # Kiểm tra widget Inspector âm thanh
    audio_widget = getattr(tab_assets, "audio_inspector_widget", None)
    assert audio_widget is not None, "FAIL: TabAssets must have audio_inspector_widget!"
    assert audio_widget._player is None or audio_widget._player.playbackState() == QMediaPlayer.StoppedState
    assert audio_widget._is_active_playback is False, "FAIL: Selection triggered auto-audition!"
    assert audio_widget.btn_play.text() == "▶ Nghe thử"


# ---------------------------------------------------------------------------
# 3. POSITIVE MANAGED AUDIO PLAYBACK
# ---------------------------------------------------------------------------
def test_audio_inspector_positive_managed_audio_playback(qapp, tmp_path, monkeypatch):
    """Phát âm thanh managed hợp lệ qua nút Nghe thử: nạp realpath, đăng ký AudioDeviceRouter."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "sfx_valid.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(
        id="sfx_valid",
        name="Valid SFX",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=3.0,
    )

    widget.set_asset(asset)
    assert widget.resolved_file_path == str(audio_file.resolve())

    # Bấm phát
    widget.toggle_playback()

    assert widget._is_active_playback is True
    assert player.play_called == 1
    assert player.source().toLocalFile() == str(audio_file.resolve())
    assert widget.btn_play.text() == "⏸ Dừng"
    widget.close()


# ---------------------------------------------------------------------------
# 4. PAUSE, RESUME, REPLAY VÀ TÍNH IDEMPOTENT
# ---------------------------------------------------------------------------
def test_audio_inspector_pause_resume_replay_idempotency(qapp, tmp_path, monkeypatch):
    """Pause -> PausedState ('▶ Tiếp tục'), Resume -> PlayingState ('⏸ Dừng'), Replay -> reset pos 0."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "test.mp3"
    audio_file.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    # 1. Bắt đầu phát
    widget.start_playback()
    assert player.play_called == 1
    assert widget._is_active_playback is True
    assert len(player.positionChanged.cbs) == 1

    # 2. Pause
    widget.toggle_playback()
    assert player.pause_called == 1
    player.playbackStateChanged.emit(QMediaPlayer.PausedState)
    assert widget.btn_play.text() == "▶ Tiếp tục"

    # 3. Resume
    widget.toggle_playback()
    assert player.play_called == 2
    player.playbackStateChanged.emit(QMediaPlayer.PlayingState)
    assert widget.btn_play.text() == "⏸ Dừng"
    assert len(player.positionChanged.cbs) == 1  # Không nhân đôi connection!

    # 4. Replay
    player.positionChanged.emit(1500)
    assert widget._position_ms == 1500
    widget.start_playback()  # hoặc replay nếu có
    assert player.source().toLocalFile() == str(audio_file.resolve())

    # 5. Stop idempotent
    widget.stop_playback()
    widget.stop_playback()
    assert widget._is_active_playback is False
    assert widget.btn_play.text() == "▶ Nghe thử"
    widget.close()


# ---------------------------------------------------------------------------
# 5. BẢO VỆ SESSION EPOCH CHỐNG STALE CALLBACKS
# ---------------------------------------------------------------------------
def test_audio_inspector_stale_callbacks_epoch_protection(qapp, tmp_path, monkeypatch):
    """Session A cũ bắn callback vị trí (99000), duration, hoặc error khi B active -> B không bị ảnh hưởng."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_a = root_dir / "audio_a.wav"
    audio_a.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)
    audio_b = root_dir / "audio_b.wav"
    audio_b.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset_a = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_a), storage_managed=True, availability="online")
    asset_b = make_studio_asset(id="b", name="B", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_b), storage_managed=True, availability="online")

    # 1. Phát A
    widget.set_asset(asset_a)
    widget.start_playback()
    pos_cb_a = player.positionChanged.cbs[0]
    dur_cb_a = player.durationChanged.cbs[0]
    err_cb_a = player.errorOccurred.cbs[0]
    state_cb_a = player.playbackStateChanged.cbs[0]

    player.positionChanged.emit(200)
    assert widget._position_ms == 200

    # 2. Chuyển sang B
    widget.set_asset(asset_b)
    assert player.stop_called >= 1
    assert player.source() == QUrl()
    assert widget._position_ms == 0
    assert widget._is_active_playback is False

    # 3. Phát B
    widget.start_playback()
    player.positionChanged.emit(500)
    assert widget._position_ms == 500

    # 4. Bắn stale callbacks từ Session A
    pos_cb_a(99000)
    assert widget._position_ms == 500, "FAIL: Stale position callback from A corrupted B!"

    dur_cb_a(180000)
    assert widget._duration_ms != 180000, "FAIL: Stale duration callback from A corrupted B!"

    err_cb_a(QMediaPlayer.Error.ResourceError, "Fake error from A")
    assert widget._is_active_playback is True, "FAIL: Stale error from A stopped active B!"
    assert widget.btn_play.text() == "⏸ Dừng"

    # 5. Dừng B và bắn state_cb_a(PlayingState) -> không đổi nút thành "Dừng"
    widget.stop_playback()
    assert widget.btn_play.text() == "▶ Nghe thử"
    state_cb_a(QMediaPlayer.PlayingState)
    assert widget.btn_play.text() == "▶ Nghe thử"
    widget.close()


# ---------------------------------------------------------------------------
# 6. ĐỔI ASSET DỪNG PHÁT VÀ RESET
# ---------------------------------------------------------------------------
def test_audio_inspector_switch_asset_stops_and_resets(qapp, tmp_path, monkeypatch):
    """Khi A đang phát, set_asset(B) lập tức dừng A, xóa source, reset pos=0, 0 autoplay cho B."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_a = root_dir / "a.wav"
    audio_a.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)
    audio_b = root_dir / "b.wav"
    audio_b.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset_a = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_a), storage_managed=True, availability="online")
    asset_b = make_studio_asset(id="b", name="B", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_b), storage_managed=True, availability="online")

    widget.set_asset(asset_a)
    widget.start_playback()
    assert widget._is_active_playback is True

    widget.set_asset(asset_b)
    assert widget._is_active_playback is False
    assert player.source() == QUrl()
    assert widget._position_ms == 0
    assert widget.btn_play.text() == "▶ Nghe thử"
    widget.close()


# ---------------------------------------------------------------------------
# 7. ACTION-TIME GUARD DENIED KHI XÓA FIXTURE
# ---------------------------------------------------------------------------
def test_audio_inspector_action_time_guard_denied(qapp, tmp_path, monkeypatch):
    """Xóa file fixture giữa lúc chọn và lúc click Play -> fail-closed, 0 new play, tooltip tiếng Việt."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "temp_fixture.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(
        id="sfx_temp",
        name="Temp Fixture",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=2.0,
    )

    widget.set_asset(asset)
    assert widget.btn_play.isEnabled() is True
    assert widget.resolved_file_path == str(audio_file.resolve())

    # Xóa file fixture trên đĩa
    audio_file.unlink()
    assert not audio_file.exists()

    # Bấm phát -> action-time recheck phải từ chối
    widget.toggle_playback()

    assert player.play_called == 0, "FAIL: Player played missing audio file!"
    assert widget._is_active_playback is False
    assert widget.btn_play.isEnabled() is False
    assert widget.btn_play.text() == "⚠️ Không khả dụng"
    assert "Không khả dụng:" in widget.btn_play.toolTip()
    assert widget.lbl_time.text() == "--:-- / --:--"
    widget.close()


# ---------------------------------------------------------------------------
# 8. STAGING & OFFLINE DENIED
# ---------------------------------------------------------------------------
def test_audio_inspector_staging_and_offline_denied(qapp, tmp_path, monkeypatch):
    """Staging và Kho Offline bị từ chối fail-closed với tooltip tiếng Việt."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "staging_sfx.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()

    # Staging asset
    asset_staging = make_studio_asset(
        id="sfx_staging",
        name="Staging SFX",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="staging",
        duration=2.0,
    )
    widget.set_asset(asset_staging)
    assert widget.btn_play.isEnabled() is False
    assert widget.btn_play.text() == "⚠️ Không khả dụng"
    assert "staging" in widget.btn_play.toolTip().lower()

    # Offline storage
    mock_offline = MockStorage(str(root_dir), "lib_audio_01", online=False)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_offline)

    asset_online_decl = make_studio_asset(
        id="sfx_offline_root",
        name="Offline Root SFX",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=2.0,
    )
    widget.set_asset(asset_online_decl)
    assert widget.btn_play.isEnabled() is False
    assert widget.btn_play.text() == "⚠️ Không khả dụng"
    assert "offline" in widget.btn_play.toolTip().lower() or "kho offline" in widget.btn_play.toolTip().lower()
    widget.close()


# ---------------------------------------------------------------------------
# 9. QUẢN LÝ VOLUME VÀ CLAMP
# ---------------------------------------------------------------------------
def test_audio_inspector_volume_clamp_and_malformed(qapp, monkeypatch):
    """Âm lượng được clamp [0.0, 1.0], dữ liệu lỗi/missing rơi về default 0.85 an toàn."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    widget = AudioInspectorWidget()
    widget._audio_output = MagicMock()

    # Set volume hợp lệ
    widget.set_volume(0.6)
    assert widget.volume == 0.6
    widget._audio_output.setVolume.assert_called_with(0.6)

    # Clamp trên
    widget.set_volume(1.5)
    assert widget.volume == 1.0

    # Clamp dưới
    widget.set_volume(-0.5)
    assert widget.volume == 0.0

    # Giá trị lỗi (NaN, Inf, chuỗi)
    widget.set_volume(float("nan"))
    assert widget.volume == 0.85 or widget.volume == 0.0  # Fallback an toàn

    widget.close()


# ---------------------------------------------------------------------------
# 10. DỪNG CHÉO: INSPECTOR START DỪNG HOVER
# ---------------------------------------------------------------------------
def test_audio_inspector_stop_hover_preview_on_explicit_start(qapp, tmp_path, monkeypatch):
    """Khi Inspector bắt đầu phát âm thanh, callback dừng hover preview của grid được gọi trước player.play."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    stop_hover_mock = MagicMock()
    widget.set_stop_hover_callback(stop_hover_mock)

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    widget.start_playback()

    assert stop_hover_mock.call_count == 1, "FAIL: Hover preview was not stopped when Inspector playback started!"
    assert player.play_called == 1
    widget.close()


# ---------------------------------------------------------------------------
# 11. DỪNG CHÉO: HOVER START DỪNG INSPECTOR
# ---------------------------------------------------------------------------
def test_audio_inspector_hover_starting_stops_inspector(qapp, tmp_path, monkeypatch):
    """Khi SharedHoverPreview bắt đầu phát âm thanh hợp lệ (audio_preview_starting), Inspector tự động dừng."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget
    from src.ui.lux.library.hover_preview import SharedHoverPreview

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)
    widget.start_playback()
    assert widget._is_active_playback is True

    # Tạo hover preview giả lập
    mock_view = MagicMock()
    hover = SharedHoverPreview(mock_view)

    # Nối signal audio_preview_starting tới widget.stop_playback
    assert hasattr(hover, "audio_preview_starting"), "FAIL: SharedHoverPreview must have audio_preview_starting Signal!"
    hover.audio_preview_starting.connect(widget.stop_playback)

    # Phát tín hiệu hover bắt đầu phát âm
    hover.audio_preview_starting.emit()

    assert widget._is_active_playback is False, "FAIL: Inspector was not stopped when hover audio started!"
    assert widget.btn_play.text() == "▶ Nghe thử"
    widget.close()


# ---------------------------------------------------------------------------
# 12. ẨN / ĐÓNG WIDGET DỪNG PHÁT VÀ DỌN SOURCE
# ---------------------------------------------------------------------------
def test_audio_inspector_hide_and_close_stops_and_clears(qapp, tmp_path, monkeypatch):
    """hideEvent và closeEvent dừng phát sạch sẽ và dọn source về QUrl()."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "sfx.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)
    widget.start_playback()
    assert widget._is_active_playback is True

    # Ẩn widget
    widget.hideEvent(None)
    assert widget._is_active_playback is False
    assert player.source() == QUrl()

    # Phát lại rồi đóng widget
    widget.start_playback()
    assert widget._is_active_playback is True
    widget.closeEvent(None)
    assert widget._is_active_playback is False
    assert player.source() == QUrl()
    widget.close()


# ---------------------------------------------------------------------------
# 13. ĐĂNG KÝ AUDIODEVICEROUTER ĐÚNG 1 LẦN
# ---------------------------------------------------------------------------
def test_audio_inspector_audio_router_registration_count_1(qapp, tmp_path, monkeypatch):
    """QAudioOutput chỉ được đăng ký duy nhất 1 lần với AudioDeviceRouter."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    router = get_audio_router()
    widget = AudioInspectorWidget()

    # Lần 1: gọi _ensure_player()
    widget._ensure_player()
    output1 = widget._audio_output
    assert output1 in router._registered_outputs

    # Lần 2: gọi lại _ensure_player() không tạo player mới hay đăng ký thêm
    widget._ensure_player()
    assert widget._audio_output is output1

    widget.close()


# ---------------------------------------------------------------------------
# 14. KHẢ NĂNG CHỐNG CHỊU TÍN HIỆU ĐỒNG BỘ TỪ SETSOURCE
# ---------------------------------------------------------------------------
def test_audio_inspector_synchronous_setsource_state_resilience(qapp, tmp_path, monkeypatch):
    """Tín hiệu StoppedState hoặc Loading phát ra đồng bộ khi setSource không làm chết cờ active trước khi play."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()

    # Giả lập player phát StoppedState ngay khi setSource được gọi
    original_set_source = player.setSource
    def set_source_with_sync_stopped(src):
        original_set_source(src)
        player.playbackStateChanged.emit(QMediaPlayer.StoppedState)
    player.setSource = set_source_with_sync_stopped

    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    widget.start_playback()

    # Cờ active vẫn phải sống và nút hiển thị đúng trạng thái Playing
    assert widget._is_active_playback is True
    assert player.play_called == 1
    assert widget.btn_play.text() == "⏸ Dừng"
    widget.close()


# ---------------------------------------------------------------------------
# 15. WAVEFORMCANVAS VẠCH TIẾN TRÌNH VÀ SAFE UNKNOWN DURATION
# ---------------------------------------------------------------------------
def test_audio_inspector_waveform_progress_and_decorative_nature(qapp):
    """WaveformCanvas vẽ phong bì trang trí, tính toán vạch tiến trình an toàn với duration=0."""
    canvas = WaveformCanvas(preset_id="sfx_whoosh", num_bars=32, height=56)
    assert len(canvas.bars) == 32

    # Vị trí hợp lệ
    canvas.set_playback_progress(500, 2000)
    assert math.isclose(canvas.progress_fraction, 0.25, rel_tol=1e-3)

    # Duration = 0 không gây ZeroDivisionError
    canvas.set_playback_progress(500, 0)
    assert canvas.progress_fraction == 0.0

    # Duration âm hoặc None
    canvas.set_playback_progress(500, -100)
    assert canvas.progress_fraction == 0.0

    # Vị trí vượt quá duration được clamp về 1.0
    canvas.set_playback_progress(3000, 2000)
    assert canvas.progress_fraction == 1.0


# ---------------------------------------------------------------------------
# 16. AN TOÀN TUYỆT ĐỐI VỚI DURATION METADATA BẤT THƯỜNG (R1)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_dur", [
    None,
    0,
    -10.5,
    True,
    False,
    "bad",
    "invalid",
    float("nan"),
    float("inf"),
    float("-inf"),
    10**50,
])
def test_audio_inspector_duration_metadata_safeparse_and_no_crash(qapp, tmp_path, monkeypatch, bad_dur):
    """Nạp asset với các giá trị duration dị thường (bad/nan/inf/bool/neg/bigint) không gây crash."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "sfx_dur.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()

    asset = make_studio_asset(
        id="sfx_dur_test",
        name="SFX Bad Duration",
        tab="sfx",
        category="sfx",
        source_kind="managed",
        origin_library_id="lib_audio_01",
        origin_root=str(root_dir),
        file_path=str(audio_file),
        storage_managed=True,
        availability="online",
        duration=bad_dur,
    )

    # Không được ném ngoại lệ ValueError/OverflowError/TypeError
    widget.set_asset(asset)

    assert widget._player is None, "Player không được khởi tạo eager!"
    assert widget._is_active_playback is False, "0 autoplay!"
    assert widget.btn_play.isEnabled() is True
    # Label hiển thị thời gian hợp lý hoặc placeholder
    assert widget.lbl_time.text() in ("00:00 / --:--", "--:-- / --:--") or "00:00" in widget.lbl_time.text()


# ---------------------------------------------------------------------------
# 17. MUTED OUTPUT KHÔNG PHÁT TÍN HIỆU CHIẾM QUYỀN INSPECTOR (R1)
# ---------------------------------------------------------------------------
def test_hover_preview_muted_output_does_not_emit_start(qapp, tmp_path, monkeypatch):
    """Khi AudioOutput của SharedHoverPreview đang muted (isMuted=True), không phát tín hiệu dừng Inspector."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget
    from src.ui.lux.library.hover_preview import SharedHoverPreview

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    # Inspector đang phát
    inspector = AudioInspectorWidget()
    player = FakePlayer()
    inspector._player = player
    inspector._audio_output = MagicMock()
    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    inspector.set_asset(asset)
    inspector.start_playback()
    assert inspector._is_active_playback is True

    # Tạo hover preview có AudioOutput giả lập isMuted() = True
    mock_view = MagicMock()
    mock_view.visualRect.return_value = QRect(0, 0, 160, 90)
    mock_view.viewport.return_value = QWidget()
    hover = SharedHoverPreview(mock_view)
    mock_player = FakePlayer()
    mock_output = MagicMock()
    mock_output.isMuted.return_value = True
    hover._player = mock_player
    hover._audio_output = mock_output
    hover._enable_audio_preview = True
    hover._volume = 0.85

    # Wire signal từ hover tới stop của inspector
    hover.audio_preview_starting.connect(inspector.stop_playback)

    emitted_count = [0]
    hover.audio_preview_starting.connect(lambda: emitted_count.__setitem__(0, emitted_count[0] + 1))

    # 1. Gọi _play_audio khi output muted -> Không được phát tín hiệu
    hover._play_audio(str(audio_file))
    assert emitted_count[0] == 0, "FAIL: audio_preview_starting was emitted while output is muted!"
    assert inspector._is_active_playback is True, "FAIL: Inspector was stopped by muted hover!"

    # 2. Gọi _play_video khi output muted -> Không được phát tín hiệu
    video_file = root_dir / "clip.mp4"
    video_file.write_bytes(b"ftypmp42" + b"\x00" * 32)
    mock_idx = MagicMock()
    hover._play_video(mock_idx, str(video_file))
    assert emitted_count[0] == 0, "FAIL: audio_preview_starting was emitted for muted video!"
    assert inspector._is_active_playback is True

    # 3. Khi unmute (isMuted = False) -> Tín hiệu phát đúng 1 lần và dừng Inspector
    mock_output.isMuted.return_value = False
    hover._play_audio(str(audio_file))
    assert emitted_count[0] == 1, "FAIL: audio_preview_starting should emit when unmuted!"
    assert inspector._is_active_playback is False, "FAIL: Inspector should be stopped by audible hover!"


# ---------------------------------------------------------------------------
# 18. TRẠNG THÁI WAVEFORM VÀ NÚT BẤM CHỈ KÍCH HOẠT KHI NHẬN PLAYINGSTATE THẬT (R1)
# ---------------------------------------------------------------------------
def test_audio_inspector_playing_state_strict_and_synchronous_error(qapp, tmp_path, monkeypatch):
    """Waveform và nhãn nút 'Dừng' chỉ bật khi nhận PlayingState; lỗi đồng bộ không bị ghi đè."""
    from src.ui.tabs.tab_assets import AudioInspectorWidget

    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = AudioInspectorWidget()
    player = FakePlayer()
    widget._player = player
    widget._audio_output = MagicMock()

    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    # FakePlayer play() không tự động phát stateChanged nếu ta ghi đè
    def delayed_play():
        player.play_called += 1
    player.play = delayed_play

    widget.toggle_playback()

    # Chưa nhận PlayingState: waveform KHÔNG được bật playing
    assert widget.waveform_canvas.is_playing is False, "Waveform không được bật trước khi có PlayingState!"
    assert widget.btn_play.text() != "⏸ Dừng", "Nút không được hiển thị 'Dừng' trước khi phát thật!"

    # Khi signal playbackStateChanged(PlayingState) thực sự phát ra:
    player.playbackStateChanged.emit(QMediaPlayer.PlayingState)
    assert widget.waveform_canvas.is_playing is True
    assert widget.btn_play.text() == "⏸ Dừng"

    # Lỗi giải mã đồng bộ phát ra
    player.errorOccurred.emit(QMediaPlayer.ResourceError, "Unrecognized format")
    assert widget.btn_play.text() == "⚠️ Lỗi phát âm thanh"
    assert widget.btn_play.isEnabled() is False
    assert widget.waveform_canvas.is_playing is False


# ---------------------------------------------------------------------------
# 19. WIRING DỪNG CHÉO THỰC TẾ TRÊN TABASSETS UI (R1)
# ---------------------------------------------------------------------------
def test_audio_inspector_tab_assets_actual_cross_stop_wiring(qapp, tmp_path, monkeypatch):
    """Xác nhận việc nối dây dừng chéo 2 chiều giữa AssetGridView.hover_controller và AudioInspectorWidget trên TabAssets."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    audio_file = root_dir / "whoosh.wav"
    audio_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVE" + b"\x00" * 36)

    mock_storage = MockStorage(str(root_dir), "lib_audio_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    tab = TabAssets(headless=True)
    assert hasattr(tab, "audio_inspector_widget") and tab.audio_inspector_widget is not None
    assert hasattr(tab, "asset_grid") and hasattr(tab.asset_grid, "hover_controller")

    inspector = tab.audio_inspector_widget
    hover = tab.asset_grid.hover_controller

    # 1. Inspector start dừng hover preview
    hover_stopped = [False]
    hover.stop = lambda: hover_stopped.__setitem__(0, True)

    inspector._player = FakePlayer()
    inspector._audio_output = MagicMock()
    asset = make_studio_asset(id="a", name="A", tab="sfx", source_kind="managed", origin_library_id="lib_audio_01", origin_root=str(root_dir), file_path=str(audio_file), storage_managed=True, availability="online")
    inspector.set_asset(asset)
    inspector.start_playback()

    assert hover_stopped[0] is True, "Inspector start phải gọi hover.stop()!"
    assert inspector._is_active_playback is True

    # 2. Hover audio starting dừng Inspector
    hover.audio_preview_starting.emit()
    assert inspector._is_active_playback is False, "Hover starting phải dừng AudioInspectorWidget!"


