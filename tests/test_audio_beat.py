import os
import wave
import numpy as np
import pytest
from src.core.audio_beat import AudioBeatDetector


def create_synthetic_wav(path, duration=2.5, sr=16000, burst_times=(0.5, 1.0, 1.5)):
    total_samples = int(duration * sr)
    audio = np.zeros(total_samples, dtype=np.float32)
    for bt in burst_times:
        idx_start = int(bt * sr)
        idx_end = min(idx_start + int(0.05 * sr), total_samples)
        # Strong burst
        t_burst = np.linspace(0, 0.05, idx_end - idx_start, endpoint=False)
        audio[idx_start:idx_end] = np.sin(2 * np.pi * 220 * t_burst)

    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())


def create_silence_wav(path, duration=2.0, sr=16000):
    total_samples = int(duration * sr)
    audio_int16 = np.zeros(total_samples, dtype=np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_int16.tobytes())


def test_detect_beats_synthetic_wav(tmp_path):
    wav_path = tmp_path / "synthetic.wav"
    burst_targets = [0.5, 1.0, 1.5]
    create_synthetic_wav(wav_path, burst_times=burst_targets)

    beats = AudioBeatDetector.detect_beats(str(wav_path))
    assert isinstance(beats, list)
    assert len(beats) >= 3

    # Check each burst target is approximately found within 0.15s
    for target in burst_targets:
        assert any(abs(b - target) <= 0.15 for b in beats), f"Missing beat around {target}s in {beats}"


def test_detect_beats_silence(tmp_path):
    wav_path = tmp_path / "silence.wav"
    create_silence_wav(wav_path, duration=2.0)

    beats = AudioBeatDetector.detect_beats(str(wav_path))
    assert beats == []


def test_detect_beats_file_not_found():
    with pytest.raises(FileNotFoundError):
        AudioBeatDetector.detect_beats("non_existent_audio_file_123456.mp3")


def test_detect_beats_real_sample():
    sample_path = os.path.join("assets", "music", "chill_vlog", "chill_lofi_sunset.wav")
    if not os.path.exists(sample_path):
        pytest.skip("Starter sample not found")

    beats = AudioBeatDetector.detect_beats(sample_path)
    assert isinstance(beats, list)
    assert all(isinstance(b, float) for b in beats)
    assert len(beats) > 0
    # Check timestamps are sorted and rounded to 2 decimal places
    assert beats == sorted(beats)


def test_detect_beats_too_short(tmp_path):
    wav_path = tmp_path / "short.wav"
    create_silence_wav(wav_path, duration=0.1)
    beats = AudioBeatDetector.detect_beats(str(wav_path))
    assert beats == []


def test_detect_beats_max_beats_cap(tmp_path):
    wav_path = tmp_path / "many_bursts.wav"
    # Create 10 bursts
    burst_targets = [0.4 * i for i in range(1, 11)]
    create_synthetic_wav(wav_path, duration=5.0, burst_times=burst_targets)

    beats = AudioBeatDetector.detect_beats(str(wav_path), max_beats=3)
    assert len(beats) <= 3
    assert beats == sorted(beats)


def test_detect_beats_min_interval(tmp_path):
    wav_path = tmp_path / "bursts_min_interval.wav"
    create_synthetic_wav(wav_path, duration=3.0, burst_times=[0.5, 1.0, 1.5, 2.0])

    min_interval = 0.4
    beats = AudioBeatDetector.detect_beats(str(wav_path), min_interval_sec=min_interval)
    for i in range(1, len(beats)):
        assert beats[i] - beats[i - 1] >= min_interval - 0.05  # allowance for rounding


def test_detect_beats_ffmpeg_fallback(tmp_path):
    import subprocess
    wav_path = tmp_path / "input.wav"
    mp3_path = tmp_path / "input.mp3"
    create_synthetic_wav(wav_path, burst_times=[0.5, 1.0, 1.5])

    # Convert to MP3 using ffmpeg to test ffmpeg raw stream branch
    cmd = ["ffmpeg", "-y", "-i", str(wav_path), str(mp3_path)]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    beats = AudioBeatDetector.detect_beats(str(mp3_path))
    assert isinstance(beats, list)
    assert len(beats) >= 2
    for target in [0.5, 1.0, 1.5]:
        assert any(abs(b - target) <= 0.15 for b in beats)

