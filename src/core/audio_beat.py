import os
import json
from typing import List

class AudioBeatDetector:
    """
    Phân tích âm thanh để lấy các mốc nhịp (beat timestamps)
    phục vụ cho tính năng Auto Beat-Sync (Cắt B-Roll theo nhạc).
    """
    
    @staticmethod
    def detect_beats(audio_path: str) -> List[float]:
        """
        Sử dụng librosa để quét file nhạc, trả về mảng các giây (float) có beat drop.
        """
        import librosa
        
        norm_path = os.path.normpath(os.path.abspath(audio_path))
        if not os.path.exists(norm_path):
            raise FileNotFoundError(f"Không tìm thấy file nhạc: {norm_path}")
            
        print(f"🎵 Đang phân tích nhịp tim (Audio Beats) cho: {os.path.basename(norm_path)}")
        
        # Load file audio (sr=None để giữ nguyên sample rate gốc hoặc sr=22050 để tăng tốc)
        y, sr = librosa.load(norm_path, sr=22050)
        
        # Phân tích beat
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        
        # Chuyển đổi khung hình (frames) sang số giây (time)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        
        # Chuyển sang mảng float chuẩn
        beat_times_list = [float(t) for t in beat_times]
        print(f"✅ Đã tìm thấy {len(beat_times_list)} beats (Tempo: {tempo[0] if isinstance(tempo, (list, tuple)) else tempo:.1f} BPM)")
        
        return beat_times_list
