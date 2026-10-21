"""
Dock Header Widget: 44px top header with Page Chip, Search box, Wide button, Pin, and Collapse.
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLineEdit, QLabel, QFrame
)
from PySide6.QtCore import Qt, QSize, Signal as pyqtSignal
from PySide6.QtGui import QKeyEvent

from src.ui.icons import get_vector_icon


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
    [ ● Edit ]  [ ⌕ Tìm: chữ, shake... ]  [ 🤖 ] [ 🎨 ] [ ⤢ ] [ 📌 ] [ » ]
    """
    search_changed = pyqtSignal(str)
    search_submitted = pyqtSignal()
    escape_requested = pyqtSignal()
    page_clicked = pyqtSignal()
    pending_chip_clicked = pyqtSignal()
    profile_lock_selected = pyqtSignal(str)
    director_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    wide_toggled = pyqtSignal()
    pin_toggled = pyqtSignal()
    collapse_clicked = pyqtSignal()
    restore_standard_window_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._current_page = "edit"
        self._is_locked = False
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
        self.btn_page_chip.clicked.connect(self._on_page_chip_clicked)
        layout.addWidget(self.btn_page_chip)

        # 1.1 Pending Page Chip (Hidden by default, shown when page change is deferred)
        self.btn_pending_chip = QPushButton("Fusion ›")
        self.btn_pending_chip.setFixedHeight(28)
        self.btn_pending_chip.setToolTip("DaVinci đã đổi trang. Bấm để chuyển đổi ngay lập tức.")
        self.btn_pending_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_pending_chip.setCursor(Qt.PointingHandCursor)
        self.btn_pending_chip.setStyleSheet("""
            QPushButton {
                background: rgba(245, 158, 11, 0.18);
                color: #fde68a;
                border: 1px solid #f59e0b;
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f59e0b;
                color: #000000;
            }
        """)
        self.btn_pending_chip.clicked.connect(self.pending_chip_clicked.emit)
        self.btn_pending_chip.hide()
        layout.addWidget(self.btn_pending_chip)

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

        # 2.4. Director Mode button (ChunDVC Director: AI AutoCut, Subtitles, Prompt JSON)
        self.btn_director = QPushButton()
        self.btn_director.setIcon(get_vector_icon("ai", "#c4b5fd", 16))
        self.btn_director.setIconSize(QSize(16, 16))
        self.btn_director.setFixedSize(28, 28)
        self.btn_director.setToolTip("Mở ChunDVC Director (AI AutoCut, Phụ đề, Web Prompt JSON 0đ)")
        self.btn_director.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_director.setCursor(Qt.PointingHandCursor)
        self.btn_director.setStyleSheet(self._button_style(active=False))
        self.btn_director.clicked.connect(self.director_clicked.emit)
        layout.addWidget(self.btn_director)

        # 2.5. Studio Mode button (Full 4-column assets library window)
        self.btn_studio = QPushButton()
        self.btn_studio.setIcon(get_vector_icon("studio", "#a1a1aa", 16))
        self.btn_studio.setIconSize(QSize(16, 16))
        self.btn_studio.setFixedSize(28, 28)
        self.btn_studio.setToolTip("Mở Kho Đạo Cụ Studio đầy đủ (4 cột)")
        self.btn_studio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_studio.setCursor(Qt.PointingHandCursor)
        self.btn_studio.setStyleSheet(self._button_style(active=False))
        self.btn_studio.clicked.connect(self.restore_standard_window_clicked.emit)
        layout.addWidget(self.btn_studio)

        # 2.6. Settings button (Dock Settings Dialog)
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(get_vector_icon("settings", "#a1a1aa", 16))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setToolTip("Cài đặt thanh Dock (Đổi bên trái/phải, phím tắt...)")
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setStyleSheet(self._button_style(active=False))
        self.btn_settings.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.btn_settings)

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

    def set_pending_page(self, pending_profile):
        """Shows or hides the pending chip (e.g. 'Fusion ›')."""
        if pending_profile:
            label = getattr(pending_profile, "label", str(pending_profile))
            self.btn_pending_chip.setText(f"{label} ›")
            self.btn_pending_chip.show()
        else:
            self.btn_pending_chip.hide()

    def _on_page_chip_clicked(self):
        self.page_clicked.emit()
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 4px;
                font-size: 11.5px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background: #27272a;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #27272a;
                margin: 4px 6px;
            }
        """)

        act_auto = menu.addAction("● Theo DaVinci (tự động)")
        act_auto.triggered.connect(lambda: self.profile_lock_selected.emit("auto"))
        menu.addSeparator()

        pages = [
            ("edit", "Edit"),
            ("fusion", "Fusion"),
            ("color", "Color"),
            ("fairlight", "Fairlight"),
            ("cut", "Cut"),
            ("deliver", "Deliver"),
            ("media", "Media"),
        ]
        for pid, plabel in pages:
            act = menu.addAction(f"Khóa: {plabel}")
            act.triggered.connect(lambda checked=False, p=pid: self.profile_lock_selected.emit(p))

        pos = self.btn_page_chip.mapToGlobal(self.btn_page_chip.rect().bottomLeft())
        menu.exec(pos)

    def set_dock_edge(self, edge: str):
        """Cập nhật biểu tượng mũi tên thu gọn dựa theo cạnh màn hình (trái/phải)."""
        if edge == "left":
            self.btn_collapse.setText("«")
            self.btn_collapse.setToolTip("Thu gọn về mép trái (Esc)")
        else:
            self.btn_collapse.setText("»")
            self.btn_collapse.setToolTip("Thu gọn về mép phải (Esc)")

