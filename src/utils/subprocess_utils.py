"""
src/utils/subprocess_utils.py
Tiện ích subprocess đa nền tảng: ẩn cửa sổ console trên Windows.

Trong bản build GUI (PyInstaller console=False), mỗi lần gọi tiến trình con
(tasklist, powershell, ffmpeg, ffprobe...) mà không có CREATE_NO_WINDOW sẽ
làm Windows bật nháy một cửa sổ console đen.
"""
import subprocess
import sys

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
