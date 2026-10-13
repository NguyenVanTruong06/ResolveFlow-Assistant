import os
import sys
import re
import urllib.parse
from pathlib import Path, PureWindowsPath, PurePosixPath
from typing import Union


class PosixPathAdapter:
    """
    Adapter chuẩn hóa đường dẫn đa nền tảng (POSIX/Linux - Windows - macOS),
    loại bỏ hoàn toàn phụ thuộc đường dẫn cứng và phục vụ chuẩn định dạng URI cho DaVinci Resolve & FFmpeg.
    """

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
        - Mã hóa các ký tự đặc biệt và khoảng trắng (%20), giữ nguyên dấu '/' (safe="/").
        - Tránh lỗi Media Offline trên DaVinci Resolve Studio Linux & Windows.
        """
        # Nếu đường dẫn đã là file URI
        str_val = str(path).strip()
        if str_val.startswith("file://"):
            # Chuẩn hóa số dấu gạch chéo sau file:
            stripped = re.sub(r'^file:/+', '', str_val)
            if not stripped.startswith("/"):
                stripped = "/" + stripped
            posix_path = stripped
        else:
            resolved = cls.resolve_path(path)
            posix_path = resolved.as_posix()

        if not posix_path.startswith("/"):
            # Trường hợp Windows như C:/path/to/file
            posix_path = "/" + posix_path

        # Mã hóa theo chuẩn RFC 3986 với safe='/'
        quoted_path = urllib.parse.quote(posix_path, safe="/")
        return f"file://{quoted_path}"

    @classmethod
    def to_local_qurl(cls, path: Union[str, Path]):
        """
        Chuyển đổi an toàn đường dẫn sang QUrl thông qua QUrl.fromLocalFile().
        Tuyệt đối không dùng QUrl(str_path) để tránh lỗi xử lý URL nội bộ trên Linux.
        """
        from PySide6.QtCore import QUrl
        resolved = cls.resolve_path(path)
        return QUrl.fromLocalFile(str(resolved))
