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


def parse_fractional_rate(rate_str: str) -> float:
    """Chuyển đổi chuỗi phân số fps (vd: '30000/1001', '30/1', '25') sang float."""
    if not rate_str or rate_str == "0/0":
        return 0.0
    try:
        if "/" in rate_str:
            num, den = rate_str.split("/", 1)
            den_val = float(den)
            return float(num) / den_val if den_val != 0 else 0.0
        return float(rate_str)
    except Exception:
        return 0.0


def detect_vfr_status(video_path: Union[str, Path]) -> dict:
    """
    Kiểm tra trạng thái Variable Frame Rate (VFR) của video bằng cách so sánh
    r_frame_rate và avg_frame_rate qua ffprobe.
    Sử dụng _VIDEO_META_CACHE để tránh gọi ffprobe lặp lại.
    """
    import json
    if not video_path:
        return {"is_vfr": False, "fps_diff": 0.0, "r_fps": 0.0, "avg_fps": 0.0, "warning": None}

    norm_path = os.path.normpath(str(video_path))
    try:
        from src.core.resolve_api import _VIDEO_META_CACHE
        if norm_path in _VIDEO_META_CACHE and "is_vfr" in _VIDEO_META_CACHE[norm_path]:
            return _VIDEO_META_CACHE[norm_path]
    except Exception:
        pass

    r_fps = 0.0
    avg_fps = 0.0
    ffprobe_bin = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
        norm_path
    ]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs
        )
        if res.stdout:
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            if streams:
                v_stream = streams[0]
                r_rate_str = v_stream.get("r_frame_rate", "")
                avg_rate_str = v_stream.get("avg_frame_rate", "")
                r_fps = parse_fractional_rate(r_rate_str)
                avg_fps = parse_fractional_rate(avg_rate_str)
    except Exception:
        pass

    fps_diff = abs(r_fps - avg_fps)
    is_vfr = bool(r_fps > 0 and avg_fps > 0 and fps_diff > 0.5)
    warning = None
    if is_vfr:
        base_name = os.path.basename(norm_path)
        warning = (
            f"Cảnh báo: Video '{base_name}' có tốc độ khung hình biến thiên (VFR - Variable Frame Rate): "
            f"r_fps={r_fps:.2f}, avg_fps={avg_fps:.2f} (chênh lệch {fps_diff:.2f} fps > 0.5). "
            f"Đưa trực tiếp vào DaVinci Resolve có thể gây trôi âm thanh (audio sync drift). "
            f"Khuyến nghị transcode sang CFR trước khi dựng."
        )

    res_dict = {
        "is_vfr": is_vfr,
        "fps_diff": round(fps_diff, 4),
        "r_fps": r_fps,
        "avg_fps": avg_fps,
        "warning": warning
    }

    try:
        from src.core.resolve_api import _VIDEO_META_CACHE
        if norm_path not in _VIDEO_META_CACHE:
            _VIDEO_META_CACHE[norm_path] = {}
        _VIDEO_META_CACHE[norm_path].update(res_dict)
        return _VIDEO_META_CACHE[norm_path]
    except Exception:
        return res_dict

