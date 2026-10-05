import os
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QFileDialog, QMessageBox, QScrollArea, QFrame,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit, QFormLayout, QStackedWidget
)
from PySide6.QtCore import Qt, Signal as pyqtSignal
from PySide6.QtGui import QFont

from src.ui.theme import ThemeColors, ThemeFonts
from src.ui.widgets.section_card import SectionCard


class TabCopilot(QWidget):
    """
    Tab 1: Đạo Diễn AI Copilot & Kịch Bản Thông Minh.
    Hỗ trợ 3 Động Cơ AI:
    1. Miễn Phí Web (Copy Prompt -> Claude/ChatGPT Web -> Dán JSON)
    2. AI Local (Ollama - Qwen 2.5 / Llama 3 trên GPU máy) [1-Click]
    3. Cloud API (DeepSeek / Claude / OpenAI) [1-Click]
    """
    open_copilot_dialog_requested = pyqtSignal()
    quick_copy_prompt_requested = pyqtSignal()
    browse_json_requested = pyqtSignal()
    apply_json_text_requested = pyqtSignal(str)
    run_local_ai_requested = pyqtSignal(str, str, str)    # format, model, endpoint
    run_cloud_api_requested = pyqtSignal(str, str, str)   # format, provider, api_key

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 4, 0, 0)
        main_layout.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(12)

        # -------------------------------------------------------------
        # CARD 1: ĐỊNH DẠNG NỘI DUNG & PHONG CÁCH
        # -------------------------------------------------------------
        card_format = SectionCard("🎯 1. CHỌN ĐỊNH DẠNG VIDEO & PHONG CÁCH DỰNG", accent_color="#8B5CF6")
        form_fmt = QFormLayout()
        card_format.set_body_layout(form_fmt)

        self.combo_story_format = QComboBox()
        self.combo_story_format.addItem("🎒 Daily Travel Vlog (16:9 - Mạch chuyện tự nhiên & B-Roll)", "travel_vlog")
        self.combo_story_format.addItem("🔥 TikTok / Reels Short (9:16 - Giật tít Hook 3s & Pacing nhanh)", "tiktok_short")
        self.combo_story_format.addItem("🎙 Podcast / Talking Head (16:9 - Lọc vấp & Luận điểm chính)", "podcast_summary")
        form_fmt.addRow("Phong cách Video:", self.combo_story_format)

        self.combo_target_duration = QComboBox()
        self.combo_target_duration.addItem("⚡ Khoảng 60 giây (Chuẩn Shorts/TikTok/Teaser)", "60s")
        self.combo_target_duration.addItem("⏱️ Khoảng 30-45 giây (Siêu ngắn, cô đọng)", "30s")
        self.combo_target_duration.addItem("🎬 Khoảng 2-3 phút (Mini Vlog tóm tắt)", "180s")
        self.combo_target_duration.addItem("🎞️ Toàn bộ video (Giữ nguyên luồng dài)", "full")
        form_fmt.addRow("Thời lượng mục tiêu:", self.combo_target_duration)

        layout.addWidget(card_format)

        # -------------------------------------------------------------
        # CARD 2: CHỌN ĐỘNG CƠ AI (3 TIERS)
        # -------------------------------------------------------------
        card_engine = SectionCard("🧠 2. CHỌN ĐỘNG CƠ ĐẠO DIỄN AI", accent_color="#38BDF8")
        v_engine = QVBoxLayout()
        card_engine.set_body_layout(v_engine)

        h_radios = QHBoxLayout()
        self.radio_free_prompt = QRadioButton("📋 Miễn Phí Web (0đ)")
        self.radio_local_ollama = QRadioButton("💻 AI Local (GPU)")
        self.radio_cloud_api = QRadioButton("⚡ Cloud API (Key)")

        self.radio_free_prompt.setChecked(True)
        self.radio_free_prompt.setToolTip("Copy Prompt sang Claude 3.7 / ChatGPT Web miễn phí để lấy JSON.")
        self.radio_local_ollama.setToolTip("Chạy trực tiếp Offline 100% qua Ollama (Qwen 2.5 / Llama 3).")
        self.radio_cloud_api.setToolTip("Gọi API trực tiếp (DeepSeek V3 / Claude 3.7 / GPT-4o) trong 3s.")

        h_radios.addWidget(self.radio_free_prompt)
        h_radios.addWidget(self.radio_local_ollama)
        h_radios.addWidget(self.radio_cloud_api)
        v_engine.addLayout(h_radios)

        # Stacked view for 3 engines
        self.stack_engine = QStackedWidget()

        # --- View 1: Free Web Prompt ---
        view_free = QWidget()
        v_free = QVBoxLayout(view_free)
        v_free.setContentsMargins(0, 8, 0, 0)
        v_free.setSpacing(8)

        lbl_free_hint = QLabel(
            "💡 <b>Quy trình Miễn Phí 0đ:</b><br>"
            "1. Bấm <b>'Copy Prompt Đạo Diễn'</b> bên dưới.<br>"
            "2. Dán vào <a href='https://claude.ai' style='color:#38BDF8;'>Claude.ai</a> hoặc <a href='https://chatgpt.com' style='color:#38BDF8;'>ChatGPT</a> (bản web miễn phí).<br>"
            "3. Copy đoạn JSON kịch bản AI trả về và dán vào ô bên dưới."
        )
        lbl_free_hint.setWordWrap(True)
        lbl_free_hint.setStyleSheet("color: #CBD5E1; font-size: 11px; line-height: 1.4;")
        v_free.addWidget(lbl_free_hint)

        h_free_btns = QHBoxLayout()
        self.btn_quick_copy = QPushButton("📋 1. Copy Prompt Đạo Diễn (Kèm Dữ Liệu Video)")
        self.btn_quick_copy.setCursor(Qt.PointingHandCursor)
        self.btn_quick_copy.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 10px 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0369A1;
            }
        """)
        self.btn_quick_copy.clicked.connect(self.quick_copy_prompt_requested.emit)

        self.btn_open_copilot = QPushButton("🧠 Tùy Chỉnh Tone...")
        self.btn_open_copilot.setCursor(Qt.PointingHandCursor)
        self.btn_open_copilot.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #94A3B8;
                border: 1px solid #334155;
                font-weight: bold;
                font-size: 11px;
                padding: 10px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0F172A;
                color: #CBD5E1;
            }
        """)
        self.btn_open_copilot.clicked.connect(self.open_copilot_dialog_requested.emit)

        h_free_btns.addWidget(self.btn_quick_copy, stretch=3)
        h_free_btns.addWidget(self.btn_open_copilot, stretch=1)
        v_free.addLayout(h_free_btns)
        self.stack_engine.addWidget(view_free)

        # --- View 2: Local Ollama ---
        view_ollama = QWidget()
        v_ollama = QVBoxLayout(view_ollama)
        v_ollama.setContentsMargins(0, 8, 0, 0)
        v_ollama.setSpacing(8)

        form_ollama = QFormLayout()
        self.txt_ollama_endpoint = QLineEdit("http://localhost:11434")
        form_ollama.addRow("Địa chỉ Ollama:", self.txt_ollama_endpoint)

        self.combo_ollama_model = QComboBox()
        self.combo_ollama_model.addItems([
            "qwen2.5:7b-instruct",
            "qwen2.5:14b-instruct",
            "llama3.1:8b",
            "deepseek-r1:7b",
            "deepseek-r1:14b"
        ])
        form_ollama.addRow("Model Local:", self.combo_ollama_model)
        v_ollama.addLayout(form_ollama)

        self.btn_run_ollama = QPushButton("🚀 1-CLICK: AI LOCAL TỰ ĐỘNG LÊN KỊCH BẢN & XUẤT TIMELINE")
        self.btn_run_ollama.setCursor(Qt.PointingHandCursor)
        self.btn_run_ollama.setStyleSheet("""
            QPushButton {
                background-color: #7C3AED;
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 11px 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #6D28D9;
            }
        """)
        self.btn_run_ollama.clicked.connect(self._on_run_ollama_clicked)
        v_ollama.addWidget(self.btn_run_ollama)
        self.stack_engine.addWidget(view_ollama)

        # --- View 3: Cloud API ---
        view_api = QWidget()
        v_api = QVBoxLayout(view_api)
        v_api.setContentsMargins(0, 8, 0, 0)
        v_api.setSpacing(8)

        form_api = QFormLayout()
        self.combo_cloud_provider = QComboBox()
        self.combo_cloud_provider.addItem("DeepSeek V3 (Ngon - Bổ - Rẻ nhất)", "deepseek")
        self.combo_cloud_provider.addItem("Claude 3.7 Sonnet (Kịch bản đỉnh nhất)", "claude")
        self.combo_cloud_provider.addItem("OpenAI GPT-4o (Ổn định)", "openai")
        form_api.addRow("Nhà cung cấp:", self.combo_cloud_provider)

        self.txt_cloud_key = QLineEdit()
        self.txt_cloud_key.setEchoMode(QLineEdit.Password)
        self.txt_cloud_key.setPlaceholderText("Dán API Key vào đây (sk-...)")
        form_api.addRow("API Key:", self.txt_cloud_key)
        v_api.addLayout(form_api)

        self.btn_run_api = QPushButton("🚀 1-CLICK: CLOUD AI TỰ ĐỘNG LÊN KỊCH BẢN & XUẤT TIMELINE")
        self.btn_run_api.setCursor(Qt.PointingHandCursor)
        self.btn_run_api.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 11px 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_run_api.clicked.connect(self._on_run_api_clicked)
        v_api.addWidget(self.btn_run_api)
        self.stack_engine.addWidget(view_api)

        v_engine.addWidget(self.stack_engine)

        # Radio button switching
        self.radio_free_prompt.toggled.connect(lambda chk: self.stack_engine.setCurrentIndex(0) if chk else None)
        self.radio_local_ollama.toggled.connect(lambda chk: self.stack_engine.setCurrentIndex(1) if chk else None)
        self.radio_cloud_api.toggled.connect(lambda chk: self.stack_engine.setCurrentIndex(2) if chk else None)

        layout.addWidget(card_engine)

        # -------------------------------------------------------------
        # CARD 3: NẠP KỊCH BẢN JSON & THI CÔNG TIMELINE
        # -------------------------------------------------------------
        card_json = SectionCard("📄 3. KHUNG DÁN KỊCH BẢN JSON ➔ XUẤT TIMELINE", accent_color="#10B981")
        v_json = QVBoxLayout()
        card_json.set_body_layout(v_json)

        lbl_json_hint = QLabel("💡 <b>Dán trực tiếp đoạn JSON kịch bản (hoặc nạp file .json):</b>")
        lbl_json_hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        v_json.addWidget(lbl_json_hint)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText("{\n  \"strategy_summary\": \"...\",\n  \"timeline_segments\": [\n    ...\n  ]\n}")
        self.txt_json_input.setMinimumHeight(160)
        self.txt_json_input.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0F172A;
                color: #E2E8F0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px;
            }
            QPlainTextEdit:focus {
                border: 1px solid #10B981;
            }
        """)
        v_json.addWidget(self.txt_json_input)

        h_actions = QHBoxLayout()
        self.btn_load_file = QPushButton("📂 Nạp File .JSON")
        self.btn_load_file.setCursor(Qt.PointingHandCursor)
        self.btn_load_file.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F1F5F9;
                font-weight: bold;
                font-size: 12px;
                padding: 10px 14px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #475569;
            }
        """)
        self.btn_load_file.clicked.connect(self.browse_json_requested.emit)

        self.btn_apply = QPushButton("⚡ THI CÔNG TIMELINE SANG DAVINCI (0.1s)")
        self.btn_apply.setCursor(Qt.PointingHandCursor)
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                font-weight: bold;
                font-size: 13px;
                padding: 11px 16px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:pressed {
                background-color: #065F46;
            }
        """)
        self.btn_apply.clicked.connect(self._on_apply_clicked)

        h_actions.addWidget(self.btn_load_file, stretch=2)
        h_actions.addWidget(self.btn_apply, stretch=3)
        v_json.addLayout(h_actions)

        layout.addWidget(card_json)
        layout.addStretch(1)

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _on_apply_clicked(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            self.browse_json_requested.emit()
        else:
            self.apply_json_text_requested.emit(text)

    def _on_run_ollama_clicked(self):
        fmt = self.combo_story_format.currentData() or "travel_vlog"
        model = self.combo_ollama_model.currentText().strip() or "qwen2.5:7b-instruct"
        endpoint = self.txt_ollama_endpoint.text().strip() or "http://localhost:11434"
        self.run_local_ai_requested.emit(fmt, model, endpoint)

    def _on_run_api_clicked(self):
        fmt = self.combo_story_format.currentData() or "travel_vlog"
        provider = self.combo_cloud_provider.currentData() or "deepseek"
        key = self.txt_cloud_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Thiếu API Key", "Vui lòng nhập API Key để sử dụng tính năng này.")
            return
        self.run_cloud_api_requested.emit(fmt, provider, key)
