"""
src/ui/lux/library/text_preview_generator.py
DaVinci-Faithful Fusion Text+ Preview Engine & Scene Spec Extractor.

Renders TextStylePreset, StudioAsset, and Fusion .setting/.drfx templates using the exact
DaVinci Resolve Fusion TextPlus 4-Shading-Element pipeline:
  - Element 1: Text Fill / Multi-stop Gradient / Active-word Kinetic Highlight
  - Element 2: Round-Join Outline Stroke (Red2/Green2/Blue2 + Thickness2)
  - Element 3: Multi-stage SoftGlow / Drop Shadow (Red3/Green3/Blue3 + Gain/XGlowSize)
  - Element 4: Background Box / Tilted Highlighter Marker Swipe / Podcast Pill / Torn Paper Cutout
  - Node FX: RGB Split Chromatic Aberration Glitch, Typewriter UTF-8 Cursor, Spring/Ease/Linear curves
"""
from __future__ import annotations

import math
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

# Cache for rendered static thumbnails and parsed .setting files
_PRESET_PIXMAP_CACHE: Dict[str, QPixmap] = {}
_SETTING_PARSE_CACHE: Dict[Tuple[str, float], Dict[str, Any]] = {}

# Showcase sample phrases when user has not entered custom text
_PRESET_SHOWCASE_TEXT: Dict[str, str] = {
    "kinetic_hormozi": "TIỀN ĐẾN TỪ ĐÂU?",
    "spring_pop": "TIỀN ĐẾN TỪ ĐÂU?",
    "karaoke_pop": "HÁT CÙNG NHAU",
    "bounce_word": "NHẢY CHỮ SPRING",
    "paper_cutout": "XÉ GIẤY VINTAGE",
    "rgb_glitch": "CYBER GLITCH",
    "neon_pulse": "ĐÈN NEON ĐÊM",
    "glow_neon": "NEON GLOW CYAN",
    "vhs_retro": "PLAY ▶ 1998",
    "highlighter_swipe": "BÚT DẠ QUANG",
    "highlighter": "BÚT DẠ QUANG",
    "box_highlight": "TIN NÓNG 24H",
    "mrbeast_impact_yellow": "THỬ THÁCH 1 TRIỆU $",
    "apple_keynote_clean": "Think Different.",
    "true_crime_documentary": "HỒ SƠ VỤ ÁN #04",
    "comic_manga_burst": "BÙNG NỔ SỨC MẠNH!",
    "luxury_gold_serif": "THE ROYAL COLLECTION",
    "minimal_podcast_pill": "Tập 12: Tự Do Tài Chính",
    "schannel_vlog_nhanh": "ẢO THẬT ĐẤY!",
    "khoai_lang_thang_chua_lanh": "Chiều Quê Bình Yên",
    "phim_truyen_hinh_vtv_retro": "PHIM TRUYỀN HÌNH VTV",
    "giat_gan_showbiz_tiktok": "CHẤN ĐỘNG SHOWBIZ!",
    "clean_minimal": "PODCAST STUDIO",
    "clean_outline": "PHỎNG VẤN ĐỘC QUYỀN",
    "gradient_fill": "HOÀNG HÔN RỰC RỠ",
    "slide_in": "CHUYỂN ĐỘNG MƯỢT",
    "typewriter": "HỒ SƠ MẬT 01:24",
    "lower_third": "NGUYỄN VĂN A • ĐẠO DIỄN",
    "quote_box": "Sáng tạo không giới hạn",
}


def _rgb_floats_to_hex(r: float, g: float, b: float) -> str:
    ri = max(0, min(255, int(round(float(r) * 255.0))))
    gi = max(0, min(255, int(round(float(g) * 255.0))))
    bi = max(0, min(255, int(round(float(b) * 255.0))))
    return f"#{ri:02X}{gi:02X}{bi:02X}"


def _safe_qcolor(val: Any, fallback: str = "#FFFFFF") -> QColor:
    if isinstance(val, QColor):
        return val if val.isValid() else QColor(fallback)
    s = str(val or "").strip()
    if not s:
        return QColor(fallback)
    c = QColor(s)
    return c if c.isValid() else QColor(fallback)


def _hash01(seed: float, step: float = 1.0) -> float:
    """Deterministic pseudo-random 0..1 matching Fusion's hash01 expression."""
    val = math.sin(math.floor(step) * 12.9898 + seed) * 43758.5453
    return val - math.floor(val)


def _lookup_builtin_preset(preset_id: str) -> Any:
    """Finds a matching TextStylePreset by ID from BUILTIN_PRESETS or PresetManager."""
    if not preset_id:
        return None
    pid_low = preset_id.strip().lower()
    try:
        from src.core.text_preset import BUILTIN_PRESETS, PresetManager
        for p in BUILTIN_PRESETS:
            if p.id.lower() == pid_low:
                return p
        for p in BUILTIN_PRESETS:
            if p.id.lower() in pid_low or pid_low in p.id.lower():
                return p
        mgr = PresetManager()
        for p in mgr.list_presets():
            if p.id.lower() == pid_low:
                return p
    except Exception:
        pass
    return None


def parse_fusion_setting_text_params(file_path: str) -> Dict[str, Any]:
    """
    Safely parses a DaVinci Resolve Fusion `.setting` file (or `.drfx` bundle containing a `.setting`)
    up to 512KB and extracts TextPlus typography, colors, shading elements, and node graph motion.
    """
    if not file_path or not isinstance(file_path, str):
        return {}
    fp = file_path.strip()
    if not fp or not os.path.isfile(fp):
        return {}

    ext = os.path.splitext(fp)[1].lower()
    if ext not in (".setting", ".drfx", ".comp"):
        return {}

    try:
        mtime = os.path.getmtime(fp)
    except OSError:
        mtime = 0.0

    cache_key = (fp, mtime)
    if cache_key in _SETTING_PARSE_CACHE:
        return dict(_SETTING_PARSE_CACHE[cache_key])

    raw_text = ""
    try:
        if ext == ".drfx" and zipfile.is_zipfile(fp):
            with zipfile.ZipFile(fp, "r") as zf:
                setting_names = [n for n in zf.namelist() if n.lower().endswith(".setting")]
                if setting_names:
                    with zf.open(setting_names[0], "r") as sf:
                        raw_text = sf.read(524288).decode("utf-8", errors="replace")
        else:
            with open(fp, "rb") as f:
                raw_text = f.read(524288).decode("utf-8", errors="replace")
    except Exception:
        return {}

    if not raw_text:
        return {}

    out: Dict[str, Any] = {}

    def _find_str_input(key: str) -> Optional[str]:
        m = re.search(rf'\b{key}\s*=\s*Input\s*\{{\s*Value\s*=\s*"([^"\r\n]*)"', raw_text)
        return m.group(1).strip() if m else None

    def _find_num_input(key: str) -> Optional[float]:
        m = re.search(rf'\b{key}\s*=\s*Input\s*\{{\s*Value\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', raw_text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    styled_text = _find_str_input("StyledText") or _find_str_input("Content")
    if styled_text:
        out["text"] = styled_text

    font = _find_str_input("Font")
    if font:
        out["font_family"] = font

    style = _find_str_input("Style")
    if style:
        s_low = style.lower()
        if "black" in s_low or "extra" in s_low or "heavy" in s_low:
            out["weight"] = "extra_bold"
        elif "regular" in s_low or "normal" in s_low or "light" in s_low:
            out["weight"] = "normal"
        else:
            out["weight"] = "bold"

    sz = _find_num_input("Size")
    if sz is not None and 0.01 <= sz <= 0.5:
        out["font_size"] = max(16, min(120, int(round(sz * 600.0))))

    r1, g1, b1 = _find_num_input("Red1"), _find_num_input("Green1"), _find_num_input("Blue1")
    if r1 is not None and g1 is not None and b1 is not None:
        out["standard_color"] = _rgb_floats_to_hex(r1, g1, b1)

    r2, g2, b2 = _find_num_input("Red2"), _find_num_input("Green2"), _find_num_input("Blue2")
    if r2 is not None and g2 is not None and b2 is not None:
        out["outline_color"] = _rgb_floats_to_hex(r2, g2, b2)

    thick2 = _find_num_input("Thickness2")
    en2 = _find_num_input("Enabled2")
    if en2 is not None and en2 == 0:
        out["outline_width"] = 0.0
    elif thick2 is not None:
        out["outline_width"] = round(max(0.0, min(0.5, thick2 / 0.35)), 3)

    r3, g3, b3 = _find_num_input("Red3"), _find_num_input("Green3"), _find_num_input("Blue3")
    if r3 is not None and g3 is not None and b3 is not None:
        out["glow_color"] = _rgb_floats_to_hex(r3, g3, b3)

    r4, g4, b4 = _find_num_input("Red4"), _find_num_input("Green4"), _find_num_input("Blue4")
    if r4 is not None and g4 is not None and b4 is not None:
        out["box_color"] = _rgb_floats_to_hex(r4, g4, b4)

    # Detect node types in macro
    if "FastNoise" in raw_text or "Displace" in raw_text or "PaperShape" in raw_text:
        out["animation"] = "paper"
    elif "SoftGlow" in raw_text:
        out["animation"] = "glow"
    elif "TextR" in raw_text and "TextB" in raw_text:
        out["animation"] = "glitch"
    elif "SwipeReveal" in raw_text or "RectangleMask" in raw_text:
        out["animation"] = "box_highlight"
    elif "FramesPerChar" in raw_text or "BlinkFrames" in raw_text:
        out["animation"] = "typewriter"
    elif "PopSize" in raw_text or "Transform" in raw_text:
        out["animation"] = "pop"

    _SETTING_PARSE_CACHE[cache_key] = dict(out)
    return out


def _normalize_animation(raw_anim: str, pid: str) -> str:
    a = (raw_anim or "").strip().lower()
    p = (pid or "").strip().lower()

    # Map Vietnamese / UI labels to canonical animation modes
    vn_map = {
        "gõ chữ": "typewriter",
        "go chu": "typewriter",
        "trượt": "slide",
        "truot": "slide",
        "tĩnh": "static",
        "tinh": "static",
        "khung nền": "box_highlight",
        "box": "box_highlight",
        "highlighter": "box_highlight",
        "phát sáng": "glow",
        "neon_pulse": "glow",
        "rgb_glitch": "glitch",
        "paper_cutout": "paper",
        "spring_pop": "pop",
        "fade": "fade_up",
    }
    if a in vn_map:
        a = vn_map[a]

    # If preset ID strongly specifies paper or glitch and user hasn't forced another specific animation
    if not raw_anim:
        if "paper" in p or "unfold" in p:
            return "paper"
        if "glitch" in p:
            return "glitch"
        if "neon" in p or "glow" in p:
            return "glow"
        if "highlighter" in p or "box" in p or "pill" in p:
            return "box_highlight"
        if "vhs" in p or "crime" in p or "typewriter" in p:
            return "typewriter"

    if a in ("pop", "bounce"):
        if "paper" in p or "unfold" in p:
            return "paper"
        if "glitch" in p:
            return "glitch"
        return a

    if a in ("typewriter", "slide", "box_highlight", "glow", "glitch", "paper", "fade_up", "static"):
        return a

    return "pop"


def _normalize_timing_curve(raw_curve: str) -> str:
    c = (raw_curve or "").strip().lower()
    if "ease" in c:
        return "ease-in-out"
    if "linear" in c or "tuyến tính" in c:
        return "linear"
    return "spring"


def _default_showcase_text(pid: str, name: str, setting_text: Optional[str] = None) -> str:
    if setting_text and setting_text.strip() and setting_text.strip() != "ResolveFlow Title":
        return setting_text.strip()

    p = (pid or "").strip().lower()
    if p in _PRESET_SHOWCASE_TEXT:
        return _PRESET_SHOWCASE_TEXT[p]

    for k, sample in _PRESET_SHOWCASE_TEXT.items():
        if k in p or p in k:
            return sample

    if "hormozi" in p:
        return "TIỀN ĐẾN TỪ ĐÂU?"
    if "karaoke" in p:
        return "HÁT CÙNG NHAU"
    if "paper" in p or "unfold" in p:
        return "XÉ GIẤY VINTAGE"
    if "glitch" in p:
        return "CYBER GLITCH"
    if "neon" in p or "glow" in p:
        return "ĐÈN NEON ĐÊM"
    if "vhs" in p:
        return "PLAY ▶ 1998"
    if "highlighter" in p:
        return "BÚT DẠ QUANG"
    if "box" in p:
        return "TIN NÓNG 24H"
    if "mrbeast" in p:
        return "THỬ THÁCH 1 TRIỆU $"
    if "apple" in p or "keynote" in p:
        return "Think Different."
    if "crime" in p:
        return "HỒ SƠ VỤ ÁN #04"
    if "comic" in p or "manga" in p:
        return "BÙNG NỔ SỨC MẠNH!"
    if "gold" in p or "luxury" in p:
        return "THE ROYAL COLLECTION"
    if "podcast" in p or "pill" in p:
        return "Tập 12: Tự Do Tài Chính"
    if "schannel" in p:
        return "ẢO THẬT ĐẤY!"
    if "khoai_lang" in p:
        return "Chiều Quê Bình Yên"
    if "vtv" in p:
        return "PHIM TRUYỀN HÌNH VTV"
    if "showbiz" in p or "drama" in p:
        return "CHẤN ĐỘNG SHOWBIZ!"

    clean_name = (name or "").split("(")[0].strip()
    return clean_name if clean_name else "RESOLVEFLOW"


def extract_davinci_text_spec(
    asset: Any,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Normalizes any TextStylePreset, StudioAsset, dict, or preset_id string plus optional
    live Inspector overrides into a complete DaVinci Resolve Fusion Text+ Scene Spec dict.
    """
    ovr = dict(overrides or {})

    # If asset is already an extracted spec dict and no overrides are changing it
    if isinstance(asset, dict) and asset.get("_is_davinci_text_spec") and not ovr:
        return dict(asset)

    preset_obj = None
    asset_dict: Dict[str, Any] = {}
    pid = ""
    name = ""
    file_path = ""

    if isinstance(asset, str):
        pid = asset.strip()
        name = ovr.get("name") or pid
        preset_obj = _lookup_builtin_preset(pid)
    elif isinstance(asset, dict):
        asset_dict = dict(asset)
        pid = str(asset_dict.get("id") or "")
        name = str(asset_dict.get("name") or pid)
        file_path = str(asset_dict.get("file_path") or asset_dict.get("path") or "")
        raw = asset_dict.get("native_preset") or asset_dict.get("_raw_obj")
        if raw is not None and hasattr(raw, "standard_color"):
            preset_obj = raw
        elif "standard_color" not in asset_dict and pid:
            preset_obj = _lookup_builtin_preset(pid)
    elif asset is not None:
        pid = str(getattr(asset, "id", "") or "")
        name = str(getattr(asset, "name", "") or pid)
        file_path = str(getattr(asset, "file_path", "") or getattr(asset, "path", "") or "")
        native = getattr(asset, "native_preset", None) or getattr(asset, "_raw_obj", None)
        if native is not None and hasattr(native, "standard_color"):
            preset_obj = native
        elif hasattr(asset, "standard_color"):
            preset_obj = asset
        elif pid:
            preset_obj = _lookup_builtin_preset(pid)

    # Parse .setting / .drfx file if available
    setting_params: Dict[str, Any] = {}
    if file_path and file_path.lower().endswith((".setting", ".drfx", ".comp")):
        setting_params = parse_fusion_setting_text_params(file_path)

    def _pick(key: str, alt_keys: Tuple[str, ...] = (), default: Any = None) -> Any:
        # 1. Overrides take highest priority
        if key in ovr and ovr[key] is not None and ovr[key] != "":
            return ovr[key]
        for ak in alt_keys:
            if ak in ovr and ovr[ak] is not None and ovr[ak] != "":
                return ovr[ak]
        # 2. Direct asset_dict fields
        if key in asset_dict and asset_dict[key] is not None and asset_dict[key] != "":
            return asset_dict[key]
        for ak in alt_keys:
            if ak in asset_dict and asset_dict[ak] is not None and asset_dict[ak] != "":
                return asset_dict[ak]
        # 3. Unwrapped TextStylePreset object
        if preset_obj is not None:
            val = getattr(preset_obj, key, None)
            if val is not None and val != "":
                return val
            for ak in alt_keys:
                val_alt = getattr(preset_obj, ak, None)
                if val_alt is not None and val_alt != "":
                    return val_alt
        # 4. Parsed .setting parameters
        if key in setting_params and setting_params[key] is not None:
            return setting_params[key]
        for ak in alt_keys:
            if ak in setting_params and setting_params[ak] is not None:
                return setting_params[ak]
        return default

    raw_text_ovr = ovr.get("text") if "text" in ovr else ovr.get("custom_text")
    if raw_text_ovr is not None and str(raw_text_ovr).strip():
        display_text = str(raw_text_ovr).strip()
    else:
        display_text = _default_showcase_text(pid, name, setting_params.get("text"))

    font_family = str(_pick("font_family", ("font",), "Arial") or "Arial")
    try:
        font_size = int(float(_pick("font_size", ("size",), 48)))
    except Exception:
        font_size = 48
    font_size = max(12, min(140, font_size))

    raw_weight = str(_pick("weight", ("style",), "bold") or "bold").lower()
    if raw_weight in ("extra_bold", "extrabold", "black", "heavy"):
        weight = "extra_bold"
    elif raw_weight in ("normal", "regular", "light"):
        weight = "normal"
    else:
        weight = "bold"

    standard_color = str(_pick("standard_color", ("color",), "#FFFFFF") or "#FFFFFF")
    highlight_color = str(_pick("highlight_color", ("accent",), "#FFD700") or "#FFD700")
    stroke_color = str(_pick("stroke_color", ("outline_color",), "#000000") or "#000000")

    raw_sw = _pick("stroke_width", ("outline_width",), 0.12)
    try:
        stroke_width = max(0.0, min(0.5, float(raw_sw)))
    except Exception:
        stroke_width = 0.12

    raw_anim = _pick("animation", ("motion",), "")
    animation = _normalize_animation(str(raw_anim or ""), pid)

    raw_curve = _pick("timing_curve", ("curve",), "spring")
    timing_curve = _normalize_timing_curve(str(raw_curve or "spring"))

    box_color = _pick("box_color", (), None)
    glow_color = _pick("glow_color", (), None)

    # Support shared `box_glow_hex` override from Studio Inspector
    box_glow_ovr = ovr.get("box_glow_hex")
    if box_glow_ovr:
        if animation in ("box_highlight", "paper") or box_color:
            box_color = str(box_glow_ovr)
        elif animation == "glow" or glow_color:
            glow_color = str(box_glow_ovr)
        else:
            box_color = str(box_glow_ovr)
            glow_color = str(box_glow_ovr)

    gradient_colors = _pick("gradient_colors", (), None)
    if isinstance(gradient_colors, (list, tuple)) and len(gradient_colors) >= 2:
        gradient_colors = [str(c) for c in gradient_colors]
    else:
        gradient_colors = None

    try:
        pos_16_9 = max(0.08, min(0.85, float(_pick("position_y_16_9", (), 0.15))))
    except Exception:
        pos_16_9 = 0.15

    try:
        pos_9_16 = max(0.10, min(0.85, float(_pick("position_y_9_16", (), 0.35))))
    except Exception:
        pos_9_16 = 0.35

    return {
        "_is_davinci_text_spec": True,
        "id": pid or "default_title",
        "name": name or pid or "Default Title",
        "text": display_text,
        "font_family": font_family,
        "font_size": font_size,
        "fusion_size": round(font_size / 600.0, 4),
        "weight": weight,
        "standard_color": standard_color,
        "highlight_color": highlight_color,
        "outline_color": stroke_color,
        "stroke_color": stroke_color,
        "outline_width": stroke_width,
        "stroke_width": stroke_width,
        "box_color": str(box_color) if box_color else None,
        "glow_color": str(glow_color) if glow_color else None,
        "gradient_colors": gradient_colors,
        "animation": animation,
        "timing_curve": timing_curve,
        "position_y_16_9": pos_16_9,
        "position_y_9_16": pos_9_16,
        "file_path": file_path,
    }


def _eval_motion_envelope(progress: float, curve: str, animation: str) -> Tuple[float, float, float, float]:
    """
    Evaluates DaVinci Fusion spline/expression keyframe envelope at normalized progress (0.0..1.0).
    Returns (scale, angle_deg, y_offset_ratio, opacity).
    """
    p = max(0.0, min(1.0, float(progress)))

    # Entrance phase occupies first 38% of the preview cycle; remainder is active hold + word cycle
    t_in = min(1.0, p / 0.38) if p < 0.38 else 1.0

    if curve == "spring":
        # Matches Fusion Spline("PopSize", [(0, 0.0), (6, 1.25), (10, 0.95), (14, 1.0)])
        if t_in < 0.45:
            u = t_in / 0.45
            scale = 1.25 * (1.0 - (1.0 - u) ** 3)
            angle = -8.0 + 11.0 * u
        elif t_in < 0.75:
            u = (t_in - 0.45) / 0.30
            scale = 1.25 + (0.95 - 1.25) * u
            angle = 3.0 + (-1.0 - 3.0) * u
        else:
            u = (t_in - 0.75) / 0.25
            scale = 0.95 + (1.0 - 0.95) * u
            angle = -1.0 + (0.0 - (-1.0)) * u
    elif curve == "ease-in-out":
        # Cubic Hermite smoothstep
        s = t_in * t_in * (3.0 - 2.0 * t_in)
        scale = 0.82 + 0.18 * s
        angle = 0.0
    else:
        # Linear
        s = t_in
        scale = 0.88 + 0.12 * s
        angle = 0.0

    y_off = 0.0
    opacity = 1.0

    if animation in ("slide", "fade_up"):
        eased = 1.0 - (1.0 - t_in) ** 3 if curve != "linear" else t_in
        y_off = (1.0 - eased) * 0.16
        opacity = max(0.15, eased)
        scale = 1.0
        angle = 0.0
    elif animation == "static":
        scale = 1.0
        angle = 0.0
        y_off = 0.0
        opacity = 1.0

    return scale, angle, y_off, opacity


def _build_qfont(font_family: str, pixel_size: float, weight: str) -> QFont:
    resolved_family = font_family
    try:
        from src.utils.path_utils import resolve_system_font
        resolved_family = resolve_system_font(font_family)
    except Exception:
        pass

    f = QFont(resolved_family)
    f.setPixelSize(max(9, int(round(pixel_size))))
    if weight == "extra_bold":
        f.setWeight(QFont.Weight.Black)
        f.setBold(True)
    elif weight == "normal":
        f.setWeight(QFont.Weight.Medium)
    else:
        f.setWeight(QFont.Weight.Bold)
        f.setBold(True)
    return f


def _split_into_lines(words: List[str], fm: QFontMetrics, max_width: float) -> List[List[str]]:
    """Splits words into 1 or 2 balanced lines so text stays inside the Title Safe area."""
    if not words:
        return [["RESOLVEFLOW"]]
    if len(words) == 1:
        return [words]

    full_str = " ".join(words)
    if fm.horizontalAdvance(full_str) <= max_width or len(words) <= 2:
        if fm.horizontalAdvance(full_str) <= max_width * 1.12:
            return [words]

    mid = max(1, len(words) // 2)
    return [words[:mid], words[mid:]]


def _draw_stroked_filled_word(
    painter: QPainter,
    word: str,
    x: float,
    baseline_y: float,
    font: QFont,
    fill_brush: QBrush,
    stroke_color: QColor,
    stroke_px: float,
    shadow_alpha: int = 150,
    glow_color: Optional[QColor] = None,
    glow_pulse: float = 0.5,
) -> None:
    """
    Renders a single word or phrase using QPainterPath to faithfully reproduce
    DaVinci Fusion TextPlus Shading Elements:
      - Element 3: SoftGlow / Drop Shadow
      - Element 2: Round-Join Outline Stroke
      - Element 1: Solid or Gradient Fill
    """
    path = QPainterPath()
    path.addText(QPointF(x, baseline_y), font, word)

    # 1. SoftGlow (Shading Element 3 / SoftGlow node)
    if glow_color is not None and glow_color.isValid():
        for radius_mult, base_alpha in ((3.2, 38), (2.0, 75), (1.1, 130)):
            gc = QColor(glow_color)
            gc.setAlpha(min(255, int(base_alpha * (0.65 + 0.55 * glow_pulse))))
            glow_pen = QPen(
                gc,
                max(2.0, font.pixelSize() * 0.14 * radius_mult),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
            painter.strokePath(path, glow_pen)

    # 2. Subtle NLE Drop Shadow for contrast on video plates
    if shadow_alpha > 0:
        sh_off = max(1.2, font.pixelSize() * 0.055)
        sh_path = path.translated(sh_off, sh_off * 1.15)
        painter.fillPath(sh_path, QColor(0, 0, 0, shadow_alpha))

    # 3. Shading Element 2: Outline / Stroke
    if stroke_px > 0.15:
        pen = QPen(
            stroke_color,
            stroke_px,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.strokePath(path, pen)

    # 4. Shading Element 1: Fill
    painter.fillPath(path, fill_brush)


def draw_davinci_text_frame(
    painter: QPainter,
    rect: Any,
    spec_or_asset: Any,
    progress: float = 0.45,
    aspect_ratio: str = "16:9",
    show_hud: bool = True,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Renders a single frame of a DaVinci Resolve Fusion Text+ composition inside `rect`.
    Used by:
      - Asset Library Grid Thumbnails (`show_hud=False`, `progress=0.45`)
      - Dock Inspector `TextPresetPreviewCanvas` (`show_hud=True`)
      - Hover Live Preview (`progress` animated `0.0..1.0`)
      - Studio Inspector `TextLivePreviewWidget` (`16:9` / `9:16` + live overrides)
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    outer_rect = QRectF(rect) if isinstance(rect, QRect) else QRectF(rect)
    if outer_rect.width() <= 4 or outer_rect.height() <= 4:
        painter.restore()
        return

    spec = extract_davinci_text_spec(spec_or_asset, overrides=overrides)
    pid = spec["id"].lower()
    anim = spec["animation"]
    curve = spec["timing_curve"]
    progress = max(0.0, min(1.0, float(progress)))

    # -------------------------------------------------------------------------
    # 1. DAVINCI NLE VIEWER BACKDROP & ASPECT RATIO FRAME (16:9 vs 9:16)
    # -------------------------------------------------------------------------
    painter.fillRect(outer_rect, QColor("#050507"))

    if aspect_ratio == "9:16" and outer_rect.width() > outer_rect.height() * 0.75:
        target_h = outer_rect.height() - 6.0
        target_w = max(48.0, min(outer_rect.width() - 8.0, target_h * (9.0 / 16.0)))
        canvas_rect = QRectF(
            outer_rect.center().x() - target_w / 2.0,
            outer_rect.center().y() - target_h / 2.0,
            target_w,
            target_h,
        )
    else:
        canvas_rect = QRectF(outer_rect)

    # Clip to active viewer frame
    clip_path = QPainterPath()
    clip_path.addRoundedRect(canvas_rect, 5.0, 5.0)
    painter.setClipPath(clip_path)

    # Graded cinematic video monitor plate (so white, black, and yellow strokes all pop clearly)
    bg_grad = QLinearGradient(canvas_rect.topLeft(), canvas_rect.bottomRight())
    if anim == "paper" or "paper" in pid:
        bg_grad.setColorAt(0.0, QColor("#1f1b16"))
        bg_grad.setColorAt(0.5, QColor("#2b241d"))
        bg_grad.setColorAt(1.0, QColor("#15120e"))
    elif anim == "glow" or spec.get("glow_color"):
        bg_grad.setColorAt(0.0, QColor("#070d19"))
        bg_grad.setColorAt(0.5, QColor("#0d1b2e"))
        bg_grad.setColorAt(1.0, QColor("#060911"))
    elif "gold" in pid or "luxury" in pid:
        bg_grad.setColorAt(0.0, QColor("#17120a"))
        bg_grad.setColorAt(0.5, QColor("#241b0d"))
        bg_grad.setColorAt(1.0, QColor("#0d0a06"))
    else:
        bg_grad.setColorAt(0.0, QColor("#0b0d14"))
        bg_grad.setColorAt(0.5, QColor("#161926"))
        bg_grad.setColorAt(1.0, QColor("#090a10"))
    painter.fillRect(canvas_rect, bg_grad)

    # Subtle ambient stage spotlight in center of DaVinci Viewer
    ambient_col = _safe_qcolor(
        spec.get("glow_color") or spec.get("box_color") or spec.get("highlight_color"),
        "#38BDF8",
    )
    ambient_col.setAlpha(28)
    rad = QRadialGradient(canvas_rect.center(), max(canvas_rect.width(), canvas_rect.height()) * 0.55)
    rad.setColorAt(0.0, ambient_col)
    rad.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillRect(canvas_rect, rad)

    # Subtle Title Safe (80%) & Action Safe (90%) DaVinci Viewer guides when show_hud=True
    if show_hud and canvas_rect.height() >= 68:
        action_rect = canvas_rect.adjusted(
            canvas_rect.width() * 0.05,
            canvas_rect.height() * 0.05,
            -canvas_rect.width() * 0.05,
            -canvas_rect.height() * 0.05,
        )
        title_rect = canvas_rect.adjusted(
            canvas_rect.width() * 0.10,
            canvas_rect.height() * 0.10,
            -canvas_rect.width() * 0.10,
            -canvas_rect.height() * 0.10,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1, Qt.PenStyle.DotLine))
        painter.drawRect(action_rect)
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1, Qt.PenStyle.DotLine))
        painter.drawRect(title_rect)

        # Center crosshair +
        cx_g = canvas_rect.center().x()
        cy_g = canvas_rect.center().y()
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.drawLine(QPointF(cx_g - 5, cy_g), QPointF(cx_g + 5, cy_g))
        painter.drawLine(QPointF(cx_g, cy_g - 5), QPointF(cx_g, cy_g + 5))

    # -------------------------------------------------------------------------
    # 2. PROPORTIONAL FUSION TYPOGRAPHY & TIMING CURVE
    # -------------------------------------------------------------------------
    cw = canvas_rect.width()
    ch = canvas_rect.height()

    # Scale font pixel size directly from spec["font_size"] (12..140 pt -> Fusion 0.02..0.23)
    font_pt = float(spec["font_size"])
    scale_ref = ch * (font_pt / 600.0) * 1.85
    target_px = max(9.0, min(ch * 0.34, max(ch * 0.075, scale_ref)))

    font = _build_qfont(spec["font_family"], target_px, spec["weight"])
    fm = QFontMetrics(font)

    # Auto-fit if single line or 2-line layout exceeds 86% of canvas width
    raw_text = spec["text"] or "RESOLVEFLOW"
    words = raw_text.split() or [raw_text]
    max_line_w = cw * 0.84
    lines = _split_into_lines(words, fm, max_line_w)
    widest_line = max((fm.horizontalAdvance(" ".join(ln)) for ln in lines), default=10)
    if widest_line > max_line_w and widest_line > 0:
        shrink = max(0.52, max_line_w / float(widest_line))
        target_px = max(9.0, target_px * shrink)
        font = _build_qfont(spec["font_family"], target_px, spec["weight"])
        fm = QFontMetrics(font)
        lines = _split_into_lines(words, fm, max_line_w)

    # Compute vertical anchor position
    pos_y_ratio = spec["position_y_9_16"] if aspect_ratio == "9:16" else spec["position_y_16_9"]
    if show_hud and ch >= 115:
        # Map position_y_ratio (0.12..0.85 from bottom) into viewer vertical space
        clamped_y = max(0.18, min(0.80, pos_y_ratio + 0.15))
        base_cy = canvas_rect.bottom() - ch * clamped_y
    else:
        base_cy = canvas_rect.center().y()

    scale_env, angle_env, y_off_ratio, opacity_env = _eval_motion_envelope(progress, curve, anim)
    painter.setOpacity(opacity_env)

    cy = base_cy + y_off_ratio * ch
    cx = canvas_rect.center().x()

    std_color = _safe_qcolor(spec["standard_color"], "#FFFFFF")
    hl_color = _safe_qcolor(spec["highlight_color"], "#FFD700")
    stroke_color = _safe_qcolor(spec["stroke_color"], "#000000")
    stroke_w = float(spec["stroke_width"])
    stroke_px = max(1.0, stroke_w * target_px * 0.45) if stroke_w > 0.005 else 0.0

    box_qcolor = _safe_qcolor(spec["box_color"], "#FFE600") if spec.get("box_color") else None
    glow_qcolor = _safe_qcolor(spec["glow_color"], "#00E5FF") if spec.get("glow_color") else None
    glow_pulse = 0.5 + 0.5 * math.sin(2.0 * math.pi * progress * 2.0)

    # Neon tube ignition flicker during initial frames in glow mode
    if (anim == "glow" or glow_qcolor is not None) and progress < 0.12:
        if _hash01(7.13, progress * 60.0) <= 0.42:
            glow_pulse *= 0.25

    # VHS Retro OSD Header if applicable
    if "vhs" in pid:
        osd_font = _build_qfont("Consolas", max(8.0, ch * 0.075), "bold")
        painter.setFont(osd_font)
        painter.setPen(QColor("#22c55e"))
        osd_y = canvas_rect.top() + (22 if show_hud else 6)
        painter.drawText(
            QRectF(canvas_rect.left() + 8, osd_y, cw - 16, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "▶ PLAY SP 00:01:24",
        )

    # -------------------------------------------------------------------------
    # 3. FUSION SHADING ELEMENTS & NODE PIPELINE RENDER
    # -------------------------------------------------------------------------
    line_h = fm.height() * 1.08
    total_block_h = len(lines) * line_h
    first_line_cy = cy - (total_block_h / 2.0) + (line_h / 2.0)

    # --- MODE A: PAPER CUTOUT / UNFOLD STOP-MOTION (`RF_Paper_Cutout_Jitter`) ---
    if anim == "paper":
        step_idx = math.floor(progress * 12.0)
        wobble_deg = 2.6 * (_hash01(3.3, step_idx) - 0.5) * 2.0
        shake_x = cw * 0.006 * (_hash01(5.1, step_idx) - 0.5)
        shake_y = ch * 0.006 * (_hash01(8.9, step_idx) - 0.5)
        stop_scale = 0.75 if progress < 0.08 else (1.12 if progress < 0.22 else 1.0)

        painter.save()
        painter.translate(cx + shake_x, cy + shake_y)
        painter.rotate(wobble_deg)
        painter.scale(stop_scale, stop_scale)

        paper_col = box_qcolor or _safe_qcolor("#FBF9F1")
        pad_x = max(10.0, target_px * 0.45)
        pad_y = max(6.0, target_px * 0.28)
        card_w = min(cw * 0.88, widest_line + pad_x * 2.0)
        card_h = total_block_h + pad_y * 1.6
        half_w, half_h = card_w / 2.0, card_h / 2.0

        # Build torn paper polygon (FastNoise + Displace)
        poly = QPolygonF()
        segs_x = 12
        segs_y = 5
        for i in range(segs_x + 1):
            tx = -half_w + (card_w * i / segs_x)
            ty = -half_h + ((_hash01(i * 1.7 + 1.0, 1.0) - 0.5) * 3.5)
            poly.append(QPointF(tx, ty))
        for i in range(1, segs_y + 1):
            tx = half_w + ((_hash01(i * 2.3 + 20.0, 1.0) - 0.5) * 3.5)
            ty = -half_h + (card_h * i / segs_y)
            poly.append(QPointF(tx, ty))
        for i in range(1, segs_x + 1):
            tx = half_w - (card_w * i / segs_x)
            ty = half_h + ((_hash01(i * 3.1 + 40.0, 1.0) - 0.5) * 3.5)
            poly.append(QPointF(tx, ty))
        for i in range(1, segs_y):
            tx = -half_w + ((_hash01(i * 4.7 + 60.0, 1.0) - 0.5) * 3.5)
            ty = half_h - (card_h * i / segs_y)
            poly.append(QPointF(tx, ty))

        # PaperShadow node
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 135))
        painter.drawPolygon(poly.translated(3.5, 4.5))

        # PaperShape card fill & subtle crease border
        painter.setBrush(paper_col)
        painter.setPen(QPen(QColor(0, 0, 0, 45), 1.0))
        painter.drawPolygon(poly)

        # Render words on paper card
        ink_col = std_color if std_color.lightness() < 200 else QColor("#1A1A1A")
        for l_idx, ln_words in enumerate(lines):
            ln_str = " ".join(ln_words)
            lw = fm.horizontalAdvance(ln_str)
            ly = -total_block_h / 2.0 + l_idx * line_h + fm.ascent()
            _draw_stroked_filled_word(
                painter,
                ln_str,
                -lw / 2.0,
                ly,
                font,
                QBrush(ink_col),
                stroke_color,
                stroke_px * 0.6,
                shadow_alpha=0,
            )
        painter.restore()

    # --- MODE B: CYBERPUNK RGB SPLIT GLITCH (`RF_Cyberpunk_RGB_Glitch`) ---
    elif anim == "glitch":
        step_idx = math.floor(progress * 24.0)
        glitch_amt = max(2.2, target_px * 0.11)
        rx = glitch_amt * (_hash01(1.7, step_idx) - 0.5) * 2.2 - 2.5
        ry = glitch_amt * 0.35 * (_hash01(12.7, step_idx) - 0.5)
        gx = glitch_amt * (_hash01(3.1, step_idx) - 0.5) * 1.4
        gy = glitch_amt * 0.25 * (_hash01(14.1, step_idx) - 0.5)
        bx = glitch_amt * (_hash01(5.3, step_idx) - 0.5) * 2.2 + 2.5
        by = glitch_amt * 0.35 * (_hash01(16.3, step_idx) - 0.5)

        for l_idx, ln_words in enumerate(lines):
            ln_str = " ".join(ln_words)
            lw = fm.horizontalAdvance(ln_str)
            lx = cx - lw / 2.0
            ly = first_line_cy + (l_idx - (len(lines) - 1) / 2.0) * line_h + fm.capHeight() / 2.0

            # Instanced R, G, B channels
            r_path = QPainterPath()
            r_path.addText(QPointF(lx + rx, ly + ry), font, ln_str)
            painter.fillPath(r_path, QColor(255, 0, 85, 190))

            g_path = QPainterPath()
            g_path.addText(QPointF(lx + gx, ly + gy), font, ln_str)
            painter.fillPath(g_path, QColor(0, 255, 140, 150))

            b_path = QPainterPath()
            b_path.addText(QPointF(lx + bx, ly + by), font, ln_str)
            painter.fillPath(b_path, QColor(0, 229, 255, 195))

            # Center Text1 + Outline2
            _draw_stroked_filled_word(
                painter,
                ln_str,
                lx,
                ly,
                font,
                QBrush(std_color),
                stroke_color,
                stroke_px,
                shadow_alpha=120,
                glow_color=glow_qcolor,
                glow_pulse=glow_pulse,
            )

        # Scanline displacement bar
        slice_y = cy + (_hash01(9.7, step_idx) - 0.5) * total_block_h * 0.8
        painter.fillRect(QRectF(cx - widest_line * 0.55, slice_y, widest_line * 1.1, 2.0), QColor(0, 229, 255, 110))

    # --- MODE C: TYPEWRITER + BLINKING CURSOR (`RF_Typewriter_Cursor`) ---
    elif anim == "typewriter":
        full_str = " ".join(words)
        # At default static preview progress=0.45, show 90% of characters; otherwise map 0..1
        reveal_ratio = 0.92 if abs(progress - 0.45) < 1e-3 else max(0.15, min(1.0, progress * 1.15))
        char_count = max(1, int(round(len(full_str) * reveal_ratio)))
        shown_str = full_str[:char_count]
        blink_on = (int(progress * 20.0) % 2 == 0) or abs(progress - 0.45) < 1e-3
        cursor_str = "|" if blink_on else " "
        render_lines = _split_into_lines((shown_str + cursor_str).split(), fm, max_line_w)

        for l_idx, ln_words in enumerate(render_lines):
            ln_str = " ".join(ln_words)
            lw = fm.horizontalAdvance(ln_str)
            lx = cx - lw / 2.0
            ly = first_line_cy + (l_idx - (len(render_lines) - 1) / 2.0) * line_h + fm.capHeight() / 2.0

            _draw_stroked_filled_word(
                painter,
                ln_str,
                lx,
                ly,
                font,
                QBrush(std_color),
                stroke_color,
                stroke_px,
                shadow_alpha=150,
                glow_color=glow_qcolor,
                glow_pulse=glow_pulse,
            )

    # --- MODE D: HIGHLIGHTER MARKER SWIPE / BOX HIGHLIGHT / KINETIC POP / GLOW ---
    else:
        painter.save()
        painter.translate(cx, cy)
        if anim in ("pop", "bounce") and len(words) == 1:
            painter.scale(scale_env, scale_env)
            painter.rotate(angle_env)
        painter.translate(-cx, -cy)

        # Full-line Highlighter Marker Swipe (`RF_Highlighter_Swipe`) or Podcast Pill (`minimal_podcast_pill`)
        is_full_marker = (
            anim == "box_highlight"
            and ("highlighter" in pid or "pill" in pid or "podcast" in pid or len(words) == 1)
        )
        if is_full_marker:
            marker_col = box_qcolor or hl_color
            swipe_reveal = min(1.0, max(0.25, progress / 0.40)) if curve != "linear" else min(1.0, max(0.3, progress / 0.5))
            pad_x = max(10.0, target_px * 0.42)
            pad_y = max(5.0, target_px * 0.22)
            full_box_w = min(cw * 0.90, widest_line + pad_x * 2.0)
            revealed_w = full_box_w * swipe_reveal
            box_h = total_block_h + pad_y * 1.4
            box_rect = QRectF(cx - full_box_w / 2.0, cy - box_h / 2.0, revealed_w, box_h)

            painter.save()
            if "highlighter" in pid:
                painter.translate(cx, cy)
                painter.rotate(-1.5)
                painter.translate(-cx, -cy)
                mc = QColor(marker_col)
                mc.setAlpha(235)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(mc)
                painter.drawRoundedRect(box_rect, 4.0, 4.0)
            else:
                # Podcast Pill / Box
                mc = QColor(marker_col)
                mc.setAlpha(230)
                painter.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
                painter.setBrush(mc)
                rad_px = box_h / 2.0 if ("pill" in pid or "podcast" in pid) else 6.0
                painter.drawRoundedRect(box_rect, rad_px, rad_px)
            painter.restore()

        # Determine active highlighted word index across all words
        total_words = sum(len(ln) for ln in lines)
        if total_words <= 1:
            active_word_idx = 0
        else:
            # At default progress=0.45, highlight the last/key word; during scrub, cycle through words
            if abs(progress - 0.45) < 1e-3:
                active_word_idx = total_words - 1 if "hormozi" in pid or "mrbeast" in pid else min(1, total_words - 1)
            else:
                active_word_idx = int(progress * total_words * 1.5) % total_words

        space_w = max(4.0, fm.horizontalAdvance(" "))
        global_w_idx = 0

        for l_idx, ln_words in enumerate(lines):
            word_widths = [fm.horizontalAdvance(w) for w in ln_words]
            line_total_w = sum(word_widths) + space_w * max(0, len(ln_words) - 1)
            cur_x = cx - line_total_w / 2.0
            baseline_y = (
                first_line_cy
                + (l_idx - (len(lines) - 1) / 2.0) * line_h
                + fm.capHeight() / 2.0
            )

            for w_i, word in enumerate(ln_words):
                ww = word_widths[w_i]
                is_active = (global_w_idx == active_word_idx)

                # Per-word Bounding Box (Shading Element 4: Box Highlight on active word)
                if (anim == "box_highlight" or box_qcolor is not None) and not is_full_marker and is_active:
                    b_col = box_qcolor or _safe_qcolor("#E60000")
                    b_pad_x = max(5.0, target_px * 0.18)
                    b_pad_y = max(3.0, target_px * 0.12)
                    w_box = QRectF(
                        cur_x - b_pad_x,
                        baseline_y - fm.ascent() - b_pad_y * 0.5,
                        ww + b_pad_x * 2.0,
                        fm.height() + b_pad_y,
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(b_col)
                    painter.drawRoundedRect(w_box, 4.5, 4.5)

                # Choose fill brush (Gradient, Active Highlight, or Standard Fill)
                grad_cols = spec.get("gradient_colors")
                if grad_cols and len(grad_cols) >= 2:
                    grad = QLinearGradient(QPointF(cur_x, baseline_y - fm.ascent()), QPointF(cur_x + ww, baseline_y))
                    for g_idx, g_hex in enumerate(grad_cols):
                        grad.setColorAt(g_idx / float(len(grad_cols) - 1), _safe_qcolor(g_hex))
                    fill_brush = QBrush(grad)
                elif is_active and anim in ("pop", "bounce", "glow", "slide", "fade_up"):
                    fill_brush = QBrush(hl_color)
                elif is_full_marker and "highlighter" in pid:
                    fill_brush = QBrush(std_color)
                else:
                    fill_brush = QBrush(std_color if not (total_words == 1 and anim in ("pop", "bounce")) else hl_color)

                painter.save()
                if is_active and anim in ("pop", "bounce") and total_words > 1:
                    w_cx = cur_x + ww / 2.0
                    w_cy = baseline_y - fm.capHeight() / 2.0
                    word_pop = 1.0 + (scale_env - 1.0) * 0.85 if scale_env != 1.0 else 1.12
                    bounce_dy = -target_px * 0.08 if anim == "bounce" else 0.0
                    painter.translate(w_cx, w_cy + bounce_dy)
                    painter.scale(word_pop, word_pop)
                    painter.rotate(angle_env * 0.5)
                    painter.translate(-w_cx, -w_cy)

                _draw_stroked_filled_word(
                    painter,
                    word,
                    cur_x,
                    baseline_y,
                    font,
                    fill_brush,
                    stroke_color,
                    stroke_px,
                    shadow_alpha=0 if (is_full_marker and "highlighter" in pid) else 155,
                    glow_color=glow_qcolor if (anim == "glow" or glow_qcolor is not None) else None,
                    glow_pulse=glow_pulse,
                )
                painter.restore()

                cur_x += ww + space_w
                global_w_idx += 1

        painter.restore()

    # -------------------------------------------------------------------------
    # 4. DAVINCI VIEWER HUD OVERLAY (WHEN `show_hud=True`)
    # -------------------------------------------------------------------------
    if show_hud and ch >= 64:
        hud_font = QFont("Segoe UI", 8)
        hud_font.setBold(True)
        painter.setFont(hud_font)
        fm_hud = QFontMetrics(hud_font)

        # Top-Left: DaVinci Text+ Viewer Badge
        tl_text = "● Text+"
        tl_w = fm_hud.horizontalAdvance(tl_text) + 12
        tl_rect = QRectF(canvas_rect.left() + 6, canvas_rect.top() + 5, tl_w, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(9, 10, 15, 185))
        painter.drawRoundedRect(tl_rect, 3.5, 3.5)
        painter.setPen(QColor("#22d3ee"))
        painter.drawText(tl_rect, Qt.AlignmentFlag.AlignCenter, tl_text)

        # Top-Right: Animation + Aspect Ratio Badge
        anim_badge = f"{anim.upper()} • {aspect_ratio}"
        tr_w = fm_hud.horizontalAdvance(anim_badge) + 12
        tr_rect = QRectF(canvas_rect.right() - tr_w - 6, canvas_rect.top() + 5, tr_w, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(9, 10, 15, 185))
        painter.drawRoundedRect(tr_rect, 3.5, 3.5)
        painter.setPen(QColor("#a1a1aa"))
        painter.drawText(tr_rect, Qt.AlignmentFlag.AlignCenter, anim_badge)

        # Bottom Keyframe Scrubber Progress Line
        bar_h = 2.5
        bar_rect = QRectF(canvas_rect.left(), canvas_rect.bottom() - bar_h, canvas_rect.width(), bar_h)
        painter.fillRect(bar_rect, QColor(255, 255, 255, 22))
        prog_rect = QRectF(canvas_rect.left(), canvas_rect.bottom() - bar_h, canvas_rect.width() * progress, bar_h)
        painter.fillRect(prog_rect, QColor("#06b6d4"))

    # Crisp frame border around active viewer
    painter.setClipping(False)
    border_col = QColor(6, 182, 212, 110) if aspect_ratio == "9:16" else QColor("#27272a")
    painter.setPen(QPen(border_col, 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(canvas_rect, 5.0, 5.0)

    painter.restore()


def draw_text_preset_preview(
    painter: QPainter,
    rect: Any,
    asset: Any,
    name: str = "",
    custom_text: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Backward-compatible entry point for drawing a Text+ preset preview onto `painter` inside `rect`.
    Accepts `asset` as a `TextStylePreset`, `StudioAsset`, `dict`, or `preset_id` string.
    """
    ovr = dict(overrides or {})
    if name and "name" not in ovr:
        ovr["name"] = name
    if custom_text is not None and str(custom_text).strip():
        ovr["text"] = str(custom_text).strip()

    draw_davinci_text_frame(
        painter=painter,
        rect=rect,
        spec_or_asset=asset,
        progress=float(ovr.get("progress", 0.45)),
        aspect_ratio=str(ovr.get("aspect_ratio", "16:9")),
        show_hud=bool(ovr.get("show_hud", False)),
        overrides=ovr,
    )


def generate_text_preset_thumbnail(
    asset: Any,
    width: int = 256,
    height: int = 144,
    name: str = "",
    custom_text: Optional[str] = None,
) -> QPixmap:
    """
    Generates or retrieves a cached 16:9 QPixmap thumbnail for a TextStylePreset, StudioAsset, dict, or ID.
    """
    spec = extract_davinci_text_spec(
        asset,
        overrides={"name": name, "text": custom_text} if (name or custom_text) else None,
    )
    cache_key = (
        f"{spec['id']}_{spec['text']}_{spec['font_family']}_{spec['font_size']}_"
        f"{spec['standard_color']}_{spec['highlight_color']}_{spec['stroke_color']}_"
        f"{spec['stroke_width']}_{spec['box_color']}_{spec['glow_color']}_"
        f"{spec['animation']}_{width}x{height}"
    )
    if cache_key in _PRESET_PIXMAP_CACHE:
        return _PRESET_PIXMAP_CACHE[cache_key]

    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    draw_davinci_text_frame(
        painter=painter,
        rect=QRectF(0, 0, width, height),
        spec_or_asset=spec,
        progress=0.45,
        aspect_ratio="16:9",
        show_hud=False,
    )
    painter.end()

    _PRESET_PIXMAP_CACHE[cache_key] = pixmap
    return pixmap


def get_text_preset_pixmap(
    preset_id: Any,
    name: str = "",
    custom_text: Optional[str] = None,
    width: int = 320,
    height: int = 180,
) -> QPixmap:
    """
    Backward-compatible alias for `generate_text_preset_thumbnail`.
    """
    return generate_text_preset_thumbnail(
        asset=preset_id,
        width=width,
        height=height,
        name=name,
        custom_text=custom_text,
    )
