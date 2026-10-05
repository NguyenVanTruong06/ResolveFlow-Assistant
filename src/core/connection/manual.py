import os
from typing import List, Dict, Any, Optional
from .base import IResolveConnection

class ManualConnection(IResolveConnection):
    """
    Kết nối thủ công. (Luôn luôn khả dụng).
    Không tương tác với API. Chỉ sinh file và hướng dẫn người dùng kéo thả.
    """
    def mode_name(self) -> str:
        return "MANUAL (Sinh file & Kéo thả tay)"

    def is_connected(self) -> bool:
        return True # Manual luôn khả dụng

    def get_media_pool_file_paths(self) -> List[str]:
        return []

    def import_media_to_media_pool(self, paths: List[str], log_callback: Optional[Any] = None) -> bool:
        if log_callback:
            log_callback("⚠️ [MANUAL MODE] Hãy tự kéo thả các file Media vào DaVinci Resolve!")
        return True

    def get_active_timeline_name(self) -> Optional[str]:
        return None

    def import_edl_to_timeline(self, edl_path: str, log_callback: Optional[Any] = None) -> bool:
        if log_callback:
            log_callback(f"⚠️ [MANUAL MODE] Đã xuất FCPXML/EDL ra: {edl_path}")
            log_callback("👉 Mở DaVinci Resolve > File > Import > Timeline... và chọn file này.")
        return True

    def get_current_playhead_seconds(self) -> float:
        return 0.0

    def apply_look_lut(self, lut_path: str, scope: str = "timeline", skip_existing: bool = True, log_callback: Optional[Any] = None) -> bool:
        if log_callback:
            log_callback(f"⚠️ [MANUAL MODE] Hãy kéo thả file LUT {os.path.basename(lut_path)} trực tiếp vào khung hình DaVinci.")
        return True
