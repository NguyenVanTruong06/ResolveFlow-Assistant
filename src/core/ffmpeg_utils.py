import shutil
import subprocess
from typing import Optional, List, Union
from pathlib import Path


import sys
import os

def get_ffmpeg_path() -> Optional[str]:
    """Tìm đường dẫn binary ffmpeg trong PATH hệ thống hoặc venv/static-ffmpeg."""
    p = shutil.which("ffmpeg")
    if p:
        return p
    # Fallback 1: check sys.prefix / bin / ffmpeg
    cand = os.path.join(sys.prefix, "bin", "ffmpeg")
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    # Fallback 2: static-ffmpeg
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        p = shutil.which("ffmpeg")
        if p:
            return p
    except Exception:
        pass
    # Fallback 3: imageio_ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


def get_ffprobe_path() -> Optional[str]:
    """Tìm đường dẫn binary ffprobe trong PATH hệ thống hoặc venv/static-ffmpeg."""
    p = shutil.which("ffprobe")
    if p:
        return p
    cand = os.path.join(sys.prefix, "bin", "ffprobe")
    if os.path.exists(cand) and os.access(cand, os.X_OK):
        return cand
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        p = shutil.which("ffprobe")
        if p:
            return p
    except Exception:
        pass
    return None


def is_ffmpeg_available() -> bool:
    """Kiểm tra xem ffmpeg có sẵn sàng thực thi trên máy hay không."""
    return get_ffmpeg_path() is not None


def is_ffprobe_available() -> bool:
    """Kiểm tra xem ffprobe có sẵn sàng thực thi trên máy hay không."""
    return get_ffprobe_path() is not None


def run_ffmpeg(args: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """
    Thực thi lệnh FFmpeg an toàn thông qua subprocess.
    Tự động xác định binary ffmpeg hợp lệ.
    """
    ffmpeg_bin = get_ffmpeg_path()
    if not ffmpeg_bin:
        raise FileNotFoundError(
            "Không tìm thấy công cụ FFmpeg trong hệ thống PATH.\n"
            "Trên Linux: vui lòng chạy 'sudo apt install -y ffmpeg'.\n"
            "Trên macOS: 'brew install ffmpeg'.\n"
            "Trên Windows: thêm thư mục ffmpeg vào PATH."
        )

    cmd = [ffmpeg_bin] + args
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        **kwargs
    )
