import os
import subprocess
import wave
from typing import List
import numpy as np


class AudioBeatDetector:
    """
    Phân tích âm thanh để lấy các mốc nhịp (beat timestamps)
    phục vụ cho tính năng Auto Beat-Sync (Cắt B-Roll theo nhạc).
    Sử dụng NumPy + FFmpeg/Wave tốc độ cao không phụ thuộc librosa.
    """

    @staticmethod
    def detect_beats(audio_path: str, max_beats: int = 60, min_interval_sec: float = 0.3) -> List[float]:
        """
        Quét file nhạc và trả về danh sách các mốc thời gian (giây) của beat drops.

        Args:
            audio_path: Đường dẫn tới file âm thanh.
            max_beats: Giới hạn số lượng beat tối đa.
            min_interval_sec: Khoảng cách thời gian tối thiểu giữa 2 beat liền kề.

        Returns:
            List[float]: Danh sách timestamp (giây) đã được làm tròn 2 chữ số thập phân.
        """
        norm_path = os.path.normpath(os.path.abspath(audio_path))
        if not os.path.exists(norm_path):
            raise FileNotFoundError(f"Không tìm thấy file nhạc: {norm_path}")

        # Tùy chọn: Thử dùng librosa nếu môi trường đã cài đặt
        try:
            import librosa
            y, sr = librosa.load(norm_path, sr=22050)
            if len(y) < int(0.2 * sr) or float(np.max(np.abs(y))) < 1e-4:
                return []
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            beat_times = librosa.frames_to_time(beat_frames, sr=sr)
            selected = []
            for b in beat_times:
                t = round(float(b), 2)
                if not selected or (t - selected[-1] >= min_interval_sec):
                    selected.append(t)
            return selected[:max_beats]
        except (ImportError, Exception):
            pass

        # Primary Engine: NumPy + FFmpeg / Wave
        audio = None
        sr = 16000

        # Nếu là file WAV PCM: đọc trực tiếp bằng module wave
        if norm_path.lower().endswith(".wav"):
            try:
                with wave.open(norm_path, "rb") as wf:
                    sr = wf.getframerate()
                    n_channels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    frames = wf.readframes(wf.getnframes())
                    if sampwidth == 2:
                        raw = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                    elif sampwidth == 1:
                        raw = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                    elif sampwidth == 4:
                        raw = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
                    else:
                        raw = None

                    if raw is not None and len(raw) > 0:
                        if n_channels > 1:
                            raw = raw.reshape(-1, n_channels).mean(axis=1)
                        audio = raw
            except Exception:
                audio = None

        # Nếu không phải WAV hoặc wave đọc lỗi: dùng FFmpeg streaming f32le
        if audio is None:
            try:
                cmd = [
                    "ffmpeg", "-y", "-i", norm_path,
                    "-f", "f32le", "-ac", "1", "-ar", "16000",
                    "-"
                ]
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True
                )
                audio = np.frombuffer(proc.stdout, dtype=np.float32)
                sr = 16000
            except Exception:
                return []

        if audio is None or len(audio) == 0:
            return []

        # Kiểm tra độ dài tối thiểu (< 0.2s) hoặc im lặng hoàn toàn
        if len(audio) < int(0.2 * sr) or float(np.max(np.abs(audio))) < 1e-4:
            return []

        frame_size = 1024
        hop_size = 512

        if len(audio) < frame_size + hop_size:
            return []

        num_frames = 1 + (len(audio) - frame_size) // hop_size
        if num_frames <= 1:
            return []

        frames_matrix = np.lib.stride_tricks.sliding_window_view(
            audio[:(num_frames - 1) * hop_size + frame_size],
            window_shape=frame_size
        )[::hop_size]

        # Short-time squared energy
        energy = np.mean(frames_matrix ** 2, axis=1)

        # Onset flux (first difference)
        diff = np.maximum(0.0, energy[1:] - energy[:-1])
        if len(diff) == 0 or float(np.max(diff)) < 1e-6:
            return []

        # Rolling mean and standard deviation
        window = min(31, len(diff))
        if window % 2 == 0:
            window += 1
        pad = window // 2
        padded = np.pad(diff, (pad, pad), mode="edge")

        c = np.cumsum(padded)
        c = np.insert(c, 0, 0.0)
        roll_mean = (c[window:window + len(diff)] - c[:len(diff)]) / window

        c2 = np.cumsum(padded ** 2)
        c2 = np.insert(c2, 0, 0.0)
        roll_mean_sq = (c2[window:window + len(diff)] - c2[:len(diff)]) / window
        roll_std = np.sqrt(np.maximum(0.0, roll_mean_sq - roll_mean ** 2))

        # Dynamic threshold
        threshold = roll_mean + 1.2 * roll_std

        candidate_peaks = []
        for i in range(1, len(diff) - 1):
            if diff[i] > threshold[i] and diff[i] > diff[i - 1] and diff[i] >= diff[i + 1]:
                t = round((i + 1) * hop_size / sr, 2)
                candidate_peaks.append((t, float(diff[i])))

        if not candidate_peaks:
            return []

        # Enforce min_interval_sec
        selected_beats = []
        for t, flux_val in candidate_peaks:
            if not selected_beats:
                selected_beats.append((t, flux_val))
            else:
                last_t, last_val = selected_beats[-1]
                if t - last_t >= min_interval_sec:
                    selected_beats.append((t, flux_val))
                elif flux_val > last_val:
                    selected_beats[-1] = (t, flux_val)

        # Cap at max_beats
        if len(selected_beats) > max_beats:
            selected_beats.sort(key=lambda x: x[1], reverse=True)
            selected_beats = selected_beats[:max_beats]
            selected_beats.sort(key=lambda x: x[0])

        return [round(b[0], 2) for b in selected_beats]
