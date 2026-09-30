import gc
import re
import os
from typing import Literal, Optional, List, Dict, Any, Callable
from src.core import transcript_quality
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

def register_nvidia_dll_dirs() -> List[str]:
    """
    Đăng ký thư mục DLL của các gói pip nvidia-cublas-cu12 / nvidia-cudnn-cu12 (nếu có) để Windows tìm thấy
    cuBLAS/cuDNN khi chạy Whisper bằng GPU mà không cần cài CUDA Toolkit. Trả về danh sách thư mục đã đăng ký.
    """
    added: List[str] = []
    try:
        import glob
        import importlib.util
        spec = importlib.util.find_spec("nvidia")
        roots = list(spec.submodule_search_locations) if spec and spec.submodule_search_locations else []
        for root in roots:
            for bin_dir in glob.glob(os.path.join(root, "*", "bin")):
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                added.append(bin_dir)
    except Exception:
        pass
    return added


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
        self.active_device = None
        self.active_compute_type = None

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
        if device == "cuda":
            register_nvidia_dll_dirs()

        # Tự động phát hiện CUDA của máy tính (dùng ctranslate2 - chính backend của faster-whisper,
        # không yêu cầu cài PyTorch)
        if device == "cuda":
            try:
                import ctranslate2
                cuda_ok = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                cuda_ok = False
            if not cuda_ok:
                msg = "⚠️ CUDA không khả dụng trên thiết bị. Tự động chuyển sang xử lý bằng CPU (float32)."
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

        # Thiếu thư viện CUDA (cuBLAS/cuDNN) chỉ lộ ra ở lần suy luận đầu tiên, nên chạy thử 1 lần ngay tại đây
        if device == "cuda":
            try:
                import numpy as np
                list(self.model.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
            except Exception as e:
                msg = f"⚠️ GPU CUDA không chạy được ({str(e).splitlines()[0][:120]}). Chuyển sang CPU (int8)."
                if log_callback:
                    log_callback(msg)
                else:
                    print(f" Warn: {msg}")
                device, compute_type = "cpu", "int8"
                self.model = WhisperModel(self.config.model_size, device=device, compute_type=compute_type)
        self.active_device = device
        self.active_compute_type = compute_type

        if log_callback:
            log_callback(f"✔ Đã nạp thành công mô hình Whisper AI '{self.config.model_size}'.")

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        is_cancelled_callback: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        fill_gaps: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Thực hiện nhận dạng giọng nói ngoại tuyến từ tệp âm thanh kèm streaming progress.
        fill_gaps=True: quét bổ sung các khoảng dài có âm thanh lớn mà lần quét đầu không ra chữ (chậm hơn).

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
            word_timestamps=True,
            # Tắt "nhớ câu trước": trên video dài nhiều tạp âm, Whisper dễ kẹt vòng lặp rồi bỏ trống cả quãng dài
            condition_on_previous_text=False
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

        results = transcript_quality.split_stretched_segments(results)
        if fill_gaps and not (is_cancelled_callback and is_cancelled_callback()):
            results = self.fill_uncovered_gaps(audio_path, results, language, is_cancelled_callback, progress_callback)
        return results

    def transcribe_slice(
        self,
        audio: "np.ndarray",
        offset: float,
        language: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Nhận dạng một đoạn âm thanh (numpy 16kHz) với VAD tắt, rồi cộng offset vào mốc thời gian.
        Loại các câu Whisper không tin tưởng (no_speech cao, lặp chữ, logprob quá thấp).
        """
        segments, _ = self.model.transcribe(
            audio, language=language, beam_size=5, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False
        )
        out = []
        for seg in segments:
            text = seg.text.strip()
            if (not text or is_whisper_hallucination(text) or seg.no_speech_prob > 0.7
                    or seg.compression_ratio > 2.4 or seg.avg_logprob < -1.3):
                continue
            words = [{"word": w.word, "start": w.start + offset, "end": w.end + offset,
                      "probability": w.probability} for w in (seg.words or [])]
            out.append({"start": seg.start + offset, "end": seg.end + offset, "text": text,
                        "words": words, "filled": True})
        return transcript_quality.split_stretched_segments(out)

    def fill_uncovered_gaps(
        self,
        audio_path: str,
        results: List[Dict[str, Any]],
        language: Optional[str] = None,
        is_cancelled_callback: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        audio, sr = transcript_quality.read_wav_mono(audio_path)
        gaps = transcript_quality.find_uncovered_gaps(results, audio, sr)
        extra: List[Dict[str, Any]] = []
        for n, (a, b) in enumerate(gaps, 1):
            if is_cancelled_callback and is_cancelled_callback():
                break
            extra.extend(self.transcribe_slice(audio[int(a * sr): int(b * sr)], a, language))
            if progress_callback:
                progress_callback(n / len(gaps), f"Quét bổ sung vùng bị bỏ sót {n}/{len(gaps)}")
        return transcript_quality.merge_segments(results, extra)

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
