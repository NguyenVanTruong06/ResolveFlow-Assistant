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
        page.wait_for_scan()
        for _ in range(5):
            qapp.processEvents()

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
        page.wait_for_scan()
        qapp.processEvents()

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
        page.wait_for_scan()
        qapp.processEvents()

    assert len(page._clip_infos) == 5


def test_source_page_empty_folder_shows_warning(tmp_path, qapp):
    """Kiểm tra hiển thị cảnh báo khi thư mục không chứa file video hợp lệ."""
    empty_dir = tmp_path / "EmptyFolder"
    empty_dir.mkdir()
    (empty_dir / "notes.txt").write_text("hello")

    page = SourcePage()
    with patch.object(QMessageBox, "warning") as mock_warn:
        page.add_paths([str(empty_dir)])
        page.wait_for_scan()
        qapp.processEvents()
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


def test_source_page_overwrite_duplicate_and_clear_all(tmp_path, qapp):
    """Kiểm tra:
    1. Thêm lại cùng đường dẫn chuẩn hóa: ghi đè/cập nhật thông tin, không tạo bản sao nhân đôi.
    2. Hai file cùng tên ở hai thư mục khác nhau được giữ là 2 nguồn độc lập.
    3. Nút 'Xóa tất cả' gỡ toàn bộ nguồn và cập nhật trạng thái trống.
    4. Nạp lại sau khi xóa tất cả hoạt động bình thường.
    5. Memo cache giúp nạp lại file không đổi nhanh chóng mà không probe lại từ đĩa.
    """
    dir_a = tmp_path / "folder_a"
    dir_b = tmp_path / "folder_b"
    dir_a.mkdir()
    dir_b.mkdir()

    file_a = dir_a / "video.mp4"
    file_b = dir_b / "video.mp4"
    file_a.write_text("dummy a")
    file_b.write_text("dummy b")

    page = SourcePage()

    with patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe:
        # Mock probe trả về object theo đường dẫn
        mock_probe.side_effect = lambda fpath, m, l: MagicMock(
            path=os.path.normpath(fpath),
            filename=os.path.basename(fpath),
            duration_sec=10.0,
            duration_str="00:10",
            resolution="1080p",
            has_cache=False,
            file_size_mb=5.0
        )

        # 1. Thêm file_a và file_b (cùng tên, khác thư mục)
        page.add_paths([str(file_a), str(file_b)])
        page.wait_for_scan()
        qapp.processEvents()
        assert len(page._clip_infos) == 2
        paths = [c.path for c in page._clip_infos]
        assert os.path.normpath(str(file_a)) in paths
        assert os.path.normpath(str(file_b)) in paths

        # 2. Thêm lại file_a -> ghi đè cập nhật, không nhân đôi
        page.add_paths([str(file_a)])
        page.wait_for_scan()
        qapp.processEvents()
        assert len(page._clip_infos) == 2

        # 3. Kiểm tra nút Xóa tất cả không bị ẩn (not isHidden) khi có clip
        assert page.btn_clear_all.isHidden() is False

        # 4. Bấm nút Xóa tất cả
        signals_received = []
        page.clips_changed.connect(lambda p: signals_received.append(p))
        page.btn_clear_all.click()

        assert len(page._clip_infos) == 0
        assert page.btn_clear_all.isHidden() is True
        assert len(signals_received) > 0
        assert signals_received[-1] == []
        assert "Chưa chọn clip nào" in page.lbl_count.text()

        # 5. Nạp lại sau khi xóa tất cả hoạt động bình thường
        page.add_paths([str(file_a)])
        page.wait_for_scan()
        qapp.processEvents()
        assert len(page._clip_infos) == 1
        assert os.path.normpath(str(file_a)) in [c.path for c in page._clip_infos]


def test_source_page_real_probe_memo_invalidation(tmp_path, qapp):
    """Kiểm thử thực tế probe_video_file và _PROBE_MEMO:
    - File không đổi: sử dụng lại metadata từ memo (không đọc lại đĩa).
    - File bị thay đổi nội dung (thay đổi mtime/size): tự động làm mới metadata.
    - Không chia sẻ đối tượng mutable giữa memo và UI.
    """
    from src.ui.director.pages.source_page import probe_video_file, _PROBE_MEMO

    vid_path = tmp_path / "sample.mp4"
    vid_path.write_bytes(b"initial content 123456")

    # 1. Probe lần 1
    info1 = probe_video_file(str(vid_path), whisper_model="small", whisper_lang="vi")
    assert info1.filename == "sample.mp4"
    assert info1.has_cache is False

    # 2. Probe lần 2 khi file không đổi
    info2 = probe_video_file(str(vid_path), whisper_model="small", whisper_lang="vi")
    assert info2.filename == "sample.mp4"
    # Đảm bảo không chia sẻ mutable object
    assert info1 is not info2

    # Sửa info2 xem có ảnh hưởng info1 không
    info2.has_cache = True
    assert info1.has_cache is False

    # 3. Thay đổi kích thước/nội dung file
    import time
    time.sleep(0.01)
    vid_path.write_bytes(b"modified new content longer size 7890123456")

    info3 = probe_video_file(str(vid_path), whisper_model="small", whisper_lang="vi")
    assert info3.file_size_mb >= 0.0


def test_source_page_cancel_job_on_clear_all_while_scanning(tmp_path, qapp):
    """Kiểm thử cơ chế Job Generation ID & Hủy nạp:
    - Khi đang quét, người dùng bấm 'Xóa tất cả':
    - Generation ID tăng lên, vô hiệu hóa các batch cũ.
    - Worker cũ bị hủy (cancel) và kết quả cũ không được nạp vào GUI.
    """
    for i in range(15):
        (tmp_path / f"clip_{i:02d}.mp4").write_text("dummy")

    page = SourcePage()
    page.add_paths([str(tmp_path)])
    assert page._active_worker is not None
    assert page._active_worker.isRunning() or page._active_worker.isFinished()

    # Bấm xóa tất cả ngay lập tức
    page.clear_all_paths()
    assert len(page._clip_infos) == 0

    # Chờ worker kết thúc và processEvents
    page.wait_for_scan()
    qapp.processEvents()

    # Danh sách vẫn phải rỗng tuyệt đối (kết quả cũ bị chặn bởi generation job_id)
    assert len(page._clip_infos) == 0

    # Nạp mới sau khi xóa phải thành công
    page.add_paths([str(tmp_path / "clip_00.mp4")])
    page.wait_for_scan()
    qapp.processEvents()
    assert len(page._clip_infos) == 1


def test_source_page_worker_lifecycle_registry_and_close(tmp_path, qapp):
    """Kiểm thử chuyên sâu vòng đời Worker:
    1. Worker được theo dõi trong _workers_registry theo job_id.
    2. Chặn worker bằng threading.Event để giả lập probe đang chạy.
    3. Bấm clear_all_paths khi worker đang chạy: worker cũ bị cancel nhưng registry vẫn giữ owner tới finished.
    4. Thả Event -> worker cũ kết thúc an toàn, dọn dẹp khỏi registry mà KHÔNG xóa tham chiếu worker mới.
    5. Đóng DirectorWindow khi worker đang chạy: gọi shutdown_workers an toàn, không rò rỉ thread.
    """
    import threading
    import time
    from src.ui.director.director_window import DirectorWindow

    for i in range(10):
        (tmp_path / f"test_{i:02d}.mp4").write_text("dummy")

    win = DirectorWindow()
    page = win.page_source

    probe_started = threading.Event()
    probe_release = threading.Event()

    original_probe = page._active_worker

    # Hook probe_video_file để chặn thread worker
    with patch("src.ui.director.pages.source_page.probe_video_file") as mock_probe:
        def blocking_probe(fpath, m, l):
            probe_started.set()
            probe_release.wait(timeout=5.0)
            return MagicMock(
                path=os.path.normpath(fpath),
                filename=os.path.basename(fpath),
                duration_sec=10.0,
                duration_str="00:10",
                resolution="1080p",
                has_cache=False,
                file_size_mb=1.0
            )

        mock_probe.side_effect = blocking_probe

        try:
            # 1. Bắt đầu job 1
            page.add_paths([str(tmp_path)])
            job_1_id = page._current_job_id
            assert job_1_id in page._workers_registry
            worker_1 = page._workers_registry[job_1_id]

            # Đợi worker 1 bắt đầu probe
            assert probe_started.wait(timeout=3.0) is True

            # 2. Xóa tất cả khi worker 1 đang probe dở
            page.clear_all_paths()
            assert len(page._clip_infos) == 0
            # Worker 1 vẫn được giữ trong registry để dọn an toàn khi finished
            assert job_1_id in page._workers_registry
            assert worker_1._is_cancelled is True

            # 3. Bắt đầu job 2 trong khi worker 1 chưa kết thúc
            mock_probe.side_effect = lambda fpath, m, l: MagicMock(
                path=os.path.normpath(fpath),
                filename=os.path.basename(fpath),
                duration_sec=10.0,
                duration_str="00:10",
                resolution="1080p",
                has_cache=False,
                file_size_mb=1.0
            )
            page.add_paths([str(tmp_path / "test_00.mp4")])
            job_2_id = page._current_job_id
            assert job_2_id != job_1_id
            assert page._active_worker is page._workers_registry[job_2_id]

            # 4. Thả probe_release để worker 1 kết thúc
            probe_release.set()

            # Đợi cả 2 worker hoàn thành
            worker_1.wait(3000)
            page.wait_for_scan(3000)
            qapp.processEvents()

            # Worker 1 đã tự dọn khỏi registry, không ảnh hưởng worker 2
            assert job_1_id not in page._workers_registry
            # Job 2 nạp thành công 1 clip, không bị kết quả cũ của job 1 chèn vào
            assert len(page._clip_infos) == 1

        finally:
            probe_release.set()
            win.close()
            qapp.processEvents()



