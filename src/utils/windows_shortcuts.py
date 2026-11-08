"""
src/utils/windows_shortcuts.py
Đăng ký ChunDVC vào Windows Start Menu ("Apps") và Desktop bằng shortcut .lnk.

Không cần quyền Admin: dùng thư mục Start Menu của user hiện tại
(%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs).
"""
import os
import subprocess
import sys
from typing import Dict, Optional

from src.utils.subprocess_utils import no_window_kwargs

APP_DISPLAY_NAME = "ChunDVC"
EXE_NAME = "ResolveFlow-Assistant.exe"
SHORTCUT_NAME = f"{APP_DISPLAY_NAME}.lnk"


def _shell_folder(csidl: int) -> Optional[str]:
    """Lấy đường dẫn thư mục đặc biệt thật (hỗ trợ Desktop bị OneDrive chuyển hướng)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return None


def default_programs_dir() -> str:
    found = _shell_folder(0x0002)  # CSIDL_PROGRAMS
    if found:
        return found
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def default_desktop_dir() -> str:
    found = _shell_folder(0x0010)  # CSIDL_DESKTOPDIRECTORY
    if found:
        return found
    return os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")


def find_app_icon(app_dir: str) -> Optional[str]:
    """Tìm chundvc.ico trong bản đóng gói (gốc / _internal) hoặc trong source tree."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates = [
        os.path.join(app_dir, "chundvc.ico"),
        os.path.join(app_dir, "_internal", "assets", "branding", "chundvc.ico"),
        os.path.join(app_dir, "assets", "branding", "chundvc.ico"),
        os.path.join(project_root, "assets", "branding", "chundvc.ico"),
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(lnk_path: str, target: str, working_dir: str,
                    icon_path: Optional[str] = None, description: str = "") -> bool:
    """Tạo file .lnk qua WScript.Shell (PowerShell ẩn, không nháy console)."""
    os.makedirs(os.path.dirname(lnk_path), exist_ok=True)
    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$s = $ws.CreateShortcut({_ps_quote(lnk_path)})",
        f"$s.TargetPath = {_ps_quote(target)}",
        f"$s.WorkingDirectory = {_ps_quote(working_dir)}",
        f"$s.Description = {_ps_quote(description or APP_DISPLAY_NAME)}",
    ]
    if icon_path:
        lines.append(f"$s.IconLocation = {_ps_quote(icon_path + ',0')}")
    lines.append("$s.Save()")
    script = "; ".join(lines)
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    return res.returncode == 0 and os.path.isfile(lnk_path)


def read_shortcut_target(lnk_path: str) -> str:
    if not os.path.isfile(lnk_path):
        return ""
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(lnk_path)}).TargetPath"],
            capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
        return (res.stdout or "").strip()
    except Exception:
        return ""


def install_shortcuts(app_dir: str, programs_dir: Optional[str] = None,
                      desktop_dir: Optional[str] = None, desktop: bool = True,
                      dry_run: bool = False) -> Dict[str, object]:
    """Tạo shortcut Start Menu (+ Desktop) trỏ tới ResolveFlow-Assistant.exe trong app_dir."""
    app_dir = os.path.abspath(app_dir)
    target = os.path.join(app_dir, EXE_NAME)
    ico = find_app_icon(app_dir)
    start_lnk = os.path.join(programs_dir or default_programs_dir(), SHORTCUT_NAME)
    desk_lnk = os.path.join(desktop_dir or default_desktop_dir(), SHORTCUT_NAME) if desktop else ""
    result: Dict[str, object] = {
        "ok": True,
        "target": target,
        "ico_path": ico or "",
        "start_menu_shortcut": start_lnk,
        "desktop_shortcut": desk_lnk,
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    if not sys.platform.startswith("win"):
        result["ok"] = False
        result["error"] = "not_windows"
        return result

    desc = "ChunDVC - Trợ lý Dựng Phim AI & Kho Đạo Cụ cho DaVinci Resolve"
    ok = create_shortcut(start_lnk, target, app_dir, ico, desc)
    if desk_lnk:
        ok = create_shortcut(desk_lnk, target, app_dir, ico, desc) and ok
    result["ok"] = ok
    return result


def ensure_windows_shortcuts(app_dir: str) -> bool:
    """
    Gọi khi bản frozen khởi động: nếu Start Menu chưa có ChunDVC.lnk hoặc trỏ sai thư mục
    (ví dụ người dùng giải nén bản mới sang chỗ khác) thì tạo/cập nhật lại.
    Desktop shortcut chỉ tạo ở lần đăng ký đầu tiên.
    """
    if not sys.platform.startswith("win"):
        return False
    target = os.path.normcase(os.path.abspath(os.path.join(app_dir, EXE_NAME)))
    start_lnk = os.path.join(default_programs_dir(), SHORTCUT_NAME)
    first_time = not os.path.isfile(start_lnk)
    if not first_time:
        current = read_shortcut_target(start_lnk)
        if current and os.path.normcase(os.path.abspath(current)) == target:
            return True
    desk_lnk = os.path.join(default_desktop_dir(), SHORTCUT_NAME)
    want_desktop = first_time or os.path.isfile(desk_lnk)
    return bool(install_shortcuts(app_dir, desktop=want_desktop)["ok"])
