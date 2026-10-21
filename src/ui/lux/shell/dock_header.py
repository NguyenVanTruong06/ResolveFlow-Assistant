"""
Dock Header Widget: 44px top header with Page Chip, Search box, Wide button, Pin, and Collapse.
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLineEdit, QLabel, QFrame
)
from PySide6.QtCore import Qt, Signal as pyqtSignal
from PySide6.QtGui import QKeyEvent


class SearchLineEdit(QLineEdit):
    """
    Search QLineEdit that captures Escape to clear text and blur focus.
    """
    escape_pressed = pyqtSignal()
    return_pressed_signal = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            self.clearFocus()
            self.escape_pressed.emit()
            event.accept()
            return
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.return_pressed_signal.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class DockHeaderWidget(QWidget):
    """
    44px Top Header Bar:
    [ ● Edit ]  [ ⌕ Tìm: chữ, shake... ]  [ ⤢ ] [ 📌 ] [ » ]
    """
    search_changed = pyqtSignal(str)
    search_submitted = pyqtSignal()
    escape_requested = pyqtSignal()
    page_clicked = pyqtSignal()
    wide_toggled = pyqtSignal()
    pin_toggled = pyqtSignal()
    collapse_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # 1. Page Chip (Clickable for profile selection / locking)
        self.btn_page_chip = QPushButton("● Edit")
        self.btn_page_chip.setFixedHeight(28)
        self.btn_page_chip.setToolTip("Hồ sơ ngữ cảnh DaVinci Resolve (Click để khóa hồ sơ)")
        self.btn_page_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_page_chip.setCursor(Qt.PointingHandCursor)
        self.btn_page_chip.setStyleSheet("""
            QPushButton {
                background: rgba(124, 58, 237, 0.15);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.4);
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(124, 58, 237, 0.25);
                border-color: #7c3aed;
            }
        """)
        self.btn_page_chip.clicked.connect(self.page_clicked.emit)
        layout.addWidget(self.btn_page_chip)

        # 2. Search box
        self.txt_search = SearchLineEdit()
        self.txt_search.setPlaceholderText("Tìm: chữ, shake, swoosh...")
        self.txt_search.setFixedHeight(28)
        self.txt_search.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.txt_search.setStyleSheet("""
            QLineEdit {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
                selection-background-color: #7c3aed;
            }
            QLineEdit:focus {
                border: 1px solid #7c3aed;
                background: #202024;
            }
        """)
        self.txt_search.textChanged.connect(self.search_changed.emit)
        self.txt_search.return_pressed_signal.connect(self.search_submitted.emit)
        self.txt_search.escape_pressed.connect(self.escape_requested.emit)
        layout.addWidget(self.txt_search, stretch=1)

        # 3. Wide toggle button (420px <-> 760px)
        self.btn_wide = QPushButton("⤢")
        self.btn_wide.setFixedSize(28, 28)
        self.btn_wide.setToolTip("Mở rộng 760px / Thu hẹp (Wide Mode)")
        self.btn_wide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_wide.setCursor(Qt.PointingHandCursor)
        self.btn_wide.setStyleSheet(self._button_style(active=False))
        self.btn_wide.clicked.connect(self.wide_toggled.emit)
        layout.addWidget(self.btn_wide)

        # 4. Pin toggle button
        self.btn_pin = QPushButton("📌")
        self.btn_pin.setFixedSize(28, 28)
        self.btn_pin.setToolTip("Ghim Dock: không tự động thu gọn")
        self.btn_pin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_pin.setCursor(Qt.PointingHandCursor)
        self.btn_pin.setStyleSheet(self._button_style(active=False))
        self.btn_pin.clicked.connect(self.pin_toggled.emit)
        layout.addWidget(self.btn_pin)

        # 5. Collapse button
        self.btn_collapse = QPushButton("»")
        self.btn_collapse.setFixedSize(28, 28)
        self.btn_collapse.setToolTip("Thu gọn về Rail (Esc)")
        self.btn_collapse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: 1px solid transparent;
                border-radius: 6px;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
                border-color: #3f3f46;
            }
        """)
        self.btn_collapse.clicked.connect(self.collapse_clicked.emit)
        layout.addWidget(self.btn_collapse)

    def _button_style(self, active: bool = False) -> str:
        if active:
            return """
                QPushButton {
                    background: rgba(124, 58, 237, 0.25);
                    color: #c4b5fd;
                    border: 1px solid #7c3aed;
                    border-radius: 6px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: rgba(124, 58, 237, 0.4);
                }
            """
        return """
            QPushButton {
                background: #18181b;
                color: #a1a1aa;
                border: 1px solid #27272a;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #27272a;
                border-color: #3f3f46;
                color: #ffffff;
            }
        """

    def set_page_label(self, label: str, locked: bool = False, color: str = "#7c3aed"):
        lock_str = " · Khóa" if locked else ""
        self.btn_page_chip.setText(f"● {label}{lock_str}")
        self.btn_page_chip.setStyleSheet(f"""
            QPushButton {{
                background: {color}22;
                color: #f4f4f5;
                border: 1px solid {color}66;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11.5px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {color}44;
                border-color: {color};
            }}
        """)

    def set_pinned(self, pinned: bool):
        self.btn_pin.setStyleSheet(self._button_style(active=pinned))

    def set_wide(self, wide: bool):
        self.btn_wide.setStyleSheet(self._button_style(active=wide))

    def clear_search(self):
        self.txt_search.clear()

    @property
    def search_text(self) -> str:
        return self.txt_search.text()
