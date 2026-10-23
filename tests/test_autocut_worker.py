import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication

from src.services.autocut_worker import AutoCutWorker
from src.services.autocut_service import AutoCutResult


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_autocut_worker_lifecycle(qapp):
    mock_service = MagicMock()
    mock_res = AutoCutResult(
        ok=True,
        message="AutoCut hoàn tất",
        cuts_count=5,
        duration_saved_sec=12.5
    )
    mock_service.run_instant_autocut.return_value = mock_res

    worker = AutoCutWorker()
    worker.service = mock_service

    results = []
    progress_calls = []

    worker.finished_result.connect(lambda res: results.append(res))
    worker.progress_changed.connect(lambda pct, msg: progress_calls.append((pct, msg)))

    worker.start()
    worker.wait(3000)
    qapp.processEvents()

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].cuts_count == 5
