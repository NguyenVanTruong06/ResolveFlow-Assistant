"""
tests/ui/test_hover_preview_lifecycle.py
Bộ kiểm thử TDD cho Vòng đời Hover Preview (G04-04C3-I).

Kiểm tra:
1. Dừng preview (timer/player/overlay) khi model reset (modelAboutToBeReset/modelReset).
2. Dừng preview khi filter loại bỏ asset đang xem trước.
3. Dừng preview khi layout thay đổi hoặc sắp xếp lại (layoutAboutToBeChanged/layoutChanged).
4. Dừng preview khi xóa dòng (rowsAboutToBeRemoved/rowsRemoved).
5. Đổi model / gán None ngắt kết nối model cũ; sự kiện từ model cũ không ảnh hưởng model mới.
6. Gán trùng model không tạo kết nối trùng lặp.
7. Ẩn/đóng/hủy View dừng preview an toàn.
8. Callback EndOfMedia sau khi stop() không tự ý phát lại file cũ.
9. Hover mới hợp lệ sau khi model reset vẫn phát bình thường.
10. Gán model / no-op không eager initialize QMediaPlayer hay Overlay (bảo toàn lazy).
"""
import os
import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt, QPoint, QRect, QUrl, QEvent
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtMultimedia import QMediaPlayer

from src.ui.lux.library.asset_grid import AssetGridView
from src.ui.lux.library.hover_preview import SharedHoverPreview
from src.ui.lux.library.asset_model import (
    AssetListModel,
    IdRole,
    NameRole,
    CategoryRole,
    FilePathRole,
    PreviewPathRole,
    AssetObjRole,
)
from src.ui.lux.library.asset_filter import AssetFilterProxyModel


class FakeMediaPlayer:
    """Mock player để kiểm tra trạng thái mà không cần Qt Multimedia backend thực tế."""
    def __init__(self):
        self.play_calls = 0
        self.stop_calls = 0
        self.position_calls = []
        self.source = None
        self._video_output = None
        self._audio_output = None

    def play(self):
        self.play_calls += 1

    def stop(self):
        self.stop_calls += 1

    def setPosition(self, pos):
        self.position_calls.append(pos)

    def setSource(self, src):
        self.source = src

    def setVideoOutput(self, vo):
        self._video_output = vo

    def setAudioOutput(self, ao):
        self._audio_output = ao


@pytest.fixture
def sample_assets(tmp_path):
    clip1 = tmp_path / "clip1.mp4"
    clip1.write_bytes(b"video1")
    clip2 = tmp_path / "clip2.mp4"
    clip2.write_bytes(b"video2")
    audio1 = tmp_path / "sfx1.wav"
    audio1.write_bytes(b"audio1")

    return [
        {
            "id": "item1",
            "name": "Funny Meme",
            "category": "meme",
            "file_path": str(clip1),
            "preview_path": str(clip1),
            "availability": "online",
        },
        {
            "id": "item2",
            "name": "Action B-Roll",
            "category": "broll",
            "file_path": str(clip2),
            "preview_path": str(clip2),
            "availability": "online",
        },
        {
            "id": "item3",
            "name": "Ding SFX",
            "category": "sfx",
            "file_path": str(audio1),
            "preview_path": str(audio1),
            "availability": "online",
        },
    ]


def test_binding_model_does_not_eagerly_initialize_player_or_overlay(qapp):
    """10. Gán model hoặc noop không khởi tạo eager QMediaPlayer hoặc Overlay (Lazy preserved)."""
    view = AssetGridView()
    model = AssetListModel()
    try:
        view.setModel(model)
        controller = view.hover_controller
        assert controller._player is None, "Không được tự ý tạo QMediaPlayer khi setModel"
        assert controller._overlay is None, "Không được tự ý tạo Overlay khi setModel"
    finally:
        view.close()


def test_same_model_bind_does_not_duplicate_connections(qapp):
    """6. Gán cùng một model nhiều lần không tạo kết nối trùng lặp."""
    view = AssetGridView()
    model = AssetListModel()
    try:
        view.setModel(model)
        controller = view.hover_controller

        # Gọi bind_model lần 2 cùng model
        controller.bind_model(model)

        with patch.object(controller, "stop") as mock_stop:
            # Phát tín hiệu reset 1 lần
            model.modelReset.emit()
            assert mock_stop.call_count == 1, "Chỉ được gọi stop đúng 1 lần khi modelReset"
    finally:
        view.close()


def test_hover_preview_stops_on_model_reset(qapp, sample_assets):
    """1. Khi model reset, preview đang phát hoặc đang chờ timer phải dừng lập tức."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        # Giả lập timer hover đang chạy
        controller._hovered_row = 0
        controller._timer.start(500)
        assert controller._timer.isActive()

        # Model reset
        model.beginResetModel()
        model.endResetModel()

        assert not controller._timer.isActive(), "Timer hover phải dừng khi model reset"
        assert controller._hovered_row == -1, "_hovered_row phải bị reset về -1"
    finally:
        view.close()


def test_hover_preview_stops_when_filter_removes_active_asset(qapp, sample_assets):
    """2. Khi Proxy filter lọc mất asset đang preview, preview phải dừng ngay."""
    view = AssetGridView()
    source_model = AssetListModel()
    source_model.set_assets(sample_assets)
    proxy = AssetFilterProxyModel()
    proxy.setSourceModel(source_model)
    try:
        view.setModel(proxy)
        controller = view.hover_controller

        # Giả lập đang xem trước item 0
        controller._hovered_row = 0
        controller._timer.start(500)

        # Lọc theo category 'sfx' (loại bỏ meme ở dòng 0)
        proxy.set_category("sfx")
        qapp.processEvents()

        assert not controller._timer.isActive()
        assert controller._hovered_row == -1
    finally:
        view.close()


def test_hover_preview_stops_on_layout_reorder_or_sort(qapp, sample_assets):
    """3. Khi layout model thay đổi hoặc sắp xếp lại, preview phải dừng ngay."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        controller._hovered_row = 1
        controller._timer.start(500)

        model.layoutAboutToBeChanged.emit()
        assert not controller._timer.isActive()
        assert controller._hovered_row == -1
    finally:
        view.close()


def test_hover_preview_stops_on_rows_removed(qapp, sample_assets):
    """4. Khi các dòng bị xóa, preview phải dừng ngay."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        controller._hovered_row = 0
        controller._timer.start(500)

        model.rowsAboutToBeRemoved.emit(model.index(0, 0).parent(), 0, 1)
        assert not controller._timer.isActive()
        assert controller._hovered_row == -1
    finally:
        view.close()


def test_hover_preview_swap_model_disconnects_old_model(qapp, sample_assets):
    """5. Đổi model sang model mới: model cũ không còn quyền dừng preview trên view mới."""
    view = AssetGridView()
    old_model = AssetListModel()
    old_model.set_assets(sample_assets)
    new_model = AssetListModel()
    new_model.set_assets(sample_assets)

    try:
        view.setModel(old_model)
        controller = view.hover_controller

        # Đổi sang new_model
        view.setModel(new_model)

        # Giả lập preview đang chạy trên new_model
        controller._hovered_row = 1
        controller._timer.start(500)

        # old_model reset không được làm dừng preview trên new_model
        old_model.modelReset.emit()
        assert controller._timer.isActive(), "old_model không được ảnh hưởng đến new_model sau khi đã swap"
        assert controller._hovered_row == 1

        # new_model reset thì phải dừng
        new_model.modelReset.emit()
        assert not controller._timer.isActive()
        assert controller._hovered_row == -1
    finally:
        view.close()


def test_hover_preview_safe_on_view_hide_and_close(qapp, sample_assets):
    """7. Khi View bị ẩn hoặc đóng, preview dừng an toàn."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        view.show()
        qapp.processEvents()

        controller._hovered_row = 0
        controller._timer.start(500)

        view.hide()
        qapp.processEvents()
        assert not controller._timer.isActive()
        assert controller._hovered_row == -1
    finally:
        view.close()


def test_queued_end_of_media_after_stop_does_not_replay(qapp, sample_assets):
    """8. Sự kiện EndOfMedia phát ra sau khi stop() không được tự ý play() lại file cũ."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        fake_player = FakeMediaPlayer()
        controller._player = fake_player

        # Bắt đầu phát
        controller._hovered_row = 0
        controller._current_source_file = "/path/video.mp4"
        controller._is_active_playback = True

        # Stop preview (người dùng rời chuột hoặc model reset)
        controller.stop()

        assert fake_player.stop_calls >= 1
        assert controller._hovered_row == -1

        # Sự kiện queued EndOfMedia đến muộn
        controller._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)

        # Không được gọi play() hay setPosition(0)
        assert fake_player.play_calls == 0, "EndOfMedia sau khi stop tuyệt đối không được phát lại"
        assert len(fake_player.position_calls) == 0
    finally:
        view.close()


def test_new_hover_after_reset_still_plays(qapp, sample_assets, tmp_path):
    """9. Hover mới hợp lệ sau khi model reset vẫn kích hoạt phát media bình thường."""
    view = AssetGridView()
    model = AssetListModel()
    model.set_assets(sample_assets)
    try:
        view.setModel(model)
        controller = view.hover_controller

        fake_player = FakeMediaPlayer()
        controller._player = fake_player

        # 1. Reset model
        model.beginResetModel()
        model.endResetModel()

        # 2. Người dùng hover vào item 0
        controller._hovered_row = 0
        controller._on_hover_timeout()

        # Player phải được gọi phát video của item 0
        assert fake_player.play_calls == 1
        assert controller._current_source_file == sample_assets[0]["file_path"]
    finally:
        view.close()
