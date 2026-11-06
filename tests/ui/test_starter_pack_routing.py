"""
Kiểm thử tích hợp UI cho luồng Starter Pack:
- PackIngestWizard hỗ trợ prepare_factory
- TabAssets._on_starter_pack_clicked chuyển hướng qua Wizard thay vì ghi repo
"""

import time
import pytest
from pathlib import Path
import shiboken6
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.core.storage_manager import StorageManager
from src.core.starter_pack_downloader import StarterPackProducer
from src.ui.widgets.pack_ingest_wizard import PackIngestWizard


@pytest.fixture(autouse=True)
def disable_studio_hardware_telemetry(monkeypatch):
    """
    Vô hiệu hóa việc dispatch HardwareStatsWorker ngầm trong các bài test routing.
    Telemetry phần cứng (/proc/stat, nvidia-smi) không thuộc phạm vi kiểm thử luồng routing
    và gây race condition / segfault khi tín hiệu stats_ready emit về StudioWindow đã bị đóng.
    """
    from src.ui.studio.studio_window import StudioWindow
    monkeypatch.setattr(StudioWindow, "_fetch_hardware_stats", lambda self: None)


@pytest.fixture
def make_studio_window():
    """Quản lý vòng đời StudioWindow được tạo trong test, đảm bảo dừng timer và đóng cửa sổ trong finally."""
    windows = []
    def _create(*args, **kwargs):
        from src.ui.studio.studio_window import StudioWindow
        win = StudioWindow(*args, **kwargs)
        windows.append(win)
        return win
    yield _create
    for win in windows:
        if hasattr(win, "_stats_timer") and win._stats_timer.isActive():
            win._stats_timer.stop()
        win.close()


def test_wizard_prepare_factory_validation(qapp, tmp_path):
    """Tham số prepare_factory phải là callable, nếu không ném TypeError."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    with pytest.raises(TypeError, match="callable"):
        PackIngestWizard(storage, prepare_factory="not_a_callable")


def test_wizard_factory_auto_prepare_and_draft_review(qapp, tmp_path):
    """
    Wizard chạy prepare_factory trong LibraryJob khi auto_prepare=True,
    hiển thị draft trong IngestReviewModel mà không tự động commit.
    """
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def fake_factory(cancel, progress):
        return StarterPackProducer.produce_to_staging(
            storage,
            cancel=cancel,
            progress=progress,
            sfx_generator_fn=lambda target: (Path(target) / "pop.wav").write_bytes(b"data") and 1,
            lut_generator_fn=lambda target: (Path(target) / "clean.cube").write_text("LUT") and 1,
        )

    wizard = PackIngestWizard(
        storage=storage,
        prepare_factory=fake_factory,
        auto_prepare=True,
    )

    try:
        # Chờ job hoàn tất và cập nhật bảng review (deadline monotonic 5s + processEvents + sleep .005)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
            if hasattr(wizard, "_prepare_timer") and wizard._prepare_timer.isActive():
                continue
            if wizard.worker is not None:
                continue
            if wizard.job is not None:
                break

        outcome_info = getattr(wizard, "_outcome", None)
        lbl_info = wizard.lbl_status.text() if hasattr(wizard, "lbl_status") else ""
        assert wizard.job is not None, (
            f"Wizard job is None sau 5s! worker={wizard.worker}, outcome={outcome_info}, status='{lbl_info}'"
        )
        assert wizard.worker is None
        assert wizard.job["name"] == "Starter Pack (SFX & LUTs)"
        assert wizard.model.rowCount() == 2
        assert not wizard.is_pack_committed()

        # Thử reject -> dọn dẹp staging
        job_dir = Path(wizard.job["job_dir"])
        assert job_dir.exists()

        wizard.reject()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
            if wizard.worker is None and not job_dir.exists():
                break

        assert not job_dir.exists()
        assert wizard.worker is None
        QTest.qWait(50)
    finally:
        if wizard.worker is not None:
            w = wizard.worker
            wizard.reject()
            w.wait(2000)
        wizard.close()
        QTest.qWait(20)


def test_wizard_factory_commit_publishes_pack(qapp, tmp_path):
    """Khi người dùng bấm Commit trên wizard từ factory, pack được nhập kho an toàn."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    def fake_factory(cancel, progress):
        return StarterPackProducer.produce_to_staging(
            storage,
            cancel=cancel,
            progress=progress,
            sfx_generator_fn=lambda target: (Path(target) / "ding.wav").write_bytes(b"data") and 1,
            lut_generator_fn=lambda target: (Path(target) / "warm.cube").write_text("LUT") and 1,
        )

    wizard = PackIngestWizard(
        storage=storage,
        prepare_factory=fake_factory,
        auto_prepare=True,
    )

    prepare_worker = None
    commit_worker = None

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
            if wizard.worker is not None and prepare_worker is None:
                prepare_worker = wizard.worker
            if hasattr(wizard, "_prepare_timer") and wizard._prepare_timer.isActive():
                continue
            if wizard.worker is not None:
                continue
            if wizard.job is not None:
                break

        assert wizard.job is not None
        assert wizard.worker is None
        if prepare_worker is not None and shiboken6.isValid(prepare_worker):
            try:
                prepare_worker.wait(2000)
            except RuntimeError:
                pass

        committed_events = []
        wizard.pack_committed.connect(committed_events.append)

        wizard._commit()
        commit_worker = wizard.worker

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
            if wizard.worker is None and wizard.is_pack_committed():
                break

        assert wizard.is_pack_committed()
        assert wizard.worker is None
        if commit_worker is not None and shiboken6.isValid(commit_worker):
            try:
                commit_worker.wait(2000)
            except RuntimeError:
                pass

        assert len(committed_events) == 1
        assert committed_events[0]["count"] == 2

        # Kiểm tra registry trong kho
        reg = storage.registry()
        assert len(reg["packs"]) == 1
        assert len(reg["assets"]) == 2
    finally:
        for w in (prepare_worker, commit_worker):
            if w is not None and shiboken6.isValid(w):
                try:
                    w.wait(2000)
                except RuntimeError:
                    pass
        wizard.close()
        QTest.qWait(50)


def test_tab_assets_starter_pack_click_routes_to_wizard_and_bound_storage(qapp, tmp_path, monkeypatch, make_studio_window):
    """
    Bấm nút Starter Pack trên TabAssets phải mở PackIngestWizard với closure gắn đúng storage,
    không chạy StarterPackWorker cũ, không ghi repo assets/.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    win = make_studio_window()
    tab = win.tab_assets
    tab.storage = storage

    opened_wizards = []

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_wizard_cls = wizard_module.PackIngestWizard

    class MockWizard(orig_wizard_cls):
        def _prepare(self):
            pass
        def exec(self):
            opened_wizards.append(self)
            return 1

    monkeypatch.setattr(wizard_module, "PackIngestWizard", MockWizard)

    # Click nút Starter Pack
    tab._on_starter_pack_clicked()

    assert len(opened_wizards) == 1
    wiz = opened_wizards[0]
    assert wiz.storage is storage
    assert wiz._prepare_factory is not None

    # Không sinh StarterPackWorker cũ
    assert not hasattr(tab, "_starter_worker") or tab._starter_worker is None
    assert getattr(tab, "_ingest_in_progress", False) is False
    assert tab.btn_starter_pack.isEnabled()


def test_tab_assets_starter_pack_reentrancy_guard(qapp, tmp_path, monkeypatch, make_studio_window):
    """Khi _ingest_in_progress đang True, gọi _on_starter_pack_clicked phải return ngay."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    win = make_studio_window()
    tab = win.tab_assets
    tab.storage = storage
    tab._ingest_in_progress = True

    called = []
    from src.ui.widgets import pack_ingest_wizard as wizard_module
    monkeypatch.setattr(wizard_module, "PackIngestWizard", lambda *args, **kwargs: called.append(True))

    tab._on_starter_pack_clicked()
    assert len(called) == 0


def test_tab_assets_starter_pack_finally_restores_button_on_constructor_raise(qapp, tmp_path, monkeypatch, make_studio_window):
    """Khi constructor của Wizard ném lỗi, finally vẫn phục hồi nút và reset guard."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    win = make_studio_window()
    tab = win.tab_assets
    tab.storage = storage

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    def broken_wizard(*args, **kwargs):
        raise RuntimeError("Lỗi khởi tạo wizard")

    monkeypatch.setattr(wizard_module, "PackIngestWizard", broken_wizard)

    with pytest.raises(RuntimeError, match="Lỗi khởi tạo wizard"):
        tab._on_starter_pack_clicked()

    assert tab.btn_starter_pack.isEnabled()
    assert "Starter Pack (SFX & LUTs)" in tab.btn_starter_pack.text()
    assert tab._ingest_in_progress is False
    assert tab._active_ingest_wizard is None


def test_tab_assets_starter_pack_finally_restores_button_on_exec_raise(qapp, tmp_path, monkeypatch, make_studio_window):
    """Khi wizard.exec() ném lỗi, finally vẫn phục hồi nút và reset guard."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "appdata"))
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    win = make_studio_window()
    tab = win.tab_assets
    tab.storage = storage

    from src.ui.widgets import pack_ingest_wizard as wizard_module
    orig_wizard_cls = wizard_module.PackIngestWizard

    class ExplodingWizard(orig_wizard_cls):
        def _prepare(self):
            pass
        def exec(self):
            raise RuntimeError("Lỗi hiển thị modal exec")

    monkeypatch.setattr(wizard_module, "PackIngestWizard", ExplodingWizard)

    with pytest.raises(RuntimeError, match="Lỗi hiển thị modal exec"):
        tab._on_starter_pack_clicked()

    assert tab.btn_starter_pack.isEnabled()
    assert tab._ingest_in_progress is False
    assert tab._active_ingest_wizard is None


def test_wizard_closed_before_timer_fires_does_not_start_factory(qapp, tmp_path):
    """Đóng hoặc reject wizard trước khi timer auto_prepare kích hoạt thì factory không được gọi."""
    storage = StorageManager(tmp_path / "config", reserve_bytes=0)
    storage.configure(tmp_path / "library")

    factory_called = []
    def fake_factory(cancel, progress):
        factory_called.append(True)
        return {"job_dir": str(tmp_path / "job"), "name": "P", "version": "1.0", "assets": []}

    wizard = PackIngestWizard(
        storage=storage,
        prepare_factory=fake_factory,
        auto_prepare=True,
    )
    try:
        # Reject ngay trước khi QTimer timeout kích hoạt
        wizard.reject()

        for _ in range(50):
            QTest.qWait(10)

        assert len(factory_called) == 0
    finally:
        if wizard.worker is not None:
            w = wizard.worker
            wizard.reject()
            w.wait(2000)
        wizard.close()

