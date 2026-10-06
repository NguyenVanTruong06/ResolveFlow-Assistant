import re
import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QFrame, QSlider, QApplication
from PySide6.QtCore import Qt

from src.ui.tabs.tab_assets import (
    AssetCard, StudioAsset, MOCKUP_LUTS, MOCKUP_SFX, MOCKUP_ICONS, MOCKUP_MEMES, TabAssets
)
from src.core.text_preset import BUILTIN_PRESETS, TextStylePreset
from src.core.transition_preset import BUILTIN_TRANSITIONS, TransitionStylePreset
from src.ui.theme import ThemeColors


def test_visual_card_renderers_text_rich_html(qapp):
    """Kiểm tra bộ render rich HTML cho thumbnail Text."""
    presets_by_id = {p.id: p for p in BUILTIN_PRESETS}

    # 1. Alex Hormozi (yellow highlight text)
    hormozi = presets_by_id.get("kinetic_hormozi")
    card_hormozi = AssetCard(hormozi)
    assert hasattr(card_hormozi, "_render_text_thumbnail"), "AssetCard must have _render_text_thumbnail"
    assert card_hormozi.lbl_text_preview is not None
    html_hormozi = card_hormozi.lbl_text_preview.text().lower()
    assert ("#facc15" in html_hormozi or "#ffe600" in html_hormozi or "#ffd700" in html_hormozi), (
        "Alex Hormozi preview must contain yellow highlight text"
    )

    # 2. Karaoke Pop (active green word)
    karaoke = presets_by_id.get("karaoke_pop")
    card_karaoke = AssetCard(karaoke)
    html_karaoke = card_karaoke.lbl_text_preview.text().lower()
    assert ("#a3e635" in html_karaoke or "#22c55e" in html_karaoke), (
        "Karaoke Pop preview must contain active green word highlight"
    )

    # 3. Box Highlight (red rounded box)
    box = presets_by_id.get("box_highlight")
    card_box = AssetCard(box)
    html_box = card_box.lbl_text_preview.text().lower()
    assert ("#ef4444" in html_box or "#e60000" in html_box), (
        "Box Highlight preview must contain red box styling"
    )

    # 4. Glow Neon Cyan (drop shadow glow / cyan neon)
    glow = presets_by_id.get("glow_neon")
    card_glow = AssetCard(glow)
    html_glow = card_glow.lbl_text_preview.text().lower()
    assert ("#06b6d4" in html_glow or "#00ffff" in html_glow or "#67e8f9" in html_glow or "#22d3ee" in html_glow), (
        "Glow Neon preview must contain cyan glow styling"
    )

    # 5. 90s VHS Camcorder (tape Consolas)
    vhs = presets_by_id.get("vhs_retro")
    card_vhs = AssetCard(vhs)
    html_vhs = card_vhs.lbl_text_preview.text().lower()
    assert ("consolas" in html_vhs or "vhs" in html_vhs or "play" in html_vhs), (
        "90s VHS preview must use tape Consolas or VHS playback elements"
    )

    # 6. Paper Cutout (paper/kraft styling)
    paper = presets_by_id.get("paper_cutout")
    card_paper = AssetCard(paper)
    html_paper = card_paper.lbl_text_preview.text().lower()
    thumb_style_paper = card_paper.thumb.styleSheet().lower()
    assert ("kraft" in html_paper or "cbb48a" in html_paper or "fef3c7" in html_paper
            or "cbb48a" in thumb_style_paper or "fbf9f1" in html_paper or "span" in html_paper), (
        "Paper Cutout must render vintage paper cutout elements"
    )

    # 7. Gradient Sunset
    gradient = presets_by_id.get("gradient_fill")
    card_gradient = AssetCard(gradient)
    html_gradient = card_gradient.lbl_text_preview.text().lower()
    thumb_gradient = card_gradient.thumb.styleSheet().lower()
    assert ("linear-gradient" in thumb_gradient or "gradient" in html_gradient or "linear-gradient" in html_gradient or "#fb923c" in html_gradient or "#f43f5e" in html_gradient or "#a855f7" in html_gradient), (
        "Gradient Sunset must render sunset gradient colors"
    )


def test_visual_card_renderers_sfx_waveform(qapp):
    """Kiểm tra bộ render Waveform 16-20 bars + nút quick play cho SFX."""
    sfx_items = [s for s in MOCKUP_SFX if s.id in ["sfx_whoosh", "sfx_pop", "sfx_ding", "sfx_riser", "sfx_glitch"]]
    assert len(sfx_items) >= 4

    for item in sfx_items:
        card = AssetCard(item)
        assert hasattr(card, "_render_sfx_thumbnail"), "AssetCard must have _render_sfx_thumbnail"
        # Nút nghe nhanh btn_quick_play
        assert hasattr(card, "btn_quick_play"), "SFX card must have btn_quick_play"
        assert isinstance(card.btn_quick_play, QPushButton)
        assert card.btn_quick_play.text() in ["▶", "⏸", "🔊"]

        # Waveform canvas có 16 đến 20 envelope bars
        assert hasattr(card, "waveform_canvas"), "SFX card must have waveform_canvas"
        assert hasattr(card.waveform_canvas, "bars"), "Waveform canvas must expose bars"
        bars = card.waveform_canvas.bars
        assert 16 <= len(bars) <= 20, f"Waveform must have 16-20 bars, got {len(bars)} for {item.id}"
        assert all(0.0 <= b <= 1.0 for b in bars), "All envelope bars must be normalized between 0.0 and 1.0"

        # Click quick play không được văng lỗi
        card.btn_quick_play.click()


def test_visual_card_renderers_lut_stripes(qapp):
    """Kiểm tra bảng màu 4 sọc đối lập + badge phân loại cho LUT."""
    lut = MOCKUP_LUTS[0]
    card = AssetCard(lut)
    assert hasattr(card, "_render_lut_thumbnail"), "AssetCard must have _render_lut_thumbnail"
    assert hasattr(card, "lut_stripes"), "LUT card must have lut_stripes"
    assert len(card.lut_stripes) == 4, f"LUT preview must have 4 stripes, got {len(card.lut_stripes)}"

    # Badge phân loại category badge
    assert hasattr(card, "badge_category"), "LUT card must have badge_category"
    assert isinstance(card.badge_category, QLabel)
    assert card.badge_category.text() != ""


def test_visual_card_renderers_transition(qapp):
    """Kiểm tra hiệu ứng chuyển cảnh: motion icon, scanline accent, badge frame count."""
    for trans in BUILTIN_TRANSITIONS:
        card = AssetCard(trans)
        assert hasattr(card, "_render_transition_thumbnail"), "AssetCard must have _render_transition_thumbnail"

        # Badge số frame (ví dụ: '16f', '20f', '24f', '30f')
        assert hasattr(card, "badge_frames"), "Transition card must have badge_frames"
        assert isinstance(card.badge_frames, QLabel)
        frame_text = card.badge_frames.text()
        assert frame_text.endswith("f") or "frame" in frame_text

        # Motion icon và scanline accent
        assert hasattr(card, "lbl_motion_icon"), "Transition card must have lbl_motion_icon"
        assert card.lbl_motion_icon.text() != ""
        assert hasattr(card, "scanline_accent"), "Transition card must have scanline_accent"


def test_visual_card_renderers_icon(qapp):
    """Kiểm tra icon vector / reaction emoji badge."""
    icon_item = MOCKUP_ICONS[0]
    card = AssetCard(icon_item)
    assert hasattr(card, "_render_icon_thumbnail"), "AssetCard must have _render_icon_thumbnail"
    assert hasattr(card, "lbl_icon_preview"), "Icon card must have lbl_icon_preview"
    assert isinstance(card.lbl_icon_preview, QLabel)
    assert card.lbl_icon_preview.text() != ""


def test_visual_card_renderers_global_constraints(qapp):
    """Kiểm tra ràng buộc kỹ thuật toàn cục: cấm cỡ chữ số thập phân (ví dụ 10.5px)."""
    fractional_px_pattern = re.compile(r'\b\d+\.\d+px\b')

    cards = [
        AssetCard(BUILTIN_PRESETS[0]),
        AssetCard(MOCKUP_SFX[0]),
        AssetCard(MOCKUP_LUTS[0]),
        AssetCard(BUILTIN_TRANSITIONS[0]),
        AssetCard(MOCKUP_ICONS[0]),
    ]

    for card in cards:
        # Kiểm tra stylesheet của card
        sheet = card.styleSheet()
        matches = fractional_px_pattern.findall(sheet)
        assert len(matches) == 0, f"Found fractional font/size px in card stylesheet: {matches}"

        # Kiểm tra thumbnail stylesheet
        if hasattr(card, "thumb") and card.thumb:
            t_sheet = card.thumb.styleSheet()
            t_matches = fractional_px_pattern.findall(t_sheet)
            assert len(t_matches) == 0, f"Found fractional px in thumb stylesheet: {t_matches}"


def test_inspector_live_previews(qapp):
    """Kiểm tra khu vực Inspector Live Preview viewports và nút chuyển tỉ lệ khung hình."""
    tab = TabAssets()
    tab.show()

    # 1. Aspect ratio switcher và Text Live Preview
    assert hasattr(tab, "inspector_aspect_btn"), "TabAssets must have inspector_aspect_btn"
    assert hasattr(tab, "current_aspect_ratio"), "TabAssets must store current_aspect_ratio"
    assert tab.current_aspect_ratio in ["16:9", "9:16"]
    assert isinstance(tab.inspector_aspect_btn, QPushButton)

    # Khởi tạo mặc định là 16:9, bấm nút chuyển sang 9:16 rồi quay lại 16:9
    initial_ratio = tab.current_aspect_ratio
    tab.inspector_aspect_btn.click()
    assert tab.current_aspect_ratio != initial_ratio
    assert tab.current_aspect_ratio in ["16:9", "9:16"]
    assert any(ratio in tab.inspector_aspect_btn.text() for ratio in ["16:9", "9:16"])

    # Text preset mặc định đang được chọn
    text_preset = BUILTIN_PRESETS[0]
    tab._on_card_selected(text_preset.id)
    assert tab.selected_asset.id == text_preset.id
    assert tab.inspector_aspect_btn.isVisible()
    # Kiểm tra viewport text preview
    assert hasattr(tab, "inspector_text_preview"), "TabAssets must have inspector_text_preview"
    assert tab.inspector_text_preview.isVisible()

    # 2. LUT Split Before/After Viewport
    assert hasattr(tab, "lut_split_slider"), "TabAssets must have lut_split_slider"
    assert isinstance(tab.lut_split_slider, QSlider)
    assert tab.lut_split_slider.minimum() == 0
    assert tab.lut_split_slider.maximum() == 100
    assert hasattr(tab, "inspector_lut_viewport"), "TabAssets must have inspector_lut_viewport"

    # Chọn một LUT
    lut_asset = MOCKUP_LUTS[0]
    tab._on_card_selected(lut_asset.id)
    assert tab.selected_asset.id == lut_asset.id
    assert tab.inspector_lut_viewport.isVisible()
    assert not tab.inspector_aspect_btn.isVisible()
    # Split slider thay đổi giá trị
    tab.lut_split_slider.setValue(75)
    assert tab.lut_split_slider.value() == 75

    # 3. SFX Waveform Visualizer & Playback Controller
    assert hasattr(tab, "sfx_waveform_canvas"), "TabAssets must have sfx_waveform_canvas"
    assert hasattr(tab, "inspector_sfx_viewport"), "TabAssets must have inspector_sfx_viewport"
    assert hasattr(tab, "sfx_play_btn"), "TabAssets must have sfx_play_btn playback controller"
    assert hasattr(tab, "sfx_time_lbl"), "TabAssets must have sfx_time_lbl time indicator"

    sfx_asset = MOCKUP_SFX[0]
    tab._on_card_selected(sfx_asset.id)
    assert tab.selected_asset.id == sfx_asset.id
    assert tab.inspector_sfx_viewport.isVisible()
    assert tab.sfx_waveform_canvas.isVisible()
    assert tab.sfx_play_btn.isVisible()
    assert tab.sfx_time_lbl.isVisible()
    tab.sfx_play_btn.click()

    # 4. Transition Visual Loop Preview
    assert hasattr(tab, "inspector_trans_viewport"), "TabAssets must have inspector_trans_viewport"
    trans_asset = BUILTIN_TRANSITIONS[0]
    tab._on_card_selected(trans_asset.id)
    assert tab.selected_asset.id == trans_asset.id
    assert tab.inspector_trans_viewport.isVisible()

    # 5. Icon/Meme Vector / Emoji / Thumbnail Preview
    assert hasattr(tab, "inspector_icon_viewport"), "TabAssets must have inspector_icon_viewport"
    icon_asset = MOCKUP_ICONS[0]
    tab._on_card_selected(icon_asset.id)
    assert tab.selected_asset.id == icon_asset.id
    assert tab.inspector_icon_viewport.isVisible()

    meme_asset = MOCKUP_MEMES[0]
    tab._on_card_selected(meme_asset.id)
    assert tab.selected_asset.id == meme_asset.id
    assert tab.inspector_icon_viewport.isVisible() or hasattr(tab, "inspector_meme_viewport")

    # 6. Global constraints: font sizes in stylesheets must use integer px (NO fractional px)
    fractional_px_pattern = re.compile(r'\b\d+\.\d+px\b')
    insp_sheet = tab.inspector.styleSheet()
    assert len(fractional_px_pattern.findall(insp_sheet)) == 0, "No fractional px in inspector stylesheet"


def test_asset_actions_and_drag_drop(qapp, monkeypatch):
    """
    Kiểm tra toàn diện bộ điều khiển tham số (Typography, Swatches, Motion & Curves, Destination Track)
    và 4 hành động DaVinci Resolve (_insert_at_playhead, _create_drag_mime_data / btn_drag_davinci,
    _get_current_fusion_macro_code / btn_copy_fusion, _install_to_fusion).
    """
    import os
    tab = TabAssets()
    tab.show()

    # -------------------------------------------------------------
    # 1. Inspector Parameter Tuning Controls:
    # -------------------------------------------------------------
    # A. Typography controls
    assert hasattr(tab, "combo_font"), "TabAssets must have combo_font dropdown"
    fonts = [tab.combo_font.itemText(i) for i in range(tab.combo_font.count())]
    for required_font in ["Montserrat", "Arial Black", "Bangers", "Be Vietnam Pro"]:
        assert any(required_font.lower() in f.lower() for f in fonts), f"Font {required_font} must be in combo_font"

    assert hasattr(tab, "slide_font_size"), "TabAssets must have slide_font_size slider"
    assert tab.slide_font_size.minimum() == 24
    assert tab.slide_font_size.maximum() == 140

    assert hasattr(tab, "weight_buttons"), "TabAssets must have weight_buttons segment"
    weight_texts = [b.text() for b in tab.weight_buttons.values()]
    for req_w in ["Thường", "Đậm", "Rất đậm"]:
        assert any(req_w in t for t in weight_texts), f"Weight segment must have {req_w}"

    assert hasattr(tab, "slide_stroke_width"), "TabAssets must have slide_stroke_width slider"

    # B. Color Swatches (Text, Highlight, Stroke, Box/Glow)
    assert hasattr(tab, "btn_color_text"), "Must have btn_color_text"
    assert hasattr(tab, "btn_color_highlight"), "Must have btn_color_highlight"
    assert hasattr(tab, "btn_color_stroke"), "Must have btn_color_stroke"
    assert hasattr(tab, "btn_color_box_glow"), "Must have btn_color_box_glow"

    # Thử đổi màu swatch
    tab._set_swatch_color("text", "#FF5500")
    assert tab.swatch_colors["text"] == "#FF5500"

    # C. Motion & Curves (Animation chips & Timing curves)
    assert hasattr(tab, "anim_chips"), "Must have anim_chips dictionary"
    for req_anim in ["pop", "bounce", "typewriter", "slide", "box_highlight", "glow", "static"]:
        assert req_anim in tab.anim_chips, f"Animation chip {req_anim} must exist"

    assert hasattr(tab, "curve_chips"), "Must have curve_chips dictionary"
    for req_curve in ["spring", "ease", "linear"]:
        assert req_curve in tab.curve_chips, f"Timing curve chip {req_curve} must exist"

    # D. Destination Track & Hints
    assert hasattr(tab, "combo_target_track"), "Must have combo_target_track"
    assert hasattr(tab, "lbl_track_hint"), "Must have lbl_track_hint for hints"

    # Khi chọn Visual Asset (Text) -> Track V2/V3
    text_preset = BUILTIN_PRESETS[0]
    tab._on_card_selected(text_preset.id)
    track_items_text = [tab.combo_target_track.itemText(i) for i in range(tab.combo_target_track.count())]
    assert any("V2" in t for t in track_items_text)

    # Khi chọn SFX -> Chuyển track Audio Track 2 và có gợi ý -12dB compensation hint
    sfx_asset = MOCKUP_SFX[0]
    tab._on_card_selected(sfx_asset.id)
    track_items_sfx = [tab.combo_target_track.itemText(i) for i in range(tab.combo_target_track.count())]
    assert any("A2" in t or "Audio" in t for t in track_items_sfx)
    assert "-12" in tab.lbl_track_hint.text()

    # E. Dọn dẹp timer khi chuyển qua lại SFX (_stop_inspector_sfx_play)
    tab._start_inspector_sfx_play()
    assert tab.sfx_waveform_canvas.is_playing is True
    assert tab.sfx_playback_timer.isActive() is True
    # Chọn lại text preset -> Phải dừng timer SFX
    tab._on_card_selected(text_preset.id)
    assert tab.sfx_waveform_canvas.is_playing is False
    assert tab.sfx_playback_timer.isActive() is False

    # -------------------------------------------------------------
    # 2. 4 Action Integrations:
    # -------------------------------------------------------------
    # Action 1: _insert_at_playhead() & btn_insert_title_playhead
    assert hasattr(tab, "_insert_at_playhead"), "TabAssets must have _insert_at_playhead"
    calls = []
    class DummyResolve:
        def insert_title_at_playhead(self, **kwargs):
            calls.append(("title", kwargs))
            return True
        def insert_sfx_to_track(self, **kwargs):
            calls.append(("sfx", kwargs))
            return True
        def apply_look_lut(self, **kwargs):
            calls.append(("lut", kwargs))
            return True

    monkeypatch.setattr("src.ui.tabs.tab_assets.ResolveAutomation", lambda: DummyResolve())
    tab.selected_asset = text_preset
    tab._insert_at_playhead()
    assert len(calls) == 1 and calls[0][0] == "title"

    tab.selected_asset = sfx_asset
    tab._insert_at_playhead()
    assert len(calls) == 2 and calls[1][0] == "sfx"

    # Action 2: _create_drag_mime_data() & btn_drag_davinci
    assert hasattr(tab, "btn_drag_davinci"), "TabAssets must have btn_drag_davinci button"
    assert hasattr(tab, "_create_drag_mime_data"), "TabAssets must have _create_drag_mime_data method"

    # Text -> .setting file
    mime_text = tab._create_drag_mime_data(text_preset)
    assert mime_text.hasUrls()
    path_text = mime_text.urls()[0].toLocalFile()
    assert path_text.endswith(".setting") and os.path.exists(path_text)

    # Transition -> .setting file
    trans_asset = BUILTIN_TRANSITIONS[0]
    mime_trans = tab._create_drag_mime_data(trans_asset)
    assert mime_trans.hasUrls()
    path_trans = mime_trans.urls()[0].toLocalFile()
    assert path_trans.endswith(".setting") and os.path.exists(path_trans)

    # SFX -> .wav file
    mime_sfx = tab._create_drag_mime_data(sfx_asset)
    assert mime_sfx.hasUrls()
    path_sfx = mime_sfx.urls()[0].toLocalFile()
    assert path_sfx.endswith(".wav")

    # LUT -> .cube file
    lut_asset = MOCKUP_LUTS[0]
    mime_lut = tab._create_drag_mime_data(lut_asset)
    assert mime_lut.hasUrls()
    path_lut = mime_lut.urls()[0].toLocalFile()
    assert path_lut.endswith(".cube")

    # Action 3: _get_current_fusion_macro_code() & btn_copy_fusion
    assert hasattr(tab, "_get_current_fusion_macro_code"), "TabAssets must have _get_current_fusion_macro_code"
    assert hasattr(tab, "btn_copy_fusion"), "TabAssets must have btn_copy_fusion"
    tab.selected_asset = text_preset
    macro_code = tab._get_current_fusion_macro_code()
    assert ("TextPlus" in macro_code or "MacroOperator" in macro_code or "Tools" in macro_code)

    tab.btn_copy_fusion.click()
    clipboard_text = QApplication.clipboard().text()
    assert clipboard_text == macro_code
    assert hasattr(tab, "lbl_toast"), "TabAssets must have notification toast label lbl_toast"
    assert tab.lbl_toast.isVisible()

    # Action 4: _install_to_fusion() & btn_install_presets
    assert hasattr(tab, "_install_to_fusion"), "TabAssets must have _install_to_fusion"
    assert hasattr(tab, "btn_install_presets"), "TabAssets must have btn_install_presets"
    installed_count, _ = tab._install_to_fusion()
    assert isinstance(installed_count, int)
    assert installed_count >= 0

    # Global constraints check on newly created inspector controls
    fractional_px_pattern = re.compile(r'\b\d+\.\d+px\b')
    insp_sheet = tab.inspector.styleSheet()
    assert len(fractional_px_pattern.findall(insp_sheet)) == 0, "No fractional px in inspector stylesheet"


