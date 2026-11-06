"""
src/ui/director/pages/source_page.py
Bước 1: Quét nguồn Footage & Quản lý Cache.
Bao gồm:
- Vùng kéo thả tệp/thư mục video.
- 4 nút chọn nguồn: Thư mục, Tệp video, Từ DaVinci Resolve, Google Drive.
- Danh sách clip kèm trạng thái cache (Xanh: đã có cache; Vàng: chưa phân tích).
- Chip nhỏ "Whisper small · vi" cấu hình nhận diện.
"""

import logging
import os
import uuid
from dataclasses import dataclass
from threading import RLock
from typing import Any, List, Optional, Dict, Tuple, Callable
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QFileDialog, QScrollArea, QMenu, QApplication, QMessageBox, QInputDialog, QLineEdit,
    QCheckBox, QComboBox
)
from PySide6.QtCore import Qt, Signal, QSettings, QThread, Slot, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from src.core.transcriber import suggest_default_model, is_model_on_disk, MODEL_DISK_SIZES
from src.ui.director.director_state import DirectorConfig, SourceClipInfo
from src.ui.director.workers.source_analysis_worker import (
    SourceAnalysisConfig,
    SourceAnalysisWorker,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredAnalysisSnapshot:
    """Snapshot bất biến cấu hình đầy đủ của phiên phân tích nguồn (Stage 1, 2, 3)."""
    whisper_model: str
    whisper_language: str
    enable_visual_describe: bool
    vision_model: str
    video_paths: Tuple[str, ...]
    vision_endpoint: str = "http://127.0.0.1:11434"
    window_sec: float = 8.0
    frames_per_window: int = 2
    max_windows: int = 10
    max_refinements: int = 1
    enable_audio: bool = True
    enable_visual_sampling: bool = True
    visual_interval_sec: float = 2.0
    visual_max_samples: int = 120
    job_id: str = ""

    def __iter__(self):
        yield self.whisper_model
        yield self.whisper_language
        yield self.enable_visual_describe
        yield self.vision_model
        yield self.video_paths

    def __len__(self):
        return 5

    def __getitem__(self, idx):
        return (
            self.whisper_model,
            self.whisper_language,
            self.enable_visual_describe,
            self.vision_model,
            self.video_paths,
        )[idx]

PRIMARY_SETTINGS_ORG = "ChunDVC"
PRIMARY_SETTINGS_APP = "ResolveFlow"
LEGACY_SETTINGS_ORG = "ResolveFlow"
LEGACY_SETTINGS_APP = "ResolveFlowAssistant"


def _get_primary_settings() -> QSettings:
    """Tạo hoặc lấy đối tượng QSettings cho Primary Domain ('ChunDVC', 'ResolveFlow')."""
    return QSettings(PRIMARY_SETTINGS_ORG, PRIMARY_SETTINGS_APP)


def _get_legacy_settings() -> QSettings:
    """Tạo hoặc lấy đối tượng QSettings cho Legacy Domain ('ResolveFlow', 'ResolveFlowAssistant')."""
    return QSettings(LEGACY_SETTINGS_ORG, LEGACY_SETTINGS_APP)


def _is_valid_whisper_setting_value(val: Any) -> bool:
    """
    Xác thực giá trị cấu hình Whisper:
    - Phải là chuỗi (str).
    - Không rỗng, không toàn khoảng trắng.
    - Không chấp nhận bool, int, list, dict, None...
    - Không ép kiểu str() cho dữ liệu hỏng.
    - Không trim/normalize giá trị đã hợp lệ để tránh đổi cache identity.
    """
    if not isinstance(val, str) or isinstance(val, bool):
        return False
    return len(val.strip()) > 0


def _import_legacy_whisper_setting(primary: QSettings, key: str, val: str) -> bool:
    """
    Thử import một giá trị hợp lệ từ legacy domain sang primary domain.
    Không xóa dữ liệu legacy, không tạo migration marker giả.
    Nếu có lỗi hoặc status không NoError, ghi log cảnh báo ngắn nhưng không làm gián đoạn giá trị đọc.
    """
    try:
        primary.setValue(key, val)
        primary.sync()
        status = primary.status()
        if status != QSettings.Status.NoError:
            logger.warning("Không thể import cấu hình '%s' sang primary domain (status=%s)", key, status)
            return False
        return True
    except Exception as exc:
        logger.warning("Ngoại lệ khi import cấu hình '%s' sang primary domain: %s", key, exc)
        return False


def _read_whisper_setting(
    key: str,
    default: str,
    primary: Optional[QSettings] = None,
    legacy: Optional[QSettings] = None,
) -> str:
    """
    Đọc cấu hình Whisper theo cơ chế tương thích (Precedence + Import-on-read):
    1. Kiểm tra Primary: nếu primary.contains(key) và value hợp lệ -> trả về primary value (không ghi lại).
    2. Fallback sang Legacy: nếu primary absent/invalid và legacy.contains(key) có value hợp lệ
       -> trả về legacy value và thử import-once sang primary (safe write).
    3. Nếu cả hai absent hoặc invalid -> trả về default (không ghi default vào đâu).
    """
    p = primary if primary is not None else _get_primary_settings()
    l = legacy if legacy is not None else _get_legacy_settings()

    # 1. Kiểm tra Primary
    try:
        if p.contains(key):
            val = p.value(key)
            if _is_valid_whisper_setting_value(val):
                return val
    except Exception as exc:
        logger.warning("Lỗi đọc cấu hình '%s' từ primary settings: %s", key, exc)

    # 2. Fallback sang Legacy
    try:
        if l.contains(key):
            val = l.value(key)
            if _is_valid_whisper_setting_value(val):
                _import_legacy_whisper_setting(p, key, val)
                return val
    except Exception as exc:
        logger.warning("Lỗi đọc cấu hình '%s' từ legacy settings: %s", key, exc)

    # 3. Fallback về default
    return default


def _write_whisper_setting(
    key: str,
    val: str,
    primary: Optional[QSettings] = None,
) -> bool:
    """
    Ghi cấu hình Whisper vào primary domain (ChunDVC/ResolveFlow).
    Không mirror về legacy.
    Kiểm tra sync() và status(). Bắt ngoại lệ để không crash app.
    """
    p = primary if primary is not None else _get_primary_settings()
    try:
        p.setValue(key, val)
        p.sync()
        status = p.status()
        if status != QSettings.Status.NoError:
            logger.warning("Lỗi ghi cấu hình '%s' vào primary settings (status=%s)", key, status)
            return False
        return True
    except Exception as exc:
        logger.warning("Ngoại lệ khi ghi cấu hình '%s' vào primary settings: %s", key, exc)
        return False


# LRU-style / Size-bounded memory cache cho metadata video gốc (tối đa 1000 file)
_PROBE_MEMO: Dict[Tuple[str, int, int], SourceClipInfo] = {}
_MAX_MEMO_ITEMS = 1000
_SORT_MEMO: dict = {}
_MEMO_LOCK = RLock()


def _remember(cache, key, value):
    # Loại phiên bản cũ của cùng file, giới hạn RAM mà không xóa cả cache.
    with _MEMO_LOCK:
        for old in list(cache):
            if old[0] == key[0]:
                del cache[old]
        if len(cache) >= _MAX_MEMO_ITEMS:
            del cache[next(iter(cache))]
        cache[key] = value


def _source_sort_key(path):
    from src.core.chronological_sorter import get_chronological_sort_key
    stat = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), stat.st_mtime_ns, stat.st_size)
    with _MEMO_LOCK:
        cached = _SORT_MEMO.get(key)
    if cached is None:
        cached = get_chronological_sort_key(path)
        _remember(_SORT_MEMO, key, cached)
    return cached



def probe_video_file(fpath: str, whisper_model: str = "small", whisper_lang: str = "vi", force_refresh: bool = False) -> SourceClipInfo:
    """Trích xuất thời lượng và thông số kỹ thuật thực tế của clip video kèm kiểm tra cache (trả về bản sao độc lập)."""
    norm_path = os.path.normcase(os.path.abspath(fpath))
    fname = os.path.basename(norm_path)
    file_size_mb = 0.0
    mtime_ns = 0
    file_size_bytes = 0
    try:
        stat = os.stat(norm_path)
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))
        file_size_bytes = stat.st_size
        file_size_mb = round(file_size_bytes / (1024 * 1024), 1)
    except Exception:
        pass

    memo_key = (norm_path, mtime_ns, file_size_bytes)
    with _MEMO_LOCK:
        base_info = None if force_refresh else _PROBE_MEMO.get(memo_key)

    if base_info is None:
        # 1. Trích xuất resolution và duration gốc
        duration_sec = 0.0
        resolution = "1080p"
        try:
            import cv2
            cap = cv2.VideoCapture(norm_path)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
                if fps > 0 and frames > 0:
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

        if duration_sec <= 0.0:
            try:
                from src.core.autocut import get_media_metadata
                meta = get_media_metadata(norm_path)
                if meta and meta.get("duration"):
                    duration_sec = float(meta["duration"])
            except Exception:
                pass

        if duration_sec <= 0.0:
            duration_sec = 10.0  # Fallback tối thiểu

        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)
        duration_str = f"{mins:02d}:{secs:02d}"

        base_info = SourceClipInfo(
            path=norm_path,
            filename=fname,
            duration_sec=duration_sec,
            duration_str=duration_str,
            resolution=resolution,
            has_cache=False,
            file_size_mb=file_size_mb
        )
        _remember(_PROBE_MEMO, memo_key, base_info)

    # 2. Kiểm tra trạng thái Scan Cache theo model và language hiện tại (không dùng chung instance mutable)
    has_cache = False
    duration_sec = base_info.duration_sec
    try:
        from src.core.cache_manager import ScanCacheManager
        cache_mgr = ScanCacheManager()
        cached = cache_mgr.get_cached_scan(norm_path, whisper_model, whisper_lang)
        if cached:
            has_cache = True
            if cached.get("clip_dur") or cached.get("duration"):
                duration_sec = float(cached.get("clip_dur") or cached.get("duration"))
    except Exception:
        pass

    mins = int(duration_sec // 60)
    secs = int(duration_sec % 60)
    duration_str = f"{mins:02d}:{secs:02d}"

    # Trả về bản sao độc lập (không chia sẻ mutable state)
    return SourceClipInfo(
        path=base_info.path,
        filename=base_info.filename,
        duration_sec=duration_sec,
        duration_str=duration_str,
        resolution=base_info.resolution,
        has_cache=has_cache,
        file_size_mb=base_info.file_size_mb
    )


class SourceScanWorker(QThread):
    """Worker quét thư mục và probe metadata video chạy trên background QThread."""
    batch_loaded = Signal(int, list)  # (job_id, list_of_SourceClipInfo)
    scan_finished = Signal(int, int)   # (job_id, total_found)
    scan_error = Signal(int, str)      # (job_id, error_message)

    def __init__(self, job_id: int, paths: List[str], whisper_model: str, whisper_lang: str, parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self.paths = list(paths)
        self.whisper_model = whisper_model
        self.whisper_lang = whisper_lang
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            self._scan()
        except Exception as exc:
            if not self._is_cancelled:
                self.scan_error.emit(self.job_id, str(exc))

    def _scan(self):
        valid_exts = {
            ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
            ".flv", ".wmv", ".ts", ".mts", ".m2ts"
        }
        system_ignore_dirs = {
            ".git", ".svn", ".cache", "__pycache__", ".idea", ".vscode",
            "_timeline_import", "_archive_xml", ".pytest_cache", ".venv", "venv"
        }

        found_files: List[str] = []
        for p in self.paths:
            if self._is_cancelled:
                return
            if not p:
                continue
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for root, dirs, files in os.walk(p):
                    if self._is_cancelled:
                        return
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

        if self._is_cancelled:
            return

        if not found_files:
            self.scan_finished.emit(self.job_id, 0)
            return

        keys = {}
        for path in dict.fromkeys(found_files):
            if self._is_cancelled:
                return
            keys[path] = _source_sort_key(path)
        found_files = sorted(keys, key=keys.get)

        batch: List[SourceClipInfo] = []
        for idx, fpath in enumerate(found_files):
            if self._is_cancelled:
                return
            info = probe_video_file(fpath, self.whisper_model, self.whisper_lang)
            info.sort_key = keys[fpath]
            if self._is_cancelled:
                return
            batch.append(info)

            # Phát theo lô 10 clip hoặc clip cuối cùng
            if len(batch) >= 10 or idx == len(found_files) - 1:
                self.batch_loaded.emit(self.job_id, list(batch))
                batch.clear()

        if not self._is_cancelled:
            self.scan_finished.emit(self.job_id, len(found_files))


class VisualScanWorker(QThread):
    """Lấy mẫu ảnh nguồn ở luồng nền; không chạy model hoặc Whisper."""

    progress = Signal(int, int)
    scan_done = Signal(int, int, int)
    scan_error = Signal(str)

    def __init__(self, job_id: int, paths: List[str], parent=None):
        super().__init__(parent)
        self.job_id = job_id
        self.paths = list(paths)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        from src.core.visual_index import VisualIndex

        index = VisualIndex()
        succeeded = 0
        reused = 0
        for path in self.paths:
            if self._is_cancelled:
                return
            try:
                result = index.scan(path, cancelled=lambda: self._is_cancelled)
            except InterruptedError:
                return
            except Exception as exc:
                self.scan_error.emit(f"{os.path.basename(path)}: {exc}")
            else:
                succeeded += 1
                reused += int(result.get("cache_hit", False))
            self.progress.emit(succeeded, len(self.paths))
        if not self._is_cancelled:
            self.scan_done.emit(succeeded, reused, len(self.paths))


class VisualDescribeWorker(QThread):
    """Gọi model thị giác local trên worker, lưu mô tả vào visual cache."""

    progress = Signal(int, int)
    describe_done = Signal(int, int)
    describe_error = Signal(str)
    window_progress = Signal(int, int)

    def __init__(self, job_id: int, paths: List[str], model: str, endpoint: str, parent=None, force=False):
        super().__init__(parent)
        self.job_id = job_id
        self.paths = list(paths)
        self.model = model
        self.endpoint = endpoint
        self.force = force
        self._is_cancelled = False
        self.errors: List[str] = []

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        from src.core.visual_describer import describe_with_ollama
        from src.core.visual_index import VisualIndex

        index = VisualIndex()
        completed = 0
        total = len(self.paths)
        for path in self.paths:
            if self._is_cancelled:
                return
            try:
                describe_with_ollama(
                    path, self.model, self.endpoint, index=index,
                    cancelled=lambda: self._is_cancelled,
                    progress=lambda done, count: self.window_progress.emit(done, count),
                    force=self.force,
                )
                if self._is_cancelled:
                    return
                analysis = (index.load(path) or {}).get("analysis", {})
                if analysis.get("status") == "complete":
                    completed += 1
                else:
                    message = "Đã phân tích một phần hoặc còn đoạn cần duyệt"
                    self.errors.append(f"{os.path.basename(path)}: {message}")
                    self.describe_error.emit(self.errors[-1])
            except InterruptedError:
                return
            except Exception as exc:
                err_msg = f"{os.path.basename(path)}: {exc}"
                self.errors.append(err_msg)
                self.describe_error.emit(err_msg)
            self.progress.emit(completed, total)
        if not self._is_cancelled:
            self.describe_done.emit(completed, total)


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
                background-color: #191a1e;
                border: 1px dashed #3d414c;
                border-radius: 5px;
            }
            QFrame:hover {
                border-color: #e05a47;
                background-color: #202227;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)

        lbl_icon = QLabel("📥")
        lbl_icon.setStyleSheet("font-size: 24px;")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_icon)

        lbl_text = QLabel("Kéo thả tệp video (.mp4, .mov, .mkv) hoặc thư mục vào đây")
        lbl_text.setStyleSheet("color: #e6e8ec; font-size: 12.5px; font-weight: 600;")
        lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_text)

        lbl_sub = QLabel("Hỗ trợ dựng multi-cam và ghép nhiều clip cùng lúc")
        lbl_sub.setStyleSheet("color: #7c828f; font-size: 11px;")
        lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_sub)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QFrame {
                    background-color: rgba(217, 79, 61, 0.12);
                    border: 1.5px dashed #e86352;
                    border-radius: 5px;
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
        self.lbl_cache: Optional[QLabel] = None
        self.btn_del: Optional[QPushButton] = None
        self._init_ui()

    def _init_ui(self):
        self.setFixedHeight(38)
        self.setStyleSheet("""
            QFrame {
                background-color: #17171d;
                border: 1px solid #282832;
                border-radius: 7px;
                padding: 0 10px;
            }
            QFrame:hover {
                background-color: #1e1e26;
                border-color: #3f3f4e;
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
        self.lbl_cache = QLabel("🟢 Cache thoại ứng viên" if self.clip.has_cache else "🟡 Chưa phân tích")
        if self.clip.has_cache:
            self.lbl_cache.setStyleSheet("""
                color: #4ade80;
                background: rgba(34, 197, 94, 0.15);
                border: 1px solid rgba(34, 197, 94, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
            self.lbl_cache.setToolTip("File này từng có cache thoại trên đĩa. Sẽ được đối chiếu full SHA256 khi phân tích.")
        else:
            self.lbl_cache.setStyleSheet("""
                color: #fbbf24;
                background: rgba(245, 158, 11, 0.15);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
            self.lbl_cache.setToolTip("Chưa phân tích thoại và khung hình.")
        layout.addWidget(self.lbl_cache)

        # Thời lượng & Độ phân giải thật
        dur_display = f"{self.clip.duration_str} · {self.clip.resolution}"
        if self.clip.file_size_mb > 0:
            dur_display += f" ({self.clip.file_size_mb}MB)"
        lbl_dur = QLabel(dur_display)
        lbl_dur.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        layout.addWidget(lbl_dur)

        # Nút xóa
        self.btn_del = QPushButton("✕")
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_del.setStyleSheet("""
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
        self.btn_del.clicked.connect(lambda: self.remove_requested.emit(self.clip.path))
        layout.addWidget(self.btn_del)

    def reset_to_candidate_status(self):
        """Khôi phục trạng thái hàng về mức ứng viên chưa thẩm định khi cấu hình hoặc nguồn thay đổi."""
        if not self.lbl_cache:
            return
        if self.clip.has_cache:
            self.lbl_cache.setText("🟢 Cache thoại ứng viên")
            self.lbl_cache.setStyleSheet("""
                color: #4ade80;
                background: rgba(34, 197, 94, 0.15);
                border: 1px solid rgba(34, 197, 94, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
            self.lbl_cache.setToolTip("File này từng có cache thoại trên đĩa. Sẽ được đối chiếu full SHA256 khi phân tích.")
        else:
            self.lbl_cache.setText("🟡 Chưa phân tích")
            self.lbl_cache.setStyleSheet("""
                color: #fbbf24;
                background: rgba(245, 158, 11, 0.15);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
            self.lbl_cache.setToolTip("Chưa phân tích thoại và khung hình.")

    def update_analysis_status(self, clip_res: dict):
        """Cập nhật huy hiệu trạng thái thoại và khung hình sau khi phân tích."""
        if not self.lbl_cache:
            return

        audio_st = clip_res.get("audio_status", "pending")
        visual_st = clip_res.get("visual_status", "pending")
        raw_subs = clip_res.get("raw_subtitles", [])
        no_speech = clip_res.get("no_speech", False)
        cache_saved = clip_res.get("cache_saved")
        warning = clip_res.get("warning")
        error = clip_res.get("error")

        # 1. Nhãn thoại (Audio)
        if audio_st == "cached":
            audio_text = "🟢 Thoại: Đã có cache" if not no_speech else "🟢 Thoại: Không có lời thoại"
        elif audio_st == "new":
            if cache_saved is False or warning:
                audio_text = "⚠️ Thoại: Đã dịch (chưa lưu cache)"
            elif no_speech:
                audio_text = "⚪ Thoại: Không nhận diện được lời thoại"
            else:
                audio_text = f"🟢 Thoại: {len(raw_subs)} câu"
        elif audio_st == "no_audio_track":
            audio_text = "🔇 Không có luồng âm thanh"
        elif audio_st == "unavailable":
            audio_text = "⚠️ Thiếu mô hình Whisper"
        elif audio_st == "failed":
            audio_text = "❌ Lỗi âm thanh"
        elif audio_st == "skipped":
            audio_text = "⚪ Thoại: Bỏ qua"
        else:
            audio_text = "⏳ Đang phân tích thoại..."

        # 2. Nhãn khung hình (Visual)
        if visual_st == "cached":
            visual_text = "🟢 Khung hình: Đã có cache"
        elif visual_st == "new":
            visual_text = "🖼️ Đã có khung hình"
        elif visual_st == "failed":
            visual_text = "❌ Lỗi khung hình"
        elif visual_st == "skipped":
            visual_text = "⚪ Khung hình: Bỏ qua"
        else:
            visual_text = "⏳ Đang lấy mẫu..."

        # 3. Nhãn mô tả hình ảnh (Visual Describe - Stage 3)
        vdesc_st = clip_res.get("visual_describe_status", "skipped")
        quality = clip_res.get("analysis_quality")
        descriptions = clip_res.get("descriptions", [])
        coverage = clip_res.get("analysis_coverage", {})
        if not isinstance(coverage, dict):
            coverage = {}

        total_w = coverage.get("total_windows")
        selected_w = coverage.get("selected_windows")
        analyzed_w = coverage.get("analyzed_windows")
        sampled_w = coverage.get("sampled_valid_windows", coverage.get("valid_windows"))

        has_analysis_ratio = (analyzed_w is not None and selected_w is not None and selected_w > 0)

        if vdesc_st == "cached":
            # Cached observations vẫn "cần đối chiếu"
            if has_analysis_ratio:
                vdesc_text = f"🤖 Có mô tả AI (Cache · {analyzed_w}/{selected_w} cửa sổ cần đối chiếu)"
            else:
                vdesc_text = "🤖 Có mô tả AI (Cache · cần đối chiếu)"
        elif vdesc_st == "new":
            if has_analysis_ratio:
                vdesc_text = f"🤖 Có mô tả AI cần đối chiếu (Mới · {analyzed_w}/{selected_w} cửa sổ)"
            else:
                vdesc_text = "🤖 Có mô tả AI cần đối chiếu"
        elif vdesc_st == "partial":
            if has_analysis_ratio:
                if sampled_w is not None and sampled_w > 0:
                    vdesc_text = f"🤖 AI đã phân tích {analyzed_w}/{selected_w} cửa sổ được chọn; khung hình {sampled_w}/{selected_w}"
                else:
                    vdesc_text = f"🤖 AI đã phân tích {analyzed_w}/{selected_w} cửa sổ được chọn"
            else:
                vdesc_text = "🤖 Mô tả AI một phần"
        elif vdesc_st == "unavailable":
            vdesc_text = "⚠️ Mô tả AI không khả dụng"
        elif vdesc_st == "failed":
            vdesc_text = "❌ Lỗi mô tả AI"
        elif vdesc_st == "skipped":
            vdesc_text = "Chỉ khung hình (chưa có mô tả)"
        else:
            vdesc_text = "⏳ Đang mô tả..."

        combined = f"{audio_text} · {visual_text} · {vdesc_text}"
        self.lbl_cache.setText(combined)

        # Style & Tooltip
        is_error = (audio_st == "failed") or (visual_st == "failed") or (vdesc_st == "failed")
        is_warn = (cache_saved is False) or (audio_st == "unavailable") or (vdesc_st in ("unavailable", "partial")) or bool(warning)
        if is_error:
            self.lbl_cache.setStyleSheet("""
                color: #f87171;
                background: rgba(239, 68, 68, 0.15);
                border: 1px solid rgba(239, 68, 68, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
        elif is_warn:
            self.lbl_cache.setStyleSheet("""
                color: #fbbf24;
                background: rgba(245, 158, 11, 0.15);
                border: 1px solid rgba(245, 158, 11, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)
        else:
            self.lbl_cache.setStyleSheet("""
                color: #4ade80;
                background: rgba(34, 197, 94, 0.15);
                border: 1px solid rgba(34, 197, 94, 0.3);
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10.5px;
                font-weight: 600;
            """)

        tt_parts = [f"Clip: {self.clip.filename}"]
        if error:
            tt_parts.append(f"Lỗi: {error}")
        if warning:
            tt_parts.append(f"Cảnh báo: {warning}")
        elif cache_saved is False:
            tt_parts.append("Cảnh báo: Dữ liệu phân tích chưa được lưu vào cache.")
        tt_parts.append("Lấy mẫu khung hình chỉ để xem trước, chưa thực hiện mô tả nội dung hay phân loại hành động AI.")
        self.lbl_cache.setToolTip("\n".join(tt_parts))


class SourcePage(QWidget):
    """Trang Bước 1: Nguồn."""
    clips_changed = Signal(list)
    whisper_changed = Signal(str, str)
    resolve_import_requested = Signal()
    rough_scan_requested = Signal()
    workers_idle = Signal()
    source_analysis_busy_changed = Signal(bool)
    source_analysis_completed = Signal(str, bool, str, dict)  # (job_id, success, message, counters)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._clip_infos: List[SourceClipInfo] = []
        self._current_job_id: int = 0
        self._shutdown_pending = False
        self._close_pending = False
        self._active_worker: Optional[SourceScanWorker] = None
        self._active_visual_worker: Optional[VisualScanWorker] = None
        self._active_describe_worker: Optional[VisualDescribeWorker] = None
        self._active_source_worker: Optional[SourceAnalysisWorker] = None
        self._active_source_job_id: Optional[str] = None
        self._active_source_thread_pending: bool = False
        self._visual_job_id = 0
        self._workers_registry: Dict[Any, Any] = {}
        self._runtime_clip_status: Dict[str, dict] = {}
        self._runtime_status_config: Optional[Any] = None
        self._runtime_vision_endpoint: str = "http://127.0.0.1:11434"
        self._runtime_window_sec: float = 8.0
        self._runtime_frames_per_window: int = 2
        self._runtime_max_windows: int = 10
        self._runtime_max_refinements: int = 1
        self._clip_rows: Dict[str, SourceClipRow] = {}
        # Đọc cấu hình tương thích (Primary > Legacy > Default)
        self._whisper_model = _read_whisper_setting("whisper_model", DirectorConfig().whisper_model)
        self._whisper_lang = _read_whisper_setting("whisper_lang", "vi")
        self._init_ui()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self.is_source_analysis_active():
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        if self.is_source_analysis_active():
            event.ignore()
            return
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
        self.btn_folder = QPushButton("📁 Thư mục")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.setStyleSheet(self._btn_source_style())
        self.btn_folder.clicked.connect(self._on_pick_folder)
        h_source_bar.addWidget(self.btn_folder)

        self.btn_files = QPushButton("🎞️ Tệp video")
        self.btn_files.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_files.setStyleSheet(self._btn_source_style())
        self.btn_files.clicked.connect(self._on_pick_files)
        h_source_bar.addWidget(self.btn_files)

        self.btn_resolve = QPushButton("⚡ Từ DaVinci")
        self.btn_resolve.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_resolve.setStyleSheet(self._btn_source_style(primary=True))
        self.btn_resolve.clicked.connect(self.resolve_import_requested.emit)
        h_source_bar.addWidget(self.btn_resolve)

        self.btn_drive = QPushButton("☁️ Google Drive")
        self.btn_drive.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_drive.setStyleSheet(self._btn_source_style())
        self.btn_drive.clicked.connect(self._on_pick_drive)
        h_source_bar.addWidget(self.btn_drive)

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

        # Nút Xóa tất cả
        self.btn_clear_all = QPushButton("🗑️ Xóa tất cả")
        self.btn_clear_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_all.setStyleSheet("""
            QPushButton {
                background-color: rgba(239, 68, 68, 0.15);
                color: #fca5a5;
                border: 1px solid rgba(239, 68, 68, 0.35);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.3);
                color: #ffffff;
            }
        """)
        self.btn_clear_all.clicked.connect(self.clear_all_paths)
        self.btn_clear_all.setVisible(False)
        h_list_header.addWidget(self.btn_clear_all)

        # Nút Quét Nguồn Nổi Bật (Thoại & Khung hình)
        self.btn_scan = QPushButton("⚡ Phân tích nguồn (Thoại & Khung hình)")
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.clicked.connect(self._on_scan_button_clicked)
        self.btn_scan.setVisible(False)
        h_list_header.addWidget(self.btn_scan)

        self.btn_visual_scan = QPushButton("🖼️ Lấy mẫu hình ảnh")
        self.btn_visual_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_visual_scan.setToolTip("Tạo contact sheet có mốc thời gian; chưa tự nhận diện nội dung cảnh")
        self.btn_visual_scan.setStyleSheet(self._btn_source_style().replace("6px 12px", "3px 8px"))
        self.btn_visual_scan.clicked.connect(self._start_visual_scan)
        self.btn_visual_scan.setVisible(False)
        h_list_header.addWidget(self.btn_visual_scan)

        self.btn_visual_folder = QPushButton("📂 Ảnh")
        self.btn_visual_folder.setToolTip("Mở thư mục contact sheet của footage đã lấy mẫu")
        self.btn_visual_folder.setStyleSheet(self._btn_source_style().replace("6px 12px", "3px 8px"))
        self.btn_visual_folder.clicked.connect(self._open_visual_folder)
        self.btn_visual_folder.setVisible(False)
        h_list_header.addWidget(self.btn_visual_folder)

        self.btn_describe = QPushButton("🤖 Mô tả ảnh")
        self.btn_describe.setToolTip("Dùng model thị giác Ollama đã cài trên máy để mô tả contact sheet")
        self.btn_describe.setStyleSheet(self._btn_source_style().replace("6px 12px", "3px 8px"))
        self.btn_describe.clicked.connect(self._ask_visual_model)
        self.btn_describe.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_describe.customContextMenuRequested.connect(self._visual_description_menu)
        self.btn_describe.setVisible(False)
        h_list_header.addWidget(self.btn_describe)

        # Chip Whisper ở góc
        self.btn_whisper_chip = QPushButton(f"Whisper {self._whisper_model} · {self._whisper_lang}")
        self.btn_whisper_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_whisper_chip.setStyleSheet("""
            QPushButton {
                background-color: #23252a;
                color: #f59e0b;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c2f36;
                border-color: #f59e0b;
                color: #fbbf24;
            }
        """)
        self.btn_whisper_chip.clicked.connect(self._on_whisper_chip_clicked)
        h_list_header.addWidget(self.btn_whisper_chip)
        layout.addLayout(h_list_header)

        # Hàng tùy chọn mô tả hình ảnh bằng AI (Stage 3 Opt-in)
        h_vision_opt = QHBoxLayout()
        h_vision_opt.setSpacing(10)
        self.chk_enable_visual_describe = QCheckBox("Mô tả hình ảnh bằng AI (Ollama local)")
        self.chk_enable_visual_describe.setChecked(False)
        self.chk_enable_visual_describe.setStyleSheet("color: #d4d4d8; font-size: 11.5px;")
        self.chk_enable_visual_describe.setToolTip(
            "Tùy chọn chạy mô hình thị giác Ollama nội bộ để mô tả từng cửa sổ hình ảnh sau khi lấy mẫu.\n"
            "Mặc định TẮT để tối ưu tốc độ và không bắt buộc GPU."
        )
        self.chk_enable_visual_describe.toggled.connect(self._on_vision_settings_changed)
        h_vision_opt.addWidget(self.chk_enable_visual_describe)

        settings = QSettings("ChunDVC", "ResolveFlow")
        saved_vmodel = str(settings.value("ai/vision_model", "qwen2.5-vl:7b"))

        self.cbo_vision_model = QComboBox()
        self.cbo_vision_model.setEditable(True)
        self.cbo_vision_model.addItems(["qwen2.5-vl:7b", "llama3.2-vision:11b", "minicpm-v"])
        self.cbo_vision_model.setCurrentText(saved_vmodel or "qwen2.5-vl:7b")
        self.cbo_vision_model.setFixedWidth(160)
        self.cbo_vision_model.setToolTip("Chọn hoặc nhập tên mô hình Ollama cục bộ (không tự động tải qua mạng)")
        self.cbo_vision_model.setStyleSheet("""
            QComboBox {
                background-color: #18181b;
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
        """)
        self.cbo_vision_model.currentTextChanged.connect(self._on_vision_settings_changed)
        h_vision_opt.addWidget(self.cbo_vision_model)
        h_vision_opt.addStretch(1)
        layout.addLayout(h_vision_opt)

        self.lbl_visual_status = QLabel("")
        self.lbl_visual_status.setWordWrap(True)
        self.lbl_visual_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self.lbl_visual_status.hide()
        layout.addWidget(self.lbl_visual_status)

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
                    background-color: #d94f3d;
                    color: #ffffff;
                    border: 1px solid #e86352;
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 11.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #e86352;
                    color: #ffffff;
                }
            """
        return """
            QPushButton {
                background-color: #23252a;
                color: #e6e8ec;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2c2f36;
                border-color: #4b505c;
            }
        """

    def _on_pick_folder(self):
        if self.is_source_analysis_active():
            return
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa video")
        if folder:
            self.add_paths([folder])

    def _on_pick_files(self):
        if self.is_source_analysis_active():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Chọn tệp video", "", "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts *.m2ts)"
        )
        if files:
            self.add_paths(files)

    def _on_pick_drive(self):
        if self.is_source_analysis_active():
            return
        from src.ui.widgets.drive_dialog import GoogleDriveImportDialog
        dialog = GoogleDriveImportDialog(self)
        dialog.import_completed.connect(self._on_drive_import_completed)
        dialog.exec()
        dialog.deleteLater()

    @Slot(str, bool)
    def _on_drive_import_completed(self, path: str, is_folder: bool):
        if self.is_source_analysis_active():
            return
        self.add_paths([path])

    def _on_whisper_chip_clicked(self):
        if self.is_source_analysis_active():
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1c1d21;
                color: #e6e8ec;
                border: 1px solid #353942;
                border-radius: 4px;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #d94f3d;
                color: #ffffff;
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
        if self.is_source_analysis_active():
            return
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
        _write_whisper_setting("whisper_model", self._whisper_model)
        self._update_whisper_label()
        self._invalidate_runtime_status()
        self.recheck_cache()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _set_lang(self, lang: str):
        if self.is_source_analysis_active():
            return
        self._whisper_lang = lang
        _write_whisper_setting("whisper_lang", self._whisper_lang)
        self._update_whisper_label()
        self._invalidate_runtime_status()
        self.recheck_cache()
        self.whisper_changed.emit(self._whisper_model, self._whisper_lang)

    def _update_whisper_label(self):
        self.btn_whisper_chip.setText(f"Whisper {self._whisper_model} · {self._whisper_lang}")

    def recheck_cache(self):
        """Cập nhật lại trạng thái cache thực tế của các clip khi đổi model hoặc ngôn ngữ."""
        if self.is_source_analysis_active():
            return
        self._invalidate_runtime_status()
        if self._clip_infos:
            self.add_paths([clip.path for clip in self._clip_infos])

    def add_paths(self, paths: List[str], sync: bool = False):
        """Thêm các đường dẫn tệp hoặc quét thư mục tìm video chạy ngầm trên Worker Thread."""
        if self.is_source_analysis_active():
            logger.warning("Đang phân tích nguồn, từ chối thêm tệp mới.")
            return False
        if not paths or self._shutdown_pending:
            return False

        # Tăng generation job ID để vô hiệu hóa mọi tác vụ scan cũ đang chạy
        self._current_job_id += 1
        job_id = self._current_job_id
        self._invalidate_runtime_status()

        # Yêu cầu dừng worker cũ nếu còn đang chạy, nhưng giữ trong registry tới khi finished
        if self._active_worker:
            self._active_worker.cancel()
            self._active_worker = None

        worker = SourceScanWorker(
            job_id=job_id,
            paths=paths,
            whisper_model=self._whisper_model,
            whisper_lang=self._whisper_lang,
            parent=None
        )
        self._workers_registry[job_id] = worker
        self._active_worker = worker
        self.btn_clear_all.setVisible(True)

        worker.batch_loaded.connect(self._on_worker_batch_loaded)
        worker.scan_finished.connect(self._on_worker_scan_finished)
        worker.scan_error.connect(self._on_worker_error)
        worker.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)

        if sync:
            worker.run()
            self._cleanup_worker(job_id)
        else:
            worker.start()

    @Slot()
    def _on_worker_finished(self):
        worker = self.sender()
        self._cleanup_worker(worker.job_id)

    def _cleanup_worker(self, job_id: int):
        """Dọn dẹp worker khi thread kết thúc an toàn trên GUI thread."""
        w = self._workers_registry.pop(job_id, None)
        if w is not None:
            if self._active_worker is w:
                self._active_worker = None
            if self._active_visual_worker is w:
                self._active_visual_worker = None
                self.btn_visual_scan.setEnabled(True)
                self.btn_describe.setEnabled(True)
            if self._active_describe_worker is w:
                self._active_describe_worker = None
                self.btn_describe.setEnabled(True)
                self.btn_visual_scan.setEnabled(True)
            w.deleteLater()
        if not self._workers_registry:
            self.btn_clear_all.setVisible(bool(self._clip_infos))
            self._shutdown_pending = False
            self.workers_idle.emit()
            if self._close_pending:
                self._close_pending = False
                QTimer.singleShot(0, self.close)

    @Slot(int, str)
    def _on_worker_error(self, job_id, message):
        if job_id == self._current_job_id and not self._shutdown_pending:
            QMessageBox.warning(self, "Không thể nạp nguồn", message)

    def wait_for_scan(self, timeout_ms: int = 5000) -> bool:
        """Chờ worker hiện tại nạp hoàn tất an toàn qua registry."""
        if self._active_worker:
            w = self._active_worker
            try:
                from shiboken6 import isValid
                if isValid(w) and w.isRunning():
                    return w.wait(timeout_ms)
            except RuntimeError:
                return True
        return True

    @Slot(int, list)
    def _on_worker_batch_loaded(self, job_id: int, batch: List[SourceClipInfo]):
        # Bỏ qua nếu tác vụ đã bị hủy hoặc thuộc về job_id cũ
        if job_id != self._current_job_id or self._shutdown_pending:
            return

        clip_map = {os.path.normpath(c.path): c for c in self._clip_infos}
        for info in batch:
            norm_p = os.path.normpath(info.path)
            clip_map[norm_p] = info

        # Khóa đã được tính trong worker; GUI không đọc metadata hoặc chạy ffprobe.
        self._clip_infos = sorted(clip_map.values(), key=lambda info: info.sort_key)
        self._refresh_clip_list()
        self.clips_changed.emit([c.path for c in self._clip_infos])

    def _on_worker_scan_finished(self, job_id: int, total_found: int):
        if job_id != self._current_job_id or self._shutdown_pending:
            return

        if total_found == 0:
            QMessageBox.warning(
                self,
                "Không tìm thấy video",
                "Không tìm thấy tệp video hợp lệ nào trong các đường dẫn đã chọn.\n\n"
                "Định dạng hỗ trợ: MP4, MOV, MKV, AVI, WEBM, M4V, FLV, WMV, TS, MTS, M2TS."
            )

    def remove_path(self, path: str):
        if self.is_source_analysis_active():
            logger.warning("Đang phân tích nguồn, từ chối xóa tệp.")
            return False
        norm_p = os.path.normpath(path)
        self._clip_infos = [c for c in self._clip_infos if os.path.normpath(c.path) != norm_p]
        self._runtime_clip_status.pop(path, None)
        self._invalidate_runtime_status()
        self._refresh_clip_list()
        self.clips_changed.emit([c.path for c in self._clip_infos])
        return True

    def clear_all_paths(self):
        """Xóa toàn bộ clip nguồn khỏi phiên làm việc và hủy mọi tác vụ nạp đang chạy."""
        if self.is_source_analysis_active():
            logger.warning("Đang phân tích nguồn, từ chối xóa tất cả.")
            return False
        self._current_job_id += 1  # Vô hiệu hóa mọi batch đang truyền dở
        if self._active_worker:
            self._active_worker.cancel()
            self._active_worker = None
        for w in list(self._workers_registry.values()):
            if hasattr(w, "cancel"):
                w.cancel()
        self._clip_infos.clear()
        self._invalidate_runtime_status()
        self.lbl_visual_status.hide()
        self._refresh_clip_list()
        self.clips_changed.emit([])
        return True

    def shutdown_workers(self, wait_timeout_ms: int = 1000) -> bool:
        """Yêu cầu hủy không chặn GUI; workers_idle báo khi mọi worker đã dừng.

        Giữ tham số timeout để tương thích caller cũ, không chờ trên GUI thread.
        """
        self._current_job_id += 1
        self._active_worker = None
        if self._active_source_worker is not None:
            self._active_source_worker.cancel()
        self._shutdown_pending = bool(self._workers_registry)
        for worker in list(self._workers_registry.values()):
            if hasattr(worker, "cancel"):
                try:
                    worker.cancel()
                except Exception:
                    pass
        return not self._workers_registry

    def closeEvent(self, event):
        """Giữ owner sống tới khi tất cả worker đã kết thúc."""
        if not self.shutdown_workers():
            self._close_pending = True
            event.ignore()
            return
        super().closeEvent(event)

    def is_source_analysis_active(self) -> bool:
        """Kiểm tra xem tác vụ phân tích nguồn độc lập có đang hoạt động hay không."""
        if self._active_source_worker is None:
            return False
        try:
            from shiboken6 import isValid
            if not isValid(self._active_source_worker):
                return False
            return self._active_source_worker.isRunning() or self._active_source_thread_pending
        except Exception:
            return False

    def get_video_paths(self) -> List[str]:
        """Lấy danh sách các đường dẫn clip nguồn hiện tại."""
        return [c.path for c in self._clip_infos]

    def get_whisper_model(self) -> str:
        """Lấy cấu hình whisper model hiện tại."""
        return self._whisper_model

    def get_whisper_language(self) -> str:
        """Lấy cấu hình whisper language hiện tại."""
        return self._whisper_lang

    def _invalidate_runtime_status(self):
        """Hủy bỏ toàn bộ trạng thái verified tạm thời khi thay đổi mô hình, ngôn ngữ hoặc danh sách clip."""
        self._runtime_clip_status.clear()
        self._runtime_status_config = None
        for row in self._clip_rows.values():
            if hasattr(row, "reset_to_candidate_status"):
                row.reset_to_candidate_status()

    def _on_vision_settings_changed(self, *args):
        """Khi cấu hình vision opt-in thay đổi, invalidate verified status của các clip row."""
        self._invalidate_runtime_status()

    def set_vision_endpoint(self, endpoint: str):
        """Thiết lập endpoint và invalidate runtime status nếu giá trị thay đổi."""
        if getattr(self, "_runtime_vision_endpoint", None) != endpoint:
            self._runtime_vision_endpoint = endpoint
            self._invalidate_runtime_status()

    def set_vision_budgets(
        self,
        window_sec: float = 8.0,
        frames_per_window: int = 2,
        max_windows: int = 10,
        max_refinements: int = 1
    ):
        """Thiết lập ngân sách phân tích thị giác và invalidate runtime status nếu thay đổi."""
        if (getattr(self, "_runtime_window_sec", None) != window_sec
                or getattr(self, "_runtime_frames_per_window", None) != frames_per_window
                or getattr(self, "_runtime_max_windows", None) != max_windows
                or getattr(self, "_runtime_max_refinements", None) != max_refinements):
            self._runtime_window_sec = window_sec
            self._runtime_frames_per_window = frames_per_window
            self._runtime_max_windows = max_windows
            self._runtime_max_refinements = max_refinements
            self._invalidate_runtime_status()

    def get_completed_analysis_snapshot(self) -> Optional[Any]:
        """Trả về snapshot cấu hình của phiên phân tích gần nhất."""
        return self._runtime_status_config

    def get_legacy_analysis_snapshot(self) -> Optional[Tuple]:
        """Trả về snapshot cấu hình dạng tuple tương thích legacy."""
        if self._runtime_status_config is None:
            return None
        snap = self._runtime_status_config
        if isinstance(snap, tuple) and not hasattr(snap, "vision_endpoint"):
            return snap
        return (
            getattr(snap, "whisper_model", ""),
            getattr(snap, "whisper_language", ""),
            getattr(snap, "enable_visual_describe", False),
            getattr(snap, "vision_model", ""),
            tuple(getattr(snap, "video_paths", ())),
        )

    def _set_analysis_controls_locked(self, locked: bool):
        """Khóa hoặc mở khóa các điều khiển khi tiến trình phân tích nguồn đang chạy."""
        can_enable = not locked and not self._shutdown_pending
        if hasattr(self, "btn_folder"):
            self.btn_folder.setEnabled(can_enable)
        if hasattr(self, "btn_files"):
            self.btn_files.setEnabled(can_enable)
        if hasattr(self, "btn_resolve"):
            self.btn_resolve.setEnabled(can_enable)
        if hasattr(self, "btn_drive"):
            self.btn_drive.setEnabled(can_enable)
        if hasattr(self, "btn_whisper_chip"):
            self.btn_whisper_chip.setEnabled(can_enable)
        if hasattr(self, "chk_enable_visual_describe"):
            self.chk_enable_visual_describe.setEnabled(can_enable)
        if hasattr(self, "cbo_vision_model"):
            self.cbo_vision_model.setEnabled(can_enable)
        if hasattr(self, "btn_visual_scan"):
            visual_busy = (self._active_visual_worker is not None) or (self._active_describe_worker is not None)
            self.btn_visual_scan.setEnabled(can_enable and not visual_busy)
        if hasattr(self, "btn_describe"):
            visual_busy = (self._active_visual_worker is not None) or (self._active_describe_worker is not None)
            self.btn_describe.setEnabled(can_enable and not visual_busy)
        if hasattr(self, "btn_clear_all"):
            self.btn_clear_all.setEnabled(can_enable)
        for row in self._clip_rows.values():
            if hasattr(row, "btn_del") and row.btn_del:
                row.btn_del.setEnabled(can_enable)

    def _refresh_scan_button_state(self):
        """Cập nhật giao diện và nhãn của nút phân tích nguồn theo trạng thái thực tế."""
        count = len(self._clip_infos)
        if count == 0:
            self.btn_scan.setVisible(False)
            return

        self.btn_scan.setVisible(True)
        if self.is_source_analysis_active():
            self.btn_scan.setEnabled(True)
            self.btn_scan.setText("✕ Hủy phân tích")
            self.btn_scan.setStyleSheet("""
                QPushButton {
                    background-color: rgba(239, 68, 68, 0.2);
                    color: #f87171;
                    border: 1px solid rgba(239, 68, 68, 0.5);
                    border-radius: 12px;
                    padding: 3px 12px;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(239, 68, 68, 0.35);
                    border-color: #ef4444;
                }
            """)
            self.btn_scan.setToolTip("Bấm để dừng quá trình phân tích nguồn. Các phần đã quét xong sẽ được lưu giữ.")
            return

        self.btn_scan.setEnabled(True)
        self.btn_scan.setText("⚡ Phân tích nguồn (Thoại & Khung hình)")
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #d94f3d;
                color: #ffffff;
                border: 1px solid #e86352;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e86352;
                border-color: #f07b6b;
            }
        """)
        self.btn_scan.setToolTip("Bóc tách lời thoại (Whisper offline) và lấy mẫu khung hình thị giác độc lập.")

    def _on_scan_button_clicked(self):
        """Xử lý sự kiện bấm nút phân tích: chạy phân tích hoặc hủy tác vụ."""
        if self.is_source_analysis_active():
            self.cancel_source_analysis()
        else:
            self.start_source_analysis()

    def start_source_analysis(
        self,
        video_paths: Optional[Tuple[str, ...]] = None,
        whisper_model: Optional[str] = None,
        whisper_language: Optional[str] = None,
        enable_audio: bool = True,
        enable_visual_sampling: bool = True,
        visual_interval_sec: float = 2.0,
        visual_max_samples: int = 120,
        enable_visual_describe: Optional[bool] = None,
        vision_model: Optional[str] = None,
        vision_endpoint: Optional[str] = None,
        window_sec: float = 8.0,
        frames_per_window: int = 2,
        max_windows: int = 10,
        max_refinements: int = 1,
        worker_factory: Optional[Callable[[SourceAnalysisConfig], SourceAnalysisWorker]] = None,
    ) -> bool:
        """Khởi chạy phiên phân tích nguồn độc lập (Source-Only)."""
        if (self._shutdown_pending
                or self.is_source_analysis_active()
                or self._active_worker is not None
                or self._active_visual_worker is not None
                or self._active_describe_worker is not None
                or bool(self._workers_registry)):
            logger.warning("Hệ thống đang có tác vụ chạy ngầm hoặc chờ tắt, từ chối khởi động phân tích nguồn mới.")
            return False

        paths = tuple(video_paths) if video_paths is not None else tuple(c.path for c in self._clip_infos)
        if not paths:
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn video nguồn trước khi phân tích.")
            return False

        # Xác định trạng thái opt-in mô tả AI
        v_enable = (
            enable_visual_describe
            if enable_visual_describe is not None
            else (self.chk_enable_visual_describe.isChecked() if hasattr(self, "chk_enable_visual_describe") else False)
        )
        v_model = ""
        v_endpoint = vision_endpoint or "http://127.0.0.1:11434"
        if v_enable:
            v_model = (
                vision_model
                if vision_model is not None
                else (self.cbo_vision_model.currentText().strip() if hasattr(self, "cbo_vision_model") else "")
            ).strip()
            if not v_model:
                QMessageBox.warning(
                    self,
                    "Chưa chọn mô hình thị giác",
                    "Vui lòng chọn hoặc nhập tên mô hình Ollama (ví dụ: qwen2.5-vl:7b) trước khi bật mô tả hình ảnh."
                )
                return False
            # Lưu lại model vào settings
            settings = QSettings("ChunDVC", "ResolveFlow")
            settings.setValue("ai/vision_model", v_model)
            v_endpoint = vision_endpoint or str(settings.value("ai/ollama_endpoint", "http://127.0.0.1:11434"))

        # Tạo snapshot cấu hình bất biến
        job_id = f"source_job_{uuid.uuid4().hex[:8]}"
        cfg = SourceAnalysisConfig(
            job_id=job_id,
            video_paths=paths,
            whisper_model=whisper_model or self._whisper_model,
            whisper_language=whisper_language or self._whisper_lang,
            enable_audio=enable_audio,
            enable_visual_sampling=enable_visual_sampling,
            visual_interval_sec=visual_interval_sec,
            visual_max_samples=visual_max_samples,
            local_files_only=True,
            enable_visual_describe=v_enable,
            vision_model=v_model,
            vision_endpoint=v_endpoint,
            window_sec=window_sec,
            frames_per_window=frames_per_window,
            max_windows=max_windows,
            max_refinements=max_refinements,
        )

        factory = worker_factory or getattr(self, "_worker_factory_override", None)
        try:
            worker = factory(cfg) if factory is not None else SourceAnalysisWorker(cfg)
        except Exception as exc:
            logger.error("Không thể khởi tạo SourceAnalysisWorker: %s", exc)
            QMessageBox.warning(self, "Lỗi khởi tạo", f"Không thể khởi tạo worker phân tích: {exc}")
            return False

        self._active_source_worker = worker
        self._active_source_job_id = job_id
        self._active_source_thread_pending = True
        registry_token = f"source_analysis_{job_id}"
        self._workers_registry[registry_token] = worker

        # Ghi nhận runtime context
        self._runtime_vision_endpoint = v_endpoint
        self._runtime_window_sec = window_sec
        self._runtime_frames_per_window = frames_per_window
        self._runtime_max_windows = max_windows
        self._runtime_max_refinements = max_refinements

        # Ghi nhận snapshot cấu hình phân tích structured
        self._runtime_status_config = StructuredAnalysisSnapshot(
            job_id=job_id,
            video_paths=paths,
            whisper_model=cfg.whisper_model,
            whisper_language=cfg.whisper_language,
            enable_visual_describe=cfg.enable_visual_describe,
            vision_model=cfg.vision_model,
            vision_endpoint=cfg.vision_endpoint,
            window_sec=cfg.window_sec,
            frames_per_window=cfg.frames_per_window,
            max_windows=cfg.max_windows,
            max_refinements=cfg.max_refinements,
            enable_audio=cfg.enable_audio,
            enable_visual_sampling=cfg.enable_visual_sampling,
            visual_interval_sec=cfg.visual_interval_sec,
            visual_max_samples=cfg.visual_max_samples,
        )

        # Khóa điều khiển và cập nhật nút
        self._set_analysis_controls_locked(True)
        self._refresh_scan_button_state()
        self.source_analysis_busy_changed.emit(True)

        # Kết nối các tín hiệu
        worker.progress_signal.connect(self._on_source_progress)
        worker.status_signal.connect(self._on_source_status)
        worker.counters_signal.connect(self._on_source_counters)
        worker.finished_signal.connect(self._on_source_finished)

        # Kết nối callback thread finished gắn chặt identity worker và token
        def _on_thread_finished(w=worker, jid=job_id, token=registry_token):
            self._on_source_thread_finished_for_job(w, jid, token)

        worker.finished.connect(_on_thread_finished, Qt.ConnectionType.QueuedConnection)

        try:
            worker.start()
        except Exception as start_exc:
            logger.error("Lỗi khởi chạy worker: %s", start_exc)
            self._workers_registry.pop(registry_token, None)
            if self._active_source_job_id == job_id:
                self._active_source_worker = None
                self._active_source_job_id = None
                self._active_source_thread_pending = False
                self._set_analysis_controls_locked(False)
                self._refresh_scan_button_state()
                self.source_analysis_busy_changed.emit(False)
            try:
                worker.deleteLater()
            except Exception:
                pass
            QMessageBox.warning(self, "Lỗi phân tích", f"Không thể khởi chạy worker: {start_exc}")
            return False

        return True

    def cancel_source_analysis(self):
        """Yêu cầu hủy cooperative tác vụ phân tích nguồn đang chạy."""
        if self._active_source_worker and not getattr(self._active_source_worker, "_is_cancelled", False):
            self.btn_scan.setEnabled(False)
            self.btn_scan.setText("⏳ Đang hủy...")
            self._active_source_worker.cancel()

    @Slot(str, int, int)
    def _on_source_progress(self, job_id: str, current: int, total: int):
        if job_id != self._active_source_job_id or self._shutdown_pending:
            return
        if self.is_source_analysis_active():
            self.btn_scan.setToolTip(f"Đang phân tích nguồn: {current}/{total} clip. Bấm để hủy.")

    @Slot(str, str, dict)
    def _on_source_status(self, job_id: str, clip_path: str, clip_res: dict):
        if job_id != self._active_source_job_id or self._shutdown_pending:
            return
        self._runtime_clip_status[clip_path] = clip_res
        row = self._clip_rows.get(clip_path)
        if row is not None:
            row.update_analysis_status(clip_res)

    @Slot(str, dict)
    def _on_source_counters(self, job_id: str, counters: dict):
        if job_id != self._active_source_job_id or self._shutdown_pending:
            return

    @Slot(str, bool, str, dict)
    def _on_source_finished(self, job_id: str, success: bool, message: str, counters: dict):
        if job_id != self._active_source_job_id:
            return
        # Phát signal hoàn tất cho Director quan sát (nhưng giữ khóa tới khi thread.finished)
        self.source_analysis_completed.emit(job_id, success, message, counters)

    def _on_source_thread_finished_for_job(self, worker, job_id: str, registry_token: str):
        """Callback khi luồng QThread của đúng worker này đã kết thúc trên GUI thread."""
        self._workers_registry.pop(registry_token, None)
        try:
            worker.deleteLater()
        except Exception:
            pass

        # Chỉ clear active state và unlock nếu chính job này đang là active job
        if self._active_source_job_id == job_id:
            self._active_source_worker = None
            self._active_source_job_id = None
            self._active_source_thread_pending = False
            self._set_analysis_controls_locked(False)
            self._refresh_scan_button_state()
            self.source_analysis_busy_changed.emit(False)

        if not self._workers_registry:
            self.btn_clear_all.setVisible(bool(self._clip_infos))
            self._shutdown_pending = False
            self.workers_idle.emit()
            if self._close_pending:
                self._close_pending = False
                QTimer.singleShot(0, self.close)

    def _cleanup_source_worker(self, specific_job_id: Optional[str] = None):
        """Dọn dẹp worker khẩn cấp (fallback tương thích)."""
        target_job_id = specific_job_id or self._active_source_job_id
        if target_job_id:
            registry_token = f"source_analysis_{target_job_id}"
            w = self._workers_registry.pop(registry_token, None)
            if w is not None:
                try:
                    w.deleteLater()
                except Exception:
                    pass
        if specific_job_id is None or self._active_source_job_id == specific_job_id:
            self._active_source_worker = None
            self._active_source_job_id = None
            self._active_source_thread_pending = False
            self._set_analysis_controls_locked(False)
            self._refresh_scan_button_state()
            self.source_analysis_busy_changed.emit(False)

        if not self._workers_registry:
            self.btn_clear_all.setVisible(bool(self._clip_infos))
            self._shutdown_pending = False
            self.workers_idle.emit()
            if self._close_pending:
                self._close_pending = False
                QTimer.singleShot(0, self.close)

    def _refresh_clip_list(self):
        # Xóa các widget cũ trong list_layout
        self._clip_rows.clear()
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, clip in enumerate(self._clip_infos):
            row = SourceClipRow(clip, self.list_container)
            row.remove_requested.connect(self.remove_path)
            self._clip_rows[clip.path] = row
            if clip.path in self._runtime_clip_status:
                row.update_analysis_status(self._runtime_clip_status[clip.path])
            if self.is_source_analysis_active() and hasattr(row, "btn_del") and row.btn_del:
                row.btn_del.setEnabled(False)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

        count = len(self._clip_infos)
        self.lbl_count.setText(f"Đã chọn · {count} clip nguồn" if count > 0 else "Chưa chọn clip nào")
        if hasattr(self, "btn_clear_all"):
            self.btn_clear_all.setVisible(count > 0)
            if self.is_source_analysis_active():
                self.btn_clear_all.setEnabled(False)

        if count > 0:
            self.btn_visual_scan.setVisible(True)
            self.btn_visual_folder.setVisible(True)
            self.btn_describe.setVisible(True)
            self._refresh_scan_button_state()
        else:
            self.btn_scan.setVisible(False)
            self.btn_visual_scan.setVisible(False)
            self.btn_visual_folder.setVisible(False)
            self.btn_describe.setVisible(False)

    def _visual_description_menu(self, position):
        menu = QMenu(self)
        action = menu.addAction("Phân tích lại hình ảnh (bỏ qua cache model)")
        if menu.exec(self.btn_describe.mapToGlobal(position)) is action:
            self._ask_visual_model(force=True)

    def _ask_visual_model(self, checked=False, force=False):
        if self.is_source_analysis_active():
            return
        settings = QSettings("ChunDVC", "ResolveFlow")
        model, accepted = QInputDialog.getText(
            self, "Model thị giác local", "Tên model Ollama đã cài:",
            QLineEdit.EchoMode.Normal, str(settings.value("ai/vision_model", "qwen3-vl:2b-instruct"))
        )
        if not accepted or not model.strip():
            return
        settings.setValue("ai/vision_model", model.strip())
        endpoint = str(settings.value("ai/ollama_endpoint", "http://127.0.0.1:11434"))
        self._start_visual_description(model.strip(), endpoint, force=force)

    def _start_visual_description(self, model: str, endpoint: str, force=False):
        if (not self._clip_infos or self._shutdown_pending
                or self._active_describe_worker or self._active_visual_worker
                or self.is_source_analysis_active()):
            return
        self._visual_job_id -= 1
        worker = VisualDescribeWorker(
            self._visual_job_id, [c.path for c in self._clip_infos], model, endpoint, force=force
        )
        self._workers_registry[worker.job_id] = worker
        self._active_describe_worker = worker
        self.btn_describe.setEnabled(False)
        self.btn_visual_scan.setEnabled(False)
        self.btn_describe.setText("🤖 Đang mô tả...")
        self.lbl_visual_status.setText("Đang kiểm tra model thị giác local và chuẩn bị cửa sổ hình ảnh...")
        self.lbl_visual_status.show()
        worker.progress.connect(self._on_describe_progress)
        worker.describe_done.connect(self._on_describe_done)
        worker.describe_error.connect(self._on_describe_error)
        worker.window_progress.connect(self._on_window_progress)
        worker.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.start()

    @Slot(int, int)
    def _on_window_progress(self, done: int, total: int):
        if self.sender() is self._active_describe_worker and not self._shutdown_pending:
            self.lbl_visual_status.setText(f"Đang mô tả cửa sổ hình ảnh {done}/{total} của clip hiện tại.")

    @Slot(int, int)
    def _on_describe_progress(self, done: int, total: int):
        if (self.sender() is self._active_describe_worker and self._active_describe_worker
                and not self._active_describe_worker._is_cancelled and not self._shutdown_pending):
            self.btn_describe.setText(f"🤖 Mô tả {done}/{total}")

    @Slot(int, int)
    def _on_describe_done(self, done: int, total: int):
        if (self.sender() is self._active_describe_worker and self._active_describe_worker
                and not self._active_describe_worker._is_cancelled and not self._shutdown_pending):
            worker_errors = list(getattr(self._active_describe_worker, "errors", []))
            failed = total - done
            if failed > 0:
                self.btn_describe.setText(f"⚠️ Mô tả {done}/{total} ({failed} lỗi)")
                summary = (
                    f"Mô tả hoàn tất {done}/{total} clip nguồn.\n"
                    f"Có {failed} clip gặp sự cố:\n" + "\n".join(f"• {e}" for e in worker_errors[:5])
                )
                if len(worker_errors) > 5:
                    summary += f"\n• ...(và {len(worker_errors) - 5} lỗi khác)"
                summary += (
                    "\n\nCách khắc phục:\n"
                    "1. Kiểm tra Ollama đang chạy ('ollama serve')\n"
                    "2. Đảm bảo model đã được tải ('ollama pull <model>')\n"
                    "3. Bấm lại nút này để thử lại các clip chưa thành công."
                )
                self.btn_describe.setToolTip(summary)
                self.lbl_visual_status.setText(summary)
                self.lbl_visual_status.show()
            else:
                self.btn_describe.setText(f"✓ Mô tả {done}/{total}")
                self.btn_describe.setToolTip("Đã mô tả các cửa sổ lấy mẫu. Bấm chuột phải để phân tích lại khi cần.")
                self.lbl_visual_status.setText("Đã mô tả các cửa sổ hình ảnh. Kết quả model vẫn cần được đối chiếu khi duyệt kịch bản.")

    @Slot(str)
    def _on_describe_error(self, message: str):
        if (self.sender() is self._active_describe_worker and self._active_describe_worker
                and not self._active_describe_worker._is_cancelled and not self._shutdown_pending):
            self.btn_describe.setToolTip(message)
            self.lbl_visual_status.setText(message)
            self.lbl_visual_status.show()

    def _open_visual_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from src.core.visual_index import VisualIndex

        folder = VisualIndex().cache_dir / "frames"
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.btn_visual_folder.setToolTip("Chưa có ảnh. Bấm 'Lấy mẫu hình ảnh' trước.")

    def _start_visual_scan(self):
        """Lấy mẫu tất cả footage nguồn; cache được tái dùng ở lần sau."""
        if (not self._clip_infos or self._shutdown_pending
                or self._active_visual_worker or self._active_describe_worker
                or self.is_source_analysis_active()):
            return
        self._visual_job_id -= 1
        worker = VisualScanWorker(self._visual_job_id, [c.path for c in self._clip_infos])
        self._workers_registry[worker.job_id] = worker
        self._active_visual_worker = worker
        self.btn_visual_scan.setEnabled(False)
        self.btn_describe.setEnabled(False)
        self.btn_visual_scan.setText("🖼️ Đang lấy mẫu...")
        worker.progress.connect(self._on_visual_progress)
        worker.scan_done.connect(self._on_visual_done)
        worker.scan_error.connect(self._on_visual_error)
        worker.finished.connect(self._on_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.start()

    @Slot(int, int)
    def _on_visual_progress(self, done: int, total: int):
        if (self.sender() is self._active_visual_worker and self._active_visual_worker
                and not self._active_visual_worker._is_cancelled and not self._shutdown_pending):
            self.btn_visual_scan.setText(f"🖼️ Lấy mẫu {done}/{total}")

    @Slot(int, int, int)
    def _on_visual_done(self, done: int, reused: int, total: int):
        if (self.sender() is self._active_visual_worker and self._active_visual_worker
                and not self._active_visual_worker._is_cancelled and not self._shutdown_pending):
            self.btn_visual_scan.setText(f"✓ Ảnh {done}/{total} · cache {reused}")
            self.btn_visual_scan.setToolTip(
                "Contact sheet được lưu trong ~/.resolveflow_cache/visual/frames/. "
                "Có thể đính kèm cho AI nhìn hình; ứng dụng chưa tự mô tả cảnh."
            )

    @Slot(str)
    def _on_visual_error(self, message: str):
        if (self.sender() is self._active_visual_worker and self._active_visual_worker
                and not self._active_visual_worker._is_cancelled and not self._shutdown_pending):
            self.btn_visual_scan.setToolTip(message)

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
