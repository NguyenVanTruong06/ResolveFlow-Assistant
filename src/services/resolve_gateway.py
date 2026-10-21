"""
src/services/resolve_gateway.py
ResolveGateway: Manages connection and serial execution of DaVinci Resolve API commands
on a dedicated QThread.

Strict Clean Architecture: NO QtWidgets or QtGui allowed.
"""
from __future__ import annotations

import logging
import queue
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QEventLoop, QObject, QThread, QTimer, Signal, Slot

logger = logging.getLogger("ResolveFlow.ResolveGateway")


class _GatewayWorker(QObject):
    """Worker living on the dedicated QThread that executes tasks sequentially."""
    task_finished = Signal(str, object)  # task_id, result
    task_failed = Signal(str, str)       # task_id, error_message
    connection_changed = Signal(bool)

    def __init__(self, resolve_override: Optional[Any] = None):
        super().__init__()
        self._mock_mode = resolve_override is not None
        self._resolve_override = resolve_override
        self._automation: Optional[Any] = None
        self._is_connected = resolve_override is not None
        self._retry_timer: Optional[QTimer] = None
        self._task_queue: queue.Queue[Tuple[str, Callable, tuple, dict]] = queue.Queue()
        self._running = True

    def initialize(self):
        """Called once the thread has started."""
        self._init_connection()
        self._retry_timer = QTimer()
        self._retry_timer.setInterval(3000)
        self._retry_timer.timeout.connect(self._check_connection)
        self._retry_timer.start()

    def _init_connection(self):
        if self._mock_mode:
            self._is_connected = self._resolve_override is not None
            self.connection_changed.emit(self._is_connected)
            return

        try:
            from src.core.resolve_api import ResolveAutomation
            self._automation = ResolveAutomation()
            connected = self._automation.connect()
            self._is_connected = connected
            self.connection_changed.emit(connected)
        except Exception as e:
            logger.warning("Gateway initial connection error: %s", e)
            self._is_connected = False
            self.connection_changed.emit(False)

    @Slot()
    def _check_connection(self):
        """Heartbeat / retry check every 3s."""
        if self._mock_mode:
            return

        prev_connected = self._is_connected
        try:
            if not self._automation:
                from src.core.resolve_api import ResolveAutomation
                self._automation = ResolveAutomation()

            # Verify connection
            if self._automation.is_connected():
                now_connected = True
            else:
                now_connected = self._automation.connect()

            if now_connected != prev_connected:
                self._is_connected = now_connected
                self.connection_changed.emit(now_connected)
        except Exception as e:
            logger.debug("Connection check failed: %s", e)
            if prev_connected:
                self._is_connected = False
                self.connection_changed.emit(False)

    def get_resolve_object(self) -> Optional[Any]:
        if self._resolve_override is not None:
            return self._resolve_override
        if self._automation and self._automation.is_connected():
            return getattr(self._automation, "resolve", None)
        return None

    def get_automation(self) -> Optional[Any]:
        return self._automation

    @Slot()
    def stop_worker(self):
        self._running = False
        if self._retry_timer:
            self._retry_timer.stop()

    @Slot()
    def process_queue(self):
        """Processes all pending tasks sequentially."""
        while not self._task_queue.empty() and self._running:
            try:
                task_id, fn, args, kwargs = self._task_queue.get_nowait()
            except queue.Empty:
                break

            try:
                # If fn accepts resolve, inject it if needed
                resolve_obj = self.get_resolve_object()
                # Run the callable
                try:
                    result = fn(resolve_obj, *args, **kwargs)
                except TypeError:
                    result = fn(*args, **kwargs)

                self.task_finished.emit(task_id, result)
            except Exception as exc:
                logger.exception("Error executing gateway task %s", task_id)
                self.task_failed.emit(task_id, str(exc))


class ResolveGateway(QObject):
    """
    ResolveGateway coordinates all calls to DaVinci Resolve.
    Runs on a dedicated QThread to guarantee thread-safety and prevent GUI freezes.
    """
    connection_changed = Signal(bool)
    task_finished = Signal(str, object)
    task_failed = Signal(str, str)
    _request_process = Signal()
    _request_stop = Signal()

    def __init__(self, parent: Optional[QObject] = None, resolve_override: Optional[Any] = None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _GatewayWorker(resolve_override=resolve_override)
        self._worker.moveToThread(self._thread)

        self._callbacks: Dict[str, Tuple[Optional[Callable], Optional[Callable]]] = {}
        self._is_connected = resolve_override is not None

        # Forward signals
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.task_finished.connect(self._on_task_finished)
        self._worker.task_failed.connect(self._on_task_failed)

        self._request_process.connect(self._worker.process_queue)
        self._request_stop.connect(self._worker.stop_worker)

        self._thread.started.connect(self._worker.initialize)
        self._thread.start()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def set_resolve_override(self, mock_resolve: Any):
        """Used in unit tests to inject mock Resolve instances."""
        self._worker._mock_mode = True
        self._worker._resolve_override = mock_resolve
        self._worker._is_connected = mock_resolve is not None
        self._is_connected = mock_resolve is not None
        self.connection_changed.emit(self._is_connected)

    def submit(
        self,
        fn: Callable,
        *args: Any,
        callback: Optional[Callable[[Any], None]] = None,
        errback: Optional[Callable[[str], None]] = None,
        **kwargs: Any
    ) -> str:
        """
        Submit a task to be run sequentially on the gateway thread.
        Returns a unique task_id.
        Optional callback and errback are invoked on the calling (usually Main) thread.
        """
        task_id = str(uuid.uuid4())
        if callback or errback:
            self._callbacks[task_id] = (callback, errback)

        self._worker._task_queue.put((task_id, fn, args, kwargs))
        # Trigger processing safely across threads via queued signal
        self._request_process.emit()
        return task_id

    def submit_sync(self, fn: Callable, *args: Any, timeout: float = 3.0, **kwargs: Any) -> Any:
        """
        Helper method to run a quick query synchronously (e.g. in test setup).
        Uses a local QEventLoop to wait with timeout without freezing Qt event processing.
        """
        loop = QEventLoop()
        result_holder: List[Any] = []
        error_holder: List[str] = []

        def _cb(res):
            result_holder.append(res)
            loop.quit()

        def _eb(err):
            error_holder.append(err)
            loop.quit()

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(int(timeout * 1000))

        self.submit(fn, *args, callback=_cb, errback=_eb, **kwargs)
        loop.exec()

        if timer.isActive():
            timer.stop()

        if result_holder:
            return result_holder[0]
        if error_holder:
            raise RuntimeError(error_holder[0])
        raise TimeoutError(f"Gateway submit_sync timed out after {timeout}s")

    @Slot(bool)
    def _on_connection_changed(self, connected: bool):
        self._is_connected = connected
        self.connection_changed.emit(connected)

    @Slot(str, object)
    def _on_task_finished(self, task_id: str, result: object):
        cb_tuple = self._callbacks.pop(task_id, None)
        if cb_tuple and cb_tuple[0]:
            try:
                cb_tuple[0](result)
            except Exception as e:
                logger.exception("Error in gateway callback: %s", e)
        self.task_finished.emit(task_id, result)

    @Slot(str, str)
    def _on_task_failed(self, task_id: str, error: str):
        cb_tuple = self._callbacks.pop(task_id, None)
        if cb_tuple and cb_tuple[1]:
            try:
                cb_tuple[1](error)
            except Exception as e:
                logger.exception("Error in gateway errback: %s", e)
        self.task_failed.emit(task_id, error)

    def shutdown(self):
        """Stops the worker thread cleanly."""
        self._request_stop.emit()
        self._thread.quit()
        self._thread.wait(2000)

    def __del__(self):
        try:
            if hasattr(self, "_thread") and self._thread.isRunning():
                self.shutdown()
        except Exception:
            pass
