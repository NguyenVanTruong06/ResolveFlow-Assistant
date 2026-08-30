import os
import re
import wave
import subprocess
import json
import numpy as np
from typing import List, Tuple, Dict, Any, Optional
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
    speed_up_silence: bool = Field(
        default=False,
        description="Tua nhanh khoảng lặng thay vì cắt bỏ (Auto Speed-Ramp / Timelapse)"
    )
    silence_speed_multiplier: float = Field(
        default=8.0, ge=2.0, le=20.0,
        description="Hệ số tốc độ tua nhanh khoảng lặng (ví dụ 8.0x)"
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
        norm_wav = os.path.normpath(os.path.abspath(wav_path))
        if not os.path.exists(norm_wav):
            raise FileNotFoundError(f"Không tìm thấy tệp âm thanh tại: {norm_wav}")

        # 1. Đọc tệp WAV PCM sử dụng thư viện wave tiêu chuẩn
        with wave.open(norm_wav, 'rb') as wav:
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

    @staticmethod
    def detect_intervals_with_speedup(
        wav_path: str,
        config: AudioCutConfig,
        speed_multiplier: float = 8.0
    ) -> List[Dict[str, Any]]:
        """
        Phân tích tệp WAV và trả về toàn bộ dòng thời gian bao gồm:
        - Các đoạn thoại bình thường (speed = 1.0x).
        - Các đoạn im lặng được gán hiệu ứng tua nhanh (speed = speed_multiplier).
        """
        keep_intervals = SilenceDetector.detect_silence_from_wav(wav_path, config)
        try:
            from src.core.audio import AudioExtractor
            duration = AudioExtractor.get_audio_duration(wav_path)
        except Exception:
            duration = keep_intervals[-1][1] if keep_intervals else 0.0

        timeline_segments = []
        current_time = 0.0

        for start, end in keep_intervals:
            # Nếu có khoảng lặng trước đoạn thoại này
            if start > current_time:
                silence_dur = start - current_time
                if silence_dur >= config.min_silent_duration:
                    timeline_segments.append({
                        "type": "speedup",
                        "start": current_time,
                        "end": start,
                        "duration": silence_dur,
                        "speed": speed_multiplier,
                        "rec_duration": silence_dur / speed_multiplier,
                        "note": f"⚡ Tua nhanh Timelapse ({speed_multiplier}x)"
                    })
                else:
                    # Khoảng lặng quá ngắn, giữ nguyên 1.0x
                    timeline_segments.append({
                        "type": "voice",
                        "start": current_time,
                        "end": start,
                        "duration": silence_dur,
                        "speed": 1.0,
                        "rec_duration": silence_dur,
                        "note": "Khoảng nghỉ ngắn"
                    })

            # Đoạn thoại chính
            voice_dur = end - start
            timeline_segments.append({
                "type": "voice",
                "start": start,
                "end": end,
                "duration": voice_dur,
                "speed": 1.0,
                "rec_duration": voice_dur,
                "note": "Thoại chính"
            })
            current_time = end

        # Xử lý khoảng lặng cuối cùng
        if current_time < duration:
            silence_dur = duration - current_time
            if silence_dur >= config.min_silent_duration:
                timeline_segments.append({
                    "type": "speedup",
                    "start": current_time,
                    "end": duration,
                    "duration": silence_dur,
                    "speed": speed_multiplier,
                    "rec_duration": silence_dur / speed_multiplier,
                    "note": f"⚡ Tua nhanh Timelapse ({speed_multiplier}x)"
                })

        return timeline_segments

def parse_timecode_to_seconds(tc_str: str, fps: float = 30.0) -> float:
    """
    Chuyển đổi chuỗi Timecode (HH:MM:SS:FF hoặc HH:MM:SS;FF) thành số giây (float).
    """
    if not tc_str or not isinstance(tc_str, str):
        return 0.0
    import re
    parts = re.split(r'[:;.]', tc_str.strip())
    if len(parts) != 4:
        return 0.0
    try:
        hrs, mins, secs, frames = [int(p) for p in parts]
        total_seconds = (hrs * 3600) + (mins * 60) + secs + (frames / fps)
        return total_seconds
    except ValueError:
        return 0.0

def get_media_metadata(video_path: str) -> Dict[str, Any]:
    """
    Trích xuất toàn bộ metadata gốc của tệp video:
    - FPS thực tế
    - Embedded Start Timecode (từ tags.timecode của video stream, tmcd hoặc format)
    - Start time offset (giây)
    - Tổng thời lượng (duration)
    """
    default_meta = {
        "fps": 30.0,
        "start_timecode": "00:00:00:00",
        "start_seconds": 0.0,
        "duration": 0.0,
        "width": 1920,
        "height": 1080
    }
    if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
        return default_meta

    probe = None
    try:
        import ffmpeg
        probe = ffmpeg.probe(video_path)
    except Exception:
        import subprocess
        import json
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            probe = json.loads(res.stdout)
        except Exception:
            return default_meta

    if not probe:
        return default_meta

    video_stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
    format_data = probe.get('format', {})

    fps = 30.0
    if video_stream:
        avg_frame_rate = video_stream.get('avg_frame_rate', video_stream.get('r_frame_rate', '30/1'))
        if '/' in avg_frame_rate:
            num, den = map(float, avg_frame_rate.split('/'))
            fps = num / den if den != 0 else 30.0
        else:
            try:
                fps = float(avg_frame_rate)
            except (ValueError, TypeError):
                fps = 30.0

    start_tc = None
    if video_stream and 'tags' in video_stream:
        v_tags = video_stream['tags']
        start_tc = v_tags.get('timecode') or v_tags.get('TIMECODE') or v_tags.get('time_code')

    if not start_tc:
        tmcd_stream = next((s for s in probe.get('streams', []) if s.get('codec_tag_string') == 'tmcd'), None)
        if tmcd_stream and 'tags' in tmcd_stream:
            start_tc = tmcd_stream['tags'].get('timecode')

    if not start_tc and 'tags' in format_data:
        f_tags = format_data['tags']
        start_tc = f_tags.get('timecode') or f_tags.get('TIMECODE') or f_tags.get('time_code')

    start_sec = 0.0
    if start_tc:
        start_sec = parse_timecode_to_seconds(start_tc, fps)
    else:
        raw_start_time = video_stream.get('start_time') if video_stream else format_data.get('start_time')
        if raw_start_time is not None:
            try:
                start_sec = float(raw_start_time)
                start_tc = seconds_to_timecode(0.0, fps, start_tc_offset_seconds=start_sec)
            except ValueError:
                start_sec = 0.0
                start_tc = "00:00:00:00"
        else:
            start_tc = "00:00:00:00"
            start_sec = 0.0

    duration = 0.0
    try:
        duration = float(format_data.get('duration', 0.0))
    except (ValueError, TypeError):
        pass

    width = int(video_stream.get('width', 1920)) if video_stream else 1920
    height = int(video_stream.get('height', 1080)) if video_stream else 1080

    has_audio = any(s.get('codec_type') == 'audio' for s in probe.get('streams', [])) if probe else True

    return {
        "fps": fps,
        "start_timecode": start_tc,
        "start_seconds": start_sec,
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio
    }

def get_video_fps(video_path: str) -> float:
    """
    Lấy tốc độ khung hình (FPS) của tệp video sử dụng ffprobe.
    """
    return get_media_metadata(video_path)["fps"]

def seconds_to_timecode(
    seconds: float, 
    fps: float = 30.0, 
    start_tc_offset_seconds: float = 0.0
) -> str:
    """
    Chuyển đổi số giây thành định dạng Timecode CMX3600 (HH:MM:SS:FF).
    Hỗ trợ cộng offset Start Timecode gốc của camera (Sony, Canon, ARRI...).
    """
    total_sec = max(0.0, seconds + start_tc_offset_seconds)
    hrs = int(total_sec // 3600)
    mins = int((total_sec % 3600) // 60)
    secs = int(total_sec % 60)
    sub_sec = total_sec - int(total_sec)
    frames = int(round(sub_sec * fps))
    if frames >= int(round(fps)):
        frames = int(round(fps)) - 1
    hrs = hrs % 24
    return f"{hrs:02d}:{mins:02d}:{secs:02d}:{frames:02d}"

def get_reel_name(video_path: str) -> str:
    """
    Sinh Reel Name chuẩn CMX3600 (tối đa 8 ký tự chữ số/chữ cái) từ tên file video.
    Ví dụ: 'C0387.MP4' -> 'C0387', 'Clip_12.mp4' -> 'CLIP12'
    """
    base = os.path.splitext(os.path.basename(video_path))[0]
    cleaned = re.sub(r'[^A-Za-z0-9]', '', base).upper()
    if not cleaned:
        return "AX"
    return cleaned[:8]

class EDLGenerator:
    """
    Lớp hỗ trợ sinh tệp tin Edit Decision List (EDL) định dạng CMX3600
    để tự động hóa cắt dựng đồng bộ Video + Audio trong DaVinci Resolve.
    """

    @staticmethod
    def get_video_fps(video_path: str) -> float:
        return get_video_fps(video_path)

    @staticmethod
    def generate_edl_content(
        video_path: str, 
        keep_intervals: List[Tuple[float, float]], 
        fps: Optional[float] = None,
        title: str = "Silence Cut"
    ) -> str:
        meta = get_media_metadata(video_path)
        fps_val = fps or meta["fps"]
        start_tc_sec = meta["start_seconds"]
        clip_name = os.path.basename(video_path)
        reel_id = get_reel_name(clip_name).ljust(8)

        lines = [
            f"TITLE: {title}",
            "FCM: NON-DROP FRAME",
            ""
        ]

        current_record_time = 0.0
        for idx, (start, end) in enumerate(keep_intervals, 1):
            duration = end - start
            if duration <= 0:
                continue

            src_in_tc = seconds_to_timecode(start, fps_val, start_tc_offset_seconds=start_tc_sec)
            src_out_tc = seconds_to_timecode(end, fps_val, start_tc_offset_seconds=start_tc_sec)

            rec_in_tc = seconds_to_timecode(current_record_time, fps_val)
            current_record_time += duration
            rec_out_tc = seconds_to_timecode(current_record_time, fps_val)

            event_num = f"{idx:03d}"

            # Event cho luồng Video
            lines.append(f"{event_num}  {reel_id} V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

            # Event cho luồng Audio (Track 1 & Track 2)
            lines.append(f"{event_num}  {reel_id} A     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

        return "\n".join(lines)

    @staticmethod
    def create_edl(video_path: str, keep_intervals: List[Tuple[float, float]], output_edl_path: str) -> str:
        content = EDLGenerator.generate_edl_content(video_path, keep_intervals)

        parent_dir = os.path.dirname(output_edl_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_edl_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_edl_path

    @staticmethod
    def create_multi_clip_edl(
        events: List[Dict[str, Any]], 
        output_edl_path: str,
        markers: Optional[List[Dict[str, Any]]] = None,
        title: str = "Silence Cut Multi-Clip"
    ) -> str:
        """
        Tạo tệp EDL ghép nối nhiều nguồn clip khác nhau (hỗ trợ Markers & Punch-in Tags).
        Mỗi phần tử trong events chứa: video_path, src_in, src_out, rec_in, rec_out, fps, punch_in (tùy chọn)
        Tự động bù đắp Start Timecode gốc của từng video.
        """
        lines = [
            f"TITLE: {title}",
            "FCM: NON-DROP FRAME",
            ""
        ]

        metadata_cache = {}

        for idx, ev in enumerate(events, 1):
            v_path = ev["video_path"]
            if v_path not in metadata_cache:
                metadata_cache[v_path] = get_media_metadata(v_path)
            meta = metadata_cache[v_path]

            clip_name = os.path.basename(v_path)
            reel_id = get_reel_name(clip_name).ljust(8)
            fps = ev.get("fps") or meta["fps"]
            start_tc_sec = meta["start_seconds"]
            
            src_in_tc = seconds_to_timecode(ev["src_in"], fps, start_tc_offset_seconds=start_tc_sec)
            src_out_tc = seconds_to_timecode(ev["src_out"], fps, start_tc_offset_seconds=start_tc_sec)
            rec_in_tc = seconds_to_timecode(ev["rec_in"], fps)
            rec_out_tc = seconds_to_timecode(ev["rec_out"], fps)
            
            event_num = f"{idx:03d}"
            
            # Event cho luồng Video
            lines.append(f"{event_num}  {reel_id} V     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")
            
            if ev.get("punch_in"):
                lines.append(f"* COMMENT: PUNCH_IN_ZOOM_{ev.get('punch_in_scale', 1.15)}X")

            if ev.get("is_speedup") or ev.get("speed", 1.0) > 1.0:
                speed_val = ev.get("speed", 8.0)
                lines.append(f"* COMMENT: SPEED_RAMP_{speed_val}X")

            # Event cho luồng Audio (Track 1 & Track 2 - Stereo)
            lines.append(f"{event_num}  {reel_id} A     C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")
            lines.append(f"{event_num}  {reel_id} A2    C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")

        # Thêm danh sách Markers nếu có (Chuẩn CMX3600 Locator của DaVinci Resolve)
        if markers:
            for m in markers:
                m_time = m.get("time", 0.0)
                m_color = m.get("color", "Blue")
                m_name = m.get("name", "Marker")
                m_note = m.get("note", "")
                fps_val = events[0]["fps"] if events else 30.0
                m_tc = seconds_to_timecode(m_time, fps_val)
                lines.append(f"* LOC: {m_tc} {m_color} {m_name} - {m_note}")

        content = "\n".join(lines)
        
        parent_dir = os.path.dirname(output_edl_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(output_edl_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_edl_path
