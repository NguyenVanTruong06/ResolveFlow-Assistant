"""
Dock Action Bar: Sticky 56px bottom bar with asset info, primary action, Sheet toggle, and More menu.
"""
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMenu
)
from PySide6.QtCore import Qt, QSize, Signal as pyqtSignal
from PySide6.QtGui import QAction, QCursor
from src.ui.icons import get_vector_icon


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
        self.setMinimumHeight(60)
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
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.lbl_info)

        # Line 2: Buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        # Primary action button
        self.btn_primary = QPushButton("Chèn tại playhead  ⏎")
        self.btn_primary.setIcon(get_vector_icon("paste", "#ffffff", 14))
        self.btn_primary.setIconSize(QSize(14, 14))
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
        self.btn_tune = QPushButton(" Tinh chỉnh (Tab)")
        self.btn_tune.setIcon(get_vector_icon("tune", "#a1a1aa", 14))
        self.btn_tune.setIconSize(QSize(14, 14))
        self.btn_tune.setToolTip("Mở bảng Tinh chỉnh thông số chi tiết (Phím tắt: Tab)")
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
        self.btn_more = QPushButton()
        self.btn_more.setIcon(get_vector_icon("more", "#a1a1aa", 16))
        self.btn_more.setIconSize(QSize(16, 16))
        self.btn_more.setFixedSize(28, 28)
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_more.setCursor(Qt.PointingHandCursor)
        self.btn_more.setStyleSheet("""
            QPushButton {
                background: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #27272a;
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

    def minimumSizeHint(self) -> QSize:
        return QSize(240, 56)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        if w < 380:
            self.btn_tune.setText(" Tab")
            self.btn_primary.setText("Chèn ⏎")
        else:
            self.btn_tune.setText(" Tinh chỉnh (Tab)")
            self.btn_primary.setText("Chèn tại playhead  ⏎")

    def set_asset(self, asset: Optional[Any], is_fav: bool = False, timecode: str = "00:00:00:00"):
        if asset is not None and not isinstance(asset, dict):
            if hasattr(asset, "model_dump") and callable(asset.model_dump):
                asset = asset.model_dump()
            elif hasattr(asset, "dict") and callable(asset.dict):
                asset = asset.dict()
            elif hasattr(asset, "__dict__"):
                asset = dict(asset.__dict__)
            else:
                asset = {"id": str(asset), "name": str(asset)}

        self._current_asset = asset
        self._is_fav = is_fav

        if not asset:
            self.lbl_info.setText("Chọn một mẫu · double-click hoặc kéo ra timeline để chèn")
            self.btn_primary.setEnabled(False)
            self.btn_primary.setToolTip("")
            self.btn_tune.setEnabled(False)
            self.btn_more.setEnabled(False)
            return

        name = asset.get("name", "Tài nguyên")
        dur = asset.get("duration", 0)
        dur_str = f" · {dur:.1f}s" if dur else ""
        cat = str(asset.get("category", "")).lower()
        if cat in ("sfx", "bgm", "audio"):
            track_hint = "track audio trống"
        elif cat in ("lut", "luts"):
            track_hint = "clip tại playhead"
        elif cat in ("trans", "transitions", "transition"):
            track_hint = "kéo thả vào điểm cắt (Cut)"
        else:
            track_hint = "track video trống"

        avail = str(asset.get("availability") or "online").lower().strip()
        is_available = avail not in ("offline", "missing", "staging")

        if not is_available:
            if avail == "offline":
                reason = "Kho Offline — hãy kết nối lại ổ đĩa trước khi chèn"
            elif avail == "missing":
                reason = "File nguồn không tồn tại trên đĩa"
            elif avail == "staging":
                reason = "Đang đồng bộ / nạp tài nguyên — chưa sẵn sàng chèn"
            else:
                reason = "Tài nguyên tạm thời không khả dụng"

            self.lbl_info.setText(f"{name}{dur_str} · ⚠ {reason}")
            self.btn_primary.setEnabled(False)
            self.btn_primary.setToolTip(reason)
            self.btn_tune.setEnabled(True)
            self.btn_more.setEnabled(True)
        else:
            self.lbl_info.setText(f"{name}{dur_str} · {track_hint} · {timecode}")
            self.btn_primary.setEnabled(True)
            self.btn_primary.setToolTip("")
            self.btn_tune.setEnabled(True)
            self.btn_more.setEnabled(True)

    def _build_more_menu(self) -> Optional[QMenu]:
        if not self._current_asset:
            return None

        avail = str(self._current_asset.get("availability") or "online").lower().strip()
        is_available = avail not in ("offline", "missing", "staging")

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
            act_paste = menu.addAction(get_vector_icon("bolt", "#f59e0b", 16), "Dán vào Fusion (Shift+Enter)")
            act_paste.setEnabled(is_available)
            if not is_available:
                act_paste.setToolTip("Tài nguyên không khả dụng")
            act_paste.triggered.connect(self.paste_fusion_triggered.emit)

            act_copy = menu.addAction(get_vector_icon("copy", "#a1a1aa", 16), "Sao chép code Fusion")
            act_copy.triggered.connect(self.copy_code_triggered.emit)

            act_install = menu.addAction(get_vector_icon("download", "#38bdf8", 16), "Cài vào thư mục Fusion")
            act_install.setEnabled(is_available)
            if not is_available:
                act_install.setToolTip("Tài nguyên không khả dụng")
            act_install.triggered.connect(self.install_fusion_triggered.emit)

            menu.addSeparator()

        act_newtrack = menu.addAction(get_vector_icon("plus", "#34d399", 16), "Chèn trên track mới")
        act_newtrack.setEnabled(is_available)
        if not is_available:
            act_newtrack.setToolTip("Tài nguyên không khả dụng")
        act_newtrack.triggered.connect(self.new_track_triggered.emit)

        menu.addSeparator()

        fav_icon = get_vector_icon("star_filled", "#f59e0b", 16) if self._is_fav else get_vector_icon("star", "#a1a1aa", 16)
        fav_label = "Bỏ yêu thích" if self._is_fav else "Thêm vào yêu thích (Ctrl+Enter)"
        act_fav = menu.addAction(fav_icon, fav_label)
        act_fav.triggered.connect(self.toggle_fav_triggered.emit)

        act_folder = menu.addAction(get_vector_icon("folder", "#fbbf24", 16), "Mở thư mục chứa")
        act_folder.triggered.connect(self.open_folder_triggered.emit)

        return menu

    def _show_more_menu(self):
        menu = self._build_more_menu()
        if not menu:
            return

        self.menu_visibility_changed.emit(True)
        menu.aboutToHide.connect(lambda: self.menu_visibility_changed.emit(False))

        # Position menu right above the more button
        btn_pos = self.btn_more.mapToGlobal(self.btn_more.rect().topLeft())
        menu.exec(btn_pos - menu.sizeHint())
