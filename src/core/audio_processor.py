import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Optional, Union, Dict, Any, Tuple

from src.utils.path_utils import PosixPathAdapter
from src.core.ffmpeg_utils import get_ffmpeg_path, get_ffprobe_path, run_ffmpeg


def probe_streams(media_path: Union[str, Path]) -> Dict[str, Any]:
    """Trích xuất thông tin các luồng (streams) của media bằng ffprobe."""
    resolved = PosixPathAdapter.resolve_path(media_path)
    if not resolved.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp media tại: {resolved}")

    ffprobe_bin = get_ffprobe_path()
    if not ffprobe_bin:
        # Fallback thử module ffmpeg-python nếu đã cài đặt
        try:
            import ffmpeg
            return ffmpeg.probe(str(resolved))
        except Exception as e:
            raise FileNotFoundError("Không tìm thấy công cụ ffprobe trong hệ thống PATH.") from e

    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(resolved)
    ]
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        encoding="utf-8",
        errors="replace"
    )
    if res.returncode != 0:
        return {}
    try:
        return json.loads(res.stdout)
    except Exception:
        return {}


def inspect_audio_codec(media_path: Union[str, Path]) -> Optional[str]:
    """
    Kiểm tra tên codec của luồng âm thanh trong video (ví dụ: 'aac', 'pcm_s16le', 'mp3').
    Trả về None nếu video không chứa luồng âm thanh hoặc không phát hiện được.
    """
    try:
        data = probe_streams(media_path)
        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if audio_stream:
            return str(audio_stream.get("codec_name", "")).lower()
    except Exception:
        pass
    return None


def is_linux_aac_conflict(media_path: Union[str, Path]) -> bool:
    """
    Kiểm tra xem video có rơi vào giới hạn không hỗ trợ codec AAC của DaVinci Resolve Linux không.
    Trên Linux, DaVinci Resolve không giải mã được codec AAC trong tệp MP4, dẫn tới mất tiếng / câm trên Timeline.
    """
    if not sys.platform.startswith("linux"):
        return False
    codec = inspect_audio_codec(media_path)
    return codec == "aac"


def extract_pcm_wav(
    video_path: Union[str, Path],
    output_wav: Optional[Union[str, Path]] = None,
    sample_rate: int = 48000
) -> Path:
    """
    Trích xuất luồng âm thanh sang WAV PCM 16-bit 48kHz không nén (chuẩn phòng dựng DaVinci Resolve).
    """
    resolved_in = PosixPathAdapter.resolve_path(video_path)
    if output_wav is None:
        cache_dir = resolved_in.parent / ".resolveflow_cache" / "audio_pcm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        resolved_out = cache_dir / f"{resolved_in.stem}_pcm.wav"
    else:
        resolved_out = PosixPathAdapter.resolve_path(output_wav)
        resolved_out.parent.mkdir(parents=True, exist_ok=True)

    if resolved_out.exists() and resolved_out.stat().st_size > 0:
        return resolved_out

    args = [
        "-y",
        "-i", str(resolved_in),
        "-vn",
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        str(resolved_out)
    ]
    res = run_ffmpeg(args, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"Lỗi FFmpeg khi trích xuất PCM WAV: {res.stderr[:400]}")
    return resolved_out


def remux_to_mov_pcm(
    video_path: Union[str, Path],
    output_mov: Optional[Union[str, Path]] = None,
    sample_rate: int = 48000
) -> Path:
    """
    Remux tệp video sang container QuickTime .mov với luồng video giữ nguyên (copy)
    và luồng âm thanh chuyển đổi sang PCM 16-bit 48kHz.
    Giúp DaVinci Resolve Linux đọc mượt mà cả hình lẫn tiếng mà không mất thời gian render lại video.
    """
    resolved_in = PosixPathAdapter.resolve_path(video_path)
    if output_mov is None:
        cache_dir = resolved_in.parent / ".resolveflow_cache" / "remux_mov"
        cache_dir.mkdir(parents=True, exist_ok=True)
        resolved_out = cache_dir / f"{resolved_in.stem}_pcm.mov"
    else:
        resolved_out = PosixPathAdapter.resolve_path(output_mov)
        resolved_out.parent.mkdir(parents=True, exist_ok=True)

    if resolved_out.exists() and resolved_out.stat().st_size > 0:
        return resolved_out

    args = [
        "-y",
        "-i", str(resolved_in),
        "-c:v", "copy",
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        str(resolved_out)
    ]
    res = run_ffmpeg(args, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"Lỗi FFmpeg khi remux sang MOV PCM: {res.stderr[:400]}")
    return resolved_out


class AudioProcessor:
    """Class wrapper cung cấp API xử lý âm thanh hướng đối tượng."""
    probe_streams = staticmethod(probe_streams)
    inspect_audio_codec = staticmethod(inspect_audio_codec)
    is_linux_aac_conflict = staticmethod(is_linux_aac_conflict)
    extract_pcm_wav = staticmethod(extract_pcm_wav)
    remux_to_mov_pcm = staticmethod(remux_to_mov_pcm)
