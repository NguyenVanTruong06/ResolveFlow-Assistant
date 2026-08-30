import os
import json
import re
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


class TextStylePreset(BaseModel):
    """
    Schema định nghĩa một Preset kiểu chữ và hiệu ứng phụ đề động (Text+/Fusion Text)
    tương thích hoàn toàn với DaVinci Resolve và FCPXML v1.9.
    """
    id: str = Field(..., description="ID định danh duy nhất của preset")
    name: str = Field(..., description="Tên hiển thị người dùng")
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
        id="karaoke_pop",
        name="Karaoke Pop (Word Highlight)",
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
        name="Bounce Word (Nhảy chữ Spring)",
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
        name="Box Highlight (Hộp màu bám từ)",
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
        name="Glow / Neon (Chữ phát sáng hiện đại)",
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
        name="Clean Outline (Podcast / Phỏng vấn nét)",
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
        name="Gradient Fill (Chuyển sắc phong cách)",
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
        name="Slide-in (Trượt mượt mà từng từ)",
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
        """Khởi tạo các file JSON preset mặc định nếu chưa tồn tại."""
        for preset in BUILTIN_PRESETS:
            file_path = os.path.join(self.base_dir, f"{preset.id}.json")
            if not os.path.exists(file_path):
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(preset.model_dump(), f, indent=2, ensure_ascii=False)

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
            json.dump(preset.model_dump(), f, indent=2, ensure_ascii=False)
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
    Tạo hình ảnh hoặc khung xem trước nhanh (Quick Preview) cho Preset phụ đề
    trên khung nền video giả lập (16:9 hoặc 9:16) mà không cần mở DaVinci Resolve.
    """
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
