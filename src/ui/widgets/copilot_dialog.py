"""
Hộp thoại AI Story Copilot (Xuất Prompt & Nhập Bản Vẽ Đạo Diễn).
Cho phép người dùng tương tác tự do với Claude / ChatGPT / Gemini trên Web
và nạp kết quả dựng tự động trực tiếp vào DaVinci Resolve.
"""

import os
import webbrowser
from typing import List, Dict, Any, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QTabWidget, QWidget, QFrame, QMessageBox,
    QGroupBox, QScrollArea, QApplication, QFileDialog
)
from PySide6.QtCore import Qt, Signal as pyqtSignal
from PySide6.QtGui import QFont, QColor

from src.core.story_copilot import StoryCopilot, CopilotDirectorPlan
from src.ui.theme import ThemeColors


class StoryCopilotDialog(QDialog):
    """
    Hộp thoại tương tác Copilot:
    - Tab 1: Xuất Prompt chuẩn Marketing kèm dữ liệu video để dán vào Claude/ChatGPT.
    - Tab 2: Nhập bản vẽ JSON từ AI và nạp trực tiếp lên DaVinci Resolve.
    """

    plan_applied_signal = pyqtSignal(object)  # Emits CopilotDirectorPlan

    def __init__(
        self,
        project_name: str,
        clips_data: List[Dict[str, Any]],
        project_structure: Optional[Any] = None,
        story_intent: str = "travel_vlog",
        hook_duration: str = "30_60s",
        target_duration: str = "full",
        parent=None
    ):
        super().__init__(parent)
        self.project_name = project_name
        self.clips_data = clips_data
        self.project_structure = project_structure
        self.story_intent = story_intent
        self.hook_duration = hook_duration
        self.target_duration = target_duration
        self.current_plan: Optional[CopilotDirectorPlan] = None

        self.setWindowTitle("🧠 AI Story Copilot (Claude / ChatGPT / Gemini)")
        self.resize(850, 650)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #121216;
                color: #E2E8F0;
            }}
            QTabWidget::pane {{
                border: 1px solid #2D3748;
                border-radius: 8px;
                background-color: #1A202C;
            }}
            QTabBar::tab {{
                background-color: #171923;
                color: #A0AEC0;
                padding: 10px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QTabBar::tab:selected {{
                background-color: #2B6CB0;
                color: #FFFFFF;
            }}
            QPlainTextEdit {{
                background-color: #0F172A;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 6px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 8px;
            }}
            QPushButton {{
                background-color: #3182CE;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 18px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #2B6CB0;
            }}
            QPushButton:disabled {{
                background-color: #4A5568;
                color: #A0AEC0;
            }}
        """)

        self._setup_ui()
        self._generate_initial_prompt()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Info
        header_lbl = QLabel("🤖 <b>AI Story Copilot:</b> Biến Claude / ChatGPT thành Tổng Đạo Diễn Dựng Phim của bạn!")
        header_lbl.setStyleSheet("font-size: 15px; color: #63B3ED;")
        layout.addWidget(header_lbl)

        sub_lbl = QLabel("Tận dụng tư duy Marketing & Giữ chân AVD của Claude.ai để lên kịch bản, sau đó ResolveFlow tự động dựng 100%.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #A0AEC0;")
        layout.addWidget(sub_lbl)

        # Tabs
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)

        # --- TAB 1: XUẤT PROMPT ---
        tab_export = QWidget()
        tab_export_layout = QVBoxLayout(tab_export)
        tab_export_layout.setContentsMargins(12, 12, 12, 12)
        tab_export_layout.setSpacing(10)

        guide_box = QFrame()
        guide_box.setStyleSheet("background-color: #2D3748; border-radius: 6px; padding: 8px;")
        guide_layout = QVBoxLayout(guide_box)
        guide_layout.setContentsMargins(8, 8, 8, 8)
        guide_text = QLabel(
            "<b>Các bước thực hiện:</b><br>"
            "1. Bấm <b>'📋 Copy Toàn Bộ Prompt'</b> ở dưới.<br>"
            "2. Mở <b>Claude.ai</b> hoặc <b>ChatGPT</b>, bấm <b>Ctrl + V (Dán)</b> và Enter.<br>"
            "3. Bạn có thể trò chuyện yêu cầu AI chỉnh sửa kịch bản theo ý thích.<br>"
            "4. Copy đoạn mã <b>JSON</b> kết quả từ AI và chuyển sang <b>Tab 2 (Nhập Bản Vẽ)</b>."
        )
        guide_text.setStyleSheet("font-size: 12px; color: #E2E8F0;")
        guide_layout.addWidget(guide_text)
        tab_export_layout.addWidget(guide_box)

        self.txt_prompt_preview = QPlainTextEdit()
        self.txt_prompt_preview.setReadOnly(True)
        tab_export_layout.addWidget(self.txt_prompt_preview, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_copy_prompt = QPushButton("📋 Copy Toàn Bộ Prompt (Clipboard)")
        self.btn_copy_prompt.setStyleSheet("background-color: #38A169; font-size: 14px; padding: 12px;")
        self.btn_copy_prompt.clicked.connect(self._copy_prompt_to_clipboard)
        btn_row.addWidget(self.btn_copy_prompt, stretch=2)

        self.btn_open_claude = QPushButton("🌐 Mở Claude.ai")
        self.btn_open_claude.setStyleSheet("background-color: #D97706;")
        self.btn_open_claude.clicked.connect(lambda: webbrowser.open("https://claude.ai"))
        btn_row.addWidget(self.btn_open_claude, stretch=1)

        self.btn_open_chatgpt = QPushButton("🌐 Mở ChatGPT")
        self.btn_open_chatgpt.setStyleSheet("background-color: #059669;")
        self.btn_open_chatgpt.clicked.connect(lambda: webbrowser.open("https://chatgpt.com"))
        btn_row.addWidget(self.btn_open_chatgpt, stretch=1)

        tab_export_layout.addLayout(btn_row)
        self.tabs.addTab(tab_export, "📋 Bước 1: Xuất Prompt Kịch Bản")

        # --- TAB 2: NHẬP BẢN VẼ ---
        tab_import = QWidget()
        tab_import_layout = QVBoxLayout(tab_import)
        tab_import_layout.setContentsMargins(12, 12, 12, 12)
        tab_import_layout.setSpacing(10)

        header_row = QHBoxLayout()
        import_lbl = QLabel("Dán khối JSON kịch bản hoặc mở trực tiếp tệp .json từ máy:")
        import_lbl.setStyleSheet("font-weight: bold; color: #E2E8F0;")
        header_row.addWidget(import_lbl, stretch=1)

        self.btn_load_file = QPushButton("📂 Mở Tệp JSON Từ Máy")
        self.btn_load_file.setStyleSheet("background-color: #4A5568; font-size: 12px; padding: 6px 12px;")
        self.btn_load_file.clicked.connect(self._load_json_file_from_disk)
        header_row.addWidget(self.btn_load_file)

        tab_import_layout.addLayout(header_row)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText('Dán toàn bộ khối JSON từ Claude/ChatGPT vào đây hoặc bấm "Mở Tệp JSON Từ Máy"...\nVí dụ:\n{\n  "strategy_summary": "...",\n  "global_hook": { ... },\n  "timeline_segments": [ ... ]\n}')
        tab_import_layout.addWidget(self.txt_json_input, stretch=1)

        # Analysis preview area
        self.lbl_analysis_preview = QLabel("Chưa có bản vẽ nào được phân tích.")
        self.lbl_analysis_preview.setStyleSheet("background-color: #2D3748; border-radius: 6px; padding: 10px; font-size: 12px; color: #90CDF4;")
        self.lbl_analysis_preview.setWordWrap(True)
        tab_import_layout.addWidget(self.lbl_analysis_preview)

        import_btn_row = QHBoxLayout()
        self.btn_parse_plan = QPushButton("🔍 Kiểm tra & Phân tích Bản vẽ")
        self.btn_parse_plan.setStyleSheet("background-color: #4A5568;")
        self.btn_parse_plan.clicked.connect(self._parse_plan_json)
        import_btn_row.addWidget(self.btn_parse_plan, stretch=1)

        self.btn_apply_plan = QPushButton("🎬 Áp dụng & Dựng ngay lên DaVinci Resolve!")
        self.btn_apply_plan.setStyleSheet("background-color: #E53E3E; font-size: 14px; padding: 12px;")
        self.btn_apply_plan.setEnabled(False)
        self.btn_apply_plan.clicked.connect(self._apply_plan_and_close)
        import_btn_row.addWidget(self.btn_apply_plan, stretch=2)

        tab_import_layout.addLayout(import_btn_row)
        self.tabs.addTab(tab_import, "📥 Bước 2: Nhập Bản Vẽ Đạo Diễn")

        # Bottom Close Button
        btn_close = QPushButton("Đóng (Close)")
        btn_close.setStyleSheet("background-color: #4A5568; padding: 6px 12px;")
        btn_close.clicked.connect(self.reject)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

    def _generate_initial_prompt(self):
        """Sinh chuỗi prompt đầy đủ dựa trên dữ liệu video hiện tại."""
        prompt = StoryCopilot.generate_copilot_prompt(
            project_name=self.project_name,
            clips_data=self.clips_data,
            project_structure=self.project_structure,
            story_intent=self.story_intent,
            hook_duration=self.hook_duration,
            target_duration=self.target_duration
        )
        self.txt_prompt_preview.setPlainText(prompt)

    def _copy_prompt_to_clipboard(self):
        """Copy toàn bộ nội dung prompt vào clipboard của hệ điều hành."""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.txt_prompt_preview.toPlainText())
        QMessageBox.information(
            self,
            "Đã Copy Prompt!",
            f"✔ Đã copy toàn bộ Prompt Kịch bản & Dữ liệu {len(self.clips_data)} video vào Clipboard!\n\n"
            "👉 Hãy mở Claude.ai hoặc ChatGPT, bấm Ctrl + V (Dán) và ấn Enter."
        )

    def _parse_plan_json(self):
        """Phân tích JSON do người dùng dán vào."""
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Trống dữ liệu", "Vui lòng dán khối JSON từ Claude/ChatGPT trước khi kiểm tra.")
            return

        try:
            plan = StoryCopilot.parse_copilot_response(text)
            self.current_plan = plan

            # Hiển thị tóm tắt
            lines = [
                f"<b>🎯 Chiến lược:</b> {plan.strategy_summary or 'AIDA Marketing'}",
                f"<b>🔥 Global Hook:</b> Clip {plan.global_hook.clip_index if plan.global_hook else 'N/A'} "
                f"({plan.global_hook.start_sec if plan.global_hook else 0}s - {plan.global_hook.end_sec if plan.global_hook else 0}s) "
                f"➔ Text: <i>\"{plan.global_hook.hook_title if plan.global_hook else ''}\"</i>",
                f"<b>🎬 Số phân đoạn trên Timeline:</b> {len(plan.timeline_segments)} cuts",
                f"<b>📣 Kêu gọi hành động (CTA):</b> {plan.call_to_action or 'Không'}"
            ]
            if plan.viral_headlines:
                lines.append(f"<b>📌 Tiêu đề đề xuất:</b> {plan.viral_headlines[0]}")

            self.lbl_analysis_preview.setText("<br>".join(lines))
            self.btn_apply_plan.setEnabled(True)
            QMessageBox.information(self, "Bản vẽ hợp lệ", "✔ Bản vẽ JSON hoàn toàn hợp lệ và sẵn sàng dựng lên DaVinci Resolve!")

        except Exception as e:
            self.btn_apply_plan.setEnabled(False)
            self.lbl_analysis_preview.setText(f"<span style='color: #FC8181;'>❌ Lỗi phân tích JSON: {str(e)}</span>")
            QMessageBox.critical(self, "Lỗi định dạng JSON", f"Không thể đọc bản vẽ từ AI:\n{str(e)}\n\nHãy đảm bảo bạn copy đúng khối JSON có ngoặc {{ ... }}.")

    def _load_json_file_from_disk(self):
        """Mở hộp thoại chọn tệp JSON kịch bản và tự động nạp phân tích."""
        f_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn Tệp Kịch Bản JSON (AI Copilot Director Plan)",
            "",
            "JSON Files (*.json);;Text Files (*.txt);;All Files (*.*)"
        )
        if f_path and os.path.exists(f_path):
            try:
                with open(f_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.txt_json_input.setPlainText(content)
                self._parse_plan_json()
            except Exception as e:
                QMessageBox.critical(self, "Lỗi đọc tệp", f"Không thể đọc tệp JSON: {e}")

    def _apply_plan_and_close(self):
        """Gửi bản vẽ đã duyệt về App chính và đóng dialog."""
        if self.current_plan:
            self.plan_applied_signal.emit(self.current_plan)
            self.accept()
