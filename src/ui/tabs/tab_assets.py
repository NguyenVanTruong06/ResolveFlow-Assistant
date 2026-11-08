"""
TabAssets: Kho Đạo Cụ & Hậu Kỳ (ResolveFlow Studio Mode).
Triển khai toàn diện cấu trúc 4 cột theo chuẩn thiết kế từ src/ui/resolveflow_ui.html:
- Cột 1: Rail điều hướng chính (Chữ, Màu LUT, Chuyển cảnh, Sticker, Lớp phủ, Meme, SFX, Mẫu của tôi)
- Cột 2: Phân loại danh mục con động (Categories) kèm số lượng
- Cột 3: Lưới đạo cụ trực quan (Main Grid) với xem trước phong cách, nút Yêu thích, nút Thêm nhanh, Kéo & Thả
- Cột 4: Bảng tinh chỉnh chi tiết (Inspector) với khung xem trước lớn và bộ nút thao tác DaVinci
"""

import os
import re
import json
import uuid
import time
import tempfile
import math
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any, Tuple
from collections.abc import Mapping
import logging

logger = logging.getLogger("ChunDVC.TabAssets")

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox,
    QSizePolicy, QFileDialog, QColorDialog, QSplitter, QStackedWidget,
    QInputDialog, QMenu
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QMimeData, QUrl, QTimer, QRectF, QPointF, QPoint, QEvent, QMetaObject, QObject
from PySide6.QtGui import (
    QDrag, QPixmap, QCursor, QMovie, QPainter, QColor, QPen, QBrush,
    QFont, QPainterPath, QLinearGradient, QFontMetrics, QDesktopServices
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS
from src.core.text_preset import TextStylePreset, BUILTIN_PRESETS, FusionSettingGenerator
from src.core.transition_preset import TransitionStylePreset, BUILTIN_TRANSITIONS, TransitionMacroGenerator
from src.core.resolve_api import ResolveAutomation
from src.core.asset_indexer import AssetIndexer
from src.core.pack_manager import PackManager
from src.core.collections_manager import CollectionsManager
from src.core.contextual_suggester import ContextualSuggester, SuggestedAsset
from src.utils.path_utils import PosixPathAdapter
from src.ui.lux.library.hover_preview import _normalize_preview_payload
from src.core.action_guard import check_action_availability

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a", ".aif", ".aiff"}
MAX_FUSION_SETTING_BYTES = 5 * 1024 * 1024  # 5 MiB giới hạn tối đa cho file .setting xuất vào clipboard



# =========================================================================
# 1. BỘ QUẢN LÝ YÊU THÍCH (Favorites Manager)
# =========================================================================
class FavoritesManager:
    FILE_PATH = "data/favorites.json"
    _cache: Optional[set] = None
    _cache_mtime: float = -1.0
    _cache_path: str = ""

    @classmethod
    def load(cls) -> set:
        try:
            abs_p = os.path.abspath(cls.FILE_PATH)
        except Exception:
            abs_p = cls.FILE_PATH

        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        if not os.path.exists(cls.FILE_PATH):
            cls._cache = set()
            cls._cache_mtime = -1.0
            cls._cache_path = abs_p
            return set()
        try:
            st_mtime = os.stat(cls.FILE_PATH).st_mtime
            if cls._cache is not None and cls._cache_path == abs_p and cls._cache_mtime == st_mtime:
                return set(cls._cache)
            with open(cls.FILE_PATH, 'r', encoding='utf-8') as f:
                data = set(json.load(f))
            cls._cache = data
            cls._cache_mtime = st_mtime
            cls._cache_path = abs_p
            return set(data)
        except Exception:
            return set()

    @classmethod
    def save(cls, favs: set):
        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        try:
            with open(cls.FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(list(favs), f)
            cls._cache = set(favs)
            cls._cache_path = os.path.abspath(cls.FILE_PATH)
            cls._cache_mtime = os.stat(cls.FILE_PATH).st_mtime
        except Exception:
            pass


class RecentManager:
    """Quản lý danh sách các đạo cụ đã dùng hoặc bấm xem gần đây."""
    FILE_PATH = "data/recent.json"
    MAX_ITEMS = 60
    _cache: Optional[list] = None
    _cache_mtime: float = -1.0
    _cache_path: str = ""

    @classmethod
    def load(cls) -> list:
        try:
            abs_p = os.path.abspath(cls.FILE_PATH)
        except Exception:
            abs_p = cls.FILE_PATH

        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        if not os.path.exists(cls.FILE_PATH):
            cls._cache = []
            cls._cache_mtime = -1.0
            cls._cache_path = abs_p
            return []
        try:
            st_mtime = os.stat(cls.FILE_PATH).st_mtime
            if cls._cache is not None and cls._cache_path == abs_p and cls._cache_mtime == st_mtime:
                return list(cls._cache)
            with open(cls.FILE_PATH, 'r', encoding='utf-8') as f:
                data = list(json.load(f))
            cls._cache = data
            cls._cache_mtime = st_mtime
            cls._cache_path = abs_p
            return list(data)
        except Exception:
            return []

    @classmethod
    def add(cls, asset_id: str):
        if not asset_id:
            return
        items = cls.load()
        if asset_id in items:
            items.remove(asset_id)
        items.insert(0, asset_id)
        items = items[:cls.MAX_ITEMS]
        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        try:
            with open(cls.FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(items, f)
            cls._cache = list(items)
            cls._cache_path = os.path.abspath(cls.FILE_PATH)
            cls._cache_mtime = os.stat(cls.FILE_PATH).st_mtime
        except Exception:
            pass



# =========================================================================
# 2. DỮ LIỆU ĐẠO CỤ CHUẨN TỪ DESIGN MOCKUP
# =========================================================================
class StudioAsset:
    """Mô hình dữ liệu đạo cụ thống nhất cho Kho Đạo Cụ."""
    def __init__(
        self,
        id: str,
        name: str,
        tab: str,               # 'text', 'lut', 'trans', 'icon', 'overlay', 'meme', 'sfx'
        category: str,          # category id
        sub: str = "",
        badge_icon: str = "✨",
        pal: Optional[List[str]] = None,
        duration: float = 3.0,
        frames: int = 24,
        color_hex: str = "#FFFFFF",
        svg_path: str = "",
        native_preset: Any = None,
        file_path: str = "",
        thumbnail_path: str = "",
        pack_name: str = "",
        preview_path: str = "",
        duration_sec: Optional[float] = None
    ):
        self.id = id
        self.name = name
        self.tab = tab
        self.category = category
        self.pack_name = pack_name
        self.sub = pack_name if (pack_name and pack_name != "Mặc định") else sub
        self.badge_icon = badge_icon
        self.pal = pal or []
        self.duration = duration
        self.duration_sec = duration_sec
        self.frames = frames
        self.color_hex = color_hex
        self.svg_path = svg_path
        self.native_preset = native_preset
        self.file_path = file_path
        self.thumbnail_path = thumbnail_path
        self.preview_path = preview_path or file_path


# Danh sách LUTs, Sticker, Overlay, Meme, SFX mockup mặc định rỗng (toàn bộ definitions đã lưu trữ tại kho xuất bản riêng)
MOCKUP_LUTS: List[StudioAsset] = []
MOCKUP_ICONS: List[StudioAsset] = []
MOCKUP_OVERLAYS: List[StudioAsset] = []
MOCKUP_MEMES: List[StudioAsset] = []
MOCKUP_SFX: List[StudioAsset] = []


# =========================================================================
# 3. LOCAL ASSET MODEL
# =========================================================================
class LocalAsset:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.name = os.path.basename(file_path)
        self.id = file_path
        self.ext = os.path.splitext(file_path)[1].lower()

        if self.ext in ['.wav', '.mp3', '.aac']:
            self.category = "sfx"
            self.tab = "sfx"
            self.badge_icon = "🎵"
        elif self.ext in ['.mp4', '.mov', '.avi', '.mkv']:
            self.category = "video"
            self.tab = "overlay"
            self.badge_icon = "🎞️"
        elif self.ext in ['.png', '.jpg', '.jpeg', '.gif']:
            self.category = "image"
            self.tab = "icon"
            self.badge_icon = "🖼️"
        elif self.ext in ['.cube']:
            self.category = "lut"
            self.tab = "lut"
            self.badge_icon = "🎨"
        else:
            self.category = "file"
            self.tab = "text"
            self.badge_icon = "📄"


# =========================================================================
# 4. DRAGGABLE ASSET LABEL (Hỗ trợ kéo thả ra DaVinci Resolve)
# =========================================================================
class DraggableAssetLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.preset_getter: Optional[Callable[[], Any]] = None
        self.sample_text_getter: Optional[Callable[[], str]] = None
        self.mime_data_generator: Optional[Callable[[Any], QMimeData]] = None
        self.drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("🖱️ Kéo thả trực tiếp vào Timeline DaVinci Resolve!")
        self.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self.drag_start_pos:
            return
        if (event.pos() - self.drag_start_pos).manhattanLength() < 8:
            return

        # Clear gesture ngay sau khi vượt ngưỡng threshold để tránh lặp drag trong cùng một gesture
        self.drag_start_pos = None

        preset = self.preset_getter() if callable(self.preset_getter) else None
        if not preset:
            return

        if callable(self.mime_data_generator):
            mime_data = self.mime_data_generator(preset)
        else:
            # Fallback dùng shared helper có guard với sampletext, không tự sinh macro/dummy text
            from src.ui.lux.library.asset_grid import create_asset_mime_data
            sample_txt = self.sample_text_getter() if callable(self.sample_text_getter) else ""
            mime_data = create_asset_mime_data(preset, sample_text=sample_txt)

        # Gate hasUrls và local URLs không rỗng trước khi tạo QDrag / exec
        if not mime_data or not mime_data.hasUrls():
            self.setCursor(Qt.OpenHandCursor)
            return

        local_urls = [u for u in mime_data.urls() if u.isLocalFile()]
        if not local_urls:
            self.setCursor(Qt.OpenHandCursor)
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        self.setCursor(Qt.OpenHandCursor)
        drag.exec(Qt.DropAction.CopyAction)


class DraggableActionButton(QPushButton):
    """Nút bấm hỗ trợ kéo thả (Drag & Drop) file .setting / .wav / .cube / .mp4 vào DaVinci Resolve."""
    def __init__(self, text: str, mime_data_getter: Optional[Callable[[], QMimeData]] = None, parent=None):
        super().__init__(text, parent)
        self.mime_data_getter = mime_data_getter
        self._drag_start_pos = None
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self._drag_start_pos:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            return

        # Clear gesture ngay sau khi vượt ngưỡng threshold
        self._drag_start_pos = None

        if not callable(self.mime_data_getter):
            return

        mime_data = self.mime_data_getter()
        if not mime_data or not mime_data.hasUrls():
            return

        local_urls = [u for u in mime_data.urls() if u.isLocalFile()]
        if not local_urls:
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        try:
            pixmap = self.grab()
            if not pixmap.isNull():
                ghost = pixmap.scaledToWidth(180, Qt.SmoothTransformation) if pixmap.width() > 180 else pixmap
                transparent_pix = QPixmap(ghost.size())
                transparent_pix.fill(Qt.transparent)
                painter = QPainter(transparent_pix)
                painter.setOpacity(0.85)
                painter.drawPixmap(0, 0, ghost)
                painter.end()
                drag.setPixmap(transparent_pix)
                drag.setHotSpot(QPoint(ghost.width() // 2, ghost.height() // 2))
        except Exception:
            pass
        drag.exec(Qt.DropAction.CopyAction)



# =========================================================================
# 5. WAVEFORM CANVAS & ASSET CARD (Bộ hiển thị đạo cụ trực quan)
# =========================================================================
class WaveformCanvas(QWidget):
    """
    Canvas vẽ dạng sóng (waveform) phong bì âm thanh (envelope bars).
    LƯU Ý KIẾN TRÚC: Đây là phong bì trang trí (decorative envelope bars) được tạo ra từ
    thuật toán băm xác định theo preset_id, KHÔNG PHẢI phân tích PCM/FFT thời gian thực.
    """
    def __init__(self, preset_id: str = "sfx", bars: Optional[List[float]] = None, parent=None, num_bars: int = 18, height: int = 30):
        super().__init__(parent)
        self.preset_id = preset_id
        self.bars = bars if bars is not None else self._generate_envelope_bars(preset_id, num_bars=num_bars)
        self.is_playing = False
        self.progress_fraction: float = 0.0
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_preset_id(self, preset_id: str, num_bars: int = 18):
        self.preset_id = preset_id
        self.bars = self._generate_envelope_bars(preset_id, num_bars=num_bars)
        self.update()

    def set_playback_progress(self, pos_ms: int, dur_ms: int):
        """Cập nhật tỷ lệ tiến trình phát trên thanh sóng (clamp [0.0, 1.0], an toàn khi duration=0/unknown)."""
        if dur_ms is None or dur_ms <= 0:
            self.progress_fraction = 0.0
        else:
            self.progress_fraction = max(0.0, min(1.0, float(pos_ms) / float(dur_ms)))
        self.update()

    @staticmethod
    def _generate_envelope_bars(preset_id: str, num_bars: int = 18) -> List[float]:
        # Hash identifier for consistent determinism
        pid = str(preset_id).lower()
        h = sum(ord(c) * (31 ** i) for i, c in enumerate(pid)) & 0x7FFFFFFF

        # Envelope classification
        if "whoosh" in pid or "swoosh" in pid:
            env_type = "hump"
        elif "riser" in pid or "drum_roll" in pid:
            env_type = "rise"
        elif any(k in pid for k in ["pop", "ding", "click", "punch", "hit", "shutter"]):
            env_type = "hit"
        elif "glitch" in pid:
            env_type = "glitch"
        elif "beep" in pid:
            env_type = "beep"
        else:
            env_type = "flat"

        bars = []
        for i in range(num_bars):
            h = (h * 1103515245 + 12345) & 0x7FFFFFFF
            noise = 0.6 + 0.4 * ((h % 1000) / 1000.0)
            x = i / max(1, num_bars - 1)

            if env_type == "rise":
                e = 0.12 + 0.88 * x
            elif env_type == "hump":
                e = math.sin(math.pi * x)
            elif env_type == "hit":
                e = math.exp(-4.5 * x)
            elif env_type == "beep":
                e = 0.85 if (0.12 < x < 0.88) else 0.12
            elif env_type == "glitch":
                e = 0.35 + 0.65 * abs(math.sin(x * 12.0))
            else:
                e = 0.55 + 0.25 * math.sin(x * 8.0)

            val = max(0.08, min(1.0, e * noise))
            bars.append(round(val, 3))
        return bars

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        n = len(self.bars)
        if n == 0:
            return

        gap = 2.0
        bar_w = max(2.0, (w - (n - 1) * gap) / n)

        color = QColor(ThemeColors.CYAN_HI if self.is_playing else ThemeColors.CYAN)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)

        for i, val in enumerate(self.bars):
            bar_h = max(3.0, val * (h - 4))
            x = i * (bar_w + gap)
            y = (h - bar_h) / 2.0
            painter.drawRoundedRect(QRectF(x, y, bar_w, bar_h), 1.0, 1.0)

        # Vẽ vạch playhead mảnh theo tiến trình thực khi đang phát
        if self.is_playing and self.progress_fraction > 0.0:
            playhead_x = int(self.progress_fraction * (w - 1))
            painter.setPen(QPen(QColor(ThemeColors.CYAN_HI), 1.5))
            painter.drawLine(playhead_x, 2, playhead_x, h - 2)


class LutSplitWidget(QWidget):
    """Widget so sánh Before / After 3D .cube LUT chuẩn DaVinci Resolve Color Gallery."""
    split_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.split_pct = 35
        self.strength = 1.0
        self.lut_colors = ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        self.lut_name = "Clean Rec.709"
        self._asset: Any = None
        self.setFixedHeight(140)
        self.setCursor(Qt.SplitHCursor)
        self.setToolTip("Kéo thanh gạt trực tiếp trên khung hình để so sánh màu GỐC | SAU")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_split(self, pct: int):
        self.split_pct = max(0, min(100, int(pct)))
        self.update()

    def set_strength(self, strength: float):
        self.strength = max(0.0, min(1.0, float(strength)))
        self.update()

    def set_lut(self, name: str, colors: Optional[List[str]] = None, asset: Any = None):
        self.lut_name = name
        self._asset = asset
        if colors and len(colors) >= 4:
            self.lut_colors = list(colors[:4])
        elif asset is not None:
            try:
                from src.ui.lux.library.lut_preview_generator import extract_lut_palette
                self.lut_colors = extract_lut_palette(asset)
            except Exception:
                self.lut_colors = ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        else:
            self.lut_colors = ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        self.update()

    def _scrub_mouse(self, event):
        w = max(1, self.width())
        pos_x = event.position().x() if hasattr(event, "position") else float(event.pos().x())
        new_pct = max(0, min(100, int(round((pos_x / w) * 100.0))))
        if new_pct != self.split_pct:
            self.split_pct = new_pct
            self.split_changed.emit(new_pct)
            self.update()

    def mousePressEvent(self, event):
        if event is not None and event.button() == Qt.LeftButton:
            self._scrub_mouse(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event is not None and (event.buttons() & Qt.LeftButton):
            self._scrub_mouse(event)
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.Antialiasing)

        try:
            from src.ui.lux.library.lut_preview_generator import draw_lut_preview_frame
            target_asset = (
                self._asset
                if self._asset is not None
                else {"name": self.lut_name, "pal": self.lut_colors}
            )
            draw_lut_preview_frame(
                painter,
                QRectF(0, 0, self.width(), self.height()),
                target_asset,
                split_pct=float(self.split_pct),
                strength=float(self.strength),
                show_hud=True,
                show_swatches=True,
                corner_radius=8.0,
            )
            return
        except Exception:
            pass

        w = self.width()
        h = self.height()
        split_x = int(w * (self.split_pct / 100.0))

        clip_path = QPainterPath()
        clip_path.addRoundedRect(0, 0, w, h, 8, 8)
        painter.setClipPath(clip_path)

        # 1. Cảnh Gốc (Original neutral scenery)
        grad_orig = QLinearGradient(0, 0, 0, h)
        grad_orig.setColorAt(0.0, QColor("#334155"))
        grad_orig.setColorAt(0.5, QColor("#1e293b"))
        grad_orig.setColorAt(1.0, QColor("#0f172a"))
        painter.fillRect(0, 0, w, h, grad_orig)

        # Mặt trời cảnh gốc
        painter.setBrush(QBrush(QColor(245, 158, 11, 140)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(w * 0.25, h * 0.35), 22, 22)

        # Núi đồi cảnh gốc
        orig_hill = QPainterPath()
        orig_hill.moveTo(0, h)
        orig_hill.lineTo(0, h * 0.6)
        orig_hill.quadTo(w * 0.25, h * 0.45, w * 0.55, h * 0.68)
        orig_hill.lineTo(w, h * 0.68)
        orig_hill.lineTo(w, h)
        painter.setBrush(QBrush(QColor("#1e293b")))
        painter.drawPath(orig_hill)

        # 2. Cảnh Sau (Graded scene with LUT palette)
        painter.save()
        painter.setClipRect(split_x, 0, w - split_x, h)
        grad_lut = QLinearGradient(0, 0, 0, h)
        c0 = QColor(self.lut_colors[0] if len(self.lut_colors) > 0 else "#09090b")
        c1 = QColor(self.lut_colors[1] if len(self.lut_colors) > 1 else "#7c3aed")
        c2 = QColor(self.lut_colors[2] if len(self.lut_colors) > 2 else "#ec4899")
        grad_lut.setColorAt(0.0, c0)
        grad_lut.setColorAt(0.5, c1)
        grad_lut.setColorAt(1.0, c2)
        painter.fillRect(split_x, 0, w - split_x, h, grad_lut)

        # Mặt trời graded
        sun_color = QColor(self.lut_colors[-1] if self.lut_colors else "#fde047")
        painter.setBrush(QBrush(sun_color))
        painter.drawEllipse(QPointF(w * 0.25, h * 0.35), 22, 22)

        # Đồi graded
        lut_hill = QPainterPath()
        lut_hill.moveTo(0, h)
        lut_hill.lineTo(0, h * 0.6)
        lut_hill.quadTo(w * 0.25, h * 0.45, w * 0.55, h * 0.68)
        lut_hill.lineTo(w, h * 0.68)
        lut_hill.lineTo(w, h)
        painter.setBrush(QBrush(c0.darker(140)))
        painter.drawPath(lut_hill)
        painter.restore()

        # 3. Vạch chia tách (Divider & handle)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.drawLine(split_x, 0, split_x, h)

        handle_y = h / 2.0
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(QPen(QColor(ThemeColors.BG_CARD), 2))
        painter.drawEllipse(QPointF(split_x, handle_y), 7, 7)

        # 4. Badges GỐC & SAU
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        # Gốc badge
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(QRectF(8, 8, 42, 18), 4, 4)
        painter.setPen(QPen(QColor("#e4e4e7")))
        painter.drawText(QRectF(8, 8, 42, 18), Qt.AlignCenter, "GỐC")

        # Sau badge
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(139, 92, 246, 200)))
        painter.drawRoundedRect(QRectF(w - 50, 8, 42, 18), 4, 4)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(QRectF(w - 50, 8, 42, 18), Qt.AlignCenter, "SAU")


PRESET_SIGNATURE_SAMPLES: Dict[str, str] = {
    "kinetic_hormozi": "TIỀN ĐẾN TỪ ĐÂU?",
    "karaoke_pop": "dính mưa rồi",
    "box_highlight": "100% sức khỏe",
    "highlighter_swipe": "BÚT DẠ QUANG HIGHLIGHT",
    "glow_neon": "NEON GLOW",
    "neon_pulse": "NEON NIGHT",
    "vhs_retro": "HÈ 1999 VINTAGE",
    "paper_cutout": "V L O G XÉ GIẤY",
    "rgb_glitch": "CYBERPUNK GLITCH",
    "gradient_fill": "HOÀNG HÔN SUNSET",
    "mrbeast_impact_yellow": "1 TRIỆU ĐÔ LA!",
    "apple_keynote_clean": "Think Different",
    "true_crime_documentary": "HỒ SƠ VỤ ÁN",
    "comic_manga_burst": "BOOM! ACTION",
    "luxury_gold_serif": "LUXURY GOLD",
    "minimal_podcast_pill": "The Podcast Talk",
    "schannel_vlog_nhanh": "ĐẬP HỘP SIÊU PHẨM",
    "khoai_lang_thang_chua_lanh": "Hành Trình Chữa Lành",
    "phim_truyen_hinh_vtv_retro": "VTV PHIM TRUYỆN",
    "giat_gan_showbiz_tiktok": "DRAMA CĂNG ĐÉT!",
    "clean_minimal": "Clean Minimal Podcast",
    "clean_outline": "Clean Outline Talk",
    "bounce_word": "Nhảy Chữ Spring",
    "slide_in": "Trượt Mượt Mà"
}


class TextLivePreviewWidget(QWidget):
    """
    Widget xem trước hiệu ứng chữ động (Text Live Preview) tốc độ cao ~30 FPS.
    Mô phỏng chân thực chuyển động từ Fusion Text+:
    - Spring Pop / Kinetic Bounce (Hormozi / TikTok Shorts): Chữ nảy lò xo kèm góc nghiêng nhẹ
    - Typewriter / VHS 90s: Hiệu ứng gõ máy từng ký tự kèm con trỏ nhấp nháy '|' và mốc thời gian retro
    - Neon Glow: Xung nhịp ánh sáng neon tỏa vầng quang mềm mại
    - Box Highlight / Marker Wipe: Dải highlight quét sau từ khóa chính
    - RGB Split Glitch: Tách quang sai đỏ-xanh kèm sọc quét số hóa
    - Paper Cutout: Nhãn dán xé giấy thủ công Scrapbook
    - Smooth Slide: Chữ trượt mượt mà từ dưới lên
    - Static: Chữ hiển thị tĩnh sắc nét, tương phản chuẩn truyền hình
    Hỗ trợ đổi tỷ lệ khung hình 16:9 / 9:16 và nhấp chuột để chạy lại (Replay).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.asset_obj = None
        self.file_path = ""
        self.text = "ResolveFlow Studio"
        self.preset_id = "kinetic_hormozi"
        self.font_family = "Montserrat"
        self.font_size = 48
        self.weight = "bold"
        self.standard_color = "#FFFFFF"
        self.highlight_color = "#FACC15"
        self.stroke_color = "#000000"
        self.stroke_width = 0.15
        self.box_color = None
        self.glow_color = None
        self.animation = "pop"
        self.timing_curve = "spring"
        self.aspect_ratio = "16:9"

        self.progress = 0.0
        self.setFixedHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Nhấp chuột để phát lại hiệu ứng hoạt họa")

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(33)  # ~30 FPS
        self.anim_timer.timeout.connect(self._on_tick)
        self.anim_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "anim_timer") and not self.anim_timer.isActive():
            self.anim_timer.start()

    def hideEvent(self, event):
        if hasattr(self, "anim_timer") and self.anim_timer.isActive():
            self.anim_timer.stop()
        super().hideEvent(event)

    def set_params(
        self,
        text: str = "",
        preset_id: str = "",
        font_family: str = "Arial",
        font_size: int = 48,
        weight: str = "bold",
        standard_color: str = "#FFFFFF",
        highlight_color: str = "#FACC15",
        stroke_color: str = "#000000",
        stroke_width: float = 0.15,
        box_color: Optional[str] = None,
        glow_color: Optional[str] = None,
        animation: str = "pop",
        timing_curve: str = "spring"
    ):
        if text is not None and text.strip():
            self.text = text.strip()
        if preset_id:
            self.preset_id = preset_id
        if font_family:
            self.font_family = font_family
        if font_size:
            self.font_size = font_size
        if weight:
            self.weight = weight
        if standard_color:
            self.standard_color = standard_color
        if highlight_color:
            self.highlight_color = highlight_color
        if stroke_color:
            self.stroke_color = stroke_color
        if stroke_width is not None:
            self.stroke_width = stroke_width
        self.box_color = box_color
        self.glow_color = glow_color
        if animation:
            self.animation = animation
        if timing_curve:
            self.timing_curve = timing_curve
        self.update()

    def set_aspect_ratio(self, aspect: str):
        self.aspect_ratio = aspect
        if aspect == "9:16":
            self.setFixedHeight(180)
        else:
            self.setFixedHeight(130)
        self.update()

    def replay(self):
        self.progress = 0.0
        self.update()

    def mousePressEvent(self, event):
        self.replay()
        super().mousePressEvent(event)

    def _on_tick(self):
        self.progress += 0.025
        if self.progress > 1.35:
            self.progress = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        try:
            from src.ui.lux.library.text_preview_generator import draw_davinci_text_frame
            overrides = {
                "text": self.text,
                "id": self.preset_id,
                "font_family": self.font_family,
                "font_size": self.font_size,
                "weight": self.weight,
                "standard_color": self.standard_color,
                "highlight_color": self.highlight_color,
                "stroke_color": self.stroke_color,
                "stroke_width": self.stroke_width,
                "box_color": self.box_color,
                "glow_color": self.glow_color,
                "animation": self.animation,
                "timing_curve": self.timing_curve,
                "file_path": self.file_path,
            }
            draw_davinci_text_frame(
                painter,
                QRectF(self.rect()),
                self.asset_obj if self.asset_obj is not None else self.preset_id,
                progress=min(1.0, max(0.0, self.progress)),
                aspect_ratio=self.aspect_ratio,
                show_hud=True,
                overrides=overrides,
            )
            return
        except Exception:
            pass

        w = self.width()
        h = self.height()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(0, 0, w, h, 10, 10)
        painter.setClipPath(clip_path)

        # 1. Nền canvas tối chuẩn monitor
        painter.fillRect(0, 0, w, h, QColor("#09090b"))

        # Xác định khung vùng hiển thị 16:9 hoặc 9:16
        if self.aspect_ratio == "9:16":
            frame_w = int(h * 9.0 / 16.0)
            fx = (w - frame_w) // 2
            # Vùng viền mờ 2 bên ngoài khung dọc 9:16
            painter.fillRect(0, 0, fx, h, QColor(0, 0, 0, 180))
            painter.fillRect(fx + frame_w, 0, w - (fx + frame_w), h, QColor(0, 0, 0, 180))
            # Đường guide màu cyan cho khung điện thoại 9:16
            painter.setPen(QPen(QColor("#06b6d4"), 1, Qt.DashLine))
            painter.drawRect(fx, 0, frame_w, h - 1)
            cx = w / 2.0
            cy = h / 2.0
            avail_w = frame_w - 24
        else:
            cx = w / 2.0
            cy = h / 2.0
            avail_w = w - 40
            # Safe area guide box 16:9
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1, Qt.DotLine))
            painter.drawRoundedRect(QRectF(12, 12, w - 24, h - 24), 6, 6)

        # 2. Xử lý kiểu hoạt họa & Visual Style (Animation & Preset Mode)
        anim = (self.animation or "pop").lower()
        pid = (self.preset_id or "").lower()

        # Nhận diện bản sắc nghệ thuật riêng của từng Preset trước (Artistic Visual Identity)
        if "paper" in pid:
            mode = "paper"
        elif "glitch" in pid or "showbiz" in pid or "rgb" in pid:
            mode = "glitch"
        elif "comic" in pid or "manga" in pid:
            mode = "comic"
        elif "luxury" in pid or "gold" in pid:
            mode = "luxury"
        elif "crime" in pid:
            mode = "crime"
        elif "vhs" in pid:
            mode = "vhs"
        elif "gradient" in pid or "sunset" in pid:
            mode = "gradient"
        elif "apple" in pid or "keynote" in pid or "khoai_lang" in pid:
            mode = "keynote"
        elif "highlight" in pid or "pill" in pid or "box" in pid:
            mode = "box"
        elif "neon" in pid or "glow" in pid:
            mode = "glow"
        elif anim == "typewriter":
            mode = "typewriter"
        elif anim == "glow":
            mode = "glow"
        elif anim == "glitch":
            mode = "glitch"
        elif anim == "paper":
            mode = "paper"
        elif anim in ("box", "box_highlight"):
            mode = "box"
        elif anim == "slide" or "slide" in pid:
            mode = "slide"
        elif anim == "static" or "retro" in pid or "minimal" in pid:
            mode = "static"
        elif anim in ("pop", "bounce") or "hormozi" in pid or "mrbeast" in pid or "schannel" in pid or "bounce" in pid:
            mode = "bounce"
        else:
            mode = "bounce"


        # 3. Typography & Size setup
        q_weight = QFont.Bold
        if self.weight in ("black", "extra_bold", "heavy"):
            q_weight = QFont.Black
        elif self.weight in ("normal", "regular", "light"):
            q_weight = QFont.Normal

        target_pt = max(11, min(24, int(self.font_size * 0.26)))
        if self.aspect_ratio == "9:16":
            target_pt = max(10, min(18, int(self.font_size * 0.20)))

        font = QFont(self.font_family, target_pt, q_weight)
        fm = QFontMetrics(font)

        raw_text = self.text.strip() if self.text.strip() else "ResolveFlow Studio"
        # Auto-scale font nếu text dài vượt chiều rộng khả dụng
        tw = fm.horizontalAdvance(raw_text)
        if tw > avail_w and avail_w > 40:
            scale_fac = max(0.5, avail_w / float(tw))
            target_pt = max(9, int(target_pt * scale_fac))
            font = QFont(self.font_family, target_pt, q_weight)
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(raw_text)

        words = raw_text.split()
        if not words:
            words = ["ResolveFlow"]

        # Độ dày stroke (viền chữ)
        sw = max(1.0, min(6.0, self.stroke_width * 20.0)) if self.stroke_width > 0.02 else 0.0
        stroke_pen = QPen(QColor(self.stroke_color), sw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        # 4. Vẽ hoạt họa theo từng Mode
        p = min(1.0, max(0.0, self.progress))

        if mode == "bounce":
            if self.progress <= 0.01:
                s = 1.0
            else:
                t = min(1.0, self.progress * 2.0)
                if t < 0.6:
                    s = (t / 0.6) * 1.24
                elif t < 0.85:
                    s = 1.24 - ((t - 0.6) / 0.25) * 0.28
                else:
                    s = 0.96 + ((t - 0.85) / 0.15) * 0.04

            # MrBeast extra impact scale
            if "mrbeast" in pid:
                s = s * 1.08

            space_w = fm.horizontalAdvance(" ")
            advances = [fm.horizontalAdvance(w) for w in words]
            tot_w = sum(advances) + space_w * (len(words) - 1)
            start_x = cx - tot_w / 2.0
            cur_x = start_x
            text_baseline = cy + fm.height() / 4.0

            hl_idx = len(words) - 1
            for i, word in enumerate(words):
                ww = advances[i]
                wh = fm.height()
                if i == hl_idx:
                    painter.save()
                    painter.translate(cur_x + ww / 2.0, text_baseline - wh / 4.0)
                    painter.scale(s, s)
                    rot = -3.0 if ("hormozi" in pid or "schannel" in pid) else 0.0
                    if rot != 0.0:
                        painter.rotate(rot)
                    path = QPainterPath()
                    path.addText(QPointF(-ww / 2.0, wh / 4.0), font, word)
                    if sw > 0:
                        painter.strokePath(path, stroke_pen)
                    painter.fillPath(path, QBrush(QColor(self.highlight_color)))
                    painter.restore()
                else:
                    path = QPainterPath()
                    path.addText(QPointF(cur_x, text_baseline), font, word)
                    if sw > 0:
                        painter.strokePath(path, stroke_pen)
                    painter.fillPath(path, QBrush(QColor(self.standard_color)))
                cur_x += ww + space_w

        elif mode == "paper":
            space_w = fm.horizontalAdvance(" ") + 6
            advances = [fm.horizontalAdvance(w) for w in words]
            tot_w = sum(advances) + space_w * (len(words) - 1)
            start_x = cx - tot_w / 2.0
            cur_x = start_x
            text_baseline = cy + fm.height() / 4.0

            hl_idx = len(words) - 1
            for i, word in enumerate(words):
                ww = advances[i]
                wh = fm.height()
                painter.save()
                rot = -2.5 if (i % 2 == 0) else 2.5
                painter.translate(cur_x + ww / 2.0, cy)
                painter.rotate(rot)

                card_rect = QRectF(-ww / 2.0 - 6, -wh / 2.0 - 2, ww + 12, wh + 6)
                # Đổ bóng nhãn giấy scrapbook
                painter.fillRect(card_rect.translated(2.0, 2.5), QColor(0, 0, 0, 110))

                # Nền giấy ngà vintage hoặc highlight đỏ
                if i == hl_idx and self.highlight_color.upper() not in ("#FFFFFF", "#FFF"):
                    paper_bg = QColor(self.highlight_color)
                    text_ink = QColor("#FFFFFF")
                else:
                    paper_bg = QColor(self.box_color or "#FBF9F1")
                    text_ink = QColor(self.standard_color if self.standard_color.upper() != "#FFFFFF" else "#18181B")

                painter.fillRect(card_rect, paper_bg)
                painter.setPen(QPen(QColor("#333333"), 1))
                painter.drawRect(card_rect)

                path = QPainterPath()
                path.addText(QPointF(-ww / 2.0, wh / 4.0), font, word)
                painter.fillPath(path, QBrush(text_ink))
                painter.restore()
                cur_x += ww + space_w

        elif mode == "glitch":
            text_baseline = cy + fm.height() / 4.0
            is_glitch_frame = (int(self.progress * 24) % 5) in (0, 1)

            if is_glitch_frame:
                # Kênh màu Cyan lệch phải
                path_cyan = QPainterPath()
                path_cyan.addText(QPointF(cx - tw / 2.0 + 3.5, text_baseline), font, raw_text)
                painter.fillPath(path_cyan, QBrush(QColor(0, 229, 255, 200)))

                # Kênh màu Magenta/Red lệch trái
                path_red = QPainterPath()
                path_red.addText(QPointF(cx - tw / 2.0 - 3.5, text_baseline), font, raw_text)
                painter.fillPath(path_red, QBrush(QColor(255, 0, 85, 200)))

                # Vạch nhiễu sọc quét ngang
                slice_y = int(cy - 8 + (int(self.progress * 48) % 18))
                painter.fillRect(int(cx - tw / 2.0 - 8), slice_y, tw + 16, 2, QColor(0, 229, 255, 180))

            path_center = QPainterPath()
            path_center.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)
            if sw > 0:
                painter.strokePath(path_center, stroke_pen)
            painter.fillPath(path_center, QBrush(QColor(self.standard_color)))

        elif mode == "comic":
            t = min(1.0, self.progress * 2.0)
            s = 1.05 + 0.08 * math.sin(t * math.pi)

            painter.save()
            painter.translate(cx, cy)
            painter.scale(s, s)
            painter.rotate(-3.5)

            burst_rect = QRectF(-tw / 2.0 - 18, -fm.height() / 2.0 - 8, tw + 36, fm.height() + 16)
            painter.setBrush(QBrush(QColor(255, 235, 59, 140)))
            painter.setPen(QPen(QColor("#000000"), 2))
            painter.drawRoundedRect(burst_rect, 6, 6)

            text_baseline = fm.height() / 4.0
            space_w = fm.horizontalAdvance(" ")
            advances = [fm.horizontalAdvance(w) for w in words]
            tot_w = sum(advances) + space_w * (len(words) - 1)
            cur_x = -tot_w / 2.0
            hl_idx = len(words) - 1

            comic_stroke = QPen(QColor("#000000"), max(3.5, sw * 1.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            for i, word in enumerate(words):
                ww = advances[i]
                path = QPainterPath()
                path.addText(QPointF(cur_x, text_baseline), font, word)
                painter.strokePath(path, comic_stroke)
                fill_c = self.highlight_color if i == hl_idx else self.standard_color
                painter.fillPath(path, QBrush(QColor(fill_c)))
                cur_x += ww + space_w

            painter.restore()

        elif mode == "luxury":
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            gold_grad = QLinearGradient(0, cy - fm.height() / 2.0, 0, cy + fm.height() / 2.0)
            gold_grad.setColorAt(0.0, QColor("#FFF8E1"))
            gold_grad.setColorAt(0.35, QColor("#FBBF24"))
            gold_grad.setColorAt(0.7, QColor("#D97706"))
            gold_grad.setColorAt(1.0, QColor("#78350F"))

            line_w = tw * 0.7
            painter.setPen(QPen(QColor(245, 158, 11, 160), 1))
            painter.drawLine(int(cx - line_w / 2.0), int(cy - fm.height() / 2.0 - 5), int(cx + line_w / 2.0), int(cy - fm.height() / 2.0 - 5))
            painter.drawLine(int(cx - line_w / 2.0), int(cy + fm.height() / 2.0 + 5), int(cx + line_w / 2.0), int(cy + fm.height() / 2.0 + 5))

            if sw > 0:
                painter.strokePath(path, QPen(QColor("#451A03"), sw))
            painter.fillPath(path, QBrush(gold_grad))

        elif mode == "gradient":
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            grad = QLinearGradient(cx - tw / 2.0, 0, cx + tw / 2.0, 0)
            grad.setColorAt(0.0, QColor("#FFA07A"))
            grad.setColorAt(0.5, QColor("#FF4500"))
            grad.setColorAt(1.0, QColor("#FF1493"))

            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(grad))

        elif mode == "crime":
            painter.setFont(QFont("Georgia", 8, QFont.Bold))
            painter.setPen(QColor("#EF4444"))
            painter.drawText(QRectF(16, 12, 160, 16), Qt.AlignLeft, "● HỒ SƠ VỤ ÁN")

            n_chars = max(1, int(min(1.0, self.progress * 1.5) * len(raw_text)))
            sub_str = raw_text[:n_chars]
            cursor = "|" if (int(self.progress * 8) % 2 == 0) else " "
            render_str = sub_str + cursor

            tw_sub = fm.horizontalAdvance(render_str)
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw_sub / 2.0, text_baseline), font, render_str)
            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(QColor(self.highlight_color if n_chars >= len(raw_text) else self.standard_color)))

        elif mode == "vhs":
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QColor("#22C55E"))
            painter.drawText(QRectF(16, 12, 140, 16), Qt.AlignLeft, "▶ PLAY SP 00:12")
            painter.drawText(QRectF(w - 70, 12, 60, 16), Qt.AlignRight, "CH 03")

            n_chars = max(1, int(min(1.0, self.progress * 1.6) * len(raw_text)))
            sub_str = raw_text[:n_chars]
            cursor = "_" if (int(self.progress * 8) % 2 == 0) else " "
            render_str = sub_str + cursor

            tw_sub = fm.horizontalAdvance(render_str)
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw_sub / 2.0, text_baseline), font, render_str)
            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(QColor(self.standard_color)))

        elif mode == "keynote":
            t = min(1.0, self.progress * 1.8)
            ease = 1.0 - (1.0 - t) ** 3
            offset_y = (1.0 - ease) * 16.0

            text_baseline = cy + fm.height() / 4.0 + offset_y
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            painter.setOpacity(t)
            path_shadow = QPainterPath()
            path_shadow.addText(QPointF(cx - tw / 2.0 + 1.5, text_baseline + 1.5), font, raw_text)
            painter.fillPath(path_shadow, QBrush(QColor(0, 0, 0, 80)))

            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(QColor(self.standard_color)))
            painter.setOpacity(1.0)

        elif mode == "box":
            t = min(1.0, self.progress * 2.0)
            ease = 1.0 - (1.0 - t) ** 3

            space_w = fm.horizontalAdvance(" ")
            advances = [fm.horizontalAdvance(w) for w in words]
            tot_w = sum(advances) + space_w * (len(words) - 1)
            start_x = cx - tot_w / 2.0
            cur_x = start_x
            text_baseline = cy + fm.height() / 4.0

            if "pill" in pid:
                pill_rect = QRectF(cx - tot_w / 2.0 - 12, cy - fm.height() / 2.0 - 4, tot_w + 24, fm.height() + 8)
                painter.setBrush(QBrush(QColor(self.box_color or "#0F172A")))
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
                painter.drawRoundedRect(pill_rect, 14, 14)

                hl_idx = len(words) - 1
                for i, word in enumerate(words):
                    ww = advances[i]
                    path = QPainterPath()
                    path.addText(QPointF(cur_x, text_baseline), font, word)
                    fill_c = self.highlight_color if i == hl_idx else self.standard_color
                    painter.fillPath(path, QBrush(QColor(fill_c)))
                    cur_x += ww + space_w

            elif "highlight" in pid or "highlighter" in pid:
                hl_idx = len(words) - 1
                box_c = QColor(self.box_color or "#FFE600")
                for i, word in enumerate(words):
                    ww = advances[i]
                    wh = fm.height()
                    if i == hl_idx:
                        mark_w = (ww + 10) * ease
                        marker_rect = QRectF(cur_x - 5, cy - wh / 2.0 + 2, mark_w, wh - 2)
                        painter.fillRect(marker_rect, QColor(box_c.red(), box_c.green(), box_c.blue(), 200))
                    path = QPainterPath()
                    path.addText(QPointF(cur_x, text_baseline), font, word)
                    painter.fillPath(path, QBrush(QColor(self.standard_color if i != hl_idx else self.highlight_color)))
                    cur_x += ww + space_w
            else:
                hl_idx = len(words) - 1
                box_c = QColor(self.box_color or self.highlight_color or "#EF4444")
                for i, word in enumerate(words):
                    ww = advances[i]
                    wh = fm.height()
                    if i == hl_idx:
                        box_w = (ww + 12) * ease
                        box_rect = QRectF(cur_x - 6, cy - wh / 2.0, box_w, wh + 2)
                        painter.setBrush(QBrush(box_c))
                        painter.setPen(Qt.NoPen)
                        painter.drawRoundedRect(box_rect, 4, 4)
                        path = QPainterPath()
                        path.addText(QPointF(cur_x, text_baseline), font, word)
                        painter.fillPath(path, QBrush(QColor("#FFFFFF")))
                    else:
                        path = QPainterPath()
                        path.addText(QPointF(cur_x, text_baseline), font, word)
                        if sw > 0:
                            painter.strokePath(path, stroke_pen)
                        painter.fillPath(path, QBrush(QColor(self.standard_color)))
                    cur_x += ww + space_w

        elif mode == "glow":
            sine_val = 0.5 + 0.5 * math.sin(self.progress * 2.0 * math.pi)
            glow_c_hex = self.glow_color or self.highlight_color or "#06B6D4"
            g_col = QColor(glow_c_hex)
            alpha_base = int(120 + 130 * sine_val)

            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            for radius in (12, 8, 4):
                g_pen = QPen(QColor(g_col.red(), g_col.green(), g_col.blue(), int(alpha_base / (radius / 3.0))), radius)
                painter.strokePath(path, g_pen)

            painter.fillPath(path, QBrush(QColor("#FFFFFF")))

        elif mode == "slide":
            t = min(1.0, self.progress * 1.8)
            ease = 1.0 - (1.0 - t) ** 3
            offset_y = (1.0 - ease) * 28.0

            text_baseline = cy + fm.height() / 4.0 + offset_y
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            painter.setOpacity(t)
            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(QColor(self.standard_color)))
            painter.setOpacity(1.0)

        elif mode == "typewriter":
            n_chars = max(1, int(min(1.0, self.progress * 1.6) * len(raw_text)))
            sub_str = raw_text[:n_chars]
            cursor = "|" if (int(self.progress * 9) % 2 == 0) else " "
            render_str = sub_str + cursor

            tw_sub = fm.horizontalAdvance(render_str)
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw_sub / 2.0, text_baseline), font, render_str)
            if sw > 0:
                painter.strokePath(path, stroke_pen)
            painter.fillPath(path, QBrush(QColor(self.standard_color)))

        else:
            text_baseline = cy + fm.height() / 4.0
            path = QPainterPath()
            path.addText(QPointF(cx - tw / 2.0, text_baseline), font, raw_text)

            if "vtv" in pid or "retro" in pid:
                path_shadow = QPainterPath()
                path_shadow.addText(QPointF(cx - tw / 2.0 + 2, text_baseline + 2), font, raw_text)
                painter.fillPath(path_shadow, QBrush(QColor("#000000")))
                painter.fillPath(path, QBrush(QColor(self.standard_color or "#FFEB3B")))
            else:
                if sw > 0:
                    painter.strokePath(path, stroke_pen)
                painter.fillPath(path, QBrush(QColor(self.standard_color)))

        # 5. Badge góc: Hiển thị thông tin Live 30fps và Tỷ lệ khung hình
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        # Top-left LIVE badge
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(QRectF(8, 8, 62, 18), 4, 4)
        painter.setPen(QPen(QColor("#10b981")))
        painter.drawText(QRectF(8, 8, 62, 18), Qt.AlignCenter, "● LIVE")

        # Top-right Aspect & Anim badge
        anim_label = mode.capitalize()
        badge_text = f"{anim_label} • {self.aspect_ratio}"
        bw = max(70, int(len(badge_text) * 6.5) + 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(QRectF(w - bw - 8, 8, bw, 18), 4, 4)
        painter.setPen(QPen(QColor(ThemeColors.CYAN_HI)))
        painter.drawText(QRectF(w - bw - 8, 8, bw, 18), Qt.AlignCenter, badge_text)


class TransitionLoopWidget(QWidget):
    """Widget xem trước chuyển cảnh dạng lặp (Transition Visual Loop Preview)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._asset: Any = None
        self.thumb_path: str = ""
        self.trans_name = "Whip Pan Left"
        self.trans_kind = "transform"
        self.frames = 20
        self.progress = 0.0
        self.setFixedHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Nhấp chuột để phát lại chuyển cảnh")

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(33)
        self.anim_timer.timeout.connect(self._on_tick)
        self.anim_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "anim_timer") and not self.anim_timer.isActive():
            self.anim_timer.start()

    def hideEvent(self, event):
        if hasattr(self, "anim_timer") and self.anim_timer.isActive():
            self.anim_timer.stop()
        super().hideEvent(event)

    def set_transition(self, name: str, kind: str, frames: int, asset: Any = None, thumb_path: str = ""):
        self.trans_name = name
        self.trans_kind = kind
        self.frames = frames
        if asset is not None:
            self._asset = asset
        if thumb_path is not None:
            self.thumb_path = thumb_path
        self.update()

    def mousePressEvent(self, event):
        self.progress = 0.0
        self.update()
        super().mousePressEvent(event)

    def _on_tick(self):
        self.progress += 0.03
        if self.progress > 1.3:
            self.progress = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QPainter.Antialiasing)

        try:
            from src.ui.lux.library.transition_preview_generator import draw_transition_frame
            draw_transition_frame(
                painter,
                QRectF(self.rect()),
                self._asset if self._asset is not None else self.trans_name,
                progress=min(1.0, max(0.0, self.progress)),
                frames=self.frames,
                kind=self.trans_kind,
                thumb_path=self.thumb_path,
                show_hud=True,
            )
            return
        except Exception:
            pass

        w = self.width()
        h = self.height()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(0, 0, w, h, 8, 8)
        painter.setClipPath(clip_path)

        # Clip A (Violet)
        painter.fillRect(0, 0, w, h, QColor("#1e1b4b"))

        p = min(1.0, max(0.0, self.progress))
        if "whip" in self.trans_kind or "slide" in self.trans_kind:
            bx = int(w * (1.0 - p))
            painter.fillRect(bx, 0, w, h, QColor("#083344"))
            if 0.05 < p < 0.95:
                painter.setPen(QPen(QColor("#06b6d4"), 3))
                painter.drawLine(bx, 0, bx, h)
        elif "zoom" in self.trans_kind:
            scale = 0.2 + 0.8 * p
            bw = int(w * scale)
            bh = int(h * scale)
            bx = int((w - bw) / 2)
            by = int((h - bh) / 2)
            painter.fillRect(bx, by, bw, bh, QColor("#083344"))
        elif "glitch" in self.trans_kind:
            if int(p * 10) % 2 == 0:
                painter.fillRect(0, 0, w, h, QColor("#083344"))
                gy = int(h * ((p * 3) % 1.0))
                painter.fillRect(0, gy, w, 14, QColor("#ef4444"))
            else:
                painter.fillRect(0, 0, w, h, QColor("#1e1b4b"))
        else:
            alpha = int(p * 255)
            painter.fillRect(0, 0, w, h, QColor(8, 51, 68, alpha))
            if "leak" in self.trans_kind or "glow" in self.trans_kind:
                painter.setBrush(QBrush(QColor(253, 224, 71, int((1.0 - abs(p - 0.5) * 2) * 160))))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(QPointF(w * p, h * 0.5), 50, 50)

        # Labels Clip A & B
        painter.setFont(QFont("Arial Black", 14))
        painter.setPen(QColor(255, 255, 255, 120))
        painter.drawText(QRectF(16, 0, 50, h), Qt.AlignVCenter, "A")
        painter.drawText(QRectF(w - 66, 0, 50, h), Qt.AlignVCenter | Qt.AlignRight, "B")

        # Frame badge
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
        painter.drawRoundedRect(QRectF(w - 74, 8, 66, 18), 4, 4)
        painter.setPen(QPen(QColor(ThemeColors.CYAN_HI)))
        painter.drawText(QRectF(w - 74, 8, 66, 18), Qt.AlignCenter, f"{self.frames} frame")

        # Transition name badge
        painter.drawRoundedRect(QRectF(8, h - 26, w - 16, 20), 4, 4)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(QRectF(14, h - 26, w - 28, 20), Qt.AlignVCenter | Qt.AlignLeft, f"🔁 {self.trans_name}")


class IconMemePreviewWidget(QWidget):
    """Widget xem trước Icon vector hoặc Meme video reaction có trình phát video thực tế được bảo vệ qua ActionGuard."""
    def __init__(self, parent=None, bundled_root: Optional[str] = None):
        super().__init__(parent)
        self._current_asset: Any = None
        self._playback_epoch: int = 0
        self._is_active_playback: bool = False
        self._player_conns: list = []
        self._last_guard_reason: str = ""

        self.item_name = ""
        self.item_type = "icon"
        self.badge_icon = "✨"
        self.color_hex = "#facc15"
        self.sub_text = ""
        self.thumbnail_path = ""
        self.file_path = ""
        self.resolved_file_path = ""
        self._pixmap = None
        self._duration_ms = 0
        self._position_ms = 0
        self._bundled_root = bundled_root

        self.setFixedHeight(175)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._init_ui()

    def _init_ui(self):
        main_vbox = QVBoxLayout(self)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(4)

        # 1. Stacked container: Sticker/Poster Canvas vs Real Video Output
        self.display_container = QStackedWidget(self)
        self.display_container.setFixedHeight(132)
        self.display_container.setStyleSheet("background-color: #09090b; border-radius: 8px;")

        # Page 0: Canvas (used for Sticker & Poster preview)
        self.canvas_widget = QWidget(self.display_container)
        self.canvas_widget.paintEvent = self._paint_canvas
        self.canvas_widget.setCursor(Qt.PointingHandCursor)
        self.canvas_widget.mousePressEvent = self._on_canvas_clicked
        self.display_container.addWidget(self.canvas_widget)

        # Page 1: Video widget (active playback)
        self.video_container = QWidget(self.display_container)
        v_layout = QVBoxLayout(self.video_container)
        v_layout.setContentsMargins(0, 0, 0, 0)
        self.video_widget = QVideoWidget(self.video_container)
        self.video_widget.setStyleSheet("background-color: #000000; border-radius: 8px;")
        v_layout.addWidget(self.video_widget)
        self.display_container.addWidget(self.video_container)

        main_vbox.addWidget(self.display_container)

        # 2. Control bar for Meme / Video
        self.control_bar = QWidget(self)
        ctrl_layout = QHBoxLayout(self.control_bar)
        ctrl_layout.setContentsMargins(4, 2, 4, 2)
        ctrl_layout.setSpacing(6)

        self.btn_play = QPushButton("▶ Phát video", self.control_bar)
        self.btn_play.setFixedHeight(26)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.CYAN};
                border-radius: 4px;
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
                padding: 2px 8px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.CYAN};
                color: #05252c;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                border-color: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.3);
            }}
        """)
        self.btn_play.clicked.connect(self._toggle_playback)

        self.btn_replay = QPushButton("↺", self.control_bar)
        self.btn_replay.setFixedSize(26, 26)
        self.btn_replay.setCursor(Qt.PointingHandCursor)
        self.btn_replay.setToolTip("Phát lại từ đầu")
        self.btn_replay.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 4px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.PRIMARY};
                color: {ThemeColors.PRIMARY};
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                border-color: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.3);
            }}
        """)
        self.btn_replay.clicked.connect(self._replay)

        self.lbl_time = QLabel("00:00 / 00:00", self.control_bar)
        self.lbl_time.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")

        self.btn_browse = QPushButton("📂", self.control_bar)
        self.btn_browse.setFixedSize(26, 26)
        self.btn_browse.setToolTip("Tính năng liên kết tệp tạm thời vô hiệu hóa; vui lòng quản lý qua Thư viện")
        self.btn_browse.setEnabled(False)
        self.btn_browse.hide()
        self.btn_browse.clicked.connect(self._browse_file)

        self.btn_open_external = QPushButton("↗ Mở ngoài", self.control_bar)
        self.btn_open_external.setFixedHeight(26)
        self.btn_open_external.setCursor(Qt.PointingHandCursor)
        self.btn_open_external.setToolTip("Mở video bằng trình phát mặc định của hệ điều hành")
        self.btn_open_external.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 4px;
                color: {ThemeColors.TEXT_MUTED};
                font-size: 11px;
                padding: 2px 6px;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
                border-color: {ThemeColors.BORDER_HOVER};
            }}
            QPushButton:disabled {{
                color: rgba(255, 255, 255, 0.2);
                border-color: rgba(255, 255, 255, 0.08);
            }}
        """)
        self.btn_open_external.clicked.connect(self._open_external)

        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(self.btn_replay)
        ctrl_layout.addWidget(self.lbl_time)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_browse)
        ctrl_layout.addWidget(self.btn_open_external)

        main_vbox.addWidget(self.control_bar)

        self._player = None
        self._audio_output = None

    def _ensure_player(self):
        if self._player is not None:
            return
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        from src.services.audio_router import get_audio_router
        get_audio_router().register(self._audio_output)
        self._audio_output.setVolume(0.9)
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self.video_widget)

    def _disconnect_session(self):
        """Ngắt toàn bộ kết nối signal của session trước mà widget sở hữu."""
        if self._player is not None and self._player_conns:
            for conn in list(self._player_conns):
                try:
                    if hasattr(QObject, "disconnect") and isinstance(conn, QMetaObject.Connection):
                        QObject.disconnect(conn)
                    elif hasattr(self._player, "disconnect"):
                        self._player.disconnect(conn)
                except Exception:
                    pass
            self._player_conns.clear()

    def _start_session(self, reset_pos: bool = False):
        """Khởi tạo phiên phát mới có gắn epoch/token độc quyền, loại trừ callback cũ."""
        self._disconnect_session()
        self._playback_epoch += 1
        epoch = self._playback_epoch
        self._is_active_playback = True

        def on_pos(pos: int):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_position_changed(pos, epoch=epoch)

        def on_dur(dur: int):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_duration_changed(dur, epoch=epoch)

        def on_state(state):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_playback_state_changed(state, epoch=epoch)

        def on_err(err, err_str=""):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_player_error(err, err_str, epoch=epoch)

        def on_media_status(status):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            try:
                from PySide6.QtMultimedia import QMediaPlayer
                if (
                    status == QMediaPlayer.MediaStatus.EndOfMedia
                    and self._is_active_playback
                    and str(self.item_type).lower() in ("trans", "transition", "transitions")
                    and self._player is not None
                ):
                    self._player.setPosition(0)
                    self._player.play()
            except Exception:
                pass

        if hasattr(self._player, "positionChanged"):
            c1 = self._player.positionChanged.connect(on_pos)
            self._player_conns.append(c1)
        if hasattr(self._player, "durationChanged"):
            c2 = self._player.durationChanged.connect(on_dur)
            self._player_conns.append(c2)
        if hasattr(self._player, "playbackStateChanged"):
            c3 = self._player.playbackStateChanged.connect(on_state)
            self._player_conns.append(c3)
        if hasattr(self._player, "mediaStatusChanged"):
            c4 = self._player.mediaStatusChanged.connect(on_media_status)
            self._player_conns.append(c4)
        if hasattr(self._player, "errorOccurred"):
            c5 = self._player.errorOccurred.connect(on_err)
            self._player_conns.append(c5)

        source = QUrl.fromLocalFile(self.resolved_file_path)
        if self._player.source() != source:
            self._player.setSource(source)
        if reset_pos:
            self._player.setPosition(0)
            self._position_ms = 0
            self._update_time_label()

        self.display_container.setCurrentWidget(self.video_container)
        self._player.play()
        self.btn_play.setText("⏸ Dừng phát")
        self.btn_play.setToolTip("Dừng phát video")

    def hideEvent(self, event):
        self.stop_playback()
        if event is not None:
            super().hideEvent(event)

    def closeEvent(self, event):
        self.stop_playback()
        if event is not None:
            super().closeEvent(event)

    def _recheck_action_time_guard(self) -> bool:
        """Thẩm định lại quyền hạn nguồn tại thời điểm thực hiện thao tác (Action-Time Guard Recheck)."""
        if self._current_asset is None:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = "Chưa chọn tài nguyên."
            self.btn_play.setEnabled(False)
            self.btn_replay.setEnabled(False)
            self.btn_open_external.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        payload = _normalize_preview_payload(self._current_asset)
        guard_res = check_action_availability(payload, intent="preview", bundled_root=getattr(self, "_bundled_root", None))
        if not guard_res.allowed or not guard_res.resolved_path:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = guard_res.reason or "Tài nguyên không khả dụng."
            self.btn_play.setEnabled(False)
            self.btn_replay.setEnabled(False)
            self.btn_open_external.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        ext = os.path.splitext(guard_res.resolved_path)[1].lower()
        if ext not in VIDEO_EXTS:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = "Định dạng tệp không được hỗ trợ để phát video."
            self.btn_play.setEnabled(False)
            self.btn_replay.setEnabled(False)
            self.btn_open_external.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        self.resolved_file_path = guard_res.resolved_path
        return True

    def set_asset(self, asset: Any, sub: str = ""):
        """Nạp tài nguyên vào Inspector với đầy đủ thẩm định ActionGuard."""
        self.stop_playback()
        self._current_asset = asset
        self._position_ms = 0
        self._duration_ms = 0
        self._last_guard_reason = ""

        if isinstance(asset, Mapping):
            self.item_name = str(asset.get("name") or asset.get("id") or "")
            self.item_type = str(asset.get("tab") or asset.get("category") or "icon")
            self.badge_icon = str(asset.get("badge_icon") or "✨")
            self.color_hex = str(asset.get("color_hex") or "#facc15")
            self.sub_text = str(sub or asset.get("sub") or "")
            thumb_path = str(asset.get("thumbnail_path") or "")
            self.file_path = str(asset.get("file_path") or "")
        else:
            self.item_name = str(getattr(asset, "name", "") or getattr(asset, "id", ""))
            self.item_type = str(getattr(asset, "tab", "") or getattr(asset, "category", "icon"))
            self.badge_icon = str(getattr(asset, "badge_icon", "✨") or "✨")
            self.color_hex = str(getattr(asset, "color_hex", "#facc15") or "#facc15")
            self.sub_text = str(sub or getattr(asset, "sub", ""))
            thumb_path = str(getattr(asset, "thumbnail_path", "") or "")
            self.file_path = str(getattr(asset, "file_path", "") or "")

        self.thumbnail_path = thumb_path
        if thumb_path and os.path.isfile(thumb_path):
            self._pixmap = QPixmap(thumb_path)
        else:
            self._pixmap = None

        payload = _normalize_preview_payload(asset)
        guard_res = check_action_availability(payload, intent="preview", bundled_root=getattr(self, "_bundled_root", None))

        if guard_res.allowed and guard_res.resolved_path:
            ext = os.path.splitext(guard_res.resolved_path)[1].lower()
            if ext in VIDEO_EXTS:
                self.resolved_file_path = guard_res.resolved_path
                self.btn_play.setEnabled(True)
                self.btn_replay.setEnabled(True)
                self.btn_open_external.setEnabled(True)
                self.btn_play.setText("▶ Phát video")
                self.btn_play.setToolTip("Phát video preview")
                self.lbl_time.setText("00:00 / 00:00")
            else:
                self.resolved_file_path = ""
                self.btn_play.setEnabled(False)
                self.btn_replay.setEnabled(False)
                self.btn_open_external.setEnabled(False)
                self._last_guard_reason = "Định dạng tệp không được hỗ trợ để phát video."
                self.btn_play.setText("⚠️ Không khả dụng")
                self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
                self.lbl_time.setText("--:-- / --:--")
        else:
            self.resolved_file_path = ""
            self.btn_play.setEnabled(False)
            self.btn_replay.setEnabled(False)
            self.btn_open_external.setEnabled(False)
            self._last_guard_reason = guard_res.reason or "Tài nguyên không khả dụng."
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")

        if self.item_type == "icon":
            self.display_container.setCurrentWidget(self.canvas_widget)
            self.control_bar.hide()
            self.setFixedHeight(136)
        else:
            self.control_bar.show()
            self.setFixedHeight(175)
            self.display_container.setCurrentWidget(self.canvas_widget)

        self.canvas_widget.update()
        self.update()

    def set_item(self, name: str, item_type: str, badge: str, color_hex: str, sub: str, thumbnail_path: str = "", file_path: str = "", asset_id: str = ""):
        """Wrapper tương thích ngược cho các unit test và callsite scalar cũ."""
        legacy_asset = {
            "id": asset_id or name,
            "name": name,
            "tab": item_type,
            "category": item_type,
            "badge_icon": badge or "✨",
            "color_hex": color_hex or "#facc15",
            "thumbnail_path": thumbnail_path,
            "file_path": file_path,
            "preview_path": file_path,
        }
        self.set_asset(legacy_asset, sub=sub)

    def _set_audio_volume(self, volume: float) -> None:
        if self._audio_output is not None:
            try:
                self._audio_output.setVolume(volume)
            except Exception:
                pass

    def start_auto_preview(self, muted: bool = True):
        """Khởi động phát xem trước tự động cho video (vd: chuyển cảnh), tuân thủ ActionGuard."""
        if not self.resolved_file_path or not os.path.isfile(self.resolved_file_path):
            return
        if not self._recheck_action_time_guard():
            return
        self._ensure_player()
        self._set_audio_volume(0.0 if muted else 0.9)
        self._start_session(reset_pos=True)

    def _browse_file(self):
        """Safe no-op: Tính năng liên kết tệp trực tiếp tạm thời vô hiệu hóa."""
        pass

    def _toggle_playback(self):
        if self.resolved_file_path:
            if not self._recheck_action_time_guard():
                return
            self._ensure_player()
            if self._player.playbackState() == QMediaPlayer.PlayingState:
                self._player.pause()
                self.btn_play.setText("▶ Tiếp tục")
                self.btn_play.setToolTip("Tiếp tục phát video")
            else:
                self._set_audio_volume(0.9)
                self._start_session(reset_pos=False)
        else:
            self.stop_playback()

    def _start_playback(self):
        if self.resolved_file_path:
            if not self._recheck_action_time_guard():
                return
            self._ensure_player()
            self._set_audio_volume(0.9)
            self._start_session(reset_pos=False)
        else:
            self.stop_playback()

    def _replay(self):
        if self.resolved_file_path:
            if not self._recheck_action_time_guard():
                return
            self._ensure_player()
            self._set_audio_volume(0.9)
            self._start_session(reset_pos=True)
        else:
            self.stop_playback()

    def stop_playback(self):
        """Dừng phát video an toàn: tăng epoch trước, dọn connections và clear player source."""
        was_active = self._is_active_playback
        self._playback_epoch += 1
        self._is_active_playback = False
        self._disconnect_session()

        if self._player is not None:
            try:
                src_fn = getattr(self._player, "source", None)
                cur_src = src_fn() if callable(src_fn) else None
                state_fn = getattr(self._player, "playbackState", None)
                cur_state = state_fn() if callable(state_fn) else None
                has_src = cur_src is None or not hasattr(cur_src, "isEmpty") or not cur_src.isEmpty()
                if was_active or cur_state != QMediaPlayer.PlaybackState.StoppedState or has_src:
                    self._player.stop()
                    self._player.setSource(QUrl())
            except Exception:
                pass

        self.display_container.setCurrentWidget(self.canvas_widget)
        if self.resolved_file_path:
            self.btn_play.setText("▶ Phát video")
            self.btn_play.setEnabled(True)
            self.btn_replay.setEnabled(True)
            self.btn_play.setToolTip("Phát video preview")
        else:
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setEnabled(False)
            self.btn_replay.setEnabled(False)
            if self._last_guard_reason:
                self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            else:
                self.btn_play.setToolTip("Tài nguyên không khả dụng.")
        self.canvas_widget.update()

    def _on_player_error(self, error, error_string="", epoch: Optional[int] = None):
        """Xử lý lỗi giải mã video: dừng phát và hiển thị lỗi trung thực, không giả lập simulation."""
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            return
        self.stop_playback()
        self.display_container.setCurrentWidget(self.canvas_widget)
        err_msg = f"Lỗi giải mã: {error_string}" if error_string else "Lỗi phát video."
        self.btn_play.setText("⚠️ Lỗi phát video")
        self.btn_play.setToolTip(f"Không thể phát: {err_msg}")
        self.btn_play.setEnabled(False)
        self.btn_replay.setEnabled(False)
        self.lbl_time.setText("--:-- / --:--")
        self.canvas_widget.update()

    def _open_external(self):
        if not self._recheck_action_time_guard():
            return
        if self.resolved_file_path and os.path.isfile(self.resolved_file_path):
            abs_p = os.path.abspath(self.resolved_file_path)
            ext = os.path.splitext(abs_p)[1].lower()
            if ext in VIDEO_EXTS:
                try:
                    import sys
                    if sys.platform == "win32":
                        os.startfile(abs_p)
                    else:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(abs_p))
                except Exception:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(abs_p))

    def _on_canvas_clicked(self, event):
        if event.button() == Qt.LeftButton:
            if self.item_type != "icon":
                self._toggle_playback()
            elif self.resolved_file_path:
                self._open_external()

    def mousePressEvent(self, event):
        self._on_canvas_clicked(event)
        super().mousePressEvent(event)

    def _on_playback_state_changed(self, state, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            if state == QMediaPlayer.StoppedState:
                self.display_container.setCurrentWidget(self.canvas_widget)
                if self.resolved_file_path:
                    self.btn_play.setText("▶ Phát video")
                    self.btn_play.setToolTip("Phát video preview")
                else:
                    self.btn_play.setText("⚠️ Không khả dụng")
                    if self._last_guard_reason:
                        self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            return

        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("⏸ Tạm dừng")
            self.btn_play.setToolTip("Tạm dừng phát video")
        elif state == QMediaPlayer.PausedState:
            self.btn_play.setText("▶ Tiếp tục")
            self.btn_play.setToolTip("Tiếp tục phát video")
        else:
            self._is_active_playback = False
            self.btn_play.setText("▶ Phát lại")
            self.btn_play.setToolTip("Phát lại video từ đầu")
            self.display_container.setCurrentWidget(self.canvas_widget)

    def _on_position_changed(self, pos_ms: int, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            return
        self._position_ms = pos_ms
        self._update_time_label()

    def _on_duration_changed(self, dur_ms: int, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            return
        self._duration_ms = dur_ms
        self._update_time_label()

    def _update_time_label(self):
        p_sec = int(self._position_ms // 1000)
        d_sec = int(self._duration_ms // 1000)
        self.lbl_time.setText(f"{p_sec//60:02d}:{p_sec%60:02d} / {d_sec//60:02d}:{d_sec%60:02d}")

    def _paint_canvas(self, event):
        painter = QPainter(self.canvas_widget)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        w = self.canvas_widget.width()
        h = self.canvas_widget.height()

        clip_path = QPainterPath()
        clip_path.addRoundedRect(0, 0, w, h, 8, 8)
        painter.setClipPath(clip_path)

        if self.item_type == "icon":
            painter.fillRect(0, 0, w, h, QColor("#18181b"))
            sq = 12
            painter.setBrush(QBrush(QColor("#27272a")))
            painter.setPen(Qt.NoPen)
            for x in range(0, w, sq):
                for y in range(0, h, sq):
                    if (x // sq + y // sq) % 2 == 0:
                        painter.drawRect(x, y, sq, sq)

            painter.setFont(QFont("Segoe UI Emoji", 36))
            painter.setPen(QColor(self.color_hex))
            painter.drawText(QRectF(0, 6, w, h - 30), Qt.AlignCenter, self.badge_icon)

            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
            painter.drawRoundedRect(QRectF(8, 8, 48, 18), 4, 4)
            painter.setPen(QPen(QColor(self.color_hex)))
            painter.drawText(QRectF(8, 8, 48, 18), Qt.AlignCenter, "STICKER")
        else:
            # Video Meme, Overlay hoặc Transition Demo
            if self._pixmap and not self._pixmap.isNull():
                scaled_pix = self._pixmap.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                px = (w - scaled_pix.width()) // 2
                py = (h - scaled_pix.height()) // 2
                painter.drawPixmap(px, py, scaled_pix)
                painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 70))

                # Nút Play ở giữa
                painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
                painter.setPen(QPen(QColor(ThemeColors.CYAN_HI) if self.resolved_file_path else QColor("#ef4444"), 1.5))
                play_btn_rect = QRectF((w - 110) / 2, (h - 28) / 2, 110, 28)
                painter.drawRoundedRect(play_btn_rect, 14, 14)

                painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
                painter.setPen(QColor("#ffffff"))
                btn_txt = "▶ Xem video" if self.resolved_file_path else "⚠️ Không khả dụng"
                painter.drawText(play_btn_rect, Qt.AlignCenter, btn_txt)
            else:
                grad = QLinearGradient(0, 0, w, h)
                grad.setColorAt(0.0, QColor("#1e1b4b"))
                grad.setColorAt(1.0, QColor("#09090b"))
                painter.fillRect(0, 0, w, h, grad)

                painter.setFont(QFont("Segoe UI Emoji", 34))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(0, 6, w, h - 34), Qt.AlignCenter, self.badge_icon)

                # Nút Play hoặc cảnh báo ở giữa
                painter.setBrush(QBrush(QColor(0, 0, 0, 160)))
                painter.setPen(QPen(QColor(ThemeColors.CYAN_HI) if self.resolved_file_path else QColor("#ef4444"), 1.5))
                play_btn_rect = QRectF((w - 120) / 2, (h - 26) / 2 + 10, 120, 26)
                painter.drawRoundedRect(play_btn_rect, 13, 13)

                painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
                painter.setPen(QColor("#ffffff"))
                btn_txt = "▶ Xem video" if self.resolved_file_path else "⚠️ Không khả dụng"
                painter.drawText(play_btn_rect, Qt.AlignCenter, btn_txt)

            # Badge định dạng video
            tag = "OVERLAY" if self.item_type == "overlay" else "TRANS" if self.item_type == "trans" else "▶ MP4"
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(6, 182, 212, 200)))
            painter.drawRoundedRect(QRectF(w - 64, 8, 56, 18), 4, 4)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(QRectF(w - 64, 8, 56, 18), Qt.AlignCenter, tag)

        # Thanh tiêu đề dưới đáy
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 190)))
        painter.drawRoundedRect(QRectF(8, h - 26, w - 16, 20), 4, 4)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(QRectF(14, h - 26, w - 28, 20), Qt.AlignVCenter | Qt.AlignLeft, self.item_name)


def format_text_preset_html(
    preset_id: str,
    preset_name: str = "Title",
    sample_text: str = "",
    standard_color: str = "#FFFFFF",
    is_large: bool = False,
    font_family: str = "Arial",
    font_size_pt: int = 18,
    highlight_color: str = "#FACC15",
    stroke_color: str = "#000000",
    stroke_width: float = 0.15,
    box_color: Optional[str] = None,
    glow_color: Optional[str] = None,
    weight: str = "bold",
    animation: Optional[str] = None
) -> str:
    """Format rich HTML cho mẫu chữ trong card và inspector với tham số động."""
    pid = (preset_id or "").lower()
    text = sample_text.strip() if sample_text.strip() else ""

    font_base = f"{font_size_pt}px" if is_large else "13px"
    font_hl = f"{int(font_size_pt * 1.2)}px" if is_large else "15px"
    css_weight = "900" if weight in ("black", "extra_bold") else ("bold" if weight == "bold" else "normal")
    f_family = f"'{font_family}', Arial, sans-serif"

    hl_col = highlight_color if highlight_color else "#facc15"
    std_col = standard_color if standard_color else "#ffffff"
    b_col = box_color if box_color else "#ef4444"
    g_col = glow_color if glow_color else "#06b6d4"

    if pid in ("kinetic_hormozi", "bounce_word") or "hormozi" in pid:
        display = text if text else "TIỀN ĐẾN<br>TỪ <span style=\"color: #facc15;\">ĐÂU?</span>"
        if text and "<span" not in text:
            words = text.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"color: {hl_col}; font-size: {font_hl};\">{words[-1]}</span>"
            else:
                display = f"<span style=\"color: {hl_col};\">{text}</span>"
        return (
            f"<div style=\"font-family: {f_family}; font-weight: {css_weight}; font-size: {font_base}; text-transform: uppercase; color: {std_col}; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid == "karaoke_pop":
        display = text if text else "dính <span style=\"color: #a3e635;\">mưa</span> rồi"
        if text and "<span" not in text:
            words = text.split()
            k_col = highlight_color if (highlight_color and highlight_color != "#FACC15") else "#a3e635"
            if len(words) > 1:
                mid = len(words) // 2
                display = " ".join(words[:mid]) + f" <span style=\"color: {k_col}; font-size: {font_hl}; font-weight: 900;\">{words[mid]}</span> " + " ".join(words[mid+1:])
            else:
                display = f"<span style=\"color: {k_col};\">{text}</span>"
        return (
            f"<div style=\"font-family: {f_family}; font-weight: {css_weight}; font-size: {font_base}; color: {std_col}; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid in ("box_highlight", "highlighter_swipe") or "highlighter" in pid:
        display = text if text else f"100% <span style=\"background-color: {b_col}; color: #ffffff; padding: 2px 6px; border-radius: 4px; font-weight: 900;\">sức khỏe</span>"
        if text and "<span" not in text:
            words = text.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"background-color: {b_col}; color: #ffffff; padding: 2px 6px; border-radius: 4px; font-weight: 900;\">{words[-1]}</span>"
            else:
                display = f"<span style=\"background-color: {b_col}; color: #ffffff; padding: 2px 6px; border-radius: 4px;\">{text}</span>"
        return (
            f"<div style=\"font-family: {f_family}; font-weight: {css_weight}; font-size: {font_base}; color: {std_col}; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid in ("glow_neon", "neon_pulse") or "neon" in pid:
        display = text if text else "NEON"
        return (
            f"<div style=\"font-family: {f_family}; font-weight: {css_weight}; font-size: {font_base}; text-align: center;\">"
            f"<span style=\"color: #ecfeff; border: 1px solid {g_col}; background-color: rgba(6, 182, 212, 0.2); padding: 4px 12px; border-radius: 6px; text-shadow: 0 0 10px {g_col};\">{display}</span>"
            "</div>"
        )
    elif pid in ("vhs_retro", "typewriter") or "typewriter" in pid:
        display = text if text else "HÈ 1999"
        sub_play = "▶ PLAY SP 0:12"
        return (
            f"<div style=\"font-family: Consolas, monospace; font-size: 11px; color: #f5f5f4; text-align: center;\">"
            f"<div style=\"color: #22c55e; font-size: 10px; margin-bottom: 3px;\">{sub_play}</div>"
            f"<div style=\"font-size: {font_base}; font-weight: bold; letter-spacing: 2px; color: #fff59d;\">{display}</div>"
            "</div>"
        )
    elif pid in ("paper_cutout",) or "paper" in pid:
        display = text if text else "VLOG"
        spans = "".join(f"<span style=\"background-color: #ffffff; color: #111111; padding: 2px 5px; margin: 1px; border-radius: 2px; box-shadow: 1px 1px 2px rgba(0,0,0,0.5);\">{c}</span>" for c in display[:8])
        return (
            f"<div style=\"font-family: Georgia, serif; font-weight: bold; font-size: {font_base}; color: #111111; text-align: center;\">"
            f"{spans}"
            "</div>"
        )
    elif pid in ("gradient_fill",):
        display = text if text else "HOÀNG HÔN SUNSET"
        return (
            f"<div style=\"font-family: 'Arial Black', sans-serif; font-weight: 900; font-size: {font_base}; text-align: center;\">"
            f"<span style=\"color: #fb923c;\">{display}</span>"
            "</div>"
        )
    elif pid in ("rgb_glitch",) or "glitch" in pid:
        display = text if text else "GLITCH CYBER"
        return (
            f"<div style=\"font-family: {f_family}; font-weight: 900; font-size: {font_base}; color: {std_col}; text-align: center; text-shadow: -2px 0 #ff0055, 2px 0 #00e5ff;\">"
            f"{display}"
            "</div>"
        )
    else:
        display = text if text else preset_name.split("(")[0].strip()
        anim = (animation or "").lower()
        if anim in ("pop", "bounce") or "spring" in pid:
            words = display.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"color: {hl_col}; font-size: {font_hl};\">{words[-1]}</span>"
            else:
                display = f"<span style=\"color: {hl_col};\">{display}</span>"
        elif anim == "glow" or "glow" in pid:
            display = f"<span style=\"color: #ecfeff; text-shadow: 0 0 10px {g_col};\">{display}</span>"
        elif anim in ("box", "box_highlight") or "highlight" in pid:
            words = display.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"background-color: {b_col}; color: #ffffff; padding: 2px 6px; border-radius: 4px;\">{words[-1]}</span>"
            else:
                display = f"<span style=\"background-color: {b_col}; color: #ffffff; padding: 2px 6px; border-radius: 4px;\">{display}</span>"

        return (
            f"<div style=\"font-family: {f_family}; font-weight: {css_weight}; font-size: {font_base}; color: {std_col}; text-align: center;\">"
            f"{display}"
            "</div>"
        )


class AudioInspectorWidget(QWidget):
    """
    Widget hiển thị và nghe thử âm thanh SFX / BGM thực tế trong Inspector.
    Tích hợp ActionGuard, AudioDeviceRouter, và bảo vệ session token chống stale callbacks.
    """
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._current_asset: Any = None
        self._playback_epoch: int = 0
        self._is_active_playback: bool = False
        self._player_conns: list = []
        self._last_guard_reason: str = ""
        self._stop_hover_callback: Optional[Callable[[], None]] = None

        self._volume: float = self._load_initial_volume()
        self.resolved_file_path: str = ""
        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._bundled_root = None
        self._session_loading: bool = False
        self._session_has_error: bool = False

        self._init_ui()

    def _load_initial_volume(self) -> float:
        """Đọc âm lượng nghe thử từ QSettings với fallback an toàn chống crash."""
        try:
            settings = QSettings("ChunDVC", "ResolveFlow")
            raw_vol = settings.value("library/inspector_audio_volume", 0.85)
            if raw_vol is None or isinstance(raw_vol, bool):
                return 0.85
            val = float(raw_vol)
            if math.isnan(val) or math.isinf(val):
                return 0.85
            return max(0.0, min(1.0, val))
        except Exception:
            return 0.85

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, val: float):
        """Thiết lập âm lượng nghe thử (clamp [0.0, 1.0]), lưu vào QSettings và áp vào QAudioOutput."""
        try:
            if val is None or isinstance(val, bool):
                val = 0.85
            v = float(val)
            if math.isnan(v) or math.isinf(v):
                v = 0.85
            self._volume = max(0.0, min(1.0, v))
        except Exception:
            self._volume = 0.85

        try:
            settings = QSettings("ChunDVC", "ResolveFlow")
            settings.setValue("library/inspector_audio_volume", self._volume)
        except Exception:
            pass

        if self._audio_output is not None:
            try:
                self._audio_output.setVolume(self._volume)
            except Exception:
                pass

    def _init_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(6)

        h_ctrl = QHBoxLayout()
        h_ctrl.setContentsMargins(0, 0, 0, 0)
        h_ctrl.setSpacing(8)

        self.btn_play = QPushButton("▶ Nghe thử", self)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setFixedHeight(26)
        self.btn_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.CYAN};
                border-radius: 6px;
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.CYAN};
                color: #05252c;
            }}
            QPushButton:disabled {{
                background-color: rgba(255, 255, 255, 0.04);
                border-color: rgba(255, 255, 255, 0.1);
                color: rgba(255, 255, 255, 0.3);
            }}
        """)
        self.btn_play.clicked.connect(self.toggle_playback)
        h_ctrl.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00 / 00:00", self)
        self.lbl_time.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_PRIMARY};")
        h_ctrl.addWidget(self.lbl_time)

        h_ctrl.addStretch()

        self.lbl_hint = QLabel("", self)
        self.lbl_hint.setStyleSheet(f"font-size: 11px; color: {ThemeColors.TEXT_MUTED};")
        h_ctrl.addWidget(self.lbl_hint)

        vbox.addLayout(h_ctrl)

        self.waveform_canvas = WaveformCanvas(preset_id="sfx", num_bars=32, height=56, parent=self)
        self.waveform_canvas.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 8px;
            padding: 4px;
        """)
        vbox.addWidget(self.waveform_canvas)

        # Aliases phục vụ tương thích với callers/tests cũ
        self.sfx_play_btn = self.btn_play
        self.sfx_time_lbl = self.lbl_time
        self.sfx_hint_lbl = self.lbl_hint
        self.sfx_waveform_canvas = self.waveform_canvas

    def set_stop_hover_callback(self, cb: Optional[Callable[[], None]]) -> None:
        """Thiết lập callback để dừng hover preview của grid khi Inspector bắt đầu phát."""
        self._stop_hover_callback = cb

    def _ensure_player(self):
        if self._player is not None:
            return
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        from src.services.audio_router import get_audio_router
        get_audio_router().register(self._audio_output)
        self._audio_output.setVolume(self._volume)
        self._player.setAudioOutput(self._audio_output)

    def _disconnect_session(self):
        """Ngắt toàn bộ kết nối signal của session trước mà widget sở hữu."""
        if self._player is not None and self._player_conns:
            for conn in list(self._player_conns):
                try:
                    if hasattr(QObject, "disconnect") and isinstance(conn, QMetaObject.Connection):
                        QObject.disconnect(conn)
                    elif hasattr(self._player, "disconnect"):
                        self._player.disconnect(conn)
                except Exception:
                    pass
            self._player_conns.clear()

    @staticmethod
    def _safe_parse_duration_ms(raw: Any) -> int:
        if raw is None or isinstance(raw, bool):
            return 0
        try:
            val = float(raw)
            if math.isnan(val) or math.isinf(val) or val <= 0:
                return 0
            clamped_sec = min(86400.0, val)
            return int(clamped_sec * 1000)
        except (ValueError, TypeError, OverflowError):
            return 0

    def set_asset(self, asset: Any):
        """Nạp tài nguyên âm thanh vào Inspector với đầy đủ thẩm định ActionGuard (0 autoplay)."""
        self.stop_playback()
        self._current_asset = asset
        self._position_ms = 0
        self._duration_ms = 0
        self._last_guard_reason = ""
        self._session_has_error = False

        # Trích xuất metadata an toàn tuyệt đối
        if isinstance(asset, Mapping):
            pid = str(asset.get("id") or asset.get("name") or "sfx")
            raw_dur = asset.get("duration")
            real_file = str(asset.get("file_path") or "")
        else:
            pid = str(getattr(asset, "id", "") or getattr(asset, "name", "") or "sfx")
            raw_dur = getattr(asset, "duration", None)
            real_file = str(getattr(asset, "file_path", "") or "")

        self._duration_ms = self._safe_parse_duration_ms(raw_dur)

        self.waveform_canvas.set_preset_id(pid, num_bars=32)
        self.waveform_canvas.set_playback_progress(0, self._duration_ms)
        self.lbl_hint.setText(os.path.basename(real_file) if real_file else f"{pid}.wav")

        payload = _normalize_preview_payload(asset)
        guard_res = check_action_availability(payload, intent="preview", bundled_root=getattr(self, "_bundled_root", None))

        if guard_res.allowed and guard_res.resolved_path:
            ext = os.path.splitext(guard_res.resolved_path)[1].lower()
            if ext in AUDIO_EXTS:
                self.resolved_file_path = guard_res.resolved_path
                self.btn_play.setEnabled(True)
                self.btn_play.setText("▶ Nghe thử")
                self.btn_play.setToolTip("Nghe thử âm thanh")
                self._update_time_label()
            else:
                self.resolved_file_path = ""
                self.btn_play.setEnabled(False)
                self._last_guard_reason = "Định dạng tệp không được hỗ trợ để phát âm thanh."
                self.btn_play.setText("⚠️ Không khả dụng")
                self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
                self.lbl_time.setText("--:-- / --:--")
        else:
            self.resolved_file_path = ""
            self.btn_play.setEnabled(False)
            self._last_guard_reason = guard_res.reason or "Tài nguyên không khả dụng."
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")

    def _recheck_action_time_guard(self) -> bool:
        """Thẩm định lại quyền hạn nguồn tại thời điểm bấm nút."""
        if self._current_asset is None:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = "Chưa chọn tài nguyên."
            self.btn_play.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        payload = _normalize_preview_payload(self._current_asset)
        guard_res = check_action_availability(payload, intent="preview", bundled_root=getattr(self, "_bundled_root", None))
        if not guard_res.allowed or not guard_res.resolved_path:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = guard_res.reason or "Tài nguyên không khả dụng."
            self.btn_play.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        ext = os.path.splitext(guard_res.resolved_path)[1].lower()
        if ext not in AUDIO_EXTS:
            self.resolved_file_path = ""
            self.stop_playback()
            self._last_guard_reason = "Định dạng tệp không được hỗ trợ để phát âm thanh."
            self.btn_play.setEnabled(False)
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            self.lbl_time.setText("--:-- / --:--")
            return False

        self.resolved_file_path = guard_res.resolved_path
        return True

    def _start_session(self, reset_pos: bool = False):
        """Khởi tạo phiên phát mới có gắn epoch/token độc quyền, loại trừ callback cũ."""
        if self._stop_hover_callback is not None:
            try:
                self._stop_hover_callback()
            except Exception:
                pass

        self._disconnect_session()
        self._playback_epoch += 1
        epoch = self._playback_epoch
        self._is_active_playback = True
        self._session_loading = True
        self._session_has_error = False

        def on_pos(pos: int):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_position_changed(pos, epoch=epoch)

        def on_dur(dur: int):
            if epoch != self._playback_epoch or not self._is_active_playback:
                return
            self._on_duration_changed(dur, epoch=epoch)

        def on_state(state):
            if epoch != self._playback_epoch:
                return
            if self._session_loading and state == QMediaPlayer.StoppedState:
                return
            self._on_playback_state_changed(state, epoch=epoch)

        def on_err(err, err_str=""):
            if epoch != self._playback_epoch:
                return
            self._session_has_error = True
            self._on_player_error(err, err_str, epoch=epoch)

        def on_status(status):
            if epoch != self._playback_epoch:
                return
            self._on_media_status_changed(status, epoch=epoch)

        if hasattr(self._player, "positionChanged"):
            c1 = self._player.positionChanged.connect(on_pos)
            self._player_conns.append(c1)
        if hasattr(self._player, "durationChanged"):
            c2 = self._player.durationChanged.connect(on_dur)
            self._player_conns.append(c2)
        if hasattr(self._player, "playbackStateChanged"):
            c3 = self._player.playbackStateChanged.connect(on_state)
            self._player_conns.append(c3)
        if hasattr(self._player, "errorOccurred"):
            c4 = self._player.errorOccurred.connect(on_err)
            self._player_conns.append(c4)
        if hasattr(self._player, "mediaStatusChanged"):
            c5 = self._player.mediaStatusChanged.connect(on_status)
            self._player_conns.append(c5)

        source = QUrl.fromLocalFile(self.resolved_file_path)
        if self._player.source() != source:
            self._player.setSource(source)

        self._session_loading = False

        if self._session_has_error:
            return

        if reset_pos:
            self._player.setPosition(0)
            self._position_ms = 0
            self._update_time_label()

        self._player.play()

        # Cập nhật UI strictly theo actual state của player (không ép playing khi chưa nhận PlayingState)
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._on_playback_state_changed(QMediaPlayer.PlayingState, epoch=epoch)

    def toggle_playback(self):
        if self.resolved_file_path:
            if not self._recheck_action_time_guard():
                return
            self._ensure_player()
            if self._player.playbackState() == QMediaPlayer.PlayingState:
                self._player.pause()
                self.btn_play.setText("▶ Tiếp tục")
                self.btn_play.setToolTip("Tiếp tục nghe thử")
                self.waveform_canvas.is_playing = False
                self.waveform_canvas.update()
            else:
                self._start_session(reset_pos=False)
        else:
            self.stop_playback()

    def start_playback(self):
        if self.resolved_file_path:
            if not self._recheck_action_time_guard():
                return
            self._ensure_player()
            self._start_session(reset_pos=True)
        else:
            self.stop_playback()

    def stop_playback(self):
        """Dừng phát âm thanh an toàn: tăng epoch trước, dọn connections và clear player source."""
        was_active = self._is_active_playback
        self._playback_epoch += 1
        self._is_active_playback = False
        self._session_has_error = False
        self._session_loading = False
        self._disconnect_session()

        if self._player is not None:
            try:
                src_fn = getattr(self._player, "source", None)
                cur_src = src_fn() if callable(src_fn) else None
                state_fn = getattr(self._player, "playbackState", None)
                cur_state = state_fn() if callable(state_fn) else None
                has_src = cur_src is None or not hasattr(cur_src, "isEmpty") or not cur_src.isEmpty()
                if was_active or cur_state != QMediaPlayer.PlaybackState.StoppedState or has_src:
                    self._player.stop()
                    self._player.setSource(QUrl())
            except Exception:
                pass

        self._position_ms = 0
        self.waveform_canvas.is_playing = False
        self.waveform_canvas.set_playback_progress(0, self._duration_ms)

        if self.resolved_file_path:
            self.btn_play.setText("▶ Nghe thử")
            self.btn_play.setEnabled(True)
            self.btn_play.setToolTip("Nghe thử âm thanh")
            self._update_time_label()
        else:
            self.btn_play.setText("⚠️ Không khả dụng")
            self.btn_play.setEnabled(False)
            if self._last_guard_reason:
                self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")
            else:
                self.btn_play.setToolTip("Tài nguyên không khả dụng.")
            self.lbl_time.setText("--:-- / --:--")

    def _on_playback_state_changed(self, state, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if self._session_has_error:
            return

        if state == QMediaPlayer.PlayingState:
            self._is_active_playback = True
            self.btn_play.setText("⏸ Dừng")
            self.btn_play.setToolTip("Dừng nghe thử âm thanh")
            self.waveform_canvas.is_playing = True
            self.waveform_canvas.update()
        elif state == QMediaPlayer.PausedState:
            self.btn_play.setText("▶ Tiếp tục")
            self.btn_play.setToolTip("Tiếp tục nghe thử")
            self.waveform_canvas.is_playing = False
            self.waveform_canvas.update()
        elif state == QMediaPlayer.StoppedState:
            self.waveform_canvas.is_playing = False
            self.waveform_canvas.update()
            if self._is_active_playback:
                self._is_active_playback = False
                self._position_ms = 0
                self._update_time_label()
                self.waveform_canvas.set_playback_progress(0, self._duration_ms)
            if self.resolved_file_path:
                self.btn_play.setText("▶ Nghe thử")
                self.btn_play.setEnabled(True)
                self.btn_play.setToolTip("Nghe thử âm thanh")
            else:
                self.btn_play.setText("⚠️ Không khả dụng")
                self.btn_play.setEnabled(False)
                if self._last_guard_reason:
                    self.btn_play.setToolTip(f"Không khả dụng: {self._last_guard_reason}")

    def _on_media_status_changed(self, status, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop_playback()

    def _on_player_error(self, error, error_string="", epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        self._session_has_error = True
        self._is_active_playback = False
        self.waveform_canvas.is_playing = False
        self.waveform_canvas.update()
        self._position_ms = 0
        err_msg = f"Lỗi giải mã: {error_string}" if error_string else "Lỗi phát âm thanh."
        self.btn_play.setText("⚠️ Lỗi phát âm thanh")
        self.btn_play.setToolTip(f"Không thể phát: {err_msg}")
        self.btn_play.setEnabled(False)
        self.lbl_time.setText("--:-- / --:--")

    def _on_position_changed(self, pos_ms: int, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            return
        self._position_ms = pos_ms
        self._update_time_label()
        self.waveform_canvas.set_playback_progress(self._position_ms, self._duration_ms)

    def _on_duration_changed(self, dur_ms: int, epoch: Optional[int] = None):
        if epoch is not None and epoch != self._playback_epoch:
            return
        if not self._is_active_playback:
            return
        if dur_ms > 0:
            self._duration_ms = dur_ms
            self._update_time_label()
            self.waveform_canvas.set_playback_progress(self._position_ms, self._duration_ms)

    def _update_time_label(self):
        if not self.resolved_file_path:
            self.lbl_time.setText("--:-- / --:--")
            return
        p_sec = max(0, int(self._position_ms // 1000))
        if self._duration_ms > 0:
            d_sec = max(0, int(self._duration_ms // 1000))
            self.lbl_time.setText(f"{p_sec//60:02d}:{p_sec%60:02d} / {d_sec//60:02d}:{d_sec%60:02d}")
        else:
            self.lbl_time.setText(f"{p_sec//60:02d}:{p_sec%60:02d} / --:--")

    def hideEvent(self, event):
        self.stop_playback()
        if event is not None:
            super().hideEvent(event)

    def closeEvent(self, event):
        self.stop_playback()
        if event is not None:
            super().closeEvent(event)


class AssetCard(QFrame):
    selected_signal = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)
    add_requested = pyqtSignal(object)
    quick_play_requested = pyqtSignal(str)

    # Class-level shared player to coordinate instant hover playback without audio overlap
    _shared_player: Optional[QMediaPlayer] = None
    _shared_audio_output: Optional[QAudioOutput] = None
    _active_playing_card: Optional['AssetCard'] = None
    enable_hover_preview: bool = True

    def __init__(self, preset: Any, is_fav: bool = False, sample_text_func=None, parent=None):
        super().__init__(parent)
        if parent is None:
            self.setAttribute(Qt.WA_DontShowOnScreen, True)
            self.hide()
        self.preset = preset
        self.is_fav = is_fav
        self.sample_text_func = sample_text_func
        self.is_selected = False
        self._drag_start_pos = None
        self._is_playing_audio = False
        self._player = None
        self._audio_output = None

        self.setFixedHeight(148)
        self.setMinimumWidth(125)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        self._init_ui()

    def set_card_scale(self, cols: int):
        """Co giãn kích thước thẻ linh hoạt theo số cột lưới (Zoom)."""
        if cols >= 4:
            self.setMinimumWidth(100)
            self.setFixedHeight(126)
            if hasattr(self, "thumb"):
                self.thumb.setFixedHeight(66)
        elif cols <= 2:
            self.setMinimumWidth(160)
            self.setFixedHeight(175)
            if hasattr(self, "thumb"):
                self.thumb.setFixedHeight(105)
        else:
            self.setMinimumWidth(125)
            self.setFixedHeight(148)
            if hasattr(self, "thumb"):
                self.thumb.setFixedHeight(80)

    def _apply_style(self):
        if getattr(self, "_is_playing_audio", False):
            border_col = "#06b6d4"
            bg_col = "rgba(6, 182, 212, 0.12)"
        elif self.is_selected:
            border_col = ThemeColors.PRIMARY
            bg_col = ThemeColors.BG_CARD_ACTIVE
        else:
            border_col = ThemeColors.BORDER_DEFAULT
            bg_col = ThemeColors.BG_CARD
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 10px;
            }}
            QFrame:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
            }}
        """)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. Khung Thumbnail xem trước
        self.thumb = QFrame()
        self.thumb.setFixedHeight(80)
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)

        # Điều phối tới bộ render chuyên biệt theo từng loại đạo cụ
        thumb_path = getattr(self.preset, "thumbnail_path", "")
        if thumb_path and os.path.isfile(thumb_path):
            self._render_image_thumbnail(thumb_path)
        else:
            file_path = getattr(self.preset, "file_path", "")
            if file_path and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ('.mp4', '.mov', '.webm', '.mkv', '.avi', '.gif', '.png', '.jpg', '.jpeg', '.webp'):
                    from src.core.asset_cache import request_thumbnail_async
                    request_thumbnail_async(file_path, self._on_async_thumb_ready)

            if isinstance(self.preset, TextStylePreset) or getattr(self.preset, "tab", "") == "text":
                self._render_text_thumbnail()
            elif getattr(self.preset, "tab", "") == "bgm":
                self._render_bgm_thumbnail()
            elif getattr(self.preset, "tab", "") == "sfx":
                self._render_sfx_thumbnail()
            elif getattr(self.preset, "tab", "") == "lut":
                self._render_lut_thumbnail()
            elif isinstance(self.preset, TransitionStylePreset) or getattr(self.preset, "tab", "") == "trans":
                thumb_p = getattr(self.preset, "thumbnail_path", "")
                prev_p = getattr(self.preset, "preview_path", "")
                f_p = getattr(self.preset, "file_path", "")
                has_visual = False
                if thumb_p and os.path.isfile(thumb_p) and not thumb_p.endswith((".setting", ".drfx")):
                    if thumb_p.lower().endswith(('.mp4', '.mov', '.webm')):
                        from src.core.asset_cache import request_thumbnail_async
                        request_thumbnail_async(thumb_p, self._on_async_thumb_ready)
                    else:
                        self._render_image_thumbnail(thumb_p)
                    has_visual = True
                elif prev_p and os.path.isfile(prev_p) and prev_p.lower().endswith(('.mp4', '.mov', '.webm', '.png', '.jpg', '.jpeg', '.webp')):
                    from src.core.asset_cache import request_thumbnail_async
                    request_thumbnail_async(prev_p, self._on_async_thumb_ready)
                    has_visual = True
                elif f_p and os.path.isfile(f_p) and f_p.lower().endswith(('.mp4', '.mov', '.webm')):
                    from src.core.asset_cache import request_thumbnail_async
                    request_thumbnail_async(f_p, self._on_async_thumb_ready)
                    has_visual = True

                if not has_visual:
                    self._render_transition_thumbnail()
            elif getattr(self.preset, "tab", "") in ("icon", "meme", "overlay") or hasattr(self.preset, "badge_icon"):
                self._render_icon_thumbnail()
            else:
                self._render_icon_thumbnail()

        layout.addWidget(self.thumb)

        # 2. Hàng thông tin tên + nút tim
        h_info = QHBoxLayout()
        h_info.setContentsMargins(2, 0, 2, 0)
        h_info.setSpacing(4)

        v_name = QVBoxLayout()
        v_name.setSpacing(0)
        name_clean = getattr(self.preset, "name", "").split("(")[0].strip()
        lbl_nm = QLabel(f"<b>{name_clean}</b>")
        lbl_nm.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; font-size: 12px;")
        if len(name_clean) > 16:
            lbl_nm.setText(f"<b>{name_clean[:14]}..</b>")
            lbl_nm.setToolTip(name_clean)

        sub_txt = getattr(self.preset, "sub", "") or getattr(self.preset, "category", "")
        lbl_sub = QLabel(sub_txt)
        lbl_sub.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10px;")

        v_name.addWidget(lbl_nm)
        v_name.addWidget(lbl_sub)
        h_info.addLayout(v_name, stretch=1)

        # Nút tim (Yêu thích)
        self.btn_fav = QPushButton("❤️" if self.is_fav else "🤍")
        self.btn_fav.setFixedSize(22, 22)
        self.btn_fav.setCursor(Qt.PointingHandCursor)
        self.btn_fav.setStyleSheet("background: transparent; border: none; font-size: 12px;")
        self.btn_fav.clicked.connect(self._toggle_fav)
        h_info.addWidget(self.btn_fav)

        # Nút Thêm nhanh (CapCut Quick Add)
        self.btn_quick_add = QPushButton("➕")
        self.btn_quick_add.setFixedSize(22, 22)
        self.btn_quick_add.setCursor(Qt.PointingHandCursor)
        self.btn_quick_add.setToolTip("Chèn vào DaVinci Resolve tại Playhead (hoặc nhấp đúp thẻ)")
        self.btn_quick_add.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_MAIN};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 4px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.PRIMARY};
                color: #ffffff;
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        self.btn_quick_add.clicked.connect(self._on_quick_add_clicked)
        h_info.addWidget(self.btn_quick_add)

        layout.addLayout(h_info)

    def _on_async_thumb_ready(self, cached_path: str):
        if not cached_path or not os.path.isfile(cached_path):
            return
        setattr(self.preset, "thumbnail_path", cached_path)
        self._render_image_thumbnail(cached_path)

    def _render_image_thumbnail(self, thumb_path: str):
        if not hasattr(self, "thumb") or self.thumb is None:
            return
        layout = self.thumb.layout()
        if layout is not None:
            while layout.count():
                child = layout.takeAt(0)
                w = child.widget()
                if w:
                    w.deleteLater()
        else:
            layout = QVBoxLayout(self.thumb)
            layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel()
        src_pixmap = QPixmap(thumb_path)
        target_w, target_h = 160, 80

        target_pixmap = QPixmap(target_w, target_h)
        target_pixmap.fill(Qt.transparent)

        painter = QPainter(target_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        path = QPainterPath()
        path.addRoundedRect(0, 0, target_w, target_h, 8, 8)
        painter.setClipPath(path)

        scaled_src = src_pixmap.scaled(target_w, target_h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (target_w - scaled_src.width()) // 2
        y = (target_h - scaled_src.height()) // 2

        painter.drawPixmap(x, y, scaled_src)
        painter.end()

        lbl.setPixmap(target_pixmap)
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)

        tab_type = getattr(self.preset, "tab", "")
        if tab_type in ("meme", "overlay", "trans"):
            overlay_container = QWidget(lbl)
            overlay_container.setGeometry(4, 4, 152, 26)
            h_over = QHBoxLayout(overlay_container)
            h_over.setContentsMargins(0, 0, 0, 0)
            h_over.setSpacing(4)
            self.btn_quick_play = QPushButton("▶", overlay_container)
            self.btn_quick_play.setFixedSize(22, 22)
            self.btn_quick_play.setCursor(Qt.PointingHandCursor)
            self.btn_quick_play.setToolTip("Xem live preview")
            self.btn_quick_play.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(9, 9, 11, 0.75);
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 11px;
                    color: {ThemeColors.CYAN_HI};
                    font-size: 10px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {ThemeColors.CYAN};
                    color: #05252c;
                    border-color: {ThemeColors.CYAN_HI};
                }}
            """)
            self.btn_quick_play.clicked.connect(self._on_quick_play_meme_clicked)
            h_over.addWidget(self.btn_quick_play)
            if tab_type == "meme":
                tag_label = "🎬 MEME"
            elif tab_type == "overlay":
                tag_label = "✨ OVERLAY"
            else:
                tag_label = "⚡ TRANS"
            lbl_tag = QLabel(tag_label, overlay_container)
            lbl_tag.setStyleSheet("color: #FFFFFF; background-color: rgba(0,0,0,0.6); border-radius: 4px; padding: 2px 4px; font-size: 9px; font-weight: bold;")
            h_over.addWidget(lbl_tag)
            h_over.addStretch()

        self.thumb.update()

    def _render_text_thumbnail(self):
        """Render xem trước phong cách chữ nghệ thuật (Alex Hormozi, Karaoke, Box, Neon, VHS, Paper, Gradient)."""
        pid = getattr(self.preset, "id", "")
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 4, 6, 4)
        th_layout.setAlignment(Qt.AlignCenter)

        self.lbl_text_preview = QLabel(self.thumb)
        self.lbl_text_preview.setTextFormat(Qt.RichText)
        self.lbl_text_preview.setAlignment(Qt.AlignCenter)
        self.lbl_text_preview.setWordWrap(True)

        if pid == "kinetic_hormozi":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_MAIN};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: 'Arial Black', Impact, sans-serif; font-weight: 900; font-size: 13px; text-transform: uppercase; color: #ffffff; text-align: center;\">"
                "TIỀN ĐẾN<br>TỪ <span style=\"color: #facc15; font-size: 15px;\">ĐÂU?</span>"
                "</div>"
            )
        elif pid == "karaoke_pop":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_MAIN};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: 13px; color: #ffffff; text-align: center;\">"
                "dính <span style=\"color: #a3e635; font-size: 15px; font-weight: 900;\">mưa</span> rồi"
                "</div>"
            )
        elif pid == "box_highlight":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_MAIN};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: 13px; color: #ffffff; text-align: center;\">"
                "100% <span style=\"background-color: #ef4444; color: #ffffff; padding: 2px 6px; border-radius: 4px; font-weight: 900;\">sức khỏe</span>"
                "</div>"
            )
        elif pid == "glow_neon":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #0b2236;
                    border: 1px solid {ThemeColors.CYAN};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: 15px; text-align: center;\">"
                "<span style=\"color: #ecfeff; border: 1px solid #06b6d4; background-color: rgba(6, 182, 212, 0.2); padding: 3px 10px; border-radius: 6px;\">NEON</span>"
                "</div>"
            )
        elif pid == "vhs_retro":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #1b1714;
                    border: 1px solid #3a302a;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Consolas, monospace; font-size: 10px; color: #f5f5f4; text-align: center;\">"
                "<div style=\"color: #22c55e; font-size: 9px; margin-bottom: 2px;\">▶ PLAY SP 0:12</div>"
                "<div style=\"font-size: 14px; font-weight: bold; letter-spacing: 2px; color: #fff59d;\">HÈ 1999</div>"
                "</div>"
            )
        elif pid == "paper_cutout":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #cbb48a;
                    border: 1px solid #a98d5f;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Georgia, serif; font-weight: bold; font-size: 13px; color: #111111; text-align: center;\">"
                "<span style=\"background-color: #ffffff; color: #111111; padding: 2px 4px; margin: 1px; border-radius: 2px;\">V</span>"
                "<span style=\"background-color: #fef3c7; color: #111111; padding: 2px 4px; margin: 1px; border-radius: 2px;\">L</span>"
                "<span style=\"background-color: #111111; color: #ffffff; padding: 2px 4px; margin: 1px; border-radius: 2px;\">O</span>"
                "<span style=\"background-color: #fecaca; color: #111111; padding: 2px 4px; margin: 1px; border-radius: 2px;\">G</span>"
                "</div>"
            )
        elif pid == "gradient_fill":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(251,146,60,0.15), stop:0.5 rgba(244,63,94,0.15), stop:1 rgba(168,85,247,0.15));
                    border: 1px solid rgba(244,63,94,0.3);
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: 'Arial Black', sans-serif; font-weight: 900; font-size: 14px; text-align: center;\">"
                "<span style=\"color: #fb923c;\">HOÀNG </span><span style=\"color: #f43f5e;\">HÔN </span><span style=\"color: #a855f7;\">SUNSET</span>"
                "</div>"
            )
        elif pid == "mrbeast_impact_yellow":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #0b0f19;
                    border: 1px solid #eab308;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: 'Arial Black', Impact, sans-serif; font-weight: 900; font-size: 14px; text-transform: uppercase; color: #ffffff; text-align: center;\">"
                "1 TRIỆU <span style=\"color: #ffe600; font-size: 16px;\">ĐÔ LA!</span>"
                "</div>"
            )
        elif pid == "apple_keynote_clean":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #0f172a;
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: -apple-system, Arial, sans-serif; font-weight: 600; font-size: 13px; color: #f8fafc; text-align: center;\">"
                "Think Different<br><span style=\"color: #38bdf8; font-size: 10px;\">Apple Keynote</span>"
                "</div>"
            )
        elif pid == "true_crime_documentary":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #09090b;
                    border: 1px solid #7f1d1d;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Georgia, serif; font-size: 12px; color: #e2e8f0; text-align: center;\">"
                "HỒ SƠ <span style=\"color: #ef4444; font-weight: bold;\">VỤ ÁN</span><br><span style=\"font-size: 10px; color: #71717a;\">01:23:45</span>"
                "</div>"
            )
        elif pid == "comic_manga_burst":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #18181b;
                    border: 1px solid #e11d48;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: 'Arial Black', sans-serif; font-weight: 900; font-size: 14px; text-align: center;\">"
                "<span style=\"color: #fff176; background-color: #000000; padding: 2px 4px;\">BOOM!</span> <span style=\"color: #ff0055;\">ACTION</span>"
                "</div>"
            )
        elif pid == "luxury_gold_serif":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #1c1308;
                    border: 1px solid #d97706;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Georgia, 'Times New Roman', serif; font-weight: bold; font-size: 13px; color: #fef3c7; text-align: center;\">"
                "LUXURY <span style=\"color: #fbbf24;\">GOLD</span><br><span style=\"font-size: 9px; letter-spacing: 2px; color: #d97706;\">COLLECTION</span>"
                "</div>"
            )
        elif pid == "minimal_podcast_pill":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_MAIN};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: bold; font-size: 11px; text-align: center;\">"
                "<span style=\"background-color: #0f172a; color: #ffffff; padding: 3px 8px; border-radius: 12px; border: 1px solid #334155;\">The Podcast Talk</span>"
                "</div>"
            )
        elif pid == "schannel_vlog_nhanh":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #18181b;
                    border: 1px solid #facc15;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: 900; font-size: 13px; color: #ffffff; text-align: center;\">"
                "ĐẬP HỘP <span style=\"color: #facc15; font-size: 15px;\">SIÊU PHẨM</span>"
                "</div>"
            )
        elif pid == "khoai_lang_thang_chua_lanh":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #272118;
                    border: 1px solid #78350f;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: bold; font-size: 12px; color: #fef3c7; text-align: center;\">"
                "Hành Trình <span style=\"color: #f59e0b;\">Chữa Lành</span><br><span style=\"font-size: 10px; color: #d4d4d8;\">Bình Yên</span>"
                "</div>"
            )
        elif pid == "phim_truyen_hinh_vtv_retro":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #0a0a0a;
                    border: 1px solid #eab308;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: 13px; color: #ffeb3b; text-align: center;\">"
                "VTV PHIM TRUYỆN<br><span style=\"font-size: 10px; color: #ffffff;\">HÀ NỘI MÙA THU</span>"
                "</div>"
            )
        elif pid == "giat_gan_showbiz_tiktok":
            self.thumb.setStyleSheet(f"""
                QFrame {{
                    background-color: #2e081d;
                    border: 1px solid #f43f5e;
                    border-radius: 8px;
                }}
            """)
            self.lbl_text_preview.setText(
                "<div style=\"font-family: 'Arial Black', sans-serif; font-weight: 900; font-size: 13px; text-align: center;\">"
                "<span style=\"color: #ff0055;\">DRAMA</span> <span style=\"color: #00ffff;\">CĂNG ĐÉT!</span>"
                "</div>"
            )
        else:
            std_col = getattr(self.preset, "standard_color", "#FFFFFF")
            name_clean = getattr(self.preset, "name", "Text").split("(")[0].strip()
            self.lbl_text_preview.setText(
                f"<div style=\"font-family: Arial, sans-serif; font-weight: bold; font-size: 13px; color: {std_col}; text-align: center;\">"
                f"{name_clean}"
                "</div>"
            )

        th_layout.addWidget(self.lbl_text_preview)

    def _render_sfx_thumbnail(self):
        """Render dạng sóng âm thanh 16-20 envelope bars + nút nghe nhanh btn_quick_play."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setSpacing(4)

        # Hàng trên: nút nghe thử nhanh btn_quick_play và thẻ tag
        h_top = QHBoxLayout()
        h_top.setContentsMargins(0, 0, 0, 0)
        h_top.setSpacing(6)

        self.btn_quick_play = QPushButton("▶", self.thumb)
        self.btn_quick_play.setFixedSize(24, 24)
        self.btn_quick_play.setCursor(Qt.PointingHandCursor)
        self.btn_quick_play.setToolTip("Nghe thử âm thanh nhanh")
        self.btn_quick_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 12px;
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.CYAN};
                color: #05252c;
                border-color: {ThemeColors.CYAN_HI};
            }}
        """)
        self.btn_quick_play.clicked.connect(self._toggle_quick_play)
        h_top.addWidget(self.btn_quick_play)

        sub_txt = getattr(self.preset, "sub", "") or "SFX"
        lbl_sfx_badge = QLabel(f"🔊 {sub_txt}")
        lbl_sfx_badge.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10px;")
        h_top.addWidget(lbl_sfx_badge)
        h_top.addStretch()

        th_layout.addLayout(h_top)

        # Waveform canvas 16-20 bars
        pid = getattr(self.preset, "id", "sfx")
        self.waveform_canvas = WaveformCanvas(preset_id=pid, parent=self.thumb)
        th_layout.addWidget(self.waveform_canvas)

    def _render_bgm_thumbnail(self):
        """Render thẻ nhạc nền BGM: icon nốt nhạc 🎵, mood badge, độ dài và nút quick play."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setSpacing(4)

        h_top = QHBoxLayout()
        h_top.setContentsMargins(0, 0, 0, 0)
        h_top.setSpacing(6)

        self.btn_quick_play = QPushButton("▶", self.thumb)
        self.btn_quick_play.setFixedSize(24, 24)
        self.btn_quick_play.setCursor(Qt.PointingHandCursor)
        self.btn_quick_play.setToolTip("Nghe thử nhạc nền nhanh")
        self.btn_quick_play.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 12px;
                color: #06b6d4;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #06b6d4;
                color: #05252c;
                border-color: #22d3ee;
            }}
        """)
        self.btn_quick_play.clicked.connect(self._toggle_quick_play)
        h_top.addWidget(self.btn_quick_play)

        sub_txt = getattr(self.preset, "sub", "") or getattr(self.preset, "category", "") or "BGM"
        lbl_bgm_badge = QLabel(f"🎵 {sub_txt}")
        lbl_bgm_badge.setStyleSheet(f"color: #67e8f9; font-size: 10px; font-weight: bold;")
        h_top.addWidget(lbl_bgm_badge)
        h_top.addStretch()

        dur = getattr(self.preset, "duration", 0.0)
        mins = int(dur // 60)
        secs = int(dur % 60)
        lbl_dur = QLabel(f"{mins}:{secs:02d}" if dur > 0 else "")
        lbl_dur.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10px;")
        h_top.addWidget(lbl_dur)

        th_layout.addLayout(h_top)

        pid = getattr(self.preset, "id", "bgm")
        self.waveform_canvas = WaveformCanvas(preset_id=pid, parent=self.thumb)
        th_layout.addWidget(self.waveform_canvas)

    def _render_lut_thumbnail(self):
        """Render dải màu 4 sọc tương phản cao + huy hiệu danh mục cho LUT."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setSpacing(4)

        # Category badge
        h_top = QHBoxLayout()
        h_top.setContentsMargins(0, 0, 0, 0)

        cat_name = getattr(self.preset, "category", "") or "cinema"
        cat_labels = {
            "natural": "Đời thường",
            "cinema": "Điện ảnh",
            "style": "Phong cách"
        }
        cat_display = cat_labels.get(cat_name.lower(), cat_name.capitalize())
        badge_icon = getattr(self.preset, "badge_icon", "🎨")
        self.badge_category = QLabel(f"{badge_icon} {cat_display}", self.thumb)
        self.badge_category.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(24, 24, 27, 0.85);
                color: #e4e4e7;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        h_top.addWidget(self.badge_category)
        h_top.addStretch()
        th_layout.addLayout(h_top)

        # 4-stripe palette (trích xuất trực tiếp từ .cube 3D LUT nếu chưa có pal)
        pal = getattr(self.preset, "pal", None)
        if not pal or len(pal) < 4:
            try:
                from src.ui.lux.library.lut_preview_generator import extract_lut_palette
                pal = extract_lut_palette(self.preset)
            except Exception:
                pal = ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        pal = pal[:4]

        stripe_container = QWidget(self.thumb)
        stripe_layout = QHBoxLayout(stripe_container)
        stripe_layout.setContentsMargins(0, 0, 0, 0)
        stripe_layout.setSpacing(2)

        self.lut_stripes = []
        for c in pal:
            stripe = QFrame(stripe_container)
            stripe.setFixedHeight(34)
            stripe.setStyleSheet(f"background-color: {c}; border-radius: 3px; border: none;")
            self.lut_stripes.append(stripe)
            stripe_layout.addWidget(stripe)

        th_layout.addWidget(stripe_container)

    def _render_transition_thumbnail(self):
        """Render chuyển cảnh: motion icon, scanline accent, frame count badge (16f, 24f, 30f)."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: #121216;
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setSpacing(4)

        # Frame count badge
        h_top = QHBoxLayout()
        h_top.setContentsMargins(0, 0, 0, 0)

        frames = getattr(self.preset, "duration_frames", None) or getattr(self.preset, "frames", 24)
        self.badge_frames = QLabel(f"{frames}f", self.thumb)
        self.badge_frames.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(17, 18, 20, 0.88);
                color: #f59e0b;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        h_top.addWidget(self.badge_frames)
        h_top.addStretch()
        th_layout.addLayout(h_top)

        # Visual motion icon
        badge_icon = getattr(self.preset, "badge_icon", "🎬")
        self.lbl_motion_icon = QLabel(f"{badge_icon}  A ➔ B", self.thumb)
        self.lbl_motion_icon.setAlignment(Qt.AlignCenter)
        self.lbl_motion_icon.setStyleSheet("""
            QLabel {{
                color: #ffffff;
                font-size: 13px;
                font-weight: bold;
            }}
        """)
        th_layout.addWidget(self.lbl_motion_icon)

        # Scanline accent
        self.scanline_accent = QFrame(self.thumb)
        self.scanline_accent.setFixedHeight(2)
        self.scanline_accent.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 transparent, stop:0.5 {ThemeColors.CYAN}, stop:1 transparent);
                border: none;
            }}
        """)
        th_layout.addWidget(self.scanline_accent)

    def _render_icon_thumbnail(self):
        """Render icon SVG hoặc reaction emoji badge cỡ lớn kèm nút xem thử nhanh."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setSpacing(4)

        tab_type = getattr(self.preset, "tab", "")
        if tab_type in ("meme", "overlay"):
            h_top = QHBoxLayout()
            h_top.setContentsMargins(0, 0, 0, 0)
            self.btn_quick_play = QPushButton("▶", self.thumb)
            self.btn_quick_play.setFixedSize(22, 22)
            self.btn_quick_play.setCursor(Qt.PointingHandCursor)
            self.btn_quick_play.setToolTip("Xem live preview")
            self.btn_quick_play.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 11px;
                    color: {ThemeColors.CYAN_HI};
                    font-size: 10px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {ThemeColors.CYAN};
                    color: #05252c;
                    border-color: {ThemeColors.CYAN_HI};
                }}
            """)
            self.btn_quick_play.clicked.connect(self._on_quick_play_meme_clicked)
            h_top.addWidget(self.btn_quick_play)

            lbl_tag = QLabel("🎬 MEME" if tab_type == "meme" else "✨ OVERLAY")
            lbl_tag.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 9px; font-weight: bold;")
            h_top.addWidget(lbl_tag)
            h_top.addStretch()
            th_layout.addLayout(h_top)

        badge = getattr(self.preset, "badge_icon", "") or getattr(self.preset, "emo", "✨")
        color = getattr(self.preset, "color_hex", "#facc15")
        self.lbl_icon_preview = QLabel(badge, self.thumb)
        self.lbl_icon_preview.setAlignment(Qt.AlignCenter)
        self.lbl_icon_preview.setStyleSheet(f"""
            QLabel {{
                font-size: 30px;
                color: {color};
                background: transparent;
                border: none;
            }}
        """)
        th_layout.addWidget(self.lbl_icon_preview)

    def _on_quick_play_meme_clicked(self):
        pid = getattr(self.preset, "id", "")
        self.selected_signal.emit(pid)
        self.quick_play_requested.emit(pid)

    def _on_quick_add_clicked(self):
        pid = getattr(self.preset, "id", "")
        self.selected_signal.emit(pid)
        self.add_requested.emit(self.preset)

    @classmethod
    def stop_all_audio(cls):
        if cls._active_playing_card is not None:
            card = cls._active_playing_card
            cls._active_playing_card = None
            card._is_playing_audio = False
            card._apply_style()
            if hasattr(card, "btn_quick_play"):
                card.btn_quick_play.setText("▶")
            if hasattr(card, "waveform_canvas"):
                card.waveform_canvas.is_playing = False
                card.waveform_canvas.update()
        if cls._shared_player is not None:
            try:
                cls._shared_player.stop()
            except Exception:
                pass

    @classmethod
    def _on_shared_player_state_changed(cls, state):
        if state == QMediaPlayer.StoppedState:
            cls.stop_all_audio()

    def _toggle_quick_play(self):
        if self._is_playing_audio:
            self._stop_quick_play()
        else:
            self._start_quick_play()

    def _start_quick_play(self):
        if AssetCard._active_playing_card is not self:
            AssetCard.stop_all_audio()

        self._is_playing_audio = True
        AssetCard._active_playing_card = self
        self._apply_style()
        if hasattr(self, "btn_quick_play"):
            self.btn_quick_play.setText("⏸")
        if hasattr(self, "waveform_canvas"):
            self.waveform_canvas.is_playing = True
            self.waveform_canvas.update()

        pid = getattr(self.preset, "id", "")
        clean_id = pid.replace("sfx_", "").replace("bgm_", "")
        candidate_paths = [
            getattr(self.preset, "file_path", ""),
            str(PosixPathAdapter.get_asset_path("sfx", f"{clean_id}.wav")),
            str(PosixPathAdapter.get_asset_path("sfx", f"{pid}.wav")),
        ]
        sound_path = None
        for p in candidate_paths:
            if p and os.path.exists(p):
                sound_path = p
                break

        if sound_path:
            try:
                if AssetCard._shared_player is None:
                    AssetCard._shared_player = QMediaPlayer()
                    AssetCard._shared_audio_output = QAudioOutput()
                    try:
                        from src.services.audio_router import get_audio_router
                        get_audio_router().register(AssetCard._shared_audio_output)
                    except Exception:
                        pass
                    AssetCard._shared_player.setAudioOutput(AssetCard._shared_audio_output)
                    AssetCard._shared_player.playbackStateChanged.connect(AssetCard._on_shared_player_state_changed)
                self._player = AssetCard._shared_player
                self._audio_output = AssetCard._shared_audio_output
                AssetCard._shared_player.setSource(QUrl.fromLocalFile(os.path.abspath(sound_path)))
                AssetCard._shared_audio_output.setVolume(0.8)
                AssetCard._shared_player.play()
            except Exception:
                QTimer.singleShot(1500, self._stop_quick_play)
        else:
            QTimer.singleShot(1500, self._stop_quick_play)

        self.quick_play_requested.emit(pid)

    def _stop_quick_play(self):
        if AssetCard._active_playing_card is self:
            AssetCard.stop_all_audio()
        else:
            self._is_playing_audio = False
            self._apply_style()
            if hasattr(self, "btn_quick_play"):
                self.btn_quick_play.setText("▶")
            if hasattr(self, "waveform_canvas"):
                self.waveform_canvas.is_playing = False
                self.waveform_canvas.update()
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass

    def _on_player_state_changed(self, state):
        if state == QMediaPlayer.StoppedState:
            self._stop_quick_play()

    def enterEvent(self, event):
        tab_type = getattr(self.preset, "tab", "")
        if AssetCard.enable_hover_preview and tab_type in ("sfx", "bgm"):
            self._start_quick_play()
        super().enterEvent(event)

    def leaveEvent(self, event):
        tab_type = getattr(self.preset, "tab", "")
        if tab_type in ("sfx", "bgm") and self._is_playing_audio:
            self._stop_quick_play()
        super().leaveEvent(event)

    def _toggle_fav(self):
        self.is_fav = not self.is_fav
        self.btn_fav.setText("❤️" if self.is_fav else "🤍")
        pid = getattr(self.preset, "id", "")
        self.favorite_toggled.emit(pid, self.is_fav)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._apply_style()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            pid = getattr(self.preset, "id", "")
            self.selected_signal.emit(pid)
            self.add_requested.emit(self.preset)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            pid = getattr(self.preset, "id", "")
            self.selected_signal.emit(pid)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self._drag_start_pos:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            return
        self._start_drag()

    def _start_drag(self):
        drag = QDrag(self)
        mime_data = QMimeData()
        temp_dir = tempfile.gettempdir()

        target = self.preset
        file_path = getattr(target, "file_path", "")
        tab_type = getattr(target, "tab", "")
        asset_id = getattr(target, "id", "asset")

        # 1. Nếu đã có file_path thực tế trên đĩa (wav, mp4, cube, setting, png, v.v.)
        if file_path and os.path.isfile(file_path):
            abs_p = os.path.abspath(file_path)
            mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
            mime_data.setText(abs_p)

        # 2. LocalAsset
        elif isinstance(target, LocalAsset):
            abs_p = os.path.abspath(target.file_path)
            mime_data.setUrls([QUrl.fromLocalFile(abs_p)])
            mime_data.setText(abs_p)

        # 3. TextStylePreset hoặc tab == 'text'
        elif isinstance(target, TextStylePreset) or tab_type == "text":
            temp_setting = os.path.join(temp_dir, f"ChunDVC_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            sample_txt = self.sample_text_func() if callable(self.sample_text_func) else getattr(target, "name", "ChunDVC Title")
            if isinstance(target, TextStylePreset):
                FusionSettingGenerator.export_setting_file(target, temp_setting, sample_text=sample_txt)
            else:
                native = getattr(target, "native_preset", None)
                if isinstance(native, TextStylePreset):
                    FusionSettingGenerator.export_setting_file(native, temp_setting, sample_text=sample_txt)
                else:
                    with open(temp_setting, "w", encoding="utf-8") as f:
                        f.write(FusionSettingGenerator.generate_setting_content(TextStylePreset(id=asset_id, name=getattr(target, "name", "Title")), sample_text=sample_txt))
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(temp_setting))])
            mime_data.setText(os.path.abspath(temp_setting))

        # 4. TransitionStylePreset hoặc tab == 'trans'
        elif isinstance(target, TransitionStylePreset) or tab_type == "trans":
            temp_setting = os.path.join(temp_dir, f"ChunDVC_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            if isinstance(target, TransitionStylePreset):
                TransitionMacroGenerator.export_setting_file(target, temp_setting)
            else:
                native = getattr(target, "native_preset", None)
                if isinstance(native, TransitionStylePreset):
                    TransitionMacroGenerator.export_setting_file(native, temp_setting)
                else:
                    TransitionMacroGenerator.export_setting_file(TransitionStylePreset(id=asset_id, name=getattr(target, "name", "Transition")), temp_setting)
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(temp_setting))])
            mime_data.setText(os.path.abspath(temp_setting))

        # 5. SFX (.wav)
        elif tab_type == "sfx":
            clean_id = asset_id.replace("sfx_", "")
            candidate_paths = [
                str(PosixPathAdapter.get_asset_path("sfx", f"{clean_id}.wav")),
                str(PosixPathAdapter.get_asset_path("sfx", f"{asset_id}.wav")),
                file_path
            ]
            sfx_file = None
            for p in candidate_paths:
                if p and os.path.isfile(p):
                    sfx_file = os.path.abspath(p)
                    break
            if not sfx_file:
                sfx_file = os.path.join(temp_dir, f"ChunDVC_{clean_id}.wav")
                if not os.path.exists(sfx_file):
                    with open(sfx_file, "wb") as f:
                        f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(sfx_file))])
            mime_data.setText(os.path.abspath(sfx_file))

        # 6. Meme / Overlay (.mp4)
        elif tab_type in ("meme", "overlay"):
            dir_name = "memes" if tab_type == "meme" else "overlays"
            candidate_paths = [
                str(PosixPathAdapter.get_asset_path("broll_memes", dir_name, f"{asset_id}.mp4")),
                str(PosixPathAdapter.get_asset_path("broll_memes", f"{asset_id}.mp4")),
                file_path
            ]
            vid_file = None
            for p in candidate_paths:
                if p and os.path.isfile(p):
                    vid_file = os.path.abspath(p)
                    break
            if vid_file:
                mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(vid_file))])
                mime_data.setText(os.path.abspath(vid_file))

        # 7. LUT (.cube)
        elif tab_type == "lut":
            candidate_paths = [
                str(PosixPathAdapter.get_asset_path("luts", f"{asset_id}.cube")),
                str(PosixPathAdapter.get_asset_path("luts", f"ResolveFlow_{asset_id}.cube")),
                file_path
            ]
            lut_file = None
            for p in candidate_paths:
                if p and os.path.isfile(p):
                    lut_file = os.path.abspath(p)
                    break
            if not lut_file:
                lut_file = os.path.join(temp_dir, f"ChunDVC_{asset_id}.cube")
                if not os.path.exists(lut_file):
                    with open(lut_file, "w", encoding="utf-8") as f:
                        f.write(f'TITLE "ChunDVC_{asset_id}"\nLUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 0.0 0.0\n0.0 1.0 0.0\n1.0 1.0 0.0\n0.0 0.0 1.0\n1.0 0.0 1.0\n0.0 1.0 1.0\n1.0 1.0 1.0\n')
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(lut_file))])
            mime_data.setText(os.path.abspath(lut_file))

        # 8. Sticker / Icon (Sinh ảnh Transparent PNG nét căng để kéo vào Track V2)
        elif tab_type == "icon":
            badge = getattr(target, "badge_icon", "✨")
            color_hex = getattr(target, "color_hex", "#facc15")
            png_path = os.path.join(temp_dir, f"ChunDVC_Sticker_{asset_id}.png")
            pixmap = QPixmap(512, 512)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.TextAntialiasing)
            font = QFont("Segoe UI Emoji", 260)
            painter.setFont(font)
            painter.setPen(QColor(color_hex))
            painter.drawText(QRectF(0, 0, 512, 512), Qt.AlignCenter, badge)
            painter.end()
            pixmap.save(png_path, "PNG")
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(png_path))])
            mime_data.setText(os.path.abspath(png_path))

        if mime_data.hasUrls():
            drag.setMimeData(mime_data)
            try:
                pixmap = self.grab()
                if not pixmap.isNull():
                    ghost = pixmap.scaledToWidth(180, Qt.SmoothTransformation) if pixmap.width() > 180 else pixmap
                    transparent_pix = QPixmap(ghost.size())
                    transparent_pix.fill(Qt.transparent)
                    painter = QPainter(transparent_pix)
                    painter.setOpacity(0.85)
                    painter.drawPixmap(0, 0, ghost)
                    painter.end()
                    drag.setPixmap(transparent_pix)
            except Exception:
                pass
            drag.exec(Qt.DropAction.CopyAction)
            try:
                from src.ui.lux.drag_drop_tracker import is_mouse_over_resolve
                if is_mouse_over_resolve():
                    self.insert_title_requested.emit(asset_id)
            except Exception:
                pass



class SuggestionChip(QFrame):
    """
    Thẻ gợi ý đạo cụ nhanh theo ngữ cảnh (CapCut AI Suggestion Chip).
    Hiển thị Icon, Tên, Lý do phân tích cảm xúc và nút 1-click chèn nhanh vào DaVinci Resolve.
    """
    selected_signal = pyqtSignal(str)   # asset_id
    add_requested = pyqtSignal(object)  # asset

    def __init__(self, suggested_asset: Any, parent=None):
        super().__init__(parent)
        self.suggested = suggested_asset
        self.asset = getattr(suggested_asset, "asset", suggested_asset)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)
        self.setMinimumWidth(130)
        self.setMaximumWidth(210)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame {
                background-color: #1c1d21;
                border: 1px solid #353841;
                border-radius: 5px;
            }
            QFrame:hover {
                background-color: #23252a;
                border-color: #d94f3d;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 1, 6, 1)
        layout.setSpacing(5)

        # Icon
        badge = getattr(self.asset, "badge_icon", "✨")
        lbl_icon = QLabel(badge)
        lbl_icon.setFixedSize(20, 20)
        lbl_icon.setAlignment(Qt.AlignCenter)
        lbl_icon.setStyleSheet("""
            background: rgba(217, 79, 61, 0.16);
            border-radius: 4px;
            font-size: 11px;
        """)
        layout.addWidget(lbl_icon)

        # Texts (Name + Reason)
        vbox = QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        name = getattr(self.asset, "name", "Đạo cụ")
        short_name = name if len(name) <= 17 else name[:15] + ".."
        self.lbl_name = QLabel(short_name)
        self.lbl_name.setToolTip(name)
        self.lbl_name.setStyleSheet("""
            font-size: 10px;
            font-weight: bold;
            color: #ffffff;
        """)
        vbox.addWidget(self.lbl_name)

        reason = getattr(self.suggested, "reason", "⚡ Đề xuất AI")
        self.lbl_reason = QLabel(reason)
        self.lbl_reason.setStyleSheet("""
            font-size: 9px;
            color: #38bdf8;
            font-weight: 500;
        """)
        vbox.addWidget(self.lbl_reason)
        layout.addLayout(vbox, stretch=1)

        # Quick Add Button
        self.btn_add = QPushButton("➕")
        self.btn_add.setFixedSize(18, 18)
        self.btn_add.setCursor(Qt.PointingHandCursor)
        self.btn_add.setToolTip("Chèn nhanh vào DaVinci Resolve tại Playhead (1-Click)")
        self.btn_add.setStyleSheet("""
            QPushButton {
                background: rgba(6, 182, 212, 0.15);
                border: 1px solid rgba(6, 182, 212, 0.5);
                border-radius: 9px;
                color: #22d3ee;
                font-size: 9px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                background: #06b6d4;
                color: #082f49;
                border-color: #22d3ee;
            }
        """)
        self.btn_add.clicked.connect(self._on_add_clicked)
        layout.addWidget(self.btn_add)

    def _on_add_clicked(self):
        aid = getattr(self.asset, "id", "")
        self.selected_signal.emit(aid)
        self.add_requested.emit(self.asset)

    def mousePressEvent(self, event):
        if event is not None:
            super().mousePressEvent(event)
        aid = getattr(self.asset, "id", "")
        self.selected_signal.emit(aid)

    def mouseDoubleClickEvent(self, event):
        if event is not None:
            super().mouseDoubleClickEvent(event)
        self._on_add_clicked()

    def enterEvent(self, event):
        super().enterEvent(event)
        if getattr(AssetCard, "enable_hover_preview", True):
            tab = getattr(self.asset, "tab", "")
            if tab in ("sfx", "bgm"):
                self._start_audio_preview()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        tab = getattr(self.asset, "tab", "")
        if tab in ("sfx", "bgm"):
            AssetCard.stop_all_audio()

    def _start_audio_preview(self):
        AssetCard.stop_all_audio()
        aid = getattr(self.asset, "id", "")
        clean_id = aid.replace("sfx_", "").replace("bgm_", "")
        candidate_paths = [
            getattr(self.asset, "file_path", ""),
            str(PosixPathAdapter.get_asset_path("sfx", f"{clean_id}.wav")),
            str(PosixPathAdapter.get_asset_path("sfx", f"{aid}.wav")),
        ]
        sound_path = None
        for p in candidate_paths:
            if p and os.path.exists(p):
                sound_path = p
                break
        if sound_path:
            try:
                if AssetCard._shared_player is None:
                    AssetCard._shared_player = QMediaPlayer()
                    AssetCard._shared_audio_output = QAudioOutput()
                    try:
                        from src.services.audio_router import get_audio_router
                        get_audio_router().register(AssetCard._shared_audio_output)
                    except Exception:
                        pass
                    AssetCard._shared_player.setAudioOutput(AssetCard._shared_audio_output)
                    AssetCard._shared_audio_output.setVolume(0.8)
                AssetCard._shared_player.setSource(QUrl.fromLocalFile(sound_path))
                AssetCard._shared_player.play()
            except Exception:
                pass


# =========================================================================
# 6. TAB ASSETS - GIAO DIỆN 4 CỘT CHUẨN KHO ĐẠO CỤ
# =========================================================================
class TabAssets(QWidget):
    library_assets_changed = pyqtSignal(list)
    """
    Kho Đạo Cụ 4 Cột:
    Rail (78px) | Categories (204px) | Main Grid (flex 1) | Inspector (336px)
    """
    insert_title_requested = pyqtSignal(str, str, float)
    install_presets_requested = pyqtSignal()
    copy_fusion_node_requested = pyqtSignal(str)
    apply_lut_requested = pyqtSignal(str)
    insert_sfx_requested = pyqtSignal(str, int, float)

    def __init__(self, parent=None, headless: bool = False):
        super().__init__(parent)
        self._current_preset_callback = None
        self.favorites = FavoritesManager.load()
        self.headless = headless
        self._last_storage_sig = None

        # Dữ liệu nạp sẵn (tận dụng Singleton Cache của AssetIndexer)
        self.indexer = AssetIndexer()
        indexer = self.indexer
        
        # Helper to convert dicts to StudioAsset
        def dicts_to_assets(dicts, tab, category, sub, badge):
            return [
                StudioAsset(
                    id=d["id"], name=d["name"], tab=tab, category=d.get("category", category),
                    sub=d.get("sub_category") or d.get("pack_name") or sub, badge_icon=badge,
                    file_path=d.get("file_path", ""),
                    thumbnail_path=d.get("thumbnail_path", ""),
                    preview_path=d.get("preview_path", ""),
                    pack_name=d.get("pack_name", ""),
                    duration_sec=d.get("duration_sec"),
                ) for d in dicts
            ]
        
        # Combine builtins with scanned settings
        scanned_titles = indexer.scan_titles()
        self.all_presets = [p.model_copy(deep=True) for p in BUILTIN_PRESETS] + dicts_to_assets(
            scanned_titles, "text", "custom", "Tùy chỉnh", "🔤"
        )
        
        scanned_transitions = indexer.scan_transitions()
        self.all_transitions = BUILTIN_TRANSITIONS.copy() + dicts_to_assets(
            scanned_transitions, "trans", "custom", "Tùy chỉnh", "🎬"
        )

        scanned_luts = indexer.scan_luts()
        self.all_luts = dicts_to_assets(scanned_luts, "lut", "cinema", "Màu tự động", "🎨") if scanned_luts else []
        
        scanned_memes = indexer.scan_memes()
        self.all_memes = dicts_to_assets(scanned_memes, "meme", "trending", "Meme", "🎭") if scanned_memes else []
        
        scanned_sfx = indexer.scan_sfx()
        self.all_sfx = dicts_to_assets(scanned_sfx, "sfx", "accent", "Âm thanh", "🔊") if scanned_sfx else []
        
        scanned_overlays = indexer.scan_overlays()
        self.all_overlays = dicts_to_assets(scanned_overlays, "overlay", "cinematic", "Lớp phủ", "✨") if scanned_overlays else []
        self.all_icons = []

        music_data = indexer.scan_music_assets()
        scanned_bgm = []
        for mood, items in music_data.items():
            for itm in items:
                scanned_bgm.append(
                    StudioAsset(
                        id=itm["id"],
                        name=itm["name"],
                        tab="bgm",
                        category=itm.get("mood", mood),
                        sub=itm.get("mood", mood),
                        badge_icon="🎵",
                        duration=itm.get("duration", 120.0),
                        file_path=itm.get("file_path", ""),
                        thumbnail_path=itm.get("thumbnail_path", "")
                    )
                )
        self.all_bgm = scanned_bgm
        
        self.local_assets: List[LocalAsset] = []

        # Tổng hợp danh sách đối tượng
        self.all_assets = []
        self.all_assets.extend(self.all_presets)
        self.all_assets.extend(self.all_transitions)
        self.all_assets.extend(self.all_luts)
        self.all_assets.extend(self.all_icons)
        self.all_assets.extend(self.all_overlays)
        self.all_assets.extend(self.all_memes)
        self.all_assets.extend(self.all_sfx)
        self.all_assets.extend(self.all_bgm)
        self._merge_storage_assets()

        self.current_rail_tab = "text"
        self.current_sub_cat = "all"
        self.current_aspect_ratio = "16:9"
        self._card_widgets_cache: Optional[List[AssetCard]] = None
        self.selected_asset = self.all_presets[0] if self.all_presets else None

        self.suggester = ContextualSuggester(assets=self.all_assets)
        self._current_context_role = ""
        self._transcript_segments: List[Dict[str, Any]] = []

        self.setAcceptDrops(True)
        self._init_ui()
        if hasattr(self, "scroll_grid"):
            self.scroll_grid.setAcceptDrops(True)
            self.scroll_grid.viewport().installEventFilter(self)

        # sfx_playback_timer đã được thay thế hoàn toàn bằng QMediaPlayer trong AudioInspectorWidget
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(lambda: self.lbl_toast.hide() if hasattr(self, "lbl_toast") else None)
        
        if not self.headless:
            self._populate_cards()
            self._filter_by_rail("text")

    def set_preset_provider(self, provider_func: Callable[[], Optional[Any]]):
        self._current_preset_callback = provider_func
        self.preview_lbl.preset_getter = provider_func
        self.preview_lbl.sample_text_getter = lambda: self.txt_single_title.text().strip() or "ResolveFlow Title"
        self.preview_lbl.mime_data_generator = lambda p=None: self._create_drag_mime_data(p or self.selected_asset)


    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # =====================================================================
        # CỘT 1: RAIL ĐIỀU HƯỚNG CHÍNH (86px)
        # =====================================================================
        self.rail = QFrame()
        self.rail.setFixedWidth(86)
        self.rail.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-right: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(6, 10, 6, 10)
        rail_layout.setSpacing(4)

        tabs_data = [
            ("text", "🔤\nChữ"),
            ("lut", "🎨\nMàu"),
            ("trans", "🎬\nChuyển cảnh"),
            ("icon", "🏷️\nSticker"),
            ("overlay", "🎞️\nLớp phủ"),
            ("meme", "🎭\nMeme"),
            ("sfx", "🔊\nÂm thanh"),
            ("bgm", "🎵\nNhạc nền"),
            ("fav", "❤️\nCủa tôi"),
            ("hub", "☁️\nKho Online")
        ]

        if any(getattr(a, "tab", "") == "broll" for a in self.all_assets):
            tabs_data.insert(6, ("broll", "🎥\nCảnh"))
        from src.core.storage_manager import StorageManager
        for cid, label in StorageManager().taxonomy().items():
            if cid not in ("broll", "sfx", "bgm", "overlays", "memes", "luts", "templates"):
                tabs_data.append((cid, "📁\n" + label))
        self.rail_btns = {}
        for tid, tname in tabs_data:
            btn = QPushButton(tname)
            btn.setCheckable(True)
            btn.setFixedSize(74, 52)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    border-radius: 8px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 10.5px;
                    line-height: 12px;
                    text-align: center;
                    border: none;
                    background: transparent;
                    padding: 2px 0;
                }}
                QPushButton:hover {{
                    color: {ThemeColors.TEXT_PRIMARY};
                    background: {ThemeColors.BG_CARD};
                }}
                QPushButton:checked {{
                    color: #f3f4f6;
                    background: #23252a;
                    border-left: 3px solid {ThemeColors.PRIMARY};
                    font-weight: bold;
                }}
            """)
            btn.clicked.connect(lambda _, x=tid: self._filter_by_rail(x))
            rail_layout.addWidget(btn)
            self.rail_btns[tid] = btn

        rail_layout.addStretch()
        self.rail_btns["text"].setChecked(True)

        # =====================================================================
        # CỘT 2: CATEGORIES PHÂN LOẠI CON (min 160px)
        # =====================================================================
        self.cats = QFrame()
        self.cats.setMinimumWidth(160)
        self.cats.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-right: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        cats_layout = QVBoxLayout(self.cats)
        cats_layout.setContentsMargins(0, 0, 0, 0)
        cats_layout.setSpacing(0)

        self.lbl_cats_head = QLabel("Mẫu chữ Text+")
        self.lbl_cats_head.setStyleSheet(f"""
            padding: 16px 14px 10px;
            font-weight: bold;
            font-size: 14px;
            color: {ThemeColors.TEXT_PRIMARY};
        """)
        cats_layout.addWidget(self.lbl_cats_head)

        scroll_cats = QScrollArea()
        scroll_cats.setWidgetResizable(True)
        scroll_cats.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_cats.setStyleSheet("background: transparent; border: none;")
        self.cats_container = QWidget()
        self.cats_vbox = QVBoxLayout(self.cats_container)
        self.cats_vbox.setContentsMargins(8, 0, 8, 0)
        self.cats_vbox.setSpacing(2)
        scroll_cats.setWidget(self.cats_container)
        cats_layout.addWidget(scroll_cats, stretch=1)

        self.btn_starter_pack = QPushButton("📦 Gói Starter Pack (SFX & LUTs)")
        self.btn_starter_pack.setCursor(Qt.PointingHandCursor)
        self.btn_starter_pack.setToolTip("Nạp bộ âm thanh hiệu ứng SFX chuẩn 48kHz và 16 Cinema 3D LUTs vào Kho qua kiểm duyệt (BGM/Overlay/Meme để lát cắt sau)")
        self.btn_starter_pack.clicked.connect(self._on_starter_pack_clicked)
        self.btn_starter_pack.setStyleSheet(f"""
            QPushButton {{
                margin: 6px 10px 2px 10px;
                background-color: rgba(6, 182, 212, 0.15);
                border: 1px solid {ThemeColors.CYAN};
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
                padding: 8px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.CYAN};
                color: #05252c;
                border-color: {ThemeColors.CYAN_HI};
            }}
        """)
        cats_layout.addWidget(self.btn_starter_pack)

        self.btn_scan_local = QPushButton("➕ Quét thư mục cá nhân")
        self.btn_scan_local.setCursor(Qt.PointingHandCursor)
        self.btn_scan_local.clicked.connect(self._scan_local_folder)
        self.btn_scan_local.setStyleSheet(f"""
            QPushButton {{
                margin: 10px;
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11px;
                padding: 8px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
            }}
        """)
        cats_layout.addWidget(self.btn_scan_local)

        # =====================================================================
        # CỘT 3: MAIN SECTION - LƯỚI CARD TRỰC QUAN (min 300px)
        # =====================================================================
        self.main_section = QWidget()
        self.main_section.setMinimumWidth(300)
        self.main_section.setStyleSheet(f"background: {ThemeColors.BG_CARD};")
        main_sec_layout = QVBoxLayout(self.main_section)
        main_sec_layout.setContentsMargins(0, 0, 0, 0)
        main_sec_layout.setSpacing(0)

        main_head = QFrame()
        main_head.setFixedHeight(44)
        main_head.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        mh_layout = QHBoxLayout(main_head)
        mh_layout.setContentsMargins(10, 4, 10, 4)
        mh_layout.setSpacing(6)

        # 1. Nút thu gọn / mở rộng cột Nhóm
        self.btn_toggle_cats = QPushButton("☰")
        self.btn_toggle_cats.setToolTip("Ẩn / Hiện cột Phân loại danh mục")
        self.btn_toggle_cats.setFixedSize(28, 28)
        self.btn_toggle_cats.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_cats.clicked.connect(self._toggle_cats_visibility)
        self.btn_toggle_cats.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                color: {ThemeColors.TEXT_MUTED};
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.BORDER_HOVER};
                color: {ThemeColors.TEXT_PRIMARY};
            }}
        """)
        mh_layout.addWidget(self.btn_toggle_cats)

        self.lbl_main_cat = QLabel("Chữ chuyển động")
        self.lbl_main_cat.setStyleSheet(f"font-weight: bold; font-size: 12.5px; color: {ThemeColors.TEXT_PRIMARY};")
        mh_layout.addWidget(self.lbl_main_cat)

        self.lbl_count_badge = QLabel("14 mẫu")
        self.lbl_count_badge.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        mh_layout.addWidget(self.lbl_count_badge)

        # 2. Thanh tìm kiếm trung tâm lớn, co giãn linh hoạt
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Tìm nhanh đạo cụ, SFX, Meme... (Ctrl+F)")
        self.txt_search.setMinimumWidth(120)
        self.txt_search.textChanged.connect(self._refresh_grid)
        self.txt_search.setStyleSheet(f"""
            QLineEdit {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 5px 10px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11.5px;
            }}
            QLineEdit:focus {{
                border-color: {ThemeColors.PRIMARY};
                background: {ThemeColors.BG_INPUT};
            }}
        """)
        mh_layout.addWidget(self.txt_search, stretch=1)

        # 3. Các nút tác vụ cốt lõi bên phải
        self.btn_open_asset_hub = QPushButton("☁️ Kho Online")
        self.btn_open_asset_hub.setToolTip("Mở Kho Gói Đám Mây Tuyển Chọn & Quản lý thư mục ngoài")
        self.btn_open_asset_hub.setFixedHeight(28)
        self.btn_open_asset_hub.setCursor(Qt.PointingHandCursor)
        self.btn_open_asset_hub.clicked.connect(lambda: self._filter_by_rail("hub"))
        self.btn_open_asset_hub.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff4757, stop:1 #e0283b);
                color: #ffffff;
                border: 1px solid #ff6b81;
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #ff5252;
            }
        """)
        mh_layout.addWidget(self.btn_open_asset_hub)

        self.btn_import_assets = QPushButton("➕ Nhập")
        self.btn_import_assets.setToolTip("Thêm mẫu chữ .setting, video meme, SFX, LUTs...")
        self.btn_import_assets.setFixedHeight(28)
        self.btn_import_assets.setCursor(Qt.PointingHandCursor)
        self.btn_import_assets.clicked.connect(lambda: self._import_files_or_folders())
        self.btn_import_assets.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 0 10px;
                color: #f3f4f6;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {ThemeColors.PRIMARY};
                background: {ThemeColors.BG_CARD_ACTIVE};
            }}
        """)
        mh_layout.addWidget(self.btn_import_assets)

        self.btn_toggle_sug_bar = QPushButton("💡 Gợi ý AI")
        self.btn_toggle_sug_bar.setToolTip("Bật / tắt bảng gợi ý đạo cụ theo lời thoại AI")
        self.btn_toggle_sug_bar.setCheckable(True)
        self.btn_toggle_sug_bar.setChecked(False)
        self.btn_toggle_sug_bar.setFixedHeight(28)
        self.btn_toggle_sug_bar.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_sug_bar.clicked.connect(self._on_sug_bar_toggled)
        self.btn_toggle_sug_bar.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 0 9px;
                color: #f59e0b;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: #f59e0b;
                color: #fbbf24;
            }}
            QPushButton:checked {{
                background: rgba(245, 158, 11, 0.2);
                border-color: #f59e0b;
                color: #fbbf24;
            }}
        """)
        mh_layout.addWidget(self.btn_toggle_sug_bar)

        self.btn_toggle_inspector = QPushButton("◫ Chi tiết")
        self.btn_toggle_inspector.setToolTip("Ẩn / Hiện cột Chi tiết Inspector")
        self.btn_toggle_inspector.setFixedHeight(28)
        self.btn_toggle_inspector.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_inspector.clicked.connect(self._toggle_inspector_visibility)
        self.btn_toggle_inspector.setStyleSheet(self.btn_import_assets.styleSheet())
        mh_layout.addWidget(self.btn_toggle_inspector)

        self.btn_rescan = QPushButton("🔄")
        self.btn_rescan.setToolTip("Quét lại toàn bộ kho đạo cụ cục bộ")
        self.btn_rescan.setFixedSize(28, 28)
        self.btn_rescan.setCursor(Qt.PointingHandCursor)
        self.btn_rescan.clicked.connect(self._rescan_assets)
        self.btn_rescan.setStyleSheet(self.btn_toggle_cats.styleSheet())
        mh_layout.addWidget(self.btn_rescan)

        # Thanh công cụ phụ: Bộ lọc nhanh & Kích thước thẻ gọn nhẹ
        filter_bar = QFrame()
        filter_bar.setFixedHeight(34)
        filter_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(10, 3, 10, 3)
        fb_layout.setSpacing(6)

        self.btn_filter_all = QPushButton("Tất cả")
        self.btn_filter_fav = QPushButton("⭐ Yêu thích")
        self.btn_filter_recent = QPushButton("🕒 Gần đây")
        self.btn_filter_recent.setToolTip("Đã dùng gần đây")
        self.filter_buttons = [self.btn_filter_all, self.btn_filter_fav, self.btn_filter_recent]

        for btn, mode in [(self.btn_filter_all, "all"), (self.btn_filter_fav, "fav"), (self.btn_filter_recent, "recent")]:
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _, m=mode: self._set_filter_mode(m))
            fb_layout.addWidget(btn)
        self.btn_filter_all.setChecked(True)
        self._update_filter_button_styles()

        fb_layout.addStretch()

        lbl_zoom = QLabel("Cỡ:")
        lbl_zoom.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
        fb_layout.addWidget(lbl_zoom)

        # 3 Nút kích thước thẻ: Nhỏ (5) | Vừa (3) | Lớn (2)
        self.btn_size_compact = QPushButton("Nhỏ")
        self.btn_size_compact.setToolTip("Thu nhỏ (5 cột) - Nhìn bao quát nhiều mẫu nhất")
        self.btn_size_medium = QPushButton("Vừa")
        self.btn_size_medium.setToolTip("Chuẩn (3 cột) - Cân đối hình ảnh & thông tin")
        self.btn_size_large = QPushButton("Lớn")
        self.btn_size_large.setToolTip("Phóng to (2 cột) - Xem chi tiết thumbnail")

        self.grid_size_buttons = [
            (self.btn_size_compact, 5),
            (self.btn_size_medium, 3),
            (self.btn_size_large, 2)
        ]

        for btn, cols in self.grid_size_buttons:
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(22)
            btn.clicked.connect(lambda _, c=cols: self._on_grid_cols_changed(c))
            fb_layout.addWidget(btn)
        self.btn_size_medium.setChecked(True)
        self._update_grid_size_button_styles()

        # Checkbox: Nghe thử (Hover Preview - CapCut style)
        self.chk_hover_preview = QCheckBox("🔊 Nghe thử")
        self.chk_hover_preview.setChecked(True)
        self.chk_hover_preview.setToolTip("🔊 Nghe thử: Tự động phát âm thanh khi rê chuột qua card SFX / Nhạc / Meme")
        self.chk_hover_preview.setStyleSheet(f"""
            QCheckBox {{
                color: {ThemeColors.TEXT_MUTED};
                font-size: 11px;
                margin-left: 4px;
            }}
            QCheckBox:checked {{
                color: {ThemeColors.CYAN_HI};
                font-weight: bold;
            }}
        """)
        self.chk_hover_preview.toggled.connect(self._on_hover_preview_toggled)
        fb_layout.addWidget(self.chk_hover_preview)

        # Responsive adaptation for narrow widths (< 580px in main section)
        def _update_filter_bar_responsive():
            w = filter_bar.width()
            if w > 0 and w < 580:
                self.chk_hover_preview.setText("")
                self.chk_hover_preview.setToolTip("🔊 Nghe thử (Tự động phát khi rê chuột qua card SFX / Nhạc / Meme)")
                self.btn_toggle_sug_bar.setText("💡")
                self.btn_toggle_sug_bar.setToolTip("💡 Gợi ý (Bật / tắt thanh gợi ý đạo cụ theo lời thoại)")
            elif w >= 580:
                self.chk_hover_preview.setText("Nghe thử")
                self.chk_hover_preview.setToolTip("🔊 Nghe thử: Tự động phát âm thanh khi rê chuột qua card SFX / Nhạc / Meme")
                self.btn_toggle_sug_bar.setText("💡 Gợi ý")
                self.btn_toggle_sug_bar.setToolTip("Bật / tắt thanh gợi ý đạo cụ theo lời thoại")

        filter_bar.resizeEvent = lambda event: (_update_filter_bar_responsive(), QFrame.resizeEvent(filter_bar, event))

        # =====================================================================
        # CỘT 3: LƯỚI ĐẠO CỤ ẢO HÓA (AssetGridView Model/View Akitt Pro UX)
        # =====================================================================
        from src.ui.lux.library import (
            AssetListModel, AssetFilterProxyModel, AssetGridView
        )
        from src.services.search_index import SearchIndex

        self.asset_grid = AssetGridView()
        self.asset_model = AssetListModel(self.all_assets, favs=self.favorites)
        self.search_index = SearchIndex(self.all_assets)
        self.asset_filter = AssetFilterProxyModel(self.search_index)
        self.asset_filter.setSourceModel(self.asset_model)
        self.asset_grid.setModel(self.asset_filter)
        self.asset_grid.set_sample_text_getter(lambda: self.txt_single_title.text().strip() if hasattr(self, "txt_single_title") else "")

        self.asset_grid.asset_selected.connect(self._on_lux_card_selected)
        self.asset_grid.asset_activated.connect(self._on_lux_card_activated)
        self.asset_grid.favorite_toggled.connect(self._on_favorite_toggled)
        self.asset_grid.fusion_paste_requested.connect(self._on_lux_fusion_paste)

        # Legacy backward-compatibility wrappers
        self.scroll_grid = self.asset_grid
        self.grid_container = QWidget(self)
        self.grid_container.hide()
        self.grid = QGridLayout(self.grid_container)

        main_sec_layout.addWidget(main_head)
        main_sec_layout.addWidget(filter_bar)
        self._setup_suggestion_bar()
        self.suggestion_bar.hide()
        main_sec_layout.addWidget(self.suggestion_bar)
        main_sec_layout.addWidget(self.asset_grid, stretch=1)

        # =====================================================================
        # CỘT 4: BẢNG TINH CHỈNH INSPECTOR (min 260px)
        # =====================================================================
        self.inspector = QFrame()
        self.inspector.setMinimumWidth(260)
        self.inspector.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-left: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        insp_layout = QVBoxLayout(self.inspector)
        insp_layout.setContentsMargins(0, 0, 0, 0)
        insp_layout.setSpacing(0)

        scroll_insp = QScrollArea()
        scroll_insp.setWidgetResizable(True)
        scroll_insp.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_insp.setStyleSheet("background: transparent; border: none;")
        self.insp_content = QWidget()
        self.insp_vbox = QVBoxLayout(self.insp_content)
        self.insp_vbox.setContentsMargins(16, 16, 16, 16)
        self.insp_vbox.setSpacing(14)

        # 1. Cụm Live Preview Header & Toggle Aspect Ratio (16:9 / 9:16)
        h_insp_header = QHBoxLayout()
        h_insp_header.setContentsMargins(0, 0, 0, 0)
        h_insp_header.setSpacing(8)

        lbl_live_title = QLabel("LIVE PREVIEW")
        lbl_live_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_MUTED};")
        h_insp_header.addWidget(lbl_live_title)
        h_insp_header.addStretch()

        self.inspector_aspect_btn = QPushButton("📐 16:9")
        self.inspector_aspect_btn.setCursor(Qt.PointingHandCursor)
        self.inspector_aspect_btn.setToolTip("Chuyển tỉ lệ khung hình: 16:9 (Ngang) ↔ 9:16 (Dọc TikTok/Shorts)")
        self.inspector_aspect_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
                padding: 3px 8px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.CYAN_HI};
            }}
        """)
        self.inspector_aspect_btn.clicked.connect(self._toggle_inspector_aspect_ratio)
        h_insp_header.addWidget(self.inspector_aspect_btn)
        self.insp_vbox.addLayout(h_insp_header)

        # 2. Viewport 1: Text Live Preview (Animated Canvas ~30 FPS)
        self.inspector_text_preview = QFrame()
        self.inspector_text_preview.setFixedHeight(130)
        self.inspector_text_preview.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
            }}
        """)
        t_prev_layout = QVBoxLayout(self.inspector_text_preview)
        t_prev_layout.setContentsMargins(0, 0, 0, 0)
        t_prev_layout.setSpacing(0)

        self.text_live_preview = TextLivePreviewWidget(self.inspector_text_preview)
        t_prev_layout.addWidget(self.text_live_preview)

        # Giữ lbl_insp_preview ẩn để đảm bảo tương thích ngược 100% với các test suite
        self.lbl_insp_preview = QLabel("PREVIEW", self.inspector_text_preview)
        self.lbl_insp_preview.setTextFormat(Qt.RichText)
        self.lbl_insp_preview.setAlignment(Qt.AlignCenter)
        self.lbl_insp_preview.setWordWrap(True)
        self.lbl_insp_preview.setStyleSheet("background: transparent; border: none; font-size: 16px; color: #FFFFFF;")
        self.lbl_insp_preview.hide()
        t_prev_layout.addWidget(self.lbl_insp_preview)
        self.insp_vbox.addWidget(self.inspector_text_preview)

        # 3. Viewport 2: LUT Split Comparison (Before / After Split Slider 0% - 100%)
        self.inspector_lut_viewport = QWidget()
        lut_v_layout = QVBoxLayout(self.inspector_lut_viewport)
        lut_v_layout.setContentsMargins(0, 0, 0, 0)
        lut_v_layout.setSpacing(6)

        self.lut_split_widget = LutSplitWidget(self.inspector_lut_viewport)
        lut_v_layout.addWidget(self.lut_split_widget)

        h_slider_row = QHBoxLayout()
        h_slider_row.setContentsMargins(2, 0, 2, 0)
        h_slider_row.setSpacing(8)

        lbl_split_title = QLabel("So sánh Before / After:")
        lbl_split_title.setStyleSheet(f"font-size: 11px; color: {ThemeColors.TEXT_MUTED};")
        self.lbl_split_pct = QLabel("50%")
        self.lbl_split_pct.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.CYAN_HI};")

        h_slider_row.addWidget(lbl_split_title)
        h_slider_row.addStretch()
        h_slider_row.addWidget(self.lbl_split_pct)
        lut_v_layout.addLayout(h_slider_row)

        self.lut_split_slider = QSlider(Qt.Horizontal, self.inspector_lut_viewport)
        self.lut_split_slider.setRange(0, 100)
        self.lut_split_slider.setValue(50)
        self.lut_split_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 6px;
                background: {ThemeColors.BG_CARD};
                border-radius: 3px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
            QSlider::sub-page:horizontal {{
                background: {ThemeColors.PRIMARY};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {ThemeColors.CYAN_HI};
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }}
        """)
        self.lut_split_slider.valueChanged.connect(self._on_lut_split_slider_changed)
        self.lut_split_widget.split_changed.connect(self.lut_split_slider.setValue)
        lut_v_layout.addWidget(self.lut_split_slider)
        self.insp_vbox.addWidget(self.inspector_lut_viewport)

        # 4. Viewport 3: SFX Waveform Visualizer & Playback Controller (Audio Inspector thật)
        self.inspector_sfx_viewport = QWidget()
        sfx_v_layout = QVBoxLayout(self.inspector_sfx_viewport)
        sfx_v_layout.setContentsMargins(0, 0, 0, 0)
        sfx_v_layout.setSpacing(6)

        self.audio_inspector_widget = AudioInspectorWidget(self.inspector_sfx_viewport)
        self.audio_inspector_widget.set_stop_hover_callback(
            lambda: self.asset_grid.hover_controller.stop()
            if hasattr(self, "asset_grid") and self.asset_grid and hasattr(self.asset_grid, "hover_controller") and self.asset_grid.hover_controller
            else None
        )
        if hasattr(self, "asset_grid") and self.asset_grid and hasattr(self.asset_grid, "hover_controller") and self.asset_grid.hover_controller:
            try:
                self.asset_grid.hover_controller.audio_preview_starting.connect(self.audio_inspector_widget.stop_playback)
            except Exception:
                pass

        sfx_v_layout.addWidget(self.audio_inspector_widget)

        # Aliases phục vụ tương thích ngược với callers / tests cũ
        self.sfx_play_btn = self.audio_inspector_widget.btn_play
        self.sfx_time_lbl = self.audio_inspector_widget.lbl_time
        self.sfx_hint_lbl = self.audio_inspector_widget.lbl_hint
        self.sfx_waveform_canvas = self.audio_inspector_widget.waveform_canvas

        self.insp_vbox.addWidget(self.inspector_sfx_viewport)

        # 5. Viewport 4: Transition Visual Loop Preview
        self.inspector_trans_viewport = QWidget()
        trans_v_layout = QVBoxLayout(self.inspector_trans_viewport)
        trans_v_layout.setContentsMargins(0, 0, 0, 0)
        trans_v_layout.setSpacing(6)

        self.trans_loop_widget = TransitionLoopWidget(self.inspector_trans_viewport)
        trans_v_layout.addWidget(self.trans_loop_widget)
        self.insp_vbox.addWidget(self.inspector_trans_viewport)

        # 6. Viewport 5: Icon/Meme Vector & Video Preview
        self.inspector_icon_viewport = QWidget()
        icon_v_layout = QVBoxLayout(self.inspector_icon_viewport)
        icon_v_layout.setContentsMargins(0, 0, 0, 0)
        icon_v_layout.setSpacing(6)

        self.icon_meme_widget = IconMemePreviewWidget(self.inspector_icon_viewport)
        icon_v_layout.addWidget(self.icon_meme_widget)

        # Nút Tách nền AI (Smart Cutout)
        self.btn_smart_cutout = QPushButton("✂️ Tách nền AI (Smart Cutout)", self.inspector_icon_viewport)
        self.btn_smart_cutout.setCursor(Qt.PointingHandCursor)
        self.btn_smart_cutout.setToolTip("Tự động bóc nền trắng/đen/phông xanh thành PNG trong suốt chuẩn DaVinci Resolve")
        self.btn_smart_cutout.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.CYAN};
                border-radius: 6px;
                color: {ThemeColors.CYAN_HI};
                font-size: 11px;
                font-weight: bold;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: {ThemeColors.CYAN};
                color: #05252c;
            }}
        """)
        self.btn_smart_cutout.clicked.connect(self._on_smart_cutout_clicked)
        icon_v_layout.addWidget(self.btn_smart_cutout)

        self.insp_vbox.addWidget(self.inspector_icon_viewport)

        # Mặc định ẩn các viewport không phải Text
        self.inspector_lut_viewport.hide()
        self.inspector_sfx_viewport.hide()
        self.inspector_trans_viewport.hide()
        self.inspector_icon_viewport.hide()

        # 2. Thông tin chi tiết
        self.lbl_insp_title = QLabel("<b>Alex Hormozi Pop</b>")
        self.lbl_insp_title.setWordWrap(True)
        self.lbl_insp_title.setStyleSheet(f"font-size: 15px; color: {ThemeColors.TEXT_PRIMARY};")
        self.lbl_insp_sub = QLabel("Kinetic bounce · Font: Arial Black")
        self.lbl_insp_sub.setWordWrap(True)
        self.lbl_insp_sub.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_MUTED};")
        self.insp_vbox.addWidget(self.lbl_insp_title)
        self.insp_vbox.addWidget(self.lbl_insp_sub)

        # Cụm Thao tác Bộ sưu tập
        coll_row = QHBoxLayout()
        coll_row.setContentsMargins(0, 2, 0, 2)
        coll_row.setSpacing(6)

        self.btn_insp_collection = QPushButton("📁 + Bộ sưu tập")
        self.btn_insp_collection.setCursor(Qt.PointingHandCursor)
        self.btn_insp_collection.setStyleSheet(f"""
            QPushButton {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                color: #a5b4fc;
                font-size: 11px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                border-color: #6366f1;
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                color: #ffffff;
            }}
        """)
        self.btn_insp_collection.clicked.connect(self._show_asset_collections_popup)
        coll_row.addWidget(self.btn_insp_collection)

        self.lbl_insp_collections = QLabel("")
        self.lbl_insp_collections.setWordWrap(True)
        self.lbl_insp_collections.setStyleSheet("font-size: 11px; color: #818cf8; font-weight: bold;")
        self.lbl_insp_collections.hide()
        coll_row.addWidget(self.lbl_insp_collections, 1)

        self.insp_vbox.addLayout(coll_row)

        # 3. Kéo thả Master Box
        self.preview_lbl = DraggableAssetLabel()
        self.preview_lbl.setFixedHeight(44)
        self.preview_lbl.setText("🖱️ Giữ chuột và kéo vào Timeline")
        self.preview_lbl.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px dashed {ThemeColors.PRIMARY};
            border-radius: 4px;
            color: #f59e0b;
            font-size: 11px;
        """)
        self.preview_lbl.preset_getter = lambda: self.selected_asset
        self.preview_lbl.sample_text_getter = lambda: self.txt_single_title.text().strip() or "ResolveFlow Title"
        self.insp_vbox.addWidget(self.preview_lbl)

        # 4. Thông số điều chỉnh
        self.grp_params = QWidget()
        p_layout = QVBoxLayout(self.grp_params)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setSpacing(12)

        # Chữ mẫu hiển thị & Thời lượng
        self.grp_sample_text = QWidget()
        sample_vbox = QVBoxLayout(self.grp_sample_text)
        sample_vbox.setContentsMargins(0, 0, 0, 0)
        sample_vbox.setSpacing(4)
        sample_vbox.addWidget(QLabel("Chữ mẫu hiển thị:"))
        self._user_has_custom_title = False
        self.txt_single_title = QLineEdit("ResolveFlow Studio")
        self.txt_single_title.textChanged.connect(self._on_sample_text_changed)
        self.txt_single_title.textEdited.connect(self._on_sample_text_edited)
        self.txt_single_title.setStyleSheet(f"""
            background: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 6px;
            color: {ThemeColors.TEXT_PRIMARY};
            font-size: 12px;
        """)
        sample_vbox.addWidget(self.txt_single_title)
        p_layout.addWidget(self.grp_sample_text)

        self.grp_duration = QWidget()
        dur_vbox = QVBoxLayout(self.grp_duration)
        dur_vbox.setContentsMargins(0, 0, 0, 0)
        dur_vbox.setSpacing(4)
        h_dur = QHBoxLayout()
        h_dur.addWidget(QLabel("Thời lượng:"))
        self.lbl_dur_val = QLabel("4.0s")
        self.lbl_dur_val.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-weight: bold; font-size: 11px;")
        h_dur.addStretch()
        h_dur.addWidget(self.lbl_dur_val)
        dur_vbox.addLayout(h_dur)

        self.slide_title_dur = QSlider(Qt.Horizontal)
        self.slide_title_dur.setRange(10, 100)
        self.slide_title_dur.setValue(40)
        self.slide_title_dur.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {ThemeColors.BG_CARD};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ThemeColors.PRIMARY};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ThemeColors.CYAN_HI};
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }}
        """)
        self.slide_title_dur.valueChanged.connect(lambda v: self.lbl_dur_val.setText(f"{v/10.0:.1f}s"))
        dur_vbox.addWidget(self.slide_title_dur)
        p_layout.addWidget(self.grp_duration)

        # A. Typography controls
        self.grp_typography = QWidget()
        t_vbox = QVBoxLayout(self.grp_typography)
        t_vbox.setContentsMargins(0, 0, 0, 0)
        t_vbox.setSpacing(6)

        lbl_typo_title = QLabel("KIỂU CHỮ")
        lbl_typo_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_MUTED};")
        t_vbox.addWidget(lbl_typo_title)

        t_vbox.addWidget(QLabel("Phông chữ:"))
        self.combo_font = QComboBox()
        self.combo_font.addItems([
            "Montserrat", "Arial Black", "Bangers", "Be Vietnam Pro",
            "Arial", "Segoe UI", "Georgia", "Consolas", "Impact"
        ])
        self.combo_font.setStyleSheet(f"""
            QComboBox {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 5px 8px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11px;
            }}
            QComboBox:hover {{
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        self.combo_font.currentTextChanged.connect(self._on_font_changed)
        t_vbox.addWidget(self.combo_font)

        h_sz = QHBoxLayout()
        h_sz.addWidget(QLabel("Cỡ chữ:"))
        self.lbl_font_size_val = QLabel("72 px")
        self.lbl_font_size_val.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-size: 11px; font-weight: bold;")
        h_sz.addStretch()
        h_sz.addWidget(self.lbl_font_size_val)
        t_vbox.addLayout(h_sz)

        self.slide_font_size = QSlider(Qt.Horizontal)
        self.slide_font_size.setRange(24, 140)
        self.slide_font_size.setValue(72)
        self.slide_font_size.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {ThemeColors.BG_CARD};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ThemeColors.PRIMARY};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ThemeColors.CYAN_HI};
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }}
        """)
        self.slide_font_size.valueChanged.connect(self._on_font_size_changed)
        t_vbox.addWidget(self.slide_font_size)

        t_vbox.addWidget(QLabel("Độ đậm:"))
        seg_w_box = QWidget()
        seg_w_layout = QHBoxLayout(seg_w_box)
        seg_w_layout.setContentsMargins(0, 0, 0, 0)
        seg_w_layout.setSpacing(4)

        self.weight_buttons = {
            "regular": QPushButton("Thường"),
            "bold": QPushButton("Đậm"),
            "extrabold": QPushButton("Rất đậm")
        }
        for w_k, w_b in self.weight_buttons.items():
            w_b.setCheckable(True)
            w_b.setCursor(Qt.PointingHandCursor)
            w_b.setFixedHeight(26)
            w_b.clicked.connect(lambda _, k=w_k: self._on_weight_button_clicked(k))
            seg_w_layout.addWidget(w_b)
        self.weight_buttons["bold"].setChecked(True)
        self._update_weight_button_styles()
        t_vbox.addWidget(seg_w_box)

        h_sw = QHBoxLayout()
        h_sw.addWidget(QLabel("Độ dày viền:"))
        self.lbl_stroke_width_val = QLabel("0.15")
        self.lbl_stroke_width_val.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-size: 11px; font-weight: bold;")
        h_sw.addStretch()
        h_sw.addWidget(self.lbl_stroke_width_val)
        t_vbox.addLayout(h_sw)

        self.slide_stroke_width = QSlider(Qt.Horizontal)
        self.slide_stroke_width.setRange(0, 50)
        self.slide_stroke_width.setValue(15)
        self.slide_stroke_width.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: 4px;
                background: {ThemeColors.BG_CARD};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ThemeColors.PRIMARY};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ThemeColors.CYAN_HI};
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }}
        """)
        self.slide_stroke_width.valueChanged.connect(self._on_stroke_width_changed)
        t_vbox.addWidget(self.slide_stroke_width)

        p_layout.addWidget(self.grp_typography)

        # B. Color Swatches
        self.grp_swatches = QWidget()
        sw_vbox = QVBoxLayout(self.grp_swatches)
        sw_vbox.setContentsMargins(0, 0, 0, 0)
        sw_vbox.setSpacing(6)
        lbl_swatches_title = QLabel("MÀU SẮC")
        lbl_swatches_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_MUTED};")
        sw_vbox.addWidget(lbl_swatches_title)

        self.swatch_colors = {
            "text": "#FFFFFF",
            "highlight": "#FACC15",
            "stroke": "#000000",
            "box_glow": "#EF4444"
        }

        sw_grid = QGridLayout()
        sw_grid.setContentsMargins(0, 0, 0, 0)
        sw_grid.setSpacing(6)

        self.btn_color_text = QPushButton("Chữ")
        self.btn_color_text.setCursor(Qt.PointingHandCursor)
        self.btn_color_text.setToolTip("Chọn màu chữ chính")
        self.btn_color_text.clicked.connect(lambda: self._pick_color("text"))

        self.btn_color_highlight = QPushButton("Highlight")
        self.btn_color_highlight.setCursor(Qt.PointingHandCursor)
        self.btn_color_highlight.setToolTip("Chọn màu highlight điểm nhấn")
        self.btn_color_highlight.clicked.connect(lambda: self._pick_color("highlight"))

        self.btn_color_stroke = QPushButton("Viền")
        self.btn_color_stroke.setCursor(Qt.PointingHandCursor)
        self.btn_color_stroke.setToolTip("Chọn màu viền chữ")
        self.btn_color_stroke.clicked.connect(lambda: self._pick_color("stroke"))

        self.btn_color_box_glow = QPushButton("Hộp/Glow")
        self.btn_color_box_glow.setCursor(Qt.PointingHandCursor)
        self.btn_color_box_glow.setToolTip("Chọn màu hộp nền hoặc viền phát sáng")
        self.btn_color_box_glow.clicked.connect(lambda: self._pick_color("box_glow"))

        sw_grid.addWidget(self.btn_color_text, 0, 0)
        sw_grid.addWidget(self.btn_color_highlight, 0, 1)
        sw_grid.addWidget(self.btn_color_stroke, 1, 0)
        sw_grid.addWidget(self.btn_color_box_glow, 1, 1)
        sw_vbox.addLayout(sw_grid)

        for k, c in self.swatch_colors.items():
            self._set_swatch_color(k, c, refresh=False)

        p_layout.addWidget(self.grp_swatches)

        # C. Motion & Curves
        self.grp_motion = QWidget()
        m_vbox = QVBoxLayout(self.grp_motion)
        m_vbox.setContentsMargins(0, 0, 0, 0)
        m_vbox.setSpacing(6)

        lbl_motion_title = QLabel("CHUYỂN ĐỘNG & ĐƯỜNG CONG")
        lbl_motion_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_MUTED};")
        m_vbox.addWidget(lbl_motion_title)

        m_vbox.addWidget(QLabel("Hiệu ứng nhảy chữ:"))
        anim_container = QWidget()
        anim_layout = QGridLayout(anim_container)
        anim_layout.setContentsMargins(0, 0, 0, 0)
        anim_layout.setSpacing(4)

        self.anim_chips = {
            "pop": QPushButton("Pop"),
            "bounce": QPushButton("Bounce"),
            "typewriter": QPushButton("Gõ chữ"),
            "slide": QPushButton("Trượt"),
            "box_highlight": QPushButton("Hộp nền"),
            "glow": QPushButton("Phát sáng"),
            "glitch": QPushButton("Nhiễu RGB"),
            "paper": QPushButton("Xé giấy"),
            "static": QPushButton("Đứng yên")
        }
        r_i, c_i = 0, 0
        for ak, abtn in self.anim_chips.items():
            abtn.setCheckable(True)
            abtn.setCursor(Qt.PointingHandCursor)
            abtn.setFixedHeight(24)
            abtn.clicked.connect(lambda _, k=ak: self._on_anim_chip_clicked(k))
            anim_layout.addWidget(abtn, r_i, c_i)
            c_i += 1
            if c_i > 2:
                c_i = 0
                r_i += 1
        self.anim_chips["pop"].setChecked(True)
        self._update_anim_chip_styles()
        m_vbox.addWidget(anim_container)

        m_vbox.addWidget(QLabel("Đường cong thời gian:"))
        curve_container = QWidget()
        curve_layout = QHBoxLayout(curve_container)
        curve_layout.setContentsMargins(0, 0, 0, 0)
        curve_layout.setSpacing(4)

        self.curve_chips = {
            "spring": QPushButton("Spring"),
            "ease": QPushButton("Ease in-out"),
            "linear": QPushButton("Tuyến tính")
        }
        for ck, cbtn in self.curve_chips.items():
            cbtn.setCheckable(True)
            cbtn.setCursor(Qt.PointingHandCursor)
            cbtn.setFixedHeight(24)
            cbtn.clicked.connect(lambda _, k=ck: self._on_curve_chip_clicked(k))
            curve_layout.addWidget(cbtn)
        self.curve_chips["spring"].setChecked(True)
        self._update_curve_chip_styles()
        m_vbox.addWidget(curve_container)

        p_layout.addWidget(self.grp_motion)

        # D. Destination Track
        self.grp_track = QWidget()
        tr_vbox = QVBoxLayout(self.grp_track)
        tr_vbox.setContentsMargins(0, 0, 0, 0)
        tr_vbox.setSpacing(4)

        tr_vbox.addWidget(QLabel("Track đích:"))
        self.combo_target_track = QComboBox()
        self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 3 (V3)", "Video Track 1 (V1)"])
        self.combo_target_track.setStyleSheet(f"""
            QComboBox {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 5px 8px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11px;
            }}
            QComboBox:hover {{
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        tr_vbox.addWidget(self.combo_target_track)

        self.lbl_track_hint = QLabel("Đề xuất: Chèn lên V2/V3 để tránh đè video chính V1.")
        self.lbl_track_hint.setWordWrap(True)
        self.lbl_track_hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 10px;")
        tr_vbox.addWidget(self.lbl_track_hint)

        p_layout.addWidget(self.grp_track)

        # E. Vị trí trên clip đang chọn (DVC Pro style - Đầu clip / Cuối clip)
        self.grp_trans_pos = QWidget()
        tp_vbox = QVBoxLayout(self.grp_trans_pos)
        tp_vbox.setContentsMargins(0, 0, 0, 0)
        tp_vbox.setSpacing(6)

        lbl_tp_title = QLabel("VỊ TRÍ ÁP DỤNG TRÊN CLIP")
        lbl_tp_title.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_MUTED};")
        tp_vbox.addWidget(lbl_tp_title)

        seg_tp_box = QWidget()
        seg_tp_layout = QHBoxLayout(seg_tp_box)
        seg_tp_layout.setContentsMargins(0, 0, 0, 0)
        seg_tp_layout.setSpacing(6)

        self.btn_trans_head = QPushButton("▶ Đầu clip (In)")
        self.btn_trans_head.setCheckable(True)
        self.btn_trans_head.setCursor(Qt.PointingHandCursor)
        self.btn_trans_head.setFixedHeight(28)
        self.btn_trans_head.clicked.connect(lambda: self._set_trans_alignment("head"))

        self.btn_trans_tail = QPushButton("◀ Cuối clip (Out)")
        self.btn_trans_tail.setCheckable(True)
        self.btn_trans_tail.setCursor(Qt.PointingHandCursor)
        self.btn_trans_tail.setFixedHeight(28)
        self.btn_trans_tail.clicked.connect(lambda: self._set_trans_alignment("tail"))

        seg_tp_layout.addWidget(self.btn_trans_head)
        seg_tp_layout.addWidget(self.btn_trans_tail)
        tp_vbox.addWidget(seg_tp_box)

        self.trans_alignment = "tail"
        self.trans_frames = 12
        self.btn_trans_tail.setChecked(True)
        self._update_trans_alignment_styles()

        h_tf = QHBoxLayout()
        h_tf.addWidget(QLabel("Thời lượng chuyển cảnh:"))
        self.lbl_trans_frames_val = QLabel("12 frames")
        self.lbl_trans_frames_val.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-size: 11px; font-weight: bold;")
        h_tf.addStretch()
        h_tf.addWidget(self.lbl_trans_frames_val)
        tp_vbox.addLayout(h_tf)

        self.slide_trans_frames = QSlider(Qt.Horizontal)
        self.slide_trans_frames.setRange(4, 48)
        self.slide_trans_frames.setValue(12)
        self.slide_trans_frames.setStyleSheet(f"""
            QSlider::groove:horizontal {{ height: 4px; background: {ThemeColors.BG_CARD}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {ThemeColors.PRIMARY}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {ThemeColors.CYAN_HI}; width: 12px; margin: -4px 0; border-radius: 6px; }}
        """)
        self.slide_trans_frames.valueChanged.connect(self._on_trans_frames_changed)
        tp_vbox.addWidget(self.slide_trans_frames)

        p_layout.addWidget(self.grp_trans_pos)

        self.insp_vbox.addWidget(self.grp_params)
        self.insp_vbox.addStretch()
        scroll_insp.setWidget(self.insp_content)

        # 5. Cụm nút hành động chính ở dưới cùng Inspector (.insp-acts)
        insp_acts = QWidget()
        insp_acts.setStyleSheet(f"border-top: 1px solid {ThemeColors.BORDER_DEFAULT};")
        acts_vbox = QVBoxLayout(insp_acts)
        acts_vbox.setContentsMargins(16, 12, 16, 14)
        acts_vbox.setSpacing(8)

        # Thông báo Toast
        self.lbl_toast = QLabel("")
        self.lbl_toast.setAlignment(Qt.AlignCenter)
        self.lbl_toast.setWordWrap(True)
        self.lbl_toast.setStyleSheet(f"""
            QLabel {{
                background-color: {ThemeColors.BG_CARD_ACTIVE};
                color: {ThemeColors.CYAN_HI};
                border: 1px solid {ThemeColors.CYAN};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: bold;
            }}
        """)
        self.lbl_toast.hide()
        acts_vbox.addWidget(self.lbl_toast)

        self.btn_insert_title_playhead = QPushButton("🚀 Chèn Text cơ bản")
        self.btn_insert_title_playhead.setToolTip(
            "Chèn tiêu đề cơ bản lên Timeline (phông, cỡ, kiểu, màu). Lưu ý: Timeline comp chưa hỗ trợ viền (stroke) và đường cong (curve) tùy biến; để giữ trọn vẹn mọi hiệu ứng, vui lòng dùng 'Sao chép mã Fusion'."
        )
        self.btn_insert_playhead = self.btn_insert_title_playhead
        self.btn_insert_title_playhead.setCursor(Qt.PointingHandCursor)
        self.btn_insert_title_playhead.setStyleSheet(f"""
            QPushButton {{
                background-color: #d94f3d;
                color: #ffffff;
                font-weight: bold;
                font-size: 12.5px;
                padding: 9px;
                border-radius: 4px;
                border: 1px solid #e86352;
            }}
            QPushButton:hover {{
                background-color: #e86352;
                border-color: #f28273;
            }}
        """)
        self.btn_insert_title_playhead.clicked.connect(self._insert_at_playhead)
        acts_vbox.addWidget(self.btn_insert_title_playhead)

        h_acts_sub = QHBoxLayout()
        h_acts_sub.setSpacing(6)

        self.btn_drag_davinci = DraggableActionButton("🖐️ Kéo DaVinci", lambda: self._create_drag_mime_data())
        self.btn_drag_davinci.setCursor(Qt.OpenHandCursor)
        self.btn_drag_davinci.setToolTip("Giữ chuột và kéo sang Timeline DaVinci Resolve!")
        self.btn_drag_davinci.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                color: {ThemeColors.TEXT_PRIMARY};
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.PRIMARY};
                color: #ffffff;
            }}
        """)
        self.btn_drag_davinci.clicked.connect(lambda: self._show_toast("Giữ chuột và kéo sang Timeline DaVinci Resolve!"))

        self.btn_copy_fusion = QPushButton("📋 Copy Fusion")
        self.btn_copy_fusion.setCursor(Qt.PointingHandCursor)
        self.btn_copy_fusion.setToolTip("Sao chép mã Fusion node vào bộ nhớ đệm (Clipboard)")
        self.btn_copy_fusion.clicked.connect(self._on_copy_fusion_clicked)
        self.btn_copy_fusion.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                color: {ThemeColors.TEXT_PRIMARY};
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.PRIMARY};
                color: #ffffff;
            }}
        """)

        self.btn_install_presets = QPushButton("📥 Cài Đặt")
        self.btn_install_presets.setCursor(Qt.PointingHandCursor)
        self.btn_install_presets.clicked.connect(self._install_to_fusion)
        self.btn_install_presets.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                color: {ThemeColors.TEXT_PRIMARY};
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {ThemeColors.BG_CARD_ACTIVE};
                border-color: {ThemeColors.PRIMARY};
                color: #ffffff;
            }}
        """)

        self.btn_capture_playhead = QPushButton("📥 Hút Preset")
        self.btn_capture_playhead.setCursor(Qt.PointingHandCursor)
        self.btn_capture_playhead.setToolTip("Hút style Text+/Fusion trực tiếp từ clip tại Playhead thành Preset vĩnh viễn")
        self.btn_capture_playhead.clicked.connect(self._capture_preset_from_playhead)
        self.btn_capture_playhead.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.CYAN};
                color: {ThemeColors.CYAN_HI};
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {ThemeColors.CYAN};
                color: #05252c;
            }}
        """)

        self.btn_fast_paste_fusion = QPushButton("⚡ Dán Fusion")
        self.btn_fast_paste_fusion.setCursor(Qt.PointingHandCursor)
        self.btn_fast_paste_fusion.setToolTip("Dán nhanh chùm node thẳng vào trang Fusion qua Ctrl+V tự động (FAST-FUSION)")
        self.btn_fast_paste_fusion.clicked.connect(self._on_fast_paste_fusion_clicked)
        self.btn_fast_paste_fusion.setStyleSheet("""
            QPushButton {
                background: #0d2825;
                border: 1px solid #1a4d46;
                color: #3dcee1;
                padding: 6px 8px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #1a4d46;
                border-color: #3dcee1;
                color: #ffffff;
            }
        """)

        # Cụm nút phụ: Chia 2 hàng tinh gọn, hiển thị trọn vẹn icon và nhãn chữ
        grid_acts = QGridLayout()
        grid_acts.setContentsMargins(0, 0, 0, 0)
        grid_acts.setSpacing(6)

        # Hàng 1: 2 nút hành động nhanh chính
        grid_acts.addWidget(self.btn_fast_paste_fusion, 0, 0)
        grid_acts.addWidget(self.btn_drag_davinci, 0, 1)

        # Hàng 2: 3 nút tiện ích bổ trợ
        grid_acts.addWidget(self.btn_copy_fusion, 1, 0)
        grid_acts.addWidget(self.btn_install_presets, 1, 1)
        grid_acts.addWidget(self.btn_capture_playhead, 1, 2)
        acts_vbox.addLayout(grid_acts)

        insp_layout.addWidget(scroll_insp, stretch=1)

        insp_layout.addWidget(insp_acts)


        # Ghép Cột 1 (Rail 78px cố định) và QSplitter chứa Cột 2, 3, 4
        self.assets_splitter = QSplitter(Qt.Horizontal)
        self.assets_splitter.addWidget(self.cats)
        self.assets_splitter.addWidget(self.main_section)
        self.assets_splitter.addWidget(self.inspector)
        self.assets_splitter.setStretchFactor(0, 0)
        self.assets_splitter.setStretchFactor(1, 1)
        self.assets_splitter.setStretchFactor(2, 0)
        self.assets_splitter.setSizes([190, 580, 310])
        self.assets_splitter.setCollapsible(0, True)
        self.assets_splitter.setCollapsible(1, False)
        self.assets_splitter.setCollapsible(2, True)

        from PySide6.QtWidgets import QStackedWidget
        from src.ui.tabs.tab_asset_hub import AssetHubWidget

        self.asset_hub_widget = AssetHubWidget(indexer=self.indexer, parent=self)
        self.asset_hub_widget.assets_changed.connect(self._rescan_assets)

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.assets_splitter)   # Index 0: 4-column browser
        self.content_stack.addWidget(self.asset_hub_widget)  # Index 1: Asset Hub

        main_layout.addWidget(self.rail)
        main_layout.addWidget(self.content_stack, stretch=1)

    def _toggle_cats_visibility(self):
        visible = not self.cats.isVisible()
        self.cats.setVisible(visible)
        if visible:
            self.btn_toggle_cats.setText("☰ Nhóm")
            self.btn_toggle_cats.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                    padding: 5px 10px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.BORDER_HOVER};
                    color: {ThemeColors.TEXT_PRIMARY};
                }}
            """)
        else:
            self.btn_toggle_cats.setText("☰ Hiện nhóm")
            self.btn_toggle_cats.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD_ACTIVE};
                    border: 1px solid {ThemeColors.PRIMARY};
                    border-radius: 6px;
                    padding: 5px 10px;
                    color: {ThemeColors.PRIMARY};
                    font-size: 11px;
                    font-weight: bold;
                }}
            """)

    def _toggle_inspector_visibility(self):
        visible = not self.inspector.isVisible()
        self.inspector.setVisible(visible)
        if visible:
            self.btn_toggle_inspector.setText("◫ Chi tiết")
            self.btn_toggle_inspector.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                    padding: 5px 10px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.PRIMARY};
                    color: {ThemeColors.TEXT_PRIMARY};
                }}
            """)
        else:
            self.btn_toggle_inspector.setText("◫ Hiện chi tiết")
            self.btn_toggle_inspector.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD_ACTIVE};
                    border: 1px solid {ThemeColors.PRIMARY};
                    border-radius: 6px;
                    padding: 5px 10px;
                    color: {ThemeColors.PRIMARY};
                    font-size: 11px;
                    font-weight: bold;
                }}
            """)

    # =========================================================================
    # 7. LOGIC ĐIỀU HƯỚNG VÀ LỌC DANH MỤC
    # =========================================================================
    def _filter_by_rail(self, tab_id: str):
        self._stop_inspector_sfx_play()
        self.icon_meme_widget.stop_playback()
        self.current_rail_tab = tab_id
        for tid, btn in self.rail_btns.items():
            btn.setChecked(tid == tab_id)

        if tab_id == "hub":
            if hasattr(self, "content_stack"):
                self.content_stack.setCurrentIndex(1)
                if hasattr(self, "asset_hub_widget"):
                    self.asset_hub_widget.refresh_cards()
            return

        if hasattr(self, "content_stack"):
            self.content_stack.setCurrentIndex(0)

        # Cập nhật danh mục con ở Cột 2 (đã bao gồm kích hoạt danh mục con và _refresh_grid)
        self._rebuild_categories_column()

        from src.ui.lux.library.asset_model import AssetObjRole
        index = self.asset_filter.index(0, 0)
        self.selected_asset = index.data(AssetObjRole) if index.isValid() else None
        self.asset_grid.setCurrentIndex(index)
        if self.selected_asset is not None:
            self.inspector.setEnabled(True)
            self._update_inspector_details()
        else:
            if not hasattr(self, "_empty_inspector_label"):
                self._empty_inspector_label = QLabel("Chưa có mẫu trong danh mục này.\nChọn danh mục khác hoặc xóa bộ lọc.")
                self._empty_inspector_label.setWordWrap(True)
                self.insp_vbox.insertWidget(0, self._empty_inspector_label)
            if not getattr(self, "_inspector_hidden_widgets", None):
                self._inspector_hidden_widgets = []
                for i in range(self.insp_vbox.count()):
                    widget = self.insp_vbox.itemAt(i).widget()
                    if widget is not None and widget is not self._empty_inspector_label:
                        self._inspector_hidden_widgets.append((widget, widget.isHidden()))
                        widget.hide()
            self._empty_inspector_label.show()


    def _rebuild_categories_column(self):
        # Dọn sạch các nút cũ
        while self.cats_vbox.count():
            item = self.cats_vbox.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        tab = self.current_rail_tab

        # 1. Trường hợp Tab "Của tôi / Yêu thích" (Custom Collections)
        if tab == "fav":
            self.lbl_cats_head.setText("Bộ sưu tập & Đã lưu")

            # A. Nút tất cả yêu thích
            fav_count = len(self.favorites)
            btn_fav_all = QPushButton(f"  ⭐ Tất cả yêu thích ({fav_count})")
            btn_fav_all.setProperty("sub_cat_id", "all")
            btn_fav_all.setCheckable(True)
            btn_fav_all.setFixedHeight(32)
            btn_fav_all.setCursor(Qt.PointingHandCursor)
            btn_fav_all.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 12px;
                    text-align: left;
                    padding-left: 6px;
                }}
                QPushButton:hover {{
                    background: {ThemeColors.BG_CARD};
                    color: {ThemeColors.TEXT_PRIMARY};
                }}
                QPushButton:checked {{
                    background: {ThemeColors.BG_CARD_ACTIVE};
                    color: #fbbf24;
                    font-weight: bold;
                }}
            """)
            btn_fav_all.clicked.connect(lambda: self._on_sub_category_clicked("all"))
            self.cats_vbox.addWidget(btn_fav_all)

            # B. Tiêu đề mục: BỘ SƯU TẬP CỦA TÔI
            lbl_coll_hdr = QLabel("📁 BỘ SƯU TẬP CỦA TÔI")
            lbl_coll_hdr.setStyleSheet(f"""
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
                color: #a5b4fc;
                padding-top: 10px;
                padding-bottom: 2px;
                padding-left: 6px;
            """)
            self.cats_vbox.addWidget(lbl_coll_hdr)

            # C. Danh sách các bộ sưu tập tùy chỉnh
            colls = CollectionsManager.get_collection_names()
            for cname in colls:
                items_in_coll = CollectionsManager.get_collection_assets(cname)
                btn_c = QPushButton(f"  📁 {cname} ({len(items_in_coll)})")
                btn_c.setProperty("sub_cat_id", f"coll:{cname}")
                btn_c.setCheckable(True)
                btn_c.setFixedHeight(32)
                btn_c.setCursor(Qt.PointingHandCursor)
                btn_c.setContextMenuPolicy(Qt.CustomContextMenu)
                btn_c.customContextMenuRequested.connect(lambda pos, name=cname, b=btn_c: self._show_collection_context_menu(pos, name, b))
                btn_c.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        border: none;
                        border-radius: 6px;
                        color: {ThemeColors.TEXT_MUTED};
                        font-size: 12px;
                        text-align: left;
                        padding-left: 6px;
                    }}
                    QPushButton:hover {{
                        background: {ThemeColors.BG_CARD};
                        color: {ThemeColors.TEXT_PRIMARY};
                    }}
                    QPushButton:checked {{
                        background: {ThemeColors.BG_CARD_ACTIVE};
                        color: #818cf8;
                        font-weight: bold;
                    }}
                """)
                btn_c.clicked.connect(lambda _, x=f"coll:{cname}": self._on_sub_category_clicked(x))
                self.cats_vbox.addWidget(btn_c)

            # D. Nút Tạo Bộ Sưu Tập Mới
            btn_new_coll = QPushButton("  ➕ Tạo Bộ Sưu Tập...")
            btn_new_coll.setFixedHeight(30)
            btn_new_coll.setCursor(Qt.PointingHandCursor)
            btn_new_coll.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px dashed {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 11px;
                    text-align: left;
                    padding-left: 8px;
                    margin-top: 6px;
                }}
                QPushButton:hover {{
                    border-color: #6366f1;
                    color: #a5b4fc;
                    background: {ThemeColors.BG_CARD};
                }}
            """)
            btn_new_coll.clicked.connect(self._prompt_create_collection)
            self.cats_vbox.addWidget(btn_new_coll)

            self.cats_vbox.addStretch()
            # Kích hoạt trạng thái đang chọn hoặc mặc định 'all'
            active_target = self.current_sub_cat if (self.current_sub_cat.startswith("coll:") or self.current_sub_cat == "all") else "all"
            self._on_sub_category_clicked(active_target)
            return

        # 2. Trường hợp các Tab thông thường (text, lut, trans, icon, overlay, meme, sfx, bgm)
        headers_map = {
            "text": "Mẫu chữ Text+",
            "lut": "Bộ lọc màu LUT",
            "trans": "Chuyển cảnh",
            "icon": "Sticker biểu tượng",
            "overlay": "Hiệu ứng lớp phủ",
            "meme": "Meme thịnh hành",
            "sfx": "Âm thanh SFX",
            "bgm": "🎵 Nhạc Nền (BGM)",
        }
        self.lbl_cats_head.setText(headers_map.get(tab, "Danh mục"))

        # Xác định nhóm con mặc định
        sub_cats = [("all", "Tất cả")]
        if tab == "text":
            sub_cats.extend([
                ("kinetic", "Kinetic / Nhảy chữ"),
                ("highlighter_paper", "Bút dạ & Giấy"),
                ("glitch_cyber", "Neon & Glitch"),
                ("retro_film", "Retro thập niên 90"),
                ("clean_minimal", "Tối giản")
            ])
        elif tab == "lut":
            sub_cats.extend([
                ("natural", "Đời thường & Vlog"),
                ("cinema", "Điện ảnh Hollywood"),
                ("style", "Phong cách & Anime")
            ])
        elif tab == "trans":
            sub_cats.extend([
                ("motion", "Lia máy nhanh"),
                ("zoom", "Phóng to nhòe"),
                ("glitch", "Nhiễu sóng số")
            ])
        elif tab == "icon":
            sub_cats.extend([
                ("arrow", "Mũi tên & Khoanh"),
                ("place", "Địa điểm & Cờ"),
                ("emo", "Cảm xúc & Ngôi sao"),
                ("alert", "Cảnh báo & Đánh dấu"),
                ("talk", "Hội thoại & Kênh")
            ])
        elif tab == "sfx":
            sub_cats.extend([
                ("chuyen_canh", "💨 Chuyển cảnh (Whoosh)"),
                ("giat_minh", "💥 Giật mình / Cú sốc"),
                ("hai_huoc", "😂 Hài hước / Troll"),
                ("diem_nhan", "✨ Điểm nhấn (Pop/Ding)"),
                ("cong_nghe", "⚡ Kỹ thuật / UI"),
                ("accent", "🔊 Nhấn mạnh khác")
            ])
        elif tab == "bgm":
            sub_cats.extend([
                ("chill_vlog", "🍃 Chill Vlog / Lofi"),
                ("upbeat_trend", "🔥 Upbeat / TikTok Trend"),
                ("cinematic", "🎬 Cinematic Điện ảnh"),
                ("funny", "🎭 Hài hước Meme")
            ])
        elif tab == "overlay":
            sub_cats.extend([
                ("cinematic", "Điện ảnh"),
                ("green_screen", "Phông xanh")
            ])
        elif tab == "meme":
            sub_cats.extend([
                ("vietnam_viral", "🇻🇳 Viral Việt Nam"),
                ("reaction", "🤦 Cảm xúc / Bất lực"),
                ("cham_hoi", "❓ Ngơ ngác / Chấm hỏi"),
                ("cay_cu", "🤬 Cay cú / Đập bàn"),
                ("trending", "🔥 Thịnh hành")
            ])

        known_subs = {cid for cid, _ in sub_cats}
        sub_counts: Dict[str, int] = {}
        tab_total_count = 0
        discovered_packs: List[str] = []
        seen_packs = set()
        pack_counts: Dict[str, int] = {}

        # Quét 1 vòng O(N) duy nhất qua self.all_assets để tính số lượng sub-category và pack
        for asset in self.all_assets:
            p_name = getattr(asset, "pack_name", None) or (asset.get("pack_name") if isinstance(asset, dict) else "")
            if p_name:
                pack_counts[p_name] = pack_counts.get(p_name, 0) + 1

            a_tab = self._get_asset_tab(asset)
            if a_tab == tab:
                tab_total_count += 1
                sub_label = self._get_asset_sub(asset)
                if sub_label:
                    sub_counts[sub_label] = sub_counts.get(sub_label, 0) + 1
                    if sub_label not in known_subs and sub_label not in ("all", "Tất cả", "default"):
                        sub_cats.append((sub_label, sub_label))
                        known_subs.add(sub_label)
                if p_name and p_name not in ("Mặc định", "all", "") and p_name not in seen_packs:
                    seen_packs.add(p_name)
                    discovered_packs.append(p_name)

        def _get_sub_count(cid_target):
            if cid_target == "all":
                return tab_total_count
            return sub_counts.get(cid_target, 0)

        # Lọc danh mục: giữ "all", các nhóm có item, hoặc nhóm mặc định hệ thống
        visible_sub_cats = []
        base_sub_ids = {c[0] for c in sub_cats[:4]}
        for cid, cname in sub_cats:
            cnt = _get_sub_count(cid)
            # Chỉ ẩn những mục rác 0 item nếu không phải là nhóm cơ bản
            if cid != "all" and cnt == 0 and cid not in base_sub_ids:
                continue
            visible_sub_cats.append((cid, cname, cnt))

        for cid, cname, count in visible_sub_cats:
            label_text = f"  {cname} ({count})" if count > 0 and cid != "all" else f"  {cname}"
            btn = QPushButton(label_text)
            btn.setProperty("sub_cat_id", cid)
            btn.setCheckable(True)
            btn.setFixedHeight(32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 12px;
                    text-align: left;
                    padding-left: 6px;
                }}
                QPushButton:hover {{
                    background: {ThemeColors.BG_CARD};
                    color: {ThemeColors.TEXT_PRIMARY};
                }}
                QPushButton:checked {{
                    background: {ThemeColors.BG_CARD_ACTIVE};
                    color: {ThemeColors.TEXT_PRIMARY};
                    font-weight: bold;
                }}
            """)
            btn.clicked.connect(lambda _, x=cid: self._on_sub_category_clicked(x))

            # Cho phép click chuột phải để Đổi tên / Xóa danh mục
            if cid != "all":
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, c=cid, n=cname, b=btn, cnt=count: self._show_category_context_menu(pos, c, n, b, cnt)
                )

            self.cats_vbox.addWidget(btn)

        # Ghi nhận thêm từ PackManager (không quét lại đĩa cứng verify_files)
        for pack_id, p_info in PackManager.get_installed_packs(verify_files=False).items():
            if not isinstance(p_info, dict):
                continue
            p_cat = (p_info.get("category") or "").lower()
            p_name = p_info.get("name") or p_info.get("folder_name") or pack_id
            cat_match = (
                (tab == "lut" and p_cat in ("luts", "lut", "mau", "color")) or
                (tab == "sfx" and p_cat in ("sfx", "sound", "am_thanh")) or
                (tab == "trans" and p_cat in ("trans", "transition", "chuyen_canh")) or
                (tab == "text" and p_cat in ("text", "title", "chu")) or
                (tab == "meme" and p_cat in ("meme", "memes")) or
                (tab == "overlay" and p_cat in ("overlay", "overlays")) or
                (tab == "bgm" and p_cat in ("bgm", "music", "nhac_nen"))
            )
            if cat_match and p_name not in seen_packs:
                seen_packs.add(p_name)
                discovered_packs.append(p_name)

        if discovered_packs:
            lbl_packs_hdr = QLabel("📦 CÁC GÓI ĐÃ CÀI")
            lbl_packs_hdr.setStyleSheet(f"""
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 0.5px;
                color: {ThemeColors.CYAN_HI};
                padding-top: 10px;
                padding-bottom: 2px;
                padding-left: 6px;
            """)
            self.cats_vbox.addWidget(lbl_packs_hdr)

            for p_name in discovered_packs:
                count = pack_counts.get(p_name, 0)
                cnt_str = f" ({count})" if count > 0 else ""
                btn_pack = QPushButton(f"  📦 {p_name}{cnt_str}")
                btn_pack.setProperty("sub_cat_id", f"pack:{p_name}")
                btn_pack.setCheckable(True)
                btn_pack.setFixedHeight(32)
                btn_pack.setCursor(Qt.PointingHandCursor)
                btn_pack.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        border: none;
                        border-radius: 6px;
                        color: {ThemeColors.TEXT_MUTED};
                        font-size: 12px;
                        text-align: left;
                        padding-left: 6px;
                    }}
                    QPushButton:hover {{
                        background: {ThemeColors.BG_CARD};
                        color: {ThemeColors.TEXT_PRIMARY};
                    }}
                    QPushButton:checked {{
                        background: {ThemeColors.BG_CARD_ACTIVE};
                        color: {ThemeColors.CYAN_HI};
                        font-weight: bold;
                    }}
                """)
                btn_pack.clicked.connect(lambda _, x=f"pack:{p_name}": self._on_sub_category_clicked(x))
                self.cats_vbox.addWidget(btn_pack)

        self.cats_vbox.addStretch()
        active_target = self.current_sub_cat if (self.current_sub_cat.startswith("pack:") or any(cid == self.current_sub_cat for cid, _ in sub_cats)) else "all"
        self._on_sub_category_clicked(active_target)

    def _show_collection_context_menu(self, pos, name: str, btn: QPushButton):
        menu = QMenu(self)
        act_rename = menu.addAction("✏️ Đổi tên bộ sưu tập...")
        act_delete = menu.addAction("🗑️ Xóa bộ sưu tập...")
        action = menu.exec(btn.mapToGlobal(pos))
        if action == act_rename:
            new_name, ok = QInputDialog.getText(self, "Đổi tên bộ sưu tập", "Tên mới:", text=name)
            if ok and new_name.strip() and new_name.strip() != name:
                CollectionsManager.rename_collection(name, new_name.strip())
                self._rebuild_categories_column()
                self._on_sub_category_clicked(f"coll:{new_name.strip()}")
        elif action == act_delete:
            confirm = QMessageBox.question(
                self, "Xóa bộ sưu tập",
                f"Bạn có chắc muốn xóa bộ sưu tập '{name}' không?\n(Các tài nguyên bên trong sẽ không bị xóa khỏi máy)",
                QMessageBox.Yes | QMessageBox.No
            )
            if confirm == QMessageBox.Yes:
                CollectionsManager.delete_collection(name)
                self._rebuild_categories_column()
                self._on_sub_category_clicked("all")

    def _show_category_context_menu(self, pos, cid: str, cname: str, btn: QPushButton, count: int):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 4px;
                color: #e4e4e7;
                font-size: 11.5px;
            }
            QMenu::item:selected {
                background-color: #27272a;
                color: #ffffff;
            }
        """)
        act_rename = menu.addAction("✏️ Đổi Tên Danh Mục...")
        act_delete = menu.addAction(f"🗑️ Xóa Danh Mục ({count} mẫu)..." if count > 0 else "🗑️ Xóa Danh Mục Trống...")
        action = menu.exec(btn.mapToGlobal(pos))
        if action == act_rename:
            new_name, ok = QInputDialog.getText(self, "Đổi tên danh mục", f"Nhập tên mới cho danh mục '{cname}':", text=cname)
            if ok and new_name.strip() and new_name.strip() != cname:
                self._rename_sub_category(cid, new_name.strip())
        elif action == act_delete:
            if count == 0:
                self._delete_sub_category(cid, cname)
            else:
                reply = QMessageBox.question(
                    self,
                    "Xác nhận xóa danh mục",
                    f"Danh mục '{cname}' đang chứa {count} đạo cụ.\nBạn có chắc muốn xóa danh mục này?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._delete_sub_category(cid, cname)

    def _rename_sub_category(self, old_cid: str, new_name: str):
        for a in self.all_assets:
            if self._get_asset_sub(a) == old_cid:
                if isinstance(a, dict):
                    a["sub"] = new_name
                    a["sub_category"] = new_name
                else:
                    setattr(a, "sub", new_name)
                    if hasattr(a, "sub_category"):
                        setattr(a, "sub_category", new_name)

        try:
            from src.core.storage_manager import StorageManager
            sm = StorageManager()
            if sm.online:
                sm.rename_category(old_cid, new_name)
        except Exception:
            pass

        self._rebuild_categories_column()
        self._on_sub_category_clicked(new_name)
        self._show_toast(f"✅ Đã đổi tên danh mục thành '{new_name}'!")

    def _delete_sub_category(self, cid: str, cname: str):
        try:
            from src.core.storage_manager import StorageManager
            sm = StorageManager()
            if sm.online:
                sm.remove_category(cid)
        except Exception:
            pass

        for a in self.all_assets:
            if self._get_asset_sub(a) == cid:
                if isinstance(a, dict):
                    a["sub"] = "all"
                    a["sub_category"] = "all"
                else:
                    setattr(a, "sub", "all")
                    if hasattr(a, "sub_category"):
                        setattr(a, "sub_category", "all")

        self.current_sub_cat = "all"
        self._rebuild_categories_column()
        self._refresh_grid()
        self._show_toast(f"🗑 Đã xóa danh mục '{cname}'!")

    def _prompt_create_collection(self):
        name, ok = QInputDialog.getText(self, "Tạo bộ sưu tập mới", "Nhập tên bộ sưu tập (VD: Vlog Du Lịch, TikTok Trend...):")
        if ok and name.strip():
            clean = name.strip()
            success = CollectionsManager.create_collection(clean)
            if success:
                self._rebuild_categories_column()
                self._on_sub_category_clicked(f"coll:{clean}")
                self._show_toast(f"✅ Đã tạo bộ sưu tập '{clean}'!")
            else:
                self._show_toast(f"⚠️ Bộ sưu tập '{clean}' đã tồn tại hoặc không hợp lệ!")

    def _on_sub_category_clicked(self, cid: str):
        self._stop_inspector_sfx_play()
        if hasattr(self, "icon_meme_widget"):
            self.icon_meme_widget.stop_playback()
        self.current_sub_cat = cid
        for i in range(self.cats_vbox.count()):
            item = self.cats_vbox.itemAt(i)
            if item and isinstance(item.widget(), QPushButton):
                btn = item.widget()
                btn_cid = btn.property("sub_cat_id")
                btn.setChecked(btn_cid == cid or (cid in ("all", "fav:all") and btn_cid in ("all", "fav:all")))
        self._refresh_grid()

    def _on_category_pill_clicked(self, category_id: str):
        """Tương thích ngược với unit tests."""
        self._on_sub_category_clicked(category_id)

    # =========================================================================
    # 8. QUẢN LÝ LƯỚI CARD & TÌM KIẾM
    # =========================================================================
    # =========================================================================
    # 8. QUẢN LÝ LƯỚI CARD & TÌM KIẾM (Model/View Architecture)
    # =========================================================================
    @property
    def card_widgets(self) -> List[AssetCard]:
        """Lazy on-demand card widget access for backward-compatible unit tests."""
        if getattr(self, "_card_widgets_cache", None) is None:
            self._card_widgets_cache = self._create_legacy_card_widgets()
            self._filter_legacy_cards()
        return self._card_widgets_cache

    @card_widgets.setter
    def card_widgets(self, val: List[AssetCard]):
        self._card_widgets_cache = val

    def _create_legacy_card_widgets(self) -> List[AssetCard]:
        cards = []
        parent_container = getattr(self, "grid_container", None) or self
        for item in self.all_assets:
            pid = getattr(item, "id", "")
            is_fav = pid in self.favorites
            sample_fn = lambda: self.txt_single_title.text().strip() if hasattr(self, "txt_single_title") else ""
            card = AssetCard(preset=item, is_fav=is_fav, sample_text_func=sample_fn, parent=parent_container)
            card.selected_signal.connect(self._on_card_selected)
            card.favorite_toggled.connect(self._on_favorite_toggled)
            card.add_requested.connect(self._on_card_add_requested)
            card.quick_play_requested.connect(self._on_asset_quick_play_requested)
            cards.append(card)
        return cards

    def _populate_cards(self):
        # Cập nhật Model & SearchIndex ảo hóa
        if hasattr(self, "asset_model"):
            self.asset_model.set_assets(self.all_assets)
            from src.services.search_index import SearchIndex
            self.search_index = SearchIndex(self.all_assets)
            self.asset_filter.set_search_index(self.search_index)

        self._card_widgets_cache = None

    def _refresh_grid(self):
        tab = self.current_rail_tab
        cat = self.current_sub_cat
        query = self.txt_search.text().strip()
        filter_mode = getattr(self, "filter_mode", "all")

        # 1. Cập nhật AssetFilterProxyModel (< 1ms, không tạo QWidget)
        if hasattr(self, "asset_filter"):
            recent_ids = RecentManager.load() if filter_mode == "recent" else None
            if hasattr(self.asset_filter, "set_filters_batch"):
                self.asset_filter.set_filters_batch(
                    category=tab,
                    sub_category=cat,
                    filter_mode=filter_mode,
                    recent_ids=recent_ids,
                    search_query=query,
                )
            else:
                self.asset_filter.set_category(tab)
                self.asset_filter.set_sub_category(cat)
                self.asset_filter.set_filter_mode(filter_mode)
                if recent_ids is not None:
                    self.asset_filter.set_recent_ids(recent_ids)
                self.asset_filter.set_search_query(query)

            count = self.asset_filter.rowCount()
            self.lbl_count_badge.setText(f"{count} mẫu")

            # Đồng bộ bỏ chọn nếu tài nguyên đang chọn bị lọc mất hoặc không còn kết quả
            if count == 0:
                self.selected_asset = None
                self._update_inspector_details()
            elif self.selected_asset is not None:
                sel_id = getattr(self.selected_asset, "id", "") if not isinstance(self.selected_asset, dict) else self.selected_asset.get("id", "")
                from src.ui.lux.library.asset_model import IdRole
                still_visible = any(self.asset_filter.index(r, 0).data(IdRole) == sel_id for r in range(count))
                if not still_visible:
                    self.selected_asset = None
                    self._update_inspector_details()

        # 2. Đồng bộ hóa card_widgets cũ nếu test đang truy cập
        if getattr(self, "_card_widgets_cache", None) is not None:
            self._filter_legacy_cards()

    def _filter_legacy_cards(self):
        if not getattr(self, "_card_widgets_cache", None):
            return

        tab = self.current_rail_tab
        cat = self.current_sub_cat
        query = self.txt_search.text().strip().lower()
        filter_mode = getattr(self, "filter_mode", "all")
        recent_items = set(RecentManager.load()) if filter_mode == "recent" else set()

        for card in self._card_widgets_cache:
            item = card.preset
            i_tab = "text" if isinstance(item, TextStylePreset) else "trans" if isinstance(item, TransitionStylePreset) else getattr(item, "tab", "")
            i_cat = getattr(item, "category", "")
            i_id = getattr(item, "id", "")
            i_name = getattr(item, "name", "").lower()
            i_fav = i_id in self.favorites

            if filter_mode == "fav" and not i_fav:
                card.setVisible(False)
                continue
            elif filter_mode == "recent" and i_id not in recent_items:
                card.setVisible(False)
                continue

            if tab == "fav":
                if cat.startswith("coll:"):
                    cname = cat[5:]
                    coll_assets = set(CollectionsManager.get_collection_assets(cname))
                    if i_id not in coll_assets:
                        card.setVisible(False)
                        continue
                elif not i_fav:
                    card.setVisible(False)
                    continue
            elif i_tab != tab:
                card.setVisible(False)
                continue

            if cat not in ("all", "fav:all", "Tất cả") and not cat.startswith("coll:"):
                if cat.startswith("pack:"):
                    target_pack = cat[5:]
                    i_pack = getattr(item, "pack_name", "") or (item.get("pack_name") if isinstance(item, dict) else "")
                    i_sub = getattr(item, "sub", "") or (item.get("sub") if isinstance(item, dict) else "")
                    if i_pack != target_pack and i_sub != target_pack:
                        card.setVisible(False)
                        continue
                else:
                    cat_aliases = {
                        "chuyen_canh": ["chuyen_canh", "trans"],
                        "hai_huoc": ["hai_huoc", "fun"],
                        "diem_nhan": ["diem_nhan", "accent"],
                        "cong_nghe": ["cong_nghe", "tech"],
                        "vietnam_viral": ["vietnam_viral", "trending"],
                        "reaction": ["reaction"],
                        "cham_hoi": ["cham_hoi"],
                        "cay_cu": ["cay_cu"],
                    }
                    allowed = cat_aliases.get(cat, [cat])
                    if i_cat not in allowed and cat not in i_cat:
                        card.setVisible(False)
                        continue

            if query:
                from src.core.motion_analyzer import matches_query
                search_targets = [i_name, i_cat, i_id, getattr(item, "sub", "")]
                if hasattr(item, "tags") and item.tags:
                    search_targets.extend(item.tags)
                if hasattr(item, "animation") and item.animation:
                    search_targets.append(item.animation)
                if hasattr(item, "motion_tags") and item.motion_tags:
                    search_targets.extend(item.motion_tags)
                if not matches_query(query, search_targets):
                    card.setVisible(False)
                    continue

            card.setVisible(True)

    def _on_lux_card_selected(self, asset: Any):
        pid = getattr(asset, "id", "") if not isinstance(asset, dict) else asset.get("id", "")
        self._on_card_selected(pid)

    def _on_lux_card_activated(self, asset: Any):
        pid = getattr(asset, "id", "") if not isinstance(asset, dict) else asset.get("id", "")
        self._on_card_selected(pid)
        self._insert_at_playhead()

    def _on_lux_fusion_paste(self, asset: Any):
        pid = self._get_asset_id(asset)
        if not pid:
            self._show_toast("⚠️ Tài nguyên không hợp lệ hoặc thiếu ID.")
            return

        # Kiểm tra sự tồn tại trong danh mục hiện tại (stale ID check)
        matched_item = next((a for a in self.all_assets if self._get_asset_id(a) == pid), None)
        if matched_item is None:
            self._show_toast(f"⚠️ Tài nguyên '{pid}' không còn tồn tại trong kho!")
            return

        self._on_card_selected(pid, record_recent=False)
        self.selected_asset = matched_item
        if hasattr(self, "btn_copy_fusion") and self.btn_copy_fusion:
            self.btn_copy_fusion.click()
        else:
            self._on_copy_fusion_clicked()

    def _on_card_add_requested(self, preset: Any):
        pid = getattr(preset, "id", "")
        self._on_card_selected(pid)
        self._insert_at_playhead()

    def _on_hover_preview_toggled(self, checked: bool):
        AssetCard.enable_hover_preview = checked
        if not checked:
            AssetCard.stop_all_audio()

    def _on_starter_pack_clicked(self):
        if getattr(self, "_ingest_in_progress", False):
            return

        from src.core.starter_pack_downloader import StarterPackProducer
        from src.ui.widgets.pack_ingest_wizard import PackIngestWizard

        storage = getattr(self, "storage", None)
        if storage is None:
            from src.core.storage_manager import StorageManager
            storage = StorageManager()
            self.storage = storage

        if not storage.online:
            self._show_toast("⚠️ Kho lưu trữ chưa kết nối hoặc chưa được cấu hình. Vui lòng kết nối Kho Chính trước.")
            return

        self._ingest_in_progress = True
        self.btn_starter_pack.setEnabled(False)
        self.btn_starter_pack.setText("⏳ Đang chuẩn bị gói...")

        wizard = None
        try:
            factory = lambda cancel, progress: StarterPackProducer.produce_to_staging(storage, cancel, progress)

            wizard = PackIngestWizard(
                storage=storage,
                parent=self,
                auto_prepare=True,
                prepare_factory=factory,
            )
            self._active_ingest_wizard = wizard

            def _on_committed(payload):
                self._rescan_assets()
                count = payload.get("count", 0) if isinstance(payload, dict) else 0
                synced = payload.get("snapshot_synced") if isinstance(payload, dict) else None
                if synced is False:
                    self._show_toast(f"✅ Đã nhập thành công {count} đạo cụ Starter Pack (SFX & LUTs) vào Kho! (⚠️ Bộ đệm offline chưa đồng bộ).")
                else:
                    self._show_toast(f"✅ Đã nhập thành công {count} đạo cụ Starter Pack (SFX & LUTs) vào Kho!")

            wizard.pack_committed.connect(_on_committed)
            wizard.exec()
        finally:
            self._ingest_in_progress = False
            self._active_ingest_wizard = None
            self.btn_starter_pack.setEnabled(True)
            self.btn_starter_pack.setText("📦 Gói Starter Pack (SFX & LUTs)")

    def _on_starter_pack_completed(self, summary: dict):
        self.btn_starter_pack.setEnabled(True)
        self.btn_starter_pack.setText("📦 Gói Starter Pack (SFX & LUTs)")
        self._rescan_assets()
        total = sum(summary.values())
        self._show_toast(f"✅ Đã nạp thành công {total} đạo cụ Starter Pack vào Kho!")

    def _on_starter_pack_error(self, err_msg: str):
        self.btn_starter_pack.setEnabled(True)
        self.btn_starter_pack.setText("📦 Gói Starter Pack (SFX & LUTs)")
        self._show_toast(f"❌ Lỗi nạp đạo cụ: {err_msg}")

    def _on_smart_cutout_clicked(self):
        if not self.selected_asset:
            return
        from src.core.smart_cutout import SmartCutout

        target_path = getattr(self.selected_asset, "file_path", "")
        # Nếu chưa có file_path thực tế (ví dụ là Mockup icon SVG/badge), sinh ảnh PNG trước
        if not target_path or not os.path.exists(target_path):
            badge = getattr(self.selected_asset, "badge_icon", "✨")
            color_hex = getattr(self.selected_asset, "color_hex", "#facc15")
            pid = getattr(self.selected_asset, "id", "sticker")
            temp_dir = tempfile.gettempdir()
            target_path = os.path.join(temp_dir, f"raw_sticker_{pid}.png")
            pixmap = QPixmap(512, 512)
            pixmap.fill(QColor(255, 255, 255))
            painter = QPainter(pixmap)
            painter.setFont(QFont("Segoe UI Emoji", 260))
            painter.setPen(QColor(color_hex))
            painter.drawText(QRectF(0, 0, 512, 512), Qt.AlignCenter, badge)
            painter.end()
            pixmap.save(target_path, "PNG")

        try:
            out_transparent = SmartCutout.auto_cutout(target_path)
            self.selected_asset.file_path = out_transparent
            self.selected_asset.thumbnail_path = out_transparent
            self._update_inspector_details()
            self._show_toast("✅ Đã tách nền AI thành công! Sẵn sàng kéo thả vào DaVinci Resolve.")
        except Exception as e:
            self._show_toast(f"❌ Lỗi tách nền: {e}")

    def _on_card_selected(self, preset_id: str, record_recent: bool = True):
        self.inspector.setEnabled(True)
        self._stop_inspector_sfx_play()

        # 1. Tìm asset trực tiếp từ danh sách dữ liệu (không cần tạo card QWidget)
        matched_item = None
        for item in self.all_assets:
            i_id = getattr(item, "id", "") if not isinstance(item, dict) else item.get("id", "")
            if i_id == preset_id:
                matched_item = item
                break

        if not matched_item:
            self._show_toast(f"⚠️ Tài nguyên '{preset_id}' không còn tồn tại trong kho!")
            return

        self.selected_asset = matched_item

        if record_recent:
            RecentManager.add(preset_id)

        # 2. Đổi category/tab nếu cần và giải phóng đúng filter đang che asset
        target_tab = "text" if isinstance(matched_item, TextStylePreset) else "trans" if isinstance(matched_item, TransitionStylePreset) else getattr(matched_item, "tab", "") or getattr(matched_item, "type", "") or getattr(matched_item, "category", "")
        if target_tab and target_tab != getattr(self, "current_rail_tab", ""):
            if hasattr(self, "_filter_by_rail"):
                self._filter_by_rail(target_tab)
            elif hasattr(self, "_on_rail_tab_clicked"):
                self._on_rail_tab_clicked(target_tab)

        need_refresh = False
        if hasattr(self, "txt_search") and self.txt_search.text().strip():
            q = self.txt_search.text().strip().lower()
            name = getattr(matched_item, "name", "") if not isinstance(matched_item, dict) else matched_item.get("name", "")
            if q not in name.lower() and q not in preset_id.lower():
                self.txt_search.blockSignals(True)
                self.txt_search.clear()
                self.txt_search.blockSignals(False)
                need_refresh = True

        if getattr(self, "current_sub_cat", "all") != "all":
            sub = getattr(matched_item, "sub", "") if not isinstance(matched_item, dict) else matched_item.get("subcategory", "")
            if sub and sub != self.current_sub_cat:
                self.current_sub_cat = "all"
                need_refresh = True

        if getattr(self, "filter_mode", "all") != "all":
            self.filter_mode = "all"
            need_refresh = True

        if need_refresh:
            self._refresh_grid()

        # 3. Đồng bộ selection & scrollTo trên AssetGridView qua ProxyModel
        if hasattr(self, "asset_model") and hasattr(self, "asset_filter") and hasattr(self, "asset_grid"):
            src_idx = self.asset_model.index_of_asset_id(preset_id)
            if src_idx.isValid():
                proxy_idx = self.asset_filter.mapFromSource(src_idx)
                if proxy_idx.isValid() and proxy_idx.row() >= 0:
                    self.selected_asset = matched_item
                    self.asset_grid.setCurrentIndex(proxy_idx)
                    self.asset_grid.scrollTo(proxy_idx)
                else:
                    return
            else:
                return
        else:
            self.selected_asset = matched_item

        # 4. Tải metadata vào audio inspector mà KHÔNG tự động phát âm thanh
        cat_lower = (target_tab or "").lower()
        if cat_lower in ("sfx", "audio", "bgm", "sound"):
            if hasattr(self, "audio_inspector_widget"):
                self.audio_inspector_widget.set_asset(matched_item)

        # 5. Đồng bộ highlight cho legacy card_widgets nếu test suite đang kích hoạt cache
        if getattr(self, "_card_widgets_cache", None) is not None:
            matched_card = None
            tab = getattr(self, "current_rail_tab", "")
            for c in self._card_widgets_cache:
                item = c.preset
                i_tab = "text" if isinstance(item, TextStylePreset) else "trans" if isinstance(item, TransitionStylePreset) else getattr(item, "tab", "")
                is_match = (getattr(item, "id", "") == preset_id)
                if is_match:
                    if matched_card is None:
                        matched_card = c
                    elif (tab and tab != "fav" and i_tab == tab) or c.isVisible():
                        matched_card = c
                c.set_selected(False)

            if matched_card:
                target_tab = "text" if isinstance(matched_card.preset, TextStylePreset) else "trans" if isinstance(matched_card.preset, TransitionStylePreset) else getattr(matched_card.preset, "tab", "")
                if target_tab and target_tab != getattr(self, "current_rail_tab", ""):
                    if hasattr(self, "_filter_by_rail"):
                        self._filter_by_rail(target_tab)
                    elif hasattr(self, "_on_rail_tab_clicked"):
                        self._on_rail_tab_clicked(target_tab)
                matched_card.set_selected(True)
                if hasattr(self, "scroll_area"):
                    self.scroll_area.ensureWidgetVisible(matched_card)

        # 6. Cập nhật Inspector
        self._update_inspector_details()

    def _setup_suggestion_bar(self):
        """Khởi tạo thanh gợi ý AI theo ngữ cảnh lời thoại (CapCut-style Suggestion Bar một hàng gọn)."""
        self.suggestion_bar = QFrame()
        self.suggestion_bar.setObjectName("suggestion_bar")
        self.suggestion_bar.setFixedHeight(38)
        self.suggestion_bar.setStyleSheet(f"""
            QFrame#suggestion_bar {{
                background-color: #16171a;
                border-bottom: 1px solid #2e3138;
            }}
        """)
        sug_layout = QHBoxLayout(self.suggestion_bar)
        sug_layout.setContentsMargins(10, 2, 10, 2)
        sug_layout.setSpacing(6)

        lbl_sug_title = QLabel("💡 Gợi ý AI:")
        lbl_sug_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #f59e0b;")
        sug_layout.addWidget(lbl_sug_title)

        self.combo_context_sentence = QComboBox()
        self.combo_context_sentence.setEditable(True)
        self.combo_context_sentence.lineEdit().setPlaceholderText("Nhập hoặc chọn câu thoại...")
        self.combo_context_sentence.setFixedWidth(200)
        self.combo_context_sentence.setFixedHeight(24)
        self.combo_context_sentence.setStyleSheet(f"""
            QComboBox {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid #353841;
                border-radius: 4px;
                padding: 1px 6px;
                color: {ThemeColors.TEXT_PRIMARY};
                font-size: 11px;
            }}
            QComboBox:focus {{
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        self.combo_context_sentence.lineEdit().returnPressed.connect(self._on_context_sentence_analyzed)
        self.combo_context_sentence.currentIndexChanged.connect(self._on_context_combo_changed)
        sug_layout.addWidget(self.combo_context_sentence)

        self.btn_analyze_sentence = QPushButton("⚡ Gợi ý")
        self.btn_analyze_sentence.setCursor(Qt.PointingHandCursor)
        self.btn_analyze_sentence.setFixedHeight(24)
        self.btn_analyze_sentence.setStyleSheet(f"""
            QPushButton {{
                background: {ThemeColors.PRIMARY};
                border: none;
                border-radius: 4px;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background: {ThemeColors.PRIMARY_HOVER};
            }}
        """)
        self.btn_analyze_sentence.clicked.connect(self._on_context_sentence_analyzed)
        sug_layout.addWidget(self.btn_analyze_sentence)

        # Scroll area for suggestion chips
        self.scroll_sug = QScrollArea()
        self.scroll_sug.setFixedHeight(28)
        self.scroll_sug.setWidgetResizable(True)
        self.scroll_sug.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_sug.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_sug.setStyleSheet("background: transparent; border: none;")

        self.sug_chips_container = QWidget()
        self.sug_chips_container.setStyleSheet("background: transparent;")
        self.sug_chips_layout = QHBoxLayout(self.sug_chips_container)
        self.sug_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.sug_chips_layout.setSpacing(6)
        self.scroll_sug.setWidget(self.sug_chips_container)
        sug_layout.addWidget(self.scroll_sug, stretch=1)

        self.btn_toggle_sug = QPushButton("▲")
        self.btn_toggle_sug.setFixedSize(20, 20)
        self.btn_toggle_sug.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_sug.setToolTip("Thu gọn / Mở rộng khay gợi ý AI")
        self.btn_toggle_sug.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid #353841;
                border-radius: 4px;
                color: {ThemeColors.TEXT_MUTED};
                font-size: 10px;
            }}
            QPushButton:hover {{
                color: {ThemeColors.TEXT_PRIMARY};
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        self.btn_toggle_sug.clicked.connect(self._toggle_suggestion_tray)
        sug_layout.addWidget(self.btn_toggle_sug)

        self.btn_close_sug = QPushButton("×")
        self.btn_close_sug.setFixedSize(20, 20)
        self.btn_close_sug.setCursor(Qt.PointingHandCursor)
        self.btn_close_sug.setToolTip("Đóng thanh gợi ý (mở lại từ nút 💡 Gợi ý trên thanh lọc)")
        self.btn_close_sug.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #353841;
                border-radius: 4px;
                color: #71717a;
                font-size: 14px;
                font-weight: bold;
                padding: 0;
            }
            QPushButton:hover {
                color: #ffffff;
                background: rgba(239, 68, 68, 0.2);
                border-color: #ef4444;
            }
        """)
        self.btn_close_sug.clicked.connect(self._close_suggestion_bar)
        sug_layout.addWidget(self.btn_close_sug)

        self._render_suggestion_chips([])

    def _render_suggestion_chips(self, suggestions: List[Any]):
        """Hiển thị các pill gợi ý hoặc danh sách quick samples."""
        while self.sug_chips_layout.count():
            item = self.sug_chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if suggestions:
            for sug in suggestions:
                chip = SuggestionChip(sug)
                chip.selected_signal.connect(self._on_card_selected)
                chip.add_requested.connect(self._on_card_add_requested)
                self.sug_chips_layout.addWidget(chip)
            self.sug_chips_layout.addStretch(1)
        else:
            # Placeholder quick sample pills
            lbl_hint = QLabel("💡 Gợi ý mẫu:")
            lbl_hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 11px;")
            self.sug_chips_layout.addWidget(lbl_hint)

            samples = [
                ("😂 Hài hước", "Toang thật rồi ông giáo ạ ảo thật đấy haha buồn cười vãi", "hai_huoc"),
                ("💥 Giật mình", "Thật không thể tin nổi cú sốc này kinh hoàng quá", "giat_minh"),
                ("🎬 Chuyển cảnh", "Tiếp theo chúng ta sẽ cùng bước sang phần 2 nhé", "chuyen_canh"),
                ("💻 Công nghệ", "Cài đặt phần mềm AI và lập trình hệ thống máy tính", "cong_nghe"),
            ]
            for label, sample_txt, sample_role in samples:
                btn_sample = QPushButton(label)
                btn_sample.setCursor(Qt.PointingHandCursor)
                btn_sample.setFixedHeight(22)
                btn_sample.setStyleSheet(f"""
                    QPushButton {{
                        background: {ThemeColors.BG_CARD};
                        border: 1px dashed #353841;
                        border-radius: 4px;
                        padding: 0 8px;
                        color: {ThemeColors.TEXT_MUTED};
                        font-size: 10px;
                    }}
                    QPushButton:hover {{
                        color: #f59e0b;
                        border-color: {ThemeColors.PRIMARY};
                        background: {ThemeColors.BG_CARD_ACTIVE};
                    }}
                """)
                btn_sample.clicked.connect(lambda _, t=sample_txt, r=sample_role: self.set_context_sentence(t, role=r))
                self.sug_chips_layout.addWidget(btn_sample)
            self.sug_chips_layout.addStretch(1)

    def _on_context_sentence_analyzed(self):
        """Phân tích câu thoại và tải danh sách gợi ý tương ứng."""
        if not hasattr(self, "combo_context_sentence"):
            return
        raw_text = self.combo_context_sentence.currentText().strip()
        clean_text = re.sub(r"^\[\d+:\d+\]\s*", "", raw_text)
        if not clean_text:
            self._render_suggestion_chips([])
            return
        self.combo_context_sentence.setToolTip(clean_text)
        suggestions = self.suggester.suggest_for_text(clean_text, role=getattr(self, "_current_context_role", ""))
        self._render_suggestion_chips(suggestions)

    def _on_context_combo_changed(self, idx: int):
        """Khi người dùng chọn một câu thoại từ transcript combobox."""
        if hasattr(self, "_transcript_segments") and 0 <= idx < len(self._transcript_segments):
            seg = self._transcript_segments[idx]
            self._current_context_role = seg.get("role", "")
            self._on_context_sentence_analyzed()

    def set_context_sentence(self, text: str, role: str = ""):
        """Đặt câu thoại mục tiêu và tự động kích hoạt gợi ý AI."""
        self._current_context_role = role
        if hasattr(self, "combo_context_sentence"):
            self.combo_context_sentence.blockSignals(True)
            self.combo_context_sentence.setEditText(text)
            self.combo_context_sentence.setToolTip(text)
            self.combo_context_sentence.blockSignals(False)
        self._on_context_sentence_analyzed()

    def load_transcript_segments(self, segments: List[Dict[str, Any]]):
        """Nạp danh sách các phân đoạn thoại từ AutoCut hoặc Shotbook."""
        self._transcript_segments = segments or []
        if not hasattr(self, "combo_context_sentence"):
            return
        self.combo_context_sentence.blockSignals(True)
        self.combo_context_sentence.clear()
        for seg in self._transcript_segments:
            st = float(seg.get("start", 0.0))
            mins = int(st // 60)
            secs = int(st % 60)
            txt = seg.get("text", "").strip()
            self.combo_context_sentence.addItem(f"[{mins:02d}:{secs:02d}] {txt}", userData=seg)
        self.combo_context_sentence.blockSignals(False)
        if self._transcript_segments:
            first = self._transcript_segments[0]
            self.set_context_sentence(first.get("text", ""), role=first.get("role", ""))

    def _toggle_suggestion_tray(self):
        """Thu gọn hoặc mở rộng thanh cuộn gợi ý."""
        is_shown = not self.scroll_sug.isHidden()
        if is_shown:
            self.scroll_sug.setVisible(False)
            self.btn_toggle_sug.setText("▼")
        else:
            self.scroll_sug.setVisible(True)
            self.btn_toggle_sug.setText("▲")

    def _close_suggestion_bar(self):
        """Đóng ribbon gợi ý AI."""
        if hasattr(self, "suggestion_bar"):
            self.suggestion_bar.hide()
        if hasattr(self, "btn_toggle_sug_bar"):
            self.btn_toggle_sug_bar.blockSignals(True)
            self.btn_toggle_sug_bar.setChecked(False)
            self.btn_toggle_sug_bar.blockSignals(False)

    def _on_sug_bar_toggled(self, checked: bool):
        """Bật / tắt hiển thị ribbon gợi ý từ nút toolbar."""
        if hasattr(self, "suggestion_bar"):
            self.suggestion_bar.setVisible(checked)

    def _set_filter_mode(self, mode: str):
        self.filter_mode = mode
        self._update_filter_button_styles()
        self._refresh_grid()

    def _update_filter_button_styles(self):
        modes = [("all", self.btn_filter_all), ("fav", self.btn_filter_fav), ("recent", self.btn_filter_recent)]
        for m, btn in modes:
            is_active = (getattr(self, "filter_mode", "all") == m)
            btn.setChecked(is_active)
            bg = ThemeColors.BG_CARD_ACTIVE if is_active else ThemeColors.BG_CARD
            border = ThemeColors.PRIMARY if is_active else ThemeColors.BORDER_DEFAULT
            col = ThemeColors.TEXT_PRIMARY if is_active else ThemeColors.TEXT_MUTED
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    border: 1px solid {border};
                    border-radius: 6px;
                    color: {col};
                    font-size: 11px;
                    font-weight: {'bold' if is_active else 'normal'};
                    padding: 2px 8px;
                }}
            """)

    def eventFilter(self, obj, event):
        if hasattr(self, "scroll_grid") and obj == self.scroll_grid.viewport():
            if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
                delta = event.angleDelta().y()
                curr = getattr(self, "grid_columns", 3)
                if delta > 0:
                    self._on_grid_cols_changed(max(2, curr - 1))
                elif delta < 0:
                    self._on_grid_cols_changed(min(5, curr + 1))
                return True
            elif event.type() in (QEvent.DragEnter, QEvent.DragMove) and event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
            elif event.type() == QEvent.Drop and event.mimeData().hasUrls():
                paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
                event.acceptProposedAction()
                if paths:
                    QTimer.singleShot(0, lambda: self._import_files_or_folders(paths))
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            event.acceptProposedAction()
            if paths:
                QTimer.singleShot(0, lambda: self._import_files_or_folders(paths))
        else:
            event.ignore()

    def _update_grid_size_button_styles(self):
        curr_cols = getattr(self, "grid_columns", 3)
        if hasattr(self, "grid_size_buttons"):
            for btn, cols in self.grid_size_buttons:
                is_active = (cols == curr_cols) or (cols == 5 and curr_cols >= 4) or (cols == 2 and curr_cols <= 2)
                btn.setChecked(is_active)
                bg = ThemeColors.BG_CARD_ACTIVE if is_active else ThemeColors.BG_CARD
                border = ThemeColors.PRIMARY if is_active else ThemeColors.BORDER_DEFAULT
                col = "#FFFFFF" if is_active else ThemeColors.TEXT_MUTED
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {bg};
                        border: 1px solid {border};
                        border-radius: 4px;
                        color: {col};
                        font-size: 11px;
                        font-weight: {'bold' if is_active else 'normal'};
                        padding: 1px 7px;
                    }}
                    QPushButton:hover {{
                        border-color: {ThemeColors.PRIMARY};
                        color: #ffffff;
                    }}
                """)

    def _on_grid_cols_changed(self, val: int):
        self.grid_columns = val
        if hasattr(self, "lbl_cols_val"):
            self.lbl_cols_val.setText(f"{val} cột")
        if hasattr(self, "slide_card_cols") and self.slide_card_cols.value() != val:
            self.slide_card_cols.blockSignals(True)
            self.slide_card_cols.setValue(val)
            self.slide_card_cols.blockSignals(False)

        self._update_grid_size_button_styles()

        # Co giãn kích thước thẻ linh hoạt theo số cột nếu legacy card cache đang mở
        if getattr(self, "_card_widgets_cache", None) is not None:
            for card in self._card_widgets_cache:
                card.set_card_scale(val)

        self._refresh_grid()

    def _check_resolve_connection(self):
        try:
            res = ResolveAutomation()
            connected = res.is_connected()
        except Exception:
            connected = False

        if connected:
            self.btn_resolve_status.setText("🟢 DaVinci")
            self.btn_resolve_status.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(34, 197, 94, 0.15);
                    border: 1px solid #22c55e;
                    border-radius: 6px;
                    padding: 4px 8px;
                    color: #86efac;
                    font-size: 11px;
                    font-weight: bold;
                }}
            """)
            self._show_toast("DaVinci Resolve đang hoạt động và đã kết nối thành công!")
        else:
            self.btn_resolve_status.setText("⚪ DaVinci")
            self.btn_resolve_status.setStyleSheet(f"""
                QPushButton {{
                    background: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                    padding: 4px 8px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 11px;
                }}
            """)
            self._show_toast("Chưa tìm thấy DaVinci Resolve đang mở. Vui lòng mở DaVinci!")

    def _run_instant_autocut(self):
        if hasattr(self, "_autocut_worker") and self._autocut_worker and self._autocut_worker.isRunning():
            self._show_toast("⚡ Đang trong tiến trình AutoCut, vui lòng đợi...")
            return

        from src.core.resolve_api import ResolveAutomation
        from src.services.autocut_worker import AutoCutWorker

        auto = ResolveAutomation()
        if not auto.is_connected():
            connected = auto.connect()
            if not connected:
                self._show_toast("⚠ Chưa mở DaVinci Resolve hoặc chưa kết nối API!")
                return

        self._show_toast("⚡ Đang đọc Timeline & phân tích khoảng lặng...")

        self._autocut_worker = AutoCutWorker(
            resolve_automation=auto,
            silence_threshold_db=-38.0,
            min_silence_duration_ms=600,
            padding_ms=80,
            parent=self,
        )
        self._autocut_worker.progress_changed.connect(
            lambda pct, msg: self._show_toast(f"⚡ AutoCut: {msg} ({pct}%)")
        )
        self._autocut_worker.finished_result.connect(self._on_autocut_studio_finished)
        self._autocut_worker.start()

    def _on_autocut_studio_finished(self, result):
        if result.ok:
            mins = int(result.duration_saved_sec // 60)
            secs = int(result.duration_saved_sec % 60)
            saved_str = f"{mins}p{secs:02d}s" if mins > 0 else f"{result.duration_saved_sec:.1f}s"
            self._show_toast(f"🎉 AutoCut thành công! {result.cuts_count} khoảng lặng đã cắt, tiết kiệm {saved_str}")
        else:
            self._show_toast(f"⚠ AutoCut: {result.message}")

    def _dict_to_studio_asset(self, record: Dict[str, Any]) -> StudioAsset:
        """Chuyển đổi UI asset dict sang StudioAsset đầy đủ metadata hiển thị và xem trước (G04-04A2a)."""
        tab = str(record.get("tab") or record.get("category") or "broll").strip().lower()
        category = str(record.get("category") or tab)

        # Trích xuất subcategory chuẩn, ưu tiên subcategory rồi tới sub, mặc định Tất cả
        raw_sub = record.get("subcategory")
        if not isinstance(raw_sub, str) or not raw_sub.strip():
            raw_sub = record.get("sub")
        if not isinstance(raw_sub, str) or not raw_sub.strip():
            raw_sub = "Tất cả"

        pack_name = str(record.get("pack_name") or "")

        # Biểu tượng badge đặc trưng theo tab
        badge_icon = record.get("badge_icon")
        if not badge_icon:
            badge_map = {
                "text": "🔤", "trans": "🎬", "lut": "🎨", "meme": "🎭",
                "sfx": "🔊", "bgm": "🎵", "overlay": "✨", "broll": "📹"
            }
            badge_icon = badge_map.get(tab, "📁")

        # Xử lý duration an toàn: không tạo 3.0s giả cho media chưa biết
        raw_dur = record.get("duration_sec", record.get("duration"))
        duration_sec = None
        display_dur = 0.0
        if raw_dur is not None and not isinstance(raw_dur, bool):
            try:
                dur_f = float(raw_dur)
                if math.isfinite(dur_f) and dur_f > 0.0:
                    duration_sec = dur_f
                    display_dur = dur_f
            except (ValueError, TypeError, OverflowError):
                pass

        file_path = str(record.get("file_path") or "")
        thumbnail_path = str(record.get("thumbnail_path") or "")
        preview_path = str(record.get("preview_path") or file_path)

        asset = StudioAsset(
            id=str(record.get("id") or ""),
            name=str(record.get("name") or record.get("id") or ""),
            tab=tab,
            category=category,
            sub=raw_sub,
            badge_icon=badge_icon,
            file_path=file_path,
            thumbnail_path=thumbnail_path,
            preview_path=preview_path,
            pack_name=pack_name,
            duration=display_dur,
        )

        # Khôi phục sub gốc vì StudioAsset.__init__ tự ý gán sub = pack_name nếu có pack
        asset.sub = raw_sub
        asset.duration_sec = duration_sec

        # Bảo toàn tags là bản sao độc lập, không chia sẻ tham chiếu mutable list
        raw_tags = record.get("tags")
        asset.tags = list(raw_tags) if isinstance(raw_tags, (list, tuple, set)) else []

        # Các trường quản lý kho và nguồn gốc
        asset.availability = str(record.get("availability") or "online")
        asset.storage_managed = True
        asset.pack_id = record.get("pack_id")
        asset.source_kind = record.get("source_kind")
        asset.source_id = record.get("source_id")
        asset.template_type = record.get("template_type")
        asset.size_bytes = int(record.get("size_bytes") or 0) if isinstance(record.get("size_bytes"), int) else 0

        # Bảo toàn đường dẫn gốc và nguồn gốc định danh (G04-04C2-P)
        asset.library_path = record.get("library_path")
        asset.external_path = record.get("external_path")
        asset.relative_path = record.get("relative_path")
        asset.origin_library_id = record.get("origin_library_id")
        asset.origin_root = record.get("origin_root")

        # Bảo toàn tham chiếu identity cho runtime objects (không deepcopy)
        if "_raw_obj" in record:
            asset._raw_obj = record["_raw_obj"]
        if "native_preset" in record:
            asset.native_preset = record["native_preset"]

        return asset

    @staticmethod
    def _get_asset_id(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("id") or "")
        return str(getattr(item, "id", "") or "")

    @staticmethod
    def _is_storage_managed(item: Any) -> bool:
        if isinstance(item, dict):
            return bool(item.get("storage_managed", False))
        return bool(getattr(item, "storage_managed", False))

    @staticmethod
    def _get_asset_tab(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("tab") or "")
        return str(getattr(item, "tab", "") or "")

    @staticmethod
    def _get_asset_sub(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("sub") or item.get("sub_category") or "")
        return str(getattr(item, "sub", "") or getattr(item, "sub_category", "") or "")

    def _sync_storage_rail(self, taxonomy: Optional[Dict[str, str]] = None):
        if taxonomy is None:
            from src.core.storage_manager import StorageManager
            labels = StorageManager().taxonomy()
        else:
            labels = dict(taxonomy)
        labels["broll"] = labels.get("broll", "Cảnh")
        for asset in self.all_assets:
            tab = self._get_asset_tab(asset)
            if tab and tab not in self.rail_btns:
                button = QPushButton("📁\n" + labels.get(tab, tab))
                button.setCheckable(True)
                button.setFixedSize(74, 42)
                button.clicked.connect(lambda checked=False, cid=tab: self._filter_by_rail(cid))
                self.rail_btns[tab] = button
                rail_layout = self.rail.layout()
                rail_layout.insertWidget(max(0, rail_layout.count() - 1), button)

    def _merge_storage_assets(self):
        """Nạp snapshot kho ngoài; metadata không phụ thuộc media đang online."""
        from src.core.storage_manager import StorageManager
        for record in StorageManager().ui_assets():
            self.all_assets.append(self._dict_to_studio_asset(record))

    def apply_storage_snapshot(
        self,
        managed_assets: Optional[List[Dict[str, Any]]] = None,
        taxonomy: Optional[Dict[str, str]] = None,
        generation_sig: Optional[Any] = None
    ) -> None:
        """
        Cập nhật snapshot tài nguyên kho ngoài mà KHÔNG quét lại đĩa cứng (zero disk re-scan).
        - Bảo toàn 100% 8 danh sách bundled và local presets
        - Zero storage/indexer I/O khi caller truyền dữ liệu
        - Kiểm tra trùng lặp ID và va chạm với non-managed trước khi thay đổi trạng thái
        - KHÔNG phát signal library_assets_changed (tránh Echo Loop)
        """
        from collections.abc import Mapping

        if managed_assets is None:
            from src.core.storage_manager import StorageManager
            managed_assets = StorageManager().ui_assets()

        if taxonomy is None:
            from src.core.storage_manager import StorageManager
            taxonomy = StorageManager().taxonomy()

        # 1. Kiểm tra tính hợp lệ trước khi thay đổi trạng thái (Transaction validation)
        seen_ids = set()
        for r in managed_assets:
            if not isinstance(r, Mapping):
                raise ValueError(f"Managed asset record must be a Mapping/dict, got: {type(r).__name__}")
            rid = r.get("id")
            if not isinstance(rid, str) or not rid:
                raise ValueError(f"Managed asset record must have a non-empty str 'id', got: {rid!r}")
            if rid in seen_ids:
                raise ValueError(f"Duplicate asset ID in managed snapshot: {rid}")
            seen_ids.add(rid)

        non_managed_ids = {
            self._get_asset_id(a) for a in self.all_assets
            if not self._is_storage_managed(a) and self._get_asset_id(a)
        }
        for r in managed_assets:
            rid = r["id"]
            if rid in non_managed_ids:
                raise ValueError(f"Asset ID collision between managed snapshot and non-managed asset: {rid}")

        # 2. Thay thế phần managed, bảo toàn phần non-managed
        base_assets = [
            a for a in self.all_assets
            if not self._is_storage_managed(a)
        ]
        new_managed = [self._dict_to_studio_asset(r) for r in managed_assets]
        self.all_assets = base_assets + new_managed

        # 3. Ghi nhận ID đang chọn trước khi cập nhật Model
        selected_id = self._get_asset_id(self.selected_asset) if self.selected_asset else None

        # 4. Invalidation các thành phần phụ trợ
        if hasattr(self, "suggester") and self.suggester is not None:
            self.suggester.set_assets(self.all_assets)

        self._populate_cards()
        self._sync_storage_rail(taxonomy)
        self._rebuild_categories_column()

        # Kiểm tra tính hợp lệ của subcategory hiện tại
        if hasattr(self, "current_sub_cat") and self.current_sub_cat not in ("all", "Tất cả"):
            cur_tab = getattr(self, "current_rail_tab", "")
            valid_subs = {self._get_asset_sub(a) for a in self.all_assets if self._get_asset_tab(a) == cur_tab}
            if self.current_sub_cat not in valid_subs:
                self.current_sub_cat = "all"

        self._refresh_grid()

        # 5. Phục hồi hoặc dọn sạch lựa chọn (selection & Inspector)
        if selected_id:
            matched = next((a for a in self.all_assets if self._get_asset_id(a) == selected_id), None)
            if matched is not None and hasattr(self, "asset_model") and hasattr(self, "asset_filter"):
                src_idx = self.asset_model.index_of_asset_id(selected_id)
                if src_idx.isValid():
                    proxy_idx = self.asset_filter.mapFromSource(src_idx)
                    if proxy_idx.isValid() and proxy_idx.row() >= 0:
                        self.selected_asset = matched
                        if hasattr(self, "asset_grid"):
                            self.asset_grid.setCurrentIndex(proxy_idx)
                        self._update_inspector_details()
                    else:
                        self.selected_asset = None
                        if hasattr(self, "asset_grid"):
                            self.asset_grid.clearSelection()
                        self._update_inspector_details()
                else:
                    self.selected_asset = None
                    if hasattr(self, "asset_grid"):
                        self.asset_grid.clearSelection()
                    self._update_inspector_details()
            else:
                self.selected_asset = None
                if hasattr(self, "asset_grid"):
                    self.asset_grid.clearSelection()
                self._update_inspector_details()
        else:
            self.selected_asset = None
            if hasattr(self, "asset_grid"):
                self.asset_grid.clearSelection()
            self._update_inspector_details()

        # 6. Ghi nhận signature checkpoint sau khi apply thành công
        self._last_storage_sig = generation_sig

    def _rescan_assets(self):
        AssetIndexer.clear_cache()
        indexer = AssetIndexer()
        def dicts_to_assets(dicts, tab, category, sub, badge):
            return [
                StudioAsset(
                    id=d["id"], name=d["name"], tab=tab, category=d.get("category", category),
                    sub=d.get("sub_category") or d.get("pack_name") or sub, badge_icon=badge,
                    file_path=d.get("file_path", ""),
                    thumbnail_path=d.get("thumbnail_path", ""),
                    preview_path=d.get("preview_path", ""),
                    pack_name=d.get("pack_name", ""),
                    duration_sec=d.get("duration_sec"),
                ) for d in dicts
            ]
        scanned_titles = indexer.scan_titles()
        self.all_presets = [p.model_copy(deep=True) for p in BUILTIN_PRESETS] + dicts_to_assets(scanned_titles, "text", "custom", "Tùy chỉnh", "🔤")
        scanned_transitions = indexer.scan_transitions()
        self.all_transitions = BUILTIN_TRANSITIONS.copy() + dicts_to_assets(scanned_transitions, "trans", "custom", "Tùy chỉnh", "🎬")
        scanned_luts = indexer.scan_luts()
        self.all_luts = dicts_to_assets(scanned_luts, "lut", "cinema", "Màu tự động", "🎨") if scanned_luts else []
        scanned_memes = indexer.scan_memes()
        self.all_memes = dicts_to_assets(scanned_memes, "meme", "trending", "Meme", "🎭") if scanned_memes else []
        scanned_sfx = indexer.scan_sfx()
        self.all_sfx = dicts_to_assets(scanned_sfx, "sfx", "accent", "Âm thanh", "🔊") if scanned_sfx else []
        scanned_overlays = indexer.scan_overlays()
        self.all_overlays = dicts_to_assets(scanned_overlays, "overlay", "cinematic", "Lớp phủ", "✨") if scanned_overlays else []
        self.all_icons = []
        
        music_data = indexer.scan_music_assets()
        scanned_bgm = []
        for mood, items in music_data.items():
            for itm in items:
                scanned_bgm.append(
                    StudioAsset(
                        id=itm["id"],
                        name=itm["name"],
                        tab="bgm",
                        category=itm.get("mood", mood),
                        sub=itm.get("mood", mood),
                        badge_icon="🎵",
                        duration=itm.get("duration", 120.0),
                        file_path=itm.get("file_path", ""),
                        thumbnail_path=itm.get("thumbnail_path", "")
                    )
                )
        self.all_bgm = scanned_bgm

        self.all_assets = []
        self.all_assets.extend(self.all_presets)
        self.all_assets.extend(self.all_transitions)
        self.all_assets.extend(self.all_luts)
        self.all_assets.extend(self.all_icons)
        self.all_assets.extend(self.all_overlays)
        self.all_assets.extend(self.all_memes)
        self.all_assets.extend(self.all_sfx)
        self.all_assets.extend(self.all_bgm)
        self._merge_storage_assets()
        self._sync_storage_rail()
        
        if hasattr(self, "suggester"):
            self.suggester.set_assets(self.all_assets)
            self._on_context_sentence_analyzed()

        self._populate_cards()
        self._rebuild_categories_column()
        self._refresh_grid()
        self.library_assets_changed.emit(self.all_assets)
        self._show_toast("✅ Đã quét và cập nhật lại toàn bộ kho tài nguyên!")

    def _set_trans_alignment(self, align: str):
        self.trans_alignment = align
        self._update_trans_alignment_styles()

    def _update_trans_alignment_styles(self):
        is_head = (getattr(self, "trans_alignment", "tail") == "head")
        if hasattr(self, "btn_trans_head") and hasattr(self, "btn_trans_tail"):
            self.btn_trans_head.setChecked(is_head)
            self.btn_trans_tail.setChecked(not is_head)
            for btn, checked in [(self.btn_trans_head, is_head), (self.btn_trans_tail, not is_head)]:
                bg = ThemeColors.BG_CARD_ACTIVE if checked else ThemeColors.BG_CARD
                border = ThemeColors.PRIMARY if checked else ThemeColors.BORDER_DEFAULT
                col = "#FFFFFF" if checked else ThemeColors.TEXT_MUTED
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {bg};
                        border: 1px solid {border};
                        border-radius: 6px;
                        color: {col};
                        font-size: 11px;
                        font-weight: {'bold' if checked else 'normal'};
                    }}
                """)
            hint = "Áp dụng vào ĐẦU clip đang chọn." if is_head else "Áp dụng vào CUỐI clip đang chọn."
            frames = getattr(self, "trans_frames", 12)
            self.lbl_track_hint.setText(f"{hint} ({frames} frames)")

    def _on_trans_frames_changed(self, val: int):
        self.trans_frames = val
        if hasattr(self, "lbl_trans_frames_val"):
            self.lbl_trans_frames_val.setText(f"{val} frames")
        if hasattr(self, "trans_loop_widget"):
            self.trans_loop_widget.frames = val
            self.trans_loop_widget.update()
        self._update_trans_alignment_styles()

    def _toggle_inspector_aspect_ratio(self):
        if self.current_aspect_ratio == "16:9":
            self.current_aspect_ratio = "9:16"
        else:
            self.current_aspect_ratio = "16:9"
        self.inspector_aspect_btn.setText(f"📐 {self.current_aspect_ratio}")
        self._update_text_preview_aspect()

    def _update_text_preview_aspect(self):
        if not hasattr(self, "inspector_text_preview"):
            return
        if self.current_aspect_ratio == "9:16":
            self.inspector_text_preview.setFixedHeight(180)
            self.inspector_text_preview.setStyleSheet(f"""
                QFrame {{
                    background-color: #09090b;
                    border: 2px solid {ThemeColors.PRIMARY};
                    border-radius: 12px;
                    margin: 0px 36px;
                }}
            """)
        else:
            self.inspector_text_preview.setFixedHeight(130)
            self.inspector_text_preview.setStyleSheet(f"""
                QFrame {{
                    background-color: {ThemeColors.BG_CARD};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 10px;
                    margin: 0px;
                }}
            """)
        if hasattr(self, "text_live_preview"):
            self.text_live_preview.set_aspect_ratio(self.current_aspect_ratio)

    def _on_lut_split_slider_changed(self, value: int):
        if hasattr(self, "lut_split_widget"):
            self.lut_split_widget.set_split(value)
        if hasattr(self, "lbl_split_pct"):
            self.lbl_split_pct.setText(f"{value}%")

    def _toggle_inspector_sfx_play(self):
        if hasattr(self, "audio_inspector_widget"):
            self.audio_inspector_widget.toggle_playback()
        elif hasattr(self, "sfx_waveform_canvas") and self.sfx_waveform_canvas.is_playing:
            self._stop_inspector_sfx_play()
        else:
            self._start_inspector_sfx_play()

    def _start_inspector_sfx_play(self):
        if hasattr(self, "audio_inspector_widget"):
            self.audio_inspector_widget.start_playback()
        else:
            if hasattr(self, "sfx_waveform_canvas"):
                self.sfx_waveform_canvas.is_playing = True
                self.sfx_waveform_canvas.update()
            if hasattr(self, "sfx_play_btn"):
                self.sfx_play_btn.setText("⏸ Dừng")

    def _stop_inspector_sfx_play(self):
        if hasattr(self, "audio_inspector_widget"):
            self.audio_inspector_widget.stop_playback()
        else:
            if hasattr(self, "sfx_waveform_canvas"):
                self.sfx_waveform_canvas.is_playing = False
                self.sfx_waveform_canvas.update()
            if hasattr(self, "sfx_play_btn"):
                self.sfx_play_btn.setText("▶ Nghe thử")

    def _on_inspector_sfx_playback_finished(self):
        self._stop_inspector_sfx_play()

    def _show_asset_collections_popup(self):
        if not self.selected_asset:
            return
        aid = getattr(self.selected_asset, "id", "") or (self.selected_asset.get("id") if isinstance(self.selected_asset, dict) else "")
        if not aid:
            return

        menu = QMenu(self)
        colls = CollectionsManager.get_collection_names()
        current_colls = set(CollectionsManager.get_asset_collections(aid))

        if colls:
            for cname in colls:
                in_coll = cname in current_colls
                mark = "✔ " if in_coll else "   "
                act = menu.addAction(f"{mark}📁 {cname}")
                act.setData((cname, in_coll))
            menu.addSeparator()

        act_new = menu.addAction("➕ Tạo bộ sưu tập mới...")

        pos = self.btn_insp_collection.mapToGlobal(QPoint(0, self.btn_insp_collection.height()))
        selected_act = menu.exec(pos)
        if not selected_act:
            return

        if selected_act == act_new:
            name, ok = QInputDialog.getText(self, "Tạo bộ sưu tập mới", "Nhập tên bộ sưu tập:")
            if ok and name.strip():
                cname = name.strip()
                CollectionsManager.create_collection(cname)
                CollectionsManager.add_asset_to_collection(cname, aid)
                self._update_inspector_details()
                if self.current_rail_tab == "fav":
                    self._rebuild_categories_column()
                    self._refresh_grid()
                self._show_toast(f"✅ Đã thêm vào bộ sưu tập '{cname}'!")
        elif selected_act.data():
            cname, in_coll = selected_act.data()
            if in_coll:
                CollectionsManager.remove_asset_from_collection(cname, aid)
                self._show_toast(f"Đã bỏ khỏi bộ sưu tập '{cname}'")
            else:
                CollectionsManager.add_asset_to_collection(cname, aid)
                self._show_toast(f"✅ Đã thêm vào bộ sưu tập '{cname}'!")
            self._update_inspector_details()
            if self.current_rail_tab == "fav":
                self._rebuild_categories_column()
                self._refresh_grid()

    def _update_inspector_details(self):
        if not self.selected_asset:
            return
        if hasattr(self, "_empty_inspector_label"):
            self._empty_inspector_label.hide()
        for widget, was_hidden in getattr(self, "_inspector_hidden_widgets", []):
            widget.setVisible(not was_hidden)
        self._inspector_hidden_widgets = []

        name = self.selected_asset.get("name", "") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "name", "")
        self.lbl_insp_title.setText(f"<b>{name}</b>")
        sub = (self.selected_asset.get("sub") or self.selected_asset.get("description", "")) if isinstance(self.selected_asset, dict) else (getattr(self.selected_asset, "sub", "") or getattr(self.selected_asset, "description", ""))
        pack_name = getattr(self.selected_asset, "pack_name", "") or (self.selected_asset.get("pack_name") if isinstance(self.selected_asset, dict) else "")
        if pack_name and pack_name != "Mặc định":
            sub_text = f"📦 {pack_name} · {sub}" if sub and sub != pack_name else f"📦 {pack_name}"
            self.lbl_insp_sub.setText(sub_text)
        else:
            self.lbl_insp_sub.setText(sub)

        aid = getattr(self.selected_asset, "id", "") or (self.selected_asset.get("id") if isinstance(self.selected_asset, dict) else "")
        if hasattr(self, "lbl_insp_collections"):
            asset_colls = CollectionsManager.get_asset_collections(aid)
            if asset_colls:
                self.lbl_insp_collections.setText(" ".join([f"[{c}]" for c in asset_colls]))
                self.lbl_insp_collections.show()
            else:
                self.lbl_insp_collections.setText("")
                self.lbl_insp_collections.hide()

        asset_tab = str(self.selected_asset.get("tab", "") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "tab", "")).lower()
        is_text = isinstance(self.selected_asset, TextStylePreset) or asset_tab == "text"
        is_lut = asset_tab in ("lut", "luts")
        is_sfx = asset_tab == "sfx"
        is_bgm = asset_tab in ("bgm", "audio", "music")
        is_trans = isinstance(self.selected_asset, TransitionStylePreset) or asset_tab in ("trans", "transition", "transitions")

        if is_text:
            self.icon_meme_widget.stop_playback()
            self.inspector_aspect_btn.show()
            self.inspector_text_preview.show()
            self.inspector_lut_viewport.hide()
            self.inspector_sfx_viewport.hide()
            self.inspector_trans_viewport.hide()
            self.inspector_icon_viewport.hide()

            self.grp_params.show()
            if hasattr(self, "grp_sample_text"):
                self.grp_sample_text.show()
            if hasattr(self, "grp_duration"):
                self.grp_duration.show()
            if hasattr(self, "grp_typography"):
                self.grp_typography.show()
            if hasattr(self, "grp_swatches"):
                self.grp_swatches.show()
            if hasattr(self, "grp_motion"):
                self.grp_motion.show()
            if hasattr(self, "grp_track"):
                self.grp_track.show()
            if hasattr(self, "grp_trans_pos"):
                self.grp_trans_pos.hide()

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 3 (V3)", "Video Track 1 (V1)"])
            self.lbl_track_hint.setText("Đề xuất: Chèn lên V2/V3 để tránh đè video chính V1.")

            controls_to_block = [
                self.txt_single_title,
                self.combo_font,
                self.slide_font_size,
                getattr(self, "slide_stroke_width", None)
            ]
            for ctrl in controls_to_block:
                if ctrl:
                    ctrl.blockSignals(True)

            try:
                pid = getattr(self.selected_asset, "id", "")
                if not getattr(self, "_user_has_custom_title", False):
                    sig_text = PRESET_SIGNATURE_SAMPLES.get(pid, getattr(self.selected_asset, "name", "ResolveFlow Studio"))
                    self.txt_single_title.setText(sig_text)

                font_val = getattr(self.selected_asset, "font", "Montserrat")
                f_idx = self.combo_font.findText(font_val)
                if f_idx >= 0:
                    self.combo_font.setCurrentIndex(f_idx)
                else:
                    self.combo_font.addItem(font_val)
                    self.combo_font.setCurrentIndex(self.combo_font.count() - 1)
                sz_val = getattr(self.selected_asset, "size", 72)
                self.slide_font_size.setValue(int(sz_val))
                if hasattr(self, "lbl_font_size_val"):
                    self.lbl_font_size_val.setText(f"{int(sz_val)} px")

                out_w = getattr(self.selected_asset, "outline_width", 0.15)
                if hasattr(self, "slide_stroke_width"):
                    self.slide_stroke_width.setValue(int(out_w * 100.0))
                if hasattr(self, "lbl_stroke_width_val"):
                    self.lbl_stroke_width_val.setText(f"{out_w:.2f}")

                w_val = getattr(self.selected_asset, "weight", "bold")
                if hasattr(self, "weight_buttons"):
                    target_w = "extrabold" if w_val in ("extra_bold", "extrabold", "black") else ("regular" if w_val in ("normal", "regular", "light") else "bold")
                    for k, b in self.weight_buttons.items():
                        b.setChecked(k == target_w)
                    self._update_weight_button_styles()

                anim_val = getattr(self.selected_asset, "animation", "pop")
                if hasattr(self, "anim_chips"):
                    if "glitch" in pid or anim_val == "glitch" or "showbiz" in pid:
                        target_anim = "glitch"
                    elif "paper" in pid or anim_val == "paper":
                        target_anim = "paper"
                    elif anim_val in ("box", "box_highlight") or "highlight" in pid or "pill" in pid or "box" in pid:
                        target_anim = "box_highlight"
                    elif anim_val == "glow" or "neon" in pid or "glow" in pid:
                        target_anim = "glow"
                    elif anim_val in ("typewriter", "vhs") or "typewriter" in pid or "vhs" in pid or "crime" in pid:
                        target_anim = "typewriter"
                    elif anim_val in ("slide", "fade_up", "fade") or "slide" in pid or "apple" in pid or "khoai_lang" in pid or "luxury" in pid:
                        target_anim = "slide"
                    elif anim_val == "bounce" or "bounce" in pid:
                        target_anim = "bounce"
                    elif anim_val == "static" or "retro" in pid or "minimal" in pid:
                        target_anim = "static"
                    else:
                        target_anim = "pop"

                    if target_anim not in self.anim_chips:
                        target_anim = "pop"

                    for k, b in self.anim_chips.items():
                        b.setChecked(k == target_anim)
                    self._update_anim_chip_styles()

                curve_val = getattr(self.selected_asset, "timing_curve", "spring")
                if hasattr(self, "curve_chips"):
                    target_curve = curve_val if curve_val in self.curve_chips else "spring"
                    for k, b in self.curve_chips.items():
                        b.setChecked(k == target_curve)
                    self._update_curve_chip_styles()

                std_col = getattr(self.selected_asset, "standard_color", None) or "#FFFFFF"
                hl_col = getattr(self.selected_asset, "highlight_color", None) or "#FACC15"
                out_col = getattr(self.selected_asset, "outline_color", None) or "#000000"
                box_col = getattr(self.selected_asset, "box_color", None) or getattr(self.selected_asset, "glow_color", None) or "#EF4444"
                self._set_swatch_color("text", std_col, refresh=False)
                self._set_swatch_color("highlight", hl_col, refresh=False)
                self._set_swatch_color("stroke", out_col, refresh=False)
                self._set_swatch_color("box_glow", box_col, refresh=False)

            finally:
                for ctrl in controls_to_block:
                    if ctrl:
                        ctrl.blockSignals(False)

            self._update_text_preview_from_params()
            self.btn_insert_title_playhead.setText("🚀 Chèn Text cơ bản")
            self.btn_insert_title_playhead.setToolTip(
                "Chèn tiêu đề cơ bản lên Timeline (phông, cỡ, kiểu, màu). Lưu ý: Timeline comp chưa hỗ trợ viền (stroke) và đường cong (curve) tùy biến; để giữ trọn vẹn mọi hiệu ứng, vui lòng dùng 'Sao chép mã Fusion'."
            )

        elif is_lut:
            self.icon_meme_widget.stop_playback()
            self.inspector_aspect_btn.hide()
            self.inspector_text_preview.hide()
            self.inspector_lut_viewport.show()
            self.inspector_sfx_viewport.hide()
            self.inspector_trans_viewport.hide()
            self.inspector_icon_viewport.hide()

            self.grp_params.show()
            if hasattr(self, "grp_sample_text"):
                self.grp_sample_text.hide()
            if hasattr(self, "grp_duration"):
                self.grp_duration.hide()
            if hasattr(self, "grp_typography"):
                self.grp_typography.hide()
            if hasattr(self, "grp_swatches"):
                self.grp_swatches.hide()
            if hasattr(self, "grp_motion"):
                self.grp_motion.hide()
            if hasattr(self, "grp_track"):
                self.grp_track.show()
            if hasattr(self, "grp_trans_pos"):
                self.grp_trans_pos.hide()

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Timeline (Màu toàn bộ)", "Clip đang chọn"])
            self.lbl_track_hint.setText("Áp màu LUT điện ảnh lên Node cấp Timeline hoặc Clip.")

            pal = getattr(self.selected_asset, "pal", [])
            self.lut_split_widget.set_lut(name, pal, asset=self.selected_asset)
            self.btn_insert_title_playhead.setText("🎨 Áp LUT lên Timeline")

        elif is_sfx or is_bgm:
            self.icon_meme_widget.stop_playback()
            self.inspector_aspect_btn.hide()
            self.inspector_text_preview.hide()
            self.inspector_lut_viewport.hide()
            self.inspector_sfx_viewport.show()
            self.inspector_trans_viewport.hide()
            self.inspector_icon_viewport.hide()

            self.grp_params.show()
            if hasattr(self, "grp_sample_text"):
                self.grp_sample_text.hide()
            if hasattr(self, "grp_duration"):
                self.grp_duration.hide()
            if hasattr(self, "grp_typography"):
                self.grp_typography.hide()
            if hasattr(self, "grp_swatches"):
                self.grp_swatches.hide()
            if hasattr(self, "grp_motion"):
                self.grp_motion.hide()
            if hasattr(self, "grp_track"):
                self.grp_track.show()
            if hasattr(self, "grp_trans_pos"):
                self.grp_trans_pos.hide()

            self.combo_target_track.clear()
            if is_bgm:
                self.combo_target_track.addItem("Tự động (Tìm track trống A4+)", {"mode": "auto", "track": None})
                self.combo_target_track.addItem("Audio Track 4 (A4 - BGM gợi ý)", {"mode": "timeline_track", "track": 4})
                self.combo_target_track.addItem("Audio Track 2 (A2)", {"mode": "timeline_track", "track": 2})
                self.combo_target_track.addItem("Audio Track 1 (A1)", {"mode": "timeline_track", "track": 1})
                self.combo_target_track.addItem("Track mới (Tự tạo track)", {"mode": "new_track", "track": None})
                self.lbl_track_hint.setText("Nhạc nền stereo chuẩn phát song song trên Track A4/A5 (Tự động tìm track trống).")
            else:
                self.combo_target_track.addItem("Tự động (Tìm track trống A2+)", {"mode": "auto", "track": None})
                self.combo_target_track.addItem("Audio Track 2 (A2 - SFX gợi ý)", {"mode": "timeline_track", "track": 2})
                self.combo_target_track.addItem("Audio Track 1 (A1 - Đè voice)", {"mode": "timeline_track", "track": 1})
                self.combo_target_track.addItem("Track mới (Tự tạo track)", {"mode": "new_track", "track": None})
                self.lbl_track_hint.setText("Gợi ý: Tự động tìm track trống từ A2 để tránh đè giọng nói chính trên A1.")

            if hasattr(self, "audio_inspector_widget"):
                self.audio_inspector_widget.set_asset(self.selected_asset)
            else:
                pid = (self.selected_asset.get("id") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "id", None)) or ("sfx" if is_sfx else "bgm")
                raw_dur = self.selected_asset.get("duration") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "duration", None)
                dur = float(raw_dur) if raw_dur is not None else (2.0 if is_sfx else 120.0)
                if hasattr(self, "sfx_waveform_canvas"):
                    self.sfx_waveform_canvas.set_preset_id(pid, num_bars=32)
                mins = int(dur // 60)
                secs = int(dur % 60)
                if hasattr(self, "sfx_time_lbl"):
                    self.sfx_time_lbl.setText(f"{mins}:{secs:02d}" if dur >= 60 else f"{dur:.1f}s")
                real_file = self.selected_asset.get("file_path", "") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "file_path", "")
                if hasattr(self, "sfx_hint_lbl"):
                    self.sfx_hint_lbl.setText(os.path.basename(real_file) if real_file else f"{pid}.wav")
                if hasattr(self, "sfx_play_btn"):
                    self.sfx_play_btn.setText("▶ Nghe thử")
            self.btn_insert_title_playhead.setText("🎵 Chèn nhạc nền tại Playhead" if is_bgm else "🔊 Chèn âm thanh tại Playhead")

        elif is_trans:
            self.inspector_aspect_btn.hide()
            self.inspector_text_preview.hide()
            self.inspector_lut_viewport.hide()
            self.inspector_sfx_viewport.hide()

            thumb_path = (
                self.selected_asset.get("thumbnail_path", "")
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "thumbnail_path", "")
            ) or ""
            prev_path = (
                self.selected_asset.get("preview_path", "")
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "preview_path", "")
            ) or ""
            fpath = (
                self.selected_asset.get("file_path", "")
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "file_path", "")
            ) or ""

            video_file = ""
            try:
                from src.ui.lux.library.transition_preview_generator import (
                    resolve_transition_preview_video,
                    resolve_drfx_thumbnail,
                )
                video_file = resolve_transition_preview_video(self.selected_asset) or ""
                if not thumb_path:
                    thumb_path = resolve_drfx_thumbnail(self.selected_asset) or ""
            except Exception:
                pass

            if not video_file:
                _vid_exts = (".mp4", ".mov", ".webm", ".mkv", ".avi")
                if prev_path and prev_path.lower().endswith(_vid_exts) and os.path.isfile(prev_path):
                    video_file = prev_path
                elif fpath and fpath.lower().endswith(_vid_exts) and os.path.isfile(fpath):
                    video_file = fpath

            has_playable_video = bool(video_file and os.path.isfile(video_file))

            kind = (
                self.selected_asset.get("category", "transform")
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "category", "transform")
            )
            frames = (
                self.selected_asset.get("frames", getattr(self, "trans_frames", 12))
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "frames", getattr(self, "trans_frames", 12))
            )
            if hasattr(self, "slide_trans_frames"):
                self.slide_trans_frames.setValue(frames)

            if has_playable_video:
                try:
                    from src.ui.lux.library.transition_preview_generator import build_verified_video_preview_payload
                    preview_asset_payload = build_verified_video_preview_payload(self.selected_asset, video_file)
                except Exception:
                    preview_asset_payload = self.selected_asset
                self.inspector_trans_viewport.hide()
                self.inspector_icon_viewport.show()
                self.icon_meme_widget.set_asset(preview_asset_payload, sub=sub)
                if getattr(self.icon_meme_widget, "resolved_file_path", "") and self.isVisible():
                    self.icon_meme_widget.start_auto_preview(muted=True)
                if not getattr(self.icon_meme_widget, "resolved_file_path", ""):
                    self.icon_meme_widget.stop_playback()
                    self.inspector_trans_viewport.show()
                    self.inspector_icon_viewport.hide()
                    self.trans_loop_widget.set_transition(
                        name, kind, frames, asset=self.selected_asset, thumb_path=thumb_path
                    )
            else:
                self.icon_meme_widget.stop_playback()
                self.inspector_trans_viewport.show()
                self.inspector_icon_viewport.hide()
                self.trans_loop_widget.set_transition(
                    name, kind, frames, asset=self.selected_asset, thumb_path=thumb_path
                )

            self.grp_params.show()
            if hasattr(self, "grp_sample_text"):
                self.grp_sample_text.hide()
            if hasattr(self, "grp_duration"):
                self.grp_duration.hide()
            if hasattr(self, "grp_typography"):
                self.grp_typography.hide()
            if hasattr(self, "grp_swatches"):
                self.grp_swatches.hide()
            if hasattr(self, "grp_motion"):
                self.grp_motion.hide()
            if hasattr(self, "grp_track"):
                self.grp_track.hide()
            if hasattr(self, "grp_trans_pos"):
                self.grp_trans_pos.show()

            self.trans_loop_widget.set_transition(
                name, kind, frames, asset=self.selected_asset, thumb_path=thumb_path
            )
            self._update_trans_alignment_styles()
            self.btn_insert_title_playhead.setText("➡️ Áp dụng vào Timeline")

        else:
            self.inspector_aspect_btn.hide()
            self.inspector_text_preview.hide()
            self.inspector_lut_viewport.hide()
            self.inspector_sfx_viewport.hide()
            self.inspector_trans_viewport.hide()
            self.inspector_icon_viewport.show()

            self.grp_params.show()
            if hasattr(self, "grp_sample_text"):
                self.grp_sample_text.hide()
            if hasattr(self, "grp_duration"):
                self.grp_duration.show()
            if hasattr(self, "grp_typography"):
                self.grp_typography.hide()
            if hasattr(self, "grp_swatches"):
                self.grp_swatches.hide()
            if hasattr(self, "grp_motion"):
                self.grp_motion.hide()
            if hasattr(self, "grp_track"):
                self.grp_track.show()
            if hasattr(self, "grp_trans_pos"):
                self.grp_trans_pos.hide()

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 3 (V3)", "Video Track 1 (V1)"])
            self.lbl_track_hint.setText("Đề xuất: Chèn lên V2/V3 để tránh đè video chính V1.")

            tab_type = self.selected_asset.get("tab", "icon") if isinstance(self.selected_asset, dict) else getattr(self.selected_asset, "tab", "icon")
            self.icon_meme_widget.set_asset(self.selected_asset, sub=sub)

            if tab_type == "meme":
                self.lbl_track_hint.setText("🎬 Đề xuất: Kéo thả trực tiếp card vào DaVinci Timeline để chèn meme.")
                self.btn_insert_title_playhead.setText("🎬 Chèn Video Meme tại Playhead")
            elif tab_type == "overlay":
                self.lbl_track_hint.setText("✨ Đề xuất Track V2: Light leak chọn Composite Mode = Screen. Phông xanh dùng 3D Keyer.")
                self.btn_insert_title_playhead.setText("✨ Chèn Lớp phủ (Overlay) lên V2")
            else:
                self.lbl_track_hint.setText("🎨 Đề xuất Track V2: Kéo thả ảnh trong suốt (Transparent PNG) đè lên video chính.")
                self.btn_insert_title_playhead.setText("🎨 Chèn Sticker lên Track V2")

    def _on_sample_text_changed(self, text: str):
        self._update_text_preview_from_params()

    def _on_sample_text_edited(self, text: str):
        self._user_has_custom_title = True

    def _on_asset_quick_play_requested(self, asset_id: str):
        self._on_card_selected(asset_id)
        if not self.selected_asset:
            return
        tab_type = (
            self.selected_asset.get("tab", "")
            if isinstance(self.selected_asset, dict)
            else getattr(self.selected_asset, "tab", "")
        )
        if tab_type in ("meme", "overlay"):
            if hasattr(self, "icon_meme_widget"):
                self.icon_meme_widget._start_playback()
        elif tab_type in ("trans", "transition", "transitions") or isinstance(self.selected_asset, TransitionStylePreset):
            if (
                hasattr(self, "inspector_icon_viewport")
                and self.inspector_icon_viewport.isVisible()
                and hasattr(self, "icon_meme_widget")
                and getattr(self.icon_meme_widget, "resolved_file_path", "")
            ):
                self.icon_meme_widget._start_playback()
            elif hasattr(self, "trans_loop_widget"):
                self.trans_loop_widget.progress = 0.0
                self.trans_loop_widget.update()

    def _on_font_changed(self, font_name: str):
        self.current_font = font_name
        self._update_text_preview_from_params()

    def _on_font_size_changed(self, value: int):
        self.lbl_font_size_val.setText(f"{value} px")
        self.current_font_size = value
        self._update_text_preview_from_params()

    def _on_stroke_width_changed(self, value: int):
        self.lbl_stroke_width_val.setText(f"{value/100.0:.2f}")
        self._update_text_preview_from_params()

    def _on_weight_button_clicked(self, key: str):
        for k, b in self.weight_buttons.items():
            b.setChecked(k == key)
        self._update_weight_button_styles()
        self._update_text_preview_from_params()

    def _update_weight_button_styles(self):
        for k, btn in self.weight_buttons.items():
            is_on = btn.isChecked()
            bg = ThemeColors.BG_CARD_ACTIVE if is_on else ThemeColors.BG_CARD
            border = ThemeColors.PRIMARY if is_on else ThemeColors.BORDER_DEFAULT
            col = "#FFFFFF" if is_on else ThemeColors.TEXT_MUTED
            weight = "bold" if is_on else "normal"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    border: 1px solid {border};
                    border-radius: 4px;
                    color: {col};
                    font-size: 11px;
                    font-weight: {weight};
                    padding: 2px 6px;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.PRIMARY};
                }}
            """)

    def _on_anim_chip_clicked(self, key: str):
        for k, b in self.anim_chips.items():
            b.setChecked(k == key)
        self._update_anim_chip_styles()
        self._update_text_preview_from_params()

    def _update_anim_chip_styles(self):
        for k, btn in self.anim_chips.items():
            is_on = btn.isChecked()
            bg = ThemeColors.PRIMARY if is_on else ThemeColors.BG_CARD
            border = ThemeColors.CYAN_HI if is_on else ThemeColors.BORDER_DEFAULT
            col = "#FFFFFF" if is_on else ThemeColors.TEXT_MUTED
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    border: 1px solid {border};
                    border-radius: 12px;
                    color: {col};
                    font-size: 11px;
                    font-weight: {"bold" if is_on else "normal"};
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.CYAN};
                    color: #FFFFFF;
                }}
            """)

    def _on_curve_chip_clicked(self, key: str):
        for k, b in self.curve_chips.items():
            b.setChecked(k == key)
        self._update_curve_chip_styles()

    def _update_curve_chip_styles(self):
        for k, btn in self.curve_chips.items():
            is_on = btn.isChecked()
            bg = ThemeColors.BG_CARD_ACTIVE if is_on else ThemeColors.BG_CARD
            border = ThemeColors.CYAN_HI if is_on else ThemeColors.BORDER_DEFAULT
            col = ThemeColors.CYAN_HI if is_on else ThemeColors.TEXT_MUTED
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    border: 1px solid {border};
                    border-radius: 12px;
                    color: {col};
                    font-size: 11px;
                    font-weight: {"bold" if is_on else "normal"};
                    padding: 2px 8px;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.CYAN_HI};
                }}
            """)

    def _pick_color(self, key: str):
        curr = self.swatch_colors.get(key, "#FFFFFF")
        col = QColorDialog.getColor(QColor(curr), self, f"Chọn màu cho {key.capitalize()}")
        if col.isValid():
            self._set_swatch_color(key, col.name().upper())

    def _set_swatch_color(self, key: str, hex_color: str, refresh: bool = True):
        if not hex_color:
            defaults = {"text": "#FFFFFF", "highlight": "#FACC15", "stroke": "#000000", "box_glow": "#EF4444"}
            hex_color = defaults.get(key, "#FFFFFF")
        hex_color = str(hex_color)
        if not hasattr(self, "swatch_colors"):
            self.swatch_colors = {}
        self.swatch_colors[key] = hex_color
        btn = getattr(self, f"btn_color_{key}", None)
        if btn:
            is_light = hex_color.upper() in ("#FFFFFF", "#FFF", "#FACC15", "#A3E635", "#FEF3C7")
            text_col = "#18181b" if is_light else "#FFFFFF"
            labels = {"text": "Chữ", "highlight": "Highlight", "stroke": "Viền", "box_glow": "Hộp/Glow"}
            name_lbl = labels.get(key, key.capitalize())
            btn.setText(f"■ {name_lbl}\n{hex_color}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {hex_color};
                    border: 1px solid {ThemeColors.BORDER_DEFAULT};
                    border-radius: 6px;
                    color: {text_col};
                    font-size: 10px;
                    font-weight: bold;
                    padding: 4px;
                    text-align: center;
                }}
                QPushButton:hover {{
                    border-color: {ThemeColors.CYAN_HI};
                }}
            """)
        if refresh:
            self._update_text_preview_from_params()

    def _update_text_preview_from_params(self):
        if not self.selected_asset:
            return
        is_text = isinstance(self.selected_asset, TextStylePreset) or getattr(self.selected_asset, "tab", "") == "text"
        if not is_text:
            return

        name = getattr(self.selected_asset, "name", "")
        pid = getattr(self.selected_asset, "id", "")
        sample = self.txt_single_title.text().strip() or "ResolveFlow Studio"

        font_name = self.combo_font.currentText() if hasattr(self, "combo_font") else getattr(self.selected_asset, "font", "Arial")
        font_size = self.slide_font_size.value() if hasattr(self, "slide_font_size") else getattr(self.selected_asset, "size", 48)
        stroke_val = self.slide_stroke_width.value() if hasattr(self, "slide_stroke_width") else int(getattr(self.selected_asset, "outline_width", 0.15) * 100.0)
        stroke_width = stroke_val / 100.0

        weight_key = "bold"
        if hasattr(self, "weight_buttons"):
            for k, b in self.weight_buttons.items():
                if b.isChecked():
                    weight_key = k
                    break

        anim_key = "pop"
        if hasattr(self, "anim_chips"):
            for k, b in self.anim_chips.items():
                if b.isChecked():
                    anim_key = k
                    break

        curve_key = "spring"
        if hasattr(self, "curve_chips"):
            for k, b in self.curve_chips.items():
                if b.isChecked():
                    curve_key = k
                    break

        std_col = self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, "swatch_colors") else getattr(self.selected_asset, "standard_color", "#FFFFFF")
        hl_col = self.swatch_colors.get("highlight", "#FACC15") if hasattr(self, "swatch_colors") else getattr(self.selected_asset, "highlight_color", "#FACC15")
        out_col = self.swatch_colors.get("stroke", "#000000") if hasattr(self, "swatch_colors") else getattr(self.selected_asset, "outline_color", "#000000")
        box_col = self.swatch_colors.get("box_glow", "#EF4444") if hasattr(self, "swatch_colors") else getattr(self.selected_asset, "box_color", "#EF4444")

        # 1. Cập nhật selected_asset để chèn hoặc kéo thả nhận đầy đủ style tùy chỉnh
        try:
            self.selected_asset.font = font_name
            self.selected_asset.size = font_size
            self.selected_asset.outline_width = stroke_width
            self.selected_asset.standard_color = std_col
            self.selected_asset.highlight_color = hl_col
            self.selected_asset.outline_color = out_col
            self.selected_asset.box_color = box_col
            self.selected_asset.glow_color = box_col
            self.selected_asset.weight = "extra_bold" if weight_key in ("extrabold", "black", "extra_bold") else ("normal" if weight_key in ("regular", "normal") else "bold")
            self.selected_asset.animation = "box_highlight" if anim_key == "box" else anim_key
            self.selected_asset.timing_curve = curve_key
        except Exception:
            pass

        # 2. Cập nhật Text Live Preview Canvas (~30 FPS)
        if hasattr(self, "text_live_preview"):
            self.text_live_preview.asset_obj = self.selected_asset
            self.text_live_preview.file_path = (
                self.selected_asset.get("file_path", "")
                if isinstance(self.selected_asset, dict)
                else getattr(self.selected_asset, "file_path", "")
            ) or ""
            self.text_live_preview.set_params(
                text=sample,
                preset_id=pid,
                font_family=font_name,
                font_size=font_size,
                weight=weight_key,
                standard_color=std_col,
                highlight_color=hl_col,
                stroke_color=out_col,
                stroke_width=stroke_width,
                box_color=box_col,
                glow_color=box_col,
                animation="box_highlight" if anim_key == "box" else anim_key,
                timing_curve=curve_key
            )

        # 3. Cập nhật lbl_insp_preview định dạng HTML phong phú (cho test suite & tooltip)
        pt_size = min(28, max(14, int(font_size * 0.3)))
        html = format_text_preset_html(
            preset_id=pid,
            preset_name=name,
            sample_text=sample,
            standard_color=std_col,
            is_large=True,
            font_family=font_name,
            font_size_pt=pt_size,
            highlight_color=hl_col,
            stroke_color=out_col,
            stroke_width=stroke_width,
            box_color=box_col,
            glow_color=box_col,
            weight=weight_key,
            animation=anim_key
        )
        self.lbl_insp_preview.setText(html)

    def _show_toast(self, message: str):
        if hasattr(self, "lbl_toast"):
            self.lbl_toast.setText(message)
            self.lbl_toast.show()
            self.toast_timer.stop()
            self.toast_timer.start(2500)

    def _get_insert_service(self):
        if getattr(self, "_insert_service", None):
            return self._insert_service
        parent = self.parent()
        if parent and getattr(parent, "_insert_service", None):
            self._insert_service = parent._insert_service
            return self._insert_service
        try:
            from src.services.resolve_gateway import ResolveGateway
            from src.services.insert_service import InsertService
            self._gateway = ResolveGateway(self)
            state_svc = getattr(parent, "state_service", None) if parent else None
            self._insert_service = InsertService(self._gateway, state_service=state_svc, parent=self)
            return self._insert_service
        except Exception as e:
            logger.warning("Không thể khởi tạo InsertService trong TabAssets: %s", e)
            return None

    @staticmethod
    def _extract_video_duration(payload: dict) -> float:
        """
        Trích xuất duration hợp lệ cho video từ payload:
        - Ưu tiên duration_sec nếu có trong payload; fallback sang duration khi không có key duration_sec.
        - Trường hợp unknown (None, 0, số âm, NaN, Inf, bool, kiểu dữ liệu không hợp lệ) -> trả về 0.0 (sentinel).
        - Trường hợp positive finite number -> trả về float.
        - Không làm biến dị (mutate) payload hay đối tượng được chọn.
        """
        if not isinstance(payload, dict):
            return 0.0

        if "duration_sec" in payload:
            val = payload.get("duration_sec")
        else:
            val = payload.get("duration")

        if val is None or isinstance(val, bool):
            return 0.0

        try:
            import math
            fval = float(val)
            if math.isnan(fval) or math.isinf(fval) or fval <= 0.0:
                return 0.0
            return fval
        except (ValueError, TypeError):
            return 0.0

    def _insert_at_playhead(self):
        if not self.selected_asset:
            if hasattr(self, "all_presets") and self.all_presets:
                self.selected_asset = self.all_presets[0]
            elif hasattr(self, "all_assets") and self.all_assets:
                self.selected_asset = self.all_assets[0]
            else:
                return

        from src.services.insert_service import to_asset_dict, InsertResult
        asset_d = to_asset_dict(self.selected_asset)
        tab = str(asset_d.get("tab") or "").lower()
        fp = str(asset_d.get("file_path") or "").lower()
        ext = os.path.splitext(fp)[1] if fp else ""

        is_trans = tab in ("trans", "transition", "transitions") or isinstance(self.selected_asset, TransitionStylePreset) or "transitions" in fp
        is_video = not is_trans and (tab in ("meme", "overlay", "broll", "video", "sticker", "green_screen") or ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".png", ".jpg", ".jpeg", ".webp", ".svg"))
        is_sfx = tab == "sfx" or ext in (".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a")
        is_bgm = tab == "bgm"
        is_lut = tab == "lut" or ext in (".cube", ".3dl", ".dctl", ".dctle", ".drx", ".dpx")
        is_text = tab == "text" or isinstance(self.selected_asset, TextStylePreset) or (not is_trans and not is_video and not is_sfx and not is_bgm and not is_lut)

        # 1. Các danh mục chuyển đổi sang InsertService (G04-04C4-I / G04-04C4-R1)
        if is_video or is_trans or is_sfx or is_bgm or is_lut:
            svc = self._get_insert_service()
            if not svc:
                self._show_toast("❌ Dịch vụ chèn không sẵn sàng. Hãy khởi động lại ứng dụng.")
                return

            payload = dict(asset_d)
            asset_name = str(payload.get("name") or payload.get("id") or "Asset")
            asset_id = str(payload.get("id") or "")

            mode = "auto"
            preferred_track = None
            duration = 0.0
            extra_params = {}

            if is_video:
                duration = self._extract_video_duration(payload)
                if hasattr(self, "combo_target_track") and self.combo_target_track:
                    cdata = self.combo_target_track.currentData()
                    if isinstance(cdata, dict):
                        mode = cdata.get("mode", mode)
                        preferred_track = cdata.get("track", preferred_track)
                    elif cdata == "new_track":
                        mode = "new_track"
                        preferred_track = None
                    elif isinstance(cdata, int):
                        mode = "timeline_track"
                        preferred_track = cdata
                    else:
                        track_text = self.combo_target_track.currentText().strip()
                        if "New" in track_text or "Mới" in track_text:
                            mode = "new_track"
                        elif "V" in track_text:
                            import re
                            digits = re.findall(r"\d+", track_text)
                            if digits:
                                try:
                                    preferred_track = int(digits[0])
                                    mode = "timeline_track"
                                except Exception:
                                    pass
                if not preferred_track and mode == "auto":
                    preferred_track = 3

            elif is_trans:
                mode = "auto"

            elif is_sfx or is_bgm:
                raw_dur = payload.get("duration")
                if raw_dur is not None and not isinstance(raw_dur, bool):
                    try:
                        d = float(raw_dur)
                        import math
                        duration = d if (d > 0 and not math.isnan(d) and not math.isinf(d)) else (1.5 if is_sfx else 120.0)
                    except Exception:
                        duration = 1.5 if is_sfx else 120.0
                else:
                    duration = 1.5 if is_sfx else 120.0

                vol_offset = -18.0 if is_bgm else -12.0
                extra_params["volume_offset"] = vol_offset

                # Audio default luôn là auto và preferred_track = None
                mode = "auto"
                preferred_track = None

                if hasattr(self, "combo_target_track") and self.combo_target_track:
                    cdata = self.combo_target_track.currentData()
                    if isinstance(cdata, dict):
                        mode = cdata.get("mode", "auto")
                        preferred_track = cdata.get("track", None)
                    elif cdata == "auto":
                        mode = "auto"
                        preferred_track = None
                    elif cdata == "new_track":
                        mode = "new_track"
                        preferred_track = None
                    elif isinstance(cdata, int):
                        mode = "timeline_track"
                        preferred_track = cdata
                    else:
                        track_text = self.combo_target_track.currentText().strip()
                        # Hỗ trợ cả nhãn tiếng Việt mới, nhãn legacy và explicit choices:
                        if any(k in track_text for k in ("Tự động", "Auto", "A2 - SFX", "A4/A5 - BGM", "Track 4/5")):
                            mode = "auto"
                            preferred_track = None
                        elif "New" in track_text or "Mới" in track_text:
                            mode = "new_track"
                            preferred_track = None
                        elif "A" in track_text:
                            import re
                            digits = re.findall(r"\d+", track_text)
                            if digits:
                                try:
                                    preferred_track = int(digits[0])
                                    mode = "timeline_track"
                                except Exception:
                                    mode = "auto"
                                    preferred_track = None

            elif is_lut:
                if hasattr(self, "combo_target_track") and self.combo_target_track:
                    cdata = self.combo_target_track.currentData()
                    if isinstance(cdata, dict) and "track" in cdata:
                        extra_params["track"] = cdata["track"]
                    else:
                        track_text = self.combo_target_track.currentText().strip()
                        if "V" in track_text:
                            import re
                            digits = re.findall(r"\d+", track_text)
                            if digits:
                                try:
                                    extra_params["track"] = int(digits[0])
                                except Exception:
                                    pass

            # Gửi duy nhất 1 lần qua InsertService
            res = svc.insert(
                payload,
                mode=mode,
                duration=duration,
                preferred_track=preferred_track,
                extra_params=extra_params,
            )

            # Tuyệt đối KHÔNG emit insert_sfx_requested hay apply_lut_requested (chống double dispatch)
            if res and res.ok:
                msg = res.message
                if not msg:
                    track_info = f" ({res.track})" if res.track else ""
                    msg = f"Đã chèn “{asset_name}” tại Playhead{track_info}"
                self._show_toast(f"✅ {msg}")
                if asset_id:
                    RecentManager.add(asset_id)
            else:
                err = res.error if res and res.error else "Chưa thể chèn tài nguyên"
                if "chưa thay đổi" in err or "đã có LUT" in err:
                    self._show_toast(f"ℹ️ {err}")
                else:
                    self._show_toast(f"❌ {err}")
            return

        # 2. Nhánh Text / Title Presets qua InsertService (G04-04C11-I)
        if is_text:
            # Filebacked .setting macro: Truthful guidance, 0 job, 0 carrier, 0 Recent
            if ext == ".setting" or fp.endswith(".setting") or getattr(self.selected_asset, "template_type", "") == "macro" or asset_d.get("template_type") == "macro":
                self._show_toast("ℹ️ Hiệu ứng macro Fusion (.setting) chưa hỗ trợ chèn tự động lên Edit Timeline; vui lòng bấm 'Sao chép mã Fusion' để sao chép node hoặc dùng phím tắt vào Fusion Page.")
                return

            # Chặn duplicate click khi đang có tác vụ in-flight
            if getattr(self, "_in_flight_requests", None):
                self._show_toast("⚠️ Đang thực hiện tác vụ chèn, vui lòng đợi...")
                return

            svc = self._get_insert_service()
            if not svc:
                self._show_toast("❌ Dịch vụ chèn không sẵn sàng. Hãy khởi động lại ứng dụng.")
                return

            sample_text = self.txt_single_title.text().strip() if hasattr(self, "txt_single_title") else ""
            if not sample_text:
                sample_text = asset_d.get("name", "ResolveFlow Title")

            font_name = self.combo_font.currentText() if hasattr(self, "combo_font") else asset_d.get("font", "Arial")
            font_size = int(self.slide_font_size.value()) if hasattr(self, "slide_font_size") else int(asset_d.get("size", 48))

            weight = "bold"
            if hasattr(self, "weight_buttons"):
                for k, b in self.weight_buttons.items():
                    if b.isChecked():
                        weight = "extra_bold" if k in ("black", "extra_bold", "extrabold") else ("normal" if k in ("regular", "light", "normal") else "bold")
                        break

            animation = "pop"
            if hasattr(self, "anim_chips"):
                for k, b in self.anim_chips.items():
                    if b.isChecked():
                        animation = "box_highlight" if k == "box" else (k if k in ("pop", "bounce", "typewriter", "slide", "box_highlight", "glow", "static", "fade_up", "fade") else "pop")
                        break

            timing_curve = "spring"
            if hasattr(self, "curve_chips"):
                for k, b in self.curve_chips.items():
                    if b.isChecked():
                        timing_curve = "ease-in-out" if k in ("ease_in_out", "ease-in-out", "smooth") else (k if k in ("spring", "linear") else "spring")
                        break

            text_col = self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, "swatch_colors") else asset_d.get("standard_color", "#FFFFFF")
            hl_col = self.swatch_colors.get("highlight", "#FACC15") if hasattr(self, "swatch_colors") else asset_d.get("highlight_color", "#FACC15")
            stroke_col = self.swatch_colors.get("stroke", "#000000") if hasattr(self, "swatch_colors") else asset_d.get("outline_color", "#000000")
            box_glow_col = self.swatch_colors.get("box_glow", "#EF4444") if hasattr(self, "swatch_colors") else asset_d.get("box_color", "#EF4444")

            stroke_width = (float(self.slide_stroke_width.value()) / 100.0) if hasattr(self, "slide_stroke_width") else float(asset_d.get("outline_width", 0.15))
            dur = float(self.slide_title_dur.value()) / 10.0 if hasattr(self, "slide_title_dur") and self.slide_title_dur.value() > 0 else 4.0

            preferred_track = 2
            if hasattr(self, "combo_target_track") and self.combo_target_track:
                cdata = self.combo_target_track.currentData()
                if isinstance(cdata, int):
                    preferred_track = cdata
                elif isinstance(cdata, dict) and "track" in cdata:
                    preferred_track = cdata["track"]
                else:
                    track_text = self.combo_target_track.currentText().strip()
                    if "V" in track_text:
                        import re
                        digits = re.findall(r"\d+", track_text)
                        if digits:
                            try:
                                preferred_track = int(digits[0])
                            except Exception:
                                pass

            extra_params = {
                "text": sample_text,
                "font": font_name,
                "size": font_size,
                "weight": weight,
                "animation": animation,
                "timing_curve": timing_curve,
                "color": text_col,
                "highlight_color": hl_col,
                "stroke_color": stroke_col,
                "stroke_width": stroke_width,
                "box_glow_hex": box_glow_col,
                "duration": dur,
                "preferred_track": preferred_track,
                "explicit_timeline": True,
            }

            tracked_asset_id = str(asset_d.get("id") or "")
            self.insert_title_requested.emit(sample_text, tracked_asset_id, dur)

            import uuid
            request_id = f"req_{tracked_asset_id}_{uuid.uuid4().hex[:8]}"
            if not hasattr(self, "_in_flight_requests"):
                self._in_flight_requests = {}
            self._in_flight_requests[request_id] = {
                "asset_id": tracked_asset_id,
                "start_time": time.time(),
            }

            self.btn_insert_title_playhead.setEnabled(False)
            self.btn_insert_title_playhead.setText("⏳ Đang chèn...")

            if not hasattr(self, "_insert_observer_timer") or not self._insert_observer_timer:
                self._insert_observer_timer = QTimer(self)
                self._insert_observer_timer.setSingleShot(True)
                self._insert_observer_timer.timeout.connect(self._on_insert_observer_timeout)
            self._insert_observer_timer.start(15000)

            def _on_insert_finished(res: InsertResult):
                try:
                    if not hasattr(self, "btn_insert_title_playhead") or self.btn_insert_title_playhead is None:
                        return
                except Exception:
                    return

                if not hasattr(self, "_in_flight_requests") or request_id not in self._in_flight_requests:
                    return
                self._in_flight_requests.pop(request_id, None)

                if hasattr(self, "_insert_observer_timer") and self._insert_observer_timer:
                    self._insert_observer_timer.stop()

                self.btn_insert_title_playhead.setEnabled(True)
                self.btn_insert_title_playhead.setText("🚀 Chèn Text cơ bản")

                if res and res.ok:
                    msg = res.message or f"Đã chèn “{sample_text}” tại Playhead"
                    self._show_toast(f"✅ {msg} (Lưu ý: Timeline áp dụng kiểu cơ bản; dùng 'Sao chép mã Fusion' để có đầy đủ hiệu ứng viền/curve)")
                    if tracked_asset_id:
                        RecentManager.add(tracked_asset_id)
                else:
                    err = res.error if res and res.error else "Chưa thể chèn Text vào DaVinci Resolve."
                    self._show_toast(f"❌ {err}")

            svc.insert_async(
                self.selected_asset,
                mode="auto",
                duration=dur,
                preferred_track=preferred_track,
                callback=_on_insert_finished,
                extra_params=extra_params,
            )
            return

        else:
            self._show_toast(f"Đã chèn “{asset_d.get('name', 'Asset')}” vào Timeline")
            # Ghi nhận vào mục Gần đây (Recent)
            RecentManager.add(asset_d.get("id", ""))

    def _on_insert_observer_timeout(self):
        """Observer timeout: cảnh báo người dùng thao tác đang xử lý chậm; tuyệt đối không mở khóa hay rerun."""
        self._show_toast("⚠️ Thao tác chèn đang mất nhiều thời gian hơn dự kiến (15s). DaVinci Resolve có thể đang xử lý; vui lòng chờ phản hồi, không bấm chèn lại để tránh trùng lặp.")

    def _build_custom_text_preset(self, target: Any) -> Tuple[Optional[TextStylePreset], str]:
        """
        Xây dựng bản sao tùy biến (customized clone) của TextStylePreset từ các điều khiển Inspector UI.
        Base preset tuyệt đối bất biến (immutable). Các trường dữ liệu được thẩm định chặt chẽ
        trước khi khởi tạo preset mới.
        """
        base_preset = None
        if isinstance(target, TextStylePreset):
            base_preset = target
        elif isinstance(getattr(target, "native_preset", None), TextStylePreset):
            base_preset = target.native_preset
        elif isinstance(getattr(target, "_raw_obj", None), TextStylePreset):
            base_preset = target._raw_obj
        elif isinstance(target, Mapping):
            if isinstance(target.get("native_preset"), TextStylePreset):
                base_preset = target["native_preset"]
            elif isinstance(target.get("_raw_obj"), TextStylePreset):
                base_preset = target["_raw_obj"]

        sample_txt = self.txt_single_title.text().strip() if hasattr(self, "txt_single_title") else ""
        if not sample_txt:
            sample_txt = getattr(target, "name", "ResolveFlow Title") if not isinstance(target, Mapping) else str(target.get("name") or "ResolveFlow Title")

        asset_id = getattr(target, "id", "asset") if not isinstance(target, Mapping) else str(target.get("id") or "asset")
        asset_name = getattr(target, "name", "Title") if not isinstance(target, Mapping) else str(target.get("name") or "Title")

        font_name = self.combo_font.currentText() if hasattr(self, "combo_font") else (getattr(base_preset, "font", "Arial") if base_preset else "Arial")
        font_size = self.slide_font_size.value() if hasattr(self, "slide_font_size") else (getattr(base_preset, "size", 48) if base_preset else 48)
        stroke_width = (float(self.slide_stroke_width.value()) / 100.0) if hasattr(self, "slide_stroke_width") else (getattr(base_preset, "outline_width", 0.15) if base_preset else 0.15)

        weight = "bold"
        if hasattr(self, "weight_buttons"):
            for k, b in self.weight_buttons.items():
                if b.isChecked():
                    weight = "extra_bold" if k in ("black", "extra_bold", "extrabold") else ("normal" if k in ("regular", "light", "normal") else "bold")
                    break
        elif base_preset:
            weight = getattr(base_preset, "weight", "bold")

        animation = "pop"
        if hasattr(self, "anim_chips"):
            for k, b in self.anim_chips.items():
                if b.isChecked():
                    animation = "box_highlight" if k == "box" else (k if k in ("pop", "bounce", "typewriter", "slide", "box_highlight", "glow", "static", "fade_up", "fade") else "pop")
                    break
        elif base_preset:
            animation = getattr(base_preset, "animation", "pop")

        timing_curve = "spring"
        if hasattr(self, "curve_chips"):
            for k, b in self.curve_chips.items():
                if b.isChecked():
                    timing_curve = "ease-in-out" if k in ("ease_in_out", "ease-in-out", "smooth") else (k if k in ("spring", "linear") else "spring")
                    break
        elif base_preset:
            timing_curve = getattr(base_preset, "timing_curve", "spring")

        text_col = self.swatch_colors.get("text", getattr(base_preset, "standard_color", "#FFFFFF")) if hasattr(self, "swatch_colors") else (getattr(base_preset, "standard_color", "#FFFFFF") if base_preset else "#FFFFFF")
        hl_col = self.swatch_colors.get("highlight", getattr(base_preset, "highlight_color", "#FACC15")) if hasattr(self, "swatch_colors") else (getattr(base_preset, "highlight_color", "#FACC15") if base_preset else "#FACC15")
        out_col = self.swatch_colors.get("stroke", getattr(base_preset, "outline_color", "#000000")) if hasattr(self, "swatch_colors") else (getattr(base_preset, "outline_color", "#000000") if base_preset else "#000000")
        box_glow_col = self.swatch_colors.get("box_glow", getattr(base_preset, "box_color", "#EF4444")) if hasattr(self, "swatch_colors") else (getattr(base_preset, "box_color", "#EF4444") if base_preset else "#EF4444")

        update_data = {
            "font": font_name,
            "size": int(font_size),
            "weight": weight,
            "outline_width": float(stroke_width),
            "animation": animation,
            "timing_curve": timing_curve,
            "standard_color": text_col,
            "highlight_color": hl_col,
            "outline_color": out_col,
            "box_color": box_glow_col,
            "glow_color": box_glow_col,
        }

        try:
            if base_preset is not None:
                from src.core.text_preset import pydantic_dump
                base_dict = dict(pydantic_dump(base_preset))
                base_dict.update(update_data)
                custom_preset = TextStylePreset(**base_dict)
            else:
                custom_preset = TextStylePreset(id=asset_id, name=asset_name, **update_data)
            return custom_preset, sample_txt
        except Exception as e:
            logger.warning("Lỗi tạo tùy biến TextStylePreset: %s", e)
            return None, sample_txt

    def _create_drag_mime_data(self, asset: Optional[Any] = None) -> QMimeData:
        target = asset if asset is not None else self.selected_asset
        mime_data = QMimeData()
        if not target:
            return mime_data

        try:
            # 1. Kiểm tra xem target có phải là procedural text preset không
            is_procedural_text = False
            if isinstance(target, TextStylePreset) or isinstance(getattr(target, "native_preset", None), TextStylePreset) or isinstance(getattr(target, "_raw_obj", None), TextStylePreset):
                is_procedural_text = True
            elif isinstance(target, Mapping):
                if isinstance(target.get("native_preset"), TextStylePreset) or isinstance(target.get("_raw_obj"), TextStylePreset):
                    is_procedural_text = True
                elif target.get("tab") == "text" and not target.get("file_path"):
                    is_procedural_text = True
            elif getattr(target, "tab", "") == "text" and not getattr(target, "file_path", ""):
                is_procedural_text = True

            # 2. Xử lý tùy biến Text nếu là procedural text
            if is_procedural_text:
                custom_preset, sample_txt = self._build_custom_text_preset(target)
                from src.ui.lux.library.asset_grid import create_asset_mime_data
                return create_asset_mime_data(target, sample_text=sample_txt, custom_preset=custom_preset)

            # 3. Với các tài nguyên file-backed hoặc chuyển cảnh native, ủy quyền toàn bộ cho helper đã bảo vệ
            from src.ui.lux.library.asset_grid import create_asset_mime_data
            return create_asset_mime_data(target, sample_text="")

        except Exception as e:
            logger.warning("Lỗi tạo MIME kéo thả Inspector: %s", e)
            return QMimeData()

    def _get_current_fusion_macro_code(self, asset: Optional[Any] = None) -> str:
        target = asset if asset is not None else self.selected_asset
        if not target:
            self._last_fusion_export_error = "Chưa chọn tài nguyên."
            return ""

        from src.ui.lux.library.asset_grid import _normalize_asset_payload
        payload = _normalize_asset_payload(target)

        from src.core.action_guard import check_action_availability
        guard_res = check_action_availability(
            payload,
            intent="fusion_export",
            bundled_root=getattr(self, "_bundled_root", None),
        )

        if not guard_res.allowed:
            self._last_fusion_export_error = guard_res.reason or "Không thể xuất mã Fusion cho tài nguyên này."
            return ""

        if guard_res.result_code == "OK_PROCEDURAL_FUSION_EXPORT":
            custom_preset, sample_txt = self._build_custom_text_preset(target)
            if custom_preset is None:
                self._last_fusion_export_error = "Dữ liệu tùy biến kiểu chữ không hợp lệ."
                return ""
            from src.core.text_preset import FusionSettingGenerator
            try:
                return FusionSettingGenerator.generate_setting_content(custom_preset, sample_text=sample_txt)
            except Exception as e:
                logger.error("Lỗi sinh nội dung Fusion Setting từ procedural preset: %s", e)
                self._last_fusion_export_error = f"Lỗi sinh mã Fusion: {e}"
                return ""

        elif guard_res.result_code == "OK_FILEBACKED_FUSION_EXPORT":
            resolved_path = guard_res.resolved_path
            if not resolved_path:
                self._last_fusion_export_error = "Đường dẫn file .setting không hợp lệ."
                return ""

            import stat
            try:
                with open(resolved_path, "rb") as f:
                    st = os.fstat(f.fileno())
                    if not stat.S_ISREG(st.st_mode):
                        self._last_fusion_export_error = "File .setting không phải file thông thường."
                        return ""
                    data = f.read(MAX_FUSION_SETTING_BYTES + 1)
                if len(data) > MAX_FUSION_SETTING_BYTES:
                    self._last_fusion_export_error = f"File .setting vượt quá kích thước tối đa ({MAX_FUSION_SETTING_BYTES // (1024 * 1024)}MB)."
                    return ""
                if not data:
                    self._last_fusion_export_error = "File .setting rỗng."
                    return ""
                text_content = data.decode("utf-8")
                if not text_content.strip():
                    self._last_fusion_export_error = "File .setting chỉ chứa khoảng trắng."
                    return ""
                return text_content
            except UnicodeDecodeError as e:
                self._last_fusion_export_error = f"File .setting không phải mã hóa UTF-8 hợp lệ: {e}"
                return ""
            except (OSError, IOError) as e:
                self._last_fusion_export_error = f"Lỗi đọc file .setting: {e}"
                return ""

        self._last_fusion_export_error = "Loại tài nguyên không hỗ trợ xuất mã Fusion."
        return ""

    def _install_to_fusion(self) -> Tuple[int, str]:
        from src.core.asset_manager import AssetManager
        is_trans = isinstance(self.selected_asset, TransitionStylePreset) or getattr(self.selected_asset, "tab", "") == "trans"
        is_lut = getattr(self.selected_asset, "tab", "") == "lut" or self.current_rail_tab == "lut"
        real_file_path = getattr(self.selected_asset, "file_path", "")

        if is_lut:
            target_dir = str(AssetManager.get_lut_install_dir())
            os.makedirs(target_dir, exist_ok=True)
            import shutil
            count = 0
            if real_file_path and os.path.isfile(real_file_path) and real_file_path.endswith(".cube"):
                fname = os.path.basename(real_file_path)
                dest = os.path.join(target_dir, fname)
                shutil.copy2(real_file_path, dest)
                count = 1
                msg = f"Đã đồng bộ LUT '{fname}' vào DaVinci Resolve ({target_dir})"
            else:
                luts_dir = os.path.join("assets", "luts")
                if os.path.isdir(luts_dir):
                    for f in os.listdir(luts_dir):
                        if f.lower().endswith(".cube"):
                            shutil.copy2(os.path.join(luts_dir, f), os.path.join(target_dir, f))
                            count += 1
                msg = f"Đã đồng bộ {count} LUT màu vào DaVinci Resolve ({target_dir})"
            self._show_toast(msg)
            return count, target_dir

        if real_file_path and os.path.isfile(real_file_path) and real_file_path.endswith(".setting"):
            import shutil
            if is_trans:
                target_dir = str(AssetManager.get_fusion_template_dir("Transitions"))
            else:
                target_dir = str(AssetManager.get_fusion_template_dir("Titles"))
            os.makedirs(target_dir, exist_ok=True)
            fname = os.path.basename(real_file_path)
            if not fname.startswith("ResolveFlow_"):
                dest_file = os.path.join(target_dir, f"ResolveFlow_{fname}")
            else:
                dest_file = os.path.join(target_dir, fname)
            shutil.copy2(real_file_path, dest_file)
            msg = f"Đã cài đặt '{os.path.basename(dest_file)}' vào ResolveFlow ({target_dir})"
            count = 1
        else:
            if is_trans:
                count, target_dir = TransitionMacroGenerator.install_transitions_to_davinci_resolve()
                msg = f"Đã cài đặt {count} chuyển cảnh vào DaVinci Resolve ({target_dir})"
            else:
                count, target_dir = FusionSettingGenerator.install_presets_to_davinci_resolve()
                msg = f"Đã cài đặt {count} mẫu chữ vào DaVinci Resolve ({target_dir})"
        self.install_presets_requested.emit()
        self._show_toast(msg)
        return count, target_dir

    def _capture_preset_from_playhead(self):
        """Hút TextPlus / Fusion comp tại Playhead thành Preset vĩnh viễn."""
        from src.core.preset_capture import capture_playhead_text_preset, safe_preset_name
        from PyQt6.QtWidgets import QInputDialog

        default_name = f"ChunDVC_Style_{len(self.all_presets) + 1}"
        name, ok = QInputDialog.getText(
            self,
            "Hút Preset từ DaVinci Resolve",
            "Nhập tên preset mới cần lưu:",
            text=default_name
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        self._show_toast(f"⏳ Đang hút style '{name}' từ Playhead...")
        try:
            res = capture_playhead_text_preset(
                preset_name=name,
                resolve_app=getattr(self, "resolve_app", None),
                log_callback=lambda m: print(m),
            )
            if res.get("ok"):
                self._show_toast(f"✅ Đã hút thành công preset '{name}'!")
                new_preset = TextStylePreset(
                    id=safe_preset_name(name).lower(),
                    name=name,
                    category="kinetic",
                    badge_icon="📥",
                    description=f"Preset được hút trực tiếp từ Timeline Resolve ({res.get('item_name', '')})",
                )
                self.all_presets.append(new_preset)
                self.all_assets.append(new_preset)
                self._filter_by_rail("text")
            else:
                self._show_toast(f"❌ {res.get('message', 'Không thể hút preset')}")
        except Exception as e:
            self._show_toast(f"❌ Lỗi: {str(e)}")

    def _import_files_or_folders(self, paths: Optional[List[str]] = None):
        if not paths:
            files, _ = QFileDialog.getOpenFileNames(
                self,
                "Chọn tệp nhập kho (Title .setting, Meme/Overlay .mp4, SFX .wav, LUT .cube, Sticker .png, Pack .zip/.rfpack)",
                "",
                "Tất cả tài nguyên (*.setting *.mp4 *.mov *.wav *.mp3 *.cube *.png *.zip *.rfpack);;Fusion Setting (*.setting);;Video (*.mp4 *.mov);;Audio (*.wav *.mp3);;LUT (*.cube);;Gói nén (*.zip *.rfpack);;Tất cả tệp (*.*)"
            )
            if not files:
                return
            paths = files

        if getattr(self, "_ingest_in_progress", False):
            return
        self._ingest_in_progress = True

        from PySide6.QtWidgets import QDialog
        from src.ui.widgets.pack_ingest_wizard import PackIngestWizard

        committed_sources = []
        committed_payloads = []
        canceled = False
        error_sources = []

        try:
            for p in paths:
                if canceled:
                    break
                if not os.path.exists(p):
                    continue

                try:
                    dialog = PackIngestWizard(parent=self, source=str(p), auto_prepare=True)
                    self._active_ingest_wizard = dialog
                    committed = [False]
                    def _on_committed(meta):
                        committed[0] = True
                        if isinstance(meta, dict):
                            committed_payloads.append(meta)
                    dialog.pack_committed.connect(_on_committed)
                    res = dialog.exec()
                    self._active_ingest_wizard = None
                    if (committed[0] or dialog.is_pack_committed()) and (res == QDialog.DialogCode.Accepted or res == 1):
                        committed_sources.append(p)
                    else:
                        canceled = True
                        break
                except Exception as exc:
                    error_sources.append((p, str(exc)))
                    break

            if committed_sources:
                self._rescan_assets()
                unsynced = sum(1 for m in committed_payloads if m.get("snapshot_synced") is False)
                unsynced_note = f" (⚠️ {unsynced} gói chưa đồng bộ bộ đệm offline)" if unsynced > 0 else ""
                if error_sources:
                    err_p, err_msg = error_sources[0]
                    self._show_toast(f"ℹ️ Đã nhập {len(committed_sources)}/{len(paths)} nguồn{unsynced_note} (gói '{os.path.basename(err_p)}' lỗi: {err_msg}; đã dừng hàng đợi).")
                elif canceled:
                    self._show_toast(f"ℹ️ Đã nhập {len(committed_sources)}/{len(paths)} nguồn{unsynced_note} (đã dừng hàng đợi).")
                else:
                    self._show_toast(f"✅ Đã nhập thành công {len(committed_sources)} nguồn vào kho!{unsynced_note}")
            elif error_sources:
                err_p, err_msg = error_sources[0]
                self._show_toast(f"❌ Lỗi nhập kho cho {os.path.basename(err_p)}: {err_msg}")
        finally:
            self._ingest_in_progress = False
            self._active_ingest_wizard = None

    def _on_insert_single_title_clicked(self):
        self._insert_at_playhead()

    def _on_install_presets_clicked(self):
        self._install_to_fusion()

    def _on_copy_fusion_clicked(self):
        target = self.selected_asset
        if not target:
            self._show_toast("⚠️ Chưa chọn tài nguyên để xuất mã Fusion.")
            return

        macro_code = self._get_current_fusion_macro_code(target)
        if not macro_code:
            err_msg = getattr(self, "_last_fusion_export_error", "Không thể xuất mã Fusion cho tài nguyên này.")
            self._show_toast(f"❌ {err_msg}")
            return

        try:
            QApplication.clipboard().setText(macro_code)
        except Exception as e:
            logger.error("Lỗi ghi clipboard: %s", e)
            self._show_toast(f"❌ Lỗi ghi clipboard: {e}")
            return

        pid = getattr(target, "id", "") if not isinstance(target, Mapping) else str(target.get("id") or "")
        if pid:
            self.copy_fusion_node_requested.emit(pid)
        self._show_toast("✅ Đã sao chép mã Fusion node! Hãy dán vào trang Fusion (Ctrl+V)")

    def _on_fast_paste_fusion_clicked(self):
        target = self.selected_asset
        if not target:
            self._show_toast("⚠️ Chưa chọn tài nguyên để dán tự động.")
            return

        macro_code = self._get_current_fusion_macro_code(target)
        if not macro_code:
            err_msg = getattr(self, "_last_fusion_export_error", "Không thể xuất mã Fusion cho tài nguyên này.")
            self._show_toast(f"❌ {err_msg}")
            return

        from src.core.fast_paste import fast_paste_to_fusion
        res = fast_paste_to_fusion(macro_code)
        if res.get("ok"):
            self._show_toast("⚡ Đã tự động dán cây node vào trang Fusion!")
        else:
            self._show_toast(f"⚠️ {res.get('message', 'Không thể dán tự động')}")

    def _on_favorite_toggled(self, preset_id: str, is_fav: bool):
        if is_fav:
            self.favorites.add(preset_id)
        else:
            self.favorites.discard(preset_id)
        FavoritesManager.save(self.favorites)
        if self.current_rail_tab == "fav":
            self._refresh_grid()

    def _scan_local_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa SFX / LUTs / Footage")
        if not folder:
            return

        found = 0
        valid_exts = ['.wav', '.mp3', '.aac', '.mp4', '.mov', '.gif', '.png', '.jpg', '.cube']
        for root, _, files in os.walk(folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in valid_exts:
                    asset = LocalAsset(os.path.join(root, f))
                    self.local_assets.append(asset)
                    self.all_assets.append(asset)
                    found += 1

        self._populate_cards()
        self._rebuild_categories_column()
        self._refresh_grid()
        QMessageBox.information(self, "Quét hoàn tất", f"Đã nạp thành công {found} tài nguyên cá nhân vào Kho Đạo Cụ!")

    def hideEvent(self, event):
        if hasattr(self, "audio_inspector_widget"):
            self.audio_inspector_widget.stop_playback()
        if hasattr(self, "icon_meme_widget"):
            self.icon_meme_widget.stop_playback()
        if event is not None:
            super().hideEvent(event)

    def closeEvent(self, event):
        if hasattr(self, "audio_inspector_widget"):
            self.audio_inspector_widget.stop_playback()
        if hasattr(self, "icon_meme_widget"):
            self.icon_meme_widget.stop_playback()
        if event is not None:
            super().closeEvent(event)

