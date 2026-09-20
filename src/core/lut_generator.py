"""
Module tạo và quản lý 3D LUT (.cube) chuyên nghiệp cho ResolveFlow Assistant.
Sinh các tệp 33x33x33 3D LUTs chuẩn ngành tương thích 100% với DaVinci Resolve (Free & Studio),
Premiere Pro, Final Cut Pro và Photoshop.
"""

import os
import math
from typing import List, Dict, Any, Tuple
from pydantic import BaseModel, Field


class ColorLookPreset(BaseModel):
    """Định nghĩa metadata của một bộ phong cách màu sắc (Color Look / LUT)."""
    id: str = Field(..., description="ID định danh")
    name: str = Field(..., description="Tên hiển thị")
    category: str = Field(default="cinematic", description="Phân loại: natural, vlog, cinematic, cyber, vintage, bw")
    badge_icon: str = Field(default="🎨", description="Biểu tượng Emoji")
    description: str = Field(default="", description="Mô tả phong cách và mood điện ảnh")
    tags: List[str] = Field(default_factory=list, description="Từ khóa hashtag")
    palette_hex: List[str] = Field(default_factory=list, description="Bảng mã màu đại diện (3-4 màu hex)")
    file_name: str = Field(default="", description="Tên file .cube")


BUILTIN_COLOR_LOOKS: List[ColorLookPreset] = [
    ColorLookPreset(
        id="clean_rec709",
        name="Clean Rec.709 Natural",
        category="natural",
        badge_icon="🌿",
        description="Chuẩn màu truyền hình tự nhiên, cân bằng trắng chính xác, giữ tông da trung thực và trong trẻo nhất.",
        tags=["Natural", "Rec709", "Broadcast", "Podcast", "Interview"],
        palette_hex=["#F5F5F7", "#4A90E2", "#50E3C2", "#333333"],
        file_name="ResolveFlow_Clean_Rec709.cube"
    ),
    ColorLookPreset(
        id="warm_vlog",
        name="Warm Vlog Lifestyle",
        category="vlog",
        badge_icon="☀️",
        description="Tông màu ấm áp tươi sáng, làm mịn và hồng hào làn da (Skin Tone Glow), cực kỳ thu hút trên TikTok và Vlog đời sống.",
        tags=["Vlog", "Warm", "SkinGlow", "TikTok", "Lifestyle"],
        palette_hex=["#FFE0B2", "#FFA726", "#FB8C00", "#5D4037"],
        file_name="ResolveFlow_Warm_Vlog.cube"
    ),
    ColorLookPreset(
        id="korean_pastel",
        name="Korean Pastel Soft Dream",
        category="vlog",
        badge_icon="🌸",
        description="Phong cách Hàn Quốc nhẹ nhàng, nâng vùng tối (Lifted Shadows), giảm độ gắt, mang lại cảm giác thơ mộng như phim truyền hình K-Drama.",
        tags=["Pastel", "Korean", "Soft", "Dreamy", "Cafe", "Aesthetic"],
        palette_hex=["#F8BBD0", "#E1BEE7", "#FFF9C4", "#455A64"],
        file_name="ResolveFlow_Korean_Pastel.cube"
    ),
    ColorLookPreset(
        id="cinematic_teal_orange",
        name="Cinematic Teal & Orange",
        category="cinematic",
        badge_icon="🎬",
        description="Tone màu điện ảnh kinh điển của các bom tấn Hollywood: Hậu cảnh xanh Teal lạnh tương phản mạnh với màu da cam ấm áp nổi bật.",
        tags=["Blockbuster", "Hollywood", "TealOrange", "Action", "Contrast"],
        palette_hex=["#008080", "#FF7F50", "#1A3636", "#FFBF00"],
        file_name="ResolveFlow_Cinematic_Teal_Orange.cube"
    ),
    ColorLookPreset(
        id="cyberpunk_neon",
        name="Cyberpunk Neon Nights",
        category="cyber",
        badge_icon="⚡",
        description="Đêm neon tương lai rực rỡ: Vùng tối ánh tím huyền bí, ánh sáng nổi bật sắc xanh Neon và hồng cánh sen cá tính.",
        tags=["Cyberpunk", "Neon", "Synthwave", "Gaming", "Night", "SciFi"],
        palette_hex=["#D500F9", "#00E5FF", "#651FFF", "#050515"],
        file_name="ResolveFlow_Cyberpunk_Neon.cube"
    ),
    ColorLookPreset(
        id="moody_dark",
        name="Moody Dark Cinema",
        category="cinematic",
        badge_icon="🦇",
        description="Phong cách điện ảnh u tối, giàu cảm xúc (Fincher / Batman vibe), khử bão hòa nhẹ và tăng cường chiều sâu bóng đổ.",
        tags=["Moody", "Dark", "Dramatic", "Mystery", "Thriller", "Cinema"],
        palette_hex=["#263238", "#37474F", "#78909C", "#101416"],
        file_name="ResolveFlow_Moody_Dark.cube"
    ),
    ColorLookPreset(
        id="golden_hour",
        name="Golden Hour Sunset",
        category="vlog",
        badge_icon="🌅",
        description="Ánh hoàng hôn vàng óng ả, phủ một lớp nắng chiều mật ong lãng mạn lên cảnh vật và chủ thể.",
        tags=["Sunset", "GoldenHour", "Amber", "Romantic", "Travel", "Warmth"],
        palette_hex=["#FFB300", "#FF6F00", "#FFD54F", "#3E2723"],
        file_name="ResolveFlow_Golden_Hour.cube"
    ),
    ColorLookPreset(
        id="anime_vibrant",
        name="Anime Studio Ghibli",
        category="cyber",
        badge_icon="🎨",
        description="Màu sắc tươi tắn, sống động rực rỡ phong cách phim hoạt hình: Tăng cường sắc xanh lá cây cỏ và bầu trời trong vắt.",
        tags=["Anime", "Ghibli", "Vibrant", "Greenery", "SkyBlue", "PopColor"],
        palette_hex=["#00E676", "#29B6F6", "#FFEA00", "#1B5E20"],
        file_name="ResolveFlow_Anime_Vibrant.cube"
    ),
    ColorLookPreset(
        id="vintage_film_kodak",
        name="Vintage Kodak 2383 Print",
        category="vintage",
        badge_icon="🎞",
        description="Mô phỏng màu phim nhựa Kodak 2383 cổ điển, highlight mềm mại, sắc ấm nhẹ nhàng kèm hạt phim hoài niệm thập niên 90.",
        tags=["Kodak", "Vintage", "35mm", "FilmLook", "Retro", "Nostalgia"],
        palette_hex=["#D7CCC8", "#8D6E63", "#4E342E", "#A1887F"],
        file_name="ResolveFlow_Vintage_Kodak_2383.cube"
    ),
    ColorLookPreset(
        id="high_contrast_bw",
        name="Noir High Contrast B&W",
        category="bw",
        badge_icon="🖤",
        description="Đen trắng tương phản sắc sảo, tách bạch chi tiết ánh sáng và bóng tối đầy tính nghệ thuật và chiều sâu.",
        tags=["BlackAndWhite", "Noir", "Monochrome", "HighContrast", "Artistic"],
        palette_hex=["#FFFFFF", "#BDBDBD", "#616161", "#000000"],
        file_name="ResolveFlow_High_Contrast_BW.cube"
    )
]


class LUT3DGenerator:
    """
    Sinh các tệp 3D LUT (.cube) theo chuẩn Adobe/DaVinci 33x33x33.
    """
    @staticmethod
    def _apply_curve(val: float, s_curve_strength: float = 0.2) -> float:
        """Áp dụng đường cong tương phản S-curve mềm."""
        # Standard cubic hermite sigmoid
        val = max(0.0, min(1.0, val))
        sig = val * val * (3.0 - 2.0 * val)
        return (1.0 - s_curve_strength) * val + s_curve_strength * sig

    @classmethod
    def generate_cube_data(cls, look_id: str, size: int = 33) -> str:
        """Tạo nội dung văn bản chuẩn .cube cho một Look ID."""
        preset = next((p for p in BUILTIN_COLOR_LOOKS if p.id == look_id), BUILTIN_COLOR_LOOKS[0])
        lines = [
            f"# ResolveFlow Assistant 3D LUT",
            f"# Look Preset: {preset.name}",
            f"TITLE \"{preset.name}\"",
            f"LUT_3D_SIZE {size}",
            ""
        ]

        step = 1.0 / (size - 1)

        for b_idx in range(size):
            b_in = b_idx * step
            for g_idx in range(size):
                g_in = g_idx * step
                for r_idx in range(size):
                    r_in = r_idx * step

                    r_out, g_out, b_out = cls._transform_color(look_id, r_in, g_in, b_in)
                    # Giới hạn 0.0 đến 1.0
                    r_out = max(0.0, min(1.0, r_out))
                    g_out = max(0.0, min(1.0, g_out))
                    b_out = max(0.0, min(1.0, b_out))

                    lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

        return "\n".join(lines)

    @classmethod
    def _transform_color(cls, look_id: str, r: float, g: float, b: float) -> Tuple[float, float, float]:
        """Thuật toán biến đổi toán học màu sắc cho từng phong cách."""
        # Tính độ sáng Luminance chuẩn Rec.709
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

        if look_id == "clean_rec709":
            # Tăng nhẹ tương phản tự nhiên, bảo toàn màu gốc
            r_o = cls._apply_curve(r, 0.15)
            g_o = cls._apply_curve(g, 0.15)
            b_o = cls._apply_curve(b, 0.15)
            return r_o, g_o, b_o

        elif look_id == "warm_vlog":
            # Nâng sắc vàng cam ấm, sáng da
            r_o = cls._apply_curve(r * 1.05 + 0.02, 0.2)
            g_o = cls._apply_curve(g * 1.02 + 0.01, 0.2)
            b_o = cls._apply_curve(b * 0.93, 0.15)
            return r_o, g_o, b_o

        elif look_id == "korean_pastel":
            # Nâng shadow +0.06, giảm tương phản, tông da mịn màng
            r_o = r * 0.88 + 0.08
            g_o = g * 0.88 + 0.07
            b_o = b * 0.90 + 0.09
            # Hơi ngả hồng nhẹ
            r_o += 0.02 * lum
            return r_o, g_o, b_o

        elif look_id == "cinematic_teal_orange":
            # Teal ở Shadow (Shadow -> Blue/Cyan), Orange ở Highlight (Highlight -> Red/Yellow)
            shadow_mask = max(0.0, 1.0 - lum * 1.6)
            highlight_mask = max(0.0, lum * 1.5 - 0.5)

            r_o = r + highlight_mask * 0.15 - shadow_mask * 0.08
            g_o = g + highlight_mask * 0.05 + shadow_mask * 0.04
            b_o = b - highlight_mask * 0.12 + shadow_mask * 0.18
            return cls._apply_curve(r_o, 0.3), cls._apply_curve(g_o, 0.3), cls._apply_curve(b_o, 0.3)

        elif look_id == "cyberpunk_neon":
            # Shadow ngả tím/indigo, Highlight ngả cyan/pink, contrast gắt
            shadow_mask = max(0.0, 1.0 - lum * 1.8)
            highlight_mask = max(0.0, lum * 1.4 - 0.4)

            r_o = r + shadow_mask * 0.12 + highlight_mask * 0.10
            g_o = g * 0.90 + highlight_mask * 0.08
            b_o = b + shadow_mask * 0.25 + highlight_mask * 0.15
            return cls._apply_curve(r_o, 0.4), cls._apply_curve(g_o, 0.35), cls._apply_curve(b_o, 0.4)

        elif look_id == "moody_dark":
            # U tối, S-curve mạnh, giảm bão hòa 20%, cool shadow
            sat_factor = 0.80
            r_sat = lum + (r - lum) * sat_factor
            g_sat = lum + (g - lum) * sat_factor
            b_sat = lum + (b - lum) * sat_factor

            r_o = cls._apply_curve(r_sat * 0.95, 0.45)
            g_o = cls._apply_curve(g_sat * 0.98, 0.45)
            b_o = cls._apply_curve(b_sat * 1.05, 0.45)
            return r_o, g_o, b_o

        elif look_id == "golden_hour":
            # Ánh hoàng hôn ấm rực rỡ
            r_o = r * 1.15 + 0.04
            g_o = g * 1.05 + 0.02
            b_o = b * 0.82
            return cls._apply_curve(r_o, 0.25), cls._apply_curve(g_o, 0.25), cls._apply_curve(b_o, 0.2)

        elif look_id == "anime_vibrant":
            # Bão hòa màu cao (+25%), xanh lá và xanh trời đậm đà
            sat_factor = 1.30
            r_sat = lum + (r - lum) * 1.20
            g_sat = lum + (g - lum) * sat_factor
            b_sat = lum + (b - lum) * sat_factor
            return cls._apply_curve(r_sat, 0.2), cls._apply_curve(g_sat, 0.2), cls._apply_curve(b_sat, 0.2)

        elif look_id == "vintage_film_kodak":
            # Kodak 2383: ấm áp, black point hơi nâng, highlight êm
            r_o = r * 0.95 + 0.03
            g_o = g * 0.92 + 0.02
            b_o = b * 0.88 + 0.04
            return cls._apply_curve(r_o, 0.35), cls._apply_curve(g_o, 0.35), cls._apply_curve(b_o, 0.3)

        elif look_id == "high_contrast_bw":
            # Đen trắng tương phản cao
            bw = cls._apply_curve(lum, 0.55)
            return bw, bw, bw

        return r, g, b

    @classmethod
    def ensure_all_luts_exist(cls, target_dir: str) -> int:
        """Đảm bảo tất cả các file .cube có sẵn trong thư mục target_dir."""
        os.makedirs(target_dir, exist_ok=True)
        created_count = 0
        for look in BUILTIN_COLOR_LOOKS:
            out_file = os.path.join(target_dir, look.file_name)
            if not os.path.exists(out_file) or os.path.getsize(out_file) == 0:
                cube_content = cls.generate_cube_data(look.id, size=33)
                with open(out_file, "w", encoding="utf-8") as f:
                    f.write(cube_content)
                created_count += 1
        return created_count
