"""
src/ui/director/director_window.py
Cửa sổ chính của ChunDVC Director (Wizard 4 bước gom AutoCut + Copilot).
Kích thước chuẩn: 1100×720px (tối thiểu 1024×640px).
Bao gồm:
- Thanh tiêu đề (Tên app, Clip/Project hiện tại, Chip Whisper, Nút đóng ✕).
- Stepper 4 bước ngang (Nguồn → Kiểu dựng → Duyệt & Kịch bản → Dựng).
- Vùng nội dung QStackedWidget 4 trang.
- Bảng nâng cao 280px (AdvancedSidePanel) trượt từ cạnh phải.
- Cột tiến độ 260px (ProgressSidePanel) trượt từ cạnh phải khi dựng timeline.
- Chân trang với DUY NHẤT một nút chính tím ("Tiếp: ..." / "Dựng timeline") và nút phụ.
"""

import os
import uuid
import logging
from typing import Optional, Dict, Any, List, Tuple, Callable, Mapping
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame, QMessageBox, QApplication
)
from PySide6.QtCore import Qt, Signal, QSize, Slot, QTimer
from src.ui.director.director_state import DirectorConfig
from src.ui.director.director_controller import DirectorController
from src.ui.director.widgets.stepper import DirectorStepperWidget
from src.ui.director.widgets.advanced_panel import AdvancedSidePanel
from src.ui.director.widgets.progress_panel import ProgressSidePanel
from src.ui.director.pages.source_page import SourcePage
from src.ui.director.pages.style_page import StylePage
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.pages.build_page import BuildPage
from src.core.branding import DIRECTOR_WINDOW_TITLE
from src.core.source_catalog_readiness import ReadinessRequest, normalize_readiness_result
from src.ui.director.workers.preflight_worker import PreflightWorker

logger = logging.getLogger(__name__)


class DirectorWindow(QMainWindow):
    """Cửa sổ đạo diễn ChunDVC Director."""
    closed = Signal()
    analysis_and_preflight_idle = Signal()
    settings_requested = Signal()
    open_studio_requested = Signal()
    open_dock_requested = Signal()

    def __init__(self, controller: Optional[DirectorController] = None, parent=None):
        super().__init__(parent)
        self.controller = controller or DirectorController()
        self.config = self.controller.config
        self.current_step = 1
        self._source_close_pending = False
        self._close_pending = False
        self._close_queued = False
        self._is_closed = False
        self._shutdown_requested = False
        self._app_quit_requested = False
        self._preflight_workers: Dict[str, PreflightWorker] = {}
        self._preflight_generation: int = 0
        self._current_preflight_request_id: Optional[str] = None
        self._pending_preflight_expected: Optional[Tuple[str, int, str, str, str]] = None
        self._pending_preflight_worker: Optional[Any] = None
        self._preflight_terminal_consumed: bool = False
        self._last_readiness_result: Optional[Any] = None
        self._last_target_authority: Optional[Any] = None
        self._last_completed_source_job_id: Optional[str] = None
        self._pending_review_refresh: bool = False
        self._pending_review_snapshot: Optional[Any] = None
        self._pending_review_runtime_results: Optional[dict] = None
        self._pending_run_ai_request_id: Optional[str] = None
        self._dispatched_run_ai_request_ids: set = set()

        self.setWindowTitle(DIRECTOR_WINDOW_TITLE)
        from src.ui.icons import get_app_icon
        self.setWindowIcon(get_app_icon())
        self.setMinimumSize(QSize(1024, 640))
        self.resize(1100, 720)

        # Style nền chung chuẩn Dark Theme Studio NLE (DaVinci Resolve 19 Graphite)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #16171a;
            }
            QWidget {
                color: #e6e8ec;
                font-family: 'Geist', 'Inter', 'Outfit', 'Segoe UI Variable Text', 'Segoe UI', 'Ubuntu', system-ui, sans-serif;
                selection-background-color: #d94f3d;
                selection-color: #ffffff;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #353841;
                min-height: 24px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #e05a47;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal {
                background: #353841;
                min-width: 24px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #e05a47;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QToolTip {
                background-color: #23252a;
                color: #e6e8ec;
                border: 1px solid #434752;
                border-radius: 4px;
                padding: 5px 9px;
                font-size: 11.5px;
            }
        """)

        self.setAcceptDrops(True)
        self._init_ui()
        self._wire_signals()
        # Đồng bộ cấu hình Whisper ban đầu từ page_source (nguồn chân lý QSettings) sang self.config và Topbar Chip
        if hasattr(self, "page_source"):
            init_model = self.page_source.get_whisper_model()
            init_lang = self.page_source.get_whisper_language()
            self.config.whisper_model = init_model
            self.config.whisper_language = init_lang
            self.btn_whisper.setText(f"Whisper {init_model} · {init_lang}")
        self.page_source.workers_idle.connect(self._on_sources_idle)
        if hasattr(self.page_review, "analysis_workers_idle"):
            self.page_review.analysis_workers_idle.connect(self._on_sources_idle)
        self.go_to_step(1)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            # Admission guard: từ chối drop khi preflight đang chạy hoặc window đang đóng / shutdown
            if (
                bool(getattr(self, "_preflight_workers", None))
                or getattr(self, "_shutdown_requested", False)
                or getattr(self, "_close_pending", False)
                or getattr(self, "_is_closed", False)
            ):
                event.ignore()
                return

            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                if hasattr(self, "page_source"):
                    self.go_to_step(1)
                    self.page_source.add_paths(paths)
        else:
            super().dropEvent(event)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # -------------------------------------------------------------
        # 1. Thanh tiêu đề Studio Topbar (44px)
        # -------------------------------------------------------------
        self.header_frame = QFrame(central_widget)
        self.header_frame.setFixedHeight(44)
        self.header_frame.setStyleSheet("""
            QFrame {
                background-color: #18191c;
                border-bottom: 1px solid #2e3138;
            }
        """)
        h_layout = QHBoxLayout(self.header_frame)
        h_layout.setContentsMargins(16, 0, 12, 0)
        h_layout.setSpacing(10)

        lbl_logo = QLabel("<b>CHUNDVC DIRECTOR</b>")
        lbl_logo.setStyleSheet("font-size: 12.5px; color: #e6e8ec; letter-spacing: 0.8px; border: none; background: transparent;")
        h_layout.addWidget(lbl_logo)

        self.lbl_project = QLabel("ResolveFlow_Project")
        self.lbl_project.setStyleSheet("""
            color: #9da3ae;
            background-color: #23252a;
            border: 1px solid #2e3138;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
        """)
        h_layout.addWidget(self.lbl_project)

        h_layout.addStretch(1)

        # Chip Whisper
        self.btn_whisper = QPushButton(f"Whisper {self.config.whisper_model} · {self.config.whisper_language}")
        self.btn_whisper.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_whisper.clicked.connect(self._show_whisper_menu)
        self.btn_whisper.setStyleSheet("""
            QPushButton {
                background-color: #23252a;
                color: #f59e0b;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c2f36;
                border-color: #f59e0b;
                color: #fbbf24;
            }
        """)
        h_layout.addWidget(self.btn_whisper)

        # Cụm tiêu đề góc trên bên phải: Phiên bản chuẩn và Nút Cài đặt (⚙)
        from src.ui.widgets.app_header_actions import AppHeaderActions
        self.header_actions = AppHeaderActions(parent=self.header_frame, show_version=True)
        self.header_actions.studio_requested.connect(self.open_studio_requested.emit)
        self.header_actions.dock_requested.connect(self.open_dock_requested.emit)
        self.header_actions.director_requested.connect(self.raise_)
        self.header_actions.settings_requested.connect(self._on_settings_requested)
        self.header_actions.ai_setup_requested.connect(self._on_open_ai_setup)
        h_layout.addWidget(self.header_actions)

        # Nút đóng ✕
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #9da3ae;
                border: 1px solid transparent;
                border-radius: 4px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(239, 68, 68, 0.16);
                border-color: rgba(239, 68, 68, 0.4);
                color: #f87171;
            }
        """)
        btn_close.clicked.connect(self.close)
        h_layout.addWidget(btn_close)
        root_layout.addWidget(self.header_frame)

        # -------------------------------------------------------------
        # 2. Stepper 4 bước
        # -------------------------------------------------------------
        self.stepper = DirectorStepperWidget(central_widget)
        self.stepper.step_selected.connect(self.go_to_step)
        root_layout.addWidget(self.stepper)

        # Divider
        divider1 = QFrame()
        divider1.setFixedHeight(1)
        divider1.setStyleSheet("background-color: #2e3138;")
        root_layout.addWidget(divider1)

        # -------------------------------------------------------------
        # 3. Thân chính: Stacked Pages + Side Panels
        # -------------------------------------------------------------
        body_widget = QWidget(central_widget)
        body_layout = QHBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Stacked pages
        self.stack = QStackedWidget(body_widget)
        self.page_source = SourcePage(self.stack)
        self.page_style = StylePage(self.stack)
        self.page_review = ReviewPage(self.stack)
        self.page_build = BuildPage(self.stack)

        self.stack.addWidget(self.page_source)   # Index 0 (Step 1)
        self.stack.addWidget(self.page_style)    # Index 1 (Step 2)
        self.stack.addWidget(self.page_review)   # Index 2 (Step 3)
        self.stack.addWidget(self.page_build)    # Index 3 (Step 4)

        body_layout.addWidget(self.stack, stretch=1)

        # Bảng trượt nâng cao 280px (mặc định ẩn)
        self.adv_panel = AdvancedSidePanel(body_widget)
        self.adv_panel.hide()
        body_layout.addWidget(self.adv_panel)

        # Cột tiến độ 260px (mặc định ẩn)
        self.prog_panel = ProgressSidePanel(body_widget)
        self.prog_panel.hide()
        body_layout.addWidget(self.prog_panel)

        root_layout.addWidget(body_widget, stretch=1)

        # Divider
        divider2 = QFrame()
        divider2.setFixedHeight(1)
        divider2.setStyleSheet("background-color: #2e3138;")
        root_layout.addWidget(divider2)

        # -------------------------------------------------------------
        # 4. Chân trang Studio Transport Bar (Footer - 54px)
        # -------------------------------------------------------------
        self.footer_frame = QFrame(central_widget)
        self.footer_frame.setFixedHeight(54)
        self.footer_frame.setStyleSheet("""
            QFrame {
                background-color: #18191c;
            }
        """)
        f_layout = QHBoxLayout(self.footer_frame)
        f_layout.setContentsMargins(18, 8, 18, 8)
        f_layout.setSpacing(12)

        # Nút phụ bên trái
        self.btn_back = QPushButton("‹ Quay lại")
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setStyleSheet("""
            QPushButton {
                background-color: #25272d;
                color: #e6e8ec;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2e3139;
                border-color: #4b505c;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1a1c20;
            }
            QPushButton:disabled {
                background-color: #191b1f;
                color: #5f6470;
                border-color: #25272c;
            }
        """)
        self.btn_back.clicked.connect(self._on_btn_back_clicked)
        f_layout.addWidget(self.btn_back)

        self.btn_save_recipe = QPushButton("Lưu Recipe")
        self.btn_save_recipe.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_recipe.setStyleSheet("""
            QPushButton {
                background-color: #202227;
                color: #9da3ae;
                border: 1px solid #2e3138;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                color: #e6e8ec;
                background-color: #292b32;
                border-color: #434752;
            }
        """)
        self.btn_save_recipe.clicked.connect(self._on_save_recipe)
        f_layout.addWidget(self.btn_save_recipe)

        f_layout.addStretch(1)

        # Nhãn phản hồi cổng kiểm duyệt (không modal, hiển thị trực quan)
        self.lbl_gate_status = QLabel("")
        self.lbl_gate_status.setObjectName("lbl_gate_status")
        self.lbl_gate_status.setStyleSheet("""
            color: #fbbf24;
            background-color: rgba(245, 158, 11, 0.10);
            border: 1px solid rgba(245, 158, 11, 0.28);
            border-radius: 4px;
            padding: 4px 10px;
            font-size: 11.5px;
            font-weight: 600;
        """)
        self.lbl_gate_status.setWordWrap(True)
        self.lbl_gate_status.hide()
        f_layout.addWidget(self.lbl_gate_status)

        # DUY NHẤT một nút chính (DaVinci Resolve Studio Coral) bên phải
        self.btn_primary = QPushButton("Tiếp: Kiểu dựng ›")
        self.btn_primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_primary.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                border: 1px solid #e86352;
                border-radius: 4px;
                padding: 7px 22px;
                font-size: 12.5px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #e86352;
                border-color: #f07b6b;
            }
            QPushButton:pressed {
                background-color: #b83f2f;
            }
            QPushButton:disabled {
                background-color: #23252a;
                border: 1px solid #2e3138;
                color: #686e7a;
            }
        """)
        self.btn_primary.clicked.connect(self._on_btn_primary_clicked)
        f_layout.addWidget(self.btn_primary)

        root_layout.addWidget(self.footer_frame)

    def _wire_signals(self):
        # Đồng bộ từ source_page
        self.page_source.clips_changed.connect(self._on_clips_changed)
        self.page_source.whisper_changed.connect(self._on_whisper_changed)
        self.page_source.resolve_import_requested.connect(self._on_import_from_resolve)

        # Style page
        self.page_style.advanced_toggle_requested.connect(self.toggle_advanced_panel)
        self.page_style.style_changed.connect(self._on_style_changed)
        if hasattr(self.page_style, "config_changed"):
            self.page_style.config_changed.connect(self._on_style_config_changed)
        if hasattr(self.page_style, "draft_changed"):
            self.page_style.draft_changed.connect(self._on_style_config_changed)

        # Advanced panel
        self.adv_panel.close_requested.connect(self.adv_panel.hide)

        # Review page
        self.page_review.set_context_supplier(self._get_current_director_context)
        self.page_review.ai_run_requested.connect(self._on_run_ai_script)
        self.page_review.prompt_ready_for_run.connect(self._on_prompt_ready_for_run)

        # Rough scan từ SourcePage & ReviewPage -> delegate sang worker source-only
        self.page_source.rough_scan_requested.connect(self._on_start_rough_scan)
        self.page_review.rough_scan_requested.connect(self._on_start_rough_scan)

        # Giám sát trạng thái phân tích nguồn (SourceAnalysisWorker)
        self.page_source.source_analysis_busy_changed.connect(self._on_source_analysis_busy_changed)
        self.page_source.source_analysis_completed.connect(self._on_source_analysis_completed)

        # Build page
        self.page_build.bgm_browse_requested.connect(self._on_browse_bgm)
        if hasattr(self.page_build, "readiness_cancel_requested"):
            self.page_build.readiness_cancel_requested.connect(self._on_readiness_cancel_requested)

        # Controller signals
        self.controller.step_status_changed.connect(self.prog_panel.set_step_status)
        self.controller.progress_changed.connect(self.prog_panel.set_progress)
        self.controller.log_received.connect(self.prog_panel.append_log)
        self.controller.pipeline_finished.connect(self._on_pipeline_finished)
        self.controller.ai_script_finished.connect(self.page_review.set_ai_result)

        # Cancel từ progress panel (phân biệt source worker hay pipeline)
        self.prog_panel.cancel_requested.connect(self._on_cancel_requested)

    def _on_cancel_requested(self):
        """Hủy tác vụ: phân biệt tác vụ phân tích nguồn (SourcePage) hay pipeline (Controller)."""
        if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
            self.page_source.cancel_source_analysis()
        else:
            self.controller.cancel_pipeline()

    def _on_start_rough_scan(self):
        """Khởi chạy quét phân tích nguồn (SourceAnalysisWorker offline) từ SourcePage hoặc ReviewPage."""
        if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
            return

        self._sync_all_pages_to_config()

        # Nếu đang ở trang khác (ví dụ ReviewPage), chuyển về Bước 1 để người dùng quan sát tiến độ trực quan
        if self.current_step != 1:
            self.go_to_step(1)

        self.page_source.start_source_analysis()

    def _show_gate_feedback(self, reason: str):
        """Hiển thị thông báo phản hồi cổng kiểm soát ngắn gọn, không modal cho người dùng."""
        if hasattr(self, "lbl_gate_status"):
            self.lbl_gate_status.setText(reason)
            self.lbl_gate_status.show()
        if hasattr(self, "btn_primary"):
            self.btn_primary.setToolTip(reason)

    def _clear_gate_feedback(self):
        """Xóa phản hồi kiểm soát cũ sau khi chuyển bước hợp lệ hoặc cập nhật thành công."""
        if hasattr(self, "lbl_gate_status"):
            self.lbl_gate_status.setText("")
            self.lbl_gate_status.hide()
        if hasattr(self, "btn_primary"):
            self.btn_primary.setToolTip("")

    def go_to_step(self, step: int):
        """Chuyển đổi giao diện sang bước tương ứng (1..4)."""
        if (
            getattr(self, "_shutdown_requested", False)
            or getattr(self, "_close_pending", False)
            or getattr(self, "_is_closed", False)
        ):
            return
        if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
            return
        if getattr(self, "_preflight_workers", None):
            return

        target_step = max(1, min(4, step))
        if target_step == 4:
            if not self._sync_all_pages_to_config():
                logger.warning("Chuyển bước sang 4 bị chặn do đồng bộ cấu hình không hợp lệ.")
                return
            approved_payload = None
            if hasattr(self, "page_review") and hasattr(self.page_review, "get_approved_proposal_and_record"):
                try:
                    approved_payload = self.page_review.get_approved_proposal_and_record()
                except Exception as exc:
                    logger.warning("Lỗi ngoại lệ khi gọi get_approved_proposal_and_record: %s", exc)
                    approved_payload = None
            if not approved_payload:
                reason = "Chưa có phê duyệt biên tập hợp lệ cho kịch bản hiện tại."
                logger.warning("Chuyển sang Bước 4 bị chặn: %s", reason)
                self._last_step4_gate_reason = reason
                self._show_gate_feedback(reason)
                self.current_step = 3
                self.stepper.set_current_step(3)
                self.stack.setCurrentIndex(2)
                self.btn_back.setEnabled(True)
                self._update_primary_button_text()
                return

        # Chuyển bước hợp lệ: xóa phản hồi gate cũ
        self._clear_gate_feedback()
        self.current_step = target_step
        self.stepper.set_current_step(self.current_step)
        self.stack.setCurrentIndex(self.current_step - 1)

        # Cập nhật nút Quay lại
        self.btn_back.setEnabled(self.current_step > 1)

        # Cập nhật nhãn nút chính
        self._update_primary_button_text()

        if self.current_step == 3:
            # Tự động nạp siêu dữ liệu nguồn hai kênh (thoại + thị giác) vào ReviewPage nếu có
            current_paths = self.config.video_paths
            snap = getattr(self.page_source, "get_completed_analysis_snapshot", lambda: None)()
            runtime_results = dict(getattr(self.page_source, "_runtime_clip_status", {})) if hasattr(self.page_source, "_runtime_clip_status") else None
            if hasattr(self.page_review, "load_analysis_data"):
                self.page_review.load_analysis_data(
                    clip_infos=current_paths,
                    snapshot=snap,
                    runtime_results=runtime_results,
                    whisper_model=self.config.whisper_model,
                    whisper_lang=self.config.whisper_language,
                    vision_model=getattr(self.config, "vision_model", None),
                    vision_endpoint=getattr(self.page_source, "_runtime_vision_endpoint", None),
                )
            elif hasattr(self.page_review, "load_transcripts_from_cache"):
                self.page_review.load_transcripts_from_cache(
                    current_paths,
                    self.config.whisper_model,
                    self.config.whisper_language
                )
            if hasattr(self.page_review, "set_story_metadata"):
                self.page_review.set_story_metadata(
                    style_intent=self.config.footage_type,
                    hook_dur=f"{int(self.config.vlog_hook_dur)}s" if self.config.vlog_hook_dur else "30_60s"
                )
        elif self.current_step == 4:
            # Lưu state và refresh summary card
            self._sync_all_pages_to_config()
            self.page_build.update_summary(self.config)

    def _update_primary_button_text(self):
        """Cập nhật nhãn của nút chính dựa trên bước hiện tại."""
        if self.current_step == 1:
            self.btn_primary.setText("Tiếp: Kiểu dựng ›")
        elif self.current_step == 2:
            self.btn_primary.setText("Tiếp: Duyệt & kịch bản ›")
        elif self.current_step == 3:
            self.btn_primary.setText("Tiếp: Dựng timeline ›")
        else:
            self.btn_primary.setText("Kiểm tra nguồn & tài nguyên")

    def _on_style_config_changed(self):
        """Thông báo reentrancy-safe khi StylePage có thay đổi nháp/cấu hình sáng tạo."""
        if getattr(self, "_in_style_sync", False):
            return
        self._in_style_sync = True
        try:
            if hasattr(self, "page_review") and hasattr(self.page_review, "notify_creative_context_changed"):
                self.page_review.notify_creative_context_changed()
        finally:
            self._in_style_sync = False

    def _on_btn_back_clicked(self):
        if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
            return
        if getattr(self, "_preflight_workers", None):
            return
        if self.current_step > 1:
            self.go_to_step(self.current_step - 1)

    def _on_btn_primary_clicked(self):
        if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
            return
        if getattr(self, "_preflight_workers", None):
            return
        if self.current_step < 4:
            if not self._sync_all_pages_to_config():
                logger.warning("Chuyển bước bị chặn do cấu hình phong cách/bản thảo không hợp lệ.")
                return
            self.go_to_step(self.current_step + 1)
        else:
            # Bước 4: Chạy kiểm tra nguồn & tài nguyên
            self._start_readiness_preflight()

    def _start_build_timeline(self):
        """Giữ tên cho caller cũ; delegate sang kiểm tra tiền kiểm readonly."""
        self._start_readiness_preflight()

    def _start_readiness_preflight(self) -> None:
        """Khởi chạy tiến trình tiền kiểm nguồn và tài nguyên cho kịch bản Bước 4."""
        # 0. Chặn admission khi window đang đóng hoặc app shutdown đang xử lý
        if (
            getattr(self, "_shutdown_requested", False)
            or getattr(self, "_close_pending", False)
            or getattr(self, "_is_closed", False)
        ):
            logger.warning("Tiền kiểm bị từ chối do cửa sổ đang trong quá trình đóng/shutdown.")
            return

        # 1. Kiểm tra trạng thái bận tổng hợp (fail-safe)
        try:
            if not self.is_analysis_and_preflight_idle():
                logger.warning("Tiền kiểm bị chặn do có tác vụ đang bận.")
                return
            if self.controller.worker and self.controller.worker.isRunning():
                logger.warning("Tiền kiểm bị chặn do controller đang chạy pipeline.")
                return
        except Exception as exc:
            logger.warning("Lỗi khi kiểm tra busy state, fail-safe coi là bận: %s", exc)
            return

        # 2. Đồng bộ các cấu hình in-memory hiện hành
        if not self._sync_all_pages_to_config():
            logger.warning("Tiền kiểm bị chặn do đồng bộ cấu hình không hợp lệ.")
            return

        # 3. Truy vấn cặp proposal + approval hợp lệ từ ReviewPage
        approved_payload = None
        try:
            if hasattr(self, "page_review") and hasattr(self.page_review, "get_approved_proposal_and_record"):
                approved_payload = self.page_review.get_approved_proposal_and_record()
        except Exception as exc:
            logger.warning("Lỗi ngoại lệ khi gọi get_approved_proposal_and_record: %s", exc)
            approved_payload = None

        if not approved_payload:
            reason = "Chưa có phê duyệt biên tập hợp lệ cho kịch bản hiện tại."
            logger.warning("Tiền kiểm bị chặn: %s", reason)
            self._last_step4_gate_reason = reason
            self.go_to_step(3)
            self._show_gate_feedback(reason)
            self.page_build.clear_readiness_result(reason)
            return

        # 4. Xác định điều kiện tiền kiểm chuyên sâu (Deep Execution Readiness)
        # Bắt buộc proposal đã có bằng chứng byte/CFR gắn nguyên khối trên toàn bộ clip
        proposal_obj = approved_payload[0]
        approval_obj = approved_payload[1]

        is_deep_eligible = self._is_proposal_evidence_complete_and_bound(proposal_obj)
        verify_source_execution = False
        authoritative_timeline_fps = None
        self._last_target_authority = None

        if is_deep_eligible:
            # Truy vấn thẩm quyền timeline từ DaVinci Resolve qua ResolveBridge boundary
            target_auth = self._query_resolve_timeline_authority()
            self._last_target_authority = target_auth
            if target_auth.ok and target_auth.timeline_fps is not None:
                verify_source_execution = True
                authoritative_timeline_fps = target_auth.timeline_fps
            else:
                logger.info(
                    "Không thể opt-in deep execution preflight do không đọc được timeline authority: %s (%s)",
                    getattr(target_auth, "error_code", "UNKNOWN"),
                    getattr(target_auth, "error_message", ""),
                )
        else:
            logger.info("Proposal chưa có đầy đủ bằng chứng nguồn byte/CFR, chạy kiểm tra siêu dữ liệu.")

        # 5. Tạo ReadinessRequest và PreflightWorker candidate trước khi đổi generation/UI
        req_id = f"preflight_{uuid.uuid4().hex[:12]}"
        cand_gen = self._preflight_generation + 1
        try:
            request = ReadinessRequest(
                request_id=req_id,
                generation=cand_gen,
                proposal=proposal_obj,
                approval=approval_obj,
                verify_source_execution=verify_source_execution,
                timeline_fps=authoritative_timeline_fps,
            )
            worker = PreflightWorker(request=request, parent=self)
        except Exception as exc:
            err_msg = f"Không thể khởi tạo kiểm tra nguồn & tài nguyên: {exc}"
            logger.error(err_msg)
            self.page_build.clear_readiness_result(err_msg)
            return

        # 6. Đăng ký candidate và pending expected authority
        self._register_preflight_candidate(worker, request)

        # 7. Nối các tín hiệu của worker qua closure ghi nhớ exact worker instance
        worker.terminal.connect(
            lambda outcome, w=worker, r=request: self._on_readiness_terminal(w, r, outcome)
        )
        worker.progress.connect(
            lambda cur, msg, w=worker: self._on_readiness_progress(w, cur, msg)
        )
        worker.finished.connect(
            lambda w=worker, rid=req_id: self._on_readiness_worker_finished(w, rid)
        )

        # 7. Khóa giao diện sang trạng thái bận
        self.btn_primary.setEnabled(False)
        self.btn_primary.setText("⏳ Đang kiểm tra nguồn...")
        self.btn_back.setEnabled(False)
        self.stepper.setEnabled(False)
        self.page_build.set_readiness_busy(True)

        # 8. Khởi chạy worker
        try:
            worker.start()
        except Exception as exc:
            err_msg = f"Lỗi khi worker.start(): {exc}"
            logger.error(err_msg)
            self.page_build.clear_readiness_result(err_msg)
            is_running = False
            try:
                is_running = worker.isRunning()
            except Exception:
                is_running = True  # treat query error as running
            if is_running:
                try:
                    worker.cancel()
                except Exception:
                    pass
            else:
                self._preflight_workers.pop(req_id, None)
                if self._current_preflight_request_id == req_id:
                    self._current_preflight_request_id = None
                self._pending_preflight_expected = None
                self._pending_preflight_worker = None
                self._preflight_terminal_consumed = False
                worker.deleteLater()
                self.page_build.set_readiness_busy(False)
                self._reconcile_controls_state()

    def _register_preflight_candidate(self, worker: PreflightWorker, request: ReadinessRequest) -> None:
        """Đăng ký candidate preflight worker cùng bản ghi pending authority đầy đủ."""
        req_id = request.request_id
        gen = request.generation
        prop_id = request.proposal.proposal_id
        rev = request.approval.proposal_revision
        appr_id = request.approval.approval_id
        self._pending_preflight_expected = (req_id, gen, prop_id, rev, appr_id)
        self._pending_preflight_worker = worker
        self._preflight_workers[req_id] = worker
        self._current_preflight_request_id = req_id
        self._preflight_generation = gen
        self._preflight_terminal_consumed = False

    def _on_readiness_terminal(self, worker: PreflightWorker, request: ReadinessRequest, outcome: Any) -> None:
        """Xử lý kết quả logical terminal từ PreflightWorker."""
        # 1. Kiểm tra pending expected record và exact worker instance
        if self._pending_preflight_expected is None or self._pending_preflight_worker is not worker:
            logger.warning("Bỏ qua terminal từ worker không khớp pending authority.")
            return

        exp_req_id, exp_gen, exp_prop_id, exp_rev, exp_appr_id = self._pending_preflight_expected

        if self._preflight_workers.get(exp_req_id) is not worker:
            logger.warning("Bỏ qua terminal từ worker không có trong registry.")
            return

        # 2. Duplicate terminal check: giữ kết quả đầu tiên, không ghi đè
        if getattr(self, "_preflight_terminal_consumed", False):
            logger.warning("Bỏ qua terminal trùng lặp cho job %s.", exp_req_id)
            return

        # 3. Chuẩn hóa kết quả
        try:
            norm_result = normalize_readiness_result(outcome)
        except Exception as exc:
            logger.error("Lỗi khi normalize kết quả readiness: %s", exc)
            self._preflight_terminal_consumed = True
            self._last_readiness_result = None
            self._last_build_result = {
                "status": "failed",
                "checks_passed": False,
                "ready_to_execute": False,
                "message": f"Lỗi chuẩn hóa kết quả: {exc}",
            }
            self.page_build.clear_readiness_result(f"Lỗi chuẩn hóa kết quả: {exc}")
            return

        # 4. Đối chiếu nghiêm ngặt cả 5 outcome fields với pending expected record
        outcome_req_id = getattr(norm_result, "request_id", None)
        outcome_gen = getattr(norm_result, "generation", None)
        outcome_prop_id = getattr(norm_result, "proposal_id", None)
        outcome_rev = getattr(norm_result, "proposal_revision", None)
        outcome_appr_id = getattr(norm_result, "approval_id", None)

        if (
            outcome_req_id != exp_req_id
            or outcome_gen != exp_gen
            or outcome_prop_id != exp_prop_id
            or outcome_rev != exp_rev
            or outcome_appr_id != exp_appr_id
        ):
            logger.warning(
                "Bỏ qua outcome do sai lệch 5 fields: outcome=(%s, %s, %s, %s, %s) != expected=(%s, %s, %s, %s, %s)",
                outcome_req_id, outcome_gen, outcome_prop_id, outcome_rev, outcome_appr_id,
                exp_req_id, exp_gen, exp_prop_id, exp_rev, exp_appr_id,
            )
            # Foreign outcome: không consume terminal, không publish
            return

        # Đánh dấu đã consume terminal cho job hợp lệ này
        self._preflight_terminal_consumed = True

        # 5. Truy vấn lại approved getter hiện hành để kiểm tra tính hợp lệ
        current_approved_payload = None
        try:
            if hasattr(self, "page_review") and hasattr(self.page_review, "get_approved_proposal_and_record"):
                current_approved_payload = self.page_review.get_approved_proposal_and_record()
        except Exception as exc:
            logger.warning("Lỗi khi kiểm tra phê duyệt hiện hành tại terminal: %s", exc)
            current_approved_payload = None

        is_stale = False
        if not current_approved_payload:
            is_stale = True
        else:
            curr_prop, curr_appr = current_approved_payload
            if (
                curr_prop.proposal_id != exp_prop_id
                or curr_appr.approval_id != exp_appr_id
                or curr_appr.proposal_revision != exp_rev
            ):
                is_stale = True

        if is_stale:
            stale_msg = "Bản phê duyệt hoặc kịch bản đã thay đổi trong quá trình kiểm tra. Kết quả đã hết hiệu lực."
            logger.warning(stale_msg)
            self._last_readiness_result = None
            self._last_build_result = {
                "status": "STALE",
                "message": stale_msg,
                "proposal_id": exp_prop_id,
                "approval_id": exp_appr_id,
            }
            self.page_build.clear_readiness_result(stale_msg)
            return

        # 6. Chuẩn hóa và hiển thị kết quả hợp lệ lên BuildPage
        self._last_readiness_result = norm_result
        self._last_build_result = {
            "status": norm_result.status,
            "checks_passed": norm_result.checks_passed,
            "ready_to_execute": norm_result.ready_to_execute,
            "proposal_id": norm_result.proposal_id,
            "approval_id": norm_result.approval_id,
        }
        self.page_build.show_readiness_result(
            norm_result, target_authority=getattr(self, "_last_target_authority", None)
        )

    def _on_readiness_worker_finished(self, worker: PreflightWorker, request_id: str) -> None:
        """Xử lý sự kiện kết thúc vật lý (native finished) của PreflightWorker."""
        # 1. Thu hồi đúng worker khỏi registry
        if self._preflight_workers.get(request_id) is not worker:
            return
        self._preflight_workers.pop(request_id, None)
        if self._current_preflight_request_id == request_id:
            self._current_preflight_request_id = None

        # 2. Nếu worker kết thúc mà chưa từng có terminal outcome hợp lệ -> báo lỗi trung thực
        if self._pending_preflight_worker is worker:
            if not self._preflight_terminal_consumed:
                fail_msg = "Tiến trình kiểm tra dừng mà không có kết quả hợp lệ."
                self._last_readiness_result = None
                self._last_build_result = {
                    "status": "failed",
                    "checks_passed": False,
                    "ready_to_execute": False,
                    "message": fail_msg,
                }
                self.page_build.clear_readiness_result(fail_msg)
            self._pending_preflight_expected = None
            self._pending_preflight_worker = None
            self._preflight_terminal_consumed = False

        # 3. Xóa đối tượng Qt an toàn
        worker.deleteLater()

        # 4. Xóa trạng thái busy trên BuildPage
        self.page_build.set_readiness_busy(False)

        # 5. Phục hồi điều khiển giao diện
        self._reconcile_controls_state()

        # 6. Phát tín hiệu rảnh tổng hợp nếu toàn bộ 3 families đã dừng
        self._on_aggregate_workers_idle()

    def _on_readiness_cancel_requested(self) -> None:
        """Hủy các worker preflight đang chạy."""
        for worker in list(self._preflight_workers.values()):
            try:
                worker.cancel()
            except Exception as exc:
                logger.warning("Lỗi khi hủy preflight worker: %s", exc)

    def _on_readiness_progress(self, worker: PreflightWorker, current: int, message: str) -> None:
        if self._pending_preflight_worker is not worker:
            return
        if getattr(self, "_preflight_terminal_consumed", False):
            return
        if hasattr(self, "btn_primary") and self.current_step == 4:
            self.btn_primary.setText(f"⏳ Đang kiểm tra ({current}%)...")

    @staticmethod
    def _is_proposal_evidence_complete_and_bound(proposal: Any) -> bool:
        """Kiểm tra xem kịch bản đã có đầy đủ bằng chứng kỹ thuật (SHA-256 toàn phần và stream_index) trên mọi clip hay chưa."""
        if not hasattr(proposal, "source_binding"):
            return False
        sb = getattr(proposal, "source_binding", None)
        if not isinstance(sb, (Mapping, dict)):
            return False
        clips = sb.get("clips", ())
        if not isinstance(clips, (list, tuple)) or len(clips) == 0:
            return False
        for c in clips:
            if not isinstance(c, (Mapping, dict)):
                return False
            sha = c.get("expected_full_sha256")
            s_idx = c.get("stream_index")
            if not sha or type(sha) is not str or len(sha) != 64:
                return False
            if s_idx is None or type(s_idx) is not int or isinstance(s_idx, bool) or s_idx < 0:
                return False
        return True

    def _query_resolve_timeline_authority(self) -> Any:
        """Truy vấn thẩm quyền đọc từ Resolve timeline qua ResolveBridge (hoặc test mock)."""
        try:
            from src.core.resolve_bridge import query_active_timeline_authority
            resolve_app = getattr(self, "_resolve_app_mock", None)
            return query_active_timeline_authority(resolve_app=resolve_app)
        except Exception as exc:
            logger.warning("Ngoại lệ khi truy vấn thẩm quyền Resolve timeline: %s", exc)
            from src.core.resolve_bridge import ResolveTimelineAuthority
            return ResolveTimelineAuthority(
                ok=False,
                error_code="ERR_RESOLVE_QUERY_EXCEPTION",
                error_message=f"Ngoại lệ khi truy vấn DaVinci Resolve: {exc}",
            )

    def _reconcile_controls_state(self) -> None:
        """Đồng bộ trạng thái các nút bấm và stepper theo trạng thái bận tổng hợp."""
        is_preflight_busy = bool(self._preflight_workers)
        is_source_busy = False
        try:
            if hasattr(self, "page_source"):
                is_source_busy = bool(getattr(self.page_source, "_workers_registry", None)) or (
                    hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active()
                )
        except Exception:
            is_source_busy = True

        is_review_busy = False
        try:
            if hasattr(self, "page_review") and hasattr(self.page_review, "is_analysis_idle"):
                is_review_busy = not self.page_review.is_analysis_idle()
        except Exception:
            is_review_busy = True

        if is_preflight_busy:
            self.stepper.setEnabled(False)
            self.btn_back.setEnabled(False)
            self.btn_primary.setEnabled(False)
            self.btn_primary.setText("⏳ Đang kiểm tra nguồn...")
        elif is_source_busy:
            self.stepper.setEnabled(False)
            self.btn_back.setEnabled(False)
            self.btn_primary.setEnabled(False)
            self.btn_primary.setText("⏳ Đang phân tích nguồn...")
        elif is_review_busy:
            self.stepper.setEnabled(False)
            self.btn_back.setEnabled(False)
            self.btn_primary.setEnabled(False)
        else:
            self.stepper.setEnabled(True)
            self.btn_back.setEnabled(self.current_step > 1)
            self.btn_primary.setEnabled(True)
            self._update_primary_button_text()

    def _on_pipeline_finished(self, success: bool, message: str):
        if not bool(getattr(self, "_preflight_workers", None)):
            self.btn_primary.setEnabled(True)
            self._update_primary_button_text()

        if success:
            QMessageBox.information(self, "Thành công", f"Dựng timeline hoàn tất:\n{message}")
        else:
            QMessageBox.warning(self, "Lỗi tiến trình", f"Tiến trình gặp lỗi:\n{message}")

    @Slot(bool)
    def _on_source_analysis_busy_changed(self, busy: bool):
        """Khóa hoặc phục hồi điều khiển khi tác vụ phân tích nguồn chạy hoặc dừng hẳn."""
        if busy:
            self.btn_primary.setEnabled(False)
            self.btn_primary.setText("⏳ Đang phân tích nguồn...")
            self.btn_back.setEnabled(False)
            self.stepper.setEnabled(False)
            if hasattr(self.page_review, "btn_review_scan"):
                self.page_review.btn_review_scan.setEnabled(False)
                self.page_review.btn_review_scan.setText("⏳ Đang phân tích...")
        else:
            # Nếu preflight đang chạy, không unlock controls hoặc đè nhãn
            if getattr(self, "_preflight_workers", None):
                return
            self.stepper.setEnabled(True)
            self.btn_back.setEnabled(self.current_step > 1)
            self.btn_primary.setEnabled(True)
            self._update_primary_button_text()
            if hasattr(self.page_review, "btn_review_scan"):
                self.page_review.btn_review_scan.setEnabled(True)
                self.page_review.btn_review_scan.setText("⚡ Quét thô Whisper")

            self._trigger_review_refresh()

            self._trigger_review_refresh()

    def _trigger_review_refresh(self):
        if not getattr(self, "_pending_review_refresh", False) or getattr(self, "_is_closed", False):
            return
        self._pending_review_refresh = False
        current_paths = self.page_source.get_video_paths()
        snap = getattr(self, "_pending_review_snapshot", None)
        runtime_results = getattr(self, "_pending_review_runtime_results", None)

        if hasattr(self.page_review, "load_analysis_data"):
            try:
                self.page_review.load_analysis_data(
                    clip_infos=current_paths,
                    snapshot=snap,
                    runtime_results=runtime_results,
                    whisper_model=self.config.whisper_model,
                    whisper_lang=self.config.whisper_language,
                    vision_model=getattr(self.config, "vision_model", None),
                    vision_endpoint=getattr(self.page_source, "_runtime_vision_endpoint", None),
                )
            except Exception as exc:
                logger.warning("Không thể nạp dữ liệu phân tích vào ReviewPage: %s", exc)
        elif hasattr(self.page_review, "load_transcripts_from_cache"):
            try:
                self.page_review.load_transcripts_from_cache(
                    current_paths,
                    self.config.whisper_model,
                    self.config.whisper_language
                )
            except Exception as exc:
                logger.warning("Không thể nạp transcript vào ReviewPage: %s", exc)

    @Slot(str, bool, str, dict)
    def _on_source_analysis_completed(self, job_id: str, success: bool, message: str, counters: dict):
        """Tổng kết trung thực tác vụ phân tích nguồn (SourceAnalysisWorker)."""
        if getattr(self, "_is_closed", False) or getattr(self, "_source_close_pending", False):
            return

        # Bỏ qua nếu là sự kiện terminal trùng lặp cho cùng một job_id
        if getattr(self, "_last_completed_source_job_id", None) == job_id:
            return
        self._last_completed_source_job_id = job_id

        # Chỉ refresh ReviewPage khi snapshot cấu hình của phiên hoàn tất match 100% cấu hình và paths hiện tại
        current_paths = self.page_source.get_video_paths()
        snap = getattr(self.page_source, "get_completed_analysis_snapshot", lambda: None)()
        can_refresh_review = False
        if snap is not None:
            if hasattr(snap, "vision_endpoint") and hasattr(snap, "window_sec"):
                # Structured snapshot: xác thực đầy đủ 100% các trường cấu hình
                snap_paths = list(snap.video_paths)
                paths_match = (current_paths == snap_paths and current_paths == self.config.video_paths)
                audio_match = (
                    self.config.whisper_model == snap.whisper_model
                    and self.config.whisper_language == snap.whisper_language
                )

                chk = getattr(self.page_source, "chk_enable_visual_describe", None)
                cur_v_enable = chk.isChecked() if chk is not None else False
                cbo = getattr(self.page_source, "cbo_vision_model", None)
                cur_v_model = cbo.currentText().strip() if cbo is not None else ""

                cur_endpoint = getattr(self.page_source, "_runtime_vision_endpoint", snap.vision_endpoint)
                cur_w_sec = getattr(self.page_source, "_runtime_window_sec", snap.window_sec)
                cur_frames = getattr(self.page_source, "_runtime_frames_per_window", snap.frames_per_window)
                cur_max_w = getattr(self.page_source, "_runtime_max_windows", snap.max_windows)
                cur_max_ref = getattr(self.page_source, "_runtime_max_refinements", snap.max_refinements)

                v_enable_matches = (cur_v_enable == bool(snap.enable_visual_describe))
                if snap.enable_visual_describe:
                    v_model_matches = (cur_v_model == snap.vision_model)
                    v_endpoint_matches = (cur_endpoint == snap.vision_endpoint)
                    v_budget_matches = (
                        cur_w_sec == snap.window_sec
                        and cur_frames == snap.frames_per_window
                        and cur_max_w == snap.max_windows
                        and cur_max_ref == snap.max_refinements
                    )
                else:
                    v_model_matches = True
                    v_endpoint_matches = True
                    v_budget_matches = True

                cfg_v_enable = getattr(self.config, "enable_visual_describe", None)
                if cfg_v_enable is not None:
                    v_enable_matches = v_enable_matches and (bool(cfg_v_enable) == bool(snap.enable_visual_describe))
                cfg_v_model = getattr(self.config, "vision_model", None)
                if cfg_v_model is not None and snap.enable_visual_describe:
                    v_model_matches = v_model_matches and ((cfg_v_model or "") == (snap.vision_model or ""))

                can_refresh_review = (
                    paths_match
                    and audio_match
                    and v_enable_matches
                    and v_model_matches
                    and v_endpoint_matches
                    and v_budget_matches
                )
            elif isinstance(snap, tuple):
                # Legacy adapter cho 3-tuple và 5-tuple
                if len(snap) == 3:
                    snap_model, snap_lang, snap_paths = snap
                    vision_match = True
                elif len(snap) == 5:
                    snap_model, snap_lang, snap_v_enable, snap_v_model, snap_paths = snap
                    chk = getattr(self.page_source, "chk_enable_visual_describe", None)
                    cur_v_enable = chk.isChecked() if chk is not None else False
                    cbo = getattr(self.page_source, "cbo_vision_model", None)
                    cur_v_model = cbo.currentText().strip() if cbo is not None else ""

                    v_enable_matches = (cur_v_enable == bool(snap_v_enable))
                    v_model_matches = (cur_v_model == (snap_v_model or "")) if snap_v_enable else True

                    cfg_v_enable = getattr(self.config, "enable_visual_describe", None)
                    if cfg_v_enable is not None:
                        v_enable_matches = v_enable_matches and (bool(cfg_v_enable) == bool(snap_v_enable))
                    cfg_v_model = getattr(self.config, "vision_model", None)
                    if cfg_v_model is not None and snap_v_enable:
                        v_model_matches = v_model_matches and ((cfg_v_model or "") == (snap_v_model or ""))

                    vision_match = v_enable_matches and v_model_matches
                else:
                    snap_model, snap_lang = snap[0], snap[1]
                    snap_paths = snap[-1]
                    vision_match = True

                can_refresh_review = (
                    current_paths == list(snap_paths)
                    and self.config.whisper_model == snap_model
                    and self.config.whisper_language == snap_lang
                    and current_paths == self.config.video_paths
                    and vision_match
                )

        if can_refresh_review:
            self._pending_review_refresh = True
            self._pending_review_snapshot = snap
            self._pending_review_runtime_results = dict(getattr(self.page_source, "_runtime_clip_status", {}))
        else:
            self._pending_review_refresh = False
            self._pending_review_snapshot = None
            self._pending_review_runtime_results = None
            logger.info("Bỏ qua nạp ReviewPage vì cấu hình hoặc danh sách footage không khớp phiên phân tích.")

        # LƯU Ý: Không enable btn_review_scan ở đây! Nút này được phục hồi tại _on_source_analysis_busy_changed(False) khi thread đã dừng hẳn.

        # Đọc schema nested counters từ worker
        total = counters.get("total_clips", 0)
        audio = counters.get("audio", {})
        visual = counters.get("visual", {})
        vdesc = counters.get("visual_describe", {})

        audio_ok = audio.get("cached", 0) + audio.get("new", 0)
        visual_ok = visual.get("cached", 0) + visual.get("new", 0)
        vdesc_ok = vdesc.get("cached", 0) + vdesc.get("new", 0)
        vdesc_partial = vdesc.get("partial", 0)
        vdesc_unavailable = vdesc.get("unavailable", 0)
        vdesc_failed = vdesc.get("failed", 0)
        vdesc_skipped = vdesc.get("skipped", 0)
        vdesc_cancelled = vdesc.get("cancelled", 0)

        audio_cancelled = audio.get("cancelled", 0)
        visual_cancelled = visual.get("cancelled", 0)
        is_cancelled = (message == "cancelled") or (audio_cancelled > 0) or (visual_cancelled > 0) or (vdesc_cancelled > 0)

        if is_cancelled or not success:
            if is_cancelled:
                title = "Đã dừng phân tích nguồn"
                if vdesc_skipped == total or not vdesc:
                    vdesc_str = "Chưa chạy mô tả AI (tùy chọn tắt)."
                else:
                    cancel_parts = []
                    if vdesc_ok > 0:
                        cancel_parts.append(f"{vdesc_ok} hoàn tất")
                    if vdesc_partial > 0:
                        cancel_parts.append(f"{vdesc_partial} một phần")
                    if vdesc_cancelled > 0:
                        cancel_parts.append(f"{vdesc_cancelled} đã hủy")
                    vdesc_str = ", ".join(cancel_parts) if cancel_parts else f"{vdesc_cancelled} đã hủy"
                body = (
                    f"Quá trình phân tích nguồn đã dừng lại theo yêu cầu.\n"
                    f"• Tổng số clip: {total}\n"
                    f"• Thoại: {audio_ok} hoàn tất ({audio.get('cached', 0)} từ cache, {audio.get('new', 0)} mới), {audio.get('no_audio_track', 0)} không có tiếng, {audio_cancelled} đã hủy.\n"
                    f"• Khung hình: {visual_ok} hoàn tất ({visual.get('cached', 0)} từ cache, {visual.get('new', 0)} mới), {visual_cancelled} đã hủy.\n"
                    f"• Mô tả AI: {vdesc_str}\n"
                    f"Các kết quả đã hoàn thành trước khi hủy vẫn được lưu giữ an toàn."
                )
                QMessageBox.information(self, title, body)
            else:
                title = "Phân tích nguồn có lỗi"
                audio_err = audio.get("failed", 0) + audio.get("unavailable", 0)
                visual_err = visual.get("failed", 0)
                if vdesc_skipped == total or not vdesc:
                    vdesc_str = "Chưa chạy mô tả AI (tùy chọn tắt)."
                else:
                    unavail_details = []
                    if vdesc.get("disconnected", 0) > 0:
                        unavail_details.append(f"{vdesc.get('disconnected')} chưa kết nối Ollama")
                    if vdesc.get("model_absent", 0) > 0:
                        unavail_details.append(f"{vdesc.get('model_absent')} chưa cài model Ollama")
                    unavail_str = f" ({', '.join(unavail_details)})" if unavail_details else ""

                    vdesc_str = (
                        f"{vdesc_ok}/{total} thành công"
                        f"{f', {vdesc_partial} một phần/giới hạn ngân sách' if vdesc_partial else ''}"
                        f"{f', {vdesc_unavailable} thiếu mô hình{unavail_str}' if vdesc_unavailable else ''}"
                        f"{f', {vdesc_failed} lỗi' if vdesc_failed else ''}."
                    )
                body = (
                    f"Quá trình phân tích nguồn kết thúc với thông báo:\n{message}\n\n"
                    f"• Thoại: {audio_ok}/{total} thành công ({audio.get('unavailable', 0)} thiếu mô hình, {audio.get('failed', 0)} lỗi).\n"
                    f"• Khung hình: {visual_ok}/{total} thành công ({visual_err} lỗi).\n"
                    f"• Mô tả AI: {vdesc_str}\n"
                    f"(Dữ liệu hoàn thành ở các chặng hợp lệ đã được lưu giữ)."
                )
                QMessageBox.warning(self, title, body)
        else:
            title = "Phân tích nguồn hoàn tất"
            if vdesc_skipped == total or not vdesc:
                vdesc_line = "• Mô tả AI: Chưa chạy mô tả AI (tùy chọn tắt)."
                vdesc_note = "\n(Lưu ý: Mô tả chi tiết đồ vật/hành động chưa chạy và sẽ được đối chiếu tại Bước 3)."
            else:
                details = []
                if vdesc.get('cached', 0) > 0:
                    details.append(f"{vdesc.get('cached', 0)} từ cache")
                if vdesc.get('new', 0) > 0:
                    details.append(f"{vdesc.get('new', 0)} vừa phân tích")
                if vdesc_partial > 0:
                    details.append(f"{vdesc_partial} một phần/giới hạn ngân sách")
                if vdesc_unavailable > 0:
                    details.append(f"{vdesc_unavailable} thiếu mô hình")
                if vdesc_failed > 0:
                    details.append(f"{vdesc_failed} lỗi")
                detail_str = f" ({', '.join(details)})" if details else ""
                vdesc_line = f"• Mô tả AI: {vdesc_ok}/{total} clip hoàn tất{detail_str}."
                vdesc_note = "\n(Lưu ý: Mô tả hình ảnh đã lưu trong chỉ mục, chưa tự động áp dụng vào dựng phim)."

            body = (
                f"✓ Đã phân tích nguồn xong {total} clip footage!\n"
                f"• Thoại: {audio_ok}/{total} clip sẵn sàng ({audio.get('cached', 0)} từ cache, {audio.get('new', 0)} vừa bóc tách).\n"
                f"• Khung hình: {visual_ok}/{total} clip đã lấy mẫu contact sheet ({visual.get('cached', 0)} từ cache, {visual.get('new', 0)} vừa lấy mẫu).\n"
                f"{vdesc_line}"
                f"{vdesc_note}"
            )
            QMessageBox.information(self, title, body)

    def toggle_advanced_panel(self):
        """Bật/tắt bảng nâng cao 280px."""
        if self.adv_panel.isVisible():
            self.adv_panel.hide()
        else:
            self.adv_panel.load_from_config(self.config)
            self.prog_panel.hide()
            self.adv_panel.show()

    def _on_clips_changed(self, paths):
        self.config.video_paths = paths
        if paths:
            fname = paths[0].split("/")[-1]
            self.lbl_project.setText(fname)

    def _on_import_from_resolve(self):
        """Tự động nạp danh sách video từ Media Pool hoặc Timeline đang mở trong DaVinci Resolve."""
        from src.core.resolve_api import ResolveAutomation
        ra = ResolveAutomation()
        if not ra.connect():
            QMessageBox.warning(
                self,
                "Không thể kết nối DaVinci Resolve",
                f"Không thể kết nối tới DaVinci Resolve.\n{ra.last_connect_error or 'Vui lòng đảm bảo DaVinci Resolve đang chạy và đã mở một Project.'}"
            )
            return

        # 1. Thử phát hiện video từ Timeline hoặc selection hiện tại
        video_paths = ra.auto_detect_video_paths()

        # 2. Nếu Timeline trống, lấy toàn bộ các file media thực tế trong Media Pool
        if not video_paths:
            pool_paths = ra.get_media_pool_file_paths()
            video_paths = [p for p in pool_paths if os.path.exists(p) and os.path.isfile(p)]

        if video_paths:
            self.page_source.add_paths(video_paths)
            proj_name = ra.current_project.GetName() if ra.current_project else "DaVinci Resolve"
            QMessageBox.information(
                self,
                "Đã nạp từ DaVinci Resolve",
                f"✓ Đã phát hiện và nạp thành công {len(video_paths)} video từ dự án '{proj_name}' vào Bước 1!"
            )
        else:
            QMessageBox.information(
                self,
                "DaVinci Resolve",
                "Đã kết nối DaVinci Resolve thành công nhưng không tìm thấy tệp video nào trong Media Pool hoặc Timeline hiện tại."
            )

    def _show_whisper_menu(self):
        """Mở menu cấu hình Whisper từ topbar chip."""
        if hasattr(self, "page_source"):
            self.page_source._show_whisper_menu(parent_btn=self.btn_whisper)

    def _on_settings_requested(self):
        """Xử lý yêu cầu mở Cài đặt từ thanh tiêu đề."""
        self.settings_requested.emit()

    def _on_open_ai_setup(self):
        """Mở hộp thoại Thiết lập AI từ thanh tiêu đề."""
        if hasattr(self, "page_source"):
            self.page_source.open_ai_setup_dialog()

    def _on_whisper_changed(self, model, lang):
        self.config.whisper_model = model
        self.config.whisper_language = lang
        self.btn_whisper.setText(f"Whisper {model} · {lang}")
        if getattr(self, "_preflight_workers", None):
            self._on_readiness_cancel_requested()
            self._last_readiness_result = None
            self.page_build.clear_readiness_result("Cấu hình Whisper đã thay đổi trong khi đang kiểm tra.")

    def _on_style_changed(self, style_id):
        self.config.apply_style_preset(style_id)
        self.page_style.load_from_config(self.config)
        self.adv_panel.load_from_config(self.config)
        if getattr(self, "_preflight_workers", None):
            self._on_readiness_cancel_requested()
            self._last_readiness_result = None
            self.page_build.clear_readiness_result("Cấu hình phong cách đã thay đổi trong khi đang kiểm tra.")

    def _get_current_director_context(self) -> Dict[str, Any]:
        """Thu thập ngữ cảnh cấu hình hiện tại từ các trang con phục vụ chuẩn bị Prompt."""
        video_paths = []
        whisper_model = "small"
        whisper_lang = "vi"
        enable_vision = False
        vision_model = ""
        vision_endpoint = "http://127.0.0.1:11434"
        window_sec = 8.0
        frames_per_window = 2
        max_windows = 10
        max_refinements = 1

        if hasattr(self, "page_source"):
            try:
                if hasattr(self.page_source, "get_video_paths"):
                    video_paths = self.page_source.get_video_paths()
                elif hasattr(self.page_source, "_clips"):
                    video_paths = [getattr(c, "path", c) for c in self.page_source._clips]

                if hasattr(self.page_source, "get_whisper_model"):
                    whisper_model = self.page_source.get_whisper_model()
                elif hasattr(self.page_source, "cbo_model"):
                    whisper_model = self.page_source.cbo_model.currentText()

                if hasattr(self.page_source, "get_whisper_language"):
                    whisper_lang = self.page_source.get_whisper_language()
                elif hasattr(self.page_source, "cbo_lang"):
                    whisper_lang = self.page_source.cbo_lang.currentText()

                if hasattr(self.page_source, "chk_enable_visual_describe"):
                    enable_vision = self.page_source.chk_enable_visual_describe.isChecked()
                elif hasattr(self.page_source, "is_visual_describe_enabled"):
                    enable_vision = self.page_source.is_visual_describe_enabled()

                if hasattr(self.page_source, "cbo_vision_model"):
                    vision_model = self.page_source.cbo_vision_model.currentText().strip()
                elif hasattr(self.page_source, "get_vision_model"):
                    vision_model = self.page_source.get_vision_model()

                # Đọc đúng runtime attributes từ page_source thay vì phantom _runtime_endpoint hay hardcoded
                vision_endpoint = getattr(self.page_source, "_runtime_vision_endpoint", getattr(self.page_source, "_runtime_endpoint", "http://127.0.0.1:11434"))
                if hasattr(self.page_source, "_runtime_window_sec"):
                    window_sec = float(self.page_source._runtime_window_sec)
                if hasattr(self.page_source, "_runtime_frames_per_window"):
                    frames_per_window = int(self.page_source._runtime_frames_per_window)
                if hasattr(self.page_source, "_runtime_max_windows"):
                    max_windows = int(self.page_source._runtime_max_windows)
                if hasattr(self.page_source, "_runtime_max_refinements"):
                    max_refinements = int(self.page_source._runtime_max_refinements)
            except Exception as e:
                logger.error("Lỗi khi đọc cấu hình từ page_source: %s", e)
                return {}  # Fail-closed: context unavailable khi đọc page_source phát sinh exception

        # 1. Thẩm định và commit StylePage draft hiện tại an toàn vào config (zero fallback to old saved context)
        if hasattr(self, "page_style") and hasattr(self.page_style, "save_to_config"):
            try:
                ok = self.page_style.save_to_config(self.config)
                if not ok:
                    logger.warning("StylePage draft không hợp lệ khi lấy director context, fail-closed.")
                    return {}
            except Exception as e:
                logger.error("Lỗi khi gọi page_style.save_to_config: %s", e)
                return {}

        creative_ctx = None
        if hasattr(self, "config") and hasattr(self.config, "get_creative_context"):
            try:
                creative_ctx = self.config.get_creative_context()
            except Exception as e:
                logger.error("Lỗi khi đọc get_creative_context từ config: %s", e)
                return {}

        style_preset = getattr(self.config, "style_preset", "travel_vlog")
        hook_dur = getattr(self.page_review, "_hook_duration", "30_60s")
        target_dur = getattr(self.page_review, "_target_duration", "full")
        proj_name = getattr(self.config, "project_name", "ChunDVC_Project") or "ChunDVC_Project"
        max_bytes = getattr(self.config, "max_prompt_bytes", 32768) or 32768

        manifest_id = ""
        if hasattr(self.page_review, "_manifest") and self.page_review._manifest:
            manifest_id = getattr(self.page_review._manifest, "manifest_id", "")

        raw_ctx = {
            "video_paths": list(video_paths),
            "whisper_model": whisper_model or getattr(self.config, "whisper_model", "small"),
            "whisper_language": whisper_lang or getattr(self.config, "whisper_language", "vi"),
            "enable_visual_describe": enable_vision,
            "vision_model": vision_model,
            "vision_endpoint": vision_endpoint,
            "window_sec": window_sec,
            "frames_per_window": frames_per_window,
            "max_windows": max_windows,
            "max_refinements": max_refinements,
            "style_intent": style_preset,
            "hook_duration": hook_dur,
            "target_duration": target_dur,
            "project_name": proj_name,
            "max_prompt_bytes": max_bytes,
            "manifest_id": manifest_id,
        }
        if creative_ctx is not None:
            raw_ctx["creative_context"] = creative_ctx

        from src.ui.director.pages.review_page import ReviewPage
        validated = ReviewPage._sanitize_and_validate_context(raw_ctx)
        if validated is None:
            logger.warning("Ngữ cảnh Director không hợp lệ (malformed value hoặc danh sách tệp rỗng), fail-closed.")
            return {}
        return validated

    def _on_run_ai_script(self):
        if not self._sync_all_pages_to_config():
            logger.warning("Không thể khởi động Run AI: lưu cấu hình thất bại (StylePage draft không hợp lệ).")
            self.page_review.set_ai_result(False, "Kiểm tra lại cấu hình ở Bước 2 · Kiểu dựng.")
            return
        ctx = self._get_current_director_context()
        if not ctx:
            logger.warning("Không thể khởi động Run AI: context rỗng hoặc không hợp lệ.")
            self.page_review.set_ai_result(False, "Kiểm tra video nguồn ở Bước 1 rồi thử lại.")
            return
        cache_mgr = getattr(self, "_cache_mgr", None) or getattr(self.page_review, "_cache_mgr", None)
        vis_index = getattr(self, "_visual_index", None) or getattr(self.page_review, "_visual_index", None)
        manifest_dir = getattr(self, "_manifest_cache_dir", None) or getattr(self.page_review, "_manifest_cache_dir", None)
        req_id = self.page_review.request_prompt_preparation(
            intent="run_ai",
            context=ctx,
            cache_mgr=cache_mgr,
            visual_index=vis_index,
            manifest_cache_dir=manifest_dir,
        )
        if req_id is None:
            logger.warning("Không thể khởi động chuẩn bị Prompt cho Run AI (danh sách tệp rỗng hoặc context không hợp lệ).")
            self.page_review.set_ai_result(False, self.page_review.lbl_json_hint.text())
        else:
            self._pending_run_ai_request_id = req_id
            self.page_review.track_ai_prompt_request(req_id)

    @Slot(str, str)
    def _on_prompt_ready_for_run(self, req_id: str, prompt_text: str):
        """Slot nhận prompt đã chuẩn bị sẵn từ ReviewPage và phát lệnh đơn (single dispatch) tới controller."""
        if getattr(self, "_is_closed", False) or getattr(self, "_source_close_pending", False):
            return
        if not prompt_text or not prompt_text.strip():
            logger.warning("Prompt chuẩn bị cho Run AI rỗng, bỏ qua dispatch.")
            return

        # Kiểm tra exact pending run ID
        pending_id = getattr(self, "_pending_run_ai_request_id", None)
        if not req_id or req_id != pending_id:
            logger.warning("Bỏ qua dispatch: req_id '%s' không khớp pending_id '%s'", req_id, pending_id)
            return

        # Kiểm tra exactly-once: đã dispatch chưa
        if not hasattr(self, "_dispatched_run_ai_request_ids"):
            self._dispatched_run_ai_request_ids = set()
        if req_id in self._dispatched_run_ai_request_ids:
            logger.warning("Bỏ qua dispatch: req_id '%s' đã được dispatch trước đó (exactly-once guard)", req_id)
            return

        # Thẩm định snapshot và ngữ cảnh hiện tại trước khi dispatch
        prepared = self.page_review.get_video_prompt()
        if not prepared or prepared != prompt_text:
            logger.warning("Ngữ cảnh đã thay đổi hoặc prompt không khớp snapshot, hủy dispatch Run AI.")
            self._pending_run_ai_request_id = None
            self.page_review.set_ai_result(False, "Dữ liệu nguồn hoặc cấu hình đã thay đổi. Hãy thử lại.")
            return

        # CONSUME ID TRƯỚC KHI GỌI CONTROLLER
        self._dispatched_run_ai_request_ids.add(req_id)
        self._pending_run_ai_request_id = None

        # Single dispatch duy nhất tới controller
        self.page_review.show_ai_generation_progress(self.config.ollama_model)
        self.controller.run_ai_script(prompt_text)

    def _on_browse_bgm(self):
        # Trỏ sang Library Âm thanh / Nhạc nền
        self.close()

    def _on_save_recipe(self):
        if not self._sync_all_pages_to_config():
            QMessageBox.warning(self, "Lỗi cấu hình", "Không thể lưu Recipe do cấu hình phong cách hoặc bản thảo không hợp lệ.")
            return
        QMessageBox.information(self, "Recipe", "Đã lưu toàn bộ cấu hình hiện tại thành Recipe mẫu!")

    def _sync_all_pages_to_config(self) -> bool:
        """Đồng bộ cấu hình từ các trang con vào self.config, trả về False nếu có trang không hợp lệ."""
        if hasattr(self, "page_style") and hasattr(self.page_style, "save_to_config"):
            try:
                if not self.page_style.save_to_config(self.config):
                    logger.warning("Lưu cấu hình page_style thất bại (draft không hợp lệ).")
                    return False
            except Exception as e:
                logger.error("Ngoại lệ khi gọi page_style.save_to_config: %s", e)
                return False

        if hasattr(self, "page_source") and hasattr(self.page_source, "save_to_config"):
            self.page_source.save_to_config(self.config)
        if hasattr(self, "adv_panel") and hasattr(self.adv_panel, "save_to_config"):
            self.adv_panel.save_to_config(self.config)
        if hasattr(self, "page_review") and hasattr(self.page_review, "save_to_config"):
            self.page_review.save_to_config(self.config)
        if hasattr(self, "page_build") and hasattr(self.page_build, "save_to_config"):
            self.page_build.save_to_config(self.config)
        return True

    def is_analysis_and_preflight_idle(self) -> bool:
        """Kiểm tra xem toàn bộ 3 families (Source, Review, Preflight) đã dừng vật lý chưa."""
        try:
            # Family 1: Source
            if hasattr(self, "page_source"):
                try:
                    if getattr(self.page_source, "_workers_registry", None):
                        return False
                    if hasattr(self.page_source, "is_source_analysis_active") and self.page_source.is_source_analysis_active():
                        return False
                except Exception:
                    return False

            # Family 2: Review
            if hasattr(self, "page_review") and hasattr(self.page_review, "is_analysis_idle"):
                try:
                    if not self.page_review.is_analysis_idle():
                        return False
                except Exception:
                    return False

            # Family 3: Preflight
            if getattr(self, "_preflight_workers", None):
                return False

            return True
        except Exception as exc:
            logger.warning("Lỗi ngoại lệ khi kiểm tra is_analysis_and_preflight_idle: %s", exc)
            return False  # Fail-safe busy

    def shutdown_analysis_and_preflight_workers(self, application_quit: bool = False) -> bool:
        """Yêu cầu hủy dừng an toàn và hợp tác đối với toàn bộ 3 families worker."""
        if application_quit:
            self._app_quit_requested = True
        self._shutdown_requested = True
        source_idle = True
        try:
            if hasattr(self, "page_source") and hasattr(self.page_source, "shutdown_workers"):
                source_idle = self.page_source.shutdown_workers()
        except Exception as exc:
            logger.warning("Lỗi khi shutdown source workers: %s", exc)
            source_idle = False

        review_idle = True
        try:
            if hasattr(self, "page_review") and hasattr(self.page_review, "shutdown_analysis_workers"):
                review_idle = self.page_review.shutdown_analysis_workers()
        except Exception as exc:
            logger.warning("Lỗi khi shutdown review workers: %s", exc)
            review_idle = False

        preflight_idle = True
        try:
            if getattr(self, "_preflight_workers", None):
                preflight_idle = False
                for w in list(self._preflight_workers.values()):
                    try:
                        w.cancel()
                    except Exception as exc:
                        logger.warning("Lỗi khi hủy preflight worker: %s", exc)
        except Exception as exc:
            logger.warning("Lỗi khi shutdown preflight workers: %s", exc)
            preflight_idle = False

        return bool(source_idle and review_idle and preflight_idle and self.is_analysis_and_preflight_idle())

    @Slot()
    def _on_aggregate_workers_idle(self) -> None:
        """Xử lý khi bất kỳ family worker nào thông báo idle."""
        if self.is_analysis_and_preflight_idle():
            self.analysis_and_preflight_idle.emit()
            if getattr(self, "_source_close_pending", False):
                self._source_close_pending = False
                self._close_pending = False
                self._close_queued = False
                self.close()
            elif getattr(self, "_close_pending", False) and not getattr(self, "_close_queued", False):
                self._close_queued = True
                QTimer.singleShot(0, self.close)

    @Slot()
    def _on_sources_idle(self) -> None:
        """Tương thích ngược cho kết nối cũ của SourcePage / ReviewPage."""
        self._on_aggregate_workers_idle()

    def closeEvent(self, event):
        if self.controller.worker and self.controller.worker.isRunning():
            ret = QMessageBox.question(
                self, "Hủy tiến trình",
                "Pipeline đang chạy dựng timeline. Bạn có chắc muốn đóng và hủy không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.controller.cancel_pipeline()

        # Dừng an toàn toàn bộ 3 families worker
        all_stopped = self.shutdown_analysis_and_preflight_workers()
        if not all_stopped or not self.is_analysis_and_preflight_idle():
            self._close_pending = True
            self._source_close_pending = True
            event.ignore()
            return

        self._close_pending = False
        self._source_close_pending = False
        self._close_queued = False
        if not getattr(self, "_is_closed", False):
            self._is_closed = True
            self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._handle_reopen_admission()

    def _handle_reopen_admission(self) -> None:
        """Khôi phục quyền điều khiển và admission khi mở lại cùng instance sau normal close."""
        # 1. Nếu global application quit đã được yêu cầu hoặc đang pending -> KHÔNG BAO GIỜ reset admission
        if getattr(self, "_app_quit_requested", False):
            logger.info("Bỏ qua khôi phục admission khi show: global application quit đã kích hoạt.")
            return

        app = QApplication.instance()
        if app is not None and getattr(app, "_source_shutdown_guard", None) is not None:
            if getattr(app._source_shutdown_guard, "_pending", False):
                logger.info("Bỏ qua khôi phục admission khi show: guard đang chờ thoát ứng dụng.")
                return

        # 2. Chỉ cho phép resume nếu toàn bộ 3 families worker đã thực sự dừng vật lý (physical idle)
        if not self.is_analysis_and_preflight_idle():
            logger.warning("Bỏ qua khôi phục admission khi show: các worker trước đó chưa dừng hẳn.")
            return

        # 3. Phục hồi các cờ điều phối vòng đời của DirectorWindow
        self._is_closed = False
        self._shutdown_requested = False
        self._close_pending = False
        self._source_close_pending = False
        self._close_queued = False

        # 4. Phục hồi vòng đời child ReviewPage (nếu đã bị shutdown_analysis_workers khóa)
        if hasattr(self, "page_review"):
            if hasattr(self.page_review, "_review_close_pending"):
                self.page_review._review_close_pending = False
            if hasattr(self.page_review, "_update_overview_button_state"):
                try:
                    self.page_review._update_overview_button_state()
                except Exception:
                    pass
            if hasattr(self.page_review, "_update_matching_button_state"):
                try:
                    self.page_review._update_matching_button_state()
                except Exception:
                    pass
            if hasattr(self.page_review, "_on_prompt_workers_busy_changed"):
                try:
                    self.page_review._on_prompt_workers_busy_changed(
                        bool(getattr(self.page_review, "_prompt_workers_registry", None))
                    )
                except Exception:
                    pass
            if hasattr(self.page_review, "_on_kinetic_workers_busy_changed"):
                try:
                    self.page_review._on_kinetic_workers_busy_changed(
                        bool(getattr(self.page_review, "_kinetic_workers_registry", None))
                    )
                except Exception:
                    pass

        # 5. Phục hồi vòng đời child SourcePage
        if hasattr(self, "page_source"):
            if hasattr(self.page_source, "_shutdown_pending"):
                self.page_source._shutdown_pending = False
            if hasattr(self.page_source, "_close_pending"):
                self.page_source._close_pending = False
            if hasattr(self.page_source, "_set_analysis_controls_locked"):
                try:
                    is_active = (
                        hasattr(self.page_source, "is_source_analysis_active")
                        and self.page_source.is_source_analysis_active()
                    )
                    self.page_source._set_analysis_controls_locked(is_active)
                except Exception:
                    pass

        # 6. Invalidate point-in-time readiness observation, yêu cầu explicit click để kiểm tra lại
        self._last_readiness_result = None
        self._pending_preflight_expected = None
        self._pending_preflight_worker = None
        self._preflight_terminal_consumed = False
        if hasattr(self, "page_build") and hasattr(self.page_build, "clear_readiness_result"):
            try:
                self.page_build.clear_readiness_result()
            except Exception:
                pass

        # 7. Đồng bộ hóa lại trạng thái các nút bấm giao diện
        self._reconcile_controls_state()
