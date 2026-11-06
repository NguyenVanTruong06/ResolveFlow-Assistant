"""
tests/ui/test_hover_preview_authority.py
Kiểm thử tích hợp cho SharedHoverPreview với ActionGuard Preview Authority (G04-04C6b-I).
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QPoint, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer

from src.ui.lux.library.asset_grid import AssetGridView
from src.ui.lux.library.asset_model import AssetListModel
from src.ui.lux.library.hover_preview import SharedHoverPreview
from src.core.text_preset import TextStylePreset



@pytest.fixture(scope="session")
def qapp():
    """Đảm bảo QApplication duy nhất cho toàn bộ session test."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolate_thumbnail_and_temp(monkeypatch, tmp_path):
    """
    Cô lập hoàn toàn I/O nền trong test:
    1. Chặn ThreadPoolExecutor gọi cv2/ffmpeg qua request_thumbnail_async và extract_thumbnail.
    2. Monkeypatch tempfile.tempdir vào tmp_path.
    """
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(
        "src.core.asset_cache.request_thumbnail_async",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.core.asset_cache.extract_thumbnail",
        lambda *args, **kwargs: None,
    )


@pytest.fixture
def grid_preview_factory(qapp):
    """Quản lý vòng đời view và hover preview, đảm bảo dọn dẹp sạch sẽ sau test."""
    created_views = []

    def _create(assets):
        model = AssetListModel(assets)
        grid = AssetGridView()
        grid.setModel(model)
        grid.resize(800, 600)
        grid.show()
        qapp.processEvents()
        created_views.append(grid)
        return grid, grid.hover_controller

    yield _create

    for g in created_views:
        try:
            g.hover_controller.stop()
            g.close()
            g.deleteLater()
        except Exception:
            pass
    qapp.processEvents()


# ==============================================================================
# UI Authority & Lifecycle Integration Tests
# ==============================================================================

def test_hover_preview_denied_guard_never_inits_player_or_shows_overlay(qapp, grid_preview_factory, tmp_path):
    """Khi Guard từ chối (ví dụ offline), SharedHoverPreview không bao giờ khởi tạo player hay hiện overlay."""
    root = tmp_path / "offline_root"
    root.mkdir(parents=True, exist_ok=True)
    real_file = root / "proxy.mp4"
    real_file.write_bytes(b"data")

    # Asset managed nhưng kho offline
    asset = {
        "id": "denied_managed",
        "name": "Denied Video",
        "source_kind": "managed",
        "origin_library_id": "lib_off",
        "origin_root": str(root),
        "library_path": "proxy.mp4",
        "preview_path": str(real_file),
        "file_path": str(real_file),
        "availability": "offline",
    }
    grid, hover = grid_preview_factory([asset])

    # Kích hoạt hover timeout
    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    assert hover._player is None
    assert hover._overlay is None or not hover._overlay.isVisible()
    assert hover._is_active_playback is False


def test_hover_preview_uses_guard_resolved_path(qapp, grid_preview_factory, tmp_path):
    """SharedHoverPreview sử dụng đúng resolved_path từ Guard để cấp cho player.setSource()."""
    ext_dir = tmp_path / "footage"
    ext_dir.mkdir(parents=True, exist_ok=True)
    master_file = ext_dir / "drone_shot.mov"
    master_file.write_bytes(b"master")
    companion_proxy = ext_dir / "drone_shot.mp4"
    companion_proxy.write_bytes(b"companion proxy")

    asset = {
        "id": "linked_comp_01",
        "name": "Drone Shot",
        "source_kind": "linked",
        "external_path": str(master_file),
        "preview_path": str(companion_proxy),
    }
    grid, hover = grid_preview_factory([asset])

    # Khởi tạo fake player spy
    player = hover._ensure_player()
    set_source_mock = MagicMock()
    player.setSource = set_source_mock
    play_mock = MagicMock()
    player.play = play_mock

    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    # Xác nhận player nhận đúng companion_proxy đã verify
    expected_url = QUrl.fromLocalFile(os.path.abspath(str(companion_proxy)))
    assert hover._is_active_playback is True
    assert hover._current_source_file == str(companion_proxy.resolve())
    set_source_mock.assert_called_once_with(expected_url)
    play_mock.assert_called_once()


def test_hover_preview_procedural_canvas_never_plays_media(qapp, grid_preview_factory):
    """TextStylePreset trả về OK_PROCEDURAL_PREVIEW_CANVAS -> SharedHoverPreview không khởi tạo media player."""
    preset = TextStylePreset(id="text_kinetic_01", name="Neon Kinetic")
    asset = {
        "id": "text_kinetic_01",
        "name": "Neon Kinetic",
        "category": "text",
        "_raw_obj": preset,
    }
    grid, hover = grid_preview_factory([asset])

    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    assert hover._player is None
    assert hover._is_active_playback is False


def test_hover_preview_switching_from_allowed_to_denied_stops_playback(qapp, grid_preview_factory, tmp_path):
    """Đang phát asset A (hợp lệ), chuyển sang asset B (bị từ chối) -> lập tức dừng phát, ẩn overlay."""
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    file_a = media_dir / "clip_a.mp4"
    file_a.write_bytes(b"media a")

    asset_a = {
        "id": "asset_a",
        "name": "Clip A",
        "file_path": str(file_a),
    }
    asset_b = {
        "id": "asset_b",
        "name": "Clip B Staging",
        "availability": "staging",
    }
    grid, hover = grid_preview_factory([asset_a, asset_b])

    # 1. Phát asset A
    player = hover._ensure_player()
    player.setSource = MagicMock()
    player.play = MagicMock()
    player.stop = MagicMock()

    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    assert hover._is_active_playback is True

    # 2. Chuyển sang asset B bị từ chối
    hover._hovered_row = 1
    hover._on_hover_timeout()
    qapp.processEvents()

    assert hover._is_active_playback is False
    assert hover._current_source_file == ""
    player.stop.assert_called()


def test_preview_asset_public_api_authority_guard(qapp, grid_preview_factory, tmp_path):
    """Public API preview_asset() cũng được bảo vệ bởi ActionGuard."""
    grid, hover = grid_preview_factory([])

    # Asset staging bị từ chối
    bad_asset = {
        "id": "bad_asset",
        "name": "Bad Asset",
        "availability": "staging",
        "file_path": str(tmp_path / "some.mp4"),
    }
    hover.preview_asset(bad_asset)
    qapp.processEvents()

    assert hover._player is None
    assert hover._is_active_playback is False


def test_hover_preview_audio_disabled_setting_respected(qapp, grid_preview_factory, tmp_path):
    """Khi tắt audio preview, audio file không được phát."""
    audio_file = tmp_path / "sfx.wav"
    audio_file.write_bytes(b"wav audio data")

    asset = {
        "id": "sfx_01",
        "name": "Click SFX",
        "file_path": str(audio_file),
    }
    grid, hover = grid_preview_factory([asset])
    hover.set_audio_preview_enabled(False)

    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    assert hover._player is None
    assert hover._is_active_playback is False


def test_hover_preview_mapping_proxy_preserves_managed_identity(qapp, grid_preview_factory, tmp_path):
    """Probe 3: MappingProxyType chứa metadata managed không khớp root/ID phải giữ nguyên
    provenance (source_kind, origin_library_id, origin_root), không bị lọt qua thành legacy."""
    from types import MappingProxyType
    from src.ui.lux.library.hover_preview import _normalize_preview_payload

    active_root = tmp_path / "active_storage"
    active_root.mkdir(parents=True, exist_ok=True)
    real_video = active_root / "real_video.mp4"
    real_video.write_bytes(b"video data")

    # Record managed nhưng thuộc thư mục kho khác (foreign root)
    managed_dict = {
        "id": "foreign_managed",
        "name": "Foreign Video",
        "source_kind": "managed",
        "origin_library_id": "lib_old_999",
        "origin_root": "/some/foreign/root",
        "library_path": "real_video.mp4",
        "file_path": str(real_video),
        "preview_path": str(real_video),
    }
    proxy_record = MappingProxyType(managed_dict)

    # 1. Kiểm tra chuẩn hóa giữ nguyên provenance
    norm = _normalize_preview_payload(proxy_record)
    assert norm["source_kind"] == "managed"
    assert norm["origin_library_id"] == "lib_old_999"
    assert norm["origin_root"] == "/some/foreign/root"

    # 2. Kiểm tra UI consumer: khi guard deny thì player không bao giờ được khởi tạo (lazy)
    grid, hover = grid_preview_factory([proxy_record])

    hover._hovered_row = 0
    hover._on_hover_timeout()
    qapp.processEvents()

    # Phải bị Action Guard từ chối (ERR_FOREIGN_ROOT), không khởi tạo player
    assert hover._player is None
    assert hover._is_active_playback is False


