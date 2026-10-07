"""
TabAssets: Kho Đạo Cụ & Hậu Kỳ (ResolveFlow Studio Mode).
Triển khai toàn diện cấu trúc 4 cột theo chuẩn thiết kế từ src/ui/resolveflow_ui.html:
- Cột 1: Rail điều hướng chính (Chữ, Màu LUT, Chuyển cảnh, Sticker, Lớp phủ, Meme, SFX, Mẫu của tôi)
- Cột 2: Phân loại danh mục con động (Categories) kèm số lượng
- Cột 3: Lưới đạo cụ trực quan (Main Grid) với xem trước phong cách, nút Yêu thích, nút Thêm nhanh, Kéo & Thả
- Cột 4: Bảng tinh chỉnh chi tiết (Inspector) với khung xem trước lớn và bộ nút thao tác DaVinci
"""

import os
import json
import uuid
import tempfile
import math
from typing import Optional, Callable, List, Dict, Any, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox,
    QSizePolicy, QFileDialog, QColorDialog
)
from PySide6.QtCore import Qt, Signal as pyqtSignal, QMimeData, QUrl, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QDrag, QPixmap, QCursor, QMovie, QPainter, QColor, QPen, QBrush,
    QFont, QPainterPath, QLinearGradient
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from src.ui.theme import ThemeColors, ThemeFonts, TOOLTIPS
from src.core.text_preset import TextStylePreset, BUILTIN_PRESETS, FusionSettingGenerator
from src.core.transition_preset import TransitionStylePreset, BUILTIN_TRANSITIONS, TransitionMacroGenerator
from src.core.resolve_api import ResolveAutomation
from src.core.asset_indexer import AssetIndexer


# =========================================================================
# 1. BỘ QUẢN LÝ YÊU THÍCH (Favorites Manager)
# =========================================================================
class FavoritesManager:
    FILE_PATH = "data/favorites.json"

    @classmethod
    def load(cls) -> set:
        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        if not os.path.exists(cls.FILE_PATH):
            return set()
        try:
            with open(cls.FILE_PATH, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except Exception:
            return set()

    @classmethod
    def save(cls, favs: set):
        if not os.path.exists("data"):
            os.makedirs("data", exist_ok=True)
        try:
            with open(cls.FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(list(favs), f)
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
        thumbnail_path: str = ""
    ):
        self.id = id
        self.name = name
        self.tab = tab
        self.category = category
        self.sub = sub
        self.badge_icon = badge_icon
        self.pal = pal or []
        self.duration = duration
        self.frames = frames
        self.color_hex = color_hex
        self.svg_path = svg_path
        self.native_preset = native_preset
        self.file_path = file_path
        self.thumbnail_path = thumbnail_path


# Danh sách LUTs điện ảnh chuẩn từ mockup
MOCKUP_LUTS: List[StudioAsset] = [
    StudioAsset("clean_rec709", "Clean Rec.709", "lut", "natural", sub="Tự nhiên, trung tính", badge_icon="🌿", pal=["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]),
    StudioAsset("warm_vlog", "Warm Vlog", "lut", "natural", sub="Ấm áp đời thường", badge_icon="☕", pal=["#3b2416", "#a16207", "#f59e0b", "#fde68a"]),
    StudioAsset("golden_hour", "Golden Hour", "lut", "natural", sub="Nắng chiều vàng", badge_icon="🌅", pal=["#431407", "#c2410c", "#fb923c", "#fef3c7"]),
    StudioAsset("cinematic_teal_orange", "Teal & Orange", "lut", "cinema", sub="Điện ảnh Hollywood", badge_icon="🎬", pal=["#042f2e", "#0f766e", "#f97316", "#fed7aa"]),
    StudioAsset("moody_dark", "Moody Dark", "lut", "cinema", sub="Tối, trầm", badge_icon="🌑", pal=["#09090b", "#1e293b", "#475569", "#94a3b8"]),
    StudioAsset("vintage_film_kodak", "Kodak 2383", "lut", "cinema", sub="Phim nhựa cổ điển", badge_icon="🎞️", pal=["#1c1917", "#78350f", "#d6a35c", "#f5e6c8"]),
    StudioAsset("high_contrast_bw", "Noir B&W", "lut", "cinema", sub="Đen trắng tương phản", badge_icon="⬛", pal=["#000000", "#3f3f46", "#a1a1aa", "#ffffff"]),
    StudioAsset("korean_pastel", "Korean Pastel", "lut", "style", sub="Mềm, sáng, nhẹ", badge_icon="🌸", pal=["#fbcfe8", "#ddd6fe", "#bae6fd", "#fef9c3"]),
    StudioAsset("cyberpunk_neon", "Cyberpunk Neon", "lut", "style", sub="Tím hồng đêm", badge_icon="🌆", pal=["#1e1b4b", "#7c3aed", "#ec4899", "#22d3ee"]),
    StudioAsset("anime_vibrant", "Anime Vibrant", "lut", "style", sub="Rực rỡ hoạt hình", badge_icon="🎨", pal=["#1d4ed8", "#22c55e", "#facc15", "#f0f9ff"])
]

# Danh sách Sticker vector chuẩn từ mockup
MOCKUP_ICONS: List[StudioAsset] = [
    StudioAsset("arrow_right", "Mũi tên thẳng", "icon", "arrow", sub="Chỉ hướng", badge_icon="➡️", color_hex="#facc15"),
    StudioAsset("arrow_curve", "Mũi tên cong", "icon", "arrow", sub="Nhấn mạnh", badge_icon="↪️", color_hex="#facc15"),
    StudioAsset("circle_mark", "Khoanh tròn", "icon", "arrow", sub="Khoanh vùng", badge_icon="⭕", color_hex="#ef4444"),
    StudioAsset("underline_mark", "Gạch chân", "icon", "arrow", sub="Gạch dưới từ", badge_icon="〰️", color_hex="#facc15"),
    StudioAsset("location_pin", "Ghim địa điểm", "icon", "place", sub="Đánh dấu tọa độ", badge_icon="📍", color_hex="#f43f5e"),
    StudioAsset("flag_red", "Lá cờ đích", "icon", "place", sub="Cột mốc", badge_icon="🚩", color_hex="#ef4444"),
    StudioAsset("heart_icon", "Trái tim", "icon", "emo", sub="Yêu thích", badge_icon="❤️", color_hex="#fb7185"),
    StudioAsset("fire_icon", "Ngọn lửa", "icon", "emo", sub="Nóng bỏng, viral", badge_icon="🔥", color_hex="#fb923c"),
    StudioAsset("sparkle_icon", "Lấp lánh", "icon", "emo", sub="Nổi bật", badge_icon="✨", color_hex="#fde047"),
    StudioAsset("star_icon", "Ngôi sao", "icon", "emo", sub="Đánh giá", badge_icon="⭐", color_hex="#facc15"),
    StudioAsset("thumb_up", "Nút Thích", "icon", "emo", sub="Like video", badge_icon="👍", color_hex="#60a5fa"),
    StudioAsset("warn_icon", "Cảnh báo", "icon", "alert", sub="Chú ý quan trọng", badge_icon="⚠️", color_hex="#facc15"),
    StudioAsset("check_icon", "Dấu tích", "icon", "alert", sub="Hoàn thành", badge_icon="✅", color_hex="#22c55e"),
    StudioAsset("cross_icon", "Dấu X đỏ", "icon", "alert", sub="Sai, hủy bỏ", badge_icon="❌", color_hex="#ef4444"),
    StudioAsset("speech_bubble", "Bong bóng thoại", "icon", "talk", sub="Lời nói", badge_icon="💬", color_hex="#e4e4e7"),
    StudioAsset("bell_sub", "Chuông thông báo", "icon", "talk", sub="Đăng ký kênh", badge_icon="🔔", color_hex="#facc15"),
    StudioAsset("play_btn", "Nút phát", "icon", "talk", sub="Video play", badge_icon="▶️", color_hex="#ef4444")
]

# Danh sách Lớp phủ (Overlay)
MOCKUP_OVERLAYS: List[StudioAsset] = [
    StudioAsset("cinematic_light_leak", "Light Leak điện ảnh", "overlay", "cinematic", sub="Vệt sáng phim", badge_icon="✨"),
    StudioAsset("sub_bell_noti", "Nút Subscribe & Chuông", "overlay", "green_screen", sub="Phông xanh", badge_icon="🟩"),
    StudioAsset("countdown_321", "Đếm ngược 3-2-1", "overlay", "green_screen", sub="Phông xanh", badge_icon="⏱️"),
    StudioAsset("breaking_news", "Breaking News Banner", "overlay", "green_screen", sub="Phông xanh", badge_icon="📺"),
    StudioAsset("censored_bar", "Thanh che Censored", "overlay", "green_screen", sub="Phông xanh", badge_icon="⬛")
]

# Danh sách Meme reaction
MOCKUP_MEMES: List[StudioAsset] = [
    StudioAsset("meme_wow", "Wow bất ngờ", "meme", "reaction", sub="Reaction", badge_icon="😮"),
    StudioAsset("meme_facepalm", "Facepalm toang", "meme", "reaction", sub="Reaction", badge_icon="🤦"),
    StudioAsset("meme_question", "Chấm hỏi ngơ ngác", "meme", "reaction", sub="Reaction", badge_icon="❓"),
    StudioAsset("meme_cat_vibe", "Mèo gật gù", "meme", "trending", sub="Trending", badge_icon="🐱"),
    StudioAsset("meme_cai_nit", "Còn đúng cái nịt", "meme", "trending", sub="Trending", badge_icon="💸"),
    StudioAsset("meme_ao_that_day", "Ảo thật đấy", "meme", "trending", sub="Trending", badge_icon="🤯"),
    StudioAsset("meme_cat_cry", "Mèo chuối khóc", "meme", "reaction", sub="Reaction", badge_icon="😿"),
    StudioAsset("meme_khaby", "Nhún vai bất lực", "meme", "reaction", sub="Reaction", badge_icon="🤷"),
    StudioAsset("meme_xach_balo", "Xách balo lên và đi", "meme", "trending", sub="Trending", badge_icon="🎒")
]

# Danh sách SFX âm thanh
MOCKUP_SFX: List[StudioAsset] = [
    StudioAsset("sfx_whoosh", "Whoosh lướt nhanh", "sfx", "trans", sub="Chuyển cảnh", badge_icon="💨"),
    StudioAsset("sfx_swoosh_sub", "Swoosh trầm", "sfx", "trans", sub="Chuyển cảnh", badge_icon="🌊"),
    StudioAsset("sfx_riser", "Riser kịch tính", "sfx", "trans", sub="Tăng tiến độ", badge_icon="📈"),
    StudioAsset("sfx_pop", "Pop nảy chữ", "sfx", "accent", sub="Xuất hiện text", badge_icon="🎈"),
    StudioAsset("sfx_ding", "Ding điểm nhấn", "sfx", "accent", sub="Điểm nhấn", badge_icon="🔔"),
    StudioAsset("sfx_click", "Click chuột", "sfx", "accent", sub="Tương tác", badge_icon="🖱️"),
    StudioAsset("sfx_shutter", "Camera Shutter", "sfx", "accent", sub="Chụp ảnh", badge_icon="📸"),
    StudioAsset("sfx_laugh", "Tiếng cười mỉa", "sfx", "fun", sub="Hài hước", badge_icon="😂"),
    StudioAsset("sfx_horn", "Tiếng còi xe", "sfx", "fun", sub="Hài hước", badge_icon="📢"),
    StudioAsset("sfx_glitch", "Glitch số", "sfx", "tech", sub="Lỗi tín hiệu", badge_icon="⚡"),
    StudioAsset("sfx_beep", "Beep kiểm duyệt", "sfx", "tech", sub="Kiểm duyệt", badge_icon="🛑")
]


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

        preset = self.preset_getter() if callable(self.preset_getter) else None
        if not preset:
            return

        drag = QDrag(self)
        if callable(self.mime_data_generator):
            mime_data = self.mime_data_generator(preset)
        else:
            mime_data = QMimeData()
            if isinstance(preset, LocalAsset):
                mime_data.setUrls([QUrl.fromLocalFile(preset.file_path)])
            elif isinstance(preset, TextStylePreset):
                temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{preset.id}_{uuid.uuid4().hex[:6]}.setting")
                sample_txt = self.sample_text_getter() if callable(self.sample_text_getter) else preset.name
                FusionSettingGenerator.export_setting_file(preset, temp_setting, sample_text=sample_txt)
                mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
            elif isinstance(preset, TransitionStylePreset):
                temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{preset.id}_{uuid.uuid4().hex[:6]}.setting")
                TransitionMacroGenerator.export_setting_file(preset, temp_setting)
                mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
            else:
                # StudioAsset ảo
                temp_path = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{getattr(preset, 'id', 'item')}.txt")
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(getattr(preset, "name", "ResolveFlow Asset"))
                mime_data.setUrls([QUrl.fromLocalFile(temp_path)])

        if mime_data:
            drag.setMimeData(mime_data)
            self.setCursor(Qt.OpenHandCursor)
            drag.exec_(Qt.CopyAction)


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
        if callable(self.mime_data_getter):
            mime_data = self.mime_data_getter()
            if mime_data and mime_data.hasUrls():
                drag = QDrag(self)
                drag.setMimeData(mime_data)
                drag.exec_(Qt.CopyAction)



# =========================================================================
# 5. WAVEFORM CANVAS & ASSET CARD (Bộ hiển thị đạo cụ trực quan)
# =========================================================================
class WaveformCanvas(QWidget):
    """Canvas vẽ dạng sóng (waveform) phong bì âm thanh (envelope bars)."""
    def __init__(self, preset_id: str = "sfx", bars: Optional[List[float]] = None, parent=None, num_bars: int = 18, height: int = 30):
        super().__init__(parent)
        self.preset_id = preset_id
        self.bars = bars if bars is not None else self._generate_envelope_bars(preset_id, num_bars=num_bars)
        self.is_playing = False
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_preset_id(self, preset_id: str, num_bars: int = 18):
        self.preset_id = preset_id
        self.bars = self._generate_envelope_bars(preset_id, num_bars=num_bars)
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


class LutSplitWidget(QWidget):
    """Widget so sánh Before / After chia đôi màn hình với thanh trượt split."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.split_pct = 50
        self.lut_colors = ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        self.lut_name = "Clean Rec.709"
        self.setFixedHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_split(self, pct: int):
        self.split_pct = max(0, min(100, pct))
        self.update()

    def set_lut(self, name: str, colors: Optional[List[str]] = None):
        self.lut_name = name
        self.lut_colors = colors or ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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


class TransitionLoopWidget(QWidget):
    """Widget xem trước chuyển cảnh dạng lặp (Transition Visual Loop Preview)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.trans_name = "Whip Pan Left"
        self.trans_kind = "transform"
        self.frames = 20
        self.progress = 0.0
        self.setFixedHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(40)
        self.anim_timer.timeout.connect(self._on_tick)
        self.anim_timer.start()

    def set_transition(self, name: str, kind: str, frames: int):
        self.trans_name = name
        self.trans_kind = kind
        self.frames = frames
        self.update()

    def _on_tick(self):
        self.progress += 0.03
        if self.progress > 1.3:
            self.progress = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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
    """Widget xem trước Icon vector hoặc Meme video reaction cỡ lớn."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.item_name = ""
        self.item_type = "icon"
        self.badge_icon = "✨"
        self.color_hex = "#facc15"
        self.sub_text = ""
        self.setFixedHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_item(self, name: str, item_type: str, badge: str, color_hex: str, sub: str):
        self.item_name = name
        self.item_type = item_type
        self.badge_icon = badge or "✨"
        self.color_hex = color_hex or "#facc15"
        self.sub_text = sub
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

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
            painter.drawText(QRectF(8, 8, 48, 18), Qt.AlignCenter, "VECTOR")
        else:
            grad = QLinearGradient(0, 0, w, h)
            grad.setColorAt(0.0, QColor("#1e1b4b"))
            grad.setColorAt(1.0, QColor("#09090b"))
            painter.fillRect(0, 0, w, h, grad)

            painter.setFont(QFont("Segoe UI Emoji", 34))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(QRectF(0, 6, w, h - 34), Qt.AlignCenter, self.badge_icon)

            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(6, 182, 212, 180)))
            painter.drawRoundedRect(QRectF(w - 56, 8, 48, 18), 4, 4)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(QRectF(w - 56, 8, 48, 18), Qt.AlignCenter, "▶ MP4")

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 170)))
        painter.drawRoundedRect(QRectF(8, h - 26, w - 16, 20), 4, 4)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(QRectF(14, h - 26, w - 28, 20), Qt.AlignVCenter | Qt.AlignLeft, self.item_name)


def format_text_preset_html(preset_id: str, preset_name: str = "Title", sample_text: str = "", standard_color: str = "#FFFFFF", is_large: bool = False) -> str:
    """Format rich HTML cho mẫu chữ trong card và inspector."""
    pid = (preset_id or "").lower()
    text = sample_text.strip() if sample_text.strip() else ""

    font_base = "18px" if is_large else "13px"
    font_hl = "22px" if is_large else "15px"

    if pid == "kinetic_hormozi":
        display = text if text else "TIỀN ĐẾN<br>TỪ <span style=\"color: #facc15;\">ĐÂU?</span>"
        if text and "<span" not in text:
            words = text.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"color: #facc15; font-size: {font_hl};\">{words[-1]}</span>"
            else:
                display = f"<span style=\"color: #facc15;\">{text}</span>"
        return (
            f"<div style=\"font-family: 'Arial Black', Impact, sans-serif; font-weight: 900; font-size: {font_base}; text-transform: uppercase; color: #ffffff; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid == "karaoke_pop":
        display = text if text else "dính <span style=\"color: #a3e635;\">mưa</span> rồi"
        if text and "<span" not in text:
            words = text.split()
            if len(words) > 1:
                mid = len(words) // 2
                display = " ".join(words[:mid]) + f" <span style=\"color: #a3e635; font-size: {font_hl}; font-weight: 900;\">{words[mid]}</span> " + " ".join(words[mid+1:])
            else:
                display = f"<span style=\"color: #a3e635;\">{text}</span>"
        return (
            f"<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: {font_base}; color: #ffffff; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid == "box_highlight":
        display = text if text else "100% <span style=\"background-color: #ef4444; color: #ffffff; padding: 2px 6px; border-radius: 4px; font-weight: 900;\">sức khỏe</span>"
        if text and "<span" not in text:
            words = text.split()
            if len(words) > 1:
                display = " ".join(words[:-1]) + f" <span style=\"background-color: #ef4444; color: #ffffff; padding: 2px 6px; border-radius: 4px; font-weight: 900;\">{words[-1]}</span>"
            else:
                display = f"<span style=\"background-color: #ef4444; color: #ffffff; padding: 2px 6px; border-radius: 4px;\">{text}</span>"
        return (
            f"<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: {font_base}; color: #ffffff; text-align: center;\">"
            f"{display}"
            "</div>"
        )
    elif pid == "glow_neon":
        display = text if text else "NEON"
        return (
            f"<div style=\"font-family: Arial, sans-serif; font-weight: 800; font-size: {font_base}; text-align: center;\">"
            f"<span style=\"color: #ecfeff; border: 1px solid #06b6d4; background-color: rgba(6, 182, 212, 0.2); padding: 4px 12px; border-radius: 6px;\">{display}</span>"
            "</div>"
        )
    elif pid == "vhs_retro":
        display = text if text else "HÈ 1999"
        sub_play = "▶ PLAY SP 0:12"
        return (
            f"<div style=\"font-family: Consolas, monospace; font-size: 11px; color: #f5f5f4; text-align: center;\">"
            f"<div style=\"color: #22c55e; font-size: 10px; margin-bottom: 3px;\">{sub_play}</div>"
            f"<div style=\"font-size: {font_base}; font-weight: bold; letter-spacing: 2px; color: #fff59d;\">{display}</div>"
            "</div>"
        )
    elif pid == "paper_cutout":
        display = text if text else "VLOG"
        spans = "".join(f"<span style=\"background-color: #ffffff; color: #111111; padding: 2px 5px; margin: 1px; border-radius: 2px;\">{c}</span>" for c in display[:8])
        return (
            f"<div style=\"font-family: Georgia, serif; font-weight: bold; font-size: {font_base}; color: #111111; text-align: center;\">"
            f"{spans}"
            "</div>"
        )
    elif pid == "gradient_fill":
        display = text if text else "HOÀNG HÔN SUNSET"
        return (
            f"<div style=\"font-family: 'Arial Black', sans-serif; font-weight: 900; font-size: {font_base}; text-align: center;\">"
            f"<span style=\"color: #fb923c;\">{display}</span>"
            "</div>"
        )
    else:
        display = text if text else preset_name.split("(")[0].strip()
        return (
            f"<div style=\"font-family: Arial, sans-serif; font-weight: bold; font-size: {font_base}; color: {standard_color}; text-align: center;\">"
            f"{display}"
            "</div>"
        )


class AssetCard(QFrame):
    selected_signal = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)
    add_requested = pyqtSignal(object)
    quick_play_requested = pyqtSignal(str)

    def __init__(self, preset: Any, is_fav: bool = False, sample_text_func=None, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.is_fav = is_fav
        self.sample_text_func = sample_text_func
        self.is_selected = False
        self._drag_start_pos = None
        self._is_playing_audio = False
        self._player = None
        self._audio_output = None

        self.setFixedHeight(148)
        self.setMinimumWidth(160)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()

        self._init_ui()

    def _apply_style(self):
        border_col = ThemeColors.PRIMARY if self.is_selected else ThemeColors.BORDER_DEFAULT
        bg_col = ThemeColors.BG_CARD_ACTIVE if self.is_selected else ThemeColors.BG_CARD
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
            th_layout = QVBoxLayout(self.thumb)
            th_layout.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel()
            lbl.setPixmap(QPixmap(thumb_path).scaled(160, 80, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("border-radius: 8px; overflow: hidden;")
            th_layout.addWidget(lbl)
        else:
            if isinstance(self.preset, TextStylePreset) or getattr(self.preset, "tab", "") == "text":
                self._render_text_thumbnail()
            elif getattr(self.preset, "tab", "") == "sfx":
                self._render_sfx_thumbnail()
            elif getattr(self.preset, "tab", "") == "lut":
                self._render_lut_thumbnail()
            elif isinstance(self.preset, TransitionStylePreset) or getattr(self.preset, "tab", "") == "trans":
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

        layout.addLayout(h_info)

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

        # 4-stripe palette
        pal = getattr(self.preset, "pal", None)
        if not pal or len(pal) < 4:
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
                background-color: rgba(24, 24, 27, 0.85);
                color: #c4b5fd;
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
        """Render icon SVG hoặc reaction emoji badge cỡ lớn."""
        self.thumb.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-radius: 8px;
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 6, 6, 6)
        th_layout.setAlignment(Qt.AlignCenter)

        badge = getattr(self.preset, "badge_icon", "") or getattr(self.preset, "emo", "✨")
        color = getattr(self.preset, "color_hex", "#facc15")
        self.lbl_icon_preview = QLabel(badge, self.thumb)
        self.lbl_icon_preview.setAlignment(Qt.AlignCenter)
        self.lbl_icon_preview.setStyleSheet(f"""
            QLabel {{
                font-size: 32px;
                color: {color};
                background: transparent;
                border: none;
            }}
        """)
        th_layout.addWidget(self.lbl_icon_preview)

    def _toggle_quick_play(self):
        if self._is_playing_audio:
            self._stop_quick_play()
        else:
            self._start_quick_play()

    def _start_quick_play(self):
        self._is_playing_audio = True
        if hasattr(self, "btn_quick_play"):
            self.btn_quick_play.setText("⏸")
        if hasattr(self, "waveform_canvas"):
            self.waveform_canvas.is_playing = True
            self.waveform_canvas.update()

        pid = getattr(self.preset, "id", "")
        clean_id = pid.replace("sfx_", "")
        candidate_paths = [
            os.path.join("assets", "sfx", f"{clean_id}.wav"),
            os.path.join("assets", "sfx", f"{pid}.wav"),
            getattr(self.preset, "file_path", "")
        ]
        sound_path = None
        for p in candidate_paths:
            if p and os.path.exists(p):
                sound_path = p
                break

        if sound_path:
            try:
                if self._player is None:
                    self._player = QMediaPlayer(self)
                    self._audio_output = QAudioOutput(self)
                    self._player.setAudioOutput(self._audio_output)
                    self._player.playbackStateChanged.connect(self._on_player_state_changed)
                self._player.setSource(QUrl.fromLocalFile(os.path.abspath(sound_path)))
                self._audio_output.setVolume(0.8)
                self._player.play()
            except Exception:
                QTimer.singleShot(1500, self._stop_quick_play)
        else:
            QTimer.singleShot(1500, self._stop_quick_play)

        self.quick_play_requested.emit(pid)

    def _stop_quick_play(self):
        self._is_playing_audio = False
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

    def _toggle_fav(self):
        self.is_fav = not self.is_fav
        self.btn_fav.setText("❤️" if self.is_fav else "🤍")
        pid = getattr(self.preset, "id", "")
        self.favorite_toggled.emit(pid, self.is_fav)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self._apply_style()

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

        if isinstance(self.preset, LocalAsset):
            mime_data.setUrls([QUrl.fromLocalFile(self.preset.file_path)])
        elif isinstance(self.preset, TextStylePreset):
            temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{self.preset.id}_{uuid.uuid4().hex[:6]}.setting")
            sample_txt = self.sample_text_func() if callable(self.sample_text_func) else self.preset.name
            FusionSettingGenerator.export_setting_file(self.preset, temp_setting, sample_text=sample_txt)
            mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
        elif isinstance(self.preset, TransitionStylePreset):
            temp_setting = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{self.preset.id}_{uuid.uuid4().hex[:6]}.setting")
            TransitionMacroGenerator.export_setting_file(self.preset, temp_setting)
            mime_data.setUrls([QUrl.fromLocalFile(temp_setting)])
        else:
            pid = getattr(self.preset, "id", "item")
            clean_id = pid.replace("sfx_", "")
            sfx_file = os.path.join("assets", "sfx", f"{clean_id}.wav")
            if os.path.exists(sfx_file):
                mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(sfx_file))])
            else:
                temp_path = os.path.join(tempfile.gettempdir(), f"ResolveFlow_{pid}.txt")
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(getattr(self.preset, "name", "ResolveFlow Asset"))
                mime_data.setUrls([QUrl.fromLocalFile(temp_path)])

        drag.setMimeData(mime_data)
        drag.exec_(Qt.CopyAction)


# =========================================================================
# 6. TAB ASSETS - GIAO DIỆN 4 CỘT CHUẨN KHO ĐẠO CỤ
# =========================================================================
class TabAssets(QWidget):
    """
    Kho Đạo Cụ 4 Cột:
    Rail (78px) | Categories (204px) | Main Grid (flex 1) | Inspector (336px)
    """
    insert_title_requested = pyqtSignal(str, str, float)
    install_presets_requested = pyqtSignal()
    copy_fusion_node_requested = pyqtSignal(str)
    apply_lut_requested = pyqtSignal(str)
    insert_sfx_requested = pyqtSignal(str, int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_preset_callback = None
        self.favorites = FavoritesManager.load()

        # Dữ liệu nạp sẵn
        indexer = AssetIndexer()
        
        # Helper to convert dicts to StudioAsset
        def dicts_to_assets(dicts, tab, category, sub, badge):
            return [
                StudioAsset(
                    id=d["id"], name=d["name"], tab=tab, category=category,
                    sub=sub, badge_icon=badge, file_path=d.get("file_path", ""),
                    thumbnail_path=d.get("thumbnail_path", "")
                ) for d in dicts
            ]
        
        # Combine builtins with scanned settings
        scanned_titles = indexer.scan_titles()
        self.all_presets = BUILTIN_PRESETS.copy() + dicts_to_assets(
            scanned_titles, "text", "custom", "Tùy chỉnh", "🔤"
        )
        
        scanned_transitions = indexer.scan_transitions()
        self.all_transitions = BUILTIN_TRANSITIONS.copy() + dicts_to_assets(
            scanned_transitions, "trans", "custom", "Tùy chỉnh", "🎬"
        )

        scanned_luts = indexer.scan_luts()
        self.all_luts = dicts_to_assets(scanned_luts, "lut", "cinema", "Màu tự động", "🎨") if scanned_luts else MOCKUP_LUTS.copy()
        
        scanned_memes = indexer.scan_memes()
        self.all_memes = dicts_to_assets(scanned_memes, "meme", "trending", "Meme", "🎭") if scanned_memes else MOCKUP_MEMES.copy()
        
        scanned_sfx = indexer.scan_sfx()
        self.all_sfx = dicts_to_assets(scanned_sfx, "sfx", "accent", "Âm thanh", "🔊") if scanned_sfx else MOCKUP_SFX.copy()
        
        self.all_icons = MOCKUP_ICONS.copy()
        self.all_overlays = MOCKUP_OVERLAYS.copy()
        
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

        self.current_rail_tab = "text"
        self.current_sub_cat = "all"
        self.current_aspect_ratio = "16:9"
        self.card_widgets: List[AssetCard] = []
        self.selected_asset = self.all_presets[0] if self.all_presets else None

        self._init_ui()
        self.sfx_playback_timer = QTimer(self)
        self.sfx_playback_timer.timeout.connect(self._on_inspector_sfx_playback_finished)
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(lambda: self.lbl_toast.hide() if hasattr(self, "lbl_toast") else None)
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
        # WIDGETS TƯƠNG THÍCH NGƯỢC (Bắt buộc cho app.py & test_ui.py)
        # =====================================================================
        self.group_presets = QGroupBox("Legacy")
        self.check_subtitle = QCheckBox()
        self.check_subtitle.setChecked(True)
        self.combo_text_preset = QComboBox()
        self.btn_preview_preset = QPushButton()
        self.btn_save_custom_preset = QPushButton()
        self.combo_split_mode = QComboBox()
        self.combo_split_mode.addItem('Ký tự', 'characters')
        self.combo_split_mode.addItem('Từ', 'words')
        self.combo_split_mode.addItem('Câu', 'sentences')
        self.txt_split_limit = QLineEdit("42")
        self.txt_font = QLineEdit("Arial")
        self.txt_size = QLineEdit("48")
        self.txt_color = QLineEdit("#FFFFFF")

        self.group_presets.hide()
        self.check_subtitle.hide()
        self.combo_text_preset.hide()
        self.btn_preview_preset.hide()
        self.btn_save_custom_preset.hide()
        self.combo_split_mode.hide()
        self.txt_split_limit.hide()
        self.txt_font.hide()
        self.txt_size.hide()
        self.txt_color.hide()

        # =====================================================================
        # CỘT 1: RAIL ĐIỀU HƯỚNG CHÍNH (78px)
        # =====================================================================
        self.rail = QFrame()
        self.rail.setFixedWidth(78)
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
            ("fav", "❤️\nMẫu của tôi")
        ]

        self.rail_btns = {}
        for tid, tname in tabs_data:
            btn = QPushButton(tname)
            btn.setCheckable(True)
            btn.setFixedSize(64, 58)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    border-radius: 10px;
                    color: {ThemeColors.TEXT_MUTED};
                    font-size: 11px;
                    text-align: center;
                    border: none;
                    background: transparent;
                }}
                QPushButton:hover {{
                    color: {ThemeColors.TEXT_PRIMARY};
                    background: {ThemeColors.BG_CARD};
                }}
                QPushButton:checked {{
                    color: #c4b5fd;
                    background: {ThemeColors.VIOLET_LO};
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
        # CỘT 2: CATEGORIES PHÂN LOẠI CON (204px)
        # =====================================================================
        self.cats = QFrame()
        self.cats.setFixedWidth(204)
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
        scroll_cats.setStyleSheet("background: transparent; border: none;")
        self.cats_container = QWidget()
        self.cats_vbox = QVBoxLayout(self.cats_container)
        self.cats_vbox.setContentsMargins(8, 0, 8, 0)
        self.cats_vbox.setSpacing(2)
        scroll_cats.setWidget(self.cats_container)
        cats_layout.addWidget(scroll_cats, stretch=1)

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
        # CỘT 3: MAIN SECTION - LƯỚI CARD TRỰC QUAN (flex 1)
        # =====================================================================
        self.main_section = QWidget()
        self.main_section.setStyleSheet(f"background: {ThemeColors.BG_CARD};")
        main_sec_layout = QVBoxLayout(self.main_section)
        main_sec_layout.setContentsMargins(0, 0, 0, 0)
        main_sec_layout.setSpacing(0)

        main_head = QFrame()
        main_head.setFixedHeight(52)
        main_head.setStyleSheet(f"""
            QFrame {{
                background-color: {ThemeColors.BG_MAIN};
                border-bottom: 1px solid {ThemeColors.BORDER_DEFAULT};
            }}
        """)
        mh_layout = QHBoxLayout(main_head)
        mh_layout.setContentsMargins(16, 8, 16, 8)
        mh_layout.setSpacing(10)

        self.lbl_main_cat = QLabel("Chữ chuyển động")
        self.lbl_main_cat.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {ThemeColors.TEXT_PRIMARY};")
        mh_layout.addWidget(self.lbl_main_cat)

        self.lbl_count_badge = QLabel("14 mẫu")
        self.lbl_count_badge.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; font-size: 12px;")
        mh_layout.addWidget(self.lbl_count_badge)

        mh_layout.addStretch()

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Tìm kiếm đạo cụ...")
        self.txt_search.setFixedWidth(260)
        self.txt_search.textChanged.connect(self._refresh_grid)
        self.txt_search.setStyleSheet(f"""
            QLineEdit {{
                background: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 8px;
                padding: 5px 10px;
                color: {ThemeColors.TEXT_PRIMARY};
            }}
            QLineEdit:focus {{
                border-color: {ThemeColors.PRIMARY};
            }}
        """)
        mh_layout.addWidget(self.txt_search)

        scroll_grid = QScrollArea()
        scroll_grid.setWidgetResizable(True)
        scroll_grid.setStyleSheet("background: transparent; border: none;")
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(self.grid_container)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.grid.setSpacing(12)
        scroll_grid.setWidget(self.grid_container)

        main_sec_layout.addWidget(main_head)
        main_sec_layout.addWidget(scroll_grid, stretch=1)

        # =====================================================================
        # CỘT 4: BẢNG TINH CHỈNH INSPECTOR (336px)
        # =====================================================================
        self.inspector = QFrame()
        self.inspector.setFixedWidth(336)
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

        # 2. Viewport 1: Text Live Preview
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
        t_prev_layout.setContentsMargins(8, 8, 8, 8)
        t_prev_layout.setAlignment(Qt.AlignCenter)

        self.lbl_insp_preview = QLabel("PREVIEW", self.inspector_text_preview)
        self.lbl_insp_preview.setTextFormat(Qt.RichText)
        self.lbl_insp_preview.setAlignment(Qt.AlignCenter)
        self.lbl_insp_preview.setWordWrap(True)
        self.lbl_insp_preview.setStyleSheet("background: transparent; border: none; font-size: 16px; color: #FFFFFF;")
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
        lut_v_layout.addWidget(self.lut_split_slider)
        self.insp_vbox.addWidget(self.inspector_lut_viewport)

        # 4. Viewport 3: SFX Waveform Visualizer & Playback Controller
        self.inspector_sfx_viewport = QWidget()
        sfx_v_layout = QVBoxLayout(self.inspector_sfx_viewport)
        sfx_v_layout.setContentsMargins(0, 0, 0, 0)
        sfx_v_layout.setSpacing(6)

        h_ctrl = QHBoxLayout()
        h_ctrl.setContentsMargins(0, 0, 0, 0)
        h_ctrl.setSpacing(8)

        self.sfx_play_btn = QPushButton("▶ Nghe thử", self.inspector_sfx_viewport)
        self.sfx_play_btn.setCursor(Qt.PointingHandCursor)
        self.sfx_play_btn.setStyleSheet(f"""
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
        """)
        self.sfx_play_btn.clicked.connect(self._toggle_inspector_sfx_play)
        h_ctrl.addWidget(self.sfx_play_btn)

        self.sfx_time_lbl = QLabel("0:02", self.inspector_sfx_viewport)
        self.sfx_time_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {ThemeColors.TEXT_PRIMARY};")
        h_ctrl.addWidget(self.sfx_time_lbl)

        h_ctrl.addStretch()

        self.sfx_hint_lbl = QLabel("assets/sfx/whoosh.wav", self.inspector_sfx_viewport)
        self.sfx_hint_lbl.setStyleSheet(f"font-size: 11px; color: {ThemeColors.TEXT_MUTED};")
        h_ctrl.addWidget(self.sfx_hint_lbl)
        sfx_v_layout.addLayout(h_ctrl)

        self.sfx_waveform_canvas = WaveformCanvas(preset_id="sfx", num_bars=32, height=56, parent=self.inspector_sfx_viewport)
        self.sfx_waveform_canvas.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 8px;
            padding: 4px;
        """)
        sfx_v_layout.addWidget(self.sfx_waveform_canvas)
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
        self.insp_vbox.addWidget(self.inspector_icon_viewport)

        # Mặc định ẩn các viewport không phải Text
        self.inspector_lut_viewport.hide()
        self.inspector_sfx_viewport.hide()
        self.inspector_trans_viewport.hide()
        self.inspector_icon_viewport.hide()

        # 2. Thông tin chi tiết
        self.lbl_insp_title = QLabel("<b>Alex Hormozi Pop</b>")
        self.lbl_insp_title.setStyleSheet(f"font-size: 15px; color: {ThemeColors.TEXT_PRIMARY};")
        self.lbl_insp_sub = QLabel("Kinetic bounce · Font: Arial Black")
        self.lbl_insp_sub.setStyleSheet(f"font-size: 12px; color: {ThemeColors.TEXT_MUTED};")
        self.insp_vbox.addWidget(self.lbl_insp_title)
        self.insp_vbox.addWidget(self.lbl_insp_sub)

        # 3. Kéo thả Master Box
        self.preview_lbl = DraggableAssetLabel()
        self.preview_lbl.setFixedHeight(44)
        self.preview_lbl.setText("🖱️ Giữ chuột và kéo vào Timeline")
        self.preview_lbl.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 2px dashed {ThemeColors.PRIMARY};
            border-radius: 6px;
            color: #c4b5fd;
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
        self.txt_single_title = QLineEdit("ResolveFlow Studio")
        self.txt_single_title.textChanged.connect(self._on_sample_text_changed)
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
            self._set_swatch_color(k, c)

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

        self.btn_insert_title_playhead = QPushButton("🚀 Chèn tại Playhead (V2)")
        self.btn_insert_playhead = self.btn_insert_title_playhead
        self.btn_insert_title_playhead.setCursor(Qt.PointingHandCursor)
        self.btn_insert_title_playhead.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:1 #7c3aed);
                color: #ffffff;
                font-weight: bold;
                font-size: 13px;
                padding: 10px;
                border-radius: 8px;
                border: none;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #8b5cf6);
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

        h_acts_sub.addWidget(self.btn_drag_davinci)
        h_acts_sub.addWidget(self.btn_copy_fusion)
        h_acts_sub.addWidget(self.btn_install_presets)
        acts_vbox.addLayout(h_acts_sub)

        insp_layout.addWidget(scroll_insp, stretch=1)
        insp_layout.addWidget(insp_acts)


        # Ghép 4 cột vào layout chính
        main_layout.addWidget(self.rail)
        main_layout.addWidget(self.cats)
        main_layout.addWidget(self.main_section, stretch=1)
        main_layout.addWidget(self.inspector)

    # =========================================================================
    # 7. LOGIC ĐIỀU HƯỚNG VÀ LỌC DANH MỤC
    # =========================================================================
    def _filter_by_rail(self, tab_id: str):
        self._stop_inspector_sfx_play()
        self.current_rail_tab = tab_id
        for tid, btn in self.rail_btns.items():
            btn.setChecked(tid == tab_id)

        # Cập nhật danh mục con ở Cột 2
        self._rebuild_categories_column()
        self._refresh_grid()


    def _rebuild_categories_column(self):
        # Dọn sạch các nút cũ
        while self.cats_vbox.count():
            item = self.cats_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tab = self.current_rail_tab
        headers_map = {
            "text": "Mẫu chữ Text+",
            "lut": "Bộ lọc màu LUT",
            "trans": "Chuyển cảnh",
            "icon": "Sticker biểu tượng",
            "overlay": "Hiệu ứng lớp phủ",
            "meme": "Meme thịnh hành",
            "sfx": "Âm thanh SFX",
            "fav": "Mẫu đã lưu"
        }
        self.lbl_cats_head.setText(headers_map.get(tab, "Danh mục"))

        # Xác định nhóm con
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
                ("trans", "Chuyển cảnh"),
                ("accent", "Nhấn mạnh"),
                ("fun", "Hài hước"),
                ("tech", "Kỹ thuật")
            ])
        elif tab == "overlay":
            sub_cats.extend([
                ("cinematic", "Điện ảnh"),
                ("green_screen", "Phông xanh")
            ])
        elif tab == "meme":
            sub_cats.extend([
                ("reaction", "Reaction"),
                ("trending", "Trending")
            ])

        for cid, cname in sub_cats:
            btn = QPushButton(f"  {cname}")
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
            self.cats_vbox.addWidget(btn)

        self.cats_vbox.addStretch()
        self.current_sub_cat = "all"
        # Bật nút 'Tất cả'
        if self.cats_vbox.count() > 1:
            first_btn = self.cats_vbox.itemAt(0).widget()
            if isinstance(first_btn, QPushButton):
                first_btn.setChecked(True)

    def _on_sub_category_clicked(self, cid: str):
        self.current_sub_cat = cid
        for i in range(self.cats_vbox.count()):
            item = self.cats_vbox.itemAt(i)
            if item and isinstance(item.widget(), QPushButton):
                btn = item.widget()
                btn.setChecked(btn.text().strip() in cid or (cid == "all" and "Tất cả" in btn.text()))
        self._refresh_grid()

    def _on_category_pill_clicked(self, category_id: str):
        """Tương thích ngược với unit tests."""
        self._on_sub_category_clicked(category_id)

    # =========================================================================
    # 8. QUẢN LÝ LƯỚI CARD & TÌM KIẾM
    # =========================================================================
    def _populate_cards(self):
        for c in self.card_widgets:
            c.deleteLater()
        self.card_widgets.clear()

        # Nạp combo_text_preset để tương thích test
        self.combo_text_preset.clear()
        for p in self.all_presets:
            if isinstance(p, TextStylePreset):
                self.combo_text_preset.addItem(p.name, p.id)

        for item in self.all_assets:
            pid = getattr(item, "id", "")
            is_fav = pid in self.favorites
            card = AssetCard(preset=item, is_fav=is_fav, sample_text_func=lambda: self.txt_single_title.text().strip())
            card.selected_signal.connect(self._on_card_selected)
            card.favorite_toggled.connect(self._on_favorite_toggled)
            self.card_widgets.append(card)

    def _refresh_grid(self):
        # Dọn sạch các ô trên grid
        while self.grid.count():
            self.grid.takeAt(0)

        tab = self.current_rail_tab
        cat = self.current_sub_cat
        query = self.txt_search.text().strip().lower()

        visible = []
        for card in self.card_widgets:
            item = card.preset
            i_tab = "text" if isinstance(item, TextStylePreset) else "trans" if isinstance(item, TransitionStylePreset) else getattr(item, "tab", "")
            i_cat = getattr(item, "category", "")
            i_id = getattr(item, "id", "")
            i_name = getattr(item, "name", "").lower()
            i_fav = i_id in self.favorites

            # Lọc theo Tab
            if tab == "fav":
                if not i_fav:
                    card.setVisible(False)
                    continue
            elif i_tab != tab:
                card.setVisible(False)
                continue

            # Lọc theo Phân loại con
            if cat != "all" and i_cat != cat:
                card.setVisible(False)
                continue

            # Lọc theo Từ khóa tìm kiếm
            if query and query not in i_name and query not in i_cat:
                card.setVisible(False)
                continue

            visible.append(card)

        # Cập nhật số lượng đếm
        self.lbl_count_badge.setText(f"{len(visible)} mẫu")

        # Đặt vào lưới (3 cột)
        cols = 3
        for idx, card in enumerate(visible):
            card.setVisible(True)
            self.grid.addWidget(card, idx // cols, idx % cols)

    def _on_card_selected(self, preset_id: str):
        self._stop_inspector_sfx_play()
        # Cập nhật combo_text_preset cho unit tests
        idx = self.combo_text_preset.findData(preset_id)
        if idx >= 0:
            self.combo_text_preset.setCurrentIndex(idx)

        # Highlight card
        for c in self.card_widgets:
            is_match = (getattr(c.preset, "id", "") == preset_id)
            c.set_selected(is_match)
            if is_match:
                self.selected_asset = c.preset

        # Cập nhật Inspector
        self._update_inspector_details()


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

    def _on_lut_split_slider_changed(self, value: int):
        if hasattr(self, "lut_split_widget"):
            self.lut_split_widget.set_split(value)
        if hasattr(self, "lbl_split_pct"):
            self.lbl_split_pct.setText(f"{value}%")

    def _toggle_inspector_sfx_play(self):
        if hasattr(self, "sfx_waveform_canvas") and self.sfx_waveform_canvas.is_playing:
            self._stop_inspector_sfx_play()
        else:
            self._start_inspector_sfx_play()

    def _start_inspector_sfx_play(self):
        if hasattr(self, "sfx_waveform_canvas"):
            self.sfx_waveform_canvas.is_playing = True
            self.sfx_waveform_canvas.update()
        if hasattr(self, "sfx_play_btn"):
            self.sfx_play_btn.setText("⏸ Dừng")
        dur = getattr(self.selected_asset, "duration", 1.5)
        if hasattr(self, "sfx_playback_timer"):
            self.sfx_playback_timer.stop()
            self.sfx_playback_timer.start(int(dur * 1000) if dur > 0 else 1500)

    def _stop_inspector_sfx_play(self):
        if hasattr(self, "sfx_waveform_canvas"):
            self.sfx_waveform_canvas.is_playing = False
            self.sfx_waveform_canvas.update()
        if hasattr(self, "sfx_play_btn"):
            self.sfx_play_btn.setText("▶ Nghe thử")
        if hasattr(self, "sfx_playback_timer"):
            self.sfx_playback_timer.stop()

    def _on_inspector_sfx_playback_finished(self):
        self._stop_inspector_sfx_play()

    def _update_inspector_details(self):
        if not self.selected_asset:
            return

        name = getattr(self.selected_asset, "name", "")
        self.lbl_insp_title.setText(f"<b>{name}</b>")
        sub = getattr(self.selected_asset, "sub", "") or getattr(self.selected_asset, "description", "")
        self.lbl_insp_sub.setText(sub)

        is_text = isinstance(self.selected_asset, TextStylePreset) or getattr(self.selected_asset, "tab", "") == "text"
        is_lut = getattr(self.selected_asset, "tab", "") == "lut"
        is_sfx = getattr(self.selected_asset, "tab", "") == "sfx"
        is_trans = isinstance(self.selected_asset, TransitionStylePreset) or getattr(self.selected_asset, "tab", "") == "trans"

        if is_text:
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

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 3 (V3)", "Video Track 1 (V1)"])
            self.lbl_track_hint.setText("Đề xuất: Chèn lên V2/V3 để tránh đè video chính V1.")

            font_val = getattr(self.selected_asset, "font", "Montserrat")
            f_idx = self.combo_font.findText(font_val)
            if f_idx >= 0:
                self.combo_font.setCurrentIndex(f_idx)
            sz_val = getattr(self.selected_asset, "size", 72)
            self.slide_font_size.setValue(int(sz_val))

            std_col = getattr(self.selected_asset, "standard_color", None) or "#FFFFFF"
            hl_col = getattr(self.selected_asset, "highlight_color", None) or "#FACC15"
            out_col = getattr(self.selected_asset, "outline_color", None) or "#000000"
            box_col = getattr(self.selected_asset, "box_color", None) or getattr(self.selected_asset, "glow_color", None) or "#EF4444"
            self._set_swatch_color("text", std_col)
            self._set_swatch_color("highlight", hl_col)
            self._set_swatch_color("stroke", out_col)
            self._set_swatch_color("box_glow", box_col)

            sample = self.txt_single_title.text().strip() or "ResolveFlow Studio"
            pid = getattr(self.selected_asset, "id", "")
            html = format_text_preset_html(pid, name, sample, std_col, is_large=True)
            self.lbl_insp_preview.setText(html)
            self.btn_insert_title_playhead.setText("🚀 Chèn tại Playhead (V2)")

        elif is_lut:
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

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Timeline (Màu toàn bộ)", "Clip đang chọn"])
            self.lbl_track_hint.setText("Áp màu LUT điện ảnh lên Node cấp Timeline hoặc Clip.")

            pal = getattr(self.selected_asset, "pal", [])
            self.lut_split_widget.set_lut(name, pal)
            self.btn_insert_title_playhead.setText("🎨 Áp LUT lên Timeline")

        elif is_sfx:
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

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Audio Track 2 (A2 - SFX)", "Audio Track 1 (A1)"])
            self.lbl_track_hint.setText("Gợi ý: Audio Track 2 với bù âm -12dB phù hợp mạng xã hội, không đè giọng nói.")

            pid = getattr(self.selected_asset, "id", "sfx")
            clean_id = pid.replace("sfx_", "")
            dur = getattr(self.selected_asset, "duration", 2.0)
            self.sfx_waveform_canvas.set_preset_id(pid, num_bars=32)
            self.sfx_time_lbl.setText(f"{dur:.1f}s")
            self.sfx_hint_lbl.setText(f"assets/sfx/{clean_id}.wav")
            self.sfx_play_btn.setText("▶ Nghe thử")
            self.btn_insert_title_playhead.setText("🔊 Chèn âm thanh tại Playhead (A2)")

        elif is_trans:
            self.inspector_aspect_btn.hide()
            self.inspector_text_preview.hide()
            self.inspector_lut_viewport.hide()
            self.inspector_sfx_viewport.hide()
            self.inspector_trans_viewport.show()
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

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Video Track 1 (V1 - Cắt cảnh)", "Video Track 2 (V2)"])
            self.lbl_track_hint.setText("Kéo hoặc chèn vào điểm giao nhau giữa 2 clip.")

            kind = getattr(self.selected_asset, "category", "transform")
            frames = getattr(self.selected_asset, "frames", 20)
            self.trans_loop_widget.set_transition(name, kind, frames)
            self.btn_insert_title_playhead.setText("🔄 Chèn chuyển cảnh vào Timeline")

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

            self.combo_target_track.clear()
            self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 3 (V3)", "Video Track 1 (V1)"])
            self.lbl_track_hint.setText("Đề xuất: Chèn lên V2/V3 để tránh đè video chính V1.")

            tab_type = getattr(self.selected_asset, "tab", "icon")
            badge = getattr(self.selected_asset, "badge_icon", "✨")
            color_hex = getattr(self.selected_asset, "color_hex", "#facc15")
            self.icon_meme_widget.set_item(name, tab_type, badge, color_hex, sub)
            self.btn_insert_title_playhead.setText("➕ Chèn vào Timeline")

    def _on_sample_text_changed(self, text: str):
        self._update_text_preview_from_params()

    def _on_font_changed(self, font_name: str):
        self.txt_font.setText(font_name)
        self._update_text_preview_from_params()

    def _on_font_size_changed(self, value: int):
        self.lbl_font_size_val.setText(f"{value} px")
        self.txt_size.setText(str(value))
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

    def _set_swatch_color(self, key: str, hex_color: str):
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
        std_col = self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, "swatch_colors") else "#FFFFFF"
        html = format_text_preset_html(pid, name, sample, std_col, is_large=True)
        self.lbl_insp_preview.setText(html)

    def _show_toast(self, message: str):
        if hasattr(self, "lbl_toast"):
            self.lbl_toast.setText(message)
            self.lbl_toast.show()
            self.toast_timer.stop()
            self.toast_timer.start(2500)

    def _insert_at_playhead(self):
        if not self.selected_asset:
            return

        is_text = isinstance(self.selected_asset, TextStylePreset) or getattr(self.selected_asset, "tab", "") == "text"
        is_sfx = getattr(self.selected_asset, "tab", "") == "sfx"
        is_lut = getattr(self.selected_asset, "tab", "") == "lut"

        try:
            resolve_auto = ResolveAutomation()
        except Exception:
            resolve_auto = None

        if is_text:
            text = self.txt_single_title.text().strip() or "ResolveFlow Title"
            preset_id = getattr(self.selected_asset, "id", "karaoke_pop")
            dur = float(self.slide_title_dur.value()) / 10.0 if hasattr(self, 'slide_title_dur') and self.slide_title_dur.value() > 0 else 4.0
            font_name = self.combo_font.currentText() if hasattr(self, 'combo_font') else "Arial"
            font_size = self.slide_font_size.value() if hasattr(self, 'slide_font_size') else 48
            color_hex = self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, 'swatch_colors') else "#FFFFFF"

            if resolve_auto and hasattr(resolve_auto, "insert_title_at_playhead"):
                try:
                    resolve_auto.insert_title_at_playhead(
                        text=text,
                        preset_id=preset_id,
                        duration_sec=dur,
                        font_name=font_name,
                        font_size=font_size,
                        color_hex=color_hex
                    )
                except Exception:
                    pass
            self.insert_title_requested.emit(text, preset_id, dur)
            self._show_toast(f"Đã chèn “{getattr(self.selected_asset, 'name', text)}” tại Playhead (V2)")
        elif is_sfx:
            pid = getattr(self.selected_asset, "id", "whoosh")
            clean_id = pid.replace("sfx_", "")
            sfx_file = os.path.join("assets", "sfx", f"{clean_id}.wav")
            if not os.path.exists(sfx_file):
                sfx_file = os.path.join("assets", "sfx", f"{pid}.wav")
            dur = getattr(self.selected_asset, "duration", 1.5)
            if resolve_auto and hasattr(resolve_auto, "insert_sfx_to_track"):
                try:
                    resolve_auto.insert_sfx_to_track(
                        sfx_path=os.path.abspath(sfx_file) if os.path.exists(sfx_file) else sfx_file,
                        target_track=2,
                        volume_offset_db=-12.0
                    )
                except Exception:
                    pass
            self.insert_sfx_requested.emit(sfx_file, 2, -12.0)
            self._show_toast(f"Đã chèn âm thanh “{getattr(self.selected_asset, 'name', pid)}” tại Playhead (A2)")
        elif is_lut:
            lut_name = getattr(self.selected_asset, "name", "")
            pid = getattr(self.selected_asset, "id", "")
            lut_file = os.path.join("assets", "luts", f"ResolveFlow_{pid}.cube")
            if resolve_auto and hasattr(resolve_auto, "apply_look_lut"):
                try:
                    resolve_auto.apply_look_lut(lut_path=lut_file, scope="timeline")
                except Exception:
                    pass
            self.apply_lut_requested.emit(pid)
            self._show_toast(f"Đã áp LUT “{lut_name}” lên Timeline")
        else:
            self._show_toast(f"Đã chèn “{getattr(self.selected_asset, 'name', 'Asset')}” vào Timeline")

    def _create_drag_mime_data(self, asset: Optional[Any] = None) -> QMimeData:
        target = asset if asset is not None else self.selected_asset
        mime_data = QMimeData()
        if not target:
            return mime_data

        temp_dir = tempfile.gettempdir()
        sample_txt = self.txt_single_title.text().strip() or "ResolveFlow Title"
        asset_id = getattr(target, "id", "asset")
        tab_type = getattr(target, "tab", "")

        if isinstance(target, TextStylePreset) or tab_type == "text":
            temp_setting = os.path.join(temp_dir, f"ResolveFlow_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            if isinstance(target, TextStylePreset):
                FusionSettingGenerator.export_setting_file(target, temp_setting, sample_text=sample_txt)
            else:
                preset_obj = next((p for p in self.all_presets if getattr(p, "id", "") == asset_id), None)
                if isinstance(preset_obj, TextStylePreset):
                    FusionSettingGenerator.export_setting_file(preset_obj, temp_setting, sample_text=sample_txt)
                else:
                    tmp_p = TextStylePreset(
                        id=asset_id,
                        name=getattr(target, "name", "Text Title"),
                        font=self.combo_font.currentText() if hasattr(self, "combo_font") else "Arial",
                        size=self.slide_font_size.value() if hasattr(self, "slide_font_size") else 48,
                        weight="bold",
                        standard_color=self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, "swatch_colors") else "#FFFFFF"
                    )
                    FusionSettingGenerator.export_setting_file(tmp_p, temp_setting, sample_text=sample_txt)
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(temp_setting))])

        elif isinstance(target, TransitionStylePreset) or tab_type == "trans":
            temp_setting = os.path.join(temp_dir, f"ResolveFlow_{asset_id}_{uuid.uuid4().hex[:6]}.setting")
            if isinstance(target, TransitionStylePreset):
                TransitionMacroGenerator.export_setting_file(target, temp_setting)
            else:
                trans_obj = next((t for t in self.all_transitions if getattr(t, "id", "") == asset_id), None)
                if isinstance(trans_obj, TransitionStylePreset):
                    TransitionMacroGenerator.export_setting_file(trans_obj, temp_setting)
                else:
                    tmp_t = TransitionStylePreset(
                        id=asset_id,
                        name=getattr(target, "name", "Transition"),
                        category="transform",
                        duration_frames=getattr(target, "frames", 20)
                    )
                    TransitionMacroGenerator.export_setting_file(tmp_t, temp_setting)
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(temp_setting))])

        elif tab_type == "sfx" or "sfx" in asset_id:
            clean_id = asset_id.replace("sfx_", "")
            candidate_paths = [
                os.path.join("assets", "sfx", f"{clean_id}.wav"),
                os.path.join("assets", "sfx", f"{asset_id}.wav"),
                getattr(target, "file_path", "")
            ]
            sfx_file = None
            for p in candidate_paths:
                if p and os.path.exists(p):
                    sfx_file = os.path.abspath(p)
                    break
            if not sfx_file:
                import wave
                sfx_file = os.path.join(temp_dir, f"ResolveFlow_{clean_id}.wav")
                if not os.path.exists(sfx_file):
                    try:
                        with wave.open(sfx_file, "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(44100)
                            wf.writeframes(b"\x00\x00" * 4410)
                    except Exception:
                        with open(sfx_file, "wb") as f:
                            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(sfx_file))])

        elif tab_type == "lut":
            candidate_paths = [
                os.path.join("assets", "luts", f"ResolveFlow_{asset_id}.cube"),
                os.path.join("assets", "luts", f"{asset_id}.cube"),
                getattr(target, "file_path", "")
            ]
            lut_file = None
            for p in candidate_paths:
                if p and os.path.exists(p):
                    lut_file = os.path.abspath(p)
                    break
            if not lut_file:
                lut_file = os.path.join(temp_dir, f"ResolveFlow_{asset_id}.cube")
                if not os.path.exists(lut_file):
                    with open(lut_file, "w", encoding="utf-8") as f:
                        f.write(f'TITLE "ResolveFlow_{asset_id}"\nLUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 0.0 0.0\n0.0 1.0 0.0\n1.0 1.0 0.0\n0.0 0.0 1.0\n1.0 0.0 1.0\n0.0 1.0 1.0\n1.0 1.0 1.0\n')
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(lut_file))])

        elif tab_type in ("meme", "overlay"):
            dir_name = "memes" if tab_type == "meme" else getattr(target, "category", "cinematic")
            candidate_paths = [
                os.path.join("assets", "broll_memes", dir_name, f"{asset_id}.mp4"),
                os.path.join("assets", "broll_memes", f"{asset_id}.mp4"),
                getattr(target, "file_path", "")
            ]
            vid_file = None
            for p in candidate_paths:
                if p and os.path.exists(p):
                    vid_file = os.path.abspath(p)
                    break
            if not vid_file:
                vid_file = os.path.join(temp_dir, f"ResolveFlow_{asset_id}.mp4")
                if not os.path.exists(vid_file):
                    with open(vid_file, "wb") as f:
                        f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42")
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(vid_file))])

        elif isinstance(target, LocalAsset):
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(target.file_path))])

        else:
            temp_path = os.path.join(temp_dir, f"ResolveFlow_{asset_id}.txt")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(getattr(target, "name", "ResolveFlow Asset"))
            mime_data.setUrls([QUrl.fromLocalFile(os.path.abspath(temp_path))])

        return mime_data

    def _get_current_fusion_macro_code(self) -> str:
        target = self.selected_asset
        if not target:
            return ""

        sample_txt = self.txt_single_title.text().strip() or "ResolveFlow Title"
        asset_id = getattr(target, "id", "asset")
        tab_type = getattr(target, "tab", "")

        if isinstance(target, TextStylePreset):
            return FusionSettingGenerator.generate_setting_content(target, sample_text=sample_txt)
        elif tab_type == "text":
            preset_obj = next((p for p in self.all_presets if getattr(p, "id", "") == asset_id), None)
            if isinstance(preset_obj, TextStylePreset):
                return FusionSettingGenerator.generate_setting_content(preset_obj, sample_text=sample_txt)
            tmp_p = TextStylePreset(
                id=asset_id,
                name=getattr(target, "name", "Title"),
                font=self.combo_font.currentText() if hasattr(self, "combo_font") else "Arial",
                size=self.slide_font_size.value() if hasattr(self, "slide_font_size") else 48,
                weight="bold",
                standard_color=self.swatch_colors.get("text", "#FFFFFF") if hasattr(self, "swatch_colors") else "#FFFFFF"
            )
            return FusionSettingGenerator.generate_setting_content(tmp_p, sample_text=sample_txt)
        elif isinstance(target, TransitionStylePreset):
            return TransitionMacroGenerator.generate_setting_content(target)
        elif tab_type == "trans":
            trans_obj = next((t for t in self.all_transitions if getattr(t, "id", "") == asset_id), None)
            if isinstance(trans_obj, TransitionStylePreset):
                return TransitionMacroGenerator.generate_setting_content(trans_obj)
            tmp_t = TransitionStylePreset(
                id=asset_id,
                name=getattr(target, "name", "Transition"),
                category="transform",
                duration_frames=getattr(target, "frames", 20)
            )
            return TransitionMacroGenerator.generate_setting_content(tmp_t)
        else:
            name = getattr(target, "name", "Asset")
            return f"""{{
    Tools = ordered() {{
        RF_{asset_id} = TextPlus {{
            Inputs = {{
                StyledText = Input {{ Value = "{name}", }},
                Font = Input {{ Value = "Arial", }},
                Size = Input {{ Value = 0.08, }},
            }},
            ViewInfo = OperatorInfo {{ Pos = {{ 220, 36.3 }} }},
        }}
    }}
}}"""

    def _install_to_fusion(self) -> Tuple[int, str]:
        is_trans = isinstance(self.selected_asset, TransitionStylePreset) or getattr(self.selected_asset, "tab", "") == "trans"
        if is_trans:
            count, target_dir = TransitionMacroGenerator.install_transitions_to_davinci_resolve()
            msg = f"Đã cài đặt {count} chuyển cảnh vào DaVinci Resolve ({target_dir})"
        else:
            count, target_dir = FusionSettingGenerator.install_presets_to_davinci_resolve()
            msg = f"Đã cài đặt {count} mẫu chữ vào DaVinci Resolve ({target_dir})"
        self.install_presets_requested.emit()
        self._show_toast(msg)
        return count, target_dir

    def _on_insert_single_title_clicked(self):
        self._insert_at_playhead()

    def _on_install_presets_clicked(self):
        self._install_to_fusion()

    def _on_copy_fusion_clicked(self):
        macro_code = self._get_current_fusion_macro_code()
        QApplication.clipboard().setText(macro_code)
        preset_id = self.combo_text_preset.currentData() or getattr(self.selected_asset, "id", "karaoke_pop")
        self.copy_fusion_node_requested.emit(preset_id)
        self._show_toast("Đã copy Fusion node! Dán vào trang Fusion bằng Ctrl+V")

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
        self._refresh_grid()
        QMessageBox.information(self, "Quét hoàn tất", f"Đã nạp thành công {found} tài nguyên cá nhân vào Kho Đạo Cụ!")

