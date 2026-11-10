"""
tests/ui/test_source_ai_analysis_dependencies.py
Regression tests for AI analysis, model dependency management, and UI consistency:
- Top Director chip vs Source chip synchronization on startup from QSettings.
- Synchronized DirectorConfig and worker snapshot preventing ReviewPage refresh blockage.
- Honest missing-model UI in SourcePage._set_model (no false promises of offline scan download).
- Background Whisper preparation lifecycle (ownership, blocked shutdown, deferred close, idle).
- Prevention of duplicate preparation starts and concurrent model/language changes.
- Offline scan invariant and frame cache retention when Whisper model is missing.
- Distinction between Ollama service disconnected vs absent vision model with actionable guidance.
- Complete isolation of QSettings and user configuration directories (no real config mutation).
"""

import threading
import time
from unittest.mock import patch, MagicMock
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QSettings, QCoreApplication, Qt
from PySide6.QtGui import QCloseEvent

from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages import source_page
from src.ui.director.pages.source_page import (
    SourcePage,
    SourceClipInfo,
    SourceClipRow,
    _write_whisper_setting,
    PRIMARY_SETTINGS_ORG,
    PRIMARY_SETTINGS_APP,
)
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisWorker,
    SourceAnalysisConfig,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def clean_qsettings(tmp_path, monkeypatch):
    """
    R2-03: Completely isolates QSettings and XDG directories to tmp_path.
    Never clears or mutates real app settings in ~/.config.
    Restores global format at teardown.
    """
    primary_ini = tmp_path / "primary.ini"
    legacy_ini = tmp_path / "legacy.ini"
    xdg_cfg = tmp_path / "xdg_config"
    xdg_data = tmp_path / "xdg_data"
    xdg_cfg.mkdir(parents=True, exist_ok=True)
    xdg_data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_cfg))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    monkeypatch.setattr(source_page, "_get_primary_settings", lambda: QSettings(str(primary_ini), QSettings.Format.IniFormat))
    monkeypatch.setattr(source_page, "_get_legacy_settings", lambda: QSettings(str(legacy_ini), QSettings.Format.IniFormat))

    orig_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "user_ini"))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, str(tmp_path / "sys_ini"))

    yield primary_ini, legacy_ini, xdg_cfg

    # Teardown: restore defaultFormat to avoid leaving globals pointing to deleted tmp_path
    QSettings.setDefaultFormat(orig_format)


def test_director_window_startup_syncs_whisper_settings_from_qsettings(qapp):
    """B1: DirectorWindow startup synchronizes whisper_model from QSettings so top chip matches Source chip."""
    _write_whisper_setting("whisper_model", "medium")
    _write_whisper_setting("whisper_lang", "vi")

    win = DirectorWindow()
    try:
        top_chip_text = win.btn_whisper.text()
        source_chip_text = win.page_source.btn_whisper_chip.text()

        assert "medium" in top_chip_text, f"Top chip must show 'medium', got: {top_chip_text}"
        assert "vi" in top_chip_text, f"Top chip must show 'vi', got: {top_chip_text}"
        assert "medium" in source_chip_text, f"Source chip must show 'medium', got: {source_chip_text}"
        assert "vi" in source_chip_text, f"Source chip must show 'vi', got: {source_chip_text}"
        assert win.config.whisper_model == "medium"
        assert win.config.whisper_language == "vi"
    finally:
        win.close()


def test_director_window_syncs_worker_snapshot_on_analysis_completed(qapp):
    """B2: When SourcePage finishes analysis, snapshot matches DirectorWindow.config without blocking ReviewPage."""
    _write_whisper_setting("whisper_model", "medium")
    _write_whisper_setting("whisper_lang", "vi")

    win = DirectorWindow()
    try:
        mock_snap = MagicMock(
            video_paths=[],
            whisper_model="medium",
            whisper_language="vi",
            enable_visual_describe=False,
            vision_endpoint="http://127.0.0.1:11434",
            window_sec=8.0,
            frames_per_window=2,
            max_windows=10,
            max_refinements=1,
        )
        win.page_source.get_completed_analysis_snapshot = lambda: mock_snap

        counters = {
            "total_clips": 1,
            "audio": {"cached": 1, "new": 0, "unavailable": 0, "failed": 0},
            "visual": {"cached": 1, "new": 0, "failed": 0},
            "visual_describe": {"skipped": 1},
        }

        with patch.object(QMessageBox, "information"):
            win._on_source_analysis_completed("job_123", True, "done", counters)

        assert win._pending_review_refresh is True, "ReviewPage refresh must not be blocked when config was synced from QSettings"
    finally:
        win.close()


def test_source_page_set_model_missing_model_honest_dialog(qapp):
    """B3: Selecting model not on disk does not falsely promise download during scan."""
    page = SourcePage()
    captured_msg = ""

    def fake_question(parent, title, text, buttons, default):
        nonlocal captured_msg
        captured_msg = text
        return QMessageBox.StandardButton.No

    with patch("src.ui.director.pages.source_page.is_model_on_disk", return_value=False):
        with patch.object(QMessageBox, "question", side_effect=fake_question):
            page._set_model("large-v3")

    assert "khi quét" not in captured_msg, "Must not falsely claim application will download during offline scan"
    assert "ngoại tuyến" in captured_msg or "offline" in captured_msg, "Must honestly inform user that scan is offline"
    assert page._whisper_model == "large-v3", "User selection must be preserved without silent fallback"


def test_source_analysis_offline_preserves_frames_when_whisper_missing(qapp, tmp_path):
    """B4: SourceAnalysisWorker preserves frame cache when Whisper model load fails (local_files_only=True)."""
    fake_video = str(tmp_path / "test_video.mp4")
    with open(fake_video, "wb") as f:
        f.write(b"0" * 1024)

    mock_visual_index = MagicMock()
    mock_visual_index.load.return_value = None
    mock_visual_index.scan.return_value = {"cache_hit": False, "frames": ["/tmp/f1.jpg"]}
    mock_visual_index.prepare_windows.return_value = {
        "fingerprint": "fake_fp",
        "temporal_config": {},
        "windows": [],
        "analysis": {},
        "descriptions": [],
    }

    mock_transcriber = MagicMock()
    mock_transcriber.load_model.side_effect = FileNotFoundError("Mô hình không tồn tại trong local cache")

    cfg = SourceAnalysisConfig(
        job_id="test_job_1",
        video_paths=(fake_video,),
        whisper_model="medium",
        whisper_language="vi",
        enable_audio=True,
        enable_visual_sampling=True,
        local_files_only=True,
    )

    worker = SourceAnalysisWorker(
        config=cfg,
        visual_index=mock_visual_index,
        transcriber_factory=lambda c: mock_transcriber,
        audio_probe_fn=lambda p: True,
        duration_fn=lambda p: 10.0,
        validate_identity_fn=lambda *a, **k: True,
        hash_fn=lambda *a, **k: ("hash123", 1024),
    )

    clip_statuses = []
    worker.status_signal.connect(lambda jid, path, res: clip_statuses.append(res))

    worker.run()

    assert len(clip_statuses) == 1
    res = clip_statuses[0]
    assert res["audio_status"] == "unavailable", "Missing local whisper model must be classified as unavailable"
    assert res["visual_status"] in ("cached", "new"), "Visual frame extraction must still succeed"
    assert mock_visual_index.scan.called


def test_source_analysis_distinguishes_ollama_disconnected_vs_model_absent(qapp):
    """B5: Clip row UI distinguishes Ollama disconnected from model absent with proper guidance."""
    clip = SourceClipInfo(
        path="/fake/vid.mp4", filename="vid.mp4", duration_sec=10.0,
        duration_str="00:10", resolution="1080p", has_cache=False, file_size_mb=10.0
    )
    row = SourceClipRow(clip)

    res_disconnected = {
        "audio_status": "cached",
        "visual_status": "cached",
        "visual_describe_status": "unavailable",
        "visual_describe_error_type": "ollama_disconnected",
        "error": "Không kết nối được Ollama tại http://127.0.0.1:11434",
    }
    row.update_analysis_status(res_disconnected)
    label_text_conn = row.lbl_cache.text()
    tooltip_conn = row.lbl_cache.toolTip()
    assert "chưa kết nối" in label_text_conn or "kết nối" in label_text_conn
    assert "khởi động" in tooltip_conn.lower() or "service" in tooltip_conn.lower()

    res_model_absent = {
        "audio_status": "cached",
        "visual_status": "cached",
        "visual_describe_status": "unavailable",
        "visual_describe_error_type": "vision_model_absent",
        "error": "Chưa cài model Ollama: qwen2.5-vl:7b",
    }
    row.update_analysis_status(res_model_absent)
    label_text_model = row.lbl_cache.text()
    tooltip_model = row.lbl_cache.toolTip()
    assert "chưa cài" in label_text_model.lower() or "thiếu model" in label_text_model.lower()
    assert "ollama pull" in tooltip_model.lower()


# ===========================================================================
# R2-01: Deterministic Fake-Download Lifecycle & Ownership Tests
# ===========================================================================

def _wait_for_prep_worker(page, qapp, timeout=5.0):
    start_t = time.time()
    while page.is_model_preparation_active() and time.time() - start_t < timeout:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def test_whisper_prep_blocked_shutdown_deferred_close_and_eventual_idle(qapp):
    """R2-01: Active download blocks shutdown, defers close, and cleans up registry upon completion."""
    page = SourcePage()
    download_started = threading.Event()
    download_blocker = threading.Event()
    idle_emitted = False

    def fake_download(model, cancelled=None, progress=None):
        download_started.set()
        download_blocker.wait(timeout=5.0)
        return "/tmp/fake_model_dir"

    page.workers_idle.connect(lambda: globals().update(idle_emitted=True))

    started = page.start_whisper_model_preparation("medium", download_fn=fake_download)
    assert started is True
    assert download_started.wait(timeout=2.0) is True

    # 1. State while running
    assert page.is_model_preparation_active() is True
    assert len(page._workers_registry) == 1
    assert "whisper_prep_medium" in page._workers_registry
    assert page.btn_whisper_chip.isEnabled() is False

    # 2. Shutdown request while running must return False (blocked)
    assert page.shutdown_workers() is False
    assert page._shutdown_pending is True

    # 3. Close event must be ignored (deferred)
    close_evt = QCloseEvent()
    page.closeEvent(close_evt)
    assert close_evt.isAccepted() is False
    assert page._close_pending is True

    # 4. Release download and verify graceful completion
    download_blocker.set()
    _wait_for_prep_worker(page, qapp)

    # 5. Registry emptied and eventual idle
    assert len(page._workers_registry) == 0
    assert page.is_model_preparation_active() is False
    assert page._whisper_prep_worker is None


def test_whisper_prep_duplicate_start_and_config_change_prevented(qapp):
    """R2-01: Prevent double starts and model/lang changes while preparing."""
    page = SourcePage()
    download_started = threading.Event()
    download_blocker = threading.Event()

    def fake_download(model, cancelled=None, progress=None):
        download_started.set()
        download_blocker.wait(timeout=5.0)
        return "/tmp/fake"

    page.start_whisper_model_preparation("medium", download_fn=fake_download)
    assert download_started.wait(timeout=2.0) is True

    # Duplicate start rejected
    dup_started = page.start_whisper_model_preparation("large-v3")
    assert dup_started is False

    # Model and language change ignored while preparing
    orig_model = page._whisper_model
    orig_lang = page._whisper_lang
    page._set_model("small")
    page._set_lang("en")
    assert page._whisper_model == orig_model
    assert page._whisper_lang == orig_lang

    # Clean unblock
    download_blocker.set()
    _wait_for_prep_worker(page, qapp)
    assert page._whisper_prep_worker is None


def test_whisper_prep_success_and_error_cleanup(qapp):
    """R2-01: Clean toast and button restoration on both success and error paths."""
    page = SourcePage()

    # Success path
    toasts = []
    page._show_toast = lambda msg: toasts.append(msg)
    page.start_whisper_model_preparation("small", download_fn=lambda m, **k: "/path/to/small")
    _wait_for_prep_worker(page, qapp)

    assert page.btn_whisper_chip.isEnabled() is True
    assert any("Đã tải xong" in t for t in toasts)
    assert len(page._workers_registry) == 0

    # Error path
    toasts.clear()
    def failing_download(m, **k):
        raise RuntimeError("Network failure in test")

    page.start_whisper_model_preparation("small", download_fn=failing_download)
    _wait_for_prep_worker(page, qapp)

    assert page.btn_whisper_chip.isEnabled() is True
    assert any("thất bại" in t for t in toasts)
    assert len(page._workers_registry) == 0


def test_whisper_prep_strong_ownership_until_qthread_finished(qapp):
    """R2-01: Worker reference and registry entry persist until native QThread.finished."""
    page = SourcePage()
    thread_finished = threading.Event()

    page.start_whisper_model_preparation("small", download_fn=lambda m, **k: "/path")
    worker = page._whisper_prep_worker
    assert worker is not None
    worker.finished.connect(thread_finished.set)

    _wait_for_prep_worker(page, qapp)

    assert thread_finished.is_set() is True
    assert page._whisper_prep_worker is None
    assert len(page._workers_registry) == 0


def test_no_real_user_config_mutations(tmp_path):
    """R2-03: Reliable evidence that real user preferences are never touched and writes land in isolated tmp_path."""
    sentinel_value = "sentinel_isolated_model_xyz"
    primary_ini = tmp_path / "primary.ini"

    # Write setting using source_page API
    ok = _write_whisper_setting("whisper_model", sentinel_value)
    assert ok is True

    # 1. Assert isolated tmp file received the write
    assert primary_ini.exists(), "Isolated primary settings file must exist in tmp_path"
    content = primary_ini.read_text(encoding="utf-8")
    assert sentinel_value in content, "Sentinel value must be written to isolated temp settings file"

    # 2. Check real user ~/.config paths without modifying them: sentinel must NOT be present
    import pathlib
    real_conf_file = pathlib.Path.home() / ".config" / PRIMARY_SETTINGS_ORG / f"{PRIMARY_SETTINGS_APP}.conf"
    if real_conf_file.exists():
        real_content = real_conf_file.read_text(encoding="utf-8", errors="ignore")
        assert sentinel_value not in real_content, "Sentinel value leaked into real user configuration file!"
