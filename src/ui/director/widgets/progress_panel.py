"""
src/ui/director/widgets/progress_panel.py
Cột tiến độ 260px bên phải của ChunDVC Director.
Xuất hiện khi người dùng bấm "Dựng timeline" ở Bước 4.
Theo dõi 6 bước tiến trình thời gian thực:
1. Kiểm tra (validate)
2. Tải AI (load_model)
3. Nhận diện (speech_to_text)
4. Đạo diễn AI (ai_director)
5. Biên tập (apply_cut)
6. Xuất bản (export)
"""

from typing import Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame
)
from PySide6.QtCore import Qt, Signal


class ProgressStepItem(QFrame):
    """Một dòng bước trong cột tiến độ."""
    def __init__(self, step_idx: int, name: str, desc: str, parent=None):
        super().__init__(parent)
        self.step_idx = step_idx
        self.name = name
        self.desc = desc
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: #1c1c20;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        h_top = QHBoxLayout()
        self.lbl_icon = QLabel("○")
        self.lbl_icon.setStyleSheet("color: #71717a; font-size: 12px; font-weight: bold;")
        h_top.addWidget(self.lbl_icon)

        self.lbl_title = QLabel(f"{self.step_idx}. {self.name}")
        self.lbl_title.setStyleSheet("color: #e4e4e7; font-size: 12px; font-weight: 600;")
        h_top.addWidget(self.lbl_title, stretch=1)

        self.lbl_status = QLabel("Chờ")
        self.lbl_status.setStyleSheet("color: #71717a; font-size: 10px;")
        h_top.addWidget(self.lbl_status)
        layout.addLayout(h_top)

        self.lbl_desc = QLabel(self.desc)
        self.lbl_desc.setStyleSheet("color: #a1a1aa; font-size: 10.5px;")
        self.lbl_desc.setWordWrap(True)
        layout.addWidget(self.lbl_desc)

    def set_status(self, status: str):
        # status: "waiting", "running", "done", "error", "skipped"
        if status == "running":
            self.lbl_icon.setText("▶")
            self.lbl_icon.setStyleSheet("color: #8b5cf6; font-size: 12px; font-weight: bold;")
            self.lbl_status.setText("Đang chạy")
            self.lbl_status.setStyleSheet("color: #a78bfa; font-size: 10px; font-weight: bold;")
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(124, 58, 237, 0.12);
                    border: 1px solid #8b5cf6;
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
        elif status == "done":
            self.lbl_icon.setText("✓")
            self.lbl_icon.setStyleSheet("color: #22c55e; font-size: 12px; font-weight: bold;")
            self.lbl_status.setText("Xong")
            self.lbl_status.setStyleSheet("color: #4ade80; font-size: 10px; font-weight: bold;")
            self.setStyleSheet("""
                QFrame {
                    background-color: #18181b;
                    border: 1px solid rgba(34, 197, 94, 0.4);
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
        elif status == "error":
            self.lbl_icon.setText("✕")
            self.lbl_icon.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold;")
            self.lbl_status.setText("Lỗi")
            self.lbl_status.setStyleSheet("color: #f87171; font-size: 10px; font-weight: bold;")
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(239, 68, 68, 0.12);
                    border: 1px solid #ef4444;
                    border-radius: 6px;
                    padding: 6px;
                }
            """)
        else:
            self.lbl_icon.setText("○")
            self.lbl_icon.setStyleSheet("color: #71717a; font-size: 12px; font-weight: bold;")
            self.lbl_status.setText("Chờ")
            self.lbl_status.setStyleSheet("color: #71717a; font-size: 10px;")
            self.setStyleSheet("""
                QFrame {
                    background-color: #1c1c20;
                    border: 1px solid #27272a;
                    border-radius: 6px;
                    padding: 6px;
                }
            """)


class ProgressSidePanel(QFrame):
    """Cột trượt 260px bên phải theo dõi 6 bước tiến trình."""
    cancel_requested = Signal()

    STEP_DEFS = [
        ("validate", "Kiểm tra", "Tệp video & định dạng nguồn"),
        ("load_model", "Tải AI", "Mô hình Faster-Whisper"),
        ("speech_to_text", "Nhận diện", "Lời thoại, khoảng lặng, chuyển động"),
        ("ai_director", "Đạo diễn AI", "Kịch bản & lọc thoại thông minh"),
        ("apply_cut", "Biên tập", "Cắt thô, B-roll, SFX, phụ đề"),
        ("export", "Xuất bản", "Tạo FCPXML & Timeline DaVinci")
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(260)
        self.setStyleSheet("""
            QFrame#progress_side_panel {
                background-color: #141417;
                border-left: 1px solid #27272a;
            }
        """)
        self.setObjectName("progress_side_panel")
        self._step_widgets: Dict[str, ProgressStepItem] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Header
        h_head = QHBoxLayout()
        lbl_head = QLabel("Tiến độ dựng timeline")
        lbl_head.setStyleSheet("color: #ffffff; font-size: 12.5px; font-weight: bold;")
        h_head.addWidget(lbl_head)

        btn_cancel = QPushButton("Hủy")
        btn_cancel.setFixedSize(48, 24)
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #ef4444;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #f87171;
            }
        """)
        btn_cancel.clicked.connect(self.cancel_requested.emit)
        h_head.addWidget(btn_cancel)
        layout.addLayout(h_head)

        # Progress bar
        self.prog_bar = QProgressBar(self)
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setFixedHeight(6)
        self.prog_bar.setTextVisible(False)
        self.prog_bar.setStyleSheet("""
            QProgressBar {
                background-color: #27272a;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #8b5cf6;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.prog_bar)

        # 6 Steps
        for i, (key, name, desc) in enumerate(self.STEP_DEFS, start=1):
            step_item = ProgressStepItem(i, name, desc, self)
            layout.addWidget(step_item)
            self._step_widgets[key] = step_item

        # Mini log box
        lbl_log = QLabel("Nhật ký xử lý:")
        lbl_log.setStyleSheet("color: #71717a; font-size: 10px; margin-top: 4px;")
        layout.addWidget(lbl_log)

        self.txt_log = QTextEdit(self)
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("""
            QTextEdit {
                background-color: #09090b;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 4px;
                font-family: monospace;
                font-size: 10px;
                padding: 4px;
            }
        """)
        self.txt_log.setFixedHeight(72)
        layout.addWidget(self.txt_log)

        layout.addStretch(1)

    def reset(self):
        """Khôi phục trạng thái ban đầu."""
        self.prog_bar.setValue(0)
        self.txt_log.clear()
        for item in self._step_widgets.values():
            item.set_status("waiting")

    def set_step_status(self, step_key: str, status: str):
        """Cập nhật trạng thái một bước (waiting, running, done, error)."""
        if step_key in self._step_widgets:
            self._step_widgets[step_key].set_status(status)

    def set_progress(self, percent: int):
        self.prog_bar.setValue(max(0, min(100, percent)))

    def append_log(self, text: str):
        self.txt_log.append(text)
        # Cuộn xuống đáy
        sb = self.txt_log.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
