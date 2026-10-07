import os
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QFileDialog, QMessageBox, QScrollArea, QFrame,
    QComboBox, QRadioButton, QButtonGroup, QLineEdit, QFormLayout, QStackedWidget,
    QCheckBox
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

        self.combo_hook_duration = QComboBox()
        self.combo_hook_duration.addItem("🔥 Dài 30s - 60s (Chuẩn Teaser Vlog / Highlight mở đầu)", "30_60s")
        self.combo_hook_duration.addItem("⚡ Dài 15s - 30s (Vừa phải, dồn dập)", "15_30s")
        self.combo_hook_duration.addItem("🎯 Dài 3s - 5s (Siêu ngắn chuẩn TikTok / Reels)", "3_5s")
        self.combo_hook_duration.addItem("🚫 Không cần đoạn Hook riêng", "none")
        form_fmt.addRow("Đoạn Hook Mở Đầu:", self.combo_hook_duration)

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
        # CARD 3: CHỌN NHẠC NỀN & AUTO BEAT-SYNC
        # -------------------------------------------------------------
        card_bgm = SectionCard("🎵 3. CHỌN NHẠC NỀN & AUTO BEAT-SYNC", accent_color="#06B6D4")
        v_bgm = QVBoxLayout()
        card_bgm.set_body_layout(v_bgm)

        form_bgm = QFormLayout()
        self.combo_bgm_mode = QComboBox()
        self.combo_bgm_mode.addItem("✨ Tự động theo Mood (AI gợi ý từ kho nhạc)", "auto_mood")
        self.combo_bgm_mode.addItem("🍃 Chill Vlog (Acoustic / Lo-fi thư giãn)", "mood_chill")
        self.combo_bgm_mode.addItem("🔥 Upbeat Trend (Beat Drop sôi động, TikTok)", "mood_upbeat")
        self.combo_bgm_mode.addItem("🎬 Cinematic (Hùng tráng, sâu lắng)", "mood_cinematic")
        self.combo_bgm_mode.addItem("🎭 Hài hước / Meme (Vui nhộn)", "mood_funny")
        self.combo_bgm_mode.addItem("📁 Chọn tệp nhạc riêng từ máy tính...", "custom_file")
        self.combo_bgm_mode.addItem("🚫 Không chèn nhạc nền", "none")

        h_bgm_row = QHBoxLayout()
        h_bgm_row.addWidget(self.combo_bgm_mode, stretch=3)

        self.btn_preview_bgm = QPushButton("▶ Nghe Thử")
        self.btn_preview_bgm.setCursor(Qt.PointingHandCursor)
        self.btn_preview_bgm.setStyleSheet("""
            QPushButton {
                background-color: #0E7490;
                color: white;
                font-weight: bold;
                font-size: 11px;
                padding: 7px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0891B2;
            }
        """)
        self.btn_browse_bgm = QPushButton("📁 Chọn tệp...")
        self.btn_browse_bgm.setCursor(Qt.PointingHandCursor)
        self.btn_browse_bgm.setStyleSheet("""
            QPushButton {
                background-color: #1E293B;
                color: #CBD5E1;
                border: 1px solid #334155;
                font-weight: bold;
                font-size: 11px;
                padding: 7px 10px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_browse_bgm.clicked.connect(self.browse_custom_bgm_file)
        h_bgm_row.addWidget(self.btn_browse_bgm, stretch=1)
        h_bgm_row.addWidget(self.btn_preview_bgm, stretch=1)
        form_bgm.addRow("Nhạc nền (BGM):", h_bgm_row)

        self.chk_beat_sync = QCheckBox("⚡ Tự động Beat-Sync (Cắt cảnh & B-Roll giật theo nhịp nhạc)")
        self.chk_beat_sync.setChecked(True)
        self.chk_beat_sync.setStyleSheet("color: #67E8F9; font-weight: bold; font-size: 11px;")
        form_bgm.addRow(self.chk_beat_sync)

        v_bgm.addLayout(form_bgm)

        self.lbl_bgm_file_info = QLabel("Kho nhạc: Sẵn sàng tự động gợi ý theo nội dung kịch bản.")
        self.lbl_bgm_file_info.setStyleSheet("color: #94A3B8; font-size: 11px;")
        v_bgm.addWidget(self.lbl_bgm_file_info)

        self._custom_bgm_file: Optional[str] = None
        self._bgm_player = None
        self._bgm_audio_output = None
        self._is_bgm_playing = False

        self.combo_bgm_mode.currentIndexChanged.connect(self._on_bgm_mode_changed)

        layout.addWidget(card_bgm)

        # -------------------------------------------------------------
        # CARD 4: NẠP KỊCH BẢN JSON & THI CÔNG TIMELINE
        # -------------------------------------------------------------
        card_json = SectionCard("📄 4. KHUNG DÁN KỊCH BẢN JSON ➔ XUẤT TIMELINE", accent_color="#10B981")
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

    def _on_bgm_mode_changed(self, index: int):
        mode = self.combo_bgm_mode.currentData()
        if mode == "custom_file":
            if getattr(self, "_custom_bgm_file", None):
                self.lbl_bgm_file_info.setText(f"📁 Đã chọn tệp: {os.path.basename(self._custom_bgm_file)}")
            else:
                self.lbl_bgm_file_info.setText("📁 Vui lòng bấm 'Chọn tệp...' để nạp file nhạc.")
        elif mode == "none":
            self.lbl_bgm_file_info.setText("🚫 Không chèn nhạc nền vào timeline.")
        elif mode == "auto_mood":
            self.lbl_bgm_file_info.setText("✨ Tự động gợi ý từ kho nhạc theo chủ đề kịch bản.")
        else:
            self.lbl_bgm_file_info.setText(f"🎵 Nhạc theo Mood: {self.combo_bgm_mode.currentText()}")

    def browse_custom_bgm_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn tệp nhạc nền từ máy tính",
            "",
            "Audio Files (*.mp3 *.wav *.m4a *.aac *.flac);;All Files (*.*)"
        )
        if file_path:
            self._custom_bgm_file = os.path.normpath(os.path.abspath(file_path))
            self.lbl_bgm_file_info.setText(f"📁 Đã chọn tệp: {os.path.basename(file_path)}")
            idx = self.combo_bgm_mode.findData("custom_file")
            if idx >= 0:
                self.combo_bgm_mode.setCurrentIndex(idx)

    def _toggle_bgm_preview(self):
        if self._is_bgm_playing:
            self._stop_bgm_preview()
        else:
            self._start_bgm_preview()

    def _start_bgm_preview(self):
        bgm_files = self.get_selected_bgm_files()
        if not bgm_files or not os.path.exists(bgm_files[0]):
            QMessageBox.information(self, "Không tìm thấy nhạc", "Chưa tìm thấy tệp nhạc tương ứng để nghe thử.")
            return

        target_file = bgm_files[0]
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtCore import QUrl
            if self._bgm_player is None:
                self._bgm_player = QMediaPlayer(self)
                self._bgm_audio_output = QAudioOutput(self)
                self._bgm_player.setAudioOutput(self._bgm_audio_output)
                self._bgm_player.playbackStateChanged.connect(self._on_bgm_player_state_changed)

            self._bgm_player.setSource(QUrl.fromLocalFile(os.path.abspath(target_file)))
            self._bgm_audio_output.setVolume(0.8)
            self._bgm_player.play()
            self._is_bgm_playing = True
            self.btn_preview_bgm.setText("⏸ Dừng")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi phát nhạc", f"Không thể phát tệp nhạc:\n{e}")

    def _stop_bgm_preview(self):
        if self._bgm_player is not None:
            try:
                self._bgm_player.stop()
            except Exception:
                pass
        self._is_bgm_playing = False
        self.btn_preview_bgm.setText("▶ Nghe Thử")

    def _on_bgm_player_state_changed(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.StoppedState:
            self._is_bgm_playing = False
            self.btn_preview_bgm.setText("▶ Nghe Thử")

    def get_selected_bgm_files(self, project_structure=None) -> list[str]:
        """
        Lấy danh sách các đường dẫn tệp BGM dựa trên chế độ người dùng lựa chọn.
        """
        mode = self.combo_bgm_mode.currentData()
        if mode == "none":
            return []

        if mode == "custom_file":
            if self._custom_bgm_file and os.path.exists(self._custom_bgm_file):
                return [self._custom_bgm_file]
            return []

        from src.core.asset_indexer import AssetIndexer
        indexer = AssetIndexer()
        music_assets = indexer.scan_music_assets()

        mood_key = "chill_vlog"
        if mode == "mood_chill":
            mood_key = "chill_vlog"
        elif mode == "mood_upbeat":
            mood_key = "upbeat_trend"
        elif mode == "mood_cinematic":
            mood_key = "cinematic"
        elif mode == "mood_funny":
            mood_key = "funny"
        elif mode == "auto_mood":
            # Gợi ý dựa trên định dạng câu chuyện hiện tại
            story_fmt = self.combo_story_format.currentData() or "travel_vlog"
            if story_fmt == "tiktok_short":
                mood_key = "upbeat_trend"
            elif story_fmt == "podcast_summary":
                mood_key = "chill_vlog"
            else:
                mood_key = "chill_vlog"

        items = music_assets.get(mood_key, [])
        if not items:
            # Fallback sang bất kỳ mood nào có bài nhạc
            for m, lst in music_assets.items():
                if lst:
                    items = lst
                    break

        if items:
            return [items[0]["file_path"]]
        return []

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

