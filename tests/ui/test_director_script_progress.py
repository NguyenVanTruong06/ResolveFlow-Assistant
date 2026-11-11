import subprocess
import sys

from PySide6.QtTest import QTest

from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.review_page import ReviewPage


def test_opening_director_first_keeps_the_local_ai_worker_available():
    result = subprocess.run(
        [sys.executable, "-c", (
            "from src.ui.director.director_window import DirectorWindow\n"
            "from src.ui.director.director_controller import LLMDirectorWorker\n"
            "assert LLMDirectorWorker is not None\n"
        )],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_web_copy_button_stays_visible_when_web_details_are_scrolled(qapp):
    page = ReviewPage()
    page.resize(1024, 640)
    page.show()
    qapp.processEvents()
    scrollbar = page.scroll_web.horizontalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    qapp.processEvents()

    button = page.btn_copy_video_prompt
    assert button.visibleRegion().boundingRect() == button.rect()
    page.close()


def test_local_run_shows_stage_and_elapsed_then_reports_error_on_original_mode(qapp):
    page = ReviewPage()
    requests = []
    page.ai_run_requested.connect(lambda: requests.append(True))
    page._switch_mode(2)

    page.btn_run_ollama.click()

    assert requests == [True]
    assert not page.btn_run_ollama.isEnabled()
    assert "Chuẩn bị" in page.lbl_script_progress.text()
    page.show_ai_generation_progress()
    QTest.qWait(1200)
    assert "Ollama" in page.lbl_script_progress.text()
    assert "0:01" in page.lbl_script_progress.text()

    page._switch_mode(1)
    page.set_ai_result(False, "Hết thời gian chờ Ollama")

    assert "Hết thời gian chờ Ollama" in page.lbl_ollama_status.text()
    assert "Hết thời gian chờ Ollama" in page.lbl_script_progress.text()
    assert not page._script_progress_timer.isActive()
    assert page.btn_run_ollama.isEnabled()


def test_local_run_without_sources_does_not_leave_waiting_state(qapp, monkeypatch):
    director = DirectorWindow()
    monkeypatch.setattr(director, "_sync_all_pages_to_config", lambda: True)
    monkeypatch.setattr(director, "_get_current_director_context", lambda: {})
    page = director.page_review
    page._switch_mode(2)

    page.btn_run_ollama.click()

    assert "Bước 1" in page.lbl_script_progress.text()
    assert not page._script_progress_timer.isActive()
    assert page.btn_run_ollama.isEnabled()
    director.close()
