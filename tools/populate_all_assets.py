"""
Công cụ Tự động Sinh & Nạp Kho Đạo Cụ Toàn Diện (Studio Assets Seeder & Pack Generator)
ResolveFlow Assistant - DaVinci Resolve Studio & Free

Thực hiện:
1. Sinh 40+ hiệu ứng âm thanh SFX chuẩn 48000Hz PCM 16-bit WAV (Whooshes, Impacts, UI Pops, Vietnamese Creator SFX).
2. Sinh 16 Cinema 3D LUTs (.cube 33x33x33) chuẩn ngành DaVinci Resolve & đồng bộ vào thư mục Resolve LUT.
3. Sinh 8 bản nhạc nền BGM Loops (Chill Lofi, Minimal Pad, Upbeat Trend, Tech Review, Cinematic Rise, Suspense Drone, Funny Comedy, Quirky Bass).
4. Sinh 17 đồ họa Overlays & Stickers chất lượng cao chuẩn Alpha Transparent PNG (Letterbox 2.35:1, Dark Vignette, REC Camcorder, Rule of Thirds, 10 Creator Stickers, 3 Vietnamese Overlays).
5. Đồng bộ 10 Presets Kiểu Chữ Phụ Đề FCPXML (.json) & 7 Gói Quy Trình Dựng Phim Recipes (.json).
6. Tự động làm mới bộ nhớ đệm AssetIndexer.
"""

import os
import sys
import shutil
import wave
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# Thêm thư mục gốc vào sys.path để import các module core
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.core.asset_indexer import AssetIndexer
from src.core.lut_generator import LUT3DGenerator, BUILTIN_COLOR_LOOKS
from src.core.text_preset import PresetManager, BUILTIN_PRESETS
from src.core.recipe_manager import RecipeManager
from src.core.transition_preset import TransitionMacroGenerator, BUILTIN_TRANSITIONS
from src.core.sfx_generator import generate_all_sfx, SAMPLE_RATE


# ==============================================================================
# 1. HỖ TRỢ PHÔNG CHỮ & TIỆN ÍCH HÌNH ẢNH
# ==============================================================================

def get_system_font(size: int = 36, bold: bool = True):
    """Tìm phông chữ hệ thống hỗ trợ tốt tiếng Việt Unicode có dấu."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ==============================================================================
# 2. BỘ SINH NHẠC NỀN BGM LOOPS (48000Hz 16-bit PCM WAV)
# ==============================================================================

def _save_bgm_wav(file_path: str, samples: np.ndarray, sample_rate: int = 48000):
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    samples = np.clip(samples, -1.0, 1.0)
    int16_samples = (samples * 32767.0).astype(np.int16)
    with wave.open(file_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_samples.tobytes())


def generate_bgm_tracks(base_dir: str = "assets/music") -> int:
    """Sinh 8 bản nhạc nền BGM loops mẫu theo 4 moods: chill_vlog, upbeat_trend, cinematic, funny."""
    sr = 48000
    tracks_count = 0

    # 1. chill_vlog: chill_lofi_sunset.wav (85 BPM lofi chords, 16s loop)
    dur = 16.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    chord_len = (60.0 / 85.0) * 4.0
    chords_lofi = [
        [146.83, 185.00, 220.00, 277.18], # Dmaj7
        [123.47, 146.83, 185.00, 220.00], # Bm7
        [164.81, 196.00, 246.94, 293.66], # Em7
        [110.00, 138.59, 164.81, 220.00], # A7
    ]
    sig_lofi = np.zeros_like(t)
    for i, ch in enumerate(chords_lofi):
        st = i * chord_len
        mask = (t >= st) & (t < st + chord_len)
        lt = t[mask] - st
        ch_s = np.zeros_like(lt)
        for f in ch:
            ch_s += np.sin(2 * np.pi * f * lt) * 0.22 + np.sin(2 * np.pi * f * 2 * lt) * 0.04
        env = np.sin(np.pi * np.clip(lt / chord_len, 0, 1)) ** 0.6
        sig_lofi[mask] = ch_s * env
    # Soft vinyl crackle
    rng = np.random.default_rng(101)
    vinyl = rng.standard_normal(len(t)) * 0.015
    out_lofi = os.path.join(base_dir, "chill_vlog", "chill_lofi_sunset.wav")
    _save_bgm_wav(out_lofi, (sig_lofi + vinyl) * 0.65)
    tracks_count += 1

    # 2. chill_vlog: podcast_minimal_pad.wav (ambient talk pad at -24dB, 20s loop)
    dur = 20.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    # Warm pad chords F#m - D - A - E
    pad_chords = [
        [185.0, 220.0, 277.18],
        [146.83, 185.0, 220.0],
        [220.0, 277.18, 329.63],
        [164.81, 207.65, 246.94],
    ]
    sig_pad = np.zeros_like(t)
    seg_len = dur / len(pad_chords)
    for i, ch in enumerate(pad_chords):
        st = i * seg_len
        mask = (t >= st) & (t < st + seg_len)
        lt = t[mask] - st
        ch_s = np.zeros_like(lt)
        for f in ch:
            # Add slow LFO chorus
            lfo = 1.0 + 0.008 * np.sin(2 * np.pi * 0.8 * lt)
            ch_s += np.sin(2 * np.pi * (f * lfo) * lt) * 0.20
        env = np.sin(np.pi * np.clip(lt / seg_len, 0, 1)) ** 0.8
        sig_pad[mask] = ch_s * env
    out_pad = os.path.join(base_dir, "chill_vlog", "podcast_minimal_pad.wav")
    _save_bgm_wav(out_pad, sig_pad * 0.35) # Low background volume (-24dB equivalent)
    tracks_count += 1

    # 3. upbeat_trend: upbeat_energetic_beat.wav (120 BPM driving beat, 16s loop)
    dur = 16.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    beat_sec = 0.5 # 120 BPM
    sig_upbeat = np.zeros_like(t)
    # 4-on-the-floor kick + bass pluck
    for b in range(int(dur / beat_sec)):
        bt = b * beat_sec
        # Kick drum at every beat
        d_k = t - bt
        mask_k = (d_k >= 0) & (d_k < 0.20)
        kick = np.sin(2 * np.pi * (50.0 + 120.0 * np.exp(-d_k[mask_k] * 35.0)) * d_k[mask_k]) * np.exp(-d_k[mask_k] * 18.0)
        sig_upbeat[mask_k] += kick * 0.70
        # Hi-hat on offbeats
        ht = bt + 0.25
        d_h = t - ht
        mask_h = (d_h >= 0) & (d_h < 0.08)
        hihat = rng.standard_normal(np.count_nonzero(mask_h)) * np.exp(-d_h[mask_h] * 50.0)
        sig_upbeat[mask_h] += hihat * 0.25
    # Bassline synths
    bass_freqs = [110.0, 110.0, 130.81, 146.83]
    for i, bf in enumerate(bass_freqs * 8):
        st = i * 0.5
        mask = (t >= st) & (t < st + 0.45)
        lt = t[mask] - st
        sig_upbeat[mask] += np.sin(2 * np.pi * bf * lt) * np.exp(-lt * 4.0) * 0.40
    out_upbeat = os.path.join(base_dir, "upbeat_trend", "upbeat_energetic_beat.wav")
    _save_bgm_wav(out_upbeat, sig_upbeat * 0.60)
    tracks_count += 1

    # 4. upbeat_trend: tech_review_pulse.wav (120 BPM futuristic electronic, 16s loop)
    dur = 16.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    sig_pulse = np.zeros_like(t)
    # 16th note arp synth (C - E - G - B)
    arp = [261.63, 329.63, 392.00, 493.88]
    step_t = 0.125
    for s_idx in range(int(dur / step_t)):
        f = arp[s_idx % len(arp)]
        st = s_idx * step_t
        mask = (t >= st) & (t < st + step_t)
        lt = t[mask] - st
        sig_pulse[mask] += (np.sin(2 * np.pi * f * lt) * 0.35 + np.sin(2 * np.pi * f * 2 * lt) * 0.10) * np.exp(-lt * 15.0)
    # Sub drone 55Hz
    sig_pulse += np.sin(2 * np.pi * 55.0 * t) * 0.30
    out_pulse = os.path.join(base_dir, "upbeat_trend", "tech_review_pulse.wav")
    _save_bgm_wav(out_pulse, sig_pulse * 0.55)
    tracks_count += 1

    # 5. cinematic: cinematic_epic_rise.wav (crescendo strings + timpani, 20s loop)
    dur = 20.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    # Chords Dm -> Bb -> F -> C
    cin_chords = [
        [146.83, 174.61, 220.00],
        [116.54, 146.83, 174.61],
        [174.61, 220.00, 261.63],
        [130.81, 164.81, 196.00],
    ]
    sig_cin = np.zeros_like(t)
    c_step = dur / len(cin_chords)
    for i, ch in enumerate(cin_chords):
        st = i * c_step
        mask = (t >= st) & (t < st + c_step)
        lt = t[mask] - st
        ch_s = np.zeros_like(lt)
        for f in ch:
            ch_s += np.sin(2 * np.pi * f * lt) * 0.30 + np.sin(2 * np.pi * (f * 1.002) * lt) * 0.20
        # Crescendo towards end
        crescendo = 0.5 + 0.5 * (t[mask] / dur)
        sig_cin[mask] = ch_s * np.sin(np.pi * np.clip(lt / c_step, 0, 1)) ** 0.5 * crescendo
    out_cin = os.path.join(base_dir, "cinematic", "cinematic_epic_rise.wav")
    _save_bgm_wav(out_cin, sig_cin * 0.65)
    tracks_count += 1

    # 6. cinematic: suspense_tension_drone.wav (dark pulse drone, 20s loop)
    dur = 20.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    # 45Hz sub drone + eerie tritone at 63.6Hz with slow pulsating LFO
    lfo_drone = 0.5 * (1.0 + np.sin(2 * np.pi * 0.35 * t))
    drone = np.sin(2 * np.pi * 45.0 * t) * 0.50 + np.sin(2 * np.pi * 63.6 * t) * 0.25 * lfo_drone
    out_susp = os.path.join(base_dir, "cinematic", "suspense_tension_drone.wav")
    _save_bgm_wav(out_susp, drone * 0.55)
    tracks_count += 1

    # 7. funny: funny_comedy_bounce.wav (pizzicato playful bounce, 16s loop)
    dur = 16.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    # Bouncing notes in C major (C4, E4, G4, A4, G4, E4)
    pizz_notes = [261.63, 329.63, 392.00, 440.00, 392.00, 329.63]
    note_step = 0.35
    sig_funny = np.zeros_like(t)
    for n_idx in range(int(dur / note_step)):
        fn = pizz_notes[n_idx % len(pizz_notes)]
        st = n_idx * note_step
        mask = (t >= st) & (t < st + note_step)
        lt = t[mask] - st
        # Short plucky sound
        sig_funny[mask] += np.sin(2 * np.pi * fn * lt) * np.exp(-lt * 18.0) * 0.60
    out_funny = os.path.join(base_dir, "funny", "funny_comedy_bounce.wav")
    _save_bgm_wav(out_funny, sig_funny * 0.65)
    tracks_count += 1

    # 8. funny: meme_quirky_bassline.wav (upright walking bass groove, 16s loop)
    dur = 16.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    bass_walk = [65.41, 82.41, 98.00, 110.00, 116.54, 110.00, 98.00, 82.41]
    step_walk = 0.50
    sig_walk = np.zeros_like(t)
    for w_idx in range(int(dur / step_walk)):
        bw = bass_walk[w_idx % len(bass_walk)]
        st = w_idx * step_walk
        mask = (t >= st) & (t < st + step_walk)
        lt = t[mask] - st
        sig_walk[mask] += (np.sin(2 * np.pi * bw * lt) + np.sin(2 * np.pi * bw * 2 * lt) * 0.4) * np.exp(-lt * 8.0) * 0.60
    out_walk = os.path.join(base_dir, "funny", "meme_quirky_bassline.wav")
    _save_bgm_wav(out_walk, sig_walk * 0.65)
    tracks_count += 1

    return tracks_count


# ==============================================================================
# 3. BỘ SINH OVERLAYS & STICKERS (Transparent PNG)
# ==============================================================================

def generate_overlays_and_stickers(target_dir: str = "assets/broll_memes/overlays") -> int:
    """Sinh toàn bộ 17 tệp đồ họa Overlay và Sticker chuẩn Alpha Transparent PNG."""
    os.makedirs(target_dir, exist_ok=True)
    count = 0

    font_title = get_system_font(42, bold=True)
    font_sub = get_system_font(26, bold=True)
    font_mono = get_system_font(30, bold=True)

    # 1. cinematic_letterbox_235.png (1920x1080)
    w, h = 1920, 1080
    img_lb = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d_lb = ImageDraw.Draw(img_lb)
    bar_h = (h - int(w / 2.35)) // 2 # ~131 px
    d_lb.rectangle([0, 0, w, bar_h], fill=(0, 0, 0, 255))
    d_lb.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0, 255))
    img_lb.save(os.path.join(target_dir, "cinematic_letterbox_235.png"))
    count += 1

    # 2. soft_dark_vignette.png (1920x1080)
    y_coords, x_coords = np.ogrid[:h, :w]
    cx, cy = w / 2.0, h / 2.0
    dist = np.sqrt(((x_coords - cx) / cx) ** 2 + ((y_coords - cy) / cy) ** 2)
    # Smoothstep dark border
    alpha = np.clip((dist - 0.40) / 0.85, 0.0, 1.0) * 215.0
    vig_arr = np.zeros((h, w, 4), dtype=np.uint8)
    vig_arr[..., 3] = alpha.astype(np.uint8)
    Image.fromarray(vig_arr, mode="RGBA").save(os.path.join(target_dir, "soft_dark_vignette.png"))
    count += 1

    # 3. rec_camcorder_frame.png (1920x1080)
    img_rec = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d_rec = ImageDraw.Draw(img_rec)
    bracket_col = (255, 255, 255, 220)
    bw_w = 4
    # 4 Corner brackets
    # Top Left
    d_rec.line([(70, 70), (160, 70)], fill=bracket_col, width=bw_w)
    d_rec.line([(70, 70), (70, 160)], fill=bracket_col, width=bw_w)
    # Top Right
    d_rec.line([(w - 160, 70), (w - 70, 70)], fill=bracket_col, width=bw_w)
    d_rec.line([(w - 70, 70), (w - 70, 160)], fill=bracket_col, width=bw_w)
    # Bottom Left
    d_rec.line([(70, h - 70), (160, h - 70)], fill=bracket_col, width=bw_w)
    d_rec.line([(70, h - 160), (70, h - 70)], fill=bracket_col, width=bw_w)
    # Bottom Right
    d_rec.line([(w - 160, h - 70), (w - 70, h - 70)], fill=bracket_col, width=bw_w)
    d_rec.line([(w - 70, h - 160), (w - 70, h - 70)], fill=bracket_col, width=bw_w)
    # REC Dot & Text
    d_rec.ellipse([110, 105, 138, 133], fill=(239, 68, 68, 255))
    d_rec.text((150, 102), "REC [STBY]", font=font_mono, fill=(255, 255, 255, 240))
    # Battery icon top-right
    d_rec.rectangle([w - 220, 105, w - 120, 135], outline=bracket_col, width=3)
    d_rec.rectangle([w - 120, 113, w - 113, 127], fill=bracket_col)
    d_rec.rectangle([w - 215, 110, w - 145, 130], fill=(34, 197, 94, 255)) # Green battery bar
    # Bottom HUD
    d_rec.text((110, h - 140), "TC 00:14:28:12", font=font_mono, fill=(255, 255, 255, 230))
    d_rec.text((w - 330, h - 140), "4K UHD 60FPS", font=font_mono, fill=(255, 255, 255, 230))
    img_rec.save(os.path.join(target_dir, "rec_camcorder_frame.png"))
    count += 1

    # 4. subtle_grid_rule_of_thirds.png (1920x1080)
    img_grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d_grid = ImageDraw.Draw(img_grid)
    grid_col = (255, 255, 255, 60)
    d_grid.line([(640, 0), (640, h)], fill=grid_col, width=1)
    d_grid.line([(1280, 0), (1280, h)], fill=grid_col, width=1)
    d_grid.line([(0, 360), (w, 360)], fill=grid_col, width=1)
    d_grid.line([(0, 720), (w, 720)], fill=grid_col, width=1)
    img_grid.save(os.path.join(target_dir, "subtle_grid_rule_of_thirds.png"))
    count += 1

    # --- 10 Creator Stickers/Badges (800x800) ---
    s_size = 800

    # 5. arrow_neon_point.png
    img_arrow = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_ar = ImageDraw.Draw(img_arrow)
    # Glow outline
    arrow_pts = [(150, 340), (480, 340), (480, 220), (700, 400), (480, 580), (480, 460), (150, 460)]
    d_ar.polygon(arrow_pts, fill=(6, 182, 212, 255), outline=(255, 255, 255, 255))
    img_arrow.save(os.path.join(target_dir, "arrow_neon_point.png"))
    count += 1

    # 6. subscribe_pill.png
    img_sub = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_sub = ImageDraw.Draw(img_sub)
    d_sub.rounded_rectangle([100, 280, 700, 520], radius=120, fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=6)
    # White Play Triangle
    d_sub.polygon([(200, 350), (200, 450), (290, 400)], fill=(255, 255, 255, 255))
    font_sub_btn = get_system_font(52, bold=True)
    d_sub.text((320, 365), "SUBSCRIBE", font=font_sub_btn, fill=(255, 255, 255, 255))
    img_sub.save(os.path.join(target_dir, "subscribe_pill.png"))
    count += 1

    # 7. warning_alert_badge.png
    img_warn = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_warn = ImageDraw.Draw(img_warn)
    tri_pts = [(400, 120), (120, 660), (680, 660)]
    d_warn.polygon(tri_pts, fill=(250, 204, 21, 255), outline=(15, 23, 42, 255))
    # Exclamation mark
    d_warn.rounded_rectangle([375, 280, 425, 500], radius=15, fill=(15, 23, 42, 255))
    d_warn.ellipse([375, 540, 425, 590], fill=(15, 23, 42, 255))
    img_warn.save(os.path.join(target_dir, "warning_alert_badge.png"))
    count += 1

    # 8. check_verified_green.png
    img_chk = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_chk = ImageDraw.Draw(img_chk)
    d_chk.ellipse([120, 120, 680, 680], fill=(34, 197, 94, 255), outline=(22, 163, 74, 255), width=10)
    d_chk.line([(260, 420), (360, 520), (550, 290)], fill=(255, 255, 255, 255), width=50)
    img_chk.save(os.path.join(target_dir, "check_verified_green.png"))
    count += 1

    # 9. fire_trending_badge.png
    img_fire = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_fire = ImageDraw.Draw(img_fire)
    d_fire.ellipse([150, 200, 650, 700], fill=(239, 68, 68, 255))
    d_fire.polygon([(400, 80), (550, 360), (650, 500), (150, 500), (250, 360)], fill=(249, 115, 22, 255))
    d_fire.ellipse([260, 380, 540, 660], fill=(250, 204, 21, 255))
    img_fire.save(os.path.join(target_dir, "fire_trending_badge.png"))
    count += 1

    # 10. question_hook_mark.png
    img_q = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_q = ImageDraw.Draw(img_q)
    font_huge_q = get_system_font(380, bold=True)
    d_q.text((290, 120), "?", font=font_huge_q, fill=(255, 230, 0, 255), stroke_width=20, stroke_fill=(15, 23, 42, 255))
    img_q.save(os.path.join(target_dir, "question_hook_mark.png"))
    count += 1

    # 11. star_highlight_burst.png
    img_star = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_star = ImageDraw.Draw(img_star)
    star_pts = [
        (400, 100), (450, 350), (700, 400), (450, 450),
        (400, 700), (350, 450), (100, 400), (350, 350)
    ]
    d_star.polygon(star_pts, fill=(250, 204, 21, 255), outline=(255, 255, 255, 255), width=8)
    img_star.save(os.path.join(target_dir, "star_highlight_burst.png"))
    count += 1

    # 12. like_thumbs_up.png
    img_like = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_like = ImageDraw.Draw(img_like)
    d_like.ellipse([120, 120, 680, 680], fill=(37, 99, 235, 255), outline=(255, 255, 255, 255), width=10)
    font_like_emoji = get_system_font(260, bold=True)
    d_like.text((270, 210), "👍", font=font_like_emoji, fill=(255, 255, 255, 255))
    img_like.save(os.path.join(target_dir, "like_thumbs_up.png"))
    count += 1

    # 13. badge_top1_gold.png
    img_top1 = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_top1 = ImageDraw.Draw(img_top1)
    # Ribbons
    d_top1.polygon([(280, 450), (220, 700), (340, 640), (400, 700)], fill=(220, 38, 38, 255))
    d_top1.polygon([(520, 450), (580, 700), (460, 640), (400, 700)], fill=(185, 28, 28, 255))
    # Medal circle
    d_top1.ellipse([180, 140, 620, 580], fill=(245, 158, 11, 255), outline=(254, 240, 138, 255), width=14)
    font_top1_txt = get_system_font(160, bold=True)
    d_top1.text((285, 240), "#1", font=font_top1_txt, fill=(255, 255, 255, 255))
    img_top1.save(os.path.join(target_dir, "badge_top1_gold.png"))
    count += 1

    # 14. speech_bubble_blank.png
    img_bub = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_bub = ImageDraw.Draw(img_bub)
    d_bub.rounded_rectangle([100, 160, 700, 580], radius=80, fill=(255, 255, 255, 255), outline=(15, 23, 42, 255), width=12)
    # Tail
    d_bub.polygon([(260, 570), (220, 680), (360, 570)], fill=(255, 255, 255, 255), outline=(15, 23, 42, 255))
    d_bub.line([(265, 570), (355, 570)], fill=(255, 255, 255, 255), width=14)
    img_bub.save(os.path.join(target_dir, "speech_bubble_blank.png"))
    count += 1

    # --- 3 Vietnamese Overlays ---
    # 15. vtv_cho_song_test_pattern.png (1920x1080)
    img_vtv = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    d_vtv = ImageDraw.Draw(img_vtv)
    smpte_colors = [
        (235, 235, 235), # White
        (235, 235, 0),   # Yellow
        (0, 235, 235),   # Cyan
        (0, 235, 0),     # Green
        (235, 0, 235),   # Magenta
        (235, 0, 0),     # Red
        (0, 0, 235),     # Blue
    ]
    col_w = w // len(smpte_colors)
    for idx, c in enumerate(smpte_colors):
        d_vtv.rectangle([idx * col_w, 0, (idx + 1) * col_w, int(h * 0.75)], fill=(*c, 255))
    # Bottom black box with header
    d_vtv.rectangle([0, int(h * 0.75), w, h], fill=(15, 23, 42, 255))
    d_vtv.text((w // 2 - 380, int(h * 0.82)), "VTV - ĐÀI TRUYỀN HÌNH VIỆT NAM", font=font_title, fill=(255, 255, 255, 255))
    d_vtv.text((w // 2 - 180, int(h * 0.90)), "CHỜ PHÁT SÓNG", font=font_sub, fill=(250, 204, 21, 255))
    img_vtv.save(os.path.join(target_dir, "vtv_cho_song_test_pattern.png"))
    count += 1

    # 16. dau_hoi_cham_xoay_dau.png (800x800)
    img_conf = Image.new("RGBA", (s_size, s_size), (0, 0, 0, 0))
    d_conf = ImageDraw.Draw(img_conf)
    font_big_q = get_system_font(180, bold=True)
    d_conf.text((120, 220), "?", font=font_big_q, fill=(250, 204, 21, 255), stroke_width=8, stroke_fill=(15, 23, 42, 255))
    d_conf.text((340, 100), "?", font=font_big_q, fill=(239, 68, 68, 255), stroke_width=8, stroke_fill=(15, 23, 42, 255))
    d_conf.text((540, 240), "?", font=font_big_q, fill=(6, 182, 212, 255), stroke_width=8, stroke_fill=(15, 23, 42, 255))
    # Confusion swirl lines
    d_conf.arc([250, 480, 550, 680], start=30, end=290, fill=(255, 255, 255, 220), width=6)
    img_conf.save(os.path.join(target_dir, "dau_hoi_cham_xoay_dau.png"))
    count += 1

    # 17. khung_tin_nong_breaking_news.png (1920x1080)
    img_news = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d_news = ImageDraw.Draw(img_news)
    # Red breaking news bar
    d_news.rectangle([80, 850, 620, 920], fill=(220, 38, 38, 255))
    d_news.text((110, 865), "● TIN NÓNG ĐẶC BIỆT", font=font_sub, fill=(255, 255, 255, 255))
    # Lower dark banner
    d_news.rectangle([80, 920, 1840, 990], fill=(15, 23, 42, 245), outline=(220, 38, 38, 255), width=2)
    d_news.text((110, 935), "TRỰC TIẾP TỪ TRƯỜNG QUAY: DIỄN BIẾN MỚI NHẤT TRONG NGÀY", font=font_sub, fill=(254, 240, 138, 255))
    img_news.save(os.path.join(target_dir, "khung_tin_nong_breaking_news.png"))
    count += 1

    # Tạo symlink hoặc sao chép thư mục assets/overlays nếu cần
    alt_overlays_dir = os.path.join(ROOT_DIR, "assets", "overlays")
    os.makedirs(alt_overlays_dir, exist_ok=True)
    for fname in os.listdir(target_dir):
        if fname.endswith(".png"):
            src_f = os.path.join(target_dir, fname)
            dst_f = os.path.join(alt_overlays_dir, fname)
            try:
                shutil.copy2(src_f, dst_f)
            except Exception:
                pass

    return count


# ==============================================================================
# 4. CHƯƠNG TRÌNH THỰC THI CHÍNH TOÀN DIỆN
# ==============================================================================

def populate_all():
    print("=" * 70)
    print("🚀 BẮT ĐẦU NẠP KHO ĐẠO CỤ TOÀN DIỆN CHO RESOLVEFLOW ASSISTANT")
    print("=" * 70)

    # 1. SFX Generator
    print("\n[1/6] 🔊 Đang tổng hợp 40+ hiệu ứng âm thanh SFX (48000Hz 16-bit PCM WAV)...")
    sfx_dir = os.path.join(ROOT_DIR, "assets", "sfx")
    sfx_count = generate_all_sfx(sfx_dir)
    print(f"   -> Đã tạo {sfx_count} tệp SFX tại '{sfx_dir}'.")

    # 2. 3D LUTs Generator
    print("\n[2/6] 🎨 Đang tính toán 16 Cinema 3D LUTs (.cube 33x33x33)...")
    lut_dir = os.path.join(ROOT_DIR, "assets", "luts")
    lut_count = LUT3DGenerator.ensure_all_luts_exist(lut_dir)
    print(f"   -> Đã đồng bộ 16 Cinema 3D LUTs tại '{lut_dir}' (tạo mới: {lut_count}).")
    # Tự động sao chép vào DaVinci Resolve nếu thư mục hệ thống có sẵn
    resolve_lut_dir = "/opt/resolve/LUT/ResolveFlow"
    if os.path.exists(resolve_lut_dir) and os.access(resolve_lut_dir, os.W_OK):
        for f in os.listdir(lut_dir):
            if f.endswith(".cube"):
                shutil.copy2(os.path.join(lut_dir, f), os.path.join(resolve_lut_dir, f))
        print(f"   -> Đã đồng bộ toàn bộ LUTs vào DaVinci Resolve: '{resolve_lut_dir}'.")

    # 3. Transitions Setting Generator
    print("\n[3/6] ⚡ Đang sinh các Fusion Macro Chuyển Cảnh (.setting)...")
    trans_dir = os.path.join(ROOT_DIR, "assets", "templates", "transitions")
    trans_count, _ = TransitionMacroGenerator.install_transitions_to_davinci_resolve(target_dir=trans_dir)
    print(f"   -> Đã tạo {trans_count} tệp chuyển cảnh tại '{trans_dir}'.")

    # 4. BGM Loops Generator
    print("\n[4/6] 🎵 Đang sinh 8 bản nhạc nền BGM Loops (48000Hz WAV)...")
    music_dir = os.path.join(ROOT_DIR, "assets", "music")
    bgm_count = generate_bgm_tracks(music_dir)
    print(f"   -> Đã tạo {bgm_count} bản nhạc nền tại '{music_dir}'.")

    # 5. Overlays & Stickers Generator
    print("\n[5/6] 🖼️  Đang vẽ 17 đồ họa Overlays & Stickers (Alpha Transparent PNG)...")
    overlays_dir = os.path.join(ROOT_DIR, "assets", "broll_memes", "overlays")
    overlay_count = generate_overlays_and_stickers(overlays_dir)
    print(f"   -> Đã tạo {overlay_count} tệp overlay/sticker tại '{overlays_dir}'.")

    # 6. Presets & Recipes
    print("\n[6/6] 📋 Đang nạp Presets Kiểu Chữ Phụ Đề & Recipes Workflow...")
    PresetManager().ensure_default_presets()
    RecipeManager().ensure_default_recipes()
    print("   -> Đã nạp đầy đủ Presets & Recipes.")

    # 7. Làm mới bộ nhớ đệm AssetIndexer
    print("\n🔄 Đang làm mới bộ nhớ đệm AssetIndexer toàn cục...")
    AssetIndexer.clear_cache()
    indexer = AssetIndexer(os.path.join(ROOT_DIR, "assets"))
    assets = indexer.scan_all_assets(force_refresh=True)

    print("\n" + "=" * 70)
    print("🎉 TỔNG KẾT TÀI NGUYÊN KHO ĐẠO CỤ (STUDIO ASSETS SUMMARY)")
    print("=" * 70)
    print(f"  • Âm thanh hiệu ứng (SFX):          {len(assets.get('sfx', []))} tệp")
    print(f"  • Bảng màu điện ảnh (3D LUTs):      {len(assets.get('luts', []))} tệp")
    print(f"  • Nhạc nền (BGM Tracks):            {len(assets.get('bgm', []))} tệp")
    print(f"  • Đồ họa phủ (Overlays/Stickers):   {len(assets.get('overlays', []))} tệp")
    print(f"  • Memes & B-Roll hài hước:          {len(assets.get('memes', []))} tệp")
    print(f"  • Chuyển cảnh (Transitions):        {len(assets.get('transitions', []))} tệp")
    print(f"  • Tiêu đề mẫu (Titles):             {len(assets.get('titles', []))} tệp")
    print("=" * 70)
    print("✅ HOÀN TẤT NẠP TOÀN BỘ KHO ĐẠO CỤ SẴN SÀNG SỬ DỤNG TRÊN DAVINCI RESOLVE!\n")


if __name__ == "__main__":
    populate_all()
