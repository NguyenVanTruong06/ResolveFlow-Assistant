"""
Dock Action Bar: Sticky 56px bottom bar with asset info, primary action, Sheet toggle, and More menu.
"""
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMenu
)
from PySide6.QtCore import Qt, Signal as pyqtSignal
from PySide6.QtGui import QAction, QCursor


class DockActionBar(QWidget):
    """
    Sticky 56px action bar:
    Top row: [ Alex Hormozi Pop · 4.0s · V2 tự động ]
    Bottom row: [ Chèn tại playhead ⏎ ]  [ Tùy chỉnh Tab ]  [ ⋯ ]
    """
    primary_triggered = pyqtSignal()
    tune_triggered = pyqtSignal()
    paste_fusion_triggered = pyqtSignal()
    copy_code_triggered = pyqtSignal()
    install_fusion_triggered = pyqtSignal()
    new_track_triggered = pyqtSignal()
    toggle_fav_triggered = pyqtSignal()
    open_folder_triggered = pyqtSignal()
    menu_visibility_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(60)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._current_asset: Optional[Dict[str, Any]] = None
        self._is_fav: bool = False

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)

        # Line 1: Asset Info Label
        self.lbl_info = QLabel("Chọn một mẫu · double-click hoặc kéo ra timeline để chèn")
        self.lbl_info.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_info.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.lbl_info)

        # Line 2: Buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        # Primary action button
        self.btn_primary = QPushButton("Chèn tại playhead  ⏎")
        self.btn_primary.setFixedHeight(28)
        self.btn_primary.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_primary.setCursor(Qt.PointingHandCursor)
        self.btn_primary.setStyleSheet("""
            QPushButton {
                background: #7c3aed;
                color: #ffffff;
                font-size: 12px;
                font-weight: 600;
                border: 1px solid #8b5cf6;
                border-radius: 6px;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: #6d28d9;
                border-color: #a78bfa;
            }
            QPushButton:disabled {
                background: #27272a;
                color: #71717a;
                border-color: #3f3f46;
            }
        """)
        self.btn_primary.clicked.connect(self.primary_triggered.emit)
        btn_row.addWidget(self.btn_primary, stretch=1)

        # Tune button (opens sheet inspector)
        self.btn_tune = QPushButton("Tùy chỉnh  Tab")
        self.btn_tune.setFixedHeight(28)
        self.btn_tune.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_tune.setCursor(Qt.PointingHandCursor)
        self.btn_tune.setStyleSheet("""
            QPushButton {
                background: #18181b;
                color: #d4d4d8;
                font-size: 11.5px;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 0 10px;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
                border-color: #3f3f46;
            }
            QPushButton:disabled {
                color: #52525b;
                border-color: #1f1f23;
            }
        """)
        self.btn_tune.clicked.connect(self.tune_triggered.emit)
        btn_row.addWidget(self.btn_tune)

        # More menu button
        self.btn_more = QPushButton("⋯")
        self.btn_more.setFixedSize(28, 28)
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.setStyleSheet("""
            QPushButton {
                background: #18181b;
                color: #d4d4d8;
                font-size: 14px;
                font-weight: bold;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #27272a;
                color: #ffffff;
                border-color: #3f3f46;
            }
            QPushButton:disabled {
                color: #52525b;
            }
        """)
        self.btn_more.clicked.connect(self._show_more_menu)
        btn_row.addWidget(self.btn_more)

        layout.addLayout(btn_row)

        self.setStyleSheet("""
            QWidget {
                background: #111114;
                border-top: 1px solid #202025;
            }
        """)

        # Default disabled when nothing selected
        self.set_asset(None)

    def set_asset(self, asset: Optional[Dict[str, Any]], is_fav: bool = False, timecode: str = "00:00:00:00"):
        self._current_asset = asset
        self._is_fav = is_fav

        if not asset:
            self.lbl_info.setText("Chọn một mẫu · double-click hoặc kéo ra timeline để chèn")
            self.btn_primary.setEnabled(False)
            self.btn_tune.setEnabled(False)
            self.btn_more.setEnabled(False)
            return

        name = asset.get("name", "Tài nguyên")
        dur = asset.get("duration", 0)
        dur_str = f" · {dur:.1f}s" if dur else ""
        cat = asset.get("category", "")
        track_hint = "track audio trống" if cat in ("sfx", "bgm") else ("clip tại playhead" if cat == "lut" else "track video trống")

        self.lbl_info.setText(f"{name}{dur_str} · {track_hint} · {timecode}")
        self.btn_primary.setEnabled(True)
        self.btn_tune.setEnabled(True)
        self.btn_more.setEnabled(True)

    def _show_more_menu(self):
        if not self._current_asset:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #18181b;
                color: #f4f4f5;
                border: 1px solid #27272a;
                border-radius: 8px;
                padding: 4px;
                font-size: 12px;
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

        cat = self._current_asset.get("category", "")
        is_fusion_applicable = cat in ("text", "trans", "fx")

        if is_fusion_applicable:
            act_paste = menu.addAction("⚡ Dán vào Fusion (Shift+Enter)")
            act_paste.triggered.connect(self.paste_fusion_triggered.emit)

            act_copy = menu.addAction("📋 Sao chép code Fusion")
            act_copy.triggered.connect(self.copy_code_triggered.emit)

            act_install = menu.addAction("📥 Cài vào thư mục Fusion")
            act_install.triggered.connect(self.install_fusion_triggered.emit)

            menu.addSeparator()

        act_newtrack = menu.addAction("➕ Chèn trên track mới")
        act_newtrack.triggered.connect(self.new_track_triggered.emit)

        menu.addSeparator()

        fav_label = "💔 Bỏ yêu thích" if self._is_fav else "❤️ Thêm vào yêu thích (Ctrl+Enter)"
        act_fav = menu.addAction(fav_label)
        act_fav.triggered.connect(self.toggle_fav_triggered.emit)

        act_folder = menu.addAction("📁 Mở thư mục chứa")
        act_folder.triggered.connect(self.open_folder_triggered.emit)

        self.menu_visibility_changed.emit(True)
        menu.aboutToHide.connect(lambda: self.menu_visibility_changed.emit(False))

        # Position menu right above the more button
        btn_pos = self.btn_more.mapToGlobal(self.btn_more.rect().topLeft())
        menu.exec(btn_pos - menu.sizeHint())
