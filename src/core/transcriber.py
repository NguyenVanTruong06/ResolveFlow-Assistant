import gc
import re
import os
from typing import Literal, Optional, List, Dict, Any, Callable
from pydantic import BaseModel, Field

class ModelConfig(BaseModel):
    """
    Lớp cấu hình kiểm soát tham số tải mô hình AI bằng Pydantic.
    """
    model_size: Literal["tiny", "base", "small", "medium", "large-v3"] = Field(
        default="small", 
        description="Kích thước mô hình Whisper (nhỏ hơn chạy nhanh hơn, lớn hơn dịch chuẩn hơn)"
    )
    device: Literal["cuda", "cpu"] = Field(
        default="cuda",
        description="Thiết bị tính toán: cuda (dành cho GPU NVIDIA) hoặc cpu"
    )
    compute_type: Literal["float16", "int8_float16", "int8", "float32"] = Field(
        default="float16",
        description="Định dạng số học để tối ưu hóa bộ nhớ: float16 (khuyên dùng cho GPU) hoặc float32"
    )

def is_whisper_hallucination(text: str) -> bool:
    """
    Phát hiện các câu sinh tự động (hallucination) phổ biến của Whisper khi gặp khoảng lặng
    hoặc âm thanh nhiễu (ví dụ: 'Thank you for watching', 'Thanks for watching',...).
    """
    clean = re.sub(r'[^a-zA-Z0-9\sàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]', '', text.lower().strip())
    
    # Danh sách các câu boilerplate/hallucination phổ biến
    patterns = [
        r"^thank\s+you\s+for\s+watching$",
        r"^thank\s+you$",
        r"^thanks\s+for\s+watching$",
        r"^thank\s+you\s+so\s+much$",
        r"^thanks$",
        r"^subscribe$",
        r"^please\s+subscribe$",
        r"^subscribe\s+to\s+my\s+channel$",
        r"^subtitles\s+by$",
        r"^cảm\s+ơn\s+các\s+bạn\s+đã\s+xem$",
        r"^cảm\s+ơn\s+đã\s+xem$",
        r"^hẹn\s+gặp\s+lại$",
        r"^tạm\s+biệt$"
    ]
    
    for pat in patterns:
        if re.search(pat, clean):
            return True
            
    # Lọc các từ lặp vô nghĩa như "you", "shh", "sh", "uh", "um", "ah", "oh"
    if clean in ["you", "shh", "sh", "uh", "um", "ah", "oh"]:
        return True
        
    return False


class ResolveTranscriber:
    """
    Lớp điều khiển tải mô hình và dịch âm thanh ngoại tuyến sử dụng faster-whisper.
    Hỗ trợ báo tiến trình tải mô hình lần đầu rõ ràng tránh cảm giác app bị treo.
    """
    def __init__(self, config: ModelConfig):
        self.config = config
        self.model = None

    def is_model_cached(self) -> bool:
        """Kiểm tra xem mô hình Whisper đã được tải về máy trước đó chưa."""
        try:
            from faster_whisper.utils import _download_model
            # Kiểm tra trong thư mục cache huggingface mặc định
            cache_dir = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface/hub"))
            model_tag = self.config.model_size
            if os.path.exists(cache_dir):
                for d in os.listdir(cache_dir):
                    if model_tag in d.lower():
                        return True
        except Exception:
            pass
        return False

    def load_model(self, log_callback: Optional[Callable[[str], None]] = None) -> None:
        """
        Tải mô hình Whisper lên thiết bị chỉ định (GPU CUDA hoặc CPU).
        Tự động kiểm tra và chuyển về CPU nếu GPU không hỗ trợ CUDA.
        """
        from faster_whisper import WhisperModel

        device = self.config.device
        compute_type = self.config.compute_type

        # Tự động phát hiện CUDA của máy tính
        if device == "cuda":
            try:
                import torch
                if not torch.cuda.is_available():
                    msg = "⚠️ CUDA không khả dụng trên thiết bị. Tự động chuyển sang xử lý bằng CPU (float32)."
                    if log_callback:
                        log_callback(msg)
                    else:
                        print(f" Warn: {msg}")
                    device = "cpu"
                    compute_type = "float32"
            except ImportError:
                msg = "⚠️ Không tìm thấy PyTorch CUDA. Tự động chuyển sang CPU."
                if log_callback:
                    log_callback(msg)
                else:
                    print(f" Warn: {msg}")
                device = "cpu"
                compute_type = "float32"

        if device == "cpu":
            compute_type = "float32"

        # Báo log tải mô hình lần đầu nếu cần
        if log_callback:
            if not self.is_model_cached():
                log_callback(f"⏳ Đang tải mô hình Whisper '{self.config.model_size}' lần đầu về máy (dung lượng ~150MB - 1.5GB)... Quá trình này chỉ diễn ra 1 lần duy nhất.")
            else:
                log_callback(f"🤖 Đang nạp mô hình Whisper '{self.config.model_size}' ({device.upper()} - {compute_type})...")

        # Khởi tạo mô hình cTranslate2 của faster-whisper
        self.model = WhisperModel(
            self.config.model_size,
            device=device,
            compute_type=compute_type
        )

        if log_callback:
            log_callback(f"✔ Đã nạp thành công mô hình Whisper AI '{self.config.model_size}'.")

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        is_cancelled_callback: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Thực hiện nhận dạng giọng nói ngoại tuyến từ tệp âm thanh kèm streaming progress.

        Args:
            audio_path (str): Đường dẫn tệp âm thanh đầu vào (.wav).
            language (str, optional): Mã ngôn ngữ đích.
            is_cancelled_callback (Callable, optional): Hàm kiểm tra yêu cầu hủy luồng.
            progress_callback (Callable, optional): Hàm callback báo tiến độ (timestamp, text).

        Returns:
            List[Dict[str, Any]]: Danh sách các phân đoạn hội thoại.
        """
        if self.model is None:
            self.load_model()

        segments, info = self.model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True
        )

        results = []
        total_duration = info.duration if hasattr(info, 'duration') and info.duration > 0 else 1.0

        for segment in segments:
            if is_cancelled_callback and is_cancelled_callback():
                break
            
            segment_text = segment.text.strip()
            if is_whisper_hallucination(segment_text):
                continue

            words_data = []
            if segment.words:
                for w in segment.words:
                    words_data.append({
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                        "probability": w.probability
                    })
            
            results.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment_text,
                "words": words_data
            })

            if progress_callback:
                progress_ratio = min(1.0, segment.end / total_duration)
                progress_callback(progress_ratio, segment_text)

        return results

    def unload_model(self) -> None:
        """
        Giải phóng mô hình khỏi bộ nhớ RAM và thu hồi triệt để VRAM của GPU.
        """
        if self.model is not None:
            self.model = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
