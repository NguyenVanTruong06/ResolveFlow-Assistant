"""
tests/test_transition_and_davinci_text_preview.py
Kiểm thử toàn diện cho:
1. Transition Preview Engine (12 archetypes, .drfx thumbnail extraction, .mp4 video resolver, TransitionLoopWidget, TransitionPreviewCanvas, HoverPreviewOverlay).
2. DaVinci-Faithful Text+ Preview Engine (Shading Elements 1-4, .setting parser, 16:9 & 9:16 viewer guides, TextLivePreviewWidget, TextPresetPreviewCanvas, Fusion TitleParams).
"""

import os
import zipfile
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QPixmap

from src.core.fusion.common import TitleParams, text_tool
from src.core.fusion.titles import spring_pop, highlighter, neon_pulse
from src.core.text_preset import TextStylePreset
from src.core.transition_preset import TransitionStylePreset
from src.ui.lux.library.transition_preview_generator import (
    infer_transition_kind,
    draw_transition_frame,
    generate_transition_thumbnail,
    resolve_drfx_thumbnail,
    resolve_transition_preview_video,
)
from src.ui.lux.library.text_preview_generator import (
    parse_fusion_setting_text_params,
    extract_davinci_text_spec,
    draw_davinci_text_frame,
    generate_text_preset_thumbnail,
)
from src.ui.lux.library.hover_preview import HoverPreviewOverlay
from src.ui.lux.inspector.editors import (
    TextPresetEditor,
    MacroEditor,
    TransitionPreviewCanvas,
    TextPresetPreviewCanvas,
)
from src.ui.tabs.tab_assets import TransitionLoopWidget, TextLivePreviewWidget


def test_transition_preview_all_archetypes(qapp):
    """Kiểm tra 12 archetypes chuyển cảnh vẽ khung hình Clip A -> Clip B mượt mà không lỗi."""
    samples = [
        ("Speed Ramp Seamless 01", "speed_ramp"),
        ("Cube 1 - Down", "3d_box"),
        ("Perspective Roll Left", "perspective"),
        ("Spin CCW Fast", "spin"),
        ("Whip Pan Left", "whip_pan"),
        ("Zoom In Impact", "zoom"),
        ("RGB Glitch Split", "glitch"),
        ("Liquid Ripple Wave", "displacement"),
        ("Circle Iris Wipe", "shape"),
        ("Frosted Ice Particles", "frost"),
        ("Unfold Paper Animator", "paper"),
        ("Flash Burn Strobe", "flash"),
    ]
    for name, expected_kind in samples:
        kind = infer_transition_kind(name)
        assert kind == expected_kind, f"{name} -> {kind} != {expected_kind}"

        img = QImage(320, 180, QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(img)
        draw_transition_frame(
            painter,
            QRectF(0, 0, 320, 180),
            name,
            progress=0.5,
            frames=24,
            kind=kind,
            show_hud=True,
        )
        painter.end()
        assert not img.isNull()


def test_drfx_thumbnail_and_video_resolver(tmp_path, qapp):
    """Giải nén thumbnail từ gói .drfx và liên kết video .mp4 trong Preview Transitions/."""
    pack_dir = tmp_path / "GR-44 ST Effects for DR v1.4"
    preview_dir = pack_dir / "Preview Transitions" / "3D Box"
    preview_dir.mkdir(parents=True)
    mp4_file = preview_dir / "Cube 1 - Down.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    drfx_path = pack_dir / "GR Seamless Transitions.drfx"
    png_img = QImage(64, 36, QImage.Format_ARGB32)
    png_img.fill(0xFF00BCD4)
    tmp_png = tmp_path / "Cube 1 - Down.png"
    png_img.save(str(tmp_png), "PNG")

    with zipfile.ZipFile(drfx_path, "w") as zf:
        zf.writestr("__MACOSX/._Cube 1 - Down.png", b"junk")
        zf.write(tmp_png, "Edit/Transitions/GR Transitions/01.3D Box/Cube 1 - Down.png")
        zf.writestr(
            "Edit/Transitions/GR Transitions/01.3D Box/Cube 1 - Down.setting",
            "{ Tools = ordered() {} }",
        )

    asset_drfx = {
        "id": "gr_cube_1",
        "name": "Cube 1 - Down",
        "file_path": str(drfx_path),
        "category": "trans",
    }
    thumb = resolve_drfx_thumbnail(asset_drfx)
    assert thumb and os.path.isfile(thumb)

    video = resolve_transition_preview_video(asset_drfx)
    assert video == str(mp4_file)

    pix = generate_transition_thumbnail(asset_drfx, 256, 144)
    assert isinstance(pix, QPixmap) and not pix.isNull()


def test_davinci_text_plus_fusion_and_preview_parity(tmp_path, qapp):
    """Đảm bảo TitleParams (Fusion Lua) và draw_davinci_text_frame dùng chung hệ tham số Shading 1-4."""
    params = TitleParams(
        text="CHUNDVC STUDIO",
        font="Impact",
        size=0.09,
        color=(1.0, 0.9, 0.0),
        outline_color=(0.0, 0.0, 0.0),
        outline_width=0.15,
        box_color=(0.85, 0.31, 0.24),
        glow_color=(0.02, 0.71, 0.83),
        timing_curve="spring",
    )
    # Kiểm tra các generator titles.py nhận màu tuỳ chỉnh và sinh Shading 2
    sp_lua = spring_pop(params).to_setting()
    hl_lua = highlighter(params).to_setting()
    np_lua = neon_pulse(params).to_setting()
    assert "Enabled2 = Input" in sp_lua
    assert "TextPlus" in sp_lua
    assert "TextPlus" in hl_lua
    assert "SoftGlow" in np_lua

    # Kiểm tra parser đọc lại file .setting
    setting_file = tmp_path / "custom_title.setting"
    setting_file.write_text(sp_lua, encoding="utf-8")
    parsed = parse_fusion_setting_text_params(str(setting_file))
    assert parsed.get("font_family") == "Impact"
    assert parsed.get("outline_width", 0) > 0

    preset = TextStylePreset(
        id="kinetic_hormozi",
        name="Alex Hormozi Pop",
        font="Impact",
        size=54,
        weight="extra_bold",
        standard_color="#FFFFFF",
        highlight_color="#FFE600",
        outline_color="#000000",
        outline_width=0.18,
        box_color="#D94F3D",
        glow_color="#06B6D4",
        animation="bounce",
        timing_curve="spring",
    )
    spec = extract_davinci_text_spec(preset, overrides={"text": "DAVINCI RESOLVE PREVIEW"})
    assert spec["font_family"] == "Impact"
    assert spec["outline_width"] == 0.18
    assert spec["box_color"] == "#D94F3D"
    assert spec["glow_color"] == "#06B6D4"

    for ratio in ("16:9", "9:16"):
        img = QImage(360, 220, QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(img)
        draw_davinci_text_frame(
            painter,
            QRectF(0, 0, 360, 220),
            preset,
            progress=0.45,
            aspect_ratio=ratio,
            show_hud=True,
            overrides={"text": "DAVINCI RESOLVE PREVIEW"},
        )
        painter.end()
        assert not img.isNull()

    thumb_pix = generate_text_preset_thumbnail(preset, 256, 144)
    assert not thumb_pix.isNull()


def test_inspector_and_hover_widgets_integration(qapp):
    """Kiểm tra các widget UI (TransitionLoopWidget, TextLivePreviewWidget, MacroEditor, TextPresetEditor, HoverPreviewOverlay)."""
    trans_widget = TransitionLoopWidget()
    trans_widget.set_transition("Cube 1 - Down", "3d_box", 24)
    trans_widget.resize(320, 130)
    trans_widget.grab()
    trans_widget.anim_timer.stop()

    text_widget = TextLivePreviewWidget()
    text_widget.set_params(
        text="ChunDVC Text+",
        preset_id="kinetic_hormozi",
        font_family="Montserrat",
        font_size=52,
        weight="extra_bold",
        standard_color="#FFFFFF",
        highlight_color="#FFE600",
        stroke_color="#000000",
        stroke_width=0.16,
        box_color="#D94F3D",
        glow_color="#06B6D4",
        animation="bounce",
        timing_curve="spring",
    )
    text_widget.set_aspect_ratio("16:9")
    text_widget.resize(320, 150)
    text_widget.grab()
    text_widget.anim_timer.stop()

    text_editor = TextPresetEditor(
        TextStylePreset(
            id="neon_pulse",
            name="Neon Pulse",
            glow_color="#00E5FF",
            animation="glow",
        )
    )
    text_editor.resize(340, 520)
    text_editor.grab()
    text_editor.preview_canvas._anim_timer.stop()

    macro_editor = MacroEditor(
        TransitionStylePreset(
            id="whip_pan_left",
            name="Whip Pan Left",
            category="whip_pan",
            duration_frames=20,
        )
    )
    macro_editor.resize(340, 420)
    macro_editor.grab()
    macro_editor.trans_preview_canvas._anim_timer.stop()

    hover = HoverPreviewOverlay(None)
    hover.preview_procedural({"name": "Whip Pan Left", "category": "trans"}, "trans", "Whip Pan Left", "Pan")
    hover.grab()
    hover.stop_all()
