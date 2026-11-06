"""
tests/ui/test_asset_grid_drag_guard.py
Bộ kiểm thử TDD cho bảo vệ kéo thả trên lưới AssetGridView và create_asset_mime_data (G04-04C6a-R1).
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QPoint, QPointF, QUrl, QEvent
from PySide6.QtGui import QMouseEvent, QDrag
from PySide6.QtWidgets import QApplication

from src.core.text_preset import TextStylePreset
from src.core.transition_preset import TransitionStylePreset
from src.ui.lux.library.asset_model import (
    AssetListModel,
    AssetObjRole,
    NameRole,
)
from src.ui.lux.library.asset_grid import (
    AssetGridView,
    create_asset_mime_data,
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


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def isolate_thumbnail_and_temp(tmp_path, monkeypatch):
    """
    Cô lập hoàn toàn thumbnail scheduling và temp root cho toàn bộ test trong module:
    1. Trỏ tempfile.tempdir vào tmp_path để không bao giờ sinh setting rác trong /tmp thật.
    2. Chặn request_thumbnail_async để fake test media không khởi động thread pool cv2/ffmpeg.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with patch("src.core.asset_cache.request_thumbnail_async"):
        yield


@pytest.fixture
def grid_factory(qapp):
    """Fixture tạo và dọn dẹp sạch sẽ AssetGridView sau mỗi test."""
    views = []

    def _create():
        v = AssetGridView()
        views.append(v)
        return v

    yield _create

    for v in views:
        try:
            v.close()
            v.deleteLater()
        except Exception:
            pass
    qapp.processEvents()


# ==============================================================================
# 1. create_asset_mime_data Guard Tests
# ==============================================================================

def test_mime_data_managed_media_success(tmp_path):
    """create_asset_mime_data trả về QMimeData chứa duy nhất QUrl của resolved_path hợp lệ."""
    root = tmp_path / "lib_root"
    media_dir = root / "meme"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_file = media_dir / "pepe.mp4"
    media_file.write_bytes(b"pepe video content")

    lib_id = "lib_pepe"
    storage = MockStorage(root=str(root), library_id=lib_id, online=True)

    asset = {
        "id": "pepe_01",
        "name": "Pepe Frog",
        "category": "meme",
        "source_kind": "managed",
        "origin_library_id": lib_id,
        "origin_root": str(root),
        "library_path": "meme/pepe.mp4",
        "file_path": str(media_file),
    }

    with patch("src.core.storage_manager.StorageManager", return_value=storage):
        mime = create_asset_mime_data(asset)

    assert mime.hasUrls()
    urls = mime.urls()
    assert len(urls) == 1
    assert urls[0].toLocalFile() == str(media_file.resolve())
    assert mime.text() == str(media_file.resolve())


def test_mime_data_native_transition_with_preview_mp4_denied(tmp_path):
    """create_asset_mime_data từ chối transition dù có file preview MP4 thật -> MIME rỗng."""
    preview_mp4 = tmp_path / "trans_preview.mp4"
    preview_mp4.write_bytes(b"preview video")

    # Kiểm tra cả trường hợp category='templates', tab='trans'
    asset = {
        "id": "trans_fade",
        "name": "Cross Dissolve",
        "category": "templates",
        "tab": "trans",
        "file_path": str(preview_mp4),
        "preview_path": str(preview_mp4),
    }

    mime = create_asset_mime_data(asset)
    assert not mime.hasUrls()
    assert not mime.text()  # Không fallback text ID


def test_mime_data_transition_runtime_preset_denied():
    """create_asset_mime_data từ chối TransitionStylePreset thực và không gọi exporter -> MIME rỗng."""
    preset = TransitionStylePreset(id="tr_1", name="Wipe Right", transition_type="Wipe")
    target = {
        "id": "tr_1",
        "name": "Wipe Right",
        "category": "trans",
        "_raw_obj": preset,
    }

    with patch("src.core.transition_preset.TransitionMacroGenerator.export_setting_file") as mock_exp:
        mime = create_asset_mime_data(target)
        mock_exp.assert_not_called()

    assert not mime.hasUrls()
    assert not mime.text()


def test_mime_data_fake_text_without_real_preset_denied():
    """Fake text (chỉ category='text' hoặc giả class TextStylePreset) không gọi exporter -> MIME rỗng."""
    class FakeTextStylePreset:
        pass
    fake_obj = FakeTextStylePreset()
    fake_obj.__class__.__name__ = "TextStylePreset"

    fake_asset = {
        "id": "fake_title",
        "name": "Fake Title",
        "category": "text",
        "_raw_obj": fake_obj,
    }
    with patch("src.core.text_preset.FusionSettingGenerator.export_setting_file") as mock_exp:
        mime = create_asset_mime_data(fake_asset)
        mock_exp.assert_not_called()

    assert not mime.hasUrls()
    assert not mime.text()


def test_mime_data_real_text_style_preset_exports_setting(tmp_path):
    """TextStylePreset thực được phép export file .setting với sample_text và đưa vào MIME."""
    preset = TextStylePreset(id="kinetic_glow", name="Kinetic Glow")
    target = {
        "id": "kinetic_glow",
        "name": "Kinetic Glow",
        "category": "text",
        "_raw_obj": preset,
    }

    mime = create_asset_mime_data(target, sample_text="ChunDVC Intro")
    assert mime.hasUrls()
    local_path = mime.urls()[0].toLocalFile()
    assert os.path.isfile(local_path)
    assert local_path.endswith(".setting")
    assert os.path.basename(local_path).startswith("ChunDVC_Drag_")
    assert os.path.getsize(local_path) > 0


def test_mime_data_temp_ownership_and_path_safety(tmp_path):
    """Temp setting dùng exclusive prefix ChunDVC_Drag_; không ghép untrusted asset_id; không thoát root."""
    preset = TextStylePreset(id="malicious/../../etc/passwd", name="Kinetic Evil")
    target = {
        "id": "../../../etc/passwd",
        "name": "Tiêu Đề / Unicode / Ký Tự Lạ",
        "category": "text",
        "_raw_obj": preset,
    }

    mime = create_asset_mime_data(target, sample_text="Safe Sample")
    assert mime.hasUrls()
    local_path = mime.urls()[0].toLocalFile()
    # Đường dẫn file phải nằm hoàn toàn trong thư mục tmp_path đã monkeypatch
    assert Path(local_path).parent == tmp_path
    assert os.path.basename(local_path).startswith("ChunDVC_Drag_")
    assert "passwd" not in os.path.basename(local_path)
    assert not os.path.exists(tmp_path / "etc" / "passwd")


def test_mime_data_export_failure_cleans_up_owned_file(tmp_path):
    """Khi export thất bại (exception hoặc partial write), file tạm do mình sở hữu phải được dọn dẹp sạch."""
    preset = TextStylePreset(id="faulty_preset", name="Faulty Preset")
    target = {
        "id": "faulty",
        "name": "Faulty",
        "category": "text",
        "_raw_obj": preset,
    }

    def _failing_export(preset_arg, out_path, sample_text=""):
        # Viết dở dang 5 byte rồi raise exception
        with open(out_path, "wb") as f:
            f.write(b"error")
        raise RuntimeError("Disk full simulation")

    with patch("src.core.text_preset.FusionSettingGenerator.export_setting_file", side_effect=_failing_export):
        mime = create_asset_mime_data(target, sample_text="Error test")
        assert not mime.hasUrls()
        # Đảm bảo không để lại file setting rác nào trong tempdir
        assert len(list(tmp_path.glob("ChunDVC_Drag_*.setting"))) == 0


def test_mime_data_wrapper_with_native_preset_text_style():
    """Wrapper chứa native_preset là TextStylePreset được nhận diện và export đúng."""
    preset = TextStylePreset(id="native_text_01", name="Native Title")

    class WrapperItem:
        def __init__(self, p):
            self.id = "wrap_01"
            self.name = "Wrapped Title"
            self.category = "text"
            self.native_preset = p

    target = WrapperItem(preset)
    mime = create_asset_mime_data(target, sample_text="Title Text")
    assert mime.hasUrls()
    local_path = mime.urls()[0].toLocalFile()
    assert os.path.isfile(local_path)
    assert local_path.endswith(".setting")


def test_mime_data_denied_staging_and_missing_file():
    """create_asset_mime_data trả về MIME rỗng khi staging hoặc mất file."""
    # Staging
    asset_staging = {
        "id": "s1",
        "name": "Staging Media",
        "availability": "staging",
        "file_path": "/tmp/any.mp4",
    }
    assert not create_asset_mime_data(asset_staging).hasUrls()

    # Missing file
    asset_missing = {
        "id": "m1",
        "name": "Missing Media",
        "category": "meme",
        "file_path": "/nonexistent/video.mp4",
    }
    assert not create_asset_mime_data(asset_missing).hasUrls()


# ==============================================================================
# 2. AssetGridView.mouseMoveEvent Drag Gating Tests (With & Without Mock)
# ==============================================================================

def test_grid_drag_real_layout_without_index_at_mock(qapp, grid_factory, tmp_path):
    """Test model/view thật, layout thật, visualRect center + mouse press/move (BỎ indexAt mock)."""
    media_file = tmp_path / "valid_clip_real.mp4"
    media_file.write_bytes(b"video bytes")

    asset = {
        "id": "real_clip_01",
        "name": "Real Layout Clip",
        "category": "meme",
        "source_kind": "builtin",
        "file_path": str(media_file),
        "availability": "online",
    }

    model = AssetListModel([asset])
    grid = grid_factory()
    grid.setModel(model)
    grid.resize(400, 300)
    grid.show()
    qapp.processEvents()

    # Lấy vị trí tâm của card thật qua visualRect
    idx = model.index(0, 0)
    rect = grid.visualRect(idx)
    assert rect.isValid() and not rect.isEmpty()
    center = rect.center()

    drag_started_spy = MagicMock()
    drag_ended_spy = MagicMock()
    grid.drag_started.connect(drag_started_spy)
    grid.drag_ended.connect(drag_ended_spy)

    # 1. Thực hiện mousePress thật tại center của card
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(center),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    grid.mousePressEvent(press_event)

    # 2. Thực hiện mouseMove thật vượt ngưỡng drag distance
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(center.x(), center.y() + 60),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    with patch.object(QDrag, "exec", return_value=Qt.DropAction.CopyAction) as mock_drag_exec:
        grid.mouseMoveEvent(move_event)
        # Khẳng định: QDrag.exec được gọi đúng 1 lần và paired signals được phát
        assert mock_drag_exec.call_count == 1
        assert drag_started_spy.call_count == 1
        assert drag_ended_spy.call_count == 1


def test_grid_drag_negative_real_layout_without_index_at_mock(qapp, grid_factory):
    """Test negative (missing file) trên model/view thật, BỎ indexAt mock -> 0 exec / 0 signals."""
    asset_missing = {
        "id": "real_missing_01",
        "name": "Missing Card",
        "category": "meme",
        "file_path": "/nonexistent/real_missing.mp4",
        "availability": "missing",
    }

    model = AssetListModel([asset_missing])
    grid = grid_factory()
    grid.setModel(model)
    grid.resize(400, 300)
    grid.show()
    qapp.processEvents()

    idx = model.index(0, 0)
    rect = grid.visualRect(idx)
    assert rect.isValid() and not rect.isEmpty()
    center = rect.center()

    drag_started_spy = MagicMock()
    drag_ended_spy = MagicMock()
    grid.drag_started.connect(drag_started_spy)
    grid.drag_ended.connect(drag_ended_spy)

    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(center),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    grid.mousePressEvent(press_event)

    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(center.x(), center.y() + 60),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    with patch.object(QDrag, "exec", return_value=Qt.DropAction.CopyAction) as mock_drag_exec:
        grid.mouseMoveEvent(move_event)
        assert mock_drag_exec.call_count == 0
        assert drag_started_spy.call_count == 0
        assert drag_ended_spy.call_count == 0


def test_grid_drag_transition_never_calls_drag_exec(qapp, grid_factory, tmp_path):
    """Transition asset: mouse drag vượt ngưỡng KHÔNG gọi drag.exec và KHÔNG phát drag_started."""
    trans_file = tmp_path / "trans.mp4"
    trans_file.write_bytes(b"dummy")

    asset_trans = {
        "id": "trans_01",
        "name": "Dissolve",
        "category": "templates",
        "tab": "trans",
        "file_path": str(trans_file),
    }

    model = AssetListModel([asset_trans])
    grid = grid_factory()
    grid.setModel(model)
    grid.resize(400, 400)

    drag_started_spy = MagicMock()
    grid.drag_started.connect(drag_started_spy)

    grid.indexAt = MagicMock(return_value=model.index(0, 0))
    grid._drag_start_pos = QPoint(50, 50)

    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(50, 100),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    with patch.object(QDrag, "exec", return_value=Qt.DropAction.CopyAction) as mock_drag_exec:
        grid.mouseMoveEvent(event)
        assert mock_drag_exec.call_count == 0
        assert drag_started_spy.call_count == 0


# ==============================================================================
# 3. create_asset_mime_data Custom Preset Additive Contract Tests (G04-04C7-I)
# ==============================================================================

def test_create_asset_mime_data_custom_preset_applied_to_procedural_target(tmp_path):
    """create_asset_mime_data với custom_preset: xuất đúng thông số tùy biến khi target là procedural text."""
    base_preset = TextStylePreset(id="base_txt", name="Base Text", font="Arial", size=48)
    target = {
        "id": "base_txt",
        "name": "Base Text",
        "category": "text",
        "_raw_obj": base_preset,
    }

    custom = TextStylePreset(id="base_txt", name="Base Text", font="Montserrat", size=80, standard_color="#FF0000")

    mime = create_asset_mime_data(target, sample_text="Sample 123", custom_preset=custom)
    assert mime.hasUrls()
    local_p = mime.urls()[0].toLocalFile()
    content = Path(local_p).read_text(encoding="utf-8")
    assert "Font = Input { Value = \"Montserrat\", }" in content
    assert "StyledText = Input { Value = \"Sample 123\", }" in content
    # Converted size: 80 / 600.0 = 0.1333
    assert "Size = Input { Value = 0.1333, }" in content


def test_create_asset_mime_data_custom_preset_cannot_override_denied_target(tmp_path):
    """custom_preset không được phép lách guard nếu target gốc bị deny (staging / missing / transition)."""
    # 1. Target staging
    staged_target = {
        "id": "staged_text",
        "name": "Staged Text",
        "category": "text",
        "availability": "staging",
    }
    custom = TextStylePreset(id="staged_text", name="Custom Staged", font="Roboto")
    assert not create_asset_mime_data(staged_target, custom_preset=custom).hasUrls()

    # 2. Target native transition
    trans = TransitionStylePreset(id="tr_push", name="Push", transition_type="Push")
    trans_target = {"id": "tr_push", "category": "trans", "_raw_obj": trans}
    assert not create_asset_mime_data(trans_target, custom_preset=custom).hasUrls()


def test_create_asset_mime_data_custom_preset_ignored_for_file_backed_media(tmp_path):
    """custom_preset không biến file-backed media thành generator; file-backed vẫn chỉ trả về URL media."""
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"sample video")

    target = {
        "id": "vid_01",
        "name": "Real Video",
        "category": "meme",
        "source_kind": "builtin",
        "file_path": str(media_file),
        "availability": "online",
    }
    custom = TextStylePreset(id="vid_01", name="Custom Pretender", font="Roboto")

    mime = create_asset_mime_data(target, custom_preset=custom)
    assert mime.hasUrls()
    assert mime.urls()[0].toLocalFile() == str(media_file.resolve())
    # Không sinh file .setting
    assert not mime.urls()[0].toLocalFile().endswith(".setting")

