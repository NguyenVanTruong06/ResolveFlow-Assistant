"""
src/ui/director/pages/source_page.py
Bước 1: Quét nguồn Footage & Quản lý Cache.
Bao gồm:
- Vùng kéo thả tệp/thư mục video.
- 4 nút chọn nguồn: Thư mục, Tệp video, Từ DaVinci Resolve, Google Drive.
- Danh sách clip kèm trạng thái cache (Xanh: đã có cache; Vàng: chưa phân tích).
- Chip nhỏ "Whisper small · vi" cấu hình nhận diện.
"""

import os
from typing import List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QScrollArea, QMenu
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from src.ui.director.director_state import DirectorConfig, SourceClipInfo


class DropZoneWidget(QFrame):
    """Vùng kéo thả tệp video hoặc thư mục."""
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(120)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(24, 24, 27, 0.6);
                border: 2px dashed #3f3f46;
                border-radius: 10px;
            }
            QFrame:hover {
                border-color: #8b5cf6;
                background-color: rgba(124, 58, 237, 0.06);
            }
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        lbl_icon = QLabel("📥")
        lbl_icon.setStyleSheet("font-size: 26px;")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_icon)

        lbl_text = QLabel("Kéo thả tệp video (.mp4, .mov, .mkv) hoặc thư mục vào đây")
        lbl_text.setStyleSheet("color: #e4e4e7; font-size: 12.5px; font-weight: 600;")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_text)

        lbl_sub = QLabel("Hỗ trợ dựng multi-cam và ghép nhiều clip cùng lúc")
        lbl_sub.setStyleSheet("color: #71717a; font-size: 11px;")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_sub)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(124, 58, 237, 0.15);
                    border: 2px dashed #a78bfa;
                    border-radius: 10px;
                }
            """)

    def dragLeaveEvent(self, event):
        self._init_ui()

    def dropEvent(self, event: QDropEvent):
        paths = []
        for url in event.mimeData().urls():
            local_file = url.toLocalFile()
            if os.path.exists(local_file):
                paths.append(local_file)
        self._init_ui()
        if paths:
            self.files_dropped.emit(paths)


class SourceClipRow(QFrame):
    """Một dòng hiển thị clip nguồn đã chọn kèm trạng thái cache."""
    remove_requested = Signal(str)

    def __init__(self, clip: SourceClipInfo, parent=None):
        super().__init__(parent)
        self.clip = clip
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(38)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 0 10px;
            }
            QFrame:hover {
                border-color: #3f3f46;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(10)

        # Icon video
        lbl_icon = QLabel("🎬")
        layout.addWidget(lbl_icon)

        # Tên file
        lbl_name = QLabel(self.clip.filename)
        lbl_name.setStyleSheet("color: #ffffff; font-size: 12px; font-weight: 500;")
        layout.addWidget(lbl_name, stretch=1)

        # Cache badge
        lbl_cache = QLabel("đã có cache" if self.clip.has_cache else "chưa phân tích")
        if self.clip.has_cache:
            lbl_cache.setStyleSheet("""
                color: #4ade80;
                background: rgba(34, 197, 94, 0.15);
                border: 1px solid rgba(34, 197, 94, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
        else:
            lbl_cache.setStyleSheet("""
                color: #fbbf24;
                background: rgba(245, 158, 11, 0.15);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
        layout.addWidget(lbl_cache)

        # Thời lượng
        lbl_dur = QLabel(self.clip.duration_str)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(lbl_dur)

        # Nút xóa
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #71717a;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        btn_del.clicked.connect(lambda: self.remove_requested.emit(self.clip.path))
        layout.addWidget(btn_del)


class SourcePage(QWidget):
    """Trang Bước 1: Nguồn."""
    clips_changed = Signal(list)
    whisper_changed = Signal(str, str)
    resolve_import_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clip_infos: List[SourceClipInfo] = []
        self._whisper_model = "small"
        self._whisper_lang = "vi"
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        # Header hàng nút nguồn
        h_source_bar = QHBoxLayout()
        h_source_bar.setSpacing(10)

        lbl_step_hint = QLabel("Chọn footage nguồn để bắt đầu:")
        lbl_step_hint.setStyleSheet("color: #ffffff; font-size: 13px; font-weight: bold;")
        h_source_bar.addWidget(lbl_step_hint)
        h_source_bar.addStretch(1)

        # 4 nút nguồn
        btn_folder = QPushButton("📁 Thư mục")
        btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_folder.setStyleSheet(self._btn_source_style())
        btn_folder.clicked.connect(self._on_pick_folder)
        h_source_bar.addWidget(btn_folder)

        btn_files = QPushButton("🎞️ Tệp video")
        btn_files.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_files.setStyleSheet(self._btn_source_style())
        btn_files.clicked.connect(self._on_pick_files)
        h_source_bar.addWidget(btn_files)

        btn_resolve = QPushButton("⚡ Từ DaVinci")
        btn_resolve.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_resolve.setStyleSheet(self._btn_source_style(primary=True))
        btn_resolve.clicked.connect(self.resolve_import_requested.emit)
        h_source_bar.addWidget(btn_resolve)

        btn_drive = QPushButton("☁️ Google Drive")
        btn_drive.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_drive.setStyleSheet(self._btn_source_style())
        btn_drive.clicked.connect(self._on_pick_drive)
        h_source_bar.addWidget(btn_drive)

        layout.addLayout(h_source_bar)

        # Drop Zone
        self.drop_zone = DropZoneWidget(self)
        self.drop_zone.files_dropped.connect(self.add_paths)
        layout.addWidget(self.drop_zone)

        # Danh sách clip
        h_list_header = QHBoxLayout()
        self.lbl_count = QLabel("Chưa chọn clip nào")
        self.lbl_count.setStyleSheet("color: #a1a1aa; font-size: 11.5px; font-weight: 600;")
        h_list_header.addWidget(self.lbl_count)

        h_list_header.addStretch(1)

        # Chip Whisper ở góc
        self.btn_whisper_chip = QPushButton(f"Whisper {self._whisper_model} · {self._whisper_lang}")
        self.btn_whisper_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_whisper_chip.setStyleSheet("""
            QPushButton {
                background-color: rgba(124, 58, 237, 0.15);
                color: #c4b5fd;
                border: 1px solid rgba(124, 58, 237, 0.35);
                border-radius: 12px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(124, 58, 237, 0.25);
            }
        """)
        self.btn_whisper_chip.clicked.connect(self._on_whisper_chip_clicked)
        h_list_header.addWidget(self.btn_whisper_chip)
        layout.addLayout(h_list_header)

        # Scroll area danh sách clip
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("background: transparent;")

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)
        layout.addWidget(self.scroll, stretch=1)

    def _btn_source_style(self, primary: bool = False) -> str:
        if primary:
            return """
                QPushButton {
                    background-color: rgba(124, 58, 237, 0.2);
                    color: #c4b5fd;
                    border: 1px solid #7c3aed;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 11.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #7c3aed;
                    color: #ffffff;
                }
            """
        return """
            QPushButton {
                background-color: #18181b;
                color: #e4e4e7;
                border: 1px solid #27272a;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11.5px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #27272a;
                border-color: #3f3f46;
            }
        """

    def _on_pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa video")
        if folder:
            self.add_paths([folder])

    def _on_pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Chọn tệp video", "", "Video files (*.mp4 *.mov *.mkv *.avi *.webm)"
        )
        if files:
            self.add_paths(files)

    def _on_pick_drive(self):
        # Placeholder cho Google Drive
        pass

    def _on_whisper_chip_clicked(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                color: #ffffff;
                border: 1px solid #33333a;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #7c3aed;
            }
        """)
        for m in ["tiny", "base", "small", "medium", "large-v3"]:
            action = menu.addAction(f"Model: {m}")
            action.triggered.connect(lambda checked=False, model=m: self._set_model(model))

        menu.addSeparator()
        for lang, code in [("Tiếng Việt", "vi"), ("Tiếng Anh", "en"), ("Tự nhận diện", "auto")]:
            action = menu.addAction(f"Ngôn ngữ: {lang}")
            action.triggered.connect(lambda checked=False, c=code: self._set_lang(c))

        menu.exec(self.btn_whisper_chip.mapToGlobal(self.btn_whisper_chip.rect().bottomLeft()))

    def _set_model(self, model: str):
        self._whisper_model = model
        self._update_whisper_label()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _set_lang(self, lang: str):
        self._whisper_lang = lang
        self._update_whisper_label()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _update_whisper_label(self):
        self.btn_whisper_chip.setText(f"Whisper {self._whisper_model} · {self._whisper_lang}")

    def add_paths(self, paths: List[str]):
        """Thêm các đường dẫn tệp hoặc quét thư mục tìm video."""
        valid_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
        found_files = []

        for p in paths:
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in valid_exts:
                            found_files.append(os.path.join(root, f))
            elif os.path.isfile(p):
                if os.path.splitext(p)[1].lower() in valid_exts:
                    found_files.append(p)

        for fpath in found_files:
            if not any(c.path == fpath for c in self._clip_infos):
                fname = os.path.basename(fpath)
                # Kiểm tra cache nhanh (giả lập hoặc check folder cache)
                has_cache = os.path.exists(fpath + ".cache") or ("cache" in fname.lower())
                info = SourceClipInfo(
                    path=fpath,
                    filename=fname,
                    duration_sec=120.0,
                    duration_str="02:00",
                    resolution="1080p",
                    has_cache=has_cache
                )
                self._clip_infos.append(info)

        self._refresh_clip_list()
        self.clips_changed.emit([c.path for c in self._clip_infos])

    def remove_path(self, path: str):
        self._clip_infos = [c for c in self._clip_infos if c.path != path]
        self._refresh_clip_list()
        self.clips_changed.emit([c.path for c in self._clip_infos])

    def _refresh_clip_list(self):
        # Xóa các widget cũ trong list_layout
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for clip in self._clip_infos:
            row = SourceClipRow(clip, self.list_container)
            row.remove_requested.connect(self.remove_path)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

        count = len(self._clip_infos)
        self.lbl_count.setText(f"Đã chọn · {count} clip nguồn" if count > 0 else "Chưa chọn clip nào")

    def load_from_config(self, config: DirectorConfig):
        self._whisper_model = config.whisper_model
        self._whisper_lang = config.whisper_language
        self._update_whisper_label()
        if config.video_paths and not self._clip_infos:
            self.add_paths(config.video_paths)

    def save_to_config(self, config: DirectorConfig):
        config.video_paths = [c.path for c in self._clip_infos]
        config.clip_infos = list(self._clip_infos)
        config.whisper_model = self._whisper_model
        config.whisper_language = self._whisper_lang
