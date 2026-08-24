import os
import wave
import numpy as np
from typing import List, Tuple, Dict, Any
from pydantic import BaseModel, Field

class AudioCutConfig(BaseModel):
    """
    Cấu hình tham số phát hiện khoảng lặng để tự động cắt video.
    """
    min_silent_duration: float = Field(
        default=0.5, ge=0.1, le=10.0,
        description="Thời gian im lặng tối thiểu (giây) để quyết định cắt bỏ"
    )
    silence_threshold_db: float = Field(
        default=-35.0, ge=-100.0, le=0.0,
        description="Ngưỡng âm lượng (dB) để coi là im lặng (dưới ngưỡng này sẽ bị cắt)"
    )
    padding_seconds: float = Field(
        default=0.25, ge=0.0, le=1.0,
        description="Thời gian đệm (giây) ở hai đầu điểm cắt để tránh mất chữ thoại đầu/cuối"
    )

class SilenceDetector:
    """
    Lớp xử lý tín hiệu âm thanh thô bằng numpy để phát hiện các khoảng lặng và khoảng tiếng nói.
    """
    
    @staticmethod
    def detect_silence_from_wav(wav_path: str, config: AudioCutConfig) -> List[Tuple[float, float]]:
        """
        Đọc tệp âm thanh WAV và phân tích cường độ âm lượng theo từng cửa sổ thời gian.
        Trả về danh sách các khoảng thời gian có tiếng nói (khoảng cần giữ lại).

        Args:
            wav_path (str): Đường dẫn tới tệp WAV đầu vào (yêu cầu WAV PCM).
            config (AudioCutConfig): Cấu hình ngưỡng cắt.

        Returns:
            List[Tuple[float, float]]: Mảng các tuple (start_time, end_time) của các đoạn giữ lại.
        """
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"Không tìm thấy tệp âm thanh tại: {wav_path}")

        # 1. Đọc tệp WAV PCM sử dụng thư viện wave tiêu chuẩn
        with wave.open(wav_path, 'rb') as wav:
            params = wav.getparams()
            sample_rate = params.framerate
            sample_width = params.sampwidth
            num_frames = params.nframes
            
            raw_frames = wav.readframes(num_frames)
            
            # Chuyển đổi dữ liệu nhị phân sang numpy array tương ứng với độ rộng mẫu
            if sample_width == 1:
                # 8-bit không dấu
                audio_data = np.frombuffer(raw_frames, dtype=np.uint8)
                # Chuẩn hóa về khoảng [-1.0, 1.0]
                audio_data = (audio_data.astype(np.float32) - 128.0) / 128.0
            elif sample_width == 2:
                # 16-bit có dấu
                audio_data = np.frombuffer(raw_frames, dtype=np.int16)
                # Chuẩn hóa về khoảng [-1.0, 1.0]
                audio_data = audio_data.astype(np.float32) / 32768.0
            else:
                raise ValueError("Định dạng WAV không được hỗ trợ (chỉ hỗ trợ 8-bit và 16-bit PCM).")

        # Thời lượng tổng thể của âm thanh
        duration = num_frames / sample_rate
        
        # 2. Định nghĩa kích thước cửa sổ phân tích (ví dụ: 50ms)
        window_duration = 0.05 
        window_size = int(window_duration * sample_rate)
        
        if len(audio_data) < window_size:
            # Tệp quá ngắn, giữ lại toàn bộ
            return [(0.0, duration)]

        # Chia tín hiệu thành các cửa sổ không chồng chập
        num_windows = len(audio_data) // window_size
        audio_windows = audio_data[:num_windows * window_size].reshape((num_windows, window_size))
        
        # Tính trị số hiệu dụng RMS cho mỗi cửa sổ
        # RMS = sqrt(mean(x^2))
        rms_values = np.sqrt(np.mean(audio_windows ** 2, axis=1))
        
        # Tránh lỗi log10(0), đặt một mức sàn âm lượng cực tiểu
        rms_values = np.clip(rms_values, 1e-10, None)
        
        # Quy đổi RMS sang đơn vị Decibel (dB)
        db_values = 20 * np.log10(rms_values)
        
        # 3. Phân loại từng cửa sổ: True là im lặng (dưới ngưỡng), False là có tiếng nói
        is_silent = db_values < config.silence_threshold_db
        
        # Tìm các khối im lặng liên tục
        silent_intervals = []
        in_silence = False
        silence_start = 0.0
        
        for idx, silent in enumerate(is_silent):
            time_sec = idx * window_duration
            if silent:
                if not in_silence:
                    in_silence = True
                    silence_start = time_sec
            else:
                if in_silence:
                    in_silence = False
                    silence_duration = time_sec - silence_start
                    if silence_duration >= config.min_silent_duration:
                        silent_intervals.append((silence_start, time_sec))
                        
        # Xử lý trường hợp im lặng kéo dài đến hết tệp
        if in_silence:
            silence_duration = duration - silence_start
            if silence_duration >= config.min_silent_duration:
                silent_intervals.append((silence_start, duration))

        # 4. Từ các khoảng im lặng, tính toán các khoảng giữ lại (non-silent intervals)
        keep_intervals = []
        current_time = 0.0
        
        for s_start, s_end in silent_intervals:
            if s_start > current_time:
                keep_intervals.append((current_time, s_start))
            current_time = s_end
            
        if current_time < duration:
            keep_intervals.append((current_time, duration))

        # 5. Áp dụng khoảng đệm (Padding) ở hai đầu để tránh cắt cụt câu thoại
        padded_keep_intervals = []
        for start, end in keep_intervals:
            # Lùi start về trước (đệm đầu) nhưng không được nhỏ hơn 0
            new_start = max(0.0, start - config.padding_seconds)
            # Tiến end về sau (đệm đuôi) nhưng không vượt quá tổng thời lượng
            new_end = min(duration, end + config.padding_seconds)
            
            # Nếu mảng đã có phần tử, kiểm tra chồng chập với đoạn trước đó
            if padded_keep_intervals:
                prev_start, prev_end = padded_keep_intervals[-1]
                if new_start < prev_end:
                    # Gộp hai đoạn bị chồng chập do áp dụng padding
                    padded_keep_intervals[-1] = (prev_start, max(prev_end, new_end))
                else:
                    padded_keep_intervals.append((new_start, new_end))
            else:
                padded_keep_intervals.append((new_start, new_end))

        return padded_keep_intervals

def get_video_fps(video_path: str) -> float:
    """
    Lấy tốc độ khung hình (FPS) của tệp video sử dụng ffprobe.
    """
    try:
        import ffmpeg
        probe = ffmpeg.probe(video_path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            avg_frame_rate = video_stream.get('avg_frame_rate', '30/1')
            if '/' in avg_frame_rate:
                num, den = map(float, avg_frame_rate.split('/'))
                return num / den if den != 0 else 30.0
            return float(avg_frame_rate)
    except Exception:
        pass
    return 30.0

def seconds_to_timecode(seconds: float, fps: float) -> str:
    """
    Chuyển đổi số giây thành định dạng Timecode CMX3600 (HH:MM:SS:FF).
    """
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    frames = int(round((seconds % 1) * fps))
    if frames >= int(fps):
        frames = int(fps) - 1
    return f"{hrs:02d}:{mins:02d}:{secs:02d}:{frames:02d}"

class EDLGenerator:
    """
    Lớp hỗ trợ sinh tệp tin Edit Decision List (EDL) định dạng CMX3600
    để tự động hóa cắt dựng đồng bộ Video + Audio trong DaVinci Resolve.
    """

    @staticmethod
    def get_video_fps(video_path: str) -> float:
        return get_video_fps(video_path)

    @staticmethod
    def generate_edl_content(video_path: str, keep_intervals: List[Tuple[float, float]], fps: float = 30.0) -> str:
        clip_name = os.path.basename(video_path)
        lines = [
            "TITLE: Silence Cut",
            "FCM: NON-DROP FRAME",
            ""
        ]

        current_record_time = 0.0
        for idx, (start, end) in enumerate(keep_intervals, 1):
            duration = end - start
            if duration <= 0:
                continue

            src_in_tc = seconds_to_timecode(start, fps)
            src_out_tc = seconds_to_timecode(end, fps)

            rec_in_tc = seconds_to_timecode(current_record_time, fps)
            current_record_time += duration
            rec_out_tc = seconds_to_timecode(current_record_time, fps)

            event_num = f"{idx:03d}"

            # Event cho luồng Video
            lines.append(f"{event_num}  AX       V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

            # Event cho luồng Audio (Track 1)
            lines.append(f"{event_num}  AX       A     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

        return "\n".join(lines)

    @staticmethod
    def create_edl(video_path: str, keep_intervals: List[Tuple[float, float]], output_edl_path: str) -> str:
        fps = get_video_fps(video_path)
        content = EDLGenerator.generate_edl_content(video_path, keep_intervals, fps)

        parent_dir = os.path.dirname(output_edl_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_edl_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_edl_path

    @staticmethod
    def create_multi_clip_edl(events: List[Dict[str, Any]], output_edl_path: str) -> str:
        """
        Tạo tệp EDL ghép nối nhiều nguồn clip khác nhau.
        Mỗi phần tử trong events chứa: video_path, src_in, src_out, rec_in, rec_out, fps
        """
        lines = [
            "TITLE: Silence Cut Multi-Clip",
            "FCM: NON-DROP FRAME",
            ""
        ]

        for idx, ev in enumerate(events, 1):
            clip_name = os.path.basename(ev["video_path"])
            fps = ev["fps"]
            
            src_in_tc = seconds_to_timecode(ev["src_in"], fps)
            src_out_tc = seconds_to_timecode(ev["src_out"], fps)
            rec_in_tc = seconds_to_timecode(ev["rec_in"], fps)
            rec_out_tc = seconds_to_timecode(ev["rec_out"], fps)
            
            event_num = f"{idx:03d}"
            
            # Event cho luồng Video
            lines.append(f"{event_num}  AX       V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

            # Event cho luồng Audio (Track 1)
            lines.append(f"{event_num}  AX       A     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

        content = "\n".join(lines)
        
        parent_dir = os.path.dirname(output_edl_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_edl_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_edl_path
