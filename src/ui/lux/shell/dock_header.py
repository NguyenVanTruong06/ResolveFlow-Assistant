"""
Dock Header Widget: 44px top header with Page Chip, Search box, Wide button, Pin, and Collapse.
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLineEdit, QLabel, QFrame
)
from PySide6.QtCore import Qt, QSize, Signal as pyqtSignal, QSettings
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
    [ ● Edit ]  [ ⌕ Tìm: chữ, shake... ]  [ 🤖 ] [ 🎨 ] [ 🔊 ] [ ⚙ ] [ ⤢ ] [ 📌 ] [ » ]
    """
    search_changed = pyqtSignal(str)
    search_submitted = pyqtSignal()
    escape_requested = pyqtSignal()
    page_clicked = pyqtSignal()
    pending_chip_clicked = pyqtSignal()
    profile_lock_selected = pyqtSignal(str)
    instant_autocut_clicked = pyqtSignal()
    director_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    audio_preview_toggled = pyqtSignal(bool)
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
                background: #23252a;
                color: #f59e0b;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2c2f36;
                border-color: #f59e0b;
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
                background: #151619;
                color: #e6e8ec;
                border: 1px solid #2e3138;
                border-radius: 4px;
                padding: 0 9px;
                font-size: 12px;
                selection-background-color: #d94f3d;
            }
            QLineEdit:hover {
                border-color: #434752;
                background: #191a1e;
            }
            QLineEdit:focus {
                border: 1px solid #e05a47;
                background: #1d1e23;
            }
        """)
        self.txt_search.textChanged.connect(self.search_changed.emit)
        self.txt_search.return_pressed_signal.connect(self.search_submitted.emit)
        self.txt_search.escape_pressed.connect(self.escape_requested.emit)
        layout.addWidget(self.txt_search, stretch=1)

        # 2.3. Instant AutoCut 1-Click button (Sub-second silence cut & ripple assembly)
        self.btn_instant_autocut = QPushButton()
        self.btn_instant_autocut.setIcon(get_vector_icon("autocut", "#f59e0b", 16))
        self.btn_instant_autocut.setIconSize(QSize(16, 16))
        self.btn_instant_autocut.setFixedSize(28, 28)
        self.btn_instant_autocut.setToolTip("⚡ AutoCut 1-Click: Cắt khoảng lặng trên Timeline đang mở & co sát clip (0.3s)")
        self.btn_instant_autocut.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_instant_autocut.setCursor(Qt.PointingHandCursor)
        self.btn_instant_autocut.setStyleSheet("""
            QPushButton {
                background: rgba(245, 158, 11, 0.12);
                border: 1px solid rgba(245, 158, 11, 0.35);
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(245, 158, 11, 0.25);
                border-color: #f59e0b;
            }
        """)
        self.btn_instant_autocut.clicked.connect(self.instant_autocut_clicked.emit)
        layout.addWidget(self.btn_instant_autocut)

        # 2.4. Director Mode button (ChunDVC Director: AI AutoCut, Subtitles, Prompt JSON)
        self.btn_director = QPushButton()
        self.btn_director.setIcon(get_vector_icon("ai", "#f59e0b", 16))
        self.btn_director.setIconSize(QSize(16, 16))
        self.btn_director.setFixedSize(28, 28)
        self.btn_director.setToolTip("Mở ChunDVC Director (AI AutoCut, Phụ đề, Web Prompt JSON 0đ)")
        self.btn_director.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_director.setCursor(Qt.PointingHandCursor)
        self.btn_director.setStyleSheet(self._button_style(active=False))
        self.btn_director.clicked.connect(self.director_clicked.emit)
        layout.addWidget(self.btn_director)

        # 2.5. Studio Mode button (Full 4-column assets library & workshop window)
        self.btn_studio = QPushButton()
        self.btn_studio.setIcon(get_vector_icon("studio", "#a1a1aa", 16))
        self.btn_studio.setIconSize(QSize(16, 16))
        self.btn_studio.setFixedSize(28, 28)
        self.btn_studio.setToolTip("Mở Kho Đạo Cụ & Xưởng Quản Lý (Nạp thư mục, quét tệp, bộ sưu tập)")
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

        # 2.6.1 Overflow button (shown when w < 410 to access Director, Studio, Settings)
        self.btn_overflow = QPushButton()
        self.btn_overflow.setIcon(get_vector_icon("more", "#a1a1aa", 16))
        self.btn_overflow.setIconSize(QSize(16, 16))
        self.btn_overflow.setFixedSize(24, 28)
        self.btn_overflow.setToolTip("Tùy chọn khác (Director, Studio, Cài đặt)")
        self.btn_overflow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_overflow.setCursor(Qt.PointingHandCursor)
        self.btn_overflow.setStyleSheet(self._button_style(active=False))
        self.btn_overflow.clicked.connect(self._show_overflow_menu)
        self.btn_overflow.hide()
        layout.addWidget(self.btn_overflow)

        # 2.7. Hover Audio toggle button (Âm thanh xem trước Meme/SFX khi lướt chuột)
        self.btn_audio = QPushButton()
        self.btn_audio.setIconSize(QSize(16, 16))
        self.btn_audio.setFixedSize(28, 28)
        self.btn_audio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_audio.setCursor(Qt.PointingHandCursor)
        self._settings = QSettings("ChunDVC", "ResolveFlow")
        self._audio_enabled = self._settings.value("library/hover_audio_enabled", True, type=bool)
        self._update_audio_button_state()
        self.btn_audio.clicked.connect(self._on_audio_btn_clicked)
        layout.addWidget(self.btn_audio)

        # 3. Wide toggle button (420px <-> 760px)
        self.btn_wide = QPushButton()
        self.btn_wide.setIcon(get_vector_icon("wide", "#a1a1aa", 16))
        self.btn_wide.setIconSize(QSize(16, 16))
        self.btn_wide.setFixedSize(28, 28)
        self.btn_wide.setToolTip("Mở rộng 760px / Thu hẹp (Wide Mode)")
        self.btn_wide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_wide.setCursor(Qt.PointingHandCursor)
        self.btn_wide.setStyleSheet(self._button_style(active=False))
        self.btn_wide.clicked.connect(self.wide_toggled.emit)
        layout.addWidget(self.btn_wide)

        # 4. Pin toggle button
        self.btn_pin = QPushButton()
        self.btn_pin.setIcon(get_vector_icon("pin", "#a1a1aa", 16))
        self.btn_pin.setIconSize(QSize(16, 16))
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
        self.btn_collapse.setToolTip("Thu ẩn hoàn toàn vào cạnh màn hình (Esc / Ctrl+Alt+D)")
        self.btn_collapse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_collapse.setCursor(Qt.PointingHandCursor)
        self.btn_collapse.setStyleSheet("""
            QPushButton {
                background: #1c1d21;
                color: #d4d4d8;
                border: 1px solid #2e3138;
                border-radius: 6px;
                font-size: 15px;
                font-weight: 700;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                background: rgba(217, 79, 61, 0.20);
                border-color: #d94f3d;
                color: #ffffff;
            }
        """)
        self.btn_collapse.clicked.connect(self.collapse_clicked.emit)
        layout.addWidget(self.btn_collapse)

    def _button_style(self, active: bool = False) -> str:
        if active:
            return """
                QPushButton {
                    background: rgba(217, 79, 61, 0.22);
                    color: #f8b4a8;
                    border: 1px solid #d94f3d;
                    border-radius: 4px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background: rgba(217, 79, 61, 0.35);
                }
            """
        return """
            QPushButton {
                background: #1c1d21;
                color: #9da3ae;
                border: 1px solid #2e3138;
                border-radius: 4px;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #272930;
                border-color: #434752;
                color: #ffffff;
            }
        """

    def set_page_label(self, label: str, locked: bool = False, color: str = "#7c3aed"):
        self._current_page = label
        self._is_locked = locked
        self._page_color = color
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

    def minimumSizeHint(self) -> QSize:
        return QSize(260, 44)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if w < 410:
            self.btn_director.hide()
            self.btn_studio.hide()
            self.btn_settings.hide()
            self.btn_overflow.show()
        else:
            self.btn_director.show()
            self.btn_studio.show()
            self.btn_settings.show()
            self.btn_overflow.hide()
        if w < 340:
            self.btn_instant_autocut.hide()
            self.btn_page_chip.setText("●")
        else:
            self.btn_instant_autocut.show()
            lock_str = " · Khóa" if getattr(self, "_is_locked", False) else ""
            self.btn_page_chip.setText(f"● {self._current_page}{lock_str}")

    def _show_overflow_menu(self):
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
        """)
        act_dir = menu.addAction("🤖 ChunDVC Director")
        act_dir.triggered.connect(self.director_clicked.emit)
        act_studio = menu.addAction("🏛️ ChunDVC Studio")
        act_studio.triggered.connect(self.restore_standard_window_clicked.emit)
        act_settings = menu.addAction("⚙️ Cài đặt Dock")
        act_settings.triggered.connect(self.settings_clicked.emit)

        pos = self.btn_overflow.mapToGlobal(self.btn_overflow.rect().bottomLeft())
        menu.exec(pos)

    def set_pinned(self, pinned: bool):
        self.btn_pin.setStyleSheet(self._button_style(active=pinned))
        if pinned:
            self.btn_pin.setIcon(get_vector_icon("pinned", "#f59e0b", 16))
            self.btn_pin.setToolTip("Đã ghim Dock (Bấm để mở tự thu gọn)")
        else:
            self.btn_pin.setIcon(get_vector_icon("pin", "#a1a1aa", 16))
            self.btn_pin.setToolTip("Ghim Dock: không tự động thu gọn")

    def set_wide(self, wide: bool):
        self.btn_wide.setStyleSheet(self._button_style(active=wide))
        if wide:
            self.btn_wide.setIcon(get_vector_icon("shrink", "#f59e0b", 16))
            self.btn_wide.setToolTip("Thu hẹp về 420px (Compact Mode)")
        else:
            self.btn_wide.setIcon(get_vector_icon("wide", "#a1a1aa", 16))
            self.btn_wide.setToolTip("Mở rộng 760px (Wide Mode)")

    def _on_audio_btn_clicked(self):
        self._audio_enabled = not self._audio_enabled
        self._settings.setValue("library/hover_audio_enabled", self._audio_enabled)
        self._update_audio_button_state()
        self.audio_preview_toggled.emit(self._audio_enabled)

    def set_audio_preview_enabled(self, enabled: bool):
        self._audio_enabled = bool(enabled)
        self._update_audio_button_state()

    def _update_audio_button_state(self):
        if self._audio_enabled:
            self.btn_audio.setIcon(get_vector_icon("sound_on", "#f59e0b", 16))
            self.btn_audio.setToolTip("Âm thanh khi lướt Meme / SFX: ĐANG BẬT (Bấm để tắt)")
            self.btn_audio.setStyleSheet(self._button_style(active=True))
        else:
            self.btn_audio.setIcon(get_vector_icon("sound_off", "#71717a", 16))
            self.btn_audio.setToolTip("Âm thanh khi lướt Meme / SFX: ĐANG TẮT (Bấm để bật)")
            self.btn_audio.setStyleSheet(self._button_style(active=False))

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
            self.btn_collapse.setToolTip("Thu ẩn hoàn toàn vào cạnh trái (Esc / Ctrl+Alt+D)")
        else:
            self.btn_collapse.setText("»")
            self.btn_collapse.setToolTip("Thu ẩn hoàn toàn vào cạnh phải (Esc / Ctrl+Alt+D)")

