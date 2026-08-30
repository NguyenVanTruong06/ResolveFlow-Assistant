import gc
import re
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
    """
    def __init__(self, config: ModelConfig):
        self.config = config
        self.model = None

    def load_model(self) -> None:
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
                    print(" Warn: CUDA is not available. Falling back to CPU execution.")
                    device = "cpu"
                    compute_type = "float32"
            except ImportError:
                print(" Warn: PyTorch/CUDA not configured properly. Falling back to CPU.")
                device = "cpu"
                compute_type = "float32"

        # Nếu chạy trên CPU, bắt buộc dùng float32 để tránh lỗi tương thích kiểu dữ liệu
        if device == "cpu":
            compute_type = "float32"

        # Khởi tạo mô hình cTranslate2 của faster-whisper
        self.model = WhisperModel(
            self.config.model_size,
            device=device,
            compute_type=compute_type
        )

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
