import os
import pytest
from src.core.text_preset import TextStylePreset, PresetManager, TextPreviewRenderer, hex_to_fcpxml_rgba
from src.core.fcpxml_generator import FCPXMLGenerator

def test_hex_to_fcpxml_rgba():
    assert hex_to_fcpxml_rgba("#FFFFFF") == "1 1 1 1"
    assert hex_to_fcpxml_rgba("#000000") == "0 0 0 1"
    assert hex_to_fcpxml_rgba("1 0.84 0 1") == "1 0.84 0 1"

def test_builtin_presets_and_manager(tmp_path):
    mgr = PresetManager(base_dir=str(tmp_path / "presets"))
    presets = mgr.list_presets()
    assert len(presets) >= 7
    
    preset_ids = [p.id for p in presets]
    assert "karaoke_pop" in preset_ids
    assert "bounce_word" in preset_ids
    assert "box_highlight" in preset_ids
    assert "glow_neon" in preset_ids
    assert "clean_outline" in preset_ids
    assert "gradient_fill" in preset_ids
    assert "slide_in" in preset_ids

def test_custom_preset_save_and_delete(tmp_path):
    mgr = PresetManager(base_dir=str(tmp_path / "presets"))
    custom_preset = TextStylePreset(
        id="my_podcast_style",
        name="My Podcast Style",
        font="Segoe UI",
        size=40,
        weight="normal",
        standard_color="#E0E0E0",
        highlight_color="#00E5FF",
        outline_color="#000000",
        outline_width=0.1,
        animation="static",
        timing_curve="linear"
    )
    saved_path = mgr.save_custom_preset(custom_preset)
    assert os.path.exists(saved_path)
    
    loaded = mgr.get_preset("my_podcast_style")
    assert loaded.name == "My Podcast Style"
    assert loaded.animation == "static"
    
    deleted = mgr.delete_custom_preset("my_podcast_style")
    assert deleted is True
    assert not os.path.exists(saved_path)

def test_fcpxml_with_text_preset(tmp_path):
    out_xml = os.path.join(tmp_path, "test_preset.fcpxml")
    subtitles = [
        {
            "start": 1.0,
            "end": 3.0,
            "text": "Xin chào DaVinci",
            "words": [
                {"word": "Xin", "start": 1.0, "end": 1.5},
                {"word": "chào", "start": 1.5, "end": 2.0},
                {"word": "DaVinci", "start": 2.0, "end": 3.0}
            ]
        }
    ]
    
    # Dùng preset id 'glow_neon'
    FCPXMLGenerator.generate_karaoke_fcpxml(
        subtitles=subtitles,
        output_path=out_xml,
        preset="glow_neon"
    )
    assert os.path.exists(out_xml)
    with open(out_xml, "r", encoding="utf-8") as f:
        content = f.read()
    assert "ts_highlight" in content
    assert "FFVideoFormat1080p" in content

def test_quick_preview_render(tmp_path):
    preview_png = os.path.join(tmp_path, "preview.png")
    mgr = PresetManager()
    preset = mgr.get_preset("karaoke_pop")
    
    res = TextPreviewRenderer.render_preview_to_file(
        preset=preset,
        output_image_path=preview_png,
        sample_words=["ResolveFlow", "Text+", "Preview"],
        active_index=1,
        aspect_ratio="16:9"
    )
    assert os.path.exists(res)
    assert os.path.getsize(res) > 0
