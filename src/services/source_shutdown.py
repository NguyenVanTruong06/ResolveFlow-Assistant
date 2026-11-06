"""Trì hoãn thoát Qt tới khi các tác vụ nạp nguồn, phân tích và tiền kiểm đã dừng."""
from typing import Set, Any
from PySide6.QtCore import QObject, QEvent, QTimer, Slot


class SourceShutdownGuard(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self._pending = False
        self._allowed = False
        self._collecting = False
        self._quit_called = False
        self._director_windows: Set[Any] = set()
        self._pages: Set[Any] = set()
        self._library_jobs: Set[Any] = set()
        self._finished_library_jobs: Set[Any] = set()
        self._library_job_slots: dict[Any, Any] = {}
        self._app._source_shutdown_guard = self
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._app and event.type() == QEvent.Type.Quit and not self._allowed:
            self.request_quit()
            return True
        return False

    @Slot()
    def request_quit(self):
        if self._pending or self._allowed:
            return
        self._pending = True
        self._collecting = True

        from PySide6.QtWidgets import QWidget
        from src.ui.director.pages.source_page import SourcePage
        from src.ui.widgets.pack_ingest_wizard import LibraryJob

        # Danh sách SourcePages trực thuộc DirectorWindow đã có protocol mới (tránh double-own)
        director_source_pages: Set[Any] = set()

        # 1. Phát hiện tất cả DirectorWindow (kể cả ẩn hoặc con lồng nhau)
        top_widgets = list(self._app.topLevelWidgets())
        for widget in top_widgets:
            candidates = [widget]
            try:
                candidates.extend(widget.findChildren(QWidget))
            except Exception:
                pass

            for c in candidates:
                is_director = (
                    hasattr(c, "is_analysis_and_preflight_idle")
                    and hasattr(c, "shutdown_analysis_and_preflight_workers")
                    and hasattr(c, "analysis_and_preflight_idle")
                )
                if is_director:
                    if c not in self._director_windows:
                        self._director_windows.add(c)
                        c.analysis_and_preflight_idle.connect(self._check_idle)
                    try:
                        for sp in c.findChildren(SourcePage):
                            director_source_pages.add(sp)
                    except Exception:
                        pass

        # 2. Phát hiện các SourcePage độc lập (non-Director)
        for widget in top_widgets:
            try:
                pages = widget.findChildren(SourcePage)
                if isinstance(widget, SourcePage):
                    pages.append(widget)
            except Exception:
                pages = []
            for page in pages:
                if page not in director_source_pages:
                    if getattr(page, "_workers_registry", None) and page not in self._pages:
                        self._pages.add(page)
                        page.workers_idle.connect(self._check_idle)

        # 3. Phát hiện LibraryJobs đang chạy
        for job in list(LibraryJob.active_jobs):
            is_running = True
            try:
                is_running = job.isRunning()
            except Exception:
                is_running = True  # unknown query -> fail-safe coi là bận
            if is_running:
                if job not in self._library_jobs:
                    self._library_jobs.add(job)
                    slot = lambda j=job: self._on_library_job_finished(j)
                    self._library_job_slots[job] = slot
                    try:
                        job.finished.connect(slot)
                    except Exception:
                        pass

        # 4. Yêu cầu hủy hợp tác cho toàn bộ các owner/job đã đăng ký
        for win in list(self._director_windows):
            try:
                win.shutdown_analysis_and_preflight_workers(application_quit=True)
            except TypeError:
                try:
                    win.shutdown_analysis_and_preflight_workers()
                except Exception:
                    pass
            except Exception:
                pass

        for page in list(self._pages):
            try:
                page.shutdown_workers()
            except Exception:
                pass

        for job in list(self._library_jobs):
            try:
                job.requestInterruption()
            except Exception:
                pass

        # 5. Hoàn tất thu thập; bắt đầu đánh giá trạng thái rảnh
        self._collecting = False
        self._check_idle()

    def _on_library_job_finished(self, job: Any) -> None:
        self._finished_library_jobs.add(job)
        slot = self._library_job_slots.pop(job, None)
        if slot is not None:
            try:
                job.finished.disconnect(slot)
            except Exception:
                pass
        self._check_idle()

    @Slot()
    def _check_idle(self):
        if not self._pending or self._collecting:
            return

        # Kiểm tra DirectorWindow
        for win in self._director_windows:
            try:
                if not win.is_analysis_and_preflight_idle():
                    return
            except Exception:
                # Lỗi query được xử lý fail-safe là bận
                return

        # Kiểm tra các SourcePage độc lập
        for page in self._pages:
            try:
                if getattr(page, "_workers_registry", None):
                    return
                if hasattr(page, "is_source_analysis_active") and page.is_source_analysis_active():
                    return
            except Exception:
                return

        # Kiểm tra LibraryJobs
        for job in list(self._library_jobs):
            if job in self._finished_library_jobs:
                continue
            try:
                if job.isRunning():
                    return
            except Exception:
                # Lỗi query được xử lý fail-safe là bận
                return

        # Toàn bộ đã rảnh vật lý: ngắt kết nối an toàn và thoát đúng 1 lần
        for win in list(self._director_windows):
            try:
                win.analysis_and_preflight_idle.disconnect(self._check_idle)
            except Exception:
                pass
        self._director_windows.clear()

        for page in list(self._pages):
            try:
                page.workers_idle.disconnect(self._check_idle)
            except Exception:
                pass
        self._pages.clear()

        for job in list(self._library_jobs):
            slot = self._library_job_slots.pop(job, None)
            if slot is not None:
                try:
                    job.finished.disconnect(slot)
                except Exception:
                    pass
        self._library_jobs.clear()
        self._finished_library_jobs.clear()

        self._pending = False
        self._allowed = True
        if not self._quit_called:
            self._quit_called = True
            QTimer.singleShot(0, self._app.quit)
