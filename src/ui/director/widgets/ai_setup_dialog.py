"""
src/ui/director/widgets/ai_setup_dialog.py
Hộp thoại Thiết Lập AI Cục Bộ (Local AI Setup Dialog) cho ChunDVC / ResolveFlow:
- Thiết lập thư mục lưu trữ (Storage Root) và hiển thị dung lượng đĩa trống thời gian thực.
- Quản lý mô hình Whisper (nhận diện giọng nói) tải qua HuggingFace Hub cache.
- Quản lý máy chủ Ollama độc lập (Cài đặt không cần sudo, Khởi động, Dừng, kiểm tra PID sở hữu).
- Quản lý mô hình thị giác Vision Model (mặc định qwen3-vl:2b-instruct, Tải về, Xác thực độ sẵn sàng).
- Toàn bộ tác vụ nặng chạy trên QThread nền riêng biệt, hỗ trợ hủy hợp tác (cooperative cancel),
  không gián đoạn luồng giao diện chính và bảo vệ an toàn khi đóng cửa sổ.
"""

import os
import sys
import logging
from typing import Optional, Callable, Dict, List
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QProgressBar, QFrame, QFileDialog, QMessageBox,
    QScrollArea
)
from PySide6.QtCore import Qt, Signal, QThread, Slot, QTimer
from PySide6.QtGui import QColor

from src.core.branding import APP_NAME
from src.core.transcriber import MODEL_DISK_SIZES
from src.services.local_ai_setup_service import (
    LocalAISetupService,
    OllamaInstallStatus,
    VisionModelStatus,
    WhisperModelStatus,
    DEFAULT_VISION_MODEL,
    VisionModelStatusResult,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# BACKGROUND THREAD WORKERS
# ==============================================================================

class OllamaInstallWorker(QThread):
    progress_signal = Signal(float, str)
    finished_signal = Signal(bool, str)

    def __init__(self, service: LocalAISetupService, parent=None):
        super().__init__(parent)
        self.service = service
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            ok, msg = self.service.install_ollama(
                progress_cb=lambda pct, text: self.progress_signal.emit(pct, text),
                cancel_cb=lambda: self._is_cancelled
            )
            self.finished_signal.emit(ok, msg)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class OllamaDaemonWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, service: LocalAISetupService, action: str = "start", parent=None):
        super().__init__(parent)
        self.service = service
        self.action = action
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                self.finished_signal.emit(False, "Thao tác đã bị hủy.")
                return
            if self.action == "start":
                ok, msg = self.service.start_ollama_daemon(timeout=10.0)
            else:
                ok, msg = self.service.stop_ollama_daemon(timeout=10.0)
            if self._is_cancelled:
                self.finished_signal.emit(False, "Thao tác đã bị hủy.")
                return
            self.finished_signal.emit(ok, msg)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class VisionModelPullWorker(QThread):
    progress_signal = Signal(float, str)
    finished_signal = Signal(bool, str)

    def __init__(self, service: LocalAISetupService, model_id: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.model_id = model_id
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            def _prog(pct, msg):
                val = pct if pct is not None else -1.0
                self.progress_signal.emit(val, msg)

            ok, msg = self.service.pull_vision_model(
                model=self.model_id,
                progress_cb=_prog,
                cancel_cb=lambda: self._is_cancelled
            )
            self.finished_signal.emit(ok, msg)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


class VisionModelVerifyWorker(QThread):
    finished_signal = Signal(bool, str)

    def __init__(self, service: LocalAISetupService, model_id: str, parent=None):
        super().__init__(parent)
        self.service = service
        self.model_id = model_id
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self._is_cancelled:
                self.finished_signal.emit(False, "Thao tác đã bị hủy.")
                return
            ok, msg = self.service.verify_vision_model(model=self.model_id)
            if self._is_cancelled:
                self.finished_signal.emit(False, "Thao tác đã bị hủy.")
                return
            self.finished_signal.emit(ok, msg)
        except Exception as exc:
            self.finished_signal.emit(False, str(exc))


# ==============================================================================
# AI SETUP DIALOG WIDGET
# ==============================================================================

class AISetupDialog(QDialog):
    """Hộp thoại cấu hình và thiết lập toàn diện Local AI."""

    whisper_model_changed = Signal(str)
    vision_model_changed = Signal(str)
    storage_root_changed = Signal(str)

    def __init__(
        self,
        service: Optional[LocalAISetupService] = None,
        selected_whisper_model: str = "small",
        selected_vision_model: str = DEFAULT_VISION_MODEL,
        parent: Optional[QWidget] = None,
        whisper_prep_delegate: Optional[Callable] = None,
        whisper_cancel_delegate: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Thiết Lập AI Cục Bộ · {APP_NAME}")
        self.setMinimumSize(640, 720)
        self.resize(680, 780)

        self.service = service if service is not None else LocalAISetupService()
        self._selected_whisper = selected_whisper_model
        self._selected_vision = selected_vision_model
        self._whisper_prep_delegate = whisper_prep_delegate
        self._whisper_cancel_delegate = whisper_cancel_delegate

        # Trạng thái tác vụ nền và luồng worker
        self._active_workers: set = set()
        self._current_worker: Optional[QThread] = None
        self._whisper_in_progress: bool = False
        self._close_pending: bool = False

        self._init_ui()
        # Khởi tạo không chặn luồng UI bằng network I/O
        self._refresh_all_statuses(probe_network=False)

    def _register_worker(self, worker: QThread):
        """Đăng ký worker và bảo vệ vòng đời qua native QThread.finished."""
        self._active_workers.add(worker)
        self._current_worker = worker
        worker.finished.connect(self._on_worker_thread_finished, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _on_worker_thread_finished(self):
        worker = self.sender()
        self._active_workers.discard(worker)
        if self._current_worker is worker:
            self._current_worker = None
        worker.deleteLater()
        self._check_pending_close()

    @property
    def _active_worker(self) -> Optional[QThread]:
        """Tương thích ngược thuộc tính _active_worker."""
        if self._current_worker and self._current_worker.isRunning():
            return self._current_worker
        for w in list(self._active_workers):
            if w.isRunning():
                return w
        return self._current_worker

    @_active_worker.setter
    def _active_worker(self, worker: Optional[QThread]):
        self._current_worker = worker
        if worker is not None:
            self._register_worker(worker)
        else:
            self._active_workers = {w for w in self._active_workers if w.isRunning()}

    def is_busy(self) -> bool:
        """Kiểm tra có tác vụ nền nào đang hoạt động hay không."""
        return bool(self._active_workers) or self._whisper_in_progress

    def _init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #121316;
                color: #e4e4e7;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
            QLabel {
                color: #d4d4d8;
                font-size: 12px;
            }
            QFrame.card_frame {
                background-color: #1a1b1f;
                border: 1px solid #2d3039;
                border-radius: 8px;
            }
            QLineEdit, QComboBox {
                background-color: #23252b;
                color: #f4f4f5;
                border: 1px solid #383c48;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #d94f3d;
            }
            QPushButton {
                background-color: #262830;
                color: #ededf0;
                border: 1px solid #383c48;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #323540;
                border-color: #4b5161;
                color: #ffffff;
            }
            QPushButton:disabled {
                background-color: #1c1d22;
                color: #71717a;
                border-color: #27272a;
            }
            QPushButton.primary_btn {
                background-color: #d94f3d;
                color: #ffffff;
                border: 1px solid #e86352;
            }
            QPushButton.primary_btn:hover {
                background-color: #e86352;
            }
            QProgressBar {
                background-color: #23252b;
                border: none;
                border-radius: 3px;
                height: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #d94f3d;
                border-radius: 3px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # 1. Header
        lbl_title = QLabel("⚙️ Cấu Hình & Cài Đặt AI Cục Bộ (Local AI)")
        lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(lbl_title)

        lbl_desc = QLabel(
            "Tự động quản lý nhận diện giọng nói (Whisper), máy chủ hình ảnh (Ollama) và mô hình AI\n"
            "hoàn toàn ngoại tuyến (offline), bảo vệ tuyệt đối sự riêng tư cho dự án dựng phim."
        )
        lbl_desc.setStyleSheet("color: #a1a1aa; font-size: 11.5px; line-height: 1.4;")
        main_layout.addWidget(lbl_desc)

        # Scroll area cho nội dung
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(0, 4, 6, 4)
        c_layout.setSpacing(14)

        # ----------------------------------------------------------------------
        # PHẦN 1: THƯ MỤC LƯU TRỮ AI (STORAGE ROOT)
        # ----------------------------------------------------------------------
        card_stor = QFrame()
        card_stor.setProperty("class", "card_frame")
        stor_layout = QVBoxLayout(card_stor)
        stor_layout.setContentsMargins(14, 12, 14, 14)
        stor_layout.setSpacing(8)

        lbl_sec1 = QLabel("📁 1. THƯ MỤC CÀI ĐẶT & LƯU TRỮ AI (STORAGE ROOT)")
        lbl_sec1.setStyleSheet("font-size: 11px; font-weight: bold; color: #f59e0b; letter-spacing: 0.5px;")
        stor_layout.addWidget(lbl_sec1)

        row_stor = QHBoxLayout()
        row_stor.setSpacing(8)
        self.txt_storage_path = QLineEdit(self.service.get_storage_root())
        self.txt_storage_path.setReadOnly(True)
        row_stor.addWidget(self.txt_storage_path, stretch=1)

        self.btn_browse_storage = QPushButton("Duyệt...")
        self.btn_browse_storage.clicked.connect(self._on_browse_storage)
        row_stor.addWidget(self.btn_browse_storage)
        stor_layout.addLayout(row_stor)

        self.lbl_disk_free = QLabel("Dung lượng ổ đĩa khả dụng: Đang kiểm tra...")
        self.lbl_disk_free.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        stor_layout.addWidget(self.lbl_disk_free)
        c_layout.addWidget(card_stor)

        # ----------------------------------------------------------------------
        # PHẦN 2: NHẬN DIỆN GIỌNG NÓI WHISPER (SPEECH-TO-TEXT)
        # ----------------------------------------------------------------------
        card_whisper = QFrame()
        card_whisper.setProperty("class", "card_frame")
        w_layout = QVBoxLayout(card_whisper)
        w_layout.setContentsMargins(14, 12, 14, 14)
        w_layout.setSpacing(8)

        lbl_sec2 = QLabel("🎙️ 2. MÔ HÌNH NHẬN DIỆN GIỌNG NÓI (WHISPER)")
        lbl_sec2.setStyleSheet("font-size: 11px; font-weight: bold; color: #f59e0b; letter-spacing: 0.5px;")
        w_layout.addWidget(lbl_sec2)

        row_w = QHBoxLayout()
        row_w.setSpacing(10)
        self.cbo_whisper_model = QComboBox()
        self.cbo_whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"])
        self.cbo_whisper_model.setCurrentText(self._selected_whisper)
        self.cbo_whisper_model.currentTextChanged.connect(self._on_whisper_model_selection_changed)
        row_w.addWidget(self.cbo_whisper_model, stretch=1)

        self.lbl_whisper_badge = QLabel("Chưa kiểm tra")
        self.lbl_whisper_badge.setStyleSheet(self._badge_style("gray"))
        row_w.addWidget(self.lbl_whisper_badge)

        self.btn_download_whisper = QPushButton("Tải Whisper")
        self.btn_download_whisper.clicked.connect(self._on_download_whisper_clicked)
        row_w.addWidget(self.btn_download_whisper)
        w_layout.addLayout(row_w)

        self.lbl_whisper_info = QLabel("Lưu tại cache chuẩn HuggingFace Hub (~/.cache/huggingface/hub)")
        self.lbl_whisper_info.setStyleSheet("font-size: 11px; color: #71717a;")
        w_layout.addWidget(self.lbl_whisper_info)

        self.prog_whisper = QProgressBar()
        self.prog_whisper.setFixedHeight(4)
        self.prog_whisper.hide()
        w_layout.addWidget(self.prog_whisper)
        c_layout.addWidget(card_whisper)

        # ----------------------------------------------------------------------
        # PHẦN 3: MÁY CHỦ AI THỊ GIÁC CỤC BỘ (OLLAMA DAEMON)
        # ----------------------------------------------------------------------
        card_ollama = QFrame()
        card_ollama.setProperty("class", "card_frame")
        o_layout = QVBoxLayout(card_ollama)
        o_layout.setContentsMargins(14, 12, 14, 14)
        o_layout.setSpacing(8)

        lbl_sec3 = QLabel("🦙 3. MÁY CHỦ AI CỤC BỘ (OLLAMA DAEMON - 127.0.0.1:11434)")
        lbl_sec3.setStyleSheet("font-size: 11px; font-weight: bold; color: #f59e0b; letter-spacing: 0.5px;")
        o_layout.addWidget(lbl_sec3)

        row_o = QHBoxLayout()
        row_o.setSpacing(10)
        self.lbl_ollama_status = QLabel("Trạng thái: Đang kiểm tra...")
        row_o.addWidget(self.lbl_ollama_status, stretch=1)

        self.btn_install_ollama = QPushButton("Cài đặt Ollama")
        self.btn_install_ollama.setProperty("class", "primary_btn")
        self.btn_install_ollama.clicked.connect(self._on_install_ollama_clicked)
        row_o.addWidget(self.btn_install_ollama)

        self.btn_toggle_daemon = QPushButton("Khởi động")
        self.btn_toggle_daemon.clicked.connect(self._on_toggle_daemon_clicked)
        row_o.addWidget(self.btn_toggle_daemon)
        o_layout.addLayout(row_o)

        self.prog_ollama = QProgressBar()
        self.prog_ollama.setFixedHeight(4)
        self.prog_ollama.hide()
        o_layout.addWidget(self.prog_ollama)

        self.lbl_ollama_details = QLabel("")
        self.lbl_ollama_details.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        o_layout.addWidget(self.lbl_ollama_details)
        c_layout.addWidget(card_ollama)

        # ----------------------------------------------------------------------
        # PHẦN 4: MÔ HÌNH THỊ GIÁC (VISION MODEL)
        # ----------------------------------------------------------------------
        card_vision = QFrame()
        card_vision.setProperty("class", "card_frame")
        v_layout = QVBoxLayout(card_vision)
        v_layout.setContentsMargins(14, 12, 14, 14)
        v_layout.setSpacing(8)

        lbl_sec4 = QLabel("👁️ 4. MÔ HÌNH THỊ GIÁC AI (VISION MODEL)")
        lbl_sec4.setStyleSheet("font-size: 11px; font-weight: bold; color: #f59e0b; letter-spacing: 0.5px;")
        v_layout.addWidget(lbl_sec4)

        row_v = QHBoxLayout()
        row_v.setSpacing(10)
        self.cbo_vision_model = QComboBox()
        self.cbo_vision_model.setEditable(True)
        self.cbo_vision_model.addItems([DEFAULT_VISION_MODEL, "qwen2.5-vl:7b", "llama3.2-vision:11b", "minicpm-v"])
        self.cbo_vision_model.setCurrentText(self._selected_vision)
        self.cbo_vision_model.currentTextChanged.connect(self._on_vision_model_selection_changed)
        row_v.addWidget(self.cbo_vision_model, stretch=1)

        self.lbl_vision_badge = QLabel("Chưa kiểm tra")
        self.lbl_vision_badge.setStyleSheet(self._badge_style("gray"))
        row_v.addWidget(self.lbl_vision_badge)

        self.btn_pull_vision = QPushButton("Tải Model")
        self.btn_pull_vision.clicked.connect(self._on_pull_vision_clicked)
        row_v.addWidget(self.btn_pull_vision)

        self.btn_verify_vision = QPushButton("Xác Thực")
        self.btn_verify_vision.setToolTip("Gửi yêu cầu smoke-test để đảm bảo mô hình phản hồi chính xác")
        self.btn_verify_vision.clicked.connect(self._on_verify_vision_clicked)
        row_v.addWidget(self.btn_verify_vision)
        v_layout.addLayout(row_v)

        self.prog_vision = QProgressBar()
        self.prog_vision.setFixedHeight(4)
        self.prog_vision.hide()
        v_layout.addWidget(self.prog_vision)

        self.lbl_vision_details = QLabel(
            f"Khuyên dùng: <b>{DEFAULT_VISION_MODEL}</b> (~1.9 GB) - tối ưu nhận diện cảnh & hành động video."
        )
        self.lbl_vision_details.setStyleSheet("font-size: 11px; color: #a1a1aa;")
        v_layout.addWidget(self.lbl_vision_details)
        c_layout.addWidget(card_vision)

        c_layout.addStretch(1)
        scroll.setWidget(container)
        main_layout.addWidget(scroll, stretch=1)

        # ----------------------------------------------------------------------
        # FOOTER: THÔNG BÁO & NÚT ĐÓNG
        # ----------------------------------------------------------------------
        self.lbl_footer_status = QLabel("")
        self.lbl_footer_status.setWordWrap(True)
        self.lbl_footer_status.setStyleSheet("font-size: 11.5px; color: #fbbf24;")
        main_layout.addWidget(self.lbl_footer_status)

        row_footer = QHBoxLayout()
        row_footer.addStretch(1)

        self.btn_cancel_task = QPushButton("Hủy thao tác")
        self.btn_cancel_task.clicked.connect(self._on_cancel_current_task)
        self.btn_cancel_task.hide()
        row_footer.addWidget(self.btn_cancel_task)

        self.btn_close = QPushButton("Đóng")
        self.btn_close.setFixedWidth(100)
        self.btn_close.clicked.connect(self.close)
        row_footer.addWidget(self.btn_close)
        main_layout.addLayout(row_footer)

    def _badge_style(self, kind: str) -> str:
        if kind == "green":
            return "background: rgba(34, 197, 94, 0.18); color: #4ade80; border: 1px solid rgba(34, 197, 94, 0.35); border-radius: 4px; padding: 2px 7px; font-weight: 600; font-size: 11px;"
        elif kind == "yellow":
            return "background: rgba(245, 158, 11, 0.18); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 4px; padding: 2px 7px; font-weight: 600; font-size: 11px;"
        elif kind == "red":
            return "background: rgba(239, 68, 68, 0.18); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 4px; padding: 2px 7px; font-weight: 600; font-size: 11px;"
        return "background: #23252a; color: #a1a1aa; border: 1px solid #353942; border-radius: 4px; padding: 2px 7px; font-size: 11px;"

    def _refresh_all_statuses(self, probe_network: bool = True):
        """Cập nhật toàn bộ trạng thái không làm giật luồng UI."""
        # 1. Ổ đĩa
        ok, free_gb, msg = self.service.check_storage_space()
        color = "#4ade80" if ok else "#f87171"
        self.lbl_disk_free.setText(f"<span style='color:{color}; font-weight:600;'>{msg}</span>")

        # 2. Whisper
        w_status, w_msg = self.service.check_whisper_status(self._selected_whisper)
        if w_status == WhisperModelStatus.DOWNLOADED:
            self.lbl_whisper_badge.setText("✓ Sẵn sàng")
            self.lbl_whisper_badge.setStyleSheet(self._badge_style("green"))
            self.btn_download_whisper.setEnabled(False)
            self.btn_download_whisper.setText("Đã tải")
        else:
            size_str = MODEL_DISK_SIZES.get(self._selected_whisper, "~1.5 GB")
            self.lbl_whisper_badge.setText(f"⚠️ Chưa tải ({size_str})")
            self.lbl_whisper_badge.setStyleSheet(self._badge_style("yellow"))
            self.btn_download_whisper.setEnabled(True)
            self.btn_download_whisper.setText("Tải Whisper")

        # 3. Ollama Daemon
        o_status = self.service.check_ollama_status(
            probe_network=probe_network,
            check_system=(self.service.os_name == "win32")
        )
        self._daemon_action = "stop" if o_status.is_running else "start"
        if o_status.install_status == OllamaInstallStatus.NOT_INSTALLED:
            self.lbl_ollama_status.setText("Trạng thái: <b>Chưa cài đặt</b>")
            self.btn_install_ollama.show()
            self.btn_install_ollama.setEnabled(True)
            self.btn_toggle_daemon.hide()
            self.lbl_ollama_details.setText("Nhấn 'Cài đặt Ollama' để tự động thiết lập.")
        elif o_status.install_status == OllamaInstallStatus.UNSUPPORTED:
            self.lbl_ollama_status.setText("Trạng thái: <b>Chưa hỗ trợ cài tự động</b>")
            self.btn_install_ollama.hide()
            self.btn_toggle_daemon.hide()
            self.lbl_ollama_details.setText(o_status.details)
        elif o_status.is_running:
            self.lbl_ollama_status.setText("Trạng thái: <b style='color:#4ade80;'>Đang chạy</b> (127.0.0.1:11434)")
            self.btn_install_ollama.hide()
            self.btn_toggle_daemon.show()
            self.btn_toggle_daemon.setText("Dừng" if o_status.is_owned else "Đang chạy")
            self.btn_toggle_daemon.setEnabled(o_status.is_owned)
            self.lbl_ollama_details.setText(o_status.details)
        else:
            state = "Đang dừng" if probe_network else "Chưa kiểm tra"
            self.lbl_ollama_status.setText(f"Trạng thái: <b style='color:#fbbf24;'>{state}</b>")
            self.btn_install_ollama.hide()
            self.btn_toggle_daemon.show()
            self.btn_toggle_daemon.setText("Khởi động")
            self.btn_toggle_daemon.setEnabled(True)
            self.lbl_ollama_details.setText(
                o_status.details if probe_network else "Bấm Khởi động để kiểm tra và bật Ollama."
            )

        # 4. Vision Model
        if probe_network and o_status.is_running:
            v_status = self.service.check_vision_model_status(self._selected_vision)
        else:
            verified_digest = str(self.service.settings.value(f"ai/verified_models/{self._selected_vision}", "") or "")
            if verified_digest and o_status.is_running:
                v_status = VisionModelStatusResult(
                    model=self._selected_vision,
                    status=VisionModelStatus.READY,
                    size_mb=0.0,
                    digest=verified_digest,
                    is_verified=True,
                    details="Đã xác thực và sẵn sàng sử dụng",
                )
            else:
                v_status = VisionModelStatusResult(
                    model=self._selected_vision,
                    status=VisionModelStatus.NOT_DOWNLOADED,
                    size_mb=0.0,
                    digest="",
                    is_verified=False,
                    details="Chưa tải về máy.",
                )

        if v_status.status == VisionModelStatus.READY:
            self.lbl_vision_badge.setText("✓ Sẵn sàng")
            self.lbl_vision_badge.setStyleSheet(self._badge_style("green"))
            self.btn_pull_vision.setEnabled(False)
            self.btn_pull_vision.setText("Đã tải")
            self.btn_verify_vision.setEnabled(True)
        elif v_status.status == VisionModelStatus.DOWNLOADED:
            self.lbl_vision_badge.setText("📦 Đã tải (Chưa xác thực)")
            self.lbl_vision_badge.setStyleSheet(self._badge_style("yellow"))
            self.btn_pull_vision.setEnabled(False)
            self.btn_pull_vision.setText("Đã tải")
            self.btn_verify_vision.setEnabled(True)
        else:
            self.lbl_vision_badge.setText("⚠️ Chưa tải")
            self.lbl_vision_badge.setStyleSheet(self._badge_style("red"))
            self.btn_pull_vision.setEnabled(o_status.is_running)
            self.btn_pull_vision.setText("Tải Model")
            self.btn_verify_vision.setEnabled(False)

        # Cập nhật danh sách model đã cài đặt nếu daemon đang chạy và có probe_network
        if probe_network and o_status.is_running:
            installed_models = self.service.list_installed_models()
            current_items = [self.cbo_vision_model.itemText(i) for i in range(self.cbo_vision_model.count())]
            for im in installed_models:
                m_name = im.get("name", "")
                if m_name and m_name not in current_items:
                    self.cbo_vision_model.addItem(m_name)

    def _on_browse_storage(self):
        if self.is_busy():
            return
        curr = self.service.get_storage_root()
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu trữ AI", curr)
        if path and path != curr:
            self.service.set_storage_root(path)
            self.txt_storage_path.setText(path)
            self.storage_root_changed.emit(path)
            self._refresh_all_statuses()

    def _on_whisper_model_selection_changed(self, model: str):
        if self.is_busy():
            return
        self._selected_whisper = model
        self.whisper_model_changed.emit(model)
        self._refresh_all_statuses()

    def _on_vision_model_selection_changed(self, model: str):
        if self.is_busy():
            return
        self._selected_vision = model
        self.vision_model_changed.emit(model)
        self._refresh_all_statuses()

    def _set_working_state(self, working: bool, label_msg: str = ""):
        """Khóa các điều khiển khi có tác vụ tải ngầm đang hoạt động để chống duplicate/overlapping jobs."""
        self.btn_browse_storage.setEnabled(not working)
        self.cbo_whisper_model.setEnabled(not working)
        self.cbo_vision_model.setEnabled(not working)
        self.btn_install_ollama.setEnabled(not working)
        self.btn_toggle_daemon.setEnabled(not working)
        self.btn_download_whisper.setEnabled(not working)
        self.btn_pull_vision.setEnabled(not working)
        self.btn_verify_vision.setEnabled(not working)
        self.btn_cancel_task.setVisible(working)
        if label_msg:
            self.lbl_footer_status.setText(label_msg)

    def _on_cancel_current_task(self):
        """Hủy hợp tác (cooperative cancel) trên tất cả các tác vụ worker đang chạy."""
        self.lbl_footer_status.setText("⏳ Đang gửi yêu cầu hủy...")
        for w in list(self._active_workers):
            if hasattr(w, "cancel"):
                try:
                    w.cancel()
                except Exception as exc:
                    logger.warning("Lỗi hủy worker: %s", exc)
        if self._whisper_in_progress and self._whisper_cancel_delegate:
            try:
                self._whisper_cancel_delegate()
            except Exception as exc:
                logger.warning("Lỗi hủy whisper prep: %s", exc)

    def _on_download_whisper_clicked(self):
        if self.is_busy():
            return
        m = self._selected_whisper
        size_str = MODEL_DISK_SIZES.get(m, "~1.5 GB")
        res = QMessageBox.question(
            self,
            "Xác nhận tải mô hình Whisper",
            f"Bạn có chắc muốn tải mô hình Whisper '{m}' ({size_str})?\n"
            f"Quá trình tải chạy trong nền và hoàn toàn ngoại tuyến sau khi hoàn tất.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        self.prog_whisper.setRange(0, 0)
        self.prog_whisper.show()
        self._set_working_state(True, f"Đang chuẩn bị mô hình Whisper '{m}'...")
        self.lbl_whisper_badge.setText("⏳ Đang tải trong nền...")

        def _on_whisper_prog(model_name: str, pct: float, msg: str):
            from shiboken6 import isValid
            if not isValid(self):
                return
            if pct >= 0:
                self.prog_whisper.setRange(0, 100)
                self.prog_whisper.setValue(int(pct))
            else:
                self.prog_whisper.setRange(0, 0)
            self.lbl_footer_status.setText(f"Whisper {model_name}: {msg}")

        def _on_whisper_fin(model_name: str, ok: bool, msg: str):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.prog_whisper.hide()
            self._whisper_in_progress = False
            self._set_working_state(False)
            self._refresh_all_statuses()
            if ok:
                self.lbl_footer_status.setText(f"✔ Đã tải xong Whisper '{model_name}'.")
            elif msg == "cancelled":
                self.lbl_footer_status.setText(f"ℹ️ Đã hủy tải Whisper '{model_name}'.")
            else:
                self.lbl_footer_status.setText(f"❌ Lỗi tải Whisper: {msg}")
            self._check_pending_close()

        if self._whisper_prep_delegate:
            self._whisper_in_progress = True
            started = False
            try:
                import inspect
                sig = inspect.signature(self._whisper_prep_delegate)
                if "on_progress" in sig.parameters:
                    started = self._whisper_prep_delegate(
                        m,
                        on_progress=_on_whisper_prog,
                        on_finished=_on_whisper_fin,
                    )
                else:
                    started = self._whisper_prep_delegate(m)
            except Exception as exc:
                logger.warning("Lỗi gọi whisper_prep_delegate: %s", exc)
                try:
                    started = self._whisper_prep_delegate(m)
                except Exception:
                    started = False

            if started:
                self.lbl_whisper_badge.setText("⏳ Đang tải trong nền...")
                self.btn_download_whisper.setEnabled(False)
            else:
                self.prog_whisper.hide()
                self._whisper_in_progress = False
                self._set_working_state(False)
                self._refresh_all_statuses()
                self.lbl_footer_status.setText(f"❌ Không thể khởi chạy tiến trình tải Whisper '{m}'.")
            return

        # Fallback worker nếu không có delegate từ SourcePage
        from src.ui.director.pages.source_page import WhisperModelDownloadWorker
        worker = WhisperModelDownloadWorker(m, parent=self)
        self._active_worker = worker
        self._whisper_in_progress = True

        def _fallback_fin(model_name, ok, msg):
            _on_whisper_fin(model_name, ok, msg)

        worker.progress_signal.connect(_on_whisper_prog, Qt.ConnectionType.QueuedConnection)
        worker.finished_signal.connect(_fallback_fin, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _on_install_ollama_clicked(self):
        if self.is_busy():
            return

        if self.service.os_name == "win32":
            confirm_msg = (
                f"Trên hệ điều hành Windows, ứng dụng sẽ tải bộ cài đặt chính thức của Ollama (OllamaSetup.exe) "
                f"từ máy chủ chính thức của nhà phát hành và khởi chạy trình cài đặt để bạn hoàn tất.\n\n"
                f"Thư mục tải về: {os.path.join(self.service.get_storage_root(), 'downloads')}\n\n"
                f"Bạn có đồng ý tải và mở trình cài đặt không?"
            )
        else:
            confirm_msg = (
                f"Ứng dụng sẽ tải gói tệp thực thi chính thức của Ollama ({self.service.os_name}) "
                f"vào thư mục riêng:\n{self.service.get_storage_root()}\n\n"
                f"Quá trình này không yêu cầu quyền quản trị viên (sudo) và không can thiệp hệ thống.\n"
                f"Bạn có muốn tiếp tục không?"
            )

        res = QMessageBox.question(
            self,
            "Cài đặt Ollama Cục Bộ",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        worker = OllamaInstallWorker(self.service, parent=self)
        self._active_worker = worker
        self.prog_ollama.setRange(0, 100)
        self.prog_ollama.setValue(0)
        self.prog_ollama.show()
        self._set_working_state(True, "Đang tải và chuẩn bị Ollama...")

        def _on_prog(pct, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            if pct >= 0:
                self.prog_ollama.setValue(int(pct))
            self.lbl_footer_status.setText(msg)

        def _on_fin(ok, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.prog_ollama.hide()
            self._set_working_state(False)
            self._refresh_all_statuses()
            if ok:
                if self.service.os_name == "win32":
                    self.lbl_footer_status.setText(
                        "ℹ️ Đã mở bộ cài đặt OllamaSetup.exe. "
                        "Vui lòng hoàn tất cài đặt trên Windows rồi bấm 'Khởi động'."
                    )
                else:
                    self.lbl_footer_status.setText("✔ Cài đặt Ollama thành công. Bạn có thể bấm 'Khởi động'.")
            else:
                self.lbl_footer_status.setText(f"❌ Cài đặt Ollama thất bại: {msg}")

        worker.progress_signal.connect(_on_prog, Qt.ConnectionType.QueuedConnection)
        worker.finished_signal.connect(_on_fin, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _on_toggle_daemon_clicked(self):
        if self.is_busy():
            return
        action = self._daemon_action
        status = self.service.check_ollama_status(probe_network=True)
        if action == "start" and status.is_running:
            self._refresh_all_statuses()
            self.lbl_footer_status.setText("Máy chủ Ollama đang hoạt động.")
            return
        if action == "stop" and (not status.is_running or not status.is_owned):
            self._refresh_all_statuses()
            self.lbl_footer_status.setText(status.details)
            return
        action_name = "dừng" if action == "stop" else "khởi động"

        worker = OllamaDaemonWorker(self.service, action=action, parent=self)
        self._active_worker = worker
        self.prog_ollama.setRange(0, 0)
        self.prog_ollama.show()
        self._set_working_state(True, f"Đang {action_name} máy chủ Ollama...")

        def _on_fin(ok, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.prog_ollama.hide()
            self._set_working_state(False)
            self._refresh_all_statuses()
            self.lbl_footer_status.setText(msg)

        worker.finished_signal.connect(_on_fin, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _on_pull_vision_clicked(self):
        if self.is_busy():
            return
        m = self._selected_vision
        res = QMessageBox.question(
            self,
            "Tải Mô Hình Thị Giác",
            f"Bạn có muốn tải mô hình thị giác '{m}' qua Ollama không?\n"
            f"Dung lượng dự kiến: ~1.9 GB.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if res != QMessageBox.StandardButton.Yes:
            return

        worker = VisionModelPullWorker(self.service, m, parent=self)
        self._active_worker = worker
        self.prog_vision.setRange(0, 0)
        self.prog_vision.show()
        self._set_working_state(True, f"Đang kết nối tải mô hình '{m}'...")

        def _on_prog(pct, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            if pct >= 0:
                self.prog_vision.setRange(0, 100)
                self.prog_vision.setValue(int(pct))
            else:
                self.prog_vision.setRange(0, 0)
            self.lbl_footer_status.setText(msg)

        def _on_fin(ok, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.prog_vision.hide()
            self._set_working_state(False)
            self._refresh_all_statuses()
            if ok:
                self.lbl_footer_status.setText(f"✔ Đã tải xong '{m}'. Hãy bấm 'Xác Thực' để kiểm tra độ sẵn sàng.")
            else:
                self.lbl_footer_status.setText(f"❌ Tải mô hình thất bại: {msg}")

        worker.progress_signal.connect(_on_prog, Qt.ConnectionType.QueuedConnection)
        worker.finished_signal.connect(_on_fin, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def _on_verify_vision_clicked(self):
        if self.is_busy():
            return
        m = self._selected_vision
        worker = VisionModelVerifyWorker(self.service, m, parent=self)
        self._active_worker = worker
        self.prog_vision.setRange(0, 0)
        self.prog_vision.show()
        self.lbl_vision_badge.setText("⏳ Đang xác thực...")
        self.lbl_vision_badge.setStyleSheet(self._badge_style("yellow"))
        self._set_working_state(True, f"Đang gửi yêu cầu kiểm tra xác thực mô hình '{m}'...")

        def _on_fin(ok, msg):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.prog_vision.hide()
            self._set_working_state(False)
            self._refresh_all_statuses()
            if ok:
                self.lbl_footer_status.setText(f"✔ Xác thực thành công: {msg}")
            else:
                self.lbl_footer_status.setText(f"❌ Xác thực thất bại: {msg}")

        worker.finished_signal.connect(_on_fin, Qt.ConnectionType.QueuedConnection)
        worker.start()

    def closeEvent(self, event):
        """Bảo vệ tiến trình nền khi người dùng cố gắng đóng cửa sổ, hủy hợp tác và hoãn đóng an toàn."""
        if self.is_busy():
            reply = QMessageBox.question(
                self,
                "Tác vụ đang chạy",
                "Đang có tác vụ tải hoặc cài đặt AI chạy ngầm. Bạn có chắc muốn hủy và đóng không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._close_pending = True
                self.lbl_footer_status.setText("⏳ Đang hủy tác vụ và đóng an toàn...")
                self._set_working_state(True)
                for w in list(self._active_workers):
                    if hasattr(w, "cancel"):
                        try:
                            w.cancel()
                        except Exception as exc:
                            logger.warning("Lỗi hủy worker: %s", exc)
                if self._whisper_in_progress and self._whisper_cancel_delegate:
                    try:
                        self._whisper_cancel_delegate()
                    except Exception as exc:
                        logger.warning("Lỗi hủy whisper prep: %s", exc)
                # Bỏ qua event để không cho QDialog hủy đối tượng khi worker thread còn chạy
                event.ignore()
                self._check_pending_close()
            else:
                event.ignore()
        else:
            event.accept()

    def _check_pending_close(self):
        """Đóng dialog an toàn khi các worker nền đã dừng hoàn toàn."""
        if getattr(self, "_close_pending", False):
            if not self.is_busy():
                self._close_pending = False
                QTimer.singleShot(0, self.accept)
