import gc
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
        is_cancelled_callback: Optional[Callable[[], bool]] = None
    ) -> List[Dict[str, Any]]:
        """
        Thực hiện nhận dạng giọng nói ngoại tuyến từ tệp âm thanh.

        Args:
            audio_path (str): Đường dẫn tệp âm thanh đầu vào (.wav).
            language (str, optional): Mã ngôn ngữ đích (ví dụ: 'vi' cho Tiếng Việt, 'en' cho Tiếng Anh). 
                                     Mặc định là None (Tự động nhận diện ngôn ngữ).
            is_cancelled_callback (Callable, optional): Hàm kiểm tra yêu cầu hủy luồng từ người dùng.

        Returns:
            List[Dict[str, Any]]: Danh sách các phân đoạn hội thoại kèm thời gian start/end và từ đơn.
        """
        if self.model is None:
            self.load_model()

        # Thực thi dịch offline
        # VAD filter tự động loại bỏ tạp âm và các khoảng im lặng dài
        # Word timestamps cho phép lấy chính xác mốc thời gian của từng từ đơn lẻ
        segments, info = self.model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            word_timestamps=True
        )

        results = []
        for segment in segments:
            if is_cancelled_callback and is_cancelled_callback():
                break
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
                "text": segment.text.strip(),
                "words": words_data
            })

        return results

    def unload_model(self) -> None:
        """
        Giải phóng mô hình khỏi bộ nhớ RAM và thu hồi triệt để VRAM của GPU.
        """
        if self.model is not None:
            self.model = None
            # Thu dọn rác bộ nhớ Python
            gc.collect()
            # Làm rỗng cache CUDA của PyTorch
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
