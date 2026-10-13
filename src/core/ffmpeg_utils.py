import shutil
import subprocess
from typing import Optional, List, Union
from pathlib import Path


def get_ffmpeg_path() -> Optional[str]:
    """Tìm đường dẫn binary ffmpeg trong PATH hệ thống (không hardcode đuôi .exe)."""
    return shutil.which("ffmpeg")


def get_ffprobe_path() -> Optional[str]:
    """Tìm đường dẫn binary ffprobe trong PATH hệ thống (không hardcode đuôi .exe)."""
    return shutil.which("ffprobe")


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
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace"
    )
