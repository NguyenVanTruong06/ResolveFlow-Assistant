"""
tests/ui/test_source_page.py
Kiểm thử bộ nạp thư mục & tệp nguồn của SourcePage & DirectorWindow:
- Quét thư mục có đường dẫn chứa các từ khóa đặc biệt (assets, broll, memes, music...)
- Quét các thư mục con phân tầng (broll, memes, DCIM camera)
- Nhận diện các định dạng video mở rộng (.ts, .mts, .m2ts, .flv, .wmv)
- Cảnh báo khi người dùng chọn thư mục trống không có video
- Kéo-thả trực tiếp vào SourcePage và DirectorWindow
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QMimeData, QUrl, QPoint
from PySide6.QtGui import QDropEvent
from src.ui.director.pages.source_page import SourcePage
from src.ui.director.director_window import DirectorWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_source_page_scan_folder_with_assets_and_memes_in_path(tmp_path, qapp):
    """Kiểm tra fix lỗi quét thư mục nằm trong đường dẫn có chứa chữ 'assets' hoặc 'memes'."""
    # Tạo đường dẫn mô phỏng có từ 'assets' và 'memes'
    nested_dir = tmp_path / "assets" / "broll_memes" / "memes"
    nested_dir.mkdir(parents=True)
    
    (nested_dir / "clip_01.mp4").write_text("dummy")
    (nested_dir / "clip_02.mov").write_text("dummy")
    (nested_dir / "clip_03.mkv").write_text("dummy")

    page = SourcePage()
    with patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe:
        mock_probe.side_effect = lambda fpath, m, l: MagicMock(
            path=fpath, filename=os.path.basename(fpath), duration_sec=15.0,
            duration_str="00:15", resolution="1080p", has_cache=False, file_size_mb=10.0
        )
        page.add_paths([str(nested_dir)])

    assert len(page._clip_infos) == 3
    loaded_filenames = [c.filename for c in page._clip_infos]
    assert "clip_01.mp4" in loaded_filenames
    assert "clip_02.mov" in loaded_filenames
    assert "clip_03.mkv" in loaded_filenames


def test_source_page_scan_subfolders_named_broll_and_memes(tmp_path, qapp):
    """Kiểm tra không bị prune các thư mục con tên broll, memes, assets."""
    proj_dir = tmp_path / "MyProject"
    proj_dir.mkdir()

    broll_sub = proj_dir / "broll"
    broll_sub.mkdir()
    (broll_sub / "sunset.mp4").write_text("dummy")

    memes_sub = proj_dir / "memes"
    memes_sub.mkdir()
    (memes_sub / "laugh.webm").write_text("dummy")

    # Thư mục hệ thống cần bị bỏ qua
    git_sub = proj_dir / ".git"
    git_sub.mkdir()
    (git_sub / "ignore.mp4").write_text("dummy")

    page = SourcePage()
    with patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe:
        mock_probe.side_effect = lambda fpath, m, l: MagicMock(
            path=fpath, filename=os.path.basename(fpath), duration_sec=10.0,
            duration_str="00:10", resolution="1080p", has_cache=False, file_size_mb=5.0
        )
        page.add_paths([str(proj_dir)])

    assert len(page._clip_infos) == 2
    loaded = [c.filename for c in page._clip_infos]
    assert "sunset.mp4" in loaded
    assert "laugh.webm" in loaded
    assert "ignore.mp4" not in loaded


def test_source_page_supports_camera_and_extended_formats(tmp_path, qapp):
    """Kiểm tra nhận diện định dạng camera .ts, .mts, .m2ts, .flv, .wmv."""
    camera_dir = tmp_path / "Sony_A7S3"
    camera_dir.mkdir()

    (camera_dir / "00001.MTS").write_text("dummy")
    (camera_dir / "00002.m2ts").write_text("dummy")
    (camera_dir / "stream.ts").write_text("dummy")
    (camera_dir / "record.flv").write_text("dummy")
    (camera_dir / "clip.wmv").write_text("dummy")

    page = SourcePage()
    with patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe:
        mock_probe.side_effect = lambda fpath, m, l: MagicMock(
            path=fpath, filename=os.path.basename(fpath), duration_sec=20.0,
            duration_str="00:20", resolution="4K", has_cache=False, file_size_mb=50.0
        )
        page.add_paths([str(camera_dir)])

    assert len(page._clip_infos) == 5


def test_source_page_empty_folder_shows_warning(tmp_path, qapp):
    """Kiểm tra hiển thị cảnh báo khi thư mục không chứa file video hợp lệ."""
    empty_dir = tmp_path / "EmptyFolder"
    empty_dir.mkdir()
    (empty_dir / "notes.txt").write_text("hello")

    page = SourcePage()
    with patch.object(QMessageBox, "warning") as mock_warn:
        page.add_paths([str(empty_dir)])
        assert mock_warn.called
        assert len(page._clip_infos) == 0


def test_director_window_drop_event_forwards_to_source_page(tmp_path, qapp):
    """Kiểm tra kéo thả thư mục vào DirectorWindow tự động chuyển tiếp tới SourcePage."""
    footage_dir = tmp_path / "DropFootage"
    footage_dir.mkdir()
    (footage_dir / "intro.mp4").write_text("dummy")

    win = DirectorWindow()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(footage_dir))])

    from PySide6.QtCore import Qt, QPointF
    event = QDropEvent(
        QPointF(100.0, 100.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier
    )

    with patch.object(win.page_source, "add_paths") as mock_add_paths:
        win.dropEvent(event)
        mock_add_paths.assert_called_once_with([str(footage_dir)])
    win.close()
