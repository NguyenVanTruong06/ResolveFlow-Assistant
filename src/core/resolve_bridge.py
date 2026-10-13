import os
import sys
import ctypes
from pathlib import Path
from typing import Optional, Tuple, Any

from src.utils.path_utils import PosixPathAdapter


def setup_resolve_environment() -> Tuple[Optional[str], Optional[str]]:
    """
    Tự động xác định và thiết lập biến môi trường RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB
    đồng thời nạp trước thư viện chia sẻ libfusionscript.so bằng ctypes.RTLD_GLOBAL trên Linux.

    Returns:
        Tuple[api_dir, lib_path]
    """
    api_dir = os.environ.get("RESOLVE_SCRIPT_API")
    lib_path = os.environ.get("RESOLVE_SCRIPT_LIB")

    if sys.platform.startswith("linux"):
        default_api = "/opt/resolve/Developer/Scripting"
        default_lib = "/opt/resolve/libs/Fusion/fusionscript.so"
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path and os.path.isfile(default_lib):
            lib_path = default_lib

        # Nạp trước thư viện fusionscript.so với cờ RTLD_GLOBAL để các symbol C++ được chia sẻ
        if lib_path and os.path.exists(lib_path):
            try:
                ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
            except Exception as e:
                # Log hoặc bỏ qua nếu đã nạp hoặc không hỗ trợ
                pass

    elif sys.platform.startswith("darwin"):
        default_api = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        default_lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.dylib"
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path and os.path.isfile(default_lib):
            lib_path = default_lib
    else:
        # Windows
        default_api = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
        default_lib = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
        if not api_dir and os.path.isdir(default_api):
            api_dir = default_api
        if not lib_path and os.path.isfile(default_lib):
            lib_path = default_lib

    if api_dir:
        os.environ["RESOLVE_SCRIPT_API"] = api_dir
        modules_dir = os.path.join(api_dir, "Modules")
        if os.path.isdir(modules_dir) and modules_dir not in sys.path:
            sys.path.append(modules_dir)

    if lib_path:
        os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    return api_dir, lib_path


def load_resolve_api() -> Tuple[Optional[Any], str]:
    """
    Nạp đối tượng ứng dụng DaVinci Resolve thông qua FusionScript API.
    Tuyệt đối không import DaVinciResolveScript ở top-level khi chưa thiết lập môi trường.

    Returns:
        Tuple[resolve_app, status_message]
    """
    setup_resolve_environment()

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError as e:
        return None, f"Không thể import module DaVinciResolveScript: {e}"

    try:
        resolve_app = dvr_script.scriptapp("Resolve")
        if resolve_app:
            return resolve_app, "Kết nối DaVinci Resolve thành công."
        return None, (
            "DaVinci Resolve không phản hồi. Hãy mở ứng dụng DaVinci Resolve và kiểm tra "
            "Preferences > General > External scripting using = Local."
        )
    except Exception as e:
        return None, f"Lỗi khởi tạo cổng FusionScript: {e}"


# Lazy import to avoid circular dependency
def _get_resolve_automation():
    from src.core.resolve_api import ResolveAutomation
    return ResolveAutomation

class ResolveBridge:
    """
    Cầu nối điều khiển DaVinci Resolve API cung cấp các phương thức quản lý kết nối chuẩn hóa.
    """
    def __init__(self):
        from src.core.resolve_api import ResolveAutomation
        self._automation = ResolveAutomation()

    def connect(self) -> bool:
        return self._automation.connect()

    def is_connected(self) -> bool:
        return self._automation.is_connected()

    def ensure_resolve_running(self, log_callback: Optional[Any] = None) -> bool:
        return self._automation.ensure_resolve_running(log_callback=log_callback)

    def __getattr__(self, name):
        return getattr(self._automation, name)
