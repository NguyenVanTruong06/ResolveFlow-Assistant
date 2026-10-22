"""
src/ui/director/pages/review_page.py
Bước 3: Duyệt Câu Thoại & Kịch Bản AI.
Bao gồm:
- Cột 1: Danh sách câu thoại Giữ/Cắt kết nối ReviewState (Ctrl+Z hoàn tác), Mini Timeline màu.
- Cột 2: Khối Kịch bản AI với 3 chế độ:
    1. 📋 Prompt Web (Miễn phí 0đ): Copy 2 luồng prompt (Dựng Video V1 & Chữ Kinetic V2) + Khu vực dán JSON tự nhận diện + Áp dụng.
    2. ☁️ Cloud API: Gemini, DeepSeek, Claude, OpenAI với ô nhập API Key tự nhận diện tiền tố và lưu QSettings.
    3. 💻 Local Ollama: Mô hình dropdown, endpoint URL, nút kiểm tra kết nối (ping) và 1-click dựng.
"""

import json
from typing import Any, Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QApplication, QStackedWidget,
    QPlainTextEdit, QLineEdit, QComboBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSettings
from PySide6.QtGui import QKeyEvent
from src.core.review_state import ReviewState
from src.ui.director.director_state import DirectorConfig, parse_web_prompt_json
from src.ui.director.widgets.mini_timeline_view import MiniTimelineWidget

try:
    from src.core.story_copilot import StoryCopilot
except Exception:
    StoryCopilot = None

try:
    from src.core.kinetic import generate_kinetic_prompt, DEMO_WORDS
except Exception:
    generate_kinetic_prompt = None
    DEMO_WORDS = []


class OllamaPingWorker(QThread):
    """Worker ngầm để ping kiểm tra kết nối Ollama không làm đơ giao diện."""
    finished_ping = Signal(bool, str, list)  # (is_online, message, model_names)

    def __init__(self, endpoint: str, parent=None):
        super().__init__(parent)
        self.endpoint = endpoint

    def run(self):
        import urllib.request
        url = f"{self.endpoint.rstrip('/')}/api/tags"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ChunDVC-Director/1.0"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                msg = f"Đã kết nối ({len(models)} model sẵn sàng)" if models else "Đã kết nối Ollama"
                self.finished_ping.emit(True, msg, models)
        except Exception as e:
            self.finished_ping.emit(False, f"Không kết nối được ({str(e)[:45]})", [])


class ReviewRowWidget(QFrame):
    """Một dòng hiển thị câu thoại với nút Giữ lại / Cắt."""
    toggled = Signal(int)

    def __init__(self, row_idx: int, tc: str, text: str, is_cut: bool, tag: str = "", parent=None):
        super().__init__(parent)
        self.row_idx = row_idx
        self.tc = tc
        self.text = text
        self.is_cut = is_cut
        self.tag = tag
        self.setFixedHeight(34)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # Timecode
        self.lbl_tc = QLabel(self.tc)
        self.lbl_tc.setStyleSheet("color: #71717a; font-family: monospace; font-size: 11px;")
        layout.addWidget(self.lbl_tc)

        # Text
        self.lbl_text = QLabel(self.text)
        self.lbl_text.setStyleSheet("font-size: 11.5px;")
        layout.addWidget(self.lbl_text, stretch=1)

        # Tag nếu có (Hook, Vấp, Lặp)
        if self.tag:
            lbl_tag = QLabel(self.tag)
            lbl_tag.setStyleSheet("""
                color: #c4b5fd;
                background: rgba(124, 58, 237, 0.15);
                border-radius: 3px;
                padding: 1px 5px;
                font-size: 10px;
                font-weight: 600;
            """)
            layout.addWidget(lbl_tag)

        # Nút Giữ / Cắt
        self.btn_act = QPushButton()
        self.btn_act.setFixedSize(54, 22)
        self.btn_act.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_act.clicked.connect(lambda: self.toggled.emit(self.row_idx))
        layout.addWidget(self.btn_act)

        self._refresh()

    def set_cut(self, is_cut: bool):
        self.is_cut = is_cut
        self._refresh()

    def _refresh(self):
        if self.is_cut:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(239, 68, 68, 0.08);
                    border: 1px solid rgba(239, 68, 68, 0.2);
                    border-radius: 4px;
                }
            """)
            self.lbl_text.setStyleSheet("color: #71717a; text-decoration: line-through; font-size: 11.5px;")
            self.btn_act.setText("Giữ lại")
            self.btn_act.setStyleSheet("""
                QPushButton {
                    background-color: #27272a;
                    color: #4ade80;
                    border: 1px solid #3f3f46;
                    border-radius: 3px;
                    font-size: 10.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #3f3f46;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #18181b;
                    border: 1px solid #27272a;
                    border-radius: 4px;
                }
            """)
            self.lbl_text.setStyleSheet("color: #ffffff; font-size: 11.5px;")
            self.btn_act.setText("Cắt")
            self.btn_act.setStyleSheet("""
                QPushButton {
                    background-color: rgba(239, 68, 68, 0.15);
                    color: #ef4444;
                    border: 1px solid rgba(239, 68, 68, 0.3);
                    border-radius: 3px;
                    font-size: 10.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(239, 68, 68, 0.3);
                }
            """)


class PasteZoneWidget(QFrame):
    """Vùng dán tự động nhận diện JSON từ Claude / ChatGPT."""
    json_recognized = Signal(dict, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(54)
        self.is_recognized = False
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self.lbl_icon = QLabel("📋")
        self.lbl_icon.setStyleSheet("font-size: 16px;")
        layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel("Bấm vào đây rồi nhấn Ctrl+V để dán nhanh JSON kịch bản")
        self.lbl_text.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(self.lbl_text, stretch=1)

        self._update_appearance()

    def _update_appearance(self):
        if self.is_recognized:
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(34, 197, 94, 0.12);
                    border: 1.5px solid #22c55e;
                    border-radius: 6px;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #141416;
                    border: 1.5px dashed #3f3f46;
                    border-radius: 6px;
                }
                QFrame:hover {
                    border-color: #8b5cf6;
                    background-color: rgba(124, 58, 237, 0.05);
                }
            """)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_V and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.paste_from_clipboard()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.paste_from_clipboard()

    def paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        ok, msg, data = parse_web_prompt_json(text)
        if ok and data:
            self.set_success(msg, data)
            self.json_recognized.emit(data, msg)
        else:
            self.set_error(msg)

    def set_success(self, summary: str, data: dict):
        self.is_recognized = True
        self.lbl_icon.setText("✓")
        self.lbl_icon.setStyleSheet("color: #4ade80; font-size: 16px; font-weight: bold;")
        self.lbl_text.setText(summary)
        self.lbl_text.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
        self._update_appearance()

    def set_error(self, err_msg: str):
        self.is_recognized = False
        self.lbl_icon.setText("✕")
        self.lbl_icon.setStyleSheet("color: #ef4444; font-size: 16px; font-weight: bold;")
        self.lbl_text.setText(f"Lỗi: {err_msg}")
        self.lbl_text.setStyleSheet("color: #f87171; font-size: 11px;")
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(239, 68, 68, 0.1);
                border: 1.5px solid #ef4444;
                border-radius: 6px;
            }
        """)


class ReviewPage(QWidget):
    """Trang Bước 3: Duyệt câu thoại & Kịch bản AI."""
    ai_run_requested = Signal()
    prompt_copied = Signal(str)
    config_changed = Signal()
    json_applied = Signal(dict, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = QSettings("ChunDVC", "ResolveFlow")
        self._review_rows: List[List[Any]] = [
            # [timecode, text, is_cut, tag]
            ["00:00:02", "Chào mừng các bạn đã quay trở lại với ChunDVC Studio", 0, "Hook"],
            ["00:00:06", "Hôm nay chúng ta sẽ cùng khám phá một tính năng rất đặc biệt", 0, ""],
            ["00:00:11", "ờ... thì là... để tôi xem lại kịch bản tí nhé", 1, "Vấp"],
            ["00:00:15", "Đó chính là hệ thống dựng tự động thông minh bằng AI", 0, ""],
            ["00:00:20", "Đó chính là hệ thống dựng tự động thông minh bằng AI", 1, "Lặp"],
            ["00:00:25", "Nó giúp rút ngắn thời gian dựng thô từ hàng tiếng xuống chỉ còn vài giây", 0, ""]
        ]
        self._row_widgets: List[ReviewRowWidget] = []
        self._pasted_data: Optional[Dict[str, Any]] = None
        self._pasted_summary: str = ""
        self._ping_worker: Optional[OllamaPingWorker] = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(18, 12, 18, 12)
        main_layout.setSpacing(16)

        # -------------------------------------------------------------
        # Cột trái: Duyệt câu thoại (flex: 6)
        # -------------------------------------------------------------
        col_left = QWidget(self)
        v_left = QVBoxLayout(col_left)
        v_left.setContentsMargins(0, 0, 0, 0)
        v_left.setSpacing(8)

        lbl_h_review = QLabel("Duyệt câu thoại")
        lbl_h_review.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        v_left.addWidget(lbl_h_review)

        # ScrollArea chứa các dòng thoại
        scroll = QScrollArea(col_left)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(4)

        for i, row in enumerate(self._review_rows):
            w = ReviewRowWidget(i, row[0], row[1], bool(row[2]), row[3], self.rows_container)
            w.toggled.connect(self._on_row_toggled)
            self.rows_layout.addWidget(w)
            self._row_widgets.append(w)

        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_container)
        v_left.addWidget(scroll, stretch=1)

        # Gợi ý phím tắt
        lbl_keys = QLabel("Ctrl+Z hoàn tác · Space nghe thử câu đang chọn")
        lbl_keys.setStyleSheet("color: #71717a; font-size: 11px;")
        v_left.addWidget(lbl_keys)

        # Mini Timeline Preview
        lbl_mtl = QLabel("Xem trước timeline:")
        lbl_mtl.setStyleSheet("color: #ffffff; font-size: 11.5px; font-weight: 600; margin-top: 2px;")
        v_left.addWidget(lbl_mtl)

        self.mini_timeline = MiniTimelineWidget(col_left)
        v_left.addWidget(self.mini_timeline)

        main_layout.addWidget(col_left, stretch=6)

        # Divider
        divider = QFrame(self)
        divider.setFixedWidth(1)
        divider.setStyleSheet("background-color: #27272a;")
        main_layout.addWidget(divider)

        # -------------------------------------------------------------
        # Cột phải: Kịch bản AI & Prompt Đạo Diễn (flex: 5)
        # -------------------------------------------------------------
        col_right = QWidget(self)
        v_right = QVBoxLayout(col_right)
        v_right.setContentsMargins(0, 0, 0, 0)
        v_right.setSpacing(10)

        lbl_h_ai = QLabel("Kịch bản AI & Prompt Đạo Diễn")
        lbl_h_ai.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        v_right.addWidget(lbl_h_ai)

        # 3 Nút chọn chế độ AI (Segmented Tabs)
        mode_btn_layout = QHBoxLayout()
        mode_btn_layout.setSpacing(4)

        self.btn_mode_web = QPushButton("📋 Prompt Web (0đ)")
        self.btn_mode_cloud = QPushButton("☁️ Cloud API")
        self.btn_mode_ollama = QPushButton("💻 Local Ollama")

        self.btn_mode_web.setCheckable(True)
        self.btn_mode_cloud.setCheckable(True)
        self.btn_mode_ollama.setCheckable(True)
        self.btn_mode_web.setChecked(True)

        mode_style = """
            QPushButton {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 6px 4px;
                font-size: 11px;
                color: #a1a1aa;
                font-weight: 500;
            }
            QPushButton:hover {
                border-color: #3f3f46;
                color: #ffffff;
            }
            QPushButton:checked {
                background-color: #8b5cf6;
                color: #ffffff;
                font-weight: bold;
                border-color: #a78bfa;
            }
        """
        self.btn_mode_web.setStyleSheet(mode_style)
        self.btn_mode_cloud.setStyleSheet(mode_style)
        self.btn_mode_ollama.setStyleSheet(mode_style)

        mode_btn_layout.addWidget(self.btn_mode_web)
        mode_btn_layout.addWidget(self.btn_mode_cloud)
        mode_btn_layout.addWidget(self.btn_mode_ollama)
        v_right.addLayout(mode_btn_layout)

        # Card container cho Stacked Pages
        ai_card = QFrame(col_right)
        ai_card.setObjectName("ai_box_frame")
        ai_card.setStyleSheet("""
            QFrame#ai_box_frame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
        """)
        v_card = QVBoxLayout(ai_card)
        v_card.setContentsMargins(12, 12, 12, 12)
        v_card.setSpacing(8)

        # Stacked Pages cho 3 chế độ
        self.ai_stack = QStackedWidget(ai_card)

        # ---------------------------------------------------------
        # Page 0: Prompt Web (0đ)
        # ---------------------------------------------------------
        p_web = QWidget()
        pw_lay = QVBoxLayout(p_web)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.setSpacing(8)

        lbl_step1 = QLabel("1. Chọn copy Prompt gửi cho ChatGPT / Claude (Miễn phí 0đ):")
        lbl_step1.setStyleSheet("color: #a1a1aa; font-size: 11.5px;")
        pw_lay.addWidget(lbl_step1)

        row_copy_btns = QHBoxLayout()
        row_copy_btns.setSpacing(6)

        self.btn_copy_video_prompt = QPushButton("📋 1. Prompt Dựng Video (V1)")
        self.btn_copy_video_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_video_prompt.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_copy_video_prompt.clicked.connect(self._copy_video_prompt)
        row_copy_btns.addWidget(self.btn_copy_video_prompt, stretch=1)

        self.btn_copy_kinetic_prompt = QPushButton("✨ 2. Prompt Chữ Kinetic (V2)")
        self.btn_copy_kinetic_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_kinetic_prompt.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
        """)
        self.btn_copy_kinetic_prompt.clicked.connect(self._copy_kinetic_prompt)
        row_copy_btns.addWidget(self.btn_copy_kinetic_prompt, stretch=1)

        pw_lay.addLayout(row_copy_btns)

        # Tương thích với alias cũ
        self.btn_copy_prompt = self.btn_copy_video_prompt

        lbl_step2 = QLabel("2. Dán mã JSON kịch bản mà AI trả về vào đây:")
        lbl_step2.setStyleSheet("color: #a1a1aa; font-size: 11.5px; margin-top: 4px;")
        pw_lay.addWidget(lbl_step2)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText("Dán JSON kịch bản vào đây (Video Plan V1 hoặc Chữ Kinetic V2)...")
        self.txt_json_input.setFixedHeight(75)
        self.txt_json_input.setStyleSheet("""
            QPlainTextEdit {
                background-color: #111113;
                border: 1px solid #27272a;
                border-radius: 6px;
                color: #22d3ee;
                font-family: monospace;
                font-size: 11px;
                padding: 6px;
            }
            QPlainTextEdit:focus {
                border-color: #8b5cf6;
            }
        """)
        self.txt_json_input.textChanged.connect(self._on_json_text_changed)
        pw_lay.addWidget(self.txt_json_input)

        # Dropzone dán nhanh
        self.paste_zone = PasteZoneWidget(p_web)
        self.paste_zone.json_recognized.connect(self._on_dropzone_json_recognized)
        pw_lay.addWidget(self.paste_zone)

        self.lbl_json_hint = QLabel("💡 Tự động nhận diện: Video Plan (cắt ghép cảnh V1) hoặc Kinetic Plan (phụ đề hoạt ảnh V2)")
        self.lbl_json_hint.setStyleSheet("color: #71717a; font-size: 10.5px;")
        self.lbl_json_hint.setWordWrap(True)
        pw_lay.addWidget(self.lbl_json_hint)

        self.btn_apply_json = QPushButton("🎬 Áp Dụng Kịch Bản Này")
        self.btn_apply_json.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_json.setStyleSheet("""
            QPushButton {
                background-color: #06b6d4;
                color: #000000;
                font-weight: bold;
                padding: 8px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #22d3ee;
            }
        """)
        self.btn_apply_json.clicked.connect(self._apply_json_script)
        pw_lay.addWidget(self.btn_apply_json)

        self.ai_stack.addWidget(p_web)

        # ---------------------------------------------------------
        # Page 1: Cloud API (Gemini / DeepSeek / Claude / OpenAI)
        # ---------------------------------------------------------
        p_cloud = QWidget()
        pc_lay = QVBoxLayout(p_cloud)
        pc_lay.setContentsMargins(0, 0, 0, 0)
        pc_lay.setSpacing(8)

        row_c1 = QHBoxLayout()
        lbl_p = QLabel("Nhà cung cấp:")
        lbl_p.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_c1.addWidget(lbl_p)

        self.combo_cloud_provider = QComboBox()
        self.combo_cloud_provider.addItem("Google Gemini (AI Studio - Tốc độ cao)", "gemini")
        self.combo_cloud_provider.addItem("DeepSeek V3 (Chi phí thấp, phản hồi nhanh)", "deepseek")
        self.combo_cloud_provider.addItem("Claude 3.7 Sonnet (Phân tích kịch bản sâu)", "claude")
        self.combo_cloud_provider.addItem("OpenAI GPT-4o (Đa dụng, ổn định)", "openai")
        self.combo_cloud_provider.setStyleSheet("""
            QComboBox {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.combo_cloud_provider.currentIndexChanged.connect(self._on_cloud_provider_changed)
        row_c1.addWidget(self.combo_cloud_provider, stretch=1)
        pc_lay.addLayout(row_c1)

        row_c2 = QHBoxLayout()
        lbl_k = QLabel("API Key:")
        lbl_k.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_c2.addWidget(lbl_k)

        self.txt_cloud_api_key = QLineEdit()
        self.txt_cloud_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_cloud_api_key.setPlaceholderText("Dán API Key vào đây (AQ... hoặc sk-...)")
        self.txt_cloud_api_key.setStyleSheet("""
            QLineEdit {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #8b5cf6;
            }
        """)
        self.txt_cloud_api_key.textChanged.connect(self._on_cloud_key_changed)
        row_c2.addWidget(self.txt_cloud_api_key, stretch=1)
        pc_lay.addLayout(row_c2)

        lbl_cloud_hint = QLabel("💡 Tự động nhận diện tiền tố: AQ./AIza (Gemini), sk-ant- (Claude), sk-proj- (OpenAI), sk- (DeepSeek). Lưu vĩnh viễn trên máy.")
        lbl_cloud_hint.setStyleSheet("color: #71717a; font-size: 10.5px;")
        lbl_cloud_hint.setWordWrap(True)
        pc_lay.addWidget(lbl_cloud_hint)

        self.lbl_cloud_status = QLabel("")
        self.lbl_cloud_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_cloud_status.setWordWrap(True)
        pc_lay.addWidget(self.lbl_cloud_status)

        self.btn_run_cloud = QPushButton("⚡ 1-Click: Cloud AI Tự Động Lên Kịch Bản && Dựng")
        self.btn_run_cloud.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_cloud.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #ffffff;
                font-weight: bold;
                padding: 8px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #7c3aed;
            }
        """)
        self.btn_run_cloud.clicked.connect(self._on_run_cloud_clicked)
        pc_lay.addWidget(self.btn_run_cloud)

        pc_lay.addStretch(1)
        self.ai_stack.addWidget(p_cloud)

        # ---------------------------------------------------------
        # Page 2: Local AI Ollama
        # ---------------------------------------------------------
        p_ollama = QWidget()
        po_lay = QVBoxLayout(p_ollama)
        po_lay.setContentsMargins(0, 0, 0, 0)
        po_lay.setSpacing(8)

        row_o1 = QHBoxLayout()
        lbl_m = QLabel("Mô hình:")
        lbl_m.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_o1.addWidget(lbl_m)

        self.combo_ollama_model = QComboBox()
        self.combo_ollama_model.setEditable(True)
        self.combo_ollama_model.addItems(["llama3:latest", "qwen2.5:7b", "mistral:latest", "deepseek-r1:8b"])
        self.combo_ollama_model.setStyleSheet("""
            QComboBox {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.combo_ollama_model.currentTextChanged.connect(self._on_ollama_model_changed)
        row_o1.addWidget(self.combo_ollama_model, stretch=1)
        po_lay.addLayout(row_o1)

        row_o2 = QHBoxLayout()
        lbl_ep = QLabel("Endpoint:")
        lbl_ep.setStyleSheet("color: #e4e4e7; font-size: 11.5px; font-weight: 500;")
        row_o2.addWidget(lbl_ep)

        self.txt_ollama_endpoint = QLineEdit("http://localhost:11434")
        self.txt_ollama_endpoint.setStyleSheet("""
            QLineEdit {
                background-color: #111113;
                border: 1px solid #27272a;
                padding: 6px;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
            }
        """)
        self.txt_ollama_endpoint.textChanged.connect(self._on_ollama_endpoint_changed)
        row_o2.addWidget(self.txt_ollama_endpoint, stretch=1)
        po_lay.addLayout(row_o2)

        row_o3 = QHBoxLayout()
        self.btn_ping_ollama = QPushButton("🔄 Kiểm tra kết nối")
        self.btn_ping_ollama.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ping_ollama.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)
        self.btn_ping_ollama.clicked.connect(self._ping_ollama)
        row_o3.addWidget(self.btn_ping_ollama)

        self.lbl_ping_status = QLabel("Chưa kiểm tra")
        self.lbl_ping_status.setStyleSheet("color: #71717a; font-size: 10.5px;")
        row_o3.addWidget(self.lbl_ping_status, stretch=1)
        po_lay.addLayout(row_o3)

        self.lbl_ollama_status = QLabel("")
        self.lbl_ollama_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_ollama_status.setWordWrap(True)
        po_lay.addWidget(self.lbl_ollama_status)

        self.btn_run_ollama = QPushButton("💻 1-Click: Local AI Tự Động Lên Kịch Bản && Dựng")
        self.btn_run_ollama.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_ollama.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #ffffff;
                font-weight: bold;
                padding: 8px;
                border-radius: 6px;
                font-size: 11.5px;
                border: none;
            }
            QPushButton:hover {
                background-color: #7c3aed;
            }
        """)
        self.btn_run_ollama.clicked.connect(self._on_run_ollama_clicked)
        po_lay.addWidget(self.btn_run_ollama)

        po_lay.addStretch(1)
        self.ai_stack.addWidget(p_ollama)

        # Kết nối 3 nút chuyển chế độ
        self.btn_mode_web.clicked.connect(lambda: self._switch_mode(0))
        self.btn_mode_cloud.clicked.connect(lambda: self._switch_mode(1))
        self.btn_mode_ollama.clicked.connect(lambda: self._switch_mode(2))

        v_card.addWidget(self.ai_stack)
        v_right.addWidget(ai_card, stretch=1)

        # Alias hỗ trợ mã cũ
        self.btn_ai_run = self.btn_run_cloud
        self.lbl_ai_res = self.lbl_cloud_status

        main_layout.addWidget(col_right, stretch=5)

        # Tải cấu hình đã lưu từ QSettings
        self._load_saved_settings()

    def _switch_mode(self, idx: int):
        self.btn_mode_web.setChecked(idx == 0)
        self.btn_mode_cloud.setChecked(idx == 1)
        self.btn_mode_ollama.setChecked(idx == 2)
        self.ai_stack.setCurrentIndex(idx)
        self.config_changed.emit()

    def _load_saved_settings(self):
        saved_key = self._settings.value("ai/api_key", "")
        if saved_key:
            self.txt_cloud_api_key.setText(str(saved_key))

        saved_prov = self._settings.value("ai/cloud_provider", "gemini")
        idx = self.combo_cloud_provider.findData(saved_prov)
        if idx >= 0:
            self.combo_cloud_provider.setCurrentIndex(idx)

        saved_model = self._settings.value("ai/ollama_model", "llama3:latest")
        if saved_model:
            self.combo_ollama_model.setCurrentText(str(saved_model))

        saved_endpoint = self._settings.value("ai/ollama_endpoint", "http://localhost:11434")
        if saved_endpoint:
            self.txt_ollama_endpoint.setText(str(saved_endpoint))

    def _on_cloud_key_changed(self, text: str):
        key = text.strip()
        self._settings.setValue("ai/api_key", key)
        # Tự nhận diện prefix
        if key.startswith("AQ.") or key.startswith("AIza"):
            idx = self.combo_cloud_provider.findData("gemini")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-ant-"):
            idx = self.combo_cloud_provider.findData("claude")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-proj-"):
            idx = self.combo_cloud_provider.findData("openai")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        elif key.startswith("sk-"):
            idx = self.combo_cloud_provider.findData("deepseek")
            if idx >= 0:
                self.combo_cloud_provider.setCurrentIndex(idx)
        self.config_changed.emit()

    def _on_cloud_provider_changed(self, idx: int):
        prov = self.combo_cloud_provider.currentData() or "gemini"
        self._settings.setValue("ai/cloud_provider", prov)
        self.config_changed.emit()

    def _on_ollama_model_changed(self, text: str):
        if text.strip():
            self._settings.setValue("ai/ollama_model", text.strip())
            self.config_changed.emit()

    def _on_ollama_endpoint_changed(self, text: str):
        if text.strip():
            self._settings.setValue("ai/ollama_endpoint", text.strip())
            self.config_changed.emit()

    def _ping_ollama(self):
        endpoint = self.txt_ollama_endpoint.text().strip() or "http://localhost:11434"
        self.lbl_ping_status.setText("Đang kiểm tra kết nối...")
        self.lbl_ping_status.setStyleSheet("color: #a78bfa; font-size: 10.5px;")

        self._ping_worker = OllamaPingWorker(endpoint, self)
        self._ping_worker.finished_ping.connect(self._on_ping_finished)
        self._ping_worker.start()

    def _on_ping_finished(self, is_online: bool, message: str, models: list):
        if is_online:
            self.lbl_ping_status.setText(f"🟢 {message}")
            self.lbl_ping_status.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
            if models:
                # Cập nhật danh sách model nếu chưa có
                current_items = [self.combo_ollama_model.itemText(i) for i in range(self.combo_ollama_model.count())]
                for m in models:
                    if m not in current_items:
                        self.combo_ollama_model.addItem(m)
        else:
            self.lbl_ping_status.setText(f"🔴 {message}")
            self.lbl_ping_status.setStyleSheet("color: #f87171; font-size: 10.5px;")

    def _extract_words_from_rows(self) -> List[Dict[str, Any]]:
        """Bóc tách từ vựng kèm timestamp từ các câu thoại chưa bị cắt."""
        words = []
        for row in self._review_rows:
            tc, text, is_cut = row[0], row[1], row[2]
            if is_cut:
                continue
            parts = tc.split(":")
            sec = 0.0
            try:
                if len(parts) == 3:
                    sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                elif len(parts) == 2:
                    sec = float(parts[0]) * 60 + float(parts[1])
            except Exception:
                sec = 0.0
            row_words = text.strip().split()
            if not row_words:
                continue
            word_dur = 0.35
            for idx, w in enumerate(row_words):
                words.append({
                    "word": w,
                    "start": round(sec + idx * word_dur, 2),
                    "end": round(sec + (idx + 1) * word_dur, 2)
                })
        return words if words else DEMO_WORDS

    def get_video_prompt(self) -> str:
        """Tạo Prompt Dựng Video & Cốt truyện (V1)."""
        lines = [f"[{r[0]}] {r[1]}" for r in self._review_rows if not r[2]]
        transcription_text = "\n".join(lines)
        if StoryCopilot:
            clips_data = [{
                "clip_index": 0,
                "clip_name": "Source_Main_Track.mp4",
                "transcription": transcription_text
            }]
            prompt = StoryCopilot.generate_copilot_prompt(
                project_name="ChunDVC_Project",
                clips_data=clips_data,
                story_intent="travel_vlog",
                target_duration="full",
                hook_duration="30_60s"
            )
            return prompt
        else:
            return (
                "Bạn là một đạo diễn dựng phim chuyên nghiệp. Dưới đây là transcript lời thoại video:\n\n"
                + transcription_text
                + "\n\nHãy phân tích và trả về một JSON định dạng Video Plan gồm { \"scenes\": [...] }."
            )

    def get_kinetic_prompt(self, aspect: str = "9:16") -> str:
        """Tạo Prompt Phụ Đề Chữ Kinetic Hoạt Ảnh (V2)."""
        words = self._extract_words_from_rows()
        if generate_kinetic_prompt:
            return generate_kinetic_prompt(words, aspect=aspect)
        else:
            return (
                "Bạn là chuyên gia thiết kế phụ đề động Kinetic Typography. Hãy tạo kịch bản JSON gồm {\"subtitles\": [...]} "
                f"với các từ sau: {json.dumps(words, ensure_ascii=False)}"
            )

    def get_active_prompt(self) -> str:
        """Lấy prompt tùy theo nhu cầu hiện tại."""
        return self.get_video_prompt()

    def _copy_video_prompt(self):
        prompt = self.get_video_prompt()
        QApplication.clipboard().setText(prompt)
        orig_text = "📋 1. Prompt Dựng Video (V1)"
        self.btn_copy_video_prompt.setText("✓ Đã sao chép Prompt V1!")
        QTimer.singleShot(2500, lambda: self.btn_copy_video_prompt.setText(orig_text))
        self.lbl_json_hint.setText("✓ Đã sao chép Prompt Dựng Video (V1) vào Clipboard! Hãy dán (Ctrl+V) vào ChatGPT hoặc Claude.")
        self.lbl_json_hint.setStyleSheet("color: #38bdf8; font-size: 10.5px;")
        self.prompt_copied.emit("video")

    def _copy_kinetic_prompt(self):
        prompt = self.get_kinetic_prompt(aspect="9:16")
        QApplication.clipboard().setText(prompt)
        orig_text = "✨ 2. Prompt Chữ Kinetic (V2)"
        self.btn_copy_kinetic_prompt.setText("✓ Đã sao chép Prompt V2!")
        QTimer.singleShot(2500, lambda: self.btn_copy_kinetic_prompt.setText(orig_text))
        self.lbl_json_hint.setText("✓ Đã sao chép Prompt Chữ Kinetic (V2) vào Clipboard! Hãy dán (Ctrl+V) vào ChatGPT hoặc Claude.")
        self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px;")
        self.prompt_copied.emit("kinetic")

    def _on_json_text_changed(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            return
        ok, msg, data = parse_web_prompt_json(text)
        if ok and data:
            self.paste_zone.set_success(msg, data)
            self.lbl_json_hint.setText(f"✓ {msg}")
            self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        else:
            self.lbl_json_hint.setText(f"⚠ {msg}")
            self.lbl_json_hint.setStyleSheet("color: #fbbf24; font-size: 10.5px;")

    def _on_dropzone_json_recognized(self, data: dict, summary: str):
        self._pasted_data = data
        self._pasted_summary = summary
        raw_dict = data.get("data", {})
        self.txt_json_input.setPlainText(json.dumps(raw_dict, indent=2, ensure_ascii=False))
        self.lbl_json_hint.setText(f"✓ {summary}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self.config_changed.emit()

    def _apply_json_script(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            # Nếu ô nhập trống, thử lấy từ clipboard
            cb_text = QApplication.clipboard().text().strip()
            if cb_text:
                text = cb_text
                self.txt_json_input.setPlainText(text)

        ok, msg, data = parse_web_prompt_json(text)
        if not ok or not data:
            self.lbl_json_hint.setText(f"✕ Không thể áp dụng: {msg}")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        self._pasted_data = data
        self._pasted_summary = msg
        self.paste_zone.set_success(msg, data)
        self.lbl_json_hint.setText(f"✓ Đã áp dụng thành công: {msg}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")

        # Nếu là Video Plan: cập nhật khối Mini Timeline
        if data.get("type") == "video_plan":
            plan_data = data.get("data", {})
            scenes = plan_data.get("scenes") or plan_data.get("timeline") or plan_data.get("segments") or []
            if scenes:
                blocks = []
                for i, sc in enumerate(scenes[:8]):
                    title = sc.get("title") or sc.get("hook_title") or sc.get("chapter_name") or f"Cảnh {i+1}"
                    color = "#7c3aed" if "hook" in str(title).lower() else ("#0284c7" if i % 2 == 0 else "#06b6d4")
                    blocks.append({"label": title, "color": color, "flex": 1})
                self.mini_timeline.set_blocks(blocks)

        self.config_changed.emit()
        self.json_applied.emit(data, msg)

    def _on_run_cloud_clicked(self):
        self.lbl_cloud_status.setText("Đang gửi transcript sang Cloud AI...")
        self.lbl_cloud_status.setStyleSheet("color: #a78bfa; font-size: 11px;")
        self.ai_run_requested.emit()

    def _on_run_ollama_clicked(self):
        self.lbl_ollama_status.setText("Đang gọi Local Ollama xử lý kịch bản...")
        self.lbl_ollama_status.setStyleSheet("color: #a78bfa; font-size: 11px;")
        self.ai_run_requested.emit()

    def set_ai_result(self, ok: bool, message: str, raw_json: str = ""):
        target_lbl = self.lbl_ollama_status if self.ai_stack.currentIndex() == 2 else self.lbl_cloud_status
        if ok:
            target_lbl.setText(message or "✓ Kịch bản AI đã hoàn thành!")
            target_lbl.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
            if raw_json and raw_json.strip():
                self.txt_json_input.setPlainText(raw_json)
                self._apply_json_script()
        else:
            target_lbl.setText(f"Lỗi: {message}")
            target_lbl.setStyleSheet("color: #ef4444; font-size: 11px;")

    def _on_row_toggled(self, idx: int):
        if 0 <= idx < len(self._review_rows):
            cur = self._review_rows[idx][2]
            new_val = 0 if cur else 1
            self._review_rows[idx][2] = new_val
            if idx < len(self._row_widgets):
                self._row_widgets[idx].set_cut(bool(new_val))
            self.config_changed.emit()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            for r in reversed(self._review_rows):
                if r[2] == 1:
                    r[2] = 0
                    break
            for i, r in enumerate(self._review_rows):
                if i < len(self._row_widgets):
                    self._row_widgets[i].set_cut(bool(r[2]))
            self.config_changed.emit()
        else:
            super().keyPressEvent(event)

    def load_from_config(self, config: DirectorConfig):
        # Chọn mode phù hợp
        if config.ai_engine_mode == "ollama":
            self._switch_mode(2)
        elif config.ai_engine_mode == "cloud":
            self._switch_mode(1)
        else:
            self._switch_mode(0)

        # Provider
        idx = self.combo_cloud_provider.findData(config.cloud_provider)
        if idx >= 0:
            self.combo_cloud_provider.setCurrentIndex(idx)

        # API key
        if config.api_key:
            self.txt_cloud_api_key.setText(config.api_key)

        # Ollama
        if config.ollama_model:
            self.combo_ollama_model.setCurrentText(config.ollama_model)
        if config.ollama_endpoint:
            self.txt_ollama_endpoint.setText(config.ollama_endpoint)

        # Pasted data
        if config.web_prompt_pasted_data:
            self._pasted_data = config.web_prompt_pasted_data
            self._pasted_summary = config.web_prompt_summary
            self.paste_zone.set_success(self._pasted_summary, self._pasted_data)
            self.txt_json_input.setPlainText(json.dumps(self._pasted_data.get("data", {}), indent=2, ensure_ascii=False))

    def save_to_config(self, config: DirectorConfig):
        curr_idx = self.ai_stack.currentIndex()
        if curr_idx == 1:
            config.ai_engine_mode = "cloud"
        elif curr_idx == 2:
            config.ai_engine_mode = "ollama"
        else:
            config.ai_engine_mode = "web"

        config.cloud_provider = self.combo_cloud_provider.currentData() or "gemini"
        config.api_key = self.txt_cloud_api_key.text().strip() or None
        config.ollama_model = self.combo_ollama_model.currentText().strip() or "llama3:latest"
        config.ollama_endpoint = self.txt_ollama_endpoint.text().strip() or "http://localhost:11434"
        config.web_prompt_pasted_data = self._pasted_data
        config.web_prompt_summary = self._pasted_summary
