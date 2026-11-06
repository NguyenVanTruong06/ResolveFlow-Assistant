"""
tests/ui/test_tab_assets_video_inspector_guard.py
Bộ kiểm thử TDD cho Task G04-04C8-I:
Guard Video Inspector, triệt tiêu repo alias và playback giả.

Bao gồm:
1. Managed media positive preview: nạp đúng realpath, không autoplay, kích hoạt phát video thật.
2. Missing media negative fail-closed: 0 player source, 0 simulation, không alias fallback, controls disabled.
3. Offline storage negative: từ chối fail-closed.
4. Staging asset negative: từ chối fail-closed.
5. Root ID swap negative: từ chối fail-closed.
6. Zero repo alias fallback: meme_cat_cry khi mất file không tự ý nạp meme_meo_khoc_banana.mp4.
7. Zero simulation: không _sim_timer, không _is_simulating, không huy hiệu "🔴 LIVE", lỗi codec báo lỗi thực.
8. Native transition demo preview: phát file demo MP4 hợp lệ; không có demo -> không crash/không giả lập.
9. Static icon / sticker: canvas hiển thị ảnh, control bar ẩn, 0 playback.
10. Lifecycle selection switch & stale callbacks: chuyển A -> B dừng sạch; callback A gửi sau khi B active bị hủy bỏ.
11. Lifecycle hide & close: ẩn/đóng widget lập tức dừng phát và dọn dẹp source.
12. Tương thích ngược set_item: hoạt động qua guard, không làm gãy callers cũ.
13. Browse button disabled/hidden và safe no-op.
14. Action-time recheck: xóa file giữa lúc chọn và lúc bấm Play -> từ chối tại action-time.
15. Integration TabAssets._update_inspector_details: truyền full selected_asset có đủ provenance.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QUrl, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer

from src.core.transition_preset import TransitionStylePreset
from src.ui.tabs.tab_assets import (
    TabAssets,
    IconMemePreviewWidget,
    StudioAsset,
)


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
    kwargs.setdefault("category", kwargs.get("tab", "meme"))
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


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolate_thumbnail_and_temp(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def test_video_inspector_positive_managed_media(qapp, tmp_path, monkeypatch):
    """1. Asset video hợp lệ trong kho: nạp đúng realpath, không autoplay, bấm Play phát thật."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "valid_meme.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 100)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    widget.show()

    asset = make_studio_asset(
        id="meme_valid_01",
        name="Valid Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="online"
    )

    widget.set_asset(asset)

    # Khẳng định không autoplay
    assert widget._player is None or widget._player.playbackState() != QMediaPlayer.PlayingState
    assert widget.resolved_file_path == str(video_file.resolve())
    assert widget.btn_play.isEnabled() is True
    assert "Phát video" in widget.btn_play.text() or "Xem video" in widget.btn_play.text()

    # Fake QMediaPlayer để kiểm tra lệnh play
    mock_player = MagicMock()
    mock_player.playbackState.return_value = QMediaPlayer.StoppedState
    widget._player = mock_player

    widget._toggle_playback()

    assert mock_player.setSource.called
    assert mock_player.play.called
    assert getattr(widget, "_is_simulating", False) is False
    assert not getattr(widget, "_sim_timer", MagicMock(isActive=lambda: False)).isActive()

    widget.stop_playback()
    widget.close()


def test_video_inspector_missing_media_fail_closed(qapp, tmp_path, monkeypatch):
    """2. Asset video mất file: Fail-closed, 0 player source, 0 simulation, controls disabled."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    missing_file = root_dir / "non_existent.mp4"

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    widget.show()

    asset = make_studio_asset(
        id="meme_missing_01",
        name="Missing Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(missing_file),
        storage_managed=True,
        availability="online"
    )

    widget.set_asset(asset)

    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False
    assert widget.btn_replay.isEnabled() is False
    assert "--:--" in widget.lbl_time.text()

    # Thử gọi _toggle_playback, _start_playback, _replay
    mock_player = MagicMock()
    widget._player = mock_player

    widget._toggle_playback()
    widget._start_playback()
    widget._replay()

    assert not mock_player.play.called
    assert getattr(widget, "_is_simulating", False) is False
    assert not getattr(widget, "_sim_timer", MagicMock(isActive=lambda: False)).isActive()

    widget.close()


def test_video_inspector_offline_storage_fail_closed(qapp, tmp_path, monkeypatch):
    """3. Ổ đĩa offline: Từ chối fail-closed, nút phát bị vô hiệu hóa."""
    root_dir = tmp_path / "offline_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "test.mp4"
    video_file.write_bytes(b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_offline", online=False)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(
        id="meme_offline_01",
        name="Offline Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_offline",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="offline"
    )

    widget.set_asset(asset)

    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False
    assert "Kho Offline" in widget.btn_play.toolTip()

    # Late callbacks từ session cũ không được làm mất lý do offline
    widget._on_playback_state_changed(QMediaPlayer.PlayingState)
    assert widget.btn_play.isEnabled() is False
    assert "Kho Offline" in widget.btn_play.toolTip()
    widget.close()


def test_video_inspector_staging_asset_fail_closed(qapp, tmp_path, monkeypatch):
    """4. Asset staging: Từ chối fail-closed."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "staging.mp4"
    video_file.write_bytes(b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(
        id="meme_staging_01",
        name="Staging Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="staging"
    )

    widget.set_asset(asset)

    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False
    assert "staging" in widget.btn_play.toolTip().lower()
    widget.close()


def test_video_inspector_root_id_swap_fail_closed(qapp, tmp_path, monkeypatch):
    """5. Tráo đổi root ID / origin root: Từ chối fail-closed."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "swapped.mp4"
    video_file.write_bytes(b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(
        id="meme_swapped_01",
        name="Swapped Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_DIFFERENT",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="online"
    )

    widget.set_asset(asset)

    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False
    widget.close()


def test_video_inspector_no_repo_alias_fallback(qapp, tmp_path, monkeypatch):
    """6. Asset id meme_cat_cry khi thiếu file KHÔNG tự ý nạp meme_meo_khoc_banana.mp4."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(
        id="meme_cat_cry",
        name="Mèo Khóc",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(root_dir / "meme_cat_cry.mp4"),
        storage_managed=True,
        availability="online"
    )

    widget.set_asset(asset)

    assert widget.resolved_file_path == ""
    assert "meme_meo_khoc_banana" not in widget.resolved_file_path
    assert widget.btn_play.isEnabled() is False
    widget.close()


def test_video_inspector_zero_simulation_on_codec_error(qapp, tmp_path, monkeypatch):
    """7. Lỗi giải mã video (codec error): hiển thị lỗi thực, KHÔNG bật simulation giả lập."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "corrupt.mp4"
    video_file.write_bytes(b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(
        id="meme_corrupt",
        name="Corrupt Meme",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="online"
    )

    widget.set_asset(asset)
    widget._is_active_playback = True
    widget._on_player_error(QMediaPlayer.Error.ResourceError, "Decoder failed")

    assert getattr(widget, "_is_simulating", False) is False
    assert not getattr(widget, "_sim_timer", MagicMock(isActive=lambda: False)).isActive()
    assert widget.btn_play.text() == "⚠️ Lỗi phát video"
    assert "Decoder failed" in widget.btn_play.toolTip()
    assert widget._is_active_playback is False
    widget.close()


def test_video_inspector_native_transition_demo(qapp, tmp_path, monkeypatch):
    """8. Chuyển cảnh native có video demo: phát demo MP4; không có demo: không crash."""
    demo_video = tmp_path / "transition_demo.mp4"
    demo_video.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    trans_preset = make_studio_asset(
        id="trans_zoom_in",
        name="Zoom In",
        tab="trans",
        category="transform",
        frames=12,
        preview_path=str(demo_video)
    )

    widget = IconMemePreviewWidget()
    widget._bundled_root = tmp_path
    widget.set_asset(trans_preset)

    assert widget.resolved_file_path == str(demo_video.resolve())
    assert widget.btn_play.isEnabled() is True

    # Transition không có demo
    trans_no_demo = make_studio_asset(
        id="trans_no_demo",
        name="No Demo Trans",
        tab="trans",
        category="transform",
        frames=12,
        preview_path=""
    )
    widget.set_asset(trans_no_demo)
    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False

    widget.close()


def test_video_inspector_static_sticker_no_playback(qapp):
    """9. Sticker tĩnh: control bar ẩn, canvas mode, không nạp player."""
    widget = IconMemePreviewWidget()
    sticker_asset = {
        "id": "sticker_star",
        "name": "Star Sticker",
        "tab": "icon",
        "category": "icon",
        "source_kind": "builtin",
        "file_path": ""
    }

    widget.set_asset(sticker_asset)

    assert widget.control_bar.isHidden() is True
    assert widget.height() == 136
    assert widget.resolved_file_path == ""
    widget.close()


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
        self._source = QUrl()
        self._state = QMediaPlayer.StoppedState
        self._pos = 0
        self.stop_called = 0
        self.play_called = 0

    def setSource(self, s):
        self._source = s

    def source(self):
        return self._source

    def playbackState(self):
        return self._state

    def play(self):
        self.play_called += 1
        self._state = QMediaPlayer.PlayingState

    def pause(self):
        self._state = QMediaPlayer.PausedState

    def stop(self):
        self.stop_called += 1
        self._state = QMediaPlayer.StoppedState

    def setPosition(self, p):
        self._pos = p

    def disconnect(self, c):
        self.positionChanged.disconnect(c)
        self.durationChanged.disconnect(c)
        self.playbackStateChanged.disconnect(c)
        self.errorOccurred.disconnect(c)


def test_video_inspector_lifecycle_selection_switch_and_stale_callbacks(qapp, tmp_path, monkeypatch):
    """10. Chuyển selection dừng sạch; stale callback từ video cũ bị loại bỏ."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_a = root_dir / "video_a.mp4"
    video_a.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)
    video_b = root_dir / "video_b.mp4"
    video_b.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    player = FakePlayer()
    widget._player = player

    asset_a = make_studio_asset(id="a", name="A", tab="meme", source_kind="managed", origin_library_id="lib_01", origin_root=str(root_dir), file_path=str(video_a), storage_managed=True, availability="online")
    asset_b = make_studio_asset(id="b", name="B", tab="meme", source_kind="managed", origin_library_id="lib_01", origin_root=str(root_dir), file_path=str(video_b), storage_managed=True, availability="online")

    # 1. Chọn và phát A
    widget.set_asset(asset_a)
    widget._start_playback()
    assert widget._is_active_playback is True
    assert len(player.positionChanged.cbs) == 1
    pos_cb_a = player.positionChanged.cbs[0]
    dur_cb_a = player.durationChanged.cbs[0]
    state_cb_a = player.playbackStateChanged.cbs[0]
    err_cb_a = player.errorOccurred.cbs[0]

    player.positionChanged.emit(100)
    assert widget._position_ms == 100

    # 2. Chọn B: A phải dừng, source phải clear, position & duration phải reset về 0
    epoch_a = getattr(widget, "_playback_epoch", 0)
    widget.set_asset(asset_b)
    assert player.stop_called >= 1
    assert player.source() == QUrl()
    assert widget._position_ms == 0
    assert widget._duration_ms == 0
    assert widget._is_active_playback is False
    epoch_b = getattr(widget, "_playback_epoch", 0)
    assert epoch_b > epoch_a

    # 3. Bắt đầu phát B với token mới
    widget._start_playback()
    assert widget._is_active_playback is True
    assert len(player.positionChanged.cbs) == 1  # Không bị duplicate connection!
    player.positionChanged.emit(500)
    assert widget._position_ms == 500

    # 4. Bắn stale callbacks từ A khi B đang active -> B hoàn toàn không bị ảnh hưởng!
    pos_cb_a(99000)
    assert widget._position_ms == 500  # Không bị 99000 ghi đè!
    dur_cb_a(120000)
    assert widget._duration_ms != 120000

    err_cb_a(QMediaPlayer.Error.ResourceError, "Stale error from A")
    assert widget._is_active_playback is True  # Lỗi từ A không được dừng B!
    assert "Dừng phát" in widget.btn_play.text()

    # 5. Dừng B và bắn late PlayingState từ A khi inactive -> không đổi nút thành "Tạm dừng"
    widget.stop_playback()
    assert widget._is_active_playback is False
    assert widget.btn_play.text() == "▶ Phát video"

    state_cb_a(QMediaPlayer.PlayingState)
    assert widget.btn_play.text() == "▶ Phát video"

    widget.close()


def test_video_inspector_lifecycle_hide_and_close(qapp, tmp_path, monkeypatch):
    """11. Ẩn / đóng widget: dừng phát lập tức và clear source."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "video.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    widget.show()

    asset = make_studio_asset(id="vid", name="Vid", tab="meme", source_kind="managed", origin_library_id="lib_01", origin_root=str(root_dir), file_path=str(video_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    mock_player = MagicMock()
    mock_player.playbackState.return_value = QMediaPlayer.PlayingState
    widget._player = mock_player
    widget._is_active_playback = True

    widget.hide()

    assert mock_player.stop.called
    assert widget._is_active_playback is False

    widget.close()


def test_video_inspector_backward_compat_set_item(qapp, tmp_path, monkeypatch):
    """12. Gọi set_item theo signature cũ vẫn hoạt động qua guard an toàn."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "compat.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()

    # Positive scalar call
    widget.set_item("Compat Meme", "meme", "🎬", "#fff", "sub", file_path=str(video_file))
    assert widget.resolved_file_path != ""
    assert widget.btn_play.isEnabled() is True

    # Negative scalar call (missing file)
    widget.set_item("Missing Meme", "meme", "🎬", "#fff", "sub", file_path=str(root_dir / "missing.mp4"))
    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False
    assert getattr(widget, "_is_simulating", False) is False

    widget.close()


def test_video_inspector_browse_button_disabled_and_safe_noop(qapp):
    """13. Nút btn_browse bị vô hiệu hóa và _browse_file là safe no-op."""
    widget = IconMemePreviewWidget()

    assert widget.btn_browse.isEnabled() is False or widget.btn_browse.isHidden() is True
    # Gọi trực tiếp _browse_file không được mở dialog hay thay đổi resolved_file_path
    curr_path = widget.resolved_file_path
    widget._browse_file()
    assert widget.resolved_file_path == curr_path

    widget.close()


def test_video_inspector_action_time_recheck(qapp, tmp_path, monkeypatch):
    """14. Recheck tại action-time: file bị xóa trước khi bấm Play -> từ chối phát."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "delete_me.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    asset = make_studio_asset(id="del", name="Del", tab="meme", source_kind="managed", origin_library_id="lib_01", origin_root=str(root_dir), file_path=str(video_file), storage_managed=True, availability="online")

    widget.set_asset(asset)
    assert widget.resolved_file_path != ""
    assert widget.btn_play.isEnabled() is True

    # Xóa file sau khi selection đã xong!
    video_file.unlink()

    mock_player = MagicMock()
    widget._player = mock_player

    # Bấm Play -> Action-time guard phải phát hiện file đã mất
    widget._toggle_playback()

    assert not mock_player.play.called
    assert widget.resolved_file_path == ""
    assert widget.btn_play.isEnabled() is False

    widget.close()


def test_tab_assets_update_inspector_details_passes_full_selected_asset(qapp, tmp_path, monkeypatch):
    """15. TabAssets._update_inspector_details chuyển full selected_asset có đủ provenance."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "meme_full.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    tab = TabAssets()
    tab.show()

    asset = make_studio_asset(
        id="meme_full_01",
        name="Full Asset",
        tab="meme",
        category="meme",
        source_kind="managed",
        origin_library_id="lib_01",
        origin_root=str(root_dir),
        file_path=str(video_file),
        storage_managed=True,
        availability="online"
    )
    tab.selected_asset = asset

    # Spy icon_meme_widget.set_asset
    captured_assets = []
    orig_set_asset = getattr(tab.icon_meme_widget, "set_asset", None)

    def spy_set_asset(target, sub=""):
        captured_assets.append(target)
        if orig_set_asset:
            return orig_set_asset(target, sub)

    monkeypatch.setattr(tab.icon_meme_widget, "set_asset", spy_set_asset)

    tab._update_inspector_details()

    assert len(captured_assets) == 1
    passed = captured_assets[0]
    # Khẳng định passed object giữ đầy đủ origin provenance
    assert getattr(passed, "origin_library_id", "") == "lib_01"
    assert getattr(passed, "source_kind", "") == "managed"
    assert getattr(passed, "file_path", "") == str(video_file)

    tab.close()


def test_video_inspector_repeated_start_resume_replay_no_duplicate_connections(qapp, tmp_path, monkeypatch):
    """16. Bắt đầu, tạm dừng, tiếp tục, phát lại nhiều lần: không duplicate signals connection; stop idempotent."""
    root_dir = tmp_path / "storage_root"
    root_dir.mkdir(parents=True, exist_ok=True)
    video_file = root_dir / "loop.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 50)

    mock_storage = MockStorage(str(root_dir), "lib_01", online=True)
    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: mock_storage)

    widget = IconMemePreviewWidget()
    player = FakePlayer()
    widget._player = player

    asset = make_studio_asset(id="loop", name="Loop", tab="meme", source_kind="managed", origin_library_id="lib_01", origin_root=str(root_dir), file_path=str(video_file), storage_managed=True, availability="online")
    widget.set_asset(asset)

    # Start
    widget._start_playback()
    assert len(player.positionChanged.cbs) == 1

    # Pause -> Resume
    widget._toggle_playback()  # pause
    assert widget._player.playbackState() == QMediaPlayer.PausedState
    widget._toggle_playback()  # resume
    assert len(player.positionChanged.cbs) == 1

    # Replay
    widget._replay()
    assert len(player.positionChanged.cbs) == 1

    # Stop repeatedly (idempotent)
    widget.stop_playback()
    assert player.source() == QUrl()
    assert len(player.positionChanged.cbs) == 0

    widget.stop_playback()
    assert player.source() == QUrl()
    assert len(player.positionChanged.cbs) == 0

    widget.close()
