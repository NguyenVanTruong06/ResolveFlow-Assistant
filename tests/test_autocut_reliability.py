"""Hồi quy AutoCut: không báo thành công khi Resolve chưa tạo timeline."""
from unittest.mock import MagicMock
import pytest
from src.core.autocut import CutSegment
from src.services.autocut_service import InstantAutoCutService


@pytest.fixture
def service(tmp_path, monkeypatch):
    source = tmp_path / 'nguon.wav'
    source.write_bytes(b'audio')
    auto = MagicMock()
    original = MagicMock()
    original.GetName.return_value = 'Gốc'
    original.GetUniqueId.return_value = 'original'
    auto.get_active_timeline.return_value = original
    auto.auto_detect_video_paths.return_value = [str(source)]
    project = auto.current_project
    project.GetTimelineCount.return_value = 1
    project.GetTimelineByIndex.return_value = original
    project.GetMediaPool.return_value.ImportTimelineFromFile.return_value = None
    auto.import_edl_to_timeline.return_value = False
    monkeypatch.setattr('src.services.autocut_service.SilenceDetector.detect_silence_from_wav',
                        lambda *a: [CutSegment(start=0, end=1, action='keep'), CutSegment(start=1, end=2, action='cut')])
    monkeypatch.setattr('src.services.autocut_service.EDLGenerator.create_edl', lambda *a: None)
    return InstantAutoCutService(auto), auto, source


def test_import_failure_is_not_success(service):
    svc, auto, source = service
    result = svc.run_instant_autocut()
    assert not result.ok
    assert 'API' in result.message


def test_fallback_must_not_reuse_original_timeline(service):
    svc, auto, source = service
    auto.import_edl_to_timeline.return_value = True
    assert not svc.run_instant_autocut().ok


def test_imported_timeline_must_belong_to_project(service):
    svc, auto, source = service
    candidate = MagicMock()
    candidate.GetUniqueId.return_value = 'new'
    auto.current_project.GetMediaPool.return_value.ImportTimelineFromFile.return_value = candidate
    assert not svc.run_instant_autocut().ok


def test_verified_timeline_reports_source_and_target(service):
    svc, auto, source = service
    candidate = MagicMock()
    candidate.GetUniqueId.return_value = 'new'
    candidate.GetName.return_value = '[AutoCut] Gốc'
    auto.current_project.GetMediaPool.return_value.ImportTimelineFromFile.return_value = candidate
    auto.current_project.GetTimelineByIndex.return_value = candidate
    result = svc.run_instant_autocut()
    assert result.ok
    assert result.source_file_path == str(source)
    assert result.new_timeline_name == '[AutoCut] Gốc'


def test_missing_source_has_distinct_error(service):
    svc, auto, source = service
    source.unlink()
    result = svc.run_instant_autocut()
    assert not result.ok
    assert 'nguồn' in result.message


def test_api_exception_returns_failure(service):
    svc, auto, source = service
    auto.get_active_timeline.side_effect = RuntimeError('disconnected')
    result = svc.run_instant_autocut()
    assert not result.ok
    assert 'API' in result.message


def test_dock_button_constructs_real_worker(qapp, monkeypatch):
    from src.ui.lux.shell.edge_dock import EdgeDockWindow
    from src.services.autocut_worker import AutoCutWorker
    auto = MagicMock()
    monkeypatch.setattr('src.core.resolve_api.ResolveAutomation', lambda: auto)
    # Giữ constructor thật; chỉ chặn chạy API trong thread.
    monkeypatch.setattr(AutoCutWorker, 'start', lambda self: None)
    dock = EdgeDockWindow()
    try:
        dock.header.btn_instant_autocut.click()
        worker = getattr(dock, '_autocut_worker', None)
        assert isinstance(worker, AutoCutWorker)
        assert worker.service.config.min_silent_duration == pytest.approx(0.6)
        assert worker.service.config.pre_roll == pytest.approx(0.08)
    finally:
        dock.close()


def test_audio_extraction_failure_has_distinct_error(service, monkeypatch):
    svc, auto, source = service
    monkeypatch.setattr(svc, 'extract_audio', lambda *a: None)
    result = svc.run_instant_autocut()
    assert not result.ok
    assert 'trích xuất âm thanh' in result.error_message


def test_created_timeline_activation_failure_is_reported(service):
    svc, auto, source = service
    candidate = MagicMock()
    candidate.GetUniqueId.return_value = 'new'
    candidate.GetName.return_value = 'Đích'
    auto.current_project.GetMediaPool.return_value.ImportTimelineFromFile.return_value = candidate
    auto.current_project.GetTimelineByIndex.return_value = candidate
    auto.current_project.SetCurrentTimeline.return_value = False
    result = svc.run_instant_autocut()
    assert result.ok
    assert 'Không thể chuyển' in result.message
    assert result.new_timeline_name == 'Đích'
