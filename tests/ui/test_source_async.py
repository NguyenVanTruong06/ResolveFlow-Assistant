"""Hồi quy nhập nguồn: chạy worker thật, thay thế I/O nặng ở biên."""
import threading
import time
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QThread, QTimer
from src.ui.director.director_state import SourceClipInfo
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages import source_page as source


def pump(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def held_scan(tmp_path, monkeypatch, qapp):
    path = tmp_path / 'clip.mp4'
    path.write_bytes(b'video')
    entered, release = threading.Event(), threading.Event()
    def probe(path, *args):
        entered.set()
        assert release.wait(5)
        return SourceClipInfo(path, 'clip.mp4', duration_sec=1)
    monkeypatch.setattr(source, 'probe_video_file', probe)
    monkeypatch.setattr('src.core.chronological_sorter.get_chronological_sort_key',
                        lambda path: ('2026', str(path), ''))
    win = DirectorWindow()
    win.show()
    win.page_source.add_paths([str(path)])
    assert entered.wait(2)
    try:
        yield win, release, path
    finally:
        release.set()
        pump(qapp, lambda: not win.page_source._workers_registry)
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_close_waits_without_blocking_gui(held_scan, qapp):
    win, release, path = held_scan
    closed, ticks = [], []
    win.closed.connect(lambda: closed.append(True))
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        win.close()
        assert win.isVisible()
        assert not closed
        pump(qapp, lambda: bool(ticks))
        release.set()
        pump(qapp, lambda: bool(closed))
        assert closed == [True]
        assert not win.page_source._workers_registry
        win.show()
        win.page_source.add_paths([str(path)])
        pump(qapp, lambda: not win.page_source._workers_registry)
        assert len(win.page_source._clip_infos) == 1
    finally:
        timer.stop()


def test_quit_waits_for_source_worker(held_scan, qapp, monkeypatch):
    from src.services.source_shutdown import SourceShutdownGuard
    win, release, _ = held_scan
    quit_spy = Mock()
    monkeypatch.setattr(qapp, 'quit', quit_spy)
    guard = SourceShutdownGuard(qapp)
    try:
        qapp.sendEvent(qapp, QEvent(QEvent.Type.Quit))
        qapp.processEvents()
        quit_spy.assert_not_called()
        release.set()
        pump(qapp, lambda: quit_spy.called)
        quit_spy.assert_called_once()
        assert not win.page_source._workers_registry
    finally:
        qapp.removeEventFilter(guard)
        guard.deleteLater()


def test_clear_discards_old_batch_then_reimport(held_scan, qapp):
    win, release, path = held_scan
    page = win.page_source
    old_job = page._current_job_id
    page.clear_all_paths()
    assert old_job in page._workers_registry
    release.set()
    pump(qapp, lambda: not page._workers_registry)
    assert page._clip_infos == []
    page.add_paths([str(path)])
    pump(qapp, lambda: not page._workers_registry)
    assert len(page._clip_infos) == 1
    qapp.processEvents()
    page.clear_all_paths()
    assert page._active_worker is None


def test_memo_avoids_probe_and_refreshes_file_and_scan_cache(tmp_path, monkeypatch):
    import cv2
    from src.core.cache_manager import ScanCacheManager
    source._PROBE_MEMO.clear()
    path = tmp_path / 'metadata.mp4'
    path.write_bytes(b'a')
    frames = [24]
    cap = Mock()
    cap.isOpened.return_value = True
    cap.get.side_effect = lambda prop: {cv2.CAP_PROP_FPS: 24, cv2.CAP_PROP_FRAME_COUNT: frames[0],
                                        cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 360}[prop]
    capture = Mock(return_value=cap)
    monkeypatch.setattr(cv2, 'VideoCapture', capture)
    scan = {}
    monkeypatch.setattr(ScanCacheManager, 'get_cached_scan', lambda self, p, m, l: scan.get((m, l)))
    first = source.probe_video_file(str(path))
    assert first.duration_sec == 1
    second = source.probe_video_file(str(path))
    assert second is not first
    capture.assert_called_once()
    scan[('small', 'vi')] = {'clip_dur': 3}
    assert source.probe_video_file(str(path)).has_cache
    assert source.probe_video_file(str(path)).duration_sec == 3
    assert not source.probe_video_file(str(path), 'small', 'en').has_cache
    scan.clear()
    assert not source.probe_video_file(str(path)).has_cache
    frames[0] = 48
    path.write_bytes(b'longer')
    assert source.probe_video_file(str(path)).duration_sec == 2
    assert capture.call_count == 2
    assert len([k for k in source._PROBE_MEMO if k[0] == str(path)]) == 1


def test_sort_io_only_in_worker_and_reused(tmp_path, qapp, monkeypatch):
    paths = [tmp_path / 'a.mp4', tmp_path / 'b.mp4']
    for path in paths:
        path.write_bytes(b'video')
    calls = []
    def key(path):
        calls.append(QThread.currentThread() == qapp.thread())
        return ('2026', str(path), '')
    monkeypatch.setattr('src.core.chronological_sorter.get_chronological_sort_key', key)
    monkeypatch.setattr(source, 'probe_video_file', lambda p, *a: SourceClipInfo(p, p))
    page = source.SourcePage()
    try:
        page.add_paths([str(tmp_path)])
        pump(qapp, lambda: not page._workers_registry)
        assert len(page._clip_infos) == 2
        assert calls == [False, False]
        page.add_paths([str(tmp_path)])
        pump(qapp, lambda: not page._workers_registry)
        assert calls == [False, False]
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_scan_error_reaches_gui(tmp_path, qapp, monkeypatch):
    (tmp_path / 'bad.mp4').write_bytes(b'bad')
    monkeypatch.setattr('src.core.chronological_sorter.get_chronological_sort_key', lambda p: ('', p, ''))
    def fail(*args):
        raise OSError('probe failed')
    monkeypatch.setattr(source, 'probe_video_file', fail)
    warning = Mock()
    monkeypatch.setattr(source.QMessageBox, 'warning', warning)
    page = source.SourcePage()
    page.add_paths([str(tmp_path)])
    pump(qapp, lambda: not page._workers_registry)
    warning.assert_called_once()
    assert 'probe failed' in warning.call_args.args[-1]
    page.close()
    page.deleteLater()
    qapp.processEvents()
