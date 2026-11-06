from unittest.mock import Mock
from PySide6.QtWidgets import QDialog
from src.ui.director.pages.source_page import SourcePage
from src.ui.widgets.drive_dialog import GoogleDriveImportDialog


def test_drive_import_routes_downloaded_path_to_source(tmp_path, monkeypatch, qapp):
    path = tmp_path / 'download.mp4'
    path.write_bytes(b'video')
    opened = []
    def execute(dialog):
        opened.append(dialog)
        dialog.import_completed.emit(str(path), False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(GoogleDriveImportDialog, 'exec', execute)
    page = SourcePage()
    add = Mock()
    monkeypatch.setattr(page, 'add_paths', add)
    page._on_pick_drive()
    assert len(opened) == 1
    add.assert_called_once_with([str(path)])
    page.close()


def test_missing_workers_report_failure(monkeypatch, qapp):
    from src.ui.director import director_controller as module
    monkeypatch.setattr(module, 'PipelineWorker', None)
    monkeypatch.setattr(module, 'LLMDirectorWorker', None)
    controller = module.DirectorController()
    controller.config.video_paths = ['/test.mp4']
    pipeline, ai = [], []
    controller.pipeline_finished.connect(lambda *args: pipeline.append(args))
    controller.ai_script_finished.connect(lambda *args: ai.append(args))
    assert controller.start_pipeline() is False
    controller.run_ai_script('test')
    assert pipeline[0][0] is False
    assert ai[0][0] is False


def test_drive_cancel_keeps_dialog_alive_until_worker_finishes(tmp_path, monkeypatch, qapp):
    import threading
    import time
    from src.ui.widgets import drive_dialog as module
    entered, release = threading.Event(), threading.Event()
    def download(**kwargs):
        entered.set()
        release.wait(4)
        return str(tmp_path)
    monkeypatch.setattr(module.GoogleDriveDownloader, 'download_file', download)
    dialog = module.GoogleDriveImportDialog()
    dialog.txt_url.setText('https://drive.google.com/file/d/123456789012345678901234567890123/view')
    dialog.txt_output_dir.setText(str(tmp_path))
    imports, closed = [], []
    dialog.import_completed.connect(lambda *a: imports.append(a))
    dialog.finished.connect(closed.append)
    dialog.show()
    dialog._start_download()
    try:
        assert entered.wait(2)
        dialog.reject()
        assert dialog.isVisible()
        assert not closed
        release.set()
        deadline = time.monotonic() + 3
        while not closed and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert closed == [QDialog.DialogCode.Rejected]
        assert imports == []
    finally:
        release.set()
        if dialog.worker:
            dialog.worker.wait(4000)
        qapp.processEvents()
        dialog.close()
