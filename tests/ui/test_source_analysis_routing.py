"""
tests/ui/test_source_analysis_routing.py
Kiểm thử toàn diện bộ điều phối phân tích nguồn (G05-05B-I2 & I2-R1):
- Nested counters thật từ worker 2 clips, spy QMessageBox chỉ 1 summary đúng 2 clip, 0 'Dựng timeline'.
- QTest.mouseClick(btn_scan) + Review actual button click: assert 0 Pipeline/Resolve; second click = Cancel.
- Logical terminal giữ Review disabled tới physical finished; repeat logical terminal không duplicate popup.
- Job A finished late sau active Job B: cleanup A không mutate active B/locks; deleteLater sau stop được gọi.
- Start failure dọn dẹp an toàn registry và phục hồi controls, không leak pending.
- Real close pending qua window.close(): cancel requested, event ignored, 0 popup; physical finished -> idle -> deferred close.
- Inventory / visual / describe active hoặc shutdown pending chặn start_source_analysis.
- Settings/source mutation hủy runtime verified status về candidate; completion lệch config không nạp Review.
- Real QThread test end-to-end với worker thật và mock backend.
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from src.ui.director.director_state import DirectorConfig, SourceClipInfo
from src.ui.director.director_controller import DirectorController
from src.ui.director.director_window import DirectorWindow
from src.ui.director.pages.source_page import SourcePage, SourceClipRow
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisConfig,
    SourceAnalysisWorker,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def suppress_modal_dialogs():
    """Tự động mock tất cả modal dialog QMessageBox để tránh treo test trên CI/offscreen."""
    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn, \
         patch.object(QMessageBox, "question") as mock_quest:
        mock_quest.return_value = QMessageBox.StandardButton.Yes
        yield mock_info, mock_warn, mock_quest


def load_clips_into_page(page, paths, qapp):
    """Helper nạp clip an toàn vào SourcePage đồng bộ không phụ thuộc ffprobe ngầm."""
    clips = [
        SourceClipInfo(
            path=os.path.normpath(p),
            filename=os.path.basename(p),
            duration_sec=10.0,
            duration_str="00:10",
            resolution="1080p",
            has_cache=False,
            file_size_mb=5.0
        )
        for p in paths
    ]
    page._clip_infos = clips
    page._refresh_clip_list()
    page.clips_changed.emit([c.path for c in clips])
    qapp.processEvents()


class FakeSourceWorker(QThread):
    """Fake worker mô phỏng tách biệt logical terminal và physical finished."""
    progress_signal = Signal(str, int, int)
    status_signal = Signal(str, str, dict)
    counters_signal = Signal(str, dict)
    finished_signal = Signal(str, bool, str, dict)
    log_signal = Signal(str, str)

    def __init__(self, config: SourceAnalysisConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._is_cancelled = False
        self._is_running_fake = False
        self.delete_later_called = False

    def isRunning(self) -> bool:
        return self._is_running_fake

    def start(self, priority=None):
        self._is_running_fake = True

    def cancel(self):
        self._is_cancelled = True

    def deleteLater(self):
        self.delete_later_called = True
        super().deleteLater()

    def emit_logical_terminal(self, success: bool, message: str, counters: dict):
        self.finished_signal.emit(self.config.job_id, success, message, counters)

    def emit_physical_finished(self):
        self._is_running_fake = False
        self.finished.emit()


def test_nested_counters_from_worker_2clips_and_zero_timeline_popup(qapp, tmp_path):
    """Nested counters schema từ worker 2 clips, spy QMessageBox chỉ 1 summary đúng 2 clip, 0 'Dựng timeline'."""
    p1 = str(tmp_path / "clip1.mp4")
    p2 = str(tmp_path / "clip2.mp4")
    with open(p1, "wb") as f:
        f.write(b"data1")
    with open(p2, "wb") as f:
        f.write(b"data2")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1, p2], qapp)

    created_workers = []

    def fake_factory(cfg):
        w = FakeSourceWorker(cfg)
        created_workers.append(w)
        return w

    window.page_source.start_source_analysis(worker_factory=fake_factory)
    assert len(created_workers) == 1
    worker = created_workers[0]

    # Schema nested counters chuẩn xác từ SourceAnalysisWorker
    nested_counters = {
        "total_clips": 2,
        "audio": {
            "cached": 1,
            "new": 1,
            "no_audio_track": 0,
            "failed": 0,
            "unavailable": 0,
            "skipped": 0,
            "cancelled": 0,
            "pending": 0,
            "no_speech": 0,
        },
        "visual": {
            "cached": 1,
            "new": 1,
            "failed": 0,
            "skipped": 0,
            "cancelled": 0,
            "pending": 0,
        }
    }

    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn:

        worker.emit_logical_terminal(True, "completed", nested_counters)
        qapp.processEvents()

        # Đúng duy nhất 1 popup summary nguồn
        assert mock_info.call_count == 1
        assert mock_warn.call_count == 0

        title = mock_info.call_args[0][1]
        body = mock_info.call_args[0][2]

        assert title == "Phân tích nguồn hoàn tất"
        assert "2 clip footage" in body
        assert "Thoại: 2/2" in body
        assert "Khung hình: 2/2" in body
        # Tuyệt đối KHÔNG có câu timeline sót
        assert "Dựng timeline" not in body

    worker.emit_physical_finished()
    qapp.processEvents()
    window.close()


def test_qtest_mouse_click_btn_scan_and_review_actual_button_click(qapp, tmp_path):
    """QTest.mouseClick(btn_scan) + Review actual button click: assert 0 Pipeline/Resolve; second click = Cancel."""
    p1 = str(tmp_path / "clip_click.mp4")
    with open(p1, "wb") as f:
        f.write(b"click_data")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1], qapp)

    controller.start_pipeline = MagicMock()
    created_workers = []

    def fake_factory(cfg):
        w = FakeSourceWorker(cfg)
        created_workers.append(w)
        return w

    window.page_source._worker_factory_override = fake_factory

    with patch("src.core.resolve_api.ResolveAutomation") as mock_resolve:
        # 1. Click thực tế nút scan trên SourcePage bằng QTest
        QTest.mouseClick(window.page_source.btn_scan, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(created_workers) == 1
        worker1 = created_workers[0]
        assert window.page_source.is_source_analysis_active() is True
        assert "Hủy" in window.page_source.btn_scan.text()

        # 2. Click lần thứ 2 khi đang active: kích hoạt Cancel, KHÔNG tạo worker thứ 2
        QTest.mouseClick(window.page_source.btn_scan, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        assert len(created_workers) == 1  # Không tạo thêm worker
        assert worker1._is_cancelled is True  # Worker nhận lệnh hủy cooperative

        # Dọn dẹp worker 1
        worker1.emit_logical_terminal(False, "cancelled", {
            "total_clips": 1,
            "audio": {"cancelled": 1},
            "visual": {"cancelled": 1}
        })
        worker1.emit_physical_finished()
        qapp.processEvents()
        assert window.page_source.is_source_analysis_active() is False

        # 3. Chuyển sang bước 3 (ReviewPage) và click nút scan trên ReviewPage
        window.go_to_step(3)
        assert window.current_step == 3

        QTest.mouseClick(window.page_review.btn_review_scan, Qt.MouseButton.LeftButton)
        qapp.processEvents()

        # Đã tự chuyển về bước 1 và khởi tạo worker 2 source-only
        assert window.current_step == 1
        assert len(created_workers) == 2
        assert controller.start_pipeline.call_count == 0
        assert mock_resolve.call_count == 0

        worker2 = created_workers[1]
        worker2.emit_logical_terminal(True, "completed", {"total_clips": 1, "audio": {"new": 1}, "visual": {"new": 1}})
        worker2.emit_physical_finished()
        qapp.processEvents()

    window.close()


def test_logical_terminal_keeps_review_disabled_until_physical_finished(qapp, tmp_path):
    """Logical terminal giữ Review disabled tới physical finished; repeat logical terminal không duplicate popup."""
    p1 = str(tmp_path / "clip_term.mp4")
    with open(p1, "wb") as f:
        f.write(b"term_data")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1], qapp)

    created = []

    def fake_factory(cfg):
        w = FakeSourceWorker(cfg)
        created.append(w)
        return w

    window.page_source.start_source_analysis(worker_factory=fake_factory)
    assert len(created) == 1
    worker = created[0]
    assert window.page_source.is_source_analysis_active() is True

    with patch.object(QMessageBox, "information") as mock_info:
        # 1. Phát logical terminal lần 1
        worker.emit_logical_terminal(True, "completed", {"total_clips": 1, "audio": {"new": 1}, "visual": {"new": 1}})
        qapp.processEvents()

        assert mock_info.call_count == 1
        # Review button và controls chính VẪN BỊ KHÓA vì physical thread chưa finished
        assert window.page_review.btn_review_scan.isEnabled() is False
        assert window.btn_primary.isEnabled() is False
        assert window.page_source.is_source_analysis_active() is True

        # 2. Phát lặp lại logical terminal cho cùng job_id: bỏ qua, không duplicate popup
        worker.emit_logical_terminal(True, "completed", {"total_clips": 1, "audio": {"new": 1}, "visual": {"new": 1}})
        qapp.processEvents()
        assert mock_info.call_count == 1  # Vẫn chỉ là 1

        # 3. Physical finished: lúc này mới mở khóa hoàn toàn
        worker.emit_physical_finished()
        qapp.processEvents()

        assert window.page_review.btn_review_scan.isEnabled() is True
        assert window.btn_primary.isEnabled() is True
        assert window.page_source.is_source_analysis_active() is False

    window.close()


def test_job_a_late_finished_does_not_mutate_active_job_b_and_disposes(qapp, tmp_path):
    """Job A finished late sau active Job B: cleanup A không mutate active B/locks; deleteLater sau stop được gọi."""
    p1 = str(tmp_path / "clip_late_a.mp4")
    with open(p1, "wb") as f:
        f.write(b"data_a")

    page = SourcePage()
    load_clips_into_page(page, [p1], qapp)

    created_a = []

    def factory_a(cfg):
        w = FakeSourceWorker(cfg)
        created_a.append(w)
        return w

    # Bắt đầu Job A
    page.start_source_analysis(worker_factory=factory_a)
    worker_a = created_a[0]
    job_a_id = page._active_source_job_id
    token_a = f"source_analysis_{job_a_id}"
    assert token_a in page._workers_registry

    # Giả lập Job B bắt đầu
    created_b = []

    def factory_b(cfg):
        w = FakeSourceWorker(cfg)
        created_b.append(w)
        return w

    cfg_b = SourceAnalysisConfig(
        job_id="job_B_custom",
        video_paths=(os.path.normpath(p1),),
        whisper_model="small",
        whisper_language="vi",
        enable_audio=True,
        enable_visual_sampling=True,
        visual_interval_sec=2.0,
        visual_max_samples=120,
        local_files_only=True
    )
    worker_b = FakeSourceWorker(cfg_b)
    page._active_source_worker = worker_b
    page._active_source_job_id = "job_B_custom"
    page._workers_registry["source_analysis_job_B_custom"] = worker_b

    # Bây giờ luồng phụ của Job A mới phát tín hiệu physical finished (đến muộn)
    worker_a.emit_physical_finished()
    qapp.processEvents()

    # Xác minh:
    # 1. Job A được dọn dẹp khỏi registry và gọi deleteLater
    assert token_a not in page._workers_registry
    assert worker_a.delete_later_called is True

    # 2. Job B đang active tuyệt đối KHÔNG bị ảnh hưởng hay bị clear
    assert page._active_source_job_id == "job_B_custom"
    assert page._active_source_worker is worker_b
    assert "source_analysis_job_B_custom" in page._workers_registry
    assert page.is_source_analysis_active() is True

    # Kết thúc Job B
    page._on_source_thread_finished_for_job(worker_b, "job_B_custom", "source_analysis_job_B_custom")
    qapp.processEvents()
    assert "source_analysis_job_B_custom" not in page._workers_registry
    assert worker_b.delete_later_called is True
    assert page.is_source_analysis_active() is False

    page.close()


def test_start_failure_recovers_state_and_unlocks_controls(qapp, tmp_path):
    """Constructor/start failure cleanup registry/controls; không leak pending."""
    p1 = str(tmp_path / "clip_fail.mp4")
    with open(p1, "wb") as f:
        f.write(b"fail_data")

    page = SourcePage()
    load_clips_into_page(page, [p1], qapp)

    # Thất bại khi worker.start() ném lỗi
    failing_worker = None

    def failing_factory(cfg):
        nonlocal failing_worker
        failing_worker = FakeSourceWorker(cfg)
        failing_worker.start = MagicMock(side_effect=RuntimeError("Cannot start thread"))
        return failing_worker

    with patch.object(QMessageBox, "warning"):
        res = page.start_source_analysis(worker_factory=failing_factory)
        assert res is False

    # State phải được phục hồi an toàn, controls không bị kẹt
    assert page.is_source_analysis_active() is False
    assert page._active_source_worker is None
    assert failing_worker.delete_later_called is True

    page.close()


def test_real_close_pending_via_window_close_defers_cleanly(qapp, tmp_path):
    """Real close pending qua window.close() khi fake worker còn running: cancel requested, deferred close."""
    p1 = str(tmp_path / "clip_close.mp4")
    with open(p1, "wb") as f:
        f.write(b"close_data")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1], qapp)

    created = []

    def fake_factory(cfg):
        w = FakeSourceWorker(cfg)
        created.append(w)
        return w

    window.page_source.start_source_analysis(worker_factory=fake_factory)
    assert len(created) == 1
    worker = created[0]
    assert window.page_source.is_source_analysis_active() is True

    with patch.object(QMessageBox, "information") as mock_info, \
         patch.object(QMessageBox, "warning") as mock_warn:

        # Đóng cửa sổ trong lúc worker đang chạy
        window.close()
        qapp.processEvents()

        # Cửa sổ chuyển sang trạng thái pending close, worker nhận lệnh cancel
        assert window._source_close_pending is True
        assert worker._is_cancelled is True

        # Late logical terminal không được mở popup
        worker.emit_logical_terminal(False, "cancelled", {"total_clips": 1, "audio": {"cancelled": 1}, "visual": {"cancelled": 1}})
        qapp.processEvents()
        assert mock_info.call_count == 0
        assert mock_warn.call_count == 0

        # Physical thread dừng hẳn -> kích hoạt idle -> đóng an toàn
        worker.emit_physical_finished()
        qapp.processEvents()

        assert window._is_closed is True
        assert window.page_source.is_source_analysis_active() is False


def test_inventory_visual_describe_active_and_shutdown_pending_block_start(qapp, tmp_path):
    """Inventory/visual/describe active và shutdown pending chặn start; không khởi model."""
    p1 = str(tmp_path / "clip_block.mp4")
    with open(p1, "wb") as f:
        f.write(b"block_data")

    page = SourcePage()
    load_clips_into_page(page, [p1], qapp)

    # 1. Khi inventory worker đang active
    page._active_worker = MagicMock()
    assert page.start_source_analysis() is False
    page._active_worker = None

    # 2. Khi visual scan worker đang active
    page._active_visual_worker = MagicMock()
    assert page.start_source_analysis() is False
    page._active_visual_worker = None

    # 3. Khi visual describe worker đang active
    page._active_describe_worker = MagicMock()
    assert page.start_source_analysis() is False
    page._active_describe_worker = None

    # 4. Khi shutdown pending
    page._shutdown_pending = True
    assert page.start_source_analysis() is False
    page._shutdown_pending = False

    page.close()


def test_settings_mutation_invalidates_runtime_status_and_wrong_config_skips_review(qapp, tmp_path):
    """Settings/source refresh invalidate runtime verified status; wrong config completion không refresh Review."""
    p1 = str(tmp_path / "clip_inv.mp4")
    with open(p1, "wb") as f:
        f.write(b"inv_data")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1], qapp)

    row = window.page_source._clip_rows[os.path.normpath(p1)]

    # Gán trạng thái verified giả lập vào hàng
    row.update_analysis_status({
        "audio_status": "new",
        "raw_subtitles": [{"text": "Đã bóc tách"}],
        "cache_saved": True,
        "visual_status": "new"
    })
    window.page_source._runtime_clip_status[os.path.normpath(p1)] = {"audio_status": "new"}
    assert "🟢 Thoại: 1 câu" in row.lbl_cache.text()

    # 1. Đổi model whisper -> lập tức invalidate trạng thái verified thành candidate
    window.page_source._set_model("medium")
    assert len(window.page_source._runtime_clip_status) == 0
    assert "🟢 Thoại: 1 câu" not in row.lbl_cache.text()

    # 2. Hoàn tất phân tích nhưng cấu hình cửa sổ đã bị lệch -> bỏ qua nạp ReviewPage
    window.page_review.load_transcripts_from_cache = MagicMock()
    window.config.whisper_model = "large-v3"  # Khác với model đã phân tích là "medium"

    # Gán snapshot cấu hình hoàn tất là "medium"
    window.page_source._runtime_status_config = ("medium", "vi", (os.path.normpath(p1),))

    window._on_source_analysis_completed(
        "job_mismatch", True, "completed",
        {"total_clips": 1, "audio": {"new": 1}, "visual": {"new": 1}}
    )
    # Vì config lệch, ReviewPage không bị nạp sai
    assert window.page_review.load_transcripts_from_cache.call_count == 0

    window.close()


def test_real_qthread_source_analysis_worker_routing(qapp, tmp_path):
    """End-to-end với real QThread SourceAnalysisWorker stubbed dependencies."""
    p1 = str(tmp_path / "clip_real.mp4")
    with open(p1, "wb") as f:
        f.write(b"REAL_QTHREAD_DATA")

    controller = DirectorController()
    window = DirectorWindow(controller=controller)
    load_clips_into_page(window.page_source, [p1], qapp)

    # Khởi tạo mock cache và visual index để worker thật chạy trơn tru offline
    mock_cache = MagicMock()
    mock_cache.get_cached_scan.return_value = {
        "raw_subtitles": [{"start": 0.0, "end": 2.0, "text": "Kiểm thử QThread thật"}],
        "clip_dur": 10.0,
        "whisper_model": "small",
        "whisper_language": "vi"
    }
    mock_vis = MagicMock()
    mock_vis.has_sampled_frames.return_value = True

    def real_worker_factory(cfg):
        return SourceAnalysisWorker(cfg, cache_mgr=mock_cache, visual_index=mock_vis)

    with patch.object(QMessageBox, "information"):
        # Chạy phân tích với worker QThread thật
        started = window.page_source.start_source_analysis(worker_factory=real_worker_factory)
        assert started is True
        assert window.page_source.is_source_analysis_active() is True

        worker = window.page_source._active_source_worker
        assert isinstance(worker, SourceAnalysisWorker)

        try:
            # Chờ worker hoàn tất trên luồng phụ
            finished = worker.wait(5000)
            assert finished is True

            # Xử lý sự kiện Qt trên main thread để nhận signals
            qapp.processEvents()

            # Worker đã dừng hoàn toàn
            assert window.page_source.is_source_analysis_active() is False
            assert window.btn_primary.isEnabled() is True
        finally:
            if worker.isRunning():
                worker.cancel()
                worker.wait(3000)

    window.close()
