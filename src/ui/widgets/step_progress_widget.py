from typing import List, Tuple
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QVBoxLayout, QFrame
from PySide6.QtCore import Qt
from src.ui.theme import ThemeColors, ThemeFonts

class StepProgressWidget(QFrame):
    """
    Widget hiển thị trạng thái của các giai đoạn lớn trong pipeline.
    Có thể reset và cập nhật trạng thái linh hoạt.
    """
    def __init__(self, step_ids_and_labels: List[Tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {ThemeColors.BG_CARD}; border: 1px solid {ThemeColors.BORDER_DEFAULT}; border-radius: 8px;")
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(5)
        
        self.steps = {}
        for idx, (step_id, label_text) in enumerate(step_ids_and_labels):
            vbox = QVBoxLayout()
            icon = QLabel("⏳")
            icon.setAlignment(Qt.AlignCenter)
            icon.setStyleSheet("font-size: 16px; border: none; background: transparent;")
            
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            
            vbox.addWidget(icon)
            vbox.addWidget(label)
            
            self.steps[step_id] = {"icon": icon, "label": label, "status": "pending"}
            self.layout.addLayout(vbox)
            
            # Add separator except for last
            if idx < len(step_ids_and_labels) - 1:
                sep = QLabel("→")
                sep.setAlignment(Qt.AlignCenter)
                sep.setStyleSheet(f"color: {ThemeColors.BORDER_DEFAULT}; font-size: 14px; font-weight: bold; border: none; background: transparent;")
                self.layout.addWidget(sep)
                
    def set_step_status(self, step_id: str, status: str):
        if step_id not in self.steps:
            return
            
        self.steps[step_id]["status"] = status
        
        icon_lbl = self.steps[step_id]["icon"]
        text_lbl = self.steps[step_id]["label"]
        
        if status == "pending":
            icon_lbl.setText("⏳")
            text_lbl.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "running":
            icon_lbl.setText("🔄")
            text_lbl.setStyleSheet(f"color: {ThemeColors.PRIMARY}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "done":
            icon_lbl.setText("✔")
            text_lbl.setStyleSheet(f"color: {ThemeColors.SUCCESS}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        elif status == "error":
            icon_lbl.setText("❌")
            text_lbl.setStyleSheet(f"color: {ThemeColors.ERROR}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            
    def reset(self):
        for step_id in self.steps:
            self.set_step_status(step_id, "pending")
