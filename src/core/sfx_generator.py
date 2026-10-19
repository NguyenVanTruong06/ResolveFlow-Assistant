"""
Bộ sinh âm thanh hiệu ứng (SFX Generator) chuẩn chuyên nghiệp cho ResolveFlow Assistant.
Tạo các tệp âm thanh 48000Hz, 16-bit PCM WAV (pcm_s16le) tương thích 100% với
DaVinci Resolve Studio & Free trên Linux và Windows.
Bao gồm bộ SFX chuẩn quốc tế (Whooshes, Impacts, UI Pops) và bộ SFX độc quyền cho nhà sáng tạo Việt Nam.
"""

import os
import wave
import struct
import math
import numpy as np
from typing import Dict, Callable

SAMPLE_RATE = 48000


def _save_pcm_wav(file_path: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """Ghi mảng numpy float (-1.0 đến 1.0) ra file WAV 16-bit PCM 48kHz mono."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    # Clip và chuyển thành int16
    samples = np.clip(samples, -1.0, 1.0)
    int16_samples = (samples * 32767.0).astype(np.int16)
    with wave.open(file_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16_samples.tobytes())


# ==============================================================================
# 1. WHOOSHES & TRANSITIONS (Chuyển cảnh)
# ==============================================================================

def synth_whoosh_fast_short() -> np.ndarray:
    """Vuốt nhanh (0.2s) cho Jump-cut hoặc Text pop."""
    dur = 0.20
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Filtered noise + frequency sweep
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(len(t))
    # Hann envelope
    env = 0.5 * (1.0 - np.cos(2.0 * np.pi * t / dur))
    # Sine sweep 300Hz -> 1600Hz -> 400Hz
    freq_sweep = 300.0 + 1300.0 * np.sin(np.pi * t / dur)
    phase = 2.0 * np.pi * np.cumsum(freq_sweep) / SAMPLE_RATE
    tone = np.sin(phase)
    return (0.6 * tone + 0.4 * noise) * env * 0.85


def synth_whoosh_deep_cinematic() -> np.ndarray:
    """Vuốt trầm điện ảnh (0.75s) cho B-Roll, Zoom, cảnh phong cảnh."""
    dur = 0.75
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(43)
    noise = rng.standard_normal(len(t)) * 0.35
    # Smooth asymmetrical envelope (rise fast, long decay)
    env = np.sin(np.pi * np.power(t / dur, 0.7)) ** 2
    # Sub bass sweep 45Hz -> 110Hz -> 35Hz
    freq = 45.0 + 65.0 * np.sin(np.pi * t / dur)
    sub = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    # Warm saturation
    combined = np.tanh(sub * 1.5 + noise) * env * 0.90
    return combined


def synth_whoosh_whip_pan() -> np.ndarray:
    """Vuốt gắt tốc độ cao (0.25s) kèm tiếng giật mạnh kiểu Whip Pan."""
    dur = 0.25
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(44)
    noise = rng.standard_normal(len(t))
    # Sharp exponential rise up to t=0.15 then instant cut
    env = np.where(t < 0.15, np.exp((t - 0.15) * 25.0), np.exp(-(t - 0.15) * 45.0))
    freq = 200.0 + 2800.0 * (t / dur) ** 2
    tone = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    return (0.7 * noise + 0.5 * tone) * env * 0.85


def synth_whoosh() -> np.ndarray:
    """Whoosh kinh điển chuẩn mực (0.45s)."""
    dur = 0.45
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(45)
    noise = rng.standard_normal(len(t)) * 0.5
    env = np.sin(np.pi * t / dur) ** 2
    freq = 150.0 + 750.0 * np.sin(np.pi * t / dur)
    tone = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    return (tone + noise) * env * 0.80


def synth_swoosh_sub() -> np.ndarray:
    """Swoosh sub-bass trầm sâu (0.60s)."""
    dur = 0.60
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    env = np.sin(np.pi * t / dur) ** 2
    freq = 60.0 + 120.0 * np.sin(np.pi * t / dur)
    sub = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    return sub * env * 0.92


# ==============================================================================
# 2. IMPACTS & HITS (Điểm nhấn / Hook)
# ==============================================================================

def synth_vine_boom_sub_drop() -> np.ndarray:
    """Sub-bass 808 trầm ấm nổ punchy kiểu Vine Boom (0.9s)."""
    dur = 0.90
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Initial punch: rapid pitch drop 140Hz -> 38Hz
    freq = 38.0 + 102.0 * np.exp(-t * 22.0)
    phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    sub = np.sin(phase)
    # Transient snap at start
    snap = np.exp(-t * 120.0) * np.sin(2.0 * np.pi * 320.0 * t)
    # Long 808 decay envelope
    env = np.exp(-t * 3.5)
    # Overdrive distortion saturation
    raw = (sub * 1.8 + snap * 0.8) * env
    return np.tanh(raw) * 0.95


def synth_cinematic_braam_hit() -> np.ndarray:
    """Tiếng nổ kèn đồng trailer điện ảnh (Braam) cho mở đầu Hook (1.4s)."""
    dur = 1.40
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Low brass saw wave approximation at 55Hz (A1) + harmonics
    saw1 = 2.0 * (t * 55.0 - np.floor(t * 55.0 + 0.5))
    saw2 = 2.0 * (t * 110.0 - np.floor(t * 110.0 + 0.5)) * 0.5
    saw3 = 2.0 * (t * 165.0 - np.floor(t * 165.0 + 0.5)) * 0.25
    brass = saw1 + saw2 + saw3
    # Heavy low-end impact hit at t=0
    impact = np.sin(2.0 * np.pi * 50.0 * t) * np.exp(-t * 12.0) * 1.5
    env = np.exp(-t * 2.2)
    # Soft clip overdrive
    return np.tanh((brass * 1.4 + impact) * env) * 0.92


def synth_glitch_digital_stutter() -> np.ndarray:
    """Tiếng nhiễu kỹ thuật số giật nhịp cho hiệu ứng RGB Glitch (0.4s)."""
    dur = 0.40
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(46)
    noise = rng.standard_normal(len(t))
    # 16-step rhythmic square pulse gate
    gate_pattern = np.array([1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 1])
    step_samples = len(t) // len(gate_pattern)
    gate = np.repeat(gate_pattern, step_samples)[:len(t)]
    # Carrier square wave 480Hz
    carrier = np.sign(np.sin(2.0 * np.pi * 480.0 * t))
    env = np.exp(-t * 3.0)
    return (carrier * 0.4 + noise * 0.6) * gate * env * 0.85


def synth_riser_tension_sweep() -> np.ndarray:
    """Âm thanh dồn dập tăng kịch tính trước đoạn cao trào (1.6s)."""
    dur = 1.60
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Exponential frequency sweep 80Hz -> 2600Hz
    freq = 80.0 * np.power(2600.0 / 80.0, t / dur)
    phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    carrier = np.sin(phase)
    # Accelerating tremolo flutter: 6Hz -> 24Hz
    tremolo_rate = 6.0 + 18.0 * (t / dur)
    tremolo = 0.5 * (1.0 + np.sin(2.0 * np.pi * tremolo_rate * t))
    # Volume rise
    env = (t / dur) ** 1.8
    # Add subtle white noise layer
    rng = np.random.default_rng(47)
    noise = rng.standard_normal(len(t)) * 0.25 * env
    return (carrier * tremolo + noise) * env * 0.90


def synth_punch_hit() -> np.ndarray:
    """Tiếng đấm / va chạm trầm mạnh mẽ (0.32s)."""
    dur = 0.32
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    freq = 40.0 + 160.0 * np.exp(-t * 25.0)
    sub = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    rng = np.random.default_rng(48)
    noise = rng.standard_normal(len(t)) * np.exp(-t * 60.0)
    env = np.exp(-t * 12.0)
    return np.tanh((sub * 1.5 + noise * 0.8) * env) * 0.90


# ==============================================================================
# 3. UI & TEXT POP (Hiệu ứng Phụ đề / Đồ họa)
# ==============================================================================

def synth_pop_bubble_clean() -> np.ndarray:
    """Tiếng nổ bong bóng mượt khi hiện chữ/sticker (0.08s)."""
    dur = 0.08
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    freq = 450.0 + 950.0 * np.exp(-t * 70.0)
    env = np.exp(-t * 55.0)
    return np.sin(2.0 * np.pi * freq * t) * env * 0.92


def synth_ding_bright_bell() -> np.ndarray:
    """Tiếng chuông sáng báo ý đúng / Highlight (0.85s)."""
    dur = 0.85
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Bell harmonics: fundamental 1760Hz (A6), 3520Hz, 5280Hz
    h1 = np.sin(2.0 * np.pi * 1760.0 * t) * np.exp(-t * 4.0)
    h2 = np.sin(2.0 * np.pi * 3520.0 * t) * np.exp(-t * 7.0) * 0.35
    h3 = np.sin(2.0 * np.pi * 5280.0 * t) * np.exp(-t * 11.0) * 0.15
    return (h1 + h2 + h3) * 0.85


def synth_error_buzzer_wrong() -> np.ndarray:
    """Tiếng báo sai / hài hước (0.45s) - 2 tiếng buzz ngắn."""
    dur = 0.45
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Dissonant square wave pair (145Hz + 155Hz)
    sq1 = np.sign(np.sin(2.0 * np.pi * 145.0 * t))
    sq2 = np.sign(np.sin(2.0 * np.pi * 155.0 * t)) * 0.5
    # Two bursts: 0.02 - 0.18s and 0.22 - 0.38s
    gate = np.where(((t >= 0.02) & (t <= 0.18)) | ((t >= 0.22) & (t <= 0.38)), 1.0, 0.0)
    return (sq1 + sq2) * gate * 0.65


def synth_camera_shutter_snap() -> np.ndarray:
    """Tiếng chụp ảnh màn hình / Freeze frame (0.24s)."""
    dur = 0.24
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(49)
    noise = rng.standard_normal(len(t))
    # Click 1 at t=0.02 (mirror up), Click 2 at t=0.10 (shutter curtain)
    click1 = noise * np.exp(-np.abs(t - 0.02) * 200.0) * 0.7
    click2 = noise * np.exp(-np.abs(t - 0.10) * 160.0) * 1.0
    snap = np.sin(2.0 * np.pi * 2200.0 * t) * np.exp(-np.abs(t - 0.10) * 140.0) * 0.4
    return (click1 + click2 + snap) * 0.85


def synth_keyboard_mech_type() -> np.ndarray:
    """Chuỗi tiếng gõ phím cơ cho chữ chạy Typewriter (0.60s - 4 phím)."""
    dur = 0.60
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(50)
    noise = rng.standard_normal(len(t))
    signal = np.zeros_like(t)
    key_times = [0.05, 0.18, 0.32, 0.47]
    for kt in key_times:
        diff = t - kt
        mask = diff >= 0
        click_click = noise * np.exp(-np.maximum(0, diff) * 220.0)
        clack_thud = np.sin(2.0 * np.pi * 380.0 * diff) * np.exp(-np.maximum(0, diff) * 60.0)
        signal += np.where(mask, click_click * 0.6 + clack_thud * 0.4, 0.0)
    return signal * 0.85


def synth_mouse_double_click() -> np.ndarray:
    """Tiếng click chuột sắc nét (0.16s - 2 clicks)."""
    dur = 0.16
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(51)
    noise = rng.standard_normal(len(t))
    c1 = noise * np.exp(-np.abs(t - 0.02) * 350.0) + np.sin(2.0 * np.pi * 3200.0 * t) * np.exp(-np.abs(t - 0.02) * 300.0) * 0.4
    c2 = noise * np.exp(-np.abs(t - 0.09) * 350.0) + np.sin(2.0 * np.pi * 3100.0 * t) * np.exp(-np.abs(t - 0.09) * 300.0) * 0.4
    return (c1 + c2) * 0.85


def synth_cash_register_ching() -> np.ndarray:
    """Tiếng tiền về / con số tăng trưởng (0.65s)."""
    dur = 0.65
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Bell chime 2489Hz + 4978Hz + metallic drawer thud
    bell1 = np.sin(2.0 * np.pi * 2489.0 * t) * np.exp(-t * 4.5)
    bell2 = np.sin(2.0 * np.pi * 4978.0 * t) * np.exp(-t * 8.0) * 0.4
    drawer = np.sin(2.0 * np.pi * 180.0 * t) * np.exp(-t * 25.0) * 0.6
    return (bell1 + bell2 + drawer) * 0.85


def synth_page_turn_swoosh() -> np.ndarray:
    """Tiếng lật trang tài liệu / sách nghiên cứu (0.35s)."""
    dur = 0.35
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(52)
    noise = rng.standard_normal(len(t))
    env = np.sin(np.pi * t / dur) ** 2 * np.exp(-t * 3.0)
    return noise * env * 0.70


def synth_notification_ping() -> np.ndarray:
    """Tiếng chuông thông báo điện thoại hiện đại (0.35s)."""
    dur = 0.35
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    ping = np.sin(2.0 * np.pi * 1318.5 * t) * np.exp(-t * 8.0)
    ping_harm = np.sin(2.0 * np.pi * 2637.0 * t) * np.exp(-t * 12.0) * 0.3
    return (ping + ping_harm) * 0.85


def synth_laser_blaster_zap() -> np.ndarray:
    """Tia laser khoa học viễn tưởng (0.22s)."""
    dur = 0.22
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    freq = 2400.0 * np.exp(-t * 18.0)
    tone = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    env = np.exp(-t * 14.0)
    return tone * env * 0.85


def synth_record_scratch_short() -> np.ndarray:
    """Tiếng cọ đĩa than DJ dừng nhạc đột ngột (0.35s)."""
    dur = 0.35
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(53)
    noise = rng.standard_normal(len(t))
    freq = np.maximum(50.0, 1800.0 * (1.0 - t / dur) ** 2)
    tone = np.sin(2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
    env = np.sin(np.pi * t / dur) ** 0.8
    return (tone * 0.6 + noise * 0.4) * env * 0.80


def synth_clock_ticking_loop() -> np.ndarray:
    """Tiếng tích tắc đồng hồ đếm ngược kịch tính (1.0s)."""
    dur = 1.00
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    tick1 = np.sin(2.0 * np.pi * 2800.0 * t) * np.exp(-np.abs(t - 0.15) * 280.0)
    tick2 = np.sin(2.0 * np.pi * 2200.0 * t) * np.exp(-np.abs(t - 0.65) * 280.0)
    return (tick1 + tick2) * 0.85


def synth_heartbeat_sub_pulse() -> np.ndarray:
    """Tiếng tim đập thình thịch căng thẳng (0.85s)."""
    dur = 0.85
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Thump 1 (Lub) at t=0.10, Thump 2 (Dub) at t=0.38
    thump1 = np.sin(2.0 * np.pi * 58.0 * (t - 0.10)) * np.exp(-np.abs(t - 0.10) * 35.0)
    thump2 = np.sin(2.0 * np.pi * 52.0 * (t - 0.38)) * np.exp(-np.abs(t - 0.38) * 35.0) * 0.8
    return (thump1 + thump2) * 0.90


def synth_airhorn_party_blast() -> np.ndarray:
    """Tiếng còi hơi Airhorn quẩy tiệc / Meme (0.65s)."""
    dur = 0.65
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # F#5 (740Hz), A5 (880Hz), C#6 (1108Hz)
    chord = (
        np.sign(np.sin(2.0 * np.pi * 740.0 * t)) * 0.4 +
        np.sign(np.sin(2.0 * np.pi * 880.0 * t)) * 0.4 +
        np.sign(np.sin(2.0 * np.pi * 1108.0 * t)) * 0.3
    )
    env = np.where(t < 0.50, 1.0, np.exp(-(t - 0.50) * 25.0))
    return chord * env * 0.70


def synth_cymbal_reverse_swell() -> np.ndarray:
    """Chũm chọe hút ngược chuyển tiếp (1.1s)."""
    dur = 1.10
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(54)
    noise = rng.standard_normal(len(t))
    env = np.where(t < 1.0, (t / 1.0) ** 2.5, np.exp(-(t - 1.0) * 80.0))
    return noise * env * 0.80


def synth_tada_chime_win() -> np.ndarray:
    """Âm thanh chiến thắng Tada Fanfare (0.85s)."""
    dur = 0.85
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Arpeggio C5 (523), E5 (659), G5 (784), C6 (1046)
    notes = [(0.0, 523.25), (0.12, 659.25), (0.24, 783.99), (0.36, 1046.50)]
    signal = np.zeros_like(t)
    for start_t, freq in notes:
        diff = t - start_t
        mask = diff >= 0
        tone = np.sin(2.0 * np.pi * freq * diff) * np.exp(-np.maximum(0, diff) * 4.5)
        signal += np.where(mask, tone, 0.0)
    return signal * 0.75


# ==============================================================================
# 4. VIETNAMESE CREATOR & EDITOR PACK (Độc quyền Việt Nam)
# ==============================================================================

def synth_de_keu_que_do() -> np.ndarray:
    """Tiếng dế kêu quê độ / im lặng lúng túng (1.4s)."""
    dur = 1.40
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    signal = np.zeros_like(t)
    # Rhythmic chirps at t=0.1, 0.22 (pair 1), 0.65, 0.77 (pair 2), 1.15 (pair 3)
    chirp_times = [0.10, 0.22, 0.65, 0.77, 1.15]
    for ct in chirp_times:
        diff = t - ct
        mask = (diff >= 0) & (diff < 0.08)
        # Fast trill at 4600Hz
        trill = np.sin(2.0 * np.pi * 4600.0 * diff) * np.sin(2.0 * np.pi * 80.0 * diff)
        env = np.sin(np.pi * np.clip(diff / 0.08, 0, 1))
        signal += np.where(mask, trill * env, 0.0)
    return signal * 0.85


def synth_gio_thoi_la_bay() -> np.ndarray:
    """Gió thổi hiu hắt lá bay xào xạc (1.6s)."""
    dur = 1.60
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(55)
    noise = rng.standard_normal(len(t))
    # Whistling frequency modulation 450Hz - 850Hz
    freq_mod = 550.0 + 200.0 * np.sin(2.0 * np.pi * 1.2 * t)
    whistle = np.sin(2.0 * np.pi * np.cumsum(freq_mod) / SAMPLE_RATE)
    env = np.sin(np.pi * t / dur) ** 2
    return (noise * 0.4 + whistle * 0.6) * env * 0.75


def synth_bonk_chao_go() -> np.ndarray:
    """Tiếng chảo gõ bonk hài hước (0.35s)."""
    dur = 0.35
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # Resonant metallic strike (860Hz, 1720Hz, 2450Hz)
    m1 = np.sin(2.0 * np.pi * 860.0 * t) * np.exp(-t * 18.0)
    m2 = np.sin(2.0 * np.pi * 1720.0 * t) * np.exp(-t * 28.0) * 0.5
    m3 = np.sin(2.0 * np.pi * 2450.0 * t) * np.exp(-t * 40.0) * 0.3
    # Sub resonance
    sub = np.sin(2.0 * np.pi * 280.0 * t) * np.exp(-t * 22.0) * 0.7
    return (m1 + m2 + m3 + sub) * 0.85


def synth_bleep_kiem_duyet_1000hz() -> np.ndarray:
    """Tiếng bíp kiểm duyệt chuẩn truyền hình 1000Hz (0.50s)."""
    dur = 0.50
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    tone = np.sin(2.0 * np.pi * 1000.0 * t)
    # Gentle fade in / out 5ms to avoid click
    fade_samples = int(SAMPLE_RATE * 0.005)
    env = np.ones_like(t)
    env[:fade_samples] = np.linspace(0, 1, fade_samples)
    env[-fade_samples:] = np.linspace(1, 0, fade_samples)
    return tone * env * 0.85


def synth_vo_tay_sitcom_vn() -> np.ndarray:
    """Tiếng vỗ tay rộn ràng khán giả trường quay (1.6s)."""
    dur = 1.60
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(56)
    signal = np.zeros_like(t)
    # Multiple overlapping clap bursts
    for _ in range(45):
        ct = rng.uniform(0.05, 1.25)
        diff = t - ct
        mask = (diff >= 0) & (diff < 0.04)
        clap = rng.standard_normal(len(t)) * np.exp(-np.maximum(0, diff) * 120.0)
        signal += np.where(mask, clap, 0.0)
    env = np.sin(np.pi * t / dur) ** 1.5
    return np.tanh(signal * 0.35) * env * 0.85


def synth_cuoi_khan_gia_haha() -> np.ndarray:
    """Tiếng cười sảng khoái sitcom (1.8s)."""
    dur = 1.80
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(57)
    signal = np.zeros_like(t)
    # Laugh bursts rhythmic pulses
    for i in range(7):
        burst_t = 0.10 + i * 0.22
        diff = t - burst_t
        mask = (diff >= 0) & (diff < 0.16)
        # Formant-like laugh chuckle at 320Hz + 640Hz
        formant = (
            np.sin(2.0 * np.pi * 320.0 * diff) * 0.6 +
            np.sin(2.0 * np.pi * 640.0 * diff) * 0.4 +
            rng.standard_normal(len(t)) * 0.2
        ) * np.sin(np.pi * np.clip(diff / 0.16, 0, 1))
        signal += np.where(mask, formant, 0.0)
    env = np.sin(np.pi * t / dur) ** 1.2
    return signal * env * 0.85


def synth_coi_xe_may_bip_bip() -> np.ndarray:
    """Tiếng còi xe máy bíp bíp đặc trưng phố xá Việt Nam (0.45s)."""
    dur = 0.45
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    signal = np.zeros_like(t)
    # 2 honks: 0.03 - 0.16s and 0.22 - 0.38s
    # Dual frequency 435Hz + 462Hz (creates characteristic metallic vehicle horn beat)
    mask1 = (t >= 0.03) & (t <= 0.16)
    mask2 = (t >= 0.22) & (t <= 0.38)
    horn = np.sin(2.0 * np.pi * 435.0 * t) * 0.55 + np.sin(2.0 * np.pi * 462.0 * t) * 0.45
    signal = np.where(mask1 | mask2, horn, 0.0)
    return signal * 0.85


def synth_trong_lan_tung_dinh() -> np.ndarray:
    """Nhịp trống lân Tùng Dinh Dinh Tùng ngày hội (1.4s)."""
    dur = 1.40
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    signal = np.zeros_like(t)

    # "Tùng 1" (Trống cái trầm ấm ở t=0.05)
    d1 = t - 0.05
    mask_tung1 = d1 >= 0
    tung1 = np.sin(2.0 * np.pi * (55.0 + 80.0 * np.exp(-np.maximum(0, d1) * 20.0)) * d1) * np.exp(-np.maximum(0, d1) * 6.0)
    signal += np.where(mask_tung1, tung1 * 0.85, 0.0)

    # "Dinh Dinh" (Chũm chọe / xập xõa ở t=0.42 và t=0.62)
    rng = np.random.default_rng(58)
    noise = rng.standard_normal(len(t))
    for dt in [0.42, 0.62]:
        d = t - dt
        mask_dinh = (d >= 0) & (d < 0.25)
        dinh = (noise * 0.6 + np.sin(2.0 * np.pi * 3400.0 * d) * 0.4) * np.exp(-np.maximum(0, d) * 16.0)
        signal += np.where(mask_dinh, dinh * 0.65, 0.0)

    # "Tùng 2" (Trống cái chốt hạ ở t=0.92)
    d2 = t - 0.92
    mask_tung2 = d2 >= 0
    tung2 = np.sin(2.0 * np.pi * (55.0 + 90.0 * np.exp(-np.maximum(0, d2) * 20.0)) * d2) * np.exp(-np.maximum(0, d2) * 5.0)
    signal += np.where(mask_tung2, tung2 * 0.90, 0.0)

    return signal * 0.90


def synth_tat_face_slap() -> np.ndarray:
    """Tiếng tát vào má hài hước chát chúa (0.28s)."""
    dur = 0.28
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    rng = np.random.default_rng(59)
    noise = rng.standard_normal(len(t))
    # Sharp crack + flesh impact
    crack = noise * np.exp(-t * 85.0)
    flesh = np.sin(2.0 * np.pi * 260.0 * t) * np.exp(-t * 25.0) * 0.7
    return (crack + flesh) * 0.88


def synth_mat_troi_chan_ly() -> np.ndarray:
    """Mặt trời chân lý chói qua tim - Kèn đồng uy nghiêm giác ngộ (1.4s)."""
    dur = 1.40
    t = np.linspace(0, dur, int(SAMPLE_RATE * dur), endpoint=False)
    # D Major Triad: D4 (293.66), F#4 (369.99), A4 (440.00), D5 (587.33)
    c1 = np.sin(2.0 * np.pi * 293.66 * t) * 0.35
    c2 = np.sin(2.0 * np.pi * 369.99 * t) * 0.30
    c3 = np.sin(2.0 * np.pi * 440.00 * t) * 0.25
    c4 = np.sin(2.0 * np.pi * 587.33 * t) * 0.20
    # Swell crescendo envelope
    env = np.where(t < 0.9, (t / 0.9) ** 1.5, np.exp(-(t - 0.9) * 4.0))
    # Sparkle shimmer on top
    shimmer = np.sin(2.0 * np.pi * 2349.32 * t) * np.exp(-t * 2.0) * 0.15
    return (c1 + c2 + c3 + c4 + shimmer) * env * 0.88


# ==============================================================================
# TỪ ĐIỂN ĐỊNH DANH SFX TOÀN BỘ
# ==============================================================================

SFX_REGISTRY: Dict[str, Callable[[], np.ndarray]] = {
    # 1. Whoosh & Transitions
    "whoosh_fast_short.wav": synth_whoosh_fast_short,
    "whoosh_deep_cinematic.wav": synth_whoosh_deep_cinematic,
    "whoosh_whip_pan.wav": synth_whoosh_whip_pan,
    "whoosh.wav": synth_whoosh,
    "swoosh_sub.wav": synth_swoosh_sub,

    # 2. Impacts & Hits
    "vine_boom_sub_drop.wav": synth_vine_boom_sub_drop,
    "cinematic_braam_hit.wav": synth_cinematic_braam_hit,
    "glitch_digital_stutter.wav": synth_glitch_digital_stutter,
    "riser_tension_sweep.wav": synth_riser_tension_sweep,
    "punch_hit.wav": synth_punch_hit,
    "riser.wav": synth_riser_tension_sweep,
    "glitch.wav": synth_glitch_digital_stutter,
    "vine_boom.wav": synth_vine_boom_sub_drop,

    # 3. UI & Text Pop
    "pop_bubble_clean.wav": synth_pop_bubble_clean,
    "pop.wav": synth_pop_bubble_clean,
    "ding_bright_bell.wav": synth_ding_bright_bell,
    "ding.wav": synth_ding_bright_bell,
    "error_buzzer_wrong.wav": synth_error_buzzer_wrong,
    "camera_shutter_snap.wav": synth_camera_shutter_snap,
    "camera_shutter.wav": synth_camera_shutter_snap,
    "keyboard_mech_type.wav": synth_keyboard_mech_type,
    "mouse_double_click.wav": synth_mouse_double_click,
    "click.wav": synth_mouse_double_click,
    "cash_register_ching.wav": synth_cash_register_ching,
    "page_turn_swoosh.wav": synth_page_turn_swoosh,
    "notification_ping.wav": synth_notification_ping,
    "laser_blaster_zap.wav": synth_laser_blaster_zap,
    "laser_pew.wav": synth_laser_blaster_zap,
    "record_scratch_short.wav": synth_record_scratch_short,
    "record_scratch.wav": synth_record_scratch_short,
    "clock_ticking_loop.wav": synth_clock_ticking_loop,
    "heartbeat_sub_pulse.wav": synth_heartbeat_sub_pulse,
    "airhorn_party_blast.wav": synth_airhorn_party_blast,
    "airhorn.wav": synth_airhorn_party_blast,
    "cymbal_reverse_swell.wav": synth_cymbal_reverse_swell,
    "tada_chime_win.wav": synth_tada_chime_win,
    "tada_fanfare.wav": synth_tada_chime_win,
    "drum_roll.wav": synth_vo_tay_sitcom_vn,
    "censor_beep.wav": synth_bleep_kiem_duyet_1000hz,
    "windows_error.wav": synth_error_buzzer_wrong,

    # 4. Vietnamese Creator & Editor Pack
    "de_keu_que_do.wav": synth_de_keu_que_do,
    "gio_thoi_la_bay.wav": synth_gio_thoi_la_bay,
    "bonk_chao_go.wav": synth_bonk_chao_go,
    "bleep_kiem_duyet_1000hz.wav": synth_bleep_kiem_duyet_1000hz,
    "vo_tay_sitcom_vn.wav": synth_vo_tay_sitcom_vn,
    "cuoi_khan_gia_haha.wav": synth_cuoi_khan_gia_haha,
    "coi_xe_may_bip_bip.wav": synth_coi_xe_may_bip_bip,
    "trong_lan_tung_dinh.wav": synth_trong_lan_tung_dinh,
    "tat_face_slap.wav": synth_tat_face_slap,
    "mat_troi_chan_ly.wav": synth_mat_troi_chan_ly,
}


def generate_sfx(filename: str, type: str = "pop"):
    """
    Hàm tương thích ngược với code cũ: sinh 1 file theo tên hoặc loại.
    """
    fn = os.path.basename(filename)
    if fn in SFX_REGISTRY:
        samples = SFX_REGISTRY[fn]()
    elif type in ("pop", "whoosh", "ding"):
        if type == "pop":
            samples = synth_pop_bubble_clean()
        elif type == "whoosh":
            samples = synth_whoosh()
        else:
            samples = synth_ding_bright_bell()
    else:
        samples = synth_pop_bubble_clean()

    _save_pcm_wav(filename, samples)


def generate_all_sfx(target_dir: str = "assets/sfx") -> int:
    """
    Sinh toàn bộ danh mục SFX chuyên nghiệp chuẩn 48000Hz 16-bit PCM WAV.
    Trả về số lượng file đã nạp.
    """
    os.makedirs(target_dir, exist_ok=True)
    count = 0
    for filename, synth_fn in SFX_REGISTRY.items():
        out_path = os.path.join(target_dir, filename)
        samples = synth_fn()
        _save_pcm_wav(out_path, samples)
        count += 1
    return count


if __name__ == "__main__":
    sfx_dir = os.path.join("assets", "sfx")
    total = generate_all_sfx(sfx_dir)
    print(f"✅ Đã sinh thành công {total} hiệu ứng âm thanh SFX chuẩn 48000Hz tại '{sfx_dir}'!")
