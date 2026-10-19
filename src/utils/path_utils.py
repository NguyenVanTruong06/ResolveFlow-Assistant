import os
import sys
import re
import urllib.parse
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import Union

# Gốc tuyệt đối của dự án ResolveFlow-Assistant
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_asset_path(*parts: str) -> Path:
    """
    Trả về đường dẫn tuyệt đối đến thư mục hoặc tệp bên trong thư mục assets của dự án.
    Tránh lỗi lệch Current Working Directory khi chạy từ phím tắt, terminal hoặc script ngoài.
    """
    return PROJECT_ROOT.joinpath("assets", *parts)


_FONT_CACHE: dict = {}
_AVAILABLE_LINUX_FONTS = None


def get_installed_linux_fonts() -> set:
    """Quét và lưu bộ nhớ đệm (cache) danh sách họ phông chữ (font families) đã cài đặt trên Linux."""
    global _AVAILABLE_LINUX_FONTS
    if _AVAILABLE_LINUX_FONTS is not None:
        return _AVAILABLE_LINUX_FONTS
    fonts = set()
    try:
        import subprocess
        res = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                for f in line.split(","):
                    name = f.strip()
                    if name:
                        fonts.add(name.lower())
    except Exception:
        pass
    _AVAILABLE_LINUX_FONTS = fonts
    return _AVAILABLE_LINUX_FONTS


def resolve_system_font(requested_font: str) -> str:
    """
    Chuẩn hóa font chữ theo từng hệ điều hành để tránh lỗi Text+ Offline (mất chữ) trên DaVinci Resolve Linux:
    - Trên Windows/macOS: giữ nguyên font yêu cầu (ví dụ: 'Arial').
    - Trên Linux: kiểm tra xem font có cài đặt trong hệ thống hay không (qua fc-list).
      Nếu không tồn tại, tự động fallback theo thứ tự ưu tiên:
      ['Liberation Sans', 'DejaVu Sans', 'Noto Sans', 'Ubuntu', 'Sans'].
    """
    if not requested_font:
        return "Arial" if sys.platform != "linux" else "Liberation Sans"

    if sys.platform != "linux":
        return requested_font

    if requested_font in _FONT_CACHE:
        return _FONT_CACHE[requested_font]

    installed = get_installed_linux_fonts()
    if installed:
        if requested_font.lower() in installed:
            _FONT_CACHE[requested_font] = requested_font
            return requested_font

        fallbacks = ["Liberation Sans", "DejaVu Sans", "Noto Sans", "Ubuntu", "Sans"]
        for fb in fallbacks:
            if fb.lower() in installed:
                _FONT_CACHE[requested_font] = fb
                return fb

    _FONT_CACHE[requested_font] = "Liberation Sans"
    return "Liberation Sans"


class PosixPathAdapter:
    """
    Adapter chuẩn hóa đường dẫn đa nền tảng (POSIX/Linux - Windows - macOS),
    loại bỏ hoàn toàn phụ thuộc đường dẫn cứng và phục vụ chuẩn định dạng URI cho DaVinci Resolve & FFmpeg.
    """

    @classmethod
    def get_asset_path(cls, *parts: str) -> Path:
        """Truy cập đường dẫn thư mục assets được neo cố định theo gốc dự án PROJECT_ROOT."""
        return get_asset_path(*parts)

    @staticmethod
    def resolve_path(path: Union[str, Path]) -> Path:
        """
        Chuẩn hóa và phân giải đường dẫn tuyệt đối đầy đủ:
        - Mở rộng dấu ngã (~) thành thư mục người dùng.
        - Xử lý các liên kết mềm (symlink) và đường dẫn tương đối.
        """
        if isinstance(path, Path):
            return path.expanduser().resolve()
        
        path_str = str(path).strip()
        # Chuyển đổi ký tự gạch chéo ngược Windows nếu đang chạy trên Linux
        if sys.platform != "win32" and "\\" in path_str:
            # Nếu có dạng C:\..., tạm chuyển sang relative hoặc chuẩn hóa
            path_str = path_str.replace("\\", "/")
        return Path(path_str).expanduser().resolve()

    @staticmethod
    def to_posix(path: Union[str, Path]) -> str:
        """
        Chuyển đổi bất kỳ đường dẫn nào (Windows hoặc POSIX) sang chuỗi POSIX chuẩn dùng dấu '/'.
        """
        if isinstance(path, Path):
            return path.as_posix()
        
        path_str = str(path).strip().replace("\\", "/")
        # Loại bỏ các dấu // dư thừa ngoại trừ đầu URI
        return re.sub(r'(?<!:)/{2,}', '/', path_str)

    @classmethod
    def to_fcpxml_uri(cls, path: Union[str, Path]) -> str:
        """
        Chuyển đổi đường dẫn tệp tin sang định dạng URI RFC 3986 chuẩn Apple FCPXML 1.9 & FCP7 XML:
        - Luôn bắt đầu bằng file:/// (3 dấu gạch chéo).
        - Giữ nguyên dấu ':' của ổ đĩa Windows (ví dụ C:) thay vì mã hóa thành %3A (safe="/:").
        - Mã hóa khoảng trắng (%20) và ký tự đặc biệt, tránh lỗi Media Offline trên DaVinci Resolve.
        """
        # Nếu đường dẫn đã là file URI
        str_val = str(path).strip()
        if str_val.startswith("file://"):
            # Chuẩn hóa số dấu gạch chéo sau file:
            stripped = re.sub(r'^file:/+', '', str_val)
            if not stripped.startswith("/"):
                stripped = "/" + stripped
            posix_path = stripped
        elif re.match(r'^[a-zA-Z]:[/\\]', str_val):
            # Đường dẫn tuyệt đối Windows (ví dụ C:\ hoặc C:/)
            norm_win = str_val.replace("\\", "/")
            posix_path = "/" + norm_win.lstrip("/")
        else:
            resolved = cls.resolve_path(path)
            posix_path = resolved.as_posix()

        if not posix_path.startswith("/"):
            # Trường hợp Windows như C:/path/to/file
            posix_path = "/" + posix_path

        # Mã hóa theo chuẩn RFC 3986 với safe="/:" (bảo vệ dấu ':' của ổ đĩa Windows)
        quoted_path = urllib.parse.quote(posix_path, safe="/:")
        return f"file://{quoted_path}"

    @classmethod
    def normalize_for_comparison(cls, path: Union[str, Path]) -> str:
        """
        Chuẩn hóa đường dẫn phục vụ so sánh (deduplication / dictionary keys):
        - Trên Windows (os.name == 'nt' hoặc sys.platform == 'win32'): dùng .as_posix().lower().
        - Trên Linux/POSIX: giữ nguyên Case-Sensitivity .as_posix() để không làm sai lệch file thực tế.
        """
        resolved = cls.resolve_path(path)
        posix_str = resolved.as_posix()
        if os.name == "nt" or sys.platform == "win32":
            return posix_str.lower()
        return posix_str

    @classmethod
    def to_local_qurl(cls, path: Union[str, Path]):
        """
        Chuyển đổi an toàn đường dẫn sang QUrl thông qua QUrl.fromLocalFile().
        Tuyệt đối không dùng QUrl(str_path) để tránh lỗi xử lý URL nội bộ trên Linux.
        """
        from PySide6.QtCore import QUrl
        resolved = cls.resolve_path(path)
        return QUrl.fromLocalFile(str(resolved))
