"""
src/ui/director/widgets/stepper.py
Thanh điều hướng 4 bước (Stepper) của ChunDVC Director.
Gồm: 1. Nguồn -> 2. Kiểu dựng -> 3. Duyệt & kịch bản -> 4. Dựng.
Hỗ trợ hiển thị trạng thái hoàn thành (Checkmark), đang chọn (Active) và bấm chuyển bước.
"""

from typing import List
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QFrame
from PySide6.QtCore import Qt, Signal


class DirectorStepButton(QPushButton):
    """Nút bấm đại diện cho 1 bước trên Stepper."""
    def __init__(self, step_idx: int, title: str, parent=None):
        super().__init__(parent)
        self.step_idx = step_idx
        self.title = title
        self.is_active = False
        self.is_completed = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self._update_appearance()

    def set_state(self, is_active: bool, is_completed: bool):
        self.is_active = is_active
        self.is_completed = is_completed
        self._update_appearance()

    def _update_appearance(self):
        prefix = "✓ " if self.is_completed and not self.is_active else f"{self.step_idx}. "
        self.setText(f"{prefix}{self.title}")

        if self.is_active:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #262930;
                    color: #ffffff;
                    border: 1px solid #3d424d;
                    border-bottom: 2px solid #e05a47;
                    border-radius: 4px;
                    padding: 0 15px;
                    font-size: 12px;
                    font-weight: 700;
                }
            """)
        elif self.is_completed:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #1f2227;
                    color: #4ade80;
                    border: 1px solid #2e333b;
                    border-radius: 4px;
                    padding: 0 15px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #272b31;
                    border-color: #3d4450;
                    color: #86efac;
                }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    color: #888e99;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 0 15px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #22242a;
                    border-color: #2e3138;
                    color: #e6e8ec;
                }
            """)


class DirectorStepperWidget(QWidget):
    """Container ngang chứa 4 bước của ChunDVC Director."""
    step_selected = Signal(int)  # 1-indexed: 1, 2, 3, 4

    STEP_TITLES = [
        "Nguồn",
        "Kiểu dựng",
        "Duyệt & kịch bản",
        "Dựng"
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_step = 1
        self._buttons: List[DirectorStepButton] = []
        self._init_ui()

    def _init_ui(self):
        self.setObjectName("director_stepper_widget")
        self.setStyleSheet("""
            QWidget#director_stepper_widget {
                background-color: #191a1e;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        for i, title in enumerate(self.STEP_TITLES, start=1):
            btn = DirectorStepButton(i, title, self)
            btn.clicked.connect(lambda checked=False, s=i: self._on_step_clicked(s))
            layout.addWidget(btn)
            self._buttons.append(btn)

            # Thêm mũi tên kết nối nhỏ giữa các bước
            if i < len(self.STEP_TITLES):
                lbl_arr = QLabel("›")
                lbl_arr.setStyleSheet("color: #4a4a56; font-size: 14px; font-weight: bold; background: transparent;")
                layout.addWidget(lbl_arr)

        layout.addStretch(1)
        self.set_current_step(1)

    def _on_step_clicked(self, step: int):
        self.set_current_step(step)
        self.step_selected.emit(step)

    def set_current_step(self, step: int):
        """Cập nhật giao diện Stepper theo bước hiện tại (1..4)."""
        self.current_step = max(1, min(4, step))
        for i, btn in enumerate(self._buttons, start=1):
            is_active = (i == self.current_step)
            is_completed = (i < self.current_step)
            btn.set_state(is_active, is_completed)
