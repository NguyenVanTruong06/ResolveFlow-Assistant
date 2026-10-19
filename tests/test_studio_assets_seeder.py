"""
Bộ kiểm thử toàn diện cho Kho Đạo Cụ (Studio Assets Seeder, Generator & Packs).
Xác thực 100% độ chuẩn xác của SFX 48kHz, 16 Cinema 3D LUTs, BGM loops,
Overlays/Stickers transparent PNG, Text Presets FCPXML và Recipes.
"""

import os
import wave
import pytest
from PIL import Image

from src.core.asset_indexer import AssetIndexer
from src.core.lut_generator import LUT3DGenerator, BUILTIN_COLOR_LOOKS
from src.core.text_preset import PresetManager, BUILTIN_PRESETS
from src.core.recipe_manager import RecipeManager
from src.core.transition_preset import BUILTIN_TRANSITIONS
from src.core.sfx_generator import SFX_REGISTRY, generate_all_sfx, SAMPLE_RATE


def test_sfx_registry_and_48khz_spec():
    """Kiểm tra danh mục SFX có trên 35 hiệu ứng và chuẩn 48000Hz 16-bit PCM."""
    assert len(SFX_REGISTRY) >= 35

    # Kiểm tra các file quan trọng
    critical_sfx = [
        "whoosh_fast_short.wav",
        "whoosh_deep_cinematic.wav",
        "whoosh_whip_pan.wav",
        "vine_boom_sub_drop.wav",
        "cinematic_braam_hit.wav",
        "glitch_digital_stutter.wav",
        "riser_tension_sweep.wav",
        "pop_bubble_clean.wav",
        "ding_bright_bell.wav",
        "error_buzzer_wrong.wav",
        "camera_shutter_snap.wav",
        "keyboard_mech_type.wav",
        "mouse_double_click.wav",
        "cash_register_ching.wav",
        "page_turn_swoosh.wav",
        "notification_ping.wav",
        "laser_blaster_zap.wav",
        "record_scratch_short.wav",
        "clock_ticking_loop.wav",
        "heartbeat_sub_pulse.wav",
        "airhorn_party_blast.wav",
        "cymbal_reverse_swell.wav",
        "tada_chime_win.wav",
        # Vietnamese SFX
        "de_keu_que_do.wav",
        "gio_thoi_la_bay.wav",
        "bonk_chao_go.wav",
        "bleep_kiem_duyet_1000hz.wav",
        "vo_tay_sitcom_vn.wav",
        "cuoi_khan_gia_haha.wav",
        "coi_xe_may_bip_bip.wav",
        "trong_lan_tung_dinh.wav",
        "tat_face_slap.wav",
        "mat_troi_chan_ly.wav",
    ]
    for sfx_name in critical_sfx:
        assert sfx_name in SFX_REGISTRY, f"Thiếu hiệu ứng âm thanh quan trọng: {sfx_name}"

    # Kiểm tra file thực tế trên đĩa
    sfx_path = os.path.join("assets", "sfx", "whoosh_fast_short.wav")
    assert os.path.exists(sfx_path)
    with wave.open(sfx_path, "rb") as wf:
        assert wf.getframerate() == 48000, "SFX phải đúng chuẩn 48000Hz"
        assert wf.getsampwidth() == 2, "SFX phải đúng 16-bit PCM (2 bytes)"
        assert wf.getnchannels() == 1, "SFX mono"


def test_cinema_16_luts():
    """Kiểm tra đủ 16 phong cách màu sắc chuẩn ngành và sinh file .cube hợp lệ."""
    assert len(BUILTIN_COLOR_LOOKS) >= 16
    look_ids = {l.id for l in BUILTIN_COLOR_LOOKS}

    required_looks = [
        "clean_rec709",
        "cinematic_teal_orange",
        "vintage_film_kodak",
        "fuji_eterna",
        "cyberpunk_neon",
        "moody_dark",
        "golden_hour",
        "crisp_tech",
        "vintage_vhs",
        "high_contrast_bw",
        "emerald_matrix",
        "korean_pastel",
        "warm_vlog",
        "anime_vibrant",
        "bleach_bypass",
        "warm_nostalgia_vn",
    ]
    for rk in required_looks:
        assert rk in look_ids, f"Thiếu LUT: {rk}"

    # Kiểm tra file .cube trên đĩa
    fuji_file = os.path.join("assets", "luts", "ResolveFlow_Fuji_Eterna_500T.cube")
    assert os.path.exists(fuji_file)
    with open(fuji_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "LUT_3D_SIZE 33" in content
        assert "Fuji Eterna" in content


def test_bgm_tracks_4_moods():
    """Kiểm tra nhạc nền BGM 48kHz tồn tại đầy đủ ở 4 mood: chill_vlog, upbeat_trend, cinematic, funny."""
    music_dir = os.path.join("assets", "music")
    assert os.path.exists(music_dir)

    expected_tracks = [
        ("chill_vlog", "chill_lofi_sunset.wav"),
        ("chill_vlog", "podcast_minimal_pad.wav"),
        ("upbeat_trend", "upbeat_energetic_beat.wav"),
        ("upbeat_trend", "tech_review_pulse.wav"),
        ("cinematic", "cinematic_epic_rise.wav"),
        ("cinematic", "suspense_tension_drone.wav"),
        ("funny", "funny_comedy_bounce.wav"),
        ("funny", "meme_quirky_bassline.wav"),
    ]
    for mood, fn in expected_tracks:
        p = os.path.join(music_dir, mood, fn)
        assert os.path.exists(p), f"Thiếu nhạc nền: {mood}/{fn}"
        with wave.open(p, "rb") as wf:
            assert wf.getframerate() == 48000
            dur = wf.getnframes() / wf.getframerate()
            assert dur >= 10.0, f"Độ dài bài nhạc {fn} phải ít nhất 10 giây loop"


def test_overlays_and_stickers_png():
    """Kiểm tra 17 tệp đồ họa Overlay và Sticker trong suốt (Alpha RGBA)."""
    ov_dir = os.path.join("assets", "broll_memes", "overlays")
    assert os.path.exists(ov_dir)

    expected_graphics = [
        "cinematic_letterbox_235.png",
        "soft_dark_vignette.png",
        "rec_camcorder_frame.png",
        "subtle_grid_rule_of_thirds.png",
        "arrow_neon_point.png",
        "subscribe_pill.png",
        "warning_alert_badge.png",
        "check_verified_green.png",
        "fire_trending_badge.png",
        "question_hook_mark.png",
        "star_highlight_burst.png",
        "like_thumbs_up.png",
        "badge_top1_gold.png",
        "speech_bubble_blank.png",
        "vtv_cho_song_test_pattern.png",
        "dau_hoi_cham_xoay_dau.png",
        "khung_tin_nong_breaking_news.png",
    ]
    for fn in expected_graphics:
        p = os.path.join(ov_dir, fn)
        assert os.path.exists(p), f"Thiếu đồ họa overlay: {fn}"
        with Image.open(p) as img:
            assert img.mode == "RGBA", f"Tệp {fn} phải là định dạng RGBA trong suốt"
            assert img.size[0] >= 600 and img.size[1] >= 600, f"Kích thước {fn} phải đạt chuẩn tối thiểu"


def test_text_presets_loaded():
    """Kiểm tra nạp toàn bộ Presets kiểu chữ (cả Global và Việt Nam)."""
    mgr = PresetManager()
    presets = mgr.list_presets()
    assert len(presets) >= 20
    preset_ids = {p.id for p in presets}

    expected_pids = [
        "mrbeast_impact_yellow",
        "apple_keynote_clean",
        "true_crime_documentary",
        "comic_manga_burst",
        "luxury_gold_serif",
        "minimal_podcast_pill",
        "schannel_vlog_nhanh",
        "khoai_lang_thang_chua_lanh",
        "phim_truyen_hinh_vtv_retro",
        "giat_gan_showbiz_tiktok",
    ]
    for pid in expected_pids:
        assert pid in preset_ids, f"Thiếu preset kiểu chữ: {pid}"
        p = mgr.get_preset(pid)
        assert p is not None
        assert p.id == pid
        assert len(p.name) > 0


def test_recipes_loaded():
    """Kiểm tra nạp toàn bộ Recipes dựng phim (cả Global và Việt Nam)."""
    mgr = RecipeManager()
    recipes = mgr.list_recipes()
    assert len(recipes) >= 10
    recipe_ids = {r.id for r in recipes}

    expected_rids = [
        "tech_review_4k",
        "gaming_funny_moments",
        "educational_course_clean",
        "cinematic_travel_story",
        "vlog_chua_lanh_vietnam",
        "tiktok_review_cong_nghe_vn",
        "podcast_chuyen_doi_talkshow",
    ]
    for rid in expected_rids:
        assert rid in recipe_ids, f"Thiếu recipe dựng phim: {rid}"
        r = mgr.get_recipe(rid)
        assert r is not None
        assert r.id == rid


def test_asset_indexer_full_scan():
    """Kiểm tra AssetIndexer quét sạch sẽ và phân loại chính xác toàn bộ tài nguyên."""
    AssetIndexer.clear_cache()
    indexer = AssetIndexer("assets")
    assets = indexer.scan_all_assets(force_refresh=True)

    assert len(assets.get("sfx", [])) >= 35, "SFX phải >= 35 tệp"
    assert len(assets.get("luts", [])) >= 16, "LUTs phải >= 16 tệp"
    assert len(assets.get("bgm", [])) >= 8, "BGM phải >= 8 tệp"
    assert len(assets.get("overlays", [])) >= 15, "Overlays phải >= 15 tệp"
    assert len(assets.get("transitions", [])) >= 4, "Transitions phải >= 4 tệp"
