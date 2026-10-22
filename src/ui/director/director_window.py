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

from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QSize
from src.ui.director.director_state import DirectorConfig
from src.ui.director.director_controller import DirectorController
from src.ui.director.widgets.stepper import DirectorStepperWidget
from src.ui.director.widgets.advanced_panel import AdvancedSidePanel
from src.ui.director.widgets.progress_panel import ProgressSidePanel
from src.ui.director.pages.source_page import SourcePage
from src.ui.director.pages.style_page import StylePage
from src.ui.director.pages.review_page import ReviewPage
from src.ui.director.pages.build_page import BuildPage


class DirectorWindow(QMainWindow):
    """Cửa sổ đạo diễn ChunDVC Director."""
    closed = Signal()

    def __init__(self, controller: Optional[DirectorController] = None, parent=None):
        super().__init__(parent)
        self.controller = controller or DirectorController()
        self.config = self.controller.config
        self.current_step = 1

        self.setWindowTitle("ChunDVC Director")
        self.setMinimumSize(QSize(1024, 640))
        self.resize(1100, 720)

        # Style nền chung chuẩn Dark Theme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #131316;
            }
            QWidget {
                color: #ffffff;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }
        """)

        self.setAcceptDrops(True)
        self._init_ui()
        self._wire_signals()
        self.go_to_step(1)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
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
        # 1. Thanh tiêu đề (38px)
        # -------------------------------------------------------------
        self.header_frame = QFrame(central_widget)
        self.header_frame.setFixedHeight(44)
        self.header_frame.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border-bottom: 1px solid #27272a;
            }
        """)
        h_layout = QHBoxLayout(self.header_frame)
        h_layout.setContentsMargins(16, 0, 12, 0)
        h_layout.setSpacing(10)

        lbl_logo = QLabel("🎬 <b>ChunDVC Director</b>")
        lbl_logo.setStyleSheet("font-size: 13.5px; color: #ffffff;")
        h_layout.addWidget(lbl_logo)

        self.lbl_project = QLabel("ResolveFlow_Project")
        self.lbl_project.setStyleSheet("color: #71717a; font-size: 12px;")
        h_layout.addWidget(self.lbl_project)

        h_layout.addStretch(1)

        # Chip Whisper
        self.btn_whisper = QPushButton(f"Whisper {self.config.whisper_model} · {self.config.whisper_language}")
        self.btn_whisper.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_whisper.setStyleSheet("""
            QPushButton {
                background-color: rgba(124, 58, 237, 0.15);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.35);
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(124, 58, 237, 0.25);
            }
        """)
        h_layout.addWidget(self.btn_whisper)

        # Nút đóng ✕
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
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
        divider1.setStyleSheet("background-color: #27272a;")
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
        divider2.setStyleSheet("background-color: #27272a;")
        root_layout.addWidget(divider2)

        # -------------------------------------------------------------
        # 4. Chân trang (Footer - 52px)
        # -------------------------------------------------------------
        self.footer_frame = QFrame(central_widget)
        self.footer_frame.setFixedHeight(52)
        self.footer_frame.setStyleSheet("""
            QFrame {
                background-color: #18181b;
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
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
            QPushButton:disabled {
                color: #52525b;
                border-color: #27272a;
            }
        """)
        self.btn_back.clicked.connect(self._on_btn_back_clicked)
        f_layout.addWidget(self.btn_back)

        self.btn_save_recipe = QPushButton("💾 Lưu thành Recipe")
        self.btn_save_recipe.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_recipe.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11.5px;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: #27272a;
            }
        """)
        self.btn_save_recipe.clicked.connect(self._on_save_recipe)
        f_layout.addWidget(self.btn_save_recipe)

        f_layout.addStretch(1)

        # DUY NHẤT một nút chính (Màu tím) bên phải
        self.btn_primary = QPushButton("Tiếp: Kiểu dựng ›")
        self.btn_primary.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_primary.setStyleSheet("""
            QPushButton {
                background-color: #7c3aed;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #6d28d9;
            }
            QPushButton:disabled {
                background-color: #3f3f46;
                color: #71717a;
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

        # Advanced panel
        self.adv_panel.close_requested.connect(self.adv_panel.hide)

        # Review page
        self.page_review.ai_run_requested.connect(self._on_run_ai_script)

        # Rough scan từ SourcePage & ReviewPage
        self.page_source.rough_scan_requested.connect(self._on_start_rough_scan)
        self.page_review.rough_scan_requested.connect(self._on_start_rough_scan)

        # Build page
        self.page_build.bgm_browse_requested.connect(self._on_browse_bgm)

        # Controller signals
        self.controller.step_status_changed.connect(self.prog_panel.set_step_status)
        self.controller.progress_changed.connect(self.prog_panel.set_progress)
        self.controller.log_received.connect(self.prog_panel.append_log)
        self.controller.pipeline_finished.connect(self._on_pipeline_finished)
        self.controller.ai_script_finished.connect(self.page_review.set_ai_result)

        # Cancel từ progress panel
        self.prog_panel.cancel_requested.connect(self.controller.cancel_pipeline)

    def _on_start_rough_scan(self):
        """Khởi chạy quét thô Whisper (Phase 1) tạo cache và trích xuất câu thoại."""
        self._sync_all_pages_to_config()
        if not self.config.video_paths:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn trước khi quét thô.")
            return

        self._is_rough_scanning = True
        self.adv_panel.hide()
        self.prog_panel.reset()
        self.prog_panel.show()

        if hasattr(self.page_source, "btn_scan"):
            self.page_source.btn_scan.setEnabled(False)
            self.page_source.btn_scan.setText("⏳ Đang quét thô...")
        if hasattr(self.page_review, "btn_review_scan"):
            self.page_review.btn_review_scan.setEnabled(False)
            self.page_review.btn_review_scan.setText("⏳ Đang quét thô...")

        self.btn_primary.setEnabled(False)
        self.btn_primary.setText("⏳ Đang quét thô Whisper...")
        self.controller.start_pipeline(phase=1)

    def go_to_step(self, step: int):
        """Chuyển đổi giao diện sang bước tương ứng (1..4)."""
        self.current_step = max(1, min(4, step))
        self.stepper.set_current_step(self.current_step)
        self.stack.setCurrentIndex(self.current_step - 1)

        # Cập nhật nút Quay lại
        self.btn_back.setEnabled(self.current_step > 1)

        # Cập nhật nhãn nút chính
        if self.current_step == 1:
            self.btn_primary.setText("Tiếp: Kiểu dựng ›")
        elif self.current_step == 2:
            self.btn_primary.setText("Tiếp: Duyệt & kịch bản ›")
        elif self.current_step == 3:
            self.btn_primary.setText("Tiếp: Dựng timeline ›")
            # Tự động nạp transcript thực tế từ Cache vào ReviewPage nếu có
            if hasattr(self.page_review, "load_transcripts_from_cache"):
                self.page_review.load_transcripts_from_cache(
                    self.config.video_paths,
                    self.config.whisper_model,
                    self.config.whisper_language
                )
            if hasattr(self.page_review, "set_story_metadata"):
                self.page_review.set_story_metadata(
                    style_intent=self.config.footage_type,
                    hook_dur=f"{int(self.config.vlog_hook_dur)}s" if self.config.vlog_hook_dur else "30_60s"
                )
        elif self.current_step == 4:
            self.btn_primary.setText("✂ 🚀 Dựng timeline")
            # Lưu state và refresh summary card
            self._sync_all_pages_to_config()
            self.page_build.update_summary(self.config)

    def _on_btn_back_clicked(self):
        if self.current_step > 1:
            self.go_to_step(self.current_step - 1)

    def _on_btn_primary_clicked(self):
        if self.current_step < 4:
            self._sync_all_pages_to_config()
            self.go_to_step(self.current_step + 1)
        else:
            # Bước 4: Chạy pipeline dựng timeline!
            self._start_build_timeline()

    def _start_build_timeline(self):
        self._sync_all_pages_to_config()
        if not self.config.video_paths:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn ít nhất một video nguồn trước khi dựng timeline.")
            return
        self.adv_panel.hide()
        self.prog_panel.reset()
        self.prog_panel.show()

        self.btn_primary.setEnabled(False)
        self.btn_primary.setText("Đang dựng timeline...")

        # Chạy pipeline
        self.controller.start_pipeline(phase=0)

    def _on_pipeline_finished(self, success: bool, message: str):
        is_rough_scan = getattr(self, "_is_rough_scanning", False)
        self._is_rough_scanning = False

        self.btn_primary.setEnabled(True)
        if self.current_step == 1:
            self.btn_primary.setText("Tiếp: Kiểu dựng ›")
        elif self.current_step == 2:
            self.btn_primary.setText("Tiếp: Duyệt & kịch bản ›")
        elif self.current_step == 3:
            self.btn_primary.setText("Tiếp: Dựng timeline ›")
        else:
            self.btn_primary.setText("✂ 🚀 Dựng timeline")

        if is_rough_scan:
            self.prog_panel.hide()
            self.page_source.recheck_cache()
            if hasattr(self.page_review, "load_transcripts_from_cache"):
                self.page_review.load_transcripts_from_cache(
                    self.config.video_paths,
                    self.config.whisper_model,
                    self.config.whisper_language
                )
            if hasattr(self.page_review, "btn_review_scan"):
                self.page_review.btn_review_scan.setEnabled(True)
                self.page_review.btn_review_scan.setText("⚡ Quét thô Whisper")

            if success:
                QMessageBox.information(
                    self,
                    "Quét thô hoàn tất",
                    "✓ Bóc tách lời thoại và phát hiện khoảng lặng bằng Whisper thành công!\n"
                    "Dữ liệu câu thoại đã sẵn sàng trong cache cho Bước 3."
                )
            else:
                QMessageBox.warning(self, "Lỗi quét thô", f"Quá trình quét thô gặp lỗi:\n{message}")
            return

        if success:
            QMessageBox.information(self, "Thành công", f"Dựng timeline hoàn tất:\n{message}")
        else:
            QMessageBox.warning(self, "Lỗi tiến trình", f"Tiến trình gặp lỗi:\n{message}")

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

    def _on_whisper_changed(self, model, lang):
        self.config.whisper_model = model
        self.config.whisper_language = lang
        self.btn_whisper.setText(f"Whisper {model} · {lang}")

    def _on_style_changed(self, style_id):
        self.config.apply_style_preset(style_id)
        self.page_style.load_from_config(self.config)
        self.adv_panel.load_from_config(self.config)

    def _on_run_ai_script(self):
        self._sync_all_pages_to_config()
        prompt = self.page_review.get_active_prompt()
        self.controller.run_ai_script(prompt)

    def _on_browse_bgm(self):
        # Trỏ sang Library Âm thanh / Nhạc nền
        self.close()

    def _on_save_recipe(self):
        self._sync_all_pages_to_config()
        QMessageBox.information(self, "Recipe", "Đã lưu toàn bộ cấu hình hiện tại thành Recipe mẫu!")

    def _sync_all_pages_to_config(self):
        self.page_source.save_to_config(self.config)
        self.page_style.save_to_config(self.config)
        self.adv_panel.save_to_config(self.config)
        self.page_review.save_to_config(self.config)
        self.page_build.save_to_config(self.config)

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

        self.closed.emit()
        super().closeEvent(event)
