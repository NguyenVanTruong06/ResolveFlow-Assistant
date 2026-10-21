"""
src/ui/director/pages/review_page.py
Bước 3: Duyệt Câu Thoại & Kịch Bản AI.
Bao gồm:
- Cột 1: Danh sách câu thoại Giữ/Cắt kết nối ReviewState (Ctrl+Z hoàn tác), Mini Timeline màu.
- Cột 2: Khối Kịch bản AI (Gemini/Ollama) & Phần mở rộng Web Prompt 0đ (Sao chép + Dán tự động nhận diện).
"""

from typing import Any, Dict, List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QApplication
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from src.core.review_state import ReviewState
from src.ui.director.director_state import DirectorConfig, parse_web_prompt_json
from src.ui.director.widgets.mini_timeline_view import MiniTimelineWidget


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
        self.setFixedHeight(72)
        self.is_recognized = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(4)

        self.lbl_icon = QLabel("📋")
        self.lbl_icon.setStyleSheet("font-size: 18px;")
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_icon)

        self.lbl_text = QLabel("2. Dán kết quả từ ChatGPT/Claude: Bấm vào đây rồi bấm Ctrl+V.")
        self.lbl_text.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_text)

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
                    background-color: #18181b;
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
        self.lbl_icon.setStyleSheet("color: #4ade80; font-size: 18px; font-weight: bold;")
        self.lbl_text.setText(summary)
        self.lbl_text.setStyleSheet("color: #4ade80; font-size: 11.5px; font-weight: bold;")
        self._update_appearance()

    def set_error(self, err_msg: str):
        self.is_recognized = False
        self.lbl_icon.setText("✕")
        self.lbl_icon.setStyleSheet("color: #ef4444; font-size: 18px; font-weight: bold;")
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
    prompt_copied = Signal()
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self._web_prompt_visible = True
        self._pasted_data: Optional[Dict[str, Any]] = None
        self._pasted_summary: str = ""
        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 14, 20, 14)
        main_layout.setSpacing(16)

        # -------------------------------------------------------------
        # Cột trái: Duyệt câu thoại
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
        lbl_mtl.setStyleSheet("color: #ffffff; font-size: 11.5px; font-weight: 600; margin-top: 4px;")
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
        # Cột phải: Kịch bản AI
        # -------------------------------------------------------------
        col_right = QWidget(self)
        v_right = QVBoxLayout(col_right)
        v_right.setContentsMargins(0, 0, 0, 0)
        v_right.setSpacing(10)

        lbl_h_ai = QLabel("Kịch bản AI")
        lbl_h_ai.setStyleSheet("color: #ffffff; font-size: 13.5px; font-weight: bold;")
        v_right.addWidget(lbl_h_ai)

        # Card container cho AI
        ai_card = QFrame(col_right)
        ai_card.setStyleSheet("""
            QFrame#ai_box_frame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 8px;
            }
        """)
        ai_card.setObjectName("ai_box_frame")
        v_ai = QVBoxLayout(ai_card)
        v_ai.setContentsMargins(12, 12, 12, 12)
        v_ai.setSpacing(10)

        lbl_engine = QLabel("Engine: Gemini (Cloud) · đổi trong Cài đặt")
        lbl_engine.setStyleSheet("color: #71717a; font-size: 11px;")
        v_ai.addWidget(lbl_engine)

        self.btn_ai_run = QPushButton("⚡ Lên kịch bản AI", ai_card)
        self.btn_ai_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ai_run.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #c4b5fd;
                border: 1px solid #8b5cf6;
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(124, 58, 237, 0.15);
                color: #ffffff;
            }
        """)
        self.btn_ai_run.clicked.connect(self._on_ai_run_clicked)
        v_ai.addWidget(self.btn_ai_run)

        self.lbl_ai_res = QLabel("")
        self.lbl_ai_res.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_ai_res.setWordWrap(True)
        v_ai.addWidget(self.lbl_ai_res)

        # Nút chuyển Web Prompt
        self.btn_web_toggle = QPushButton("Dùng Web Prompt miễn phí ▾", ai_card)
        self.btn_web_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_web_toggle.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a78bfa;
                border: none;
                font-size: 11.5px;
                font-weight: 600;
                text-align: left;
                padding: 4px 0;
            }
            QPushButton:hover {
                color: #c4b5fd;
            }
        """)
        self.btn_web_toggle.clicked.connect(self._toggle_web_prompt)
        v_ai.addWidget(self.btn_web_toggle)

        # Khối Web Prompt
        self.web_box = QWidget(ai_card)
        v_web = QVBoxLayout(self.web_box)
        v_web.setContentsMargins(0, 0, 0, 0)
        v_web.setSpacing(8)

        self.btn_copy_prompt = QPushButton("1. Sao chép prompt (kèm transcript)", self.web_box)
        self.btn_copy_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy_prompt.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 8px;
                font-size: 11.5px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #ffffff;
            }
        """)
        self.btn_copy_prompt.clicked.connect(self._on_copy_prompt)
        v_web.addWidget(self.btn_copy_prompt)

        self.paste_zone = PasteZoneWidget(self.web_box)
        self.paste_zone.json_recognized.connect(self._on_json_recognized)
        v_web.addWidget(self.paste_zone)

        v_ai.addWidget(self.web_box)
        v_ai.addStretch(1)

        v_right.addWidget(ai_card, stretch=1)
        main_layout.addWidget(col_right, stretch=4)

    def _on_row_toggled(self, idx: int):
        if 0 <= idx < len(self._review_rows):
            # Toggle 0 <-> 1
            cur = self._review_rows[idx][2]
            new_val = 0 if cur else 1
            self._review_rows[idx][2] = new_val
            if idx < len(self._row_widgets):
                self._row_widgets[idx].set_cut(bool(new_val))
            self.config_changed.emit()

    def _on_ai_run_clicked(self):
        self.lbl_ai_res.setText("Đang gửi transcript sang AI...")
        self.lbl_ai_res.setStyleSheet("color: #a78bfa; font-size: 11px;")
        self.ai_run_requested.emit()

    def set_ai_result(self, ok: bool, message: str):
        if ok:
            self.lbl_ai_res.setText(message or "Xong: 14 cảnh · Hook 5s · 62s tổng")
            self.lbl_ai_res.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: bold;")
        else:
            self.lbl_ai_res.setText(f"Lỗi: {message}")
            self.lbl_ai_res.setStyleSheet("color: #f87171; font-size: 11px;")

    def _toggle_web_prompt(self):
        self._web_prompt_visible = not self._web_prompt_visible
        self.web_box.setVisible(self._web_prompt_visible)
        arr = "▴" if self._web_prompt_visible else "▾"
        self.btn_web_toggle.setText(f"Dùng Web Prompt miễn phí {arr}")

    def _on_copy_prompt(self):
        # Tạo prompt mẫu kèm dữ liệu thoại
        lines = [f"{r[0]} | {r[1]}" for r in self._review_rows]
        prompt_content = (
            "Bạn là một đạo diễn dựng phim chuyên nghiệp. Dưới đây là transcript lời thoại video:\n\n"
            + "\n".join(lines)
            + "\n\nHãy phân tích và trả về một JSON định dạng Video Plan gồm { \"scenes\": [...] }."
        )
        QApplication.clipboard().setText(prompt_content)
        self.btn_copy_prompt.setText("✓ Đã sao chép prompt vào Clipboard!")
        self.prompt_copied.emit()

    def _on_json_recognized(self, data: dict, summary: str):
        self._pasted_data = data
        self._pasted_summary = summary
        self.config_changed.emit()

    def keyPressEvent(self, event: QKeyEvent):
        # Bắt phím Ctrl+Z để Undo câu thoại gần nhất
        if event.key() == Qt.Key.Key_Z and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            # Undo thao tác cắt
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
        if config.web_prompt_pasted_data:
            self._pasted_data = config.web_prompt_pasted_data
            self._pasted_summary = config.web_prompt_summary
            self.paste_zone.set_success(self._pasted_summary, self._pasted_data)

    def save_to_config(self, config: DirectorConfig):
        config.web_prompt_pasted_data = self._pasted_data
        config.web_prompt_summary = self._pasted_summary
