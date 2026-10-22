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
    QFrame, QFileDialog, QScrollArea, QMenu, QApplication, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from src.core.transcriber import suggest_default_model, is_model_on_disk, MODEL_DISK_SIZES
from src.ui.director.director_state import DirectorConfig, SourceClipInfo


def probe_video_file(fpath: str, whisper_model: str = "small", whisper_lang: str = "vi") -> SourceClipInfo:
    """Trích xuất thời lượng và thông số kỹ thuật thực tế của clip video kèm kiểm tra cache."""
    fname = os.path.basename(fpath)
    file_size_mb = 0.0
    try:
        file_size_mb = round(os.path.getsize(fpath) / (1024 * 1024), 1)
    except Exception:
        pass

    # 1. Tra cứu bộ nhớ đệm thực tế từ ScanCacheManager
    has_cache = False
    duration_sec = 0.0
    try:
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()
        cached = cache_mgr.get_cached_scan(fpath, whisper_model, whisper_lang)
        if cached:
            has_cache = True
            duration_sec = float(cached.get("clip_dur") or cached.get("duration") or 0.0)
    except Exception:
        pass

    # 2. Nếu chưa có duration từ cache, probe thực tế bằng OpenCV
    resolution = "1080p"
    if not has_cache:
        try:
            import cv2
            cap = cv2.VideoCapture(fpath)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
                if duration_sec <= 0.0 and fps > 0 and frames > 0:
                    duration_sec = round(frames / fps, 2)
                if h >= 2160 or w >= 3840:
                    resolution = "4K"
                elif h >= 1440:
                    resolution = "2K"
                elif h >= 1080:
                    resolution = "1080p"
                elif h >= 720:
                    resolution = "720p"
                elif w > 0 and h > 0:
                    resolution = f"{w}x{h}"
                cap.release()
        except Exception:
            pass
    else:
        # Nếu đã có cache, ước lượng resolution siêu tốc không đọc đĩa USB
        resolution = "4K" if (file_size_mb > 350 or "dji" in fname.lower() or "4k" in fname.lower()) else "1080p"

    if duration_sec <= 0.0:
        try:
            from src.core.autocut import get_media_metadata
            meta = get_media_metadata(fpath)
            if meta and meta.get("duration"):
                duration_sec = float(meta["duration"])
        except Exception:
            pass

    if duration_sec <= 0.0:
        duration_sec = 10.0  # Fallback tối thiểu

    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    duration_str = f"{mins:02d}:{secs:02d}"

    return SourceClipInfo(
        path=fpath,
        filename=fname,
        duration_sec=duration_sec,
        duration_str=duration_str,
        resolution=resolution,
        has_cache=has_cache,
        file_size_mb=file_size_mb
    )


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
    """Một dòng hiển thị clip nguồn đã chọn kèm trạng thái cache thực tế."""
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
        lbl_cache = QLabel("🟢 Đã có cache" if self.clip.has_cache else "🟡 Chưa quét thô")
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

        # Thời lượng & Độ phân giải thật
        dur_display = f"{self.clip.duration_str} · {self.clip.resolution}"
        if self.clip.file_size_mb > 0:
            dur_display += f" ({self.clip.file_size_mb}MB)"
        lbl_dur = QLabel(dur_display)
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
    rough_scan_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._clip_infos: List[SourceClipInfo] = []
        settings = QSettings("ResolveFlow", "ResolveFlowAssistant")
        saved_model = settings.value("whisper_model", "")
        self._whisper_model = str(saved_model) if saved_model else suggest_default_model()
        self._whisper_lang = str(settings.value("whisper_lang", "vi"))
        self._init_ui()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.add_paths(paths)
        else:
            super().dropEvent(event)

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

        # Nút Quét Thô Nổi Bật
        self.btn_scan = QPushButton("⚡ Quét thô (Whisper)")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.clicked.connect(self.rough_scan_requested.emit)
        self.btn_scan.setVisible(False)
        h_list_header.addWidget(self.btn_scan)

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
            self, "Chọn tệp video", "", "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.m2ts)"
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
        for m in ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]:
            action = menu.addAction(f"Model: {m}")
            action.triggered.connect(lambda checked=False, model=m: self._set_model(model))

        menu.addSeparator()
        for lang, code in [("Tiếng Việt", "vi"), ("Tiếng Anh", "en"), ("Tự nhận diện", "auto")]:
            action = menu.addAction(f"Ngôn ngữ: {lang}")
            action.triggered.connect(lambda checked=False, c=code: self._set_lang(c))

        menu.exec(self.btn_whisper_chip.mapToGlobal(self.btn_whisper_chip.rect().bottomLeft()))

    def _set_model(self, model: str):
        if not is_model_on_disk(model):
            size_str = MODEL_DISK_SIZES.get(model, "~1.5 GB")
            reply = QMessageBox.question(
                self,
                "Tải mô hình Whisper",
                f"Mô hình '{model}' chưa có trên máy.\n"
                f"Ứng dụng sẽ cần tải khoảng {size_str} từ internet khi quét.\n\n"
                f"Bạn có muốn chọn mô hình này không?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._whisper_model = model
        settings = QSettings("ResolveFlow", "ResolveFlowAssistant")
        settings.setValue("whisper_model", self._whisper_model)
        self._update_whisper_label()
        self.recheck_cache()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _set_lang(self, lang: str):
        self._whisper_lang = lang
        settings = QSettings("ResolveFlow", "ResolveFlowAssistant")
        settings.setValue("whisper_lang", self._whisper_lang)
        self._update_whisper_label()
        self.recheck_cache()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _update_whisper_label(self):
        self.btn_whisper_chip.setText(f"Whisper {self._whisper_model} · {self._whisper_lang}")

    def recheck_cache(self):
        """Cập nhật lại trạng thái cache thực tế của các clip khi đổi model hoặc ngôn ngữ."""
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()
        for c in self._clip_infos:
            cached = cache_mgr.get_cached_scan(c.path, self._whisper_model, self._whisper_lang)
            c.has_cache = bool(cached)
            if cached and cached.get("clip_dur"):
                c.duration_sec = float(cached["clip_dur"])
                mins = int(c.duration_sec // 60)
                secs = int(c.duration_sec % 60)
                c.duration_str = f"{mins:02d}:{secs:02d}"
        self._refresh_clip_list()

    def add_paths(self, paths: List[str]):
        """Thêm các đường dẫn tệp hoặc quét thư mục tìm video với thông số kỹ thuật thực."""
        valid_exts = {
            ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
            ".flv", ".wmv", ".ts", ".mts", ".m2ts"
        }
        found_files = []

        system_ignore_dirs = {
            ".git", ".svn", ".cache", "__pycache__", ".idea", ".vscode",
            "_timeline_import", "_archive_xml", ".pytest_cache", ".venv", "venv"
        }

        for p in paths:
            if not p:
                continue
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    # Chỉ bỏ qua các thư mục hệ thống / cache ở cấp con hiện tại
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith((".", "__")) and d.lower() not in system_ignore_dirs
                    ]
                    for f in files:
                        if not f.startswith(".") and os.path.splitext(f)[1].lower() in valid_exts:
                            found_files.append(os.path.join(root, f))
            elif os.path.isfile(p):
                if not os.path.basename(p).startswith(".") and os.path.splitext(p)[1].lower() in valid_exts:
                    found_files.append(p)

        if not found_files and paths:
            folder_names = ", ".join([os.path.basename(p) or p for p in paths])
            QMessageBox.warning(
                self,
                "Không tìm thấy video",
                f"Không tìm thấy tệp video hợp lệ nào trong:\n{folder_names}\n\n"
                "Định dạng hỗ trợ: MP4, MOV, MKV, AVI, WEBM, M4V, FLV, WMV, TS, MTS, M2TS."
            )
            return

        from src.core.chronological_sorter import sort_clips_chronologically
        found_files = sort_clips_chronologically(found_files)

        total_files = len(found_files)
        for idx, fpath in enumerate(found_files):
            if not any(c.path == fpath for c in self._clip_infos):
                info = probe_video_file(fpath, self._whisper_model, self._whisper_lang)
                self._clip_infos.append(info)
            if idx % 5 == 0:
                QApplication.processEvents()

        self._clip_infos = sort_clips_chronologically(self._clip_infos)
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

        for idx, clip in enumerate(self._clip_infos):
            row = SourceClipRow(clip, self.list_container)
            row.remove_requested.connect(self.remove_path)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            if idx % 20 == 0:
                QApplication.processEvents()

        count = len(self._clip_infos)
        self.lbl_count.setText(f"Đã chọn · {count} clip nguồn" if count > 0 else "Chưa chọn clip nào")

        uncached_count = sum(1 for c in self._clip_infos if not c.has_cache)
        if count > 0:
            self.btn_scan.setVisible(True)
            self.btn_scan.setEnabled(True)
            if uncached_count > 0:
                self.btn_scan.setText(f"⚡ Quét thô Whisper ({uncached_count} clip chưa cache)")
                self.btn_scan.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c3aed, stop:1 #4f46e5);
                        color: #ffffff;
                        border: 1px solid #a78bfa;
                        border-radius: 12px;
                        padding: 3px 12px;
                        font-size: 11px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #6366f1);
                        border-color: #c4b5fd;
                    }
                """)
                self.btn_scan.setToolTip("Bóc tách lời thoại và phát hiện khoảng lặng bằng Whisper để chuẩn bị kịch bản AI")
            else:
                self.btn_scan.setText("✓ Đã có cache toàn bộ (Quét lại)")
                self.btn_scan.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(16, 185, 129, 0.15);
                        color: #34d399;
                        border: 1px solid rgba(16, 185, 129, 0.4);
                        border-radius: 12px;
                        padding: 3px 12px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: rgba(16, 185, 129, 0.25);
                        color: #6ee7b7;
                    }
                """)
                self.btn_scan.setToolTip("Toàn bộ clip đã có dữ liệu cache. Bấm nếu muốn quét lại bằng Whisper model khác.")
        else:
            self.btn_scan.setVisible(False)

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
