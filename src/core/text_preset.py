import os
import json
import re
import hashlib
from typing import List, Dict, Any, Optional, Literal, Tuple
from pydantic import BaseModel, Field

def hex_to_fcpxml_rgba(hex_color: str) -> str:
    """
    Chuyển đổi mã màu Hex (ví dụ: '#FFD700' hoặc '#FFD700FF') sang định dạng RGBA 'r g b a'
    chuẩn của Apple FCPXML / Text+ (từ 0.0 đến 1.0).
    """
    if not hex_color:
        return "1 1 1 1"
    
    # Nếu đã là định dạng '1 0.84 0 1'
    if " " in hex_color.strip():
        return hex_color.strip()

    c = hex_color.lstrip("#")
    if len(c) == 3:
        c = "".join([x * 2 for x in c]) + "FF"
    elif len(c) == 6:
        c = c + "FF"
    elif len(c) != 8:
        return "1 1 1 1"

    try:
        r = int(c[0:2], 16) / 255.0
        g = int(c[2:4], 16) / 255.0
        b = int(c[4:6], 16) / 255.0
        a = int(c[6:8], 16) / 255.0
        
        def fmt_val(v: float) -> str:
            if v == 0.0:
                return "0"
            if v == 1.0:
                return "1"
            return f"{v:.3f}".rstrip('0').rstrip('.')
            
        return f"{fmt_val(r)} {fmt_val(g)} {fmt_val(b)} {fmt_val(a)}"
    except Exception:
        return "1 1 1 1"

def hex_to_rgb_tuple(hex_color: str) -> Tuple[int, int, int, int]:
    """Chuyển mã màu Hex sang tuple (R, G, B, A) từ 0-255."""
    if not hex_color:
        return (255, 255, 255, 255)
    c = hex_color.lstrip("#")
    if len(c) == 3:
        c = "".join([x * 2 for x in c]) + "FF"
    elif len(c) == 6:
        c = c + "FF"
    elif len(c) != 8:
        return (255, 255, 255, 255)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16))
    except Exception:
        return (255, 255, 255, 255)


def pydantic_dump(model: BaseModel) -> Dict[str, Any]:
    """Hỗ trợ serialize Pydantic model trên cả v1 (Python 3.10) và v2 (Python 3.11+)."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    elif hasattr(model, "dict"):
        return model.dict()
    return vars(model)


class TextStylePreset(BaseModel):
    """
    Schema định nghĩa một Preset kiểu chữ và hiệu ứng phụ đề động (Text+/Fusion Text)
    tương thích hoàn toàn với DaVinci Resolve và FCPXML v1.9.
    Được thiết kế theo tiêu chuẩn Visual Technique Library (Eyecandy, Mixkit & Shotdeck).
    """
    id: str = Field(..., description="ID định danh duy nhất của preset")
    name: str = Field(..., description="Tên hiển thị người dùng")
    category: Literal["kinetic", "highlighter_paper", "glitch_cyber", "retro_film", "clean_minimal"] = Field(
        default="kinetic", description="Danh mục phân loại kỹ xảo thị giác"
    )
    badge_icon: str = Field(default="✨", description="Biểu tượng Emoji đại diện")
    tags: List[str] = Field(default_factory=list, description="Danh sách từ khóa tìm kiếm & phong cách")
    description: str = Field(default="", description="Mô tả kỹ thuật hoạt ảnh và cấu trúc visual")
    font: str = Field(default="Arial", description="Tên phông chữ")
    size: int = Field(default=48, description="Kích cỡ chữ cơ bản (pt)")
    weight: Literal["normal", "bold", "extra_bold"] = Field(default="bold", description="Độ đậm phông chữ")
    standard_color: str = Field(default="#FFFFFF", description="Màu chữ tiêu chuẩn (Hex)")
    highlight_color: str = Field(default="#FFD700", description="Màu chữ highlight khi đang đọc (Hex)")
    outline_color: str = Field(default="#000000", description="Màu viền chữ (Hex)")
    outline_width: float = Field(default=0.12, description="Độ dày viền chữ (0.0 đến 0.5)")
    animation: Literal["pop", "bounce", "typewriter", "slide", "box_highlight", "glow", "static"] = Field(
        default="pop", description="Kiểu hoạt ảnh từ / dòng"
    )
    timing_curve: Literal["ease-in-out", "spring", "linear"] = Field(
        default="spring", description="Đường cong thời gian chuyển động"
    )
    position_y_16_9: float = Field(default=0.15, description="Vị trí lề dưới tỷ lệ 16:9 (0.0 đến 1.0)")
    position_y_9_16: float = Field(default=0.35, description="Vị trí lề dưới tỷ lệ 9:16 (0.0 đến 1.0)")
    box_color: Optional[str] = Field(default=None, description="Màu khung nền hộp nếu có (Hex)")
    glow_color: Optional[str] = Field(default=None, description="Màu ánh sáng Neon tỏa ra nếu có (Hex)")
    gradient_colors: Optional[List[str]] = Field(default=None, description="Danh sách màu gradient chuyển tiếp nếu có")

    def get_fcpxml_standard_color(self) -> str:
        return hex_to_fcpxml_rgba(self.standard_color)

    def get_fcpxml_highlight_color(self) -> str:
        return hex_to_fcpxml_rgba(self.highlight_color)

    def get_fcpxml_outline_color(self) -> str:
        return hex_to_fcpxml_rgba(self.outline_color)


BUILTIN_PRESETS: List[TextStylePreset] = [
    TextStylePreset(
        id="kinetic_hormozi",
        name="Alex Hormozi Pop (Kinetic Bounce)",
        category="kinetic",
        badge_icon="💥",
        tags=["Viral", "Hormozi", "TikTok", "Shorts", "SpringPop", "HighEnergy"],
        description="Chữ nảy lò xo (Spring curve) bám từng từ khóa chính, màu vàng neon/xanh chuối viền đen dày tương phản cao cho Shorts/Reels triệu view.",
        font="Arial",
        size=54,
        weight="extra_bold",
        standard_color="#FFFFFF",
        highlight_color="#FFE600",
        outline_color="#000000",
        outline_width=0.18,
        animation="bounce",
        timing_curve="spring",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="highlighter_swipe",
        name="Highlighter Marker (Bút Dạ Quang)",
        category="highlighter_paper",
        badge_icon="🖍️",
        tags=["Marker", "Highlighter", "Documentary", "Study", "Focus", "YellowBar"],
        description="Khung màu vàng dạ quang quét sau lưng từ khóa như đánh dấu bút nhớ dòng tài liệu nghiên cứu.",
        font="Arial",
        size=48,
        weight="bold",
        standard_color="#000000",
        highlight_color="#1A1A1A",
        outline_color="#000000",
        outline_width=0.0,
        box_color="#FFE600",
        animation="box_highlight",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="paper_cutout",
        name="Paper Cutout (Xé Giấy Vintage)",
        category="highlighter_paper",
        badge_icon="📄",
        tags=["Scrapbook", "Collage", "StopMotion", "Retro", "PaperCut", "Vintage"],
        description="Phong cách nhãn dán xé giấy thủ công Stop-motion, nền giấy ngà cổ điển tạo cảm giác mộc mạc và chân thực.",
        font="Courier New",
        size=46,
        weight="bold",
        standard_color="#1A1A1A",
        highlight_color="#C62828",
        outline_color="#333333",
        outline_width=0.06,
        box_color="#FBF9F1",
        animation="pop",
        timing_curve="spring",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="rgb_glitch",
        name="RGB Split Glitch (Nhiễu Sóng Số)",
        category="glitch_cyber",
        badge_icon="⚡",
        tags=["Cyberpunk", "Glitch", "Gaming", "SciFi", "RGB", "Aberration"],
        description="Tách kênh màu quang sai (Chromatic Aberration) đỏ-xanh kèm viền phát quang điện tử sắc nét phong cách Cyberpunk.",
        font="Arial",
        size=52,
        weight="extra_bold",
        standard_color="#E0F7FA",
        highlight_color="#00E5FF",
        outline_color="#D500F9",
        outline_width=0.12,
        glow_color="#FF0055",
        animation="bounce",
        timing_curve="spring",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="neon_pulse",
        name="Neon Pulse Glow (Đèn Neon Đêm)",
        category="glitch_cyber",
        badge_icon="🟣",
        tags=["Neon", "Glow", "Synthwave", "Nightclub", "Cyber", "Purple"],
        description="Ánh sáng ống đèn Neon huỳnh quang phát sáng tỏa bóng mờ ảo đa lớp rực rỡ trong không gian tối.",
        font="Arial",
        size=50,
        weight="bold",
        standard_color="#F3E5F5",
        highlight_color="#E040FB",
        outline_color="#6A1B9A",
        outline_width=0.08,
        glow_color="#00E5FF",
        animation="glow",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="vhs_retro",
        name="90s VHS Camcorder (Thước Phim Băng)",
        category="retro_film",
        badge_icon="📺",
        tags=["VHS", "Camcorder", "90s", "Retro", "Tape", "Nostalgia", "Y2K"],
        description="Phông chữ monospaced màu vàng cam viền đen đặc trưng máy quay băng gia đình thập niên 90 kèm cảm giác hoài niệm.",
        font="Consolas",
        size=44,
        weight="bold",
        standard_color="#FFF59D",
        highlight_color="#FFEB3B",
        outline_color="#000000",
        outline_width=0.15,
        animation="typewriter",
        timing_curve="linear",
        position_y_16_9=0.12,
        position_y_9_16=0.25
    ),
    TextStylePreset(
        id="clean_minimal",
        name="Clean Minimal (Podcast & Phỏng Vấn)",
        category="clean_minimal",
        badge_icon="🧊",
        tags=["Minimal", "Interview", "Podcast", "Documentary", "Clean", "Modern"],
        description="Thiết kế tối giản thanh lịch, đường nét sạch sẽ với viền mờ tinh tế giúp khán giả tập trung 100% vào nội dung.",
        font="Arial",
        size=44,
        weight="normal",
        standard_color="#FFFFFF",
        highlight_color="#64B5F6",
        outline_color="#000000",
        outline_width=0.12,
        animation="static",
        timing_curve="linear",
        position_y_16_9=0.12,
        position_y_9_16=0.25
    ),
    TextStylePreset(
        id="karaoke_pop",
        name="Karaoke Pop (Word Highlight)",
        category="kinetic",
        badge_icon="🎤",
        tags=["Karaoke", "Subtitles", "Classic", "WordByWord", "YellowGold"],
        description="Đổi màu vàng kim rực rỡ theo từng từ đang phát âm, phong cách TV show và Karaoke chuyên nghiệp.",
        font="Arial",
        size=48,
        weight="bold",
        standard_color="#FFFFFF",
        highlight_color="#FFD700",
        outline_color="#000000",
        outline_width=0.12,
        animation="pop",
        timing_curve="spring",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="bounce_word",
        name="Bounce Word (Nhảy Chữ Spring)",
        category="kinetic",
        badge_icon="🏀",
        tags=["Bounce", "Motion", "Spring", "Energy", "Cyan"],
        description="Chữ nhảy nảy bật spring vui tươi thu hút ánh mắt người xem trên từng nhịp thoại.",
        font="Arial",
        size=52,
        weight="extra_bold",
        standard_color="#FFFFFF",
        highlight_color="#00FFCC",
        outline_color="#111111",
        outline_width=0.15,
        animation="bounce",
        timing_curve="spring",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="box_highlight",
        name="Box Highlight (Hộp Màu Đỏ Bám Từ)",
        category="highlighter_paper",
        badge_icon="📦",
        tags=["Box", "RedBox", "CapCut", "News", "HighlightBox"],
        description="Đóng hộp chữ nhật màu đỏ bám sát từ khóa đang đọc, tăng độ tương phản tuyệt đối trên nền video phức tạp.",
        font="Arial",
        size=46,
        weight="bold",
        standard_color="#FFFFFF",
        highlight_color="#FFFFFF",
        outline_color="#000000",
        outline_width=0.0,
        box_color="#E60000",
        animation="box_highlight",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="glow_neon",
        name="Glow Neon Cyan (Phát Sáng Xanh)",
        category="glitch_cyber",
        badge_icon="💡",
        tags=["Cyan", "Glow", "Tech", "Future", "Neon"],
        description="Hào quang xanh lam công nghệ tỏa bóng mềm mại hiện đại.",
        font="Arial",
        size=50,
        weight="bold",
        standard_color="#E0FFFF",
        highlight_color="#00FFFF",
        outline_color="#008B8B",
        outline_width=0.1,
        glow_color="#00FFFF",
        animation="glow",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="clean_outline",
        name="Clean Outline (Podcast Nét)",
        category="clean_minimal",
        badge_icon="🎙️",
        tags=["Podcast", "Outline", "Subtitle", "Crisp"],
        description="Chữ trắng viền đen chuẩn mực cho mọi chương trình podcast và trò chuyện chuyên sâu.",
        font="Arial",
        size=44,
        weight="normal",
        standard_color="#FFFFFF",
        highlight_color="#FFFFFF",
        outline_color="#000000",
        outline_width=0.15,
        animation="static",
        timing_curve="linear",
        position_y_16_9=0.12,
        position_y_9_16=0.25
    ),
    TextStylePreset(
        id="gradient_fill",
        name="Gradient Sunset (Chuyển Sắc Hoàng Hôn)",
        category="clean_minimal",
        badge_icon="🌅",
        tags=["Gradient", "Sunset", "Vibrant", "Creative", "Modern"],
        description="Dải màu hoàng hôn chuyển tiếp từ cam san hô sang hồng tím đầy tính nghệ thuật.",
        font="Arial",
        size=50,
        weight="extra_bold",
        standard_color="#FFFFFF",
        highlight_color="#FF5E36",
        outline_color="#222222",
        outline_width=0.1,
        gradient_colors=["#FFA07A", "#FF4500", "#FF1493"],
        animation="slide",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    ),
    TextStylePreset(
        id="slide_in",
        name="Slide-in (Trượt Mượt Mà)",
        category="kinetic",
        badge_icon="🚀",
        tags=["Slide", "Smooth", "Intro", "Fluid", "Motion"],
        description="Chữ trượt vào từ cạnh dưới êm ái tạo cảm giác thanh thoát và lôi cuốn.",
        font="Arial",
        size=48,
        weight="bold",
        standard_color="#F0F0F0",
        highlight_color="#FFEA00",
        outline_color="#000000",
        outline_width=0.12,
        animation="slide",
        timing_curve="ease-in-out",
        position_y_16_9=0.15,
        position_y_9_16=0.35
    )
]


class PresetManager:
    """
    Quản lý lưu trữ, tải và tạo mới các Preset kiểu chữ phụ đề FCPXML Text+.
    """
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "presets", "text_styles")
        self.base_dir = base_dir
        self.custom_dir = os.path.join(self.base_dir, "custom")
        self.ensure_directories()
        self.ensure_default_presets()

    def ensure_directories(self):
        os.makedirs(self.base_dir, exist_ok=True)
        os.makedirs(self.custom_dir, exist_ok=True)

    def ensure_default_presets(self):
        """Khởi tạo hoặc đồng bộ các file JSON preset mặc định."""
        for preset in BUILTIN_PRESETS:
            file_path = os.path.join(self.base_dir, f"{preset.id}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(pydantic_dump(preset), f, indent=2, ensure_ascii=False)

    def list_presets(self) -> List[TextStylePreset]:
        """Tải toàn bộ danh sách preset (cả mặc định và tùy chỉnh)."""
        presets = []
        preset_ids = set()

        # 1. Quét thư mục custom trước để ưu tiên override
        if os.path.exists(self.custom_dir):
            for fname in sorted(os.listdir(self.custom_dir)):
                if fname.endswith(".json"):
                    fpath = os.path.join(self.custom_dir, fname)
                    p = self._load_preset_file(fpath)
                    if p:
                        presets.append(p)
                        preset_ids.add(p.id)

        # 2. Quét thư mục cơ bản
        if os.path.exists(self.base_dir):
            for fname in sorted(os.listdir(self.base_dir)):
                if fname.endswith(".json"):
                    fpath = os.path.join(self.base_dir, fname)
                    p = self._load_preset_file(fpath)
                    if p and p.id not in preset_ids:
                        presets.append(p)
                        preset_ids.add(p.id)

        # 3. Đảm bảo nếu chưa có file nào thì nạp từ BUILTIN_PRESETS
        if not presets:
            return BUILTIN_PRESETS.copy()
            
        return presets

    def _load_preset_file(self, file_path: str) -> Optional[TextStylePreset]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TextStylePreset(**data)
        except Exception:
            return None

    def get_preset(self, preset_id: str) -> TextStylePreset:
        """Lấy preset theo ID, fallback về karaoke_pop nếu không tìm thấy."""
        all_presets = self.list_presets()
        for p in all_presets:
            if p.id == preset_id:
                return p
        return BUILTIN_PRESETS[0]

    def save_custom_preset(self, preset: TextStylePreset) -> str:
        """Lưu một preset tùy chỉnh vào thư mục custom."""
        self.ensure_directories()
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', preset.id).lower()
        preset.id = safe_id
        file_path = os.path.join(self.custom_dir, f"{safe_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(pydantic_dump(preset), f, indent=2, ensure_ascii=False)
        return file_path

    def delete_custom_preset(self, preset_id: str) -> bool:
        """Xóa một preset tùy chỉnh."""
        file_path = os.path.join(self.custom_dir, f"{preset_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                return True
            except Exception:
                return False
        return False


class TextPreviewRenderer:
    """
    Tạo hình ảnh hoặc danh sách khung hình xem trước (Live Preview Animation) cho Preset phụ đề
    trên khung nền video giả lập (16:9 hoặc 9:16) mà không cần mở DaVinci Resolve.
    """
    
    @classmethod
    def render_preview_frames(
        cls,
        preset: TextStylePreset,
        sample_words: Optional[List[str]] = None,
        aspect_ratio: str = "16:9",
        width: int = 640,
        height: int = 360,
        num_frames: int = 4
    ) -> List[str]:
        """
        Sinh danh sách các file ảnh PNG (đường dẫn tạm) để tạo thành một Animation Live Preview.
        Sử dụng cơ chế cache MD5 hash (dựa trên preset content và parameters) để tránh phải dùng PIL vẽ lại.
        """
        if sample_words is None:
            sample_words = ["ResolveFlow", "Assistant", "v4.1", "Studio"]

        import tempfile
        
        # Build cache key
        preset_dump = pydantic_dump(preset)
        cache_str = json.dumps(preset_dump, sort_keys=True) + f"{aspect_ratio}_{width}_{height}_{num_frames}_" + "_".join(sample_words)
        version_hash = hashlib.md5(cache_str.encode("utf-8")).hexdigest()[:10]
        
        temp_dir = tempfile.gettempdir()
        frames_paths = []
        all_exist = True
        
        for i in range(num_frames):
            frame_path = os.path.join(temp_dir, f"rf_preview_{preset.id}_{version_hash}_f{i}.png")
            frames_paths.append(frame_path)
            if not os.path.exists(frame_path):
                all_exist = False
                
        if all_exist:
            return frames_paths
            
        # Render if not cached
        for i, fpath in enumerate(frames_paths):
            cls.render_preview_to_file(
                preset=preset,
                output_image_path=fpath,
                sample_words=sample_words,
                active_index=i % len(sample_words) if sample_words else i,
                aspect_ratio=aspect_ratio,
                width=width,
                height=height
            )
            
        return frames_paths

    @staticmethod
    def render_preview_to_file(
        preset: TextStylePreset,
        output_image_path: str,
        sample_words: Optional[List[str]] = None,
        active_index: int = 1,
        aspect_ratio: str = "16:9",
        width: int = 640,
        height: int = 360
    ) -> str:
        """
        Sinh file ảnh PNG xem trước kiểu dáng phụ đề Text+.
        """
        if sample_words is None:
            sample_words = ["ResolveFlow", "Assistant", "v4.1", "Studio"]

        if aspect_ratio == "9:16":
            width = 360
            height = 640

        from PIL import Image, ImageDraw, ImageFont

        # Tạo background video gradient tối mô phỏng timeline DaVinci
        img = Image.new("RGBA", (width, height), (18, 18, 24, 255))
        draw = ImageDraw.Draw(img)

        # Vẽ một khung mô phỏng video mờ nhẹ
        draw.rectangle([10, 10, width - 10, height - 10], outline=(45, 45, 60, 255), width=2)
        
        # Thước đo tỷ lệ
        pos_y_ratio = preset.position_y_9_16 if aspect_ratio == "9:16" else preset.position_y_16_9
        target_center_y = int(height * (1.0 - pos_y_ratio))

        # Cố gắng load font từ hệ thống hoặc dùng default
        font_size = int(preset.size * (height / 720.0))
        if font_size < 16:
            font_size = 16

        font_obj = None
        font_candidates = [
            f"C:/Windows/Fonts/{preset.font.lower()}.ttf",
            f"C:/Windows/Fonts/{preset.font}.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf"
        ]
        for fc in font_candidates:
            if os.path.exists(fc):
                try:
                    font_obj = ImageFont.truetype(fc, font_size)
                    break
                except Exception:
                    continue

        if font_obj is None:
            font_obj = ImageFont.load_default()

        # Tính toán độ rộng toàn câu
        space_width = font_size // 2
        word_sizes = []
        for w in sample_words:
            bbox = draw.textbbox((0, 0), w, font=font_obj)
            w_width = bbox[2] - bbox[0]
            w_height = bbox[3] - bbox[1]
            word_sizes.append((w_width, w_height))

        total_text_width = sum(w[0] for w in word_sizes) + space_width * (len(sample_words) - 1)
        start_x = (width - total_text_width) // 2

        cur_x = start_x
        for idx, word in enumerate(sample_words):
            w_w, w_h = word_sizes[idx]
            is_active = (idx == active_index)

            # Màu sắc
            if is_active:
                text_col = hex_to_rgb_tuple(preset.highlight_color)
            else:
                text_col = hex_to_rgb_tuple(preset.standard_color)

            outline_col = hex_to_rgb_tuple(preset.outline_color)
            out_w = max(1, int(preset.outline_width * font_size * 0.4)) if preset.outline_width > 0 else 0

            # Vị trí Y cho animation (Bounce / Pop)
            w_y = target_center_y - (w_h // 2)
            if is_active and preset.animation in ["pop", "bounce"]:
                w_y -= int(font_size * 0.15) # Nảy lên nhẹ

            # Nếu có Box Highlight
            if preset.box_color and is_active:
                box_rgba = hex_to_rgb_tuple(preset.box_color)
                pad_x = 8
                pad_y = 4
                draw.rounded_rectangle(
                    [cur_x - pad_x, w_y - pad_y, cur_x + w_w + pad_x, w_y + w_h + pad_y],
                    radius=6,
                    fill=box_rgba
                )

            # Nếu có Glow effect
            if preset.glow_color and is_active:
                glow_rgba = hex_to_rgb_tuple(preset.glow_color)
                # Vẽ bóng tỏa viền
                for g_off in range(1, 4):
                    draw.text((cur_x - g_off, w_y), word, font=font_obj, fill=(glow_rgba[0], glow_rgba[1], glow_rgba[2], 80 // g_off))
                    draw.text((cur_x + g_off, w_y), word, font=font_obj, fill=(glow_rgba[0], glow_rgba[1], glow_rgba[2], 80 // g_off))
                    draw.text((cur_x, w_y - g_off), word, font=font_obj, fill=(glow_rgba[0], glow_rgba[1], glow_rgba[2], 80 // g_off))
                    draw.text((cur_x, w_y + g_off), word, font=font_obj, fill=(glow_rgba[0], glow_rgba[1], glow_rgba[2], 80 // g_off))

            # Vẽ viền Outline
            if out_w > 0:
                for ox in range(-out_w, out_w + 1):
                    for oy in range(-out_w, out_w + 1):
                        if ox != 0 or oy != 0:
                            draw.text((cur_x + ox, w_y + oy), word, font=font_obj, fill=outline_col)

            # Vẽ chữ chính
            draw.text((cur_x, w_y), word, font=font_obj, fill=text_col)

            cur_x += w_w + space_width

        # Watermark / Indicator
        draw.text((15, 15), f"ResolveFlow Preview: [{preset.name}] ({aspect_ratio})", fill=(160, 160, 180, 255))
        
        parent_dir = os.path.dirname(output_image_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
            
        img.save(output_image_path, "PNG")
        return output_image_path


class FusionSettingGenerator:
    """
    Tạo và xuất các tệp mẫu Fusion Text+ Macro (.setting) tương thích hoàn toàn
    với DaVinci Resolve Free và Studio.
    Cho phép kéo thả trực tiếp (Drag & Drop) hoặc cài sẵn vào thư mục Titles của Resolve.
    """
    @staticmethod
    def generate_setting_content(preset: TextStylePreset, sample_text: str = "ResolveFlow Title") -> str:
        """Sinh chuỗi cú pháp Lua (.setting) của Fusion Text+ tool."""
        r_hl, g_hl, b_hl, _ = hex_to_rgb_tuple(preset.highlight_color or preset.standard_color)
        r_out, g_out, b_out, _ = hex_to_rgb_tuple(preset.outline_color)
        
        fusion_size = round(max(0.04, min(0.25, preset.size / 600.0)), 4)
        out_thickness = round(max(0.01, min(0.15, preset.outline_width * 0.3)), 3)
        
        outline_block = f"""
                Enabled2 = Input {{ Value = 1, }},
                ElementShape2 = Input {{ Value = 1, }}, -- Outline
                Red2 = Input {{ Value = {r_out / 255.0:.3f}, }},
                Green2 = Input {{ Value = {g_out / 255.0:.3f}, }},
                Blue2 = Input {{ Value = {b_out / 255.0:.3f}, }},
                Thickness2 = Input {{ Value = {out_thickness}, }},
                JoinStyle2 = Input {{ Value = 2, }}, -- Round""" if preset.outline_width > 0 else ""

        extra_shading = ""
        if preset.glow_color:
            r_g, g_g, b_g, _ = hex_to_rgb_tuple(preset.glow_color)
            extra_shading += f"""
                Enabled3 = Input {{ Value = 1, }},
                ElementShape3 = Input {{ Value = 3, }}, -- Glow
                Red3 = Input {{ Value = {r_g / 255.0:.3f}, }},
                Green3 = Input {{ Value = {g_g / 255.0:.3f}, }},
                Blue3 = Input {{ Value = {b_g / 255.0:.3f}, }},
                Softness3 = Input {{ Value = 10, }},"""
        elif preset.box_color:
            r_bx, g_bx, b_bx, _ = hex_to_rgb_tuple(preset.box_color)
            extra_shading += f"""
                Enabled4 = Input {{ Value = 1, }},
                ElementShape4 = Input {{ Value = 2, }}, -- Bounding Box
                Red4 = Input {{ Value = {r_bx / 255.0:.3f}, }},
                Green4 = Input {{ Value = {g_bx / 255.0:.3f}, }},
                Blue4 = Input {{ Value = {b_bx / 255.0:.3f}, }},"""

        clean_tool_name = re.sub(r'[^a-zA-Z0-9_]', '', f"RF_{preset.id.title()}")

        setting_str = f"""{{
    Tools = ordered() {{
        {clean_tool_name} = TextPlus {{
            Inputs = {{
                Width = Input {{ Value = 1920, }},
                Height = Input {{ Value = 1080, }},
                UseFrameFormatSettings = Input {{ Value = 1, }},
                Font = Input {{ Value = "{preset.font}", }},
                Style = Input {{ Value = "{preset.weight.title() if preset.weight != 'normal' else 'Regular'}", }},
                Size = Input {{ Value = {fusion_size}, }},
                VerticalTopCenterBottom = Input {{ Value = 1, }},
                HorizontalLeftCenterRight = Input {{ Value = 0, }},
                Center = Input {{ Value = {{ 0.5, {preset.position_y_16_9:.2f} }}, }},
                StyledText = Input {{ Value = "{sample_text}", }},
                Red1 = Input {{ Value = {r_hl / 255.0:.3f}, }},
                Green1 = Input {{ Value = {g_hl / 255.0:.3f}, }},
                Blue1 = Input {{ Value = {b_hl / 255.0:.3f}, }},{outline_block}{extra_shading}
            }},
            ViewInfo = OperatorInfo {{ Pos = {{ 220, 36.3 }} }},
        }}
    }}
}}"""
        return setting_str

    @staticmethod
    def export_setting_file(preset: TextStylePreset, output_path: str, sample_text: str = "ResolveFlow Title") -> str:
        """Xuất preset ra file .setting."""
        content = FusionSettingGenerator.generate_setting_content(preset, sample_text)
        parent_dir = os.path.dirname(output_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    @staticmethod
    def get_davinci_resolve_titles_dir() -> str:
        """Lấy đường dẫn thư mục Fusion Titles Template của DaVinci Resolve trên Windows."""
        app_data = os.environ.get("APPDATA", "")
        if app_data:
            return os.path.join(app_data, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Templates", "Edit", "Titles", "ChunDVC")
        return os.path.join(os.path.expanduser("~"), "ChunDVC_Titles")

    @classmethod
    def install_presets_to_davinci_resolve(cls, presets: Optional[List[TextStylePreset]] = None) -> Tuple[int, str]:
        """Tự động cài đặt danh sách Presets vào thư mục Effects Library của DaVinci Resolve."""
        if presets is None:
            presets = BUILTIN_PRESETS
        
        target_dir = cls.get_davinci_resolve_titles_dir()
        os.makedirs(target_dir, exist_ok=True)
        
        installed_count = 0
        for p in presets:
            clean_name = re.sub(r'[^\w\s-]', '', p.name).strip().replace(' ', '_')
            filename = f"ChunDVC_{clean_name}.setting"
            dest_path = os.path.join(target_dir, filename)
            cls.export_setting_file(p, dest_path, sample_text=f"{p.name}")
            installed_count += 1
            
        return installed_count, target_dir

