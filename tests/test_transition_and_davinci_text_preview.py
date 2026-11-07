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


def test_davinci_3d_cube_lut_preview_engine_and_inspector(tmp_path, qapp):
    """Kiểm tra bộ đọc 3D .cube LUT chuẩn DaVinci Resolve (Trilinear + Before/After Split Wipe + LutEditor + LutSplitWidget)."""
    import numpy as np
    from src.ui.lux.library.lut_preview_generator import (
        parse_cube_lut,
        apply_lut_to_rgb,
        extract_lut_palette,
        generate_lut_thumbnail,
    )
    from src.ui.lux.library.asset_model import AssetListModel, ThumbnailRole
    from src.ui.lux.inspector.editors import LutEditor
    from src.ui.lux.inspector.inspector_host import SheetInspector
    from src.ui.tabs.tab_assets import LutSplitWidget
    from PySide6.QtWidgets import QWidget

    # Sinh file .cube 3D 4^3 đẩy mạnh kênh Đỏ và hạ Xanh dương
    lines = ['TITLE "Warm Test 3D LUT"', "LUT_3D_SIZE 4", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    for bi in range(4):
        b = bi / 3.0
        for gi in range(4):
            g = gi / 3.0
            for ri in range(4):
                r = ri / 3.0
                out_r = min(1.0, r * 0.7 + 0.30)
                out_g = g * 0.85
                out_b = b * 0.35
                lines.append(f"{out_r:.6f} {out_g:.6f} {out_b:.6f}")

    cube_file = tmp_path / "Morocco_Warm_Test.cube"
    cube_file.write_text("\n".join(lines), encoding="utf-8")

    lut_info = parse_cube_lut(str(cube_file))
    assert lut_info is not None and lut_info["kind"] == "3d"
    assert lut_info["table"].shape == (4, 4, 4, 3)

    sample_rgb = np.array([[[0.4, 0.5, 0.6]]], dtype=np.float32)
    graded_rgb = apply_lut_to_rgb(sample_rgb, lut_info)
    assert float(graded_rgb[0, 0, 0]) > float(sample_rgb[0, 0, 0])
    assert float(graded_rgb[0, 0, 2]) < float(sample_rgb[0, 0, 2])

    lut_asset = {
        "id": "lut_morocco_test",
        "name": "Morocco Warm Test",
        "tab": "lut",
        "category": "cinema",
        "file_path": str(cube_file),
    }
    swatches = extract_lut_palette(lut_asset)
    assert len(swatches) == 4 and all(s.startswith("#") and len(s) == 7 for s in swatches)

    thumb = generate_lut_thumbnail(lut_asset, 256, 144)
    assert isinstance(thumb, QPixmap) and not thumb.isNull()
    qimg = thumb.toImage()
    # Vùng GỐC bên trái (x=24) và vùng SAU bên phải (x=210) phải khác màu sau khi áp 3D LUT
    c_left = qimg.pixelColor(24, 72)
    c_right = qimg.pixelColor(210, 72)
    assert (c_left.red(), c_left.blue()) != (c_right.red(), c_right.blue())

    # AssetListModel tự động tạo thumbnail 3D LUT cho danh mục lut/color
    model = AssetListModel([lut_asset])
    idx = model.index(0, 0)
    model_pix = model.data(idx, ThumbnailRole)
    assert isinstance(model_pix, QPixmap) and not model_pix.isNull()

    # HoverPreviewOverlay hỗ trợ quét Before/After trực tiếp cho LUT
    hover = HoverPreviewOverlay(None)
    hover.resize(256, 144)
    hover.preview_procedural(lut_asset, "lut", "Morocco Warm Test", "LUT")
    assert hover._proc_category == "lut"
    hover.grab()
    hover.stop_all()

    # SheetInspector định tuyến đúng cả tài nguyên có tab="lut", category="cinema" vào LutEditor
    host_parent = QWidget()
    host_parent.resize(400, 600)
    sheet = SheetInspector(host_parent)
    sheet.open_sheet(lut_asset)
    assert isinstance(sheet._editor_widget, LutEditor)
    sheet._editor_widget.slider_str.setValue(65)
    assert abs(sheet._editor_widget.lut_preview_canvas.strength - 0.65) < 1e-6
    sheet.close_sheet(animated=False)

    # Studio LutSplitWidget
    split_w = LutSplitWidget()
    split_w.resize(300, 140)
    split_w.set_lut("Morocco Warm Test", None, asset=lut_asset)
    split_w.set_split(40)
    split_w.grab()


def test_real_transition_media_priority_over_procedural(tmp_path, qapp):
    """Đảm bảo chuyển cảnh có ảnh/video thật luôn ưu tiên hiển thị ảnh & phát video gốc, không bị ghi đè bởi hình vẽ giả lập."""
    from pathlib import Path
    from src.ui.lux.library.asset_model import AssetListModel, ThumbnailRole
    from src.ui.lux.library.transition_preview_generator import build_verified_video_preview_payload

    # 1. Tạo ảnh PNG thật màu Đỏ thuần để kiểm chứng AssetListModel giữ nguyên ảnh thật của gói chuyển cảnh
    real_png = tmp_path / "Real_Pack_Frame.png"
    img = QImage(128, 72, QImage.Format_ARGB32)
    img.fill(0xFFDC2626)  # #DC2626 pure red
    img.save(str(real_png), "PNG")

    trans_with_real_png = {
        "id": "tr_real_png",
        "name": "3D Box Bounce - Down",
        "category": "trans",
        "thumbnail_path": str(real_png),
    }
    model = AssetListModel([trans_with_real_png])
    pix = model.data(model.index(0, 0), ThumbnailRole)
    assert isinstance(pix, QPixmap) and not pix.isNull()
    mid_col = pix.toImage().pixelColor(64, 36)
    assert mid_col.red() > 200 and mid_col.green() < 60, "Thumbnail thật của chuyển cảnh không được bị ghi đè bởi procedural!"

    # 2. Chuyển cảnh có video .mp4 hợp lệ trong assets/ phải kích hoạt chế độ video thật trong MacroEditor
    proj_assets = Path(__file__).resolve().parents[1] / "assets"
    bundled_tmp_vid = proj_assets / "_tmp_test_trans_preview.mp4"
    try:
        bundled_tmp_vid.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        trans_with_vid = {
            "id": "tr_real_mp4",
            "name": "Real Video Transition",
            "category": "trans",
            "source_kind": "builtin",
            "file_path": str(bundled_tmp_vid),
            "preview_path": str(bundled_tmp_vid),
        }
        ed_vid = MacroEditor(trans_with_vid)
        assert ed_vid.is_video_preview is True
        assert ed_vid.resolved_video_path == str(bundled_tmp_vid.resolve())
        ed_vid.stop_preview()
    finally:
        if bundled_tmp_vid.exists():
            bundled_tmp_vid.unlink()

    # 3. Bảo mật ActionGuard: build_verified_video_preview_payload không được ghi đè provenance của tài nguyên offline/foreign
    foreign_asset = {
        "id": "tr_foreign",
        "name": "Foreign Transition",
        "category": "trans",
        "source_kind": "managed",
        "origin_library_id": "foreign-lib-999",
        "origin_root": "/nonexistent/foreign/kho",
        "availability": "offline",
    }
    preserved = build_verified_video_preview_payload(foreign_asset, str(real_png))
    assert preserved["origin_library_id"] == "foreign-lib-999"
    assert preserved["availability"] == "offline"


def test_software_video_canvas_and_video_sink(qapp):
    """Kiểm tra canvas QVideoSink phần mềm phục vụ EdgeDockWindow trong suốt và tránh lỗi black box."""
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtMultimedia import QVideoSink, QVideoFrame, QMediaPlayer
    from src.ui.lux.library.hover_preview import _SoftwareVideoCanvas, HoverPreviewOverlay

    canvas = _SoftwareVideoCanvas()
    assert hasattr(canvas, "video_sink")
    assert isinstance(canvas.video_sink, QVideoSink)

    # Zero-dimension paint guard & valid resize paint check
    img = QImage(32, 32, QImage.Format.Format_ARGB32)
    img.fill(QColor("#22c55e"))
    canvas._current_image = img
    canvas.resize(0, 0)
    canvas.repaint()
    canvas.resize(160, 90)
    canvas.repaint()

    # Frame clear
    canvas.clear_frame()
    assert canvas._current_image is None

    # Test overlay integration
    overlay = HoverPreviewOverlay()
    vw = overlay.ensure_video_widget()
    assert isinstance(vw, _SoftwareVideoCanvas)
    assert overlay.video_widget is vw
    overlay.stop_all()


def test_icon_meme_preview_widget_start_auto_preview(tmp_path, qapp):
    """Kiểm tra tính năng start_auto_preview, auto-loop và khôi phục âm lượng khi chuyển sang Meme."""
    import pytest
    from PySide6.QtMultimedia import QMediaPlayer
    from src.ui.tabs.tab_assets import IconMemePreviewWidget

    vid_path = tmp_path / "test_trans.mp4"
    vid_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    meme_path = tmp_path / "test_meme.mp4"
    meme_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    widget = IconMemePreviewWidget(bundled_root=str(tmp_path))
    asset = {
        "id": "trans_loop_test",
        "name": "Loop Transition",
        "tab": "trans",
        "category": "trans",
        "source_kind": "builtin",
        "file_path": str(vid_path),
        "preview_path": str(vid_path),
    }
    widget.set_asset(asset)
    assert widget.resolved_file_path == str(vid_path.resolve())

    # Gọi start_auto_preview(muted=True) cho chuyển cảnh
    widget.start_auto_preview(muted=True)
    assert widget._is_active_playback is True
    assert widget._audio_output is not None
    assert widget._audio_output.volume() == 0.0

    # Chuyển sang Meme và phát thủ công -> âm lượng phải khôi phục về ~0.9 (không bị kẹt mute)
    meme_asset = {
        "id": "meme_sound_test",
        "name": "Meme With Audio",
        "tab": "meme",
        "category": "meme",
        "source_kind": "builtin",
        "file_path": str(meme_path),
        "preview_path": str(meme_path),
    }
    widget.set_asset(meme_asset)
    widget._start_playback()
    assert widget._audio_output.volume() == pytest.approx(0.9, abs=1e-2)

    # Dừng phát để giải phóng tài nguyên
    widget.stop_playback()
    assert widget._is_active_playback is False


def test_asset_indexer_fallback_and_oserror_guard(tmp_path, monkeypatch):
    """Kiểm tra AssetIndexer ưu tiên _MEIPASS/assets khi đóng gói PyInstaller và bọc OSError trong ensure_dirs."""
    import sys
    from src.core.asset_indexer import AssetIndexer
    from pathlib import Path

    # Đổi cwd sang tmp_path để giả lập môi trường cwd không có thư mục assets/ (như khi chạy từ /opt)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    # 1. Giả lập thư mục assets ứng viên cạnh executable (khi không có _MEIPASS)
    mock_assets = tmp_path / "app_bundle" / "assets"
    mock_assets.mkdir(parents=True)
    (mock_assets / "luts").mkdir()

    monkeypatch.setattr("sys.executable", str(tmp_path / "app_bundle" / "ResolveFlow-Assistant"))
    indexer = AssetIndexer(base_dir="assets")
    assert indexer.base_dir.resolve() == mock_assets.resolve()

    # 2. Giả lập môi trường /opt PyInstaller: tồn tại cả app_bundle/assets rỗng (kể cả khi cwd=app_bundle)
    # và _MEIPASS/assets thật -> phải ưu tiên _MEIPASS/assets khớp với ActionGuard bundled_root
    meipass_root = tmp_path / "app_bundle" / "_internal"
    meipass_assets = meipass_root / "assets"
    (meipass_assets / "luts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "app_bundle")
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_root), raising=False)

    indexer_frozen = AssetIndexer(base_dir="assets")
    assert indexer_frozen.base_dir.resolve() == meipass_assets.resolve()

    # Kiểm tra ensure_dirs không văng lỗi khi thư mục chỉ đọc
    def mock_mkdir_fail(*args, **kwargs):
        raise OSError("Read-only file system")

    monkeypatch.setattr(Path, "mkdir", mock_mkdir_fail)
    indexer_frozen.ensure_dirs()


def test_scan_transitions_managed_mp4_provenance_for_action_guard(tmp_path, monkeypatch):
    """Đảm bảo video .mp4 chuyển cảnh độc lập trong StorageManager được gắn đủ provenance cho ActionGuard."""
    import sys
    from src.core.action_guard import check_action_availability
    from src.core.asset_indexer import AssetIndexer
    from src.core.storage_manager import StorageManager

    meipass_root = tmp_path / "frozen_bundle" / "_internal"
    (meipass_root / "assets" / "templates" / "transitions").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass_root), raising=False)

    sm = StorageManager(config_dir=tmp_path / "sm_cfg", reserve_bytes=0)
    sm_root = tmp_path / "managed_kho"
    sm.configure(sm_root)
    pack_preview_dir = sm_root / "02_cloud_downloads" / "packs" / "GR44_Pack" / "Preview Transitions"
    pack_preview_dir.mkdir(parents=True, exist_ok=True)
    mp4_file = pack_preview_dir / "Whip Pan Standalone.mp4"
    mp4_file.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    monkeypatch.setattr("src.core.storage_manager.StorageManager", lambda: sm)
    AssetIndexer.clear_cache()

    indexer = AssetIndexer(base_dir="assets", settings_scope="ResolveFlowManagedTransTest")
    results = indexer.scan_transitions(force_refresh=True)
    matched = [r for r in results if r["name"] == "Whip Pan Standalone"]
    assert len(matched) == 1
    item = matched[0]
    assert item.get("source_kind") == "managed"
    assert item.get("storage_managed") is True
    assert item.get("origin_library_id") == sm._config["library_id"]
    assert item.get("origin_root") == str(sm_root.resolve())
    assert item.get("library_path") == "02_cloud_downloads/packs/GR44_Pack/Preview Transitions/Whip Pan Standalone.mp4"

    guard_res = check_action_availability(item, intent="preview", bundled_root=str(meipass_root / "assets"))
    assert guard_res.allowed is True
    assert guard_res.resolved_path == str(mp4_file.resolve())

