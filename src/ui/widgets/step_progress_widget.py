from typing import List, Tuple
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QVBoxLayout, QFrame
from PySide6.QtCore import Qt
from src.ui.theme import ThemeColors, ThemeFonts
from src.ui.icons import create_vector_icon


class StepProgressWidget(QFrame):
    """
    Widget hiển thị trạng thái của các giai đoạn lớn trong pipeline.
    Sử dụng biểu tượng vector sắc nét, chống lỗi hiển thị emoji trên Linux/Windows.
    """
    def __init__(self, step_ids_and_labels: List[Tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            StepProgressWidget {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(12, 10, 12, 10)
        self.layout.setSpacing(6)
        
        self.steps = {}
        for idx, (step_id, label_text) in enumerate(step_ids_and_labels):
            vbox = QVBoxLayout()
            vbox.setSpacing(3)
            
            icon = QLabel()
            icon.setAlignment(Qt.AlignCenter)
            icon.setFixedSize(22, 22)
            icon.setPixmap(create_vector_icon("clock", ThemeColors.TEXT_MUTED, 18))
            icon.setStyleSheet("border: none; background: transparent;")
            
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
            
            vbox.addWidget(icon, 0, Qt.AlignCenter)
            vbox.addWidget(label, 0, Qt.AlignCenter)
            
            self.steps[step_id] = {"icon": icon, "label": label, "status": "pending"}
            self.layout.addLayout(vbox)
            
            # Separator giữa các bước
            if idx < len(step_ids_and_labels) - 1:
                sep = QLabel()
                sep.setAlignment(Qt.AlignCenter)
                sep.setFixedSize(16, 16)
                sep.setPixmap(create_vector_icon("arrow_right", ThemeColors.BORDER_LINE3, 14))
                sep.setStyleSheet("border: none; background: transparent;")
                self.layout.addWidget(sep, 0, Qt.AlignCenter)
                
    def set_step_status(self, step_id: str, status: str):
        if step_id not in self.steps:
            return
            
        self.steps[step_id]["status"] = status
        
        icon_lbl = self.steps[step_id]["icon"]
        text_lbl = self.steps[step_id]["label"]
        
        if status == "pending":
            icon_lbl.setPixmap(create_vector_icon("clock", ThemeColors.TEXT_MUTED, 18))
            text_lbl.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11.5px; font-weight: 600; border: none; background: transparent;")
        elif status == "running":
            icon_lbl.setPixmap(create_vector_icon("refresh", ThemeColors.PRIMARY_HOVER, 18))
            text_lbl.setStyleSheet(f"color: {ThemeColors.PRIMARY_HOVER}; font-size: 11.5px; font-weight: bold; border: none; background: transparent;")
        elif status == "done":
            icon_lbl.setPixmap(create_vector_icon("check", ThemeColors.SUCCESS, 18))
            text_lbl.setStyleSheet(f"color: {ThemeColors.SUCCESS}; font-size: 11.5px; font-weight: bold; border: none; background: transparent;")
        elif status == "error":
            icon_lbl.setPixmap(create_vector_icon("cross", ThemeColors.ERROR, 18))
            text_lbl.setStyleSheet(f"color: {ThemeColors.ERROR}; font-size: 11.5px; font-weight: bold; border: none; background: transparent;")
            
    def reset(self):
        for step_id in self.steps:
            self.set_step_status(step_id, "pending")

