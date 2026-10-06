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
from typing import Optional, Callable, List, Dict, Any, Tuple

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QSlider, QFrame, QScrollArea, QGridLayout, QApplication, QMessageBox,
    QSizePolicy, QFileDialog
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
        native_preset: Any = None
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

        drag.setMimeData(mime_data)
        self.setCursor(Qt.OpenHandCursor)
        drag.exec_(Qt.CopyAction)


# =========================================================================
# 5. ASSET CARD (Thẻ đạo cụ hiển thị trên lưới)
# =========================================================================
class AssetCard(QFrame):
    selected_signal = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)
    add_requested = pyqtSignal(object)

    def __init__(self, preset: Any, is_fav: bool = False, sample_text_func=None, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.is_fav = is_fav
        self.sample_text_func = sample_text_func
        self.is_selected = False

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
        th_layout = QVBoxLayout(self.thumb)
        th_layout.setContentsMargins(6, 4, 6, 4)
        th_layout.setAlignment(Qt.AlignCenter)

        # Vẽ preview theo kiểu
        if isinstance(self.preset, TextStylePreset):
            lbl_p = QLabel(self.preset.name.split("(")[0].strip())
            f = QFont("Arial", 11)
            f.setBold(True)
            lbl_p.setFont(f)
            lbl_p.setStyleSheet(f"color: {self.preset.standard_color}; text-shadow: 0 1px 2px #000;")
            lbl_p.setAlignment(Qt.AlignCenter)
            th_layout.addWidget(lbl_p)
        elif getattr(self.preset, "tab", "") == "lut":
            # Dải màu LUT
            pal = getattr(self.preset, "pal", ["#1f2937", "#64748b", "#cbd5e1", "#f8fafc"])
            h_pal = QHBoxLayout()
            h_pal.setSpacing(2)
            for c in pal:
                stripe = QFrame()
                stripe.setFixedHeight(36)
                stripe.setStyleSheet(f"background-color: {c}; border-radius: 3px;")
                h_pal.addWidget(stripe)
            th_layout.addLayout(h_pal)
        elif getattr(self.preset, "tab", "") == "sfx":
            lbl_wave = QLabel(" ▂▃▅▆▇▆▅▃▂ ")
            lbl_wave.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-size: 16px;")
            lbl_wave.setAlignment(Qt.AlignCenter)
            th_layout.addWidget(lbl_wave)
        else:
            badge = getattr(self.preset, "badge_icon", "✨")
            lbl_icon = QLabel(badge)
            lbl_icon.setStyleSheet("font-size: 26px;")
            lbl_icon.setAlignment(Qt.AlignCenter)
            th_layout.addWidget(lbl_icon)

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
            pid = getattr(self.preset, "id", "")
            self.selected_signal.emit(pid)
        super().mousePressEvent(event)


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
        self.all_presets = BUILTIN_PRESETS.copy()
        self.all_transitions = BUILTIN_TRANSITIONS.copy()
        self.all_luts = MOCKUP_LUTS.copy()
        self.all_icons = MOCKUP_ICONS.copy()
        self.all_overlays = MOCKUP_OVERLAYS.copy()
        self.all_memes = MOCKUP_MEMES.copy()
        self.all_sfx = MOCKUP_SFX.copy()
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
        self.card_widgets: List[AssetCard] = []
        self.selected_asset = self.all_presets[0] if self.all_presets else None

        self._init_ui()
        self._populate_cards()
        self._filter_by_rail("text")

    def set_preset_provider(self, provider_func: Callable[[], Optional[Any]]):
        self._current_preset_callback = provider_func
        self.preview_lbl.preset_getter = provider_func
        self.preview_lbl.sample_text_getter = lambda: self.txt_single_title.text().strip() or "ResolveFlow Title"

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
            font-size: 13.5px;
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
        self.lbl_main_cat.setStyleSheet(f"font-weight: bold; font-size: 13.5px; color: {ThemeColors.TEXT_PRIMARY};")
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

        # 1. Khung Preview lớn
        self.lbl_insp_preview = QLabel("PREVIEW")
        self.lbl_insp_preview.setFixedHeight(120)
        self.lbl_insp_preview.setAlignment(Qt.AlignCenter)
        self.lbl_insp_preview.setStyleSheet(f"""
            QLabel {{
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
                font-size: 18px;
                font-weight: bold;
                color: #FFFFFF;
            }}
        """)
        self.insp_vbox.addWidget(self.lbl_insp_preview)

        # 2. Thông tin chi tiết
        self.lbl_insp_title = QLabel("<b>Alex Hormozi Pop</b>")
        self.lbl_insp_title.setStyleSheet(f"font-size: 15px; color: {ThemeColors.TEXT_PRIMARY};")
        self.lbl_insp_sub = QLabel("Kinetic bounce · Font: Arial Black")
        self.lbl_insp_sub.setStyleSheet(f"font-size: 11.5px; color: {ThemeColors.TEXT_MUTED};")
        self.insp_vbox.addWidget(self.lbl_insp_title)
        self.insp_vbox.addWidget(self.lbl_insp_sub)

        # 3. Kéo thả Master Box
        self.preview_lbl = DraggableAssetLabel()
        self.preview_lbl.setFixedHeight(44)
        self.preview_lbl.setText("🖱️ Giữ chuột và kéo vào Timeline")
        self.preview_lbl.setStyleSheet(f"""
            background-color: {ThemeColors.BG_CARD};
            border: 1.5px dashed {ThemeColors.PRIMARY};
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
        p_layout.setSpacing(8)

        p_layout.addWidget(QLabel("Chữ mẫu hiển thị:"))
        self.txt_single_title = QLineEdit("ResolveFlow Studio")
        self.txt_single_title.textChanged.connect(self._on_sample_text_changed)
        self.txt_single_title.setStyleSheet(f"""
            background: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 6px;
            color: {ThemeColors.TEXT_PRIMARY};
        """)
        p_layout.addWidget(self.txt_single_title)

        h_dur = QHBoxLayout()
        h_dur.addWidget(QLabel("Thời lượng:"))
        self.lbl_dur_val = QLabel("4.0s")
        self.lbl_dur_val.setStyleSheet(f"color: {ThemeColors.CYAN_HI}; font-weight: bold;")
        h_dur.addWidget(self.lbl_dur_val)
        p_layout.addLayout(h_dur)

        self.slide_title_dur = QSlider(Qt.Horizontal)
        self.slide_title_dur.setRange(10, 100)
        self.slide_title_dur.setValue(40)
        self.slide_title_dur.valueChanged.connect(lambda v: self.lbl_dur_val.setText(f"{v/10.0:.1f}s"))
        p_layout.addWidget(self.slide_title_dur)

        p_layout.addWidget(QLabel("Track đích:"))
        self.combo_target_track = QComboBox()
        self.combo_target_track.addItems(["Video Track 2 (V2)", "Video Track 1 (V1)", "Audio Track 2 (A2)", "Audio Track 1 (A1)"])
        self.combo_target_track.setStyleSheet(f"""
            background: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            border-radius: 6px;
            padding: 4px;
            color: {ThemeColors.TEXT_PRIMARY};
        """)
        p_layout.addWidget(self.combo_target_track)

        self.insp_vbox.addWidget(self.grp_params)
        self.insp_vbox.addStretch()
        scroll_insp.setWidget(self.insp_content)

        # 5. Cụm nút hành động chính ở dưới cùng Inspector (.insp-acts)
        insp_acts = QWidget()
        insp_acts.setStyleSheet(f"border-top: 1px solid {ThemeColors.BORDER_DEFAULT};")
        acts_vbox = QVBoxLayout(insp_acts)
        acts_vbox.setContentsMargins(16, 12, 16, 14)
        acts_vbox.setSpacing(8)

        self.btn_insert_title_playhead = QPushButton("🚀 Chèn tại Playhead (V2)")
        self.btn_insert_title_playhead.setCursor(Qt.PointingHandCursor)
        self.btn_insert_title_playhead.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8b5cf6, stop:1 #7c3aed);
                color: #ffffff;
                font-weight: bold;
                font-size: 12.5px;
                padding: 10px;
                border-radius: 8px;
                border: none;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #a78bfa, stop:1 #8b5cf6);
            }}
        """)
        self.btn_insert_title_playhead.clicked.connect(self._on_insert_single_title_clicked)
        acts_vbox.addWidget(self.btn_insert_title_playhead)

        h_acts_sub = QHBoxLayout()
        h_acts_sub.setSpacing(8)

        self.btn_install_presets = QPushButton("📥 Cài Đặt")
        self.btn_install_presets.setCursor(Qt.PointingHandCursor)
        self.btn_install_presets.clicked.connect(self._on_install_presets_clicked)
        self.btn_install_presets.setStyleSheet(f"""
            background: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            color: {ThemeColors.TEXT_PRIMARY};
            padding: 6px;
            border-radius: 6px;
        """)

        self.btn_copy_fusion = QPushButton("📋 Copy Fusion")
        self.btn_copy_fusion.setCursor(Qt.PointingHandCursor)
        self.btn_copy_fusion.clicked.connect(self._on_copy_fusion_clicked)
        self.btn_copy_fusion.setStyleSheet(f"""
            background: {ThemeColors.BG_CARD};
            border: 1px solid {ThemeColors.BORDER_DEFAULT};
            color: {ThemeColors.TEXT_PRIMARY};
            padding: 6px;
            border-radius: 6px;
        """)

        h_acts_sub.addWidget(self.btn_install_presets)
        h_acts_sub.addWidget(self.btn_copy_fusion)
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
                    font-size: 11.5px;
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

    def _update_inspector_details(self):
        if not self.selected_asset:
            return

        name = getattr(self.selected_asset, "name", "")
        self.lbl_insp_title.setText(f"<b>{name}</b>")
        sub = getattr(self.selected_asset, "sub", "") or getattr(self.selected_asset, "description", "")
        self.lbl_insp_sub.setText(sub)

        # Cập nhật text preview
        if isinstance(self.selected_asset, TextStylePreset):
            sample = self.txt_single_title.text().strip() or "ResolveFlow"
            self.lbl_insp_preview.setText(sample)
            self.lbl_insp_preview.setStyleSheet(f"""
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
                font-size: 20px;
                font-weight: bold;
                color: {self.selected_asset.standard_color};
            """)
            self.btn_insert_title_playhead.setText("🚀 Chèn tại Playhead (V2)")
        elif getattr(self.selected_asset, "tab", "") == "lut":
            self.lbl_insp_preview.setText("🎨 LUT PREVIEW")
            self.lbl_insp_preview.setStyleSheet(f"""
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
                color: #22d3ee;
                font-weight: bold;
            """)
            self.btn_insert_title_playhead.setText("🎨 Áp LUT lên Timeline")
        elif getattr(self.selected_asset, "tab", "") == "sfx":
            self.lbl_insp_preview.setText("🔊 SOUNDBOARD SFX")
            self.lbl_insp_preview.setStyleSheet(f"""
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
                color: #34d399;
                font-weight: bold;
            """)
            self.btn_insert_title_playhead.setText("🔊 Chèn âm thanh tại Playhead (A2)")
        else:
            badge = getattr(self.selected_asset, "badge_icon", "✨")
            self.lbl_insp_preview.setText(f"{badge}\n{name}")
            self.lbl_insp_preview.setStyleSheet(f"""
                background-color: {ThemeColors.BG_CARD};
                border: 1px solid {ThemeColors.BORDER_DEFAULT};
                border-radius: 10px;
                color: #FFFFFF;
                font-weight: bold;
            """)
            self.btn_insert_title_playhead.setText("➕ Chèn vào Timeline")

    def _on_sample_text_changed(self, text: str):
        if isinstance(self.selected_asset, TextStylePreset):
            self.lbl_insp_preview.setText(text.strip() or "ResolveFlow")

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

    # =========================================================================
    # 9. HÀNH ĐỘNG INSERT / COPY / INSTALL
    # =========================================================================
    def _on_insert_single_title_clicked(self):
        text = self.txt_single_title.text().strip() or "ResolveFlow Title"
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        dur = float(self.slide_title_dur.value()) / 10.0 if hasattr(self, 'slide_title_dur') and self.slide_title_dur.value() > 0 else 4.0
        self.insert_title_requested.emit(text, preset_id, dur)

    def _on_install_presets_clicked(self):
        from src.core.text_preset import FusionSettingGenerator
        FusionSettingGenerator.install_presets_to_davinci_resolve()
        self.install_presets_requested.emit()

    def _on_copy_fusion_clicked(self):
        preset_id = self.combo_text_preset.currentData() or "karaoke_pop"
        self.copy_fusion_node_requested.emit(preset_id)
