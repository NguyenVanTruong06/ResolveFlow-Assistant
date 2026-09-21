from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from src.ui.theme import ThemeColors, ThemeFonts

class SectionCard(QFrame):
    """
    Card UI hiện đại thay thế cho QGroupBox.
    Hỗ trợ viền màu tạo điểm nhấn bên trái và title in đậm.
    """
    def __init__(self, title: str, accent_color: str = ThemeColors.PRIMARY, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SectionCard {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-left: 4px solid {accent_color};
                border-radius: 6px;
                margin-top: 4px;
            }}
            SectionCard[active="true"] {{
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                border: 1px solid {ThemeColors.BORDER_ACTIVE};
                border-left: 4px solid {ThemeColors.BORDER_ACTIVE};
            }}
        """)
        
        # Main layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 10, 12, 12)
        self.main_layout.setSpacing(8)
        
        # Header (Title)
        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet(f"""
            color: {ThemeColors.TEXT_PRIMARY};
            font-size: 12px;
            font-weight: bold;
            border: none;
            background: transparent;
        """)
        self.main_layout.addWidget(self.lbl_title)
        
        # Body container
        self.body_widget = QWidget()
        self.body_widget.setStyleSheet("background: transparent; border: none;")
        self.main_layout.addWidget(self.body_widget)
        
    def set_body_layout(self, layout):
        """
        Thiết lập layout bên trong card.
        Layout này sẽ chứa các control (slider, checkbox, etc.)
        """
        self.body_widget.setLayout(layout)
