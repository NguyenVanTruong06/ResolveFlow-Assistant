"""
src/utils/subprocess_utils.py
Tiện ích subprocess đa nền tảng: ẩn cửa sổ console trên Windows và làm sạch
môi trường thực thi cho các tiến trình con ngoài (ffmpeg, ffprobe).

Trong bản build GUI (PyInstaller console=False), mỗi lần gọi tiến trình con
(tasklist, powershell, ffmpeg, ffprobe...) mà không có CREATE_NO_WINDOW sẽ
làm Windows bật nháy một cửa sổ console đen.

Trên Linux/macOS, khi chạy trong ngữ cảnh DaVinci Resolve hoặc PyInstaller standalone,
LD_LIBRARY_PATH bị ô nhiễm bởi các thư viện Resolve (/opt/resolve/libs...) hoặc
_internal, dẫn đến xung đột symbol động (như av_bessel_i0) khiến ffmpeg/ffprobe
hệ thống sập với exit code 127.
"""
import os
import subprocess
import sys
from typing import Dict, List, Optional

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window_kwargs() -> dict:
    """Trả về kwargs cần truyền vào subprocess.run/Popen để không hiện console trên Windows."""
    if sys.platform.startswith("win"):
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def install_global_no_window_patch() -> bool:
    """
    Lưới an toàn toàn cục: trên Windows, mặc định mọi subprocess.Popen (kể cả thư viện
    bên thứ 3 như ffmpeg-python) đều chạy với CREATE_NO_WINDOW nếu caller không chỉ định
    creationflags. Idempotent.
    """
    if not sys.platform.startswith("win"):
        return False
    popen_cls = subprocess.Popen
    if getattr(popen_cls, "_chundvc_no_window", False):
        return True

    original_init = popen_cls.__init__

    def _patched_init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    popen_cls.__init__ = _patched_init
    popen_cls._chundvc_no_window = True
    return True


def _is_same_or_subpath(target_path: str, base_path: str) -> bool:
    """Kiểm tra target_path là chính base_path hoặc nằm trong base_path theo đúng ranh giới đường dẫn."""
    try:
        t = os.path.normcase(os.path.abspath(target_path))
        b = os.path.normcase(os.path.abspath(base_path))
        return os.path.commonpath([t, b]) == b
    except Exception:
        return False


def _is_contaminated_media_dir(path: str) -> bool:
    """
    Xác định một thư mục có chứa thư viện media xung đột với ffmpeg/ffprobe hệ thống hay không:
    - Thư viện DaVinci Resolve (/opt/resolve/libs, /opt/resolve/Developer...)
    - Ranh giới trích xuất tạm PyInstaller tùy ý (sys._MEIPASS)
    - Thư mục đóng gói _internal của ứng dụng phân phối
    - Thư mục tạm thời PyInstaller _MEIxxxxxx
    Tránh lọc nhầm (substring overfilter) các thư mục hợp lệ của người dùng (lookalikes).
    """
    norm_p = os.path.normpath(path)
    if not norm_p or norm_p == ".":
        return True

    # 1. Ranh giới DaVinci Resolve thực tế
    if _is_same_or_subpath(norm_p, "/opt/resolve"):
        return True
    parts_lower = [c.lower() for c in norm_p.split(os.sep) if c]
    if "resolve" in parts_lower and "libs" in parts_lower:
        return True
    if any("fusionscript" in c for c in parts_lower):
        return True

    # 2. Ranh giới PyInstaller sys._MEIPASS tùy ý
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and _is_same_or_subpath(norm_p, str(meipass)):
        return True

    # 3. Môi trường PyInstaller một tệp (onefile) trong thư mục tạm: _MEIxxxxxx
    if any(c.startswith("_MEI") and len(c) >= 5 and c[4:].isalnum() for c in norm_p.split(os.sep) if c):
        return True

    # 4. Thư mục _internal của gói ứng dụng ResolveFlow / ChunDVC
    if _is_same_or_subpath(norm_p, "/opt/resolveflow-assistant"):
        return True
    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if _is_same_or_subpath(norm_p, os.path.join(exe_dir, "_internal")):
            return True
    if "_internal" in parts_lower and any("resolveflow" in c for c in parts_lower):
        return True

    return False


def clean_media_subprocess_env(base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Tạo bản sao môi trường độc lập, an toàn cho các tiến trình con xử lý media ngoài (ffmpeg, ffprobe).
    - Không làm biến đổi (mutate) os.environ của tiến trình cha.
    - Chuẩn ngữ nghĩa PyInstaller: Khi LD_LIBRARY_PATH_ORIG tồn tại, khôi phục giá trị gốc rồi lọc sạch,
      TUYỆT ĐỐI KHÔNG gộp (append) các đường dẫn nạp từ LD_LIBRARY_PATH hiện tại (vốn đã bị PyInstaller tiêm vào).
      Nếu LD_LIBRARY_PATH_ORIG rỗng, biến LD_LIBRARY_PATH được loại bỏ hoàn toàn.
    - Loại bỏ triệt để các đường dẫn nạp thư viện DaVinci Resolve (/opt/resolve/libs...) vốn
      chứa các bản dựng libav* cũ/tùy biến xung đột nhãn symbol với ffmpeg hệ thống (gây lỗi 127).
    - Loại bỏ đường dẫn đóng gói tạm thời của PyInstaller theo ranh giới sys._MEIPASS thực tế.
    - Bảo toàn toàn bộ đường dẫn thư mục hợp lệ (kể cả lookalikes), PATH, và các cấu hình hệ thống khác.
    """
    env = dict(base_env if base_env is not None else os.environ)

    # 1. Thu thập ứng viên thư viện Linux LD_LIBRARY_PATH theo đúng thứ tự ưu tiên PyInstaller
    meipass2 = env.pop("_MEIPASS2", None)
    candidates: List[str] = []
    if "LD_LIBRARY_PATH_ORIG" in env:
        orig = env.pop("LD_LIBRARY_PATH_ORIG")
        env.pop("LD_LIBRARY_PATH", None)
        if orig:
            candidates = [p.strip() for p in orig.split(os.pathsep) if p.strip()]
    elif "LD_LIBRARY_PATH" in env:
        cur = env.get("LD_LIBRARY_PATH", "")
        if cur:
            candidates = [p.strip() for p in cur.split(os.pathsep) if p.strip()]

    cleaned_dirs: List[str] = []
    seen = set()
    for raw_p in candidates:
        norm_p = os.path.normpath(raw_p)
        if meipass2 and _is_same_or_subpath(norm_p, meipass2):
            continue
        if _is_contaminated_media_dir(norm_p):
            continue
        if norm_p not in seen:
            seen.add(norm_p)
            cleaned_dirs.append(norm_p)

    if cleaned_dirs:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(cleaned_dirs)
    else:
        env.pop("LD_LIBRARY_PATH", None)

    # 2. Tương tự cho macOS DYLD_LIBRARY_PATH nếu có
    dyld_candidates: List[str] = []
    if "DYLD_LIBRARY_PATH_ORIG" in env:
        orig_dyld = env.pop("DYLD_LIBRARY_PATH_ORIG")
        env.pop("DYLD_LIBRARY_PATH", None)
        if orig_dyld:
            dyld_candidates = [p.strip() for p in orig_dyld.split(os.pathsep) if p.strip()]
    elif "DYLD_LIBRARY_PATH" in env:
        cur_dyld = env.get("DYLD_LIBRARY_PATH", "")
        if cur_dyld:
            dyld_candidates = [p.strip() for p in cur_dyld.split(os.pathsep) if p.strip()]

    dyld_cleaned: List[str] = []
    dyld_seen = set()
    for raw_dp in dyld_candidates:
        norm_dp = os.path.normpath(raw_dp)
        if meipass2 and _is_same_or_subpath(norm_dp, meipass2):
            continue
        if _is_contaminated_media_dir(norm_dp):
            continue
        if norm_dp not in dyld_seen:
            dyld_seen.add(norm_dp)
            dyld_cleaned.append(norm_dp)

    if dyld_cleaned:
        env["DYLD_LIBRARY_PATH"] = os.pathsep.join(dyld_cleaned)
    else:
        env.pop("DYLD_LIBRARY_PATH", None)

    return env
