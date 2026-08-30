import os
import subprocess
import concurrent.futures
from typing import Dict, Any, Optional, Tuple, Callable, List

def suggest_whisper_model(duration_seconds: float) -> Dict[str, Any]:
    """
    Tự động gợi ý kích thước mô hình Whisper tối ưu dựa trên độ dài video
    kèm ước tính thời gian và độ chính xác.
    """
    mins = duration_seconds / 60.0
    if duration_seconds <= 0:
        return {
            "suggested_model": "small",
            "reason": "Độ dài mặc định",
            "speed_factor": "1x",
            "accuracy": "95%",
            "is_warning": False
        }
    
    if mins < 3.0:
        return {
            "suggested_model": "large-v3",
            "reason": f"Video ngắn ({mins:.1f} phút): Khuyên dùng 'large-v3' để đạt độ chính xác tối đa và phát hiện từ ngữ chuẩn nhất.",
            "speed_factor": "1x (Rất nhanh trên video ngắn)",
            "accuracy": "99%",
            "is_warning": False
        }
    elif mins <= 30.0:
        return {
            "suggested_model": "small",
            "reason": f"Video trung bình ({mins:.1f} phút): Khuyên dùng 'small' để tốc độ nhanh gấp ~3x trong khi độ chính xác vẫn đạt 94-96%.",
            "speed_factor": "~3x nhanh hơn large-v3",
            "accuracy": "95%",
            "is_warning": False
        }
    else:
        return {
            "suggested_model": "small",
            "reason": f"⚠️ Video dài ({mins:.1f} phút): Khuyên dùng 'small' hoặc 'medium' để tiết kiệm 65-75% thời gian quét. Model 'small' xử lý nhanh hơn 4x mà vẫn giữ độ chính xác ~92-94%.",
            "speed_factor": "~4x nhanh hơn large-v3 (Tiết kiệm hàng chục phút)",
            "accuracy": "93%",
            "is_warning": True
        }


class ProxyManager:
    """
    Quản lý tạo Video Proxy 480p siêu tốc và Audio Mono 16kHz phục vụ khâu
    quét nhận diện hình ảnh & giọng nói nhanh gấp nhiều lần so với file gốc 4K/8K.
    """
    @staticmethod
    def generate_video_proxy(
        video_path: str, 
        output_proxy_path: str, 
        target_height: int = 480
    ) -> bool:
        """
        Tạo tệp video proxy 480p siêu nhanh bằng FFmpeg ultrafast preset (bỏ kênh tiếng).
        """
        norm_in = os.path.normpath(os.path.abspath(video_path))
        norm_out = os.path.normpath(os.path.abspath(output_proxy_path))

        if not os.path.exists(norm_in):
            return False

        out_dir = os.path.dirname(norm_out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        cmd = [
            "ffmpeg", "-y", "-i", norm_in,
            "-vf", f"scale=-2:{target_height}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-an",
            norm_out
        ]

        try:
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=180, encoding="utf-8", errors="replace"
            )
            return (res.returncode == 0) and os.path.exists(norm_out) and os.path.getsize(norm_out) > 0
        except Exception:
            return False


class ParallelScanPipeline:
    """
    Điều phối quét song song (Parallel Pipeline) độc lập giữa hai tác vụ nặng:
    1. Nhận diện giọng nói Speech-to-Text (Audio 16kHz + Whisper)
    2. Phân tích thị giác (Proxy 480p + Face Detect / Vision Reframe / Hook)
    Sau đó gộp kết quả mượt mà vào AI Director.
    """
    @staticmethod
    def execute_parallel_scan(
        audio_task_fn: Callable[[], Any],
        video_task_fn: Optional[Callable[[], Any]] = None
    ) -> Tuple[Any, Any]:
        """
        Chạy 2 tác vụ song song qua ThreadPoolExecutor.
        """
        audio_res = None
        video_res = None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_audio = executor.submit(audio_task_fn)
            future_video = executor.submit(video_task_fn) if video_task_fn else None

            audio_res = future_audio.result()
            if future_video:
                video_res = future_video.result()

        return audio_res, video_res
