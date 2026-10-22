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

import os
import json
import logging
from typing import Any, Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QApplication, QStackedWidget,
    QPlainTextEdit, QLineEdit, QComboBox, QButtonGroup,
    QMessageBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSettings
from PySide6.QtGui import QKeyEvent
from src.core.review_state import ReviewState
from src.ui.director.director_state import DirectorConfig, parse_web_prompt_json
from src.ui.director.widgets.mini_timeline_view import MiniTimelineWidget

logger = logging.getLogger(__name__)

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
    rough_scan_requested = Signal()

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
        self._video_plan_data: Optional[Dict[str, Any]] = None
        self._video_plan_summary: str = ""
        self._kinetic_plan_data: Optional[Dict[str, Any]] = None
        self._kinetic_plan_summary: str = ""
        self._clip_infos: List[Any] = []
        self._style_intent: str = "travel_vlog"
        self._hook_duration: str = "30_60s"
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

        h_review_header = QHBoxLayout()
        lbl_h_review = QLabel("Duyệt câu thoại")
        lbl_h_review.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        h_review_header.addWidget(lbl_h_review)
        h_review_header.addStretch(1)

        self.btn_review_scan = QPushButton("⚡ Quét thô Whisper")
        self.btn_review_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_review_scan.setStyleSheet("""
            QPushButton {
                background: rgba(124, 58, 237, 0.2);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.4);
                border-radius: 6px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #7c3aed;
                color: #ffffff;
            }
        """)
        self.btn_review_scan.setToolTip("Chạy bóc tách lại câu thoại và phát hiện khoảng lặng bằng Whisper")
        self.btn_review_scan.clicked.connect(self.rough_scan_requested.emit)
        h_review_header.addWidget(self.btn_review_scan)
        v_left.addLayout(h_review_header)

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

        self.btn_copy_final_transcript = QPushButton("✂ 2. Copy Transcript Bản Dựng")
        self.btn_copy_final_transcript.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_final_transcript.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                padding: 7px 8px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 11px;
                border: none;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_copy_final_transcript.setToolTip("Trích xuất chỉ riêng các câu thoại thuộc Bản Dựng Cuối (Final Cut) đã lọc sạch ảo giác, đánh số [0]..[N-1] liên tục không tốn token")
        self.btn_copy_final_transcript.clicked.connect(self._copy_final_cut_transcript)
        row_copy_btns.addWidget(self.btn_copy_final_transcript, stretch=1)

        self.btn_copy_kinetic_prompt = QPushButton("✨ 3. Prompt Chữ Kinetic (V2)")
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

        # Thẻ trạng thái 2 Kế hoạch độc lập (Dual Plan Slots V1 & V2)
        lbl_slots_head = QLabel("Trạng thái nạp 2 kịch bản (Độc lập không bị đè mất):")
        lbl_slots_head.setStyleSheet("color: #a1a1aa; font-size: 11px; margin-top: 4px; font-weight: 500;")
        pw_lay.addWidget(lbl_slots_head)

        plan_status_layout = QHBoxLayout()
        plan_status_layout.setSpacing(8)

        self.card_slot_video = QFrame()
        self.card_slot_video.setStyleSheet("""
            QFrame {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px 6px;
            }
        """)
        v_slot_vid = QVBoxLayout(self.card_slot_video)
        v_slot_vid.setContentsMargins(4, 2, 4, 2)
        v_slot_vid.setSpacing(3)

        h_slot_vid_top = QHBoxLayout()
        self.lbl_slot_vid_title = QLabel("🎬 1. Video Plan (V1)")
        self.lbl_slot_vid_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #38bdf8;")
        h_slot_vid_top.addWidget(self.lbl_slot_vid_title)
        h_slot_vid_top.addStretch(1)

        self.btn_paste_slot1 = QPushButton("📋 Dán vào Slot 1")
        self.btn_paste_slot1.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_paste_slot1.setStyleSheet("""
            QPushButton {
                background: rgba(56, 189, 248, 0.15);
                color: #38bdf8;
                border: 1px solid rgba(56, 189, 248, 0.35);
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #0284c7;
                color: #ffffff;
            }
        """)
        self.btn_paste_slot1.setToolTip("Dán trực tiếp JSON từ Clipboard vào riêng Slot 1 (Video Plan)")
        self.btn_paste_slot1.clicked.connect(lambda: self._paste_to_slot("slot1"))
        h_slot_vid_top.addWidget(self.btn_paste_slot1)
        v_slot_vid.addLayout(h_slot_vid_top)

        self.lbl_slot_vid_status = QLabel("Chưa nạp (Dùng cắt thô)")
        self.lbl_slot_vid_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_vid.addWidget(self.lbl_slot_vid_status)
        plan_status_layout.addWidget(self.card_slot_video, stretch=1)

        self.card_slot_kinetic = QFrame()
        self.card_slot_kinetic.setStyleSheet("""
            QFrame {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 4px 6px;
            }
        """)
        v_slot_kin = QVBoxLayout(self.card_slot_kinetic)
        v_slot_kin.setContentsMargins(4, 2, 4, 2)
        v_slot_kin.setSpacing(3)

        h_slot_kin_top = QHBoxLayout()
        self.lbl_slot_kin_title = QLabel("✨ 2. Kinetic Plan (V2)")
        self.lbl_slot_kin_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #a78bfa;")
        h_slot_kin_top.addWidget(self.lbl_slot_kin_title)
        h_slot_kin_top.addStretch(1)

        self.btn_auto_kinetic = QPushButton("⚡ Tự tạo (0đ Token)")
        self.btn_auto_kinetic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_auto_kinetic.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #ec4899);
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c3aed, stop:1 #db2777);
            }
        """)
        self.btn_auto_kinetic.setToolTip("Tự động ngắt trang, căn thời gian và highlight từ khóa hoàn toàn Offline (0.05s, không tốn token)")
        self.btn_auto_kinetic.clicked.connect(self._auto_generate_kinetic_plan)
        h_slot_kin_top.addWidget(self.btn_auto_kinetic)

        self.btn_paste_slot2 = QPushButton("✨ Dán vào Slot 2")
        self.btn_paste_slot2.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_paste_slot2.setStyleSheet("""
            QPushButton {
                background: rgba(167, 139, 250, 0.15);
                color: #a78bfa;
                border: 1px solid rgba(167, 139, 250, 0.35);
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #7c3aed;
                color: #ffffff;
            }
        """)
        self.btn_paste_slot2.setToolTip("Dán trực tiếp JSON từ Clipboard vào riêng Slot 2 (Kinetic Plan)")
        self.btn_paste_slot2.clicked.connect(lambda: self._paste_to_slot("slot2"))
        h_slot_kin_top.addWidget(self.btn_paste_slot2)
        v_slot_kin.addLayout(h_slot_kin_top)

        self.lbl_slot_kin_status = QLabel("Chưa nạp (Dùng phụ đề)")
        self.lbl_slot_kin_status.setStyleSheet("color: #71717a; font-size: 10px;")
        v_slot_kin.addWidget(self.lbl_slot_kin_status)
        plan_status_layout.addWidget(self.card_slot_kinetic, stretch=1)

        pw_lay.addLayout(plan_status_layout)

        # Header bước 2 + Bộ chọn Slot mục tiêu
        row_step2_header = QHBoxLayout()
        row_step2_header.setSpacing(6)

        lbl_step2 = QLabel("2. Dán mã JSON (Dán lần lượt hoặc dán cả 2 cùng lúc):")
        lbl_step2.setStyleSheet("color: #a1a1aa; font-size: 11px; margin-top: 2px;")
        row_step2_header.addWidget(lbl_step2)
        row_step2_header.addStretch(1)

        self.btn_target_auto = QPushButton("🤖 Tự động")
        self.btn_target_slot1 = QPushButton("🎬 Slot 1")
        self.btn_target_slot2 = QPushButton("✨ Slot 2")
        self.target_btn_group = QButtonGroup(self)
        self.target_btn_group.addButton(self.btn_target_auto, 0)
        self.target_btn_group.addButton(self.btn_target_slot1, 1)
        self.target_btn_group.addButton(self.btn_target_slot2, 2)

        target_btn_style = """
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 10px;
                color: #a1a1aa;
            }
            QPushButton:hover {
                color: #ffffff;
                border-color: #3f3f46;
            }
            QPushButton:checked {
                background: #27272a;
                color: #ffffff;
                border-color: #8b5cf6;
                font-weight: bold;
            }
        """
        for b in [self.btn_target_auto, self.btn_target_slot1, self.btn_target_slot2]:
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(target_btn_style)
            row_step2_header.addWidget(b)
        self.btn_target_auto.setChecked(True)
        self.target_btn_group.idClicked.connect(self._on_target_slot_changed)

        pw_lay.addLayout(row_step2_header)

        self.txt_json_input = QPlainTextEdit()
        self.txt_json_input.setPlaceholderText("Dán JSON kịch bản vào đây (Dán từng JSON hoặc dán cả 2 cùng lúc — Hệ thống nạp độc lập từng Slot không sợ đè)...")
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

        self.lbl_json_hint = QLabel("💡 Bạn có thể dán lần lượt từng JSON hoặc dán cả 2 cùng lúc — Hệ thống nạp độc lập từng Slot.")
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

    def set_review_rows(self, rows: List[List[Any]]):
        """Cập nhật toàn bộ danh sách dòng thoại và làm mới giao diện."""
        if not rows:
            return
        self._review_rows = list(rows)
        while self.rows_layout.count() > 1:
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_widgets.clear()

        for i, row in enumerate(self._review_rows):
            tag = row[3] if len(row) > 3 else ""
            w = ReviewRowWidget(i, row[0], row[1], bool(row[2]), tag, self.rows_container)
            w.toggled.connect(self._on_row_toggled)
            self.rows_layout.insertWidget(i, w)
            self._row_widgets.append(w)
            if i % 30 == 0:
                QApplication.processEvents()

        # Cập nhật Mini Timeline
        blocks = []
        for i, r in enumerate(self._review_rows[:12]):
            lbl = f"Đoạn {i+1}"
            color = "#ef4444" if r[2] else ("#8b5cf6" if "hook" in str(r[3] if len(r) > 3 else "").lower() else "#22c55e")
            blocks.append((lbl, 10.0, color))
        if blocks:
            self.mini_timeline.set_segments(blocks)

    def set_story_metadata(self, style_intent: str = "travel_vlog", hook_dur: str = "30_60s"):
        """Cập nhật thể loại kịch bản và thời lượng Hook mong muốn."""
        self._style_intent = style_intent or "travel_vlog"
        self._hook_duration = hook_dur or "30_60s"

    def load_transcripts_from_cache(self, clip_infos: List[Any], whisper_model: str = "small", whisper_lang: str = "vi"):
        """Nạp các câu thoại thực tế từ bộ nhớ đệm ScanCacheManager đã lọc nhiễu và sắp xếp theo thời gian."""
        from src.core.chronological_sorter import sort_clips_chronologically
        from src.core.whisper_cleaner import clean_whisper_segments
        self._clip_infos = sort_clips_chronologically(list(clip_infos or []))
        self._whisper_model = whisper_model
        self._whisper_lang = whisper_lang
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()
        rows = []
        for c in self._clip_infos:
            cpath = getattr(c, "path", c) if not isinstance(c, str) else c
            cached = cache_mgr.get_cached_scan(cpath, whisper_model, whisper_lang)
            if cached and cached.get("raw_subtitles"):
                clean_subs = clean_whisper_segments(cached["raw_subtitles"])
                for sub in clean_subs:
                    s = float(sub.get("start", 0.0))
                    h = int(s // 3600)
                    m = int((s % 3600) // 60)
                    sec = int(s % 60)
                    tc = f"{h:02d}:{m:02d}:{sec:02d}"
                    txt = sub.get("text", "").strip()
                    if txt:
                        rows.append([tc, txt, 0, ""])
        if rows:
            self.set_review_rows(rows)

    def _extract_words_from_rows(self) -> List[Dict[str, Any]]:
        """Bóc tách từ vựng kèm timestamp từ cache Whisper thực tế hoặc review rows."""
        from src.core.cache_manager import ScanCacheManager
        from src.core.chronological_sorter import sort_clips_chronologically
        from src.core.whisper_cleaner import clean_whisper_segments
        cache_mgr = ScanCacheManager()
        w_model = getattr(self, "_whisper_model", "small")
        w_lang = getattr(self, "_whisper_lang", "vi")

        words = []
        if self._clip_infos:
            timeline_cursor = 0.0
            sorted_clips = sort_clips_chronologically(self._clip_infos)
            for c in sorted_clips:
                cpath = getattr(c, "path", c) if not isinstance(c, str) else c
                dur = float(getattr(c, "duration_sec", 0.0) or 0.0)
                cached = cache_mgr.get_cached_scan(cpath, w_model, w_lang)
                if cached:
                    if dur <= 0:
                        dur = float(cached.get("clip_dur") or cached.get("duration") or 0.0)
                    clean_subs = clean_whisper_segments(cached.get("raw_subtitles", []))
                    for sub in clean_subs:
                        sub_words = sub.get("words", [])
                        if sub_words:
                            for w in sub_words:
                                w_txt = str(w.get("word", "")).strip()
                                if w_txt:
                                    words.append({
                                        "word": w_txt,
                                        "start": round(timeline_cursor + float(w.get("start", 0.0)), 2),
                                        "end": round(timeline_cursor + float(w.get("end", 0.0)), 2)
                                    })
                        else:
                            st = float(sub.get("start", 0.0))
                            txt_words = str(sub.get("text", "")).strip().split()
                            word_dur = 0.35
                            for idx, w_txt in enumerate(txt_words):
                                words.append({
                                    "word": w_txt,
                                    "start": round(timeline_cursor + st + idx * word_dur, 2),
                                    "end": round(timeline_cursor + st + (idx + 1) * word_dur, 2)
                                })
                timeline_cursor += (dur if dur > 0 else 60.0)

        if words:
            return words

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

    def get_final_cut_words(self) -> List[Dict[str, Any]]:
        """
        Bóc tách danh sách từ ngữ thực tế thuộc bản dựng cuối (Final Cut).
        - Nếu đã nạp Video Plan (Slot 1), chỉ lấy lời thoại nằm trong các đoạn cắt được chọn
          (global_hook + timeline_segments), căn mốc thời gian tịnh tiến liên tục trên timeline cuối.
        - Nếu chưa có Video Plan, lấy lời thoại từ các dòng review không bị loại bỏ (is_cut == 0).
        - Toàn bộ từ ngữ đều được lọc sạch ảo giác Whisper, ký tự ngoại ngữ CJK/Nga và khử lặp stutter.
        """
        import os
        from src.core.cache_manager import ScanCacheManager
        from src.core.chronological_sorter import sort_clips_chronologically
        from src.core.whisper_cleaner import clean_whisper_segments, has_foreign_script, is_noise_chatter

        cache_mgr = ScanCacheManager()
        w_model = getattr(self, "_whisper_model", "small")
        w_lang = getattr(self, "_whisper_lang", "vi")

        # 1. Kiểm tra xem đã nạp Video Plan (Slot 1) chưa
        v_plan = getattr(self, "_video_plan_data", None)
        has_v_plan = bool(v_plan and isinstance(v_plan, dict) and (
            v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments")
        ))

        if has_v_plan and self._clip_infos:
            from src.core.timeline_mapper import TimelineMapper, add_subtitles_for_range
            from src.core.autocut import get_media_metadata

            sorted_clips = sort_clips_chronologically(self._clip_infos)
            video_map = {}
            for idx, c in enumerate(sorted_clips):
                cpath = getattr(c, "path", c) if not isinstance(c, str) else c
                base = os.path.basename(cpath).lower()
                video_map[base] = (idx + 1, cpath, c)
                video_map[str(idx + 1)] = (idx + 1, cpath, c)

            real_fps = 30.0
            first_c = sorted_clips[0]
            first_p = getattr(first_c, "path", first_c) if not isinstance(first_c, str) else first_c
            try:
                m_fps = get_media_metadata(first_p).get("fps")
                if m_fps and float(m_fps) > 0:
                    real_fps = float(m_fps)
            except Exception as e:
                logger.debug(f"Không thể đọc fps từ {first_p}: {e}")

            mapper = TimelineMapper(fps=real_fps)

            # Thu thập danh sách các đoạn cắt theo thứ tự timeline và nạp vào TimelineMapper
            gh = v_plan.get("global_hook")
            if gh and isinstance(gh, dict):
                ref = str(gh.get("clip_name") or gh.get("clip_index") or "").lower()
                st = float(gh.get("start_sec", 0.0) or 0.0)
                en = float(gh.get("end_sec", 0.0) or 0.0)
                if en > st and ref:
                    match = video_map.get(ref) or video_map.get(os.path.basename(ref).lower())
                    if not match:
                        for k, v in video_map.items():
                            if ref in k:
                                match = v
                                break
                    if match:
                        _, cpath, cinfo = match
                        meta = get_media_metadata(cpath)
                        max_dur = meta.get("duration", 0.0) or float(getattr(cinfo, "duration_sec", 0.0) or 0.0) or 3600.0
                        mapper.add_segment(
                            clip_id=str(gh.get("clip_id") or f"C{gh.get('clip_index', 1):02d}"),
                            src_in_sec=st,
                            src_out_sec=en,
                            speed=1.0,
                            role="hook",
                            pad_sec=0.3,
                            clip_duration_sec=max_dur,
                            clip_name=os.path.basename(cpath),
                            file_path=cpath,
                            title="Intro Teaser"
                        )

            segs = v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments") or []
            for s in segs:
                ref = str(s.get("clip_name") or s.get("clip_index") or "").lower()
                st = float(s.get("start_sec", 0.0) or 0.0)
                en = float(s.get("end_sec", 0.0) or 0.0)
                spd = float(s.get("speed", 1.0) or 1.0)
                if en > st and ref:
                    match = video_map.get(ref) or video_map.get(os.path.basename(ref).lower())
                    if not match:
                        for k, v in video_map.items():
                            if ref in k:
                                match = v
                                break
                    if match:
                        _, cpath, cinfo = match
                        meta = get_media_metadata(cpath)
                        max_dur = meta.get("duration", 0.0) or float(getattr(cinfo, "duration_sec", 0.0) or 0.0) or 3600.0
                        mapper.add_segment(
                            clip_id=str(s.get("clip_id") or f"C{s.get('clip_index', 1):02d}"),
                            src_in_sec=st,
                            src_out_sec=en,
                            speed=spd,
                            role=s.get("role", "story"),
                            pad_sec=0.3,
                            clip_duration_sec=max_dur,
                            clip_name=os.path.basename(cpath),
                            file_path=cpath,
                            title=s.get("chapter_name", "") or os.path.basename(cpath)
                        )

            final_words = []
            for seg_idx, placed in enumerate(mapper.segments):
                cached = cache_mgr.get_cached_scan(placed.file_path, w_model, w_lang)
                if cached:
                    clean_subs = clean_whisper_segments(cached.get("raw_subtitles", []))
                    mapped_subs = add_subtitles_for_range(mapper, seg_idx, clean_subs, vpath=placed.file_path)
                    for sub in mapped_subs:
                        for w in sub.get("words", []):
                            w_txt = str(w.get("word", "")).strip()
                            if w_txt and not has_foreign_script(w_txt) and not is_noise_chatter(w_txt):
                                final_words.append({
                                    "word": w_txt,
                                    "start": w["start"],
                                    "end": w["end"]
                                })

            if final_words:
                return final_words


        # 2. Fallback nếu chưa có Video Plan: Lấy từ các dòng review được giữ lại
        return self._extract_words_from_rows()

    def export_final_cut_transcript(self) -> str:
        """Xuất chuỗi transcript định dạng [0] Từ (start - end) [1] ... của Bản Dựng Cuối."""
        words = self.get_final_cut_words()
        if not words:
            return ""
        return " ".join([f"[{i}] {w['word']} ({w['start']:.2f}s - {w['end']:.2f}s)" for i, w in enumerate(words)])

    def _copy_final_cut_transcript(self):
        """Sao chép toàn bộ lời thoại Bản Dựng Cuối (đã lọc sạch, đánh số liên tục) vào Clipboard."""
        transcript_str = self.export_final_cut_transcript()
        words = self.get_final_cut_words()
        if not transcript_str or not words:
            self.lbl_json_hint.setText("⚠ Không có câu thoại nào trong bản dựng để trích xuất transcript.")
            self.lbl_json_hint.setStyleSheet("color: #fbbf24; font-size: 10.5px;")
            return

        QApplication.clipboard().setText(transcript_str)
        orig_text = self.btn_copy_final_transcript.text()
        self.btn_copy_final_transcript.setText(f"✓ Đã copy {len(words)} từ!")
        QTimer.singleShot(2500, lambda: self.btn_copy_final_transcript.setText(orig_text))

        has_vplan = bool(getattr(self, "_video_plan_data", None))
        plan_desc = "bản dựng cắt tinh (Final Cut)" if has_vplan else "các đoạn thoại được chọn"
        self.lbl_json_hint.setText(f"✓ Đã copy {len(words)} từ thoại của {plan_desc} vào Clipboard! Bạn có thể dán vào Prompt V2 để AI tạo Kinetic đỉnh cao không tốn token.")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")

    def _auto_generate_kinetic_plan(self):
        """Tự động sinh kịch bản Kinetic Typography chuẩn xác 100% liên tục bằng auto_plan offline."""
        words = self.get_final_cut_words()
        if not words:
            self.lbl_slot_kin_status.setText("⚠ Không có từ ngữ nào để tạo Kinetic")
            self.lbl_slot_kin_status.setStyleSheet("color: #f59e0b; font-size: 10px;")
            return

        try:
            from src.core.kinetic.kinetic_engine import auto_plan, validate_plan
            plan = auto_plan(words, aspect="9:16", max_words=3, gap_s=0.45, emphasis_ratio=0.12)
            validate_plan(plan, len(words))

            has_vplan = bool(getattr(self, "_video_plan_data", None))
            plan_label = "Bản Dựng Cuối" if has_vplan else "Video"
            summary = f"Tự động tạo {len(plan['pages'])} trang Kinetic Typography cho {plan_label} (100% liên tục, 0đ Token)"
            wrapped = {
                "type": "kinetic_plan",
                "count": len(plan["pages"]),
                "is_offline": True,
                "data": plan
            }
            self._apply_json_data(wrapped, summary)
            self.lbl_json_hint.setText(f"✓ Đã tự động tạo {len(plan['pages'])} trang phụ đề Kinetic cho {plan_label} chuẩn xác 100% (Offline - 0đ Token)!")
            self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px; font-weight: bold;")
        except Exception as e:
            self.lbl_slot_kin_status.setText(f"⚠ Lỗi tạo kinetic: {str(e)[:40]}")
            self.lbl_slot_kin_status.setStyleSheet("color: #ef4444; font-size: 10px;")

    def get_video_prompt(self) -> str:
        """Tạo Prompt Dựng Video & Cốt truyện (V1) với dữ liệu clip thực tế."""
        lines = [f"[{r[0]}] {r[1]}" for r in self._review_rows if not r[2]]
        transcription_text = "\n".join(lines)
        if StoryCopilot:
            from src.core.cache_manager import ScanCacheManager
            from src.core.clip_manifest import ClipManifest, build_clips_data
            cache_mgr = ScanCacheManager()
            w_model = getattr(self, "_whisper_model", "small")
            w_lang = getattr(self, "_whisper_lang", "vi")

            manifest_id = None
            if self._clip_infos:
                raw_paths = [getattr(c, "path", c) if not isinstance(c, str) else c for c in self._clip_infos]
                manifest = ClipManifest.get_or_create(raw_paths, project_name="ChunDVC_Project")
                self._manifest = manifest
                manifest_id = manifest.manifest_id
                clips_data = build_clips_data(manifest, cache_mgr=cache_mgr, model=w_model, lang=w_lang)
            else:
                clips_data = [{
                    "clip_id": "C01",
                    "clip_index": 1,
                    "id": 1,
                    "name": "Source_Main_Track.mp4",
                    "clip_name": "Source_Main_Track.mp4",
                    "chapter": "Chung",
                    "duration": 120.0,
                    "transcript": transcription_text,
                    "is_broll": False,
                    "dialogue_intervals": []
                }]

            prompt = StoryCopilot.generate_copilot_prompt(
                project_name="ChunDVC_Project",
                clips_data=clips_data,
                story_intent=self._style_intent,
                target_duration="full",
                hook_duration=self._hook_duration,
                manifest_id=manifest_id
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
        words = self.get_final_cut_words()
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
        orig_text = "✨ 3. Prompt Chữ Kinetic (V2)"
        self.btn_copy_kinetic_prompt.setText("✓ Đã sao chép Prompt V2!")
        QTimer.singleShot(2500, lambda: self.btn_copy_kinetic_prompt.setText(orig_text))
        self.lbl_json_hint.setText("✓ Đã sao chép Prompt Chữ Kinetic (V2) vào Clipboard! Hãy dán (Ctrl+V) vào ChatGPT hoặc Claude.")
        self.lbl_json_hint.setStyleSheet("color: #a78bfa; font-size: 10.5px;")
        self.prompt_copied.emit("kinetic")

    def _get_current_target_slot(self) -> Optional[str]:
        if hasattr(self, "btn_target_slot1") and self.btn_target_slot1.isChecked():
            return "slot1"
        if hasattr(self, "btn_target_slot2") and self.btn_target_slot2.isChecked():
            return "slot2"
        return None

    def _on_target_slot_changed(self, btn_id: int):
        if self.txt_json_input.toPlainText().strip():
            self._on_json_text_changed()

    def _paste_to_slot(self, slot: str):
        """Dán trực tiếp nội dung từ Clipboard vào riêng một Slot (Slot 1 hoặc Slot 2)."""
        cb_text = QApplication.clipboard().text().strip()
        if not cb_text:
            self.lbl_json_hint.setText("⚠ Clipboard rỗng. Hãy copy mã JSON từ ChatGPT / Claude trước.")
            self.lbl_json_hint.setStyleSheet("color: #fbbf24; font-size: 10.5px;")
            return

        ok, msg, data = parse_web_prompt_json(cb_text, target_slot=slot)
        if not ok or not data:
            self.lbl_json_hint.setText(f"✕ Không thể nạp vào {slot.upper()}: {msg}")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        self.txt_json_input.setPlainText(cb_text)
        self.paste_zone.set_success(msg, data)
        self.lbl_json_hint.setText(f"✓ Đã nạp thành công vào {slot.upper()}: {msg}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self._apply_json_data(data, msg)

    def _on_json_text_changed(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            return
        slot = self._get_current_target_slot()
        ok, msg, data = parse_web_prompt_json(text, target_slot=slot)
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
        self._apply_json_data(data, summary)

    def _apply_json_script(self):
        text = self.txt_json_input.toPlainText().strip()
        if not text:
            # Nếu ô nhập trống, thử lấy từ clipboard
            cb_text = QApplication.clipboard().text().strip()
            if cb_text:
                text = cb_text
                self.txt_json_input.setPlainText(text)

        slot = self._get_current_target_slot()
        ok, msg, data = parse_web_prompt_json(text, target_slot=slot)
        if not ok or not data:
            self.lbl_json_hint.setText(f"✕ Không thể áp dụng: {msg}")
            self.lbl_json_hint.setStyleSheet("color: #ef4444; font-size: 10.5px;")
            return

        self._pasted_data = data
        self._pasted_summary = msg
        self.paste_zone.set_success(msg, data)
        self.lbl_json_hint.setText(f"✓ Đã áp dụng thành công: {msg}")
        self.lbl_json_hint.setStyleSheet("color: #4ade80; font-size: 10.5px; font-weight: bold;")
        self._apply_json_data(data, msg)

    def _apply_json_data(self, data: dict, summary: str):
        """Phân loại và nạp độc lập vào Slot 1 (Video Plan) hoặc Slot 2 (Kinetic Plan) hoặc CẢ HAI (dual_plan)."""
        ptype = data.get("type", "generic_plan")
        raw_plan = data.get("data", {})

        # Kiểm tra manifest_id đối soát an toàn
        target_dict = data.get("video_plan", {}) if ptype == "dual_plan" else (raw_plan if isinstance(raw_plan, dict) and raw_plan else data)
        plan_manifest_id = target_dict.get("manifest_id") if isinstance(target_dict, dict) else None

        # Khởi tạo manifest nếu chưa có
        if not getattr(self, "_manifest", None) and getattr(self, "_clip_infos", None):
            try:
                from src.core.clip_manifest import ClipManifest
                raw_paths = [getattr(c, "path", c) if not isinstance(c, str) else c for c in self._clip_infos]
                self._manifest = ClipManifest.get_or_create(raw_paths, project_name="ChunDVC_Project")
            except Exception as e:
                logger.warning(f"Không thể khởi tạo ClipManifest trong _apply_json_data: {e}")

        if getattr(self, "_manifest", None) and ptype in ("video_plan", "dual_plan", "generic_plan"):
            curr_id = self._manifest.manifest_id
            if plan_manifest_id and plan_manifest_id != curr_id:
                reply = QMessageBox.critical(
                    self,
                    "Lệch Manifest ID - Nguy cơ dựng sai clip!",
                    f"⚠ CẢNH BÁO: Kịch bản AI được sinh từ danh sách clip khác!\n\n"
                    f"- Manifest ID trong kịch bản: {plan_manifest_id}\n"
                    f"- Manifest ID dự án hiện tại: {curr_id}\n\n"
                    f"Nếu tiếp tục, DaVinci Resolve sẽ cắt nhầm clip. Bạn có muốn tạo lại Prompt mới theo danh sách clip hiện tại không?",
                    QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel
                )
                if reply == QMessageBox.StandardButton.Retry:
                    self._copy_video_prompt()
                return
            elif not plan_manifest_id:
                logger.warning("Kịch bản cũ không có manifest_id, có thể dựng nhầm clip nếu thứ tự file đổi.")
                self.lbl_json_hint.setText("⚠ Cảnh báo: Kịch bản cũ không có manifest_id, có thể lệch clip.")
                self.lbl_json_hint.setStyleSheet("color: #eab308; font-size: 10.5px; font-weight: bold;")

        if ptype == "dual_plan":
            v_plan = data.get("video_plan", {})
            k_plan = data.get("kinetic_plan", {})
            v_sum = data.get("video_summary", "✓ Đã nạp Video Plan (V1)")
            k_sum = data.get("kinetic_summary", "✓ Đã nạp Kinetic Plan (V2)")

            self._video_plan_data = v_plan
            self._video_plan_summary = v_sum
            self.lbl_slot_vid_status.setText(v_sum)
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 6px;")

            self._kinetic_plan_data = k_plan
            self._kinetic_plan_summary = k_sum
            self.lbl_slot_kin_status.setText(k_sum)
            self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_kinetic.setStyleSheet("background: rgba(167, 139, 250, 0.12); border: 1px solid #a78bfa; border-radius: 6px; padding: 4px 6px;")

            scenes = v_plan.get("timeline_segments") or v_plan.get("scenes") or v_plan.get("timeline") or v_plan.get("segments") or []
            if scenes:
                blocks = []
                for i, sc in enumerate(scenes[:8]):
                    title = sc.get("title") or sc.get("hook_title") or sc.get("chapter_name") or f"Cảnh {i+1}"
                    color = "#8b5cf6" if "hook" in str(title).lower() else ("#0284c7" if i % 2 == 0 else "#06b6d4")
                    blocks.append({"label": title, "color": color, "flex": 1})
                self.mini_timeline.set_blocks(blocks)

        elif ptype == "video_plan":
            self._video_plan_data = raw_plan
            self._video_plan_summary = summary
            cnt = data.get("count", 0)
            br_cnt = data.get("broll_count", 0)
            sfx_cnt = data.get("sfx_count", 0)
            self.lbl_slot_vid_status.setText(f"✓ Đã nạp ({cnt} cảnh · {br_cnt} meme · {sfx_cnt} sfx)")
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 6px;")

            scenes = raw_plan.get("timeline_segments") or raw_plan.get("scenes") or raw_plan.get("timeline") or raw_plan.get("segments") or []
            if scenes:
                blocks = []
                for i, sc in enumerate(scenes[:8]):
                    title = sc.get("title") or sc.get("hook_title") or sc.get("chapter_name") or f"Cảnh {i+1}"
                    color = "#8b5cf6" if "hook" in str(title).lower() else ("#0284c7" if i % 2 == 0 else "#06b6d4")
                    blocks.append({"label": title, "color": color, "flex": 1})
                self.mini_timeline.set_blocks(blocks)

        elif ptype == "kinetic_plan":
            self._kinetic_plan_data = raw_plan
            self._kinetic_plan_summary = summary
            cnt = data.get("count", 0)
            if "0đ token" in summary.lower() or data.get("is_offline"):
                self.lbl_slot_kin_status.setText(f"✓ Đã nạp ({cnt} trang Kinetic · 0đ Token)")
            else:
                self.lbl_slot_kin_status.setText(f"✓ Đã nạp ({cnt} phụ đề động)")
            self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.card_slot_kinetic.setStyleSheet("background: rgba(167, 139, 250, 0.12); border: 1px solid #a78bfa; border-radius: 6px; padding: 4px 6px;")

        else:
            self._video_plan_data = raw_plan
            self._video_plan_summary = summary
            self.lbl_slot_vid_status.setText("✓ Đã nạp kịch bản tùy biến")
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")

        self.config_changed.emit()
        self.json_applied.emit(data, summary)

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

        # Khôi phục Dual Plan slots nếu có sẵn trong config
        if getattr(config, "video_plan_data", None):
            self._video_plan_data = config.video_plan_data
            self._video_plan_summary = getattr(config, "video_plan_summary", "") or "Kịch bản mạch video & B-Roll / SFX"
            self.card_slot_video.setStyleSheet("background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; padding: 4px 8px;")
            self.lbl_slot_vid_status.setText(f"✓ Đã nạp: {self._video_plan_summary}")
            self.lbl_slot_vid_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")

        if getattr(config, "kinetic_plan_data", None):
            self._kinetic_plan_data = config.kinetic_plan_data
            self._kinetic_plan_summary = getattr(config, "kinetic_plan_summary", "") or "Kịch bản phụ đề Kinetic Typography"
            self.card_slot_kinetic.setStyleSheet("background: rgba(167, 139, 250, 0.12); border: 1px solid #a78bfa; border-radius: 6px; padding: 4px 8px;")
            self.lbl_slot_kin_status.setText(f"✓ Đã nạp: {self._kinetic_plan_summary}")
            self.lbl_slot_kin_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")

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
        config.video_plan_data = self._video_plan_data
        config.video_plan_summary = self._video_plan_summary
        config.kinetic_plan_data = self._kinetic_plan_data
        config.kinetic_plan_summary = self._kinetic_plan_summary
